"""Architecture and method diagrams (drawn, not data-driven)."""

from __future__ import annotations

from pathlib import Path

import style
from style import NEUTRAL, PELIAS, PGEO_API, PGEO_SQL, arrow, box, canvas, save

LIGHT_P = "#e3edf5"
LIGHT_G = "#f8e3df"
LIGHT_O = "#fcebd9"
LIGHT_N = "#efefef"


def architecture(out: Path) -> str:
    """Both stacks side by side, from the client to the data."""
    fig, ax = canvas(7.2, 5.0)
    box(ax, 0.17, 0.915, 0.66, 0.06, "Clients: demo page, <pelias-search> widget, LANCER, k6 load tests, accuracy harness",
        fc=LIGHT_N)  # fmt: skip
    box(ax, 0.17, 0.815, 0.66, 0.06, "HTTPS edge: wharf Caddy (TLS) -> nginx on the VM (GET only, path mapping)", fc=LIGHT_N)
    arrow(ax, 0.5, 0.915, 0.5, 0.875)
    arrow(ax, 0.38, 0.815, 0.30, 0.78)
    arrow(ax, 0.62, 0.815, 0.70, 0.78)
    ax.text(0.24, 0.745, "Pelias (reference engine)", color=PELIAS, fontsize=8.5, fontweight="bold", ha="center")
    ax.text(0.76, 0.745, "pgeo (PostgreSQL 18 / PostGIS 3.6)", color=PGEO_SQL, fontsize=8.5, fontweight="bold", ha="center")
    # Pelias services
    box(ax, 0.07, 0.62, 0.34, 0.08, "pelias/api (Node.js)\nquery planning, ranking, JSON", fc=LIGHT_P, ec=PELIAS)
    # gap in the middle of the row for the arrow to Elasticsearch
    svc = [("libpostal\nparser", 0.008), ("placeholder\nadmin names", 0.117), ("pip\nadmin\npolygons", 0.262),
           ("interpolation\nhouse\nnumbers", 0.371)]  # fmt: skip
    for t, x in svc:
        box(ax, x, 0.43, 0.102, 0.10, t, ec=PELIAS, fs=5.8)
        arrow(ax, 0.24, 0.62, x + 0.052, 0.52, color=PELIAS)
    box(ax, 0.05, 0.20, 0.38, 0.14, "Elasticsearch 7.17\n1,649,644 documents\n435 MB index (text + geo queries)",
        fc=LIGHT_P, ec=PELIAS)  # fmt: skip
    arrow(ax, 0.24, 0.62, 0.24, 0.34, color=PELIAS)
    ax.text(0.24, 0.13, "6 containers; 8.3-10.8 GB memory budget", ha="center", fontsize=7, color=PELIAS)
    # pgeo front ends
    box(ax, 0.55, 0.62, 0.19, 0.08, "PostgREST v16.3\nstateless gateway", fc=LIGHT_G, ec=PGEO_SQL)
    box(ax, 0.79, 0.62, 0.19, 0.08, "FastAPI + asyncpg\nrule parser", fc=LIGHT_O, ec=PGEO_API)
    ax.text(0.645, 0.71, "pure SQL", fontsize=6.5, color=PGEO_SQL, ha="center")
    ax.text(0.885, 0.71, "application", fontsize=6.5, color=PGEO_API, ha="center")
    box(ax, 0.53, 0.18, 0.46, 0.36, "", fc=LIGHT_G, ec=PGEO_SQL)
    ax.text(0.76, 0.505, "PostgreSQL 18.6 + PostGIS 3.6.4 (674 MB database)", fontsize=6.8, ha="center",
            fontweight="bold")  # fmt: skip
    inner = [("geocode_api.v1_*\nPelias JSON, validation", 0.55, 0.35), ("geocode.search, reverse,\nautocomplete (PL/pgSQL)", 0.77, 0.35),
             ("pgeo.feature, 906,101 rows\ntrigram, FTS, GiST, B-tree", 0.55, 0.21), ("pgeo.admin WOF polygons;\nUSPS + county tables", 0.77, 0.21)]  # fmt: skip
    for t, x, y in inner:
        box(ax, x, y, 0.20, 0.11, t, fs=6)
    arrow(ax, 0.645, 0.62, 0.65, 0.46, color=PGEO_SQL)
    arrow(ax, 0.885, 0.62, 0.87, 0.46, color=PGEO_API)
    ax.text(0.76, 0.13, "2-3 containers; 1.6-3.3 GB memory budget", ha="center", fontsize=7, color=PGEO_SQL)
    ax.text(0.5, 0.04, "pgeo extensions: postgis, pg_trgm, unaccent, fuzzystrmatch (contrib); optional pgsql-postal, pg_search",
            ha="center", fontsize=6.5, color=NEUTRAL, style="italic")  # fmt: skip
    return save(fig, out, "fig_architecture")


def data_pipeline(out: Path) -> str:
    fig, ax = canvas(7.2, 3.8)
    sources = [
        ("OpenStreetMap\nGeofabrik PBF, 87 MB", 0.87), ("OpenAddresses\nME statewide, 223 MB", 0.72),
        ("Who's On First\nadmin polygons", 0.57), ("USGS GNIS\nnames, 3.6 MB", 0.40),
        ("Census ZCTA\ngazetteer, 0.9 MB", 0.25), ("Overture Maps\nplaces parquet, 203 MB", 0.10),
    ]  # fmt: skip
    for t, y in sources:
        box(ax, 0.01, y - 0.055, 0.19, 0.11, t, fc=LIGHT_N)
    box(ax, 0.29, 0.06, 0.17, 0.24, "pelias-prep (Python)\nclip to Maine,\nnormalize to\nPelias CSV")
    for _, y in sources[3:]:
        arrow(ax, 0.20, y, 0.29, 0.18)
    box(ax, 0.58, 0.68, 0.20, 0.18, "Pelias importers\n(osm, oa, wof, csv,\ninterpolation)", fc=LIGHT_P, ec=PELIAS)
    box(ax, 0.83, 0.69, 0.16, 0.16, "Elasticsearch\nindex + snapshot\n(~15 min build)", fc=LIGHT_P, ec=PELIAS)
    box(ax, 0.58, 0.32, 0.20, 0.22, "pgeo-load build\nDuckDB, ogr2ogr, COPY,\npoint-in-polygon,\ndedupe, indexes",
        fc=LIGHT_G, ec=PGEO_SQL)  # fmt: skip
    box(ax, 0.83, 0.34, 0.16, 0.18, "schema pgeo\natomic swap,\nVACUUM ANALYZE\n(~13 min build)", fc=LIGHT_G, ec=PGEO_SQL)
    for _, y in sources[:3]:
        arrow(ax, 0.20, y, 0.58, 0.77, color=PELIAS)
        arrow(ax, 0.20, y, 0.58, 0.43, color=PGEO_SQL, ls="--")
    arrow(ax, 0.46, 0.24, 0.58, 0.70, color=PELIAS)
    arrow(ax, 0.46, 0.16, 0.58, 0.36, color=PGEO_SQL, ls="--")
    arrow(ax, 0.78, 0.77, 0.83, 0.77, color=PELIAS)
    arrow(ax, 0.78, 0.43, 0.83, 0.43, color=PGEO_SQL)
    ax.text(0.99, 0.96, "same inputs, two engines (solid: Pelias, dashed: pgeo)", ha="right", fontsize=6.8,
            style="italic", color=NEUTRAL)  # fmt: skip
    return save(fig, out, "fig_data_pipeline")


def query_pipeline(out: Path) -> str:
    """pgeo forward search, with the tuned steps marked."""
    fig, ax = canvas(7.2, 2.5)
    steps = [
        ("1 Normalize", "expand abbrev.:\nst -> street,\nmt -> mount;\ndrop trailing\nstate word"),
        ("2 Parse", "rule parser:\nnumber, street,\ntown, ZIP,\nname part"),
        ("3 Candidates", "trigram names;\nstreets + house\nnumbers;\ninterpolation;\nZIP"),
        ("4 Anchor +\nfilter", "focus, circle,\nrect or town\nlocation; layers,\nsources, gid,\ncategories"),
        ("5 Score", "name similarity\nx town\nagreement (by\nname and by\ndistance)"),
        ("6 Dedupe +\nambiguity", "one result per\nlabel; ties\nbetween places\nlower confidence"),
        ("7 Pelias\nJSON", "GeoJSON\nenvelope with\nhierarchy gids"),
    ]
    w, gap = 0.125, 0.018
    for i, (title, body) in enumerate(steps):
        x = 0.01 + i * (w + gap)
        box(ax, x, 0.06, w, 0.76, "", fc=LIGHT_G, ec=PGEO_SQL)
        ax.text(x + w / 2, 0.74, title, ha="center", va="top", fontsize=7, fontweight="bold", linespacing=1.05)
        ax.text(x + w / 2, 0.50, body, ha="center", va="top", fontsize=6, color="#222222", linespacing=1.15)
        if i < len(steps) - 1:
            arrow(ax, x + w + 0.002, 0.44, x + w + gap - 0.002, 0.44, color=PGEO_SQL)
    ax.text(0.5, 0.93, "Every step runs inside PostgreSQL; the FastAPI and PostgREST front ends pass parameters in "
            "and JSON out", ha="center", fontsize=7, style="italic", color=NEUTRAL)  # fmt: skip
    return save(fig, out, "fig_query_pipeline")


def test_harness(out: Path) -> str:
    fig, ax = canvas(7.2, 2.5)
    box(ax, 0.01, 0.20, 0.25, 0.66, "k6 2.2.0 load generator\n(pinned to core 5)\n\nper user: 60% type-ahead,\n15% search, 10% structured,\n"
        "15% reverse; 3-8 s think", fc=LIGHT_N)  # fmt: skip
    box(ax, 0.35, 0.20, 0.29, 0.66, "engine under test\n(pinned to cores 0..N-1)\n\nmemory limit per service,\nno swap\n\n"
        "1, 2, 4 vCPU or unconstrained", fc=LIGHT_P, ec=PELIAS)  # fmt: skip
    box(ax, 0.73, 0.56, 0.26, 0.30, "sampler: docker stats every 2 s\nCPU, memory, OOM kills,\nrestarts")
    box(ax, 0.73, 0.20, 0.26, 0.30, "ramp 1, 2, 3, 4, 6 ... 512 users;\n15 s warm-up + 60 s steady;\nstop at > 5% errors,\n"
        "p95 > 5x target or a crash")  # fmt: skip
    arrow(ax, 0.26, 0.53, 0.35, 0.53, text="HTTP", color=NEUTRAL)
    arrow(ax, 0.64, 0.70, 0.73, 0.70, color=NEUTRAL)
    arrow(ax, 0.64, 0.36, 0.73, 0.36, color=NEUTRAL)
    ax.text(0.5, 0.05, "Host: AMD Ryzen 5 3600 (6 cores / 12 threads), 31 GB RAM, NVMe. The engine and the load "
            "generator never share a physical core.", ha="center", fontsize=6.8, style="italic", color=NEUTRAL)  # fmt: skip
    return save(fig, out, "fig_test_harness")


def all_diagrams(out: Path) -> list[str]:
    style.apply()
    return [architecture(out), data_pipeline(out), query_pipeline(out), test_harness(out)]


if __name__ == "__main__":
    print(all_diagrams(Path(__file__).parent / "build" / "figures"))
