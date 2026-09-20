"""Give the report's tables grid lines and roomier cells.

pandoc's LaTeX writer emits booktabs tables: three horizontal rules and no vertical ones. That
is conventional for typesetting but hard to read for the wide, dense tables in this report, where
a reader tracks a value across nine columns. This rewrites each longtable to draw a rule between
every row and every column.

It is a post-processing step rather than a filter because the table LaTeX is produced by pandoc's
writer, which no filter can reach.

    python3 report/pdf/gridtables.py build/report.tex
"""

from __future__ import annotations

import pathlib
import re
import sys

# Lines inside a longtable that are structure, not data: a rule after them would double up.
STRUCTURAL = ("\\toprule", "\\midrule", "\\bottomrule", "\\endhead", "\\endfirsthead",
              "\\endlastfoot", "\\endfoot", "\\noalign", "\\caption")  # fmt: skip


def rule_columns(spec: str) -> str:
    r"""Put a vertical rule before every column and after the last, and pay for them.

    pandoc sizes each column as a fraction of (\linewidth - N\tabcolsep). Vertical rules are
    drawn outside that budget, so without subtracting them every table grows by one rule width
    per column and the widest ones run into the margin.
    """
    spec = spec.replace("@{}", "")
    spec = re.sub(r"(?m)^(\s*)(>\{|[lrc]\b)", r"\1|\2", spec)
    columns = spec.count("|")
    spec = spec.replace("\\linewidth -", f"\\linewidth - {columns + 1}\\arrayrulewidth -")
    # pandoc's budget assumes its outer @{} suppress the padding either side of the table. An
    # outer rule separates them from the columns, so those two gaps come back: pay for them.
    spec = re.sub(r"(\d+)\\tabcolsep", lambda m: f"{int(m.group(1)) + 2}\\tabcolsep", spec)
    # Keep pandoc's @{} at both ends: without them LaTeX pads outside the first and last rule,
    # which is 2\tabcolsep the column widths were never given.
    return "@{}" + spec.rstrip() + "|@{}"


def grid_one(body: str) -> str:
    """Add \\hline after each data row of one longtable body."""
    out, in_row = [], False
    for line in body.split("\n"):
        stripped = line.strip()
        out.append(line)
        if stripped.startswith(STRUCTURAL):
            in_row = False
            continue
        # a data row ends with \\ (possibly followed by nothing); multi-line cells end there too
        if stripped.endswith("\\\\"):
            out.append("\\hline")
            in_row = False
        elif stripped:
            in_row = True
    del in_row
    return "\n".join(out)


START = "\\begin{longtable}[]{"
END = "\\end{longtable}"


def matching_brace(text: str, start: int) -> int:
    """Index just past the brace that closes the one at start-1 (the column spec)."""
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return i


def convert(tex: str) -> tuple[str, int]:
    out, i, count = [], 0, 0
    while True:
        a = tex.find(START, i)
        if a < 0:
            out.append(tex[i:])
            break
        spec_start = a + len(START)
        spec_end = matching_brace(tex, spec_start)          # just past the spec's closing brace
        body_end = tex.find(END, spec_end)
        if body_end < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:a])
        out.append(START + rule_columns(tex[spec_start:spec_end - 1]) + "}")
        out.append(grid_one(tex[spec_end:body_end]))
        out.append(END)
        i = body_end + len(END)
        count += 1
    return "".join(out), count


def main() -> int:
    path = pathlib.Path(sys.argv[1])
    tex, n = convert(path.read_text())
    path.write_text(tex)
    print(f"   grid lines applied to {n} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
