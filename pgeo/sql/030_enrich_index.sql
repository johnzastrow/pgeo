-- Build step 2 (search_path = pgeo_build, public): admin hierarchy by point-in-polygon
-- (the Pelias "pip" service equivalent), normalization, labels, importance, dedupe,
-- helper tables and indexes.

CREATE INDEX admin_geom_idx ON admin USING gist (geom);
CREATE INDEX admin_placetype_idx ON admin (placetype);
ANALYZE admin;

-- Smallest containing polygon per placetype for each raw point: its name, and its WOF id
-- for the Pelias hierarchy fields (locality_gid, county_gid, ...).
CREATE TEMP TABLE hier AS
SELECT r.rid,
  nb.name AS neighbourhood, lo.name AS locality, la.name AS localadmin, co.name AS county,
  jsonb_strip_nulls(jsonb_build_object(
    'neighbourhood_gid', 'whosonfirst:neighbourhood:' || nb.source_id,
    'locality_gid', 'whosonfirst:locality:' || lo.source_id,
    'localadmin_gid', 'whosonfirst:localadmin:' || la.source_id,
    'county_gid', 'whosonfirst:county:' || co.source_id,
    'county_a', (SELECT c.abbr FROM geocode.county_ref c WHERE c.county = lower(co.name)))) AS hier
FROM feature_raw r
LEFT JOIN LATERAL (SELECT a.name, a.source_id FROM admin a WHERE a.placetype = 'neighbourhood'
                   AND ST_Intersects(a.geom, r.geom) ORDER BY ST_Area(a.geom) LIMIT 1) nb ON true
LEFT JOIN LATERAL (SELECT a.name, a.source_id FROM admin a WHERE a.placetype = 'locality'
                   AND ST_Intersects(a.geom, r.geom) ORDER BY ST_Area(a.geom) LIMIT 1) lo ON true
LEFT JOIN LATERAL (SELECT a.name, a.source_id FROM admin a WHERE a.placetype = 'localadmin'
                   AND ST_Intersects(a.geom, r.geom) ORDER BY ST_Area(a.geom) LIMIT 1) la ON true
LEFT JOIN LATERAL (SELECT a.name, a.source_id FROM admin a WHERE a.placetype = 'county'
                   AND ST_Intersects(a.geom, r.geom) ORDER BY ST_Area(a.geom) LIMIT 1) co ON true;

-- Address dedupe: OpenAddresses and OSM carry most Maine addresses twice. Keep one row per
-- (house number, street, town), preferring OpenAddresses (authoritative E911 points). Pelias removes such
-- duplicates at query time instead; the outcome for users is the same.
CREATE TEMP TABLE raw_ranked AS
SELECT r.*, h.neighbourhood, h.locality AS pip_locality, h.localadmin, h.county, h.hier,
       geocode.norm(r.name) AS name_norm,
       geocode.norm(r.street) AS street_norm,
       geocode.hn_int(r.housenumber) AS hn_int,
       row_number() OVER (
         PARTITION BY CASE WHEN r.layer = 'address' THEN
             lower(r.housenumber) || '|' || geocode.norm(r.street) || '|'
             || coalesce(geocode.norm(coalesce(h.locality, h.localadmin, r.locality_hint)), '')
           ELSE r.source || r.layer || r.source_id END
         ORDER BY CASE r.source WHEN 'openaddresses' THEN 0 WHEN 'openstreetmap' THEN 1 ELSE 2 END
       ) AS dup_rank
FROM feature_raw r JOIN hier h USING (rid);

INSERT INTO feature (
    id, gid, source, layer, source_id, name, housenumber, street, unit, postcode,
    neighbourhood, locality, localadmin, county, region, region_a, label, category, addendum,
    geom, bbox, admin_id, hier, importance, name_norm, street_norm, locality_norm, postal_locality_norm,
    hn_int, tokens)
SELECT
    row_number() OVER (ORDER BY r.layer, r.source, r.source_id),
    r.source || ':' || r.layer || ':' || r.source_id,
    r.source, r.layer, r.source_id, r.name, r.housenumber, r.street, r.unit, r.postcode,
    r.neighbourhood,
    coalesce(r.pip_locality, CASE WHEN r.localadmin IS NULL THEN r.locality_hint END),
    r.localadmin, r.county, 'Maine', 'ME',
    -- Pelias-style label
    CASE r.layer
      WHEN 'region' THEN 'Maine, USA'
      WHEN 'county' THEN r.name || ', ME, USA'
      WHEN 'locality' THEN r.name || ', ME, USA'
      WHEN 'localadmin' THEN r.name || ', ME, USA'
      WHEN 'postalcode' THEN r.name || coalesce(', ' || coalesce(r.pip_locality, r.localadmin), '') || ', ME, USA'
      ELSE r.name || coalesce(', ' || coalesce(r.pip_locality, r.localadmin, r.locality_hint, r.county), '') || ', ME, USA'
    END,
    r.category, r.addendum, r.geom, r.bbox, r.admin_id,
    -- the region and country are the same for every Maine feature
    r.hier || jsonb_build_object('region_gid', 'whosonfirst:region:85688769'),
    -- importance: layer prior, then modest boosts from population / popularity
    least(1.0, CASE r.layer
        WHEN 'region' THEN 1.0 WHEN 'county' THEN 0.85 WHEN 'locality' THEN 0.75
        WHEN 'localadmin' THEN 0.65 WHEN 'postalcode' THEN 0.6 WHEN 'neighbourhood' THEN 0.5
        WHEN 'venue' THEN 0.4 WHEN 'street' THEN 0.35 ELSE 0.3 END
      + coalesce(r.popularity, 0))::real,
    r.name_norm, r.street_norm,
    geocode.norm(coalesce(r.pip_locality, r.localadmin, r.locality_hint)),
    geocode.norm(r.locality_hint),
    r.hn_int,
    to_tsvector('simple', coalesce(r.name_norm, '') || ' ' ||
                coalesce(geocode.norm(coalesce(r.pip_locality, r.localadmin, r.locality_hint)), ''))
FROM raw_ranked r
WHERE r.dup_rank = 1;

-- Distinct streets per town (from addresses and street features), keyed both by the
-- WOF town and by the postal city the source gave (what people type).
INSERT INTO street_name (street_norm, locality_norm, street, locality, n_addresses)
SELECT street_norm, town, min(street), min(town_name), count(*) FILTER (WHERE layer = 'address')
FROM (
  SELECT street_norm, coalesce(locality_norm, '') AS town, street,
         coalesce(locality, localadmin) AS town_name, layer
  FROM feature WHERE street_norm IS NOT NULL AND street_norm <> ''
  UNION ALL
  SELECT street_norm, postal_locality_norm, street, postal_locality_norm, layer
  FROM feature
  WHERE street_norm IS NOT NULL AND street_norm <> '' AND postal_locality_norm IS NOT NULL
    AND postal_locality_norm <> coalesce(locality_norm, '')
) t
GROUP BY street_norm, town;

-- Known town names (lowercase), for the SQL rule parser (geocode.parse_rule).
CREATE TABLE town AS
SELECT DISTINCT lower(name) AS name FROM feature WHERE layer IN ('locality', 'localadmin');
ALTER TABLE town ADD PRIMARY KEY (name);

-- Top 25 non-address features per 1-3 character prefix of the name.
INSERT INTO ac_prefix (prefix, rank, feature_id)
SELECT prefix, rn, id FROM (
  SELECT p.prefix, f.id,
         row_number() OVER (PARTITION BY p.prefix ORDER BY f.importance DESC, length(f.name_norm), f.id) AS rn
  FROM feature f
  CROSS JOIN LATERAL (SELECT left(f.name_norm, n) AS prefix FROM generate_series(1, 3) n) p
  WHERE f.layer <> 'address' AND length(f.name_norm) >= 1
) t WHERE rn <= 25;

-- Indexes (the tuning log records experiments with alternatives).
CREATE UNIQUE INDEX feature_gid_idx ON feature (gid);
CREATE INDEX feature_geom_idx ON feature USING gist (geom);
CREATE INDEX feature_tokens_idx ON feature USING gin (tokens);
CREATE INDEX feature_name_trgm_idx ON feature USING gin (name_norm gin_trgm_ops) WHERE layer <> 'address';
CREATE INDEX feature_name_idx ON feature (name_norm text_pattern_ops) WHERE layer <> 'address';
CREATE INDEX feature_addr_idx ON feature (street_norm, locality_norm, hn_int) WHERE layer = 'address';
CREATE INDEX feature_addr_hn_idx ON feature (housenumber, street_norm) WHERE layer = 'address';
CREATE INDEX feature_addr_street_hn_idx ON feature (street_norm, hn_int) WHERE layer = 'address';
CREATE INDEX feature_street_idx ON feature (street_norm, locality_norm) WHERE layer = 'street';
CREATE INDEX feature_postcode_idx ON feature (postcode) WHERE layer = 'postalcode';
CREATE INDEX feature_layer_idx ON feature (layer);
-- Admin features by their admin row (3,289 rows): reverse joins containing polygons to their
-- features; without it every reverse request scanned the whole table.
CREATE INDEX feature_admin_idx ON feature (admin_id) WHERE admin_id IS NOT NULL;
CREATE INDEX street_name_trgm_idx ON street_name USING gin (street_norm gin_trgm_ops);
CREATE INDEX street_name_locality_idx ON street_name (locality_norm);

ANALYZE feature;
ANALYZE street_name;
ANALYZE ac_prefix;
