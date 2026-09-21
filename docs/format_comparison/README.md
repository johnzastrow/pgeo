# Three ways to produce the PDF, compared

Built from the same source on 2026-09-20, same content, same page size. Look at page 17-19 in
each: a dense configuration table with wrapping cells is where the routes differ most.

| Route | How | Pages | Tables | Verdict |
|---|---|---|---|---|
| **A — LaTeX** (`route-a-latex.pdf`) | pandoc → `.tex` → `gridtables.py` → xelatex | **59** | Full grid, alternating row shading, controlled column widths, code strings wrapped at punctuation | **Keep this.** The tables are the reason |
| **B — ODT first** (`route-b-odt-to-pdf.pdf`) | pandoc → `.odt` → LibreOffice | 65 | **No borders at all**; columns sized by LibreOffice, wide cells crammed | Worst of the three for tables |
| **C — Word first** (`route-c-word-to-pdf.pdf`) | pandoc → `.docx` (9pt table style) → LibreOffice | 68 | Horizontal rules only, no vertical lines or shading; 9pt table text is readable | Good enough for Word; not better than A |

## What this settles

The suggestion was to generate OpenDocument first and render the PDF from it, on the grounds that
ODT is more native on Linux. Tested, that loses rather than gains: **pandoc's default ODT output
draws no table borders**, so route B gives up the grid lines, the shading and the column control
that the LaTeX route was changed to provide, and it is six pages longer. Route C keeps horizontal
rules but no vertical ones, and is nine pages longer than A.

The separate complaint — that the table font in the Word document was too large and the wrapping
hard to read — was real and is fixed independently of the PDF question:
`report/pdf/reference.docx` sets the table and compact styles to 9pt, and `docs/REPORT.docx` now
uses it. That change needed no route change at all.

Reproduce:

```bash
scripts/build_report.sh                                   # route A (and the .docx)
pandoc report/build/report_pdf.md -o r.odt --from markdown+pipe_tables \
  --resource-path docs --metadata-file report/pdf/metadata.yaml --toc
soffice --headless --convert-to pdf r.odt                 # route B
soffice --headless --convert-to pdf docs/REPORT.docx      # route C
```
