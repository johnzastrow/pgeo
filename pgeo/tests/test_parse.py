"""Parser tests (pure Python; no database)."""

from __future__ import annotations

from pgeo.api.parse import RuleParser, from_libpostal

TOWNS = {"portland", "south portland", "bar harbor", "augusta", "bangor", "mount desert"}
# The states the build covers, as the app loads them from geocode.region_ref. The parser has no
# built-in list any more, so a parser given none strips no state at all - which is what a build
# whose region_ref is empty should do.
STATES = {"me": "ME", "maine": "ME"}


def rp(states: dict[str, str] | None = STATES) -> RuleParser:
    return RuleParser(TOWNS, states)


def test_full_address_with_commas():
    p = rp().parse("389 Congress St, Portland, ME 04101")
    assert (p.housenumber, p.street, p.locality, p.postcode, p.state) == (
        "389",
        "Congress St",
        "Portland",
        "04101",
        "ME",
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


def test_misspelled_town_after_comma_is_kept():
    p = rp().parse("27 Libert St, Newcatsle, ME")
    assert (p.housenumber, p.street, p.locality) == ("27", "Libert St", "Newcatsle")


def test_libpostal_venue_misread_falls_back_to_rule_street():
    from pgeo.api.parse import merge_rule_fallback

    lp = from_libpostal(
        "12 Wenbelle Df, Buckspotr, ME",
        [{"label": "house_number", "value": "12"}, {"label": "house", "value": "wenbelle df buckspotr me"}],
    )
    p = merge_rule_fallback(lp, rp().parse("12 Wenbelle Df, Buckspotr, ME"))
    assert (p.housenumber, p.street, p.locality, p.name) == ("12", "Wenbelle Df", "Buckspotr", None)


def test_a_trailing_state_is_stripped_whatever_the_state_is():
    """The parser has no built-in state list: it uses the ones the build covers."""
    ny = RuleParser({"new york", "albany", "buffalo"}, {"ny": "NY", "new york": "NY"})
    p = ny.parse("350 5th Ave, New York, NY")
    assert (p.housenumber, p.street, p.locality, p.state) == ("350", "5th Ave", "New York", "NY")


def test_a_state_name_that_is_also_a_town_stays_the_town():
    """"New York" is both; stripping it as the state would lose the locality."""
    ny = RuleParser({"new york", "albany"}, {"ny": "NY", "new york": "NY"})
    p = ny.parse("350 5th Ave, New York")
    assert (p.housenumber, p.street, p.locality) == ("350", "5th Ave", "New York")


def test_a_multi_state_build_reports_whichever_state_was_written():
    two = RuleParser({"burlington", "concord"}, {"vt": "VT", "vermont": "VT",
                                                 "nh": "NH", "new hampshire": "NH"})
    assert two.parse("Burlington, VT").state == "VT"
    assert two.parse("Concord, New Hampshire").state == "NH"


def test_an_ambiguous_state_name_is_decided_by_the_head():
    """New York is both a state and a town, so what precedes it decides which one is meant."""
    ny = RuleParser({"new york", "north woodmere", "brooklyn"},
                    {"ny": "NY", "new york": "NY"})
    # the head is a town, so New York is the state
    p = ny.parse("North Woodmere, New York")
    assert (p.locality, p.state) == (None, "NY") or p.locality == "North Woodmere"
    assert p.state == "NY"
    # the head is an address, so New York is the city
    q = ny.parse("350 5th Ave, New York")
    assert (q.housenumber, q.street, q.locality, q.state) == ("350", "5th Ave", "New York", None)


def test_a_multi_word_state_without_commas():
    """Eleven of the fifty states have two-word names; only the last word used to be tested."""
    ny = RuleParser({"new york", "penfield", "south portland"}, {"ny": "NY", "new york": "NY"})
    p = ny.parse("101 Penbrooke Drive Penfield New York")
    assert (p.housenumber, p.locality, p.state) == ("101", "Penfield", "NY")
    assert p.street == "Penbrooke Drive"


def test_a_two_word_town_before_a_two_word_state():
    nh = RuleParser({"new hampshire", "north conway"}, {"nh": "NH", "new hampshire": "NH"})
    p = nh.parse("5 Elm St North Conway New Hampshire")
    assert (p.housenumber, p.locality, p.state) == ("5", "North Conway", "NH")


def test_a_five_digit_house_number_is_not_a_postcode():
    """Phoenix and Las Vegas number houses in five digits; reading one as a ZIP broke the parse."""
    az = RuleParser({"prescott valley", "glendale"}, {"az": "AZ", "arizona": "AZ"})
    p = az.parse("13023 E Lima St, Prescott Valley")
    assert (p.housenumber, p.locality, p.postcode) == ("13023", "Prescott Valley", None)
    # a real postcode after the address is still found
    q = az.parse("13023 E Lima St, Prescott Valley AZ 86314")
    assert (q.housenumber, q.postcode, q.state) == ("13023", "86314", "AZ")
    # and a bare postcode is still a postcode
    assert az.parse("86314").postcode == "86314"
