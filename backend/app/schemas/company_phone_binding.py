"""Schemas for the operator-curated phone -> company binding API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PhoneBindingCreate(BaseModel):
    """Payload to create a new binding.

    ``phone`` is accepted in any human format; the service normalizes it
    via ``normalize_phone`` before insert.
    """

    phone: str = Field(min_length=7, max_length=30)
    company_id: UUID
    note: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def _phone_has_digits(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("phone must contain at least 10 digits")
        return v


class PhoneBindingRead(BaseModel):
    """Single binding row for the configuration table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phone_e164: str
    company_id: UUID
    company_name: str
    company_display_name: str | None = None
    note: str | None = None
    created_at: datetime


class PhoneBindingList(BaseModel):
    """List response for ``GET /phone-bindings``."""

    items: list[PhoneBindingRead]
    total: int


class PhoneBindingSuggestion(BaseModel):
    """A number observed to regex-classify to the same company.

    The frontend renders these as one-click "Bind" rows. ``from_number``
    is the original human-readable form pulled from the most recent
    ``IncomingMessage``; ``phone_e164`` is the 10-digit form used as the
    eventual PK.
    """

    phone_e164: str
    from_number: str
    company_id: UUID
    company_name: str
    company_display_name: str | None = None
    hits: int
    last_seen_at: datetime


class PhoneBindingSuggestionList(BaseModel):
    """List response for ``GET /phone-bindings/suggestions``."""

    items: list[PhoneBindingSuggestion]
    total: int
