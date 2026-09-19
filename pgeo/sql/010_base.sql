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

-- Engine version reported by the SQL API. The loader replaces this placeholder with the
-- package version from pgeo/pyproject.toml after applying 050_api.sql (single source).
CREATE OR REPLACE FUNCTION geocode.engine_version() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT '0+unknown'::text $$;

-- Pelias reverse-geocoding confidence bands by distance.
CREATE OR REPLACE FUNCTION geocode.distance_confidence(meters double precision) RETURNS real
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT CASE
  WHEN meters < 1 THEN 1.0 WHEN meters < 10 THEN 0.9 WHEN meters < 100 THEN 0.8
  WHEN meters < 250 THEN 0.7 WHEN meters < 1000 THEN 0.6 ELSE 0.5 END::real $$;
