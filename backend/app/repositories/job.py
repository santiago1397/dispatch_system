"""Repository for the Job parent record (cross-message dedup key)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.dispatch_job import DispatchJob
from app.db.models.job import Job
from app.db.models.openphone import IncomingMessage


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


async def find_open_by_address_phone(
    db: AsyncSession,
    *,
    street_number: str | None,
    street_name: str | None,
    customer_phone_e164: str | None,
    since: datetime,
) -> Job | None:
    """Company-agnostic match of a Job by address+phone for the closing-signal gate.

    Like ``find_for_closing`` but **without** the ``company_id`` filter — a
    tech's payment/closing re-paste arrives in an arbitrary chat where the
    company hasn't been (and needn't be) classified. A candidate matches
    when **either** the normalized address (street_number AND street_name)
    matches, **or** the normalized customer phone matches — within the
    ``since`` window. Returns the first-seen (oldest) Job, mirroring the
    closing pipeline's "original first job" semantics.

    Returns any matching Job regardless of ``lifecycle_status``; the caller
    (``ClosingSignalService``) decides whether to transition it (non-terminal)
    or drop the signal (already ``completed``/terminal). Same-address
    collisions between two companies in the window are tolerated — the same
    edge the dedup pipeline already accepts.
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
        .where(Job.first_message_at >= since, or_(*conditions))
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


async def find_reject_candidate(
    db: AsyncSession,
    *,
    chat_jid: str,
    before: datetime,
) -> tuple[Job, str] | None:
    """Find the pending Job a reject reply refers to, with its source body.

    The reply "pass"/"have it"/re-paste carries no address, so it is tied
    to the most-recent still-``pending`` Job that originated from the same
    chat and whose first message predates the reply. Returns ``(job,
    source_body)`` — ``source_body`` is one of the job's message bodies,
    used for the re-paste similarity check — or ``None`` when there is no
    pending job from this chat to reject.

    Only ``pending`` jobs are eligible: a dispatched/closed/rejected job is
    never un-done by a later reject phrase, and this filter also makes the
    detection idempotent under the extension's re-send behaviour (once the
    job is ``rejected`` it stops matching).
    """
    from app.db.models.dispatch_job import DispatchJob
    from app.db.models.openphone import IncomingMessage

    query = (
        select(Job, IncomingMessage.content)
        .join(DispatchJob, DispatchJob.job_id == Job.id)
        .join(IncomingMessage, IncomingMessage.id == DispatchJob.incoming_message_id)
        .where(
            IncomingMessage.raw_payload["chat_jid"].astext == chat_jid,
            Job.lifecycle_status == "pending",
            Job.first_message_at < before,
        )
        .order_by(Job.first_message_at.desc())
        .limit(1)
    )
    row = (await db.execute(query)).first()
    if row is None:
        return None
    job, content = row
    return job, (content or "")


async def find_origin_incoming_for_job(db: AsyncSession, job_id: uuid.UUID):
    """Return the earliest IncomingMessage that opened this Job.

    Used to compose the company-relay message: it carries the original job
    body (``content``) and the company's address (WhatsApp
    ``raw_payload.chat_jid`` or OpenPhone ``from_number``). Returns the
    ``IncomingMessage`` ORM row, or ``None`` if the job has no dispatch
    children yet.
    """
    from app.db.models.dispatch_job import DispatchJob
    from app.db.models.openphone import IncomingMessage

    query = (
        select(IncomingMessage)
        .join(DispatchJob, DispatchJob.incoming_message_id == IncomingMessage.id)
        .where(DispatchJob.job_id == job_id)
        .order_by(IncomingMessage.created_at.asc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def find_reject_candidate_openphone(
    db: AsyncSession,
    *,
    counterparty: str,
    before: datetime,
) -> tuple[Job, str] | None:
    """OpenPhone twin of :func:`find_reject_candidate`.

    The operator's "pass" reply is an *outbound* OpenPhone message to the
    company that texted the job in. The conversation is keyed on that
    counterparty phone: match the pending Job whose originating *inbound*
    message came ``from_number == counterparty``. ``counterparty`` is the
    raw phone string from the reply's ``to_numbers`` — matched against the
    raw inbound ``from_number`` so both sides come from Quo in the same
    format. Returns ``(job, source_body)`` or ``None``.
    """
    from app.db.models.dispatch_job import DispatchJob
    from app.db.models.openphone import IncomingMessage

    query = (
        select(Job, IncomingMessage.content)
        .join(DispatchJob, DispatchJob.job_id == Job.id)
        .join(IncomingMessage, IncomingMessage.id == DispatchJob.incoming_message_id)
        .where(
            IncomingMessage.source == "openphone",
            IncomingMessage.direction == "incoming",
            IncomingMessage.from_number == counterparty,
            Job.lifecycle_status == "pending",
            Job.first_message_at < before,
        )
        .order_by(Job.first_message_at.desc())
        .limit(1)
    )
    row = (await db.execute(query)).first()
    if row is None:
        return None
    job, content = row
    return job, (content or "")


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


async def get_alert_job_summaries(
    db: AsyncSession,
    job_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    """Batch-resolve parent-Job summaries for the alerts dashboard.

    Alerts reference a parent ``jobs.id``, but the operator-facing job
    page is keyed by the child ``dispatch_jobs.id``. This bridges the two:
    for each parent Job it returns the company + address + lifecycle status
    plus the *originating* ``DispatchJob`` (the earliest child, i.e. the
    message that opened the job) and a short preview of that message so the
    alert row can show "why + which job + which message" and link straight
    to ``/jobs/{dispatch_job_id}``.

    Returns a dict keyed by parent ``job_id``; job_ids with no matching
    Job are simply absent. One query for the Jobs and one for the child
    DispatchJobs — no per-alert round-trip.
    """
    if not job_ids:
        return {}

    unique_ids = list(set(job_ids))

    jobs_q = select(Job).where(Job.id.in_(unique_ids)).options(selectinload(Job.company))
    jobs = list((await db.execute(jobs_q)).scalars().all())

    # Earliest child DispatchJob per parent = the message that opened the
    # job. Ordering by (job_id, created_at ASC) then keeping the first seen
    # per job_id gives us that without a window function.
    dj_q = (
        select(DispatchJob)
        .where(DispatchJob.job_id.in_(unique_ids))
        .order_by(DispatchJob.job_id, DispatchJob.created_at.asc())
        .options(selectinload(DispatchJob.incoming_message))
    )
    origin_by_job: dict[uuid.UUID, DispatchJob] = {}
    for dj in (await db.execute(dj_q)).scalars().all():
        if dj.job_id is not None and dj.job_id not in origin_by_job:
            origin_by_job[dj.job_id] = dj

    summaries: dict[uuid.UUID, dict] = {}
    for job in jobs:
        origin = origin_by_job.get(job.id)
        message = origin.incoming_message if origin is not None else None
        address = " ".join(
            p for p in (job.address_street_number, job.address_street_name) if p
        ).strip() or (origin.address if origin is not None else None)
        preview = None
        if message is not None and message.content:
            preview = message.content[:200]
        summaries[job.id] = {
            "job_id": job.id,
            "dispatch_job_id": origin.id if origin is not None else None,
            "company_name": job.company.display_name if job.company else None,
            "lifecycle_status": job.lifecycle_status,
            "address": address or None,
            "customer_name": origin.customer_name if origin is not None else None,
            "customer_phone": job.customer_phone_e164
            or (origin.customer_phone if origin is not None else None),
            "job_type": job.job_type or (origin.job_type if origin is not None else None),
            "message_preview": preview,
            "message_source": message.source if message is not None else None,
        }
    return summaries


async def search_job_ids_by_message(db: AsyncSession, search: str) -> list[uuid.UUID]:
    """Find parent Job ids whose raw incoming message text matches ``search``.

    Used by the alerts search bar — the operator recalls a phrase from the
    job message ("no hot water", a street name) and needs the alert(s) it
    triggered. Matches any ``DispatchJob`` under the job whose
    ``IncomingMessage.content`` contains ``search`` (case-insensitive), not
    just the originating one, since a follow-up message may carry the term.
    """
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query = (
        select(DispatchJob.job_id)
        .join(IncomingMessage, DispatchJob.incoming_message_id == IncomingMessage.id)
        .where(
            DispatchJob.job_id.is_not(None),
            IncomingMessage.content.ilike(f"%{escaped}%", escape="\\"),
        )
        .distinct()
    )
    result = await db.execute(query)
    return [row[0] for row in result.all()]
