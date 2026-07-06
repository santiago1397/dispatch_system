"""Repository for the JobLifecycleEvent append-only audit log.

Every transition that mutates ``jobs.lifecycle_status`` MUST go through
``create_event``. There is no update path — events are immutable; if
a transition was wrong, the correct fix is a NEW event correcting it.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job_lifecycle_event import JobLifecycleEvent


async def create_event(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    source: str,
    from_status: str,
    to_status: str,
    payload: dict | None = None,
    created_by_user_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> JobLifecycleEvent:
    """Append one lifecycle transition to the audit log.

    Returns the inserted row. Caller is expected to also update
    ``jobs.lifecycle_status`` in the same transaction so the two stay
    in sync (use ``LifecycleService.transition`` for that).
    """
    event = JobLifecycleEvent(
        job_id=job_id,
        source=source,
        from_status=from_status,
        to_status=to_status,
        payload=payload or {},
        created_by_user_id=created_by_user_id,
    )
    if at is not None:
        event.created_at = at
    db.add(event)
    await db.flush()
    await db.refresh(event)
    return event


async def list_for_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[JobLifecycleEvent]:
    """List events for a job, newest first.

    The (job_id, created_at) index makes this a single index scan even
    for jobs with hundreds of transitions.
    """
    query = (
        select(JobLifecycleEvent)
        .where(JobLifecycleEvent.job_id == job_id)
        .order_by(JobLifecycleEvent.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_for_job_paginated(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[JobLifecycleEvent], int]:
    """Paginated event list + total count for a single job.

    ``total`` is the unfiltered count of events for this job, used by
    the ``JobLifecycleEventList`` response so the timeline can show
    "showing 50 of N events".
    """
    from sqlalchemy import func

    count_q = (
        select(func.count())
        .select_from(JobLifecycleEvent)
        .where(JobLifecycleEvent.job_id == job_id)
    )
    total = (await db.execute(count_q)).scalar_one()

    query = (
        select(JobLifecycleEvent)
        .where(JobLifecycleEvent.job_id == job_id)
        .order_by(JobLifecycleEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), int(total)


async def latest_for_job(
    db: AsyncSession,
    job_id: uuid.UUID,
) -> JobLifecycleEvent | None:
    """Return the most recent event for a job, or ``None`` if no events exist."""
    query = (
        select(JobLifecycleEvent)
        .where(JobLifecycleEvent.job_id == job_id)
        .order_by(JobLifecycleEvent.created_at.desc())
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def exists_for_openphone_id(
    db: AsyncSession,
    *,
    source: str,
    openphone_id: str,
) -> bool:
    """True if an event of ``source`` already recorded this OpenPhone message.

    OpenPhone can redeliver the same webhook (e.g. ``message.delivered``
    firing more than once). Dispatch/reply handlers stamp
    ``payload.openphone_id`` on the event they create; this guard lets
    them no-op on a redelivery instead of appending a duplicate
    transition.
    """
    if not openphone_id:
        return False
    query = (
        select(JobLifecycleEvent.id)
        .where(
            JobLifecycleEvent.source == source,
            JobLifecycleEvent.payload["openphone_id"].astext == openphone_id,
        )
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


async def latest_with_to_status(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    to_status: str,
) -> JobLifecycleEvent | None:
    """Find the most recent event whose ``to_status`` matches.

    Used by the alert engine (e.g. "when did this job become
    dispatched?") and by ``tech_reply_parser`` to anchor reply
    attribution.
    """
    query = (
        select(JobLifecycleEvent)
        .where(
            JobLifecycleEvent.job_id == job_id,
            JobLifecycleEvent.to_status == to_status,
        )
        .order_by(JobLifecycleEvent.created_at.desc())
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()
