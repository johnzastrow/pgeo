"""OpenAddresses GeoJSON -> the CSV layout pelias/interpolation still expects.

Only the Pelias path needs this. A pgeo build downloads the OpenAddresses run archives, which
already carry that CSV layout (scripts/fetch_data.sh oa), so it converts nothing.

OpenAddresses now publishes newline-delimited GeoJSON, which the Pelias OA importer reads,
but the interpolation builder (script/concat_oa.sh) only globs `*.csv` with the legacy
header LON,LAT,NUMBER,STREET,UNIT,CITY,DISTRICT,REGION,POSTCODE,ID,HASH. Without this
conversion it silently skips OA conflation. Output mirrors the input tree under `out_dir`,
which the interpolation container reads via OAPATH.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .common import Region

SQL = """
SELECT
    geometry.coordinates[1]      AS LON,
    geometry.coordinates[2]      AS LAT,
    properties.number            AS NUMBER,
    properties.street            AS STREET,
    properties.unit              AS UNIT,
    properties.city              AS CITY,
    properties.district          AS DISTRICT,
    properties.region            AS REGION,
    properties.postcode          AS POSTCODE,
    properties.id                AS ID,
    properties.hash              AS HASH
FROM read_json(?, format = 'newline_delimited', columns = {
    geometry: 'STRUCT(type VARCHAR, coordinates DOUBLE[])',
    properties: 'STRUCT("number" VARCHAR, street VARCHAR, unit VARCHAR, city VARCHAR,
                        district VARCHAR, region VARCHAR, postcode VARCHAR, id VARCHAR,
                        hash VARCHAR)'
})
WHERE geometry.coordinates[1] IS NOT NULL
  AND nullif(trim(properties.number), '') IS NOT NULL
  AND nullif(trim(properties.street), '') IS NOT NULL
"""


@dataclass(frozen=True)
class OaFileResult:
    path: Path
    rows: int
    outside_bbox: int


def convert_file(
    con: duckdb.DuckDBPyConnection, src: Path, out: Path, reg: Region
) -> OaFileResult:
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _oa AS {SQL}", [str(src)])
    lon_min, lat_min, lon_max, lat_max = reg.bbox
    rows, outside = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE LAT NOT BETWEEN ? AND ? "
        "OR LON NOT BETWEEN ? AND ?) FROM _oa",
        [lat_min, lat_max, lon_min, lon_max],
    ).fetchone()
    if rows == 0:
        raise ValueError(f"no usable address rows in {src}")
    con.execute("COPY _oa TO ? (FORMAT csv, HEADER true)", [str(out)])
    return OaFileResult(path=out, rows=rows, outside_bbox=outside)


def convert_tree(
    con: duckdb.DuckDBPyConnection, oa_dir: Path, out_dir: Path, reg: Region
) -> list[OaFileResult]:
    sources = sorted(oa_dir.rglob("*.geojson"))
    if not sources:
        raise FileNotFoundError(f"no .geojson files under {oa_dir} (run `pelias download oa`)")
    results = []
    for src in sources:
        rel = src.relative_to(oa_dir).with_suffix(".csv")
        results.append(convert_file(con, src, out_dir / rel, reg))
    return results
