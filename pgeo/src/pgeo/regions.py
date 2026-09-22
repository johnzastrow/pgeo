"""Which states a build covers, and where its inputs live.

The facts come from regions/regions.json at the repository root, the same file the download
scripts and the prep package read (scripts/gen_regions.py writes it). Nothing in the loader
names a state.

A build is either a name from the registry's `builds` table (`me`, `ny`) or a bare
comma-separated list of state codes (`me,nh,vt`), in which case the build is named after the
codes. `PGEO_BUILD` in the environment sets the default.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pgeo.settings import DATA_DIR, REPO_ROOT

REGISTRY = REPO_ROOT / "regions" / "regions.json"


@dataclass(frozen=True)
class Region:
    build: str
    states: tuple[str, ...]
    wof_ids: tuple[int, ...]
    bbox: tuple[float, float, float, float]

    @property
    def raw_dir(self) -> Path:
        return DATA_DIR / "raw" / self.build

    @property
    def processed_dir(self) -> Path:
        return DATA_DIR / "processed" / self.build / "csv"

    @property
    def oa_glob(self) -> str:
        """Every OpenAddresses CSV downloaded for this build, at any depth."""
        return str(self.raw_dir / "oa" / "**" / "*.csv")

    def ref_rows(self) -> list[tuple[int, str, str, str]]:
        """(wof_id, name, abbr, fips) per member state, for geocode.region_ref."""
        reg = _registry()["states"]
        return [(int(reg[st]["wof_id"]), reg[st]["name"], st, reg[st]["fips"])
                for st in self.states]

    def pbfs(self) -> list[Path]:
        """The OSM extracts for this build, in state order. Missing files are an error."""
        reg = _registry()["states"]
        out = []
        for st in self.states:
            slug = reg[st]["geofabrik"]
            pbf = self.raw_dir / "osm" / f"{slug}-latest.osm.pbf"
            if not pbf.exists():
                raise FileNotFoundError(
                    f"missing {pbf}; run: scripts/fetch_data.sh --build {self.build} osm"
                )
            out.append(pbf)
        return out


def _registry() -> dict:
    if not REGISTRY.exists():
        raise FileNotFoundError(f"missing {REGISTRY}; run scripts/gen_regions.py")
    return json.loads(REGISTRY.read_text())


def stack_env(build: str) -> dict[str, str]:
    """The per-build stack settings scripts/pgeo_setup.sh generated, if this build has any.

    A build other than the default runs its own containers on its own ports with its own
    database, so every pgeo-load command has to be told which one it means. Reading the file the
    setup script already wrote keeps `--build ny` sufficient on its own.
    """
    path = REPO_ROOT / "pgeo" / "builds" / f"{build}.env"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def region(build: str | None = None) -> Region:
    """Resolve a build name or state list. Defaults to $PGEO_BUILD, else `me`."""
    build = build or os.environ.get("PGEO_BUILD") or "me"
    reg = _registry()
    states = reg.get("builds", {}).get(build, {}).get("states")
    if states is None:
        # Either separator: "me,nh,vt" as a person types it, or the dashed form the
        # scripts pass around because it also names directories and containers.
        states = [s.strip().upper() for s in re.split(r"[,-]", build) if s.strip()]
        build = "-".join(s.lower() for s in states)  # the build names a directory
    unknown = [s for s in states if s not in reg["states"]]
    if not states or unknown:
        raise ValueError(f"unknown state or build: {build} ({', '.join(unknown) or 'no states'})")
    missing_wof = [s for s in states if reg["states"][s].get("wof_id") is None]
    if missing_wof:
        raise ValueError(f"no Who's on First region id for {', '.join(missing_wof)}")
    boxes = [reg["states"][s]["bbox"] for s in states]
    return Region(
        build=build,
        states=tuple(states),
        wof_ids=tuple(int(reg["states"][s]["wof_id"]) for s in states),
        bbox=(min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes)),
    )
