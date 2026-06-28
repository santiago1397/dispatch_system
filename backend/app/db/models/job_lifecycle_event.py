"""Append-only audit log of Job lifecycle state transitions.

Each row records one transition: ``from_status`` → ``to_status``, with
``source`` indicating what triggered it (operator WhatsApp message,
parsed tech reply, closing pipeline, manual override, ambiguous
attribution, etc.). The full LLM extraction payload is preserved in
``payload`` so the original intent can be reconstructed on replay.

This is the backbone of the operator-control story: every state change
is auditable, every override is timestamped + attributed to a user.
"""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class LifecycleEventSource(StrEnum):
    """What triggered a lifecycle transition."""

    OPERATOR_WHATSAPP = "operator_whatsapp"
    TECH_WHATSAPP = "tech_whatsapp"
    CLOSING_CHAT = "closing_chat"
    MANUAL = "manual"
    AMBIGUOUS_ATTRIBUTION = "ambiguous_attribution"


class JobLifecycleEvent(Base, TimestampMixin):
    """One row per Job lifecycle state transition.

    Append-only at the application layer (no UPDATE / DELETE in normal
    code paths). ``payload`` carries the LLM extraction (intent,
    ``appt_iso``, totals, notes) and any correlation IDs (``batch_id``,
    ``wa_message_id``) for cross-referencing with ``whatsapp_messages``
    or ``incoming_messages``.
    """

    __tablename__ = "job_lifecycle_events"
    __table_args__ = (
        Index(
            "ix_job_lifecycle_events_job_id_created_at_idx",
            "job_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=True, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<JobLifecycleEvent(job_id={self.job_id}, "
            f"{self.from_status}→{self.to_status}, source={self.source})>"
        )
