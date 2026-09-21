#!/usr/bin/env bash
# Build one short sample PDF per formatting theme, so a theme can be chosen by looking at it.
#
#   scripts/report_themes.sh            build every theme into docs/theme_samples/
#   scripts/report_themes.sh b-serif-pagella   just that one
#
# Each sample is the same few pages of the real report - a title, a dense table, a wide table, a
# figure and a callout - because those are the places a font choice actually shows.
# To adopt one: copy report/pdf/themes/<name>.yaml over report/pdf/metadata.yaml (keeping the
# title, subtitle and author lines) and rebuild with scripts/build_report.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="docs/theme_samples"
SRC="report/build/theme_sample.md"
mkdir -p "$OUT"

[[ -f report/build/report_pdf.md ]] || { echo "run scripts/build_report.sh first" >&2; exit 1; }

# The sample: the executive summary, one results section with a figure, and two tables.
python3 - "$SRC" <<'PY'
import pathlib, re, sys

full = pathlib.Path("report/build/report_pdf.md").read_text()


def section(start: str, end: str) -> str:
    a = full.find(start)
    b = full.find(end, a + 1)
    return full[a:b] if a >= 0 and b > a else ""


parts = [
    "## Executive summary\n\n"
    + section("This study built and compared", "**Capacity.**")[:1500],
    section("### 3.1 Accuracy", "#### 3.1.1")[:3000],
    section("### 3.4 Capacity under load", "#### 3.4.2")[:2500],
]
pathlib.Path(sys.argv[1]).write_text("\n\n".join(p for p in parts if p))
PY

themes=("${@:-}")
[[ -z "${themes[0]}" ]] && mapfile -t themes < <(cd report/pdf/themes && ls *.yaml | sed 's/\.yaml$//')

for t in "${themes[@]}"; do
  printf '%-18s ' "$t"
  tex="report/build/theme-$t.tex"
  pandoc "$SRC" -o "$tex" \
    --from 'markdown+pipe_tables+implicit_figures+raw_tex-yaml_metadata_block-tex_math_dollars-tex_math_single_backslash' \
    --metadata-file "report/pdf/themes/$t.yaml" \
    --include-in-header report/pdf/header.tex \
    --lua-filter report/pdf/breakcode.lua --standalone
  python3 report/pdf/gridtables.py "$tex" >/dev/null
  (cd docs && xelatex -interaction=nonstopmode -output-directory="../report/build" "../$tex" >/dev/null 2>&1) || true
  if [[ -f "report/build/theme-$t.pdf" ]]; then
    cp "report/build/theme-$t.pdf" "$OUT/$t.pdf"
    echo "-> $OUT/$t.pdf  ($(pdfinfo "$OUT/$t.pdf" | awk '/Pages/{print $2}') pages)"
  else
    echo "FAILED (see report/build/theme-$t.log)"
  fi
done

echo
echo "Compare them side by side, then copy the winner's font lines into report/pdf/metadata.yaml."
