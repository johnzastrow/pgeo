"""The Photon and Nominatim request adapters.

Neither engine speaks the Pelias API, so each accuracy run translates the request before scoring.
A translation bug does not raise - it quietly scores the wrong thing, and would read as the engine
being inaccurate. These tests pin the translations that actually decide results.

    uv run --project pgeo pytest tests/accuracy/test_adapters.py -q
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

photon = pytest.importorskip("run_accuracy_photon")
nominatim = pytest.importorskip("run_accuracy_nominatim")

CASES = json.loads((HERE / "cases.json").read_text())


def case_of(endpoint: str) -> dict:
    return next(c for c in CASES if c["endpoint"] == endpoint)


# ---- reverse: the filter that decides whether a housenumber check is fair ---------------------


def test_photon_translates_the_address_layer_filter():
    """layers=address -> layer=house. Without it Photon answers with the nearest street and
    fails a housenumber check it was never asked to satisfy."""
    path, q = photon.request_for(case_of("reverse"))
    assert path == "reverse"
    assert q["layer"] == "house"


def test_nominatim_asks_for_house_level_reverse():
    """Nominatim has no layer filter in this position; zoom=18 is the house-level equivalent."""
    path, q = nominatim.request_for(case_of("reverse"))
    assert path == "reverse"
    assert q["zoom"] == 18


@pytest.mark.parametrize("mod", [photon, nominatim], ids=["photon", "nominatim"])
def test_reverse_carries_the_point_unchanged(mod):
    c = case_of("reverse")
    _, q = mod.request_for(c)
    assert (q["lat"], q["lon"]) == (c["params"]["point.lat"], c["params"]["point.lon"])


# ---- structured: one engine has the endpoint, the other does not -----------------------------


def test_nominatim_uses_its_real_structured_endpoint():
    c = case_of("structured")
    path, q = nominatim.request_for(c)
    assert path == "search"
    assert q["street"] == c["params"]["address"]
    assert q["city"] == c["params"]["locality"]
    assert q["state"] == c["params"]["region"]
    assert q["postalcode"] == c["params"]["postalcode"]
    assert "q" not in q, "a structured query must not collapse to free text for Nominatim"


def test_photon_collapses_structured_to_one_line_in_postal_order():
    """Photon has no structured endpoint, so the fields go as one line - but every field must
    survive, in postal order, or the comparison understates it."""
    c = case_of("structured")
    p = c["params"]
    _, q = photon.request_for(c)
    assert q["q"] == f"{p['address']}, {p['locality']}, {p['region']}, {p['postalcode']}"


# ---- free text --------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["search", "autocomplete"])
def test_free_text_reaches_both_engines_intact(endpoint):
    c = case_of(endpoint)
    _, pq = photon.request_for(c)
    _, nq = nominatim.request_for(c)
    assert pq["q"] == c["params"]["text"]
    assert nq["q"] == c["params"]["text"]


def test_nominatim_always_requests_geojson_with_address_details():
    """The scorer reads geometry.coordinates and a housenumber; without these two parameters
    Nominatim returns neither in the shape it expects."""
    for endpoint in ("search", "autocomplete", "structured", "reverse"):
        _, q = nominatim.request_for(case_of(endpoint))
        assert q["format"] == "geojson"
        assert q["addressdetails"] == 1


# ---- the one response normalisation ------------------------------------------------------------


def test_nominatim_housenumber_is_lifted_where_the_scorer_looks():
    body = {
        "features": [
            {"properties": {"address": {"house_number": "389", "road": "Congress Street"}}},
            {"properties": {"address": {"road": "Congress Street"}}},  # no number
        ]
    }
    out = nominatim.normalise(body)
    assert out["features"][0]["properties"]["housenumber"] == "389"
    assert "housenumber" not in out["features"][1]["properties"], (
        "a feature with no house number must not gain an empty one - that would score as a miss"
    )


def test_nominatim_normalise_tolerates_an_error_response():
    assert nominatim.normalise(None) is None
    assert nominatim.normalise({}) == {}
