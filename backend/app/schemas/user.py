"""User schemas."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.base import BaseSchema, TimestampSchema


class UserRole(str, Enum):
    """User role enumeration for API schemas."""

    ADMIN = "admin"
    USER = "user"


class UserBase(BaseSchema):
    """Base user schema."""

    email: EmailStr = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class UserCreate(BaseSchema):
    """Schema for creating a user."""

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole = UserRole.USER


class UserUpdate(BaseSchema):
    """Schema for updating a user."""

    email: EmailStr | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    role: UserRole | None = None


class UserRead(UserBase, TimestampSchema):
    """Schema for reading a user.

    Service-account fields are exposed here but should be stripped from
    non-admin responses at the route layer (see ``app/api/routes/v1/users.py``).
    """

    id: UUID
    is_superuser: bool = False
    role: UserRole = UserRole.USER
    is_service_account: bool = False
    service_account_name: str | None = None
    service_api_key_prefix: str | None = None
    service_account_last_used_at: datetime | None = None


class UserInDB(UserRead):
    """User schema with hashed password (internal use)."""

    hashed_password: str
