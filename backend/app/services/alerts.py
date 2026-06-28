"""Pipeline alert engine — surfaces stuck jobs + missing closings.

The engine runs one idempotent pass per call (``scan``). It is invoked
from two places:

1. APScheduler — every ``ALERT_ENGINE_INTERVAL_MINUTES`` (default 5)
   inside the FastAPI lifespan, gated on ``SCHEDULER_ENABLED``.
2. The ``alert-engine`` CLI command — for ad-hoc runs and tests.

Each scan returns a counts dict ``{kind: number_created}`` so the
caller can log ``ALERT_SCAN_DONE counts=...``. Re-running the scan
never creates duplicates — ``alert_repo.create_or_get_open`` is
idempotent on ``(kind, job_id)`` / ``(kind, chat_jid)``.

Alert kinds emitted here (the chat-bound ones
``dispatch_no_match`` / ``unattributed_reply`` are emitted directly
from Phase-3 ingestion paths, not here):

- ``stuck_dispatched``   — dispatched > STUCK_DISPATCHED_MINUTES, no later event
- ``stuck_in_progress``  — in_progress > STUCK_IN_PROGRESS_MINUTES, no later event
- ``appt_time_passed``   — appt_set with appt_iso < now-1h, no later event
- ``closing_missing``    — non-terminal with no close in CLOSING_GRACE_MINUTES

When a job reaches a terminal status (``closed`` / ``canceled``),
``LifecycleService.transition`` calls ``alert_repo.auto_resolve_for_job``
to clear any stuck- alerts; this scan respects the ``resolved_at IS
NULL`` filter so already-resolved rows are not double-counted.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.alert import Alert, AlertKind
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import JobLifecycleEvent, LifecycleEventSource
from app.repositories import alert as alert_repo
from app.services.lifecycle import LifecycleStatus

logger = logging.getLogger(__name__)


@dataclass
class AlertScanCounts:
    """Outcome of one ``AlertEngine.scan`` pass.

    ``created`` is the number of NEW alerts raised this pass;
    ``already_open`` is the count of ``create_or_get_open`` calls that
    returned the existing row (i.e. the threshold was already flagged
    on a previous pass — the idempotent path).
    """

    created: dict[str, int]
    already_open: dict[str, int]

    def total_created(self) -> int:
        return sum(self.created.values())

    def to_log_dict(self) -> dict[str, int]:
        return {
            **{f"created.{k}": v for k, v in self.created.items()},
            **{f"already_open.{k}": v for k, v in self.already_open.items()},
        }


class AlertEngine:
    """Pipeline-health alert scanner."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def scan(self, *, now: datetime | None = None) -> AlertScanCounts:
        """Run one full scan pass. Caller commits."""
        now = now or datetime.now(UTC)
        created: dict[str, int] = {}
        already_open: dict[str, int] = {}

        for fn in (
            self._scan_stuck_dispatched,
            self._scan_stuck_in_progress,
            self._scan_appt_time_passed,
            self._scan_closing_missing,
        ):
            c, a = await fn(now)
            created[fn.__name__.removeprefix("_scan_")] = c
            already_open[fn.__name__.removeprefix("_scan_")] = a

        logger.info(
            "ALERT_SCAN_DONE created=%s already_open=%s",
            created,
            already_open,
        )
        return AlertScanCounts(created=created, already_open=already_open)

    # -----------------------------------------------------------------
    # Per-kind scanners
    # -----------------------------------------------------------------

    async def _scan_stuck_dispatched(self, now: datetime) -> tuple[int, int]:
        """Flag jobs that have been ``dispatched`` for too long."""
        return await self._scan_stuck(
            kind=AlertKind.STUCK_DISPATCHED,
            threshold_minutes=settings.ALERTS_STUCK_DISPATCHED_MINUTES,
            now=now,
        )

    async def _scan_stuck_in_progress(self, now: datetime) -> tuple[int, int]:
        """Flag jobs that have been ``in_progress`` for too long."""
        return await self._scan_stuck(
            kind=AlertKind.STUCK_IN_PROGRESS,
            threshold_minutes=settings.ALERTS_STUCK_IN_PROGRESS_MINUTES,
            now=now,
        )

    async def _scan_stuck(
        self,
        *,
        kind: str,
        threshold_minutes: int,
        now: datetime,
    ) -> tuple[int, int]:
        """Generic stuck-status scanner.

        A job is "stuck" when ``lifecycle_status == X`` AND
        ``lifecycle_status_changed_at < now - threshold`` AND there is
        no later event AND no existing open alert of the same kind for
        the job. ``create_or_get_open`` does the dedup, so the count
        here is the size of the candidate set — not a guarantee of new
        inserts.
        """
        cutoff = now - timedelta(minutes=threshold_minutes)
        target_status = (
            LifecycleStatus.DISPATCHED.value
            if kind == AlertKind.STUCK_DISPATCHED.value
            else LifecycleStatus.IN_PROGRESS.value
        )
        query = (
            select(Job)
            .where(
                Job.lifecycle_status == target_status,
                Job.lifecycle_status_changed_at.is_not(None),
                Job.lifecycle_status_changed_at < cutoff,
            )
            .order_by(Job.lifecycle_status_changed_at.asc())
        )
        candidates = list((await self.db.execute(query)).scalars().all())

        # Subtract jobs that already have an open alert of this kind.
        # We use a single IN-list query rather than per-job existence
        # checks (the candidate set is bounded by ``list_by_status``'s
        # default cap of 100 in the repo; this scan does the same).
        already_job_ids = await self._open_alert_job_ids(kind)
        candidates = [j for j in candidates if j.id not in already_job_ids]

        created = 0
        for job in candidates:
            await alert_repo.create_or_get_open(
                self.db,
                kind=kind,
                job_id=job.id,
                threshold_minutes=threshold_minutes,
                payload={
                    "since": job.lifecycle_status_changed_at.isoformat()
                    if job.lifecycle_status_changed_at
                    else None,
                },
                detected_at=now,
            )
            created += 1
        return created, len(already_job_ids)

    async def _scan_appt_time_passed(self, now: datetime) -> tuple[int, int]:
        """Flag jobs whose scheduled appointment is in the past by 1h+.

        Looks for the most recent ``appt_set`` event per job whose
        ``payload.appt_iso`` is parseable and ``< now - grace`` AND the
        job hasn't transitioned since.
        """
        grace_minutes = settings.ALERTS_APPT_PASSED_GRACE_MINUTES
        cutoff = now - timedelta(minutes=grace_minutes)
        # Pull recent appt_set events (last 7 days to bound the scan).
        window_start = now - timedelta(days=7)
        query = (
            select(JobLifecycleEvent)
            .where(
                JobLifecycleEvent.to_status == LifecycleStatus.APPT_SET.value,
                JobLifecycleEvent.created_at >= window_start,
                JobLifecycleEvent.payload["appt_iso"].astext.is_not(None),
            )
            .order_by(JobLifecycleEvent.created_at.desc())
        )
        events = list((await self.db.execute(query)).scalars().all())

        # Group by job_id and keep the most recent event per job.
        latest_by_job: dict[uuid.UUID, JobLifecycleEvent] = {}
        for ev in events:
            if ev.job_id not in latest_by_job:
                latest_by_job[ev.job_id] = ev

        # For each job with a parseable appt_iso, check if a later event
        # exists. If not AND the appt is in the past past the grace,
        # raise an alert.
        already_job_ids = await self._open_alert_job_ids(AlertKind.APPT_TIME_PASSED.value)
        created = 0
        for job_id, ev in latest_by_job.items():
            if job_id in already_job_ids:
                continue
            # Did anything happen after this appt_set?
            later = await self.db.execute(
                select(JobLifecycleEvent.id)
                .where(
                    JobLifecycleEvent.job_id == job_id,
                    JobLifecycleEvent.created_at > ev.created_at,
                )
                .limit(1)
            )
            if later.scalar_one_or_none() is not None:
                continue  # job has progressed past appt
            appt_iso = ev.payload.get("appt_iso")
            appt_dt = _parse_iso8601(appt_iso)
            if appt_dt is None or appt_dt > cutoff:
                continue  # free-text or still in the future within grace
            await alert_repo.create_or_get_open(
                self.db,
                kind=AlertKind.APPT_TIME_PASSED.value,
                job_id=job_id,
                threshold_minutes=grace_minutes,
                payload={"appt_iso": appt_iso, "event_id": str(ev.id)},
                detected_at=now,
            )
            created += 1
        return created, len(already_job_ids)

    async def _scan_closing_missing(self, now: datetime) -> tuple[int, int]:
        """Flag non-terminal jobs with no close after CLOSING_GRACE_MINUTES.

        Uses ``first_message_at`` as the anchor — a job has had this
        long to receive its totals. We exclude jobs that already have a
        closing-pipeline event (``source='closing_chat'``) since those
        are already in flight; the alert fires when the closing is just
        missing.
        """
        threshold_minutes = settings.ALERTS_CLOSING_GRACE_MINUTES
        cutoff = now - timedelta(minutes=threshold_minutes)
        non_terminal = (
            LifecycleStatus.PENDING.value,
            LifecycleStatus.DISPATCHED.value,
            LifecycleStatus.IN_PROGRESS.value,
            LifecycleStatus.APPT_SET.value,
            LifecycleStatus.NEEDS_FOLLOW_UP.value,
        )
        query = (
            select(Job)
            .where(
                Job.lifecycle_status.in_(non_terminal),
                Job.first_message_at < cutoff,
                Job.closed_at.is_(None),
            )
            .order_by(Job.first_message_at.asc())
        )
        candidates = list((await self.db.execute(query)).scalars().all())

        # Exclude jobs with a closing_chat event already in flight.
        already_job_ids = await self._open_alert_job_ids(AlertKind.CLOSING_MISSING.value)
        in_flight_job_ids = await self._jobs_with_closing_event()

        created = 0
        for job in candidates:
            if job.id in already_job_ids or job.id in in_flight_job_ids:
                continue
            await alert_repo.create_or_get_open(
                self.db,
                kind=AlertKind.CLOSING_MISSING.value,
                job_id=job.id,
                threshold_minutes=threshold_minutes,
                payload={
                    "since": job.first_message_at.isoformat(),
                    "lifecycle_status": job.lifecycle_status,
                },
                detected_at=now,
            )
            created += 1
        return created, len(already_job_ids)

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def _open_alert_job_ids(self, kind: str) -> set[uuid.UUID]:
        """Return the set of job_ids that already have an open alert of ``kind``."""
        query = select(Alert.job_id).where(
            and_(
                Alert.kind == kind,
                Alert.resolved_at.is_(None),
                Alert.job_id.is_not(None),
            )
        )
        rows = list((await self.db.execute(query)).scalars().all())
        return {row for row in rows if row is not None}

    async def _jobs_with_closing_event(self) -> set[uuid.UUID]:
        """Jobs that already have a ``closing_chat`` event (in flight)."""
        query = select(JobLifecycleEvent.job_id).where(
            JobLifecycleEvent.source == LifecycleEventSource.CLOSING_CHAT
        )
        rows = list((await self.db.execute(query)).scalars().all())
        return {row for row in rows if row is not None}


def _parse_iso8601(value: object) -> datetime | None:
    """Parse an ISO-8601 string into a datetime; return None on failure.

    The tech reply parser may store ``appt_iso`` as a free-text phrase
    (e.g. "tomorrow 3pm") rather than a parseable timestamp — those
    rows are skipped, not silently misflagged.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        # ``datetime.fromisoformat`` handles most ISO-8601 shapes
        # including the ``Z`` suffix in Python 3.11+.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
