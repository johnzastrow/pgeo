"""Render the report: template + data -> docs/REPORT.md (PNG figures) and a pandoc-ready copy.

Template tokens (report/template.md):
  {{value:name}}              a number or phrase computed from the results (values.py)
  {{table:name|Caption}}      a generated table with its number and caption
  {{figure:name|Caption}}     a figure (report/build/figures/<fig_name>.png) with number and caption
  {{ref:table:name}}          "Table N"   {{ref:figure:name}}  "Figure N"
Figures and tables are numbered in order of appearance; references resolve in a second pass,
so the Markdown and the PDF carry the same numbers.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALUE = re.compile(r"\{\{value:([A-Za-z0-9_]+)\}\}")
TOKEN = re.compile(r"\{\{(value|table|figure|ref):([A-Za-z0-9_:]+)(?:\|(.*?))?\}\}", re.S)


class Renderer:
    def __init__(self, values: dict, tables: dict, figures: dict[str, str | None], fig_dir_rel: str):
        self.values = values
        self.tables = tables
        self.figures = figures
        self.fig_dir_rel = fig_dir_rel
        self.numbers: dict[str, int] = {}
        self.counts = {"table": 0, "figure": 0}
        self.missing: list[str] = []

    def _number(self, kind: str, name: str) -> int:
        key = f"{kind}:{name}"
        if key not in self.numbers:
            self.counts[kind] += 1
            self.numbers[key] = self.counts[kind]
        return self.numbers[key]

    def _value(self, name: str) -> str:
        if name not in self.values:
            self.missing.append(f"value:{name}")
            return f"[missing value {name}]"
        return str(self.values[name])

    def _sub(self, m: re.Match) -> str:
        kind, name, caption = m.group(1), m.group(2), (m.group(3) or "").strip()
        caption = " ".join(caption.split())
        if kind == "value":
            if name not in self.values:
                self.missing.append(f"value:{name}")
                return f"[missing value {name}]"
            return str(self.values[name])
        if kind == "table":
            n = self._number("table", name)
            body = self.tables.get(name)
            if body is None:
                self.missing.append(f"table:{name}")
                body = "_(table not available)_"
            return f"**Table {n}.** {caption}\n\n{body}\n"
        if kind == "figure":
            n = self._number("figure", name)
            fig = self.figures.get(name)
            if not fig:
                self.missing.append(f"figure:{name}")
                return f"_(Figure {n} not available: {caption})_\n"
            # empty alt text: pandoc then adds no automatic caption, so our numbering is the only one
            return f"![]({self.fig_dir_rel}/{fig}.png)\n\n**Figure {n}.** {caption}\n"
        return m.group(0)  # refs: second pass

    def render(self, template: str) -> str:
        # values first, so captions of tables and figures may contain numbers
        valued = VALUE.sub(lambda m: self._value(m.group(1)), template)
        first = TOKEN.sub(lambda m: m.group(0) if m.group(1) == "ref" else self._sub(m), valued)

        def ref(m: re.Match) -> str:
            key = m.group(2)
            if key not in self.numbers:
                self.missing.append(f"ref:{key}")
                return f"[missing {key}]"
            kind = key.split(":", 1)[0]
            return f"{kind.capitalize()} {self.numbers[key]}"

        out = TOKEN.sub(lambda m: ref(m) if m.group(1) == "ref" else m.group(0), first)
        # "Table Table 3": the template wrote the word before a reference that already includes it
        for dup in re.findall(r"\b(Table Table|Figure Figure) \d+", out):
            self.missing.append(f"doubled word before a reference: {dup}")
        # anything that still looks like a token is a template error (e.g. a malformed name)
        for left in re.findall(r"\{\{[^}]*\}\}", out):
            self.missing.append(f"unrendered {left[:60]}")
        return out


def md_table(header: list[str], rows: list[list], align: str | None = None) -> str:
    """Pipe table; align: one of l/r/c per column."""
    align = align or "l" * len(header)
    marks = {"l": ":--", "r": "--:", "c": ":-:"}
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(marks[a] for a in align) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def publish_figures(src: Path, dest: Path) -> None:
    """Copy PNGs (report) and SVGs (editable sources) next to the Markdown report."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in src.glob("*"):
        if p.suffix in (".png", ".svg"):
            shutil.copy2(p, dest / p.name)
