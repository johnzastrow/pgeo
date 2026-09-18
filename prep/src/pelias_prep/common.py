"""Shared helpers: DuckDB connection, Maine boundary, CSV export and validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb

# Generous Maine bounding box (lon_min, lat_min, lon_max, lat_max). Used as a sanity check on
# every output row, and as a cheap prefilter before the exact polygon test.
MAINE_BBOX = (-71.2, 42.9, -66.8, 47.5)


@dataclass(frozen=True)
class ExportResult:
    path: Path
    rows: int


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    return con


def load_maine_polygon(con: duckdb.DuckDBPyConnection, states_zip: Path) -> None:
    """Create table `maine(geom)` from the Census cartographic boundary states shapefile zip."""
    shp = f"/vsizip/{states_zip.resolve()}/cb_2024_us_state_500k.shp"
    con.execute(
        "CREATE OR REPLACE TABLE maine AS SELECT geom FROM ST_Read(?) WHERE STUSPS = 'ME'",
        [shp],
    )
    (n,) = con.execute("SELECT count(*) FROM maine").fetchone()
    if n != 1:
        raise ValueError(f"expected exactly one Maine polygon in {states_zip}, found {n}")


def load_polygon_wkt(con: duckdb.DuckDBPyConnection, wkt: str) -> None:
    """Create table `maine(geom)` from WKT. Used by tests and ad-hoc runs."""
    con.execute("CREATE OR REPLACE TABLE maine AS SELECT ST_GeomFromText(?) AS geom", [wkt])


def export_csv(con: duckdb.DuckDBPyConnection, sql: str, params: list, out: Path) -> ExportResult:
    """Materialize `sql` into a Pelias CSV file, then validate it. Fails closed on bad output."""
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _export AS {sql}", params)
    con.execute("COPY _export TO ? (FORMAT csv, HEADER true, DELIMITER ',')", [str(out)])
    rows = validate_export(con)
    return ExportResult(path=out, rows=rows)


def validate_export(con: duckdb.DuckDBPyConnection) -> int:
    """Check the rows just exported. Raises ValueError rather than shipping bad data."""
    lon_min, lat_min, lon_max, lat_max = MAINE_BBOX
    (rows, bad_required, bad_coords, dup_ids) = con.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE source IS NULL OR name IS NULL OR trim(name) = ''
                             OR lat IS NULL OR lon IS NULL),
            count(*) FILTER (WHERE lat NOT BETWEEN ? AND ? OR lon NOT BETWEEN ? AND ?),
            count(*) - count(DISTINCT id)
        FROM _export
        """,
        [lat_min, lat_max, lon_min, lon_max],
    ).fetchone()
    problems = []
    if rows == 0:
        problems.append("no rows")
    if bad_required:
        problems.append(f"{bad_required} rows missing source/name/lat/lon")
    if bad_coords:
        problems.append(f"{bad_coords} rows outside the Maine bbox")
    if dup_ids:
        problems.append(f"{dup_ids} duplicate ids")
    if problems:
        raise ValueError("export validation failed: " + "; ".join(problems))
    return rows


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
