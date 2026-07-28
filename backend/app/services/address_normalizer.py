"""Address normalization for the dedup lookup.

The dedup is a strict component match (street_number exact, street_name
normalized, city/zip optional). This module parses a free-form US-style
address string into the components used as dedup keys, and normalizes the
street name so ``"123 N Main St"`` and ``"123 North Main Street"`` collapse
to the same key.

The normalizer is best-effort. If parsing fails (no leading digits, no
recognizable state/zip, etc.), the returned components are ``None`` and
the caller should treat the row as ineligible for the dedup lookup.

Parsing rules — all three exist because the naive version of each shipped a
silent data bug. ``tests/test_address_normalizer.py`` asserts them as
invariants over a corpus of real address *shapes* rather than as one-off
examples, because the original tests all used ``"123 Main St"`` and the
bugs below are unreachable with a three-digit house number:

1. **The ZIP is never scanned for inside the street number.** The house
   number is consumed first, then the ZIP is the *last* 5-digit token in
   what remains. Scanning the whole string took the first 5-digit token,
   so ``"12650 Wisteria Ct, Palos Park, IL, 60464"`` parsed as
   ``zip=12650`` — the house number. 13% of production jobs were affected.

2. **The state is matched against a known set**, spelled-out names
   included. The old ``[A-Z]{2}`` regex required two consecutive capitals,
   so ``"Chicago, Illinois 60622"`` yielded ``state=None`` — 10% of
   production jobs — which also silently disabled the ZIP plausibility
   warning below.

3. **The street name stops at the last street suffix.** Without commas,
   ``"7215 W Peterson Ave Chicago Illinois 60631"`` swallowed the city and
   state into ``street_name``, and ``street_name`` is a dedup key — those
   jobs could never match a correctly-formatted repeat of the same address.

Region knowledge (Illinois/Indiana ZIP prefixes) is a *smoke detector*, not
a parser input: an implausible ZIP is logged, never rewritten or dropped.
An out-of-region job must still parse correctly.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

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

#: Spelled-out state name -> USPS code. Operators type both forms
#: interchangeably ("Chicago, IL" and "Chicago, Illinois" in the same day),
#: and matching only the two-letter form left 10% of jobs with no state.
STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "wisconsin": "WI",
    "wyoming": "WY",
}

#: Valid USPS codes. Kept separate from the name map so a bare two-letter
#: token is only accepted when it is a real state — the old regex happily
#: read a directional ("NE") or an abbreviation ("DR") as a state.
VALID_STATE_CODES: frozenset[str] = frozenset(STATE_NAME_TO_CODE.values()) | frozenset(
    {"DC", "NH", "NJ", "NM", "NY", "NC", "ND", "RI", "SC", "SD", "WV"}
)

#: ZIP prefixes per state, for the *plausibility warning only*. Deliberately
#: covers just the service region — a state absent from this map is never
#: warned about, so an out-of-region job stays silent rather than noisy.
ZIP_PREFIX_BY_STATE: dict[str, tuple[str, ...]] = {
    "IL": ("60", "61", "62"),
    "IN": ("46", "47"),
    "WI": ("53", "54"),
    "MI": ("48", "49"),
}

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_STREET_NUMBER_RE = re.compile(r"^(\d+[A-Za-z]?)\s+(.+)$")
#: Alphanumeric so numbered streets survive — the alphabetic-only version
#: turned "70th" into "th".
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_PHONE_DIGITS_RE = re.compile(r"\D+")
#: A unit/apartment designator and everything after it is not part of the
#: street name. Without this, "#1006" contributes a bare "1006" token now
#: that the tokenizer is alphanumeric, and "Apt B" always did.
_UNIT_MARKER_RE = re.compile(
    r"(?:#|\b(?:apt|apartment|unit|ste|suite|fl|floor|rm|room|bldg|lot)\b\.?)",
    re.IGNORECASE,
)


def normalize_phone(raw: str | None) -> str | None:
    """Reduce a US phone string to 10 digits for exact-match dedup.

    Strips everything non-numeric, drops a leading "1" country code if
    present. Returns ``None`` when fewer than 10 digits remain — those
    inputs cannot drive a reliable phone-based dedup match.
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


def _state_code(token: str) -> str | None:
    """Return the USPS code for a token that names a state, else ``None``."""
    lowered = token.lower()
    if lowered in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[lowered]
    upper = token.upper()
    if len(upper) == 2 and upper in VALID_STATE_CODES:
        return upper
    return None


def zip_is_plausible(state: str | None, zip_code: str | None) -> bool | None:
    """Does ``zip_code`` fall in the ZIP range of ``state``?

    Returns ``None`` when the question cannot be answered — no state, no
    ZIP, or a state outside the service region. Callers must treat ``None``
    as "no opinion", never as a failure.
    """
    if not state or not zip_code:
        return None
    prefixes = ZIP_PREFIX_BY_STATE.get(state)
    if not prefixes:
        return None
    return zip_code.startswith(prefixes)


def normalize_street_name(name: str) -> str:
    """Normalize a street name to a canonical form.

    Lowercases, strips punctuation, expands suffixes (``St -> Street``) and
    directional prefixes (``N -> North``). The first directional and the
    suffix are detected positionally: a leading token is a directional, a
    trailing token is a suffix, and the rest is the stem.
    """
    return _street_name_from_tokens(_TOKEN_RE.findall(name))


def _street_name_from_tokens(tokens: list[str]) -> str:
    """Shared tail of :func:`normalize_street_name`, for pre-split tokens."""
    tokens = [t.lower() for t in tokens]
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


def _split_street_and_city(tokens: list[str]) -> tuple[list[str], str | None, list[str]]:
    """Split tokens into (street tokens, post-directional, trailing city tokens).

    The street name ends at the *last* street suffix — everything after it
    is the city. ``"W Peterson Ave Chicago"`` splits into
    ``["W", "Peterson", "Ave"]`` + ``["Chicago"]``. When no suffix is
    present the split is not determinable, so all tokens stay with the
    street and no city is inferred: better a missing city than one carved
    out of the street name.

    Two trailing forms are *not* cities and must stay attached, or two
    distinct streets collapse onto one dedup key:

    * a post-directional — ``"Lake Shore Dr N"`` vs ``"... Dr S"``;
    * a single letter — Chicago's lettered streets, ``"South Avenue O"``.
    """
    suffix_idx = None
    for idx, token in enumerate(tokens):
        if token.lower() in STREET_SUFFIXES:
            suffix_idx = idx
    if suffix_idx is None or suffix_idx == len(tokens) - 1:
        return tokens, None, []

    trailing = tokens[suffix_idx + 1 :]
    if len(trailing) == 1:
        lowered = trailing[0].lower()
        if lowered in DIRECTIONALS:
            return tokens[: suffix_idx + 1], DIRECTIONALS[lowered], []
        if len(lowered) == 1:
            return tokens, None, []
    return tokens[: suffix_idx + 1], None, trailing


def _find_state(
    chunks: list[str], street_tokens: list[str]
) -> tuple[str | None, int | None, int | None]:
    """Locate the state.

    Returns ``(code, chunk_index, token_index_within_chunk)``. For a
    comma-separated address the state is searched in the chunks *after* the
    street line, last match winning, so a street named "Indiana Ave" in
    Chicago cannot be mistaken for the state. For a comma-less address the
    search is limited to the final three tokens, where a state can actually
    appear.
    """
    if len(chunks) >= 2:
        for idx in range(len(chunks) - 1, 0, -1):
            tokens = _TOKEN_RE.findall(chunks[idx])
            for token_idx in range(len(tokens) - 1, -1, -1):
                code = _state_code(tokens[token_idx])
                if code:
                    return code, idx, token_idx
        return None, None, None

    # Comma-less: only the tail of the string can hold a state.
    for token_idx in range(len(street_tokens) - 1, max(len(street_tokens) - 4, -1), -1):
        code = _state_code(street_tokens[token_idx])
        if code:
            return code, 0, token_idx
    return None, None, None


def normalize_address(raw: str) -> NormalizedAddress:
    """Parse a US-style address string into its components.

    Best-effort. Any component the parser cannot determine stays ``None``.
    """
    if not raw or not raw.strip():
        return NormalizedAddress(
            raw=raw or "",
            street_number=None,
            street_name=None,
            city=None,
            state=None,
            zip_code=None,
        )

    text = raw.strip()
    chunks = [c.strip() for c in text.split(",") if c.strip()]
    if not chunks:
        return NormalizedAddress(
            raw=raw, street_number=None, street_name=None, city=None, state=None, zip_code=None
        )

    # --- Street number, consumed first so it can never be read as a ZIP ---
    street_number: str | None = None
    street_remainder = chunks[0]
    match = _STREET_NUMBER_RE.match(chunks[0])
    if match:
        street_number = match.group(1)
        street_remainder = match.group(2)

    # The ZIP is searched for in everything *after* the street line — never
    # inside it. With commas the street line is chunk 0 and is excluded
    # wholesale, which also covers a truncated address whose street line is
    # a bare number ("15298, Auburn Gresham, Chicago, IL"): that number is a
    # house number with a missing street, not a ZIP. Without commas the
    # whole address is one chunk, so only the house number is excluded.
    if len(chunks) >= 2:
        zip_search_area = text.partition(",")[2]
    elif street_number and text.startswith(street_number):
        zip_search_area = text[len(street_number) :]
    else:
        zip_search_area = text
    zip_matches = _ZIP_RE.findall(zip_search_area)
    zip_code = zip_matches[-1] if zip_matches else None

    # A unit designator is not part of the street name.
    street_remainder = _UNIT_MARKER_RE.split(street_remainder, maxsplit=1)[0]
    street_tokens = _TOKEN_RE.findall(street_remainder)

    state, state_chunk_idx, state_token_idx = _find_state(chunks, street_tokens)

    # For a comma-less address the state (and the ZIP after it) sit inside
    # the street line — cut them off before deciding where the street ends.
    if state is not None and state_chunk_idx == 0 and len(chunks) == 1:
        street_tokens = street_tokens[:state_token_idx]

    street_tokens, post_directional, trailing_city_tokens = _split_street_and_city(street_tokens)

    street_name: str | None = None
    if match:
        street_name = _street_name_from_tokens(street_tokens) or None
        if street_name and post_directional:
            street_name = f"{street_name} {post_directional}"

    # --- City, strongest source first ---
    city: str | None = None
    if state_chunk_idx is not None and state_chunk_idx > 0:
        state_chunk_tokens = _TOKEN_RE.findall(chunks[state_chunk_idx])
        leading = [t for t in state_chunk_tokens[: state_token_idx or 0] if not t.isdigit()]
        if leading:
            # "Schaumburg IL 60173" — the city shares the chunk with the state.
            city = " ".join(t.lower() for t in leading)
        elif state_chunk_idx >= 2:
            # "..., Palos Park, IL, 60464" — the city is its own chunk.
            city = chunks[state_chunk_idx - 1].lower()
    if city is None and trailing_city_tokens:
        city = " ".join(t.lower() for t in trailing_city_tokens)

    normalized = NormalizedAddress(
        raw=raw,
        street_number=street_number,
        street_name=street_name,
        city=city,
        state=state,
        zip_code=zip_code,
    )
    _warn_if_suspicious(normalized)
    return normalized


def _warn_if_suspicious(addr: NormalizedAddress) -> None:
    """Log — never correct — a parse that looks wrong.

    WARNING level on purpose: ``app/main.py`` only configures logging when
    ``ENVIRONMENT != "production"``, so in production Python's last-resort
    handler emits WARNING and above to stderr while INFO is invisible.
    """
    if addr.zip_code and addr.street_number and addr.zip_code == addr.street_number:
        logger.warning(
            "ADDR_ZIP_EQUALS_STREET_NUMBER zip=%s addr=%r — house number read as the ZIP",
            addr.zip_code,
            addr.raw,
        )
    if zip_is_plausible(addr.state, addr.zip_code) is False:
        logger.warning(
            "ADDR_ZIP_IMPLAUSIBLE state=%s zip=%s addr=%r — ZIP outside the range of the state",
            addr.state,
            addr.zip_code,
            addr.raw,
        )
