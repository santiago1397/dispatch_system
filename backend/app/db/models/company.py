"""Company model for dispatch message classification."""

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PatternType(StrEnum):
    REGEX = "regex"
    AI = "ai"


class Company(Base, TimestampMixin):
    """Company that sends dispatch job messages.

    Stores identification patterns (regex) and known sender phone numbers
    used to classify incoming messages.
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pattern_type: Mapped[str] = mapped_column(
        String(20), default=PatternType.REGEX.value, nullable=False
    )
    identification_patterns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    phone_numbers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Company(id={self.id}, name={self.name})>"
