"""Replay operator-decline detection over recent outbound OpenPhone messages.

Three detection gaps meant a plainly-declined job stayed ``pending``:

1. Every phrase rule in ``is_reject_phrase`` anchors on the decline leading
   the message, so a leading apology hid it — "cant do" matched, "sorry
   cant do" did not.
2. ``_PUNCT_STRIP_RE`` stripped ``'`` but not ``’``. Phone keyboards
   substitute automatically, so "Sorry can’t do" normalized to "can’t" and
   matched nothing.
3. The reject path required the decline to fall within two outbound
   messages of intake. An operator who answered "Lmc" and "k" first blew
   that budget without any second job ever arriving.

All three are fixed; this command applies the transitions that should have
happened. It calls the live ``OpenPhoneService.maybe_reject_job`` rather
than reimplementing the decision, so a replay can never drift from what
production does with the next message.

Safety:

- **Idempotent.** A message that already produced an
  ``operator_reject``/``operator_cancel`` event is skipped, keyed on
  ``payload.openphone_id``.
- **Pending-only**, inherited from ``find_reject_candidate_openphone``,
  which only ever returns a ``pending`` job. A job since dispatched,
  closed or canceled is never touched.
- **Conflict repair.** Where an earlier ``backfill-relay-updates`` run
  attributed *this same message* (same ``openphone_id``) to a *different*
  job, that transition was a mis-attribution: one message cannot be about
  two jobs. The stale job is reverted to the status it held before, and a
  ``manual`` event records the correction rather than deleting history.

Dry runs execute and roll back, so the preview is exactly what ``--apply``
does. Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-operator-rejects           # preview
    uv run agents_bots cmd backfill-operator-rejects --apply   # write
"""
# ruff: noqa: RUF002 - the curly apostrophe is quoted deliberately in the
# docstring above; failing to normalize it is one of the bugs repaired here.

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import select

from app.commands import command, info, success, warning
from app.db.models.job_lifecycle_event import JobLifecycleEvent, LifecycleEventSource
from app.db.models.openphone import IncomingMessage, MessageSource
from app.db.session import get_db_context
from app.repositories import job as job_repo
from app.repositories import job_lifecycle_event as lifecycle_event_repo
from app.services.openphone import OpenPhoneService

logger = logging.getLogger(__name__)

_ALREADY_APPLIED_SOURCES = (
    LifecycleEventSource.OPERATOR_REJECT.value,
    LifecycleEventSource.OPERATOR_CANCEL.value,
)


async def _latest_event(db, openphone_id: str, sources: tuple[str, ...]):
    """The newest lifecycle event this OpenPhone message produced."""
    query = (
        select(JobLifecycleEvent)
        .where(
            JobLifecycleEvent.source.in_(sources),
            JobLifecycleEvent.payload["openphone_id"].astext == openphone_id,
        )
        .order_by(JobLifecycleEvent.created_at.desc())
        .limit(1)
    )
    return (await db.execute(query)).scalars().first()


async def _repair_conflict(db, *, openphone_id: str, applied_job_id, counts: Counter) -> None:
    """Undo a relay transition this same message caused on a different job.

    One message cannot be about two jobs. If the earlier relay backfill
    resolved it by sticky reference to some other job, that was a
    mis-attribution and the job it hit must go back to where it was.
    """
    stale = await _latest_event(db, openphone_id, (LifecycleEventSource.OPERATOR_RELAY.value,))
    if stale is None or stale.job_id == applied_job_id:
        return

    job = await job_repo.get_job_by_id(db, stale.job_id)
    if job is None or job.lifecycle_status != stale.to_status:
        # Something else moved it since; leave it alone rather than
        # stomping a status this message is no longer responsible for.
        counts["conflict_skipped_moved_on"] += 1
        return

    click.echo(
        f"    revert {str(job.id)[:8]}  {stale.to_status} -> {stale.from_status}"
        f"  (same message, now attributed elsewhere)"
    )
    await job_repo.set_lifecycle_status(db, job=job, status=stale.from_status)
    db.add(
        JobLifecycleEvent(
            job_id=job.id,
            from_status=stale.to_status,
            to_status=stale.from_status,
            source=LifecycleEventSource.MANUAL.value,
            payload={
                "reason": "mis_attributed_relay_update",
                "openphone_id": openphone_id,
                "reattributed_to_job_id": str(applied_job_id),
                "reverted_event_id": str(stale.id),
                "backfill": True,
            },
        )
    )
    counts["conflicts_reverted"] += 1


async def _run(*, apply: bool, days: int, limit: int | None) -> None:
    counts: Counter[str] = Counter()
    since = datetime.now(UTC) - timedelta(days=days)

    async with get_db_context() as db:
        query = (
            select(IncomingMessage)
            .where(
                IncomingMessage.source == MessageSource.OPENPHONE.value,
                IncomingMessage.direction == "outgoing",
                IncomingMessage.created_at >= since,
            )
            .order_by(IncomingMessage.created_at.asc())
        )
        if limit:
            query = query.limit(limit)
        messages = list((await db.execute(query)).scalars().all())
        info(f"Scanning {len(messages)} outbound message(s) from the last {days} day(s).")

        svc = OpenPhoneService(db)
        for message in messages:
            body = (message.content or "").strip()
            if not body:
                counts["empty_body"] += 1
                continue

            openphone_id = message.openphone_id or ""
            already_applied = False
            for source in _ALREADY_APPLIED_SOURCES:
                if openphone_id and await lifecycle_event_repo.exists_for_openphone_id(
                    db, source=source, openphone_id=openphone_id
                ):
                    already_applied = True
                    break
            if already_applied:
                counts["already_applied"] += 1
                continue

            if not await svc.maybe_reject_job(message):
                counts["no_signal"] += 1
                continue

            event = await _latest_event(db, openphone_id, _ALREADY_APPLIED_SOURCES)
            if event is None:
                counts["applied_unknown"] += 1
                continue

            counts[f"applied_{event.to_status}"] += 1
            click.echo(
                f"  {str(event.job_id)[:8]}  {message.created_at:%m-%d %H:%M}"
                f"  -> {event.to_status:<9}  {body[:46]!r}"
            )
            if openphone_id:
                await _repair_conflict(
                    db, openphone_id=openphone_id, applied_job_id=event.job_id, counts=counts
                )

        await db.flush()
        if apply:
            await db.commit()
        else:
            # get_db_context commits on a clean exit — undo first.
            await db.rollback()

    verb = "Applied" if apply else "Would apply"
    applied = {k: v for k, v in counts.items() if k.startswith(("applied", "conflict"))}
    skipped = {k: v for k, v in counts.items() if k not in applied}
    success(f"{verb}: " + (", ".join(f"{k}={v}" for k, v in sorted(applied.items())) or "nothing"))
    info("Skipped: " + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items())) or "nothing"))
    if not apply:
        warning("Dry run — nothing was written. Re-run with --apply to commit.")


@command(
    "backfill-operator-rejects", help="Replay operator-decline detection over outbound messages"
)
@click.option("--apply", is_flag=True, help="Write the transitions (default: dry run)")
@click.option("--days", type=int, default=30, help="How far back to scan (default: 30)")
@click.option("--limit", type=int, default=None, help="Only scan the first N messages")
def backfill_operator_rejects(apply: bool, days: int, limit: int | None) -> None:
    """Retroactively reject jobs whose decline the detector used to miss."""
    asyncio.run(_run(apply=apply, days=days, limit=limit))
