"""Replay the widened relay-intent parser over historical outbound messages.

``services/company_relay_parser.py`` used to recognise exactly one intent
(``no_answer_follow_up``); every other status update an operator relayed to
a broker in free text resolved to ``none`` and was discarded. The clearest
casualty was the plain cancellation — "Cx answered now, said already got
help" — which no code path could express: the regex detector
(``reject_detector.is_cancel_signal``) only fires on a re-paste of the full
job block or a bare "DNS" token, and the LLM had no ``canceled`` code to
return. Those jobs sat ``pending`` until ``closing_missing`` fired.

At the time this command was written, 205 of the 208 pending OpenPhone jobs
had later operator→broker messages that had never been evaluated.

This command re-runs the *current* parser over historical outbound messages
and applies the transitions that should have happened.

It deliberately differs from the live path in two ways:

1. **Attribution before intent.** The live path runs the LLM first, then
   attributes, so it can raise ``unattributed_update`` for an actionable
   update it cannot place. Here the order is reversed: resolve the job
   first and only spend an LLM call when one resolves. Replaying thousands
   of historical messages otherwise costs thousands of calls, the vast
   majority on chatter belonging to jobs that have long since closed.
2. **No alerts.** An unattributable historical update is counted and
   reported, never turned into an alert row. Backfilling hundreds of alerts
   for messages weeks old would bury the live dashboard.

Everything else — the sticky-reference window, the trivial-ack pre-filter,
the intent set, and the ``LifecycleService`` guard that forbids an
``operator_relay`` source from touching a settled job — is shared with the
live path rather than reimplemented, so a replay cannot diverge from what
the pipeline would do today.

Idempotent: a message whose ``openphone_id`` already produced an
``operator_relay`` event is skipped, so re-running is safe.

Dry-run by default. Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-relay-updates             # preview only
    uv run agents_bots cmd backfill-relay-updates --apply     # write transitions
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import select

from app.commands import command, info, success, warning
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.db.models.openphone import IncomingMessage
from app.db.session import get_db_context
from app.repositories import lifecycle_event_repo
from app.services.company_relay_parser import (
    _INTENT_TO_STATUS,
    _extract_intent,
    _resolve_job,
    should_parse,
)
from app.services.lifecycle import LifecycleService, LifecycleStatus

logger = logging.getLogger(__name__)


async def _run(apply: bool, days: int, limit: int | None) -> None:
    skipped: Counter[str] = Counter()
    applied: Counter[str] = Counter()
    planned: list[tuple[str, str, str, str]] = []  # (job_id, address, status, note)

    since = datetime.now(UTC) - timedelta(days=days)

    async with get_db_context() as db:
        query = (
            select(IncomingMessage)
            .where(
                IncomingMessage.source == "openphone",
                IncomingMessage.direction == "outgoing",
                IncomingMessage.created_at >= since,
            )
            # Chronological: a job can legitimately walk
            # pending → needs_follow_up → canceled across consecutive
            # messages, and replaying out of order would apply the final
            # state first and then refuse the rest.
            .order_by(IncomingMessage.created_at)
        )
        if limit:
            query = query.limit(limit)
        messages = (await db.execute(query)).scalars().all()

    info(f"Scanning {len(messages)} outbound OpenPhone messages from the last {days} days...")

    for message in messages:
        body = (message.content or "").strip()
        if not body or message.created_at is None:
            skipped["empty_body"] += 1
            continue
        if not should_parse(body):
            skipped["trivial_ack"] += 1
            continue

        matched = False
        for counterparty in message.to_numbers or []:
            async with get_db_context() as db:
                resolved = await _resolve_job(
                    db,
                    counterparty=counterparty,
                    body=body,
                    reply_at=message.created_at,
                )
                if resolved is None:
                    continue
                job, matched_by = resolved
                matched = True

                if message.openphone_id and await lifecycle_event_repo.exists_for_openphone_id(
                    db,
                    source=LifecycleEventSource.OPERATOR_RELAY.value,
                    openphone_id=message.openphone_id,
                ):
                    skipped["already_applied"] += 1
                    break

                try:
                    intent = await _extract_intent(db, body)
                except Exception:
                    logger.exception(
                        "BACKFILL_RELAY_LLM_FAILED openphone_id=%s", message.openphone_id
                    )
                    skipped["llm_failed"] += 1
                    break

                if intent.intent == "none":
                    skipped["intent_none"] += 1
                    break

                to_status = _INTENT_TO_STATUS[intent.intent]
                address = (
                    f"{job.address_street_number or ''} {job.address_street_name or ''}".strip()
                )
                note = (intent.notes or body.split("\n")[-1].strip() or body)[:80]
                planned.append((str(job.id), address, to_status.value, note))
                applied[to_status.value] += 1

                if apply:
                    payload = {
                        "counterparty": counterparty,
                        "openphone_id": message.openphone_id,
                        "body_preview": body[:120],
                        "matched_by": matched_by,
                        "intent": intent.intent,
                        "backfill": True,
                    }
                    if intent.follow_up_at:
                        payload["follow_up_at"] = intent.follow_up_at
                    if intent.appt_iso:
                        payload["appt_iso"] = intent.appt_iso
                    if intent.reason:
                        payload["reason"] = intent.reason
                    if intent.notes:
                        payload["notes"] = intent.notes
                    if to_status == LifecycleStatus.CANCELED:
                        payload["note"] = note

                    try:
                        await LifecycleService(db).transition(
                            job=job,
                            to_status=to_status,
                            source=LifecycleEventSource.OPERATOR_RELAY,
                            payload=payload,
                            at=message.created_at,
                        )
                        await db.commit()
                        logger.info(
                            "BACKFILL_RELAY_APPLIED job_id=%s openphone_id=%s status=%s",
                            job.id,
                            message.openphone_id,
                            to_status.value,
                        )
                    except Exception:
                        # Job already settled — the lifecycle guard refused.
                        await db.rollback()
                        skipped["refused_settled"] += 1
                        applied[to_status.value] -= 1
                        planned.pop()
                break

        if not matched:
            skipped["unattributed"] += 1

    verb = "Applied" if apply else "Would apply"
    for job_id, address, status, note in planned:
        click.echo(f"  {job_id}  {address:<30.30}  {status:<16}  {note}")

    if planned:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(applied.items()) if v)
        success(f"{verb} {len(planned)} transition(s): {breakdown}")
    else:
        warning("No relay updates matched.")

    info("Skipped: " + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items())) or "nothing"))
    if not apply and planned:
        warning("Dry run — nothing was written. Re-run with --apply to commit.")


@command(
    "backfill-relay-updates",
    help="Replay the relay-intent parser over historical outbound messages",
)
@click.option("--apply", is_flag=True, help="Write the transitions (default: dry run)")
@click.option("--days", type=int, default=30, help="Only scan messages from the last N days")
@click.option("--limit", type=int, default=None, help="Only scan the first N messages")
def backfill_relay_updates(apply: bool, days: int, limit: int | None) -> None:
    """Retroactively apply status updates the old two-intent parser dropped."""
    asyncio.run(_run(apply=apply, days=days, limit=limit))
