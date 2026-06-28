"""Repository for operator-curated phone -> company bindings."""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.company import Company
from app.db.models.company_phone_binding import CompanyPhoneBinding


async def get_by_id(db: AsyncSession, binding_id: UUID) -> CompanyPhoneBinding | None:
    """Fetch a binding by primary key, with the company eager-loaded."""
    query = (
        select(CompanyPhoneBinding)
        .where(CompanyPhoneBinding.id == binding_id)
        .options(selectinload(CompanyPhoneBinding.company))
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_company_by_phone(db: AsyncSession, phone_e164: str | None) -> Company | None:
    """Look up the bound company for a normalized phone.

    Returns ``None`` for empty input or when no binding exists. Used by
    the classifier as a fallback tier — call ``normalize_phone`` on the
    raw sender first.
    """
    if not phone_e164:
        return None
    query = (
        select(Company)
        .join(
            CompanyPhoneBinding,
            CompanyPhoneBinding.company_id == Company.id,
        )
        .where(CompanyPhoneBinding.phone_e164 == phone_e164)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def list_all(db: AsyncSession) -> list[CompanyPhoneBinding]:
    """List every binding with its company eager-loaded.

    The configuration page is small (a few dozen rows at most) — no
    pagination yet.
    """
    query = (
        select(CompanyPhoneBinding)
        .options(selectinload(CompanyPhoneBinding.company))
        .order_by(CompanyPhoneBinding.created_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_by_phone(db: AsyncSession, phone_e164: str) -> CompanyPhoneBinding | None:
    """Return the binding row for a phone (used to detect duplicates)."""
    query = select(CompanyPhoneBinding).where(CompanyPhoneBinding.phone_e164 == phone_e164)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    phone_e164: str,
    company_id: UUID,
    note: str | None,
    created_by_user_id: UUID | None,
) -> CompanyPhoneBinding:
    """Insert a new binding. Caller is responsible for duplicate checks."""
    binding = CompanyPhoneBinding(
        phone_e164=phone_e164,
        company_id=company_id,
        note=note,
        created_by_user_id=created_by_user_id,
    )
    db.add(binding)
    await db.flush()
    # Re-fetch so the company relationship is populated for the response.
    return await get_by_id(db, binding.id)  # type: ignore[return-value]


async def delete_by_id(db: AsyncSession, binding_id: UUID) -> int:
    """Delete a binding by ID. Returns rows deleted (0 if not found)."""
    result = await db.execute(
        delete(CompanyPhoneBinding).where(CompanyPhoneBinding.id == binding_id)
    )
    await db.flush()
    return result.rowcount


async def list_suggestions(db: AsyncSession) -> list[dict]:
    """Aggregate observed regex hits as suggested bindings.

    Groups OpenPhone messages by ``(from_number, company_id)`` over the
    rows the classifier picked via regex, excludes numbers already bound,
    and ranks by hit count. Returns plain dicts — the route layer maps
    them to ``PhoneBindingSuggestion`` schemas.
    """
    from app.db.models.dispatch_job import DispatchJob
    from app.db.models.openphone import IncomingMessage

    # Last 10 digits of im.from_number — matches the storage format of
    # phone_e164 (10-digit normalized).
    phone_digits = func.right(
        func.regexp_replace(IncomingMessage.from_number, r"\D", "", "g"),
        10,
    )

    bound_subq = select(CompanyPhoneBinding.phone_e164)

    query = (
        select(
            phone_digits.label("phone_e164"),
            IncomingMessage.from_number.label("from_number"),
            DispatchJob.company_id.label("company_id"),
            Company.name.label("company_name"),
            Company.display_name.label("company_display_name"),
            func.count().label("hits"),
            func.max(IncomingMessage.created_at).label("last_seen_at"),
        )
        .join(DispatchJob, DispatchJob.incoming_message_id == IncomingMessage.id)
        .join(Company, Company.id == DispatchJob.company_id)
        .where(
            IncomingMessage.source == "openphone",
            DispatchJob.classification_method == "regex",
            DispatchJob.company_id.isnot(None),
            IncomingMessage.from_number.isnot(None),
            phone_digits.notin_(bound_subq),
            func.length(phone_digits) == 10,
        )
        .group_by(
            phone_digits,
            IncomingMessage.from_number,
            DispatchJob.company_id,
            Company.name,
            Company.display_name,
        )
        .order_by(func.count().desc(), func.max(IncomingMessage.created_at).desc())
    )
    result = await db.execute(query)
    return [dict(row._mapping) for row in result.all()]
