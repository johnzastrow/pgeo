"""Build progressively fuzzier accuracy rounds from one fixed base set (seeded).

Output: tests/accuracy/fuzz_cases.json (same case format as cases.json, plus "level").

Base: 300 exact cases taken from cases.json (150 addresses, 50 towns, 50 lakes/summits,
50 venues). Every level corrupts the same base queries, so accuracy can be plotted against
fuzziness per engine:

  F0  exact (baseline)
  F1  one character error
  F2  two character errors (different words where possible)
  F3  three errors + abbreviation flips (St<->Street), commas dropped, lowercase
  F4  F3 + one component dropped (town, suffix or state) and word order changes for places
  F5  heavy: ~25% of letters corrupted, phonetic misspellings (ph->f, ck->k, doubled
      letters collapsed, vowel swaps)

House numbers are never corrupted (the query must stay answerable).
Run from the repo root:  uv run --project prep python tests/accuracy/build_fuzz_rounds.py
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
NEAR = {
    c: "".join(
        KB[r2][c2]
        for r2 in (r - 1, r, r + 1)
        for c2 in (i - 1, i, i + 1)
        if 0 <= r2 < 3 and 0 <= c2 < len(KB[r2]) and (r2, c2) != (r, i)
    )
    for r, row in enumerate(KB)
    for i, c in enumerate(row)
}
ABBR = {
    "street": "st",
    "road": "rd",
    "avenue": "ave",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "mount": "mt",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}
ABBR |= {v: k for k, v in ABBR.items()}
PHONETIC = [("ph", "f"), ("ck", "k"), ("ee", "ea"), ("ou", "ow"), ("tion", "shun"), ("qu", "kw"), ("c", "k")]
VOWELS = "aeiou"


def letter_positions(text: str) -> list[int]:
    """Indexes of letters inside words (never digits, never the first letter of a word)."""
    return [i for i, ch in enumerate(text) if ch.isalpha() and i > 0 and text[i - 1].isalpha()]


def one_error(text: str, rng: random.Random, avoid_words: set[int] | None = None) -> tuple[str, int]:
    chars = list(text)
    idx = letter_positions(text)
    if avoid_words:
        words = word_index(text)
        idx = [i for i in idx if words[i] not in avoid_words] or idx
    if not idx:
        return text, -1
    i = rng.choice(idx)
    op = rng.choice(("swap", "drop", "double", "neighbour"))
    if op == "swap" and i + 1 < len(chars) and chars[i + 1].isalpha():
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    elif op == "drop":
        del chars[i]
    elif op == "double":
        chars.insert(i, chars[i])
    else:
        near = NEAR.get(chars[i].lower())
        if near:
            chars[i] = rng.choice(near)
    return "".join(chars), word_index(text)[i]


def word_index(text: str) -> list[int]:
    out, w = [], 0
    for i, ch in enumerate(text):
        if ch == " " and i > 0 and text[i - 1] != " ":
            w += 1
        out.append(w)
    return out


def errors(text: str, n: int, rng: random.Random) -> str:
    used: set[int] = set()
    for _ in range(n):
        text, w = one_error(text, rng, used)
        used.add(w)
    return text


def flip_abbrev(text: str, rng: random.Random) -> str:
    words = text.split()
    return " ".join(ABBR.get(w.lower(), w) if rng.random() < 0.7 else w for w in words)


def drop_component(case: dict, text: str, rng: random.Random) -> str:
    parts = [p.strip() for p in text.split(",")]
    if case["kind"] == "address" and len(parts) >= 3:
        drop = rng.choice(("town", "state", "suffix"))
        if drop == "town":
            parts = [parts[0], parts[-1]]
        elif drop == "state":
            parts = parts[:-1]
        else:
            words = parts[0].split()
            parts[0] = " ".join(words[:-1]) if len(words) > 2 else parts[0]
        return ", ".join(parts)
    words = text.replace(",", "").split()
    if case["kind"] in ("lake_summit", "venue") and len(words) >= 2:
        return " ".join(words[1:] + words[:1])  # "Moosehead Lake" -> "Lake Moosehead"
    return text.replace(", Maine", "")


def heavy(text: str, rng: random.Random) -> str:
    s = text.lower()
    for a, b in PHONETIC:
        if a in s and rng.random() < 0.5:
            s = s.replace(a, b, 1)
    s = re.sub(r"([a-z])\1", r"\1", s)  # collapse doubled letters
    chars = list(s)
    for i in letter_positions(s):
        if rng.random() < 0.25:
            if chars[i] in VOWELS:
                chars[i] = rng.choice([v for v in VOWELS if v != chars[i]])
            else:
                chars[i] = rng.choice(NEAR.get(chars[i], chars[i]) or chars[i])
    return "".join(chars)


def main() -> None:
    cases = json.loads((HERE / "cases.json").read_text())
    rng = random.Random(4242)
    pools = {
        "address": [c for c in cases if c["kind"] == "address" and c["endpoint"] == "search" and c["qtype"] == "exact"],
        "town": [c for c in cases if c["kind"] == "town" and c["qtype"] == "exact"],
        "lake_summit": [c for c in cases if c["kind"] == "lake_summit" and c["qtype"] == "exact"],
        "venue": [c for c in cases if c["kind"] == "venue" and c["qtype"] == "exact"],
    }
    want = {"address": 150, "town": 50, "lake_summit": 50, "venue": 50}
    base = [c for k, n in want.items() for c in rng.sample(pools[k], min(n, len(pools[k])))]

    out: list[dict] = []
    for level in range(6):
        lrng = random.Random(1000 + level)
        for c in base:
            t = c["params"]["text"]
            if level == 1:
                t = errors(t, 1, lrng)
            elif level == 2:
                t = errors(t, 2, lrng)
            elif level == 3:
                t = errors(flip_abbrev(t, lrng), 3, lrng).replace(",", "").lower()
            elif level == 4:
                t = errors(flip_abbrev(drop_component(c, t, lrng), lrng), 3, lrng).replace(",", "").lower()
            elif level == 5:
                t = heavy(drop_component(c, t, lrng) if lrng.random() < 0.5 else t, lrng)
            out.append(
                {
                    **c,
                    "id": len(out) + 1,
                    "base_id": c["id"],
                    "level": f"F{level}",
                    "qtype": "exact" if level == 0 else "fuzz",
                    "params": {"text": t},
                }
            )
    (HERE / "fuzz_cases.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(len(out), "fuzz cases", {f"F{i}": len(base) for i in range(6)})
    for c in out[:: len(base)][:6]:
        print(f"  {c['level']}: {c['params']['text']}")


if __name__ == "__main__":
    main()
