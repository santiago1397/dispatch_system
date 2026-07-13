"""Per-company live job-status report.

Unlike ``daily_stats.py``, this is not precomputed by a nightly cron —
every request queries ``jobs`` directly (see
``app.repositories.job.get_company_status_breakdown``), so "today" is
always current. The date range is caller-supplied and works identically
for a single day, a week, or a month; there is no server-side notion of
"period" beyond ``[start, end)``.

"Day" here means the Chicago business day (5am-to-midnight), not UTC
midnight — see ``app.core.timezone``.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import business_range_bounds
from app.db.models.company import Company
from app.repositories.job import get_company_status_breakdown, get_company_status_jobs
from app.schemas.company_report import (
    REPORT_BUCKETS,
    CompanyReportJobRow,
    CompanyReportJobsResponse,
    CompanyReportResponse,
    CompanyReportRow,
)

_BUCKETS = REPORT_BUCKETS


async def get_company_report(
    db: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    include_scheduled_appts: bool = False,
) -> CompanyReportResponse:
    """Compute the live per-company status breakdown for ``[start_date, end_date]``.

    ``include_scheduled_appts`` widens each company's job set to also pull
    in jobs that arrived on a different day but whose appointment lands in
    range (see ``app.repositories.job._in_range_membership``) — so a job
    scheduled for today, however it currently stands, counts toward
    today's report even if it was dispatched last week.
    """
    start, end = business_range_bounds(start_date, end_date)
    rows = await get_company_status_breakdown(
        db, start=start, end=end, include_scheduled_appts=include_scheduled_appts
    )

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


async def get_company_report_jobs(
    db: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    company_id: UUID,
    bucket: str | None,
    include_scheduled_appts: bool = False,
) -> CompanyReportJobsResponse:
    """Drill-down for one company/bucket cell — or the company's "Total"
    column when ``bucket`` is ``None`` — of ``get_company_report``.

    Reuses the same bucket classification the breakdown counts are built
    from (``app.repositories.job._status_bucket_case``), so this can never
    disagree with the count an operator is trying to verify. Pass the same
    ``include_scheduled_appts`` the breakdown call used, so the job list
    matches the count being drilled into.
    """
    start, end = business_range_bounds(start_date, end_date)
    rows = await get_company_status_jobs(
        db,
        start=start,
        end=end,
        company_id=company_id,
        bucket=bucket,
        include_scheduled_appts=include_scheduled_appts,
    )

    company_row = (
        await db.execute(select(Company.display_name, Company.name).where(Company.id == company_id))
    ).first()
    company_name = (company_row.display_name or company_row.name) if company_row else "Unknown"

    return CompanyReportJobsResponse(
        start_date=start_date,
        end_date=end_date,
        company_id=company_id,
        company_name=company_name,
        bucket=bucket or "total",
        items=[CompanyReportJobRow(**row) for row in rows],
    )
