"""Build the accuracy test set: queries with ground truth from the source data (seeded).

Output: tests/accuracy/cases.json for the default build, tests/accuracy/cases_<build>.json for
any other. A list of
  {"id", "endpoint": "search"|"autocomplete"|"reverse"|"structured", "qtype": "exact"|"typo"|"variant"|"miss",
   "kind": "address"|"town"|"lake_summit"|"venue"|"zip"|"reverse_address"|"miss",
   "params": {...Pelias query params...},
   "truth": {"lat", "lon", "radius_m", "housenumber"?, "locality"?} or null for misses}

Ground truth is independent of both engines: OpenAddresses points, WOF town label points,
GNIS feature points, Overture place points, Census ZCTA internal points.

Run from the repo root:
    uv run --project prep python tests/accuracy/build_cases.py                 # Maine, 1,560
    uv run --project prep python tests/accuracy/build_cases.py --build ny --n 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "load"))
sys.path.insert(0, str(ROOT / "prep" / "src"))
from build_corpus import OUT_OF_STATE, misses, typo, variant_address, variant_place  # noqa: E402
from pelias_prep.common import region  # noqa: E402

DATA = ROOT / "data"
HERE = Path(__file__).resolve().parent
RNG = random.Random(918)
# The Maine set, and the shape every other build is scaled to.
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
TOTAL = sum(N.values())
# How close a result must be to count as correct.
RADIUS_M = {"address": 150, "town": 8000, "lake_summit": 4000, "venue": 300, "zip": 12000, "reverse_address": 60}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", default="me", help="build name or state list (regions/regions.json)")
    ap.add_argument("--n", type=int, help=f"roughly this many cases in total (default {TOTAL})")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    reg = region(args.build)
    out_path = args.out or (HERE / ("cases.json" if reg.build == "me" else f"cases_{reg.build}.json"))
    scale = (args.n / TOTAL) if args.n else 1.0
    n = {k: max(5, round(v * scale)) for k, v in N.items()}

    registry = json.loads((ROOT / "regions" / "regions.json").read_text())["states"]
    # A single-state build can name its state in the query text the way a person would. A
    # multi-state build cannot, so it takes the state from the row (OpenAddresses records it)
    # and leaves it off where the row does not say.
    solo = reg.states[0] if len(reg.states) == 1 else None
    solo_name = registry[solo]["name"] if solo else ""

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL sqlite; LOAD sqlite; SET threads TO 1")
    con.execute("SELECT setseed(0.918)")
    rows = lambda sql, p=None: con.execute(sql, p or []).fetchall()  # noqa: E731
    cases: list[dict] = []

    def add(endpoint, qtype, kind, params, truth):
        cases.append(
            {"id": len(cases) + 1, "endpoint": endpoint, "qtype": qtype, "kind": kind, "params": params, "truth": truth}
        )

    # Every OpenAddresses file the build downloaded. Sources overlap (New York publishes a
    # statewide file and county files over the same ground), so rows are deduplicated on HASH.
    oa = str(DATA / "raw" / reg.build / "oa" / "**" / "*.csv")
    con.execute(
        "CREATE TABLE oa AS SELECT DISTINCT ON (HASH) NUMBER n, STREET s, CITY c, POSTCODE z, "
        "REGION st, CAST(LAT AS DOUBLE) lat, CAST(LON AS DOUBLE) lon "
        "FROM read_csv(?, all_varchar=true, union_by_name=true, delim=',', quote='\"', "
        "              escape='\"', header=true) "
        "WHERE NUMBER ~ '^[0-9]+$' AND CITY <> '' AND STREET <> '' AND coalesce(HASH, '') <> ''",
        [oa],
    )
    n_oa = rows("SELECT count(*) FROM oa")[0][0]
    state_of = lambda st: (solo or (st or "").strip().upper() or "")  # noqa: E731

    # Addresses: exact / typo / variant, truth = the OA point.
    for num, s, c, z, st, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [n["address"]]):
        code = state_of(st)
        text = f"{num} {s}, {c}, {code}".rstrip(", ")
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["address"], "housenumber": num, "locality": c}
        r = RNG.random()
        if r < 0.5:
            add("search", "exact", "address", {"text": text}, truth)
        elif r < 0.75:
            add("search", "typo", "address", {"text": typo(text)}, truth)
        else:
            name = registry[code]["name"] if code in registry else solo_name
            add("search", "variant", "address",
                {"text": variant_address(text, state_code=code or "", state_name=name)}, truth)

    # Structured addresses.
    for num, s, c, z, st, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [n["structured"]]):
        params = {"address": f"{num} {s}", "locality": c, "postalcode": z}
        if state_of(st):
            params["region"] = state_of(st)
        add("structured", "exact", "address", params,
            {"lat": lat, "lon": lon, "radius_m": RADIUS_M["address"], "housenumber": num, "locality": c})

    # Reverse at address points: the nearest result should be that address.
    for num, s, c, z, st, lat, lon in rows("SELECT * FROM oa ORDER BY random() LIMIT ?", [n["reverse"]]):
        add(
            "reverse",
            "exact",
            "reverse_address",
            {"point.lat": lat, "point.lon": lon, "layers": "address"},
            {"lat": lat, "lon": lon, "radius_m": RADIUS_M["reverse_address"], "housenumber": num},
        )

    # Towns (WOF localities with a label point), from the build's states.
    wof = str(DATA / "pelias" / "whosonfirst" / "sqlite" / "whosonfirst-data-admin-us-latest.db")
    # ATTACH does not take bind parameters; the path is our own constant (no quotes in it).
    assert "'" not in wof
    con.execute(f"ATTACH '{wof}' AS w (TYPE sqlite, READ_ONLY)")
    ids = ", ".join(str(int(registry[st]["wof_id"])) for st in reg.states)  # from the registry
    con.execute(
        f"CREATE TABLE towns AS SELECT s.name, s.latitude AS lat, s.longitude AS lon "  # noqa: S608
        f"FROM w.spr s JOIN w.ancestors a ON a.id = s.id WHERE a.ancestor_id IN ({ids}) "
        f"AND s.placetype = 'locality' AND s.is_current <> 0 AND s.is_deprecated = 0"
    )
    towns = rows("SELECT name, lat, lon FROM towns ORDER BY random() LIMIT ?", [n["town"]])
    for name, lat, lon in towns:
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["town"]}
        r = RNG.random()
        if r < 0.6 and solo_name:
            add("search", "exact", "town", {"text": f"{name}, {solo_name}"}, truth)
        elif r < 0.8:
            add("search", "typo", "town", {"text": typo(name)}, truth)
        else:
            add("search", "variant", "town", {"text": name.lower()}, truth)

    # Names that are not unique get the nearest town added, the way a person would say it
    # ("Mud Pond, Beaver Cove"): Maine has dozens of Mud Ponds and a bare name has no one
    # right answer.
    def qualify(name: str, lat: float, lon: float, dup: int) -> str:
        if dup <= 1:
            return name
        near = rows(
            "SELECT name FROM towns ORDER BY (lat - ?) * (lat - ?) + ((lon - ?) * 0.72) * ((lon - ?) * 0.72) LIMIT 1",
            [lat, lat, lon, lon],
        )
        return f"{name}, {near[0][0]}" if near else name

    # Lakes and summits (GNIS).
    gnis = str(DATA / "processed" / reg.build / "csv" / "gnis.csv")
    for name, lat, lon, dup in rows(
        "WITH g AS (SELECT name, lat, lon, category, count(*) OVER (PARTITION BY lower(name)) AS dup "
        "FROM read_csv(?)) SELECT name, lat, lon, dup FROM g WHERE category IN ('lake', 'summit') "
        "ORDER BY random() LIMIT ?",
        [gnis, n["lake_summit"]],
    ):
        name = qualify(name, lat, lon, dup)
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["lake_summit"]}
        r = RNG.random()
        if r < 0.55:
            add("search", "exact", "lake_summit", {"text": name}, truth)
        elif r < 0.8:
            add("search", "typo", "lake_summit", {"text": typo(name)}, truth)
        else:
            add("search", "variant", "lake_summit", {"text": variant_place(name)}, truth)

    # Venues (Overture, confident ones) with their town.
    ov = str(DATA / "processed" / reg.build / "csv" / "overture.csv")
    venues_sql = (
        "WITH o AS (SELECT name, lat, lon, addendum_json_overture AS a, "
        "count(*) OVER (PARTITION BY lower(name)) AS dup FROM read_csv(?)) "
        "SELECT name, lat, lon, dup FROM o WHERE TRY_CAST(json_extract_string(a, '$.confidence') AS DOUBLE) >= 0.9 "
        "ORDER BY random() LIMIT ?"
    )
    for name, lat, lon, dup in rows(venues_sql, [ov, n["venue"]]):
        name = qualify(name, lat, lon, dup)
        truth = {"lat": lat, "lon": lon, "radius_m": RADIUS_M["venue"]}
        r = RNG.random()
        add("search", "exact" if r < 0.7 else "typo", "venue", {"text": name if r < 0.7 else typo(name)}, truth)

    # ZIP codes.
    zc = str(DATA / "processed" / reg.build / "csv" / "zcta.csv")
    for z, lat, lon in rows(
        "SELECT postcode, lat, lon FROM read_csv(?, all_varchar=true) ORDER BY random() LIMIT ?", [zc, n["zip"]]
    ):
        add("search", "exact", "zip", {"text": z}, {"lat": float(lat), "lon": float(lon), "radius_m": RADIUS_M["zip"]})

    # Autocomplete: a prefix (60-80% of the text) of towns and venues; hit if in the top 5.
    ac_venues = [(v[0], v[1], v[2], "venue") for v in rows(venues_sql, [ov, n["autocomplete"] // 2])]
    for name, lat, lon, kind in [(t[0], t[1], t[2], "town") for t in towns[: n["autocomplete"] // 2]] + ac_venues:
        cut = max(3, int(len(name) * RNG.uniform(0.6, 0.8)))
        add("autocomplete", "exact", kind, {"text": name[:cut]}, {"lat": lat, "lon": lon, "radius_m": RADIUS_M[kind]})

    # Misses: must return nothing, or nothing confident (confidence < 0.8). The prepared Maine
    # corpus has a hand-checked list; any other build synthesises them from its own streets,
    # with places outside the build to stand in for "somewhere else".
    corpus_file = ROOT / "tests" / "load" / "corpus" / f"{reg.build}_corpus.json"
    legacy = ROOT / "tests" / "load" / "corpus" / "maine_corpus.json"
    if reg.build == "me" and not corpus_file.exists():
        corpus_file = legacy
    if corpus_file.exists():
        pool = json.loads(corpus_file.read_text())["misses"]
    else:
        streets = [r[0] for r in rows("SELECT DISTINCT s FROM oa ORDER BY random() LIMIT 400")]
        # "Somewhere else" has to actually be somewhere else. Drop any stand-in that names one
        # of the build's states, or whose place name is a town inside it - "Niagara Falls,
        # Ontario" is not a miss on a New York build.
        own = {registry[st]["name"].upper() for st in reg.states} | set(reg.states)
        own_towns = {r[0].upper() for r in rows("SELECT name FROM towns")}
        elsewhere = [
            q for q in OUT_OF_STATE
            if not any(t in q.upper() for t in own) and q.split(",")[0].upper() not in own_towns
        ]
        pool = misses(streets, n=n["miss"] * 3, out_of_state=elsewhere)
    add_misses = RNG.sample(pool, min(n["miss"], len(pool)))
    for text in add_misses:
        add("search", "miss", "miss", {"text": text}, None)

    out_path.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    by: dict[str, int] = {}
    for c in cases:
        by[f"{c['endpoint']}/{c['qtype']}"] = by.get(f"{c['endpoint']}/{c['qtype']}", 0) + 1
    print(f"build {reg.build} ({', '.join(reg.states)}): {n_oa:,} usable OpenAddresses rows")
    print(len(cases), "cases", by, "->", out_path)


if __name__ == "__main__":
    main()
