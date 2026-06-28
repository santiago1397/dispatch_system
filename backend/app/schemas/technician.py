"""Pydantic schemas for Technician CRUD."""

from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampSchema


class TechnicianBase(BaseSchema):
    name: str = Field(..., min_length=1, max_length=255)
    phone_e164: str | None = Field(default=None, max_length=15)
    whatsapp_chat_jid: str | None = Field(default=None, max_length=100)
    is_active: bool = True
    notes: str | None = None


class TechnicianCreate(TechnicianBase):
    """Schema for creating a technician."""


class TechnicianUpdate(BaseSchema):
    """Schema for updating a technician. ``None`` leaves the column unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone_e164: str | None = Field(default=None, max_length=15)
    whatsapp_chat_jid: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    notes: str | None = None


class TechnicianRead(TechnicianBase, TimestampSchema):
    id: UUID


class TechnicianList(BaseSchema):
    items: list[TechnicianRead]
    total: int
