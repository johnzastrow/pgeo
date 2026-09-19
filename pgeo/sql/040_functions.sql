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
    confidence real, match_type text, accuracy text, distance_km double precision, score real
);

-- Focus-point boost: 1 at the focus, ~0.37 at 50 km, fading with distance.
CREATE OR REPLACE FUNCTION geocode.focus_boost(g geometry, focus geometry) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT CASE WHEN focus IS NULL THEN 0
       ELSE exp(-(ST_Distance(g::geography, focus::geography) / 50000.0)) END $$;

-- Shared filter: layers, sources, bounding rectangle.
CREATE OR REPLACE FUNCTION geocode.keep(f pgeo.feature, layers text[], sources text[], rect geometry)
RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT (layers IS NULL OR cardinality(layers) = 0 OR f.layer = ANY (layers))
         AND (sources IS NULL OR cardinality(sources) = 0 OR f.source = ANY (sources))
         AND (rect IS NULL OR ST_Intersects(f.geom, rect)) $$;

-- Copy of a hit with a different confidence.
CREATE OR REPLACE FUNCTION geocode.set_conf(h geocode.hit, c real) RETURNS geocode.hit
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ROW(h.id, h.gid, h.source, h.layer, h.source_id, h.name, h.housenumber, h.street, h.postcode,
              h.neighbourhood, h.locality, h.localadmin, h.county, h.region, h.region_a, h.label,
              h.category, h.addendum, h.lon, h.lat, h.bbox, c, h.match_type, h.accuracy,
              h.distance_km, h.score)::geocode.hit $$;

CREATE OR REPLACE FUNCTION geocode.to_hit(f pgeo.feature, conf real, mt text, acc text,
                                          dist_km double precision, score real)
RETURNS geocode.hit LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ROW(f.id, f.gid, f.source, f.layer, f.source_id, f.name, f.housenumber, f.street,
              f.postcode, f.neighbourhood, f.locality, f.localadmin, f.county, f.region, f.region_a,
              f.label, f.category, f.addendum, ST_X(f.geom), ST_Y(f.geom), f.bbox,
              conf, mt, acc, dist_km, score)::geocode.hit $$;

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
    p_rect double precision[] DEFAULT NULL, p_size integer DEFAULT 10)
RETURNS SETOF geocode.hit
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  qf    text := nullif(geocode.norm(coalesce(p_text, '')), '');      -- full query
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
  rect  geometry := CASE WHEN cardinality(p_rect) = 4
                         THEN ST_MakeEnvelope(p_rect[1], p_rect[2], p_rect[3], p_rect[4], 4326) END;
  lim   integer := least(greatest(coalesce(p_size, 10), 1), 40);
BEGIN
  PERFORM set_config('pg_trgm.similarity_threshold', '0.3', true);
  PERFORM set_config('pg_trgm.word_similarity_threshold', '0.5', true);
  -- Name strings to try: the parser's name part; the full text unless an address was parsed;
  -- a bare town ("Portland, Maine"). Parsers misread odd input ("T4 R9 WELS"), so the full
  -- text is the safety net.
  targets := ARRAY(SELECT DISTINCT t FROM unnest(ARRAY[
               qn,
               CASE WHEN NOT addr_intent THEN qf END,
               CASE WHEN NOT addr_intent AND st IS NULL AND hn IS NULL THEN loc END]) t
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
             greatest(similarity(f.name_norm, q.txt), 0.9 * word_similarity(q.txt, f.name_norm))::double precision AS nsim
      FROM pgeo.feature f
      WHERE f.layer <> 'address' AND f.name_norm % q.txt
      ORDER BY similarity(f.name_norm, q.txt) DESC
      LIMIT 60) n
    GROUP BY n.id
  ),
  names_exact AS (
    SELECT f.id, 1.0::double precision AS nsim FROM pgeo.feature f
    WHERE cardinality(targets) > 0 AND f.layer <> 'address' AND f.name_norm = ANY (targets)
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
           n.nsim, NULL, NULL, NULL
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
                  CASE WHEN f.layer IN ('locality', 'localadmin', 'postalcode') THEN 1.0 ELSE 0 END)
           END AS agree
    FROM cand c JOIN pgeo.feature f ON f.id = c.id
    WHERE geocode.keep(f, p_layers, p_sources, rect)
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
  CROSS JOIN LATERAL (
    SELECT CASE WHEN fi.ihn IS NULL THEN
      geocode.to_hit(fi.f, fi.conf, fi.mt, fi.acc,
                     CASE WHEN focus IS NULL THEN NULL
                          ELSE ST_Distance((fi.f).geom::geography, focus::geography) / 1000 END,
                     (fi.conf + 0.05 * (fi.f).importance + 0.1 * geocode.focus_boost((fi.f).geom, focus)
                      + CASE (fi.f).source WHEN 'openaddresses' THEN 0.01 ELSE 0 END)::real)
    ELSE
      -- interpolated address: synthesize the row at the interpolated position
      ROW(NULL, 'interpolation:address:' || (fi.f).street_norm || ':' || fi.ihn || ':' || coalesce((fi.f).locality_norm, ''),
          'interpolation', 'address', (fi.f).street_norm || ':' || fi.ihn,
          fi.ihn || ' ' || (fi.f).street, fi.ihn::text, (fi.f).street, (fi.f).postcode,
          (fi.f).neighbourhood, (fi.f).locality, (fi.f).localadmin, (fi.f).county, (fi.f).region, (fi.f).region_a,
          fi.ihn || ' ' || (fi.f).street || coalesce(', ' || coalesce((fi.f).locality, (fi.f).localadmin), '') || ', ME, USA',
          NULL, NULL, ST_X(fi.igeom), ST_Y(fi.igeom), NULL, fi.conf, 'interpolated', 'point',
          CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(fi.igeom::geography, focus::geography) / 1000 END,
          (fi.conf + 0.05 * (fi.f).importance + 0.1 * geocode.focus_boost(fi.igeom, focus))::real)::geocode.hit
    END AS h
  ) z
  WHERE fi.conf >= 0.3                         -- drop weak matches: misses return nothing
  ) d
  WHERE d.dup = 1                               -- one result per label (locality/localadmin, ZIP sources)
  ),
  -- Ambiguity: when several distinct places (~1 km grid) tie with the top confidence, the
  -- query cannot say which one is meant ("Mud Pond"). Their confidence is scaled down so it
  -- separates right answers from lucky guesses (calibration finding F28).
  top AS (SELECT max((h).confidence) AS c FROM hits),
  amb AS (
    SELECT count(DISTINCT (round((h).lon::numeric, 2), round((h).lat::numeric, 2))) AS n
    FROM hits, top WHERE (h).confidence >= top.c - 0.02
  )
  SELECT (y.h2).*
  FROM hits CROSS JOIN top CROSS JOIN amb
  CROSS JOIN LATERAL (
    SELECT geocode.set_conf(hits.h,
             CASE WHEN (hits.h).confidence >= top.c - 0.02 AND amb.n > 1
                  THEN ((hits.h).confidence / (1 + 0.35 * (least(amb.n, 6) - 1)))::real
                  ELSE (hits.h).confidence END) AS h2
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
    p_rect double precision[] DEFAULT NULL, p_size integer DEFAULT 10)
RETURNS SETOF geocode.hit
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  q     text := geocode.norm(coalesce(p_text, ''));
  toks  text[] := CASE WHEN q = '' THEN ARRAY[]::text[] ELSE string_to_array(q, ' ') END;
  n     integer := cardinality(toks);
  tsq   tsquery;
  focus geometry := CASE WHEN p_focus_lon IS NOT NULL AND p_focus_lat IS NOT NULL
                         THEN ST_SetSRID(ST_MakePoint(p_focus_lon, p_focus_lat), 4326) END;
  rect  geometry := CASE WHEN cardinality(p_rect) = 4
                         THEN ST_MakeEnvelope(p_rect[1], p_rect[2], p_rect[3], p_rect[4], 4326) END;
  lim   integer := least(greatest(coalesce(p_size, 10), 1), 40);
  found integer := 0;
BEGIN
  IF n = 0 THEN
    RETURN;
  END IF;
  -- all tokens must match; the last one as a prefix (the user is still typing it)
  tsq := to_tsquery('simple',
           array_to_string(toks[1:n - 1], ' & ')
           || CASE WHEN n > 1 THEN ' & ' ELSE '' END || toks[n] || ':*');

  IF n >= 2 AND toks[1] ~ '^\d+[a-z]?$' THEN
    -- Address mode: "389 cong..." -> exact house number, street prefix.
    RETURN QUERY
    SELECT (z.h).*
    FROM pgeo.feature f
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, NULL, 'point',
             CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(f.geom::geography, focus::geography) / 1000 END,
             (f.importance + 0.3 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE f.layer = 'address' AND f.housenumber = toks[1] AND f.tokens @@ tsq
      AND geocode.keep(f, p_layers, p_sources, rect)
    ORDER BY (z.h).score DESC, f.id
    LIMIT lim;
    GET DIAGNOSTICS found = ROW_COUNT;
  ELSIF n = 1 AND length(q) <= 3 THEN
    -- Very short input: precomputed top features for the prefix.
    RETURN QUERY
    SELECT (z.h).*
    FROM pgeo.ac_prefix p JOIN pgeo.feature f ON f.id = p.feature_id
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, NULL, 'centroid', NULL,
             (f.importance + 0.3 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE p.prefix = q AND geocode.keep(f, p_layers, p_sources, rect)
    ORDER BY (z.h).score DESC
    LIMIT lim;
    GET DIAGNOSTICS found = ROW_COUNT;
  ELSE
    RETURN QUERY
    SELECT (z.h).*
    FROM (SELECT DISTINCT ON (y.label) y.* FROM
            (SELECT * FROM pgeo.feature x WHERE x.tokens @@ tsq AND x.layer <> 'address'
             ORDER BY x.importance DESC LIMIT 400) y
          ORDER BY y.label, y.importance DESC) f
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, NULL, 'centroid',
             CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(f.geom::geography, focus::geography) / 1000 END,
             (f.importance
              + CASE WHEN f.name_norm LIKE q || '%' THEN 0.25 ELSE 0 END
              + 0.3 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE geocode.keep(f, p_layers, p_sources, rect)
    ORDER BY (z.h).score DESC
    LIMIT lim;
    GET DIAGNOSTICS found = ROW_COUNT;
  END IF;

  -- Typo tolerance: if prefix matching found too little, fall back to trigram similarity.
  IF found < lim AND length(q) >= 4 THEN
    PERFORM set_config('pg_trgm.similarity_threshold', '0.35', true);
    RETURN QUERY
    SELECT (z.h).*
    FROM pgeo.feature f
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, 'fallback', 'centroid',
             CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(f.geom::geography, focus::geography) / 1000 END,
             (similarity(f.name_norm, q) + 0.2 * f.importance
              + 0.2 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE f.layer <> 'address' AND f.name_norm % q AND NOT (f.tokens @@ tsq)
      AND geocode.keep(f, p_layers, p_sources, rect)
    ORDER BY (z.h).score DESC
    LIMIT lim - found;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------------------
-- reverse: nearest point features, plus the admin areas containing the point.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.reverse(
    p_lon double precision, p_lat double precision,
    p_layers text[] DEFAULT NULL, p_sources text[] DEFAULT NULL,
    p_radius_km double precision DEFAULT NULL, p_size integer DEFAULT 10)
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
  RETURN QUERY
  WITH nearest AS (
    SELECT x AS f, ST_Distance(x.geom::geography, pt::geography) AS d
    FROM pgeo.feature x
    WHERE x.layer = ANY (point_layers)
      AND (p_sources IS NULL OR cardinality(p_sources) = 0 OR x.source = ANY (p_sources))
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
