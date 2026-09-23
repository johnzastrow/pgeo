-- Build step 1b (search_path = pgeo_build, public): staging -> admin / feature_raw.

INSERT INTO admin (id, source, source_id, placetype, name, longname, abbr, population, parent_id, geom, centroid, bbox)
SELECT id, source, source_id, placetype, name, longname, abbr, population, parent_id,
       CASE WHEN geom_hex IS NOT NULL THEN
         ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromWKB(decode(geom_hex, 'hex')), 4326)), 3))
       END,
       ST_SetSRID(ST_MakePoint(lon, lat), 4326),
       CASE WHEN minlon IS NOT NULL THEN ARRAY[minlon, minlat, maxlon, maxlat] END
FROM stage_admin
-- Null island is missing data, not a location: eight Who's on First postcodes came through with
-- (0, 0) and were labelled ", ME, USA" 8,206 km off the coast of Africa. No build covers it.
WHERE lon IS NOT NULL AND lat IS NOT NULL AND NOT (lon = 0 AND lat = 0)
ON CONFLICT (id) DO NOTHING;

-- Admin areas are also searchable features (towns, counties, ZIPs, the state).
INSERT INTO feature_raw (source, layer, source_id, name, postcode, geom, bbox, admin_id, popularity)
SELECT a.source, a.placetype, a.source_id, a.name,
       CASE WHEN a.placetype = 'postalcode' THEN a.name END,
       a.centroid, a.bbox, a.id,
       least(0.2, ln(coalesce(a.population, 0) + 1) / 60)::real
FROM admin a;

-- The region, buffered by ~300 m and with the neighbouring countries taken back out, for
-- clipping every point source to the build. Built here rather than in 025_osm.sql because it is
-- needed whether or not OpenStreetMap is loaded; that file used to create it and now reuses it.
--
-- The buffer exists because the boundary generalises the coast, and without it piers, wharves
-- and island shoreline are dropped. Seaward that is exactly right. Across a land border it
-- reached into the other country, and a single-state build then labelled what it found with its
-- own state: New York held "Akwesasne Canada Post" 77 m outside the state as ", NY, USA", on a
-- Mohawk territory the border runs through. Subtracting Canada and Mexico removes that strip and
-- leaves the seaward buffer alone, there being nothing out there to subtract.
CREATE TEMP TABLE region_clip AS
WITH buffered AS (
  SELECT ST_Buffer(ST_Union(geom), 0.003) AS g FROM admin WHERE placetype = 'region'
),
-- Canada and Mexico, cut down to the region's envelope first so the difference below is a local
-- operation rather than one against the whole of Canada.
neighbour AS (
  SELECT ST_Union(ST_MakeValid(ST_Intersection(
           ST_MakeValid(ST_GeomFromWKB(decode(n.geom_hex, 'hex'), 4326)),
           ST_Envelope(b.g)))) AS g
  FROM stage_neighbour n, buffered b
  WHERE ST_Intersects(ST_MakeValid(ST_GeomFromWKB(decode(n.geom_hex, 'hex'), 4326)), b.g)
)
SELECT CASE
         WHEN nb.g IS NULL OR ST_IsEmpty(nb.g) THEN b.g
         ELSE ST_Difference(b.g, nb.g)
       END AS clip
FROM buffered b LEFT JOIN neighbour nb ON true;
CREATE INDEX region_clip_gix ON region_clip USING gist (clip);
ANALYZE region_clip;

INSERT INTO feature_raw (source, layer, source_id, name, housenumber, street, unit, postcode,
                         locality_hint, category, addendum, geom, popularity)
SELECT s.source, s.layer, s.source_id, s.name,
       nullif(s.housenumber, ''), nullif(s.street, ''), nullif(s.unit, ''),
       nullif(s.postcode, ''), nullif(s.locality_hint, ''),
       string_to_array(nullif(s.category, ''), '|'),
       nullif(s.addendum, '')::jsonb,
       ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326), s.popularity
FROM stage_point s
WHERE s.lon BETWEEN -180 AND 180 AND s.lat BETWEEN -90 AND 90 AND coalesce(s.name, '') <> ''
  AND NOT (s.lon = 0 AND s.lat = 0)
  -- Clipped to the region, as OpenStreetMap is. Filtering by the build's bounding box is not the
  -- same thing: a box around Arizona and Nevada admitted 297 GNIS features up to 66 km outside
  -- them, in Utah, New Mexico and Sonora, and 240 OpenAddresses rows. In a single-state build the
  -- county fallback then labels every one of them with the build's own state, so New York
  -- answered "Ringwood River, NY, USA" for a river in New Jersey.
  --
  -- ZCTAs are exempt. A ZCTA belongs to the state holding most of its land area, not the state
  -- holding its internal point, and that is deliberate: Maine owns 03579 on 846 km2 against New
  -- Hampshire's 611. Clipping them by point would undo the rule that assigns them.
  AND (s.source = 'zcta'
       OR EXISTS (SELECT 1 FROM region_clip c
                  WHERE ST_Intersects(c.clip, ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326))));

TRUNCATE stage_admin, stage_point;
