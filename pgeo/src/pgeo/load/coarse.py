"""The coarse tier: a downsampled worldwide backbone of places, staged for pgeo.feature.

A build holds address data for a few US states. The coarse tier adds countries, regions,
counties, localadmins, the larger localities and the ZIP codes outside the build, from two
inputs fetched by scripts/fetch_data.sh:

  coarse  data/raw/shared/coarse/divisions_<release>.parquet  (Overture divisions, downsampled)
  zcta    data/raw/shared/zcta/                               (Census Gazetteer + county relationship)

Everything is computed here, so each row reaches `stage_coarse` (sql/020_tables.sql) with the
label, hierarchy names and importance already decided; 030_enrich_index.sql only numbers the
rows and adds the normalised forms. Rows the build covers in detail are left out: Overture rows
whose ISO 3166-2 region is one of the build's states, and ZCTAs belonging to those states (a
ZIP already loaded in-region is skipped again in 030 by its gid).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import duckdb

from pgeo.regions import Region
from pgeo.settings import DATA_DIR

SHARED = DATA_DIR / "raw" / "shared"
COARSE_DIR = SHARED / "coarse"
ZCTA_DIR = SHARED / "zcta"

# Order must match the columns of stage_coarse in sql/020_tables.sql and of out_rows below.
STAGE_COARSE_COLS = (
    "source, layer, source_id, name, postcode, locality, localadmin, county, region, region_a, "
    "country_a, country, label, addendum, lon, lat, minlon, minlat, maxlon, maxlat, importance, hier"
)

# One statement per entry. The first reads the downsampled parquet; the second lists the ISO
# 3166-2 codes of the build's own states ('US-ME', ...) so _BASE can leave them out.
_COUNTRY = """
CREATE TEMP TABLE country_name AS
  SELECT country, name FROM (
    SELECT country, coalesce(nullif(name_en, ''), name_primary) AS name,
           row_number() OVER (PARTITION BY country
                              ORDER BY (subtype = 'country') DESC, id) AS rn
    FROM d WHERE subtype IN ('country', 'dependency')) WHERE rn = 1"""

_REGION = """
CREATE TEMP TABLE region_name AS
  SELECT region, name FROM (
    SELECT region, coalesce(nullif(name_en, ''), name_primary) AS name,
           row_number() OVER (PARTITION BY region ORDER BY id) AS rn
    FROM d WHERE subtype = 'region' AND region IS NOT NULL) WHERE rn = 1"""

_COUNTY = """
CREATE TEMP TABLE county_name AS
  SELECT id, coalesce(nullif(name_en, ''), name_primary) AS name FROM d WHERE subtype = 'county'"""

_BASE = """
CREATE TEMP TABLE base AS
SELECT d.id,
       CASE WHEN d.subtype = 'dependency' THEN 'country' ELSE d.subtype END AS layer,
       coalesce(nullif(d.name_en, ''), d.name_primary) AS name,
       d.name_primary, d.country, d.region AS region_code, d.population, d.lon, d.lat,
       d.xmin, d.ymin, d.xmax, d.ymax,
       d.country = 'US' AS is_us,
       CASE WHEN d.country = 'US' AND d.region LIKE 'US-%' THEN substr(d.region, 4) END AS region_a,
       CASE WHEN d.subtype = 'region' THEN coalesce(nullif(d.name_en, ''), d.name_primary)
            ELSE rn.name END AS region_name,
       CASE WHEN d.subtype = 'county' THEN coalesce(nullif(d.name_en, ''), d.name_primary)
            ELSE cn.name END AS county_name,
       CASE WHEN d.subtype IN ('country', 'dependency')
            THEN coalesce(nullif(d.name_en, ''), d.name_primary)
            ELSE ct.name END AS country_name
FROM d
LEFT JOIN region_name rn ON rn.region = d.region
LEFT JOIN county_name cn ON cn.id = d.county_id
LEFT JOIN country_name ct ON ct.country = d.country
-- the build's own states are held in detail already
WHERE d.region IS NULL OR d.region NOT IN (SELECT region FROM own_regions)"""

# Labels are Pelias-style. US rows carry the state abbreviation and "USA"; elsewhere the parents
# are named, and a locality that shares its region's name ("Oslo, Oslo, Norway") drops the
# repeated segment. Importance: countries and regions 0.9 (a bare "California" must beat a
# village of that name in the build's own data); the other layers sit below the build's own
# equivalents (0.75 to 1.0 there).
_PRIMARY = """
CREATE TEMP TABLE primary_rows AS
SELECT 'overture' AS source, layer, id AS source_id, name,
       CAST(NULL AS VARCHAR) AS postcode,
       CASE WHEN layer = 'locality' THEN name END AS locality,
       CASE WHEN layer = 'localadmin' THEN name END AS localadmin,
       CASE WHEN layer IN ('country', 'region') THEN NULL ELSE county_name END AS county,
       CASE WHEN layer = 'country' THEN NULL ELSE region_name END AS region,
       region_a,
       upper(country) AS country_a, country_name AS country,
       CASE
         WHEN layer = 'country' THEN name
         WHEN is_us AND layer = 'region' THEN name || ', USA'
         WHEN is_us THEN name || coalesce(', ' || region_a, '') || ', USA'
         WHEN layer = 'region' THEN name || coalesce(', ' || country_name, '')
         ELSE name
              || CASE WHEN region_name IS NOT NULL
                       AND NOT (layer IN ('locality', 'localadmin') AND region_name = name)
                      THEN ', ' || region_name ELSE '' END
              || coalesce(', ' || country_name, '')
       END AS label,
       CASE WHEN name_primary <> name
            THEN CAST(json_object('local_name', name_primary) AS VARCHAR) END AS addendum,
       lon, lat,
       CASE WHEN layer <> 'locality' THEN xmin END AS minlon,
       CASE WHEN layer <> 'locality' THEN ymin END AS minlat,
       CASE WHEN layer <> 'locality' THEN xmax END AS maxlon,
       CASE WHEN layer <> 'locality' THEN ymax END AS maxlat,
       CASE layer WHEN 'country' THEN 0.9 WHEN 'region' THEN 0.9 WHEN 'county' THEN 0.4
                  WHEN 'localadmin' THEN 0.3
                  ELSE 0.35 + CASE WHEN population IS NOT NULL
                                   THEN least(0.15, 0.03 * ln(1 + population / 1000.0)) ELSE 0 END
       END AS importance,
       -- the ISO 3166-2 short code of a non-US region ("CA-ON" -> ON), for region_a in the output
       CASE WHEN NOT is_us AND region_code ~ '^[A-Z]{2}-[A-Z0-9]{1,3}$'
            THEN CAST(json_object('region_a', substr(region_code, 4)) AS VARCHAR) END AS hier,
       name_primary
FROM base
WHERE name IS NOT NULL AND lon IS NOT NULL AND lat IS NOT NULL"""

# Alias rows carry a country's, region's or county's local-language name under the English
# label; their source_id gets '#alt' so the gid stays unique.
_OUT = """
CREATE TEMP TABLE out_rows AS
SELECT source, layer, source_id, name, postcode, locality, localadmin, county, region, region_a,
       country_a, country, label, addendum, lon, lat, minlon, minlat, maxlon, maxlat,
       CAST(importance AS REAL) AS importance, hier
FROM primary_rows
UNION ALL
SELECT source, layer, source_id || '#alt', name_primary, postcode, locality, localadmin, county,
       region, region_a, country_a, country, label, addendum, lon, lat, minlon, minlat,
       maxlon, maxlat, CAST(importance AS REAL), hier
FROM primary_rows
WHERE layer IN ('country', 'region', 'county') AND name_primary <> name"""

_DIVISIONS = [
    ("CREATE TEMP TABLE d AS SELECT * FROM read_parquet($1)", 0),
    ("CREATE TEMP TABLE own_regions AS SELECT unnest($1::VARCHAR[]) AS region", 1),
    (_COUNTRY, None), (_REGION, None), (_COUNTY, None), (_BASE, None), (_PRIMARY, None),
    (_OUT, None),
]

# National ZCTAs. $1 is the Gazetteer text file, $2 the Census ZCTA-to-county relationship file,
# $3 the FIPS codes of the build's own states; table `states` (fips, abbr, name) is filled by the
# caller. A ZCTA belongs to the state holding most of its land, as in the in-region loader.
_ZIPS = """
CREATE TEMP TABLE zips AS
WITH rel AS (
    SELECT geoid_zcta5_20 AS geoid, substr(geoid_county_20, 1, 2) AS statefp,
           sum(CAST(nullif(trim(arealand_part), '') AS HUGEINT)) AS land
    FROM read_csv($2, delim = '|', header = true, all_varchar = true, normalize_names = true)
    WHERE nullif(trim(geoid_zcta5_20), '') IS NOT NULL
    GROUP BY 1, 2
),
home AS (
    SELECT geoid, statefp FROM (
        SELECT geoid, statefp,
               row_number() OVER (PARTITION BY geoid ORDER BY land DESC, statefp) AS rn
        FROM rel) WHERE rn = 1
),
gaz AS (
    SELECT trim(geoid) AS geoid, CAST(trim(intptlat) AS DOUBLE) AS lat,
           CAST(trim(intptlong) AS DOUBLE) AS lon,
           CAST(trim(aland_sqmi) AS DOUBLE) AS aland_sqmi,
           CAST(trim(awater_sqmi) AS DOUBLE) AS awater_sqmi
    FROM read_csv($1, delim = '|', header = true, all_varchar = true, normalize_names = true)
)
SELECT g.*, h.statefp, s.abbr, s.name AS state_name
FROM gaz g
LEFT JOIN home h USING (geoid)
LEFT JOIN states s ON s.fips = h.statefp
WHERE g.lat IS NOT NULL AND g.lon IS NOT NULL
  AND NOT (h.statefp IS NOT NULL AND list_contains($3::VARCHAR[], h.statefp))"""

_ZIP_ROWS = """
INSERT INTO out_rows
SELECT 'zcta', 'postalcode', geoid, geoid, geoid, NULL, NULL, NULL, state_name, abbr,
       'US', 'United States',
       geoid || coalesce(', ' || abbr, '') || ', USA',
       CAST(json_object('zcta', json_object('zcta5', geoid, 'aland_sqmi', aland_sqmi,
                                            'awater_sqmi', awater_sqmi)) AS VARCHAR),
       lon, lat, NULL, NULL, NULL, NULL, CAST(0.3 AS REAL), CAST(NULL AS VARCHAR)
FROM zips"""


def areas_file() -> Path | None:
    """The simplified region and country boundaries, the newest release if several are present."""
    files = sorted(COARSE_DIR.glob("areas_*.parquet")) if COARSE_DIR.is_dir() else []
    return files[-1] if files else None


def build_areas_csv(out: Path, areas: Path | None = None) -> int:
    """Write the boundaries as a CSV for stage_coarse_area (hex WKB); the row count, 0 if no file."""
    src = areas or areas_file()
    if src is None:
        return 0
    con = duckdb.connect()
    con.execute(
        "COPY (SELECT division_id AS source_id, "
        "CASE WHEN subtype = 'dependency' THEN 'country' ELSE subtype END AS layer, "
        "hex(wkb) AS wkb_hex FROM read_parquet($1)) TO $2 (FORMAT csv, HEADER true)",
        [str(src), str(out)],
    )
    return int(con.execute("SELECT count(*) FROM read_parquet($1)", [str(src)]).fetchone()[0])


def divisions_file() -> Path | None:
    """The downsampled divisions extract, the newest release if several are present."""
    files = sorted(COARSE_DIR.glob("divisions_*.parquet")) if COARSE_DIR.is_dir() else []
    return files[-1] if files else None


def _zip_inputs(work: Path) -> tuple[Path, Path] | None:
    """The Gazetteer text file (extracted from its zip) and the relationship file, if present."""
    gaz = sorted(ZCTA_DIR.glob("*_Gaz_zcta_national.zip")) if ZCTA_DIR.is_dir() else []
    rel = ZCTA_DIR / "tab20_zcta520_county20_natl.txt"
    if not gaz or not rel.is_file():
        return None
    with zipfile.ZipFile(gaz[-1]) as zf:
        members = [m for m in zf.namelist() if m.endswith("_Gaz_zcta_national.txt")]
        if len(members) != 1:
            return None
        # Written under a fixed name of ours; archive paths are never trusted.
        work.mkdir(parents=True, exist_ok=True)
        target = work / "coarse_gaz_zcta.txt"
        with zf.open(members[0]) as src, target.open("wb") as out:
            out.write(src.read())
    return target, rel


def build_csv(
    out: Path,
    reg: Region,
    all_states: list[tuple[str, str, str]],
    work: Path,
    divisions: Path | None = None,
) -> tuple[int, dict[str, int], str | None]:
    """Write the coarse rows as a CSV for stage_coarse.

    Returns (rows, rows by layer, note). With no divisions file nothing is written, the rows are
    0 and the note says why, so the build carries on without the tier. With divisions but no ZIP
    inputs the note says the ZIP codes were left out. `all_states` is (fips, abbr, name) for every
    state in the registry, to put a state on each ZIP.
    """
    src = divisions or divisions_file()
    if src is None:
        return 0, {}, f"no divisions file in {COARSE_DIR} (run scripts/fetch_data.sh coarse)"
    con = duckdb.connect()
    # Values are bound as parameters; only our own fixed statements are ever SQL text.
    # args[i] is the single parameter of the statement tagged i; None means it takes none.
    args = [[str(src)], [[f"US-{st}" for st in reg.states]]]
    for stmt, which in _DIVISIONS:
        con.execute(stmt, args[which] if which is not None else [])
    note = None
    zips = _zip_inputs(work)
    if zips is None:
        note = "no ZIP inputs (run scripts/fetch_data.sh zcta): ZIP codes outside the build not added"
    else:
        con.execute("CREATE TEMP TABLE states (fips VARCHAR, abbr VARCHAR, name VARCHAR)")
        con.executemany("INSERT INTO states VALUES (?, ?, ?)", all_states)
        own = [fips for fips, abbr, _ in all_states if abbr in reg.states]
        con.execute(_ZIPS, [str(zips[0]), str(zips[1]), own])
        con.execute(_ZIP_ROWS)
    con.execute("COPY out_rows TO $1 (FORMAT csv, HEADER true)", [str(out)])
    by_layer = dict(con.execute("SELECT layer, count(*) FROM out_rows GROUP BY 1").fetchall())
    return sum(by_layer.values()), by_layer, note
