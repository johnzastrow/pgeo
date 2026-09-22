#!/usr/bin/env python3
"""Regenerate regions/regions.json: the per-state facts every build step needs.

A pgeo build covers one or more US states. Everything that differs between states - the Geofabrik
extract, the bounding box, the Who's on First region id, the GNIS file, the OpenAddresses sources -
lives in regions/regions.json so that no script has to know about a particular state.

This generator fills that file from authoritative sources rather than from memory:

  * Census cartographic boundary file  STUSPS, NAME, STATEFP and the true geometry bbox
  * Who's on First admin SQLite        the region id used to select descendants
  * Geofabrik                          the extract slug, derived from the state name
  * OpenAddresses on GitHub            the list of sources published for the state

Values already present in regions.json are kept, never overwritten: the Maine entry records the
box and the source list the measured Maine build actually used, and a later run of this script
must not quietly move them. Delete an entry to have it regenerated.

Run it through prep's environment, which pins DuckDB:

  uv run --project prep python scripts/gen_regions.py                 # fill in anything missing
  uv run --project prep python scripts/gen_regions.py --states NY,VT  # only these
  uv run --project prep python scripts/gen_regions.py --check         # report drift, write nothing
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "regions" / "regions.json"
BOUNDARY = ROOT / "data" / "raw" / "shared" / "boundary" / "cb_2024_us_state_500k.zip"
WOF_DB = ROOT / "data" / "pelias" / "whosonfirst" / "sqlite" / "whosonfirst-data-admin-us-latest.db"
GEOFABRIK = "https://download.geofabrik.de/north-america/us"
OA_API = "https://api.github.com/repos/openaddresses/openaddresses/contents/sources/us"
GNIS = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/"
        "DomesticNames_{st}_Text.zip")

# The bbox stored for a state is its true extent padded outwards. The padding covers boundary
# generalisation in the cartographic file and points that sit just off the coast; it is a cheap
# prefilter, and the exact polygon test in prep/ is what actually decides membership.
PAD_DEG = 0.15


def census_states() -> dict[str, dict]:
    """STUSPS -> {name, fips, bbox} straight from the Census boundary shapefile, via DuckDB."""
    if not BOUNDARY.exists():
        sys.exit(f"missing {BOUNDARY}; run: scripts/fetch_data.sh boundary")
    shp = f"/vsizip/{BOUNDARY}/cb_2024_us_state_500k.shp"
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    rows = con.execute(
        "SELECT STUSPS, NAME, STATEFP, ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), "
        "ST_YMax(geom) FROM ST_Read(?)",
        [shp],
    ).fetchall()
    states = {}
    for stusps, name, fips, xmin, ymin, xmax, ymax in rows:
        states[stusps] = {
            "name": name,
            "fips": fips,
            "bbox": [
                round(math.floor((xmin - PAD_DEG) * 20) / 20, 2),
                round(math.floor((ymin - PAD_DEG) * 20) / 20, 2),
                round(math.ceil((xmax + PAD_DEG) * 20) / 20, 2),
                round(math.ceil((ymax + PAD_DEG) * 20) / 20, 2),
            ],
        }
    return states


def wof_ids() -> dict[str, int]:
    """State name -> Who's on First region id, from the admin distribution we already download."""
    if not WOF_DB.exists():
        sys.exit(f"missing {WOF_DB}; run: scripts/fetch_data.sh wof")
    con = sqlite3.connect(f"file:{WOF_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT name, id FROM spr WHERE placetype='region' AND is_current=1 AND country='US'"
    ).fetchall()
    con.close()
    return dict(rows)


def geofabrik_slug(name: str) -> str:
    """The Geofabrik extract slug for a state name.

    Derived, not probed. Geofabrik refuses HEAD and stalls ranged GETs from non-browser clients,
    so probing 50 states to learn what the download itself will establish is both unfriendly to
    their servers and a source of false negatives: two earlier versions of this script recorded
    "New York has no extract". scripts/fetch_data.sh fails loudly with the URL when a slug is
    wrong, and checks the published md5 when it is right.
    """
    return name.lower().replace(" ", "-")


def oa_sources(stusps: str) -> list[str] | None:
    """Every OpenAddresses source published for the state, as `us/<st>/<name>` paths."""
    req = urllib.request.Request(  # noqa: S310 - fixed https host
        f"{OA_API}/{stusps.lower()}", headers={"User-Agent": "pgeo-gen-regions"}
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:  # noqa: S310
            listing = json.load(r)
    except Exception as exc:
        print(f"  {stusps}: OpenAddresses listing unavailable ({exc})", file=sys.stderr)
        return None
    if isinstance(listing, dict):  # an error body, e.g. rate limiting
        print(f"  {stusps}: OpenAddresses listing unavailable ({listing.get('message')})",
              file=sys.stderr)
        return None
    names = sorted(e["name"][:-5] for e in listing if e["name"].endswith(".json"))
    return [f"us/{stusps.lower()}/{n}" for n in names]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--states", help="comma-separated STUSPS codes; default: all")
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"states": {}, "builds": {}}
    states, ids = census_states(), wof_ids()
    wanted = ([s.strip().upper() for s in args.states.split(",")] if args.states
              else sorted(states))

    # Existing values are authority: an entry records what a build actually used. Only fields that
    # are absent or null get filled, so re-running this completes gaps without moving pinned data.
    def gaps(stusps: str) -> list[str]:
        have = registry["states"].get(stusps, {})
        return [k for k in ("name", "fips", "bbox", "wof_id", "geofabrik", "gnis", "openaddresses")
                if have.get(k) is None]

    todo = {s: gaps(s) for s in wanted if gaps(s)}
    print(f"{len(wanted)} states requested, {len(todo)} with fields to fill")

    slugs = {s: geofabrik_slug(states[s]["name"]) for s in todo}
    for stusps, missing_fields in todo.items():
        facts = states[stusps]
        fresh = {
            "name": facts["name"],
            "fips": facts["fips"],
            "bbox": facts["bbox"],
            "wof_id": ids.get(facts["name"]),
            "geofabrik": slugs.get(stusps),
            "gnis": GNIS.format(st=stusps),
            "openaddresses": oa_sources(stusps) if "openaddresses" in missing_fields else None,
        }
        entry = registry["states"].setdefault(stusps, {})
        for key in missing_fields:
            entry[key] = fresh[key]
        still = [k for k in missing_fields if entry[k] is None]
        if still:
            print(f"  {stusps} {facts['name']}: no {', '.join(still)}")
        registry["states"][stusps] = {k: entry.get(k) for k in
                                      ("name", "fips", "bbox", "wof_id", "geofabrik", "gnis",
                                       "openaddresses")}

    registry["states"] = dict(sorted(registry["states"].items()))
    registry.setdefault("builds", {})
    registry["generated"] = datetime.now(UTC).strftime("%Y-%m-%d")
    if args.check:
        print("--check: nothing written")
        return 0
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"wrote {REGISTRY} ({len(registry['states'])} states)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
