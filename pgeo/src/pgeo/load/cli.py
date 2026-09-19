"""pgeo-load: build the geocoder database from the raw inputs in data/.

    uv run pgeo-load build [--sources whosonfirst,openaddresses,openstreetmap,gnis,zcta,overture]
    uv run pgeo-load functions        # re-apply sql/040_functions.sql only
    uv run pgeo-load info             # show the current build's metadata

The build runs in schema pgeo_build and is swapped into pgeo atomically (together with the
query functions, which depend on the table types), so the API never sees a partial build.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from pgeo.load import sources
from pgeo.settings import DATA_DIR, SECRETS_FILE, SQL_DIR, Settings, _read_env_file

ALL_SOURCES = ["whosonfirst", "openaddresses", "openstreetmap", "gnis", "zcta", "overture"]
STAGE_DIR = DATA_DIR / "pgeo" / "stage"
PBF = DATA_DIR / "pelias" / "openstreetmap" / "maine-latest.osm.pbf"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def run_sql(con: asyncpg.Connection, name: str) -> None:
    await con.execute((SQL_DIR / name).read_text())


async def copy_csv(con: asyncpg.Connection, table: str, columns: str, path: Path) -> None:
    cols = [c.strip() for c in columns.split(",")]
    with path.open("rb") as f:
        await con.copy_to_table(table, source=f, columns=cols, schema_name="pgeo_build", format="csv", header=True)


def pg_conn_string(dsn: str) -> str:
    """libpq keyword string for ogr2ogr (password omitted; passed via PGPASSWORD)."""
    u = urlparse(dsn)
    return f"host={u.hostname} port={u.port or 5432} dbname={u.path.lstrip('/')} user={u.username}"


async def ensure_api_role(con: asyncpg.Connection) -> None:
    """Read-only role for the API (least privilege)."""
    env = _read_env_file(SECRETS_FILE)
    pw = env.get("PGEO_API_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9]{24,128}", pw):
        raise RuntimeError("PGEO_API_PASSWORD in pgeo/pgeo.secrets must be 24-128 alphanumeric characters")
    exists = await con.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'pgeo_api'")
    # pw is validated to [A-Za-z0-9] above, so literal interpolation is safe here.
    verb = "ALTER" if exists else "CREATE"
    await con.execute(f"{verb} ROLE pgeo_api LOGIN PASSWORD '{pw}'")  # noqa: S608


async def build(settings: Settings, selected: list[str]) -> None:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    counts: dict[str, int] = {}
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        t0 = time.time()
        await run_sql(con, "010_base.sql")
        await con.execute("DROP SCHEMA IF EXISTS pgeo_build CASCADE; CREATE SCHEMA pgeo_build")
        await con.execute("SET search_path = pgeo_build, public")
        await run_sql(con, "020_tables.sql")

        # Who's On First is always loaded: it provides the admin hierarchy.
        step = time.time()
        f = STAGE_DIR / "wof_admin.csv"
        counts["whosonfirst"] = sources.wof_admin(f)
        await copy_csv(con, "stage_admin", sources.STAGE_ADMIN_COLS, f)
        for name in ("openaddresses", "gnis", "zcta", "overture"):
            if name in selected:
                f = STAGE_DIR / f"{name}.csv"
                counts[name] = sources.openaddresses(f) if name == "openaddresses" else sources.csv_source(name, f)
                await copy_csv(con, "stage_point", sources.STAGE_POINT_COLS, f)
                log(f"staged {name}: {counts[name]:,}")
        await run_sql(con, "022_stage.sql")
        timings["stage"] = time.time() - step

        if "openstreetmap" in selected:
            step = time.time()
            pw = urlparse(settings.dsn).password or ""
            await asyncio.to_thread(sources.osm_to_postgis, PBF, pg_conn_string(settings.dsn), "pgeo_build", pw)
            await run_sql(con, "025_osm.sql")
            timings["osm"] = time.time() - step
            log("loaded openstreetmap")

        step = time.time()
        await con.execute(
            "SET maintenance_work_mem = '1GB'; SET work_mem = '256MB'; "
            "SET max_parallel_workers_per_gather = 4; SET max_parallel_maintenance_workers = 4"
        )
        await run_sql(con, "030_enrich_index.sql")
        timings["enrich_index"] = time.time() - step
        log("enriched and indexed")

        stats = await con.fetch("SELECT layer, count(*) AS n FROM feature GROUP BY layer ORDER BY 2 DESC")
        info = {
            "sources": selected,
            "raw_counts": counts,
            "features_by_layer": {r["layer"]: r["n"] for r in stats},
            "timings_s": {k: round(v, 1) for k, v in timings.items()},
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        await con.execute("INSERT INTO build_info (key, value) VALUES ('build', $1::jsonb)", json.dumps(info))
        await con.execute("DROP TABLE stage_admin, stage_point")

        # Atomic swap + function rebuild (functions depend on the pgeo table types).
        await ensure_api_role(con)
        async with con.transaction():
            await con.execute("SET LOCAL search_path = public")
            has_old = await con.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = 'pgeo'")
            await con.execute("DROP SCHEMA IF EXISTS pgeo_old CASCADE")
            if has_old:
                await con.execute("ALTER SCHEMA pgeo RENAME TO pgeo_old")
            await con.execute("ALTER SCHEMA pgeo_build RENAME TO pgeo")
            await con.execute("DROP SCHEMA IF EXISTS pgeo_old CASCADE")
            await run_sql(con, "040_functions.sql")
            await run_sql(con, "050_api.sql")
            await con.execute(
                "GRANT USAGE ON SCHEMA pgeo, geocode, geocode_api TO pgeo_api; "
                "GRANT SELECT ON ALL TABLES IN SCHEMA pgeo TO pgeo_api; "
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA geocode, geocode_api TO pgeo_api"
            )
        log(f"build complete in {time.time() - t0:.0f}s: {json.dumps(info['features_by_layer'])}")
    finally:
        await con.close()


async def functions_only(settings: Settings) -> None:
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        async with con.transaction():
            await run_sql(con, "010_base.sql")
            await run_sql(con, "040_functions.sql")
            await run_sql(con, "050_api.sql")
            await con.execute(
                "GRANT USAGE ON SCHEMA geocode, geocode_api TO pgeo_api; "
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA geocode, geocode_api TO pgeo_api"
            )
        log("functions applied")
    finally:
        await con.close()


async def info(settings: Settings) -> None:
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        v = await con.fetchval("SELECT value FROM pgeo.build_info WHERE key = 'build'")
        print(json.dumps(json.loads(v), indent=2))
    finally:
        await con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pgeo-load")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sources", default=",".join(ALL_SOURCES))
    sub.add_parser("functions")
    sub.add_parser("info")
    args = ap.parse_args(argv)
    settings = Settings.load()
    if args.cmd == "build":
        selected = [s for s in args.sources.split(",") if s]
        bad = [s for s in selected if s not in ALL_SOURCES]
        if bad:
            ap.error(f"unknown sources: {bad}")
        if "whosonfirst" not in selected:
            selected.insert(0, "whosonfirst")
        asyncio.run(build(settings, selected))
    elif args.cmd == "functions":
        asyncio.run(functions_only(settings))
    else:
        asyncio.run(info(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
