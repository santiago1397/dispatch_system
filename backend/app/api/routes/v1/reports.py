"""Live per-company job-status report.

Unlike ``/stats``, this is not backed by a precomputed snapshot table —
every request queries ``jobs`` directly, so "today" is always current.
The same endpoint serves day, week, and month views: the caller just
widens ``start_date``/``end_date``.
"""

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.company_report import CompanyReportResponse
from app.services.company_report import get_company_report

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
        default_factory=date.today,
        description="First day (inclusive) of the range. Defaults to today.",
    ),
    end_date: date = Query(
        default_factory=date.today,
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
