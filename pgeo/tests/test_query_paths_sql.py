"""The fast paths in geocode.autocomplete and geocode.search return what the slow paths return.

The September 2026 performance work (docs/PERFORMANCE_OPTIMIZATION.md) gave unfiltered requests a
cheaper route: a narrow table, no keep() call, the short prefix kept out of the index scan, hits
built only for the rows that survive the LIMIT. Accuracy is this project's selling point, so none
of that may change an answer. The accuracy suite would notice a wrong answer eventually; these
tests say which mechanism broke, in seconds, and they hold on any data build.

Three kinds of check:

  * two routes, one answer   a filter that excludes nothing forces the slow route (the wide table,
                             keep() on every row); the result must equal the unfiltered one
  * an independent oracle    address mode has a total order (score, then id), so the expected
                             rows can be computed by a plain query written here, with no
                             optimization in it at all
  * stated behaviour         typos are rescued, including the ones that still prefix-match
                             something; reported distances are real distances

Run against the local database; skipped when it is not reachable.
"""

from __future__ import annotations

import asyncio

import pytest

asyncpg = pytest.importorskip("asyncpg")

from pgeo.settings import Settings  # noqa: E402

PORTLAND = (43.6591, -70.2568)
BANGOR = (44.8016, -68.7712)

# Every layer there is: as a filter it excludes nothing, but it is a filter, so it takes the slow route.
ALL_LAYERS = ["address", "venue", "street", "neighbourhood", "locality", "localadmin",
              "county", "region", "postalcode"]  # fmt: skip

NAMES = ["portland city hall", "moosehead lake", "main street bangor", "walmart supercenter",
         "acadia national park", "saco", "mount katahdin", "bowdoin college", "04101",
         "university of maine orono", "l l bean freeport", "old orchard beach"]  # fmt: skip
ADDRESSES = ["389 congress st portland", "12 main street bangor", "1 college circle bangor",
             "100 state st augusta", "25 pearl street", "7 s main st"]  # fmt: skip
TYPOS = ["portlnd", "walmrt supercenter", "moosehed lake", "bangorr", "acadai national park"]


def _rows(sql: str, *args) -> list:
    async def run():
        con = await asyncpg.connect(Settings.load().dsn, timeout=5)
        try:
            return await con.fetch(sql, *args)
        finally:
            await con.close()

    try:
        return asyncio.run(run())
    except (OSError, asyncpg.PostgresError, RuntimeError) as e:
        if isinstance(e, asyncpg.PostgresError) and not isinstance(e, asyncpg.InvalidPasswordError):
            raise
        pytest.skip(f"database not reachable: {e}")


def prefixes(text: str, start: int = 2) -> list[str]:
    return [text[:i] for i in range(start, len(text) + 1)]


# Whole statements, not fragments: every value below is a bound parameter and nothing is
# interpolated into SQL, here or in the functions under test.
AUTOCOMPLETE_SQL = (
    "SELECT h.gid, h.label, h.layer, h.match_type, h.score, h.distance_km, h.lon, h.lat "
    "FROM geocode.autocomplete($1, $2, $3, $4::text[], NULL, NULL, $5) h"
)
SEARCH_SQL = (
    "SELECT h.gid, h.label, h.layer, h.match_type, h.score, h.distance_km, h.lon, h.lat, h.confidence "
    "FROM geocode.search($1, NULL, NULL, NULL, NULL, NULL, $2, $3, $4::text[]) h"
)


def autocomplete(text: str, focus=None, layers=None, size: int = 10) -> list[tuple]:
    lat, lon = focus if focus else (None, None)
    return [tuple(r.values()) for r in _rows(AUTOCOMPLETE_SQL, text, lon, lat, layers, size)]


# ---- two routes, one answer ---------------------------------------------------------------------


@pytest.mark.parametrize("text", NAMES + ADDRESSES)
def test_autocomplete_fast_and_slow_routes_agree_on_every_keystroke(text):
    """Narrow table, no keep(), against wide table with keep() on every row: same rows, same
    order, same scores, for each prefix a person would type."""
    for q in prefixes(text):
        fast = autocomplete(q, focus=PORTLAND)
        slow = autocomplete(q, focus=PORTLAND, layers=ALL_LAYERS)
        assert fast == slow, f"routes disagree on {q!r}"


@pytest.mark.parametrize("text", TYPOS)
def test_the_typo_fallback_agrees_across_routes(text):
    fast = autocomplete(text, focus=BANGOR)
    slow = autocomplete(text, focus=BANGOR, layers=ALL_LAYERS)
    assert fast and fast == slow


@pytest.mark.parametrize("text", NAMES + ADDRESSES + TYPOS)
def test_search_is_unchanged_by_a_filter_that_excludes_nothing(text):
    """search() skips keep() when there is no filter; with one it runs on every candidate."""
    sql = SEARCH_SQL
    fast = [tuple(r.values()) for r in _rows(sql, text, PORTLAND[1], PORTLAND[0], None)]
    slow = [tuple(r.values()) for r in _rows(sql, text, PORTLAND[1], PORTLAND[0], ALL_LAYERS)]
    assert fast == slow


# ---- an independent oracle for address mode -------------------------------------------------------


@pytest.mark.parametrize("text", ["12 main s", "12 main st", "389 congress s", "1 college c",
                                  "100 state st a", "25 pearl", "7 s m", "7 s main"])  # fmt: skip
def test_address_mode_matches_a_plain_query_with_no_optimization_in_it(text):
    """The short-prefix split asks the index for the complete words and tests the prefix
    afterwards. This oracle asks for everything at once, the slow and obvious way. Address mode
    orders by score then id, so the expected rows are exact - no ties to excuse a difference."""
    # Prefix rows only: when address mode finds nothing the function goes on to the typo rescue,
    # which is a different mechanism with its own tests.
    got = [r[0] for r in autocomplete(text) if r[3] is None]
    toks = text.split()
    # Built the way the function builds it, from norm()'s tokens: every complete word, "saint"
    # from a raw "st" read as saint-or-street, and the last word as a prefix in either form.
    want = [r["gid"] for r in _rows(
        """
        WITH t AS (SELECT string_to_array(geocode.norm($1), ' ') AS toks),
             mid AS (SELECT string_agg(CASE WHEN w = 'saint' THEN '(saint | street)' ELSE w END, ' & ' ORDER BY o) AS q
                     FROM t, unnest(t.toks[1:cardinality(t.toks)-1]) WITH ORDINALITY u(w, o))
        SELECT f.gid FROM pgeo.feature f, t, mid
        WHERE f.layer = 'address' AND f.housenumber = $2
          AND f.tokens @@ to_tsquery('simple', mid.q || ' & (' ||
                t.toks[cardinality(t.toks)] || ':* | ' || $3 || ':*)')
        ORDER BY f.importance::real DESC, f.id LIMIT 10
        """, text, toks[0], toks[-1])]  # fmt: skip
    assert got == want


# ---- stated behaviour ---------------------------------------------------------------------------


def test_a_typo_that_still_prefix_matches_something_is_rescued():
    """Why the typo fallback is NOT gated to "the prefix match found nothing".

    It is over half of autocomplete's CPU, and gating it looked free: on the 150 autocomplete
    accuracy cases nothing changed. The fuzz set found the hole. "Walker Ci" is one character off
    "Walker Corner", but it still prefix-matches one wrong row - Walker Heights Circle - so the
    gate suppressed the fallback, and the right town had been fourth, among the "filler". A typo
    does not always match nothing; sometimes it matches the wrong thing. Do not re-try the gate
    without making this pass."""
    rows = autocomplete("Walker Ci")
    kinds = [r[3] for r in rows]
    assert None in kinds and "fallback" in kinds, "expected a prefix row followed by fallback rows"
    assert any("Walker Corner" in r[1] for r in rows[:5]), [r[1] for r in rows[:5]]


def test_fallback_rows_follow_prefix_rows_and_fill_the_list():
    """Results are appended, never re-sorted: every prefix row precedes every fallback row."""
    for q in ("Walker Ci", "389 congress st po", "moosehead lak"):
        kinds = [r[3] for r in autocomplete(q, focus=PORTLAND)]
        if "fallback" in kinds:
            first = kinds.index("fallback")
            assert all(k is None for k in kinds[:first]) and all(k == "fallback" for k in kinds[first:]), (q, kinds)


@pytest.mark.parametrize(("typo", "meant"), [("portlnd", "Portland"), ("moosehed lake", "Moosehead Lake"),
                                             ("walmrt supercenter", "Walmart Supercenter"),
                                             ("bangorr", "Bangor")])  # fmt: skip
def test_a_typo_is_still_rescued(typo, meant):
    """The thing the fallback exists for."""
    rows = autocomplete(typo, focus=PORTLAND)
    assert rows, f"{typo!r} returned nothing"
    assert any(meant.lower() in r[1].lower() for r in rows[:5]), [r[1] for r in rows[:5]]


@pytest.mark.parametrize("text", ["portland city hall", "12 main street bangor", "portlnd"])
def test_the_reported_distance_is_the_real_distance(text):
    """The distance is now computed once and reused for the focus boost; it must still be the
    spheroidal distance from the focus to the point that was returned."""
    rows = _rows(
        """SELECT h.distance_km,
                  ST_Distance(ST_SetSRID(ST_MakePoint(h.lon, h.lat), 4326)::geography,
                              ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography) / 1000 AS real_km
           FROM geocode.autocomplete($1, $2, $3) h""", text, BANGOR[1], BANGOR[0])  # fmt: skip
    assert rows
    for r in rows:
        assert r["distance_km"] == pytest.approx(r["real_km"], abs=1e-6)


@pytest.mark.parametrize("text", ["portland", "main street", "389 congress st portland"])
def test_without_a_focus_there_is_no_distance_and_no_boost(text):
    rows = autocomplete(text)
    assert rows and all(r[5] is None for r in rows)


@pytest.mark.parametrize("text", NAMES[:6] + ADDRESSES[:3])
def test_results_come_back_best_first(text):
    """Within the prefix rows, and within the fallback rows: the two are scored on different
    scales and the fallback block always follows, so the list as a whole is not one sort."""
    rows = autocomplete(text, focus=PORTLAND)
    for kind in (None, "fallback"):
        scores = [r[4] for r in rows if r[3] == kind]
        assert scores == sorted(scores, reverse=True), (kind, scores)


@pytest.mark.parametrize("size", [1, 3, 10, 40])
def test_size_is_honoured_on_both_routes(size):
    assert len(autocomplete("main", size=size)) == size
    assert len(autocomplete("main", size=size, layers=ALL_LAYERS)) == size


def test_a_real_filter_still_filters():
    """The fast route must not be taken when a filter is set."""
    rows = autocomplete("portland", focus=PORTLAND, layers=["venue"])
    assert rows and {r[2] for r in rows} == {"venue"}
    assert autocomplete("portland", focus=PORTLAND) != rows
