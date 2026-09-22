#!/usr/bin/env python3
"""Write the /region.json the demo page reads: what region this deployment covers.

The page uses it for the map extent, the basemap file, its own title and the feature count, so
one page serves any build. See web/js/map.js.

    scripts/gen_region_json.py --build ny            # to stdout
    scripts/gen_region_json.py --build ny -o web/region.json

The feature count is read from the build's database when it is running, and left out otherwise -
the page simply omits it. The Ansible edge role writes the same file on a server from its own
template, so this script is for local previews.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "regions" / "regions.json"


def states_of(registry: dict, build: str) -> tuple[str, list[str]]:
    """Return (build name, member state codes) for a build name or bare state list."""
    states = registry.get("builds", {}).get(build, {}).get("states")
    if states is None:
        states = [s.strip().upper() for s in build.split(",") if s.strip()]
        build = "-".join(s.lower() for s in states)
    unknown = [s for s in states if s not in registry["states"]]
    if not states or unknown:
        sys.exit(f"unknown state or build: {build} ({', '.join(unknown) or 'no states'})")
    return build, states


def feature_count(build: str) -> int:
    """Ask the build's database how many features it holds; 0 when it is not reachable."""
    container = "pgeo_db" if build == "me" else f"pgeo-{build}_db"
    # Same rule as scripts/pgeo_setup.sh: a database name is an SQL identifier, so "vt-nh"
    # becomes "vt_nh".
    safe = re.sub(r"[^a-z0-9]", "_", build)
    database = "pgeo" if build == "me" else f"pgeo_{safe}"
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["docker", "exec", container, "psql", "-U", "pgeo", "-d", database,  # noqa: S607
             "-Atc", "SELECT count(*) FROM pgeo.feature"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return int(out.stdout.strip())
    except Exception:
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", default="me")
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args(argv)

    registry = json.loads(REGISTRY.read_text())
    build, states = states_of(registry, args.build)
    boxes = [registry["states"][s]["bbox"] for s in states]
    names = [registry["states"][s]["name"] for s in states]
    doc = {
        "build": build,
        "name": ", ".join(names),
        "states": states,
        # [[west, south], [east, north]], the order MapLibre wants.
        "bounds": [[min(b[0] for b in boxes), min(b[1] for b in boxes)],
                   [max(b[2] for b in boxes), max(b[3] for b in boxes)]],
        "tiles": f"/tiles/{build}.pmtiles",
    }
    n = feature_count(build)
    if n:
        doc["features"] = n
    text = json.dumps(doc, indent=2) + "\n"
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
