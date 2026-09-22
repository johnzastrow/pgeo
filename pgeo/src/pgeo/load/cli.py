"""pgeo-load: build the geocoder database from the raw inputs in data/.

    uv run pgeo-load build [--build ny] [--sources whosonfirst,openaddresses,openstreetmap,...]
    uv run pgeo-load functions        # re-apply sql/040_functions.sql only
    uv run pgeo-load info             # show the current build's metadata

--build names the states to load (regions/regions.json); it defaults to $PGEO_BUILD, else "me".
One database holds one build.

The build runs in schema pgeo_build and is swapped into pgeo atomically (together with the
query functions, which depend on the table types), so the API never sees a partial build.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from pgeo.load import sources
from pgeo.regions import Region, region, stack_env
from pgeo.settings import DATA_DIR, SECRETS_FILE, SQL_DIR, Settings, _read_env_file

ALL_SOURCES = ["whosonfirst", "openaddresses", "openstreetmap", "gnis", "zcta", "overture"]
STAGE_DIR = DATA_DIR / "pgeo" / "stage"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def stamp_version(con: asyncpg.Connection) -> None:
    """geocode.engine_version() returns the package version (single source: pyproject.toml)."""
    from importlib.metadata import version

    v = version("pgeo")
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):  # validated before it becomes SQL text
        raise ValueError(f"unexpected package version {v!r}")
    await con.execute(
        "CREATE OR REPLACE FUNCTION geocode.engine_version() RETURNS text "
        f"LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT '{v}'::text $$"
    )


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


async def build(settings: Settings, selected: list[str], reg: Region) -> None:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"build {reg.build}: {', '.join(reg.states)}")
    timings: dict[str, float] = {}
    counts: dict[str, int] = {}
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        t0 = time.time()
        await run_sql(con, "010_base.sql")
        # Which states this build holds, for the region name, abbreviation and gid that every
        # feature carries. 010_base.sql creates the table empty; the facts are the registry's.
        await con.executemany(
            "INSERT INTO geocode.region_ref (wof_id, name, abbr, fips) VALUES ($1, $2, $3, $4)",
            reg.ref_rows(),
        )
        await con.execute("DROP SCHEMA IF EXISTS pgeo_build CASCADE; CREATE SCHEMA pgeo_build")
        await con.execute("SET search_path = pgeo_build, public")
        await run_sql(con, "020_tables.sql")

        # Who's On First is always loaded: it provides the admin hierarchy.
        step = time.time()
        f = STAGE_DIR / "wof_admin.csv"
        counts["whosonfirst"] = sources.wof_admin(f, reg)
        await copy_csv(con, "stage_admin", sources.STAGE_ADMIN_COLS, f)
        for name in ("openaddresses", "gnis", "zcta", "overture"):
            if name in selected:
                f = STAGE_DIR / f"{name}.csv"
                counts[name] = (sources.openaddresses(f, reg) if name == "openaddresses"
                                else sources.csv_source(name, f, reg))
                await copy_csv(con, "stage_point", sources.STAGE_POINT_COLS, f)
                log(f"staged {name}: {counts[name]:,}")
        await run_sql(con, "022_stage.sql")
        timings["stage"] = time.time() - step

        if "openstreetmap" in selected:
            step = time.time()
            pw = urlparse(settings.dsn).password or ""
            await asyncio.to_thread(sources.osm_to_postgis, reg.pbfs(),
                                    pg_conn_string(settings.dsn), "pgeo_build", pw, log)
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
            "build": reg.build,
            "states": list(reg.states),
            "sources": selected,
            "raw_counts": counts,
            "features_by_layer": {r["layer"]: r["n"] for r in stats},
            "timings_s": {k: round(v, 1) for k, v in timings.items()},
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        await con.execute("INSERT INTO build_info (key, value) VALUES ('build', $1::jsonb)", json.dumps(info))
        # Staging and raw tables are not needed at query time (~300 MB).
        await con.execute("DROP TABLE stage_admin, stage_point, feature_raw")

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
            await run_sql(con, "060_address.sql")
            await stamp_version(con)
            # PostgREST caches the function list; tell it to reload (no-op without PostgREST)
            await con.execute("NOTIFY pgrst, 'reload schema'")
            await con.execute(
                "GRANT USAGE ON SCHEMA pgeo, geocode, geocode_api TO pgeo_api; "
                "GRANT SELECT ON ALL TABLES IN SCHEMA pgeo TO pgeo_api; "
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA geocode, geocode_api TO pgeo_api"
            )
        # VACUUM sets the visibility map, so index-only scans skip the heap from the first query
        # of a fresh build (ANALYZE ran in 030; VACUUM cannot run inside the swap transaction).
        for (stmt,) in await con.fetch(
            "SELECT format('VACUUM (ANALYZE) %I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'pgeo'"
        ):
            await con.execute(stmt)
        log(f"build complete in {time.time() - t0:.0f}s: {json.dumps(info['features_by_layer'])}")
    finally:
        await con.close()


async def functions_only(settings: Settings, reg: Region) -> None:
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        async with con.transaction():
            # 010_base.sql recreates geocode.region_ref empty, and the query functions read it
            # (the trailing-state strip in the parser, the boundary.gid filters, attribution),
            # so it has to be refilled here too, not only by a build.
            await run_sql(con, "010_base.sql")
            await con.executemany(
                "INSERT INTO geocode.region_ref (wof_id, name, abbr, fips) "
                "VALUES ($1, $2, $3, $4)",
                reg.ref_rows(),
            )
            await run_sql(con, "040_functions.sql")
            await run_sql(con, "050_api.sql")
            await run_sql(con, "060_address.sql")
            await stamp_version(con)
            # PostgREST caches the function list; tell it to reload (no-op without PostgREST)
            await con.execute("NOTIFY pgrst, 'reload schema'")
            await con.execute(
                "GRANT USAGE ON SCHEMA geocode, geocode_api TO pgeo_api; "
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA geocode, geocode_api TO pgeo_api"
            )
        log("functions applied")
    finally:
        await con.close()


async def info(settings: Settings, reg: Region) -> None:
    con = await asyncpg.connect(settings.dsn, timeout=30)
    try:
        built = await con.fetchval("SELECT to_regclass('pgeo.build_info')")
        if built is None:
            # A stack that has been set up but never built, or one still building: say so
            # instead of failing on a missing table.
            print(f"no build in {settings.dsn.rsplit('/', 1)[-1]} yet "
                  f"(run: scripts/pgeo_rebuild.sh --build {reg.build})")
            return
        v = await con.fetchval("SELECT value FROM pgeo.build_info WHERE key = 'build'")
        print(json.dumps(json.loads(v), indent=2))
    finally:
        await con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pgeo-load")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sources", default=",".join(ALL_SOURCES))
    b.add_argument("--build", dest="build_name", default=None,
                   help="build name or state list (default: $PGEO_BUILD, else me)")
    f = sub.add_parser("functions")
    f.add_argument("--build", dest="build_name", default=None,
                   help="build name or state list (default: $PGEO_BUILD, else me)")
    i = sub.add_parser("info")
    i.add_argument("--build", dest="build_name", default=None,
                   help="build name or state list (default: $PGEO_BUILD, else me)")
    args = ap.parse_args(argv)
    # --build also says which stack to talk to: its database, port and parser service. Anything
    # already set in the environment wins, so PGEO_DSN still overrides everything.
    reg = region(getattr(args, "build_name", None))
    for key, value in stack_env(reg.build).items():
        os.environ.setdefault(key, value)
    settings = Settings.load()
    if args.cmd == "build":
        selected = [s for s in args.sources.split(",") if s]
        bad = [s for s in selected if s not in ALL_SOURCES]
        if bad:
            ap.error(f"unknown sources: {bad}")
        if "whosonfirst" not in selected:
            selected.insert(0, "whosonfirst")
        asyncio.run(build(settings, selected, reg))
    elif args.cmd == "functions":
        asyncio.run(functions_only(settings, reg))
    else:
        asyncio.run(info(settings, reg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
