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
-- Does the first `upto` words end with a town this build knows? Used to decide an ambiguous
-- trailing state: in "101 Penbrooke Drive Penfield New York", Penfield is a town, so New York is
-- the state. Towns can be several words ("South Portland"), so it tries each length.
CREATE OR REPLACE FUNCTION geocode.tail_is_town(words text[], upto int, maxw int)
RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT upto > 0 AND EXISTS (
    SELECT 1 FROM generate_series(1, least(maxw, upto)) k
    JOIN pgeo.town t
      ON t.name = lower(array_to_string(words[upto - k + 1 : upto], ' '))
  )
$$;

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
  tail   text;
  prev   text;
  lead   text;
BEGIN
  s := regexp_replace(s, '\m(apt|apartment|unit|ste|suite)\M\.?\s*[[:alnum:]-]+|#\s*[[:alnum:]-]+', ' ', 'gi');
  -- Look for a postcode past any leading house number. Phoenix and Las Vegas number houses in
  -- five digits - 13023 E Lima St - and reading that as a ZIP made the whole address parse fail:
  -- every one of the 16 five-digit-house-number cases in the Arizona and Nevada accuracy set
  -- answered with the street instead of the house. A bare "04101" still parses as a postcode,
  -- because nothing follows it.
  -- non-capturing group, so regexp_match returns the whole match rather than a group
  lead := coalesce((regexp_match(s, '^\s*\d{1,6}[a-zA-Z]?(?:\s*-\s*\d{1,6})?\s'))[1], '');
  m := regexp_match(substr(s, length(lead) + 1), '\m(\d{5})(-\d{4})?\M');
  IF m IS NOT NULL THEN
    postcode := m[1];
    s := lead || regexp_replace(substr(s, length(lead) + 1), '\m\d{5}(-\d{4})?\M', ' ');
  END IF;
  parts := ARRAY(SELECT trim(x) FROM unnest(string_to_array(s, ',')) x WHERE trim(x) <> '');
  words := regexp_split_to_array(trim(array_to_string(parts, ' ')), '\s+');
  -- Strip a trailing state, for any state this build covers (geocode.region_ref). It used to be
  -- the literals 'me' and 'maine'. A state name can be several words, so the comma-separated
  -- part is tried before the last word.
  --
  -- The abbreviation is unambiguous. The full name may also be a town - New York is both - and
  -- then the head decides: "North Woodmere, New York" names a town, so New York is the state;
  -- "350 5th Ave, New York" names an address, so New York is the city and must survive as the
  -- locality. Without this, every "<hamlet>, New York" searched inside New York City, found
  -- nothing, fell back to matching names across a million venues, and answered "Canoga, New
  -- York" with "New York Yoga".
  IF cardinality(parts) > 0 THEN
    tail := lower(rtrim(parts[cardinality(parts)], '.'));
    prev := CASE WHEN cardinality(parts) > 1
                 THEN lower(rtrim(parts[cardinality(parts) - 1], '.')) END;
    SELECT r.abbr INTO state FROM geocode.region_ref r
     WHERE tail = lower(r.abbr)
        OR (tail = lower(r.name)
            AND (NOT EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = tail)
                 OR EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = prev)))
     LIMIT 1;
    IF state IS NOT NULL THEN
      parts := parts[1:cardinality(parts) - 1];
      words := regexp_split_to_array(trim(array_to_string(parts, ' ')), '\s+');
    END IF;
  END IF;
  -- Without commas the state is the last few words, and how many depends on the state: fifteen
  -- of the fifty are two or more ("New York", "North Carolina", "District of Columbia"). Testing
  -- only the last word meant "101 Penbrooke Drive Penfield New York" kept "New York", the town
  -- matcher then took it as the town, and the answer came back 395 km away in the city.
  IF state IS NULL AND cardinality(words) > 0 THEN
    FOR n IN REVERSE least(maxw, cardinality(words)) .. 1 LOOP
      tail := lower(rtrim(array_to_string(words[cardinality(words) - n + 1 : cardinality(words)],
                                          ' '), '.'));
      SELECT r.abbr INTO state FROM geocode.region_ref r
       WHERE tail = lower(r.abbr)
          OR (tail = lower(r.name)
              AND (NOT EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = tail)
                   -- the head decides, as above; here it is any town ending the remainder
                   OR geocode.tail_is_town(words, cardinality(words) - n, maxw)))
       LIMIT 1;
      IF state IS NOT NULL THEN
        words := words[1 : cardinality(words) - n];
        EXIT;
      END IF;
    END LOOP;
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
      'country', 'United States', 'country_gid', 'whosonfirst:country:85633793', 'country_a', 'USA',
      'country_code', 'US', 'region', h.region, 'region_a', h.region_a,
      -- Pelias (WOF) names counties "Cumberland County"
      'county', CASE WHEN h.county IS NULL OR h.county ~* ' County$' THEN h.county ELSE h.county || ' County' END,
      'localadmin', h.localadmin, 'locality', h.locality,
      'neighbourhood', h.neighbourhood, 'label', h.label,
      'category', to_jsonb(h.category), 'addendum', h.addendum) || coalesce(h.hier, '{}'::jsonb),
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
      'engine', jsonb_build_object('name', 'pgeo-sql', 'author', 'pelias_maine', 'version', geocode.engine_version()),
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
-- boundary.circle.radius -> radius, boundary.country -> country, boundary.gid -> gid):
-- PostgREST keeps only the last segment of a dotted query key. boundary.circle.lat/lon would
-- collide with focus.point.lat/lon, so the edge renames them to circle_lat/circle_lon/
-- circle_radius on search, structured and autocomplete (scripts/dev/nginx.pgeo-rest.conf).
-- lang, api_key and debug are accepted and ignored, as Pelias clients send them.
--
-- Errors: invalid input returns HTTP 400 with the Pelias envelope (geocoding.errors), via
-- PostgREST's response.status setting.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.api_error(p_query jsonb, p_message text) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
BEGIN
  PERFORM set_config('response.status', '400', true);  -- read by PostgREST; harmless elsewhere
  RETURN geocode.envelope(coalesce(p_query, '{}'::jsonb), NULL, ARRAY[p_message]);
END $$;

-- boundary.country, boundary.gid, categories, lang, api_key: validated; returns categories.
CREATE OR REPLACE FUNCTION geocode.check_extras(p_country text, p_gid text, p_categories text,
                                               p_lang text, p_api_key text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE cats text[];
BEGIN
  IF p_country IS NOT NULL AND btrim(p_country) !~ '^[A-Za-z]{2,3}$' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'boundary.country must be an ISO 3166 alpha-2 or alpha-3 code';
  END IF;
  IF p_gid IS NOT NULL AND (length(p_gid) > 200 OR p_gid !~ '^[a-z_]+:[a-z]+:[A-Za-z0-9_/.:-]+$') THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'boundary.gid must look like whosonfirst:locality:85948877';
  END IF;
  IF p_lang IS NOT NULL AND p_lang !~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid lang';
  END IF;
  IF p_api_key IS NOT NULL AND length(p_api_key) > 200 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid api_key';
  END IF;
  IF p_categories IS NOT NULL AND btrim(p_categories) <> '' THEN
    cats := ARRAY(SELECT lower(btrim(x)) FROM unnest(string_to_array(p_categories, ',')) x WHERE btrim(x) <> '');
    IF cardinality(cats) > 20 OR EXISTS (SELECT 1 FROM unnest(cats) c WHERE c !~ '^[a-z0-9_=:.-]{1,60}$') THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid categories';
    END IF;
  END IF;
  RETURN cats;
END $$;

-- boundary.circle (lon, lat, radius km; radius defaults to 50) as an array, or NULL
CREATE OR REPLACE FUNCTION geocode.check_circle(p_lat double precision, p_lon double precision,
                                               p_radius double precision) RETURNS double precision[]
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF p_lat IS NULL AND p_lon IS NULL THEN
    IF p_radius IS NOT NULL THEN
      RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'boundary.circle.radius needs boundary.circle.lat and boundary.circle.lon';
    END IF;
    RETURN NULL;
  END IF;
  IF p_lat IS NULL OR p_lon IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'boundary.circle needs both lat and lon';
  END IF;
  PERFORM geocode.check_range(p_lat, -90, 90, 'boundary.circle.lat');
  PERFORM geocode.check_range(p_lon, -180, 180, 'boundary.circle.lon');
  PERFORM geocode.check_range(p_radius, 0.001, 1000, 'boundary.circle.radius');
  RETURN ARRAY[p_lon, p_lat, coalesce(p_radius, 50)];
END $$;

CREATE OR REPLACE FUNCTION geocode.check_rect(a double precision, b double precision,
                                             c double precision, d double precision) RETURNS double precision[]
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF a IS NULL AND b IS NULL AND c IS NULL AND d IS NULL THEN RETURN NULL; END IF;
  IF a IS NULL OR b IS NULL OR c IS NULL OR d IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'boundary.rect needs min_lon, min_lat, max_lon, max_lat';
  END IF;
  RETURN ARRAY[a, b, c, d];
END $$;

CREATE OR REPLACE FUNCTION geocode.check_focus(p_lat double precision, p_lon double precision) RETURNS void
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF (p_lat IS NULL) <> (p_lon IS NULL) THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'focus.point needs both lat and lon';
  END IF;
  PERFORM geocode.check_range(p_lat, -90, 90, 'focus.point.lat');
  PERFORM geocode.check_range(p_lon, -180, 180, 'focus.point.lon');
END $$;

CREATE OR REPLACE FUNCTION geocode_api.v1_search(
    text text DEFAULT NULL, size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    min_lon double precision DEFAULT NULL, min_lat double precision DEFAULT NULL,
    max_lon double precision DEFAULT NULL, max_lat double precision DEFAULT NULL,
    circle_lat double precision DEFAULT NULL, circle_lon double precision DEFAULT NULL,
    circle_radius double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL, country text DEFAULT NULL,
    gid text DEFAULT NULL, categories text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL,
    parser text DEFAULT 'rule')
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  t   text;
  p   record;
  lp  jsonb;
  cats text[];
  feats jsonb;
BEGIN
  t := geocode.check_text(v1_search.text, 'text');
  PERFORM geocode.check_focus(lat, lon);
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
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
                      geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
                      least(greatest(coalesce(size, 10), 1), 40),
                      geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats, country) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size, 'parsed_text', jsonb_strip_nulls(to_jsonb(p))), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(jsonb_strip_nulls(jsonb_build_object('text', v1_search.text)), SQLERRM);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_search_structured(
    address text DEFAULT NULL, neighbourhood text DEFAULT NULL, locality text DEFAULT NULL,
    county text DEFAULT NULL, region text DEFAULT NULL, postalcode text DEFAULT NULL,
    country text DEFAULT NULL, size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    min_lon double precision DEFAULT NULL, min_lat double precision DEFAULT NULL,
    max_lon double precision DEFAULT NULL, max_lat double precision DEFAULT NULL,
    circle_lat double precision DEFAULT NULL, circle_lon double precision DEFAULT NULL,
    circle_radius double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL,
    gid text DEFAULT NULL, categories text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  a     record;
  nm    text;
  full_text text := concat_ws(' ', address, locality, county, postalcode);
  cats  text[];
  feats jsonb;
  q     jsonb := jsonb_strip_nulls(jsonb_build_object('address', address, 'locality', locality,
                  'postalcode', postalcode, 'county', county, 'region', region, 'size', size));
BEGIN
  IF coalesce(address, locality, postalcode, county, region) IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'at least one of address, locality, postalcode, county, region is required';
  END IF;
  PERFORM geocode.check_focus(lat, lon);
  -- structured "country" is the address's country; like boundary.country, only the US matches
  cats := geocode.check_extras(CASE WHEN country ~ '^[A-Za-z]{2,3}$' THEN country END, gid, categories, lang, api_key);
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
                      geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
                      least(greatest(coalesce(size, 10), 1), 40),
                      geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats,
                      CASE WHEN country IS NULL OR country ~* '^(us|usa|united states( of america)?)$' THEN NULL ELSE country END) h;
  RETURN geocode.envelope(q, feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(q, SQLERRM);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_autocomplete(
    text text DEFAULT NULL, size integer DEFAULT 10,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    min_lon double precision DEFAULT NULL, min_lat double precision DEFAULT NULL,
    max_lon double precision DEFAULT NULL, max_lat double precision DEFAULT NULL,
    circle_lat double precision DEFAULT NULL, circle_lon double precision DEFAULT NULL,
    circle_radius double precision DEFAULT NULL,
    layers text DEFAULT NULL, sources text DEFAULT NULL, country text DEFAULT NULL,
    gid text DEFAULT NULL, categories text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  t text;
  cats text[];
  feats jsonb;
BEGIN
  t := geocode.check_text(v1_autocomplete.text, 'text');
  PERFORM geocode.check_focus(lat, lon);
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.autocomplete(t, lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
         geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
         least(greatest(coalesce(size, 10), 1), 40),
         geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats, country) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(jsonb_strip_nulls(jsonb_build_object('text', v1_autocomplete.text)), SQLERRM);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_reverse(
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL, size integer DEFAULT 10,
    radius double precision DEFAULT NULL, layers text DEFAULT NULL, sources text DEFAULT NULL,
    country text DEFAULT NULL, gid text DEFAULT NULL, categories text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  cats text[];
  feats jsonb;
  q jsonb := jsonb_strip_nulls(jsonb_build_object('point.lat', lat, 'point.lon', lon, 'size', size));
BEGIN
  IF lat IS NULL OR lon IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'point.lat and point.lon are required';
  END IF;
  PERFORM geocode.check_range(lat, -90, 90, 'point.lat');
  PERFORM geocode.check_range(lon, -180, 180, 'point.lon');
  PERFORM geocode.check_range(radius, 0.001, 50, 'boundary.circle.radius');
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.reverse(lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture'], 'sources'),
         radius, least(greatest(coalesce(size, 10), 1), 40), gid, cats, country) h;
  RETURN geocode.envelope(q, feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(q, SQLERRM);
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_place(ids text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  g text[];
  feats jsonb;
BEGIN
  PERFORM geocode.check_extras(NULL, NULL, NULL, lang, api_key);
  g := ARRAY(SELECT btrim(x) FROM unnest(string_to_array(geocode.check_text(ids, 'ids'), ',')) x
             WHERE btrim(x) <> '' LIMIT 40);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats FROM geocode.place(g) h;
  RETURN geocode.envelope(jsonb_build_object('ids', to_jsonb(g)), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(jsonb_build_object('ids', ids), SQLERRM);
END
$$;

-- ---------------------------------------------------------------------------------------
-- /v1/attribution: the data licences, as Pelias serves them. HTML rather than GeoJSON,
-- because a client shows it to a person; PostgREST returns a function whose return type is
-- a domain named after a media type as that media type, verbatim, when the request asks
-- for it (the edge sets "Accept: text/html" so every client gets the page, as Pelias does).
-- No parameters and no user input reach this page; the only interpolated value is the
-- engine version, which the build writes and validates as X.Y.Z, and it is escaped anyway.
-- ---------------------------------------------------------------------------------------
CREATE DOMAIN geocode_api."text/html" AS text;

CREATE OR REPLACE FUNCTION geocode_api.v1_attribution() RETURNS geocode_api."text/html"
LANGUAGE sql STABLE PARALLEL SAFE AS $fn$
  SELECT ('<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>pgeo Geocoder</title>
    <style>html { font-family: system-ui, sans-serif; margin: 2rem; max-width: 46rem; }
           li { margin: 0.35rem 0; }</style>
  </head>
  <body>
    <h1>pgeo API</h1>
    <h3>Version: ' || replace(replace(replace(geocode.engine_version(), '&', '&amp;'),
                              '<', '&lt;'), '>', '&gt;') || '</h3>
    <h3>Attribution</h3>
    <p>Geocoding by <a href="https://github.com/">pgeo</a>, a PostgreSQL/PostGIS geocoder,
       serving ' || (SELECT string_agg(r.name, ', ' ORDER BY r.name) FROM geocode.region_ref r)
              || '. Data from:</p>
    <ul>
      <li><a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>
          &copy; OpenStreetMap contributors, under
          <a href="https://opendatacommons.org/licenses/odbl/">ODbL 1.0</a>. See also the
          <a href="https://operations.osmfoundation.org/policies/nominatim/">OSM geocoding
          guidelines</a> for acceptable use.</li>
      <li><a href="https://openaddresses.io/">OpenAddresses</a> (E911 and municipal
          sources), under the licence of each contributing source.</li>
      <li><a href="https://whosonfirst.org/">Who&#39;s On First</a>, under CC-BY 4.0 with
          per-record source licences.</li>
      <li><a href="https://www.usgs.gov/us-board-on-geographic-names">USGS GNIS</a> domestic
          names: public domain.</li>
      <li><a href="https://www.census.gov/">US Census Bureau</a> TIGER/Line and the ZCTA
          gazetteer: public domain.</li>
      <li><a href="https://overturemaps.org/">Overture Maps</a> places, under
          CDLA-Permissive-2.0; Overture themes derived from OpenStreetMap under ODbL 1.0.</li>
    </ul>
  </body>
</html>
')::geocode_api."text/html"
$fn$;
