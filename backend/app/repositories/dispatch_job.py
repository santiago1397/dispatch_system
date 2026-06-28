"""Repository for dispatch job data access."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.dispatch_job import DispatchJob
from app.db.models.openphone import IncomingMessage


async def create_dispatch_job(
    db: AsyncSession,
    *,
    incoming_message_id: UUID,
    classification_status: str = "pending",
) -> DispatchJob:
    """Create a new dispatch job record."""
    job = DispatchJob(
        incoming_message_id=incoming_message_id,
        classification_status=classification_status,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def get_by_id(db: AsyncSession, job_id: UUID) -> DispatchJob | None:
    """Get a dispatch job by ID with company eagerly loaded."""
    query = (
        select(DispatchJob)
        .where(DispatchJob.id == job_id)
        .options(
            selectinload(DispatchJob.company),
            selectinload(DispatchJob.incoming_message),
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_by_message_id(db: AsyncSession, message_id: UUID) -> DispatchJob | None:
    """Get a dispatch job by its incoming message ID."""
    query = select(DispatchJob).where(DispatchJob.incoming_message_id == message_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


# Fields that are allowed to be explicitly set to None (for reclassify resets)
_NULLABLE_FIELDS = frozenset(
    {
        "company_id",
        "job_id",
        "classification_status",
        "classification_method",
        "classification_error",
        "address",
        "job_type",
        "total",
        "parts",
        "payment_method",
        "tech_name",
        "car_make",
        "car_model",
        "car_year",
        "customer_name",
        "customer_phone",
        "scheduled_at",
        "job_description",
        "extraction_raw",
    }
)


async def update_dispatch_job(db: AsyncSession, *, job: DispatchJob, **kwargs) -> DispatchJob:
    """Update a dispatch job with the given fields.

    Fields in _NULLABLE_FIELDS can be explicitly set to None (used by reclassify).
    Other fields are only set when the value is not None.
    """
    for key, value in kwargs.items():
        if value is not None or key in _NULLABLE_FIELDS:
            setattr(job, key, value)
    await db.flush()
    await db.refresh(job)
    return job


async def list_dispatch_jobs(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    company_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_statuses: list[str] | None = None,
    search: str | None = None,
) -> list[DispatchJob]:
    """List dispatch jobs with optional filters.

    ``since`` and ``until`` are inclusive lower/upper bounds on
    ``dispatch_jobs.created_at``. Either may be ``None`` for an open
    bound. The two combine to form a date range — typical use is the
    operator's "today" or "this week" view.

    ``exclude_statuses`` is a denylist applied on top of ``status``
    (logical AND). Use it to hide states the caller doesn't care about
    (e.g., ``["not_a_job"]`` for the operator Jobs view).
    """
    query = (
        select(DispatchJob)
        .order_by(DispatchJob.created_at.desc())
        .options(
            selectinload(DispatchJob.company),
            selectinload(DispatchJob.incoming_message),
        )
    )
    if status:
        query = query.where(DispatchJob.classification_status == status)
    if exclude_statuses:
        query = query.where(DispatchJob.classification_status.not_in(exclude_statuses))
    if company_id:
        query = query.where(DispatchJob.company_id == company_id)
    if since is not None:
        query = query.where(DispatchJob.created_at >= since)
    if until is not None:
        query = query.where(DispatchJob.created_at <= until)
    if search:
        query = query.join(
            IncomingMessage, DispatchJob.incoming_message_id == IncomingMessage.id
        ).where(IncomingMessage.content.ilike(f"%{search}%"))
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_dispatch_jobs(
    db: AsyncSession,
    *,
    status: str | None = None,
    company_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_statuses: list[str] | None = None,
    search: str | None = None,
) -> int:
    """Count dispatch jobs with optional filters. See ``list_dispatch_jobs``."""
    query = select(func.count()).select_from(DispatchJob)
    if status:
        query = query.where(DispatchJob.classification_status == status)
    if exclude_statuses:
        query = query.where(DispatchJob.classification_status.not_in(exclude_statuses))
    if company_id:
        query = query.where(DispatchJob.company_id == company_id)
    if since is not None:
        query = query.where(DispatchJob.created_at >= since)
    if until is not None:
        query = query.where(DispatchJob.created_at <= until)
    if search:
        query = query.join(
            IncomingMessage, DispatchJob.incoming_message_id == IncomingMessage.id
        ).where(IncomingMessage.content.ilike(f"%{search}%"))
    result = await db.execute(query)
    return result.scalar_one()
