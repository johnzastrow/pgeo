"""Pelias API compatibility contract: the same requests against Pelias and both pgeo front ends.

    uv run --project pgeo python tests/compat/compat_test.py \
        [--pelias http://127.0.0.1:4000] [--api http://127.0.0.1:4500] [--sql http://127.0.0.1:4700]

Each case is a documented Pelias request plus a check that must hold on every engine
(status, envelope shape, property names, filter behaviour, error shape). Pelias is the
reference: a case that fails on Pelias itself is reported as "reference" and not counted
against pgeo. Exit status 1 if any pgeo engine fails a case Pelias passes.

Not compared: ranking (tests/accuracy covers it) and gids (engine-specific ids for the same
record, see docs/PELIAS_COMPATIBILITY.md).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable

import httpx

ENVELOPE = {"geocoding", "type", "features"}
BASE_PROPS = {"gid", "layer", "source", "source_id", "name", "confidence", "label", "country", "country_gid",
              "country_a", "country_code", "region", "region_gid", "region_a"}  # fmt: skip
HIER_PROPS = {"county", "county_gid", "county_a", "locality", "locality_gid"}
BANGOR = (44.8016, -68.7712)
PORTLAND_GID = "whosonfirst:locality:85948877"


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def feats(d: dict) -> list[dict]:
    return d.get("features") or []


def ok_shape(d: dict) -> str | None:
    missing = ENVELOPE - set(d)
    if missing:
        return f"envelope missing {sorted(missing)}"
    if not {"version", "query", "timestamp"} <= set(d["geocoding"]):
        return "geocoding block incomplete"
    return None


def has_results(d: dict) -> str | None:
    return None if feats(d) else "no results"


def props_complete(required: set[str]) -> Callable[[dict], str | None]:
    def check(d: dict) -> str | None:
        if not feats(d):
            return "no results"
        missing = required - set(feats(d)[0]["properties"])
        return f"missing properties {sorted(missing)}" if missing else None

    return check


def within_circle(center: tuple[float, float], radius_km: float) -> Callable[[dict], str | None]:
    def check(d: dict) -> str | None:
        if not feats(d):
            return "no results"
        far = [f["properties"]["label"] for f in feats(d)
               if km(center, (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])) > radius_km + 0.5]  # fmt: skip
        return f"outside circle: {far[:2]}" if far else None

    return check


def within_gid(gid: str) -> Callable[[dict], str | None]:
    def check(d: dict) -> str | None:
        if not feats(d):
            return "no results"
        bad = [f["properties"]["label"] for f in feats(d)
               if gid not in {f["properties"].get("gid"), f["properties"].get("locality_gid"),
                              f["properties"].get("localadmin_gid"), f["properties"].get("county_gid")}]  # fmt: skip
        return f"outside {gid}: {bad[:2]}" if bad else None

    return check


def no_results(d: dict) -> str | None:
    return None if not feats(d) else f"expected none, got {len(feats(d))}"


def pelias_error(d: dict) -> str | None:
    errs = d.get("geocoding", {}).get("errors")
    return None if errs else "no geocoding.errors in the error response"


# (name, path, params, expected status, checks)
CASES: list[tuple[str, str, dict, int, list]] = [
    ("search basic", "search", {"text": "389 Congress St, Portland, ME"}, 200, [ok_shape, has_results]),
    ("search properties", "search", {"text": "389 Congress St, Portland, ME"}, 200,
     [props_complete(BASE_PROPS | HIER_PROPS)]),
    ("search focus", "search", {"text": "main st", "focus.point.lat": BANGOR[0], "focus.point.lon": BANGOR[1]}, 200,
     [ok_shape, has_results]),
    ("search boundary.rect", "search", {"text": "main st", "boundary.rect.min_lat": 44.7, "boundary.rect.max_lat": 44.9,
     "boundary.rect.min_lon": -68.9, "boundary.rect.max_lon": -68.6}, 200, [within_circle(BANGOR, 20)]),
    ("search boundary.circle", "search", {"text": "main st", "boundary.circle.lat": BANGOR[0],
     "boundary.circle.lon": BANGOR[1], "boundary.circle.radius": 15}, 200, [within_circle(BANGOR, 15)]),
    ("search circle + focus", "search", {"text": "main st", "boundary.circle.lat": BANGOR[0],
     "boundary.circle.lon": BANGOR[1], "boundary.circle.radius": 15, "focus.point.lat": BANGOR[0],
     "focus.point.lon": BANGOR[1]}, 200, [within_circle(BANGOR, 15)]),
    ("search boundary.gid", "search", {"text": "congress st", "boundary.gid": PORTLAND_GID}, 200,
     [within_gid(PORTLAND_GID)]),
    ("search boundary.country USA", "search", {"text": "bangor", "boundary.country": "USA"}, 200, [has_results]),
    ("search boundary.country FRA", "search", {"text": "bangor", "boundary.country": "FRA"}, 200, [no_results]),
    ("search layers+sources", "search", {"text": "portland", "layers": "locality", "sources": "wof"}, 200,
     [has_results]),
    ("search lang + api_key", "search", {"text": "bangor", "lang": "en", "api_key": "abc123"}, 200, [has_results]),
    ("search size", "search", {"text": "main st", "size": 3}, 200,
     [lambda d: None if 0 < len(feats(d)) <= 3 else f"{len(feats(d))} results for size=3"]),
    ("structured", "search/structured", {"address": "389 Congress St", "locality": "Portland", "region": "ME"}, 200,
     [ok_shape, has_results]),
    ("structured circle", "search/structured", {"address": "Main St", "boundary.circle.lat": BANGOR[0],
     "boundary.circle.lon": BANGOR[1], "boundary.circle.radius": 15}, 200, [within_circle(BANGOR, 15)]),
    ("autocomplete", "autocomplete", {"text": "389 congress"}, 200, [ok_shape, has_results]),
    ("autocomplete circle", "autocomplete", {"text": "main", "boundary.circle.lat": BANGOR[0],
     "boundary.circle.lon": BANGOR[1], "boundary.circle.radius": 15}, 200, [within_circle(BANGOR, 15)]),
    ("autocomplete gid", "autocomplete", {"text": "congress", "boundary.gid": PORTLAND_GID}, 200,
     [within_gid(PORTLAND_GID)]),
    ("reverse", "reverse", {"point.lat": 43.6568, "point.lon": -70.2626}, 200, [ok_shape, has_results]),
    ("reverse radius", "reverse", {"point.lat": 43.6568, "point.lon": -70.2626, "boundary.circle.radius": 1}, 200,
     [within_circle((43.6568, -70.2626), 1)]),
    ("reverse properties", "reverse", {"point.lat": 43.6568, "point.lon": -70.2626, "layers": "address"}, 200,
     [props_complete(BASE_PROPS | HIER_PROPS)]),
    ("reverse country", "reverse", {"point.lat": 43.6568, "point.lon": -70.2626, "boundary.country": "US"}, 200,
     [has_results]),
    ("place", "place", {"ids": PORTLAND_GID}, 200, [ok_shape, has_results]),
    ("error: search without text", "search", {}, 400, [pelias_error]),
    ("error: bad focus", "search", {"text": "x", "focus.point.lat": 999, "focus.point.lon": 0}, 400, [pelias_error]),
    ("error: bad layer", "search", {"text": "x", "layers": "planet"}, 400, [pelias_error]),
    ("error: reverse without point", "reverse", {}, 400, [pelias_error]),
    ("error: circle without lon", "search", {"text": "x", "boundary.circle.lat": 44.8}, 400, [pelias_error]),
]


def run_case(client: httpx.Client, base: str, path: str, params: dict, status: int, checks: list) -> str | None:
    try:
        r = client.get(f"{base}/v1/{path}", params=params, timeout=20)
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return f"{type(e).__name__}"
    if r.status_code != status:
        return f"HTTP {r.status_code} (want {status}): {str(d.get('geocoding', {}).get('errors') or d)[:90]}"
    for check in checks:
        why = check(d)
        if why:
            return why
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pelias", default="http://127.0.0.1:4000")
    ap.add_argument("--api", default="http://127.0.0.1:4500")
    ap.add_argument("--sql", default="http://127.0.0.1:4700")
    ap.add_argument("--json", help="also write the result matrix to this file (used by the report)")
    a = ap.parse_args()
    matrix: list[dict] = []
    engines = {"pelias": a.pelias, "pgeo-api": a.api, "pgeo-sql": a.sql}
    failures = 0
    with httpx.Client() as client:
        print(f"{'case':30} " + " ".join(f"{e:10}" for e in engines))
        for name, path, params, status, checks in CASES:
            res = {e: run_case(client, base, path, params, status, checks) for e, base in engines.items()}
            matrix.append({"case": name, "path": path, "params": {k: str(v) for k, v in params.items()},
                           "expect": status, "results": res})  # fmt: skip
            cells = []
            for e in engines:
                if res[e] is None:
                    cells.append("ok")
                elif e == "pelias":
                    cells.append("reference")
                else:
                    cells.append("FAIL" if res["pelias"] is None else "fail*")
                    failures += res["pelias"] is None
            print(f"{name:30} " + " ".join(f"{c:10}" for c in cells))
            for e in engines:
                if res[e]:
                    print(f"    {e}: {res[e]}")
    print(f"\ncompat: {'passed' if not failures else f'{failures} pgeo failure(s) where Pelias passes'}"
          "  (fail* = fails on Pelias too; reference = Pelias itself fails the contract)")  # fmt: skip
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"engines": engines, "cases": matrix, "failures": failures}, fh, indent=1)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
