"""Run the capacity matrix from docs/LOAD_TEST_PLAN.md against the local Pelias stack.

For each configuration: recreate the query services with CPU pinning, memory limits,
Elasticsearch heap and API worker count; warm up; run the 3-user validation; then ramp
users until the breaking point. Resource usage is sampled every 2 s. Results go to
data/loadtest/<run-id>/ as JSON; tests/load/report.py turns them into Markdown.

Usage (repo root):
    python3 tests/load/run_matrix.py [--configs M0,C1,...] [--datasets D1,...] [--quick] [--no-restore]

The load generator (k6 in Docker) is pinned to physical core 5 (CPUs 5,11); stacks use
distinct physical cores starting at 0, so they never share a core with k6.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "pelias_maine"
K6_DIR = Path(__file__).resolve().parent
K6_IMAGE = "grafana/k6:2.2.0"
K6_CPUS = "5,11"
API = "http://127.0.0.1:4000"
SERVICES = ["elasticsearch", "libpostal", "placeholder", "pip", "interpolation", "api"]

# SLO p95 targets (ms) per endpoint; see docs/LOAD_TEST_PLAN.md section 2.
SLO = {"autocomplete": 250, "search": 750, "structured": 750, "reverse": 400}
ERR_SLO = 0.01
BREAK_ERR = 0.05
BREAK_FACTOR = 5  # p95 above 5x target counts as broken

# Memory limits per service (GB) for the "standard" and "floor" profiles. The trial run
# (20260918-1814) OOM-killed pip at 0.4 GB (it grows under reverse load from ~0.36 GB) and
# crash-looped interpolation at 1.9 GB (~1.8 GB working set), so the floor sits above that.
MEM_STD = {"libpostal": 2.2, "interpolation": 2.3, "pip": 0.9, "placeholder": 0.8}
# Full run 20260918-1834: placeholder was OOM-killed at 0.4 GB under load (C1 at 16 users,
# C2 at 12), peaking ~408 MB, so the floor profile gives it 0.8 GB.
MEM_FLOOR = {"libpostal": 2.0, "interpolation": 2.1, "pip": 0.7, "placeholder": 0.8}
OS_RESERVE_GB = 0.8  # what a small Debian VM needs besides the containers

CONFIGS = {
    # id: cpus (None = unconstrained), es heap, es limit, api workers, memory profile
    "M0": {"cpus": None, "heap": "4g", "es_limit": None, "workers": 1, "mem": None},
    "C1": {"cpus": 1, "heap": "768m", "es_limit": 1.5, "workers": 1, "mem": MEM_FLOOR},
    # Added after C1 hit the floor profile's memory wall (placeholder OOM at 16 users):
    # the same single CPU with standard memory isolates the CPU-bound limit.
    "C1s": {
        "cpus": 1,
        "heap": "768m",
        "es_limit": 1.5,
        "workers": 1,
        "mem": MEM_STD | {"placeholder": 1.0},
    },
    "C2": {"cpus": 2, "heap": "768m", "es_limit": 1.5, "workers": 2, "mem": MEM_FLOOR},
    "C3": {"cpus": 2, "heap": "1g", "es_limit": 2.0, "workers": 2, "mem": MEM_STD},
    "C4": {"cpus": 4, "heap": "2g", "es_limit": 3.0, "workers": 4, "mem": MEM_STD},
    "C4a": {"cpus": 4, "heap": "2g", "es_limit": 3.0, "workers": 1, "mem": MEM_STD},
}
# Data-volume dimension: cumulative source subsets, each its own ES index built by
# _reindex from the full index (D5 = the full "pelias" index as deployed).
ES = "http://127.0.0.1:9200"
DATASETS = {
    "D1": ["whosonfirst"],
    "D2": ["whosonfirst", "openaddresses"],
    "D3": ["whosonfirst", "openaddresses", "openstreetmap"],
    "D4": ["whosonfirst", "openaddresses", "openstreetmap", "gnis", "zcta"],
    "D5": None,
}

RAMP = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512]


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def env() -> dict[str, str]:
    e = dict(os.environ)
    e["DOCKER_USER"] = f"{os.getuid()}:{os.getgid()}"
    return e


def api_limit_gb(cfg: dict) -> float:
    return 0.3 + 0.12 * cfg["workers"]


def budget_gb(cfg: dict) -> float | None:
    if cfg["mem"] is None:
        return None
    return round(
        sum(cfg["mem"].values()) + cfg["es_limit"] + api_limit_gb(cfg) + OS_RESERVE_GB,
        1,
    )


def es(method: str, path: str, body: dict | None = None, timeout: int = 3600) -> dict:
    cmd = [
        "curl",
        "-sf",
        "-m",
        str(timeout),
        "-X",
        method,
        f"{ES}{path}",
        "-H",
        "Content-Type: application/json",
    ]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ES {method} {path} failed: {r.stderr or r.stdout}")
    return json.loads(r.stdout or "{}")


def wait_es(timeout: int = 600) -> None:
    """Wait for Elasticsearch after a stack restart (the 2026-09-18 dataset run failed on
    `GET /pelias` because the previous run had just recreated the stack)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if es("GET", "/_cluster/health?wait_for_status=yellow&timeout=10s", timeout=20).get("status") in (
                "yellow",
                "green",
            ):
                return
        except RuntimeError:
            pass
        time.sleep(5)
    raise RuntimeError("Elasticsearch not ready")


def index_name(dataset: str | None) -> str:
    return "pelias" if dataset in (None, "D5") else f"pelias_{dataset.lower()}"


def ensure_dataset_index(dataset: str) -> int:
    """Create the subset index (same settings and mappings) by reindexing; return doc count."""
    name = index_name(dataset)
    exists = (
        subprocess.run(["curl", "-sf", "-o", "/dev/null", f"{ES}/{name}"]).returncode
        == 0
    )
    if not exists:
        src = es("GET", "/pelias")["pelias"]
        settings = src["settings"]["index"]
        for k in (
            "uuid",
            "creation_date",
            "version",
            "provided_name",
            "routing",
            "blocks",
        ):
            settings.pop(k, None)
        es(
            "PUT",
            f"/{name}",
            {"settings": {"index": settings}, "mappings": src["mappings"]},
        )
        es(
            "POST",
            "/_reindex?wait_for_completion=true&refresh=true",
            {
                "source": {
                    "index": "pelias",
                    "query": {"terms": {"source": DATASETS[dataset]}},
                },
                "dest": {"index": name},
            },
        )
        es("POST", f"/{name}/_forcemerge?max_num_segments=1")
    return es("GET", f"/{name}/_count")["count"]


def index_mb(name: str) -> int:
    rows = es("GET", f"/_cat/indices/{name}?bytes=b&h=store.size&format=json")
    return round(int(rows[0]["store.size"]) / 1048576)


def write_api_config(dataset: str, path: Path) -> None:
    cfg = json.loads((PROJECT / "pelias.json").read_text())
    cfg.setdefault("api", {})["indexName"] = index_name(dataset)
    path.write_text(json.dumps(cfg, indent=1))


def write_override(cfg: dict, path: Path, api_config: Path | None = None) -> None:
    """Compose override: cpuset, memory limits (no swap), ES heap, API workers."""
    lines = ["services:"]
    cpuset = (
        None if cfg["cpus"] is None else ",".join(str(c) for c in range(cfg["cpus"]))
    )
    for svc in SERVICES:
        lines.append(f"  {svc}:")
        if cpuset:
            lines.append(f'    cpuset: "{cpuset}"')
        limit = None
        if cfg["mem"] is not None:
            if svc == "elasticsearch":
                limit = cfg["es_limit"]
            elif svc == "api":
                limit = api_limit_gb(cfg)
            else:
                limit = cfg["mem"][svc]
        if limit:
            mb = int(limit * 1024)
            lines += [f"    mem_limit: {mb}m", f"    memswap_limit: {mb}m"]
        if svc == "elasticsearch":
            heap = cfg["heap"]
            lines += [
                "    environment:",
                f'      - "ES_JAVA_OPTS=-Xms{heap} -Xmx{heap}"',
                '      - "path.repo=/usr/share/elasticsearch/snapshots"',
            ]
        if svc == "api" and api_config is not None:
            lines += ["    volumes:", f'      - "{api_config}:/code/pelias.json:ro"']
        if svc == "api":
            lines += [
                "    environment:",
                '      - "PORT=4000"',
                f'      - "CPUS={cfg["workers"]}"',
            ]
    path.write_text("\n".join(lines) + "\n")


def compose(files: list[Path], *args: str) -> None:
    cmd = ["docker", "compose", "--project-directory", str(PROJECT)]
    for f in files:
        cmd += ["-f", str(f)]
    sh(cmd + list(args), env=env(), cwd=PROJECT)


def wait_ready(timeout: int = 300) -> None:
    probes = [
        f"{API}/v1/search?text=portland%20maine&size=1",
        f"{API}/v1/reverse?point.lat=44.31&point.lon=-69.78&size=1",
        f"{API}/v1/autocomplete?text=bang&size=1",
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok = all(
            subprocess.run(["curl", "-sf", "-m", "5", "-o", "/dev/null", p]).returncode
            == 0
            for p in probes
        )
        if ok:
            return
        time.sleep(3)
    raise RuntimeError("stack did not become ready")


def container_health() -> dict:
    out = {}
    for svc in SERVICES:
        name = f"pelias_maine_{'pip' if svc == 'pip' else svc}"
        r = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}} {{.State.OOMKilled}} {{.RestartCount}}",
                name,
            ],
            text=True,
            capture_output=True,
        )
        status, oom, restarts = (r.stdout.split() + ["?", "?", "?"])[:3]
        out[svc] = {
            "status": status,
            "oom": oom == "true",
            "restarts": int(restarts) if restarts.isdigit() else -1,
        }
    return out


class Sampler(threading.Thread):
    """Samples docker stats for the query containers every ~2 s."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def run(self) -> None:
        names = [f"pelias_maine_{s}" for s in SERVICES]
        while not self._stop.is_set():
            r = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}",
                    *names,
                ],
                text=True,
                capture_output=True,
            )
            t = time.time()
            for line in r.stdout.splitlines():
                name, cpu, mem = line.split("|")
                used = mem.split("/")[0].strip()
                num = float(used[:-3]) if used[-3:] in ("GiB", "MiB", "KiB") else 0.0
                mb = num * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024}.get(used[-3:], 0)
                self.samples.append(
                    {
                        "t": t,
                        "svc": name.removeprefix("pelias_maine_"),
                        "cpu": float(cpu.rstrip("%") or 0),
                        "mem_mb": mb,
                    }
                )
            self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=10)


def summarize_resources(samples: list[dict]) -> dict:
    out = {}
    for svc in SERVICES:
        s = [x for x in samples if x["svc"] == svc]
        if s:
            cpus = sorted(x["cpu"] for x in s)
            out[svc] = {
                "cpu_avg": round(sum(cpus) / len(cpus), 1),
                "cpu_p95": round(cpus[int(0.95 * (len(cpus) - 1))], 1),
                "mem_max_mb": round(max(x["mem_mb"] for x in s)),
            }
    tot = {}
    for x in samples:
        tot.setdefault(round(x["t"]), 0.0)
        tot[round(x["t"])] += x["cpu"]
    if tot:
        v = sorted(tot.values())
        out["_stack"] = {
            "cpu_avg": round(sum(v) / len(v), 1),
            "cpu_p95": round(v[int(0.95 * (len(v) - 1))], 1),
        }
    return out


def run_k6(vus: int, duration: str, warmup: str, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm", "--network", "host", "--cpuset-cpus", K6_CPUS,
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{K6_DIR}:/work:ro", "-v", f"{out.parent}:/out",
        "-w", "/work/k6",
        "-e", f"VUS={vus}", "-e", f"DURATION={duration}", "-e", f"WARMUP={warmup}", "-e", f"BASE_URL={API}",
        K6_IMAGE, "run", "--quiet", "--no-color", "--summary-export", f"/out/{out.name}", "session.js",
    ]  # fmt: skip
    subprocess.run(
        cmd, text=True, capture_output=True
    )  # non-zero exit when thresholds fail
    return json.loads(out.read_text())


def evaluate(summary: dict) -> dict:
    m = summary["metrics"]
    res: dict = {"endpoints": {}}
    worst = 0.0
    for ep, target in SLO.items():
        d = m.get(f"http_req_duration{{ep:{ep},phase:steady}}", {})
        p95 = d.get("p(95)")
        if not d or not d.get("max"):
            continue  # endpoint not exercised in this window
        res["endpoints"][ep] = {
            k: round(d[k], 1) for k in ("med", "p(95)", "p(99)", "max") if k in d
        }
        worst = max(worst, p95 / target)
    res["qtypes"] = {}
    for qt in ("exact", "typo", "variant", "miss"):
        d = m.get(f"http_req_duration{{qtype:{qt},phase:steady}}", {})
        if d.get("max"):
            res["qtypes"][qt] = {
                k: round(d[k], 1) for k in ("med", "p(95)", "p(99)") if k in d
            }
    # k6 only emits a sub-metric that some threshold names. If a session script forgets the
    # declaration these reads fall back to 0.0, which looks exactly like a clean, idle run - so
    # say which ones were absent rather than scoring a pass on defaults.
    missing = [k for k in ("http_req_failed{phase:steady}", "http_reqs{phase:steady}") if k not in m]
    fail = m.get("http_req_failed{phase:steady}", {})
    err = fail.get("value", fail.get("rate", 0.0))
    reqs = m.get("http_reqs{phase:steady}", {})
    res.update(
        {
            "error_rate": round(err, 4),
            "req_rate": round(reqs.get("rate", 0.0), 1),
            "worst_p95_ratio": round(worst, 2),
            "pass": not missing and err < ERR_SLO and worst <= 1.0,
            "broken": err > BREAK_ERR or worst > BREAK_FACTOR,
        }
    )
    if missing:
        res["missing_metrics"] = missing
    return res


def run_config(
    cid: str, cfg: dict, out_dir: Path, quick: bool, dataset: str | None = None
) -> dict:
    rid = cid if dataset is None else f"{cid}-{dataset}"
    print(f"\n=== {rid} {cfg}", flush=True)
    override = out_dir / f"override-{rid}.yml"
    api_cfg = None
    if dataset is not None:
        api_cfg = out_dir / f"pelias-{dataset}.json"
        write_api_config(dataset, api_cfg)
    write_override(cfg, override, api_cfg)
    compose(
        [PROJECT / "docker-compose.yml", override],
        "up",
        "-d",
        "--force-recreate",
        *SERVICES,
    )
    wait_ready()
    run_k6(2, "30s", "5s", out_dir / rid / "warmup.json")  # warm caches, not recorded
    result = {
        "id": rid,
        "base_config": cid,
        "dataset": dataset,
        "docs": es("GET", f"/{index_name(dataset)}/_count")["count"],
        "index_mb": index_mb(index_name(dataset)),
        "config": cfg | {"mem": cfg["mem"]},
        "budget_gb": budget_gb(cfg),
        "runs": {},
    }

    # 1. validation at 3 users
    val_dur, val_warm = ("60s", "15s") if quick else ("180s", "45s")
    sampler = Sampler()
    sampler.start()
    summ = run_k6(3, val_dur, val_warm, out_dir / rid / "validate-3.json")
    sampler.stop()
    health = container_health()
    v = evaluate(summ) | {
        "resources": summarize_resources(sampler.samples),
        "health": health,
    }
    # A restart or OOM during validation fails it regardless of latency.
    v["died"] = any(
        h["status"] != "running" or h["oom"] or h["restarts"] > 0
        for h in health.values()
    )
    v["pass"] = v["pass"] and not v["died"]
    result["runs"]["validate"] = v
    print(
        f"  validate@3: pass={v['pass']} err={v['error_rate']} worst={v['worst_p95_ratio']} "
        f"rps={v['req_rate']} stack_cpu_p95={v['resources'].get('_stack', {}).get('cpu_p95')}",
        flush=True,
    )

    # 2. ramp
    ramp = []
    limit = 0
    step_dur, step_warm = ("30s", "10s") if quick else ("60s", "15s")
    for n in RAMP:
        sampler = Sampler()
        sampler.start()
        summ = run_k6(n, step_dur, step_warm, out_dir / rid / f"ramp-{n:03d}.json")
        sampler.stop()
        e = evaluate(summ)
        health = container_health()
        died = any(
            h["status"] != "running" or h["oom"] or h["restarts"] > 0
            for h in health.values()
        )
        step = e | {
            "vus": n,
            "resources": summarize_resources(sampler.samples),
            "health": health,
            "died": died,
        }
        ramp.append(step)
        if e["pass"]:
            limit = n
        print(
            f"  ramp@{n}: pass={e['pass']} err={e['error_rate']} worst={e['worst_p95_ratio']} "
            f"rps={e['req_rate']} stack_cpu_p95={step['resources'].get('_stack', {}).get('cpu_p95')}"
            f"{' DIED' if died else ''}",
            flush=True,
        )
        if died or e["broken"]:
            break
    result["runs"]["ramp"] = ramp
    result["limit_users"] = limit
    result["breaking_users"] = (
        ramp[-1]["vus"] if ramp and (ramp[-1]["broken"] or ramp[-1]["died"]) else None
    )
    (out_dir / f"{rid}.json").write_text(json.dumps(result, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument(
        "--quick", action="store_true", help="short windows, for trying the harness"
    )
    ap.add_argument(
        "--no-restore", action="store_true", help="leave the last config running"
    )
    ap.add_argument(
        "--datasets",
        default="",
        help="comma list of data subsets (D1..D5) to run each config against; default: full index",
    )
    args = ap.parse_args()
    datasets = [d for d in args.datasets.split(",") if d] or [None]
    for d in datasets:
        if d is not None and d not in DATASETS:
            ap.error(f"unknown dataset {d}")
    run_id = datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = ROOT / "data" / "loadtest" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"run {run_id} -> {out_dir}", flush=True)
    try:
        if any(d is not None for d in datasets):
            wait_es()
        for d in datasets:
            if d is not None:
                print(
                    f"dataset {d}: {ensure_dataset_index(d):,} docs in {index_name(d)}",
                    flush=True,
                )
        for d in datasets:
            for cid in args.configs.split(","):
                run_config(cid, CONFIGS[cid], out_dir, args.quick, d)
    finally:
        if not args.no_restore:
            print("restoring the default stack", flush=True)
            compose(
                [PROJECT / "docker-compose.yml"],
                "up",
                "-d",
                "--force-recreate",
                *SERVICES,
            )
    print(f"done: {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
