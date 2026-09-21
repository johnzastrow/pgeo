"""pgeo capacity against data volume: the subset comparison already done for Pelias.

    python3 tests/load/run_datavol_pgeo.py [--configs P2] [--datasets D1,D2,D3,D4,D5]

For each subset the database is rebuilt from those sources only, then the standard ramp runs at a
fixed configuration, so the only thing changing is how much data is loaded. The subsets are the
same as the Pelias ones (tests/load/run_matrix.py DATASETS), which makes the two curves
comparable.

Runs D1 to D5 in that order on purpose: D5 is the full set, so the database is left complete.

Results: data/loadtest/<run>-pgeo/ per dataset, plus datavol-pgeo.json with the summary.
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

ROOT = Path(__file__).resolve().parents[2]
DATASETS = {k: (v if v is not None else rm.DATASETS["D4"] + ["overture"]) for k, v in rm.DATASETS.items()}


def build(sources: list[str]) -> bool:
    """Rebuild the pgeo database from these sources only."""
    print(f"  building from {', '.join(sources)}", flush=True)
    r = subprocess.run(  # noqa: S603
        ["uv", "run", "--project", "pgeo", "pgeo-load", "build", "--sources", ",".join(sources)],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True)  # fmt: skip
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:], file=sys.stderr)
    return r.returncode == 0


def features() -> int | None:
    r = subprocess.run(  # noqa: S603
        ["docker", "exec", "pgeo_db", "psql", "-U", "pgeo", "-d", "pgeo", "-Atc",  # noqa: S607
         "SELECT count(*) FROM pgeo.feature"], capture_output=True, text=True)  # fmt: skip
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="P2", help="pgeo configuration to hold fixed")
    ap.add_argument("--datasets", default="D1,D2,D3,D4,D5")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()

    out = ROOT / "data" / "loadtest" / (datetime.now().astimezone().strftime("%Y%m%d-%H%M") + "-pgeo-datavol")
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    print(f"run {out.name}", flush=True)

    for name in [d for d in a.datasets.split(",") if d]:
        sources = DATASETS.get(name)
        if not sources:
            print(f"  unknown dataset {name}", file=sys.stderr)
            continue
        print(f"\n=== {name}: {', '.join(sources)}", flush=True)
        if not build(sources):
            print(f"  {name}: build failed, skipping", flush=True)
            continue
        n = features()
        print(f"  {n:,} features" if n else "  feature count unavailable", flush=True)
        for cfg in [c for c in a.configs.split(",") if c]:
            doc = rp.run_config("rest", cfg, rp.CONFIGS[cfg], out, a.quick, f"dv-{name}")
            summary.append({"dataset": name, "sources": sources, "features": n, "config": cfg,
                            "limit_users": doc.get("limit_users"),
                            "breaking_users": doc.get("breaking_users")})  # fmt: skip
            print(f"  {name} {cfg}: {doc.get('limit_users')} users within targets", flush=True)
            (out / "datavol-pgeo.json").write_text(json.dumps(summary, indent=1))

    print(f"\ndone: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
