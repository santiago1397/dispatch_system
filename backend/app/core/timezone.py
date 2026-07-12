"""Business-day (Chicago, 5am-cutoff) date math.

The dispatch business operates 5:00 AM to midnight America/Chicago each
day. Every "day"/"today"/"week"/"month" concept in reporting (the live
company-status report, the daily-stats cron, and their date defaults)
is defined against this business day, not UTC midnight. Hardcoded per
product decision — single-city operation, no per-org timezone needed.

``app.repositories.job.get_company_status_breakdown`` encodes this same
5am-Chicago rule in SQL (it can't call into Python per-row); the two
must be changed together if the cutoff hour or timezone ever changes.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/Chicago")
BUSINESS_DAY_START = time(5, 0)


def business_now() -> datetime:
    """Current instant, Chicago-aware."""
    return datetime.now(BUSINESS_TZ)


def business_today() -> date:
    """Which business day is "today" right now, given the 5am cutoff.

    Before 5am Chicago, we're still in yesterday's business day.
    """
    now = business_now()
    if now.time() < BUSINESS_DAY_START:
        return (now - timedelta(days=1)).date()
    return now.date()


def business_day_bounds(d: date) -> tuple[datetime, datetime]:
    """UTC-aware ``[start, end)`` for one Chicago business day.

    ``start`` = 5:00 AM Chicago on ``d``; ``end`` = 5:00 AM Chicago on
    ``d + 1``. Both converted to UTC so callers can bind them directly
    against ``timestamptz`` columns.
    """
    start_local = datetime.combine(d, BUSINESS_DAY_START, tzinfo=BUSINESS_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def business_range_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """UTC-aware ``[start, end)`` spanning business days ``start_date..end_date`` inclusive."""
    start, _ = business_day_bounds(start_date)
    _, end = business_day_bounds(end_date)
    return start, end


def business_day_of(dt: datetime) -> date:
    """Which Chicago business day a given (aware) timestamp falls into."""
    local = dt.astimezone(BUSINESS_TZ)
    if local.time() < BUSINESS_DAY_START:
        return (local - timedelta(days=1)).date()
    return local.date()
