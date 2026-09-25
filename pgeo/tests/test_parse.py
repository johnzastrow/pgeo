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


def test_a_misspelled_town_still_parses_as_the_town():
    """"Albny, NY" used to parse to nothing but the state, so the raw text went to name
    matching, where venues that carry their own town in their name beat the town itself."""
    ny = RuleParser({"albany", "schenectady", "rochester", "parks", "new york"},
                    {"ny": "NY", "new york": "NY"})
    # the corrected spelling is what comes out, because the search matches towns by trigram
    for typo, want in (("Albny, NY", "albany"), ("Schenctady, NY", "schenectady"),
                       ("Rochestr, NY", "rochester")):
        assert ny.parse(typo).locality == want, typo
    # a transposition is two edits but the same letters, and is the commonest typo of all - and
    # the one that most needs correcting, since it shares almost no trigrams with its own word
    az = RuleParser({"tucson", "phoenix"}, {"az": "AZ", "arizona": "AZ"})
    assert az.parse("Tuscon, AZ").locality == "tucson"
    # correct spellings are unaffected
    assert ny.parse("Albany, NY").locality == "Albany"


def test_a_typo_rule_does_not_invent_towns():
    """The risk of accepting a misspelling is turning a query that was never a town into one."""
    ny = RuleParser({"albany", "balmat", "parks", "central islip", "new york"},
                    {"ny": "NY", "new york": "NY"})
    # two edits from Balmat, but not a transposition of it
    assert ny.parse("walmart").locality is None
    # only the whole remaining text is tried, so a trailing word is not a town
    assert ny.parse("central park").locality is None
    # below five characters one edit reaches too far
    assert ny.parse("park").locality is None


def test_the_fuzzy_town_rule_matches_the_sql_one():
    """geocode.town_fuzzy in sql/050_api.sql must agree with this; the two front ends each
    carry their own parser and have drifted apart before."""
    p = RuleParser({"albany", "tucson", "balmat", "parks", "westwind", "central islip"})
    assert p.town_fuzzy("albny") == "albany"       # one edit
    assert p.town_fuzzy("tuscon") == "tucson"      # two edits, same letters
    assert p.town_fuzzy("walmart") is None         # two edits, different letters
    assert p.town_fuzzy("west end") is None        # two edits, and a different word count
    assert p.town_fuzzy("park") is None            # too short to risk an edit
    assert p.town_fuzzy("albany") is None          # an exact match is not this function's job


def test_the_typo_fallback_prefers_the_state_the_query_named():
    """"Hosuton, TX" is one edit from Hosston, Louisiana and two from Houston, Texas - a
    transposition - so edit distance alone picks the wrong state's village."""
    towns = {"houston", "hosston", "texarkana"}
    states = {"tx": "TX", "texas": "TX", "la": "LA", "louisiana": "LA", "ar": "AR", "arkansas": "AR"}
    where = {"houston": {"TX", "AR"}, "hosston": {"LA"}, "texarkana": {"TX", "AR"}}
    p = RuleParser(towns, states, where)
    assert p.town_fuzzy("hosuton") == "hosston"          # closest spelling, no state given
    assert p.town_fuzzy("hosuton", "TX") == "houston"    # the state the query named wins
    assert p.town_fuzzy("hosuton", "LA") == "hosston"
    assert p.parse("Hosuton, TX").locality == "houston"
    # with no state map at all it behaves as it did before
    assert RuleParser(towns, states).town_fuzzy("hosuton", "TX") == "hosston"
