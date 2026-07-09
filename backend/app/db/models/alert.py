"""Pipeline alerts — surfaced in the /alerts dashboard only.

Distinct from lifecycle events: alerts are about pipeline health
(stuck jobs, missing closings, unattributed replies, no-match
dispatches), not job state. Resolved alerts stay in the table with
``resolved_at`` set for historical analysis.

Written by the ``AlertEngine.scan`` cron (every 5 minutes) and
directly by Phase-3 ingestion paths when the dispatch detector cannot
match a message to a Job (``dispatch_no_match``) or when tech-reply
attribution is ambiguous (``unattributed_reply``).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class AlertKind(StrEnum):
    # Pending too long — neither dispatched nor rejected within the SLA.
    UNDISPATCHED = "undispatched"
    STUCK_DISPATCHED = "stuck_dispatched"
    STUCK_IN_PROGRESS = "stuck_in_progress"
    APPT_TIME_PASSED = "appt_time_passed"
    # Friendly reminder — a needs_follow_up job's callback time has arrived.
    FOLLOW_UP_DUE = "follow_up_due"
    # The operator hasn't relayed a tech update to the source company yet.
    COMPANY_UPDATE_UNSENT = "company_update_unsent"
    CLOSING_MISSING = "closing_missing"
    # A tech's payment signal marked the job ``completed`` but the operator
    # hasn't filed the closing in the "Dispatch Closing" group yet.
    CLOSING_UNFILED = "closing_unfiled"
    UNATTRIBUTED_REPLY = "unattributed_reply"
    DISPATCH_NO_MATCH = "dispatch_no_match"


class Alert(Base, TimestampMixin):
    """Pipeline health alert. Resolved alerts keep ``resolved_at`` set.

    ``created_at`` is when the alert was first raised. ``resolved_at``
    is when an operator marked it resolved (or when the engine
    auto-resolved it because the underlying condition cleared).
    """

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_kind_resolved_at_idx", "kind", "resolved_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=True,
    )
    chat_jid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    threshold_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=True, default=dict)

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, kind={self.kind}, job_id={self.job_id})>"
