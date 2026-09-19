"""Data-driven figures for the report. Every function reads saved results (never live systems)
and returns the figure's base name; captions live in the report text."""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D

import style
from style import ENGINE_COLOR, ENGINE_LABEL, GOOD, NEUTRAL, PELIAS, PGEO_API, PGEO_SQL, PGEO_SVC, SLO_RED, plain_log_y, save

ROOT = Path(__file__).resolve().parents[1]
INPUTS = tomllib.loads((ROOT / "report" / "inputs.toml").read_text())
SLO = {"autocomplete": 250, "search": 750, "structured": 750, "reverse": 400}
EPS = list(SLO)
EP_LABEL = {"autocomplete": "Autocomplete", "search": "Search", "structured": "Structured", "reverse": "Reverse"}
KIND_LABEL = {"address": "Addresses", "town": "Towns", "lake_summit": "Lakes, summits", "venue": "Venues",
              "zip": "ZIP codes", "reverse_address": "Reverse", "miss": "Misses"}  # fmt: skip
QTYPES = ["exact", "typo", "variant", "miss"]


# ---- loading ------------------------------------------------------------------------------


def load_runs(dirs: list[str]) -> dict[str, dict]:
    """Configuration results from run directories; later directories win for the same id."""
    found: dict[str, dict] = {}
    for d in dirs:
        for p in sorted((ROOT / d).glob("*.json")):
            doc = json.loads(p.read_text())
            if "runs" in doc:
                found[doc["id"]] = doc | {"run": Path(d).name}
    return found


def load_accuracy(kind: str) -> dict[str, dict]:
    out = {}
    for engine, rel in INPUTS["accuracy"][kind].items():
        p = ROOT / rel
        if p.is_file():
            out[engine] = json.loads(p.read_text())
    return out


def pelias_runs() -> dict[str, dict]:
    return load_runs(INPUTS["load"]["pelias"])


def pgeo_runs() -> dict[str, dict]:
    return load_runs(INPUTS["load"]["pgeo"])


def dataset_runs() -> dict[str, dict]:
    return load_runs(INPUTS["load"]["pelias_datasets"])


def rate(rows: list[dict]) -> float:
    return 100.0 * sum(bool(r.get("correct")) for r in rows) / max(len(rows), 1)


def by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[r.get(key)].append(r)
    return g


def engine_color(cid: str) -> str:
    if cid.startswith("rest-"):
        return PGEO_SQL
    if cid.startswith("api-svc"):
        return PGEO_SVC
    if cid.startswith("api-"):
        return PGEO_API
    return PELIAS


def bar_labels(ax, bars, fmt="{:.0f}", fs=6.5, pad=1.0):
    for b in bars:
        h = b.get_height()
        ax.text(b.get_x() + b.get_width() / 2, h + pad, fmt.format(h), ha="center", va="bottom", fontsize=fs,
                color=NEUTRAL)  # fmt: skip


# ---- data inventory -----------------------------------------------------------------------


def fig_inventory(out: Path, snap: dict) -> str:
    layers = ["address", "venue", "street", "locality", "postalcode", "neighbourhood", "localadmin", "county"]
    pel = defaultdict(int)
    geo = defaultdict(int)
    for r in snap["pelias"]["by_layer_source"]:
        pel[r["layer"]] += r["n"]
    for r in snap["pgeo"]["by_layer_source"]:
        geo[r["layer"]] += r["n"]
    y = np.arange(len(layers))
    fig, ax = plt.subplots(figsize=(7.0, 3.2))
    h = 0.38
    b1 = ax.barh(y - h / 2, [pel[k] for k in layers], h, color=PELIAS, label=ENGINE_LABEL["pelias"])
    b2 = ax.barh(y + h / 2, [geo[k] for k in layers], h, color=PGEO_SQL, label="pgeo")
    ax.set_xscale("log")
    ax.set_yticks(y, layers)
    ax.invert_yaxis()
    ax.set_xlabel("records (log scale)")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_width() * 1.08, b.get_y() + b.get_height() / 2, f"{int(b.get_width()):,}", va="center",
                    fontsize=6.3, color=NEUTRAL)  # fmt: skip
    ax.set_xlim(5, 6e6)
    ax.legend(loc="lower right")
    ax.set_title("Records by layer in each engine")
    return save(fig, out, "fig_inventory")


# ---- accuracy -----------------------------------------------------------------------------


def fig_accuracy_category(out: Path) -> str:
    acc = load_accuracy("main")
    engines = [e for e in ("pelias", "pgeo-sql", "pgeo-api-svc") if e in acc]
    kinds = ["address", "town", "lake_summit", "venue", "zip", "reverse_address", "miss"]
    x = np.arange(len(kinds) + 1)
    w = 0.8 / len(engines)
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    for i, e in enumerate(engines):
        rows = acc[e]["results"]
        g = by(rows, "kind")
        vals = [rate(g[k]) for k in kinds] + [rate(rows)]
        bars = ax.bar(x + (i - (len(engines) - 1) / 2) * w, vals, w, color=ENGINE_COLOR[e], label=ENGINE_LABEL[e])
        bar_labels(ax, bars, fs=5.8)
    ax.set_xticks(x, [KIND_LABEL[k].replace(", ", ",\n") for k in kinds] + ["All\n1,560"])
    ax.set_ylim(0, 110)
    ax.set_ylabel("correct at rank 1 (%)")
    ax.axvline(len(kinds) - 0.5, color=NEUTRAL, lw=0.6, ls=":")
    ax.legend(ncols=3, loc="upper center", bbox_to_anchor=(0.5, 1.13))
    return save(fig, out, "fig_accuracy_category")


def fig_accuracy_quality(out: Path) -> str:
    acc = load_accuracy("main")
    engines = [e for e in ("pelias", "pgeo-sql", "pgeo-api-svc") if e in acc]
    x = np.arange(len(QTYPES))
    w = 0.8 / len(engines)
    fig, ax = plt.subplots(figsize=(5.2, 3.0))
    for i, e in enumerate(engines):
        g = by(acc[e]["results"], "qtype")
        bars = ax.bar(x + (i - (len(engines) - 1) / 2) * w, [rate(g[q]) for q in QTYPES], w, color=ENGINE_COLOR[e],
                      label=ENGINE_LABEL[e])  # fmt: skip
        bar_labels(ax, bars, fs=6)
    ax.set_xticks(x, ["Exact", "Typo", "Variant\n(off-name)", "Miss\n(should fail)"])
    ax.set_ylim(0, 112)
    ax.set_ylabel("correct (%)")
    ax.legend(ncols=3, loc="upper center", bbox_to_anchor=(0.5, 1.14), fontsize=7)
    return save(fig, out, "fig_accuracy_quality")


def fig_fuzz(out: Path) -> str:
    fz = load_accuracy("fuzz")
    levels = [f"F{i}" for i in range(6)]
    kinds = ["address", "town", "lake_summit", "venue"]
    fig, axes = plt.subplots(1, 5, figsize=(7.4, 2.5), sharey=True, gridspec_kw={"width_ratios": [1.6, 1, 1, 1, 1]})
    for e, d in fz.items():
        g = by(d["results"], "level")
        axes[0].plot(levels, [rate(g[lv]) for lv in levels], marker="o", ms=3.5, color=ENGINE_COLOR[e],
                     label=ENGINE_LABEL[e])  # fmt: skip
        for ax, k in zip(axes[1:], kinds, strict=True):
            vals = [rate([r for r in g[lv] if r["kind"] == k]) for lv in levels]
            ax.plot(levels, vals, marker="o", ms=2.5, color=ENGINE_COLOR[e], lw=1)
    axes[0].set_title("All 300 bases")
    axes[0].set_ylabel("correct (%)")
    fig.legend(*axes[0].get_legend_handles_labels(), ncols=2, loc="upper center", bbox_to_anchor=(0.5, 1.07),
               fontsize=7)  # fmt: skip
    for ax, k in zip(axes[1:], kinds, strict=True):
        ax.set_title(KIND_LABEL[k], fontsize=8)
        ax.tick_params(axis="x", labelsize=6)
    for ax in axes:
        ax.set_ylim(0, 100)
        ax.set_xlabel("fuzz level", fontsize=7)
    fig.tight_layout(w_pad=0.6)
    return save(fig, out, "fig_fuzz")


def fig_tuning(out: Path) -> str:
    t = json.loads((ROOT / "report/data/tuning_steps.json").read_text())
    steps = t["steps"]
    x = np.arange(len(steps))
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.4, 2.9), gridspec_kw={"width_ratios": [2.3, 1]})
    ax.plot(x, [s["correct"] for s in steps], marker="o", color=PGEO_SQL, lw=1.6, label="pgeo")
    ax.axhline(t["pelias"], color=PELIAS, ls="--", lw=1.1, label=f"Pelias ({t['pelias']}%)")
    for i, s in enumerate(steps):
        ax.annotate(f"{s['correct']:.1f}", (i, s["correct"]), textcoords="offset points", xytext=(0, 5), ha="center",
                    fontsize=6.5)  # fmt: skip
    ax.set_xticks(x, [s["step"] for s in steps], rotation=28, ha="right", fontsize=7)
    ax.set_ylim(70, 100)
    ax.set_ylabel("correct at rank 1 (%)")
    ax.set_title("Accuracy by tuning step")
    ax.legend(loc="lower right")
    cal = t["calibration"]
    cx = np.arange(len(cal))
    ax2.plot(cx, [c["right"] for c in cal], marker="o", color=GOOD, label="when right")
    ax2.plot(cx, [c["wrong"] for c in cal], marker="o", color=SLO_RED, label="when wrong")
    ax2.set_xticks(cx, [c["step"] for c in cal], rotation=28, ha="right", fontsize=7)
    ax2.set_ylim(0.5, 1.0)
    ax2.set_ylabel("mean top-result confidence")
    ax2.set_title("Calibration")
    ax2.legend(loc="lower left")
    fig.tight_layout()
    return save(fig, out, "fig_tuning")


def fig_calibration(out: Path) -> str:
    acc = load_accuracy("main")
    engines = [e for e in ("pelias", "pgeo-sql") if e in acc]
    bins = np.linspace(0, 1, 21)
    fig, axes = plt.subplots(1, len(engines), figsize=(7.0, 2.6), sharey=True)
    for ax, e in zip(np.atleast_1d(axes), engines, strict=True):
        rows = [r for r in acc[e]["results"] if r.get("top_conf") is not None and r["kind"] != "miss"]
        right = [r["top_conf"] for r in rows if r["correct"]]
        wrong = [r["top_conf"] for r in rows if not r["correct"]]
        ax.hist(right, bins=bins, color=GOOD, alpha=0.75, label=f"right (n={len(right)})", density=True)
        ax.hist(wrong, bins=bins, color=SLO_RED, alpha=0.6, label=f"wrong (n={len(wrong)})", density=True)
        ax.set_title(ENGINE_LABEL[e])
        ax.set_xlabel("confidence of the top result")
        ax.legend(loc="upper left")
    np.atleast_1d(axes)[0].set_ylabel("density")
    fig.tight_layout()
    return save(fig, out, "fig_calibration")


# ---- load ---------------------------------------------------------------------------------

PELIAS_SHOW = ["C1", "C2", "C3", "C4", "M0"]
PGEO_SHOW = ["Pmin", "P1", "P2", "P4", "PM"]


def fig_latency_3users(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    rows = [(c, pel[c], PELIAS) for c in PELIAS_SHOW if c in pel]
    rows += [(f"rest-{c}", pg[f"rest-{c}"], PGEO_SQL) for c in PGEO_SHOW if f"rest-{c}" in pg]
    rows += [(f"api-{c}", pg[f"api-{c}"], PGEO_API) for c in PGEO_SHOW if f"api-{c}" in pg]
    y = np.arange(len(rows))
    fig, axes = plt.subplots(1, 4, figsize=(7.4, 3.6), sharey=True)
    for ax, ep in zip(axes, EPS, strict=True):
        vals = [r["runs"]["validate"]["endpoints"].get(ep, {}).get("p(95)", np.nan) for _, r, _ in rows]
        ax.barh(y, vals, color=[c for _, _, c in rows], height=0.72)
        ax.axvline(SLO[ep], color=SLO_RED, ls="--", lw=1)
        ax.set_xlim(0, SLO[ep] * 1.12)
        ax.set_title(f"{EP_LABEL[ep]}\n(target {SLO[ep]} ms)", fontsize=8)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="x", labelsize=6.5)
    axes[0].set_yticks(y, [cid for cid, _, _ in rows], fontsize=6.8)
    axes[0].invert_yaxis()
    fig.supxlabel("p95 latency at 3 users (ms)", fontsize=8)
    handles = [Line2D([], [], color=c, lw=6) for c in (PELIAS, PGEO_SQL, PGEO_API)]
    fig.legend(handles, ["Pelias", "pgeo pure SQL (rest)", "pgeo FastAPI (api)"], ncols=3, loc="upper center",
               bbox_to_anchor=(0.5, 1.05))  # fmt: skip
    fig.tight_layout()
    return save(fig, out, "fig_latency_3users")


def _ramp_lines(ax, runs: dict, ids: list[str], metric, ylabel: str, title: str, logy=True):
    cmap = plt.get_cmap("viridis")
    for i, cid in enumerate(ids):
        r = runs[cid]
        steps = r["runs"]["ramp"]
        xs = [s["vus"] for s in steps]
        ys = [metric(s) for s in steps]
        ax.plot(xs, ys, marker="o", ms=2.8, lw=1.2, color=cmap(i / max(len(ids) - 1, 1)), label=cid)
        if steps and steps[-1].get("died"):
            ax.plot(xs[-1], ys[-1], marker="x", color=SLO_RED, ms=7, mew=1.6)
    ax.set_xscale("log", base=2)
    if logy:
        ax.set_yscale("log")
    ax.set_xlabel("concurrent users (log scale)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks([1, 4, 16, 64, 256], ["1", "4", "16", "64", "256"])


def fig_ramp(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    pel_ids = [c for c in ["C1", "C1s", "C2", "C3", "C4", "C4a", "M0"] if c in pel]
    pg_ids = [c for c in [f"rest-{p}" for p in PGEO_SHOW] + [f"api-{p}" for p in PGEO_SHOW] if c in pg]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.2), sharey=True)
    metric = lambda s: s["worst_p95_ratio"]  # noqa: E731
    _ramp_lines(a1, pel, pel_ids, metric, "worst p95 / target (log)", "Pelias")
    _ramp_lines(a2, pg, pg_ids, metric, "", "pgeo (rest = pure SQL, api = FastAPI)")
    for ax in (a1, a2):
        ax.axhline(1, color=GOOD, ls="--", lw=1)
        ax.axhline(5, color=SLO_RED, ls=":", lw=1)
        ax.set_ylim(0.05, 60)
        plain_log_y(ax)
        ax.legend(fontsize=6, ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.27))
    a1.text(1.1, 1.1, "within target", color=GOOD, fontsize=6.5)
    a1.text(1.1, 5.6, "broken (5x target)", color=SLO_RED, fontsize=6.5)
    fig.tight_layout()
    return save(fig, out, "fig_ramp")


def fig_throughput(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    fig, ax = plt.subplots(figsize=(7.0, 3.1))
    series = [("C4", pel, PELIAS, "-"), ("C2", pel, PELIAS, "--"), ("api-P4", pg, PGEO_API, "-"),
              ("rest-P4", pg, PGEO_SQL, "-"), ("api-P2", pg, PGEO_API, "--"), ("rest-P2", pg, PGEO_SQL, "--")]  # fmt: skip
    for cid, runs, col, ls in series:
        if cid not in runs:
            continue
        steps = runs[cid]["runs"]["ramp"]
        ax.plot([s["vus"] for s in steps], [s["req_rate"] for s in steps], marker="o", ms=2.5, color=col, ls=ls,
                label=f"{cid} ({runs[cid]['config'].get('cpus') or 'all'} vCPU)")  # fmt: skip
        lim = runs[cid].get("limit_users")
        if lim:
            y = next((s["req_rate"] for s in steps if s["vus"] == lim), None)
            if y:
                ax.plot(lim, y, marker="*", color=col, ms=9, mec="black", mew=0.4)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("concurrent users (log scale)")
    ax.set_ylabel("requests per second (log)")
    ax.set_ylim(0.8, 1000)
    plain_log_y(ax, (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000))
    ax.set_xticks([1, 4, 16, 64, 256], ["1", "4", "16", "64", "256"])
    ax.legend(ncols=2, fontsize=6.5, loc="upper left")
    ax.set_title("Throughput as users are added (star = last step within all targets)")
    return save(fig, out, "fig_throughput")


def fig_frontier(out: Path) -> str:
    """Capacity within SLO versus memory budget: the resource-efficiency picture."""
    pel, pg = pelias_runs(), pgeo_runs()
    fig, ax = plt.subplots(figsize=(6.8, 3.6))
    pts = []
    for cid, r in list(pel.items()) + list(pg.items()):
        if r["budget_gb"] is None or cid.startswith("api-svc"):
            continue
        pts.append((cid, r["budget_gb"], max(r.get("limit_users") or 0, 0.8), engine_color(cid), r["config"].get("cpus")))
    marker = {1: "o", 2: "s", 4: "D"}
    offsets = {"rest-Pmin": (-44, -3), "rest-P1": (6, -3)}
    for cid, x, y, col, cpus in pts:
        ax.scatter(x, y, color=col, marker=marker.get(cpus, "o"), s=38, edgecolor="black", lw=0.4, zorder=3)
        ax.annotate(cid, (x, y), textcoords="offset points", xytext=offsets.get(cid, (5, 2)), fontsize=6.3)
    ax.axhline(3, color=NEUTRAL, ls=":", lw=1)
    ax.text(6.0, 3.25, "target: 3 users", fontsize=6.5, color=NEUTRAL, ha="center")
    ax.set_yscale("log")
    ax.set_ylim(2, 700)
    plain_log_y(ax, (2, 4, 8, 16, 32, 64, 128, 256, 512))
    ax.set_xlabel("memory budget (GB, all containers + 0.8 GB OS)")
    ax.set_ylabel("most users within all targets (log)")
    ax.set_xlim(0, 12)
    handles = [Line2D([], [], color=c, marker="o", ls="", ms=6) for c in (PELIAS, PGEO_SQL, PGEO_API)] + [
        Line2D([], [], color=NEUTRAL, marker=marker[k], ls="", ms=5, mfc="none") for k in (1, 2, 4)]
    ax.legend(handles, ["Pelias", "pgeo pure SQL", "pgeo FastAPI", "1 vCPU", "2 vCPU", "4 vCPU"], ncols=2,
              loc="upper center", bbox_to_anchor=(0.42, 0.98), fontsize=6.8)  # fmt: skip
    ax.set_title("Capacity versus memory budget")
    return save(fig, out, "fig_frontier")


def fig_memory(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    ids = [c for c in ["C1", "C3", "C4", "M0"] if c in pel] + [c for c in ["rest-Pmin", "rest-P2", "api-P2", "api-P4", "api-svc-P2"] if c in pg]
    runs = pel | pg
    services = sorted({s for c in ids for s in runs[c]["runs"]["validate"]["resources"] if s != "_stack"})
    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    bottom = np.zeros(len(ids))
    for i, svc in enumerate(services):
        vals = np.array([runs[c]["runs"]["validate"]["resources"].get(svc, {}).get("mem_max_mb", 0) / 1024 for c in ids])
        if vals.sum() == 0:
            continue
        ax.bar(ids, vals, bottom=bottom, color=cmap(i % 20), label=svc, width=0.7)
        bottom += vals
    for i, v in enumerate(bottom):
        ax.text(i, v + 0.1, f"{v:.1f}", ha="center", fontsize=6.5)
    ax.set_ylabel("peak memory at 3 users (GB)")
    ax.legend(ncols=2, fontsize=6.2, loc="upper right")
    ax.tick_params(axis="x", labelsize=7)
    ax.set_title("Memory in use by service (Pelias left, pgeo right)")
    return save(fig, out, "fig_memory")


def fig_endpoint_ramp(out: Path) -> str:
    """Per-endpoint p95 under load: shows which endpoint gives out first on each engine."""
    pel, pg = pelias_runs(), pgeo_runs()
    panels = [("C4", pel, "Pelias C4 (4 vCPU)"), ("rest-P4", pg, "pgeo pure SQL P4 (4 vCPU)"),
              ("api-P4", pg, "pgeo FastAPI P4 (4 vCPU)")]  # fmt: skip
    colors = {"autocomplete": "#4c72b0", "search": "#55a868", "structured": "#8172b2", "reverse": "#c44e52"}
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.8), sharey=True)
    for ax, (cid, runs, title) in zip(axes, panels, strict=True):
        if cid not in runs:
            continue
        steps = runs[cid]["runs"]["ramp"]
        for ep in EPS:
            ys = [s["endpoints"].get(ep, {}).get("p(95)", np.nan) / SLO[ep] for s in steps]
            ax.plot([s["vus"] for s in steps], ys, marker="o", ms=2.3, lw=1.1, color=colors[ep], label=EP_LABEL[ep])
        ax.axhline(1, color=GOOD, ls="--", lw=1)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_title(title, fontsize=8)
        ax.set_xlabel("users (log)")
        ax.set_xticks([1, 8, 64, 512], ["1", "8", "64", "512"])
    for ax in axes:
        ax.set_ylim(0.02, 60)
        plain_log_y(ax, (0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50))
    axes[0].set_ylabel("p95 / endpoint target (log)")
    axes[0].legend(fontsize=6.3, loc="upper left")
    fig.tight_layout()
    return save(fig, out, "fig_endpoint_ramp")


def fig_cpu(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    panels = [("C4", pel, "Pelias C4"), ("api-P4", pg, "pgeo FastAPI P4"), ("rest-P4", pg, "pgeo pure SQL P4")]
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.7), sharey=True)
    for ax, (cid, runs, title) in zip(axes, panels, strict=True):
        if cid not in runs:
            continue
        steps = runs[cid]["runs"]["ramp"]
        svcs = sorted({k for s in steps for k in s["resources"] if k != "_stack"})
        xs = [s["vus"] for s in steps]
        stack = np.array([[s["resources"].get(v, {}).get("cpu_avg", 0) / 100 for s in steps] for v in svcs])
        ax.stackplot(xs, stack, labels=svcs, colors=plt.get_cmap("tab20")(np.arange(len(svcs)) % 20), alpha=0.9)
        ax.axhline(4, color=SLO_RED, ls=":", lw=1)
        ax.set_xscale("log", base=2)
        ax.set_xticks([1, 8, 64, 512], ["1", "8", "64", "512"])
        ax.set_title(title, fontsize=8)
        ax.set_xlabel("users (log)")
        ax.legend(fontsize=5.5, loc="upper left")
    axes[0].set_ylabel("average CPU (cores)")
    axes[0].text(1.1, 4.1, "4 vCPU limit", color=SLO_RED, fontsize=6)
    fig.tight_layout()
    return save(fig, out, "fig_cpu")


def fig_datavol(out: Path, snap: dict) -> str:
    ds = dataset_runs()
    ids = sorted(ds, key=lambda k: ds[k]["dataset"])
    docs = [ds[k]["docs"] / 1e6 for k in ids]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 2.9))
    labels = [ds[k]["dataset"] for k in ids]
    bars = a1.bar(labels, [ds[k]["limit_users"] for k in ids], color=PELIAS, width=0.6)
    bar_labels(a1, bars, pad=4)
    a1b = a1.twinx()
    a1b.plot(labels, docs, color=NEUTRAL, marker="o", ms=3)
    a1b.set_ylabel("documents (millions)", color=NEUTRAL)
    a1b.set_ylim(0, 2)
    a1b.grid(False)
    a1.set_ylabel("most users within targets")
    a1.set_title("Capacity by dataset (Pelias C3)")
    a1.set_ylim(0, 450)
    for ep, col in zip(EPS, ["#4c72b0", "#55a868", "#8172b2", "#c44e52"], strict=True):
        a2.plot(labels, [ds[k]["runs"]["validate"]["endpoints"].get(ep, {}).get("p(95)") for k in ids], marker="o",
                ms=3, color=col, label=EP_LABEL[ep])  # fmt: skip
    a2.set_ylabel("p95 at 3 users (ms)")
    a2.set_title("Latency at 3 users by dataset")
    a2.legend(fontsize=6.5)
    fig.tight_layout()
    return save(fig, out, "fig_datavol")


def fig_qtype(out: Path) -> str:
    pel, pg = pelias_runs(), pgeo_runs()
    series = [("C3", pel, PELIAS, "Pelias C3"), ("rest-P2", pg, PGEO_SQL, "pgeo pure SQL P2"),
              ("api-P2", pg, PGEO_API, "pgeo FastAPI P2")]  # fmt: skip
    x = np.arange(len(QTYPES))
    w = 0.26
    fig, ax = plt.subplots(figsize=(5.4, 2.8))
    for i, (cid, runs, col, lab) in enumerate(series):
        if cid not in runs:
            continue
        q = runs[cid]["runs"]["validate"]["qtypes"]
        bars = ax.bar(x + (i - 1) * w, [q.get(t, {}).get("p(95)", np.nan) for t in QTYPES], w, color=col, label=lab)
        bar_labels(ax, bars, pad=2, fs=6)
    ax.set_xticks(x, ["Exact", "Typo", "Variant", "Miss"])
    ax.set_ylabel("p95 at 3 users (ms), all endpoints")
    ax.legend(fontsize=6.8)
    return save(fig, out, "fig_qtype")


def fig_compat(out: Path) -> str | None:
    p = ROOT / INPUTS["compat"]["result"]
    if not p.is_file():
        return None
    d = json.loads(p.read_text())
    engines = list(d["engines"])
    cases = d["cases"]
    grid = np.zeros((len(cases), len(engines)))
    for i, c in enumerate(cases):
        for j, e in enumerate(engines):
            ok = c["results"][e] is None
            grid[i, j] = 2 if ok else (1 if e == "pelias" or c["results"]["pelias"] is not None else 0)
    fig, ax = plt.subplots(figsize=(4.6, 0.19 * len(cases) + 0.8))
    cmap = ListedColormap([SLO_RED, "#bdbdbd", GOOD])
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=2, aspect="auto")
    ax.set_yticks(range(len(cases)), [c["case"] for c in cases], fontsize=6.3)
    ax.set_xticks(range(len(engines)), [ENGINE_LABEL.get(e, e).replace(" (PostgREST)", "") for e in engines],
                  fontsize=6.8)  # fmt: skip
    ax.xaxis.tick_top()
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    handles = [Line2D([], [], color=c, lw=7) for c in (GOOD, "#bdbdbd", SLO_RED)]
    ax.legend(handles, ["passes", "differs from contract on Pelias too", "fails"], loc="upper center",
              bbox_to_anchor=(0.5, -0.01), ncols=3, fontsize=6)  # fmt: skip
    return save(fig, out, "fig_compat")


def all_figures(out: Path, snap: dict) -> dict[str, str | None]:
    style.apply()
    return {
        "inventory": fig_inventory(out, snap),
        "accuracy_category": fig_accuracy_category(out),
        "accuracy_quality": fig_accuracy_quality(out),
        "fuzz": fig_fuzz(out),
        "tuning": fig_tuning(out),
        "calibration": fig_calibration(out),
        "latency_3users": fig_latency_3users(out),
        "ramp": fig_ramp(out),
        "endpoint_ramp": fig_endpoint_ramp(out),
        "throughput": fig_throughput(out),
        "frontier": fig_frontier(out),
        "memory": fig_memory(out),
        "cpu": fig_cpu(out),
        "datavol": fig_datavol(out, snap),
        "qtype": fig_qtype(out),
        "compat": fig_compat(out),
    }
