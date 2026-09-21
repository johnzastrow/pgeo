"""Nominatim capacity, measured the same way as Pelias, pgeo and Photon.

    python3 tests/load/run_nominatim.py [--base http://127.0.0.1:8081] [--quick]

Nominatim is the reference OpenStreetMap geocoder and the database a Photon index is exported
from, so measuring it closes the OSM side of the comparison (report Section 3.15). Same corpus,
session mix, keystroke pattern, think times, ramp and latency targets as the other engines; only
the URL shape differs (tests/load/k6/session_nominatim.js).

Nominatim has no autocomplete endpoint - its absence is the reason Photon exists - so the
keystroke prefixes go to /search, which is what a client would have to do.
"""

from __future__ import annotations

import sys

from run_alt import main

if __name__ == "__main__":
    sys.exit(main("nominatim", "session_nominatim.js", "http://127.0.0.1:8081"))
