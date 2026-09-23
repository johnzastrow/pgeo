-- Build step 1c (search_path = pgeo_build, public): OpenStreetMap staging tables written by
-- ogr2ogr (osm_points, osm_lines, osm_polygons) -> feature_raw. Rules follow Pelias' OSM
-- importer: named POIs become venues, addr:* become addresses, named roads become streets.
--
-- Clipped to the region. OpenStreetMap is the one source with no claim to be in this build:
-- Who's on First comes from the region's own descendants, GNIS from the state's own file,
-- Overture is clipped in prep and ZCTAs are assigned by Census land area, but a Geofabrik
-- extract reaches past the border. A Maine build held 4,270 features outside Maine, among them
-- "22 Chemin Martin, Sainte-Anne-de-Madawaska" and "Gagetown" in New Brunswick, and
-- "Yarmouth Ferry Terminal" in Nova Scotia 118 km away - every one of them labelled ", ME, USA".
--
-- The polygon is Who's on First's, not the Census cartographic one, and buffered by ~300 m as
-- Overture's clip is. That matters: the cartographic outline generalises away Maine's islands,
-- while this keeps all 276 features on Peaks Island, Cliff Island and the Cranberry Isles. The
-- buffer keeps piers and shoreline; 2,007 features are dropped, almost all of them within 2 km
-- of the border.
CREATE TEMP TABLE region_clip AS
SELECT ST_Buffer(ST_Union(geom), 0.003) AS clip FROM admin WHERE placetype = 'region';
CREATE INDEX region_clip_gix ON region_clip USING gist (clip);
ANALYZE region_clip;

-- Venues from nodes and from areas (point on surface).
WITH poi AS (
  SELECT 'node/' || osm_id AS sid, name, geom AS g, amenity, shop, tourism, leisure, office, craft,
         historic, healthcare, "natural", aeroway, railway, public_transport, place, man_made, sport
  FROM osm_points
  UNION ALL
  SELECT CASE WHEN osm_way_id IS NOT NULL THEN 'way/' || osm_way_id ELSE 'relation/' || osm_id END,
         name, ST_PointOnSurface(geom), amenity, shop, tourism, leisure, office, craft,
         historic, healthcare, "natural", aeroway, railway, public_transport, place, man_made, sport
  FROM osm_polygons
)
INSERT INTO feature_raw (source, layer, source_id, name, category, geom, popularity)
SELECT 'openstreetmap', 'venue', sid, name,
       array_remove(ARRAY[
         CASE WHEN amenity IS NOT NULL THEN 'amenity=' || amenity END,
         CASE WHEN shop IS NOT NULL THEN 'shop=' || shop END,
         CASE WHEN tourism IS NOT NULL THEN 'tourism=' || tourism END,
         CASE WHEN leisure IS NOT NULL THEN 'leisure=' || leisure END,
         CASE WHEN office IS NOT NULL THEN 'office=' || office END,
         CASE WHEN craft IS NOT NULL THEN 'craft=' || craft END,
         CASE WHEN historic IS NOT NULL THEN 'historic=' || historic END,
         CASE WHEN healthcare IS NOT NULL THEN 'healthcare=' || healthcare END,
         CASE WHEN "natural" IN ('peak', 'bay', 'beach', 'cape', 'water', 'island', 'wood') THEN 'natural=' || "natural" END,
         CASE WHEN aeroway IN ('aerodrome', 'terminal') THEN 'aeroway=' || aeroway END,
         CASE WHEN railway IN ('station', 'halt') THEN 'railway=' || railway END,
         CASE WHEN public_transport = 'station' THEN 'public_transport=station' END,
         CASE WHEN sport IS NOT NULL THEN 'sport=' || sport END], NULL),
       g,
       CASE WHEN aeroway = 'aerodrome' OR amenity IN ('hospital', 'university', 'college')
                 OR tourism IN ('attraction', 'museum') THEN 0.2
            WHEN amenity IS NOT NULL OR tourism IS NOT NULL THEN 0.05 ELSE 0 END
FROM poi, region_clip
WHERE name IS NOT NULL AND name <> '' AND ST_IsValid(g) AND ST_Intersects(g, region_clip.clip)
  AND (amenity IS NOT NULL OR shop IS NOT NULL OR tourism IS NOT NULL OR leisure IS NOT NULL
       OR office IS NOT NULL OR craft IS NOT NULL OR historic IS NOT NULL OR healthcare IS NOT NULL
       OR "natural" IN ('peak', 'bay', 'beach', 'cape', 'water', 'island', 'wood')
       OR aeroway IN ('aerodrome', 'terminal') OR railway IN ('station', 'halt')
       OR public_transport = 'station' OR sport IS NOT NULL);

-- Addresses from nodes and building areas.
WITH a AS (
  SELECT 'node/' || osm_id AS sid, addr_housenumber AS hn, addr_street AS st, addr_postcode AS pc,
         addr_city AS city, geom AS g FROM osm_points
  UNION ALL
  SELECT CASE WHEN osm_way_id IS NOT NULL THEN 'way/' || osm_way_id ELSE 'relation/' || osm_id END,
         addr_housenumber, addr_street, addr_postcode, addr_city, ST_PointOnSurface(geom)
  FROM osm_polygons
)
INSERT INTO feature_raw (source, layer, source_id, name, housenumber, street, postcode, locality_hint, geom)
SELECT 'openstreetmap', 'address', sid, hn || ' ' || st, hn, st, pc, city, g
FROM a, region_clip
WHERE hn IS NOT NULL AND hn <> '' AND st IS NOT NULL AND st <> '' AND ST_IsValid(g)
  AND ST_Intersects(g, region_clip.clip);

-- Streets: named roads, merged per name within ~300 m clusters (like Pelias polylines).
WITH roads AS (
  SELECT l.osm_id, l.name, l.geom FROM osm_lines l, region_clip
  WHERE l.name IS NOT NULL AND l.name <> '' AND ST_Intersects(l.geom, region_clip.clip)
    AND l.highway IN (
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential',
    'service', 'living_street', 'pedestrian', 'road', 'motorway_link', 'trunk_link',
    'primary_link', 'secondary_link', 'tertiary_link', 'track')
),
clustered AS (
  SELECT osm_id, name, geom,
         ST_ClusterDBSCAN(geom, eps := 0.003, minpoints := 1) OVER (PARTITION BY name) AS cid
  FROM roads
),
merged AS (
  SELECT name, min(osm_id) AS sid, ST_LineMerge(ST_Union(geom)) AS g
  FROM clustered GROUP BY name, cid
)
INSERT INTO feature_raw (source, layer, source_id, name, street, geom, bbox)
SELECT 'openstreetmap', 'street', 'way/' || sid, name, name,
       ST_ClosestPoint(g, ST_Centroid(g)),
       ARRAY[ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g)]
FROM merged;

DROP TABLE osm_points, osm_lines, osm_polygons;
