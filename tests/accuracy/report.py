"""Compare accuracy runs (data/accuracy/*.json) in a Markdown report with Mermaid and PNGs.

uv run --with matplotlib python tests/accuracy/report.py [--out docs/ACCURACY_RESULTS.md]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
QTYPES = ["exact", "typo", "variant", "miss"]
ENDPOINTS = ["search", "structured", "autocomplete", "reverse"]
KINDS = ["address", "town", "lake_summit", "venue", "zip", "reverse_address", "miss"]


def pct(v):
    return "-" if v is None else f"{v:.0%}"


def load(src: Path) -> list[dict]:
    runs = [json.loads(p.read_text()) for p in sorted(src.glob("*.json"))]
    return sorted(runs, key=lambda r: (r["engine"] != "pelias", r["engine"], r["label"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=ROOT / "data" / "accuracy")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "ACCURACY_RESULTS.md")
    ap.add_argument("--png-dir", type=Path, default=ROOT / "docs" / "accuracy")
    a = ap.parse_args()
    runs = load(a.src)
    names = [f"{r['engine']}/{r['label']}" for r in runs]
    L = [
        "# Accuracy Results",
        "",
        "Test set: `tests/accuracy/cases.json` (seeded; ground truth from the source data, "
        'independent of both engines). "Correct" = right place at rank 1 (autocomplete: in the '
        "top 5); misses are correct when nothing, or nothing with confidence >= 0.8, is returned.",
        "",
        "## Overall",
        "",
        "| Engine / config | Correct | hit@1 | hit@5 | Median error (m) | No result | Conf. when right | Conf. when wrong | p50 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, r in zip(names, runs, strict=True):
        s = r["summary"]["ALL"]
        L.append(
            f"| {n} | {pct(s['correct'])} | {pct(s['hit1'])} | {pct(s['hit5'])} | {s['median_dist_m']} "
            f"| {pct(s['no_result'])} | {s['conf_when_right']} | {s['conf_when_wrong']} | {s['p50_ms']} |"
        )
    L += [
        "",
        "## By query quality",
        "",
        "| Engine / config | " + " | ".join(QTYPES) + " |",
        "|---|" + "---|" * len(QTYPES),
    ]
    for n, r in zip(names, runs, strict=True):
        L.append(
            f"| {n} | " + " | ".join(pct(r["summary"].get(f"qtype:{q}", {}).get("correct")) for q in QTYPES) + " |"
        )
    L += [
        "",
        "## By endpoint",
        "",
        "| Engine / config | " + " | ".join(ENDPOINTS) + " |",
        "|---|" + "---|" * len(ENDPOINTS),
    ]
    for n, r in zip(names, runs, strict=True):
        L.append(f"| {n} | " + " | ".join(pct(r["summary"].get(e, {}).get("correct")) for e in ENDPOINTS) + " |")
    L += [
        "",
        "## By kind of place",
        "",
        "| Engine / config | " + " | ".join(KINDS) + " |",
        "|---|" + "---|" * len(KINDS),
    ]
    for n, r in zip(names, runs, strict=True):
        L.append(f"| {n} | " + " | ".join(pct(r["summary"].get(f"kind:{k}", {}).get("correct")) for k in KINDS) + " |")
    L += [
        "",
        "## Search detail (endpoint / query type)",
        "",
        "| Engine / config | group | n | correct | hit@1 | hit@5 | median error (m) | no result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for n, r in zip(names, runs, strict=True):
        for key, s in r["summary"].items():
            if "/" in key:
                L.append(
                    f"| {n} | {key} | {s['n']} | {pct(s['correct'])} | {pct(s['hit1'])} | {pct(s['hit5'])} "
                    f"| {s['median_dist_m']} | {pct(s['no_result'])} |"
                )

    # Mermaid: correct % by query type, one bar series per engine (listed in the title order).
    for q in QTYPES:
        vals = [round(100 * (r["summary"].get(f"qtype:{q}", {}).get("correct") or 0)) for r in runs]
        labels = ", ".join(f'"{n}"' for n in names)
        L += [
            "",
            "```mermaid",
            "xychart-beta",
            f'    title "Correct % on {q} queries"',
            f"    x-axis [{labels}]",
            '    y-axis "%" 0 --> 100',
            f"    bar [{', '.join(map(str, vals))}]",
            "```",
        ]

    a.png_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    width = 0.8 / max(1, len(runs))
    for i, (n, r) in enumerate(zip(names, runs, strict=True)):
        vals = [100 * (r["summary"].get(f"qtype:{q}", {}).get("correct") or 0) for q in QTYPES]
        ax.bar([x + i * width for x in range(len(QTYPES))], vals, width, label=n)
    ax.set_xticks([x + width * (len(runs) - 1) / 2 for x in range(len(QTYPES))], QTYPES)
    ax.set_ylabel("% correct")
    ax.set_ylim(0, 100)
    ax.set_title("Accuracy by query quality")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    f = a.png_dir / "accuracy-by-qtype.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    rel = "accuracy" if a.out.parent.name == "docs" else str(a.png_dir)
    L += ["", f"![accuracy by query type]({rel}/{f.name})", ""]
    a.out.write_text("\n".join(L) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
