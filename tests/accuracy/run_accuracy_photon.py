"""Run the accuracy set against Photon, scored exactly as the other engines are.

    uv run --project pgeo python tests/accuracy/run_accuracy_photon.py --base http://127.0.0.1:2322

Photon does not speak the Pelias API, so this translates the request and leaves the scoring
alone - the same cases, the same ground truth, the same radius, the same housenumber check
(run_accuracy.score). Photon already answers in GeoJSON with `properties.housenumber`, so the
response needs no translation at all.

Two differences are properties of Photon rather than of this harness, and both are reported
rather than worked around:

  * There is no structured endpoint. The structured cases are sent as one line, which is what a
    client would have to do - the same concession the load session makes.
  * There is no confidence score. A "miss" case counts as correct when the engine returns nothing
    or scores its best answer below 0.8; Photon can only ever satisfy the first of those, so it
    has no way to say "I do not know".

Writes data/accuracy/photon-<label>.json, in the shape the report's collector reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_accuracy import OUT_DIR, score, summarize

CASES = Path(__file__).resolve().parent / "cases.json"


def request_for(case: dict) -> tuple[str, dict]:
    """A Pelias case as a Photon path and query string."""
    p = case["params"]
    if case["endpoint"] == "reverse":
        q = {"lat": p["point.lat"], "lon": p["point.lon"], "limit": 10}
        # Every reverse case asks Pelias for layers=address. Photon spells that layer=house;
        # without it Photon may answer with the nearest street or locality and fail a
        # housenumber check it was never asked to satisfy.
        if p.get("layers") == "address":
            q["layer"] = "house"
        return "reverse", q

    if case["endpoint"] == "structured":
        # No structured endpoint: send the fields as one line, in postal order.
        text = ", ".join(
            str(p[k]) for k in ("address", "locality", "region", "postalcode") if p.get(k)
        )
    else:
        text = p["text"]

    q = {"q": text, "limit": 10}
    if p.get("focus.point.lat") is not None:
        q["lat"], q["lon"] = p["focus.point.lat"], p["focus.point.lon"]
    return "api", q


async def run(base: str, concurrency: int, cases_path: Path) -> list[dict]:
    cases = json.loads(cases_path.read_text())
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=20) as client:

        async def one(case: dict) -> dict:
            async with sem:
                path, params = request_for(case)
                t = time.perf_counter()
                try:
                    r = await client.get(f"/{path}", params=params)
                    body, err = (r.json(), None) if r.status_code == 200 else (None, f"HTTP {r.status_code}")
                except (httpx.HTTPError, ValueError) as e:
                    body, err = None, type(e).__name__
                out = score(case, body, err)
                out["ms"] = round((time.perf_counter() - t) * 1000, 1)
                if "level" in case:
                    out["level"] = case["level"]
                return out

        return await asyncio.gather(*(one(c) for c in cases))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:2322")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--cases", type=Path, default=CASES)
    a = ap.parse_args()

    results = asyncio.run(run(a.base, a.concurrency, a.cases))
    summary = summarize(results)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"photon-{a.label}.json"
    out.write_text(
        json.dumps(
            {"engine": "photon", "label": a.label, "params": {}, "summary": summary, "results": results},
            indent=1,
        )
    )
    s = summary["ALL"]
    print(
        f"photon/{a.label}: correct {s['correct']:.1%}  hit@1 {s['hit1']:.1%}  hit@5 {s['hit5']:.1%}  "
        f"-> {out}"
    )
    for ep in ("search", "autocomplete", "structured", "reverse"):
        e = summary.get(ep)
        if e:
            print(f"  {ep:<13} n={e['n']:<5} correct {e['correct']:.1%}  no result {e['no_result']:.1%}")


if __name__ == "__main__":
    main()
