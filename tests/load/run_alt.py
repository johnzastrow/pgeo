"""Capacity for an engine that does not speak the Pelias API, measured like the ones that do.

Photon and Nominatim need their own k6 session scripts because their URLs differ, but everything
that decides the result - corpus, query mix, keystroke pattern, think times, ramp, latency targets
and the pass rule - is shared with run_matrix.py. This module holds that shared loop; the
per-engine entry points are run_photon.py and run_nominatim.py.

Results: data/loadtest/<run>-<engine>/<engine>.json, in the same shape as the other runners.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import run_matrix as rm

ROOT = Path(__file__).resolve().parents[2]


def run_k6(session: str, base: str, vus: int, duration: str, warmup: str, out: Path) -> dict:
    """As run_matrix.run_k6, but an arbitrary session script and base URL."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm", "--network", "host", "--cpuset-cpus", rm.K6_CPUS,
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{rm.K6_DIR}:/work:ro", "-v", f"{out.parent}:/out",
        "-w", "/work/k6",
        "-e", f"VUS={vus}", "-e", f"DURATION={duration}", "-e", f"WARMUP={warmup}",
        "-e", f"BASE_URL={base}",
        rm.K6_IMAGE, "run", "--quiet", "--no-color",
        "--summary-export", f"/out/{out.name}", session,
    ]  # fmt: skip
    # check=False: k6 exits non-zero whenever a threshold is breached, which is the whole
    # point of the ramp. The summary JSON is the result, not the exit code.
    subprocess.run(cmd, text=True, capture_output=True, check=False)
    return json.loads(out.read_text())


def capacity(engine: str, session: str, base: str, quick: bool = False) -> int:
    """Validate at 3 users, then ramp until the engine misses its targets. Returns an exit code."""
    out = ROOT / "data" / "loadtest" / (
        datetime.now().astimezone().strftime("%Y%m%d-%H%M") + f"-{engine}"
    )
    out.mkdir(parents=True, exist_ok=True)
    print(f"run {out.name}", flush=True)

    val_dur, val_warm = ("60s", "15s") if quick else ("180s", "45s")
    step_dur, step_warm = ("30s", "10s") if quick else ("60s", "15s")

    print(f"=== {engine}: 3-user validation", flush=True)
    summ = run_k6(session, base, 3, val_dur, val_warm, out / "validate-3.json")
    validate = rm.evaluate(summ)
    print(f"  validate@3: pass={validate['pass']} err={validate['error_rate']} "
          f"worst={validate['worst_p95_ratio']}", flush=True)  # fmt: skip

    ramp, limit, broke = [], None, None
    for vus in rm.RAMP:
        s = run_k6(session, base, vus, step_dur, step_warm, out / f"ramp-{vus}.json")
        e = rm.evaluate(s) | {"vus": vus}
        ramp.append(e)
        print(f"  ramp@{vus}: pass={e['pass']} err={e['error_rate']} worst={e['worst_p95_ratio']} "
              f"rps={e['req_rate']}", flush=True)  # fmt: skip
        if e["pass"]:
            limit = vus
        if e["worst_p95_ratio"] and e["worst_p95_ratio"] > 5 or e["error_rate"] >= 0.05:
            broke = vus
            break

    doc = {"id": engine, "engine": engine, "base": base, "session": session,
           "limit_users": limit, "breaking_users": broke,
           "runs": {"validate": validate, "ramp": ramp}}  # fmt: skip
    (out / f"{engine}.json").write_text(json.dumps(doc, indent=1))
    print(f"\n{engine}: {limit} users within targets, broke at {broke}\ndone: {out}", flush=True)
    return 0


def main(engine: str, session: str, default_base: str) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=default_base)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    return capacity(engine, session, a.base, a.quick)


if __name__ == "__main__":
    sys.exit(main("photon", "session_photon.js", "http://127.0.0.1:2322"))
