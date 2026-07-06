"""Shared ISO-8601 parsing for lifecycle timestamps.

Both the alert engine (appointment / follow-up SLA checks) and the
lifecycle service (denormalizing appt/follow-up times onto the Job row)
parse the same free-text-tolerant ISO strings the LLM produces. Kept in
one place so the "Z suffix / naive → UTC" handling stays consistent.
"""

from datetime import UTC, datetime


def parse_iso8601(value: object) -> datetime | None:
    """Parse an ISO-8601 string into a tz-aware datetime, or ``None``.

    Returns ``None`` for non-strings and unparseable values (e.g. the LLM
    stored a free-text phrase like "tomorrow morning"). Naive datetimes are
    assumed UTC; a trailing ``Z`` is accepted.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
