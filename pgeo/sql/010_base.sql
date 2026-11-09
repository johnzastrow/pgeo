-- pgeo base: extensions and shared helpers (idempotent). Tables are built per load in
-- schema pgeo_build and swapped into pgeo (see 020_tables.sql and the loader).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

CREATE SCHEMA IF NOT EXISTS geocode;

-- unaccent() is STABLE because the dictionary could change; with the dictionary named
-- explicitly the result is deterministic, so this wrapper may be IMMUTABLE (usable in
-- index expressions and generated columns).
CREATE OR REPLACE FUNCTION geocode.unaccent_i(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, t) $$;

-- Canonical form used on both sides of every comparison: lowercase, no accents,
-- punctuation to spaces, and common street/place abbreviations EXPANDED to full words
-- ("Rd" -> "road", "Mt" -> "mount", "N" -> "north", "St" -> "street", or "saint" when a
-- name follows: "St George"). Full words make typo matching work: "moountain rd" shares
-- few trigrams with "mtn rd" (0.25) but many with "mountain road" (0.81).
CREATE OR REPLACE FUNCTION geocode.norm(t text) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE STRICT
AS $$
DECLARE
  s text := lower(geocode.unaccent_i(t));
BEGIN
  s := regexp_replace(s, '[''’]', '', 'g');                 -- O'Brien -> obrien
  s := regexp_replace(s, '[^a-z0-9]+', ' ', 'g');
  -- Double the separators so adjacent words each keep their own bounding spaces for the
  -- substitutions below; collapsed again at the end.
  s := ' ' || replace(trim(s), ' ', '  ') || ' ';
  -- "st" followed by a name (not a direction or the end) is "saint"
  s := regexp_replace(s, ' st  (?=(?!(n|s|e|w|ne|nw|se|sw|north|south|east|west|ext|extension) )[a-z])', ' saint  ', 'g');
  s := replace(s, ' st ', ' street ');
  s := replace(s, ' rd ', ' road ');      s := replace(s, ' ave ', ' avenue ');
  s := replace(s, ' av ', ' avenue ');    s := replace(s, ' dr ', ' drive ');
  s := replace(s, ' ln ', ' lane ');      s := replace(s, ' ct ', ' court ');
  s := replace(s, ' cir ', ' circle ');   s := replace(s, ' pl ', ' place ');
  s := replace(s, ' ter ', ' terrace ');  s := replace(s, ' blvd ', ' boulevard ');
  s := replace(s, ' pkwy ', ' parkway '); s := replace(s, ' hwy ', ' highway ');
  s := replace(s, ' rte ', ' route ');    s := replace(s, ' rt ', ' route ');
  s := replace(s, ' ext ', ' extension '); s := replace(s, ' sq ', ' square ');
  s := replace(s, ' pt ', ' point ');     s := replace(s, ' mtn ', ' mountain ');
  s := replace(s, ' mt ', ' mount ');     s := replace(s, ' ft ', ' fort ');
  s := replace(s, ' n ', ' north ');      s := replace(s, ' s ', ' south ');
  s := replace(s, ' e ', ' east ');       s := replace(s, ' w ', ' west ');
  s := replace(s, ' ne ', ' northeast '); s := replace(s, ' nw ', ' northwest ');
  s := replace(s, ' se ', ' southeast '); s := replace(s, ' sw ', ' southwest ');
  RETURN trim(regexp_replace(s, ' +', ' ', 'g'));
END
$$;

-- Leading integer of a house number ("12A" -> 12, "10-12" -> 10), NULL if none.
CREATE OR REPLACE FUNCTION geocode.hn_int(hn text) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT nullif(substring(hn from '^\s*(\d{1,7})'), '')::integer $$;

-- A house number as it is compared: lower case, no leading zeros ("0045" -> "45", "007A" -> "7a").
-- Builds loaded before the loader dropped the zeros still hold "0045", and a query never has one.
CREATE OR REPLACE FUNCTION geocode.hn_norm(hn text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(regexp_replace(btrim(hn), '^0+([0-9])', '\1')) $$;

-- Engine version reported by the SQL API. The loader replaces this placeholder with the
-- package version from pgeo/pyproject.toml after applying 050_api.sql (single source).
CREATE OR REPLACE FUNCTION geocode.engine_version() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT '0+unknown'::text $$;

-- The regions (US states) this build covers: name, postal abbreviation, Census FIPS, and the
-- Who's on First id that ties a feature to its state through its county. The loader fills it
-- from regions/regions.json, because which states a build holds is a property of the build.
-- Who's on First carries no wof:abbreviation on US region records, so "NY" has to come from
-- somewhere, and the registry is where the rest of the per-state facts already live.
DROP TABLE IF EXISTS geocode.region_ref CASCADE;
CREATE TABLE geocode.region_ref (
  wof_id bigint PRIMARY KEY,
  name text NOT NULL,
  abbr text NOT NULL,
  fips text NOT NULL
);

-- Maine counties: Census FIPS codes and the Pelias/WOF abbreviations (county_a), which do
-- not follow one rule (Waldo WL, Washington WS). Reference data, rebuilt on every apply.
-- Maine only: no other state has a curated set of these, so county_a is null elsewhere.
DROP TABLE IF EXISTS geocode.county_ref CASCADE;
CREATE TABLE geocode.county_ref (county text PRIMARY KEY, abbr text NOT NULL, fips text NOT NULL);
INSERT INTO geocode.county_ref (county, abbr, fips) VALUES
  ('androscoggin','AN','23001'),('aroostook','AR','23003'),('cumberland','CU','23005'),
  ('franklin','FR','23007'),('hancock','HA','23009'),('kennebec','KE','23011'),('knox','KN','23013'),
  ('lincoln','LI','23015'),('oxford','OX','23017'),('penobscot','PE','23019'),
  ('piscataquis','PI','23021'),('sagadahoc','SA','23023'),('somerset','SO','23025'),
  ('waldo','WL','23027'),('washington','WS','23029'),('york','YO','23031');

-- The query text with a trailing state and country removed, for the whole-string match. The
-- states are whichever ones this build covers; it was the literals me|maine until 2026-09-24,
-- so on any other build the state word survived and venues carrying it in their own name
-- ("Houston TX", "Albany, NY - Albany.com") matched the full string better than the city did.
CREATE OR REPLACE FUNCTION geocode.strip_trailing_state(t text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT nullif(regexp_replace(
           regexp_replace(coalesce(t, ''), '(\s+(us|usa|united states))?$', ''),
           coalesce((SELECT '(\s+(' || string_agg(lower(abbr) || '|' || lower(name), '|') || '))?$'
                     FROM geocode.region_ref), '$'),
           ''), '')
$$;

-- ---------------------------------------------------------------------------------------
-- API limits. Each is a custom PostgreSQL setting, read per request, with the default below
-- when it is not set. Set them on the API role, so they apply to every connection the front
-- ends open and to nothing else:
--   ALTER ROLE pgeo_api SET pgeo.max_circle_km = '150';
-- New connections pick a change up; restart PostgREST (or the API) to apply it at once.
-- A value outside its range is a configuration error, reported as such, rather than ignored.
-- FastAPI reads the same values once at startup (geocode.api_limits()), so both front ends
-- enforce the same limits.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geocode.api_limit_spec()
RETURNS TABLE (name text, default_value double precision, min_value double precision,
               max_value double precision, description text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  VALUES
    ('max_text',       200::float8, 20::float8, 1000::float8, 'characters in text, address and structured fields'),
    ('max_size',        40,          1,          100,          'results per request (size), and ids per /v1/place'),
    ('max_circle_km',  100,          1,          1000,         'boundary.circle.radius on search, structured and autocomplete'),
    ('max_reverse_km',  50,          0.1,        200,          'boundary.circle.radius on reverse'),
    ('max_address_km',   5,          0.1,        50,           'radius on /v1/address')
$$;

CREATE OR REPLACE FUNCTION geocode.api_limit(p_name text) RETURNS double precision
LANGUAGE plpgsql STABLE PARALLEL SAFE AS $$
DECLARE
  spec record;
  raw  text;
BEGIN
  SELECT * INTO spec FROM geocode.api_limit_spec() s WHERE s.name = p_name;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown pgeo limit %', p_name;
  END IF;
  raw := nullif(btrim(current_setting('pgeo.' || p_name, true)), '');
  IF raw IS NULL THEN
    RETURN spec.default_value;
  END IF;
  IF raw !~ '^[0-9]+(\.[0-9]+)?$' OR raw::float8 < spec.min_value OR raw::float8 > spec.max_value THEN
    RAISE EXCEPTION USING ERRCODE = 'F0000',
      MESSAGE = format('server setting pgeo.%s = %L must be a number from %s to %s',
                       p_name, raw, spec.min_value, spec.max_value);
  END IF;
  RETURN raw::float8;
END $$;

-- The limits configured on the pgeo_api role (ALTER ROLE ... SET pgeo.*), checked against their
-- ranges before anything depends on them: pgeo_swap.sh calls this on the staged database and
-- stops before switching if a value is out of range, instead of every request failing after.
-- Returns the effective values. Needs to read pg_db_role_setting, so it is for the owner role.
CREATE OR REPLACE FUNCTION geocode.check_api_role_limits() RETURNS jsonb
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
  kv text;
  k  text;
BEGIN
  FOR kv IN SELECT unnest(s.setconfig) FROM pg_db_role_setting s
            JOIN pg_roles r ON r.oid = s.setrole
            WHERE r.rolname = 'pgeo_api' AND s.setdatabase = 0 LOOP
    IF kv LIKE 'pgeo.%=%' THEN
      k := split_part(substr(kv, 6), '=', 1);
      -- Apply it to this transaction only, then let api_limit judge it as a request would.
      PERFORM set_config('pgeo.' || k, substr(kv, length(k) + 7), true);
      PERFORM geocode.api_limit(k);
    END IF;
  END LOOP;
  RETURN geocode.api_limits();
END $$;

-- Every limit in force for this connection, for the FastAPI front end and for operators.
CREATE OR REPLACE FUNCTION geocode.api_limits() RETURNS jsonb
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT jsonb_object_agg(s.name, geocode.api_limit(s.name)) FROM geocode.api_limit_spec() s
$$;

-- Pelias reverse-geocoding confidence bands by distance.
CREATE OR REPLACE FUNCTION geocode.distance_confidence(meters double precision) RETURNS real
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT CASE
  WHEN meters < 1 THEN 1.0 WHEN meters < 10 THEN 0.9 WHEN meters < 100 THEN 0.8
  WHEN meters < 250 THEN 0.7 WHEN meters < 1000 THEN 0.6 ELSE 0.5 END::real $$;
