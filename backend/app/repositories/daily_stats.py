"""Repository for DailyStatsSnapshot — pre-computed rollups.

``upsert_snapshot`` overwrites the row for a (date, scope, scope_id)
key. The (snapshot_date, scope) index keeps the "give me stats for
date X" query cheap.
"""

import uuid
from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.daily_stats import DailyStatsSnapshot


async def upsert_snapshot(
    db: AsyncSession,
    *,
    snapshot_date: date,
    scope: str,
    scope_id: uuid.UUID | None,
    payload: dict,
) -> DailyStatsSnapshot:
    """Insert or overwrite the snapshot row for the given key.

    Keyed on ``(snapshot_date, scope, scope_id)`` — the unique
    combination that distinguishes a per-job entry from a per-tech
    rollup on the same date. ``scope_id`` is NULL for ``per_job``
    entries.
    """
    stmt = pg_insert(DailyStatsSnapshot).values(
        snapshot_date=snapshot_date,
        scope=scope,
        scope_id=scope_id,
        payload=payload,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["snapshot_date", "scope", "scope_id"],
        set_={"payload": stmt.excluded.payload},
    )
    await db.execute(stmt)
    await db.flush()
    # Re-read so we return the canonical row (with id + timestamps).
    query = select(DailyStatsSnapshot).where(
        and_(
            DailyStatsSnapshot.snapshot_date == snapshot_date,
            DailyStatsSnapshot.scope == scope,
            DailyStatsSnapshot.scope_id.is_(scope_id)
            if scope_id is None
            else DailyStatsSnapshot.scope_id == scope_id,
        )
    )
    return (await db.execute(query)).scalar_one()


async def list_for_date(
    db: AsyncSession,
    snapshot_date: date,
    *,
    scope: str | None = None,
) -> list[DailyStatsSnapshot]:
    """List snapshots for a date, optionally filtered by scope."""
    query = select(DailyStatsSnapshot).where(DailyStatsSnapshot.snapshot_date == snapshot_date)
    if scope is not None:
        query = query.where(DailyStatsSnapshot.scope == scope)
    query = query.order_by(DailyStatsSnapshot.scope, DailyStatsSnapshot.scope_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_for_date_range(
    db: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    scope: str | None = None,
) -> list[DailyStatsSnapshot]:
    """List snapshots for a date range, optionally filtered by scope."""
    query = select(DailyStatsSnapshot).where(
        DailyStatsSnapshot.snapshot_date >= start_date,
        DailyStatsSnapshot.snapshot_date <= end_date,
    )
    if scope is not None:
        query = query.where(DailyStatsSnapshot.scope == scope)
    query = query.order_by(
        DailyStatsSnapshot.snapshot_date.desc(),
        DailyStatsSnapshot.scope,
    )
    result = await db.execute(query)
    return list(result.scalars().all())
