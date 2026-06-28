"""Dispatch job routes — classified jobs from webhook messages."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.repositories import job as job_repo
from app.schemas.dispatch_job import DispatchJobList, DispatchJobRead
from app.schemas.job_lifecycle_event import (
    JobLifecycleEventList,
    JobLifecycleEventRead,
    LifecycleTransitionIn,
)
from app.services.dispatch_job import DispatchJobService
from app.services.lifecycle import LifecycleService

router = APIRouter()


def _job_to_read(job) -> DispatchJobRead:
    """Convert a DispatchJob ORM object to a DispatchJobRead schema."""
    data = DispatchJobRead.model_validate(job)
    raw = job.extraction_raw or {}
    parent_job = job.job
    return data.model_copy(
        update={
            "company_name": job.company.display_name if job.company else None,
            "source": job.incoming_message.source if job.incoming_message else None,
            # Closing-flow extras live in extraction_raw on the closing
            # dispatch_job. Tip and notes aren't in the standard column set.
            "closing_tip": raw.get("tip"),
            "closing_notes": raw.get("notes"),
            # Lifecycle pipeline state — denormalized from the parent Job.
            "lifecycle_status": parent_job.lifecycle_status if parent_job else None,
            "lifecycle_status_changed_at": (
                parent_job.lifecycle_status_changed_at if parent_job else None
            ),
        }
    )


@router.get("/jobs", response_model=DispatchJobList)
async def list_dispatch_jobs(
    db: DBSession,
    _user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
    status: str | None = Query(default=None, description="Filter by classification status"),
    company_id: UUID | None = Query(default=None, description="Filter by company UUID"),
    since: datetime | None = Query(
        default=None, description="Lower bound on dispatch_jobs.created_at (inclusive)"
    ),
    until: datetime | None = Query(
        default=None, description="Upper bound on dispatch_jobs.created_at (inclusive)"
    ),
    exclude_statuses: list[str] = Query(
        default=["not_a_job"],
        description="Statuses to exclude (repeated param). Defaults to hiding rows the "
        "classifier determined aren't jobs.",
    ),
    q: str | None = Query(
        default=None,
        min_length=2,
        description="Case-insensitive substring search over the raw incoming message body.",
    ),
):
    """List classified dispatch jobs with pagination.

    All filters are optional and combine with AND. ``since``/``until``
    accept ISO-8601 datetimes (e.g., ``2026-06-01T00:00:00Z``).
    ``exclude_statuses`` defaults to ``["not_a_job"]`` so the operator
    Jobs view hides non-job rows; pass an empty list to disable.
    """
    service = DispatchJobService(db)
    jobs, total = await service.list_jobs(
        skip=skip,
        limit=limit,
        status=status,
        company_id=company_id,
        since=since,
        until=until,
        exclude_statuses=exclude_statuses,
        search=q,
    )
    return DispatchJobList(items=[_job_to_read(job) for job in jobs], total=total)


@router.get("/jobs/{job_id}", response_model=DispatchJobRead)
async def get_dispatch_job(
    job_id: UUID,
    db: DBSession,
    _user: CurrentUser,
):
    """Get a single dispatch job by ID."""
    service = DispatchJobService(db)
    job = await service.get_job(job_id)
    return _job_to_read(job)


@router.post("/jobs/{job_id}/reclassify", response_model=DispatchJobRead)
async def reclassify_job(
    job_id: UUID,
    db: DBSession,
    _user: CurrentUser,
):
    """Re-run classification pipeline on a dispatch job."""
    service = DispatchJobService(db)
    job = await service.reclassify(job_id)
    await db.commit()
    await db.refresh(job)
    return _job_to_read(job)


@router.post("/jobs/{job_id}/rematch-closing", response_model=DispatchJobRead)
async def rematch_closing(
    job_id: UUID,
    db: DBSession,
    _user: CurrentUser,
):
    """Replay closing-to-Job matching for a closing_unmatched row.

    Uses the stored ClosingExtraction in ``extraction_raw`` — no
    re-extraction. Intended for the case where the original Job lands
    after the closing message.
    """
    service = DispatchJobService(db)
    job = await service.rematch_closing(job_id)
    await db.commit()
    # Reload with company + incoming_message eager-loaded so _job_to_read works.
    fresh = await service.get_job(job.id)
    return _job_to_read(fresh)


@router.patch(
    "/jobs/{job_id}/lifecycle",
    response_model=DispatchJobRead,
    summary="Manual lifecycle override from /jobs/[id]",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Invalid transition or missing note for cancellation"},
    },
)
async def set_lifecycle_status(
    job_id: UUID,
    body_in: LifecycleTransitionIn,
    db: DBSession,
    user: CurrentUser,
):
    """Manually transition a Job to a new lifecycle status.

    Every transition flows through ``LifecycleService.transition`` so the
    audit log + outbound draft are written in the same transaction. The
    state-machine guard rejects ``to_status='closed'`` (closing must come
    through ``CLOSING_CHAT_JID``) and requires a non-empty ``note`` when
    manually canceling.
    """
    job = await job_repo.get_job_by_id(db, job_id)
    if job is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError(
            message="Dispatch job not found",
            details={"job_id": str(job_id)},
        )

    payload = {"note": body_in.note} if body_in.note else {}
    service = LifecycleService(db)
    await service.transition(
        job=job,
        to_status=body_in.to_status,
        source=LifecycleEventSource.MANUAL,
        payload=payload,
        user_id=user.id,
    )
    await db.commit()
    await db.refresh(job)
    # Reload with company + incoming_message eager-loaded so _job_to_read works.
    fresh = await DispatchJobService(db).get_job(job.id)
    return _job_to_read(fresh)


@router.get(
    "/jobs/{job_id}/lifecycle",
    response_model=JobLifecycleEventList,
    summary="Get the lifecycle event timeline for a job",
)
async def get_job_lifecycle(
    job_id: UUID,
    db: DBSession,
    _user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List the append-only lifecycle events for a job, newest-first.

    Powers the ``<LifecycleTimeline>`` component on ``/jobs/[id]``.
    """
    from app.repositories import job_lifecycle_event as lifecycle_repo

    events, total = await lifecycle_repo.list_for_job_paginated(
        db, job_id, limit=limit, offset=offset
    )
    return JobLifecycleEventList(
        items=[JobLifecycleEventRead.model_validate(e) for e in events],
        total=total,
    )
