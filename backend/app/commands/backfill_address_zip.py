"""Re-parse every job's address with the fixed normalizer.

Three parsing bugs, all fixed in ``app/services/address_normalizer.py``,
left wrong components on rows already in the database:

1. A five-digit house number was read as the ZIP ("12650 Wisteria Ct,
   Palos Park, IL, 60464" -> ``address_zip='12650'``). 85 of 649 jobs.
2. A spelled-out state ("Chicago, Illinois 60622") never matched, leaving
   ``address_state`` and ``address_city`` NULL. 67 of 649 jobs.
3. Without commas the street name swallowed the city and state
   ("west peterson ave chicago illinois"), and the alphabetic-only
   tokenizer dropped the digits from numbered streets, collapsing "1st
   St", "10th St" and "17th St" onto the same key.

Bug 3 matters most: ``address_street_name`` is a dedup key. Until these
rows are re-parsed they cannot match a correctly-parsed repeat of the same
address, and distinct numbered streets can falsely match each other. Run
this soon after the parser fix deploys so historical rows and new rows
share one normalization.

Idempotent: it recomputes from the raw ``dispatch_jobs.address`` text
rather than mutating in place, and writes only where the recomputed value
differs. Safe to re-run — a second run finds nothing.

Read-only by default. Pass ``--apply`` to write. Every job considered is
written to a CSV under ``backfill_output/address_zip_backfill/`` for
audit, whether or not ``--apply`` was passed.

Run with::

    uv run agents_bots cmd backfill-address-zip           # dry run + CSV
    uv run agents_bots cmd backfill-address-zip --apply   # write
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
from app.services.address_normalizer import normalize_address, zip_is_plausible

DEFAULT_OUTPUT_DIR = Path("backfill_output/address_zip_backfill")

CSV_COLUMNS = [
    "job_id",
    "raw_address",
    "column",
    "old_value",
    "new_value",
    "zip_was_house_number",
    "zip_implausible_before",
    "zip_implausible_after",
]

#: The parsed components mirrored onto ``jobs`` by the classifier, paired
#: with the ``NormalizedAddress`` attribute each one comes from.
COLUMN_TO_FIELD: dict[str, str] = {
    "address_street_number": "street_number",
    "address_street_name": "street_name",
    "address_city": "city",
    "address_state": "state",
    "address_zip": "zip_code",
}

#: The raw address text that produced each Job — the earliest DispatchJob
#: with a non-empty address, which is the message that opened the Job.
_JOB_ADDRESSES_SQL = text("""
    SELECT DISTINCT ON (j.id)
        j.id AS job_id,
        j.address_street_number,
        j.address_street_name,
        j.address_city,
        j.address_state,
        j.address_zip,
        d.address AS raw_address
    FROM jobs j
    JOIN dispatch_jobs d ON d.job_id = j.id
    WHERE d.address IS NOT NULL AND d.address <> ''
    ORDER BY j.id, d.created_at ASC
""")


def _collect(rows: list[Any]) -> tuple[list[dict[str, Any]], list[tuple]]:
    """Return (audit rows, update batch) for every job whose parse changed."""
    report: list[dict[str, Any]] = []
    batch: list[tuple] = []

    for row in rows:
        parsed = normalize_address(row.raw_address)
        current = {column: getattr(row, column) for column in COLUMN_TO_FIELD}
        recomputed = {column: getattr(parsed, field) for column, field in COLUMN_TO_FIELD.items()}
        if current == recomputed:
            continue

        zip_was_house_number = bool(
            current["address_zip"] and current["address_zip"] == current["address_street_number"]
        )
        implausible_before = zip_is_plausible(current["address_state"], current["address_zip"])
        implausible_after = zip_is_plausible(recomputed["address_state"], recomputed["address_zip"])

        for column in COLUMN_TO_FIELD:
            if current[column] == recomputed[column]:
                continue
            report.append(
                {
                    "job_id": str(row.job_id),
                    "raw_address": row.raw_address,
                    "column": column,
                    "old_value": current[column] if current[column] is not None else "",
                    "new_value": recomputed[column] if recomputed[column] is not None else "",
                    "zip_was_house_number": zip_was_house_number,
                    "zip_implausible_before": implausible_before is False,
                    "zip_implausible_after": implausible_after is False,
                }
            )

        batch.append(
            (
                row.job_id,
                recomputed["address_street_number"],
                recomputed["address_street_name"],
                recomputed["address_city"],
                recomputed["address_state"],
                recomputed["address_zip"],
            )
        )

    return report, batch


_UPDATE_SQL = text("""
    UPDATE jobs SET
        address_street_number = :street_number,
        address_street_name = :street_name,
        address_city = :city,
        address_state = :state,
        address_zip = :zip_code
    WHERE id = :job_id
""")


async def _apply(db, batch: list[tuple]) -> int:
    for job_id, street_number, street_name, city, state, zip_code in batch:
        await db.execute(
            _UPDATE_SQL,
            {
                "job_id": job_id,
                "street_number": street_number,
                "street_name": street_name,
                "city": city,
                "state": state,
                "zip_code": zip_code,
            },
        )
    return len(batch)


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
        rows = (await db.execute(_JOB_ADDRESSES_SQL)).all()
        info(f"Scanned {len(rows)} job(s) with a raw address.")

        report, batch = _collect(rows)
        if not report:
            success("No corrections needed — every job already matches the current parser.")
            return

        by_column: dict[str, int] = {}
        for entry in report:
            by_column[entry["column"]] = by_column.get(entry["column"], 0) + 1

        info(f"{len(batch)} job(s) would change, {len(report)} column value(s) in total:")
        for column, count in sorted(by_column.items()):
            info(f"  {column}: {count}")
        # Counted over jobs, not report rows — one job contributes several
        # rows (one per changed column) and would otherwise be counted twice.
        jobs_with_zip_bug = {e["job_id"] for e in report if e["zip_was_house_number"]}
        jobs_implausible_before = {e["job_id"] for e in report if e["zip_implausible_before"]}
        jobs_implausible_after = {e["job_id"] for e in report if e["zip_implausible_after"]}
        info(f"  jobs whose ZIP was the house number: {len(jobs_with_zip_bug)}")
        info(
            f"  jobs with an implausible ZIP: {len(jobs_implausible_before)} before "
            f"-> {len(jobs_implausible_after)} after"
        )

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        csv_path = output_dir / f"corrections_{stamp}.csv"
        _write_csv(csv_path, report)
        info(f"Wrote full before/after report: {csv_path.resolve()}")

        if not apply:
            warning("Dry run — nothing written. Re-run with --apply to write these corrections.")
            return

        updated = await _apply(db, batch)
        # get_db_context commits on clean exit.
        success(f"Applied corrections to {updated} job(s).")


@command(
    "backfill-address-zip",
    help="Re-parse jobs.address_* with the fixed address normalizer",
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
def backfill_address_zip(output_dir: Path, apply: bool) -> None:
    """Recompute jobs.address_* from the raw message text.

    Dry-run by default: prints a summary and writes a CSV of every column
    that would change without touching the database. Pass --apply to run
    the UPDATEs in one transaction.
    """
    try:
        asyncio.run(_run(output_dir, apply))
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        error(f"Backfill failed: {exc}")
        raise
