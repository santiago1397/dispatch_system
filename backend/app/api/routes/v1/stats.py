"""Daily stats routes — read snapshots and stream CSV/JSON exports.

The dashboard at ``/stats`` is read-only against ``daily_stats_snapshots``
(the ``daily-stats`` service / scheduler pre-computes the rollups). The
``GET /stats/export`` endpoint streams a CSV or JSON download without
loading the full result set in memory — important because a single
``per_job`` snapshot over a high-traffic day can be tens of thousands of
rows.
"""

import csv
import io
import json
from datetime import date, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DBSession
from app.core.timezone import business_today
from app.repositories import daily_stats as stats_repo
from app.schemas.daily_stats import DailyStatsList, DailyStatsSnapshotRead

router = APIRouter()


def _snapshot_to_read(snap) -> DailyStatsSnapshotRead:
    """Convert a DailyStatsSnapshot ORM row to the response schema."""
    return DailyStatsSnapshotRead.model_validate(snap)


@router.get(
    "",
    response_model=DailyStatsList,
    summary="List snapshots for a date",
)
async def list_stats(
    db: DBSession,
    _user: CurrentUser,
    snapshot_date: date = Query(
        default_factory=lambda: business_today() - timedelta(days=1),
        description="Date to fetch snapshots for. Defaults to yesterday.",
    ),
    scope: str | None = Query(
        default=None,
        description="Filter by scope (per_job / per_tech / per_company).",
    ),
):
    """List the snapshot rows for one date, newest first.

    The dashboard renders this directly. CSV/JSON export are streamed
    via the ``/stats/export`` endpoint below.
    """
    items = await stats_repo.list_for_date(db, snapshot_date, scope=scope)
    return DailyStatsList(
        items=[_snapshot_to_read(s) for s in items],
        total=len(items),
    )


@router.get(
    "/export",
    summary="Stream a CSV or JSON export of daily snapshots",
    responses={
        200: {
            "description": "StreamingResponse with the requested format",
            "content": {
                "text/csv": {},
                "application/json": {},
            },
        }
    },
)
async def export_stats(
    db: DBSession,
    _user: CurrentUser,
    snapshot_date: date = Query(
        default_factory=lambda: business_today() - timedelta(days=1),
        description="Date to export. Defaults to yesterday.",
    ),
    scope: str | None = Query(
        default=None,
        description="Filter by scope. When omitted, all three scopes are included in the export.",
    ),
    format: str = Query(
        default="csv",
        alias="format",
        pattern="^(csv|json)$",
        description="Output format: csv or json.",
    ),
):
    """Stream a CSV or JSON download of the snapshot rows.

    CSV columns: ``snapshot_date, scope, scope_id, payload_json``. The
    payload is dumped as a JSON-encoded string so a downstream analyst
    can re-shape it in pandas / jq without us hard-coding every payload
    key into the export header.
    """
    items = await stats_repo.list_for_date(db, snapshot_date, scope=scope)

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["snapshot_date", "scope", "scope_id", "payload"])
        for snap in items:
            writer.writerow(
                [
                    snap.snapshot_date.isoformat(),
                    snap.scope,
                    str(snap.scope_id) if snap.scope_id else "",
                    json.dumps(snap.payload or {}),
                ]
            )
        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="daily-stats-{snapshot_date.isoformat()}.csv"'
                )
            },
        )

    # JSON path
    payload = [
        {
            "snapshot_date": snap.snapshot_date.isoformat(),
            "scope": snap.scope,
            "scope_id": str(snap.scope_id) if snap.scope_id else None,
            "payload": snap.payload or {},
        }
        for snap in items
    ]
    body = json.dumps(payload, indent=2)
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="daily-stats-{snapshot_date.isoformat()}.json"'
            )
        },
    )
