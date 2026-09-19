"""Build the study report: figures, tables, docs/REPORT.md and docs/REPORT.pdf.

    uv run --project report python report/build_report.py [--no-pdf] [--collect]

Normally run through scripts/build_report.sh, which can also refresh the live inputs first.
Fails if the template references a value, table or figure that the data does not provide.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import diagrams
import figures
import values
from render import Renderer, publish_figures

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "report"
BUILD = HERE / "build"
FIG_BUILD = BUILD / "figures"
DOCS = ROOT / "docs"
FIG_PUBLISH = DOCS / "report_figures"  # PNG (used) + SVG (editable sources)
OUT_MD = DOCS / "REPORT.md"
OUT_PDF = DOCS / "REPORT.pdf"
STATIC_TABLE = re.compile(r"^```table ([a-z0-9_]+)\n(.*?)\n```\n?", re.S | re.M)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)  # noqa: S603, S607
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--collect", action="store_true", help="refresh report/data/snapshot.json from live systems")
    ap.add_argument("--draft", action="store_true", help="build even if the template has PENDING markers")
    a = ap.parse_args()

    if a.collect:
        subprocess.run([sys.executable, str(HERE / "collect.py")], check=True)  # noqa: S603
    snap = json.loads((ROOT / values.INPUTS["snapshot"]["file"]).read_text())

    print("report: figures")
    figs = dict(zip(["architecture", "data_pipeline", "query_pipeline", "test_harness"],
                    diagrams.all_diagrams(FIG_BUILD), strict=True))  # fmt: skip
    figs |= figures.all_figures(FIG_BUILD, snap)

    print("report: values and tables")
    vals, tabs = values.build_all(snap)
    head = git("rev-parse", "--short", "HEAD")
    vals |= {
        "build_date": datetime.now().strftime("%Y-%m-%d"),
        "git_commit": head + (" (with uncommitted changes)" if git("status", "--porcelain") else ""),
        "project_version": (ROOT / "VERSION").read_text().strip(),
        "load_runs": ", ".join(Path(d).name for k in ("pelias", "pelias_datasets", "pgeo") for d in values.INPUTS["load"][k]),
        "snapshot_date": snap.get("pgeo", {}).get("captured", "-")[:10],
    }

    print("report: markdown")
    template = (HERE / "template.md").read_text()
    # Static tables written in the template as ```table <name> ... ``` blocks (feature matrix,
    # recommendations): lifted out here and numbered like the generated ones.
    for m in list(STATIC_TABLE.finditer(template)):
        tabs.setdefault(m.group(1), m.group(2).strip())
    template = STATIC_TABLE.sub("", template)
    # Editorial markers must not survive into a release
    if "<!-- PENDING" in template and not a.draft:
        print("report: template still has PENDING markers (use --draft to build anyway)", file=sys.stderr)
        return 1
    r = Renderer(vals, tabs, figs, fig_dir_rel=FIG_PUBLISH.name, target="md")
    md = r.render(template)
    if r.missing:
        # a draft may reference results that a running experiment has not produced yet
        where = "warning (draft)" if a.draft else "unresolved references"
        print(f"report: {where}:\n  " + "\n  ".join(sorted(set(r.missing))), file=sys.stderr)
        if not a.draft:
            return 1
    publish_figures(FIG_BUILD, FIG_PUBLISH)
    OUT_MD.write_text(md)
    print(f"report: wrote {OUT_MD.relative_to(ROOT)} ({r.counts['figure']} figures, {r.counts['table']} tables)")

    if not a.no_pdf:
        print("report: pdf")
        # The PDF takes its title block from pdf/metadata.yaml: drop the Markdown's own title lines
        pdf_md = BUILD / "report_pdf.md"
        rp = Renderer(vals, tabs, figs, fig_dir_rel=FIG_PUBLISH.name, target="pdf")
        pdf_text = rp.render(template)
        lines = pdf_text.splitlines()
        pdf_md.write_text("\n".join(lines[3:]) if lines[0].startswith("# ") else pdf_text)
        cmd = [
            "pandoc", str(pdf_md), "-o", str(OUT_PDF),
            "--from", "markdown+pipe_tables+implicit_figures+raw_tex-yaml_metadata_block-tex_math_dollars-tex_math_single_backslash",
            "--pdf-engine", "xelatex",
            "--metadata-file", str(HERE / "pdf" / "metadata.yaml"),
            "--include-in-header", str(HERE / "pdf" / "header.tex"),
            "--lua-filter", str(HERE / "pdf" / "breakcode.lua"),
            "--resource-path", str(DOCS),
            "--toc", "--toc-depth", "2",
        ]  # fmt: skip
        res = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
        if res.returncode != 0:
            print(res.stderr[-3000:], file=sys.stderr)
            return 1
        print(f"report: wrote {OUT_PDF.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
