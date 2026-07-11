"""Pydantic schemas for the per-company job status report.

Unlike ``daily_stats``, this report has no persisted table — every
request is computed live against ``jobs`` (see
``app.services.company_report``), so there is no ``Read`` model tied to
an ORM row here, just the response shape.
"""

from datetime import date
from uuid import UUID

from app.schemas.base import BaseSchema


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
