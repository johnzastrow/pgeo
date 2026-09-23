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

from pgeo.regions import Region
from pgeo.settings import DATA_DIR

# The Who's on First distributions are national: every build reads the same two files.
WOF_DIR = DATA_DIR / "pelias" / "whosonfirst" / "sqlite"
# Canada and Mexico, one country polygon each, fetched by scripts/fetch_data.sh neighbours.
NEIGHBOUR_DIR = DATA_DIR / "raw" / "shared" / "neighbours"
STAGE_NEIGHBOUR_COLS = "name, geom_hex"
STAGE_POINT_COLS = (
    "source, layer, source_id, name, housenumber, street, unit, postcode, locality_hint, "
    "category, addendum, lon, lat, popularity"
)
# Order must match the SELECT in wof_admin() and the columns of stage_admin.
STAGE_ADMIN_COLS = (
    "id, source, source_id, placetype, name, longname, abbr, population, parent_id, geom_hex, "
    "lon, lat, minlon, minlat, maxlon, maxlat"
)


def _duck() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for ext in ("spatial", "sqlite"):
        con.execute(f"INSTALL {ext}")
        con.execute(f"LOAD {ext}")
    return con


def wof_admin(out: Path, reg: Region) -> int:
    """The build's admin areas and postal codes from the WOF SQLite distributions."""
    con = _duck()
    # ATTACH does not take bind parameters; these are our own fixed paths (checked for quotes).
    for alias, fname in (
        ("wa", "whosonfirst-data-admin-us-latest.db"),
        ("wp", "whosonfirst-data-postalcode-us-latest.db"),
    ):
        path = str(WOF_DIR / fname)
        if "'" in path:
            raise ValueError(f"unexpected quote in path {path!r}")
        con.execute(f"ATTACH '{path}' AS {alias} (TYPE sqlite, READ_ONLY)")
    select = """
      SELECT s.id, 'whosonfirst' AS source, CAST(s.id AS VARCHAR) AS source_id, s.placetype, s.name,
             -- The name carrying whatever suffix the place actually has. Louisiana's county
             -- equivalents are parishes and Alaska's are boroughs, municipalities and census
             -- areas, and Who's on First files all of them under placetype 'county' with the
             -- bare name - "Acadia", not "Acadia Parish". The suffix exists only here.
             json_extract_string(g.body, '$.properties."label:eng_x_preferred_longname"[0]') AS longname,
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
      WHERE s.id IN (SELECT id FROM {db}.ancestors WHERE ancestor_id IN ({me})
                     UNION SELECT unnest([{me}]))
        AND s.is_current <> 0 AND s.is_deprecated = 0 AND s.placetype IN ({types})
    """
    # reg.wof_ids are integers read from the registry, so they are safe as SQL literals.
    ids = ", ".join(str(int(i)) for i in reg.wof_ids)
    admin = select.format(
        db="wa", me=ids, types="'region', 'county', 'localadmin', 'locality', 'neighbourhood'"
    )
    postal = select.format(db="wp", me=ids, types="'postalcode'")
    con.execute(f"COPY ({admin} UNION ALL {postal}) TO ? (FORMAT csv, HEADER true)", [str(out)])
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


# Note: in DuckDB, `COPY (subquery) TO ?` binds the TO placeholder before placeholders in the
# subquery, so these statements use explicit numbered parameters ($1 input, $2 output).


def openaddresses(out: Path, reg: Region) -> int:
    """OpenAddresses CSVs (scripts/fetch_data.sh oa) -> address points.

    Sources overlap - New York publishes a statewide file and county files covering the same
    addresses - so rows are deduplicated on the OpenAddresses HASH, as they always were.
    """
    src = reg.oa_glob
    con = _duck()
    con.execute(
        """COPY (
          SELECT DISTINCT ON (HASH)
            'openaddresses' AS source, 'address' AS layer, HASH AS source_id,
            NUMBER || ' ' || STREET AS name, NUMBER AS housenumber, STREET AS street, UNIT AS unit,
            POSTCODE AS postcode, CITY AS locality_hint, '' AS category, '' AS addendum,
            LON AS lon, LAT AS lat, NULL::REAL AS popularity
          -- The dialect is stated, not sniffed. OpenAddresses files are quoted comma CSV with
          -- CRLF endings, but quoted fields can be rare: on New York's statewide file DuckDB
          -- sampled 20,480 rows, saw no quote character, chose quote='' and then failed on line
          -- 90,718, a unit field reading "BLDG 16, Boys Girls Club Room".
          FROM read_csv($1, all_varchar=true, union_by_name=true,
                        delim=',', quote='"', escape='"', header=true)
          WHERE coalesce(NUMBER, '') <> '' AND coalesce(STREET, '') <> '' AND coalesce(HASH, '') <> ''
          ORDER BY HASH
        ) TO $2 (FORMAT csv, HEADER true)""",
        [src, str(out)],
    )
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


CSV_SOURCES = ("gnis", "zcta", "overture")


def csv_source(name: str, out: Path, reg: Region) -> int:
    """Processed Pelias-CSV files (gnis, zcta, overture) -> points, addendum kept as JSON."""
    if name not in CSV_SOURCES:  # name is interpolated into the SQL below
        raise ValueError(f"unknown CSV source {name!r}")
    src = str(reg.processed_dir / f"{name}.csv")
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
          FROM read_csv($1, all_varchar=false)
        ) TO $2 (FORMAT csv, HEADER true)""",  # noqa: S608 (allowlisted names only)
        [src, str(out)],
    )
    return con.execute("SELECT count(*) FROM read_csv(?)", [str(out)]).fetchone()[0]


def osm_to_postgis(
    pbfs: list[Path], pg_conn: str, schema: str, password: str, log=print
) -> None:
    """Load OSM points, lines and polygons into <schema>.osm_* with ogr2ogr (GDAL OSM driver).

    One extract per state: the first creates the tables, the rest append to them.
    The password is passed through the environment (PGPASSWORD), never on the command line.
    """
    ogr2ogr = shutil.which("ogr2ogr")
    if ogr2ogr is None:
        raise RuntimeError("ogr2ogr (GDAL) is required for the OpenStreetMap loader")
    conf = Path(__file__).with_name("osmconf.ini")
    env = dict(os.environ, PGPASSWORD=password, OSM_CONFIG_FILE=str(conf), OSM_USE_CUSTOM_INDEXING="NO")
    layers = (("points", "osm_points"), ("lines", "osm_lines"),
              ("multipolygons", "osm_polygons"))
    for i, pbf in enumerate(pbfs):
        log(f"ogr2ogr {pbf.name} ({i + 1}/{len(pbfs)})")
        for layer, table in layers:
            proc = subprocess.run(  # noqa: S603 - fixed argv, our own paths, no shell
                [
                    ogr2ogr,
                    # The first extract creates the tables; later ones add to them.
                    *(["-append"] if i else []),
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
                stderr=subprocess.PIPE,
                text=True,
            )
            # ogr2ogr can abandon a layer and still exit zero: "Terminating translation
            # prematurely after failed translation of layer lines" left a build with New
            # Hampshire's streets and none of Vermont's, and nothing downstream noticed. Treat
            # its own error lines as failure, since a partly loaded extract is worse than none.
            errs = [ln for ln in (proc.stderr or "").splitlines()
                    if ln.startswith("ERROR") or "Terminating translation" in ln]
            if errs:
                detail = "\n  ".join(errs[:6])
                raise RuntimeError(
                    f"ogr2ogr reported errors loading layer {layer!r} from {pbf.name} "
                    f"(exit status {proc.returncode}):\n  {detail}"
                )


def neighbour_countries(out: Path) -> int:
    """Canada and Mexico as WKB, for subtracting from the region clip.

    The clip buffers the region by ~300 m so that piers and island shoreline survive a boundary
    that generalises the coast. Seaward that is what we want; across a land border it takes in a
    strip of the other country, which a single-state build then labels with its own state.
    Subtracting these two removes that strip and leaves the seaward buffer alone, there being
    nothing out there to subtract.

    Absent files are not an error: a build simply keeps the plain buffer, as it did before.
    """
    con = _duck()
    con.execute("INSTALL spatial; LOAD spatial;")
    files = sorted(NEIGHBOUR_DIR.glob("*.geojson")) if NEIGHBOUR_DIR.is_dir() else []
    if not files:
        out.write_text("name,geom_hex\n")
        return 0
    rows = []
    for f in files:
        # read_json_objects keeps the record whole; read_json would flatten it into columns.
        name, geom = con.execute(
            "SELECT json_extract_string(json, '$.properties.\"wof:name\"'), "
            "       ST_AsHEXWKB(ST_GeomFromGeoJSON(json_extract(json, '$.geometry'))) "
            "FROM read_json_objects(?, maximum_object_size => 200000000)",
            [str(f)],
        ).fetchone()
        rows.append((name, geom))
    con.execute("CREATE TABLE n (name VARCHAR, geom_hex VARCHAR)")
    con.executemany("INSERT INTO n VALUES (?, ?)", rows)
    con.execute("COPY (SELECT name, geom_hex FROM n) TO ? (FORMAT csv, HEADER true)", [str(out)])
    return len(rows)
