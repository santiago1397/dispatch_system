"""Company service — read-only listing for the operator UI dropdowns."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company
from app.repositories import company_repo

logger = logging.getLogger(__name__)


class CompanyService:
    """Service for company lookups.

    Currently a thin pass-through to the repository — the only consumer
    is the operator UI's filter dropdown, which just needs an
    alphabetical list of active companies. New write paths (create,
    update, deactivate) would belong here when added.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active(self) -> list[Company]:
        """Return all active companies, ordered by display name (falling
        back to machine name when display name is null)."""
        companies = await company_repo.get_all_active(self.db)
        return sorted(
            companies,
            key=lambda c: (c.display_name or c.name).lower(),
        )
