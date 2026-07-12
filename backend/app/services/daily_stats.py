"""Daily statistics service — pre-computes rollups for /stats + export.

Three scopes:

- ``per_job`` — one row per Job that had any lifecycle event in the
  snapshot date. Payload carries from→to status sequence, time-to-
  dispatch / complete, appointment time, and the closed totals.
- ``per_tech`` — one row per Technician that received a dispatch in
  the date. Payload carries dispatched count, completed count, and
  average response / completion minutes.
- ``per_company`` — one row per Company that received jobs in the
  date. Payload carries jobs received, jobs completed, average total
  minutes, and total revenue (sum of ``closed_total`` for closed jobs).

The service is invoked:

1. From the APScheduler cron at ``STATS_DAILY_HOUR:STATS_DAILY_MINUTE``
   (default 00:15 Chicago, 15 minutes after the business day closes) —
   see ``main.py:lifespan``.
2. From the ``daily-stats`` CLI command — for ad-hoc runs against a
   backfill date.

The snapshot is upserted on ``(snapshot_date, scope, scope_id)`` so
re-running for the same date overwrites the prior rollup — important
for the manual reprocessing case (operator changed a job's closed_at
yesterday and wants fresh stats).

``snapshot_date`` is a Chicago business day (5am-to-midnight, see
``app.core.timezone``), not a UTC calendar day.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import business_day_bounds
from app.db.models.daily_stats import StatsScope
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import (
    JobLifecycleEvent,
    LifecycleEventSource,
)
from app.repositories import daily_stats as stats_repo

logger = logging.getLogger(__name__)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    """Return [start_of_business_day, start_of_next_business_day) in UTC."""
    return business_day_bounds(d)


def _parse_money(s: str | None) -> float:
    """Parse a money string like ``'$123.45'`` or ``'123.45'`` into a float.

    ``Job.closed_total`` is stored as a free-text string (the tech may
    type ``'$123.45 + tip'``); for rollups we accept anything that has
    at least one parseable numeric prefix.
    """
    if not s:
        return 0.0
    cleaned = s.replace("$", "").replace(",", "").strip()
    # Take the first whitespace-delimited token.
    head = cleaned.split()[0] if cleaned.split() else cleaned
    try:
        return float(head)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class _PerJobAccumulator:
    """Mutable accumulator while iterating events for one job on a date."""

    job_id: uuid.UUID
    company_id: uuid.UUID | None
    technician_id: uuid.UUID | None
    sequence: list[dict]
    first_event_at: datetime
    last_event_at: datetime
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None
    appt_iso: str | None = None
    closed_total: str | None = None
    closed_payment_method: str | None = None

    def to_payload(self) -> dict:
        """Serialize to the JSONB payload."""
        time_to_dispatch_min: float | None = None
        if self.dispatched_at is not None:
            time_to_dispatch_min = (self.dispatched_at - self.first_event_at).total_seconds() / 60.0
        time_to_complete_min: float | None = None
        if self.completed_at is not None:
            time_to_complete_min = (self.completed_at - self.first_event_at).total_seconds() / 60.0
        return {
            "job_id": str(self.job_id),
            "company_id": str(self.company_id) if self.company_id else None,
            "technician_id": str(self.technician_id) if self.technician_id else None,
            "from_to_sequence": self.sequence,
            "first_event_at": self.first_event_at.isoformat(),
            "last_event_at": self.last_event_at.isoformat(),
            "time_to_dispatch_min": time_to_dispatch_min,
            "time_to_complete_min": time_to_complete_min,
            "appt_iso": self.appt_iso,
            "closed_total": self.closed_total,
            "closed_payment_method": self.closed_payment_method,
        }


class DailyStatsService:
    """Snapshot rollup service."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def snapshot(self, *, snapshot_date: date) -> int:
        """Run the full rollup for one date. Returns the number of rows upserted.

        Caller commits.
        """
        start, end = _day_bounds(snapshot_date)
        per_job_count = await self._snapshot_per_job(start, end, snapshot_date)
        per_tech_count = await self._snapshot_per_tech(start, end, snapshot_date)
        per_company_count = await self._snapshot_per_company(start, end, snapshot_date)
        total = per_job_count + per_tech_count + per_company_count
        logger.info(
            "DAILY_STATS_DONE date=%s per_job=%d per_tech=%d per_company=%d",
            snapshot_date.isoformat(),
            per_job_count,
            per_tech_count,
            per_company_count,
        )
        return total

    # -----------------------------------------------------------------
    # per_job
    # -----------------------------------------------------------------

    async def _snapshot_per_job(self, start: datetime, end: datetime, snapshot_date: date) -> int:
        """One snapshot row per Job that had an event in [start, end).

        ``scope_id`` is the job's id — required so the
        ``(snapshot_date, scope, scope_id)`` unique constraint can tell
        two jobs on the same day apart (Postgres treats NULL as never
        equal to NULL, so a shared NULL scope_id would neither dedupe
        nor conflict across jobs).
        """
        # Pull all events for the date in one query, with eager job info.
        query = (
            select(JobLifecycleEvent, Job.company_id, Job.customer_phone_e164)
            .join(Job, JobLifecycleEvent.job_id == Job.id)
            .where(
                JobLifecycleEvent.created_at >= start,
                JobLifecycleEvent.created_at < end,
            )
            .order_by(JobLifecycleEvent.job_id, JobLifecycleEvent.created_at.asc())
        )
        rows = list((await self.db.execute(query)).all())

        # Group events by job_id.
        by_job: dict[uuid.UUID, list[tuple[JobLifecycleEvent, uuid.UUID | None]]] = defaultdict(
            list
        )
        for event, company_id, _phone in rows:
            by_job[event.job_id].append((event, company_id))

        count = 0
        for job_id, events in by_job.items():
            events.sort(key=lambda pair: pair[0].created_at)
            company_id = events[0][1]
            acc = _PerJobAccumulator(
                job_id=job_id,
                company_id=company_id,
                technician_id=None,
                sequence=[],
                first_event_at=events[0][0].created_at,
                last_event_at=events[-1][0].created_at,
            )
            for event, _company_id in events:
                acc.sequence.append(
                    {
                        "from": event.from_status,
                        "to": event.to_status,
                        "at": event.created_at.isoformat(),
                        "source": event.source,
                    }
                )
                if event.to_status == "dispatched" and acc.dispatched_at is None:
                    acc.dispatched_at = event.created_at
                    # ``payload.technician_id`` is written by the
                    # operator-dispatch handler in whatsapp.py.
                    tid = event.payload.get("technician_id") if event.payload else None
                    if isinstance(tid, str):
                        try:
                            acc.technician_id = uuid.UUID(tid)
                        except ValueError:
                            acc.technician_id = None
                if event.to_status == "completed" and acc.completed_at is None:
                    acc.completed_at = event.created_at
                if event.to_status == "appt_set":
                    iso = event.payload.get("appt_iso") if event.payload else None
                    if iso:
                        acc.appt_iso = iso
                if event.source == LifecycleEventSource.CLOSING_CHAT and event.payload:
                    if event.payload.get("closed_total"):
                        acc.closed_total = event.payload["closed_total"]
                    if event.payload.get("closed_payment_method"):
                        acc.closed_payment_method = event.payload["closed_payment_method"]
            await stats_repo.upsert_snapshot(
                self.db,
                snapshot_date=snapshot_date,
                scope=StatsScope.PER_JOB.value,
                scope_id=job_id,
                payload=acc.to_payload(),
            )
            count += 1
        return count

    # -----------------------------------------------------------------
    # per_tech
    # -----------------------------------------------------------------

    async def _snapshot_per_tech(self, start: datetime, end: datetime, snapshot_date: date) -> int:
        """One snapshot row per Technician that received dispatches.

        ``avg_response_min`` = average minutes from operator dispatch
        event to the first tech reply in the same chat. ``avg_completion_min``
        = average minutes from dispatch to terminal (completed/closed).
        """
        # Pull all dispatch events for the date.
        dispatch_query = select(JobLifecycleEvent).where(
            and_(
                JobLifecycleEvent.created_at >= start,
                JobLifecycleEvent.created_at < end,
                JobLifecycleEvent.source == LifecycleEventSource.OPERATOR_WHATSAPP,
            )
        )
        dispatch_events = list((await self.db.execute(dispatch_query)).scalars().all())
        # Group by technician_id extracted from payload.
        by_tech: dict[uuid.UUID, list[JobLifecycleEvent]] = defaultdict(list)
        for ev in dispatch_events:
            tid_raw = ev.payload.get("technician_id") if ev.payload else None
            if not isinstance(tid_raw, str):
                continue
            try:
                tid = uuid.UUID(tid_raw)
            except ValueError:
                continue
            by_tech[tid].append(ev)

        if not by_tech:
            return 0

        # For response time: find the earliest non-operator event after
        # each dispatch within the same chat.
        response_minutes_by_tech: dict[uuid.UUID, list[float]] = defaultdict(list)
        for tid, evs in by_tech.items():
            for dispatch_ev in evs:
                chat_jid = dispatch_ev.payload.get("chat_jid") if dispatch_ev.payload else None
                if not chat_jid:
                    continue
                # First non-operator event after dispatch_ev.created_at
                # in the same chat. We bound the search to 7 days after
                # dispatch to avoid pulling the entire history.
                reply_query = (
                    select(JobLifecycleEvent)
                    .where(
                        JobLifecycleEvent.created_at > dispatch_ev.created_at,
                        JobLifecycleEvent.created_at < dispatch_ev.created_at + timedelta(days=7),
                        JobLifecycleEvent.payload["chat_jid"].astext == chat_jid,
                        JobLifecycleEvent.source != LifecycleEventSource.OPERATOR_WHATSAPP,
                    )
                    .order_by(JobLifecycleEvent.created_at.asc())
                    .limit(1)
                )
                reply = (await self.db.execute(reply_query)).scalar_one_or_none()
                if reply is not None:
                    minutes = (reply.created_at - dispatch_ev.created_at).total_seconds() / 60.0
                    response_minutes_by_tech[tid].append(minutes)

        # For completion time: look up the terminal event per dispatch.
        completion_minutes_by_tech: dict[uuid.UUID, list[float]] = defaultdict(list)
        for tid, evs in by_tech.items():
            for dispatch_ev in evs:
                terminal_query = (
                    select(JobLifecycleEvent)
                    .where(
                        JobLifecycleEvent.job_id == dispatch_ev.job_id,
                        JobLifecycleEvent.created_at > dispatch_ev.created_at,
                        JobLifecycleEvent.to_status.in_(["completed", "closed", "canceled"]),
                    )
                    .order_by(JobLifecycleEvent.created_at.asc())
                    .limit(1)
                )
                terminal = (await self.db.execute(terminal_query)).scalar_one_or_none()
                if terminal is not None:
                    minutes = (terminal.created_at - dispatch_ev.created_at).total_seconds() / 60.0
                    completion_minutes_by_tech[tid].append(minutes)

        count = 0
        for tid, evs in by_tech.items():
            completed_count = sum(1 for mn in completion_minutes_by_tech.get(tid, []))
            payload = {
                "technician_id": str(tid),
                "jobs_dispatched": len(evs),
                "jobs_completed": completed_count,
                "avg_response_min": (
                    sum(response_minutes_by_tech.get(tid, []))
                    / len(response_minutes_by_tech.get(tid, []))
                    if response_minutes_by_tech.get(tid)
                    else None
                ),
                "avg_completion_min": (
                    sum(completion_minutes_by_tech.get(tid, []))
                    / len(completion_minutes_by_tech.get(tid, []))
                    if completion_minutes_by_tech.get(tid)
                    else None
                ),
            }
            await stats_repo.upsert_snapshot(
                self.db,
                snapshot_date=snapshot_date,
                scope=StatsScope.PER_TECH.value,
                scope_id=tid,
                payload=payload,
            )
            count += 1
        return count

    # -----------------------------------------------------------------
    # per_company
    # -----------------------------------------------------------------

    async def _snapshot_per_company(
        self, start: datetime, end: datetime, snapshot_date: date
    ) -> int:
        """One snapshot row per Company that received jobs in the date.

        ``total_revenue`` is the sum of ``closed_total`` for jobs that
        closed in the date (free-text money → numeric prefix).
        """
        # Pull Jobs whose company_id is non-null AND (first_message_at in
        # date OR closed_at in date). One query, two branches.
        query = select(Job).where(
            Job.company_id.is_not(None),
            or_(
                and_(
                    Job.first_message_at >= start,
                    Job.first_message_at < end,
                ),
                and_(
                    Job.closed_at >= start,
                    Job.closed_at < end,
                ),
            ),
        )
        jobs = list((await self.db.execute(query)).scalars().all())

        # Group by company_id; track received / completed counts + revenue.
        received: dict[uuid.UUID, int] = defaultdict(int)
        completed: dict[uuid.UUID, int] = defaultdict(int)
        revenue: dict[uuid.UUID, float] = defaultdict(float)
        completion_times: dict[uuid.UUID, list[float]] = defaultdict(list)
        for job in jobs:
            assert job.company_id is not None
            received[job.company_id] += 1
            if job.closed_at is not None and start <= job.closed_at < end:
                completed[job.company_id] += 1
                revenue[job.company_id] += _parse_money(job.closed_total)
                if job.first_message_at is not None:
                    minutes = (job.closed_at - job.first_message_at).total_seconds() / 60.0
                    completion_times[job.company_id].append(minutes)

        count = 0
        for company_id in set(received) | set(completed):
            times = completion_times.get(company_id, [])
            payload = {
                "company_id": str(company_id),
                "jobs_received": received.get(company_id, 0),
                "jobs_completed": completed.get(company_id, 0),
                "avg_total_min": (sum(times) / len(times)) if times else None,
                "total_revenue": revenue.get(company_id, 0.0),
            }
            await stats_repo.upsert_snapshot(
                self.db,
                snapshot_date=snapshot_date,
                scope=StatsScope.PER_COMPANY.value,
                scope_id=company_id,
                payload=payload,
            )
            count += 1
        return count
