"""Ranking behaviour (pgeo/sql/040_functions.sql), one test per tuning step.

The accuracy suite (tests/accuracy, 1,560 cases) says *that* accuracy moved; these say *which
behaviour* broke, in seconds rather than minutes. Each test names the step it guards from the
tuning table in docs/TUNING_REPORT.md section 3.

Run against the local database; skipped when it is not reachable.
"""

from __future__ import annotations

import asyncio
import json

import pytest

asyncpg = pytest.importorskip("asyncpg")

from pgeo.settings import Settings  # noqa: E402

BANGOR = (44.8016, -68.7712)
PORTLAND = (43.6591, -70.2568)


def _fetchval(sql: str, *args):
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


def search(**kw) -> dict:
    """geocode_api.v1_search with named arguments, as the HTTP front ends call it."""
    names = list(kw)
    args = ", ".join(f"{n} := ${i + 1}" for i, n in enumerate(names))
    return json.loads(_fetchval(f"SELECT geocode_api.v1_search({args})", *kw.values()))


def autocomplete(**kw) -> dict:
    names = list(kw)
    args = ", ".join(f"{n} := ${i + 1}" for i, n in enumerate(names))
    return json.loads(_fetchval(f"SELECT geocode_api.v1_autocomplete({args})", *kw.values()))


def props(doc: dict) -> list[dict]:
    return [f["properties"] for f in doc.get("features", [])]


def first(doc: dict) -> dict:
    p = props(doc)
    assert p, f"no results: {doc.get('geocoding', {}).get('errors')}"
    return p[0]


# ---- step: normalization (abbreviations) ------------------------------------------------


@pytest.mark.parametrize(
    ("query", "want_in_label"),
    [
        ("congress st portland", "Congress"),
        ("congress street portland", "Congress"),
        ("main rd", "Main"),
        ("us rte 1", "Route 1"),
    ],  # fmt: skip
)
def test_abbreviations_and_spelled_out_words_reach_the_same_places(query, want_in_label):
    """ "rd" and "road", "st" and "street" must not be different words."""
    assert want_in_label.lower() in first(search(text=query))["label"].lower()


# ---- step: towns ------------------------------------------------------------------------


def test_structured_search_ranks_the_address_above_its_zip_code():
    """Before the towns step this returned the ZIP polygon at rank 1, so structured search
    scored 0%."""
    doc = json.loads(
        _fetchval(
            "SELECT geocode_api.v1_search_structured(address := $1, locality := $2, region := $3, postalcode := $4)",
            "389 Congress St",
            "Portland",
            "ME",
            "04101",
        )
    )
    assert first(doc)["layer"] == "address"


def test_a_town_query_returns_the_town_itself_first():
    assert first(search(text="bangor", layers="locality"))["layer"] == "locality"


# ---- step: dedupe -----------------------------------------------------------------------


def test_results_carry_no_duplicate_labels():
    """OpenAddresses and OpenStreetMap hold most Maine addresses twice; one must be shown."""
    labels = [p["label"] for p in props(search(text="congress street portland", size=10))]
    assert len(labels) == len(set(labels)), f"duplicates: {sorted(labels)}"


# ---- step: word_similarity direction ----------------------------------------------------


def test_a_generic_word_does_not_drag_in_every_name_containing_it():
    """similarity(query, name), not the reverse: "Mountain" used to match every "... Mountain
    ..." equally, which cost the lakes and summits category 18 points."""
    top = [p["name"].lower() for p in props(search(text="mountain", size=5))]
    assert any(n == "mountain" or n.startswith("mountain") for n in top), top


# ---- step: ties + focus -----------------------------------------------------------------


def test_the_focus_point_decides_between_identically_named_streets():
    """Hundreds of Main Streets: the nearest to the map centre must win, and the candidate cut
    must happen after the focus is applied, not before."""
    near_bangor = first(search(text="main st", lat=BANGOR[0], lon=BANGOR[1]))
    near_portland = first(search(text="main st", lat=PORTLAND[0], lon=PORTLAND[1]))
    assert near_bangor["label"] != near_portland["label"]
    assert "bangor" in near_bangor["label"].lower()


# ---- step: town-aware (the queried town is a location) ----------------------------------


def test_a_named_town_is_a_location_not_only_a_name_to_match():
    """ "Calvary Bible Church, Stratton" is in Eustis, next to Stratton. Matching the town as a
    name finds nothing; treating it as a place finds the church."""
    top = first(search(text="calvary bible church stratton"))
    assert "calvary bible church" in top["label"].lower()


# ---- step: calibration / confidence -----------------------------------------------------


def test_confidence_is_present_and_within_range():
    for p in props(search(text="portland", size=5)):
        assert 0.0 <= p["confidence"] <= 1.0


def test_an_impossible_place_returns_nothing_or_low_confidence():
    """A miss must be answerable as a miss: either no results, or confidence a client can
    threshold on. The accuracy suite counts a miss as handled below 0.8."""
    doc = search(text="Qqxwv Blorptangle Institute")
    ps = props(doc)
    assert not ps or ps[0]["confidence"] < 0.8, ps[:1]


def test_confidence_grades_exact_above_partial_above_nonsense():
    """The one number a client thresholds on has to be ordered, not just present."""
    exact = first(search(text="bangor"))["confidence"]
    partial = first(search(text="Zzyzx Memorial Fountain"))["confidence"]  # contains a real name
    nonsense = first(search(text="Qqxwv Blorptangle Institute"))["confidence"]
    assert exact > partial > nonsense, (exact, partial, nonsense)


def test_a_query_that_embeds_a_real_name_is_marked_as_a_fallback_match():
    """ "Zzyzx Memorial Fountain" does find "Memorial Fountain": the name is really in the data.
    match_type is how a client tells that apart from a clean hit."""
    top = first(search(text="Zzyzx Memorial Fountain"))
    assert top["match_type"] == "fallback", top


def test_a_famous_name_that_really_exists_in_maine_is_still_found():
    """ "The Eiffel Tower of Paris, Maine" is a real venue in South Paris, and one of the eight
    cases the accuracy set wrongly calls a miss. Suppressing famous names to score better on
    misses would break it."""
    assert "eiffel tower" in first(search(text="Eiffel Tower, Maine"))["label"].lower()


# ---- filters ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "layer"),
    [("portland", "locality"), ("389 congress st portland", "address"), ("cumberland", "county")],
)
def test_the_layers_filter_returns_only_that_layer(query, layer):
    """The query has to be one that layer can answer: "portland" matches no address, because an
    address is matched by house number and street."""
    ps = props(search(text=query, layers=layer, size=5))
    assert ps, f"no {layer} results for {query!r}"
    assert {p["layer"] for p in ps} == {layer}


@pytest.mark.parametrize(
    ("query", "source"),
    [("portland", "wof"), ("main", "osm"), ("389 congress st portland", "oa")],
)
def test_the_sources_filter_returns_only_that_source(query, source):
    ps = props(search(text=query, sources=source, size=5))
    assert ps, f"no {source} results for {query!r}"
    assert all(p["source"] in {source, {"wof": "whosonfirst", "osm": "openstreetmap",
                                        "oa": "openaddresses"}[source]} for p in ps)  # fmt: skip


def test_a_rectangle_excludes_everything_outside_it():
    ps = props(search(text="main st", min_lat=44.7, max_lat=44.9, min_lon=-68.9, max_lon=-68.6, size=10))
    assert ps
    for f in search(text="main st", min_lat=44.7, max_lat=44.9, min_lon=-68.9, max_lon=-68.6,
                    size=10)["features"]:  # fmt: skip
        lon, lat = f["geometry"]["coordinates"]
        assert 44.7 <= lat <= 44.9 and -68.9 <= lon <= -68.6, f["properties"]["label"]


def test_another_country_returns_nothing_rather_than_maine():
    assert not props(search(text="bangor", country="FRA"))


def test_size_caps_the_number_of_results():
    assert len(props(search(text="portland", size=3))) <= 3


# ---- autocomplete -----------------------------------------------------------------------


def test_autocomplete_matches_a_prefix_mid_word():
    ps = props(autocomplete(text="389 congres"))
    assert ps, "no autocomplete results for a half-typed street"
    assert "congress" in ps[0]["label"].lower()


def test_autocomplete_applies_its_filters_inside_the_candidate_cut():
    """The filter used to run after the row limit, so a filtered autocomplete could come back
    empty even though matches existed."""
    ps = props(autocomplete(text="main", layers="street", size=5))
    assert ps
    assert {p["layer"] for p in ps} == {"street"}


# ---- envelope ---------------------------------------------------------------------------


def test_every_response_carries_the_pelias_geocoding_block():
    doc = search(text="bangor")
    g = doc["geocoding"]
    assert g["version"] == "0.2"
    assert g["engine"]["name"] == "pgeo-sql"
    assert doc["type"] == "FeatureCollection"


def test_a_bad_parameter_is_a_pelias_shaped_error_not_an_exception():
    doc = search(text="x", layers="planet")
    assert doc["geocoding"]["errors"], doc
    assert doc["features"] == []
