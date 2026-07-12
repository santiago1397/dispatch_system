"""Live per-company job-status report.

Unlike ``/stats``, this is not backed by a precomputed snapshot table —
every request queries ``jobs`` directly, so "today" is always current.
The same endpoint serves day, week, and month views: the caller just
widens ``start_date``/``end_date``.
"""

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DBSession
from app.core.timezone import business_today
from app.schemas.company_report import (
    REPORT_BUCKETS,
    CompanyReportJobsResponse,
    CompanyReportResponse,
)
from app.services.company_report import get_company_report, get_company_report_jobs

router = APIRouter()

_MAX_RANGE_DAYS = 92  # a few days over 3 calendar months of slack


@router.get(
    "/company-status",
    response_model=CompanyReportResponse,
    summary="Live per-company job status breakdown (rejected/closed.../canceled/open)",
)
async def company_status_report(
    db: DBSession,
    _user: CurrentUser,
    start_date: date = Query(
        default_factory=business_today,
        description="First day (inclusive) of the range. Defaults to today.",
    ),
    end_date: date = Query(
        default_factory=business_today,
        description="Last day (inclusive) of the range. Defaults to today.",
    ),
):
    """Bucket every job that arrived in ``[start_date, end_date]`` into
    rejected / closed-or-completed / scheduled-for-another-day / canceled /
    still-open, grouped by company. Computed live on every call — pass the
    same single date for a "today" view, or widen the range for a week/
    month rollup.
    """
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )
    if (end_date - start_date) > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"date range cannot exceed {_MAX_RANGE_DAYS} days",
        )
    return await get_company_report(db, start_date=start_date, end_date=end_date)


@router.get(
    "/company-status/jobs",
    response_model=CompanyReportJobsResponse,
    summary="Jobs behind one company/bucket cell of the company-status report",
)
async def company_status_report_jobs(
    db: DBSession,
    _user: CurrentUser,
    company_id: UUID = Query(description="Company to drill into."),
    bucket: str | None = Query(
        default=None,
        description=(
            f"One of: {', '.join(REPORT_BUCKETS)}. Omit for the company's "
            '"Total" column — every job in range, across all buckets.'
        ),
    ),
    start_date: date = Query(
        default_factory=business_today,
        description="First day (inclusive) of the range. Defaults to today.",
    ),
    end_date: date = Query(
        default_factory=business_today,
        description="Last day (inclusive) of the range. Defaults to today.",
    ),
):
    """List the individual jobs classified into ``bucket`` for ``company_id``
    within ``[start_date, end_date]`` — lets an operator confirm the
    per-company report counts are classifying jobs correctly. Omit
    ``bucket`` for the company's "Total" column.
    """
    if bucket is not None and bucket not in REPORT_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"bucket must be one of: {', '.join(REPORT_BUCKETS)}",
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )
    if (end_date - start_date) > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"date range cannot exceed {_MAX_RANGE_DAYS} days",
        )
    return await get_company_report_jobs(
        db,
        start_date=start_date,
        end_date=end_date,
        company_id=company_id,
        bucket=bucket,
    )
