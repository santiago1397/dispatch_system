"""Service for operator-curated phone -> company bindings."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from app.db.models.company_phone_binding import CompanyPhoneBinding
from app.repositories import company_repo, phone_binding_repo
from app.services.address_normalizer import normalize_phone

logger = logging.getLogger(__name__)


class PhoneBindingService:
    """List, create, delete bindings and surface auto-suggestions.

    Suggestions come straight from ``phone_binding_repo.list_suggestions``
    — an aggregate of past regex-classified OpenPhone messages. No
    persisted suggestion state; the query reruns on every fetch.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[CompanyPhoneBinding]:
        return await phone_binding_repo.list_all(self.db)

    async def create(
        self,
        *,
        phone: str,
        company_id: UUID,
        note: str | None,
        created_by_user_id: UUID | None,
    ) -> CompanyPhoneBinding:
        """Normalize the phone and insert a binding.

        Raises ``ValidationError`` when the phone can't be normalized to
        10 digits, ``NotFoundError`` when ``company_id`` is unknown, and
        ``AlreadyExistsError`` when the phone is already bound.
        """
        phone_e164 = normalize_phone(phone)
        if not phone_e164:
            raise ValidationError(message="phone must normalize to 10 US digits")

        company = await company_repo.get_by_id(self.db, company_id)
        if company is None:
            raise NotFoundError(message="Company not found")

        existing = await phone_binding_repo.get_by_phone(self.db, phone_e164)
        if existing is not None:
            raise AlreadyExistsError(
                message=f"Phone {phone_e164} is already bound",
                details={"binding_id": str(existing.id)},
            )

        binding = await phone_binding_repo.create(
            self.db,
            phone_e164=phone_e164,
            company_id=company_id,
            note=note,
            created_by_user_id=created_by_user_id,
        )
        logger.info(
            "PHONE_BINDING_CREATED phone=%s company=%s by_user=%s",
            phone_e164,
            company.name,
            created_by_user_id,
        )
        return binding

    async def delete(self, binding_id: UUID) -> None:
        deleted = await phone_binding_repo.delete_by_id(self.db, binding_id)
        if deleted == 0:
            raise NotFoundError(message="Binding not found")
        logger.info("PHONE_BINDING_DELETED id=%s", binding_id)

    async def list_suggestions(self) -> list[dict]:
        return await phone_binding_repo.list_suggestions(self.db)
