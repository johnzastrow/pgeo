"""Turn a load-test run (data/loadtest/<run-id>/*.json) into a Markdown report with
tables, Mermaid charts (render in Forgejo) and matplotlib PNGs.

Usage (repo root):
    uv run --with matplotlib python tests/load/report.py data/loadtest/<run-id> \
        [--engine Pelias] [--out docs/LOAD_TEST_RESULTS.md] [--png-dir docs/loadtest]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

EPS = ["autocomplete", "search", "structured", "reverse"]
SLO = {"autocomplete": 250, "search": 750, "structured": 750, "reverse": 400}
QTYPES = ["exact", "typo", "variant", "miss"]
ORDER = ["M0", "C1", "C1s", "C2", "C3", "C4", "C4a"]


def load(run_dirs: list[Path]) -> list[dict]:
    """Collect config results from one or more run directories (later runs win)."""
    found = {}
    for d in run_dirs:
        for p in d.glob("*.json"):
            found[p.stem] = json.loads(p.read_text()) | {"run": d.name}
    return [found[k] for k in ORDER if k in found] + [
        v for k, v in sorted(found.items()) if k not in ORDER
    ]


def fmt(v, digits=0, unit=""):
    if v is None:
        return "-"
    return f"{v:,.{digits}f}{unit}"


def p95(step: dict, ep: str):
    return step["endpoints"].get(ep, {}).get("p(95)")


def cfg_label(r: dict) -> str:
    c = r["config"]
    cpus = "all" if c["cpus"] is None else str(c["cpus"])
    mem = "unlimited" if r["budget_gb"] is None else f"{r['budget_gb']} GB"
    return f"{cpus} CPU / {mem} / heap {c['heap']} / {c['workers']} API worker(s)"


def first_failure(r: dict) -> str:
    """What gave out first: the first ramp step that failed an SLO, and why."""
    for s in r["runs"]["ramp"]:
        if s.get("died"):
            dead = [
                k
                for k, h in s["health"].items()
                if h["status"] != "running" or h["oom"] or h.get("restarts", 0) > 0
            ]
            return f"container died at {s['vus']} users ({', '.join(dead)})"
        if not s["pass"]:
            bad = [ep for ep in EPS if (p95(s, ep) or 0) > SLO[ep]]
            why = (
                f"p95 over target: {', '.join(bad)}"
                if bad
                else f"errors {s['error_rate']:.1%}"
            )
            hot = max(
                (
                    (k, v["cpu_p95"])
                    for k, v in s["resources"].items()
                    if not k.startswith("_")
                ),
                key=lambda kv: kv[1],
                default=("?", 0),
            )
            return f"{why} at {s['vus']} users; busiest service {hot[0]} ({hot[1]:.0f}% CPU)"
    return "no SLO failure within the ramp"


def mermaid_bar(results: list[dict]) -> str:
    labels = ", ".join(f'"{r["id"]}"' for r in results)
    vals = ", ".join(str(r["limit_users"]) for r in results)
    top = max([r["limit_users"] for r in results] + [1])
    return (
        "```mermaid\nxychart-beta\n"
        '    title "Max concurrent users within SLO"\n'
        f"    x-axis [{labels}]\n"
        f'    y-axis "users" 0 --> {int(top * 1.15) + 1}\n'
        f"    bar [{vals}]\n```"
    )


def mermaid_line(r: dict) -> str:
    ramp = r["runs"]["ramp"]
    xs = ", ".join(str(s["vus"]) for s in ramp)
    ac = ", ".join(fmt(p95(s, "autocomplete") or 0, 0) for s in ramp)
    se = ", ".join(fmt(p95(s, "search") or 0, 0) for s in ramp)
    ymax = max(
        [p95(s, "autocomplete") or 0 for s in ramp]
        + [p95(s, "search") or 0 for s in ramp]
        + [260]
    )
    return (
        "```mermaid\nxychart-beta\n"
        f'    title "{r["id"]}: p95 latency (ms) vs users (first line autocomplete, second search)"\n'
        f"    x-axis [{xs}]\n"
        f'    y-axis "ms" 0 --> {int(ymax * 1.1)}\n'
        f"    line [{ac}]\n"
        f"    line [{se}]\n```"
    ).replace(",,", ",")


def plots(results: list[dict], png_dir: Path, engine: str) -> list[str]:
    png_dir.mkdir(parents=True, exist_ok=True)
    files = []

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for ax, ep in zip(axes.flat, EPS, strict=True):
        for r in results:
            ramp = [s for s in r["runs"]["ramp"] if p95(s, ep)]
            ax.plot(
                [s["vus"] for s in ramp],
                [p95(s, ep) for s in ramp],
                marker="o",
                ms=3,
                label=r["id"],
            )
        ax.axhline(SLO[ep], color="crimson", ls="--", lw=1, label="SLO")
        ax.set_title(f"/v1/{ep} p95")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_ylabel("ms")
        ax.grid(alpha=0.3, which="both")
    for ax in axes[1]:
        ax.set_xlabel("concurrent users")
    axes[0][0].legend(fontsize=8)
    fig.suptitle(f"{engine}: p95 latency by endpoint vs concurrent users")
    fig.tight_layout()
    f = png_dir / f"{engine.lower()}-latency.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    files.append(f.name)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    for r in results:
        ramp = r["runs"]["ramp"]
        a1.plot(
            [s["vus"] for s in ramp],
            [s["req_rate"] for s in ramp],
            marker="o",
            ms=3,
            label=r["id"],
        )
        a2.plot(
            [s["vus"] for s in ramp],
            [s["resources"].get("_stack", {}).get("cpu_p95", 0) / 100 for s in ramp],
            marker="o",
            ms=3,
            label=r["id"],
        )
    a1.set_title("Throughput (steady-state req/s)")
    a2.set_title("Stack CPU, p95 (cores busy)")
    for ax in (a1, a2):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("concurrent users")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    fig.tight_layout()
    f = png_dir / f"{engine.lower()}-throughput-cpu.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    files.append(f.name)

    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.bar(
        [r["id"] for r in results], [r["limit_users"] for r in results], color="#14213d"
    )
    for i, r in enumerate(results):
        ax.text(
            i,
            r["limit_users"],
            str(r["limit_users"]),
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_ylabel("users")
    ax.set_title(f"{engine}: max concurrent users within SLO")
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    f = png_dir / f"{engine.lower()}-limits.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    files.append(f.name)
    return files


def dataset_section(ds: list[dict], png_dir: Path, rel: Path, engine: str) -> list[str]:
    """Data-volume dimension: same resources, growing source subsets."""
    ds = sorted(ds, key=lambda r: r["dataset"])
    L = [
        "## Data volume: how more data layers affect performance",
        "",
        f"Fixed resources ({cfg_label(ds[0])}); each dataset adds sources "
        "(see LOAD_TEST_PLAN.md). p95 ms at 3 users.",
        "",
        "| Dataset | Documents | Index (MB) | 3 users: pass | ac p95 | search p95 | struct p95 "
        "| rev p95 | miss p95 | ES mem peak (MB) | Max users in SLO | Breaking point |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ds:
        v = r["runs"]["validate"]
        es_mem = v["resources"].get("elasticsearch", {}).get("mem_max_mb")
        miss = v.get("qtypes", {}).get("miss", {}).get("p(95)")
        L.append(
            f"| {r['dataset']} | {fmt(r.get('docs'))} | {fmt(r.get('index_mb'))} | "
            f"{'yes' if v['pass'] else '**no**'} | "
            + " | ".join(fmt(p95(v, ep), 0) for ep in EPS)
            + f" | {fmt(miss, 0)} | {fmt(es_mem)} | {r['limit_users']} | {r['breaking_users'] or '-'} |"
        )
    labels = ", ".join(f'"{r["dataset"]}"' for r in ds)
    top = max([r["limit_users"] for r in ds] + [1])
    L += [
        "",
        "```mermaid\nxychart-beta\n"
        '    title "Max concurrent users within SLO by dataset"\n'
        f"    x-axis [{labels}]\n"
        f'    y-axis "users" 0 --> {int(top * 1.15) + 1}\n'
        f"    bar [{', '.join(str(r['limit_users']) for r in ds)}]\n```",
        "",
    ]
    ac = [p95(r["runs"]["validate"], "autocomplete") or 0 for r in ds]
    se = [p95(r["runs"]["validate"], "search") or 0 for r in ds]
    L += [
        "```mermaid\nxychart-beta\n"
        '    title "p95 at 3 users by dataset (first line autocomplete, second search)"\n'
        f"    x-axis [{labels}]\n"
        f'    y-axis "ms" 0 --> {int(max(ac + se + [10]) * 1.2)}\n'
        f"    line [{', '.join(f'{x:.0f}' for x in ac)}]\n"
        f"    line [{', '.join(f'{x:.0f}' for x in se)}]\n```",
        "",
    ]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    docs = [r.get("docs") or 1 for r in ds]
    for ep in EPS:
        a1.plot(
            docs,
            [p95(r["runs"]["validate"], ep) or 0 for r in ds],
            marker="o",
            label=ep,
        )
    a1.set_xscale("log")
    a1.set_xlabel("documents in index")
    a1.set_ylabel("p95 ms at 3 users")
    a1.set_title("Latency vs data volume")
    a1.legend(fontsize=8)
    a1.grid(alpha=0.3, which="both")
    a2.bar([r["dataset"] for r in ds], [r["limit_users"] for r in ds], color="#b3246b")
    a2.set_title("Max users within SLO")
    a2.grid(alpha=0.3, axis="y")
    fig.suptitle(f"{engine}: data volume at fixed resources")
    fig.tight_layout()
    f = png_dir / f"{engine.lower()}-data-volume.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    L += [f"![data volume]({rel / f.name})", ""]
    return L


def report(run_dirs: list[Path], engine: str, out: Path, png_dir: Path) -> None:
    everything = load(run_dirs)
    results = [r for r in everything if not r.get("dataset")]
    datasets = [r for r in everything if r.get("dataset")]
    png_dir.mkdir(parents=True, exist_ok=True)
    pngs = plots(results, png_dir, engine) if results else []
    rel = Path("loadtest") if out.parent.name == "docs" else png_dir
    L: list[str] = []
    L += [
        f"# Load Test Results: {engine}",
        "",
        f"Runs {', '.join(f'`{d.name}`' for d in run_dirs)}; method and SLOs in [LOAD_TEST_PLAN.md](LOAD_TEST_PLAN.md). "
        "Latencies in ms (steady state, warm caches). Direct to the API, no edge rate limits.",
        "",
        "## Summary",
        "",
        "| Config | Resources | 3 users: pass | ac p95 | search p95 | struct p95 | rev p95 "
        "| Stack CPU p95 at 3 | Max users in SLO | Breaking point | First limit hit |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        v = r["runs"]["validate"]
        cpu = v["resources"].get("_stack", {}).get("cpu_p95")
        L.append(
            f"| {r['id']} | {cfg_label(r)} | {'yes' if v['pass'] else '**no**'} | "
            + " | ".join(fmt(p95(v, ep), 0) for ep in EPS)
            + f" | {fmt(cpu, 0, '%')} | {r['limit_users']} | {r['breaking_users'] or '-'} | {first_failure(r)} |"
        )
    L += ["", mermaid_bar(results), "", f"![limits]({rel / pngs[2]})", ""]

    L += [
        "## Latency and throughput vs users",
        "",
        f"![latency]({rel / pngs[0]})",
        "",
        f"![throughput and cpu]({rel / pngs[1]})",
        "",
    ]

    L += [
        "## Query types at 3 users",
        "",
        "p50 / p95 ms by query quality (exact, typo, off-name variant, complete miss).",
        "",
        "| Config | " + " | ".join(QTYPES) + " |",
        "|---|" + "---|" * len(QTYPES),
    ]
    for r in results:
        q = r["runs"]["validate"].get("qtypes", {})
        cells = [
            f"{fmt(q[t].get('med'))} / {fmt(q[t].get('p(95)'))}" if t in q else "-"
            for t in QTYPES
        ]
        L.append(f"| {r['id']} | " + " | ".join(cells) + " |")
    L.append("")

    rel_dir = Path("loadtest") if out.parent.name == "docs" else png_dir
    if datasets:
        L += dataset_section(datasets, png_dir, rel_dir, engine)

    L += ["## Ramp detail per configuration", ""]
    for r in results + sorted(datasets, key=lambda r: r["dataset"]):
        L += [
            f"### {r['id']}: {cfg_label(r)}",
            "",
            mermaid_line(r),
            "",
            "| Users | req/s | Errors | ac p95 | search p95 | struct p95 | rev p95 | miss p95 "
            "| Stack CPU p95 | Busiest service | Peak mem (MB) | Result |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in r["runs"]["ramp"]:
            res = s["resources"]
            svc = [(k, v) for k, v in res.items() if not k.startswith("_")]
            busiest = max(
                svc, key=lambda kv: kv[1]["cpu_p95"], default=("-", {"cpu_p95": 0})
            )
            mem = sum(v.get("mem_max_mb", 0) for _, v in svc)
            miss = s.get("qtypes", {}).get("miss", {}).get("p(95)")
            result = (
                "DIED"
                if s.get("died")
                else ("broken" if s["broken"] else ("pass" if s["pass"] else "fail"))
            )
            L.append(
                f"| {s['vus']} | {fmt(s['req_rate'], 1)} | {s['error_rate']:.1%} | "
                + " | ".join(fmt(p95(s, ep), 0) for ep in EPS)
                + f" | {fmt(miss, 0)} | {fmt(res.get('_stack', {}).get('cpu_p95'), 0, '%')} "
                f"| {busiest[0]} ({busiest[1]['cpu_p95']:.0f}%) | {fmt(mem)} | {result} |"
            )
        L.append("")
    out.write_text("\n".join(L) + "\n")
    print(f"wrote {out} and {len(pngs)} PNGs in {png_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", type=Path, nargs="+")
    ap.add_argument("--engine", default="Pelias")
    ap.add_argument("--out", type=Path, default=Path("docs/LOAD_TEST_RESULTS.md"))
    ap.add_argument("--png-dir", type=Path, default=Path("docs/loadtest"))
    a = ap.parse_args()
    report(a.run_dirs, a.engine, a.out, a.png_dir)


if __name__ == "__main__":
    main()
