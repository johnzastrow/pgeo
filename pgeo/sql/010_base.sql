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
-- punctuation to spaces, and common street/place words reduced to one abbreviation
-- ("Street"/"St" -> "st", "North" -> "n", "Mount" -> "mt", "Saint" -> "st", ...).
CREATE OR REPLACE FUNCTION geocode.norm(t text) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE STRICT
AS $$
DECLARE
  s text := lower(geocode.unaccent_i(t));
BEGIN
  s := regexp_replace(s, '[''’]', '', 'g');                 -- O'Brien -> obrien
  s := regexp_replace(s, '[^a-z0-9]+', ' ', 'g');
  -- Double the separators so adjacent words ("north east") each keep their own
  -- bounding spaces for the substitutions below; collapsed again at the end.
  s := ' ' || replace(trim(s), ' ', '  ') || ' ';
  -- word substitutions (bounded by spaces so partial words are untouched)
  s := replace(s, ' street ', ' st ');   s := replace(s, ' road ', ' rd ');
  s := replace(s, ' avenue ', ' ave ');  s := replace(s, ' av ', ' ave ');
  s := replace(s, ' drive ', ' dr ');    s := replace(s, ' lane ', ' ln ');
  s := replace(s, ' court ', ' ct ');    s := replace(s, ' circle ', ' cir ');
  s := replace(s, ' place ', ' pl ');    s := replace(s, ' terrace ', ' ter ');
  s := replace(s, ' boulevard ', ' blvd '); s := replace(s, ' parkway ', ' pkwy ');
  s := replace(s, ' highway ', ' hwy '); s := replace(s, ' route ', ' rte ');
  s := replace(s, ' rt ', ' rte ');      s := replace(s, ' extension ', ' ext ');
  s := replace(s, ' square ', ' sq ');   s := replace(s, ' point ', ' pt ');
  s := replace(s, ' mountain ', ' mtn ');
  s := replace(s, ' mount ', ' mt ');    s := replace(s, ' saint ', ' st ');
  s := replace(s, ' fort ', ' ft ');
  s := replace(s, ' north ', ' n ');     s := replace(s, ' south ', ' s ');
  s := replace(s, ' east ', ' e ');      s := replace(s, ' west ', ' w ');
  s := replace(s, ' northeast ', ' ne '); s := replace(s, ' northwest ', ' nw ');
  s := replace(s, ' southeast ', ' se '); s := replace(s, ' southwest ', ' sw ');
  RETURN trim(regexp_replace(s, ' +', ' ', 'g'));
END
$$;

-- Leading integer of a house number ("12A" -> 12, "10-12" -> 10), NULL if none.
CREATE OR REPLACE FUNCTION geocode.hn_int(hn text) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT nullif(substring(hn from '^\s*(\d{1,7})'), '')::integer $$;

-- Pelias reverse-geocoding confidence bands by distance.
CREATE OR REPLACE FUNCTION geocode.distance_confidence(meters double precision) RETURNS real
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT CASE
  WHEN meters < 1 THEN 1.0 WHEN meters < 10 THEN 0.9 WHEN meters < 100 THEN 0.8
  WHEN meters < 250 THEN 0.7 WHEN meters < 1000 THEN 0.6 ELSE 0.5 END::real $$;
