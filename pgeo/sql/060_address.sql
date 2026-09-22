-- Structured US addresses in the style of USPS Publication 28 (Postal Addressing Standards).
--
-- geocode_api.v1_address returns, for a selected feature (ids=), a free-text address (text=)
-- or a coordinate (lat=/lon=), the address split into Publication 28 components with the
-- standard abbreviations, the delivery and last lines, and the place context (municipality,
-- county, FIPS codes). Non-address features (venues, towns, lakes) get their place context
-- plus the nearest address point, flagged as such.
--
-- Scope: this standardizes what the source data says; it is not CASS-certified and does not
-- check deliverability. ZIP+4 is not in the open data, so it is always null.
-- Reference tables below are USPS Publication 28 Appendix B (states), C1 (street suffixes)
-- and C2 (secondary unit designators), and Census FIPS codes for Maine's counties.
-- Applied by `pgeo-load build` and `pgeo-load functions` after 050_api.sql.

-- ---------------------------------------------------------------------------------------
-- Reference data (schema geocode: survives rebuilds; recreated here so edits apply)
-- ---------------------------------------------------------------------------------------
DROP TABLE IF EXISTS geocode.usps_suffix, geocode.usps_unit, geocode.usps_direction, geocode.county_fips CASCADE;
-- county FIPS codes live in geocode.county_ref and state ones in geocode.region_ref
-- (010_base.sql), shared with the build
-- OUT-parameter functions cannot change their result columns in place
DROP FUNCTION IF EXISTS geocode.usps_street(text), geocode.usps_secondary(text),
  geocode.nearest_address(geometry, double precision),
  geocode.usps_json(pgeo.feature, text, double precision),
  geocode.address_result(pgeo.feature, text, real, double precision) CASCADE;

-- C1: every spelling found in addresses (common abbreviations and full names) -> standard
CREATE TABLE geocode.usps_suffix (variant text PRIMARY KEY, standard text NOT NULL);
INSERT INTO geocode.usps_suffix (variant, standard)
SELECT v, s FROM (VALUES
  ('ALLEY','ALY'),('ALLEE','ALY'),('ALLY','ALY'),('ALY','ALY'),('ANNEX','ANX'),('ANEX','ANX'),('ANX','ANX'),
  ('ARCADE','ARC'),('ARC','ARC'),('AVENUE','AVE'),('AV','AVE'),('AVE','AVE'),('AVEN','AVE'),('AVENU','AVE'),
  ('AVN','AVE'),('AVNUE','AVE'),('BAYOU','BYU'),('BAYOO','BYU'),('BEACH','BCH'),('BCH','BCH'),('BEND','BND'),
  ('BND','BND'),('BLUFF','BLF'),('BLUF','BLF'),('BLF','BLF'),('BLUFFS','BLFS'),('BOTTOM','BTM'),('BOT','BTM'),
  ('BTM','BTM'),('BOTTM','BTM'),('BOULEVARD','BLVD'),('BLVD','BLVD'),('BOUL','BLVD'),('BOULV','BLVD'),
  ('BRANCH','BR'),('BR','BR'),('BRNCH','BR'),('BRIDGE','BRG'),('BRDGE','BRG'),('BRG','BRG'),('BROOK','BRK'),
  ('BRK','BRK'),('BROOKS','BRKS'),('BURG','BG'),('BURGS','BGS'),('BYPASS','BYP'),('BYP','BYP'),('BYPA','BYP'),
  ('BYPAS','BYP'),('BYPS','BYP'),('CAMP','CP'),('CP','CP'),('CMP','CP'),('CANYON','CYN'),('CANYN','CYN'),
  ('CNYN','CYN'),('CAPE','CPE'),('CPE','CPE'),('CAUSEWAY','CSWY'),('CAUSWA','CSWY'),('CSWY','CSWY'),
  ('CENTER','CTR'),('CEN','CTR'),('CENT','CTR'),('CENTR','CTR'),('CENTRE','CTR'),('CNTER','CTR'),('CNTR','CTR'),
  ('CTR','CTR'),('CENTERS','CTRS'),('CIRCLE','CIR'),('CIR','CIR'),('CIRC','CIR'),('CIRCL','CIR'),('CRCL','CIR'),
  ('CRCLE','CIR'),('CIRCLES','CIRS'),('CLIFF','CLF'),('CLF','CLF'),('CLIFFS','CLFS'),('CLFS','CLFS'),
  ('CLUB','CLB'),('CLB','CLB'),('COMMON','CMN'),('COMMONS','CMNS'),('CORNER','COR'),('COR','COR'),
  ('CORNERS','CORS'),('CORS','CORS'),('COURSE','CRSE'),('CRSE','CRSE'),('COURT','CT'),('CT','CT'),('CRT','CT'),
  ('COURTS','CTS'),('CTS','CTS'),('COVE','CV'),('CV','CV'),('COVES','CVS'),('CREEK','CRK'),('CRK','CRK'),
  ('CRESCENT','CRES'),('CRES','CRES'),('CRSENT','CRES'),('CRSNT','CRES'),('CREST','CRST'),('CROSSING','XING'),
  ('CRSSNG','XING'),('XING','XING'),('CROSSROAD','XRD'),('CROSSROADS','XRDS'),('CURVE','CURV'),('DALE','DL'),
  ('DL','DL'),('DAM','DM'),('DM','DM'),('DIVIDE','DV'),('DIV','DV'),('DV','DV'),('DVD','DV'),('DRIVE','DR'),
  ('DR','DR'),('DRIV','DR'),('DRV','DR'),('DRIVES','DRS'),('ESTATE','EST'),('EST','EST'),('ESTATES','ESTS'),
  ('ESTS','ESTS'),('EXPRESSWAY','EXPY'),('EXP','EXPY'),('EXPR','EXPY'),('EXPRESS','EXPY'),('EXPW','EXPY'),
  ('EXPY','EXPY'),('EXTENSION','EXT'),('EXT','EXT'),('EXTN','EXT'),('EXTNSN','EXT'),('EXTENSIONS','EXTS'),
  ('EXTS','EXTS'),('FALL','FALL'),('FALLS','FLS'),('FLS','FLS'),('FERRY','FRY'),('FRRY','FRY'),('FRY','FRY'),
  ('FIELD','FLD'),('FLD','FLD'),('FIELDS','FLDS'),('FLDS','FLDS'),('FLAT','FLT'),('FLT','FLT'),('FLATS','FLTS'),
  ('FLTS','FLTS'),('FORD','FRD'),('FRD','FRD'),('FORDS','FRDS'),('FOREST','FRST'),('FORESTS','FRST'),
  ('FRST','FRST'),('FORGE','FRG'),('FORG','FRG'),('FRG','FRG'),('FORGES','FRGS'),('FORK','FRK'),('FRK','FRK'),
  ('FORKS','FRKS'),('FRKS','FRKS'),('FORT','FT'),('FRT','FT'),('FT','FT'),('FREEWAY','FWY'),('FREEWY','FWY'),
  ('FRWAY','FWY'),('FRWY','FWY'),('FWY','FWY'),('GARDEN','GDN'),('GARDN','GDN'),('GRDEN','GDN'),('GRDN','GDN'),
  ('GDN','GDN'),('GARDENS','GDNS'),('GDNS','GDNS'),('GRDNS','GDNS'),('GATEWAY','GTWY'),('GATEWY','GTWY'),
  ('GATWAY','GTWY'),('GTWAY','GTWY'),('GTWY','GTWY'),('GLEN','GLN'),('GLN','GLN'),('GLENS','GLNS'),
  ('GREEN','GRN'),('GRN','GRN'),('GREENS','GRNS'),('GROVE','GRV'),('GROV','GRV'),('GRV','GRV'),
  ('GROVES','GRVS'),('HARBOR','HBR'),('HARB','HBR'),('HARBR','HBR'),('HBR','HBR'),('HRBOR','HBR'),
  ('HARBORS','HBRS'),('HAVEN','HVN'),('HVN','HVN'),('HEIGHTS','HTS'),('HT','HTS'),('HTS','HTS'),
  ('HIGHWAY','HWY'),('HIGHWY','HWY'),('HIWAY','HWY'),('HIWY','HWY'),('HWAY','HWY'),('HWY','HWY'),('HILL','HL'),
  ('HL','HL'),('HILLS','HLS'),('HLS','HLS'),('HOLLOW','HOLW'),('HLLW','HOLW'),('HOLLOWS','HOLW'),('HOLW','HOLW'),
  ('HOLWS','HOLW'),('INLET','INLT'),('INLT','INLT'),('ISLAND','IS'),('IS','IS'),('ISLND','IS'),
  ('ISLANDS','ISS'),('ISLNDS','ISS'),('ISS','ISS'),('ISLE','ISLE'),('ISLES','ISLE'),('JUNCTION','JCT'),
  ('JCT','JCT'),('JCTION','JCT'),('JCTN','JCT'),('JUNCTN','JCT'),('JUNCTON','JCT'),('JUNCTIONS','JCTS'),
  ('JCTNS','JCTS'),('JCTS','JCTS'),('KEY','KY'),('KY','KY'),('KEYS','KYS'),('KYS','KYS'),('KNOLL','KNL'),
  ('KNL','KNL'),('KNOL','KNL'),('KNOLLS','KNLS'),('KNLS','KNLS'),('LAKE','LK'),('LK','LK'),('LAKES','LKS'),
  ('LKS','LKS'),('LAND','LAND'),('LANDING','LNDG'),('LNDG','LNDG'),('LNDNG','LNDG'),('LANE','LN'),('LN','LN'),
  ('LIGHT','LGT'),('LGT','LGT'),('LIGHTS','LGTS'),('LOAF','LF'),('LF','LF'),('LOCK','LCK'),('LCK','LCK'),
  ('LOCKS','LCKS'),('LCKS','LCKS'),('LODGE','LDG'),('LDG','LDG'),('LDGE','LDG'),('LODG','LDG'),('LOOP','LOOP'),
  ('LOOPS','LOOP'),('MALL','MALL'),('MANOR','MNR'),('MNR','MNR'),('MANORS','MNRS'),('MNRS','MNRS'),
  ('MEADOW','MDW'),('MDW','MDW'),('MEADOWS','MDWS'),('MDWS','MDWS'),('MEDOWS','MDWS'),('MEWS','MEWS'),
  ('MILL','ML'),('ML','ML'),('MILLS','MLS'),('MLS','MLS'),('MISSION','MSN'),('MISSN','MSN'),('MSSN','MSN'),
  ('MSN','MSN'),('MOTORWAY','MTWY'),('MNT','MT'),('MOUNT','MT'),('MT','MT'),('MOUNTAIN','MTN'),('MNTAIN','MTN'),
  ('MNTN','MTN'),('MOUNTIN','MTN'),('MTIN','MTN'),('MTN','MTN'),('MOUNTAINS','MTNS'),('MNTNS','MTNS'),
  ('NECK','NCK'),('NCK','NCK'),('ORCHARD','ORCH'),('ORCH','ORCH'),('ORCHRD','ORCH'),('OVAL','OVAL'),
  ('OVL','OVAL'),('OVERPASS','OPAS'),('PARK','PARK'),('PRK','PARK'),('PARKS','PARK'),('PARKWAY','PKWY'),
  ('PARKWY','PKWY'),('PKWAY','PKWY'),('PKWY','PKWY'),('PKY','PKWY'),('PARKWAYS','PKWY'),('PKWYS','PKWY'),
  ('PASS','PASS'),('PASSAGE','PSGE'),('PATH','PATH'),('PATHS','PATH'),('PIKE','PIKE'),('PIKES','PIKE'),
  ('PINE','PNE'),('PINES','PNES'),('PNES','PNES'),('PLACE','PL'),('PL','PL'),('PLAIN','PLN'),('PLN','PLN'),
  ('PLAINS','PLNS'),('PLNS','PLNS'),('PLAZA','PLZ'),('PLZ','PLZ'),('PLZA','PLZ'),('POINT','PT'),('PT','PT'),
  ('POINTS','PTS'),('PTS','PTS'),('PORT','PRT'),('PRT','PRT'),('PORTS','PRTS'),('PRTS','PRTS'),
  ('PRAIRIE','PR'),('PR','PR'),('PRR','PR'),('RADIAL','RADL'),('RAD','RADL'),('RADIEL','RADL'),('RADL','RADL'),
  ('RAMP','RAMP'),('RANCH','RNCH'),('RANCHES','RNCH'),('RNCH','RNCH'),('RNCHS','RNCH'),('RAPID','RPD'),
  ('RPD','RPD'),('RAPIDS','RPDS'),('RPDS','RPDS'),('REST','RST'),('RST','RST'),('RIDGE','RDG'),('RDG','RDG'),
  ('RDGE','RDG'),('RIDGES','RDGS'),('RDGS','RDGS'),('RIVER','RIV'),('RIV','RIV'),('RVR','RIV'),('RIVR','RIV'),
  ('ROAD','RD'),('RD','RD'),('ROADS','RDS'),('RDS','RDS'),('ROUTE','RTE'),('RTE','RTE'),('ROW','ROW'),
  ('RUE','RUE'),('RUN','RUN'),('SHOAL','SHL'),('SHL','SHL'),('SHOALS','SHLS'),('SHLS','SHLS'),('SHORE','SHR'),
  ('SHOAR','SHR'),('SHR','SHR'),('SHORES','SHRS'),('SHOARS','SHRS'),('SHRS','SHRS'),('SKYWAY','SKWY'),
  ('SPRING','SPG'),('SPG','SPG'),('SPNG','SPG'),('SPRNG','SPG'),('SPRINGS','SPGS'),('SPGS','SPGS'),
  ('SPNGS','SPGS'),('SPRNGS','SPGS'),('SPUR','SPUR'),('SPURS','SPUR'),('SQUARE','SQ'),('SQ','SQ'),('SQR','SQ'),
  ('SQRE','SQ'),('SQU','SQ'),('SQUARES','SQS'),('SQRS','SQS'),('STATION','STA'),('STA','STA'),('STATN','STA'),
  ('STN','STA'),('STRAVENUE','STRA'),('STRA','STRA'),('STREAM','STRM'),('STREME','STRM'),('STRM','STRM'),
  ('STREET','ST'),('ST','ST'),('STR','ST'),('STRT','ST'),('STREETS','STS'),('SUMMIT','SMT'),('SMT','SMT'),
  ('SUMIT','SMT'),('SUMITT','SMT'),('TERRACE','TER'),('TER','TER'),('TERR','TER'),('THROUGHWAY','TRWY'),
  ('TRACE','TRCE'),('TRACES','TRCE'),('TRCE','TRCE'),('TRACK','TRAK'),('TRACKS','TRAK'),('TRAK','TRAK'),
  ('TRK','TRAK'),('TRKS','TRAK'),('TRAFFICWAY','TRFY'),('TRAIL','TRL'),('TRAILS','TRL'),('TRL','TRL'),
  ('TRLS','TRL'),('TRAILER','TRLR'),('TRLR','TRLR'),('TRLRS','TRLR'),('TUNNEL','TUNL'),('TUNEL','TUNL'),
  ('TUNL','TUNL'),('TUNLS','TUNL'),('TUNNELS','TUNL'),('TUNNL','TUNL'),('TURNPIKE','TPKE'),('TRNPK','TPKE'),
  ('TURNPK','TPKE'),('TPKE','TPKE'),('UNDERPASS','UPAS'),('UNION','UN'),('UN','UN'),('UNIONS','UNS'),
  ('VALLEY','VLY'),('VALLY','VLY'),('VLLY','VLY'),('VLY','VLY'),('VALLEYS','VLYS'),('VLYS','VLYS'),
  ('VIADUCT','VIA'),('VDCT','VIA'),('VIA','VIA'),('VIADCT','VIA'),('VIEW','VW'),('VW','VW'),('VIEWS','VWS'),
  ('VWS','VWS'),('VILLAGE','VLG'),('VILL','VLG'),('VILLAG','VLG'),('VILLG','VLG'),('VILLIAGE','VLG'),
  ('VLG','VLG'),('VILLAGES','VLGS'),('VLGS','VLGS'),('VILLE','VL'),('VL','VL'),('VISTA','VIS'),('VIS','VIS'),
  ('VIST','VIS'),('VST','VIS'),('VSTA','VIS'),('WALK','WALK'),('WALKS','WALK'),('WALL','WALL'),('WAY','WAY'),
  ('WY','WAY'),('WAYS','WAYS'),('WELL','WL'),('WELLS','WLS'),('WLS','WLS')
) t(v, s);

-- C2: secondary unit designators (variant -> standard)
CREATE TABLE geocode.usps_unit (variant text PRIMARY KEY, standard text NOT NULL);
INSERT INTO geocode.usps_unit (variant, standard) VALUES
  ('APARTMENT','APT'),('APT','APT'),('BASEMENT','BSMT'),('BSMT','BSMT'),('BUILDING','BLDG'),('BLDG','BLDG'),
  ('DEPARTMENT','DEPT'),('DEPT','DEPT'),('FLOOR','FL'),('FL','FL'),('FRONT','FRNT'),('FRNT','FRNT'),
  ('HANGAR','HNGR'),('HNGR','HNGR'),('KEY','KEY'),('LOBBY','LBBY'),('LBBY','LBBY'),('LOT','LOT'),
  ('LOWER','LOWR'),('LOWR','LOWR'),('OFFICE','OFC'),('OFC','OFC'),('PENTHOUSE','PH'),('PH','PH'),
  ('PIER','PIER'),('REAR','REAR'),('ROOM','RM'),('RM','RM'),('SIDE','SIDE'),('SLIP','SLIP'),('SPACE','SPC'),
  ('SPC','SPC'),('STOP','STOP'),('SUITE','STE'),('STE','STE'),('TRAILER','TRLR'),('TRLR','TRLR'),
  ('UNIT','UNIT'),('UPPER','UPPR'),('UPPR','UPPR');

CREATE TABLE geocode.usps_direction (variant text PRIMARY KEY, standard text NOT NULL);
INSERT INTO geocode.usps_direction (variant, standard) VALUES
  ('N','N'),('NORTH','N'),('S','S'),('SOUTH','S'),('E','E'),('EAST','E'),('W','W'),('WEST','W'),
  ('NE','NE'),('NORTHEAST','NE'),('NW','NW'),('NORTHWEST','NW'),('SE','SE'),('SOUTHEAST','SE'),
  ('SW','SW'),('SOUTHWEST','SW');

GRANT SELECT ON geocode.usps_suffix, geocode.usps_unit, geocode.usps_direction,
  geocode.county_ref, geocode.region_ref TO pgeo_api;

-- Invalid input: same error code as the 050 helpers, which the APIs return as HTTP 400
CREATE OR REPLACE FUNCTION geocode.check_fail(msg text) RETURNS void
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = msg;
END $$;

-- ---------------------------------------------------------------------------------------
-- Street line: "North Main Street Extension" -> predir N, name MAIN, suffix ST, postdir null
-- ---------------------------------------------------------------------------------------
-- Rules (Publication 28 section 2):
-- - uppercase, punctuation removed except the hyphen inside house numbers and "1/2"
-- - a leading direction is a predirectional only when a name follows it ("North Rd" keeps
--   NORTH as the name: USPS spells a direction out when it is the street name)
-- - the last word is the suffix when it is a C1 word and not the only word
-- - a trailing direction after the suffix is a postdirectional
-- - numbered routes and highways ("US Route 1", "State Route 9", "Route 202") keep their
--   words as the name; the number is not a suffix
CREATE OR REPLACE FUNCTION geocode.usps_street(p_street text,
    OUT predirectional text, OUT street_name text, OUT suffix text, OUT postdirectional text,
    OUT post_modifier text)
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  w   text[] := regexp_split_to_array(
                  trim(regexp_replace(regexp_replace(upper(geocode.unaccent_i(coalesce(p_street, ''))),
                       '[.,''’#]', '', 'g'), '\s+', ' ', 'g')), ' ');
  n   integer;
  d   text;
  s   text;
BEGIN
  IF w = ARRAY[''] OR w IS NULL THEN
    RETURN;
  END IF;
  n := cardinality(w);
  -- postdirectional: last word is a direction after a suffix ("Park Ave W"), a route number
  -- ("US Route 2 W"), or a one-word name ("Parkway N")
  IF n >= 2 THEN
    SELECT standard INTO d FROM geocode.usps_direction WHERE variant = w[n];
    IF d IS NOT NULL AND (
         (n >= 3 AND (w[n - 1] ~ '^\d+[A-Z]?$' OR EXISTS (SELECT 1 FROM geocode.usps_suffix WHERE variant = w[n - 1])))
         OR (n = 2 AND NOT EXISTS (SELECT 1 FROM geocode.usps_direction WHERE variant = w[1]))) THEN
      postdirectional := d;
      w := w[1:n - 1];
      n := n - 1;
    END IF;
  END IF;
  -- predirectional: first word is a direction and at least one more word follows
  -- (two more when the rest would be only a suffix: "North Street" keeps NORTH as the name)
  IF n >= 2 THEN
    SELECT standard INTO d FROM geocode.usps_direction WHERE variant = w[1];
    IF d IS NOT NULL AND NOT (n = 2 AND EXISTS (SELECT 1 FROM geocode.usps_suffix WHERE variant = w[2])) THEN
      predirectional := d;
      w := w[2:n];
      n := n - 1;
    END IF;
  END IF;
  -- numbered routes: the name ends with a number, so there is no suffix
  IF w[n] ~ '^\d+[A-Z]?$' AND n >= 2 THEN
    street_name := array_to_string(
      ARRAY(SELECT CASE x WHEN 'RT' THEN 'ROUTE' WHEN 'RTE' THEN 'ROUTE' WHEN 'HWY' THEN 'HIGHWAY'
                          ELSE x END FROM unnest(w) x), ' ');
    RETURN;
  END IF;
  -- "Main Street Extension": EXT after another suffix is a trailing modifier (MAIN ST EXT)
  IF n >= 3 AND w[n] IN ('EXTENSION', 'EXT', 'EXTN', 'EXTNSN')
     AND EXISTS (SELECT 1 FROM geocode.usps_suffix WHERE variant = w[n - 1]) THEN
    post_modifier := 'EXT';
    w := w[1:n - 1];
    n := n - 1;
  END IF;
  -- suffix: last word, when it is a C1 word and not the whole name
  IF n >= 2 THEN
    SELECT standard INTO s FROM geocode.usps_suffix WHERE variant = w[n];
    IF s IS NOT NULL THEN
      suffix := s;
      w := w[1:n - 1];
    END IF;
  END IF;
  street_name := array_to_string(w, ' ');
END
$$;

-- Secondary unit: "Apt 2", "Suite 200", "#3", "2B" -> (APT, 2) / (STE, 200) / (#, 3) / (#, 2B)
CREATE OR REPLACE FUNCTION geocode.usps_secondary(p_unit text, OUT designator text, OUT unit_number text)
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  u text := trim(regexp_replace(upper(coalesce(p_unit, '')), '[.,]', '', 'g'));
  m text[];
BEGIN
  IF u = '' THEN
    RETURN;
  END IF;
  m := regexp_match(u, '^([A-Z]+)\s*#?\s*([A-Z0-9-]*)$');
  IF m IS NOT NULL THEN
    SELECT standard INTO designator FROM geocode.usps_unit WHERE variant = m[1];
    IF designator IS NOT NULL THEN
      unit_number := nullif(m[2], '');
      RETURN;
    END IF;
  END IF;
  -- no known designator: Publication 28 uses "#" before the number
  designator := '#';
  unit_number := regexp_replace(u, '^#\s*', '');
END
$$;

-- ---------------------------------------------------------------------------------------
-- Structured address for one feature
-- ---------------------------------------------------------------------------------------
-- p_kind: 'exact' (the feature is the address), 'nearest' (nearest address to a place or a
-- coordinate), 'interpolated' (position estimated between known house numbers)
CREATE OR REPLACE FUNCTION geocode.usps_json(f pgeo.feature, p_kind text, p_distance_m double precision,
                                             p_unit text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  st   record;
  sec  record;
  city text;
  zip  text := substring(f.postcode from '\d{5}');
  hn   text := upper(regexp_replace(coalesce(f.housenumber, ''), '\s+', '', 'g'));
  line text;
BEGIN
  SELECT * INTO st FROM geocode.usps_street(f.street);
  -- Units come only from the caller (typed in the text, or unit=). Search collapses the
  -- per-unit address records of a building into one, so a record's own unit is arbitrary.
  SELECT * INTO sec FROM geocode.usps_secondary(p_unit);
  -- last-line city: the postal city when the source gives one (OpenAddresses "South Paris"),
  -- else the municipality
  city := upper(coalesce(nullif(f.postal_locality_norm, ''), geocode.norm(coalesce(f.locality, f.localadmin))));
  line := concat_ws(' ', nullif(hn, ''), st.predirectional, st.street_name, st.suffix, st.post_modifier,
                    st.postdirectional, sec.designator, sec.unit_number);
  RETURN jsonb_strip_nulls(jsonb_build_object(
    'match', p_kind,
    'distance_m', CASE WHEN p_distance_m IS NULL THEN NULL ELSE round(p_distance_m::numeric, 1) END,
    'primary_number', nullif(hn, ''),
    'predirectional', st.predirectional,
    'street_name', st.street_name,
    'suffix', st.suffix,
    'post_modifier', st.post_modifier,
    'postdirectional', st.postdirectional,
    'secondary_designator', sec.designator,
    'secondary_number', sec.unit_number,
    'city', nullif(city, ''),
    'state', upper(f.region_a),
    'zip5', zip,
    'delivery_line', nullif(line, ''),
    'last_line', nullif(concat_ws(' ', nullif(city, ''), upper(f.region_a), zip), ''),
    'source', f.source,
    'gid', f.gid,
    -- the address point itself (for a 'nearest' match it differs from the selected place)
    'lat', round(ST_Y(f.geom)::numeric, 6),
    'lon', round(ST_X(f.geom)::numeric, 6)
  ));
END
$$;

-- Place context for any feature: municipality, county, FIPS codes, coordinates
CREATE OR REPLACE FUNCTION geocode.place_json(f pgeo.feature) RETURNS jsonb
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'name', f.name,
    'layer', f.layer,
    'municipality', coalesce(f.locality, f.localadmin),
    'neighbourhood', f.neighbourhood,
    'county', f.county,
    -- County names repeat across states, so the state's FIPS has to match; county_ref holds
    -- Maine's counties only, so this is null for other states rather than wrong.
    'county_fips', (SELECT c.fips FROM geocode.county_ref c
                    WHERE c.county = lower(regexp_replace(coalesce(f.county, ''), '\s+County$', '', 'i'))
                      AND substr(c.fips, 1, 2) = (SELECT r.fips FROM geocode.region_ref r
                                                  WHERE r.abbr = f.region_a)),
    'state', f.region,
    'state_code', f.region_a,
    'state_fips', (SELECT r.fips FROM geocode.region_ref r WHERE r.abbr = f.region_a),
    'zip5', substring(f.postcode from '\d{5}'),
    'lat', round(ST_Y(f.geom)::numeric, 6),
    'lon', round(ST_X(f.geom)::numeric, 6)))
$$;

-- Nearest address point to a geometry (KNN on the geometry index), within p_radius_m
CREATE OR REPLACE FUNCTION geocode.nearest_address(p_geom geometry, p_radius_m double precision,
    OUT f pgeo.feature, OUT distance_m double precision)
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT n.x, n.d FROM (
    SELECT x, ST_Distance(x.geom::geography, p_geom::geography) AS d
    FROM pgeo.feature x
    WHERE x.layer = 'address'
    ORDER BY x.geom <-> p_geom          -- index-ordered nearest neighbour
    LIMIT 1) n
  WHERE n.d <= p_radius_m
$$;

-- One result object: place context + structured address (the feature's own, or the nearest)
CREATE OR REPLACE FUNCTION geocode.address_result(f pgeo.feature, p_query_kind text,
    p_confidence real, p_radius_m double precision, p_unit text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  near record;
  addr jsonb;
BEGIN
  IF f.layer = 'address' THEN
    addr := geocode.usps_json(f, p_query_kind, NULL, p_unit);
  ELSIF f.layer IN ('venue', 'street') THEN
    -- a venue or street has a street address nearby; a town or county centroid does not
    SELECT * INTO near FROM geocode.nearest_address(f.geom, p_radius_m);
    IF near.distance_m IS NOT NULL THEN
      addr := geocode.usps_json(near.f, 'nearest', near.distance_m, NULL);
    END IF;
  END IF;
  RETURN jsonb_build_object(
    'type', 'Feature',
    'geometry', jsonb_build_object('type', 'Point', 'coordinates', jsonb_build_array(ST_X(f.geom), ST_Y(f.geom))),
    'properties', jsonb_strip_nulls(jsonb_build_object(
      'gid', f.gid, 'label', f.label, 'confidence', p_confidence,
      'place', geocode.place_json(f),
      'usps', addr)));
END
$$;

-- ---------------------------------------------------------------------------------------
-- HTTP API: /v1/address?ids=... | ?text=... | ?point.lat=&point.lon=
-- ---------------------------------------------------------------------------------------
-- Exactly one input. ids: up to 10 gids from search results. text: the best search match.
-- lat/lon: the nearest address point within radius (km, default 0.5, max 5), else the
-- place context. unit: secondary unit to add ("Apt 2"); in text= mode it is also read from
-- the text.
-- Argument names are the last segment of the Pelias-style names (PostgREST keeps only that).
CREATE OR REPLACE FUNCTION geocode_api.v1_address(
    ids text DEFAULT NULL, text text DEFAULT NULL,
    lat double precision DEFAULT NULL, lon double precision DEFAULT NULL,
    radius double precision DEFAULT NULL, unit text DEFAULT NULL,
    lang text DEFAULT NULL, api_key text DEFAULT NULL, debug text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
  n_inputs integer := (ids IS NOT NULL)::int + (text IS NOT NULL)::int + (lat IS NOT NULL OR lon IS NOT NULL)::int;
  r_m      double precision := coalesce(radius, 0.5) * 1000;
  gids     text[];
  feats    jsonb := '[]'::jsonb;
  f        pgeo.feature;
  h        geocode.hit;
  p        record;
  near     record;
  q        jsonb;
  unit_q   text;
BEGIN
  PERFORM geocode.check_extras(NULL, NULL, NULL, lang, api_key);
  IF n_inputs <> 1 THEN
    PERFORM geocode.check_fail('give exactly one of ids, text, or point.lat + point.lon');
  END IF;
  IF unit IS NOT NULL AND (length(unit) > 20 OR unit !~ '^[A-Za-z0-9 #.-]+$') THEN
    PERFORM geocode.check_fail('unit: up to 20 letters, digits, spaces, # . -');
  END IF;
  IF radius IS NOT NULL AND (radius <= 0 OR radius > 5) THEN
    PERFORM geocode.check_fail('radius must be > 0 and <= 5 (km)');
  END IF;

  IF ids IS NOT NULL THEN
    gids := ARRAY(SELECT trim(g) FROM unnest(string_to_array(ids, ',')) g WHERE trim(g) <> '');
    IF cardinality(gids) = 0 OR cardinality(gids) > 10
       OR EXISTS (SELECT 1 FROM unnest(gids) g WHERE length(g) > 200 OR g !~ '^[a-z_]+:[a-z]+:[^\s]+$') THEN
      PERFORM geocode.check_fail('ids: 1 to 10 gids like openaddresses:address:<id>');
    END IF;
    q := jsonb_build_object('ids', to_jsonb(gids));
    FOR f IN SELECT x.* FROM unnest(gids) WITH ORDINALITY g(gid, ord)
             JOIN pgeo.feature x ON x.gid = g.gid ORDER BY g.ord LOOP
      feats := feats || geocode.address_result(f, 'exact', 1.0, r_m, unit);
    END LOOP;

  ELSIF text IS NOT NULL THEN
    IF length(text) > 200 OR btrim(text) = '' THEN
      PERFORM geocode.check_fail('text: 1 to 200 characters');
    END IF;
    q := jsonb_build_object('text', text);
    SELECT * INTO p FROM geocode.parse_rule(text);
    -- the parser drops units; keep the query's own ("389 Congress St Apt 2")
    unit_q := (SELECT nullif(concat_ws(' ', m[1], m[2]), '') FROM regexp_match(text,
                 '\m(apt|apartment|unit|ste|suite|bldg|building|fl|floor|rm|room|lot|trlr|trailer)\M\.?\s*#?\s*([a-z0-9-]+)', 'i') m);
    IF unit_q IS NULL THEN
      unit_q := (SELECT m[1] FROM regexp_match(text, '#\s*([a-z0-9-]+)', 'i') m);
    END IF;
    SELECT * INTO h FROM geocode.search(text, p.name, p.housenumber, p.street, p.locality, p.postcode,
                                         NULL, NULL, NULL, NULL, NULL, 1);
    IF h.gid IS NOT NULL AND h.id IS NOT NULL THEN
      SELECT * INTO f FROM pgeo.feature WHERE id = h.id;
      feats := feats || geocode.address_result(f,
                 CASE WHEN h.match_type = 'exact' THEN 'exact' ELSE 'fallback' END, h.confidence, r_m,
                 coalesce(unit, unit_q));
    ELSIF h.gid IS NOT NULL THEN
      -- interpolated address: no stored feature; return the nearest known address point
      SELECT * INTO near FROM geocode.nearest_address(ST_SetSRID(ST_MakePoint(h.lon, h.lat), 4326), r_m);
      IF near.distance_m IS NOT NULL THEN
        feats := feats || geocode.address_result(near.f, 'nearest', h.confidence, r_m);
      END IF;
    END IF;

  ELSE
    IF lat IS NULL OR lon IS NULL OR lat NOT BETWEEN -90 AND 90 OR lon NOT BETWEEN -180 AND 180 THEN
      PERFORM geocode.check_fail('point.lat and point.lon are both required and must be valid coordinates');
    END IF;
    q := jsonb_build_object('point.lat', lat, 'point.lon', lon, 'radius', r_m / 1000);
    SELECT * INTO near FROM geocode.nearest_address(ST_SetSRID(ST_MakePoint(lon, lat), 4326), r_m);
    IF near.distance_m IS NOT NULL THEN
      feats := feats || jsonb_set(geocode.address_result(near.f, 'nearest', NULL, r_m, unit),
                                  '{properties,usps,distance_m}', to_jsonb(round(near.distance_m::numeric, 1)));
    ELSE
      -- no address point within the radius: return the place context (town, county, FIPS)
      SELECT * INTO h FROM geocode.reverse(lon, lat, ARRAY['locality', 'localadmin'], NULL, NULL, 1);
      IF h.id IS NOT NULL THEN
        SELECT * INTO f FROM pgeo.feature WHERE id = h.id;
        feats := feats || geocode.address_result(f, 'place', NULL, r_m);
      END IF;
    END IF;
  END IF;

  RETURN jsonb_build_object(
    'geocoding', jsonb_build_object(
      'version', '0.2', 'query', q,
      'engine', jsonb_build_object('name', 'pgeo-sql', 'author', 'pelias_maine', 'version', geocode.engine_version()),
      'standard', 'USPS Publication 28 style; not CASS-certified; ZIP+4 not available',
      'timestamp', (extract(epoch FROM clock_timestamp()) * 1000)::bigint),
    'type', 'FeatureCollection',
    'features', feats);
EXCEPTION WHEN SQLSTATE '22023' THEN
  RETURN geocode.api_error(jsonb_strip_nulls(jsonb_build_object('ids', ids, 'text', text, 'point.lat', lat,
                                                                'point.lon', lon)), SQLERRM);
END
$$;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA geocode, geocode_api TO pgeo_api;
