"""Backfill timestamps mislabeled UTC that were actually Chicago wall-clock time.

Two independent instances of the same bug, both fixed going forward as of
this command's companion code changes (``app.services.timeparse.parse_iso8601``
and ``WhatsappMessageCreate._fix_extension_utc_mislabel``):

1. ``whatsapp_messages.timestamp`` — the Chrome extension reads WhatsApp
   Web's displayed local (Chicago) message time off the DOM and serializes
   it with a bogus ``+00:00``/``Z`` suffix without ever converting it, so
   e.g. a message actually sent at 9:08 AM Chicago is stored as
   ``09:08:00 UTC`` (should be ``14:08:00 UTC``). Every row ingested before
   the extension-side fix ships is affected — there is no reliable way to
   tell a corrected row from an uncorrected one after the fact, so this
   part of the backfill is **not idempotent**: run it exactly once, right
   after the code fix deploys, and only against rows older than the
   deploy. ``--before`` (default: now) bounds the affected window.

2. ``jobs.appt_at`` / ``jobs.follow_up_at`` — denormalized from the latest
   ``appt_set``/``needs_follow_up`` lifecycle event's
   ``payload->>'appt_iso'`` / ``payload->>'follow_up_at'``, which is a
   naive LLM-extracted local-time string (e.g. ``"2026-07-12T09:00:00"``)
   that ``parse_iso8601`` used to assume was UTC instead of Chicago. This
   part recomputes from the stored payload with the fixed parser and only
   applies where the recomputed value differs from the current one, so it
   IS idempotent (safe to re-run).

After part 1 applies, ``jobs.first_message_at`` / ``jobs.closed_at`` /
``job_lifecycle_events.created_at`` are still stale (they were denormalized
from the old, wrong ``whatsapp_messages.timestamp`` values) — re-run the
sibling ``backfill-whatsapp-timestamps`` command afterward to propagate the
correction into those columns.

Read-only by default. Pass ``--apply`` to write. Every row considered is
written to a CSV under ``backfill_output/timezone_offset_backfill/`` for
audit, whether or not ``--apply`` was passed.

Run with::

    uv run agents_bots cmd backfill-timezone-offset                    # dry run
    uv run agents_bots cmd backfill-timezone-offset --apply            # write
    uv run agents_bots cmd backfill-whatsapp-timestamps --apply        # propagate (part 1 only)
"""

from __future__ import annotations

import asyncio
import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import click
from sqlalchemy import text

from app.commands import command, error, info, success, warning
from app.services.timeparse import parse_iso8601

BUSINESS_TZ = ZoneInfo("America/Chicago")

#: Skip corrections smaller than this — avoids churning on sub-minute clock
#: skew that isn't the bug we're fixing.
MIN_DRIFT_SECONDS = 60

DEFAULT_OUTPUT_DIR = Path("backfill_output/timezone_offset_backfill")

_WHATSAPP_MESSAGES_SQL = text("""
    SELECT id AS pk, timestamp AS old_value
    FROM whatsapp_messages
    WHERE timestamp < :before
    ORDER BY timestamp ASC
""")

_APPT_SET_PAYLOAD_SQL = text("""
    SELECT DISTINCT ON (job_id)
        job_id, payload->>'appt_iso' AS raw_value
    FROM job_lifecycle_events
    WHERE to_status = 'appt_set' AND payload->>'appt_iso' IS NOT NULL
    ORDER BY job_id, created_at DESC, id DESC
""")

_FOLLOW_UP_PAYLOAD_SQL = text("""
    SELECT DISTINCT ON (job_id)
        job_id, payload->>'follow_up_at' AS raw_value
    FROM job_lifecycle_events
    WHERE to_status = 'needs_follow_up' AND payload->>'follow_up_at' IS NOT NULL
    ORDER BY job_id, created_at DESC, id DESC
""")

_JOB_APPT_FOLLOWUP_SQL = text("""
    SELECT id AS pk, appt_at, follow_up_at
    FROM jobs
    WHERE id = ANY(:job_ids)
""")

CSV_COLUMNS = ["table", "pk", "job_id", "column", "old_value", "new_value", "drift_hours"]


def _relabel_chicago(dt: datetime) -> datetime:
    """Strip a (wrong) UTC offset and re-localize the same wall-clock digits as Chicago."""
    return dt.replace(tzinfo=BUSINESS_TZ).astimezone(UTC)


async def _collect_whatsapp_messages(db, before: datetime) -> tuple[list[dict[str, Any]], list[tuple]]:
    report: list[dict[str, Any]] = []
    batch: list[tuple] = []
    rows = (await db.execute(_WHATSAPP_MESSAGES_SQL, {"before": before})).all()
    for row in rows:
        new_value = _relabel_chicago(row.old_value)
        drift_h = round((row.old_value - new_value).total_seconds() / 3600.0, 2)
        report.append(
            {
                "table": "whatsapp_messages.timestamp",
                "pk": row.pk,
                "job_id": "",
                "column": "timestamp",
                "old_value": row.old_value.isoformat(),
                "new_value": new_value.isoformat(),
                "drift_hours": drift_h,
            }
        )
        batch.append((row.pk, new_value))
    return report, batch


async def _collect_appt_and_follow_up(db) -> tuple[list[dict[str, Any]], list[tuple], list[tuple]]:
    report: list[dict[str, Any]] = []
    appt_batch: list[tuple] = []
    follow_up_batch: list[tuple] = []

    appt_rows = (await db.execute(_APPT_SET_PAYLOAD_SQL)).all()
    follow_up_rows = (await db.execute(_FOLLOW_UP_PAYLOAD_SQL)).all()

    job_ids = list({r.job_id for r in appt_rows} | {r.job_id for r in follow_up_rows})
    if not job_ids:
        return report, appt_batch, follow_up_batch

    current_rows = (await db.execute(_JOB_APPT_FOLLOWUP_SQL, {"job_ids": job_ids})).all()
    current_appt_by_job = {r.pk: r.appt_at for r in current_rows}
    current_follow_up_by_job = {r.pk: r.follow_up_at for r in current_rows}

    for row in appt_rows:
        new_value = parse_iso8601(row.raw_value)
        if new_value is None:
            continue
        old_value = current_appt_by_job.get(row.job_id)
        if old_value is not None and abs((old_value - new_value).total_seconds()) <= MIN_DRIFT_SECONDS:
            continue
        drift_h = round((old_value - new_value).total_seconds() / 3600.0, 2) if old_value else None
        report.append(
            {
                "table": "jobs.appt_at",
                "pk": row.job_id,
                "job_id": row.job_id,
                "column": "appt_at",
                "old_value": old_value.isoformat() if old_value else "",
                "new_value": new_value.isoformat(),
                "drift_hours": drift_h,
            }
        )
        appt_batch.append((row.job_id, new_value))

    for row in follow_up_rows:
        new_value = parse_iso8601(row.raw_value)
        if new_value is None:
            continue
        old_value = current_follow_up_by_job.get(row.job_id)
        if old_value is not None and abs((old_value - new_value).total_seconds()) <= MIN_DRIFT_SECONDS:
            continue
        drift_h = round((old_value - new_value).total_seconds() / 3600.0, 2) if old_value else None
        report.append(
            {
                "table": "jobs.follow_up_at",
                "pk": row.job_id,
                "job_id": row.job_id,
                "column": "follow_up_at",
                "old_value": old_value.isoformat() if old_value else "",
                "new_value": new_value.isoformat(),
                "drift_hours": drift_h,
            }
        )
        follow_up_batch.append((row.job_id, new_value))

    return report, appt_batch, follow_up_batch


async def _apply(db, wm_batch: list[tuple], appt_batch: list[tuple], follow_up_batch: list[tuple]) -> dict[str, int]:
    counts: dict[str, int] = {}

    wm_stmt = text("UPDATE whatsapp_messages SET timestamp = :new WHERE id = :pk")
    for pk, new_value in wm_batch:
        await db.execute(wm_stmt, {"pk": pk, "new": new_value})
    counts["whatsapp_messages.timestamp"] = len(wm_batch)

    appt_stmt = text("UPDATE jobs SET appt_at = :new WHERE id = :pk")
    for pk, new_value in appt_batch:
        await db.execute(appt_stmt, {"pk": pk, "new": new_value})
    counts["jobs.appt_at"] = len(appt_batch)

    follow_up_stmt = text("UPDATE jobs SET follow_up_at = :new WHERE id = :pk")
    for pk, new_value in follow_up_batch:
        await db.execute(follow_up_stmt, {"pk": pk, "new": new_value})
    counts["jobs.follow_up_at"] = len(follow_up_batch)

    return counts


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


async def _run(output_dir: Path, apply: bool, before: datetime) -> None:
    from app.db.session import get_db_context

    async with get_db_context() as db:
        wm_report, wm_batch = await _collect_whatsapp_messages(db, before)
        appt_report, appt_batch, follow_up_batch = await _collect_appt_and_follow_up(db)
        report = wm_report + appt_report

        if not report:
            success("No corrections needed.")
            return

        by_table: dict[str, int] = {}
        for row in report:
            by_table[row["table"]] = by_table.get(row["table"], 0) + 1

        info(f"Found {len(report)} row(s) to correct:")
        for table_col, count in by_table.items():
            info(f"  {table_col}: {count}")

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        csv_path = output_dir / f"corrections_{stamp}.csv"
        _write_csv(csv_path, report)
        info(f"Wrote full before/after report: {csv_path.resolve()}")

        if not apply:
            warning("Dry run — no changes written. Re-run with --apply to write these corrections.")
            warning(
                "After applying, also re-run `backfill-whatsapp-timestamps --apply` to propagate "
                "the corrected whatsapp_messages.timestamp into first_message_at/closed_at/events."
            )
            return

        counts = await _apply(db, wm_batch, appt_batch, follow_up_batch)
        # get_db_context commits on clean exit.
        success(f"Applied corrections: {counts}")
        info(
            "Now run `uv run agents_bots cmd backfill-whatsapp-timestamps --apply` to propagate "
            "the whatsapp_messages.timestamp correction into jobs/events."
        )


@command(
    "backfill-timezone-offset",
    help="Correct timestamps mislabeled UTC that were actually Chicago wall-clock time",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Where to write the before/after audit CSV.",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Write the corrections. Without this flag, only the audit CSV is produced.",
)
@click.option(
    "--before",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]),
    default=None,
    help=(
        "Only correct whatsapp_messages rows older than this UTC timestamp "
        "(default: now — i.e. every row currently in the table). Use this to "
        "avoid double-correcting rows ingested after the extension-side fix ships."
    ),
)
def backfill_timezone_offset(output_dir: Path, apply: bool, before: datetime | None) -> None:
    """Backfill whatsapp_messages.timestamp and jobs.appt_at/follow_up_at.

    Dry-run by default: prints a summary and writes a full CSV of every row
    that would change (old value, new value, drift in hours) without writing
    anything. Pass --apply to actually run the UPDATEs, in one transaction.
    """
    before = (before.replace(tzinfo=UTC) if before else datetime.now(UTC))
    try:
        asyncio.run(_run(output_dir, apply, before))
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        error(f"Backfill failed: {exc}")
        raise
