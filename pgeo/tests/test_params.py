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
        {"boundary.circle.lat": "44.8", "boundary.circle.lon": "-68.7", "boundary.circle.radius": "101"},
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


RECT = {"boundary.rect.min_lon": "-70.5", "boundary.rect.min_lat": "43.5",
        "boundary.rect.max_lon": "-70.0", "boundary.rect.max_lat": "44.0"}  # fmt: skip


def test_common_accepts_a_well_formed_rect():
    assert common(RECT)["rect"] == [-70.5, 43.5, -70.0, 44.0]


@pytest.mark.parametrize(
    "change",
    [
        {"boundary.rect.min_lat": "-95"},  # latitude out of range (it used to be checked against +-180)
        {"boundary.rect.max_lat": "nan"},
        {"boundary.rect.max_lon": "inf"},
        {"boundary.rect.min_lon": "-69"},  # min_lon > max_lon
        {"boundary.rect.min_lat": "45"},  # min_lat > max_lat
    ],
)
def test_common_rejects_a_bad_rect(change):
    with pytest.raises(BadRequest):
        common(RECT | change)


def test_limits_follow_what_the_database_reports(monkeypatch):
    """load_limits takes the pgeo.* settings from the database; the checks then use them."""
    import asyncio

    from pgeo.api import app as api

    class Con:
        async def fetchval(self, sql):
            assert "geocode.api_limits()" in sql
            return '{"max_circle_km": 20, "max_size": 5, "max_text": 50}'

    class Acquire:
        async def __aenter__(self):
            return Con()

        async def __aexit__(self, *exc):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    monkeypatch.setattr(api, "LIMITS", dict(api.LIMITS))
    asyncio.run(api.load_limits(Pool()))
    assert api.LIMITS["max_circle_km"] == 20 and api.LIMITS["max_reverse_km"] == 50
    with pytest.raises(BadRequest, match="between 0.001 and 20 km"):
        circle({"boundary.circle.lat": "44.8", "boundary.circle.lon": "-68.7", "boundary.circle.radius": "25"})
    assert api.size_param("30") == 5
    with pytest.raises(BadRequest, match="longer than 50 characters"):
        api.clean_text("x" * 51)
