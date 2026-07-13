"""Shared ISO-8601 parsing for lifecycle timestamps.

Both the alert engine (appointment / follow-up SLA checks) and the
lifecycle service (denormalizing appt/follow-up times onto the Job row)
parse the same free-text-tolerant ISO strings the LLM produces. Kept in
one place so the "Z suffix / naive → Chicago" handling stays consistent.
"""

from datetime import UTC, datetime

from app.core.timezone import BUSINESS_TZ


def parse_iso8601(value: object) -> datetime | None:
    """Parse an ISO-8601 string into a tz-aware (UTC) datetime, or ``None``.

    Returns ``None`` for non-strings and unparseable values (e.g. the LLM
    stored a free-text phrase like "tomorrow morning"). Every caller feeds
    this LLM-extracted appointment/follow-up text (see the extraction
    prompt in ``classification.py``, which explicitly asks for a naive
    ``"2026-07-10T12:00:00"`` built from the operator-typed local date and
    time window) — never a genuinely UTC-native value. So a naive
    datetime is assumed to be Chicago wall-clock time, not UTC; a
    trailing ``Z`` is still accepted for already-aware values.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BUSINESS_TZ)
    return dt.astimezone(UTC)
