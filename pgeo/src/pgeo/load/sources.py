"""Source extractors: raw inputs -> staging CSVs (DuckDB) or staging tables (ogr2ogr).

Every function reads only local files under data/ and writes into data/pgeo/stage/.
CSV columns match the stage_admin / stage_point tables in sql/020_tables.sql.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import duckdb

from pgeo.settings import DATA_DIR

MAINE_WOF_ID = 85688769
WOF_DIR = DATA_DIR / "pelias" / "whosonfirst" / "sqlite"
STAGE_POINT_COLS = (
    "source, layer, source_id, name, housenumber, street, unit, postcode, locality_hint, "
    "category, addendum, lon, lat, popularity"
)
STAGE_ADMIN_COLS = (
    "id, source, source_id, placetype, name, abbr, population, parent_id, geom_hex, "
    "lon, lat, minlon, minlat, maxlon, maxlat"
)


def _duck() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for ext in ("spatial", "sqlite"):
        con.execute(f"INSTALL {ext}")
        con.execute(f"LOAD {ext}")
    return con


def wof_admin(out: Path) -> int:
    """Maine admin areas and postal codes from the WOF SQLite distributions."""
    con = _duck()
    con.execute("ATTACH ? AS wa (TYPE sqlite, READ_ONLY)", [str(WOF_DIR / "whosonfirst-data-admin-us-latest.db")])
    con.execute(
        "ATTACH ? AS wp (TYPE sqlite, READ_ONLY)",
        [str(WOF_DIR / "whosonfirst-data-postalcode-us-latest.db")],
    )
    select = """
      SELECT s.id, 'whosonfirst' AS source, CAST(s.id AS VARCHAR) AS source_id, s.placetype, s.name,
             json_extract_string(g.body, '$.properties."wof:abbreviation"') AS abbr,
             TRY_CAST(coalesce(json_extract_string(g.body, '$.properties."wof:population"'),
                               json_extract_string(g.body, '$.properties."gn:population"')) AS BIGINT) AS population,
             s.parent_id,
             CASE WHEN json_extract_string(g.body, '$.geometry.type') IN ('Polygon', 'MultiPolygon')
                  THEN ST_AsHEXWKB(ST_GeomFromGeoJSON(json_extract(g.body, '$.geometry'))) END AS geom_hex,
             coalesce(TRY_CAST(json_extract_string(g.body, '$.properties."lbl:longitude"') AS DOUBLE),
                      s.longitude) AS lon,
             coalesce(TRY_CAST(json_extract_string(g.body, '$.properties."lbl:latitude"') AS DOUBLE),
                      s.latitude) AS lat,
             s.min_longitude AS minlon, s.min_latitude AS minlat, s.max_longitude AS maxlon, s.max_latitude AS maxlat
      FROM {db}.spr s
      JOIN {db}.geojson g ON g.id = s.id AND g.is_alt = 0
      WHERE s.id IN (SELECT id FROM {db}.ancestors WHERE ancestor_id = {me} UNION SELECT {me})
        AND s.is_current <> 0 AND s.is_deprecated = 0 AND s.placetype IN ({types})
    """
    admin = select.format(
        db="wa", me=MAINE_WOF_ID, types="'region', 'county', 'localadmin', 'locality', 'neighbourhood'"
    )
    postal = select.format(db="wp", me=MAINE_WOF_ID, types="'postalcode'")
    con.execute(f"COPY ({admin} UNION ALL {postal}) TO ? (FORMAT csv, HEADER true)", [str(out)])
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


def openaddresses(out: Path) -> int:
    """OpenAddresses (already converted to CSV for Pelias interpolation) -> address points."""
    src = str(DATA_DIR / "pelias" / "interpolation_oa" / "us" / "me" / "*.csv")
    con = _duck()
    con.execute(
        """COPY (
          SELECT DISTINCT ON (HASH)
            'openaddresses' AS source, 'address' AS layer, HASH AS source_id,
            NUMBER || ' ' || STREET AS name, NUMBER AS housenumber, STREET AS street, UNIT AS unit,
            POSTCODE AS postcode, CITY AS locality_hint, '' AS category, '' AS addendum,
            LON AS lon, LAT AS lat, NULL::REAL AS popularity
          FROM read_csv(?, all_varchar=true, union_by_name=true)
          WHERE coalesce(NUMBER, '') <> '' AND coalesce(STREET, '') <> '' AND coalesce(HASH, '') <> ''
          ORDER BY HASH
        ) TO ? (FORMAT csv, HEADER true)""",
        [src, str(out)],
    )
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


CSV_SOURCES = ("gnis", "zcta", "overture")


def csv_source(name: str, out: Path) -> int:
    """Processed Pelias-CSV files (gnis, zcta, overture) -> points, addendum kept as JSON."""
    if name not in CSV_SOURCES:  # name is interpolated into the SQL below
        raise ValueError(f"unknown CSV source {name!r}")
    src = str(DATA_DIR / "processed" / "csv" / f"{name}.csv")
    con = _duck()
    cols = {c[0] for c in con.execute("DESCRIBE SELECT * FROM read_csv(?)", [src]).fetchall()}

    def col(c: str) -> str:
        return f"CAST({c} AS VARCHAR)" if c in cols else "NULL"

    add = f"addendum_json_{name}"
    popularity = (
        "0.1 * TRY_CAST(json_extract_string(addendum_json_overture, '$.confidence') AS DOUBLE)"
        if name == "overture"
        else "NULL"
    )
    # Only allowlisted names and column names read from our own file appear in this SQL.
    con.execute(
        f"""COPY (
          SELECT source, layer, CAST(id AS VARCHAR) AS source_id, name,
                 {col("housenumber")} AS housenumber, {col("street")} AS street, NULL AS unit,
                 {col("postcode")} AS postcode, NULL AS locality_hint,
                 coalesce({col("category")}, '') AS category,
                 CASE WHEN {add if add in cols else "NULL"} IS NULL THEN ''
                      ELSE json_object('{name}', json({add})) END AS addendum,
                 lon, lat, {popularity} AS popularity
          FROM read_csv(?, all_varchar=false)
        ) TO ? (FORMAT csv, HEADER true)""",  # noqa: S608 (allowlisted names only)
        [src, str(out)],
    )
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


def osm_to_postgis(pbf: Path, pg_conn: str, schema: str, password: str) -> None:
    """Load OSM points, lines and polygons into <schema>.osm_* with ogr2ogr (GDAL OSM driver).

    The password is passed through the environment (PGPASSWORD), never on the command line.
    """
    ogr2ogr = shutil.which("ogr2ogr")
    if ogr2ogr is None:
        raise RuntimeError("ogr2ogr (GDAL) is required for the OpenStreetMap loader")
    conf = Path(__file__).with_name("osmconf.ini")
    env = dict(os.environ, PGPASSWORD=password, OSM_CONFIG_FILE=str(conf), OSM_USE_CUSTOM_INDEXING="NO")
    for layer, table in (("points", "osm_points"), ("lines", "osm_lines"), ("multipolygons", "osm_polygons")):
        subprocess.run(  # noqa: S603 - fixed argv, our own paths, no shell
            [
                ogr2ogr,
                "-f",
                "PostgreSQL",
                f"PG:{pg_conn} active_schema={schema}",
                str(pbf),
                layer,
                "-nln",
                table,
                "-lco",
                "GEOMETRY_NAME=geom",
                "-lco",
                "SPATIAL_INDEX=NONE",
                "-lco",
                "FID=ogc_fid",
                "-gt",
                "65536",
                "--config",
                "PG_USE_COPY",
                "YES",
            ],  # fmt: skip
            check=True,
            env=env,
        )
