"""Numbers and tables for the report, computed from saved results (report/inputs.toml)."""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path

import figures as F
from render import md_table

ROOT = Path(__file__).resolve().parents[1]
INPUTS = tomllib.loads((ROOT / "report" / "inputs.toml").read_text())
SLO = F.SLO
EPS = F.EPS


def pct(x: float, d: int = 1) -> str:
    return f"{x:.{d}f}%"


def gb(nbytes: float | None, d: int = 2) -> str:
    return "-" if nbytes is None else f"{nbytes / 1024**3:.{d}f} GB"


def mb(nbytes: float | None) -> str:
    return "-" if nbytes is None else f"{nbytes / 1024**2:,.0f} MB"


def first_fail(run: dict) -> tuple[int | None, list[str]]:
    for st in run["runs"]["ramp"]:
        if not st["pass"]:
            return st["vus"], [ep for ep, v in st["endpoints"].items() if v.get("p(95)", 0) > SLO[ep]] or (
                ["errors"] if st["error_rate"] >= 0.01 else [])
    return None, []


def peak_mem_mb(run: dict) -> int:
    return round(sum(v.get("mem_max_mb", 0) for k, v in run["runs"]["validate"]["resources"].items() if k != "_stack"))


def rps_at(run: dict, users: int | None) -> float | None:
    return next((s["req_rate"] for s in run["runs"]["ramp"] if s["vus"] == users), None)


# ---- accuracy ---------------------------------------------------------------------------------


def accuracy_values(v: dict, t: dict) -> None:
    acc = F.load_accuracy("main")
    fz = F.load_accuracy("fuzz")
    for e, d in acc.items():
        rows = d["results"]
        key = e.replace("-", "_")
        v[f"acc_{key}"] = pct(F.rate(rows))
        g = F.by(rows, "qtype")
        for q in F.QTYPES:
            v[f"acc_{key}_{q}"] = pct(F.rate(g[q]), 0)
        k = F.by([r for r in rows if r["qtype"] == "exact"], "kind")
        for kind in ("address", "town", "lake_summit", "venue", "zip", "reverse_address"):
            v[f"acc_{key}_exact_{kind}"] = pct(F.rate(k[kind]), 0)
        s = d["summary"]["ALL"]
        v[f"conf_right_{key}"] = f"{s['conf_when_right']:.2f}" if s.get("conf_when_right") is not None else "-"
        v[f"conf_wrong_{key}"] = f"{s['conf_when_wrong']:.2f}" if s.get("conf_when_wrong") is not None else "-"
        v[f"p50_{key}"] = f"{s['p50_ms']:.0f} ms"
        v[f"n_wrong_{key}"] = f"{sum(not r['correct'] for r in rows):,}"
    n_cases = len(next(iter(acc.values()))["results"]) if acc else 0
    v["n_cases"] = f"{n_cases:,}"
    engines = [e for e in ("pelias", "pgeo-sql", "pgeo-api", "pgeo-api-svc") if e in acc]
    label = {"pelias": "Pelias", "pgeo-sql": "pgeo pure SQL", "pgeo-api": "pgeo FastAPI (rule parser)",
             "pgeo-api-svc": "pgeo FastAPI (libpostal service)"}  # fmt: skip
    rows = []
    for e in engines:
        s = acc[e]["summary"]["ALL"]
        g = F.by(acc[e]["results"], "qtype")
        rows.append([label[e], pct(100 * s["correct"]), pct(100 * s["hit1"]), pct(100 * s["hit5"]),
                     *(pct(F.rate(g[q]), 0) for q in F.QTYPES), f"{s['conf_when_right']:.2f} / {s['conf_when_wrong']:.2f}",
                     f"{s['p50_ms']:.0f}"])  # fmt: skip
    t["accuracy_overall"] = md_table(
        ["Engine", "Correct", "Hit@1", "Hit@5", "Exact", "Typo", "Variant", "Miss", "Conf. right / wrong", "p50 ms"],
        rows, "lrrrrrrrcr")
    kinds = ["address", "town", "lake_summit", "venue", "zip", "reverse_address", "miss"]
    rows = []
    for kind in kinds:
        row = [F.KIND_LABEL[kind]]
        n = 0
        for e in engines:
            rs = [r for r in acc[e]["results"] if r["kind"] == kind]
            n = len(rs)
            row.append(pct(F.rate(rs), 0))
        rows.append([row[0], f"{n:,}", *row[1:]])
    t["accuracy_category"] = md_table(["Category", "Cases", *(label[e] for e in engines)], rows, "lr" + "r" * len(engines))
    # fuzz
    levels = [f"F{i}" for i in range(6)]
    rows = []
    for e, d in fz.items():
        g = F.by(d["results"], "level")
        rows.append([label[e], *(pct(F.rate(g[lv]), 0) for lv in levels)])
        for lv in levels:
            v[f"fuzz_{e.replace('-', '_')}_{lv}"] = pct(F.rate(g[lv]), 0)
    t["fuzz_results"] = md_table(["Engine", *levels], rows, "l" + "r" * 6)
    # parity: per category, Pelias vs pgeo pure SQL, with the gap in points
    if "pelias" in acc and "pgeo-sql" in acc:
        rows = []
        for kind in kinds:
            a_ = F.rate([r for r in acc["pelias"]["results"] if r["kind"] == kind])
            b_ = F.rate([r for r in acc["pgeo-sql"]["results"] if r["kind"] == kind])
            gap = b_ - a_
            # a gap that rounds to zero point is a tie, whichever side it falls on
            lead = "pgeo" if gap >= 0.5 else ("Pelias" if gap <= -0.5 else "tie")
            # one decimal: rounding to whole points showed "100% vs 100%" with a -0 gap
            d = 0 if abs(gap) >= 1 else 1
            rows.append([F.KIND_LABEL[kind], pct(a_, d), pct(b_, d), f"{gap:+.{d}f}", lead])
        a_ = F.rate(acc["pelias"]["results"])
        b_ = F.rate(acc["pgeo-sql"]["results"])
        rows.append(["All cases", pct(a_), pct(b_), f"{b_ - a_:+.1f}", "pgeo" if b_ > a_ else "Pelias"])
        t["parity_accuracy"] = md_table(["Category", "Pelias", "pgeo", "Gap (points)", "Ahead"], rows, "lrrrl")
    # remaining failures of the main pgeo engine
    if "pgeo-sql" in acc:
        bad = [r for r in acc["pgeo-sql"]["results"] if not r["correct"]]
        grp = defaultdict(int)
        for r in bad:
            grp[f"{F.KIND_LABEL.get(r['kind'], r['kind'])} ({r['qtype']})"] += 1
        t["pgeo_failures"] = md_table(["Category (query quality)", "Failures"],
                                      [[k, n] for k, n in sorted(grp.items(), key=lambda x: -x[1])], "lr")
        v["pgeo_failures_total"] = f"{len(bad)}"


# ---- test sets and corpus -------------------------------------------------------------------


def testset_values(v: dict, t: dict, snap: dict) -> None:
    ts = snap["test_sets"]
    comp = defaultdict(lambda: defaultdict(int))
    for key, n in ts["accuracy_composition"].items():
        ep, kind, q = key.split("|")
        comp[(ep, kind)][q] += n
    rows = []
    for (ep, kind), qs in sorted(comp.items(), key=lambda x: (x[0][0], x[0][1])):
        rows.append([ep, F.KIND_LABEL.get(kind, kind), *(qs.get(q, 0) or "" for q in F.QTYPES), sum(qs.values())])
    t["testset"] = md_table(["Endpoint", "Category", "Exact", "Typo", "Variant", "Miss", "Total"], rows, "llrrrrr")
    v["n_fuzz"] = f"{ts['fuzz_cases']:,}"
    c = ts["load_corpus"]
    t["corpus"] = md_table(["Corpus pool", "Items", "Used for"], [
        ["addresses", f"{c.get('addresses', 0):,}", "type-ahead and search, exact form"],
        ["places", f"{c.get('places', 0):,}", "towns, lakes, summits"],
        ["venues", f"{c.get('venues', 0):,}", "businesses and landmarks"],
        ["zips", f"{c.get('zips', 0):,}", "ZIP code searches"],
        ["structured", f"{c.get('structured', 0):,}", "structured search field sets"],
        ["typos", f"{c.get('typos', 0):,}", "one or two character errors"],
        ["variants", f"{c.get('variants', 0):,}", "off-name variants (abbreviations, missing town)"],
        ["misses", f"{c.get('misses', 0):,}", "places that do not exist in Maine"],
        ["oa_points / random_points", f"{c.get('oa_points', 0):,} / {c.get('random_points', 0):,}", "reverse geocoding on land"],
        ["miss_points", f"{c.get('miss_points', 0):,}", "reverse geocoding offshore (misses)"],
        ["foci", f"{c.get('foci', 0):,}", "focus points (map centers) for type-ahead"],
    ], "lrl")


# ---- data inventory ---------------------------------------------------------------------------


def data_values(v: dict, t: dict, snap: dict) -> None:
    pel, geo = snap["pelias"], snap["pgeo"]
    by_src = lambda rows: {s: sum(r["n"] for r in rows if r["source"] == s) for s in {r["source"] for r in rows}}  # noqa: E731
    by_layer = lambda rows: {s: sum(r["n"] for r in rows if r["layer"] == s) for s in {r["layer"] for r in rows}}  # noqa: E731
    ps, gs = by_src(pel["by_layer_source"]), by_src(geo["by_layer_source"])
    pl, gl = by_layer(pel["by_layer_source"]), by_layer(geo["by_layer_source"])
    v["pelias_docs"] = f"{pel['docs']:,}"
    v["pelias_index"] = mb(pel["index_bytes"])
    v["pgeo_features"] = f"{sum(gl.values()):,}"
    # tables with their indexes: what the schema actually holds. pg_database_size is larger while
    # the schema the atomic swap replaced has not been dropped and vacuumed away.
    v["pgeo_db"] = mb(sum(geo.get("table_bytes", {}).values()) or geo["db_bytes"])
    v["pgeo_db_file"] = mb(geo["db_bytes"])
    v["pelias_addresses"] = f"{pl.get('address', 0):,}"
    v["pgeo_addresses"] = f"{gl.get('address', 0):,}"
    v["address_ratio"] = f"{pl.get('address', 0) / max(gl.get('address', 1), 1):.2f}"
    raw = {r["source"]: r for r in snap["raw_data"]}
    names = {"openaddresses": "OpenAddresses", "openstreetmap": "OpenStreetMap", "whosonfirst": "Who's On First",
             "gnis": "USGS GNIS", "zcta": "Census ZCTA", "overture": "Overture Maps"}  # fmt: skip
    what = {"openaddresses": "address points (Maine statewide E911 feed)",
            "openstreetmap": "addresses, streets, venues (Geofabrik extract)",
            "whosonfirst": "admin polygons: towns, counties, ZIPs, neighbourhoods",
            "gnis": "named features: lakes, summits, streams, populated places",
            "zcta": "ZIP code centroids", "overture": "places (businesses, landmarks), 2026-08-19 release"}  # fmt: skip
    rows = []
    for s in ["openaddresses", "openstreetmap", "overture", "gnis", "whosonfirst", "zcta"]:
        r = raw.get(names[s], {})
        rows.append([names[s], what[s], mb(r.get("bytes")), f"{ps.get(s, 0):,}", f"{gs.get(s, 0):,}"])
    # the interpolation database is a Pelias-only input: no documents, but it is data to build and ship
    interp = raw.get("TIGER / OA interpolation", {})
    rows.append(["TIGER / OpenAddresses", "street ranges for the Pelias interpolation service",
                 mb(interp.get("bytes")), "(not indexed)", "-"])
    rows.append(["Total", "", "", f"{sum(ps.values()):,}", f"{sum(gs.values()):,}"])
    t["sources"] = md_table(["Source", "Content", "Raw input", "Pelias documents", "pgeo features"], rows, "llrrr")
    layers = ["address", "venue", "street", "locality", "localadmin", "neighbourhood", "postalcode", "county", "region",
              "country"]  # fmt: skip
    t["layers"] = md_table(["Layer", "Pelias", "pgeo", "pgeo / Pelias"],
                           [[x, f"{pl.get(x, 0):,}", f"{gl.get(x, 0):,}",
                             f"{gl.get(x, 0) / pl[x]:.2f}" if pl.get(x) else "-"] for x in layers], "lrrr")  # fmt: skip
    # pgeo tables and indexes
    tb = geo.get("table_bytes", {})
    ib = geo.get("index_bytes", {})
    t["pgeo_storage"] = md_table(["Table", "Size (incl. indexes)"], [[k, mb(b)] for k, b in tb.items()], "lr")
    t["pgeo_indexes"] = md_table(["Index", "Size"], [[k, mb(b)] for k, b in list(ib.items())[:12]], "lr")


# ---- load -----------------------------------------------------------------------------------------


def load_values(v: dict, t: dict) -> None:
    pel, pg, ds = F.pelias_runs(), F.pgeo_runs(), F.dataset_runs()
    before = F.load_runs(INPUTS["load"].get("pgeo_before", []))

    def cfg_rows(runs: dict, ids: list[str], pgeo: bool) -> list[list]:
        rows = []
        for cid in ids:
            if cid not in runs:
                continue
            r = runs[cid]
            c = r["config"]
            fu, eps = first_fail(r)
            rows.append([
                cid, c.get("cpus") or "all", "-" if r["budget_gb"] is None else f"{r['budget_gb']} GB",
                (f"sb {c['sb']}, {c['conns']} conn." if pgeo else f"heap {c['heap']}, {c['workers']} worker(s)"),
                f"{r['limit_users']}", r.get("breaking_users") or "not reached",
                ", ".join(eps) + (f" at {fu}" if fu else "") if eps else "-",
                f"{rps_at(r, r['limit_users']) or 0:.0f}", f"{peak_mem_mb(r) / 1024:.1f} GB",
            ])
        return rows

    hdr = ["Config", "vCPU", "Budget", "Settings", "Users within SLO", "Broken at", "First over target", "req/s at limit",
           "Memory at 3 users"]  # fmt: skip
    t["pelias_limits"] = md_table(hdr, cfg_rows(pel, ["C1", "C1s", "C2", "C3", "C4", "C4a", "M0"], False), "lrrlrrlrr")
    pg_ids = [f"{e}-{c}" for e in ("rest", "api", "api-svc") for c in ("Pmin", "P1", "P2", "P4", "PM")]
    t["pgeo_limits"] = md_table(hdr, cfg_rows(pg, pg_ids, True), "lrrlrrlrr")
    if before:
        rows = []
        for cid in [f"{e}-{c}" for e in ("rest", "api") for c in ("Pmin", "P1", "P2", "P4", "PM")]:
            if cid in before and cid in pg:
                b, a = before[cid], pg[cid]
                rows.append([cid, b["limit_users"], a["limit_users"],
                             f"{b['runs']['validate']['endpoints']['reverse']['p(95)']:.0f}",
                             f"{a['runs']['validate']['endpoints']['reverse']['p(95)']:.0f}"])
        t["reverse_fix"] = md_table(["Config", "Users within SLO, before", "after", "Reverse p95 at 3 users (ms), before",
                                     "after"], rows, "lrrrr")  # fmt: skip
    # latency at 3 users
    rows = []
    for runs, ids in ((pel, ["C1", "C2", "C3", "C4", "M0"]), (pg, pg_ids)):
        for cid in ids:
            if cid not in runs:
                continue
            e = runs[cid]["runs"]["validate"]["endpoints"]
            rows.append([cid, *(f"{e.get(ep, {}).get('med', float('nan')):.0f} / {e.get(ep, {}).get('p(95)', float('nan')):.0f}"
                                for ep in EPS)])  # fmt: skip
    t["latency3"] = md_table(["Config", *(f"{F.EP_LABEL[ep]} p50 / p95 ms" for ep in EPS)], rows, "lrrrr")
    t["slo"] = md_table(["Endpoint", "p95 target", "Why"], [
        ["Autocomplete", "250 ms", "type-ahead must keep up with typing"],
        ["Search", "750 ms", "a full search after pressing Enter"],
        ["Structured search", "750 ms", "form-based search"],
        ["Reverse", "400 ms", "map click; feels immediate"],
        ["All", "errors < 1%", "no restarts or out-of-memory kills during a run"],
    ], "lrl")
    # data volume
    rows = []
    for cid in sorted(ds, key=lambda k: ds[k]["dataset"]):
        r = ds[cid]
        e = r["runs"]["validate"]["endpoints"]
        rows.append([r["dataset"], f"{r['docs']:,}", f"{r['index_mb']:,} MB", f"{r['limit_users']}",
                     r.get("breaking_users") or "not reached",
                     *(f"{e.get(ep, {}).get('p(95)', float('nan')):.0f}" for ep in EPS)])  # fmt: skip
    t["datavol"] = md_table(["Dataset", "Documents", "Index", "Users within SLO", "Broken at",
                             *(f"{F.EP_LABEL[ep]} p95" for ep in EPS)], rows, "lrrrrrrrr")  # fmt: skip
    # headline numbers
    for cid in ("C1", "C2", "C3", "C4", "C4a", "M0"):
        if cid in pel:
            v[f"lim_{cid}"] = str(pel[cid]["limit_users"])
            v[f"budget_{cid}"] = f"{pel[cid]['budget_gb']} GB" if pel[cid]["budget_gb"] else "unlimited"
    for cid in pg:
        v[f"lim_{cid.replace('-', '_')}"] = str(pg[cid]["limit_users"])
        v[f"budget_{cid.replace('-', '_')}"] = f"{pg[cid]['budget_gb']} GB" if pg[cid]["budget_gb"] else "unlimited"
    for cid in before:
        v[f"before_lim_{cid.replace('-', '_')}"] = str(before[cid]["limit_users"])
    # throughput ratio per CPU
    ratios = []
    for p_id, g_ids in (("C1", ["rest-P1", "api-P1"]), ("C2", ["rest-P2", "api-P2"]), ("C4", ["rest-P4", "api-P4"])):
        for g in g_ids:
            if p_id in pel and g in pg and pg[g]["limit_users"]:
                ratios.append(pel[p_id]["limit_users"] / pg[g]["limit_users"])
    if ratios:
        v["ratio_min"] = f"{min(ratios):.0f}"
        v["ratio_max"] = f"{max(ratios):.0f}"
    # parity: users within targets per CPU size, Pelias vs pgeo (both front ends)
    rows = []
    # at one vCPU the smaller Pmin budget reaches the same user count as P1, so quote that one
    per_size = {1: ["Pmin", "P1"], 2: ["P2"], 4: ["P4"]}
    for size, p_id, _g in ((1, "C1", "P1"), (2, "C2", "P2"), (4, "C4", "P4")):
        if p_id not in pel:
            continue
        pl = pel[p_id]["limit_users"]
        cells = [f"{size} vCPU", f"{pl} ({pel[p_id]['budget_gb']} GB)"]
        chosen = {}
        for fe in ("rest", "api"):
            cands = [pg[f"{fe}-{c}"] for c in per_size[size] if f"{fe}-{c}" in pg]
            r = min(cands, key=lambda x: (-x["limit_users"], x["budget_gb"])) if cands else None
            chosen[fe] = r
            cells.append("-" if r is None else f"{r['limit_users']} ({r['budget_gb']} GB)")
        best = max((r["limit_users"] for r in chosen.values() if r), default=0)
        cells.append(f"{pl / best:.1f}x" if best else "-")
        rows.append(cells)
    t["parity_capacity"] = md_table(["CPU", "Pelias users (budget)", "pgeo pure SQL", "pgeo FastAPI",
                                     "Pelias / best pgeo"], rows, "lrrrr")  # fmt: skip
    ds_sorted = sorted(ds.values(), key=lambda r: r["dataset"])
    for r in ds_sorted:
        v[f"ds_{r['dataset']}"] = str(r["limit_users"])


# ---- compatibility -------------------------------------------------------------------------------


def compat_values(v: dict, t: dict) -> None:
    p = ROOT / INPUTS["compat"]["result"]
    if not p.is_file():
        return
    d = json.loads(p.read_text())
    engines = list(d["engines"])
    rows = []
    for c in d["cases"]:
        cells = []
        for e in engines:
            res = c["results"][e]
            cells.append("pass" if res is None else ("differs" if e == "pelias" else "FAIL"))
        q = "&".join(f"{k}={val}" for k, val in c["params"].items())
        # full request: the PDF breaks long code spans at punctuation (report/pdf/breakcode.lua)
        rows.append([c["case"], f"`/v1/{c['path']}?{q}`", *cells])
    t["compat"] = md_table(["Case", "Request", "Pelias", "pgeo FastAPI", "pgeo pure SQL"], rows, "llccc")
    v["compat_cases"] = str(len(d["cases"]))
    v["compat_failures"] = str(d["failures"])


def resource_values(v: dict, t: dict) -> None:
    """Sizing guide (smallest tested configuration per load) and build requirements."""
    pel, pg = F.pelias_runs(), F.pgeo_runs()
    fams = {
        "Pelias": {k: r for k, r in pel.items() if r["budget_gb"]},
        "pgeo pure SQL": {k: r for k, r in pg.items() if k.startswith("rest-") and r["budget_gb"]},
        "pgeo FastAPI": {k: r for k, r in pg.items() if k.startswith("api-") and not k.startswith("api-svc")
                         and r["budget_gb"]},
    }  # fmt: skip
    rows = []
    for users in (3, 10, 25, 50, 100, 200, 400):
        row = [str(users)]
        for runs in fams.values():
            ok = [r for r in runs.values() if (r["limit_users"] or 0) >= users]
            if ok:
                best = min(ok, key=lambda r: (r["budget_gb"], r["config"].get("cpus") or 99))
                row.append(f"{best['config'].get('cpus')} vCPU, {best['budget_gb']} GB ({best['id']})")
            else:
                row.append("not reached in tests")
        rows.append(row)
    t["sizing"] = md_table(["Concurrent users", *fams], rows, "llll")
    # build requirements from tests/build/measure_build.py output (latest per engine)
    rows = []
    for engine in ("pelias", "pgeo"):
        files = sorted((ROOT / "data" / "buildstats").glob(f"{engine}-*.json"))
        files = [f for f in files if json.loads(f.read_text()).get("exit_code") == 0]
        if not files:
            rows.append([engine, "pending measurement", "", "", "", ""])
            continue
        d = json.loads(files[-1].read_text())
        s = d["summary"]
        disk = sum(b for b in d["disk_after_bytes"].values() if b)
        rows.append(["Pelias" if engine == "pelias" else "pgeo", f"{s['wall_s'] / 60:.0f} min",
                     f"{s['peak_mem_mb'] / 1024:.1f} GB", f"{s['avg_cpu_cores']:.1f} / {s['peak_cpu_cores']:.1f}",
                     gb(disk, 1), d["started"][:10]])  # fmt: skip
    t["build_resources"] = md_table(["Engine", "Wall time", "Peak memory", "CPU cores (avg / peak)",
                                     "Disk after build", "Measured"], rows, "lrrrrl")  # fmt: skip


def floor_values(v: dict, t: dict) -> None:
    """Minimum server for 3 users: the search path, the floor per engine, and its VM confirmation."""
    ld = INPUTS["load"]
    fdir = ROOT / ld.get("floor", "")
    label = {"pgeo": "pgeo (pure SQL)", "pelias": "Pelias"}
    rows, path_rows = [], []
    for engine in ("pgeo", "pelias"):
        f = fdir / f"floor-{engine}.json"
        if not ld.get("floor") or not f.is_file():
            continue
        d = json.loads(f.read_text())
        r = d.get("result")
        if r:
            v[f"floor_{engine}_vcpu"] = f"{r['vcpu']:g}"
            v[f"floor_{engine}_gb"] = f"{r['memory_gb']:.2f} GB"
            containers = sum(val for k, val in r["config"].items() if k != "cpus")
            v[f"floor_{engine}_ct_gb"] = f"{containers:.2f} GB"
        for i, p in enumerate(d["path"]):
            c = p["config"]
            size = ", ".join(f"{k} {val:g}" for k, val in c.items())
            path_rows.append([label[engine], i, size, f"{p.get('total_gb') or 0:.2f} GB", "pass" if p["pass"] else "fail",
                              p["why"]])  # fmt: skip
    if path_rows:
        t["floor_path"] = md_table(["Engine", "Trial", "Configuration (vCPU quota; memory limits in GB)", "Total",
                                    "3 users", "Result"], path_rows, "lrlrcl")  # fmt: skip
    # temporary-VM confirmation (3-user validation + ramp on a real VM of the floor size)
    vm_runs = F.load_runs(ld.get("floor_vm", []))
    for rid, r in sorted(vm_runs.items()):
        c = r["config"]
        eng = r.get("engine", "")
        val = r["runs"]["validate"]
        rows.append([label.get(eng, eng), f"{c.get('cpus')} vCPU (limit {c.get('cpulimit'):g})",
                     f"{c.get('memory_gb'):g} GB", "pass" if val["pass"] else "fail",
                     f"{val['worst_p95_ratio']:.2f}", str(r["limit_users"]), r.get("breaking_users") or "not reached"])
        v[f"floorvm_{eng}_limit"] = str(r["limit_users"])
        v[f"floorvm_{eng}_size"] = f"{c.get('cpulimit'):g} vCPU, {c.get('memory_gb'):g} GB"
    if rows:
        t["floor_vm"] = md_table(["Engine", "VM CPU", "VM memory", "3 users", "Worst p95 / target at 3",
                                  "Users within targets", "Broken at"], rows, "lllcrrr")  # fmt: skip
    vm = F.load_runs(ld.get("vm120", []))
    rows = []
    for rid, r in sorted(vm.items()):
        val = r["runs"]["validate"]
        rows.append([r.get("engine", rid), "pass" if val["pass"] else "fail",
                     *(f"{val['endpoints'].get(ep, {}).get('p(95)', float('nan')):.0f}" for ep in EPS),
                     str(r["limit_users"]), r.get("breaking_users") or "not reached"])  # fmt: skip
        v[f"vm120_{r.get('engine', rid)}_limit"] = str(r["limit_users"])
    if rows:
        t["vm120"] = md_table(["Engine", "3 users", *(f"{F.EP_LABEL[ep]} p95" for ep in EPS), "Users within targets",
                               "Broken at"], rows, "lcrrrrrr")  # fmt: skip


def build_all(snap: dict) -> tuple[dict, dict]:
    v: dict = {}
    t: dict = {}
    accuracy_values(v, t)
    testset_values(v, t, snap)
    data_values(v, t, snap)
    load_values(v, t)
    compat_values(v, t)
    resource_values(v, t)
    floor_values(v, t)
    host = snap["host"]
    v["host_cpu"] = host["cpu"]
    v["host_threads"] = str(host["logical_cpus"])
    v["host_mem"] = f"{host['memory_gb']:.0f} GB"
    v["pg_version"] = snap["pgeo"].get("postgres") or "-"
    v["postgis_version"] = snap["pgeo"].get("postgis") or "-"
    v["es_version"] = snap["pelias"].get("elasticsearch") or "-"
    v["pgeo_version"] = snap["pgeo"].get("engine_version") or "-"
    b = (snap["pgeo"].get("build") or {}).get("timings_s") or {}
    v["pgeo_build_min"] = f"{sum(b.values()) / 60:.0f} min" if b else "-"
    t["pgeo_build_steps"] = md_table(["Build step", "Seconds"], [[k, f"{s:,.0f}"] for k, s in b.items()], "lr") if b else None
    return v, t


if __name__ == "__main__":
    snap = json.loads((ROOT / INPUTS["snapshot"]["file"]).read_text())
    vals, tabs = build_all(snap)
    print(len(vals), "values;", len(tabs), "tables")
    print({k: vals[k] for k in list(vals)[:25]})
