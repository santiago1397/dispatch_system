"""CompanyUpdate — a status update the operator should relay to the company.

When a technician update lands on a job (``in_progress`` / ``appt_set`` /
``needs_follow_up`` / ``canceled``), we compose the message the operator
should forward to the company that sent the job — the original job message
plus the update — and persist it here as *pending*.

The system never sends it: the operator relays it natively in
WhatsApp/OpenPhone (see ``memory/feedback_no_outbound_automation.md``).
The alert engine watches for the operator's outbound to the company; if it
doesn't appear within ``ALERTS_COMPANY_UPDATE_UNSENT_MINUTES`` the relay is
still ``pending`` and a ``company_update_unsent`` reminder fires.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class CompanyUpdate(Base, TimestampMixin):
    """A pending/sent status relay from the operator to the source company."""

    __tablename__ = "company_updates"
    __table_args__ = (Index("ix_company_updates_sent_at_created_at", "sent_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lifecycle_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_lifecycle_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The tech update that triggered this relay (in_progress / appt_set /
    # needs_follow_up / canceled).
    update_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # How the operator should relay it, and to whom. WhatsApp jobs carry a
    # ``company_chat_jid``; OpenPhone jobs carry a ``company_phone``.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    company_chat_jid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    company_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The composed message: original job body + the update line.
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL while pending; set when the operator's relay is observed.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<CompanyUpdate(id={self.id}, job={self.job_id}, "
            f"kind={self.update_kind}, sent={self.sent_at is not None})>"
        )
