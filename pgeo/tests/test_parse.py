"""Parser tests (pure Python; no database)."""

from __future__ import annotations

from pgeo.api.parse import RuleParser, from_libpostal

TOWNS = {"portland", "south portland", "bar harbor", "augusta", "bangor", "mount desert"}


def rp() -> RuleParser:
    return RuleParser(TOWNS)


def test_full_address_with_commas():
    p = rp().parse("389 Congress St, Portland, ME 04101")
    assert (p.housenumber, p.street, p.locality, p.postcode, p.state) == (
        "389", "Congress St", "Portland", "04101", "ME",
    )


def test_address_without_commas_multiword_town():
    p = rp().parse("12 Main St South Portland Maine")
    assert (p.housenumber, p.street, p.locality) == ("12", "Main St", "South Portland")


def test_unit_is_dropped():
    p = rp().parse("210 State St Apt 2, Augusta, ME")
    assert (p.housenumber, p.street, p.locality) == ("210", "State St", "Augusta")


def test_place_name_with_town():
    p = rp().parse("Jordan Pond, Bar Harbor")
    assert (p.name, p.locality, p.housenumber) == ("Jordan Pond", "Bar Harbor", None)
    assert p.name_query() == "Jordan Pond"


def test_bare_town_searches_the_town():
    p = rp().parse("Bangor ME")
    assert p.locality == "Bangor"
    assert p.name_query() == "Bangor"


def test_unknown_text_is_left_for_name_search():
    p = rp().parse("Moosehead Lake")
    assert p.housenumber is None and p.locality is None
    assert p.name_query() == "Moosehead Lake"


def test_zip_plus_four():
    p = rp().parse("73 Harlow St Bangor 04401-5102")
    assert (p.housenumber, p.postcode, p.locality) == ("73", "04401", "Bangor")


def test_libpostal_list_and_dict_forms():
    comps = [
        {"label": "house_number", "value": "389"},
        {"label": "road", "value": "congress st"},
        {"label": "city", "value": "portland"},
        {"label": "state", "value": "me"},
    ]
    a = from_libpostal("389 congress st portland me", comps)
    b = from_libpostal("x", {"house_number": "389", "road": "congress st", "city": "portland"})
    assert (a.housenumber, a.street, a.locality, a.state) == ("389", "congress st", "portland", "me")
    assert (b.housenumber, b.street, b.locality) == ("389", "congress st", "portland")


def test_libpostal_venue():
    p = from_libpostal("portland jetport", [{"label": "house", "value": "portland jetport"}])
    assert p.name_query() == "portland jetport"
