"""Address parsing: three interchangeable modes feeding the same SQL search.

- service:   libpostal HTTP service (pelias/libpostal-service: GET /parse?address=)
- extension: libpostal inside Postgres (pgsql-postal: SELECT postal_parse($1))
- none:      rule-based parser (house number, street, a town this build knows, ZIP, state)

All modes return a Parsed. The search never trusts a parse: every component only shapes
candidate retrieval and scoring, and the full text is always searched as a name too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
HN_RE = re.compile(r"^\s*(\d{1,6}[a-zA-Z]?)(?:\s*-\s*\d{1,6})?\s+(.+)$")
HN_LEAD_RE = re.compile(r"^\s*\d{1,6}[a-zA-Z]?(?:\s*-\s*\d{1,6})?\s")
UNIT_RE = re.compile(r"\b(?:apt|apartment|unit|ste|suite|#)\s*[\w-]+", re.I)


@dataclass
class Parsed:
    text: str
    name: str | None = None  # venue / place name (libpostal "house")
    housenumber: str | None = None
    street: str | None = None
    locality: str | None = None
    postcode: str | None = None
    state: str | None = None
    raw: dict = field(default_factory=dict)

    def name_query(self) -> str:
        """What to search as a place name when there is no street address."""
        if self.name:
            return self.name
        if self.locality and not self.street and not self.housenumber:
            return self.locality
        return self.text


def merge_rule_fallback(lp: Parsed, rule: Parsed) -> Parsed:
    """libpostal often reads a misspelled street as a venue name ("12 Wenbelle Df,
    Buckspotr" -> house_number + house). When it found a number but no street, take the
    street and town from the rule parser instead."""
    if lp.housenumber and not lp.street and rule.housenumber and rule.street:
        lp.street = rule.street
        lp.name = None
        lp.locality = lp.locality or rule.locality
        lp.postcode = lp.postcode or rule.postcode
    return lp


def from_libpostal(text: str, components: list[dict] | dict) -> Parsed:
    """Map libpostal labels (list of {label, value} or {label: value}) to a Parsed."""
    if isinstance(components, list):
        comp = {c["label"]: c["value"] for c in components if "label" in c and "value" in c}
    else:
        comp = dict(components)
    p = Parsed(text=text, raw=comp)
    p.name = comp.get("house")
    p.housenumber = comp.get("house_number")
    p.street = comp.get("road")
    p.locality = comp.get("city") or comp.get("suburb") or comp.get("city_district")
    p.postcode = comp.get("postcode")
    p.state = comp.get("state")
    # libpostal sometimes reads a bare town or place as "road"; keep the full text search.
    return p


def _edits_within(a: str, b: str, limit: int) -> int | None:
    """Levenshtein distance between a and b, or None once it is known to exceed `limit`."""
    if abs(len(a) - len(b)) > limit:
        return None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:  # every alignment from here on is already too far
            return None
        prev = cur
    return prev[-1] if prev[-1] <= limit else None


class RuleParser:
    """Small US-address parser. Towns are recognized from the loaded admin names."""

    def __init__(self, localities: set[str], states: dict[str, str] | None = None,
                 town_states: dict[str, set[str]] | None = None) -> None:
        self.localities = localities  # normalized, lowercase
        # town name -> the states it is in, for the typo fallback. Empty is fine: the fallback
        # then behaves as it did before, preferring the closest spelling in any state.
        self.town_states = town_states or {}
        self.max_words = max((len(x.split()) for x in localities), default=1)
        # Towns grouped by (word count, length), so the typo search below compares against a
        # handful of names instead of all of them.
        self._by_shape: dict[tuple[int, int], list[str]] = {}
        for t in localities:
            self._by_shape.setdefault((len(t.split()), len(t)), []).append(t)
        # Lower-case spelling -> postal abbreviation, for every state this build covers: {"me":
        # "ME", "maine": "ME"}. It used to be the literal {"me", "maine"}, which on a New York
        # build left "NY" in the text and the parser then read it as the town, turning
        # "350 5th Ave, New York, NY" into a search for 350 New York Ave.
        self.states = states or {}
        self.max_state_words = max((len(x.split()) for x in self.states), default=1)

    def town_fuzzy(self, cand: str, want_state: str | None = None) -> str | None:
        """The town `cand` is a misspelling of, or None. Mirrors geocode.town_fuzzy in SQL.

        One edit, or two when the two strings have the same letters - that second case is a
        transposition, "Tuscon" for "Tucson". Same word count and a length within one, and at
        least five characters, because below that a single edit reaches too far ("park" is one
        edit from Parks). Only ever called after an exact match has failed.

        A town in `want_state` wins over a closer spelling elsewhere: "Hosuton, TX" is one edit
        from Hosston, Louisiana and two from Houston, Texas, so edit distance alone picks the
        wrong state's village.
        """
        if len(cand) < 5:
            return None
        best: tuple[int, int, str] | None = None
        nwords = len(cand.split())
        for length in (len(cand) - 1, len(cand), len(cand) + 1):
            for name in self._by_shape.get((nwords, length), ()):
                d = _edits_within(name, cand, 2)
                if d is None or d == 0:
                    continue
                if d == 2 and sorted(name) != sorted(cand):
                    continue
                in_state = bool(want_state and want_state.upper() in self.town_states.get(name, ()))
                key = (0 if in_state else 1, d, name)
                if best is None or key < best:
                    best = key
        return best[2] if best else None

    def parse(self, text: str) -> Parsed:
        p = Parsed(text=text)
        s = UNIT_RE.sub(" ", text)
        # Look for a postcode past any leading house number. Phoenix and Las Vegas number houses
        # in five digits - 13023 E Lima St - and reading that as a ZIP made the address parse
        # fail and the search answer with the street. A bare "04101" is still a postcode.
        lead = HN_LEAD_RE.match(s)
        off = lead.end() if lead else 0
        m = ZIP_RE.search(s, off)
        if m:
            p.postcode = m.group(1)
            s = s[: m.start()] + s[m.end() :]
        parts = [x.strip() for x in s.split(",") if x.strip()]
        # trailing state
        words = " ".join(parts).split()
        # A state name can be several words ("New York"), so try the comma part before the last
        # word; and never strip a name that is also a town here, or "350 5th Ave, New York" loses
        # its locality.
        def tail_is_town(ws: list[str]) -> bool:
            """Does this word list end with a town? Towns can be several words."""
            return any(" ".join(ws[-k:]).lower() in self.localities
                       for k in range(1, min(self.max_words, len(ws)) + 1)) if ws else False

        def is_state(tail: str, rest: list[str]) -> bool:
            """A trailing state, allowing for one that is also a town here.

            New York is both. What precedes decides: "North Woodmere, New York" names a town, so
            New York is the state; "350 5th Ave, New York" names an address, so it is the city
            and has to survive as the locality.
            """
            if tail not in self.states:
                return False
            return tail not in self.localities or tail_is_town(rest)

        tail_part = parts[-1].lower().strip(".") if parts else ""
        if is_state(tail_part, " ".join(parts[:-1]).split()):
            p.state = self.states[tail_part]
            parts = parts[:-1]
            words = " ".join(parts).split()
        else:
            # Without commas the state is the last few words, and how many depends on the state:
            # eleven of the fifty are two or more. Testing only the last word left "New York" in
            # "101 Penbrooke Drive Penfield New York", and the town matcher then took it as the
            # town, answering 395 km away in the city.
            for n in range(min(self.max_state_words, len(words)), 0, -1):
                tail = " ".join(words[-n:]).lower().strip(".")
                if is_state(tail, words[:-n]):
                    p.state = self.states[tail]
                    words = words[:-n]
                    if parts and parts[-1].lower().strip(".") == tail:
                        parts = parts[:-1]
                    break
        # town: last comma part if it is a known town, else longest known suffix of words;
        # "number street, Town" keeps an unknown (possibly misspelled) last part as the town,
        # since the SQL compares towns fuzzily.
        if len(parts) >= 2 and (parts[-1].lower() in self.localities or HN_RE.match(parts[0])):
            p.locality = parts[-1]
            head = ", ".join(parts[:-1])
        else:
            head = " ".join(words)
            lw = head.lower().split()
            # the town may be the whole remaining text ("Bangor ME")
            for n in range(min(self.max_words, len(lw)), 0, -1):
                cand = " ".join(lw[-n:])
                if cand in self.localities:
                    p.locality = " ".join(head.split()[-n:])
                    head = " ".join(head.split()[:-n])
                    break
            else:
                # No town spelled this way. Try once more allowing a typo, and only when the
                # misspelling is the whole remaining text: loosed on a trailing word it turns
                # "central park" into the town of Parks. Without this the query parses to
                # nothing and the raw string goes to name matching, where venues carrying their
                # own town in their name ("Albany, NY - Albany.com") beat the town itself.
                fixed = self.town_fuzzy(" ".join(lw), p.state) if lw else None
                if fixed:
                    # the corrected spelling, not what was typed: downstream matching is by
                    # trigram, and a transposition shares almost no trigrams with the word it
                    # came from, so "Tuscon" would never reach Tucson on its own
                    p.locality = fixed
                    head = ""
        head = head.strip(" ,")
        m = HN_RE.match(head)
        if m:
            p.housenumber, p.street = m.group(1), m.group(2).strip(" ,")
        elif head and p.locality:
            p.name = head
        return p
