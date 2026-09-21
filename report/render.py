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
# The argument may itself contain a token (a {{ref:...}} inside a callout or caption), so it
# is matched as "text or a nested token", not as anything up to the first "}}".
TOKEN = re.compile(r"\{\{(value|table|figure|ref|callout|cite):([A-Za-z0-9_:]+)"
                   r"(?:\|((?:[^{}]|\{\{[^{}]*\}\})*?))?\}\}", re.S)
# Callouts: coloured boxes in the PDF, block quotes in the Markdown.
CALLOUT = {"key": ("Key result", "keybox"), "impact": ("What this means", "impactbox"),
           "caution": ("Caution", "cautionbox")}
# A callout becomes a raw LaTeX environment, which pandoc passes through untouched, so its text
# has to be escaped here: an unescaped "%" comments out the rest of the line and "$60 ... $580"
# would be typeset as mathematics.
TEX_ESCAPE = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
              "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
              "^": r"\textasciicircum{}"}


BARE_URL = re.compile(r"https?://\S+")


def _linkify(text: str) -> str:
    """Wrap bare URLs in <> so pandoc emits \\url{}.

    Bibliography entries are mostly URLs, and as plain text LaTeX cannot break them: the longest
    ran two inches into the margin. As autolinks they become \\url{}, which xurl breaks anywhere.
    """

    def wrap(m: re.Match[str]) -> str:
        url = m.group(0).rstrip(".,;")
        return f"<{url}>{m.group(0)[len(url):]}"

    return BARE_URL.sub(wrap, text)


def tex_text(s: str) -> str:
    """Escape prose for a raw LaTeX block, leaving any {{token}} for the reference pass."""
    def esc(part: str) -> str:
        out = "".join(TEX_ESCAPE.get(c, c) for c in part)
        return re.sub(r'"([^"]*)"', r"``\1''", out)  # pandoc does this for the body text

    return "".join(p if p.startswith("{{") else esc(p) for p in re.split(r"(\{\{[^{}]*\}\})", s))


class Renderer:
    def __init__(self, values: dict, tables: dict, figures: dict[str, str | None], fig_dir_rel: str,
                 target: str = "md", references: dict[str, str] | None = None):
        self.values = values
        self.tables = tables
        self.figures = figures
        self.fig_dir_rel = fig_dir_rel
        self.target = target  # "md": block quotes; "pdf": coloured LaTeX boxes
        self.references = references or {}
        self.cited: list[str] = []          # citation keys, in order of first appearance
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

    @staticmethod
    def _caption(label: str, caption: str) -> str:
        """"**Figure 3. Short title.** The rest, in normal weight." The short title is the
        caption's first sentence, which is how every caption in this report is written."""
        m = re.match(r"(.+?[.:?])(\s+)(.*)", caption, re.S)
        if not m:
            return f"**{label} {caption}**" if caption else f"**{label}**"
        title, _, body = m.groups()
        return f"**{label} {title}** {body}".rstrip()

    def _sub(self, m: re.Match) -> str:
        kind, name, caption = m.group(1), m.group(2), (m.group(3) or "").strip()
        caption = " ".join(caption.split())
        if kind == "callout":
            label, env = CALLOUT.get(name, CALLOUT["key"])
            if self.target == "pdf":
                return f"\n\\begin{{{env}}}\n{tex_text(caption)}\n\\end{{{env}}}\n"
            return f"\n> **{label}.** {caption}\n"
        if kind == "cite":
            nums = []
            for k in [x for x in name.split(":") if x]:
                if k not in self.references:
                    self.missing.append(f"cite:{k}")
                    continue
                if k not in self.cited:
                    self.cited.append(k)
                nums.append(str(self.cited.index(k) + 1))
            return f"[{', '.join(nums)}]" if nums else "[?]"
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
            else:
                # A static ```table``` block is lifted out of the template before the token pass
                # and inserted here, so its own {{value}} and {{cite}} tokens have not been seen
                # yet. Resolve them now; {{ref}} still waits for the numbering pass.
                body = VALUE.sub(lambda mm: self._value(mm.group(1)), body)
                body = TOKEN.sub(lambda mm: self._sub(mm) if mm.group(1) == "cite" else mm.group(0), body)
            # enough for the caption, the header row and two rows: a table that starts lower
            # than this gets pushed, rather than printing its header twice at the break
            head = "\\needspace{11\\baselineskip}\n\n" if self.target == "pdf" else ""
            return f"{head}{self._caption(f'Table {n}.', caption)}\n\n{body}\n"
        if kind == "figure":
            n = self._number("figure", name)
            fig = self.figures.get(name)
            if not fig:
                self.missing.append(f"figure:{name}")
                return f"_(Figure {n} not available: {caption})_\n"
            # empty alt text: pandoc then adds no automatic caption, so our numbering is the only one
            return f"![]({self.fig_dir_rel}/{fig}.png)\n\n{self._caption(f'Figure {n}.', caption)}\n"
        return m.group(0)  # refs: second pass

    @staticmethod
    def _toc(text: str) -> str:
        """Contents for the Markdown output; the PDF and the Word document get pandoc's own."""
        out = []
        for line in text.split("\n"):
            m = re.match(r"^(#{2,3}) (.+)$", line)
            if not m or m.group(2).startswith("Contents"):
                continue
            depth, title = len(m.group(1)) - 2, m.group(2).strip()
            anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
            out.append(f"{'  ' * depth}- [{title}](#{anchor})")
        return "\n".join(out)

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
        if "{{bibliography}}" in out:
            items = [f"{i + 1}. {_linkify(self.references[k])}" for i, k in enumerate(self.cited)]
            out = out.replace("{{bibliography}}", "\n".join(items) or "_(nothing cited)_")
        if "{{toc}}" in out:
            out = out.replace("{{toc}}", self._toc(out) if self.target == "md" else "\\tableofcontents")
        # A heading needs a blank line before it or Markdown reads it as text: "... a service
        # dies. #### 2.5.1 Caching" reached a published PDF that way.
        for m in re.finditer(r"(?m)^(.*\S.*)\n(#{2,6} .+)$", out):
            self.missing.append(f"heading glued to the previous line: {m.group(2)[:40]}")
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
