"""The report renderer (report/render.py).

Every bug fixed here has corrupted a published document at least once, so each has a test
named after what the reader saw. Run: uv run --project report pytest report/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render import Renderer, md_table, tex_text  # noqa: E402

VALUES = {"acc": "95.8%", "cost": "$60", "n": "1,560", "plain": "x"}
TABLES = {"cap": "| a |\n|:--|\n| 1 |"}
FIGURES = {"ramp": "fig_ramp", "missing_fig": None}


def render(template: str, target: str = "md") -> tuple[str, list[str]]:
    r = Renderer(dict(VALUES), dict(TABLES), dict(FIGURES), fig_dir_rel="figs", target=target)
    return r.render(template), r.missing


# ---- values ---------------------------------------------------------------------------


def test_substitutes_a_value():
    out, missing = render("accuracy is {{value:acc}}.")
    assert out == "accuracy is 95.8%."
    assert not missing


def test_reports_an_unknown_value_instead_of_failing_silently():
    out, missing = render("{{value:nope}}")
    assert "missing value nope" in out
    assert missing == ["value:nope"]


# ---- numbering and references ---------------------------------------------------------


def test_numbers_tables_and_figures_in_order_of_appearance():
    out, _ = render("{{figure:ramp|First}}\n\n{{table:cap|Second}}\n\n{{figure:ramp|Again}}")
    assert "**Figure 1. First**" in out
    assert "**Table 1. Second**" in out
    assert "**Figure 1. Again**" in out, "the same figure keeps its number"


def test_a_reference_resolves_to_the_number_assigned_later():
    out, missing = render("See {{ref:table:cap}}.\n\n{{table:cap|The table}}")
    assert "See Table 1." in out
    assert not missing


def test_reports_a_reference_to_something_never_defined():
    _, missing = render("See {{ref:table:ghost}}.")
    assert missing == ["ref:table:ghost"]


def test_flags_the_word_table_written_before_a_reference():
    """"Table Table 3" reached a published draft."""
    _, missing = render("{{table:cap|C}}\n\nSee Table {{ref:table:cap}}.")
    assert any("doubled word" in m for m in missing)


def test_flags_anything_that_still_looks_like_a_token():
    _, missing = render("{{value:plain}} and {{bogus:thing}}")
    assert any("unrendered" in m for m in missing)


# ---- callouts -------------------------------------------------------------------------


def test_callout_is_a_block_quote_in_markdown():
    out, _ = render("{{callout:key|It works.}}")
    assert "> **Key result.** It works." in out


def test_callout_is_a_coloured_box_in_the_pdf():
    out, _ = render("{{callout:impact|It works.}}", target="pdf")
    assert "\\begin{impactbox}" in out and "\\end{impactbox}" in out


def test_unknown_callout_kind_falls_back_to_key_rather_than_vanishing():
    out, _ = render("{{callout:banana|Text.}}")
    assert "Key result" in out and "Text." in out


def test_callout_percent_is_escaped_for_latex():
    """An unescaped % commented out the rest of the line: the first Key result box in the
    published PDF ended mid-sentence at "95.8"."""
    out, _ = render("{{callout:key|pgeo answers {{value:acc}} correctly, which is good.}}", target="pdf")
    assert "95.8\\%" in out
    assert "which is good." in out


def test_callout_dollar_is_escaped_so_it_is_not_typeset_as_mathematics():
    """"$60 and about $580" came out as italic mathematics in the published PDF."""
    out, _ = render("{{callout:impact|about {{value:cost}} and about $580 a year}}", target="pdf")
    assert "\\$60" in out and "\\$580" in out


def test_a_reference_inside_a_callout_survives_and_resolves():
    """The token pattern stopped at the first "}}", leaving ").}}" in the text."""
    out, missing = render("{{table:cap|C}}\n\n{{callout:key|See {{ref:table:cap}} for the numbers.}}",
                          target="pdf")  # fmt: skip
    assert "See Table 1 for the numbers." in out
    assert "}}" not in out
    assert not missing


def test_markdown_callout_keeps_the_reference_too():
    out, _ = render("{{table:cap|C}}\n\n{{callout:key|See {{ref:table:cap}}.}}")
    assert "See Table 1." in out


# ---- LaTeX escaping -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [("100%", "100\\%"), ("$5", "\\$5"), ("a&b", "a\\&b"), ("x_y", "x\\_y"), ("#1", "\\#1"),
     ("{x}", "\\{x\\}"), ("~", "\\textasciitilde{}"), ("^", "\\textasciicircum{}")],  # fmt: skip
)
def test_tex_text_escapes_every_special_character(raw, want):
    assert want in tex_text(raw)


def test_tex_text_leaves_tokens_alone_for_the_reference_pass():
    assert "{{ref:table:cap}}" in tex_text("see {{ref:table:cap}} now")


def test_tex_text_turns_straight_quotes_into_typographic_ones():
    assert tex_text('a "quoted" word') == "a ``quoted'' word"


# ---- tables and figures ---------------------------------------------------------------


def test_md_table_renders_a_pipe_table_with_the_requested_alignment():
    out = md_table(["a", "b"], [[1, 2]], "lr")
    assert out.splitlines()[1] == "|:--|--:|"


def test_md_table_renders_none_as_an_empty_cell():
    assert "|  |" in md_table(["a", "b"], [[1, None]])


def test_md_table_row_widths_match_the_header():
    out = md_table(["a", "b", "c"], [[1, 2, 3]], "lll")
    counts = {line.count("|") for line in out.splitlines()}
    assert counts == {4}, "every row must have the same number of cell separators"


def test_a_figure_renders_an_image_and_a_numbered_caption():
    out, _ = render("{{figure:ramp|Users over time.}}")
    assert "![](figs/fig_ramp.png)" in out
    assert "**Figure 1. Users over time.**" in out


def test_a_figure_with_no_file_is_reported_not_silently_dropped():
    out, missing = render("{{figure:missing_fig|Gone.}}")
    assert "not available" in out
    assert missing == ["figure:missing_fig"]


def test_a_table_with_no_data_is_reported():
    out, missing = render("{{table:ghost|Gone.}}")
    assert "not available" in out
    assert missing == ["table:ghost"]


def test_captions_collapse_whitespace_so_wrapped_source_reads_as_one_line():
    out, _ = render("{{table:cap|A caption\n    wrapped over lines.}}")
    assert "**Table 1. A caption wrapped over lines.**" in out


def test_a_caption_bolds_its_number_and_short_title_only():
    """"**Figure 1. Short title.** The explanation stays normal weight."""
    out, _ = render("{{figure:ramp|Users over time. The ramp stops when a target is missed.}}")
    assert "**Figure 1. Users over time.** The ramp stops when a target is missed." in out


def test_a_caption_with_no_sentence_break_is_bolded_whole():
    out, _ = render("{{table:cap|Records by layer}}")
    assert "**Table 1. Records by layer**" in out


def test_pdf_reserves_space_so_a_caption_is_not_orphaned_from_its_table():
    """Table 9's caption was published at the foot of the page before its table."""
    out, _ = render("{{table:cap|C}}", target="pdf")
    assert "\\needspace" in out


def test_flags_a_heading_glued_to_the_previous_line():
    """"... a service dies. #### 2.5.1 Caching" reached a published PDF: Markdown needs a blank
    line before a heading or it is just text."""
    _, missing = render("Some prose ends here.\n#### 2.5.1 Caching\n\nMore.")
    assert any("glued" in m for m in missing)


def test_a_properly_separated_heading_is_not_flagged():
    _, missing = render("Some prose ends here.\n\n#### 2.5.1 Caching\n\nMore.")
    assert not any("glued" in m for m in missing)


def test_markdown_has_no_latex_in_it():
    out, _ = render("{{table:cap|C}}\n\n{{callout:key|Text.}}")
    assert "\\needspace" not in out and "\\begin{" not in out
