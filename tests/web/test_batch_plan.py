"""The batch page's planner (web/js/batch.js planJobs) on the upload shapes people really send.

Run through node, which the demo page's own code needs nowhere else; skipped when node is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

SCRIPT = """
import { planJobs } from '%s';
const [text, states, coarse] = JSON.parse(process.argv[1]);
const p = planJobs(text, states, coarse);
console.log(JSON.stringify({ mode: p.mode, delimiter: p.delimiter, columns: p.columns,
  jobs: p.jobs.map((j) => ({ input: j.input, skip: j.skip || '' })) }));
"""


def plan(text: str, states: list[str] | None = None, coarse: bool = True) -> dict:
    src = (ROOT / "web" / "js" / "batch.js").as_uri()
    out = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT % src, json.dumps([text, states or [], coarse])],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    return json.loads(out.stdout)


PIPE = (
    '"RecID"|"Company Name"|"City"|"State"|"Address Line 1"\n'
    '1|"Acme"|"Portland"|"ME"|"360 Spring Street"\n'
    '2|"Beta"|"Sacramento"|"CA"|"3626 Fair Oaks Blvd. Suite 100"\n'
    '3|"Gamma"|"Auburn"|"ME"|"3157 PO Box"\n'
    '4|"Delta"|"Concord"|"NH"|"10101 Global Way, Suite 101"\n'
    '5|"Epsilon"|"Boston"|"MA"|""\n'
    '6|"Zeta"|"Freeport"|"ME"|"31 Main St, Freeport, ME"\n'
    '7|"Eta"|""|""|"Homestead Rd"\n'
)


def test_an_address_split_across_columns_is_put_back_together():
    p = plan(PIPE, ["ME", "NH"])
    assert p["mode"] == "forward"
    assert p["delimiter"] == "|"
    by = [j["input"] for j in p["jobs"]]
    assert by[0] == "360 Spring Street, Portland, ME"
    # a unit tail is left off the query, and the town and state still go on
    assert by[3] == "10101 Global Way, Concord, NH"


def test_a_town_or_state_the_street_already_names_is_not_added_twice():
    p = plan(PIPE, ["ME", "NH"])
    assert p["jobs"][5]["input"] == "31 Main St, Freeport, ME"
    # "ME" inside "Homestead" is not the state ME
    assert p["jobs"][6]["input"] == "Homestead Rd"


def test_rows_that_cannot_be_answered_say_why_and_are_kept():
    p = plan(PIPE, ["ME", "NH"], coarse=False)
    assert len(p["jobs"]) == 7
    assert p["jobs"][1]["skip"] == "outside coverage (CA)"
    assert p["jobs"][2]["skip"] == "PO box"


def test_with_the_worldwide_tier_on_a_state_outside_the_build_is_searched():
    p = plan(PIPE, ["ME", "NH"], coarse=True)
    assert p["jobs"][1]["skip"] == ""
    assert p["jobs"][1]["input"] == "3626 Fair Oaks Blvd., Sacramento, CA"
    assert p["jobs"][2]["skip"] == "PO box"  # a PO box has no place to find, whichever tier


def test_a_row_with_no_street_is_geocoded_to_the_place_it_does_name():
    p = plan(PIPE, ["ME", "NH"])
    assert p["jobs"][4]["input"] == "Boston, MA"  # town and state, no street


def test_a_row_with_only_a_state_is_looked_up_by_the_states_full_name():
    text = '"Name"|"City"|"State"|"Address Line 1"\n"Acme"|""|"CA"|""\n"Beta"|""|""|""\n'
    p = plan(text, ["ME", "NH"])
    assert p["jobs"][0]["input"] == "California"
    assert p["jobs"][1]["input"] == ""  # nothing at all: reported as "no address"


def test_without_a_state_list_nothing_is_excluded_by_state():
    p = plan(PIPE, coarse=False)
    assert p["jobs"][1]["skip"] == ""
