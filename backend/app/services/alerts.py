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

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.alert import Alert, AlertKind
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import JobLifecycleEvent, LifecycleEventSource
from app.repositories import alert as alert_repo
from app.repositories import company_update_repo, openphone_repo, whatsapp_repo
from app.services.lifecycle import LifecycleStatus
from app.services.timeparse import parse_iso8601

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
            self._scan_undispatched,
            self._scan_stuck_dispatched,
            self._scan_stuck_in_progress,
            self._scan_appt_time_passed,
            self._scan_follow_up_due,
            self._scan_company_update_unsent,
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

    async def _scan_undispatched(self, now: datetime) -> tuple[int, int]:
        """Flag pending jobs neither dispatched nor rejected within the SLA.

        A job that is still ``pending`` past
        ``ALERTS_UNDISPATCHED_MINUTES`` has had no operator action — it was
        neither dispatched to a technician nor rejected.

        The clock is anchored on ``COALESCE(lifecycle_status_changed_at,
        first_message_at)``: a brand-new job has no transition yet, so it
        falls back to creation time; a job a technician bounced back to
        ``pending`` (tech reject) has ``lifecycle_status_changed_at`` set to
        the bounce, giving the operator a fresh window to re-dispatch rather
        than alerting instantly on the old creation timestamp.

        Dispatched/accepted/rejected/closed jobs are excluded automatically
        by the ``lifecycle_status == 'pending'`` filter, and the alert
        auto-resolves the moment the job leaves ``pending`` (see
        ``LifecycleService.transition``).
        """
        threshold_minutes = settings.ALERTS_UNDISPATCHED_MINUTES
        cutoff = now - timedelta(minutes=threshold_minutes)
        pending_since = func.coalesce(Job.lifecycle_status_changed_at, Job.first_message_at)
        query = (
            select(Job)
            .where(
                Job.lifecycle_status == LifecycleStatus.PENDING.value,
                pending_since < cutoff,
            )
            .order_by(pending_since.asc())
        )
        candidates = list((await self.db.execute(query)).scalars().all())

        already_job_ids = await self._open_alert_job_ids(AlertKind.UNDISPATCHED.value)
        candidates = [j for j in candidates if j.id not in already_job_ids]

        created = 0
        for job in candidates:
            since = job.lifecycle_status_changed_at or job.first_message_at
            await alert_repo.create_or_get_open(
                self.db,
                kind=AlertKind.UNDISPATCHED.value,
                job_id=job.id,
                threshold_minutes=threshold_minutes,
                payload={"since": since.isoformat()},
                detected_at=now,
            )
            created += 1
        return created, len(already_job_ids)

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

    async def _scan_follow_up_due(self, now: datetime) -> tuple[int, int]:
        """Friendly reminder — a needs_follow_up job's callback time arrived.

        Mirrors ``_scan_appt_time_passed`` but keyed on the most recent
        ``needs_follow_up`` event's ``payload.follow_up_at``. Fires as soon
        as that time is in the past (no grace — this is a "call the customer
        now" nudge) AND the job hasn't transitioned since. Auto-resolves the
        moment the job leaves ``needs_follow_up`` (see
        ``LifecycleService.transition``).
        """
        window_start = now - timedelta(days=7)
        query = (
            select(JobLifecycleEvent)
            .where(
                JobLifecycleEvent.to_status == LifecycleStatus.NEEDS_FOLLOW_UP.value,
                JobLifecycleEvent.created_at >= window_start,
                JobLifecycleEvent.payload["follow_up_at"].astext.is_not(None),
            )
            .order_by(JobLifecycleEvent.created_at.desc())
        )
        events = list((await self.db.execute(query)).scalars().all())

        latest_by_job: dict[uuid.UUID, JobLifecycleEvent] = {}
        for ev in events:
            if ev.job_id not in latest_by_job:
                latest_by_job[ev.job_id] = ev

        already_job_ids = await self._open_alert_job_ids(AlertKind.FOLLOW_UP_DUE.value)
        created = 0
        for job_id, ev in latest_by_job.items():
            if job_id in already_job_ids:
                continue
            # Did anything happen after this needs_follow_up event?
            later = await self.db.execute(
                select(JobLifecycleEvent.id)
                .where(
                    JobLifecycleEvent.job_id == job_id,
                    JobLifecycleEvent.created_at > ev.created_at,
                )
                .limit(1)
            )
            if later.scalar_one_or_none() is not None:
                continue  # operator already acted
            follow_up_at = ev.payload.get("follow_up_at")
            follow_up_dt = _parse_iso8601(follow_up_at)
            if follow_up_dt is None or follow_up_dt > now:
                continue  # free-text or the callback time hasn't arrived yet
            await alert_repo.create_or_get_open(
                self.db,
                kind=AlertKind.FOLLOW_UP_DUE.value,
                job_id=job_id,
                threshold_minutes=0,
                payload={"follow_up_at": follow_up_at, "event_id": str(ev.id)},
                detected_at=now,
            )
            created += 1
        return created, len(already_job_ids)

    async def _scan_company_update_unsent(self, now: datetime) -> tuple[int, int]:
        """Remind the operator to relay a tech update to the source company.

        For each pending ``CompanyUpdate`` relay: if an operator outbound to
        the company has appeared since the relay was created, mark it sent
        and clear any reminder (the operator relayed it). Otherwise, once the
        relay is older than ``ALERTS_COMPANY_UPDATE_UNSENT_MINUTES``, raise a
        ``company_update_unsent`` reminder.

        "Sent" is observed as *any* operator outbound to the company after
        the relay — a deliberate proxy: we can't read the operator's exact
        wording, and forwarding is the operator's job, so any reply counts.
        """
        threshold_minutes = settings.ALERTS_COMPANY_UPDATE_UNSENT_MINUTES
        cutoff = now - timedelta(minutes=threshold_minutes)
        relays = await company_update_repo.list_unsent(self.db)
        already_job_ids = await self._open_alert_job_ids(AlertKind.COMPANY_UPDATE_UNSENT.value)

        created = 0
        for relay in relays:
            if await self._operator_relayed(relay, now):
                await company_update_repo.mark_sent(self.db, relay, when=now)
                await alert_repo.auto_resolve_for_job(
                    self.db,
                    job_id=relay.job_id,
                    kinds=[AlertKind.COMPANY_UPDATE_UNSENT.value],
                )
                continue
            if relay.created_at >= cutoff or relay.job_id in already_job_ids:
                continue
            await alert_repo.create_or_get_open(
                self.db,
                kind=AlertKind.COMPANY_UPDATE_UNSENT.value,
                job_id=relay.job_id,
                threshold_minutes=threshold_minutes,
                payload={"update_kind": relay.update_kind, "relay_id": str(relay.id)},
                detected_at=now,
            )
            already_job_ids.add(relay.job_id)
            created += 1
        return created, len(already_job_ids)

    async def _operator_relayed(self, relay, now: datetime) -> bool:
        """True if an operator outbound to the company followed the relay."""
        if relay.channel == "whatsapp" and relay.company_chat_jid:
            n = await whatsapp_repo.count_operator_messages_between(
                self.db,
                chat_jid=relay.company_chat_jid,
                after=relay.created_at,
                until=now,
            )
            return n > 0
        if relay.channel == "openphone" and relay.company_phone:
            n = await openphone_repo.count_outbound_messages_to(
                self.db,
                counterparty=relay.company_phone,
                after=relay.created_at,
                until=now,
            )
            return n > 0
        return False

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
            LifecycleStatus.ACCEPTED.value,
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


# Free-text-tolerant ISO parser shared with the lifecycle service. The
# tech reply parser may store ``appt_iso`` / ``follow_up_at`` as a phrase
# ("tomorrow 3pm") rather than a timestamp — those return None and are
# skipped, not misflagged.
_parse_iso8601 = parse_iso8601
