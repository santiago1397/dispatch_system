"""Item repository (PostgreSQL async).

Contains database operations for Item entity. Business logic
should be handled by ItemService in app/services/item.py.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.item import Item


async def get_by_id(db: AsyncSession, item_id: UUID) -> Item | None:
    """Get item by ID."""
    return await db.get(Item, item_id)


async def get_multi(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
) -> list[Item]:
    """Get multiple items with pagination."""
    query = select(Item)
    if active_only:
        query = query.where(Item.is_active == True)  # noqa: E712
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def create(
    db: AsyncSession,
    *,
    title: str,
    description: str | None = None,
) -> Item:
    """Create a new item."""
    item = Item(
        title=title,
        description=description,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


async def update(
    db: AsyncSession,
    *,
    db_item: Item,
    update_data: dict,
) -> Item:
    """Update an item."""
    for field, value in update_data.items():
        setattr(db_item, field, value)

    db.add(db_item)
    await db.flush()
    await db.refresh(db_item)
    return db_item


async def delete(db: AsyncSession, item_id: UUID) -> Item | None:
    """Delete an item."""
    item = await get_by_id(db, item_id)
    if item:
        await db.delete(item)
        await db.flush()
    return item
