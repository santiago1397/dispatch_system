"""Repair Job rows left behind by reclassify, and re-parent bad duplicate links.

Two distinct kinds of damage, both predating the fixes in this change:

1. **Stranded Jobs.** ``DispatchJobService.reclassify`` used to clear
   ``dispatch_job.job_id`` and re-run classification, which mints a *new*
   Job. The Job the message used to point at was never removed, so it sat
   in ``pending`` forever — no message behind it, no history, but still
   counted as open work and still raising ``undispatched`` alerts.

2. **Mis-parented duplicates.** ``find_dedup_candidate`` used to order
   candidates by age alone, so an older phone hit could outrank the exact
   address hit. Jobs ended up flagged ``duplicate_of`` a job on a
   different street — often a different company's, which also forced the
   cross-company branch to create a second Job instead of linking.

Pass 1 deletes; pass 2 re-links. Both are conservative:

- A stranded Job is only deleted when ``delete_if_unreferenced`` agrees it
  is inert — no DispatchJob children, no lifecycle events, not another
  Job's ``duplicate_of`` parent. Alerts cascade with it by design; they
  describe a job that no longer exists.
- A duplicate link is only rewritten when the current parent sits on a
  different street *and* the corrected ranking finds a better one. When
  no better candidate exists the flag is cleared rather than left
  pointing somewhere wrong.

Deleting rows is not reversible, so every affected Job is snapshotted into
``cleanup_duplicate_jobs_backup`` (created on demand) before it changes.

A dry run does the whole thing for real and rolls the transaction back at
the end, rather than predicting it. Predicting would be wrong: pass 2 has
to see the post-delete state to pick a surviving parent, so a preview that
skipped the deletes would report re-parenting onto a row ``--apply`` is
about to remove.

Dry-run by default. Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd cleanup-duplicate-jobs            # preview only
    uv run agents_bots cmd cleanup-duplicate-jobs --apply    # write
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import timedelta

import click
from sqlalchemy import func, select, text
from sqlalchemy.orm import aliased

from app.commands import command, info, success, warning
from app.db.models.dispatch_job import DispatchJob
from app.db.models.job import Job
from app.db.session import get_db_context
from app.repositories import job as job_repo

logger = logging.getLogger(__name__)

BACKUP_TABLE = "cleanup_duplicate_jobs_backup"

# Matches DEDUP_WINDOW_DAYS in services/classification.py. Re-parenting has
# to search the same window the original decision used, or it would "find"
# a parent the live path never would have considered.
DEDUP_WINDOW_DAYS = 14


async def _snapshot(db, job_ids: list, reason: str) -> None:
    """Copy whole Job rows into the backup table before they change."""
    if not job_ids:
        return
    await db.execute(text(f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (LIKE jobs)"))
    await db.execute(
        text(f"ALTER TABLE {BACKUP_TABLE} ADD COLUMN IF NOT EXISTS backup_reason text")
    )
    await db.execute(
        text(f"ALTER TABLE {BACKUP_TABLE} ADD COLUMN IF NOT EXISTS backed_up_at timestamptz")
    )
    await db.execute(
        text(
            f"INSERT INTO {BACKUP_TABLE} "
            "SELECT j.*, :reason, now() FROM jobs j WHERE j.id = ANY(:ids)"
        ),
        {"reason": reason, "ids": job_ids},
    )


async def _find_stranded(db) -> list[Job]:
    """Jobs with no DispatchJob pointing at them."""
    has_child = (
        select(func.count())
        .select_from(DispatchJob)
        .where(DispatchJob.job_id == Job.id)
        .scalar_subquery()
    )
    result = await db.execute(select(Job).where(has_child == 0).order_by(Job.created_at))
    return list(result.scalars().all())


async def _find_misparented(db) -> list[tuple[Job, Job]]:
    """Duplicate rows whose parent sits on a different street."""
    # aliased(), not Job.__table__.alias() — the latter flattens the parent
    # into bare columns, so row[1] comes back as the parent's first column
    # rather than a Job.
    parent = aliased(Job)
    query = (
        select(Job, parent)
        .join(parent, parent.id == Job.duplicate_of)
        .where(
            Job.is_duplicate.is_(True),
            func.coalesce(Job.address_street_name, "")
            != func.coalesce(parent.address_street_name, ""),
        )
        .order_by(Job.first_message_at)
    )
    rows = (await db.execute(query)).all()
    return [(row[0], row[1]) for row in rows]


async def _run(*, apply: bool) -> None:
    counts: Counter[str] = Counter()

    async with get_db_context() as db:
        # --- Pass 1: stranded Jobs -------------------------------------
        stranded = await _find_stranded(db)
        info(f"Stranded Jobs (no dispatch_job): {len(stranded)}")

        deletable = []
        # Stranded rows kept only because they carry history. They are still
        # artifacts, so pass 2 must not pick one as a parent — see the
        # exclude_ids call below.
        kept_stranded: set = set()
        for job in stranded:
            # Mirrors delete_if_unreferenced's guards read-only, purely so
            # the preview can name the rows before they go. The real gating
            # is still delete_if_unreferenced's, below.
            events = await db.execute(
                text("SELECT count(*) FROM job_lifecycle_events WHERE job_id = :id"),
                {"id": job.id},
            )
            children = await db.execute(
                select(func.count()).select_from(Job).where(Job.duplicate_of == job.id)
            )
            if events.scalar_one() or children.scalar_one():
                counts["stranded_kept_has_history"] += 1
                kept_stranded.add(job.id)
                continue
            deletable.append(job)

        alert_rows = 0
        if deletable:
            alerts = await db.execute(
                text("SELECT count(*) FROM alerts WHERE job_id = ANY(:ids)"),
                {"ids": [j.id for j in deletable]},
            )
            alert_rows = alerts.scalar_one()

        info(f"  deletable: {len(deletable)} (cascading {alert_rows} alert(s))")
        for job in deletable[:10]:
            addr = f"{job.address_street_number or ''} {job.address_street_name or ''}".strip()
            click.echo(f"    {str(job.id)[:8]}  {job.created_at:%m-%d %H:%M}  {addr:<34.34}")
        if len(deletable) > 10:
            click.echo(f"    ... and {len(deletable) - 10} more")

        if deletable:
            await _snapshot(db, [j.id for j in deletable], "stranded")
            for job in deletable:
                if await job_repo.delete_if_unreferenced(db, job.id):
                    counts["stranded_deleted"] += 1
                else:
                    counts["stranded_refused"] += 1

        # --- Pass 2: mis-parented duplicates ---------------------------
        # Deliberately after pass 1, and reading the post-delete state: a
        # stranded row is not a legitimate parent, so re-parenting must
        # choose among the jobs that survive. Because pass 1 has already
        # run in this transaction, ``find_dedup_candidate`` below can no
        # longer return a deleted row — which is also why the whole run
        # happens for real and is rolled back for a dry run, rather than
        # being predicted. A prediction would disagree with the outcome.
        misparented = await _find_misparented(db)
        info(f"Duplicates parented to a different street: {len(misparented)}")

        if misparented:
            await _snapshot(db, [c.id for c, _ in misparented], "misparented")

        for child, old_parent in misparented:
            since = child.first_message_at - timedelta(days=DEDUP_WINDOW_DAYS)
            better, is_cross = await job_repo.find_dedup_candidate(
                db,
                company_id=child.company_id,
                street_number=child.address_street_number,
                street_name=child.address_street_name,
                customer_phone_e164=None,  # address-only: the phone is what mis-parented it
                since=since,
                # A row no message backs is not a legitimate original, even
                # when history keeps it alive — in prod the one such row had
                # a closing signal for an entirely different job, attributed
                # to it through the same shared relay phone.
                exclude_ids=kept_stranded,
            )
            if better is not None and better.id == child.id:
                # It is its own oldest address match — it *is* the original.
                better = None

            old = str(old_parent.id)[:8]
            addr = f"{child.address_street_number or ''} {child.address_street_name or ''}".strip()
            if better is None:
                click.echo(f"    {str(child.id)[:8]}  {addr:<30.30}  {old} -> (not a duplicate)")
                counts["reparent_cleared"] += 1
                child.is_duplicate = False
                child.duplicate_of = None
            else:
                click.echo(
                    f"    {str(child.id)[:8]}  {addr:<30.30}  {old} -> {str(better.id)[:8]}"
                    f"{' (cross-company)' if is_cross else ''}"
                )
                counts["reparent_relinked"] += 1
                child.duplicate_of = better.id
                child.is_duplicate = True

        await db.flush()
        if apply:
            await db.commit()
        else:
            # get_db_context commits on a clean exit — undo everything
            # before it gets the chance.
            await db.rollback()

    verb = "Applied" if apply else "Would apply"
    success(f"{verb}: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing"))
    if apply:
        info(f"Affected rows snapshotted into {BACKUP_TABLE}.")
    else:
        warning("Dry run — nothing was written. Re-run with --apply to commit.")


@command("cleanup-duplicate-jobs", help="Delete stranded Jobs and re-parent bad duplicate links")
@click.option("--apply", is_flag=True, help="Write the changes (default: dry run)")
def cleanup_duplicate_jobs(apply: bool) -> None:
    """Repair reclassify strays and mis-parented duplicate links."""
    asyncio.run(_run(apply=apply))
