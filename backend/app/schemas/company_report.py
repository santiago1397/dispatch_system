"""Pydantic schemas for the per-company job status report.

Unlike ``daily_stats``, this report has no persisted table — every
request is computed live against ``jobs`` (see
``app.services.company_report``), so there is no ``Read`` model tied to
an ORM row here, just the response shape.
"""

from datetime import date, datetime
from uuid import UUID

from app.schemas.base import BaseSchema

REPORT_BUCKETS: tuple[str, ...] = (
    "rejected",
    "closed_completed",
    "scheduled_another_day",
    "canceled",
    "still_open",
)


class CompanyReportRow(BaseSchema):
    """One company's job-status breakdown for the requested date range."""

    company_id: UUID
    company_name: str
    rejected: int
    closed_completed: int
    scheduled_another_day: int
    canceled: int
    still_open: int
    total: int


class CompanyReportResponse(BaseSchema):
    start_date: date
    end_date: date
    items: list[CompanyReportRow]


class CompanyReportJobRow(BaseSchema):
    """One job behind a single company/bucket cell of the report — lets an
    operator confirm a job landed in the right bucket."""

    job_id: UUID
    dispatch_job_id: UUID | None
    bucket: str
    lifecycle_status: str
    matched_by: str
    """``"arrival"`` if ``first_message_at`` put this job in range, or
    ``"appointment"`` if it only qualifies because ``appt_at`` lands in
    range (it arrived on a different day) — set only when the caller
    passed ``include_scheduled_appts=True``."""
    first_message_at: datetime
    appt_at: datetime | None
    address: str | None
    customer_name: str | None
    customer_phone: str | None
    job_type: str | None
    message_preview: str | None


class CompanyReportJobsResponse(BaseSchema):
    """``bucket`` is one of ``REPORT_BUCKETS``, or ``"total"`` for the
    unfiltered "Total" column — every job for the company in range, across
    all buckets."""

    start_date: date
    end_date: date
    company_id: UUID
    company_name: str
    bucket: str
    items: list[CompanyReportJobRow]
