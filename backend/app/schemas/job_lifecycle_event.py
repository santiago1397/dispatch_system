"""Pydantic schemas for JobLifecycleEvent audit-log reads."""

from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampSchema


class JobLifecycleEventRead(TimestampSchema):
    """A single lifecycle transition for display in the timeline UI."""

    id: UUID
    job_id: UUID
    source: str
    from_status: str
    to_status: str
    payload: dict = Field(default_factory=dict)
    created_by_user_id: UUID | None = None


class JobLifecycleEventList(BaseSchema):
    items: list[JobLifecycleEventRead]
    total: int


class LifecycleTransitionIn(BaseSchema):
    """Request body for ``PATCH /jobs/{id}/lifecycle`` (manual override)."""

    to_status: str = Field(
        ...,
        description=(
            "Target status. Must be a valid LifecycleStatus value. The 'closed' "
            "value is rejected for manual overrides — closing must come through "
            "the CLOSING_CHAT_JID WhatsApp group."
        ),
    )
    note: str | None = Field(
        default=None,
        description="Operator note. Required when to_status='canceled'.",
    )
