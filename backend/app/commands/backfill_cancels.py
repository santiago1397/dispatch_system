"""Replay cancel detection over historical outbound OpenPhone messages.

Operators report a cancellation by re-pasting the job block with a note
appended ("cx canceled the appt because his garage door is working now").
Until ``_EXPLICIT_CANCEL_RE`` landed, the detector only recognised the
*circumstances* of a cancellation ("nobody home", "never answered", "already
has someone") and not the plain statement that the job was canceled, so those
messages were ignored and their jobs stayed ``pending`` forever. The
cancel-vs-reject path itself only shipped 2026-07-24, so anything reported
before that was never evaluated at all.

This command re-runs the *current* detector over historical outbound messages
and applies the transitions that should have happened.

Two deliberate safety rules, both narrower than the live path:

1. **Reference-only.** A message is only acted on when
   ``services/job_reference.py`` pulls a PDL / customer phone / street address
   out of it and ``find_job_by_reference_openphone`` resolves that to a job.
   The live path may fall back to "most recent open job from this
   counterparty"; for a broker carrying 100+ concurrent open jobs that is close
   to a coin flip, and this command writes a *terminal* status. A guess is not
   good enough to do that in bulk.
2. **Pending-only.** ``statuses=("pending",)`` is passed explicitly, so a job
   that has since been dispatched, closed, completed or rejected is never
   touched. Note that ``LifecycleService`` does NOT guard transitions out of a
   terminal state — ``_TERMINAL_STATUSES`` is only consulted for alert
   auto-resolution — so this filter is the thing standing between a bulk replay
   and overwritten closed jobs.

Every skip is counted and reported, so a run that does little says so plainly
rather than looking like a clean sweep.

Dry-run by default. Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-cancels             # preview only
    uv run agents_bots cmd backfill-cancels --apply     # write transitions
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

import click
from sqlalchemy import select

from app.commands import command, info, success, warning
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.db.models.openphone import IncomingMessage
from app.db.session import get_db_context
from app.repositories import job as job_repo
from app.repositories import lifecycle_event_repo
from app.services import reject_detector
from app.services.job_reference import extract_job_reference
from app.services.lifecycle import LifecycleService, LifecycleStatus

logger = logging.getLogger(__name__)


async def _run(apply: bool, limit: int | None) -> None:
    skipped: Counter[str] = Counter()
    planned: list[tuple[str, str, str]] = []  # (job_id, address, note)

    async with get_db_context() as db:
        query = (
            select(IncomingMessage)
            .where(
                IncomingMessage.source == "openphone",
                IncomingMessage.direction == "outgoing",
            )
            .order_by(IncomingMessage.created_at)
        )
        if limit:
            query = query.limit(limit)
        messages = (await db.execute(query)).scalars().all()

    info(f"Scanning {len(messages)} outbound OpenPhone messages...")

    for message in messages:
        body = (message.content or "").strip()
        if not body or message.created_at is None:
            skipped["empty_body"] += 1
            continue

        reference = extract_job_reference(body)
        if not reference:
            # No PDL / phone / address in the body — the live path would fall
            # back to recency here. We refuse to guess. See rule 1 above.
            skipped["no_reference"] += 1
            continue

        matched = False
        for counterparty in message.to_numbers or []:
            async with get_db_context() as db:
                candidate = await job_repo.find_job_by_reference_openphone(
                    db,
                    counterparty=counterparty,
                    before=message.created_at,
                    reference=reference,
                    statuses=("pending",),  # rule 2 — never touch a moved job
                )
                if candidate is None:
                    continue
                job, source_body = candidate
                matched = True

                if not reject_detector.is_cancel_signal(body, source_body):
                    skipped["not_a_cancel"] += 1
                    break

                if message.openphone_id and await lifecycle_event_repo.exists_for_openphone_id(
                    db,
                    source=LifecycleEventSource.OPERATOR_CANCEL.value,
                    openphone_id=message.openphone_id,
                ):
                    skipped["already_applied"] += 1
                    break

                note = body.split("\n")[-1].strip()[:120] or body[:120]
                address = (
                    f"{job.address_street_number or ''} {job.address_street_name or ''}".strip()
                )
                planned.append((str(job.id), address, note))

                if apply:
                    await LifecycleService(db).transition(
                        job=job,
                        to_status=LifecycleStatus.CANCELED,
                        source=LifecycleEventSource.OPERATOR_CANCEL,
                        payload={
                            "counterparty": counterparty,
                            "openphone_id": message.openphone_id,
                            "body_preview": body[:120],
                            "matched_by": "reference",
                            "backfill": True,
                            "note": note,
                        },
                        at=message.created_at,
                    )
                    await db.commit()
                    logger.info(
                        "BACKFILL_CANCEL_APPLIED job_id=%s openphone_id=%s",
                        job.id,
                        message.openphone_id,
                    )
                break

        if not matched:
            skipped["no_pending_match"] += 1

    verb = "Applied" if apply else "Would apply"
    for job_id, address, note in planned:
        click.echo(f"  {job_id}  {address:<32.32}  {note}")

    if planned:
        success(f"{verb} {len(planned)} cancellation(s).")
    else:
        warning("No cancellations matched.")

    info("Skipped: " + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items())) or "nothing"))
    if not apply and planned:
        warning("Dry run — nothing was written. Re-run with --apply to commit.")


@command("backfill-cancels", help="Replay cancel detection over historical outbound messages")
@click.option("--apply", is_flag=True, help="Write the transitions (default: dry run)")
@click.option("--limit", type=int, default=None, help="Only scan the first N messages")
def backfill_cancels(apply: bool, limit: int | None) -> None:
    """Retroactively cancel jobs whose cancellation note was never detected."""
    asyncio.run(_run(apply=apply, limit=limit))
