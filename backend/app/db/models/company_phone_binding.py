"""Operator-curated phone -> company bindings.

Third classification tier (OpenPhone only). Sits behind the body regex
match: when regex finds nothing, the sender's normalized phone is looked
up here. Conflicts with regex are logged but regex wins.

Bindings are created by an operator from the dashboard, either by
accepting an auto-generated suggestion (numbers that have regex-matched
to the same company N times) or by entering one manually.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.user import User


class CompanyPhoneBinding(Base, TimestampMixin):
    """A normalized phone number that maps to a single company."""

    __tablename__ = "company_phone_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_e164: Mapped[str] = mapped_column(String(15), unique=True, nullable=False, index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    company: Mapped["Company"] = relationship("Company")
    created_by: Mapped["User | None"] = relationship("User")

    def __repr__(self) -> str:
        return f"<CompanyPhoneBinding(phone={self.phone_e164}, company_id={self.company_id})>"
