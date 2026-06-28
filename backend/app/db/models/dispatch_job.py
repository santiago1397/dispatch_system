"""DispatchJob model for classified job messages."""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.job import Job
    from app.db.models.openphone import IncomingMessage


class ClassificationStatus(StrEnum):
    PENDING = "pending"
    CLASSIFIED = "classified"
    LINKED = "linked"
    FAILED = "failed"
    NOT_A_JOB = "not_a_job"
    # Closing-message pipeline (messages from the "Dispatch closing" group).
    # CLOSED: closing message matched to an original Job; Job.closed_at set.
    # CLOSING_UNMATCHED: closing extracted, but no original Job found within
    # the dedup window. Extracted amounts are kept in extraction_raw so the
    # operator can manually rematch later.
    CLOSED = "closed"
    CLOSING_UNMATCHED = "closing_unmatched"


class DispatchJob(Base, TimestampMixin):
    """Per-message classification record.

    One row per ``IncomingMessage`` that has been processed by the
    classification pipeline. The same real-world job can produce multiple
    ``DispatchJob`` rows (one per message) — they share a parent via
    ``job_id``. The 1:1 relationship to ``IncomingMessage`` is preserved.
    """

    __tablename__ = "dispatch_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incoming_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incoming_messages.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    classification_status: Mapped[str] = mapped_column(
        String(20),
        default=ClassificationStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    classification_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    classification_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extracted fields (LLM output, denormalized for fast query)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tech_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    car_make: Mapped[str | None] = mapped_column(String(50), nullable=True)
    car_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    car_year: Mapped[str | None] = mapped_column(String(10), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scheduled_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full AI response for debugging
    extraction_raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    incoming_message: Mapped["IncomingMessage"] = relationship(
        "IncomingMessage", back_populates="dispatch_job"
    )
    company: Mapped["Company | None"] = relationship("Company")
    job: Mapped["Job | None"] = relationship(
        "Job", back_populates="dispatch_jobs", foreign_keys="[DispatchJob.job_id]"
    )

    def __repr__(self) -> str:
        return (
            f"<DispatchJob(id={self.id}, status={self.classification_status}, "
            f"company={self.company_id}, job={self.job_id})>"
        )
