"""Schemas for the Quo (OpenPhone) API integration."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.schemas.base import BaseSchema, TimestampSchema

# =============================================================================
# Quo API Response Schemas
# =============================================================================


class OpenPhoneUser(BaseSchema):
    """User from the Quo API."""

    id: str
    email: str
    first_name: str
    last_name: str
    picture_url: str | None = None
    role: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OpenPhonePhoneNumberUser(BaseSchema):
    """User associated with a phone number."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    group_id: str | None = None


class OpenPhoneRestrictions(BaseSchema):
    """Calling/messaging restrictions for a phone number."""

    calling: dict[str, str] | None = None
    messaging: dict[str, str] | None = None


class OpenPhonePhoneNumber(BaseSchema):
    """Phone number from the Quo API."""

    id: str
    group_id: str | None = None
    port_request_id: str | None = None
    formatted_number: str | None = None
    forward: str | None = None
    name: str | None = None
    number: str | None = None
    porting_status: str | None = None
    symbol: str | None = None
    users: list[OpenPhonePhoneNumberUser] = Field(default_factory=list)
    restrictions: OpenPhoneRestrictions | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OpenPhoneMessage(BaseSchema):
    """Message from the Quo API.

    Quo sends camelCase JSON; aliases map to our snake_case fields.
    """

    id: str
    to: list[str] = Field(default_factory=list)
    from_number: str | None = Field(None, alias="from")
    text: str | None = Field(None, validation_alias="body")
    phone_number_id: str | None = Field(None, alias="phoneNumberId")
    direction: str | None = None
    user_id: str | None = Field(None, alias="userId")
    status: str | None = None
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _accept_text_or_body(cls, values):
        if not isinstance(values, dict):
            return values
        # Quo uses "body" for SMS text; some payload shapes use "text".
        if "text" not in values and "body" in values:
            values["text"] = values["body"]
        # Quo can send "to" as a single string for 1-recipient SMS; coerce to list.
        to = values.get("to")
        if isinstance(to, str):
            values["to"] = [to]
        return values


class OpenPhoneWebhook(BaseSchema):
    """Webhook from the Quo API."""

    id: str
    user_id: str | None = None
    org_id: str | None = None
    label: str | None = None
    status: str | None = None
    url: str | None = None
    key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    events: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)


class OpenPhoneConversation(BaseSchema):
    """Conversation from the Quo API."""

    id: str
    assigned_to: str | None = None
    created_at: datetime | None = None
    deleted_at: datetime | None = None
    last_activity_at: datetime | None = None
    last_activity_id: str | None = None
    muted_until: datetime | None = None
    name: str | None = None
    participants: list[str] = Field(default_factory=list)
    phone_number_id: str | None = None
    snoozed_until: datetime | None = None
    updated_at: datetime | None = None


class QuoPaginatedResponse(BaseSchema):
    """Generic paginated response from Quo API."""

    data: list[dict]
    total_items: int | None = None
    next_page_token: str | None = None


# =============================================================================
# Webhook Payload Schemas
# =============================================================================


class MessageWebhookPayload(BaseSchema):
    """Payload sent by Quo when a message event occurs.

    Quo v3 envelope: ``{"id": "EV...", "type": "message.received", "data": {"object": {...message...}}}``.
    We normalize ``type``→``event`` and unwrap ``data.object``→``data`` so the rest of the
    service code can stay simple.
    """

    event: str
    data: OpenPhoneMessage

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _normalize_envelope(cls, values):
        if not isinstance(values, dict):
            return values
        if "event" not in values and "type" in values:
            values["event"] = values["type"]
        data = values.get("data")
        if isinstance(data, dict) and "object" in data and "id" not in data:
            values["data"] = data["object"]
        return values


# =============================================================================
# Incoming Message Schemas (our DB records)
# =============================================================================


class IncomingMessageRead(BaseSchema, TimestampSchema):
    """Schema for reading a persisted incoming message."""

    id: UUID
    source: str
    openphone_id: str | None = None
    direction: str | None = None
    from_number: str | None = None
    to_numbers: list[str] = Field(default_factory=list)
    content: str | None = None
    status: str | None = None
    event_type: str | None = None
    phone_number_id: str | None = None


class IncomingMessageList(BaseSchema):
    """Schema for listing incoming messages."""

    items: list[IncomingMessageRead]
    total: int
