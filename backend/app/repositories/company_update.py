"""Repository for CompanyUpdate — pending operator→company status relays."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company_update import CompanyUpdate


async def create_company_update(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    update_kind: str,
    channel: str,
    message_text: str,
    company_id: uuid.UUID | None = None,
    lifecycle_event_id: uuid.UUID | None = None,
    company_chat_jid: str | None = None,
    company_phone: str | None = None,
) -> CompanyUpdate:
    """Persist a new pending company-update relay."""
    relay = CompanyUpdate(
        job_id=job_id,
        update_kind=update_kind,
        channel=channel,
        message_text=message_text,
        company_id=company_id,
        lifecycle_event_id=lifecycle_event_id,
        company_chat_jid=company_chat_jid,
        company_phone=company_phone,
    )
    db.add(relay)
    await db.flush()
    await db.refresh(relay)
    return relay


async def list_unsent(db: AsyncSession, *, limit: int = 200) -> list[CompanyUpdate]:
    """Unsent relays (``sent_at IS NULL``), oldest-first — the scanner set."""
    query = (
        select(CompanyUpdate)
        .where(CompanyUpdate.sent_at.is_(None))
        .order_by(CompanyUpdate.created_at.asc())
        .limit(limit)
    )
    return list((await db.execute(query)).scalars().all())


async def get_latest_pending_for_job(
    db: AsyncSession, job_id: uuid.UUID
) -> CompanyUpdate | None:
    """Most recent unsent relay for a job (for the /jobs detail pane)."""
    query = (
        select(CompanyUpdate)
        .where(CompanyUpdate.job_id == job_id, CompanyUpdate.sent_at.is_(None))
        .order_by(CompanyUpdate.created_at.desc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def mark_sent(
    db: AsyncSession, relay: CompanyUpdate, *, when: datetime
) -> CompanyUpdate:
    """Stamp ``sent_at`` once the operator's relay is observed. Idempotent."""
    if relay.sent_at is None:
        relay.sent_at = when
        db.add(relay)
        await db.flush()
    return relay
