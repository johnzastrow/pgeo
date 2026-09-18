"""Build the accuracy test set: queries with ground truth from the source data (seeded).

Output: tests/accuracy/cases.json, a list of
  {"id", "endpoint": "search"|"autocomplete"|"reverse"|"structured", "qtype": "exact"|"typo"|"variant"|"miss",
   "kind": "address"|"town"|"lake_summit"|"venue"|"zip"|"reverse_address"|"miss",
   "params": {...Pelias query params...},
   "truth": {"lat", "lon", "radius_m", "housenumber"?, "locality"?} or null for misses}

Ground truth is independent of both engines: OpenAddresses points, WOF town label points,
GNIS feature points, Overture place points, Census ZCTA internal points.

Run from the repo root:  uv run --project prep python tests/accuracy/build_cases.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "load"))
from build_corpus import typo, variant_address, variant_place  # noqa: E402

DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "cases.json"
RNG = random.Random(918)
N = {
    "address": 400,
    "town": 150,
    "lake_summit": 150,
    "venue": 150,
    "zip": 60,
    "reverse": 200,
    "structured": 150,
    "autocomplete": 150,
    "miss": 150,
}
# How close a result must be to count as correct.
RADIUS_M = {"address": 150, "town": 8000, "lake_summit": 4000, "venue": 300, "zip": 12000, "reverse_address": 60}


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL sqlite; LOAD sqlite; SET threads TO 1")
    con.execute("SELECT setseed(0.918)")
    rows = lambda sql, p=None: con.execute(sql, p or []).fetchall()  # noqa: E731
    cases: list[dict] = []

    def add(endpoint, qtype, kind, params, truth):
        cases.append(
            {"id": len(cases) + 1, "endpoint": endpoint, "qtype": qtype, "kind": kind, "params": params, "truth": truth}
        )

    oa = str(DATA / "pelias" / "interpolation_oa" / "us" / "me" / "statewide.csv")
    con.execute(
        "CREATE TABLE oa AS SELECT NUMBER n, STREET s, CITY c, POSTCODE z, CAST(LAT AS DOUBLE) lat, "
        "CAST(LON AS DOUBLE) lon FROM read_csv(?, all_varchar=true) "
        "WHERE NUMBER ~ '^[0-9]+$' AND CITY <> '' AND STREET <> ''",
        [oa],
    )

    # Addresses: exact / typo / variant, truth = the OA point.
    for n, s, c, z, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [N["address"]]):
        text = f"{n} {s}, {c}, ME"
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["address"], "housenumber": n, "locality": c}
        r = RNG.random()
        if r < 0.5:
            add("search", "exact", "address", {"text": text}, truth)
        elif r < 0.75:
            add("search", "typo", "address", {"text": typo(text)}, truth)
        else:
            add("search", "variant", "address", {"text": variant_address(text)}, truth)

    # Structured addresses.
    for n, s, c, z, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [N["structured"]]):
        add(
            "structured",
            "exact",
            "address",
            {"address": f"{n} {s}", "locality": c, "postalcode": z, "region": "ME"},
            {"lat": lat, "lon": lon, "radius_m": RADIUS_M["address"], "housenumber": n, "locality": c},
        )

    # Reverse at address points: the nearest result should be that address.
    for n, s, c, z, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [N["reverse"]]):
        add(
            "reverse",
            "exact",
            "reverse_address",
            {"point.lat": lat, "point.lon": lon, "layers": "address"},
            {"lat": lat, "lon": lon, "radius_m": RADIUS_M["reverse_address"], "housenumber": n},
        )

    # Towns (WOF localities with a label point).
    wof = str(DATA / "pelias" / "whosonfirst" / "sqlite" / "whosonfirst-data-admin-us-latest.db")
    # ATTACH does not take bind parameters; the path is our own constant (no quotes in it).
    assert "'" not in wof
    con.execute(f"ATTACH '{wof}' AS w (TYPE sqlite, READ_ONLY)")
    towns = rows(
        "SELECT s.name, s.latitude, s.longitude FROM w.spr s JOIN w.ancestors a ON a.id = s.id "
        "WHERE a.ancestor_id = 85688769 AND s.placetype = 'locality' AND s.is_current <> 0 "
        "AND s.is_deprecated = 0 ORDER BY random() LIMIT ?",
        [N["town"]],
    )
    for name, lat, lon in towns:
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["town"]}
        r = RNG.random()
        text = f"{name}, Maine" if r < 0.6 else (typo(name) if r < 0.8 else name.lower())
        add("search", "exact" if r < 0.6 else ("typo" if r < 0.8 else "variant"), "town", {"text": text}, truth)

    # Lakes and summits (GNIS).
    gnis = str(DATA / "processed" / "csv" / "gnis.csv")
    for name, lat, lon in rows(
        "SELECT name, lat, lon FROM read_csv(?) WHERE category IN ('lake', 'summit') ORDER BY random() LIMIT ?",
        [gnis, N["lake_summit"]],
    ):
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["lake_summit"]}
        r = RNG.random()
        if r < 0.55:
            add("search", "exact", "lake_summit", {"text": name}, truth)
        elif r < 0.8:
            add("search", "typo", "lake_summit", {"text": typo(name)}, truth)
        else:
            add("search", "variant", "lake_summit", {"text": variant_place(name)}, truth)

    # Venues (Overture, confident ones) with their town.
    ov = str(DATA / "processed" / "csv" / "overture.csv")
    for name, lat, lon in rows(
        "SELECT name, lat, lon FROM read_csv(?) WHERE TRY_CAST(json_extract_string(addendum_json_overture, "
        "'$.confidence') AS DOUBLE) >= 0.9 ORDER BY random() LIMIT ?",
        [ov, N["venue"]],
    ):
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["venue"]}
        r = RNG.random()
        add("search", "exact" if r < 0.7 else "typo", "venue", {"text": name if r < 0.7 else typo(name)}, truth)

    # ZIP codes.
    zc = str(DATA / "processed" / "csv" / "zcta.csv")
    for z, lat, lon in rows(
        "SELECT postcode, lat, lon FROM read_csv(?, all_varchar=true) ORDER BY random() LIMIT ?", [zc, N["zip"]]
    ):
        add("search", "exact", "zip", {"text": z}, {"lat": float(lat), "lon": float(lon), "radius_m": RADIUS_M["zip"]})

    # Autocomplete: a prefix (60-80% of the text) of towns and venues; hit if in the top 5.
    for name, lat, lon, kind in [(t[0], t[1], t[2], "town") for t in towns[: N["autocomplete"] // 2]] + [
        (v[0], v[1], v[2], "venue")
        for v in rows(
            "SELECT name, lat, lon FROM read_csv(?) WHERE TRY_CAST(json_extract_string(addendum_json_overture, "
            "'$.confidence') AS DOUBLE) >= 0.9 ORDER BY random() LIMIT ?",
            [ov, N["autocomplete"] // 2],
        )
    ]:
        cut = max(3, int(len(name) * RNG.uniform(0.6, 0.8)))
        add("autocomplete", "exact", kind, {"text": name[:cut]}, {"lat": lat, "lon": lon, "radius_m": RADIUS_M[kind]})

    # Misses: must return nothing, or nothing confident (confidence < 0.8).
    corpus = json.loads((ROOT / "tests" / "load" / "corpus" / "maine_corpus.json").read_text())
    for text in RNG.sample(corpus["misses"], N["miss"]):
        add("search", "miss", "miss", {"text": text}, None)

    OUT.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    by = {}
    for c in cases:
        by[f"{c['endpoint']}/{c['qtype']}"] = by.get(f"{c['endpoint']}/{c['qtype']}", 0) + 1
    print(len(cases), "cases", by, "->", OUT)


if __name__ == "__main__":
    main()
