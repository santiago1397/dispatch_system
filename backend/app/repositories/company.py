"""Repository for company data access."""

import re
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company


async def get_by_id(db: AsyncSession, company_id: UUID) -> Company | None:
    """Get a company by ID."""
    return await db.get(Company, company_id)


async def get_by_name(db: AsyncSession, name: str) -> Company | None:
    """Get a company by machine name."""
    query = select(Company).where(Company.name == name)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_all_active(db: AsyncSession) -> list[Company]:
    """Get all active companies (for classification)."""
    query = select(Company).where(Company.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_by_phone_number(db: AsyncSession, phone_number: str | None) -> Company | None:
    """Find a company by a normalized sender phone number.

    Normalizes the input by stripping non-digits, then searches
    all active companies' phone_numbers JSONB arrays for a match.
    Returns ``None`` if ``phone_number`` is empty or absent (the WhatsApp
    path stores ``from_number=None`` because no verified number exists).
    """
    if not phone_number:
        return None
    normalized = re.sub(r"\D", "", phone_number)
    if len(normalized) < 7:
        return None

    companies = await get_all_active(db)
    for company in companies:
        stored_numbers = company.phone_numbers or []
        for stored in stored_numbers:
            stored_normalized = re.sub(r"\D", "", str(stored))
            # Match last 10 digits (handles country code differences)
            if len(stored_normalized) >= 10 and len(normalized) >= 10:
                if stored_normalized[-10:] == normalized[-10:]:
                    return company
            elif stored_normalized == normalized:
                return company
    return None


async def bulk_create(db: AsyncSession, companies: list[dict]) -> list[Company]:
    """Create multiple companies in a single transaction."""
    created = []
    for data in companies:
        company = Company(
            name=data["name"],
            display_name=data.get("display_name"),
            pattern_type=data.get("pattern_type", "regex"),
            identification_patterns=data.get("identification_patterns", []),
            phone_numbers=data.get("phone_numbers", []),
            is_active=data.get("is_active", True),
        )
        db.add(company)
        created.append(company)
    await db.flush()
    for c in created:
        await db.refresh(c)
    return created


async def clear_all(db: AsyncSession) -> int:
    """Delete all companies. Returns count deleted."""
    result = await db.execute(delete(Company))
    await db.flush()
    return result.rowcount
