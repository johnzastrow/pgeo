"""Shared figure style: one palette and typography for every figure in the report."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# Engines keep one colour everywhere; configurations use shades of it.
PELIAS = "#1f5f8b"  # deep blue
PGEO_SQL = "#c0392b"  # brick red (pure SQL / PostgREST)
PGEO_API = "#e67e22"  # orange (FastAPI)
PGEO_SVC = "#8e6c3b"  # brown (FastAPI + libpostal service)
NEUTRAL = "#4d4d4d"
GRID = "#d9d9d9"
SLO_RED = "#b03a2e"
GOOD = "#2e7d32"

ENGINE_COLOR = {"pelias": PELIAS, "pgeo-sql": PGEO_SQL, "pgeo-api": PGEO_API, "pgeo-api-svc": PGEO_SVC}
ENGINE_LABEL = {
    "pelias": "Pelias",
    "pgeo-sql": "pgeo pure SQL (PostgREST)",
    "pgeo-api": "pgeo FastAPI",
    "pgeo-api-svc": "pgeo FastAPI + libpostal",
}


def apply() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Noto Sans", "DejaVu Sans"],  # same face as the PDF body text
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.edgecolor": NEUTRAL,
            "axes.linewidth": 0.8,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": NEUTRAL,
            "ytick.color": NEUTRAL,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.05,
            "axes.unicode_minus": False,  # Noto Sans via mathtext lacks U+2212
            "svg.fonttype": "none",  # SVG keeps text as editable text (Inkscape, Illustrator)
        }
    )


def save(fig, out_dir: Path, name: str) -> str:
    """300 dpi PNG for publication (Markdown and PDF report) and SVG with live text for editing."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.png", dpi=300)
    fig.savefig(out_dir / f"{name}.svg")
    plt.close(fig)
    return name


# ---- diagram helpers --------------------------------------------------------------------


def box(ax, x, y, w, h, text, fc="#ffffff", ec=NEUTRAL, fs=6.5, bold=False, tc="#111111", r=0.015):
    ax.add_patch(
        FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.004,rounding_size={r}", fc=fc, ec=ec, lw=0.9)
    )
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=tc,
            fontweight="bold" if bold else "normal", linespacing=1.1)  # fmt: skip


def arrow(ax, x1, y1, x2, y2, text=None, color=NEUTRAL, fs=7, style="-|>", ls="-", rad=0.0):
    ax.add_patch(
        FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=9, color=color, lw=0.9,
                        linestyle=ls, connectionstyle=f"arc3,rad={rad}")  # fmt: skip
    )
    if text:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2, text, fontsize=fs, color=color, ha="center", va="bottom")


def canvas(w_in: float, h_in: float):
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def plain_log_y(ax, ticks=(0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50)):
    """Log y axis with plain decimal tick labels (no mathtext exponents)."""
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

    lo, hi = ax.get_ylim()
    t = [v for v in ticks if lo <= v <= hi] or list(ticks)
    ax.yaxis.set_major_locator(FixedLocator(t))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
