"""Repository for the Job parent record (cross-message dedup key)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job import Job


async def create_job(
    db: AsyncSession,
    *,
    company_id: uuid.UUID | None,
    first_message_at: datetime,
    address_street_number: str | None,
    address_street_name: str | None,
    address_city: str | None,
    address_state: str | None,
    address_zip: str | None,
    customer_phone_e164: str | None,
    job_type: str | None,
    is_duplicate: bool = False,
    duplicate_of: uuid.UUID | None = None,
    original_inbound_from_number: str | None = None,
    original_inbound_channel: str | None = None,
) -> Job:
    """Create a new Job row.

    The parent of one or more DispatchJob children. ``first_message_at`` is
    sticky — set here, never updated. The 14-day dedup window is anchored
    to it.

    ``original_inbound_from_number`` + ``original_inbound_channel`` are
    frozen at creation so the outbound-draft pipeline always reaches the
    same contact (OpenPhone ``from_number`` for OpenPhone sources, the
    WhatsApp sender for WhatsApp sources).
    """
    job = Job(
        company_id=company_id,
        first_message_at=first_message_at,
        address_street_number=address_street_number,
        address_street_name=address_street_name,
        address_city=address_city,
        address_state=address_state,
        address_zip=address_zip,
        customer_phone_e164=customer_phone_e164,
        job_type=job_type,
        is_duplicate=is_duplicate,
        duplicate_of=duplicate_of,
        original_inbound_from_number=original_inbound_from_number,
        original_inbound_channel=original_inbound_channel,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def get_job_by_id(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Get a Job by ID."""
    return await db.get(Job, job_id)


async def find_dedup_candidate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    street_number: str | None,
    street_name: str | None,
    customer_phone_e164: str | None,
    since: datetime,
) -> tuple[Job | None, bool]:
    """Find the first-seen Job matching the dedup keys.

    A candidate matches when **either** the normalized address
    (street_number AND street_name) matches, **or** the normalized
    customer phone matches — within the ``since`` window. ``job_type``
    is intentionally not part of the match: a rekey and a lockout at
    the same address from two companies on the same day is still a
    cross-company duplicate worth flagging.

    Returns ``(job, is_cross_company)``:
    - ``(None, False)`` if no candidate exists in the window.
    - ``(job, False)`` if the candidate belongs to the same company
      (append-only, the caller marks the DispatchJob as ``LINKED``).
    - ``(job, True)`` if the candidate belongs to a different company
      (informational duplicate — caller creates a new Job with
      ``is_duplicate=True`` and ``duplicate_of=candidate.id``).

    The first-seen (oldest) candidate is returned when multiple match.
    """
    address_match = None
    if street_number and street_name:
        address_match = and_(
            Job.address_street_number == street_number,
            Job.address_street_name == street_name,
        )

    phone_match = None
    if customer_phone_e164:
        phone_match = Job.customer_phone_e164 == customer_phone_e164

    conditions = [c for c in (address_match, phone_match) if c is not None]
    if not conditions:
        return None, False

    query = (
        select(Job)
        .where(Job.first_message_at >= since, or_(*conditions))
        .order_by(Job.first_message_at.asc())
        .limit(1)
    )

    result = await db.execute(query)
    candidate = result.scalar_one_or_none()
    if candidate is None:
        return None, False

    is_cross = candidate.company_id is not None and candidate.company_id != company_id
    return candidate, is_cross


async def find_for_closing(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    street_number: str | None,
    street_name: str | None,
    customer_phone_e164: str | None,
    since: datetime,
) -> Job | None:
    """Find the original Job for a closing message.

    Like ``find_dedup_candidate`` but scoped to a single company (the
    closing's company), no cross-company branch, and returns the
    first-seen (oldest) Job in the window — the "original first job
    classified" per the closing-pipeline spec. Re-close is permitted:
    a Job whose ``closed_at`` is already set still matches and the
    caller overwrites the closed_* columns.
    """
    address_match = None
    if street_number and street_name:
        address_match = and_(
            Job.address_street_number == street_number,
            Job.address_street_name == street_name,
        )

    phone_match = None
    if customer_phone_e164:
        phone_match = Job.customer_phone_e164 == customer_phone_e164

    conditions = [c for c in (address_match, phone_match) if c is not None]
    if not conditions:
        return None

    query = (
        select(Job)
        .where(
            Job.company_id == company_id,
            Job.first_message_at >= since,
            or_(*conditions),
        )
        .order_by(Job.first_message_at.asc())
        .limit(1)
    )

    result = await db.execute(query)
    return result.scalar_one_or_none()


async def mark_job_closed(
    db: AsyncSession,
    *,
    job: Job,
    closed_total: str | None,
    closed_parts: str | None,
    closed_tip: str | None,
    closed_payment_method: str | None,
    closed_notes: str | None,
    closed_at: datetime,
    closed_from_dispatch_job_id: uuid.UUID,
) -> Job:
    """Stamp closing fields onto a Job. Overwrites prior closing on re-close."""
    job.closed_total = closed_total
    job.closed_parts = closed_parts
    job.closed_tip = closed_tip
    job.closed_payment_method = closed_payment_method
    job.closed_notes = closed_notes
    job.closed_at = closed_at
    job.closed_from_dispatch_job_id = closed_from_dispatch_job_id
    await db.flush()
    await db.refresh(job)
    return job


async def find_dispatch_target(
    db: AsyncSession,
    *,
    street_number: str | None,
    street_name: str | None,
    zip_code: str | None,
    customer_phone_e164: str | None,
) -> Job | None:
    """Find the most-recent pending Job matching the operator's dispatch.

    The operator types only the address + phone in the technician's chat;
    we fuzzy-match against ``jobs.lifecycle_status='pending'`` rows.
    Matches when **all** provided fields agree; ``zip_code`` is optional
    because the operator sometimes forgets it.

    Returns ``None`` if no candidate matches (the alert engine then
    raises ``dispatch_no_match`` from the ingest path).
    """
    conditions = []
    if street_number:
        conditions.append(Job.address_street_number == street_number)
    if street_name:
        conditions.append(Job.address_street_name == street_name)
    if zip_code:
        conditions.append(Job.address_zip == zip_code)
    if customer_phone_e164:
        conditions.append(Job.customer_phone_e164 == customer_phone_e164)
    if not conditions:
        return None

    from sqlalchemy import and_

    query = (
        select(Job)
        .where(
            Job.lifecycle_status == "pending",
            and_(*conditions),
        )
        .order_by(Job.first_message_at.desc())
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def set_lifecycle_status(
    db: AsyncSession,
    *,
    job: Job,
    status: str,
    when: datetime | None = None,
) -> Job:
    """Update the denormalized lifecycle fields on a Job.

    Called by ``LifecycleService.transition`` after the audit-log row is
    appended. ``when`` defaults to now (UTC). Idempotent — re-stamping
    the same status is a no-op that still updates
    ``lifecycle_status_changed_at``.
    """
    job.lifecycle_status = status
    job.lifecycle_status_changed_at = when or datetime.now(UTC)
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def list_by_status(
    db: AsyncSession,
    status: str,
    *,
    limit: int = 100,
) -> list[Job]:
    """List Jobs in a given lifecycle status.

    Used by the alert engine for SLA scans (e.g. "all jobs that have
    been in ``dispatched`` for more than 4 hours").
    """
    query = (
        select(Job)
        .where(Job.lifecycle_status == status)
        .order_by(Job.lifecycle_status_changed_at.asc())
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())
