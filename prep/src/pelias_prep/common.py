"""Shared helpers: DuckDB connection, the build's region, CSV export and validation.

A build covers one or more US states, named in regions/regions.json at the repository root.
Everything state-specific comes from there - no converter names a state. Three tables carry the
region through a run and the converters read them:

  region_poly(state, geom)   one row per member state, from the Census boundary file
  zctas(geoid, lat, lon, …)  the build's ZCTAs, by Census land area (load_region_zctas)
  zcta_prefixes(p)           three-digit ZIP prefixes this build has to itself

The last two replace the literal ZIP-prefix range an earlier Maine-only version carried
('039'-'049'). A hand-written range is a fact about one state that has to be looked up again for
every other; these are derived from the Census files for whatever states the build names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

REGISTRY = Path(__file__).resolve().parents[3] / "regions" / "regions.json"


@dataclass(frozen=True)
class Region:
    """The states a build covers, and the facts the converters need about them."""

    build: str
    states: tuple[str, ...]
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max

    @property
    def state_boxes(self) -> dict[str, tuple[float, float, float, float]]:
        """Each member state's own box, for per-state inputs such as the GNIS files."""
        reg = _registry()["states"]
        return {s: tuple(reg[s]["bbox"]) for s in self.states}

    @property
    def evidence(self) -> tuple[str, ...]:
        """Upper-case spellings a source might use in a `region` field: 'ME', 'MAINE', ..."""
        reg = _registry()["states"]
        out: list[str] = []
        for st in self.states:
            out += [st, reg[st]["name"].upper()]
        return tuple(out)


def _registry() -> dict:
    if not REGISTRY.exists():
        raise FileNotFoundError(f"missing {REGISTRY}; run scripts/gen_regions.py")
    return json.loads(REGISTRY.read_text())


def region(build: str) -> Region:
    """Resolve a build name (`ny`) or a bare list of state codes (`me,nh,vt`) to a Region."""
    reg = _registry()
    states = reg.get("builds", {}).get(build, {}).get("states")
    if states is None:
        states = [s.strip().upper() for s in build.split(",") if s.strip()]
        build = "-".join(s.lower() for s in states)  # the build names a directory
    unknown = [s for s in states if s not in reg["states"]]
    if not states or unknown:
        raise ValueError(f"unknown state or build: {build} ({', '.join(unknown) or 'no states'})")
    boxes = [reg["states"][s]["bbox"] for s in states]
    bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
    return Region(build=build, states=tuple(states), bbox=bbox)


@dataclass(frozen=True)
class ExportResult:
    path: Path
    rows: int


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    return con


def load_region_polygon(
    con: duckdb.DuckDBPyConnection, states_zip: Path, reg: Region
) -> None:
    """Create `region_poly(state, geom)` from the Census cartographic boundary states shapefile."""
    shp = f"/vsizip/{states_zip.resolve()}/cb_2024_us_state_500k.shp"
    placeholders = ", ".join("?" for _ in reg.states)
    con.execute(
        f"CREATE OR REPLACE TABLE region_poly AS "  # noqa: S608 - placeholders only
        f"SELECT STUSPS AS state, geom FROM ST_Read(?) WHERE STUSPS IN ({placeholders})",
        [shp, *reg.states],
    )
    (n,) = con.execute("SELECT count(*) FROM region_poly").fetchone()
    if n != len(reg.states):
        raise ValueError(
            f"expected {len(reg.states)} state polygons ({', '.join(reg.states)}) "
            f"in {states_zip}, found {n}"
        )


def load_polygon_wkt(con: duckdb.DuckDBPyConnection, wkt: str, state: str = "XX") -> None:
    """Create `region_poly` from WKT. Used by tests and ad-hoc runs."""
    con.execute(
        "CREATE OR REPLACE TABLE region_poly AS SELECT ? AS state, ST_GeomFromText(?) AS geom",
        [state, wkt],
    )


def load_region_zctas(
    con: duckdb.DuckDBPyConnection, gazetteer: Path, relationship: Path, reg: Region
) -> int:
    """Create `zctas(geoid, lat, lon, ...)`: the ZCTAs belonging to the build's states.

    `gazetteer` is the national Gazetteer file (a point and land/water area per ZCTA, pipe
    delimited since 2025); `relationship` is the Census ZCTA-to-county file, which is how a ZCTA
    is tied to a state at all - the Gazetteer does not say, and ZCTAs cross state lines.

    A ZCTA is assigned to the state holding most of its land, so each belongs to exactly one
    build. Deciding this from geometry instead - is the Gazetteer point inside the state polygon -
    reads the coast wrong: the cartographic boundary file generalises away Maine's islands, so
    Peaks Island, Cliff Island, Cranberry Isles, Bailey Island, Blue Hill and Boothbay Harbor all
    fall outside, while 03579 in New Hampshire falls inside. This is measured, not hypothetical.
    """
    fips = [_registry()["states"][s]["fips"] for s in reg.states]
    placeholders = ", ".join("?" for _ in fips)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE zctas AS
        WITH rel AS (
            SELECT geoid_zcta5_20 AS geoid,
                   substr(geoid_county_20, 1, 2) AS statefp,
                   sum(CAST(nullif(trim(arealand_part), '') AS HUGEINT)) AS land
            FROM read_csv(?, delim = '|', header = true, all_varchar = true,
                          normalize_names = true)
            WHERE nullif(trim(geoid_zcta5_20), '') IS NOT NULL
            GROUP BY 1, 2
        ),
        home AS (  -- the state holding most of the ZCTA's land
            SELECT geoid, statefp FROM (
                SELECT geoid, statefp,
                       row_number() OVER (PARTITION BY geoid
                                          ORDER BY land DESC, statefp) AS rn
                FROM rel
            ) WHERE rn = 1
        ),
        gaz AS (
            SELECT trim(geoid) AS geoid,
                   CAST(trim(intptlat) AS DOUBLE) AS lat,
                   CAST(trim(intptlong) AS DOUBLE) AS lon,
                   CAST(trim(aland_sqmi) AS DOUBLE) AS aland_sqmi,
                   CAST(trim(awater_sqmi) AS DOUBLE) AS awater_sqmi
            FROM read_csv(?, delim = '|', header = true, all_varchar = true,
                          normalize_names = true)
        )
        SELECT gaz.* FROM gaz JOIN home USING (geoid)
        WHERE home.statefp IN ({placeholders})
        """,  # noqa: S608 - placeholders only
        [str(relationship), str(gazetteer), *fips],
    )
    (n,) = con.execute("SELECT count(*) FROM zctas").fetchone()
    if n == 0:
        raise ValueError(
            f"no ZCTAs belong to {', '.join(reg.states)} (relationship {relationship})"
        )
    _load_zcta_prefixes(con, relationship, fips)
    return n


def _load_zcta_prefixes(
    con: duckdb.DuckDBPyConnection, relationship: Path, fips: list[str]
) -> None:
    """Create `zcta_prefixes(p)`: three-digit ZIP prefixes used by this build and no other state.

    Postcode fields in source data include ZIPs that are PO-box-only and so have no ZCTA at all.
    Rejecting them as errors costs real data - 180 Maine venues - and accepting any prefix the
    build touches would admit a neighbour's ZIPs, because prefixes straddle borders: 035 covers
    both New Hampshire and one majority-Maine ZCTA, and 063 covers Connecticut and New York's
    Fishers Island. A prefix qualifies only when every ZCTA that uses it is inside the build.
    """
    placeholders = ", ".join("?" for _ in fips)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE zcta_prefixes AS
        WITH rel AS (
            SELECT geoid_zcta5_20 AS geoid,
                   substr(geoid_county_20, 1, 2) AS statefp,
                   sum(CAST(nullif(trim(arealand_part), '') AS HUGEINT)) AS land
            FROM read_csv(?, delim = '|', header = true, all_varchar = true,
                          normalize_names = true)
            WHERE nullif(trim(geoid_zcta5_20), '') IS NOT NULL
            GROUP BY 1, 2
        ),
        home AS (
            SELECT geoid, statefp FROM (
                SELECT geoid, statefp,
                       row_number() OVER (PARTITION BY geoid
                                          ORDER BY land DESC, statefp) AS rn
                FROM rel
            ) WHERE rn = 1
        )
        SELECT substr(geoid, 1, 3) AS p
        FROM home
        GROUP BY 1
        HAVING bool_and(statefp IN ({placeholders}))
        """,  # noqa: S608 - placeholders only
        [str(relationship), *fips],
    )


def export_csv(
    con: duckdb.DuckDBPyConnection, sql: str, params: list, out: Path, reg: Region
) -> ExportResult:
    """Materialize `sql` into a Pelias CSV file, then validate it. Fails closed on bad output."""
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _export AS {sql}", params)  # noqa: S608
    con.execute("COPY _export TO ? (FORMAT csv, HEADER true, DELIMITER ',')", [str(out)])
    rows = validate_export(con, reg)
    return ExportResult(path=out, rows=rows)


def validate_export(con: duckdb.DuckDBPyConnection, reg: Region) -> int:
    """Check the rows just exported. Raises ValueError rather than shipping bad data."""
    lon_min, lat_min, lon_max, lat_max = reg.bbox
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
        problems.append(f"{bad_coords} rows outside the {reg.build} bbox")
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
