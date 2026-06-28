"""Pydantic schemas for the WhatsApp ingestion module.

Mirrors the OpenPhone schema pattern (``app/schemas/openphone.py``):
``Base`` shared, separate ``Create``/``Update``/``Read``/``List`` classes.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import BaseSchema, TimestampSchema

# =============================================================================
# Tracked Chats (whitelist)
# =============================================================================


class WhatsappTrackedChatBase(BaseSchema):
    """Base schema for a tracked WhatsApp chat."""

    chat_jid: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Stable WhatsApp JID (e.g. '120363...@g.us'). Immutable key.",
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    is_group: bool = True


class WhatsappTrackedChatCreate(WhatsappTrackedChatBase):
    """Schema for adding a new chat to the whitelist."""


class WhatsappTrackedChatUpdate(BaseSchema):
    """Schema for updating a tracked chat (rename, enable/disable, set role)."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    chat_role: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "Routing tag. ``tech_dispatch`` makes the chat a candidate for the "
            "operator-dispatch detector. ``other`` (default) keeps the chat on the "
            "customer-facing mirror + classify path."
        ),
    )


class WhatsappTrackedChatRead(WhatsappTrackedChatBase, TimestampSchema):
    """Schema for reading a tracked chat."""

    id: UUID
    is_active: bool
    chat_role: str
    last_scraped_at: datetime | None = None
    last_seen_message_id: str | None = None


class WhatsappTrackedChatList(BaseSchema):
    """Paginated list of tracked chats."""

    items: list[WhatsappTrackedChatRead]
    total: int


class WhatsappTrackedChatDiscoverItem(BaseSchema):
    """Item surfaced by the extension when it encounters a chat not yet tracked.

    The JID is the source of truth; the display_name is the DOM-extracted
    human-readable label, defaulted at create time.
    """

    chat_jid: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=255)
    is_group: bool = True


# =============================================================================
# Messages
# =============================================================================


class WhatsappMessageCreate(BaseSchema):
    """Schema for ingesting a single message from the Chrome extension.

    No id, no created_at — those are server-assigned. The full schema is
    captured here so the extension's parser can dump a structured object
    and the server can persist it (and so the raw_payload column has a
    typed shape in the API contract).
    """

    wa_message_id: str = Field(..., min_length=1, max_length=100)
    chat_jid: str = Field(..., min_length=1, max_length=100)
    sender_jid: str | None = Field(default=None, max_length=50)
    sender_name: str | None = Field(default=None, max_length=255)
    is_from_me: bool = False
    body: str | None = None
    timestamp: datetime
    edited_at: datetime | None = None
    is_deleted: bool = False
    quoted_wa_message_id: str | None = Field(default=None, max_length=100)
    media_type: str | None = Field(default=None, max_length=30)
    media_mime: str | None = Field(default=None, max_length=100)
    media_filename: str | None = Field(default=None, max_length=500)
    media_size_bytes: int | None = None
    media_caption: str | None = None
    media_url: str | None = Field(default=None, max_length=2000)
    reactions: list[dict] = Field(default_factory=list)
    is_system_message: bool = False
    system_event_type: str | None = Field(default=None, max_length=50)
    raw_payload: dict = Field(default_factory=dict)


class WhatsappMessageRead(TimestampSchema):
    """Schema for reading a persisted WhatsApp message."""

    id: UUID
    wa_message_id: str
    chat_jid: str
    sender_jid: str | None = None
    sender_name: str | None = None
    is_from_me: bool
    body: str | None = None
    timestamp: datetime
    edited_at: datetime | None = None
    is_deleted: bool
    quoted_wa_message_id: str | None = None
    media_type: str | None = None
    media_mime: str | None = None
    media_filename: str | None = None
    media_size_bytes: int | None = None
    media_caption: str | None = None
    media_url: str | None = None
    reactions: list[dict] = Field(default_factory=list)
    is_system_message: bool
    system_event_type: str | None = None
    raw_payload: dict = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class WhatsappMessageList(BaseSchema):
    """Paginated list of WhatsApp messages."""

    items: list[WhatsappMessageRead]
    total: int


# =============================================================================
# Batch Ingest (extension → server)
# =============================================================================


class WhatsappMessageBatchIngest(BaseSchema):
    """Request body for ``POST /api/v1/whatsapp/messages/batch``."""

    messages: list[WhatsappMessageCreate] = Field(..., min_length=1, max_length=500)


class WhatsappMessageBatchError(BaseSchema):
    """Per-item error from a batch ingest (the rest of the batch can still succeed)."""

    index: int
    error: str


class WhatsappMessageBatchResult(BaseSchema):
    """Response body for ``POST /api/v1/whatsapp/messages/batch``.

    ``inserted``: number of new rows created.
    ``updated``: number of existing rows updated (reactions, edits, etc.).
    ``skipped``: number rejected by the timestamp guard (older message tried
    to overwrite a newer one).
    ``deduplicated``: incoming messages removed because the same
    ``(chat_jid, wa_message_id)`` appeared more than once in the batch
    (PostgreSQL cannot process a single row twice in one
    ``ON CONFLICT DO UPDATE`` statement).
    ``errors``: per-item errors (validation, missing tracked chat, etc.).
    """

    inserted: int
    updated: int
    skipped: int
    deduplicated: int = 0
    errors: list[WhatsappMessageBatchError] = Field(default_factory=list)


# =============================================================================
# Service-Account Auth (extension login)
# =============================================================================


class ServiceTokenRequest(BaseSchema):
    """Request body for the service-token exchange route.

    Sent as a header (``X-Service-Api-Key``) in the standard flow, but
    accepting it in the body keeps the door open for future browser-based
    service account creation.
    """

    api_key: str | None = Field(
        default=None, description="sk_live_<32 hex>. Prefer the X-Service-Api-Key header."
    )


class ServiceTokenResponse(BaseModel):
    """Response body for service-token exchange and refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access-token TTL in seconds.")


class ServiceRefreshRequest(BaseSchema):
    """Request body for service-token refresh."""

    refresh_token: str | None = Field(
        default=None, description="Prefer the X-Refresh-Token header."
    )
