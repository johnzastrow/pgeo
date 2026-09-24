-- Build step 2 (search_path = pgeo_build, public): admin hierarchy by point-in-polygon
-- (the Pelias "pip" service equivalent), normalization, labels, importance, dedupe,
-- helper tables and indexes.

CREATE INDEX admin_geom_idx ON admin USING gist (geom);
CREATE INDEX admin_placetype_idx ON admin (placetype);
ANALYZE admin;

-- Point-in-polygon against whole admin polygons is the slowest thing the build does: a county
-- or a state is tens of thousands of vertices, and every candidate costs a full ST_Intersects.
-- ST_Subdivide cuts them into pieces of at most 128 vertices, so a probe touches a small piece
-- instead of the whole outline. Measured on New York, 200,372 points against 8,619 polygons:
-- 62.6 s with four probes against `admin`, 39.9 s with one probe, 7.8 s with one probe against
-- these pieces - eight times faster, and every one of the 200,372 answers identical.
-- `full_area` is the original polygon's area, because "smallest containing polygon" has to mean
-- the smallest whole one, not the smallest piece. Dropped before the swap: build-time only.
CREATE TABLE admin_parts AS
SELECT a.id, a.placetype, a.name, a.longname, a.source_id, a.parent_id,
       ST_Area(a.geom) AS full_area, ST_Subdivide(a.geom, 128) AS geom
FROM admin a
WHERE a.placetype IN ('neighbourhood', 'locality', 'localadmin', 'county', 'region');
CREATE INDEX admin_parts_geom_idx ON admin_parts USING gist (geom);
ANALYZE admin_parts;

-- Smallest containing polygon per placetype for each raw point: its name, and its WOF id
-- for the Pelias hierarchy fields (locality_gid, county_gid, ...).
-- The state a feature is in comes from its county's parent, which is how Who's on First records
-- it. A feature with no county - offshore, or in a sliver between county polygons - falls back to
-- whichever region polygon actually contains it, which the same probe answers.
--
-- It used to fall back to the build's own state whenever the build had exactly one, and that was
-- wrong for anything outside the state: New York labelled a New Jersey river and the Canadian
-- side of Akwesasne ", NY, USA" on the strength of being a single-state build. A feature outside
-- every region polygon now carries no state, which is the true answer. Nothing larger is loaded
-- to fall back to - the build holds states, not countries - so null is where it stops.
CREATE TEMP TABLE hier AS
SELECT r.rid,
  pip.nb_name AS neighbourhood, pip.lo_name AS locality, pip.la_name AS localadmin,
  -- the county's own name, suffix and all: "Cumberland County", but "Acadia Parish" in
  -- Louisiana and a borough or census area in Alaska. Both front ends used to append the
  -- word " County" here on the strength of Maine. Falls back to the bare name when Who's on
  -- First has no long form.
  coalesce(pip.co_longname, pip.co_name) AS county,
  coalesce(rg.name, pip_rg.name) AS region,
  coalesce(rg.abbr, pip_rg.abbr) AS region_a,
  coalesce(rg.wof_id, pip_rg.wof_id) AS region_wof,
  jsonb_strip_nulls(jsonb_build_object(
    'neighbourhood_gid', 'whosonfirst:neighbourhood:' || pip.nb_id,
    'locality_gid', 'whosonfirst:locality:' || pip.lo_id,
    'localadmin_gid', 'whosonfirst:localadmin:' || pip.la_id,
    'county_gid', 'whosonfirst:county:' || pip.co_id,
    -- county_ref holds Maine's counties only, and county names repeat across states (New York
    -- has a Franklin and a Washington too), so the state has to match as well or Maine's
    -- abbreviations would be attached to another state's counties.
    'county_a', (SELECT c.abbr FROM geocode.county_ref c
                 WHERE c.county = lower(pip.co_name) AND substr(c.fips, 1, 2) = rg.fips))) AS hier
FROM feature_raw r
-- One probe for all four placetypes: ask the index once, then keep the smallest whole polygon
-- of each kind among what came back.
LEFT JOIN LATERAL (
  SELECT max(name)      FILTER (WHERE placetype = 'neighbourhood') AS nb_name,
         max(source_id) FILTER (WHERE placetype = 'neighbourhood') AS nb_id,
         max(name)      FILTER (WHERE placetype = 'locality')      AS lo_name,
         max(source_id) FILTER (WHERE placetype = 'locality')      AS lo_id,
         max(name)      FILTER (WHERE placetype = 'localadmin')    AS la_name,
         max(source_id) FILTER (WHERE placetype = 'localadmin')    AS la_id,
         max(name)      FILTER (WHERE placetype = 'county')        AS co_name,
         max(longname)  FILTER (WHERE placetype = 'county')        AS co_longname,
         max(source_id) FILTER (WHERE placetype = 'county')        AS co_id,
         max(parent_id) FILTER (WHERE placetype = 'county')        AS co_parent,
         max(source_id) FILTER (WHERE placetype = 'region')        AS rg_id
  FROM (SELECT DISTINCT ON (p.placetype) p.placetype, p.name, p.longname, p.source_id, p.parent_id
        FROM admin_parts p
        WHERE ST_Intersects(p.geom, r.geom)
        ORDER BY p.placetype, p.full_area) one_each
) pip ON true
LEFT JOIN geocode.region_ref rg ON rg.wof_id = pip.co_parent
-- the region the point is actually inside, for features whose county lookup found nothing
LEFT JOIN geocode.region_ref pip_rg ON pip_rg.wof_id = pip.rg_id::bigint;

-- Address dedupe: OpenAddresses and OSM carry most Maine addresses twice. Keep one row per
-- (house number, street, town), preferring OpenAddresses (authoritative E911 points). Pelias removes such
-- duplicates at query time instead; the outcome for users is the same.
CREATE TEMP TABLE raw_ranked AS
WITH joined AS (
  SELECT r.*, h.neighbourhood, h.locality AS pip_locality, h.localadmin, h.county, h.hier,
         h.region, h.region_a, h.region_wof,
         geocode.norm(r.name) AS name_norm,
         geocode.norm(r.street) AS street_norm,
         geocode.hn_int(r.housenumber) AS hn_int,
         CASE WHEN r.layer = 'address' THEN
             lower(r.housenumber) || '|' || geocode.norm(r.street) || '|'
             || coalesce(geocode.norm(coalesce(h.locality, h.localadmin, r.locality_hint)), '')
           ELSE r.source || r.layer || r.source_id END AS dup_key
  FROM feature_raw r JOIN hier h USING (rid)
),
-- Two things share this step.
--
-- Rows that key alike but are far apart are not duplicates at all. Normalisation makes "18 N St"
-- and "18 North St" the same string, and Bangor has both, 5 km apart; merging them threw one
-- real address away. Clustering within the key at ~200 m keeps them as the separate buildings
-- they are, and still collects the copies of one address.
--
-- Among true duplicates, the majority point wins. OpenAddresses publishes one row per unit, so a
-- block of flats arrives as several rows at one coordinate and occasionally one row somewhere
-- else. Ordering only by source left the winner to whatever the table happened to hold first,
-- which put 101 Hancock St, Rumford 25 m off its seven agreeing siblings and made two builds of
-- the same data disagree on 10,769 rows.
clustered AS (
  SELECT j.*,
         CASE WHEN layer = 'address'
              THEN ST_ClusterDBSCAN(geom, eps := 0.002, minpoints := 1)
                     OVER (PARTITION BY dup_key)
         END AS dup_cluster
  FROM joined j
),
voted AS (
  SELECT c.*,
         count(*) OVER (PARTITION BY dup_key, dup_cluster, round(ST_Y(geom)::numeric, 6),
                        round(ST_X(geom)::numeric, 6)) AS point_votes
  FROM clustered c
)
SELECT v.*,
       row_number() OVER (
         PARTITION BY dup_key, dup_cluster
         ORDER BY CASE source WHEN 'openaddresses' THEN 0 WHEN 'openstreetmap' THEN 1 ELSE 2 END,
                  point_votes DESC,
                  source_id          -- last resort, so the same data always builds the same way
       ) AS dup_rank
FROM voted v;

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
    r.localadmin, r.county, r.region, r.region_a,
    -- Pelias-style label. The state segment is the feature's own, so a build covering several
    -- states labels each one correctly; it is dropped rather than guessed when unknown.
    CASE r.layer
      WHEN 'region' THEN r.name || ', USA'
      WHEN 'postalcode' THEN r.name || coalesce(', ' || coalesce(r.pip_locality, r.localadmin), '')
                             || coalesce(', ' || r.region_a, '') || ', USA'
      WHEN 'county' THEN r.name || coalesce(', ' || r.region_a, '') || ', USA'
      WHEN 'locality' THEN r.name || coalesce(', ' || r.region_a, '') || ', USA'
      WHEN 'localadmin' THEN r.name || coalesce(', ' || r.region_a, '') || ', USA'
      ELSE r.name || coalesce(', ' || coalesce(r.pip_locality, r.localadmin, r.locality_hint, r.county), '')
           || coalesce(', ' || r.region_a, '') || ', USA'
    END,
    r.category, r.addendum, r.geom, r.bbox, r.admin_id,
    -- the country is the same for every feature; the region is the feature's own
    r.hier || jsonb_strip_nulls(jsonb_build_object(
      'region_gid', CASE WHEN r.region_wof IS NOT NULL
                         THEN 'whosonfirst:region:' || r.region_wof END)),
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

-- feature_ac: the narrow copy of the non-address features that autocomplete matches names
-- against, as street_name is for streets. Ranking reads importance, name_norm, label, geom and
-- tokens; in "feature" those sit behind some twenty variable-length columns of a 780-byte row,
-- and PostgreSQL cannot jump to an attribute that follows a variable-length one - it walks them,
-- for every candidate. Here they come first, in a row a quarter the size, so a prefix with
-- 12,559 candidates visits 2,912 heap pages instead of 7,809 (docs/PERFORMANCE_OPTIMIZATION.md,
-- F3). A request that carries filters needs columns this table lacks, and reads "feature".
--
-- It MUST be made with CREATE TABLE AS ... ORDER BY ctid, not CREATE TABLE + INSERT. Where
-- candidates tie - two sources for one pond with the same label and importance, two towns with
-- the same score - the survivor is decided by the order rows reach the sort, which is physical
-- order. CREATE TABLE AS bulk-appends, so the copy keeps the big table's order exactly and ties
-- fall the same way whichever table a query reads. INSERT consults the free-space map and
-- backfills earlier pages: it left 660 of 203,399 rows out of place, and that alone changed 12
-- of 4,018 golden autocomplete results. pgeo/tests/test_feature_ac_sql.py guards the order.
CREATE TABLE feature_ac AS
SELECT id, importance, geom, layer, label, name_norm, tokens
FROM feature WHERE layer <> 'address' ORDER BY ctid;
ALTER TABLE feature_ac ADD PRIMARY KEY (id);

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
CREATE INDEX feature_ac_tokens_idx ON feature_ac USING gin (tokens);
CREATE INDEX feature_ac_name_trgm_idx ON feature_ac USING gin (name_norm gin_trgm_ops);

ANALYZE feature;
ANALYZE street_name;
ANALYZE ac_prefix;
ANALYZE feature_ac;
