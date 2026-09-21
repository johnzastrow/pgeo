"""Photon capacity, measured the same way as Pelias, pgeo and Nominatim.

    python3 tests/load/run_photon.py [--base http://127.0.0.1:2322] [--quick]

Photon is the type-ahead specialist, and autocomplete is the endpoint that limits pgeo on every
configuration, so this is the comparison that presses hardest on pgeo's weakest axis (report
Section 3.14). Same corpus, session mix, keystroke pattern, think times, ramp and latency targets
as the other engines; only the URL shape differs (tests/load/k6/session_photon.js).

The shared ramp lives in run_alt.py.
"""

from __future__ import annotations

import sys

from run_alt import main

if __name__ == "__main__":
    sys.exit(main("photon", "session_photon.js", "http://127.0.0.1:2322"))
