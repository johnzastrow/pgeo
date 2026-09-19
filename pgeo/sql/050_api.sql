-- Phase 13: the whole API in SQL (core + contrib + PostGIS only).
-- geocode.parse_rule()   PL/pgSQL port of the rule parser (pgeo/src/pgeo/api/parse.py)
-- geocode_api.v1_*       Pelias-compatible endpoints returning complete GeoJSON (jsonb)
-- Exposed over HTTP by a logic-free gateway (PostgREST); only schema geocode_api is exposed.

-- Recreated on every apply: holds only these functions (argument names can change, which
-- CREATE OR REPLACE cannot do); the loader re-grants access afterwards.
DROP SCHEMA IF EXISTS geocode_api CASCADE;
CREATE SCHEMA geocode_api;

-- ---------------------------------------------------------------------------------------
-- Rule parser: house number, street, town (known towns from pgeo.town, or the last comma
-- part after "number street"), ZIP, state; otherwise a place name.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.parse_rule(p_text text,
    OUT name text, OUT housenumber text, OUT street text, OUT locality text,
    OUT postcode text, OUT state text)
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  s      text := coalesce(p_text, '');
  parts  text[];
  words  text[];
  head   text;
  m      text[];
  n      integer;
  maxw   integer := 4;
  cand   text;
BEGIN
  s := regexp_replace(s, '\m(apt|apartment|unit|ste|suite)\M\.?\s*[[:alnum:]-]+|#\s*[[:alnum:]-]+', ' ', 'gi');
  m := regexp_match(s, '\m(\d{5})(-\d{4})?\M');
  IF m IS NOT NULL THEN
    postcode := m[1];
    s := regexp_replace(s, '\m\d{5}(-\d{4})?\M', ' ');
  END IF;
  parts := ARRAY(SELECT trim(x) FROM unnest(string_to_array(s, ',')) x WHERE trim(x) <> '');
  words := regexp_split_to_array(trim(array_to_string(parts, ' ')), '\s+');
  IF cardinality(words) > 0 AND lower(rtrim(words[cardinality(words)], '.')) IN ('me', 'maine') THEN
    state := 'ME';
    words := words[1:cardinality(words) - 1];
    IF cardinality(parts) > 0 AND lower(rtrim(parts[cardinality(parts)], '.')) IN ('me', 'maine') THEN
      parts := parts[1:cardinality(parts) - 1];
    END IF;
  END IF;

  IF cardinality(parts) >= 2 AND (
       EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = lower(parts[cardinality(parts)]))
       OR parts[1] ~ '^\s*\d{1,6}[a-zA-Z]?(\s*-\s*\d{1,6})?\s+\S') THEN
    locality := parts[cardinality(parts)];
    head := array_to_string(parts[1:cardinality(parts) - 1], ', ');
  ELSE
    head := array_to_string(words, ' ');
    FOR n IN REVERSE least(maxw, cardinality(words))..1 LOOP
      cand := lower(array_to_string(words[cardinality(words) - n + 1:cardinality(words)], ' '));
      IF EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = cand) THEN
        locality := array_to_string(words[cardinality(words) - n + 1:cardinality(words)], ' ');
        head := array_to_string(words[1:cardinality(words) - n], ' ');
        EXIT;
      END IF;
    END LOOP;
  END IF;
  head := trim(both ' ,' from coalesce(head, ''));
  m := regexp_match(head, '^\s*(\d{1,6}[a-zA-Z]?)(?:\s*-\s*\d{1,6})?\s+(.+)$');
  IF m IS NOT NULL THEN
    housenumber := m[1];
    street := trim(both ' ,' from m[2]);
  ELSIF head <> '' AND locality IS NOT NULL THEN
    name := head;
  END IF;
END
$$;

-- Optional libpostal (third-party pgsql-postal): used only when installed and requested.
CREATE OR REPLACE FUNCTION geocode.parse_postal(p_text text) RETURNS jsonb
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE r jsonb;
BEGIN
  IF to_regproc('postal_parse') IS NULL THEN
    RETURN NULL;
  END IF;
  EXECUTE 'SELECT postal_parse($1)::jsonb' INTO r USING p_text;
  RETURN r;
END
$$;

-- ---------------------------------------------------------------------------------------
-- Pelias GeoJSON
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.feature_json(h geocode.hit) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'type', 'Feature',
    'geometry', jsonb_build_object('type', 'Point', 'coordinates', jsonb_build_array(h.lon, h.lat)),
    'properties', jsonb_build_object(
      'id', h.source_id, 'gid', h.gid, 'layer', h.layer, 'source', h.source, 'source_id', h.source_id,
      'name', h.name, 'housenumber', h.housenumber, 'street', h.street, 'postalcode', h.postcode,
      'confidence', round(h.confidence::numeric, 3), 'match_type', h.match_type, 'accuracy', h.accuracy,
      'distance', round(h.distance_km::numeric, 3),
      'country', 'United States', 'country_a', 'USA', 'region', h.region, 'region_a', h.region_a,
      'county', h.county, 'localadmin', h.localadmin, 'locality', h.locality,
      'neighbourhood', h.neighbourhood, 'label', h.label,
      'category', to_jsonb(h.category), 'addendum', h.addendum),
    'bbox', to_jsonb(h.bbox)))
$$;

CREATE OR REPLACE FUNCTION geocode.envelope(p_query jsonb, p_features jsonb, p_errors text[] DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'geocoding', jsonb_build_object(
      'version', '0.2',
      'attribution', 'pgeo (PostgreSQL/PostGIS): OSM, OpenAddresses, WOF, USGS GNIS, US Census, Overture',
      'query', p_query,
      'engine', jsonb_build_object('name', 'pgeo-sql', 'author', 'pelias_maine', 'version', '0.1.0'),
      'timestamp', (extract(epoch FROM clock_timestamp()) * 1000)::bigint,
      'errors', to_jsonb(p_errors)),
    'type', 'FeatureCollection',
    'features', coalesce(p_features, '[]'::jsonb),
    'bbox', (SELECT jsonb_build_array(min((f -> 'geometry' -> 'coordinates' ->> 0)::float8),
                                      min((f -> 'geometry' -> 'coordinates' ->> 1)::float8),
                                      max((f -> 'geometry' -> 'coordinates' ->> 0)::float8),
                                      max((f -> 'geometry' -> 'coordinates' ->> 1)::float8))
             FROM jsonb_array_elements(coalesce(p_features, '[]'::jsonb)) f
             HAVING count(*) > 0)))
$$;

-- Validation helpers: invalid input raises a clear error that the API returns as 400.
CREATE OR REPLACE FUNCTION geocode.check_text(v text, field text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF v IS NULL OR btrim(v) = '' THEN RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = format('missing param %s', field); END IF;
  IF length(v) > 200 THEN RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = format('%s is longer than 200 characters', field); END IF;
  RETURN btrim(v);
END $$;

CREATE OR REPLACE FUNCTION geocode.check_list(v text, allowed text[], field text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE out text[];
BEGIN
  IF v IS NULL OR v = '' THEN RETURN NULL; END IF;
  out := ARRAY(SELECT CASE lower(btrim(x)) WHEN 'oa' THEN 'openaddresses' WHEN 'osm' THEN 'openstreetmap'
                                           WHEN 'wof' THEN 'whosonfirst' ELSE lower(btrim(x)) END
               FROM unnest(string_to_array(v, ',')) x);
  IF field = 'layers' AND 'coarse' = ANY (out) THEN
    out := array_remove(out, 'coarse') || ARRAY['neighbourhood','locality','localadmin','county','region','postalcode'];
  END IF;
  IF NOT out <@ allowed THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = format('invalid %s: %s', field, v);
  END IF;
  RETURN out;
END $$;

CREATE OR REPLACE FUNCTION geocode.check_range(v double precision, lo double precision, hi double precision, field text)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF v IS NOT NULL AND (v < lo OR v > hi OR v = 'NaN'::float8) THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = format('%s must be between %s and %s', field, lo, hi);
  END IF;
  RETURN v;
END $$;

-- ---------------------------------------------------------------------------------------
-- Endpoints. Argument names are the LAST segment of the Pelias parameter names
-- (focus.point.lat -> lat, boundary.rect.min_lon -> min_lon, point.lat -> lat,
-- boundary.circle.radius -> radius): PostgREST keeps only the last segment of a dotted
-- query key, so Pelias-style URLs map onto these arguments with no rewriting. Within each
-- endpoint the last segments do not collide.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode_api.v1_search(
    text text,
    size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    min_lon double precision DEFAULT NULL, min_lat double precision DEFAULT NULL,
    max_lon double precision DEFAULT NULL, max_lat double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL, parser text DEFAULT 'rule')
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  t   text := geocode.check_text(v1_search.text, 'text');
  p   record;
  lp  jsonb;
  rect double precision[];
  feats jsonb;
BEGIN
  PERFORM geocode.check_range(lat, -90, 90, 'focus.point.lat');
  PERFORM geocode.check_range(lon, -180, 180, 'focus.point.lon');
  IF min_lon IS NOT NULL THEN
    rect := ARRAY[min_lon, min_lat, max_lon, max_lat];
  END IF;
  p := geocode.parse_rule(t);
  IF parser = 'postal' THEN
    lp := geocode.parse_postal(t);
    IF lp IS NOT NULL THEN
      -- libpostal components; keep the rule parser's street when libpostal found none
      p := ROW(lp ->> 'house',
               coalesce(lp ->> 'house_number', p.housenumber),
               coalesce(lp ->> 'road', CASE WHEN lp ? 'house_number' THEN p.street END),
               coalesce(lp ->> 'city', p.locality),
               coalesce(lp ->> 'postcode', p.postcode), lp ->> 'state');
    END IF;
  END IF;
  SELECT jsonb_agg(geocode.feature_json(h) ORDER BY h.score DESC) INTO feats
  FROM geocode.search(t, p.name, p.housenumber, p.street, p.locality, p.postcode,
                      lon, lat,
                      geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
                      geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
                      rect, least(greatest(coalesce(size, 10), 1), 40)) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size, 'parsed_text', jsonb_strip_nulls(to_jsonb(p))), feats);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_search_structured(
    address text DEFAULT NULL, neighbourhood text DEFAULT NULL, locality text DEFAULT NULL,
    county text DEFAULT NULL, region text DEFAULT NULL, postalcode text DEFAULT NULL,
    country text DEFAULT NULL, size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  a     record;
  nm    text;
  full_text text := concat_ws(' ', address, locality, county, postalcode);
  feats jsonb;
BEGIN
  IF coalesce(address, locality, postalcode, county, region) IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'at least one of address, locality, postalcode, county, region is required';
  END IF;
  IF address IS NOT NULL THEN
    a := geocode.parse_rule(geocode.check_text(address, 'address'));
    nm := CASE WHEN a.housenumber IS NULL THEN address END;
  ELSE
    nm := coalesce(locality, county, postalcode);
  END IF;
  SELECT jsonb_agg(geocode.feature_json(h) ORDER BY h.score DESC) INTO feats
  FROM geocode.search(full_text, nm, a.housenumber, a.street, coalesce(locality, neighbourhood), postalcode,
                      lon, lat,
                      geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
                      geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
                      NULL, least(greatest(coalesce(size, 10), 1), 40)) h;
  RETURN geocode.envelope(jsonb_strip_nulls(jsonb_build_object('address', address, 'locality', locality,
                           'postalcode', postalcode, 'county', county, 'region', region, 'size', size)), feats);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_autocomplete(
    text text, size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    min_lon double precision DEFAULT NULL, min_lat double precision DEFAULT NULL,
    max_lon double precision DEFAULT NULL, max_lat double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  t text := geocode.check_text(v1_autocomplete.text, 'text');
  rect double precision[];
  feats jsonb;
BEGIN
  PERFORM geocode.check_range(lat, -90, 90, 'focus.point.lat');
  PERFORM geocode.check_range(lon, -180, 180, 'focus.point.lon');
  IF min_lon IS NOT NULL THEN
    rect := ARRAY[min_lon, min_lat, max_lon, max_lat];
  END IF;
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.autocomplete(t, lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
         rect, least(greatest(coalesce(size, 10), 1), 40)) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size), feats);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_reverse(
    lat double precision, lon double precision, size integer DEFAULT 10,
    radius double precision DEFAULT NULL, layers text DEFAULT NULL, sources text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE feats jsonb;
BEGIN
  IF lat IS NULL OR lon IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'point.lat and point.lon are required';
  END IF;
  PERFORM geocode.check_range(lat, -90, 90, 'point.lat');
  PERFORM geocode.check_range(lon, -180, 180, 'point.lon');
  PERFORM geocode.check_range(radius, 0.001, 50, 'boundary.circle.radius');
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.reverse(lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture'], 'sources'),
         radius, least(greatest(coalesce(size, 10), 1), 40)) h;
  RETURN geocode.envelope(jsonb_build_object('point.lat', lat, 'point.lon', lon, 'size', size), feats);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_place(ids text)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  g text[] := ARRAY(SELECT btrim(x) FROM unnest(string_to_array(geocode.check_text(ids, 'ids'), ',')) x
                    WHERE btrim(x) <> '' LIMIT 40);
  feats jsonb;
BEGIN
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats FROM geocode.place(g) h;
  RETURN geocode.envelope(jsonb_build_object('ids', to_jsonb(g)), feats);
END
$$;
