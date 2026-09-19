"""Accuracy regression gate: compare a run against the committed baseline.

    uv run --project pgeo python tests/accuracy/gate.py data/accuracy/pgeo-rebuild.json \
        [--fuzz data/accuracy/pgeo-fuzz-rebuild.json] [--baseline tests/accuracy/baseline.json]
    ... --write-baseline      record the given run(s) as the new baseline (after review)

Fails (exit 1) when, against the baseline:
- overall correct drops by more than 0.5 percentage points,
- any kind/query-quality group loses more than max(2 cases, 2%) of its cases,
- confidence of wrong answers rises by more than 0.05 (calibration got worse),
- any fuzz level drops by more than 2 points.
Small tolerances absorb data refreshes (a new OSM extract moves a few answers); anything
larger means a tuning change was lost or a new change hurt accuracy.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"


def groups(run: dict) -> dict[str, list[int]]:
    g: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in run["results"]:
        key = f"{r['kind']}/{r['qtype']}"
        g[key][0] += bool(r.get("correct"))
        g[key][1] += 1
    return dict(g)


def snapshot(run: dict, fuzz: dict | None) -> dict:
    s = run["summary"]["ALL"]
    out = {
        "source": f"{run['engine']}/{run['label']}",
        "correct": s["correct"],
        "conf_when_wrong": s["conf_when_wrong"],
        "groups": groups(run),
    }
    if fuzz:
        out["fuzz"] = {k.split(":")[1]: v["correct"] for k, v in fuzz["summary"].items() if k.count(":") == 1 and k.startswith("level:")}
    return out


def compare(base: dict, cur: dict) -> list[str]:
    fails = []
    if cur["correct"] < base["correct"] - 0.005:
        fails.append(f"overall {cur['correct']:.3f} < baseline {base['correct']:.3f} - 0.005")
    if cur["conf_when_wrong"] is not None and base["conf_when_wrong"] is not None:
        if cur["conf_when_wrong"] > base["conf_when_wrong"] + 0.05:
            fails.append(f"confidence when wrong {cur['conf_when_wrong']} > {base['conf_when_wrong']} + 0.05")
    for key, (b_ok, b_n) in base["groups"].items():
        c_ok, _ = cur["groups"].get(key, [0, 0])
        allowed = max(2, round(0.02 * b_n))
        if c_ok < b_ok - allowed:
            fails.append(f"{key}: {c_ok}/{b_n} correct, baseline {b_ok} (tolerance {allowed})")
    for level, b in base.get("fuzz", {}).items():
        c = cur.get("fuzz", {}).get(level)
        if c is not None and c < b - 0.02:
            fails.append(f"fuzz {level}: {c:.3f} < baseline {b:.3f} - 0.02")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--fuzz", type=Path)
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()
    cur = snapshot(json.loads(args.run.read_text()), json.loads(args.fuzz.read_text()) if args.fuzz else None)
    if args.write_baseline:
        args.baseline.write_text(json.dumps(cur, indent=1, sort_keys=True) + "\n")
        print(f"baseline written: {args.baseline} ({cur['source']}, correct {cur['correct']:.3f})")
        return 0
    base = json.loads(args.baseline.read_text())
    fails = compare(base, cur)
    print(f"gate: {cur['source']} correct {cur['correct']:.3f} vs baseline {base['correct']:.3f} ({base['source']})")
    for f in fails:
        print("  FAIL", f)
    print("gate:", "passed" if not fails else f"{len(fails)} regression(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
