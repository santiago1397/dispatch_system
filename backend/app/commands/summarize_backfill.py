"""Summarize a backfill run.

Reads the ``manifest.csv`` produced by ``backfill-openphone`` and (if it
exists) the ``extractions.csv`` produced by ``extract-backfill``, and
prints a review-friendly summary.

Sections printed, in order:

1. Manifest totals (total messages, is_job_likely=true, gate_passed=true).
2. Direction breakdown (incoming vs outgoing).
3. Match-method breakdown (job-likely only).
4. By company roll-up with sample bodies (existing behavior).
5. Binding-conflict count (operator-curated phone bindings disagree
   with regex).
6. Extraction quality (if extractions.csv exists):
   - Job-type breakdown.
   - Consistency-score distribution (mean / min / max / count below 0.5).
   - Dedup stats (total rows / canonical / superseded / groups).
   - Closing stats (canonical rows that look closed).

Intended workflow::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-openphone        # produces backfill_output/<ts>/
    uv run agents_bots cmd summarize-backfill --path backfill_output/<ts>
    uv run agents_bots cmd extract-backfill          # populates extractions.csv
    uv run agents_bots cmd summarize-backfill --path backfill_output/<ts>

No DB writes. No new dependencies. No AI calls.
"""

import csv
import statistics
from collections import Counter
from pathlib import Path

import click

from app.commands import command, error, info, warning

MANIFEST_FILENAME = "manifest.csv"
EXTRACTIONS_FILENAME = "extractions.csv"
SAMPLES_PER_COMPANY = 3
NEEDS_REVIEW_SCORE_THRESHOLD = 0.5

# Columns that MUST exist for a given section to print. We fail loudly
# (not silently skip) when the schema drifts so the operator notices.
_REQUIRED_EXTRACTION_COLUMNS = (
    "message_id",
    "job_type",
    "consistency_score",
    "dedup_status",
    "closing_likely",
)


@command("summarize-backfill", help="Summarize a backfill-openphone run (manifest + extractions)")
@click.option(
    "--path",
    "manifest_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Path to a backfill-openphone output directory.",
)
def summarize_backfill(manifest_dir: Path) -> None:
    """Print a per-company summary plus extraction quality stats."""
    manifest_path = manifest_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        error(f"No {MANIFEST_FILENAME} at {manifest_path}")
        raise click.ClickException(f"manifest.csv not found in {manifest_dir}")

    rows = _read_manifest(manifest_path)
    _print_manifest_summary(manifest_path, rows)

    extractions_path = manifest_dir / EXTRACTIONS_FILENAME
    if extractions_path.exists():
        _print_extractions_summary(extractions_path)
    else:
        info("")
        warning(
            f"No {EXTRACTIONS_FILENAME} at {extractions_path}. "
            f"Run `cmd extract-backfill` to populate it."
        )


# =============================================================================
# Manifest section
# =============================================================================


def _read_manifest(manifest_path: Path) -> list[dict[str, str]]:
    with manifest_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _print_manifest_summary(manifest_path: Path, rows: list[dict[str, str]]) -> None:
    total = len(rows)
    job_likely = 0
    gate_passed = 0
    binding_conflicts = 0
    by_company: dict[str, list[dict[str, str]]] = {}
    by_method: Counter[str] = Counter()
    by_direction: Counter[str] = Counter()

    for row in rows:
        direction = row.get("direction", "") or "?"
        by_direction[direction] += 1
        if (row.get("gate_passed") or "").lower() == "true":
            gate_passed += 1
        if (row.get("binding_conflict") or "").strip():
            binding_conflicts += 1
        if (row.get("is_job_likely") or "").lower() != "true":
            continue
        job_likely += 1
        method = row.get("match_method") or "none"
        by_method[method] += 1
        company = row.get("matched_company") or "(no company)"
        by_company.setdefault(company, []).append(row)

    info("")
    info(f"Manifest: {manifest_path}")
    info(f"Total messages:           {total}")
    info(f"gate_passed=true:         {gate_passed}")
    info(f"is_job_likely=true:       {job_likely}")
    info(f"binding_conflict count:   {binding_conflicts}")
    info("")
    info("Direction breakdown:")
    for direction, count in sorted(by_direction.items(), key=lambda kv: -kv[1]):
        info(f"  {direction:10s} {count}")
    info("")
    info("Match-method breakdown (job-likely only):")
    for method, count in sorted(by_method.items(), key=lambda kv: -kv[1]):
        info(f"  {method:14s} {count}")
    info("")
    info(f"By company ({len(by_company)} distinct):")

    for company in sorted(by_company, key=lambda c: -len(by_company[c])):
        rows = by_company[company]
        info("")
        info(f"  {company}  ({len(rows)} messages)")
        for row in rows[:SAMPLES_PER_COMPANY]:
            sent = row.get("sent_at", "")
            direction = row.get("direction", "")
            excerpt = (row.get("body_excerpt") or "").replace("\n", " ").strip()
            if len(excerpt) > 120:
                excerpt = excerpt[:117] + "..."
            info(f"    [{sent}] ({direction}) {excerpt}")
        if len(rows) > SAMPLES_PER_COMPANY:
            info(f"    ... and {len(rows) - SAMPLES_PER_COMPANY} more")


# =============================================================================
# Extractions section
# =============================================================================


def _print_extractions_summary(extractions_path: Path) -> None:
    """Print quality + dedup + closing stats from extractions.csv.

    Aborts loudly if the schema doesn't match — schema drift is a bug
    that should never silently pass.
    """
    rows = _read_extractions(extractions_path)
    _verify_extraction_schema(rows)

    info("")
    info(f"Extractions: {extractions_path}  ({len(rows)} rows)")
    info("")

    # === Job-type breakdown ===
    by_job_type: Counter[str] = Counter()
    not_found_count = 0
    for row in rows:
        jt = (row.get("job_type") or "").strip() or "(empty)"
        if jt == "NOT_FOUND":
            not_found_count += 1
        by_job_type[jt] += 1

    info(f"Job-type breakdown ({len(by_job_type)} distinct):")
    for jt, count in sorted(by_job_type.items(), key=lambda kv: -kv[1]):
        info(f"  {jt:30s} {count}")
    info("")
    info(f"job_type=NOT_FOUND count: {not_found_count}  (model returned non-whitelist value)")
    info("")

    # === Consistency-score distribution ===
    scores: list[float] = []
    for row in rows:
        raw = (row.get("consistency_score") or "").strip()
        if not raw:
            continue
        try:
            scores.append(float(raw))
        except ValueError:
            continue

    if scores:
        below = sum(1 for s in scores if s < NEEDS_REVIEW_SCORE_THRESHOLD)
        info("Consistency-score distribution:")
        info(f"  n:                {len(scores)}")
        info(f"  mean:             {statistics.mean(scores):.2f}")
        info(f"  min / max:        {min(scores):.2f} / {max(scores):.2f}")
        if len(scores) >= 2:
            info(f"  median:           {statistics.median(scores):.2f}")
        info(f"  < {NEEDS_REVIEW_SCORE_THRESHOLD:.1f}:          {below}  (needs-review)")
        info("")

    # === Dedup stats ===
    by_group: dict[str, list[dict[str, str]]] = {}
    canonical_count = 0
    superseded_count = 0
    for row in rows:
        gid = (row.get("dedup_group_id") or "").strip()
        if not gid:
            continue
        by_group.setdefault(gid, []).append(row)
        status = (row.get("dedup_status") or "").strip()
        if status == "canonical":
            canonical_count += 1
        elif status == "superseded":
            superseded_count += 1

    info(
        f"Dedup: {canonical_count} canonical / {superseded_count} superseded "
        f"across {len(by_group)} groups"
    )
    # Top groups by supersedes_count — the most-edited jobs.
    group_sizes = [(gid, len(g)) for gid, g in by_group.items()]
    group_sizes.sort(key=lambda kv: -kv[1])
    top_groups = [g for g in group_sizes if g[1] > 1][:5]
    if top_groups:
        info("Top groups by message count:")
        for gid, size in top_groups:
            info(f"  group {gid}: {size} messages")
    info("")

    # === Closing stats ===
    canonical_rows = [r for r in rows if (r.get("dedup_status") or "").strip() == "canonical"]
    closing_canonical = sum(
        1 for r in canonical_rows
        if (r.get("closing_likely") or "").lower() == "true"
    )
    info(
        f"Closing: {closing_canonical}/{len(canonical_rows)} canonical rows look closed "
        f"(has total + parts + no estimate language)"
    )
    info("")


def _read_extractions(extractions_path: Path) -> list[dict[str, str]]:
    with extractions_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _verify_extraction_schema(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    header = set(rows[0].keys())
    missing = [c for c in _REQUIRED_EXTRACTION_COLUMNS if c not in header]
    if missing:
        raise click.ClickException(
            f"extractions.csv is missing required columns: {missing}. "
            f"Re-run `cmd extract-backfill` with the upgraded script."
        )
