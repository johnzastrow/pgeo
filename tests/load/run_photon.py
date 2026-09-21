"""Photon capacity, measured the same way as Pelias and pgeo.

    python3 tests/load/run_photon.py [--base http://127.0.0.1:2322] [--quick]

Photon is the type-ahead specialist, and autocomplete is the endpoint that limits pgeo on every
configuration, so this is the comparison that presses hardest on pgeo's weakest axis (report
Section 1.5.1). Same corpus, same session mix, same keystroke pattern, same think times, same
ramp and the same latency targets as the other engines; only the URL shape differs
(tests/load/k6/session_photon.js), because Photon does not speak the Pelias API.

Results: data/loadtest/<run>-photon/photon.json, in the same shape as the other runners.
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


def run_k6(base: str, vus: int, duration: str, warmup: str, out: Path) -> dict:
    """As run_matrix.run_k6, but the Photon session script and base URL."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm", "--network", "host", "--cpuset-cpus", rm.K6_CPUS,
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{rm.K6_DIR}:/work:ro", "-v", f"{out.parent}:/out",
        "-w", "/work/k6",
        "-e", f"VUS={vus}", "-e", f"DURATION={duration}", "-e", f"WARMUP={warmup}",
        "-e", f"BASE_URL={base}",
        rm.K6_IMAGE, "run", "--quiet", "--no-color",
        "--summary-export", f"/out/{out.name}", "session_photon.js",
    ]  # fmt: skip
    subprocess.run(cmd, text=True, capture_output=True)  # noqa: S603
    return json.loads(out.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:2322")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--label", default="photon")
    a = ap.parse_args()

    out = ROOT / "data" / "loadtest" / (datetime.now().astimezone().strftime("%Y%m%d-%H%M") + "-photon")
    out.mkdir(parents=True, exist_ok=True)
    print(f"run {out.name}", flush=True)

    val_dur, val_warm = ("60s", "15s") if a.quick else ("180s", "45s")
    step_dur, step_warm = ("30s", "10s") if a.quick else ("60s", "15s")

    print("=== photon: 3-user validation", flush=True)
    summ = run_k6(a.base, 3, val_dur, val_warm, out / "validate-3.json")
    validate = rm.evaluate(summ)
    print(f"  validate@3: pass={validate['pass']} err={validate['error_rate']} "
          f"worst={validate['worst_p95_ratio']}", flush=True)  # fmt: skip

    ramp, limit, broke = [], None, None
    for vus in rm.RAMP:
        s = run_k6(a.base, vus, step_dur, step_warm, out / f"ramp-{vus}.json")
        e = rm.evaluate(s) | {"vus": vus}
        ramp.append(e)
        print(f"  ramp@{vus}: pass={e['pass']} err={e['error_rate']} worst={e['worst_p95_ratio']} "
              f"rps={e['req_rate']}", flush=True)  # fmt: skip
        if e["pass"]:
            limit = vus
        if e["worst_p95_ratio"] and e["worst_p95_ratio"] > 5 or e["error_rate"] >= 0.05:
            broke = vus
            break

    doc = {"id": "photon", "engine": "photon", "base": a.base,
           "limit_users": limit, "breaking_users": broke,
           "runs": {"validate": validate, "ramp": ramp}}  # fmt: skip
    (out / "photon.json").write_text(json.dumps(doc, indent=1))
    print(f"\nphoton: {limit} users within targets, broke at {broke}\ndone: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
