"""Run tests/accuracy/cases.json against an engine and score it.

    uv run --project pgeo python tests/accuracy/run_accuracy.py --engine pelias --base http://127.0.0.1:4000
    uv run --project pgeo python tests/accuracy/run_accuracy.py --engine pgeo --base http://127.0.0.1:4500 \
        [--param pgeo.parse=none]

Scoring per case:
- hit@1 / hit@5: a result within truth.radius_m of the true point (and, for addresses, with
  the right house number) at rank 1 / in the top 5
- misses: correct when there is no result or the top result's confidence is < 0.8
- distance error (m) of the top result for scored cases
- confidence of the top result, split by correct / wrong (calibration)
Writes data/accuracy/<engine>-<label>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "cases.json"
OUT_DIR = ROOT / "data" / "accuracy"
PATHS = {"search": "search", "structured": "search/structured", "autocomplete": "autocomplete", "reverse": "reverse"}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def matches(feature: dict, truth: dict) -> tuple[bool, float]:
    lon, lat = feature["geometry"]["coordinates"]
    d = haversine_m(truth["lat"], truth["lon"], lat, lon)
    ok = d <= truth["radius_m"]
    if ok and truth.get("housenumber"):
        hn = str(feature["properties"].get("housenumber", "")).lower()
        ok = hn == str(truth["housenumber"]).lower()
    return ok, d


def score(case: dict, body: dict | None, error: str | None) -> dict:
    feats = (body or {}).get("features", []) if not error else []
    top = feats[0] if feats else None
    top_conf = top["properties"].get("confidence") if top else None
    res = {
        "id": case["id"],
        "endpoint": case["endpoint"],
        "qtype": case["qtype"],
        "kind": case["kind"],
        "n_results": len(feats),
        "top_conf": top_conf,
        "error": error,
    }
    if case["truth"] is None:  # miss
        res["correct"] = top is None or (top_conf is not None and top_conf < 0.8)
        return res
    hits = [matches(f, case["truth"]) for f in feats[:5]]
    res["hit1"] = bool(hits and hits[0][0])
    res["hit5"] = any(h[0] for h in hits)
    res["dist1_m"] = round(hits[0][1], 1) if hits else None
    res["correct"] = res["hit1"] if case["endpoint"] != "autocomplete" else res["hit5"]
    return res


async def run(base: str, extra: dict, concurrency: int) -> list[dict]:
    cases = json.loads(CASES.read_text())
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=20) as client:

        async def one(case: dict) -> dict:
            async with sem:
                params = {**case["params"], **extra, "size": 10}
                t = time.perf_counter()
                try:
                    r = await client.get(f"/v1/{PATHS[case['endpoint']]}", params=params)
                    body, err = (r.json(), None) if r.status_code == 200 else (None, f"HTTP {r.status_code}")
                except (httpx.HTTPError, ValueError) as e:
                    body, err = None, type(e).__name__
                out = score(case, body, err)
                out["ms"] = round((time.perf_counter() - t) * 1000, 1)
                return out

        return await asyncio.gather(*(one(c) for c in cases))


def summarize(results: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in results:
        for key in (
            "ALL",
            f"{r['endpoint']}",
            f"{r['endpoint']}/{r['qtype']}",
            f"kind:{r['kind']}",
            f"qtype:{r['qtype']}",
        ):
            groups.setdefault(key, []).append(r)
    out = {}
    for key, rs in sorted(groups.items()):
        scored = [r for r in rs if "hit1" in r]
        dists = sorted(r["dist1_m"] for r in scored if r.get("dist1_m") is not None)
        right = [r["top_conf"] for r in rs if r["correct"] and r["top_conf"] is not None]
        wrong = [r["top_conf"] for r in rs if not r["correct"] and r["top_conf"] is not None]
        out[key] = {
            "n": len(rs),
            "correct": round(sum(r["correct"] for r in rs) / len(rs), 3),
            "hit1": round(sum(r["hit1"] for r in scored) / len(scored), 3) if scored else None,
            "hit5": round(sum(r["hit5"] for r in scored) / len(scored), 3) if scored else None,
            "median_dist_m": round(dists[len(dists) // 2], 1) if dists else None,
            "no_result": round(sum(r["n_results"] == 0 for r in rs) / len(rs), 3),
            "errors": sum(1 for r in rs if r["error"]),
            "conf_when_right": round(sum(right) / len(right), 3) if right else None,
            "conf_when_wrong": round(sum(wrong) / len(wrong), 3) if wrong else None,
            "p50_ms": sorted(r["ms"] for r in rs)[len(rs) // 2],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", default="default")
    ap.add_argument("--param", action="append", default=[], help="extra query param k=v (e.g. pgeo.parse=none)")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    extra = dict(p.split("=", 1) for p in a.param)
    results = asyncio.run(run(a.base, extra, a.concurrency))
    summary = summarize(results)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{a.engine}-{a.label}.json"
    out.write_text(
        json.dumps(
            {"engine": a.engine, "label": a.label, "params": extra, "summary": summary, "results": results}, indent=1
        )
    )
    s = summary["ALL"]
    print(
        f"{a.engine}/{a.label}: correct {s['correct']:.1%}  hit@1 {s['hit1']:.1%}  hit@5 {s['hit5']:.1%}  "
        f"no-result {s['no_result']:.1%}  errors {s['errors']}  -> {out}"
    )


if __name__ == "__main__":
    main()
