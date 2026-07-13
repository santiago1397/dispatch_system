"""Repository for Alert — pipeline-health open issues.

``create_or_get_open`` is idempotent: if an unresolved alert of the
same ``kind`` (and ``job_id`` if present) already exists, it returns
the existing row instead of creating a duplicate. This is the key
guarantee that lets the alert engine run every 5 minutes without
spamming the dashboard with duplicates.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert, AlertKind


async def create_or_get_open(
    db: AsyncSession,
    *,
    kind: str,
    job_id: uuid.UUID | None = None,
    chat_jid: str | None = None,
    threshold_minutes: int | None = None,
    payload: dict | None = None,
    detected_at: datetime | None = None,
) -> Alert:
    """Insert an alert, or return the existing unresolved alert of the same kind.

    For ``job_id``-bound kinds (``stuck_dispatched``, ``stuck_in_progress``,
    ``appt_time_passed``, ``closing_missing``), dedup matches on
    ``(kind, job_id)``. For chat-bound kinds (``dispatch_no_match``,
    ``unattributed_reply``), dedup matches on ``(kind, chat_jid)``.
    """
    detected_at = detected_at or datetime.now(UTC)

    if job_id is not None:
        existing_query = select(Alert).where(
            and_(
                Alert.kind == kind,
                Alert.job_id == job_id,
                Alert.resolved_at.is_(None),
            )
        )
    elif chat_jid is not None:
        existing_query = select(Alert).where(
            and_(
                Alert.kind == kind,
                Alert.chat_jid == chat_jid,
                Alert.resolved_at.is_(None),
            )
        )
    else:
        existing_query = None

    if existing_query is not None:
        existing = (await db.execute(existing_query)).scalar_one_or_none()
        if existing is not None:
            return existing

    alert = Alert(
        job_id=job_id,
        chat_jid=chat_jid,
        kind=kind,
        threshold_minutes=threshold_minutes,
        detected_at=detected_at,
        payload=payload or {},
    )
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert


async def list_open(
    db: AsyncSession,
    *,
    kinds: list[str] | None = None,
    job_ids: list[uuid.UUID] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Alert]:
    """List unresolved alerts. The ``(kind, resolved_at)`` index makes
    this a single index scan even when filtering by ``kinds``.

    ``job_ids``, when given, restricts to alerts bound to one of those
    parent Jobs — used by the alerts search bar (matches resolved via
    ``job_repo.search_job_ids_by_message``).
    """
    query = select(Alert).where(Alert.resolved_at.is_(None))
    if kinds is not None:
        query = query.where(Alert.kind.in_(kinds))
    if job_ids is not None:
        query = query.where(Alert.job_id.in_(job_ids))
    query = query.order_by(Alert.detected_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_recent(
    db: AsyncSession,
    *,
    limit: int = 200,
    offset: int = 0,
    include_resolved: bool = True,
    job_ids: list[uuid.UUID] | None = None,
) -> list[Alert]:
    """List alerts newest-first, optionally including resolved ones."""
    query = select(Alert)
    if not include_resolved:
        query = query.where(Alert.resolved_at.is_(None))
    if job_ids is not None:
        query = query.where(Alert.job_id.in_(job_ids))
    query = query.order_by(Alert.detected_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, alert_id: uuid.UUID) -> Alert | None:
    """Get an alert by id."""
    return await db.get(Alert, alert_id)


async def count_open(
    db: AsyncSession,
    *,
    kinds: list[str] | None = None,
    job_ids: list[uuid.UUID] | None = None,
) -> int:
    """Count unresolved alerts (for sidebar badge)."""
    query = select(func.count()).select_from(Alert).where(Alert.resolved_at.is_(None))
    if kinds is not None:
        query = query.where(Alert.kind.in_(kinds))
    if job_ids is not None:
        query = query.where(Alert.job_id.in_(job_ids))
    result = await db.execute(query)
    return result.scalar_one()


async def count_unseen(
    db: AsyncSession,
    *,
    kinds: list[str] | None = None,
) -> int:
    """Count open alerts an operator hasn't viewed yet (for the navbar badge)."""
    query = (
        select(func.count())
        .select_from(Alert)
        .where(Alert.resolved_at.is_(None), Alert.seen_at.is_(None))
    )
    if kinds is not None:
        query = query.where(Alert.kind.in_(kinds))
    result = await db.execute(query)
    return result.scalar_one()


async def mark_all_seen(db: AsyncSession) -> int:
    """Mark every open, unseen alert as seen. Returns the number updated.

    Called when the operator opens the Alerts dashboard — viewing the
    open queue is what "seen" means here, distinct from resolving.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(Alert)
        .where(Alert.resolved_at.is_(None), Alert.seen_at.is_(None))
        .values(seen_at=now)
    )
    return result.rowcount or 0


async def resolve(
    db: AsyncSession,
    alert: Alert,
    *,
    user_id: uuid.UUID | None = None,
    resolved_at: datetime | None = None,
) -> Alert:
    """Mark an alert resolved. Idempotent — re-resolving is a no-op."""
    if alert.resolved_at is not None:
        return alert
    alert.resolved_at = resolved_at or datetime.now(UTC)
    alert.resolved_by_user_id = user_id
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert


async def auto_resolve_for_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    kinds: list[str] | None = None,
) -> int:
    """Mark all open alerts for ``job_id`` as resolved.

    Called when a job reaches a terminal state (closed / canceled) so
    downstream stuck- alerts clear automatically. Returns the count
    resolved (0 if none).
    """
    query = select(Alert).where(
        and_(
            Alert.job_id == job_id,
            Alert.resolved_at.is_(None),
        )
    )
    if kinds is not None:
        query = query.where(Alert.kind.in_(kinds))
    result = await db.execute(query)
    alerts = list(result.scalars().all())
    now = datetime.now(UTC)
    for alert in alerts:
        alert.resolved_at = now
        db.add(alert)
    if alerts:
        await db.flush()
    return len(alerts)


# Re-export AlertKind for convenience so callers can do
# `from app.repositories.alert import AlertKind`.
__all__ = [
    "AlertKind",
    "auto_resolve_for_job",
    "count_open",
    "count_unseen",
    "create_or_get_open",
    "get_by_id",
    "list_open",
    "list_recent",
    "mark_all_seen",
    "resolve",
]
