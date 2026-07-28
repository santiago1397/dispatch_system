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

1. **Reference first, and recency only when it cannot be wrong.** When
   ``services/job_reference.py`` pulls a PDL / customer phone / street
   address out of the body, ``find_job_by_reference_openphone`` resolves it
   and that is the match.

   Reference-only is not sufficient here, though, and the first version of
   this command was wrong for exactly that reason: the declines it exists
   to catch are *bare*. "only dealer" carries no PDL, no phone and no
   address, so a reference-only rule skipped the very job that motivated
   the command (1263 of 1921 messages skipped as ``no_reference``).

   So a keyless body falls back to ``find_reject_candidate_openphone``
   (already pending-only) under a gate strictly *stronger* than the live
   path's: the reply must be inside the two-outbound-message window AND the
   broker must have posted no other job in the interval. Production takes
   either condition; this requires both. Zero competing jobs is the real
   proof — if no second job arrived between intake and the reply, a decline
   can only be about the one job on the table.
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

        matched = False
        for counterparty in message.to_numbers or []:
            async with get_db_context() as db:
                matched_by = "reference"
                candidate = None
                if reference:
                    candidate = await job_repo.find_job_by_reference_openphone(
                        db,
                        counterparty=counterparty,
                        before=message.created_at,
                        reference=reference,
                        statuses=("pending",),  # rule 2 — never touch a moved job
                    )
                if candidate is None:
                    # Bare decline ("only dealer") — no identity keys to match
                    # on. Already pending-only; the unambiguity gate below is
                    # what makes this safe. See rule 1.
                    matched_by = "recency_unambiguous"
                    candidate = await job_repo.find_reject_candidate_openphone(
                        db, counterparty=counterparty, before=message.created_at
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

                # Rule 4 — the reject window. A reference-matched body names
                # its own job, so it takes the live path's rule (late is fine
                # as long as no other job competes). A keyless body matched by
                # recency must clear BOTH halves: inside the window AND no
                # other job posted in the interval.
                outbound_count = await openphone_repo.count_outbound_messages_to(
                    db,
                    counterparty=counterparty,
                    after=job.first_message_at,
                    until=message.created_at,
                )
                competing = await job_repo.count_newer_jobs_openphone(
                    db,
                    counterparty=counterparty,
                    after=job.first_message_at,
                    until=message.created_at,
                )
                if competing:
                    skipped["too_late_ambiguous"] += 1
                    break
                if matched_by == "recency_unambiguous" and outbound_count > 2:
                    skipped["keyless_outside_window"] += 1
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
                            "matched_by": matched_by,
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
