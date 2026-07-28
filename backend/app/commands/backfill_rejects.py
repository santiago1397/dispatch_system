"""Replay reject detection over historical outbound OpenPhone messages.

The twin of ``backfill_cancels`` for the other terminal status. An operator
declines a job in the source chat and the job should go to ``rejected``;
when the detector had no word for the decline, nothing happened and the job
stayed ``pending`` forever.

The motivating gap: ``reject_detector`` carried no capability-decline
vocabulary, so "only dealer" — locksmith shorthand for "this vehicle needs a
dealer-supplied key, we cannot do it" — matched no phrase rule, and at 11
characters could never reach the re-paste path. Job ``0964f020`` (Always
24/7, PDL HTE27, Melrose Park, 2023 Ford Transit) sat at ``pending`` from
2026-07-18 with zero lifecycle events despite the operator declining it 39
seconds after intake.

This command re-runs the *current* detector over historical outbound
messages and applies the transitions that should have happened.

Safety rules, each one at least as strict as the live path:

1. **Reference-only.** Acted on only when ``services/job_reference.py``
   pulls a PDL / customer phone / street address out of the body AND
   ``find_job_by_reference_openphone`` resolves it. The live path may fall
   back to "most recent open job from this counterparty"; this writes a
   *terminal* status in bulk, and a broker can hold 100+ concurrent open
   jobs, so a guess is not good enough.
2. **Pending-only.** ``statuses=("pending",)`` — a job that has since been
   dispatched, closed, completed or rejected is never touched. Note that
   ``LifecycleService`` does NOT guard transitions out of a terminal state
   (``_TERMINAL_STATUSES`` is only consulted for alert auto-resolution), so
   this filter is the thing standing between a bulk replay and overwritten
   closed jobs.
3. **Cancels yield.** A body that reads as a cancel is left to
   ``backfill-cancels``, mirroring the live ordering where
   ``is_cancel_signal`` is checked before ``is_reject_signal``. A
   cancellation carries more information than a bare decline.
4. **The reject window still applies.** Unlike a cancel — which legitimately
   arrives hours and many messages after intake — a decline is something an
   operator does immediately, so a late "pass" is more likely about a
   different job. The live two-outbound-message cutoff is reproduced here,
   including its escape hatch: if the broker posted no *other* job in the
   interval, the decline can only be about this one however many chat
   messages intervened.

Every skip is counted and reported, so a run that does little says so
plainly rather than looking like a clean sweep.

Dry-run by default. Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-rejects             # preview only
    uv run agents_bots cmd backfill-rejects --apply     # write transitions
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
from app.repositories import openphone as openphone_repo
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

                # Rule 3 — a cancel is backfilled by its own command.
                if reject_detector.is_cancel_signal(body, source_body):
                    skipped["is_a_cancel"] += 1
                    break

                if not reject_detector.is_reject_signal(body, source_body):
                    skipped["not_a_reject"] += 1
                    break

                # Rule 4 — the live two-outbound-message window, with the
                # same "no competing newer job" escape hatch.
                outbound_count = await openphone_repo.count_outbound_messages_to(
                    db,
                    counterparty=counterparty,
                    after=job.first_message_at,
                    until=message.created_at,
                )
                if outbound_count > 2:
                    competing = await job_repo.count_newer_jobs_openphone(
                        db,
                        counterparty=counterparty,
                        after=job.first_message_at,
                        until=message.created_at,
                    )
                    if competing:
                        skipped["too_late_ambiguous"] += 1
                        break

                if message.openphone_id and await lifecycle_event_repo.exists_for_openphone_id(
                    db,
                    source=LifecycleEventSource.OPERATOR_REJECT.value,
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
                        to_status=LifecycleStatus.REJECTED,
                        source=LifecycleEventSource.OPERATOR_REJECT,
                        payload={
                            "counterparty": counterparty,
                            "openphone_id": message.openphone_id,
                            "body_preview": body[:120],
                            "operator_msg_index": outbound_count,
                            "matched_by": "reference",
                            "backfill": True,
                        },
                        at=message.created_at,
                    )
                    await db.commit()
                    logger.info(
                        "BACKFILL_REJECT_APPLIED job_id=%s openphone_id=%s",
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
        success(f"{verb} {len(planned)} rejection(s).")
    else:
        warning("No rejections matched.")

    info("Skipped: " + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items())) or "nothing"))
    if not apply and planned:
        warning("Dry run — nothing was written. Re-run with --apply to commit.")


@command("backfill-rejects", help="Replay reject detection over historical outbound messages")
@click.option("--apply", is_flag=True, help="Write the transitions (default: dry run)")
@click.option("--limit", type=int, default=None, help="Only scan the first N messages")
def backfill_rejects(apply: bool, limit: int | None) -> None:
    """Retroactively reject jobs whose decline was never detected."""
    asyncio.run(_run(apply=apply, limit=limit))
