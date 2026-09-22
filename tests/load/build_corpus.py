"""Build the load-test query corpus from the loaded Maine data (deterministic, seeded).

Output: tests/load/corpus/maine_corpus.json with lists of
  addresses   "389 Congress St, Portland, ME"          (OpenAddresses)
  places      lake / summit / island / township names  (GNIS)
  venues      "<venue name>"                           (Overture places)
  zips        "04101"                                  (ZCTA)
  structured  {address, locality, postalcode, region}  (OpenAddresses)
  points      [lat, lon] for reverse                   (OA points + uniform in Maine polygon)
  foci        [lat, lon] map centers for focus.point   (town centers)
  typos       1-2 character errors in real names/addresses ("Mosehead Lake")
  variants    off names: St<->Street, N<->North, missing town/state, unit added, Mt<->Mount,
              word order, partial venue names
  misses      things that are not in Maine: impossible house numbers on real streets,
              invented names, out-of-state places, gibberish
  miss_points [lat, lon] offshore in the Gulf of Maine (reverse with nothing nearby)

Run from the repo root:  uv run --project prep python tests/load/build_corpus.py
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "corpus" / "maine_corpus.json"
SEED = 0.42  # duckdb setseed() takes a value in [-1, 1]
# Sized well above the number of queries a run sends per category, to limit cache hits
# from exact repeats (see docs/LOAD_TEST_PLAN.md, caching).
N = {
    "addresses": 3000,
    "places": 800,
    "venues": 1000,
    "zips": 200,
    "structured": 1000,
    "oa_points": 1000,
    "random_points": 1000,
    "foci": 120,
    "typos": 1200,
    "variants": 1200,
    "misses": 600,
    "miss_points": 300,
}
RNG = random.Random(20260918)

# QWERTY neighbours for realistic wrong-key typos.
_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
KEY_NEIGHBOURS = {
    c: "".join(
        _ROWS[r2][c2]
        for r2 in (r - 1, r, r + 1)
        for c2 in (i - 1, i, i + 1)
        if 0 <= r2 < 3 and 0 <= c2 < len(_ROWS[r2]) and (r2, c2) != (r, i)
    )
    for r, row in enumerate(_ROWS)
    for i, c in enumerate(row)
}
SUFFIXES = {
    "St": "Street",
    "Rd": "Road",
    "Ave": "Avenue",
    "Dr": "Drive",
    "Ln": "Lane",
    "Ct": "Court",
    "Hwy": "Highway",
    "Cir": "Circle",
    "Pl": "Place",
    "Ter": "Terrace",
}
DIRECTIONS = {"N": "North", "S": "South", "E": "East", "W": "West"}


def typo(text: str) -> str:
    """Apply one or two character errors inside alphabetic words (numbers stay intact)."""
    chars = list(text)
    for _ in range(RNG.choice((1, 1, 2))):
        idx = [
            i
            for i, ch in enumerate(chars)
            if ch.isalpha() and i > 0 and chars[i - 1].isalpha()
        ]
        if not idx:
            break
        i = RNG.choice(idx)
        op = RNG.choice(("swap", "drop", "double", "neighbour"))
        if op == "swap" and i + 1 < len(chars) and chars[i + 1].isalpha():
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        elif op == "drop":
            del chars[i]
        elif op == "double":
            chars.insert(i, chars[i])
        else:
            near = KEY_NEIGHBOURS.get(chars[i].lower())
            if near:
                chars[i] = RNG.choice(near)
    return "".join(chars)


def variant_address(addr: str, state_code: str = "ME", state_name: str = "Maine") -> str:
    """A real address written the way people actually type it.

    The state is a parameter so the same mangling serves any build (tests/accuracy/build_cases.py
    passes the build's own state); the defaults keep the Maine corpus exactly as it was.
    """
    number_street, town, _state = (addr.split(", ") + ["", ""])[:3]
    words = number_street.split()
    words = [SUFFIXES.get(w, w) if RNG.random() < 0.5 else w for w in words]
    words = [DIRECTIONS.get(w, w) for w in words]
    street = " ".join(words)
    form = RNG.choice(("no_state", "no_town", "unit", "lower", "zip_style"))
    if form == "no_state":
        return f"{street}, {town}"
    if form == "no_town":
        return f"{street} {state_name}"
    if form == "unit":
        return f"{street} Apt {RNG.randint(1, 12)}, {town}, {state_code}"
    if form == "lower":
        return f"{street} {town} {state_code}".lower()
    return f"{street} {town} {state_name}"


def variant_place(name: str) -> str:
    n = re.sub(r"^Mount ", "Mt ", name) if name.startswith("Mount ") else name
    if n.endswith((" Lake", " Pond")) and RNG.random() < 0.5:
        head, tail = n.rsplit(" ", 1)
        n = f"{tail} {head}"  # "Moosehead Lake" -> "Lake Moosehead"
    return n.lower() if RNG.random() < 0.3 else n


def variant_venue(name: str) -> str:
    words = name.split()
    return (
        " ".join(words[: max(1, len(words) // 2)]) if len(words) > 1 else name.lower()
    )


SYLLABLES = [
    "zor",
    "blax",
    "quen",
    "vi",
    "trum",
    "osk",
    "pel",
    "dran",
    "yux",
    "mib",
    "kro",
    "fen",
]
OUT_OF_STATE = [
    "1600 Pennsylvania Ave NW, Washington, DC",
    "Eiffel Tower",
    "Golden Gate Bridge",
    "Times Square, New York",
    "Toronto",
    "Vancouver",
    "Miami Beach",
    "Grand Canyon",
    "Space Needle, Seattle",
    "10 Downing Street, London",
    "Mount Rainier",
    "Lake Tahoe",
    "Austin, TX",
    "Chicago Union Station",
    "Niagara Falls, Ontario",
    "Death Valley",
]


def misses(streets: list[str], n: int | None = None,
           out_of_state: list[str] | None = None) -> list[str]:
    """Queries that should return nothing, or nothing confident.

    `out_of_state` is a parameter because the list below is only out of state for Maine: on a
    New York build "Times Square, New York" and "Niagara Falls, Ontario" are not misses at all.
    """
    elsewhere = OUT_OF_STATE if out_of_state is None else out_of_state
    out: list[str] = []
    while len(out) < (n or N["misses"]):
        kind = RNG.choice(("big_number", "invented", "out_of_state", "gibberish"))
        if kind == "big_number":
            out.append(f"{RNG.randint(90000, 99999)} {RNG.choice(streets)}")
        elif kind == "invented":
            word = "".join(
                RNG.choice(SYLLABLES) for _ in range(RNG.randint(2, 3))
            ).title()
            out.append(
                f"{word} {RNG.choice(('Pond', 'Mountain', 'Street', 'Plaza', 'Brewing Co'))}"
            )
        elif kind == "out_of_state":
            out.append(RNG.choice(elsewhere))
        else:
            out.append(
                "".join(
                    RNG.choice("bcdfghjklmnpqrstvwxz")
                    for _ in range(RNG.randint(5, 12))
                )
            )
    return out


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    # One thread: with parallel scans, random() order (and so the sample) varies per run.
    con.execute("SET threads TO 1")
    con.execute(f"SELECT setseed({SEED})")
    oa = str(DATA / "pelias" / "interpolation_oa" / "us" / "me" / "statewide.csv")
    con.execute(
        "CREATE TABLE oa AS SELECT NUMBER n, STREET s, CITY c, POSTCODE z, "
        "CAST(LAT AS DOUBLE) lat, CAST(LON AS DOUBLE) lon "
        "FROM read_csv(?, all_varchar=true) WHERE NUMBER ~ '^[0-9]+$' AND CITY <> ''",
        [oa],
    )

    def rows(sql: str, params: list | None = None) -> list[tuple]:
        return con.execute(sql, params or []).fetchall()

    corpus: dict[str, list] = {}
    corpus["addresses"] = [
        f"{n} {s}, {c}, ME"
        for n, s, c in rows(
            "SELECT n, s, c FROM oa ORDER BY random() LIMIT ?", [N["addresses"]]
        )
    ]
    corpus["structured"] = [
        {"address": f"{n} {s}", "locality": c, "postalcode": z, "region": "ME"}
        for n, s, c, z in rows(
            "SELECT n, s, c, z FROM oa ORDER BY random() LIMIT ?", [N["structured"]]
        )
    ]
    corpus["oa_points"] = [
        [round(lat, 6), round(lon, 6)]
        for lat, lon in rows(
            "SELECT lat, lon FROM oa ORDER BY random() LIMIT ?", [N["oa_points"]]
        )
    ]

    gnis = str(DATA / "processed" / "csv" / "gnis.csv")
    corpus["places"] = [
        r[0]
        for r in rows(
            "SELECT name FROM read_csv(?) WHERE category IN "
            "('lake','summit','island','populated_place','civil','bay','cape') "
            "ORDER BY random() LIMIT ?",
            [gnis, N["places"]],
        )
    ]
    overture = str(DATA / "processed" / "csv" / "overture.csv")
    corpus["venues"] = [
        r[0]
        for r in rows(
            "SELECT name FROM read_csv(?) ORDER BY random() LIMIT ?",
            [overture, N["venues"]],
        )
    ]
    zcta = str(DATA / "processed" / "csv" / "zcta.csv")
    corpus["zips"] = [
        r[0]
        for r in rows(
            "SELECT postcode FROM read_csv(?, all_varchar=true) ORDER BY random() LIMIT ?",
            [zcta, N["zips"]],
        )
    ]
    corpus["foci"] = [
        [round(lat, 4), round(lon, 4)]
        for lat, lon in rows(
            "SELECT avg(lat), avg(lon) FROM oa GROUP BY c ORDER BY random() LIMIT ?",
            [N["foci"]],
        )
    ]

    # Uniform random points inside the Maine polygon (map clicks anywhere, incl. woods).
    shp = f"/vsizip/{DATA / 'raw' / 'boundary' / 'cb_2024_us_state_500k.zip'}/cb_2024_us_state_500k.shp"
    con.execute(
        "CREATE TABLE me AS SELECT geom FROM ST_Read(?) WHERE STUSPS='ME'", [shp]
    )
    pts: list[list[float]] = []
    while len(pts) < N["random_points"]:
        cand = rows(
            "WITH c AS (SELECT 42.97 + random()*4.5 lat, -71.08 + random()*4.2 lon "
            "FROM range(2000)) SELECT lat, lon FROM c, me "
            "WHERE ST_Intersects(ST_Point(lon, lat), me.geom)"
        )
        pts.extend([round(a, 6), round(b, 6)] for a, b in cand)
    corpus["random_points"] = pts[: N["random_points"]]

    # Fuzzy, off-name and miss queries derived from the real ones (seeded Python RNG).
    typo_src = (
        RNG.sample(corpus["addresses"], N["typos"] // 2)
        + RNG.sample(corpus["places"], N["typos"] // 4)
        + RNG.sample(corpus["venues"], N["typos"] // 4)
    )
    corpus["typos"] = [typo(t) for t in typo_src]
    corpus["variants"] = (
        [
            variant_address(a)
            for a in RNG.sample(corpus["addresses"], N["variants"] // 2)
        ]
        + [variant_place(p) for p in RNG.sample(corpus["places"], N["variants"] // 4)]
        + [variant_venue(v) for v in RNG.sample(corpus["venues"], N["variants"] // 4)]
    )
    streets = sorted({a.split(", ")[0].split(" ", 1)[1] for a in corpus["addresses"]})
    corpus["misses"] = misses(streets)
    # Offshore points in the Gulf of Maine, outside the state polygon.
    offshore: list[list[float]] = []
    while len(offshore) < N["miss_points"]:
        cand = rows(
            "WITH c AS (SELECT 42.95 + random()*0.5 lat, -69.9 + random()*2.8 lon "
            "FROM range(1000)) SELECT lat, lon FROM c, me "
            "WHERE NOT ST_Intersects(ST_Point(lon, lat), ST_Buffer(me.geom, 0.05))"
        )
        offshore.extend([round(a, 6), round(b, 6)] for a, b in cand)
    corpus["miss_points"] = offshore[: N["miss_points"]]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + "\n")
    print({k: len(v) for k, v in corpus.items()}, "->", OUT)


if __name__ == "__main__":
    main()
