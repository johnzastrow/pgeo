"""Find the smallest server that still serves 3 concurrent users within every latency target.

    python3 tests/load/find_floor.py [--engines pgeo,pelias] [--minutes 5]
    python3 tests/load/find_floor.py --resume data/loadtest/<run>-floor --only cpus

For each engine, start from a configuration known to pass and shrink one resource at a time
(CPU quota, then each service's memory), keeping a step only if a 3-user run of --minutes stays
within all targets with no errors, restarts or out-of-memory kills. The result is the smallest
passing configuration, reported as vCPU and total memory (containers + 0.8 GB for the OS).

Trials: data/loadtest/<time>-floor/<engine>-<n>.json (same format as the matrix runs) and
floor-<engine>.json with the search path and the result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import run_matrix as rm
import run_matrix_pgeo as rp

OS_GB = rm.OS_RESERVE_GB

# pgeo pure SQL: db and PostgREST memory (GB), shared_buffers follows the db limit (~25%)
PGEO_START = {"cpus": 1.0, "db": 0.6, "rest": 0.25}
PGEO_STEPS = [("cpus", [0.5, 0.25, 0.15, 0.1]), ("db", [0.45, 0.35, 0.25]), ("rest", [0.15, 0.1])]

# Pelias: per-service memory (GB); Elasticsearch heap is 60% of its limit
PELIAS_START = {"cpus": 1.0, "es": 1.5, "api": 0.42, "libpostal": 2.0, "interpolation": 2.1, "pip": 0.7,
                "placeholder": 0.8}  # fmt: skip
PELIAS_STEPS = [("cpus", [0.5, 0.25, 0.15]), ("es", [1.2, 1.0, 0.8]), ("placeholder", [0.6, 0.45, 0.35]),
                ("pip", [0.55, 0.45]), ("interpolation", [1.8, 1.5, 1.2]), ("libpostal", [1.8, 1.6]),
                ("api", [0.3, 0.22])]  # fmt: skip


def total_gb(engine: str, c: dict) -> float:
    return round(sum(v for k, v in c.items() if k != "cpus") + OS_GB, 2)


def pgeo_override(c: dict, path: Path, tuning: Path) -> None:
    sb = max(32, int(c["db"] * 1024 * 0.25) // 16 * 16)
    tuning.mkdir(parents=True, exist_ok=True)
    (tuning / "active.conf").write_text(
        f"shared_buffers = '{sb}MB'\neffective_cache_size = '{int(c['db'] * 1024 * 0.75)}MB'\n"
        "work_mem = '4MB'\nmax_parallel_workers_per_gather = 0\nmax_connections = 20\n"
    )
    lines = ["services:"]
    for svc, key in (("db", "db"), ("rest", "rest")):
        mb = int(c[key] * 1024)
        lines += [f"  {svc}:", '    cpuset: "0"', f"    cpus: {c['cpus']}", f"    mem_limit: {mb}m", f"    memswap_limit: {mb}m"]
        if svc == "db":
            lines += ["    volumes:", f'      - "{tuning}:/etc/postgresql/tuning:ro"']
        else:
            lines += ["    environment:", '      PGRST_DB_POOL: "3"']
    path.write_text("\n".join(lines) + "\n")


def pelias_override(c: dict, path: Path) -> None:
    lines = ["services:"]
    lim = {"elasticsearch": c["es"], "api": c["api"], "libpostal": c["libpostal"], "interpolation": c["interpolation"],
           "pip": c["pip"], "placeholder": c["placeholder"]}  # fmt: skip
    for svc in rm.SERVICES:
        mb = int(lim[svc] * 1024)
        lines += [f"  {svc}:", '    cpuset: "0"', f"    cpus: {c['cpus']}", f"    mem_limit: {mb}m", f"    memswap_limit: {mb}m"]
        if svc == "elasticsearch":
            heap = max(256, int(mb * 0.6) // 64 * 64)
            lines += ["    environment:", f'      - "ES_JAVA_OPTS=-Xms{heap}m -Xmx{heap}m"',
                      '      - "path.repo=/usr/share/elasticsearch/snapshots"']  # fmt: skip
        if svc == "api":
            lines += ["    environment:", '      - "PORT=4000"', '      - "CPUS=1"']
    path.write_text("\n".join(lines) + "\n")


def trial(engine: str, c: dict, out: Path, n: int, minutes: int) -> dict:
    rid = f"{engine}-{n:02d}"
    ov = out / f"override-{rid}.yml"
    try:
        if engine == "pgeo":
            rp.stop_all()
            pgeo_override(c, ov, out / f"tuning-{rid}")
            rp.compose([rp.PGEO / "compose.yml", ov], "up", "-d", "--force-recreate", "--no-deps", "db", "rest")
            rp.start_edge("0")
            rm.API = rp.ENGINES["rest"]["url"]
            names = rp.names("rest")
        else:
            pelias_override(c, ov)
            rm.compose([rm.PROJECT / "docker-compose.yml", ov], "up", "-d", "--force-recreate", *rm.SERVICES)
            rm.API = "http://127.0.0.1:4000"
            names = None
        rm.wait_ready(timeout=240)
    except (subprocess.CalledProcessError, RuntimeError) as e:
        why = f"did not start: {type(e).__name__}"
        out_doc = {"id": rid, "engine": engine, "config": c, "total_gb": total_gb(engine, c), "pass": False, "why": why}
        (out / f"{rid}.json").write_text(json.dumps(out_doc, indent=1))
        # print here too: without it a trial that never started leaves a gap in the log
        print(f"  {rid}: {json.dumps(c)} total {out_doc['total_gb']} GB -> {why}", flush=True)
        return out_doc
    rm.run_k6(2, "30s", "5s", out / rid / "warmup.json")
    if engine == "pgeo":
        s = rp.Sampler("rest")
    else:
        s = rm.Sampler()
    s.start()
    summ = rm.run_k6(3, f"{minutes * 60}s", "30s", out / rid / "validate-3.json")
    s.stop()
    h = rp.health("rest") if engine == "pgeo" else rm.container_health()
    died = any(x["status"] != "running" or x["oom"] or x["restarts"] > 0 for x in h.values())
    e = rm.evaluate(summ)
    res = rp.summarize("rest", s.samples) if engine == "pgeo" else rm.summarize_resources(s.samples)
    ok = e["pass"] and not died
    why = "ok" if ok else ("died or OOM" if died else f"over target (worst {e['worst_p95_ratio']}x, errors {e['error_rate']})")
    out_doc = {"id": rid, "engine": engine, "config": c, "total_gb": total_gb(engine, c), "pass": ok, "why": why,
               "validate": e | {"resources": res, "health": h}}  # fmt: skip
    (out / f"{rid}.json").write_text(json.dumps(out_doc, indent=1))
    print(f"  {rid}: {json.dumps(c)} total {out_doc['total_gb']} GB -> {why}", flush=True)
    del names
    return out_doc


def search(engine: str, out: Path, minutes: int, resume: Path | None = None,
           only: list[str] | None = None) -> dict:
    start, steps = (PGEO_START, PGEO_STEPS) if engine == "pgeo" else (PELIAS_START, PELIAS_STEPS)
    if resume:  # continue from an earlier floor, e.g. to probe a step list that has grown
        found = json.loads((resume / f"floor-{engine}.json").read_text()).get("result")
        if found:
            start = found["config"]
    if only:
        steps = [(k, vs) for k, vs in steps if k in only]
    # a resumed search only tries values smaller than the ones already reached
    steps = [(k, [v for v in vs if v < start.get(k, float("inf"))]) for k, vs in steps]
    steps = [(k, vs) for k, vs in steps if vs]
    best = dict(start)
    path = []
    n = 0
    print(f"\n=== {engine}: start {json.dumps(best)}", flush=True)
    t = trial(engine, best, out, n, minutes)
    path.append(t)
    if not t["pass"]:
        print(f"  {engine}: the starting configuration fails; no floor found", flush=True)
        return {"engine": engine, "result": None, "path": path}
    for key, values in steps:
        for val in values:
            n += 1
            cand = best | {key: val}
            t = trial(engine, cand, out, n, minutes)
            path.append(t)
            if not t["pass"]:
                break  # smaller values of this resource will fail too; move to the next resource
            best = cand
    result = {"config": best, "vcpu": best["cpus"], "memory_gb": total_gb(engine, best)}
    print(f"=== {engine} floor: {best['cpus']} vCPU, {result['memory_gb']} GB total", flush=True)
    doc = {"engine": engine, "minutes": minutes, "result": result,
           "path": [{"config": p["config"], "total_gb": p.get("total_gb"), "pass": p["pass"], "why": p["why"]}
                    for p in path]}  # fmt: skip
    (out / f"floor-{engine}.json").write_text(json.dumps(doc, indent=1))
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="pgeo,pelias")
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--resume", type=Path, help="an earlier -floor directory: start from its result")
    ap.add_argument("--only", help="comma-separated resources to shrink (default: all), e.g. cpus")
    a = ap.parse_args()
    out = rm.ROOT / "data" / "loadtest" / (datetime.now().astimezone().strftime("%Y%m%d-%H%M") + "-floor")
    out.mkdir(parents=True, exist_ok=True)
    print(f"run {out.name}", flush=True)
    try:
        for e in a.engines.split(","):
            search(e, out, a.minutes, a.resume, a.only.split(",") if a.only else None)
    finally:
        print("restoring default stacks", flush=True)
        rm.compose([rm.PROJECT / "docker-compose.yml"], "up", "-d", "--force-recreate", *rm.SERVICES)
        rp.restore()
    print(f"done: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
