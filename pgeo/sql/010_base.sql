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

-- Every US state, the District of Columbia and the territories (USPS abbreviation, name, Census
-- FIPS), whatever the build holds. region_ref is the build's own states; this one is what a query
-- can name. A query naming California is answered from the worldwide tier even on a Maine build,
-- and the parser needs to know "CA" is a state to take it off the end of the address.
DROP TABLE IF EXISTS geocode.state_ref CASCADE;
CREATE TABLE geocode.state_ref (abbr text PRIMARY KEY, name text NOT NULL, fips text NOT NULL);
INSERT INTO geocode.state_ref (abbr, name, fips) VALUES
  ('AL','Alabama','01'),('AK','Alaska','02'),('AZ','Arizona','04'),('AR','Arkansas','05'),('CA','California','06'),('CO','Colorado','08'),('CT','Connecticut','09'),('DE','Delaware','10'),('DC','District of Columbia','11'),('FL','Florida','12'),('GA','Georgia','13'),('HI','Hawaii','15'),('ID','Idaho','16'),('IL','Illinois','17'),('IN','Indiana','18'),('IA','Iowa','19'),('KS','Kansas','20'),('KY','Kentucky','21'),('LA','Louisiana','22'),('ME','Maine','23'),('MD','Maryland','24'),('MA','Massachusetts','25'),('MI','Michigan','26'),('MN','Minnesota','27'),('MS','Mississippi','28'),('MO','Missouri','29'),('MT','Montana','30'),('NE','Nebraska','31'),('NV','Nevada','32'),('NH','New Hampshire','33'),('NJ','New Jersey','34'),('NM','New Mexico','35'),('NY','New York','36'),('NC','North Carolina','37'),('ND','North Dakota','38'),('OH','Ohio','39'),('OK','Oklahoma','40'),('OR','Oregon','41'),('PA','Pennsylvania','42'),('RI','Rhode Island','44'),('SC','South Carolina','45'),('SD','South Dakota','46'),('TN','Tennessee','47'),('TX','Texas','48'),('UT','Utah','49'),('VT','Vermont','50'),('VA','Virginia','51'),('WA','Washington','53'),('WV','West Virginia','54'),('WI','Wisconsin','55'),('WY','Wyoming','56'),('AS','American Samoa','60'),('GU','Guam','66'),('MP','Northern Mariana Islands','69'),('PR','Puerto Rico','72'),('VI','Virgin Islands','78');

-- ISO 3166-1 country codes: alpha-2 (what the data carries), alpha-3 (what Pelias returns as country_a
-- and accepts in boundary.country) and numeric. Kosovo's XKX is the user-assigned code in common use.
-- Overture also uses X-codes for other disputed areas and CP for Clipperton: no alpha-3 exists, so
-- they have no row and their country_a is left out. Reference data, rebuilt on every apply.
DROP TABLE IF EXISTS geocode.country_ref CASCADE;
CREATE TABLE geocode.country_ref (alpha2 text PRIMARY KEY, alpha3 text NOT NULL UNIQUE, numeric_code text NOT NULL);
INSERT INTO geocode.country_ref (alpha2, alpha3, numeric_code) VALUES
  ('AD','AND','020'),('AE','ARE','784'),('AF','AFG','004'),('AG','ATG','028'),('AI','AIA','660'),('AL','ALB','008'),('AM','ARM','051'),('AO','AGO','024'),('AQ','ATA','010'),('AR','ARG','032'),('AS','ASM','016'),('AT','AUT','040'),('AU','AUS','036'),('AW','ABW','533'),('AX','ALA','248'),('AZ','AZE','031'),('BA','BIH','070'),('BB','BRB','052'),('BD','BGD','050'),('BE','BEL','056'),('BF','BFA','854'),('BG','BGR','100'),('BH','BHR','048'),('BI','BDI','108'),('BJ','BEN','204'),('BL','BLM','652'),('BM','BMU','060'),('BN','BRN','096'),('BO','BOL','068'),('BQ','BES','535'),('BR','BRA','076'),('BS','BHS','044'),('BT','BTN','064'),('BV','BVT','074'),('BW','BWA','072'),('BY','BLR','112'),('BZ','BLZ','084'),('CA','CAN','124'),('CC','CCK','166'),('CD','COD','180'),('CF','CAF','140'),('CG','COG','178'),('CH','CHE','756'),('CI','CIV','384'),('CK','COK','184'),('CL','CHL','152'),('CM','CMR','120'),('CN','CHN','156'),('CO','COL','170'),('CR','CRI','188'),('CU','CUB','192'),('CV','CPV','132'),('CW','CUW','531'),('CX','CXR','162'),('CY','CYP','196'),('CZ','CZE','203'),('DE','DEU','276'),('DJ','DJI','262'),('DK','DNK','208'),('DM','DMA','212'),('DO','DOM','214'),('DZ','DZA','012'),('EC','ECU','218'),('EE','EST','233'),('EG','EGY','818'),('EH','ESH','732'),('ER','ERI','232'),('ES','ESP','724'),('ET','ETH','231'),('FI','FIN','246'),('FJ','FJI','242'),('FK','FLK','238'),('FM','FSM','583'),('FO','FRO','234'),('FR','FRA','250'),('GA','GAB','266'),('GB','GBR','826'),('GD','GRD','308'),('GE','GEO','268'),('GF','GUF','254'),('GG','GGY','831'),('GH','GHA','288'),('GI','GIB','292'),('GL','GRL','304'),('GM','GMB','270'),('GN','GIN','324'),('GP','GLP','312'),('GQ','GNQ','226'),('GR','GRC','300'),('GS','SGS','239'),('GT','GTM','320'),('GU','GUM','316'),('GW','GNB','624'),('GY','GUY','328'),('HK','HKG','344'),('HM','HMD','334'),('HN','HND','340'),('HR','HRV','191'),('HT','HTI','332'),('HU','HUN','348'),('ID','IDN','360'),('IE','IRL','372'),('IL','ISR','376'),('IM','IMN','833'),('IN','IND','356'),('IO','IOT','086'),('IQ','IRQ','368'),('IR','IRN','364'),('IS','ISL','352'),('IT','ITA','380'),('JE','JEY','832'),('JM','JAM','388'),('JO','JOR','400'),('JP','JPN','392'),('KE','KEN','404'),('KG','KGZ','417'),('KH','KHM','116'),('KI','KIR','296'),('KM','COM','174'),('KN','KNA','659'),('KP','PRK','408'),('KR','KOR','410'),('KW','KWT','414'),('KY','CYM','136'),('KZ','KAZ','398'),('LA','LAO','418'),('LB','LBN','422'),('LC','LCA','662'),('LI','LIE','438'),('LK','LKA','144'),('LR','LBR','430'),('LS','LSO','426'),('LT','LTU','440'),('LU','LUX','442'),('LV','LVA','428'),('LY','LBY','434'),('MA','MAR','504'),('MC','MCO','492'),('MD','MDA','498'),('ME','MNE','499'),('MF','MAF','663'),('MG','MDG','450'),('MH','MHL','584'),('MK','MKD','807'),('ML','MLI','466'),('MM','MMR','104'),('MN','MNG','496'),('MO','MAC','446'),('MP','MNP','580'),('MQ','MTQ','474'),('MR','MRT','478'),('MS','MSR','500'),('MT','MLT','470'),('MU','MUS','480'),('MV','MDV','462'),('MW','MWI','454'),('MX','MEX','484'),('MY','MYS','458'),('MZ','MOZ','508'),('NA','NAM','516'),('NC','NCL','540'),('NE','NER','562'),('NF','NFK','574'),('NG','NGA','566'),('NI','NIC','558'),('NL','NLD','528'),('NO','NOR','578'),('NP','NPL','524'),('NR','NRU','520'),('NU','NIU','570'),('NZ','NZL','554'),('OM','OMN','512'),('PA','PAN','591'),('PE','PER','604'),('PF','PYF','258'),('PG','PNG','598'),('PH','PHL','608'),('PK','PAK','586'),('PL','POL','616'),('PM','SPM','666'),('PN','PCN','612'),('PR','PRI','630'),('PS','PSE','275'),('PT','PRT','620'),('PW','PLW','585'),('PY','PRY','600'),('QA','QAT','634'),('RE','REU','638'),('RO','ROU','642'),('RS','SRB','688'),('RU','RUS','643'),('RW','RWA','646'),('SA','SAU','682'),('SB','SLB','090'),('SC','SYC','690'),('SD','SDN','729'),('SE','SWE','752'),('SG','SGP','702'),('SH','SHN','654'),('SI','SVN','705'),('SJ','SJM','744'),('SK','SVK','703'),('SL','SLE','694'),('SM','SMR','674'),('SN','SEN','686'),('SO','SOM','706'),('SR','SUR','740'),('SS','SSD','728'),('ST','STP','678'),('SV','SLV','222'),('SX','SXM','534'),('SY','SYR','760'),('SZ','SWZ','748'),('TC','TCA','796'),('TD','TCD','148'),('TF','ATF','260'),('TG','TGO','768'),('TH','THA','764'),('TJ','TJK','762'),('TK','TKL','772'),('TL','TLS','626'),('TM','TKM','795'),('TN','TUN','788'),('TO','TON','776'),('TR','TUR','792'),('TT','TTO','780'),('TV','TUV','798'),('TW','TWN','158'),('TZ','TZA','834'),('UA','UKR','804'),('UG','UGA','800'),('UM','UMI','581'),('US','USA','840'),('UY','URY','858'),('UZ','UZB','860'),('VA','VAT','336'),('VC','VCT','670'),('VE','VEN','862'),('VG','VGB','092'),('VI','VIR','850'),('VN','VNM','704'),('VU','VUT','548'),('WF','WLF','876'),('WS','WSM','882'),('XK','XKX','383'),('YE','YEM','887'),('YT','MYT','175'),('ZA','ZAF','710'),('ZM','ZMB','894'),('ZW','ZWE','716');

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
