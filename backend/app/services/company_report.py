"""Per-company live job-status report.

Unlike ``daily_stats.py``, this is not precomputed by a nightly cron —
every request queries ``jobs`` directly (see
``app.repositories.job.get_company_status_breakdown``), so "today" is
always current. The date range is caller-supplied and works identically
for a single day, a week, or a month; there is no server-side notion of
"period" beyond ``[start, end)``.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company
from app.repositories.job import get_company_status_breakdown
from app.schemas.company_report import CompanyReportResponse, CompanyReportRow

_BUCKETS = (
    "rejected",
    "closed_completed",
    "scheduled_another_day",
    "canceled",
    "still_open",
)


def _range_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Return ``[start_of(start_date), start_of(end_date + 1 day))`` in UTC.

    ``end_date`` is inclusive from the caller's perspective (e.g. a
    calendar month), so the upper bound is pushed one day past it —
    matching the ``[start, end)`` convention used by ``daily_stats.py``.
    """
    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    end = datetime.combine(end_date, time.min, tzinfo=UTC) + timedelta(days=1)
    return start, end


async def get_company_report(
    db: AsyncSession,
    *,
    start_date: date,
    end_date: date,
) -> CompanyReportResponse:
    """Compute the live per-company status breakdown for ``[start_date, end_date]``."""
    start, end = _range_bounds(start_date, end_date)
    rows = await get_company_status_breakdown(db, start=start, end=end)

    counts_by_company: dict[UUID, dict[str, int]] = {}
    for company_id, bucket, count in rows:
        counts_by_company.setdefault(company_id, dict.fromkeys(_BUCKETS, 0))
        counts_by_company[company_id][bucket] = count

    if not counts_by_company:
        return CompanyReportResponse(start_date=start_date, end_date=end_date, items=[])

    name_rows = (
        await db.execute(
            select(Company.id, Company.display_name, Company.name).where(
                Company.id.in_(counts_by_company.keys())
            )
        )
    ).all()
    company_names = {row.id: (row.display_name or row.name) for row in name_rows}

    items = [
        CompanyReportRow(
            company_id=company_id,
            company_name=company_names.get(company_id, "Unknown"),
            rejected=counts["rejected"],
            closed_completed=counts["closed_completed"],
            scheduled_another_day=counts["scheduled_another_day"],
            canceled=counts["canceled"],
            still_open=counts["still_open"],
            total=sum(counts[b] for b in _BUCKETS),
        )
        for company_id, counts in counts_by_company.items()
    ]
    items.sort(key=lambda row: row.total, reverse=True)

    return CompanyReportResponse(start_date=start_date, end_date=end_date, items=items)
