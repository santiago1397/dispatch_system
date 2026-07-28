"""Repository for the Job parent record (cross-message dedup key)."""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.dispatch_job import DispatchJob
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import JobLifecycleEvent
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


async def delete_if_unreferenced(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """Delete a Job that nothing points at any more. Returns True if removed.

    A reclassify re-runs the pipeline over the same message and may land it
    on a different Job. The Job it *used* to point at is then referenced by
    nothing — but it stays in the table as ``pending`` forever, inflating
    the open-job count and raising ``undispatched`` alerts about work that
    is being tracked under a different row.

    Only deletes when the row is genuinely inert: no ``DispatchJob``
    children, no lifecycle history, and not named as another Job's
    ``duplicate_of`` parent (the FK is ``ON DELETE SET NULL``, so deleting
    a parent would silently unlink its children rather than fail). Alerts
    are ``ON DELETE CASCADE`` and go with it — that is the point, they
    describe a job that no longer exists.
    """
    job = await db.get(Job, job_id)
    if job is None:
        return False

    for model, column in (
        (DispatchJob, DispatchJob.job_id),
        (JobLifecycleEvent, JobLifecycleEvent.job_id),
        (Job, Job.duplicate_of),
    ):
        referenced = await db.execute(
            select(func.count()).select_from(model).where(column == job_id)
        )
        if referenced.scalar_one():
            return False

    await db.delete(job)
    await db.flush()
    return True


async def find_dedup_candidate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    street_number: str | None,
    street_name: str | None,
    customer_phone_e164: str | None,
    since: datetime,
    exclude_ids: Collection[uuid.UUID] | None = None,
) -> tuple[Job | None, bool]:
    """Find the first-seen Job matching the dedup keys.

    ``exclude_ids`` drops specific rows from consideration. The live path
    never needs it; ``cleanup-duplicate-jobs`` uses it to keep stranded
    rows — Jobs no message points at — from being chosen as a parent.

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

    Candidates are ranked by **match strength first, age second**:

    0. address match, same company
    1. address match, other company
    2. phone match, same company
    3. phone match, other company

    Age alone is not enough. Ordering purely by ``first_message_at`` lets
    an older, weaker phone hit shadow the exact address hit on the right
    job — and when that older job belongs to another company, the caller
    takes the cross-company branch and *creates a new Job* instead of
    linking, so two messages about one address become two Jobs parented
    to a third, unrelated one. Ranking address above phone, and the
    caller's own company above a stranger's, keeps the strongest
    available signal in charge.
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

    same_company = Job.company_id == company_id
    branches = []
    if address_match is not None:
        branches.append((and_(address_match, same_company), 0))
        branches.append((address_match, 1))
    if phone_match is not None:
        branches.append((and_(phone_match, same_company), 2))
    match_rank = case(*branches, else_=3)

    filters = [Job.first_message_at >= since, or_(*conditions)]
    if exclude_ids:
        filters.append(Job.id.notin_(list(exclude_ids)))

    query = (
        select(Job)
        .where(*filters)
        # created_at breaks ties: two rows can share a first_message_at
        # (the same message reprocessed), and an arbitrary winner would
        # make repeated runs disagree with each other.
        .order_by(match_rank.asc(), Job.first_message_at.asc(), Job.created_at.asc())
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


async def count_newer_jobs_openphone(
    db: AsyncSession,
    *,
    counterparty: str,
    after: datetime,
    until: datetime,
) -> int:
    """Count jobs this counterparty texted in during ``(after, until]``.

    Answers "is it ambiguous which job the operator's reply is about?".
    Zero means the candidate is the only job this broker has posted since,
    so a decline can only be about that one however many chat messages
    intervened.

    Backs the late-reject escape hatch in
    ``OpenPhoneService.maybe_reject_job``: the two-outbound-message cutoff
    is a proxy for that ambiguity, and a poor one — an operator who answers
    "Lmc" and "k" before declining has burned the budget without any second
    job ever arriving.
    """
    from app.db.models.dispatch_job import DispatchJob
    from app.db.models.openphone import IncomingMessage

    query = (
        select(func.count(func.distinct(Job.id)))
        .select_from(Job)
        .join(DispatchJob, DispatchJob.job_id == Job.id)
        .join(IncomingMessage, IncomingMessage.id == DispatchJob.incoming_message_id)
        .where(
            IncomingMessage.source == "openphone",
            IncomingMessage.direction == "incoming",
            IncomingMessage.from_number == counterparty,
            Job.first_message_at > after,
            Job.first_message_at <= until,
        )
    )
    result = await db.execute(query)
    return result.scalar_one()


# Non-terminal statuses eligible for a company-relay follow-up update.
# Excludes ``completed``/``closed``/``canceled``/``rejected`` — those jobs
# are done, so a later "customer never answered" remark in the same thread
# can't be about them. Kept as plain strings (matching ``Job.lifecycle_status``,
# a VARCHAR column) rather than importing ``LifecycleStatus`` to avoid a
# circular import between ``repositories.job`` and ``services.lifecycle``.
_FOLLOW_UP_ELIGIBLE_STATUSES = (
    "pending",
    "dispatched",
    "accepted",
    "in_progress",
    "appt_set",
    "needs_follow_up",
)


async def find_follow_up_candidate_openphone(
    db: AsyncSession,
    *,
    counterparty: str,
    before: datetime,
) -> Job | None:
    """Find the most-recent open Job an operator's status update is about.

    Same conversation-keyed lookup as :func:`find_reject_candidate_openphone`
    (matches the pending/open Job whose originating inbound message came
    ``from_number == counterparty``), but not restricted to ``pending`` —
    a "customer never answered, left a vm" update is expected to land well
    after intake, once the job has already moved past it. Returns just the
    ``Job`` (unlike the reject candidate, callers here don't need the
    original job body for a re-paste comparison).
    """
    query = (
        select(Job)
        .join(DispatchJob, DispatchJob.job_id == Job.id)
        .join(IncomingMessage, IncomingMessage.id == DispatchJob.incoming_message_id)
        .where(
            IncomingMessage.source == "openphone",
            IncomingMessage.direction == "incoming",
            IncomingMessage.from_number == counterparty,
            Job.lifecycle_status.in_(_FOLLOW_UP_ELIGIBLE_STATUSES),
            Job.first_message_at < before,
        )
        .order_by(Job.first_message_at.desc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def find_job_by_reference_openphone(
    db: AsyncSession,
    *,
    counterparty: str,
    before: datetime,
    reference,
    statuses: tuple[str, ...] = _FOLLOW_UP_ELIGIBLE_STATUSES,
) -> tuple[Job, str] | None:
    """Find the open Job an operator's re-paste explicitly names.

    ``reference`` is an ``app.services.job_reference.JobReference`` carrying
    whichever of PDL code / customer phone / street address the re-pasted
    body contained. Keys are tried strongest-first and the first one that
    resolves wins:

    1. **PDL** — matched against the raw text of the job's own inbound
       messages (the broker's per-job code is not modelled as a column).
    2. **Customer phone** — against ``Job.customer_phone_e164``.
    3. **Street number + name** — against the normalized address columns.

    Scoped to jobs whose originating inbound message came from
    ``counterparty``, so an update can never jump to another broker's job.
    Returns ``(job, source_body)`` — the body being the job's originating
    message, which callers need for the re-paste similarity comparison — or
    ``None`` when the reply names no job this counterparty has open.

    This exists because the plain "most recent open job from this
    counterparty" lookups (:func:`find_reject_candidate_openphone`,
    :func:`find_follow_up_candidate_openphone`) mis-target badly at volume:
    a single broker can have well over a hundred jobs open at once, so
    "most recent" is near-random. Callers should try this first and fall
    back to the recency lookups only when the reply carries no identity
    keys at all (a bare "dns"/"pass").
    """
    if not reference:
        return None

    base_conditions = [
        IncomingMessage.source == "openphone",
        IncomingMessage.direction == "incoming",
        IncomingMessage.from_number == counterparty,
        Job.lifecycle_status.in_(statuses),
        Job.first_message_at < before,
    ]

    def _query(extra):
        return (
            select(Job, IncomingMessage.content)
            .join(DispatchJob, DispatchJob.job_id == Job.id)
            .join(IncomingMessage, IncomingMessage.id == DispatchJob.incoming_message_id)
            .where(*base_conditions, extra)
            .order_by(Job.first_message_at.desc())
            .limit(1)
        )

    attempts = []
    if reference.pdl:
        # Match the code as it appears in the body ("PDL: PY3YA"). The
        # separator varies, so anchor on the code itself and require the
        # PDL label somewhere in the message via the second clause.
        escaped = reference.pdl.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        attempts.append(
            and_(
                IncomingMessage.content.ilike(f"%{escaped}%", escape="\\"),
                IncomingMessage.content.ilike("%pdl%", escape="\\"),
            )
        )
    if reference.customer_phone_e164:
        attempts.append(Job.customer_phone_e164 == reference.customer_phone_e164)
    if reference.has_address:
        attempts.append(
            and_(
                Job.address_street_number == reference.street_number,
                Job.address_street_name == reference.street_name,
            )
        )

    for extra in attempts:
        row = (await db.execute(_query(extra))).first()
        if row is not None:
            job, content = row
            return job, (content or "")

    return None


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


async def set_company(
    db: AsyncSession,
    *,
    job: Job,
    company_id: uuid.UUID | None,
) -> Job:
    """Manually reassign (or detach, if ``company_id`` is None) a Job's company.

    Used for correcting misclassifications after the fact — e.g. a message
    was regex-matched to the wrong company via a shared broker phone number.
    Setting ``company_id`` to None removes the job from every company's
    report (``get_company_status_breakdown`` filters on
    ``company_id IS NOT NULL``) without deleting the underlying row.
    """
    job.company_id = company_id
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


def _business_day_expr(col):
    """Chicago business-day bucket for a timestamptz column, in SQL.

    Converts to Chicago local time, shifts back by the 5am cutoff, then
    truncates to a day — the SQL equivalent of
    ``app.core.timezone.business_day_of``. Must stay in sync with that
    function if the cutoff hour or timezone ever changes.
    """
    chicago_local = func.timezone("America/Chicago", col)
    return func.date_trunc("day", chicago_local - text("interval '5 hours'"))


def _status_bucket_case():
    """The Job outcome-bucket CASE expression, shared by the count and
    detail queries below so they can never classify a job differently.

    Buckets (evaluated in order, first match wins):
    - ``rejected`` — ``lifecycle_status == 'rejected'``.
    - ``closed_completed`` — ``lifecycle_status`` in ``('closed', 'completed')``.
      Deliberately merged: the system elsewhere distinguishes a tech's
      "done" (``completed``) from the operator's authoritative filing
      (``closed``, see ``closing_unfiled`` alert), but this report reports
      them as one bucket per product decision.
    - ``scheduled_another_day`` — ``lifecycle_status == 'appt_set'`` AND the
      appointment's Chicago business day differs from the job's arrival
      business day. A same-day appointment does NOT count here — it falls
      through to ``still_open``.
    - ``canceled`` — ``lifecycle_status == 'canceled'``.
    - ``still_open`` — everything else (pending, dispatched, accepted,
      in_progress, needs_follow_up, and same-day appt_set).
    """
    return case(
        (Job.lifecycle_status == "rejected", "rejected"),
        (Job.lifecycle_status.in_(["closed", "completed"]), "closed_completed"),
        (
            and_(
                Job.lifecycle_status == "appt_set",
                Job.appt_at.is_not(None),
                _business_day_expr(Job.appt_at) != _business_day_expr(Job.first_message_at),
            ),
            "scheduled_another_day",
        ),
        (Job.lifecycle_status == "canceled", "canceled"),
        else_="still_open",
    )


_JOB_DETAIL_LIMIT = 500


def _in_range_membership(start: datetime, end: datetime, *, include_scheduled_appts: bool):
    """The Job membership filter for the report's date range.

    Base case: ``first_message_at`` falls in ``[start, end)`` — a job is
    anchored to the day it *arrived*.

    When ``include_scheduled_appts`` is set, membership widens to also
    include jobs that arrived on a *different* business day but whose
    appointment (``appt_at``) lands in ``[start, end)`` — i.e. exactly the
    jobs the base case already tags ``scheduled_another_day`` from the
    arrival day's perspective, now also surfaced from the appointment
    day's perspective. The "different business day" guard is what keeps
    same-day arrival+appointment jobs from being counted twice (they're
    already covered by the base ``first_message_at`` clause).
    """
    arrived_in_range = and_(Job.first_message_at >= start, Job.first_message_at < end)
    if not include_scheduled_appts:
        return arrived_in_range

    appt_in_range = and_(
        Job.appt_at.is_not(None),
        Job.appt_at >= start,
        Job.appt_at < end,
        _business_day_expr(Job.appt_at) != _business_day_expr(Job.first_message_at),
    )
    return or_(arrived_in_range, appt_in_range)


async def get_company_status_breakdown(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    include_scheduled_appts: bool = False,
) -> list[tuple[uuid.UUID, str, int]]:
    """Bucket every Job in range into one of 5 outcome buckets, grouped by company.

    A job is anchored to the day it *arrived*, not the day its status last
    changed — so a job that arrives on day X and closes 3 days later still
    counts toward day X, just in the ``closed_completed`` bucket instead of
    ``still_open``. "Day" means the Chicago business day (5am-to-midnight,
    see ``app.core.timezone``), not a UTC calendar day. See
    ``_status_bucket_case`` for the bucket rules.

    When ``include_scheduled_appts`` is true, the range membership widens
    per :func:`_in_range_membership` to also pull in jobs whose
    *appointment* (not arrival) lands in range — e.g. a job that arrived
    last week for a job scheduled today shows up in today's counts,
    whatever its current status (still pending, or already closed today).

    Returns ``(company_id, bucket, count)`` rows. Jobs with no company
    match (``company_id IS NULL``) are excluded — they never reached
    classification and have no company to report against. Jobs flagged
    ``is_duplicate`` are excluded too: they are a second row describing a
    real-world job already counted under ``duplicate_of``, so leaving them
    in double-counts the work. ``get_company_status_jobs`` applies the
    same filter so the drill-down rows always add up to the count.
    """
    bucket = _status_bucket_case().label("bucket")

    query = (
        select(Job.company_id, bucket, func.count().label("count"))
        .where(
            Job.company_id.is_not(None),
            Job.is_duplicate.is_(False),
            _in_range_membership(start, end, include_scheduled_appts=include_scheduled_appts),
        )
        .group_by(Job.company_id, bucket)
    )
    result = await db.execute(query)
    return [(row.company_id, row.bucket, row.count) for row in result.all()]


async def get_company_status_jobs(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    company_id: uuid.UUID,
    bucket: str | None = None,
    include_scheduled_appts: bool = False,
) -> list[dict]:
    """Detail rows behind one cell (or the whole row) of
    ``get_company_status_breakdown``.

    Uses the exact same ``_status_bucket_case`` classification and the same
    ``_in_range_membership`` range widening, filtered down to a single
    company, so operators can audit *which* jobs landed in a bucket instead
    of trusting the count alone. Pass ``bucket=None`` for the "Total"
    column — every job for the company in range, regardless of bucket.
    Ordered by ``first_message_at`` ascending (the order jobs actually
    arrived), capped at ``_JOB_DETAIL_LIMIT`` rows.

    Each row carries ``matched_by``: ``"arrival"`` if the job's
    ``first_message_at`` is what put it in range, ``"appointment"`` if it
    only qualifies because ``appt_at`` lands in range (arrived on a
    different day) — so the UI can flag those rows distinctly.
    """
    bucket_expr = _status_bucket_case().label("bucket")
    conditions = [
        Job.company_id == company_id,
        Job.is_duplicate.is_(False),
        _in_range_membership(start, end, include_scheduled_appts=include_scheduled_appts),
    ]
    if bucket is not None:
        conditions.append(_status_bucket_case() == bucket)

    jobs_q = (
        select(Job, bucket_expr)
        .where(*conditions)
        .order_by(Job.first_message_at.asc())
        .limit(_JOB_DETAIL_LIMIT)
    )
    result_rows = (await db.execute(jobs_q)).all()
    if not result_rows:
        return []

    jobs = [row[0] for row in result_rows]
    bucket_by_job = {row[0].id: row[1] for row in result_rows}

    job_ids = [job.id for job in jobs]
    dj_q = (
        select(DispatchJob)
        .where(DispatchJob.job_id.in_(job_ids))
        .order_by(DispatchJob.job_id, DispatchJob.created_at.asc())
        .options(selectinload(DispatchJob.incoming_message))
    )
    origin_by_job: dict[uuid.UUID, DispatchJob] = {}
    for dj in (await db.execute(dj_q)).scalars().all():
        if dj.job_id is not None and dj.job_id not in origin_by_job:
            origin_by_job[dj.job_id] = dj

    rows = []
    for job in jobs:
        origin = origin_by_job.get(job.id)
        message = origin.incoming_message if origin is not None else None
        address = " ".join(
            p for p in (job.address_street_number, job.address_street_name) if p
        ).strip() or (origin.address if origin is not None else None)
        preview = None
        if message is not None and message.content:
            preview = message.content[:200]
        arrived_in_range = start <= job.first_message_at < end
        rows.append(
            {
                "job_id": job.id,
                "dispatch_job_id": origin.id if origin is not None else None,
                "bucket": bucket_by_job[job.id],
                "lifecycle_status": job.lifecycle_status,
                "matched_by": "arrival" if arrived_in_range else "appointment",
                "first_message_at": job.first_message_at,
                "appt_at": job.appt_at,
                "address": address or None,
                "customer_name": origin.customer_name if origin is not None else None,
                "customer_phone": job.customer_phone_e164
                or (origin.customer_phone if origin is not None else None),
                "job_type": job.job_type or (origin.job_type if origin is not None else None),
                "message_preview": preview,
            }
        )
    return rows


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
