"""Regenerate the anonymized address corpus used by the normalizer tests.

The ZIP-extraction bug (a five-digit house number read as the ZIP) survived
five unit tests because every one of them used ``"123 Main St"``. Real
operator traffic looks nothing like that: five-digit house numbers, no
commas at all, spelled-out ``Illinois``, numbered streets, apartment
markers, trailing directionals. The fix is a corpus that carries those
*shapes* into the test suite.

Customer PII does not belong in git, so the house number and the street
stem are substituted while every structural property the parser depends on
is preserved byte-for-byte:

* the digit *count* of the house number (the whole point of the bug),
* comma count and placement, and stray double spaces,
* directionals, street suffixes, numbered-street tokens (``70th``),
* apartment/unit markers,
* the real city, state and ZIP (not identifying on their own, and the
  state/ZIP correspondence is what the plausibility invariant checks).

Run against production to refresh::

    set -a; . ./.env.prod; set +a
    POSTGRES_HOST=localhost backend/.venv/bin/python \\
        backend/scripts/generate_address_corpus.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "tests" / "fixtures" / "address_corpus.py"

sys.path.insert(0, str(REPO_ROOT))

from app.services.address_normalizer import (  # noqa: E402
    DIRECTIONALS,
    STATE_NAME_TO_CODE,
    STREET_SUFFIXES,
)

#: Neutral stand-in stems, bucketed by length so the substitution keeps the
#: rough shape of the original token.
FAKE_STEMS: dict[int, list[str]] = {
    3: ["oak", "elm", "bay", "fox", "ash"],
    4: ["pine", "lark", "reed", "vale", "cove"],
    5: ["maple", "birch", "heron", "quail", "cedar"],
    6: ["willow", "laurel", "juno", "orchid", "walnut"],
    7: ["clarion", "fenwick", "juniper", "redwood", "sequoia"],
    8: ["braeburn", "cranmore", "hazelton", "kingston", "larkspur"],
}
_LONG_STEMS = ["marbleton", "wintergreen", "ashfordshire", "pennyroyal", "quartermaine"]

_TOKEN_SPLIT_RE = re.compile(r"([A-Za-z0-9]+)")
_LEADING_NUMBER_RE = re.compile(r"^(\d+)")
_NUMBERED_STREET_RE = re.compile(r"^\d+(?:st|nd|rd|th)$", re.IGNORECASE)
_UNIT_WORDS = {
    "apt",
    "apartment",
    "unit",
    "ste",
    "suite",
    "fl",
    "floor",
    "rm",
    "room",
    "bldg",
    "lot",
}


def _seed(text: str) -> int:
    """Deterministic per-address seed — the corpus must not churn on re-run."""
    total = 0
    for ch in text:
        total = (total * 31 + ord(ch)) % 1_000_003
    return total


def _fake_stem(token: str, seed: int, offset: int) -> str:
    """Swap a street-stem token for a neutral one of similar length."""
    bank = FAKE_STEMS.get(len(token), _LONG_STEMS)
    replacement = bank[(seed + offset) % len(bank)]
    return replacement.upper() if token.isupper() else replacement.capitalize()


def _scramble_house_number(number: str, seed: int, forbidden: set[str]) -> str:
    """Substitute the house number, preserving its digit count.

    The digit count is the structural property under test, so it is never
    changed. The result is nudged until it differs from every ZIP in the
    address, so the ``zip != street_number`` invariant can never pass or
    fail for an accidental reason.
    """
    for attempt in range(10):
        digits = [str((int(d) + seed + attempt + i) % 10) for i, d in enumerate(number)]
        if digits[0] == "0":
            digits[0] = "1"
        candidate = "".join(digits)
        if candidate not in forbidden:
            return candidate
    return number


def anonymize(address: str) -> str:
    """Return a structurally identical address with the identifying bits swapped."""
    seed = _seed(address)
    zips = set(re.findall(r"\b\d{5}\b", address))

    # The street stem is everything in the first comma chunk before the last
    # street suffix. Detected independently of the parser under test, so a
    # parser bug cannot quietly shrink the corpus.
    head, sep, tail = address.partition(",")
    parts = _TOKEN_SPLIT_RE.split(head)

    suffix_part_idx = None
    for idx, part in enumerate(parts):
        if idx % 2 == 1 and part.lower() in STREET_SUFFIXES:
            suffix_part_idx = idx

    out: list[str] = []
    stem_seen = 0
    leading_number_done = False
    for idx, part in enumerate(parts):
        if idx % 2 == 0:  # separators (spaces, '#', punctuation) pass through
            out.append(part)
            continue
        lowered = part.lower()
        if not leading_number_done and part.isdigit() and idx == 1:
            out.append(_scramble_house_number(part, seed, zips))
            leading_number_done = True
            continue
        keep = (
            lowered in DIRECTIONALS
            or lowered in STREET_SUFFIXES
            or lowered in STATE_NAME_TO_CODE
            or lowered in _UNIT_WORDS
            or part.isdigit()
            or _NUMBERED_STREET_RE.match(part) is not None
            or len(part) == 1
            or (suffix_part_idx is not None and idx > suffix_part_idx)
        )
        if keep:
            out.append(part)
        else:
            out.append(_fake_stem(part, seed, stem_seen))
            stem_seen += 1

    return "".join(out) + sep + tail


def main() -> None:
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT address FROM dispatch_jobs "
        "WHERE address IS NOT NULL AND address <> '' ORDER BY address"
    )
    addresses = [row[0] for row in cur.fetchall()]

    anonymized = sorted({anonymize(a) for a in addresses})

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        fh.write('"""Anonymized real-shape address corpus — GENERATED, do not hand-edit.\n\n')
        fh.write("Regenerate with ``backend/scripts/generate_address_corpus.py``.\n")
        fh.write("House numbers and street stems are substitutes; comma placement, digit\n")
        fh.write("counts, directionals, suffixes, unit markers, city, state and ZIP are\n")
        fh.write('real. See that script for why.\n"""\n\n')
        fh.write("ADDRESS_CORPUS: list[str] = [\n")
        for addr in anonymized:
            fh.write(f"    {addr!r},\n")
        fh.write("]\n")

    print(f"wrote {len(anonymized)} addresses to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
