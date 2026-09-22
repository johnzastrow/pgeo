-- Query functions. They live in schema geocode (stable across builds) and read the tables
-- in schema pgeo (swapped in by each build). Every function returns geocode.hit rows; the
-- API only parses input and formats these rows as Pelias GeoJSON.
--
-- Inputs are typed parameters only; nothing is concatenated into SQL. The one piece of
-- text built here, the autocomplete tsquery, is assembled from tokens that geocode.norm()
-- has already reduced to [a-z0-9].

DROP TYPE IF EXISTS geocode.hit CASCADE;
CREATE TYPE geocode.hit AS (
    id bigint, gid text, source text, layer text, source_id text, name text,
    housenumber text, street text, postcode text,
    neighbourhood text, locality text, localadmin text, county text, region text, region_a text,
    label text, category text[], addendum jsonb,
    lon double precision, lat double precision, bbox double precision[],
    confidence real, match_type text, accuracy text, distance_km double precision, score real,
    hier jsonb                                   -- Pelias hierarchy ids (locality_gid, county_gid, county_a, ...)
);

-- Focus-point boost: 1 at the focus, ~0.37 at 50 km, fading with distance.
CREATE OR REPLACE FUNCTION geocode.focus_boost(g geometry, focus geometry) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT CASE WHEN focus IS NULL THEN 0
       ELSE exp(-(ST_Distance(g::geography, focus::geography) / 50000.0)) END $$;

-- Shared filter: layers, sources, bounding rectangle.
DROP FUNCTION IF EXISTS geocode.keep(pgeo.feature, text[], text[], geometry) CASCADE;
-- Shared filter: layers, sources, area (boundary.rect and/or boundary.circle), boundary.gid
-- (the feature is, or lies in, that admin area), categories (any overlap).
CREATE OR REPLACE FUNCTION geocode.keep(f pgeo.feature, layers text[], sources text[], rect geometry,
                                        gid text DEFAULT NULL, cats text[] DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT (layers IS NULL OR cardinality(layers) = 0 OR f.layer = ANY (layers))
         AND (sources IS NULL OR cardinality(sources) = 0 OR f.source = ANY (sources))
         AND (rect IS NULL OR ST_Intersects(f.geom, rect))
         -- the country, or any state this build covers, means "everywhere in this build"
         AND (gid IS NULL OR f.gid = gid OR gid = 'whosonfirst:country:85633793'
              OR EXISTS (SELECT 1 FROM geocode.region_ref r
                         WHERE gid = 'whosonfirst:region:' || r.wof_id)
              OR EXISTS (SELECT 1 FROM jsonb_each_text(f.hier) e WHERE e.value = gid))
         AND (cats IS NULL OR cardinality(cats) = 0 OR f.category && cats) $$;

-- Search area from boundary.rect (min_lon, min_lat, max_lon, max_lat) and boundary.circle
-- (lon, lat, radius km); NULL when neither is given.
CREATE OR REPLACE FUNCTION geocode.search_area(p_rect double precision[], p_circle double precision[])
RETURNS geometry LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
  WITH r AS (SELECT CASE WHEN cardinality(p_rect) = 4
                         THEN ST_MakeEnvelope(p_rect[1], p_rect[2], p_rect[3], p_rect[4], 4326) END AS g),
       c AS (SELECT CASE WHEN cardinality(p_circle) = 3
                         THEN ST_Buffer(ST_SetSRID(ST_MakePoint(p_circle[1], p_circle[2]), 4326)::geography,
                                        p_circle[3] * 1000)::geometry END AS g)
  SELECT CASE WHEN r.g IS NOT NULL AND c.g IS NOT NULL THEN ST_Intersection(r.g, c.g) ELSE coalesce(r.g, c.g) END
  FROM r, c
$$;

-- boundary.country: the data is Maine only, so any country other than the US matches nothing.
CREATE OR REPLACE FUNCTION geocode.country_ok(p_country text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT p_country IS NULL OR upper(btrim(p_country)) IN ('US', 'USA') $$;

-- Copy of a hit with a different confidence.
CREATE OR REPLACE FUNCTION geocode.set_conf(h geocode.hit, c real) RETURNS geocode.hit
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ROW(h.id, h.gid, h.source, h.layer, h.source_id, h.name, h.housenumber, h.street, h.postcode,
              h.neighbourhood, h.locality, h.localadmin, h.county, h.region, h.region_a, h.label,
              h.category, h.addendum, h.lon, h.lat, h.bbox, c, h.match_type, h.accuracy,
              h.distance_km, h.score, h.hier)::geocode.hit $$;

CREATE OR REPLACE FUNCTION geocode.to_hit(f pgeo.feature, conf real, mt text, acc text,
                                          dist_km double precision, score real)
RETURNS geocode.hit LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ROW(f.id, f.gid, f.source, f.layer, f.source_id, f.name, f.housenumber, f.street,
              f.postcode, f.neighbourhood, f.locality, f.localadmin, f.county, f.region, f.region_a,
              f.label, f.category, f.addendum, ST_X(f.geom), ST_Y(f.geom), f.bbox,
              conf, mt, acc, dist_km, score, f.hier)::geocode.hit $$;

-- ---------------------------------------------------------------------------------------
-- search: unstructured and structured forward geocoding.
-- The caller passes whatever components its parser found (any may be NULL):
--   p_text full query, p_name place/venue name part, p_hn house number, p_street street,
--   p_locality town, p_postcode ZIP.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.search(
    p_text text, p_name text, p_hn text, p_street text, p_locality text, p_postcode text,
    p_focus_lon double precision DEFAULT NULL, p_focus_lat double precision DEFAULT NULL,
    p_layers text[] DEFAULT NULL, p_sources text[] DEFAULT NULL,
    p_rect double precision[] DEFAULT NULL, p_size integer DEFAULT 10,
    p_circle double precision[] DEFAULT NULL, p_gid text DEFAULT NULL,
    p_categories text[] DEFAULT NULL, p_country text DEFAULT NULL)
RETURNS SETOF geocode.hit
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  -- full query without a trailing state/country ("portlnd me"): venues literally named
  -- "Portland, ME" otherwise match the state word better than the town does
  qf    text := nullif(regexp_replace(geocode.norm(coalesce(p_text, '')),
                                      '(\s+(me|maine))?(\s+(us|usa|united states))?$', ''), '');
  qn    text := nullif(geocode.norm(p_name), '');                     -- parser's name part
  st    text := nullif(geocode.norm(p_street), '');
  loc   text := nullif(geocode.norm(p_locality), '');
  hn    text := nullif(lower(trim(p_hn)), '');
  -- A parsed house number + street means address intent: the full text is then not searched
  -- as a place name (it would match the town and outrank the address).
  addr_intent boolean := hn IS NOT NULL AND st IS NOT NULL;
  targets text[];
  hni   integer := geocode.hn_int(p_hn);
  pc    text := substring(p_postcode from '\d{5}');
  focus geometry := CASE WHEN p_focus_lon IS NOT NULL AND p_focus_lat IS NOT NULL
                         THEN ST_SetSRID(ST_MakePoint(p_focus_lon, p_focus_lat), 4326) END;
  rect  geometry := geocode.search_area(p_rect, p_circle);
  lim   integer := least(greatest(coalesce(p_size, 10), 1), 40);
  townpt geometry;   -- the queried town's location(s), when the town name is known
  anchor geometry;   -- tie-break point for same-named candidates: focus, else the town
  -- keep() cannot be inlined and takes the whole row; with no filter set it is pure cost
  -- (docs/PERFORMANCE_OPTIMIZATION.md, F1), so that case is decided once, here.
  nofilter boolean := (p_layers IS NULL OR cardinality(p_layers) = 0)
                  AND (p_sources IS NULL OR cardinality(p_sources) = 0)
                  AND p_gid IS NULL
                  AND (p_categories IS NULL OR cardinality(p_categories) = 0);
BEGIN
  PERFORM set_config('pg_trgm.similarity_threshold', '0.3', true);
  IF loc IS NOT NULL THEN
    SELECT ST_Collect(t.geom) INTO townpt FROM (
      SELECT f.geom FROM pgeo.feature f
      WHERE f.layer IN ('locality', 'localadmin', 'neighbourhood') AND f.name_norm = loc
      ORDER BY f.importance DESC LIMIT 5) t;
  END IF;
  IF NOT geocode.country_ok(p_country) THEN
    RETURN;
  END IF;
  -- tie-break anchor: focus, else the circle or rectangle centre, else the boundary.gid area,
  -- else the town. Candidates are cut to 40/60 rows before the area filter applies, so without
  -- an anchor inside the area the survivors could all lie outside it.
  anchor := coalesce(focus,
                     CASE WHEN cardinality(p_circle) = 3 THEN ST_SetSRID(ST_MakePoint(p_circle[1], p_circle[2]), 4326) END,
                     CASE WHEN rect IS NOT NULL THEN ST_Centroid(rect) END,
                     (SELECT x.geom FROM pgeo.feature x WHERE x.gid = p_gid LIMIT 1),
                     townpt);
  PERFORM set_config('pg_trgm.word_similarity_threshold', '0.5', true);
  -- Name strings to try: the parser's name part; the full text unless an address was parsed;
  -- a bare town ("Portland, Maine") only when no name part was parsed: in "Subway, Cumberland
  -- Mills" the town is context, and as a target it matched exactly and beat the venue (the
  -- town CTE still offers it as a low-confidence fallback). Parsers misread odd input
  -- ("T4 R9 WELS"), so the full text is the safety net.
  targets := ARRAY(SELECT DISTINCT t FROM unnest(ARRAY[
               qn,
               CASE WHEN NOT addr_intent THEN qf END,
               CASE WHEN NOT addr_intent AND st IS NULL AND hn IS NULL AND qn IS NULL THEN loc END]) t
             WHERE t IS NOT NULL AND t <> '');

  RETURN QUERY
  WITH
  -- Streets whose name resembles the parsed street (typo tolerant), with town agreement.
  streets AS (
    SELECT s.street_norm, s.locality_norm,
           similarity(s.street_norm, st) AS st_sim,
           CASE WHEN loc IS NULL THEN NULL ELSE similarity(s.locality_norm, loc) END AS loc_sim
    FROM pgeo.street_name s
    WHERE st IS NOT NULL AND hn IS NOT NULL AND s.street_norm % st
    ORDER BY similarity(s.street_norm, st)
             + CASE WHEN loc IS NOT NULL THEN similarity(s.locality_norm, loc) ELSE 0 END DESC
    LIMIT 25
  ),
  addr_exact AS (
    SELECT f.id, s.st_sim, s.loc_sim
    FROM streets s
    JOIN pgeo.feature f ON f.layer = 'address' AND f.street_norm = s.street_norm AND f.hn_int = hni
                        AND (f.locality_norm = s.locality_norm OR f.postal_locality_norm = s.locality_norm)
    WHERE lower(f.housenumber) = hn
    UNION ALL
    -- The same number on the best street names in *any* town. "streets" keeps 25 of ~150
    -- (Church Street, town) pairs, so without this "21 Church St, Maine" looked unique.
    SELECT f.id, n.st_sim,
           CASE WHEN loc IS NULL THEN NULL
                ELSE greatest(similarity(coalesce(f.locality_norm, ''), loc),
                              similarity(coalesce(f.postal_locality_norm, ''), loc)) END
    FROM (SELECT x.street_norm, max(x.st_sim) AS st_sim FROM streets x
          GROUP BY x.street_norm ORDER BY 2 DESC LIMIT 3) n
    JOIN pgeo.feature f ON f.layer = 'address' AND f.street_norm = n.street_norm AND f.hn_int = hni
    WHERE lower(f.housenumber) = hn
  ),
  -- Interpolation on the best streets that lack the exact number: nearest known numbers
  -- below and above on the same street and town, position linearly between them.
  interp AS (
    SELECT s.street_norm, s.locality_norm, s.st_sim, s.loc_sim, lo.geom AS g_lo, hi.geom AS g_hi,
           lo.hn_int AS n_lo, hi.hn_int AS n_hi, lo.id AS lo_id
    FROM (SELECT * FROM streets x
          WHERE x.st_sim >= 0.6
            AND NOT EXISTS (SELECT 1 FROM addr_exact e JOIN pgeo.feature f ON f.id = e.id
                            WHERE f.street_norm = x.street_norm AND f.locality_norm = x.locality_norm)
          ORDER BY x.st_sim + coalesce(x.loc_sim, 0) DESC LIMIT 3) s
    CROSS JOIN LATERAL (
      SELECT f.id, f.geom, f.hn_int FROM pgeo.feature f
      WHERE f.layer = 'address' AND f.street_norm = s.street_norm AND f.locality_norm = s.locality_norm
        AND f.hn_int < hni AND f.hn_int % 2 = hni % 2
      ORDER BY f.hn_int DESC LIMIT 1) lo
    CROSS JOIN LATERAL (
      SELECT f.id, f.geom, f.hn_int FROM pgeo.feature f
      WHERE f.layer = 'address' AND f.street_norm = s.street_norm AND f.locality_norm = s.locality_norm
        AND f.hn_int > hni AND f.hn_int % 2 = hni % 2
      ORDER BY f.hn_int LIMIT 1) hi
    WHERE hni IS NOT NULL AND ST_DWithin(lo.geom::geography, hi.geom::geography, 1500)
  ),
  street_fb AS (
    SELECT DISTINCT ON (f.street_norm, f.locality_norm) f.id, s.st_sim, s.loc_sim
    FROM streets s
    JOIN pgeo.feature f ON f.layer = 'street' AND f.street_norm = s.street_norm
                        AND f.locality_norm = s.locality_norm
    WHERE s.st_sim >= 0.6
    ORDER BY f.street_norm, f.locality_norm, f.id
  ),
  -- Place / venue / admin names (addresses excluded from this index).
  names AS (
    SELECT n.id, max(n.nsim) AS nsim
    FROM unnest(targets) AS q(txt)
    CROSS JOIN LATERAL (
      SELECT f.id,
             -- word_similarity(query, name): how well the query appears inside the name ("jetport"
             -- in "portland international jetport"). The reverse direction let one-word generic
             -- names ("Mountain", "Hill") match any query that contains that word.
             -- The small whole-string term breaks ties among names that all contain the query
             -- ("Portlnd" in Portland, New Portland, Portland Glass): the closest name wins.
             (greatest(similarity(f.name_norm, q.txt), 0.9 * word_similarity(q.txt, f.name_norm))
              + 0.1 * similarity(f.name_norm, q.txt))::double precision AS nsim
      -- This reads "feature", not the narrow feature_ac, on purpose. Generic names tie in their
      -- hundreds at one similarity ("Flying Hill": the 60th and 61st candidates both 0.375), and
      -- which of them survive the cut is decided by the order rows reach the sort - physical
      -- order, which a second table cannot reproduce: a heap insert backfills earlier pages, so
      -- even INSERT ... ORDER BY ctid left 660 of 203,399 rows out of place. Tried, and it
      -- changed the top result of 16 of 5,002 golden queries for a 17% gain on this stage.
      FROM pgeo.feature f
      WHERE f.layer <> 'address' AND f.name_norm % q.txt
      -- ties (hundreds of "Main Street"s) are broken by distance to the focus point, so the
      -- nearby ones survive the candidate limit
      ORDER BY similarity(f.name_norm, q.txt) DESC,
               CASE WHEN anchor IS NULL THEN 0 ELSE f.geom <-> anchor END
      LIMIT 60) n
    GROUP BY n.id
  ),
  names_exact AS (
    SELECT f.id, 1.0::double precision AS nsim FROM pgeo.feature f
    WHERE cardinality(targets) > 0 AND f.layer <> 'address' AND f.name_norm = ANY (targets)
    ORDER BY CASE WHEN anchor IS NULL THEN 0 ELSE f.geom <-> anchor END
    LIMIT 40
  ),
  town AS (  -- the town itself, for fallbacks when nothing finer matches
    SELECT f.id FROM pgeo.feature f
    WHERE loc IS NOT NULL AND f.layer IN ('locality', 'localadmin') AND f.name_norm = loc
    LIMIT 2
  ),
  postal AS (
    SELECT f.id FROM pgeo.feature f WHERE pc IS NOT NULL AND f.layer = 'postalcode' AND f.postcode = pc
  ),
  -- Candidate scoring. "agree" = how well the result's town / ZIP match the query's (1 when
  -- the query gave neither). A town mismatch is penalized hard (Pelias finding F16).
  cand AS (
    SELECT e.id, 'exact'::text AS mt, 'point'::text AS acc,
           (0.35 + 0.35 * e.st_sim + 0.30 * 1.0)::double precision AS base, e.loc_sim, NULL::integer AS ihn,
           NULL::geometry AS igeom
    FROM addr_exact e
    UNION ALL
    SELECT i.lo_id, 'interpolated', 'point', 0.25 + 0.30 * i.st_sim + 0.25, i.loc_sim, hni,
           ST_LineInterpolatePoint(ST_MakeLine(i.g_lo, i.g_hi),
                                   (hni - i.n_lo)::double precision / nullif(i.n_hi - i.n_lo, 0))
    FROM interp i
    UNION ALL
    SELECT sf.id, 'fallback', 'centroid', 0.2 + 0.3 * sf.st_sim + 0.1, sf.loc_sim, NULL, NULL FROM street_fb sf
    UNION ALL
    SELECT n.id, CASE WHEN n.nsim >= 0.99 THEN 'exact' ELSE 'fallback' END, 'centroid',
           least(n.nsim, 1.0), NULL, NULL, NULL
    FROM (SELECT * FROM names UNION ALL SELECT * FROM names_exact) n
    UNION ALL
    SELECT t.id, 'fallback', 'centroid', 0.45, NULL, NULL, NULL FROM town t
    UNION ALL
    SELECT p.id, CASE WHEN addr_intent THEN 'fallback' ELSE 'exact' END, 'centroid',
           CASE WHEN addr_intent THEN 0.4 ELSE 1.0 END, NULL, NULL, NULL FROM postal p
  ),
  scored AS (
    SELECT DISTINCT ON (c.id, c.ihn) c.*, f,
           -- agreement with the query's town / ZIP
           CASE WHEN loc IS NULL AND pc IS NULL THEN 1.0
                WHEN loc IS NOT NULL AND c.loc_sim IS NULL AND position(loc in coalesce(f.name_norm, '')) > 0 THEN 1.0
                ELSE greatest(
                  coalesce(c.loc_sim, CASE WHEN loc IS NULL THEN 0 ELSE
                    greatest(similarity(coalesce(f.locality_norm, ''), loc),
                             similarity(coalesce(f.postal_locality_norm, ''), loc)) END),
                  CASE WHEN pc IS NOT NULL AND f.postcode = pc THEN 1.0 ELSE 0 END,
                  -- an admin result is its own town, unless the query named a place in another
                  -- town ("long pond, new city" is not the locality Long Pond)
                  CASE WHEN f.layer IN ('locality', 'localadmin', 'postalcode') AND qn IS NULL THEN 1.0 ELSE 0 END,
                  -- near the named town counts as agreement for places and venues: the query's
                  -- town is often a village or the nearest town, not the containing one
                  -- ("Calvary Bible Church, Stratton" is in Eustis); 1 km 0.85, 3 km 0.6
                  CASE WHEN townpt IS NOT NULL AND f.layer <> 'address'
                       THEN exp(-ST_Distance(f.geom::geography, townpt::geography) / 6000.0) ELSE 0 END)
           END AS agree
    FROM cand c JOIN pgeo.feature f ON f.id = c.id
    WHERE (nofilter AND rect IS NULL) OR geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories)
    ORDER BY c.id, c.ihn, c.base DESC
  ),
  final AS (
    SELECT s.*,
           least(1.0, s.base * (0.4 + 0.6 * least(1.0, s.agree)))::real AS conf
    FROM scored s
  ),
  hits AS (
  SELECT d.h FROM (
  SELECT z.h, row_number() OVER (
           PARTITION BY (z.h).label,
                        CASE (z.h).layer WHEN 'localadmin' THEN 'locality' ELSE (z.h).layer END
           ORDER BY (z.h).score DESC, CASE (z.h).layer WHEN 'locality' THEN 0 ELSE 1 END,
                    CASE (z.h).source WHEN 'whosonfirst' THEN 0 WHEN 'openaddresses' THEN 1 ELSE 2 END) AS dup
  FROM final fi
  -- The spheroidal distance to the focus, once. It was computed twice per row: for the reported
  -- distance, and again inside focus_boost(), whose formula - exp(-(metres / 50000)), 0 without
  -- a focus - is written out below. An interpolated row is measured from its interpolated point.
  CROSS JOIN LATERAL (
    SELECT CASE WHEN focus IS NULL THEN NULL
                ELSE ST_Distance(coalesce(fi.igeom, (fi.f).geom)::geography, focus::geography) END AS dm) g0
  CROSS JOIN LATERAL (SELECT g0.dm, CASE WHEN g0.dm IS NULL THEN 0 ELSE exp(-(g0.dm / 50000.0)) END AS boost) g
  CROSS JOIN LATERAL (
    SELECT CASE WHEN fi.ihn IS NULL THEN
      geocode.to_hit(fi.f, fi.conf, fi.mt, fi.acc, g.dm / 1000,
                     (fi.conf + 0.05 * (fi.f).importance + 0.1 * g.boost
                      + CASE (fi.f).source WHEN 'openaddresses' THEN 0.01 ELSE 0 END)::real)
    ELSE
      -- interpolated address: synthesize the row at the interpolated position
      ROW(NULL, 'interpolation:address:' || (fi.f).street_norm || ':' || fi.ihn || ':' || coalesce((fi.f).locality_norm, ''),
          'interpolation', 'address', (fi.f).street_norm || ':' || fi.ihn,
          fi.ihn || ' ' || (fi.f).street, fi.ihn::text, (fi.f).street, (fi.f).postcode,
          (fi.f).neighbourhood, (fi.f).locality, (fi.f).localadmin, (fi.f).county, (fi.f).region, (fi.f).region_a,
          fi.ihn || ' ' || (fi.f).street || coalesce(', ' || coalesce((fi.f).locality, (fi.f).localadmin), '') || ', ME, USA',
          NULL, NULL, ST_X(fi.igeom), ST_Y(fi.igeom), NULL, fi.conf, 'interpolated', 'point',
          g.dm / 1000,
          (fi.conf + 0.05 * (fi.f).importance + 0.1 * g.boost)::real,
          (fi.f).hier)::geocode.hit
    END AS h
  ) z
  WHERE fi.conf >= 0.3                         -- drop weak matches: misses return nothing
  ) d
  WHERE d.dup = 1                               -- one result per label (locality/localadmin, ZIP sources)
  ),
  -- Ambiguity: when several distinct places (~1 km grid) tie with the top confidence, the
  -- query cannot say which one is meant ("Mud Pond"). Their confidence is scaled down so it
  -- separates right answers from lucky guesses (calibration finding F28). Lower-ranked hits
  -- are capped at the reduced top confidence so confidence never rises down the list.
  top AS (SELECT max((h).confidence) AS c FROM hits),
  -- When a town is among the ties, only other towns count as rivals: a venue or GNIS point
  -- that shares the town's name ("Portland, Maine") is not a different answer.
  tied AS (SELECT hits.h FROM hits, top WHERE (hits.h).confidence >= top.c - 0.02),
  amb AS (
    SELECT count(DISTINCT (round((h).lon::numeric, 2), round((h).lat::numeric, 2))) AS n
    FROM tied
    WHERE NOT EXISTS (SELECT 1 FROM tied t WHERE (t.h).layer IN ('locality', 'localadmin'))
       OR (h).layer IN ('locality', 'localadmin')
  )
  SELECT (y.h2).*
  FROM hits CROSS JOIN top CROSS JOIN amb
  CROSS JOIN LATERAL (
    SELECT geocode.set_conf(hits.h,
             CASE WHEN amb.n <= 1 THEN (hits.h).confidence
                  WHEN (hits.h).confidence >= top.c - 0.02
                  THEN ((hits.h).confidence / (1 + 0.35 * (least(amb.n, 6) - 1)))::real
                  ELSE least((hits.h).confidence, top.c / (1 + 0.35 * (least(amb.n, 6) - 1)))::real
             END) AS h2
  ) y
  ORDER BY (y.h2).score DESC
  LIMIT lim;
END
$$;

-- ---------------------------------------------------------------------------------------
-- autocomplete: prefix search while typing, typo tolerant for longer input.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.autocomplete(
    p_text text,
    p_focus_lon double precision DEFAULT NULL, p_focus_lat double precision DEFAULT NULL,
    p_layers text[] DEFAULT NULL, p_sources text[] DEFAULT NULL,
    p_rect double precision[] DEFAULT NULL, p_size integer DEFAULT 10,
    p_circle double precision[] DEFAULT NULL, p_gid text DEFAULT NULL,
    p_categories text[] DEFAULT NULL, p_country text DEFAULT NULL)
RETURNS SETOF geocode.hit
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  q     text := geocode.norm(coalesce(p_text, ''));
  toks  text[] := CASE WHEN q = '' THEN ARRAY[]::text[] ELSE string_to_array(q, ' ') END;
  n     integer := cardinality(toks);
  -- raw tokens (same word boundaries as norm, which maps each word to one word)
  rtoks text[] := string_to_array(trim(regexp_replace(regexp_replace(lower(geocode.unaccent_i(coalesce(p_text, ''))),
                                  '[''’]', '', 'g'), '[^a-z0-9]+', ' ', 'g')), ' ');
  parts text[] := ARRAY[]::text[];
  i     integer;
  aligned boolean;
  tsq   tsquery;     -- what a row must satisfy
  tsq_idx tsquery;   -- what the GIN index is asked (see the short-prefix note below)
  split boolean := false;
  focus geometry := CASE WHEN p_focus_lon IS NOT NULL AND p_focus_lat IS NOT NULL
                         THEN ST_SetSRID(ST_MakePoint(p_focus_lon, p_focus_lat), 4326) END;
  rect  geometry := geocode.search_area(p_rect, p_circle);
  lim   integer := least(greatest(coalesce(p_size, 10), 1), 40);
  found integer := 0;
  -- No filter at all is the normal request. keep() holds an EXISTS, so it cannot be inlined, and
  -- it takes the whole 780-byte row: called per candidate it was 63% of the candidate stage while
  -- filtering nothing (docs/PERFORMANCE_OPTIMIZATION.md, F1). Decide once, here.
  nofilter boolean := (p_layers IS NULL OR cardinality(p_layers) = 0)
                  AND (p_sources IS NULL OR cardinality(p_sources) = 0)
                  AND rect IS NULL AND p_gid IS NULL
                  AND (p_categories IS NULL OR cardinality(p_categories) = 0);
BEGIN
  IF n = 0 OR NOT geocode.country_ok(p_country) THEN
    RETURN;
  END IF;
  -- All tokens must match; the last one as a prefix (the user is still typing it). norm()
  -- reads "st" before another word as "saint" ("St George"), but while typing
  -- "389 congress st portland" it is "street", so such a token matches either. The last
  -- token also matches its raw form, so "congress s" is not locked to "south". Tokens are
  -- [a-z0-9] only (norm), so the tsquery text cannot inject operators.
  aligned := cardinality(rtoks) = n;
  FOR i IN 1..n LOOP
    parts := parts || CASE
      WHEN i < n AND aligned AND toks[i] = 'saint' AND rtoks[i] = 'st' THEN '(saint | street)'
      WHEN i < n THEN toks[i]
      WHEN aligned AND rtoks[i] <> toks[i] THEN '(' || toks[i] || ':* | ' || rtoks[i] || ':*)'
      ELSE toks[i] || ':*' END;
  END LOOP;
  tsq := to_tsquery('simple', array_to_string(parts, ' & '));
  -- Short prefix (F4). GIN expands a prefix term to every token that starts with it and unions
  -- their posting lists: "s:*" includes "street", which is in nearly every address, and the
  -- selective words in the same query narrow nothing until that union is done ("12 main s":
  -- 43 ms in the index for 54 rows). So when the word being typed is one or two letters and
  -- there are complete words to search on, the index is asked for those alone and the prefix is
  -- tested on the rows that come back. Same rows: tsq implies tsq_idx, and the test runs before
  -- any sort or limit. It is written ts_match_vq(), not @@, so that the planner does not
  -- recognise it as an index condition and put it back in the scan.
  IF n >= 2 AND length(toks[n]) <= 2 THEN
    split := true;
    tsq_idx := to_tsquery('simple', array_to_string(parts[1:n-1], ' & '));
  ELSE
    tsq_idx := tsq;
  END IF;

  -- In every branch below the candidates are ranked on a plain expression and only the rows that
  -- survive the LIMIT are turned into hits (F2): to_hit() builds a 27-field record, it used to
  -- run for every candidate, and all but "size" of them were thrown away. The distance to the
  -- focus is computed once and serves both the reported distance and the focus boost, which is
  -- focus_boost()'s own formula written out: exp(-(metres / 50000)).
  IF n >= 2 AND toks[1] ~ '^\d+[a-z]?$' THEN
    -- Address mode: "389 cong..." -> exact house number, street prefix.
    RETURN QUERY
    WITH cand AS (
      SELECT f.id, f.importance,
             CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(f.geom::geography, focus::geography) END AS dm
      FROM pgeo.feature f
      WHERE f.layer = 'address' AND f.housenumber = toks[1] AND f.tokens @@ tsq_idx
        AND (NOT split OR ts_match_vq(f.tokens, tsq))
        AND (nofilter OR geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories))),
    top AS (
      SELECT c.id, c.dm,
             (c.importance + 0.3 * CASE WHEN c.dm IS NULL THEN 0 ELSE exp(-(c.dm / 50000.0)) END)::real AS sc
      FROM cand c ORDER BY sc DESC, c.id LIMIT lim)
    SELECT (geocode.to_hit(f, NULL, NULL, 'point', t.dm / 1000, t.sc)).*
    FROM top t JOIN pgeo.feature f ON f.id = t.id
    ORDER BY t.sc DESC, t.id;
    GET DIAGNOSTICS found = ROW_COUNT;
  ELSIF n = 1 AND length(q) <= 3 THEN
    -- Very short input: precomputed top features for the prefix. At most 25 rows, so there is
    -- nothing to gain by ranking before building.
    RETURN QUERY
    SELECT (z.h).*
    FROM pgeo.ac_prefix p JOIN pgeo.feature f ON f.id = p.feature_id
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, NULL, 'centroid', NULL,
             (f.importance + 0.3 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE p.prefix = q AND (nofilter OR geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories))
    ORDER BY (z.h).score DESC
    LIMIT lim;
    GET DIAGNOSTICS found = ROW_COUNT;
  ELSE
    -- Name mode. Unfiltered requests read the narrow table (F3); filtered ones need columns it
    -- does not have. Exactly one arm of each UNION ALL runs: the other's first condition is a
    -- constant false.
    RETURN QUERY
    WITH cand AS (
      (SELECT x.id, x.label, x.importance, x.name_norm, x.geom
       FROM pgeo.feature_ac x
       WHERE nofilter AND x.tokens @@ tsq_idx AND (NOT split OR ts_match_vq(x.tokens, tsq))
       ORDER BY x.importance DESC LIMIT 400)
      UNION ALL
      (SELECT x.id, x.label, x.importance, x.name_norm, x.geom
       FROM pgeo.feature x
       WHERE NOT nofilter AND x.tokens @@ tsq_idx AND (NOT split OR ts_match_vq(x.tokens, tsq))
         AND x.layer <> 'address'
         AND geocode.keep(x, p_layers, p_sources, rect, p_gid, p_categories)  -- filter before the cut
       ORDER BY x.importance DESC LIMIT 400)),
    ded AS (SELECT DISTINCT ON (c.label) c.* FROM cand c ORDER BY c.label, c.importance DESC),
    dist AS (SELECT d.*, CASE WHEN focus IS NULL THEN NULL
                              ELSE ST_Distance(d.geom::geography, focus::geography) END AS dm FROM ded d),
    top AS (
      SELECT t.id, t.dm,
             (t.importance + CASE WHEN t.name_norm LIKE q || '%' THEN 0.25 ELSE 0 END
              + 0.3 * CASE WHEN t.dm IS NULL THEN 0 ELSE exp(-(t.dm / 50000.0)) END)::real AS sc
      FROM dist t ORDER BY sc DESC LIMIT lim)
    SELECT (geocode.to_hit(f, NULL, NULL, 'centroid', t.dm / 1000, t.sc)).*
    FROM top t JOIN pgeo.feature f ON f.id = t.id
    ORDER BY t.sc DESC;
    GET DIAGNOSTICS found = ROW_COUNT;
  END IF;

  -- Typo tolerance: if prefix matching found too little, fall back to trigram similarity.
  --
  -- This is over half of autocomplete's CPU and fires on 69% of keystrokes, because "fewer than
  -- size" is the normal state of a good query, and much of what it appends is filler. Firing it
  -- only when the prefix match found nothing was tried, since that is what a typo usually looks
  -- like - and rejected, because it is not what a typo always looks like. "Walker Ci", one
  -- character off "Walker Corner", still prefix-matches one wrong row (Walker Heights Circle);
  -- the right town was fourth, among the "filler". One case of 3,360, and accuracy is the point
  -- of this engine, so the rule stands as it was (docs/PERFORMANCE_OPTIMIZATION.md, F5).
  IF found < lim AND length(q) >= 4 THEN
    PERFORM set_config('pg_trgm.similarity_threshold', '0.35', true);
    RETURN QUERY
    WITH cand AS (
      (SELECT x.id, x.label, x.layer, x.importance, x.name_norm, x.geom
       FROM pgeo.feature_ac x
       WHERE nofilter AND x.name_norm % q AND NOT (x.tokens @@ tsq))
      UNION ALL
      (SELECT x.id, x.label, x.layer, x.importance, x.name_norm, x.geom
       FROM pgeo.feature x
       WHERE NOT nofilter AND x.layer <> 'address' AND x.name_norm % q AND NOT (x.tokens @@ tsq))),
    ded AS (SELECT DISTINCT ON (c.label) c.* FROM cand c   -- one row per label (locality/localadmin)
            ORDER BY c.label, CASE c.layer WHEN 'locality' THEN 0 ELSE 1 END, c.importance DESC),
    -- Filters apply after the one-per-label cut here, not before it as in name mode: a label
    -- whose best row fails the filter is dropped, not replaced by its runner-up. That is how
    -- this branch has always behaved, and the order is kept so that its output is unchanged.
    kept AS (SELECT d.* FROM ded d
             WHERE nofilter OR EXISTS (SELECT 1 FROM pgeo.feature f WHERE f.id = d.id
                     AND geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories))),
    dist AS (SELECT d.*, CASE WHEN focus IS NULL THEN NULL
                              ELSE ST_Distance(d.geom::geography, focus::geography) END AS dm FROM kept d),
    top AS (
      SELECT t.id, t.dm,
             (similarity(t.name_norm, q) + 0.2 * t.importance
              + 0.2 * CASE WHEN t.dm IS NULL THEN 0 ELSE exp(-(t.dm / 50000.0)) END)::real AS sc
      FROM dist t ORDER BY sc DESC LIMIT lim - found)
    SELECT (geocode.to_hit(f, NULL, 'fallback', 'centroid', t.dm / 1000, t.sc)).*
    FROM top t JOIN pgeo.feature f ON f.id = t.id
    ORDER BY t.sc DESC;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------------------
-- reverse: nearest point features, plus the admin areas containing the point.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.reverse(
    p_lon double precision, p_lat double precision,
    p_layers text[] DEFAULT NULL, p_sources text[] DEFAULT NULL,
    p_radius_km double precision DEFAULT NULL, p_size integer DEFAULT 10,
    p_gid text DEFAULT NULL, p_categories text[] DEFAULT NULL, p_country text DEFAULT NULL)
RETURNS SETOF geocode.hit
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  pt     geometry := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);
  lim    integer := least(greatest(coalesce(p_size, 10), 1), 40);
  radius double precision := least(coalesce(p_radius_km, 50), 50) * 1000;
  point_layers text[] := CASE WHEN cardinality(p_layers) > 0
                              THEN ARRAY(SELECT unnest(p_layers) INTERSECT SELECT unnest(ARRAY['address','venue','street']))
                              ELSE ARRAY['address', 'venue', 'street'] END;
  admin_layers text[] := CASE WHEN cardinality(p_layers) > 0
                              THEN ARRAY(SELECT unnest(p_layers) INTERSECT
                                         SELECT unnest(ARRAY['neighbourhood','locality','localadmin','county','region','postalcode']))
                              ELSE ARRAY['neighbourhood','locality','localadmin','county','region'] END;
BEGIN
  IF NOT geocode.country_ok(p_country) THEN
    RETURN;
  END IF;
  RETURN QUERY
  WITH nearest AS (
    SELECT x AS f, ST_Distance(x.geom::geography, pt::geography) AS d
    FROM pgeo.feature x
    WHERE x.layer = ANY (point_layers)
      AND (p_sources IS NULL OR cardinality(p_sources) = 0 OR x.source = ANY (p_sources))
      AND (p_gid IS NULL AND p_categories IS NULL OR geocode.keep(x, NULL, NULL, NULL, p_gid, p_categories))
    ORDER BY x.geom <-> pt
    LIMIT lim * 3
  ),
  containing AS (
    SELECT f, 0::double precision AS d,
           array_position(ARRAY['neighbourhood','locality','localadmin','postalcode','county','region'], f.layer) AS o
    FROM pgeo.admin a JOIN pgeo.feature f ON f.admin_id = a.id
    WHERE cardinality(admin_layers) > 0 AND a.placetype = ANY (admin_layers)
      AND ST_Intersects(a.geom, pt)
      AND (p_sources IS NULL OR cardinality(p_sources) = 0 OR f.source = ANY (p_sources))
      -- boundary.gid, written inline: a function call here stopped the planner from using the
      -- admin_id index (13 s per request instead of milliseconds)
      AND (p_gid IS NULL OR f.gid = p_gid OR f.hier ->> 'county_gid' = p_gid OR f.hier ->> 'localadmin_gid' = p_gid
           OR p_gid = 'whosonfirst:country:85633793'
           OR EXISTS (SELECT 1 FROM geocode.region_ref r
                      WHERE p_gid = 'whosonfirst:region:' || r.wof_id))
  )
  SELECT (x.h).* FROM (
    SELECT geocode.to_hit(n.f, geocode.distance_confidence(n.d), NULL,
             CASE WHEN (n.f).layer = 'address' THEN 'point' ELSE 'centroid' END,
             n.d / 1000, 0::real) AS h, 0 AS grp, n.d AS ord
    FROM nearest n WHERE n.d <= radius
    UNION ALL
    SELECT geocode.to_hit(c.f, 1.0::real, NULL, 'centroid', 0, 0::real), 1, c.o
    FROM containing c
  ) x
  ORDER BY x.grp, x.ord
  LIMIT lim;
END
$$;

-- ---------------------------------------------------------------------------------------
-- place: lookup by Pelias gid.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.place(p_gids text[])
RETURNS SETOF geocode.hit
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT (z.h).*
  FROM pgeo.feature f
  CROSS JOIN LATERAL (SELECT geocode.to_hit(f, 1.0::real, 'exact',
           CASE WHEN f.layer = 'address' THEN 'point' ELSE 'centroid' END, NULL, 0::real) AS h) z
  WHERE f.gid = ANY (p_gids[1:40])
$$;
