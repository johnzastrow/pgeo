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


class RuleParser:
    """Small US-address parser. Towns are recognized from the loaded admin names."""

    def __init__(self, localities: set[str], states: dict[str, str] | None = None) -> None:
        self.localities = localities  # normalized, lowercase
        self.max_words = max((len(x.split()) for x in localities), default=1)
        # Lower-case spelling -> postal abbreviation, for every state this build covers: {"me":
        # "ME", "maine": "ME"}. It used to be the literal {"me", "maine"}, which on a New York
        # build left "NY" in the text and the parser then read it as the town, turning
        # "350 5th Ave, New York, NY" into a search for 350 New York Ave.
        self.states = states or {}

    def parse(self, text: str) -> Parsed:
        p = Parsed(text=text)
        s = UNIT_RE.sub(" ", text)
        m = ZIP_RE.search(s)
        if m:
            p.postcode = m.group(1)
            s = s[: m.start()] + s[m.end() :]
        parts = [x.strip() for x in s.split(",") if x.strip()]
        # trailing state
        words = " ".join(parts).split()
        # A state name can be several words ("New York"), so try the comma part before the last
        # word; and never strip a name that is also a town here, or "350 5th Ave, New York" loses
        # its locality.
        tail_part = parts[-1].lower().strip(".") if parts else ""
        tail_word = words[-1].lower().strip(".") if words else ""
        if tail_part in self.states and tail_part not in self.localities:
            p.state = self.states[tail_part]
            parts = parts[:-1]
            words = " ".join(parts).split()
        elif tail_word in self.states and tail_word not in self.localities:
            p.state = self.states[tail_word]
            words = words[:-1]
            if parts and parts[-1].lower().strip(".") == tail_word:
                parts = parts[:-1]
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
        head = head.strip(" ,")
        m = HN_RE.match(head)
        if m:
            p.housenumber, p.street = m.group(1), m.group(2).strip(" ,")
        elif head and p.locality:
            p.name = head
        return p
