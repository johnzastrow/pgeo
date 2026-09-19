"""Pelias parameter parsing in the FastAPI front end (pure Python; no database)."""

from __future__ import annotations

import pytest

from pgeo.api.app import BadRequest, circle, common, extras


def test_circle_defaults_radius_and_orders_lon_lat():
    assert circle({"boundary.circle.lat": "44.8", "boundary.circle.lon": "-68.77"}) == [-68.77, 44.8, 50.0]
    assert circle({}) is None


@pytest.mark.parametrize(
    "q",
    [
        {"boundary.circle.lat": "44.8"},
        {"boundary.circle.radius": "5"},
        {"boundary.circle.lat": "44.8", "boundary.circle.lon": "-68.7", "boundary.circle.radius": "5000"},
    ],
)
def test_circle_rejects_incomplete_or_out_of_range(q):
    with pytest.raises(BadRequest):
        circle(q)


def test_extras_accepts_pelias_params():
    e = extras({"boundary.country": "USA", "boundary.gid": "whosonfirst:locality:85948877",
                "categories": "restaurant, lake", "lang": "en-US", "api_key": "k"})  # fmt: skip
    assert e == {"country": "USA", "gid": "whosonfirst:locality:85948877", "categories": ["restaurant", "lake"]}


@pytest.mark.parametrize(
    "q",
    [
        {"boundary.country": "United States of America"},
        {"boundary.gid": "portland"},
        {"lang": "english!"},
        {"categories": "a;DROP"},
        {"api_key": "x" * 201},
    ],
)
def test_extras_rejects_bad_values(q):
    with pytest.raises(BadRequest):
        extras(q)


def test_common_carries_all_filters():
    c = common({"boundary.circle.lat": "44.8", "boundary.circle.lon": "-68.77", "boundary.gid": "whosonfirst:county:1"})
    assert c["circle"] == [-68.77, 44.8, 50.0] and c["gid"] == "whosonfirst:county:1" and c["country"] is None
