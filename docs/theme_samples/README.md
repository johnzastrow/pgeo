# Formatting theme samples

Same few pages of the report — a dense table, a wide table, a figure and a callout — set six ways.
Rebuild with `scripts/report_themes.sh`; definitions are in `report/pdf/themes/`.

| Sample | Body font | Size | Character |
|---|---|---|---|
| `a-sans-current.pdf` | Noto Sans | 10pt | What the report uses today |
| `b-serif-pagella.pdf` | TeX Gyre Pagella, sans headings | 10pt | Warm book serif; the most "published" look |
| `c-serif-termes.pdf` | TeX Gyre Termes, sans headings | 10pt | Times-like; the densest, most journal-conventional |
| `d-noto-serif.pdf` | Noto Serif, Noto Sans headings | 10pt | Serif body that matches the existing figures exactly |
| `e-sans-compact.pdf` | Noto Sans | 9pt | Tighter leading and margins; most content per page |
| `f-sans-airy.pdf` | Noto Sans | 11pt | Generous leading; easiest on screen, longest document |

The figures are rendered in Noto Sans whichever theme is chosen, so `a`, `e`, `f` and `d` keep
text and charts consistent; `b` and `c` deliberately contrast a serif body against sans charts,
which is normal in journals.

To adopt one, copy its font and spacing lines into `report/pdf/metadata.yaml` and rebuild.
