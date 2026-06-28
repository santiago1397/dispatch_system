"""Pydantic schemas for Alert — pipeline-health open issues."""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema, TimestampSchema


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
    resolved_by_user_id: UUID | None = None
    payload: dict = Field(default_factory=dict)


class AlertList(BaseSchema):
    items: list[AlertRead]
    total: int
