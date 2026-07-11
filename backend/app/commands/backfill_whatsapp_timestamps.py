"""Backfill 2026 WhatsApp-sourced timestamps that were stamped with processing
time instead of the message's real send time.

Before the ``at``-threading fix in ``LifecycleService.transition`` /
``JobClassificationService`` (see ``_message_timestamp`` in
``app/services/classification.py``), every WhatsApp-originated Job and
lifecycle event was stamped with ``datetime.now(UTC)`` — i.e. whenever the
extension's batch scrape happened to be processed, not when the WhatsApp
message was actually sent. Since the extension re-sends full chat history on
every open, a backlog scrape could stamp a job from weeks ago with today's
date.

This command re-derives the real send time from ``whatsapp_messages.timestamp``
(the DOM-scraped time, joined via the stable ``(chat_jid, wa_message_id)`` key
that ``incoming_messages.raw_payload`` / lifecycle-event payloads already
carry) and corrects four columns:

1. ``jobs.first_message_at`` — from the earliest WhatsApp-sourced
   ``DispatchJob`` for the job.
2. ``jobs.closed_at`` — from the closing message that set it.
3. ``job_lifecycle_events.created_at`` — for ``closing_chat`` events (joined
   via ``payload->>'dispatch_job_id'``) and ``closing_signal`` events whose
   ``payload->>'channel' = 'whatsapp'`` (joined via
   ``payload->>'chat_jid'``/``wa_message_id``). OpenPhone-sourced events are
   left untouched — OpenPhone's ``created_at`` was already close to
   real-time.
4. ``jobs.lifecycle_status_changed_at`` — only for jobs whose current latest
   lifecycle event is one of the corrected events in (3), so the denormalized
   field stays consistent with the audit log.

Read-only by default. Pass ``--apply`` to write. Every row considered is
written to a CSV under ``backfill_output/whatsapp_timestamp_backfill/`` for
audit, whether or not ``--apply`` was passed.

Run with::

    uv run agents_bots cmd backfill-whatsapp-timestamps              # dry run
    uv run agents_bots cmd backfill-whatsapp-timestamps --apply      # write
"""

from __future__ import annotations

import asyncio
import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from sqlalchemy import text

from app.commands import command, error, info, success, warning

#: Skip corrections smaller than this — avoids churning on sub-minute clock
#: skew that isn't the bug we're fixing.
MIN_DRIFT_SECONDS = 60

DEFAULT_OUTPUT_DIR = Path("backfill_output/whatsapp_timestamp_backfill")

_FIRST_MESSAGE_AT_SQL = text(f"""
    WITH first_dj AS (
        SELECT DISTINCT ON (dj.job_id)
            dj.job_id,
            im.raw_payload->>'chat_jid' AS chat_jid,
            im.raw_payload->>'wa_message_id' AS wa_message_id,
            im.source
        FROM dispatch_jobs dj
        JOIN incoming_messages im ON im.id = dj.incoming_message_id
        WHERE dj.job_id IS NOT NULL
        ORDER BY dj.job_id, dj.created_at ASC
    )
    SELECT j.id AS pk, j.first_message_at AS old_value, wm.timestamp AS new_value
    FROM first_dj fd
    JOIN jobs j ON j.id = fd.job_id
    JOIN whatsapp_messages wm
        ON wm.chat_jid = fd.chat_jid AND wm.wa_message_id = fd.wa_message_id
    WHERE fd.source = 'whatsapp'
      AND abs(extract(epoch FROM (j.first_message_at - wm.timestamp))) > {MIN_DRIFT_SECONDS}
""")

_CLOSED_AT_SQL = text(f"""
    SELECT j.id AS pk, j.closed_at AS old_value, wm.timestamp AS new_value
    FROM jobs j
    JOIN dispatch_jobs dj ON dj.id = j.closed_from_dispatch_job_id
    JOIN incoming_messages im ON im.id = dj.incoming_message_id
    JOIN whatsapp_messages wm
        ON wm.chat_jid = im.raw_payload->>'chat_jid'
       AND wm.wa_message_id = im.raw_payload->>'wa_message_id'
    WHERE j.closed_at IS NOT NULL
      AND abs(extract(epoch FROM (j.closed_at - wm.timestamp))) > {MIN_DRIFT_SECONDS}
""")

_CLOSING_CHAT_EVENTS_SQL = text(f"""
    SELECT jle.id AS pk, jle.job_id AS job_id, jle.created_at AS old_value, wm.timestamp AS new_value
    FROM job_lifecycle_events jle
    JOIN dispatch_jobs dj ON dj.id = (jle.payload->>'dispatch_job_id')::uuid
    JOIN incoming_messages im ON im.id = dj.incoming_message_id
    JOIN whatsapp_messages wm
        ON wm.chat_jid = im.raw_payload->>'chat_jid'
       AND wm.wa_message_id = im.raw_payload->>'wa_message_id'
    WHERE jle.source = 'closing_chat'
      AND abs(extract(epoch FROM (jle.created_at - wm.timestamp))) > {MIN_DRIFT_SECONDS}
""")

_CLOSING_SIGNAL_EVENTS_SQL = text(f"""
    SELECT jle.id AS pk, jle.job_id AS job_id, jle.created_at AS old_value, wm.timestamp AS new_value
    FROM job_lifecycle_events jle
    JOIN whatsapp_messages wm
        ON wm.chat_jid = jle.payload->>'chat_jid'
       AND wm.wa_message_id = jle.payload->>'wa_message_id'
    WHERE jle.source = 'closing_signal'
      AND jle.payload->>'channel' = 'whatsapp'
      AND abs(extract(epoch FROM (jle.created_at - wm.timestamp))) > {MIN_DRIFT_SECONDS}
""")

_LATEST_EVENT_PER_JOB_SQL = text("""
    SELECT DISTINCT ON (job_id) job_id, id AS latest_event_id
    FROM job_lifecycle_events
    WHERE job_id = ANY(:job_ids)
    ORDER BY job_id, created_at DESC, id DESC
""")

_JOB_STATUS_CHANGED_AT_SQL = text("""
    SELECT id AS pk, lifecycle_status_changed_at AS old_value
    FROM jobs
    WHERE id = ANY(:job_ids)
""")

CSV_COLUMNS = ["table", "pk", "job_id", "column", "old_value", "new_value", "drift_hours"]


async def _collect_corrections(db) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    """Run all read-only queries and return (report_rows, apply_batches).

    ``apply_batches`` maps table name -> list of (pk, new_value) tuples, ready
    to feed straight into per-row UPDATE statements.
    """
    report: list[dict[str, Any]] = []
    apply_batches: dict[str, list[Any]] = {
        "jobs.first_message_at": [],
        "jobs.closed_at": [],
        "job_lifecycle_events.created_at": [],
        "jobs.lifecycle_status_changed_at": [],
    }

    def _add(table_col: str, pk: Any, job_id: Any, old: datetime | None, new: datetime) -> None:
        drift_h = (
            round((old - new).total_seconds() / 3600.0, 2) if old is not None else None
        )
        report.append(
            {
                "table": table_col,
                "pk": pk,
                "job_id": job_id,
                "column": table_col.split(".")[-1],
                "old_value": old.isoformat() if old else "",
                "new_value": new.isoformat(),
                "drift_hours": drift_h,
            }
        )
        apply_batches[table_col].append((pk, new))

    first_msg_rows = (await db.execute(_FIRST_MESSAGE_AT_SQL)).all()
    for row in first_msg_rows:
        _add("jobs.first_message_at", row.pk, row.pk, row.old_value, row.new_value)

    closed_rows = (await db.execute(_CLOSED_AT_SQL)).all()
    for row in closed_rows:
        _add("jobs.closed_at", row.pk, row.pk, row.old_value, row.new_value)

    event_rows = list((await db.execute(_CLOSING_CHAT_EVENTS_SQL)).all())
    event_rows += list((await db.execute(_CLOSING_SIGNAL_EVENTS_SQL)).all())
    corrected_event_new_by_id: dict[Any, datetime] = {}
    for row in event_rows:
        _add("job_lifecycle_events.created_at", row.pk, row.job_id, row.old_value, row.new_value)
        corrected_event_new_by_id[row.pk] = row.new_value

    # jobs.lifecycle_status_changed_at: only for jobs whose truly-latest
    # event (by current created_at, pre-correction) is one we just corrected.
    job_ids = list({row.job_id for row in event_rows})
    if job_ids:
        latest_rows = (
            await db.execute(_LATEST_EVENT_PER_JOB_SQL, {"job_ids": job_ids})
        ).all()
        latest_event_id_by_job = {r.job_id: r.latest_event_id for r in latest_rows}
        eligible_job_ids = [
            jid
            for jid, latest_id in latest_event_id_by_job.items()
            if latest_id in corrected_event_new_by_id
        ]
        if eligible_job_ids:
            old_status_rows = (
                await db.execute(_JOB_STATUS_CHANGED_AT_SQL, {"job_ids": eligible_job_ids})
            ).all()
            old_by_job = {r.pk: r.old_value for r in old_status_rows}
            for jid in eligible_job_ids:
                new_value = corrected_event_new_by_id[latest_event_id_by_job[jid]]
                old_value = old_by_job.get(jid)
                if old_value is not None and abs((old_value - new_value).total_seconds()) <= MIN_DRIFT_SECONDS:
                    continue
                _add("jobs.lifecycle_status_changed_at", jid, jid, old_value, new_value)

    return report, apply_batches


async def _apply_corrections(db, apply_batches: dict[str, list[Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    updates = {
        "jobs.first_message_at": text("UPDATE jobs SET first_message_at = :new WHERE id = :pk"),
        "jobs.closed_at": text("UPDATE jobs SET closed_at = :new WHERE id = :pk"),
        "job_lifecycle_events.created_at": text(
            "UPDATE job_lifecycle_events SET created_at = :new WHERE id = :pk"
        ),
        "jobs.lifecycle_status_changed_at": text(
            "UPDATE jobs SET lifecycle_status_changed_at = :new WHERE id = :pk"
        ),
    }
    for table_col, rows in apply_batches.items():
        stmt = updates[table_col]
        for pk, new_value in rows:
            await db.execute(stmt, {"pk": pk, "new": new_value})
        counts[table_col] = len(rows)
    return counts


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


async def _run(output_dir: Path, apply: bool) -> None:
    from app.db.session import get_db_context

    async with get_db_context() as db:
        report, apply_batches = await _collect_corrections(db)

        if not report:
            success("No corrections needed — all WhatsApp timestamps already match.")
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
            return

        counts = await _apply_corrections(db, apply_batches)
        # get_db_context commits on clean exit.
        success(f"Applied corrections: {counts}")


@command(
    "backfill-whatsapp-timestamps",
    help="Correct WhatsApp Job/lifecycle timestamps stamped with processing time instead of send time",
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
def backfill_whatsapp_timestamps(output_dir: Path, apply: bool) -> None:
    """Backfill jobs/job_lifecycle_events timestamps for WhatsApp-sourced rows.

    Dry-run by default: prints a summary and writes a full CSV of every row
    that would change (old value, new value, drift in hours) without writing
    anything. Pass --apply to actually run the UPDATEs, in one transaction.
    """
    try:
        asyncio.run(_run(output_dir, apply))
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        error(f"Backfill failed: {exc}")
        raise
