"""Measure the resources a build uses: wall time, CPU and memory over time, disk before and after.

    python3 tests/build/measure_build.py --engine pgeo   -- uv run --project pgeo pgeo-load build
    python3 tests/build/measure_build.py --engine pelias -- scripts/build_local.sh setup prepare import

While the command runs, every ~2 s: CPU and memory of every container whose name contains the
engine prefix (build containers such as importers included) and of host-side build processes
(DuckDB and ogr2ogr run in the pgeo loader's Python process). Output: data/buildstats/
<engine>-<time>.json with the time series and a summary (the report reads it).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = {"pelias": ("pelias", "pelias_maine"), "pgeo": ("pgeo",)}
HOST_PROCS = ("ogr2ogr", "python3", "python", "duckdb", "uv")
DISK = {
    "pelias": ["data/pelias/elasticsearch", "data/pelias/placeholder", "data/pelias/interpolation",
               "data/pelias/whosonfirst", "data/pelias/openaddresses", "data/pelias/openstreetmap"],
    "pgeo": ["data/pgeo/pgdata"],
}  # fmt: skip


def du_bytes(rel: str) -> int | None:
    p = ROOT / rel
    if not p.exists():
        return None
    r = subprocess.run(["du", "-sb", str(p)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if r.returncode != 0:  # pgdata is owned by the container's postgres user: ask docker
        r = subprocess.run(["sudo", "-n", "du", "-sb", str(p)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def mem_mb(s: str) -> float:
    s = s.strip()
    for unit, f in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("kB", 1 / 1024), ("B", 1 / 1048576)):
        if s.endswith(unit):
            try:
                return float(s[: -len(unit)]) * f
            except ValueError:
                return 0.0
    return 0.0


class Sampler(threading.Thread):
    def __init__(self, engine: str, cmd_pid: int) -> None:
        super().__init__(daemon=True)
        self.engine, self.cmd_pid = engine, cmd_pid
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            t = time.time()
            r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"],  # noqa: S603, S607
                               capture_output=True, text=True, check=False)  # fmt: skip
            containers = {}
            for line in r.stdout.splitlines():
                name, cpu, mem = (line.split("|") + ["", "", ""])[:3]
                if any(p in name for p in PREFIX[self.engine]):
                    containers[name] = {"cpu": float(cpu.rstrip("%") or 0), "mem_mb": round(mem_mb(mem.split("/")[0]))}
            # host processes started by the build command (its process tree)
            ps = subprocess.run(["ps", "--ppid", str(self.cmd_pid), "-o", "pid=", "--no-headers"],  # noqa: S603, S607
                                capture_output=True, text=True, check=False)  # fmt: skip
            pids = [str(self.cmd_pid), *ps.stdout.split()]
            host = subprocess.run(["ps", "-o", "rss=,pcpu=,comm=", "-p", ",".join(pids)],  # noqa: S603, S607
                                  capture_output=True, text=True, check=False)  # fmt: skip
            rss = cpu = 0.0
            for line in host.stdout.splitlines():
                parts = line.split(None, 2)
                if len(parts) == 3:
                    rss += float(parts[0])
                    cpu += float(parts[1])
            self.samples.append({"t": round(t, 1), "containers": containers,
                                 "host": {"mem_mb": round(rss / 1024), "cpu": round(cpu, 1)}})  # fmt: skip
            self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=30)


def summarize(samples: list[dict], t0: float, t1: float) -> dict:
    tot_mem = [sum(c["mem_mb"] for c in s["containers"].values()) + s["host"]["mem_mb"] for s in samples]
    tot_cpu = [sum(c["cpu"] for c in s["containers"].values()) + s["host"]["cpu"] for s in samples]
    peak_by: dict[str, int] = {}
    for s in samples:
        for n, c in s["containers"].items():
            peak_by[n] = max(peak_by.get(n, 0), c["mem_mb"])
        peak_by["host build processes"] = max(peak_by.get("host build processes", 0), s["host"]["mem_mb"])
    return {
        "wall_s": round(t1 - t0),
        "peak_mem_mb": max(tot_mem, default=0),
        "avg_cpu_cores": round(sum(tot_cpu) / max(len(tot_cpu), 1) / 100, 2),
        "peak_cpu_cores": round(max(tot_cpu, default=0) / 100, 2),
        "peak_mem_by_process_mb": dict(sorted(peak_by.items(), key=lambda kv: -kv[1])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=list(PREFIX), required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("give the build command after --")
    disk_before = {d: du_bytes(d) for d in DISK[a.engine]}
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=ROOT)  # noqa: S603 (operator-supplied build command)
    sampler = Sampler(a.engine, proc.pid)
    sampler.start()
    rc = proc.wait()
    t1 = time.time()
    sampler.stop()
    disk_after = {d: du_bytes(d) for d in DISK[a.engine]}
    out = {
        "engine": a.engine,
        "command": cmd,
        "exit_code": rc,
        "started": datetime.fromtimestamp(t0).astimezone().isoformat(timespec="seconds"),
        "host_cpu_threads": subprocess.run(["nproc"], capture_output=True, text=True, check=False).stdout.strip(),  # noqa: S607
        "summary": summarize(sampler.samples, t0, t1),
        "disk_before_bytes": disk_before,
        "disk_after_bytes": disk_after,
        "samples": sampler.samples,
    }
    dest = ROOT / "data" / "buildstats" / f"{a.engine}-{datetime.now().astimezone():%Y%m%d-%H%M}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    s = out["summary"]
    print(f"measure_build: {a.engine} exit={rc} wall={s['wall_s']}s peak_mem={s['peak_mem_mb']} MB "
          f"avg_cpu={s['avg_cpu_cores']} cores -> {dest.relative_to(ROOT)}")  # fmt: skip
    return rc


if __name__ == "__main__":
    sys.exit(main())
