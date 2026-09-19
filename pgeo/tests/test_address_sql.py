"""USPS Publication 28 address functions (pgeo/sql/060_address.sql), run against the local
database; skipped when it is not reachable."""

from __future__ import annotations

import asyncio
import json

import pytest

asyncpg = pytest.importorskip("asyncpg")

from pgeo.settings import Settings  # noqa: E402


def q(sql: str, *args):
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


@pytest.mark.parametrize(
    ("street", "want"),
    [
        ("Congress Street", (None, "CONGRESS", "ST", None, None)),
        ("North Main Street", ("N", "MAIN", "ST", None, None)),
        ("Main St N", (None, "MAIN", "ST", "N", None)),
        ("North Street", (None, "NORTH", "ST", None, None)),  # a direction as the name
        ("US Route 1", (None, "US ROUTE 1", None, None, None)),
        ("Rte 202", (None, "ROUTE 202", None, None, None)),
        ("St. John Street", (None, "ST JOHN", "ST", None, None)),
        ("East Main Street Extension", ("E", "MAIN", "ST", None, "EXT")),
        ("Park Avenue West", (None, "PARK", "AVE", "W", None)),
        ("West Broadway", ("W", "BROADWAY", None, None, None)),
        ("US Route 2 W", (None, "US ROUTE 2", None, "W", None)),
        ("Parkway N", (None, "PARKWAY", None, "N", None)),
    ],
)
def test_usps_street(street, want):
    r = q("SELECT * FROM geocode.usps_street($1)", street)[0]
    assert (r["predirectional"], r["street_name"], r["suffix"], r["postdirectional"], r["post_modifier"]) == want


@pytest.mark.parametrize(
    ("unit", "want"),
    [
        ("Apt 2", ("APT", "2")),
        ("Suite 200", ("STE", "200")),
        ("#3", ("#", "3")),
        ("2B", ("#", "2B")),
        ("Rear", ("REAR", None)),
    ],
)
def test_usps_secondary(unit, want):
    r = q("SELECT * FROM geocode.usps_secondary($1)", unit)[0]
    assert (r["designator"], r["unit_number"]) == want


def address_doc(**kw) -> dict:
    args = {k: kw.get(k) for k in ("ids", "text", "lat", "lon", "radius", "unit")}
    row = q("SELECT geocode_api.v1_address($1, $2, $3, $4, $5, $6) AS d", *args.values())[0]
    return json.loads(row["d"])


def address(**kw):
    return address_doc(**kw)["features"]


def test_exact_address_with_typed_unit():
    f = address(text="389 congress st apt 2, portland me")[0]["properties"]
    assert f["usps"]["delivery_line"] == "389 CONGRESS ST APT 2"
    assert f["usps"]["last_line"] == "PORTLAND ME 04101"
    assert f["place"]["county_fips"] == "23005"


def test_venue_gets_nearest_street_address():
    u = address(text="Just in Time, Lewiston Maine")[0]["properties"]["usps"]
    assert u["match"] == "nearest" and u["delivery_line"] and u["last_line"].startswith("LEWISTON ME")


def test_town_gets_place_context_only():
    p = address(text="Portland, Maine")[0]["properties"]
    assert "usps" not in p and p["place"]["municipality"] == "Portland"


def test_rejects_ambiguous_or_bad_input():
    for kw in (
        {"ids": "x", "text": "y"},
        {"ids": "bad id"},
        {"lat": 43.6},
        {"lat": 43.6, "lon": -70.2, "radius": 50.0},
    ):
        # input errors come back as a Pelias-style envelope (HTTP 400 through the front ends)
        doc = address_doc(**kw)
        assert doc["geocoding"]["errors"] and doc["features"] == []
