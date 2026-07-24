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


class CompanyReassignIn(BaseSchema):
    """Request body for ``PATCH /jobs/{id}/company`` (manual company override).

    Set ``company_id`` to null to detach the job from any company —
    e.g. to correct a misclassification (shared broker phone, wrong regex
    match) that's inflating a company's report count for a job that was
    never really theirs. A detached job keeps its row (for audit) but
    disappears from every company's report, since the report only counts
    jobs with a non-null ``company_id``.
    """

    company_id: UUID | None = Field(
        default=None,
        description="Company to reassign the job to. Null detaches it from any company.",
    )
    note: str = Field(
        ...,
        min_length=1,
        description="Required operator note explaining the correction.",
    )
