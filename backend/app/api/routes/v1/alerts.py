"""Pipeline alerts routes — read + resolve the open-alert dashboard.

The alerts dashboard lists every open ``Alert`` row the engine has
raised. Operators either:

- click into the linked Job and act on it (which auto-resolves stuck
  alerts via ``LifecycleService.transition`` → ``auto_resolve_for_job``);
- or click the Resolve button here to mark the alert resolved
  manually (e.g. they confirmed the false positive, or the engine
  fired too aggressively and the operator wants to suppress).

POST ``/alerts/{alert_id}/resolve`` is the only state-mutating
endpoint. Alert rows are otherwise append-only — there is no edit /
delete path on purpose.
"""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import NotFoundError
from app.repositories import alert as alert_repo
from app.repositories import job as job_repo
from app.schemas.alert import AlertJobSummary, AlertList, AlertMarkSeenResult, AlertRead

router = APIRouter()


def _alert_to_read(alert, *, job_summary: dict | None = None) -> AlertRead:
    """Convert an Alert ORM row to the response schema.

    ``job_summary`` is the pre-resolved parent-Job dict (from
    ``job_repo.get_alert_job_summaries``) for job-bound alerts; None for
    chat-bound alerts or when the parent Job no longer exists.
    """
    data = AlertRead.model_validate(alert)
    if job_summary is not None:
        return data.model_copy(update={"job": AlertJobSummary(**job_summary)})
    return data


async def _read_list(db, alerts: list) -> list[AlertRead]:
    """Batch-enrich a list of alerts with their parent-Job summaries."""
    job_ids = [a.job_id for a in alerts if a.job_id is not None]
    summaries = await job_repo.get_alert_job_summaries(db, job_ids)
    return [_alert_to_read(a, job_summary=summaries.get(a.job_id)) for a in alerts]


@router.get(
    "",
    response_model=AlertList,
    summary="List alerts (default: open only)",
)
async def list_alerts(
    db: DBSession,
    _user: CurrentUser,
    resolved: bool = Query(
        default=False,
        description="Include resolved alerts. Defaults to False so the "
        "dashboard shows only the operator's working queue.",
    ),
    kinds: list[str] | None = Query(
        default=None,
        description="Filter by alert kind. Repeat the param to allow-list "
        "multiple kinds (e.g. ``?kinds=stuck_dispatched&kinds=closing_missing``).",
    ),
    search: str | None = Query(
        default=None,
        min_length=1,
        description="Filter to alerts whose related job's raw incoming "
        "message contains this text (case-insensitive substring).",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List alerts newest-first.

    Without ``resolved=true`` this returns only the open set, which is
    what the dashboard shows. The ``include_resolved`` variant is
    available for the audit-trail view.
    """
    job_ids: list[uuid.UUID] | None = None
    if search is not None:
        job_ids = await job_repo.search_job_ids_by_message(db, search)
        if not job_ids:
            return AlertList(items=[], total=0)

    if resolved:
        items = await alert_repo.list_recent(
            db, limit=limit, offset=offset, include_resolved=True, job_ids=job_ids
        )
        total = len(items)  # naive but small enough for a v1
        unseen = 0  # "seen" only tracks the open queue, not the audit view
    else:
        items = await alert_repo.list_open(
            db, kinds=kinds, job_ids=job_ids, limit=limit, offset=offset
        )
        total = await alert_repo.count_open(db, kinds=kinds, job_ids=job_ids)
        unseen = await alert_repo.count_unseen(db, kinds=kinds)
    return AlertList(items=await _read_list(db, items), total=total, unseen=unseen)


@router.post(
    "/mark-seen",
    response_model=AlertMarkSeenResult,
    summary="Mark all open alerts as seen",
)
async def mark_alerts_seen(
    db: DBSession,
    _user: CurrentUser,
):
    """Clear the navbar's unseen count by marking every open alert as seen.

    Called when the operator opens the Alerts dashboard. Does not resolve
    anything — an alert can be seen and still unsolved.
    """
    marked = await alert_repo.mark_all_seen(db)
    await db.commit()
    return AlertMarkSeenResult(marked=marked)


@router.get(
    "/{alert_id}",
    response_model=AlertRead,
    summary="Get a single alert",
)
async def get_alert(
    alert_id: uuid.UUID,
    db: DBSession,
    _user: CurrentUser,
):
    """Fetch a single alert by id (used by the detail pane)."""
    alert = await alert_repo.get_by_id(db, alert_id)
    if alert is None:
        raise NotFoundError(
            message="Alert not found",
            details={"alert_id": str(alert_id)},
        )
    summaries = await job_repo.get_alert_job_summaries(db, [alert.job_id] if alert.job_id else [])
    return _alert_to_read(alert, job_summary=summaries.get(alert.job_id))


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertRead,
    summary="Manually resolve an alert",
)
async def resolve_alert(
    alert_id: uuid.UUID,
    db: DBSession,
    user: CurrentUser,
):
    """Mark an alert as resolved. Idempotent — re-resolving is a no-op."""
    alert = await alert_repo.get_by_id(db, alert_id)
    if alert is None:
        raise NotFoundError(
            message="Alert not found",
            details={"alert_id": str(alert_id)},
        )
    updated = await alert_repo.resolve(db, alert, user_id=user.id)
    await db.commit()
    await db.refresh(updated)
    return _alert_to_read(updated)
