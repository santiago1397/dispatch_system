"""WhatsApp Web scraper ingestion models.

Two tables:

* ``whatsapp_tracked_chats`` — the whitelist. One row per WhatsApp group/chat
  the extension is allowed to scrape. Keyed by the stable WhatsApp JID
  (e.g. ``120363...@g.us``) which is extracted from WhatsApp Web's
  ``data-id`` attribute and never changes, even when the user renames the
  group in WhatsApp. ``display_name`` is the human-readable label shown in
  the popup; it is editable and does not affect matching.

* ``whatsapp_messages`` — the persisted scrape output. One row per
  observed message. The dedup key is the pair ``(chat_jid, wa_message_id)``
  so re-opening a chat (or seeing the same message from two browser tabs)
  produces an idempotent upsert. There is intentionally no FK to
  ``dispatch_jobs`` in v1 — adding one later is a single nullable column.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class WhatsappTrackedChat(Base, TimestampMixin):
    """A WhatsApp chat the extension is allowed to scrape.

    Attributes:
        chat_jid: Stable WhatsApp JID, the real key (immutable).
        display_name: Human-readable label (mutable).
        is_group: True for group chats, False for 1:1.
        is_active: Soft-disable flag; deactivated chats keep their history.
        last_scraped_at: Last time the extension wrote messages for this chat.
        last_seen_message_id: Highest wa_message_id observed; hint for incremental
            backfill (not authoritative — the upsert timestamp guard is).
    """

    __tablename__ = "whatsapp_tracked_chats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_jid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_group: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    chat_role: Mapped[str] = mapped_column(
        String(20),
        default="other",
        server_default="other",
        nullable=False,
        index=True,
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WhatsappTrackedChat(jid={self.chat_jid}, "
            f"name={self.display_name!r}, active={self.is_active})>"
        )


class WhatsappMessage(Base, TimestampMixin):
    """A single WhatsApp message scraped from the user's WhatsApp Web tab.

    The dedup key is ``(chat_jid, wa_message_id)``. The repository's
    ``ON CONFLICT`` update includes a ``timestamp`` guard — an older
    message never overwrites a newer one (matters because WhatsApp reuses
    ``wa_message_id`` after a delete-and-resend in the same chat).

    Attributes:
        wa_message_id: The message ID encoded in WhatsApp Web's ``data-id``.
        chat_jid: FK-target-by-convention to ``whatsapp_tracked_chats.chat_jid``.
            No DB-level FK; the extension only scrapes tracked chats anyway.
        sender_jid: Sender's phone/JID if extractable (often hidden in groups).
        sender_name: Display name as shown in the chat.
        is_from_me: True for messages the logged-in user sent.
        body: Text content (NULL for media-only or deleted).
        timestamp: Message send time, from ``data-pre-plain-text`` or
            ``[data-testid=message-time]``. Source of truth for ordering.
        edited_at: Set when the message is edited; bumps via upsert.
        is_deleted: Soft-delete flag; ``body`` becomes NULL when True.
        quoted_wa_message_id: Parent message ID for replies (no DB FK — JIDs
            across chats would not match anyway).
        media_type: ``image|video|document|audio|sticker|location|contact|vcard``
            or NULL for text-only.
        media_mime, media_filename, media_size_bytes, media_caption, media_url:
            Metadata for media. Bytes are NOT downloaded.
        reactions: JSONB list of ``{emoji, sender}`` pairs. Replaced whole on
            each upsert — no per-event audit.
        is_system_message: True for "X joined", "Y changed subject", etc.
        system_event_type: Free-form type string for system messages.
        raw_payload: Full DOM-derived dict for the message (audit/replay).
    """

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_jid",
            "wa_message_id",
            name="uq_whatsapp_messages_chat_jid_wa_message_id_key",
        ),
        Index("ix_whatsapp_messages_chat_jid_timestamp_idx", "chat_jid", "timestamp"),
        Index("ix_whatsapp_messages_timestamp_idx", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wa_message_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    chat_jid: Mapped[str] = mapped_column(String(100), nullable=False)

    sender_jid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_from_me: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    quoted_wa_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    media_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    media_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    media_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    reactions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    is_system_message: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_event_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return (
            f"<WhatsappMessage(jid={self.chat_jid}, "
            f"wa_id={self.wa_message_id}, ts={self.timestamp})>"
        )
