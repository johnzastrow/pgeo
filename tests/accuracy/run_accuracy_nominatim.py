"""Run the accuracy set against Nominatim, scored exactly as the other engines are.

    uv run --project pgeo python tests/accuracy/run_accuracy_nominatim.py --base http://127.0.0.1:8081

Same cases, same ground truth, same radius, same housenumber check (run_accuracy.score). Only the
request is translated, plus one normalisation: Nominatim reports the house number at
`properties.address.house_number`, where the scorer looks for `properties.housenumber`.

Differences that belong to Nominatim rather than to this harness, reported rather than worked
around:

  * No autocomplete endpoint. Its absence is the reason Photon exists. The autocomplete cases go
    to /search, which is what a client would have to do.
  * No confidence score. A "miss" case counts as correct when the engine returns nothing or scores
    its best answer below 0.8; Nominatim can only ever satisfy the first. (It returns an
    `importance`, but that ranks results against each other, it does not express belief.)
  * Reverse returns exactly one result, so hit@5 equals hit@1 there.

Writes data/accuracy/nominatim-<label>.json.
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
    """A Pelias case as a Nominatim path and query string."""
    p = case["params"]
    common = {"format": "geojson", "addressdetails": 1}

    if case["endpoint"] == "reverse":
        # zoom=18 is house level, the closest equivalent to the other engines' layers=address.
        return "reverse", {**common, "lat": p["point.lat"], "lon": p["point.lon"], "zoom": 18}

    if case["endpoint"] == "structured":
        q = {**common, "limit": 10}
        for src, dst in (("address", "street"), ("locality", "city"),
                         ("region", "state"), ("postalcode", "postalcode")):  # fmt: skip
            if p.get(src):
                q[dst] = p[src]
        return "search", q

    return "search", {**common, "q": p["text"], "limit": 10}


def normalise(body: dict | None) -> dict | None:
    """Lift address.house_number to the key the scorer reads."""
    if not body:
        return body
    for f in body.get("features", []):
        props = f.get("properties", {})
        hn = (props.get("address") or {}).get("house_number")
        if hn is not None:
            props["housenumber"] = hn
    return body


async def run(base: str, concurrency: int, cases_path: Path) -> list[dict]:
    cases = json.loads(cases_path.read_text())
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60) as client:

        async def one(case: dict) -> dict:
            async with sem:
                path, params = request_for(case)
                t = time.perf_counter()
                try:
                    r = await client.get(f"/{path}", params=params)
                    body, err = (r.json(), None) if r.status_code == 200 else (None, f"HTTP {r.status_code}")
                except (httpx.HTTPError, ValueError) as e:
                    body, err = None, type(e).__name__
                out = score(case, normalise(body), err)
                out["ms"] = round((time.perf_counter() - t) * 1000, 1)
                if "level" in case:
                    out["level"] = case["level"]
                return out

        return await asyncio.gather(*(one(c) for c in cases))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--cases", type=Path, default=CASES)
    a = ap.parse_args()

    results = asyncio.run(run(a.base, a.concurrency, a.cases))
    summary = summarize(results)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"nominatim-{a.label}.json"
    out.write_text(
        json.dumps(
            {"engine": "nominatim", "label": a.label, "params": {}, "summary": summary, "results": results},
            indent=1,
        )
    )
    s = summary["ALL"]
    print(
        f"nominatim/{a.label}: correct {s['correct']:.1%}  hit@1 {s['hit1']:.1%}  "
        f"hit@5 {s['hit5']:.1%}  -> {out}"
    )
    for ep in ("search", "autocomplete", "structured", "reverse"):
        e = summary.get(ep)
        if e:
            print(f"  {ep:<13} n={e['n']:<5} correct {e['correct']:.1%}  no result {e['no_result']:.1%}")


if __name__ == "__main__":
    main()
