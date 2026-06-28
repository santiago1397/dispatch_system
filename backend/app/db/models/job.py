"""Job model — parent record that clusters related DispatchJob rows by real-world job.

One ``Job`` row exists per real-world dispatch job. A job is the cluster of all
incoming messages (across sources) that describe the same physical work, within
a 14-day window from the first message. Multiple ``DispatchJob`` rows (one per
message) point at the same ``Job`` via ``dispatch_jobs.job_id``.

Dedup keys (address components + job_type + first_message_at window) live on
this table. Raw LLM extractions live on the child ``DispatchJob`` rows.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.dispatch_job import DispatchJob


class Job(Base, TimestampMixin):
    """Parent row for a real-world dispatch job.

    Attributes:
        id: Unique record identifier.
        company_id: FK to the company that owns this job. Nullable during the
            brief window between job creation and classifier completion.
        first_message_at: Timestamp of the first message that opened this job.
            Sticky — never updated. The 14-day dedup window is anchored here.
        address_street_number: House/building number, exact.
        address_street_name: Normalized street name (suffix + direction
            expanded). Indexed because it is a primary dedup key.
        address_city: Lowercased city name.
        address_state: Uppercased 2-letter state code.
        address_zip: First 5 digits of the ZIP, as a string.
        job_type: Job type as extracted from the first-classified message.
            Indexed for dedup and analytics.
        is_duplicate: True when a different company dispatched the same
            address+job_type within the dedup window. Informational only —
            the row is still a real job, just flagged.
        duplicate_of: FK to the first-seen ``Job`` row when ``is_duplicate``
            is true. None otherwise.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    address_street_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_street_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    address_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address_zip: Mapped[str | None] = mapped_column(String(10), nullable=True)
    customer_phone_e164: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    job_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Closing/payment data — populated when a message from the
    # "Dispatch closing" WhatsApp group matches this Job. Estimates from
    # the original job message live on the DispatchJob row; these are the
    # final actuals. ``closed_at IS NOT NULL`` is the canonical closed check.
    closed_total: Mapped[str | None] = mapped_column(String(50), nullable=True)
    closed_parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_tip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    closed_payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    closed_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    closed_from_dispatch_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dispatch_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Lifecycle pipeline state. Populated by Phase-1 migration; written by
    # ``JobLifecycleEvent`` rows. ``lifecycle_status`` is the latest
    # transition's ``to_status`` denormalized for fast filtering.
    # ``lifecycle_status_changed_at`` is the timestamp of the latest
    # transition — anchor for the alert engine's SLA checks.
    lifecycle_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )
    lifecycle_status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Original inbound contact for outbound-draft routing. Frozen at Job
    # creation so the same company phone (OpenPhone) or chat_jid
    # (WhatsApp) is used for every status-update draft the Job generates.
    # Populated by ``JobClassificationService.classify_message`` when the
    # parent Job is created or linked.
    original_inbound_from_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    original_inbound_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)

    company: Mapped["Company | None"] = relationship("Company")
    dispatch_jobs: Mapped[list["DispatchJob"]] = relationship(
        "DispatchJob", back_populates="job", foreign_keys="[DispatchJob.job_id]"
    )

    def __repr__(self) -> str:
        return (
            f"<Job(id={self.id}, company={self.company_id}, "
            f"address={self.address_street_number} {self.address_street_name}, "
            f"is_duplicate={self.is_duplicate})>"
        )
