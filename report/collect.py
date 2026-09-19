"""Capture live facts for the report into report/data/snapshot.json.

Facts that only exist in running systems (document counts, index and table sizes, versions,
host hardware) are read here and saved, so the report can be rebuilt offline. Anything that
cannot be reached keeps its previous snapshot value, and the report shows when each part was
captured.

    uv run --project report python report/collect.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = tomllib.loads((ROOT / "report" / "inputs.toml").read_text())
SNAP = ROOT / INPUTS["snapshot"]["file"]
ES = "http://127.0.0.1:9200"


def run(cmd: list[str], timeout: int = 30) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603 (fixed argv)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def psql(sql: str) -> list[list[str]] | None:
    out = run(["docker", "exec", "pgeo_db", "psql", "-U", "pgeo", "-d", "pgeo", "-AtF", "|", "-c", sql])
    return None if out is None else [line.split("|") for line in out.strip().splitlines() if line]


def es(path: str, body: dict | None = None) -> dict | None:
    cmd = ["curl", "-sf", "-m", "20", f"{ES}{path}", "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = run(cmd)
    return None if out is None else json.loads(out)


def pgeo_facts() -> dict | None:
    rows = psql("SELECT layer, source, count(*) FROM pgeo.feature GROUP BY 1, 2 ORDER BY 1, 2")
    if rows is None:
        return None
    sizes = psql(
        "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'pgeo' AND c.relkind = 'r' ORDER BY 2 DESC"
    )
    build = psql("SELECT value FROM pgeo.build_info WHERE key = 'build'")
    ver = psql("SELECT version(), postgis_lib_version(), pg_database_size('pgeo'), geocode.engine_version()")
    idx = psql(
        "SELECT indexrelname, pg_relation_size(indexrelid) FROM pg_stat_user_indexes WHERE schemaname = 'pgeo' "
        "ORDER BY 2 DESC"
    )
    return {
        "by_layer_source": [{"layer": a, "source": b, "n": int(c)} for a, b, c in rows],
        "table_bytes": {a: int(b) for a, b in sizes or []},
        "index_bytes": {a: int(b) for a, b in idx or []},
        "build": json.loads(build[0][0]) if build else None,
        "postgres": ver[0][0].split(" on ")[0] if ver else None,
        "postgis": ver[0][1] if ver else None,
        "db_bytes": int(ver[0][2]) if ver else None,
        "engine_version": ver[0][3] if ver else None,
    }


def pelias_facts() -> dict | None:
    agg = es("/pelias/_search?size=0", {"aggs": {"layer": {"terms": {"field": "layer", "size": 50}},
                                                   "source": {"terms": {"field": "source", "size": 50}},
                                                   "ls": {"multi_terms": {"terms": [{"field": "layer"}, {"field": "source"}],
                                                                          "size": 200}}}})  # fmt: skip
    if agg is None:
        return None
    cat = run(["curl", "-sf", "-m", "10", f"{ES}/_cat/indices/pelias?bytes=b&h=docs.count,store.size"])
    docs, store = (cat or "0 0").split()
    info = es("/")
    ls = [{"layer": b["key"][0], "source": b["key"][1], "n": b["doc_count"]} for b in agg["aggregations"]["ls"]["buckets"]]
    images = sorted(set(re.findall(r"image:\s*(pelias/[\w-]+):", (ROOT / "projects/pelias_maine/docker-compose.yml").read_text())))
    return {
        "by_layer_source": ls,
        "docs": int(docs),
        "index_bytes": int(store),
        "elasticsearch": (info or {}).get("version", {}).get("number"),
        "images": images,
    }


def host_facts() -> dict:
    lscpu = run(["lscpu"]) or ""
    cpu = re.search(r"Model name:\s*(.+)", lscpu)
    mem_kb = int(re.search(r"MemTotal:\s*(\d+)", Path("/proc/meminfo").read_text()).group(1))
    return {
        "cpu": cpu.group(1).strip() if cpu else None,
        "logical_cpus": os.cpu_count(),
        "memory_gb": round(mem_kb / 1048576, 1),
        "kernel": os.uname().release,
        "docker": (run(["docker", "--version"]) or "").strip(),
    }


def raw_data() -> list[dict]:
    """Raw inputs (bytes on disk) as downloaded or extracted."""
    items = [
        ("OpenStreetMap", "Geofabrik Maine extract (PBF)", "data/raw/osm/maine-latest.osm.pbf"),
        ("OpenAddresses", "Maine statewide addresses", "data/pelias/openaddresses"),
        ("Who's On First", "admin polygons (US SQLite, Maine subset used)", "data/pelias/whosonfirst"),
        ("USGS GNIS", "Domestic Names, Maine", "data/raw/gnis"),
        ("Census ZCTA", "2025 Gazetteer (national, filtered)", "data/raw/zcta"),
        ("Overture Maps", "places, 2026-08-19 release, Maine bbox", "data/raw/overture/places_2026-08-19.0_me_bbox.parquet"),
        ("TIGER / OA interpolation", "Pelias interpolation database", "data/pelias/interpolation"),
    ]
    out = []
    for name, what, rel in items:
        p = ROOT / rel
        if p.is_file():
            size = p.stat().st_size
        elif p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        else:
            size = None
        out.append({"source": name, "what": what, "path": rel, "bytes": size})
    return out


def test_sets() -> dict:
    cases = json.loads((ROOT / "tests/accuracy/cases.json").read_text())
    fuzz = json.loads((ROOT / "tests/accuracy/fuzz_cases.json").read_text())
    corpus = json.loads((ROOT / "tests/load/corpus/maine_corpus.json").read_text())
    comp: dict[str, int] = {}
    for c in cases:
        k = f"{c['endpoint']}|{c['kind']}|{c['qtype']}"
        comp[k] = comp.get(k, 0) + 1
    return {
        "accuracy_cases": len(cases),
        "accuracy_composition": comp,
        "fuzz_cases": len(fuzz),
        "fuzz_levels": sorted({c["level"] for c in fuzz}),
        "load_corpus": {k: len(v) for k, v in corpus.items()},
    }


def main() -> None:
    old = json.loads(SNAP.read_text()) if SNAP.is_file() else {}
    now = datetime.now().isoformat(timespec="seconds")
    snap = dict(old)
    for key, fn in (("pgeo", pgeo_facts), ("pelias", pelias_facts)):
        val = fn()
        if val is not None:
            snap[key] = val | {"captured": now}
        else:
            print(f"collect: {key} not reachable; keeping the snapshot from {old.get(key, {}).get('captured', 'never')}")
    snap["host"] = host_facts() | {"captured": now}
    snap["raw_data"] = raw_data()
    snap["test_sets"] = test_sets()
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    SNAP.write_text(json.dumps(snap, indent=1) + "\n")
    print(f"collect: wrote {SNAP.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
