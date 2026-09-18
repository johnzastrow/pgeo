"""pelias-prep: build Pelias CSV-importer files from raw downloads in data/raw/.

Usage:
    uv run pelias-prep all [--data-dir ../data]
    uv run pelias-prep gnis|zcta|overture [...]
    uv run pelias-prep oa-interp   # after `pelias download oa`; see oa_interp.py
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import gnis, oa_interp, overture, zcta
from .common import connect, load_maine_polygon, sha256

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
    oa_dir = args.data_dir / "pelias" / "openaddresses"
    out_dir = args.data_dir / "pelias" / "interpolation_oa"
    for r in oa_interp.convert_tree(connect(), oa_dir, out_dir):
        note = f" ({r.outside_bbox:,d} outside Maine bbox)" if r.outside_bbox else ""
        print(f"oa-interp {r.rows:>8,d} rows -> {r.path}{note}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.target == "oa-interp":
        return run_oa_interp(args)
    raw = args.data_dir / "raw"
    work = args.data_dir / "work"
    out_dir = args.data_dir / "processed" / "csv"
    targets = ["gnis", "zcta", "overture"] if args.target == "all" else [args.target]

    con = connect()
    manifest: dict = {"generated_at": datetime.now(UTC).isoformat(), "outputs": {}}

    for t in targets:
        if t == "gnis":
            src_zip = raw / "gnis" / "DomesticNames_ME_Text.zip"
            src = _extract_member(src_zip, "DomesticNames_ME.txt", work / "gnis")
            res = gnis.convert(con, src, out_dir / "gnis.csv", args.include_historical)
        elif t == "zcta":
            src_zip = _single(raw / "zcta", "*_Gaz_zcta_national.zip")
            src = _extract_member(src_zip, "_Gaz_zcta_national.txt", work / "zcta")
            res = zcta.convert(con, src, out_dir / "zcta.csv")
        else:
            load_maine_polygon(con, raw / "boundary" / "cb_2024_us_state_500k.zip")
            # The Overture input is the parquet extract itself (no zip).
            src_zip = _single(raw / "overture", "places_*_me_bbox.parquet")
            res = overture.convert(con, src_zip, out_dir / "overture.csv", args.min_confidence)
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
