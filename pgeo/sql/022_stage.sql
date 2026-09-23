-- Build step 1b (search_path = pgeo_build, public): staging -> admin / feature_raw.

INSERT INTO admin (id, source, source_id, placetype, name, abbr, population, parent_id, geom, centroid, bbox)
SELECT id, source, source_id, placetype, name, abbr, population, parent_id,
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

INSERT INTO feature_raw (source, layer, source_id, name, housenumber, street, unit, postcode,
                         locality_hint, category, addendum, geom, popularity)
SELECT source, layer, source_id, name, nullif(housenumber, ''), nullif(street, ''), nullif(unit, ''),
       nullif(postcode, ''), nullif(locality_hint, ''),
       string_to_array(nullif(category, ''), '|'),
       nullif(addendum, '')::jsonb,
       ST_SetSRID(ST_MakePoint(lon, lat), 4326), popularity
FROM stage_point
WHERE lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90 AND coalesce(name, '') <> ''
  AND NOT (lon = 0 AND lat = 0);

TRUNCATE stage_admin, stage_point;
