"""User database model."""

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.session import Session


class UserRole(str, Enum):
    """User role enumeration.

    Roles hierarchy (higher includes lower permissions):
    - ADMIN: Full system access, can manage users and settings
    - USER: Standard user access
    """

    ADMIN = "admin"
    USER = "user"


class User(Base, TimestampMixin):
    """User model.

    Service-account extension:
        A row with ``is_service_account=True`` has no ``hashed_password`` and
        authenticates via ``service_api_key_hash`` instead. ``service_api_key_prefix``
        stores the first 12 characters of the plaintext key (``sk_live_xxxx``) for
        audit-log lookups; the full plaintext is never persisted (only returned
        once at creation time). The ``get_service_account`` dependency verifies
        both the bcrypt hash and the ``svc: true`` JWT claim.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[str] = mapped_column(String(50), default=UserRole.USER.value, nullable=False)

    # Service-account columns (used by the WhatsApp Chrome extension auth flow)
    is_service_account: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    service_api_key_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    service_api_key_prefix: Mapped[str | None] = mapped_column(String(12), nullable=True)
    service_account_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service_account_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationship to sessions
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def user_role(self) -> UserRole:
        """Get role as enum."""
        return UserRole(self.role)

    def has_role(self, required_role: UserRole) -> bool:
        """Check if user has the required role or higher.

        Admin role has access to everything.
        """
        if self.role == UserRole.ADMIN.value:
            return True
        return self.role == required_role.value

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"
