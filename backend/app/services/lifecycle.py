"""LifecycleService — the single gate for all Job lifecycle transitions.

Every status change on a Job flows through ``LifecycleService.transition``.
The service:

1. Validates the transition against the state machine.
2. Inserts a row in ``job_lifecycle_events`` (append-only audit log).
3. Updates ``jobs.lifecycle_status`` + ``lifecycle_status_changed_at``.
4. Auto-resolves stuck-alert rows once the job leaves the offending status.

The service does NOT commit — the caller owns the transaction boundary
so the audit event + job update either both land or both roll back.

The state machine intentionally allows broad transitions from any
non-terminal status because operators frequently need to jump states
(cancel a job from any point, mark as needs_follow_up without going
through in_progress, etc.). The single hard rule is: ``closed`` can
only be entered via the closing pipeline (``source='closing_chat'``).

**Outbound messages are never produced by this service.** The operator
types every customer message natively in WhatsApp / OpenPhone; we
only observe what they did. See ``memory/feedback_no_outbound_automation.md``.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidTransitionError, ValidationError
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.repositories import alert as alert_repo
from app.repositories import job as job_repo
from app.repositories import job_lifecycle_event as lifecycle_event_repo


class LifecycleStatus(StrEnum):
    """The 8 lifecycle states a Job can be in.

    Defined here (rather than on the model) because they are domain
    values used in business logic, not column constraints. The
    ``jobs.lifecycle_status`` column is a plain VARCHAR(20) so adding a
    new value does not require a DDL change.
    """

    PENDING = "pending"
    DISPATCHED = "dispatched"
    # Non-terminal. Set when the assigned technician confirms the dispatch
    # ("ok"/"k"/…) — distinct from ``dispatched`` (sent, awaiting reply) and
    # ``in_progress`` (tech en route / working). See ``tech_reply_parser``.
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    APPT_SET = "appt_set"
    NEEDS_FOLLOW_UP = "needs_follow_up"
    CANCELED = "canceled"
    COMPLETED = "completed"
    CLOSED = "closed"
    # Terminal state reached when the operator declines a job in the
    # source chat (e.g. "pass", "have it", "<zip> pass", or a re-paste of
    # the job with a short note) within the next two operator messages.
    # Rejected jobs are never dispatched, so the alert engine must not
    # flag them as stuck/unclosed — see ``services/reject_detector.py``.
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# State-machine guard
# ---------------------------------------------------------------------------


# Hard rules. Each entry lists which sources may transition INTO the
# key status. ``MANUAL`` and ``AMBIGUOUS_ATTRIBUTION`` may enter most
# non-terminal states (operator override path).
#
# The state machine is intentionally permissive: most transitions are
# allowed from any non-terminal source. The single hard rule is the
# terminal ``closed`` state, which may ONLY come from ``closing_chat``
# — the closing pipeline is the only source of truth for "the totals
# arrived and the job is paid".
_TERMINAL_STATUSES = {
    LifecycleStatus.CLOSED,
    LifecycleStatus.CANCELED,
    LifecycleStatus.REJECTED,
}


def _validate_transition(
    *,
    from_status: str,
    to_status: LifecycleStatus,
    source: str,
) -> None:
    """Raise ``InvalidTransitionError`` if the transition is forbidden.

    Rules:
    - ``to_status='closed'`` requires ``source='closing_chat'`` (no
      manual close — closing must come through the closing pipeline).
    - Manual overrides (``source='manual'``) on ``to_status='canceled'``
      REQUIRE a non-empty operator note. The caller (``transition``)
      validates that separately because the note lives in the event
      payload, not in the function signature.
    - All other transitions are permitted.
    """
    if to_status == LifecycleStatus.CLOSED and source != LifecycleEventSource.CLOSING_CHAT:
        raise InvalidTransitionError(
            message=(
                "Manual close is not allowed. The 'closed' status can only be "
                "set by the closing pipeline when a totals message arrives in "
                "the CLOSING_CHAT_JID WhatsApp group."
            ),
            details={
                "from": from_status,
                "to": to_status.value,
                "source": source,
            },
        )


# ---------------------------------------------------------------------------
# LifecycleService
# ---------------------------------------------------------------------------


class LifecycleService:
    """The single gate for all Job lifecycle transitions.

    Stateless apart from the ``db`` session. Construct one per request
    (``LifecycleService(db)``) or per background task.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def transition(
        self,
        *,
        job: Job,
        to_status: LifecycleStatus | str,
        source: str,
        payload: dict | None = None,
        user_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Run a full lifecycle transition.

        Steps (single transaction, no commit):

        1. Validate the transition (state-machine guard).
        2. Insert ``job_lifecycle_events`` row.
        3. Update ``jobs.lifecycle_status`` + ``lifecycle_status_changed_at``.
        4. Auto-resolve stuck alerts when leaving a terminal state.

        Returns the new ``job_lifecycle_events.id``. Caller commits.

        Raises ``InvalidTransitionError`` if the transition is forbidden.
        Raises ``ValidationError`` if ``source='manual'`` and
        ``to_status='canceled'`` is sent without a non-empty note in the
        payload.
        """
        if isinstance(to_status, str):
            try:
                to_status = LifecycleStatus(to_status)
            except ValueError as err:
                raise ValidationError(
                    message=f"Unknown lifecycle status: {to_status!r}",
                    details={"to_status": to_status},
                ) from err

        payload = dict(payload or {})
        from_status_str = job.lifecycle_status

        if to_status == LifecycleStatus.CANCELED and source == LifecycleEventSource.MANUAL:
            note = (payload.get("note") or "").strip()
            if not note:
                raise ValidationError(
                    message=(
                        "Manual cancellation requires a non-empty 'note' "
                        "explaining why the job was canceled."
                    ),
                    details={"to_status": to_status.value},
                )

        _validate_transition(
            from_status=from_status_str,
            to_status=to_status,
            source=source,
        )

        # Denormalize tech-update timings onto the Job so the /jobs views
        # can show them without a per-row event query. Set on the relevant
        # transition; a parseable value overwrites, free-text is ignored.
        from app.services.timeparse import parse_iso8601

        if to_status == LifecycleStatus.APPT_SET:
            appt_dt = parse_iso8601(payload.get("appt_iso"))
            if appt_dt is not None:
                job.appt_at = appt_dt
        if to_status == LifecycleStatus.NEEDS_FOLLOW_UP:
            follow_up_dt = parse_iso8601(payload.get("follow_up_at"))
            if follow_up_dt is not None:
                job.follow_up_at = follow_up_dt
        reason = payload.get("reason")
        if reason:
            job.last_tech_reason = str(reason)[:30]

        now = datetime.now(UTC)
        event = await lifecycle_event_repo.create_event(
            self.db,
            job_id=job.id,
            source=source,
            from_status=from_status_str,
            to_status=to_status.value,
            payload=payload,
            created_by_user_id=user_id,
            at=now,
        )
        await job_repo.set_lifecycle_status(
            self.db,
            job=job,
            status=to_status.value,
            when=now,
        )

        # A job leaving ``pending`` (dispatched, rejected, canceled, …) has
        # been acted on, so clear any open ``undispatched`` alert. Handled
        # separately from the terminal-state cleanup below because
        # ``dispatched`` is non-terminal.
        if (
            from_status_str == LifecycleStatus.PENDING.value
            and to_status != LifecycleStatus.PENDING
        ):
            await alert_repo.auto_resolve_for_job(
                self.db,
                job_id=job.id,
                kinds=[alert_repo.AlertKind.UNDISPATCHED.value],
            )

        # A job leaving ``needs_follow_up`` means the operator called the
        # customer back (or moved it on), so clear the friendly
        # ``follow_up_due`` reminder. Non-terminal, so handled here.
        if (
            from_status_str == LifecycleStatus.NEEDS_FOLLOW_UP.value
            and to_status != LifecycleStatus.NEEDS_FOLLOW_UP
        ):
            await alert_repo.auto_resolve_for_job(
                self.db,
                job_id=job.id,
                kinds=[alert_repo.AlertKind.FOLLOW_UP_DUE.value],
            )

        # Auto-resolve any stuck alerts once the job leaves the offending
        # status. This keeps the dashboard clean without requiring the
        # operator to manually resolve alerts that have self-cleared.
        if to_status in _TERMINAL_STATUSES:
            await alert_repo.auto_resolve_for_job(
                self.db,
                job_id=job.id,
                kinds=[
                    "stuck_dispatched",
                    "stuck_in_progress",
                    "appt_time_passed",
                    "follow_up_due",
                    "closing_missing",
                    "closing_unfiled",
                ],
            )

        return event.id

    async def current_status(self, job_id: uuid.UUID) -> str | None:
        """Read the latest event for a job and return its ``to_status``.

        Returns ``None`` if no events exist (job is in its initial
        ``pending`` state from the migration backfill).
        """
        latest = await lifecycle_event_repo.latest_for_job(self.db, job_id)
        return None if latest is None else latest.to_status

    async def events_for_job(
        self,
        job_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list:
        """Return the lifecycle events for a job, newest-first."""
        return await lifecycle_event_repo.list_for_job(self.db, job_id, limit=limit)
