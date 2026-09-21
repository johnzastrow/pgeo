"""The bill of materials in report Section 2.1.1 against the repository it describes.

The section itemises what pgeo is built from: line counts per file, pinned dependency versions,
and the extensions the SQL actually creates. All of that drifts the moment someone edits the code,
and a stale inventory is worse than none - so every number in those tables is checked here.

    uv run --project pgeo pytest report/tests/test_bom.py -q
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "report/template.md"
PGEO = ROOT / "pgeo"


def table(name: str) -> list[list[str]]:
    """The rows of a ```table <name> block, as lists of stripped cells."""
    src = TEMPLATE.read_text()
    m = re.search(rf"```table {re.escape(name)}\n(.*?)```", src, re.DOTALL)
    assert m, f"no ```table {name} block in report/template.md"
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- "):
            continue  # header separator
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows[1:]  # drop the header row


def count_lines(rel: str) -> int:
    """Lines in a file, or in every .py under a directory."""
    target = PGEO / rel
    if target.is_dir():
        return sum(len(p.read_text().splitlines()) for p in sorted(target.glob("*.py")))
    return len(target.read_text().splitlines())


OWN_CODE = table("pgeo_own_code")


# ---- the code inventory ---------------------------------------------------------------------


@pytest.mark.parametrize("row", OWN_CODE, ids=[r[0].strip("`") for r in OWN_CODE])
def test_each_stated_line_count_is_the_real_one(row):
    rel, stated = row[0].strip("`"), row[1]
    assert (PGEO / rel).exists(), f"{rel} is in the report but not in the repository"
    assert count_lines(rel) == int(stated.replace(",", "")), (
        f"{rel}: report says {stated} lines, the file has {count_lines(rel)}"
    )


def test_the_sql_total_in_the_prose_matches_the_table():
    sql = sum(int(r[1]) for r in OWN_CODE if r[0].strip("`").startswith("sql/"))
    assert f"{sql:,} lines of SQL" in TEMPLATE.read_text(), f"the SQL files total {sql:,} lines"


def test_the_python_total_in_the_prose_matches_the_table():
    py = sum(int(r[1]) for r in OWN_CODE if r[0].strip("`").startswith("src/"))
    assert f"{py:,} lines of Python" in TEMPLATE.read_text(), f"the Python totals {py:,} lines"


def test_every_sql_file_in_the_repository_is_listed():
    """A new SQL file must appear in the inventory, or the report understates the geocoder."""
    listed = {r[0].strip("`") for r in OWN_CODE}
    for path in sorted((PGEO / "sql").glob("*.sql")):
        assert f"sql/{path.name}" in listed, f"sql/{path.name} is not in the Section 2.1.1 table"


# ---- the run-time inventory -----------------------------------------------------------------


def test_the_listed_extensions_are_the_ones_the_sql_creates():
    """The run-time table claims three contrib extensions plus PostGIS and nothing else."""
    created = set(
        re.findall(
            r"CREATE EXTENSION IF NOT EXISTS (\w+)",
            "\n".join(p.read_text() for p in (PGEO / "sql").glob("*.sql")),
        )
    )
    listed = {c[0].strip("`") for c in table("pgeo_runtime_bom")}
    assert created == {"postgis", "pg_trgm", "unaccent", "fuzzystrmatch"}, created
    for ext in created - {"postgis"}:
        assert ext in listed, f"the SQL creates {ext} but Section 2.1.1 does not list it"


def test_the_postgrest_version_matches_the_pinned_image():
    defaults = (ROOT / "infra/ansible/group_vars/pelias/main.yml").read_text()
    pinned = re.search(r"postgrest/postgrest:(v[\d.]+)", defaults)
    assert pinned, "no pinned PostgREST image to check against"
    row = next(r for r in table("pgeo_runtime_bom") if r[0] == "PostgREST")
    assert row[1] == pinned.group(1), f"report says {row[1]}, the deployment pins {pinned.group(1)}"


@pytest.mark.parametrize("pkg", ["fastapi", "uvicorn", "asyncpg", "pydantic", "httpx"])
def test_the_front_end_versions_match_pyproject(pkg):
    pins = dict(
        re.findall(r'"([a-z]+)==([\d.]+)"', (PGEO / "pyproject.toml").read_text())
    )
    rows = {r[0].lower().strip("`"): r[1] for r in table("pgeo_fastapi_bom")}
    assert rows[pkg] == pins[pkg], f"{pkg}: report says {rows[pkg]}, pyproject pins {pins[pkg]}"


def test_duckdb_is_listed_at_its_pinned_version():
    pins = dict(re.findall(r'"([a-z]+)==([\d.]+)"', (PGEO / "pyproject.toml").read_text()))
    row = next(r for r in table("pgeo_build_bom") if r[0] == "DuckDB")
    assert row[1] == pins["duckdb"]


def test_the_vendored_web_libraries_match_the_lock():
    """The demo entries are only useful if they say what is actually vendored."""
    lock = (ROOT / "web/vendor/VENDOR.lock").read_text()
    versions = dict(re.findall(r"^(@?[\w/-]+)@([\d.]+) ", lock, re.MULTILINE))
    rows = table("pgeo_aux_bom")
    for label, key in (
        ("MapLibre GL JS", "maplibre-gl"),
        ("PMTiles", "pmtiles"),
        ("Protomaps basemaps", "@protomaps/basemaps"),
    ):
        row = next(r for r in rows if r[0] == label)
        assert row[1] == versions[key], f"{label}: report {row[1]}, vendored {versions[key]}"
