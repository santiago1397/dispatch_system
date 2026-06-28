"""Address normalization for the dedup lookup.

The dedup is a strict component match (street_number exact, street_name
normalized, city/zip optional). This module parses a free-form US-style
address string into the components used as dedup keys, and normalizes the
street name so ``"123 N Main St"`` and ``"123 North Main Street"`` collapse
to the same key.

The normalizer is best-effort. If parsing fails (no leading digits, no
recognizable state/zip, etc.), the returned components are ``None`` and
the caller should treat the row as ineligible for the dedup lookup.
"""

import re
from dataclasses import dataclass

STREET_SUFFIXES: dict[str, str] = {
    "st": "street",
    "street": "street",
    "ave": "avenue",
    "avenue": "avenue",
    "blvd": "boulevard",
    "boulevard": "boulevard",
    "dr": "drive",
    "drive": "drive",
    "ln": "lane",
    "lane": "lane",
    "rd": "road",
    "road": "road",
    "way": "way",
    "ct": "court",
    "court": "court",
    "pl": "place",
    "place": "place",
    "pkwy": "parkway",
    "parkway": "parkway",
    "ter": "terrace",
    "terrace": "terrace",
    "cir": "circle",
    "circle": "circle",
    "hwy": "highway",
    "highway": "highway",
}

DIRECTIONALS: dict[str, str] = {
    "n": "north",
    "north": "north",
    "s": "south",
    "south": "south",
    "e": "east",
    "east": "east",
    "w": "west",
    "west": "west",
    "ne": "northeast",
    "northeast": "northeast",
    "nw": "northwest",
    "northwest": "northwest",
    "se": "southeast",
    "southeast": "southeast",
    "sw": "southwest",
    "southwest": "southwest",
}

_US_STATE_RE = re.compile(r"\b([A-Za-z]{2})\b")
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_STREET_NUMBER_RE = re.compile(r"^(\d+[A-Za-z]?)\s+(.+)$")
_TOKEN_RE = re.compile(r"[A-Za-z]+")
_PHONE_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Reduce a US phone string to 10 digits for exact-match dedup.

    Strips everything non-numeric, drops a leading "1" country code if
    present. Returns ``None`` when fewer than 10 digits remain — those
    inputs can't drive a reliable phone-based dedup match.
    """
    if not raw:
        return None
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


@dataclass(frozen=True)
class NormalizedAddress:
    """Parsed address components used as dedup keys.

    All fields are best-effort. ``None`` means the parser could not extract
    that component from the input. The caller decides what to do with
    partial parses — for the dedup lookup, missing ``street_number`` or
    ``street_name`` disqualifies the candidate.
    """

    raw: str
    street_number: str | None
    street_name: str | None
    city: str | None
    state: str | None
    zip_code: str | None


def normalize_street_name(name: str) -> str:
    """Normalize a street name to a canonical form.

    Lowercases, strips punctuation, expands suffixes (``St → Street``) and
    directional prefixes (``N → North``). The first directional and the
    suffix are detected positionally: a leading token is a directional, a
    trailing token is a suffix, and the rest is the stem.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(name)]
    if not tokens:
        return ""

    directional = ""
    if tokens[0] in DIRECTIONALS:
        directional = DIRECTIONALS[tokens[0]]
        tokens = tokens[1:]

    suffix = ""
    if tokens and tokens[-1] in STREET_SUFFIXES:
        suffix = STREET_SUFFIXES[tokens[-1]]
        tokens = tokens[:-1]

    stem = " ".join(tokens)
    parts = [p for p in (directional, stem, suffix) if p]
    return " ".join(parts)


def normalize_address(raw: str) -> NormalizedAddress:
    """Parse a US-style address string into its components.

    Best-effort. Strips the input, splits on commas, takes the first chunk
    as the street line, and walks subsequent chunks for city/state/zip. Any
    missing component stays ``None``.
    """
    if not raw:
        return NormalizedAddress(
            raw="", street_number=None, street_name=None, city=None, state=None, zip_code=None
        )

    text = raw.strip()
    chunks = [c.strip() for c in text.split(",") if c.strip()]

    street_number: str | None = None
    street_name: str | None = None

    if chunks:
        street_line = chunks[0]
        m = _STREET_NUMBER_RE.match(street_line)
        if m:
            street_number = m.group(1)
            street_name = normalize_street_name(m.group(2))
        else:
            # No leading number — address doesn't fit our component match.
            street_name = None

    zip_m = _ZIP_RE.search(text)
    zip_code = zip_m.group(1) if zip_m else None

    state: str | None = None
    state_chunk_idx: int | None = None
    # Search for the state in chunks after the street line. Skipping index 0
    # avoids matching a 2-letter directional ("N") or suffix ("Dr") in the
    # street chunk. A bare "IL 60601" without a separate city chunk leaves
    # city = None — that's accepted: dedup with city is opportunistic.
    for idx in range(1, len(chunks)):
        m = re.search(r"\b([A-Z]{2})\b", chunks[idx])
        if m:
            state = m.group(1)
            state_chunk_idx = idx
            break

    city: str | None = None
    if state_chunk_idx is not None and state_chunk_idx >= 2:
        city = chunks[state_chunk_idx - 1].lower()

    return NormalizedAddress(
        raw=raw,
        street_number=street_number,
        street_name=street_name,
        city=city,
        state=state,
        zip_code=zip_code,
    )
