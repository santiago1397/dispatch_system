"""Operator-curated reference label for an OpenPhone conversation thread.

Purely a display convenience for the ``/openphone`` chat view — lets an
operator tag "who this number is" (a company, a free-text note, or both)
without touching the classification pipeline. This is deliberately NOT
``CompanyPhoneBinding`` (``app/db/models/company_phone_binding.py``): that
table feeds the tier-3 classifier, so editing a chat label here must never
change how a future inbound message gets classified.
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


class OpenPhoneThreadLabel(Base, TimestampMixin):
    """A company reference and/or free-text label for one OpenPhone counterparty."""

    __tablename__ = "openphone_thread_labels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    counterparty: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    company: Mapped["Company | None"] = relationship("Company")
    created_by: Mapped["User | None"] = relationship("User")

    def __repr__(self) -> str:
        return f"<OpenPhoneThreadLabel(counterparty={self.counterparty}, company_id={self.company_id})>"
