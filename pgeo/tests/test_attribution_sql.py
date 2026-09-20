"""/v1/attribution (pgeo/sql/050_api.sql): the data-licence page Pelias also serves.

Run against the local database; skipped when it is not reachable. The HTTP behaviour (the
media-type domain, and the edge asking for text/html on the client's behalf) is covered by
the compatibility contract, tests/compat/compat_test.py.
"""

from __future__ import annotations

import asyncio
import re

import pytest

asyncpg = pytest.importorskip("asyncpg")

from pgeo.settings import Settings  # noqa: E402


def q(sql: str, *args):
    async def run():
        con = await asyncpg.connect(Settings.load().dsn, timeout=5)
        try:
            return await con.fetchval(sql, *args)
        finally:
            await con.close()

    try:
        return asyncio.run(run())
    except (OSError, asyncpg.PostgresError, RuntimeError) as e:
        if isinstance(e, asyncpg.PostgresError) and not isinstance(e, asyncpg.InvalidPasswordError):
            raise
        pytest.skip(f"database not reachable: {e}")


@pytest.fixture(scope="module")
def page() -> str:
    return q("SELECT geocode_api.v1_attribution()")


def test_is_a_complete_html_document(page):
    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert page.rstrip().endswith("</html>")
    for tag in ("html", "head", "title", "body"):
        assert f"<{tag}" in page and f"</{tag}>" in page, f"unbalanced <{tag}>"


@pytest.mark.parametrize(
    "source",
    ["OpenStreetMap", "OpenAddresses", "Who&#39;s On First", "GNIS", "Census", "Overture"],
)
def test_names_every_data_source(page, source):
    """Every source the build loads has to be attributed: that is what the page is for."""
    assert source in page


@pytest.mark.parametrize("licence", ["ODbL", "CC-BY 4.0", "public domain", "CDLA-Permissive-2.0"])
def test_names_every_licence(page, licence):
    assert licence in page


def test_reports_the_engine_version(page):
    version = q("SELECT geocode.engine_version()")
    assert version in page


def test_escapes_the_interpolated_version():
    """The version is the only value interpolated into the page, so the deployed function must
    escape it. Asserted against the function body rather than a copy of the expression, so the
    test fails if the escaping is ever dropped from the real definition."""
    body = q(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'geocode_api' AND p.proname = 'v1_attribution'"
    )
    assert "engine_version()" in body, "the page should report the engine version"
    escaped = body[body.index("engine_version()") - 200 : body.index("engine_version()") + 200]
    for char, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
        assert entity in escaped, f"{char!r} is not escaped around the interpolated version"


def test_no_unescaped_ampersands_outside_entities(page):
    """A bare & is invalid HTML; every one in the page must open an entity."""
    bare = [m.start() for m in re.finditer(r"&(?!#?\w+;)", page)]
    assert not bare, f"bare ampersand at {bare[:3]}"


def test_links_are_absolute_https(page):
    hrefs = re.findall(r'href="([^"]+)"', page)
    assert hrefs, "no links on the attribution page"
    assert all(h.startswith("https://") for h in hrefs), [h for h in hrefs if not h.startswith("https://")]


def test_is_stable_and_side_effect_free(page):
    """Declared STABLE and PARALLEL SAFE, and it takes no arguments: two calls must agree."""
    assert q("SELECT geocode_api.v1_attribution()") == page
    provolatile, proparallel, nargs = q(
        "SELECT ARRAY[p.provolatile::text, p.proparallel::text, p.pronargs::text]"
        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'geocode_api' AND p.proname = 'v1_attribution'"
    )
    assert provolatile == "s", "should be STABLE"
    assert proparallel == "s", "should be PARALLEL SAFE"
    assert nargs == "0", "takes no parameters, so no user input reaches the page"


def test_readable_by_the_api_role():
    """PostgREST connects as pgeo_api; without EXECUTE the endpoint 404s in production."""
    assert q("SELECT has_function_privilege('pgeo_api', 'geocode_api.v1_attribution()', 'EXECUTE')")
