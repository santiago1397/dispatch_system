"""Repository for OpenPhone thread labels (display-only chat-view metadata)."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.openphone_thread_label import OpenPhoneThreadLabel


async def get_by_counterparty(
    db: AsyncSession,
    counterparty: str,
) -> OpenPhoneThreadLabel | None:
    """Get the label row for a counterparty, with its company eager-loaded."""
    query = (
        select(OpenPhoneThreadLabel)
        .options(selectinload(OpenPhoneThreadLabel.company))
        .where(OpenPhoneThreadLabel.counterparty == counterparty)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_by_counterparties(
    db: AsyncSession,
    counterparties: list[str],
) -> dict[str, OpenPhoneThreadLabel]:
    """Bulk-fetch label rows for a set of counterparties, keyed by counterparty.

    Used to enrich a thread list in one query instead of N+1.
    """
    if not counterparties:
        return {}
    query = (
        select(OpenPhoneThreadLabel)
        .options(selectinload(OpenPhoneThreadLabel.company))
        .where(OpenPhoneThreadLabel.counterparty.in_(counterparties))
    )
    result = await db.execute(query)
    return {row.counterparty: row for row in result.scalars().all()}


async def upsert(
    db: AsyncSession,
    *,
    counterparty: str,
    company_id: UUID | None,
    label: str | None,
    created_by_user_id: UUID | None,
) -> OpenPhoneThreadLabel:
    """Create or update the label row for ``counterparty``."""
    existing = await get_by_counterparty(db, counterparty)
    if existing is not None:
        existing.company_id = company_id
        existing.label = label
        existing.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(existing, attribute_names=["company"])
        return existing

    row = OpenPhoneThreadLabel(
        counterparty=counterparty,
        company_id=company_id,
        label=label,
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row, attribute_names=["company"])
    return row


async def delete(db: AsyncSession, counterparty: str) -> bool:
    """Delete the label row for ``counterparty``. Returns whether one existed."""
    existing = await get_by_counterparty(db, counterparty)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True
