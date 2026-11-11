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

-- The letters of a string, sorted: two strings share it exactly when one is an anagram of the
-- other, which is what a transposed pair of characters leaves behind.
CREATE OR REPLACE FUNCTION geocode.letters(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT string_agg(c, '' ORDER BY c) FROM regexp_split_to_table(lower(coalesce(t, '')), '') AS c
$$;

-- The town this text is a misspelling of, or NULL. Used only after an exact match has failed,
-- so a correctly spelled town is never routed through here. `want_state` is the state the query
-- named, and a town in it is preferred over a closer spelling elsewhere.
--
-- A geocoder is typed into by people, so a misspelled town has to reach the town it means. What
-- it must not do is turn a query that was never a town into one: "walmart" is within two edits
-- of Balmat, New York. The rule that separates them is one edit, or two when the two strings
-- have the same letters - that second case is a transposition, "Tuscon" for "Tucson", which is
-- the most common typo of all and the one a trigram index cannot see (the two share almost no
-- trigrams). Requiring the same word count and a length within one rejects the rest:
-- "west end" / "westwind", "hudson river" / "indian river", "central park" / "central islip".
-- the one-argument form from 0.13.0, or CREATE OR REPLACE leaves both and calls are ambiguous
DROP FUNCTION IF EXISTS geocode.town_fuzzy(text);
CREATE OR REPLACE FUNCTION geocode.town_fuzzy(cand text, want_state text DEFAULT NULL) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT t.name FROM pgeo.town t
  -- Below five characters a single edit reaches too far: "park" is one from Parks, "bath" one
  -- from Bethel's neighbours. Short names must be spelled correctly.
  WHERE length(cand) >= 5
    -- the build's own towns only: a typo is corrected to a town the build covers, and scanning the
    -- worldwide towns (300,000 names) on every misspelling would cost far more than it finds
    AND NOT t.coarse
    AND abs(length(t.name) - length(cand)) <= 1
    AND array_length(string_to_array(t.name, ' '), 1) = array_length(string_to_array(cand, ' '), 1)
    AND (levenshtein(t.name, cand) <= 1
         OR (levenshtein(t.name, cand) = 2 AND geocode.letters(t.name) = geocode.letters(cand)))
  -- A town in the state the query named wins over a closer spelling in another state. "Hosuton,
  -- TX" is one edit from Hosston, Louisiana and two from Houston, Texas - a transposition - so
  -- on edit distance alone the wrong state's village wins.
  ORDER BY (want_state IS NOT NULL AND t.region_a = upper(want_state)) DESC,
           levenshtein(t.name, cand), t.name
  LIMIT 1
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
  world  boolean := false;  -- the query named a state this build does not hold: worldwide towns count
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
  -- Strip a trailing state, any US state (geocode.state_ref). It used to be
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
    -- A state the build does not hold counts only with a known town before it ("Sacramento, CA"):
    -- the tokens MD, PA, MS and IN end names and credentials ("Eric Broen, MD") as often as they
    -- name a state. The build's own states keep the rules they always had.
    SELECT r.abbr INTO state FROM geocode.state_ref r
     WHERE CASE WHEN EXISTS (SELECT 1 FROM geocode.region_ref h WHERE h.abbr = r.abbr)
                THEN tail = lower(r.abbr)
                     OR (tail = lower(r.name)
                         AND (NOT EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = tail AND NOT t.coarse)
                              OR EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = prev)))
                ELSE (tail = lower(r.abbr) OR tail = lower(r.name))
                     AND prev IS NOT NULL AND EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = prev)
           END
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
      -- Without a comma, a state the build does not hold is taken only when the town before it is
      -- in that state ("Sacramento CA": Sacramento is a California town). Bare tokens such as MD,
      -- PA and MS end names and credentials ("Richard Batstone Md Pa", "Third Lake North
      -- Washington") and no town of that name lies in those states, so they stay in the text.
      SELECT r.abbr INTO state FROM geocode.state_ref r
       WHERE CASE WHEN EXISTS (SELECT 1 FROM geocode.region_ref h WHERE h.abbr = r.abbr)
                  THEN tail = lower(r.abbr)
                       OR (tail = lower(r.name)
                           AND (NOT EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = tail AND NOT t.coarse)
                                -- the head decides, as above; here it is any town ending the remainder
                                OR geocode.tail_is_town(words, cardinality(words) - n, maxw)))
                  ELSE (tail = lower(r.abbr) OR tail = lower(r.name))
                       AND EXISTS (SELECT 1
                                   FROM generate_series(1, least(maxw, cardinality(words) - n)) k
                                   JOIN pgeo.town t
                                     ON t.name = lower(array_to_string(
                                          words[cardinality(words) - n - k + 1 : cardinality(words) - n], ' '))
                                    AND t.region_a = r.abbr)
             END
       LIMIT 1;
      IF state IS NOT NULL THEN
        words := words[1 : cardinality(words) - n];
        EXIT;
      END IF;
    END LOOP;
  END IF;

  world := state IS NOT NULL AND NOT EXISTS (SELECT 1 FROM geocode.region_ref h WHERE h.abbr = state);
  IF cardinality(parts) >= 2 AND (
       EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = lower(parts[cardinality(parts)])
                                          AND (world OR NOT t.coarse))
       OR parts[1] ~ '^\s*\d{1,6}[a-zA-Z]?(\s*-\s*\d{1,6})?\s+\S') THEN
    locality := parts[cardinality(parts)];
    head := array_to_string(parts[1:cardinality(parts) - 1], ', ');
  ELSE
    head := array_to_string(words, ' ');
    FOR n IN REVERSE least(maxw, cardinality(words))..1 LOOP
      cand := lower(array_to_string(words[cardinality(words) - n + 1:cardinality(words)], ' '));
      IF EXISTS (SELECT 1 FROM pgeo.town t WHERE t.name = cand AND (world OR NOT t.coarse)) THEN
        locality := array_to_string(words[cardinality(words) - n + 1:cardinality(words)], ' ');
        head := array_to_string(words[1:cardinality(words) - n], ' ');
        EXIT;
      END IF;
    END LOOP;
    -- No town spelled the way this query spells it. Try once more allowing a typo, so "Albny,
    -- NY" reaches Albany instead of parsing to nothing at all - in which case the whole raw
    -- string goes to name matching, where venues carrying their own town in their name
    -- ("Albany, NY - Albany.com") beat the town itself.
    --
    -- Only when the misspelling is the entire remaining text. A typo rule loosed on a trailing
    -- word turns "central park" into the town of Parks, and the query was never about a town;
    -- when a real town does trail real text, the exact pass above has already found it.
    IF locality IS NULL AND cardinality(words) > 0 THEN
      cand := lower(array_to_string(words, ' '));
      -- `state` is already set by the block above when the query named one
      tail := geocode.town_fuzzy(cand, state);
      IF tail IS NOT NULL THEN
        -- the corrected spelling, not what was typed: downstream matching is by trigram, and a
        -- transposition shares almost no trigrams with the word it came from, so "Tuscon" would
        -- never reach Tucson on its own
        locality := tail;
        head := '';
      END IF;
    END IF;
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
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'type', 'Feature',
    'geometry', jsonb_build_object('type', 'Point', 'coordinates', jsonb_build_array(h.lon, h.lat)),
    'properties', jsonb_build_object(
      'id', h.source_id, 'gid', h.gid, 'layer', h.layer, 'source', h.source, 'source_id', h.source_id,
      'name', h.name, 'housenumber', h.housenumber, 'street', h.street, 'postalcode', h.postcode,
      'confidence', round(h.confidence::numeric, 3), 'match_type', h.match_type, 'accuracy', h.accuracy,
      'distance', round(h.distance_km::numeric, 3),
      -- The country follows the state: the state is the only evidence this build has that a
      -- feature is in the United States, since the build holds states and not countries. A
      -- feature outside every region polygon carries neither, rather than a country asserted on
      -- the assumption that a US build contains only US places - which is how "Akwesasne Canada
      -- Post" came to read ", NY, USA" and then ", USA".
      -- The country is the feature's own (a Maine build also holds worldwide places, see
      -- geocode.hit.coarse). country_a is the ISO alpha-3 code, as Pelias returns it, looked up from
      -- the alpha-2 the data carries (geocode.country_ref); a territory Overture codes outside ISO
      -- 3166-1 has none. country_code is the alpha-2. region_a is the US state abbreviation; for
      -- a worldwide place it comes in with hier (the ISO 3166-2 code), which is merged below.
      'country', h.country,
      'country_gid', CASE WHEN h.country_a = 'US' THEN 'whosonfirst:country:85633793' END,
      'country_a', (SELECT r.alpha3 FROM geocode.country_ref r WHERE r.alpha2 = h.country_a),
      'country_code', h.country_a,
      'coarse', CASE WHEN h.coarse THEN to_jsonb(true) END,
      'region', h.region, 'region_a', h.region_a,
      -- already carries its own suffix from Who's on First (Parish, Borough, Census Area)
      'county', h.county,
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
LANGUAGE plpgsql STABLE AS $$
DECLARE maxlen integer := geocode.api_limit('max_text');
BEGIN
  IF v IS NULL OR btrim(v) = '' THEN RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = format('missing param %s', field); END IF;
  IF length(v) > maxlen THEN RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = format('%s is longer than %s characters', field, maxlen); END IF;
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
    out := array_remove(out, 'coarse') || ARRAY['neighbourhood','locality','localadmin','county','region','country','postalcode'];
  END IF;
  IF NOT out <@ allowed THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = format('invalid %s: %s', field, v);
  END IF;
  RETURN out;
END $$;

CREATE OR REPLACE FUNCTION geocode.check_range(v double precision, lo double precision, hi double precision, field text)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF v IS NOT NULL AND (v < lo OR v > hi OR v = 'NaN'::float8) THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = format('%s must be between %s and %s', field, lo, hi);
  END IF;
  RETURN v;
END $$;

-- An optional text argument: NULL is fine, anything longer than pgeo.max_text is refused.
-- An earlier form took the limit as a third argument; drop it, or two-argument calls would be
-- ambiguous between the two.
DROP FUNCTION IF EXISTS geocode.check_len(text, text, integer);
-- A boolean request parameter: absent is the default, true, so the coarse tier answers unless asked
-- not to. Anything but true/false/1/0/yes/no is the client's mistake, reported as one.
CREATE OR REPLACE FUNCTION geocode.check_bool(v text, field text, dflt boolean DEFAULT true) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $f$
BEGIN
  IF v IS NULL OR btrim(v) = '' THEN RETURN dflt; END IF;
  IF lower(btrim(v)) IN ('true', 't', '1', 'yes', 'y', 'on') THEN RETURN true; END IF;
  IF lower(btrim(v)) IN ('false', 'f', '0', 'no', 'n', 'off') THEN RETURN false; END IF;
  RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = field || ' must be true or false';
END
$f$;

CREATE OR REPLACE FUNCTION geocode.check_len(v text, field text) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE maxlen integer := geocode.api_limit('max_text');
BEGIN
  IF v IS NOT NULL AND length(v) > maxlen THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = format('%s is longer than %s characters', field, maxlen);
  END IF;
  RETURN v;
END $$;

-- What a client is told about a 22023 error. The validation helpers above tag their own errors
-- with HINT 'pgeo', and those messages are written for clients. Anything else with that SQLSTATE
-- was raised inside PostgreSQL or PostGIS, and its text describes internals, so it is replaced.
CREATE OR REPLACE FUNCTION geocode.client_message(p_message text, p_hint text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p_hint = 'pgeo' THEN p_message ELSE 'invalid parameter value' END
$$;

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
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'boundary.country must be an ISO 3166 alpha-2 or alpha-3 code';
  END IF;
  IF p_gid IS NOT NULL AND (length(p_gid) > 200 OR p_gid !~ '^[a-z_]+:[a-z]+:[A-Za-z0-9_/.:-]+$') THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'boundary.gid must look like whosonfirst:locality:85948877';
  END IF;
  IF p_lang IS NOT NULL AND p_lang !~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'invalid lang';
  END IF;
  IF p_api_key IS NOT NULL AND length(p_api_key) > 200 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'invalid api_key';
  END IF;
  IF p_categories IS NOT NULL AND btrim(p_categories) <> '' THEN
    cats := ARRAY(SELECT lower(btrim(x)) FROM unnest(string_to_array(p_categories, ',')) x WHERE btrim(x) <> '');
    IF cardinality(cats) > 20 OR EXISTS (SELECT 1 FROM unnest(cats) c WHERE c !~ '^[a-z0-9_=:.-]{1,60}$') THEN
      RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'invalid categories';
    END IF;
  END IF;
  RETURN cats;
END $$;

-- boundary.circle (lon, lat, radius km; radius defaults to 50) as an array, or NULL
CREATE OR REPLACE FUNCTION geocode.check_circle(p_lat double precision, p_lon double precision,
                                               p_radius double precision) RETURNS double precision[]
LANGUAGE plpgsql STABLE AS $$
-- STABLE, not IMMUTABLE: the radius cap is a setting (pgeo.max_circle_km).
BEGIN
  IF p_lat IS NULL AND p_lon IS NULL THEN
    IF p_radius IS NOT NULL THEN
      RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'boundary.circle.radius needs boundary.circle.lat and boundary.circle.lon';
    END IF;
    RETURN NULL;
  END IF;
  IF p_lat IS NULL OR p_lon IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'boundary.circle needs both lat and lon';
  END IF;
  PERFORM geocode.check_range(p_lat, -90, 90, 'boundary.circle.lat');
  PERFORM geocode.check_range(p_lon, -180, 180, 'boundary.circle.lon');
  PERFORM geocode.check_range(p_radius, 0.001, geocode.api_limit('max_circle_km'), 'boundary.circle.radius (km)');
  RETURN ARRAY[p_lon, p_lat, coalesce(p_radius, 50)];
END $$;

CREATE OR REPLACE FUNCTION geocode.check_rect(a double precision, b double precision,
                                             c double precision, d double precision) RETURNS double precision[]
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF a IS NULL AND b IS NULL AND c IS NULL AND d IS NULL THEN RETURN NULL; END IF;
  IF a IS NULL OR b IS NULL OR c IS NULL OR d IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'boundary.rect needs min_lon, min_lat, max_lon, max_lat';
  END IF;
  -- check_range refuses NaN; the bounds refuse +-Infinity. Unchecked, either reached
  -- ST_MakeEnvelope and the spatial index.
  PERFORM geocode.check_range(a, -180, 180, 'boundary.rect.min_lon');
  PERFORM geocode.check_range(b, -90, 90, 'boundary.rect.min_lat');
  PERFORM geocode.check_range(c, -180, 180, 'boundary.rect.max_lon');
  PERFORM geocode.check_range(d, -90, 90, 'boundary.rect.max_lat');
  IF a > c THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo',
      MESSAGE = format('boundary.rect.min_lon (%s) is greater than boundary.rect.max_lon (%s)', a, c);
  END IF;
  IF b > d THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo',
      MESSAGE = format('boundary.rect.min_lat (%s) is greater than boundary.rect.max_lat (%s)', b, d);
  END IF;
  RETURN ARRAY[a, b, c, d];
END $$;

CREATE OR REPLACE FUNCTION geocode.check_focus(p_lat double precision, p_lon double precision) RETURNS void
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF (p_lat IS NULL) <> (p_lon IS NULL) THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'focus.point needs both lat and lon';
  END IF;
  PERFORM geocode.check_range(p_lat, -90, 90, 'focus.point.lat');
  PERFORM geocode.check_range(p_lon, -180, 180, 'focus.point.lon');
END $$;

-- A structured query's country, given as an ISO code or a name: the ISO alpha-2 code to filter on,
-- NULL when none was given, ZZ (which matches nothing) for a name that is not a country the
-- worldwide tier knows. The United States, by any of its names, is US.
CREATE OR REPLACE FUNCTION geocode.country_code_of(c text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN nullif(btrim(c), '') IS NULL THEN NULL
              WHEN c ~* '^(us|usa|united states( of america)?)$' THEN 'US'
              WHEN c ~ '^[A-Za-z]{2,3}$' THEN geocode.country_code(c)
              ELSE coalesce((SELECT f.country_a FROM pgeo.feature f
                              WHERE f.layer = 'country' AND f.name_norm = geocode.norm(c) LIMIT 1), 'ZZ') END
$$;

-- A country named at the end of the query ("Oslo, Norway"): the query without it, and the country's
-- ISO alpha-2 code, so the search can be limited to that country. The United States is left to the
-- parser, which already strips it, and a name that is the whole query ("Norway") is not split:
-- the country and a town of that name are both answers then, and neither is chosen for the caller.
-- Names come from the worldwide tier's country rows, so a build without one finds none.
CREATE OR REPLACE FUNCTION geocode.split_country(t text, OUT rest text, OUT cc text)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  -- the country is the last comma-separated part and not all of the query, so "Oslo, Norway" splits
  -- and "red hill, west peru" (West Peru is a town in Maine) and "Norway" alone do not
  WITH p AS (SELECT string_to_array(t, ',') AS parts),
       n AS (SELECT array_length(parts, 1) AS k,
                    geocode.norm(parts[array_length(parts, 1)]) AS last,
                    geocode.norm(array_to_string(parts[1:array_length(parts, 1) - 1], ' ')) AS head
             FROM p)
  SELECT n.head, f.country_a
  FROM n, pgeo.feature f
  WHERE n.k > 1 AND n.head <> ''
    AND f.layer = 'country' AND f.coarse AND f.country_a <> 'US' AND f.name_norm = n.last
  ORDER BY f.id
  LIMIT 1
$$;

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
    parser text DEFAULT 'rule', coarse text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  err_hint text;
  t   text;
  p   record;
  lp  jsonb;
  cats text[];
  feats jsonb;
  sc_rest text;
  sc_cc   text;
  tq  text;   -- the text that is parsed and searched: t without a trailing country
  cty text;   -- boundary.country, or the one the text named
BEGIN
  t := geocode.check_text(v1_search.text, 'text');
  PERFORM geocode.check_focus(lat, lon);
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
  SELECT x.rest, x.cc INTO sc_rest, sc_cc FROM geocode.split_country(t) x;
  IF sc_cc IS NOT NULL AND country IS NULL THEN tq := sc_rest; cty := sc_cc; ELSE tq := t; cty := country; END IF;
  p := geocode.parse_rule(tq);
  IF parser = 'postal' THEN
    lp := geocode.parse_postal(tq);
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
  FROM geocode.search(tq, p.name, p.housenumber, p.street, p.locality, p.postcode,
                      lon, lat,
                      geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','country','postalcode'], 'layers'),
                      geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
                      geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
                      least(greatest(coalesce(size, 10), 1), geocode.api_limit('max_size')::integer),
                      geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats, cty,
                      p.state, geocode.check_bool(v1_search.coarse, 'coarse')) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size, 'parsed_text', jsonb_strip_nulls(to_jsonb(p))), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  GET STACKED DIAGNOSTICS err_hint = PG_EXCEPTION_HINT;
  RETURN geocode.api_error(jsonb_strip_nulls(jsonb_build_object('text', left(v1_search.text, 200))),
                           geocode.client_message(SQLERRM, err_hint));
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
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL, coarse text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  a     record;
  -- Kept apart from the record deliberately. `a` is assigned only when an address was given, and
  -- an unassigned plpgsql record cannot be read at all - even for a field that would be NULL, it
  -- raises 55000. A structured query is allowed to carry no address (locality and region alone
  -- are a valid Pelias query), so the two fields the search needs are held as scalars that are
  -- simply NULL in that case.
  hn    text;
  st    text;
  nm    text;
  lay   text[];
  full_text text := concat_ws(' ', address, locality, county, postalcode);
  cats  text[];
  feats jsonb;
  q     jsonb := jsonb_strip_nulls(jsonb_build_object('address', left(address, 200), 'locality', left(locality, 200),
                  'postalcode', left(postalcode, 200), 'county', left(county, 200), 'region', left(region, 200),
                  'size', size));
  err_hint text;
BEGIN
  -- Every field is bounded, not just address: the others reach the trigram index (through
  -- full_text) or are echoed back, and were previously limited only by the URL length.
  PERFORM geocode.check_len(neighbourhood, 'neighbourhood');
  PERFORM geocode.check_len(locality, 'locality');
  PERFORM geocode.check_len(county, 'county');
  PERFORM geocode.check_len(region, 'region');
  PERFORM geocode.check_len(postalcode, 'postalcode');
  PERFORM geocode.check_len(country, 'country');
  IF coalesce(address, locality, postalcode, county, region) IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'at least one of address, locality, postalcode, county, region is required';
  END IF;
  PERFORM geocode.check_focus(lat, lon);
  -- structured "country" is the address's country; like boundary.country, only the US matches
  cats := geocode.check_extras(CASE WHEN country ~ '^[A-Za-z]{2,3}$' THEN country END, gid, categories, lang, api_key);
  IF address IS NOT NULL THEN
    a := geocode.parse_rule(geocode.check_text(address, 'address'));
    hn := a.housenumber;
    st := a.street;
    nm := CASE WHEN hn IS NULL THEN address END;
  ELSE
    nm := coalesce(locality, county, postalcode);
  END IF;
  lay := geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','country','postalcode'], 'layers');
  -- Nothing but a region: that asks for the region itself, and the two front ends used to
  -- answer it differently. region is otherwise a filter and never part of the text, so the SQL
  -- edge had no name to search for and returned nothing, while FastAPI folded the bare "ME" into
  -- the free text and matched venues called "Mrs & Me". Resolve it to the region's own name and
  -- pin the answer to the region layer, unless the caller asked for particular layers.
  -- "nothing but a region" has to name the fields: nm above ignores neighbourhood (it is passed
  -- to the search as a locality, not as the subject), so testing nm alone sent
  -- neighbourhood=Bangor&region=ME down this path and answered with the state.
  IF region IS NOT NULL AND address IS NULL AND neighbourhood IS NULL AND locality IS NULL
     AND county IS NULL AND postalcode IS NULL THEN
    nm := geocode.region_name(region);
    full_text := coalesce(nm, full_text);
    IF nm IS NOT NULL AND lay IS NULL THEN lay := ARRAY['region']; END IF;
  END IF;
  SELECT jsonb_agg(geocode.feature_json(h) ORDER BY h.score DESC) INTO feats
  FROM geocode.search(full_text, nm, hn, st, coalesce(locality, neighbourhood), postalcode,
                      lon, lat,
                      lay,
                      geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
                      geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
                      least(greatest(coalesce(size, 10), 1), geocode.api_limit('max_size')::integer),
                      geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats,
                      -- a country given by name is looked up among the worldwide tier's countries;
                      -- one it does not know matches nothing (ZZ is no country)
                      geocode.country_code_of(country),
                      region, geocode.check_bool(v1_search_structured.coarse, 'coarse')) h;
  RETURN geocode.envelope(q, feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  GET STACKED DIAGNOSTICS err_hint = PG_EXCEPTION_HINT;
  RETURN geocode.api_error(q, geocode.client_message(SQLERRM, err_hint));
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
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL, coarse text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  err_hint text;
  t text;
  cats text[];
  feats jsonb;
BEGIN
  t := geocode.check_text(v1_autocomplete.text, 'text');
  PERFORM geocode.check_focus(lat, lon);
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.autocomplete(t, lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','country','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture','interpolation'], 'sources'),
         geocode.check_rect(min_lon, min_lat, max_lon, max_lat),
         least(greatest(coalesce(size, 10), 1), geocode.api_limit('max_size')::integer),
         geocode.check_circle(circle_lat, circle_lon, circle_radius), gid, cats, country,
         geocode.check_bool(v1_autocomplete.coarse, 'coarse')) h;
  RETURN geocode.envelope(jsonb_build_object('text', t, 'size', size), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  GET STACKED DIAGNOSTICS err_hint = PG_EXCEPTION_HINT;
  RETURN geocode.api_error(jsonb_strip_nulls(jsonb_build_object('text', left(v1_autocomplete.text, 200))),
                           geocode.client_message(SQLERRM, err_hint));
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_reverse(
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL, size integer DEFAULT 10,
    radius double precision DEFAULT NULL, layers text DEFAULT NULL, sources text DEFAULT NULL,
    country text DEFAULT NULL, gid text DEFAULT NULL, categories text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL, coarse text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  err_hint text;
  cats text[];
  feats jsonb;
  q jsonb := jsonb_strip_nulls(jsonb_build_object('point.lat', lat, 'point.lon', lon, 'size', size));
BEGIN
  IF lat IS NULL OR lon IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', HINT = 'pgeo', MESSAGE = 'point.lat and point.lon are required';
  END IF;
  PERFORM geocode.check_range(lat, -90, 90, 'point.lat');
  PERFORM geocode.check_range(lon, -180, 180, 'point.lon');
  PERFORM geocode.check_range(radius, 0.001, geocode.api_limit('max_reverse_km'), 'boundary.circle.radius (km)');
  cats := geocode.check_extras(country, gid, categories, lang, api_key);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats
  FROM geocode.reverse(lon, lat,
         geocode.check_list(layers, ARRAY['address','venue','street','neighbourhood','locality','localadmin','county','region','country','postalcode'], 'layers'),
         geocode.check_list(sources, ARRAY['openaddresses','openstreetmap','whosonfirst','gnis','zcta','overture'], 'sources'),
         radius, least(greatest(coalesce(size, 10), 1), geocode.api_limit('max_size')::integer), gid, cats, country,
         geocode.check_bool(v1_reverse.coarse, 'coarse')) h;
  RETURN geocode.envelope(q, feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  GET STACKED DIAGNOSTICS err_hint = PG_EXCEPTION_HINT;
  RETURN geocode.api_error(q, geocode.client_message(SQLERRM, err_hint));
END
$$;

CREATE OR REPLACE FUNCTION geocode_api.v1_place(ids text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  err_hint text;
  g text[];
  feats jsonb;
BEGIN
  PERFORM geocode.check_extras(NULL, NULL, NULL, lang, api_key);
  g := ARRAY(SELECT btrim(x) FROM unnest(string_to_array(geocode.check_text(ids, 'ids'), ',')) x
             WHERE btrim(x) <> '' LIMIT geocode.api_limit('max_size')::integer);
  SELECT jsonb_agg(geocode.feature_json(h)) INTO feats FROM geocode.place(g) h;
  RETURN geocode.envelope(jsonb_build_object('ids', to_jsonb(g)), feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  GET STACKED DIAGNOSTICS err_hint = PG_EXCEPTION_HINT;
  RETURN geocode.api_error(jsonb_build_object('ids', left(ids, 200)), geocode.client_message(SQLERRM, err_hint));
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
          CDLA-Permissive-2.0. Overture divisions (the worldwide towns, counties, regions and countries
          used outside the region the build covers) are derived from OpenStreetMap under ODbL 1.0 and from
          Esri Community Maps contributors under CC BY 4.0.</li>
    </ul>
  </body>
</html>
')::geocode_api."text/html"
$fn$;
