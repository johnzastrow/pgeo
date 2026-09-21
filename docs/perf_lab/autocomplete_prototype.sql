CREATE SCHEMA IF NOT EXISTS perf_lab;
CREATE OR REPLACE FUNCTION perf_lab.autocomplete4(
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
  tsq   tsquery;
  tsq_idx tsquery;   -- what the GIN index is asked; tsq is what a row must satisfy
  split boolean := false;
  focus geometry := CASE WHEN p_focus_lon IS NOT NULL AND p_focus_lat IS NOT NULL
                         THEN ST_SetSRID(ST_MakePoint(p_focus_lon, p_focus_lat), 4326) END;
  rect  geometry := geocode.search_area(p_rect, p_circle);
  lim   integer := least(greatest(coalesce(p_size, 10), 1), 40);
  found integer := 0;
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
  -- A one- or two-letter prefix expands to every token that starts with it ("s:*" includes
  -- "street", which is in nearly every address), and GIN unions all of those posting lists
  -- before the selective words get to narrow anything. When there are complete words to search
  -- on, ask the index for those alone and test the prefix on the rows that come back. The
  -- result is the same set: tsq implies tsq_idx.
  IF n >= 2 AND length(toks[n]) <= 2 THEN
    split := true;
    tsq_idx := to_tsquery('simple', array_to_string(parts[1:n-1], ' & '));
  ELSE
    tsq_idx := tsq;
  END IF;

  IF n >= 2 AND toks[1] ~ '^\d+[a-z]?$' THEN
    -- Address mode: "389 cong..." -> exact house number, street prefix.
    RETURN QUERY
    SELECT (z.h).*
    FROM pgeo.feature f
    CROSS JOIN LATERAL (SELECT geocode.to_hit(f, NULL, NULL, 'point',
             CASE WHEN focus IS NULL THEN NULL ELSE ST_Distance(f.geom::geography, focus::geography) / 1000 END,
             (f.importance + 0.3 * geocode.focus_boost(f.geom, focus))::real) AS h) z
    WHERE f.layer = 'address' AND f.housenumber = toks[1] AND f.tokens @@ tsq_idx
      AND (NOT split OR ts_match_vq(f.tokens, tsq))
      AND (nofilter OR geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories))
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
    WHERE p.prefix = q AND (nofilter OR geocode.keep(f, p_layers, p_sources, rect, p_gid, p_categories))
    ORDER BY (z.h).score DESC
    LIMIT lim;
    GET DIAGNOSTICS found = ROW_COUNT;
  ELSE
    RETURN QUERY
    WITH cand AS (
      (SELECT x.id, x.label, x.importance, x.name_norm, x.geom
       FROM perf_lab.feature_ac x
       WHERE nofilter AND x.tokens @@ tsq_idx AND (NOT split OR ts_match_vq(x.tokens, tsq))
       ORDER BY x.importance DESC LIMIT 400)
      UNION ALL
      (SELECT x.id, x.label, x.importance, x.name_norm, x.geom
       FROM pgeo.feature x
       WHERE NOT nofilter AND x.tokens @@ tsq_idx AND (NOT split OR ts_match_vq(x.tokens, tsq))
         AND x.layer <> 'address'
         AND geocode.keep(x, p_layers, p_sources, rect, p_gid, p_categories)
       ORDER BY x.importance DESC LIMIT 400)),
    ded AS (SELECT DISTINCT ON (c.label) c.* FROM cand c ORDER BY c.label, c.importance DESC),
    dist AS (SELECT d.*, CASE WHEN focus IS NULL THEN NULL
                              ELSE ST_Distance(d.geom::geography, focus::geography) END AS dm FROM ded d),
    top AS (SELECT t.id, t.dm,
                   (t.importance + CASE WHEN t.name_norm LIKE q || '%' THEN 0.25 ELSE 0 END
                    + 0.3 * CASE WHEN t.dm IS NULL THEN 0 ELSE exp(-(t.dm / 50000.0)) END)::real AS sc
            FROM dist t ORDER BY sc DESC LIMIT lim)
    SELECT (geocode.to_hit(f, NULL, NULL, 'centroid', t.dm / 1000, t.sc)).*
    FROM top t JOIN pgeo.feature f ON f.id = t.id
    ORDER BY t.sc DESC;
    GET DIAGNOSTICS found = ROW_COUNT;
  END IF;

  -- Typo tolerance: if prefix matching found too little, fall back to trigram similarity.
  IF found < lim AND length(q) >= 4 THEN
    PERFORM set_config('pg_trgm.similarity_threshold', '0.35', true);
    RETURN QUERY
    WITH cand AS (
      (SELECT x.id, x.label, x.layer, x.importance, x.name_norm, x.geom
       FROM perf_lab.feature_ac x
       WHERE nofilter AND x.name_norm % q AND NOT (x.tokens @@ tsq))
      UNION ALL
      (SELECT x.id, x.label, x.layer, x.importance, x.name_norm, x.geom
       FROM pgeo.feature x
       WHERE NOT nofilter AND x.layer <> 'address' AND x.name_norm % q AND NOT (x.tokens @@ tsq)
         AND geocode.keep(x, p_layers, p_sources, rect, p_gid, p_categories))),
    ded AS (SELECT DISTINCT ON (c.label) c.* FROM cand c
            ORDER BY c.label, CASE c.layer WHEN 'locality' THEN 0 ELSE 1 END, c.importance DESC),
    dist AS (SELECT d.*, CASE WHEN focus IS NULL THEN NULL
                              ELSE ST_Distance(d.geom::geography, focus::geography) END AS dm FROM ded d),
    top AS (SELECT t.id, t.dm,
                   (similarity(t.name_norm, q) + 0.2 * t.importance
                    + 0.2 * CASE WHEN t.dm IS NULL THEN 0 ELSE exp(-(t.dm / 50000.0)) END)::real AS sc
            FROM dist t ORDER BY sc DESC LIMIT lim - found)
    SELECT (geocode.to_hit(f, NULL, 'fallback', 'centroid', t.dm / 1000, t.sc)).*
    FROM top t JOIN pgeo.feature f ON f.id = t.id
    ORDER BY t.sc DESC;
  END IF;
END
$$;
