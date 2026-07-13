"""Pydantic schemas for Alert — pipeline-health open issues."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema, TimestampSchema


class AlertJobSummary(BaseSchema):
    """The parent Job an alert points at, resolved for the dashboard.

    ``dispatch_job_id`` is the originating child row — the operator-facing
    ``/jobs/{id}`` page is keyed by it, not by the parent ``job_id``.
    ``message_preview`` is the first ~200 chars of the message that opened
    the job, so the alert row can show the related message inline.
    """

    job_id: UUID
    dispatch_job_id: UUID | None = None
    company_name: str | None = None
    lifecycle_status: str | None = None
    address: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    job_type: str | None = None
    message_preview: str | None = None
    message_source: str | None = None


class AlertRead(TimestampSchema):
    """A pipeline-health alert (open or resolved)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID | None = None
    chat_jid: str | None = None
    kind: str
    threshold_minutes: int | None = None
    detected_at: datetime
    resolved_at: datetime | None = None
    seen_at: datetime | None = None
    resolved_by_user_id: UUID | None = None
    payload: dict = Field(default_factory=dict)
    # Resolved parent-Job summary (company, address, originating message).
    # Populated by the route; None for chat-bound alerts with no job_id.
    job: AlertJobSummary | None = None


class AlertList(BaseSchema):
    items: list[AlertRead]
    # Unresolved count — the dashboard's "unsolved" figure. Unaffected by
    # whether the operator has viewed the alert yet.
    total: int
    # Unresolved AND unseen — what the navbar badge shows. Always 0 on the
    # ``resolved=true`` (audit) view, since "seen" only tracks the open queue.
    unseen: int = 0


class AlertMarkSeenResult(BaseSchema):
    marked: int
