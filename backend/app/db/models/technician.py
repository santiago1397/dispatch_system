"""Technician model — dispatch targets for Jobs.

A Technician is a person (or crew) who receives dispatched jobs in their
own WhatsApp chat. One Technician is linked to one WhatsApp chat via
``whatsapp_chat_jid`` (UNIQUE). When the operator posts a dispatch in
that chat, the system reads the JID → Technician mapping to populate
``jobs.dispatched_to_technician_id``.

Inactive technicians stop receiving new dispatches but their historical
Jobs and events are preserved.
"""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Technician(Base, TimestampMixin):
    """A technician (or crew) who handles dispatched jobs.

    Attributes:
        name: Human-readable display name (operator-facing).
        phone_e164: Direct phone for SMS / voice escalation. Optional —
            most comms go through the dispatch chat.
        whatsapp_chat_jid: The JID of the dispatch chat. UNIQUE so each
            chat maps to at most one technician.
        is_active: Soft-disable flag. Deactivated techs keep their
            history but their chats stop being routed by the dispatch
            detector.
        notes: Operator notes (working hours, regions, specialties).
    """

    __tablename__ = "technicians"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(String(15), nullable=True)
    whatsapp_chat_jid: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Technician(id={self.id}, name={self.name!r}, active={self.is_active})>"
