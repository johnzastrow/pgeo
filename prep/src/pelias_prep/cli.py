"""pelias-prep: build Pelias CSV-importer files from raw downloads in data/raw/.

Usage:
    uv run pelias-prep all --build ny
    uv run pelias-prep gnis|zcta|overture --build me,nh,vt
    uv run pelias-prep oa-interp --build me   # Pelias path only; see oa_interp.py

A build covers one or more states (regions/regions.json). Inputs come from
data/raw/<build>/ and data/raw/shared/, outputs go to data/processed/<build>/csv/.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import gnis, oa_interp, overture, zcta
from .common import (
    connect,
    load_region_polygon,
    load_region_zctas,
    region,
    sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _extract_member(zip_path: Path, suffix: str, dest: Path) -> Path:
    """Extract the single member ending in `suffix` from `zip_path` into `dest`."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.endswith(suffix)]
        if len(members) != 1:
            raise ValueError(f"expected one *{suffix} in {zip_path}, found {members}")
        # Write under our own name only; never trust archive paths (zip-slip).
        target = dest / Path(members[0]).name
        dest.mkdir(parents=True, exist_ok=True)
        with zf.open(members[0]) as src, target.open("wb") as out:
            out.write(src.read())
        return target


def _single(pattern_dir: Path, pattern: str) -> Path:
    matches = sorted(pattern_dir.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one {pattern} in {pattern_dir}, found {matches}")
    return matches[0]


def run_oa_interp(args: argparse.Namespace) -> int:
    reg = region(args.build)
    oa_dir = args.data_dir / "pelias" / "openaddresses"
    out_dir = args.data_dir / "pelias" / "interpolation_oa"
    for r in oa_interp.convert_tree(connect(), oa_dir, out_dir, reg):
        note = f" ({r.outside_bbox:,d} outside the {reg.build} bbox)" if r.outside_bbox else ""
        print(f"oa-interp {r.rows:>8,d} rows -> {r.path}{note}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.target == "oa-interp":
        return run_oa_interp(args)
    reg = region(args.build)
    raw = args.data_dir / "raw" / reg.build
    shared = args.data_dir / "raw" / "shared"
    work = args.data_dir / "work" / reg.build
    out_dir = args.data_dir / "processed" / reg.build / "csv"
    targets = ["gnis", "zcta", "overture"] if args.target == "all" else [args.target]

    con = connect()
    # Both the ZCTA export and the Overture clip need to know which ZCTAs are in the region,
    # and that needs the polygon, so build them once up front.
    gaz_zip = _single(shared / "zcta", "*_Gaz_zcta_national.zip")
    load_region_polygon(con, shared / "boundary" / "cb_2024_us_state_500k.zip", reg)
    n_zcta = load_region_zctas(
        con,
        _extract_member(gaz_zip, "_Gaz_zcta_national.txt", work / "zcta"),
        shared / "zcta" / "tab20_zcta520_county20_natl.txt",
        reg,
    )
    print(f"build {reg.build}: {', '.join(reg.states)}; {n_zcta:,d} ZCTAs in region")
    manifest: dict = {"generated_at": datetime.now(UTC).isoformat(), "outputs": {}}

    for t in targets:
        if t == "gnis":
            sources = []
            for st in reg.states:
                src_zip = raw / "gnis" / f"DomesticNames_{st}_Text.zip"
                sources.append((st, _extract_member(src_zip, f"DomesticNames_{st}.txt",
                                                    work / "gnis")))
            res = gnis.convert(con, sources, out_dir / "gnis.csv", reg, args.include_historical)
        elif t == "zcta":
            src_zip = gaz_zip
            res = zcta.convert(con, out_dir / "zcta.csv", reg)
        else:
            # The Overture input is the parquet extract itself (no zip).
            src_zip = _single(raw / "overture", "places_*_bbox.parquet")
            res = overture.convert(con, src_zip, out_dir / "overture.csv", reg,
                                   args.min_confidence)
        manifest["outputs"][t] = {
            "file": res.path.name,
            "rows": res.rows,
            "input": str(src_zip.relative_to(args.data_dir)),
            "input_sha256": sha256(src_zip),
            "output_sha256": sha256(res.path),
        }
        print(f"{t:9s} {res.rows:>8,d} rows -> {res.path}")

    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        # Merge with previous runs so single-target runs keep other entries.
        previous = json.loads(manifest_path.read_text())
        previous.get("outputs", {}).update(manifest["outputs"])
        manifest["outputs"] = previous["outputs"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pelias-prep", description=__doc__.splitlines()[0])
    p.add_argument("target", choices=["all", "gnis", "zcta", "overture", "oa-interp"])
    p.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    p.add_argument("--build", default="me",
                   help="build name in regions/regions.json, or a comma-separated state list")
    p.add_argument("--include-historical", action="store_true", help="keep GNIS (historical)")
    p.add_argument("--min-confidence", type=float, default=overture.DEFAULT_MIN_CONFIDENCE)
    args = p.parse_args(argv)
    try:
        return run(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
