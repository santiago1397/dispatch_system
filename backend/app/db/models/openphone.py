"""Incoming message model — unified message source for the classification pipeline.

Originally OpenPhone-specific; now extended to also receive messages derived
from the WhatsApp scraper (see ``app/services/whatsapp.py``). OpenPhone-only
columns are nullable so WhatsApp-sourced rows can leave them empty. The
``source`` column discriminates the two.
"""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.dispatch_job import DispatchJob


class MessageSource(StrEnum):
    """Originating source of an IncomingMessage."""

    OPENPHONE = "openphone"
    WHATSAPP = "whatsapp"


class IncomingMessage(Base, TimestampMixin):
    """Persisted record of an incoming message from OpenPhone or WhatsApp.

    The OpenPhone path (``app/api/routes/v1/openphone.py``) populates the
    OpenPhone-specific fields. The WhatsApp path (see
    ``app/services/whatsapp.py``) populates ``source='whatsapp'`` and leaves
    OpenPhone-specific fields empty.

    Attributes:
        id: Unique record identifier.
        source: Originating channel — see ``MessageSource``.
        openphone_id: The Quo message ID. NULL for WhatsApp rows.
        direction: Message direction (incoming/outgoing). NULL for WhatsApp.
        from_number: Sender phone number. NULL for WhatsApp (no verified
            number is available; the chat title is in ``whatsapp_messages``).
        to_numbers: Recipient phone numbers (JSONB array). Empty for WhatsApp.
        content: Message body text.
        status: Quo message status (queued/sent/delivered/received). NULL
            for WhatsApp.
        event_type: Webhook event type (message.received / message.delivered).
            NULL for WhatsApp.
        phone_number_id: Quo phone number ID that received the message.
            NULL for WhatsApp.
        raw_payload: Full source payload for auditing. OpenPhone uses the
            full webhook payload; WhatsApp uses a small synthesized dict.
    """

    __tablename__ = "incoming_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(
        String(20),
        default=MessageSource.OPENPHONE.value,
        nullable=False,
        index=True,
    )
    openphone_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    from_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_numbers: Mapped[list] = mapped_column(JSONB, nullable=True, default=list)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=True, default=dict)

    # Optional pointer to the lifecycle event this message triggered.
    # NULL for messages that did not trigger a transition (most customer
    # chat noise). One-way FK — lifecycle_event does not have a
    # relationship back to incoming_messages.
    lifecycle_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_lifecycle_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    dispatch_job: Mapped["DispatchJob | None"] = relationship(
        "DispatchJob",
        back_populates="incoming_message",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<IncomingMessage(id={self.id}, source={self.source}, "
            f"openphone_id={self.openphone_id}, event={self.event_type})>"
        )
