"""Backfill 2026 conversation history from a hard-coded list of Quo phone numbers.

Pure observability / one-shot extractor. Reads Quo's REST API to:

1. Resolve the hard-coded E.164 numbers -> Quo internal ``PN...`` IDs.
2. For each number, paginate ``GET /v1/conversations`` for the full 2026
   calendar year (Jan 1 -> Dec 31 UTC).
3. For each conversation, paginate ``GET /v1/messages`` (both directions).
4. Run a non-destructive **dry-run preview classifier** on every message
   that mirrors the live ``JobClassificationService`` logic exactly
   (phone lookup -> regex -> phone-binding fallback) but does not write
   to the DB and does not call OpenAI.

Outputs go to ``<repo>/backend/backfill_output/`` (flat — re-runs overwrite):

- ``raw/<PN>/<CN>.json`` — full conversation JSON + its full message list
- ``_unreachable.json`` — conversations that Quo refused (e.g. >10 participants)
- ``manifest.csv`` — flat one-row-per-message, Excel-friendly
- ``conversations.csv`` — per-conversation roll-up (counts, date range, % job-likely)
- ``run.log`` — what happened, including pagination counts and skipped items

This command does not modify any application file. It imports existing
read-only helpers from ``app.services.classification``,
``app.repositories.company_repo``, and ``app.repositories.phone_binding_repo``.

Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd backfill-openphone            # full run
    uv run agents_bots cmd backfill-openphone --dry-run  # wires up but hits Quo with no real calls
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import httpx

from app.commands import command, info, success, warning
from app.core.config import settings
from app.repositories import company_repo, phone_binding_repo
from app.services.address_normalizer import normalize_phone
from app.services.classification import (
    ADDRESS_PATTERNS,
    PHONE_PATTERN,
    _clean_for_match,
)

# Reuse _clean_for_match from the live classifier (classification.py:97-107).
# Even though Quo messages aren't WhatsApp DOM-polluted, applying it here
# keeps the backfill's regex hits aligned with the live pipeline — a
# difference of one stripped footer shouldn't change which company regex
# fires. Applied to the body ONLY; from_number is a phone and must NOT be
# passed through it (the header-strip regex would eat leading digits).

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

#: Hard-coded list of E.164 phone numbers of interest. The script:
#: 1. Discovers which Quo-managed numbers exist in the API key's workspace.
#: 2. Picks those as ``quo_side_numbers`` (the numbers we can fetch
#:    conversations FOR via Quo's API — only Quo-side numbers work).
#: 3. Uses the FULL hard-coded list as a participant filter — a
#:    conversation is kept only if at least one of its ``participants``
#:    appears in this list.
#:
#: Update this list to change the participant filter. If the workspace
#: contains numbers that are NOT in this list, the script will still
#: pull their conversations but they'll be filtered out unless they
#: involve a number from this list.
HARDCODED_PHONE_NUMBERS: list[str] = [
    "+17864421923",
    "+14704714943",
    "+13235082727",
    "+16025606444",
    "+16303492601",
    "+12674855331",
    "+18182755551",
    "+17739006515",
    "+17739006635",
    "+12138639187",
    "+15593153296",
    "+18883204762",
    "+17739999354",
    "+18664799491",
    "+12178584185",  # Quo-side: the locksmith number visible in this workspace.
]

#: Full 2026 calendar year window. Future months return empty pages harmlessly.
CREATED_AFTER = "2026-01-01T00:00:00Z"
CREATED_BEFORE = "2027-01-01T00:00:00Z"

#: Quo page size cap is 100; this is the max allowed.
PAGE_SIZE = 100

#: Polite delay between Quo API calls. 4 req/s is conservative given
#: undocumented rate limits. Increase if you see 429s.
CALL_DELAY_SECONDS = 0.25

#: Quo's ``participants`` parameter caps at 10. Threads with more are
#: unreachable via ``/v1/messages``; we capture them in ``_unreachable.json``
#: rather than fail the whole run.
MAX_PARTICIPANTS = 10

#: Output location relative to backend/. Flat — re-runs overwrite contents.
DEFAULT_OUTPUT_DIR = Path("backfill_output")

#: CSV excerpt length for spreadsheet-friendliness. Full body stays in JSON.
BODY_EXCERPT_LENGTH = 200


# =============================================================================
# Preview classifier — mirrors live classify_message() without writing
# =============================================================================


@dataclass(frozen=True)
class PreviewResult:
    """Outcome of the dry-run preview classifier.

    Mirrors the live ``JobClassificationService`` outcomes 1:1 plus two
    audit fields the live pipeline logs but doesn't surface to the caller:

    - ``gate_passed``: True if the body passed the phone+address gate.
      A row where the gate failed AND we matched a company by phone is
      still NOT_A_JOB in live code — the operator needs to see this.
    - ``binding_conflict``: set when regex matched company A but a
      phone_binding for the same sender points to company B. Live code
      logs this at classification.py:244-252 but proceeds with the regex
      win; we surface it so the operator can audit.
    """

    is_job_likely: bool
    matched_company: str | None
    match_method: str  # "phone" | "regex" | "phone_binding" | "none" | "not_a_job"
    gate_passed: bool = False
    binding_conflict: str | None = None


def _is_job_message(content: str) -> bool:
    """Same gate as JobClassificationService._is_job_message — phone AND address."""
    if not PHONE_PATTERN.search(content):
        return False
    return any(p.search(content) for p in ADDRESS_PATTERNS)


async def preview_classify(
    db,
    *,
    content: str | None,
    from_number: str | None,
) -> PreviewResult:
    """Dry-run classifier — same priority as live JobClassificationService.

    Tier ordering mirrors ``JobClassificationService.classify_message`` 1:1
    (see ``app/services/classification.py:194-252``):

    1. **Phone lookup** (unconditional). Even if the body fails the gate
       later, we record what the phone lookup said.
    2. **Job-detection gate** (phone AND address pattern). Failing the
       gate short-circuits to ``not_a_job`` — matches live behavior at
       line 204-210. The ``gate_passed`` flag surfaces this so the
       operator can see rows where phone matched but gate failed.
    3. **Regex** against all active companies' ``identification_patterns``.
       Only runs if Tier 1 missed AND gate passed.
    4. **Phone-binding fallback** — only runs if Tiers 1+3 missed AND gate
       passed. Live code also gates this on ``source == "openphone"``;
       the backfill is exclusively OpenPhone so we apply it unconditionally.

    The AI extraction tier is intentionally skipped — the dry-run only
    flags job-likely without calling OPENAI_API_KEY or writing to the DB.

    Binding-conflict audit: when regex matched company A but a phone-binding
    for the same sender points to company B, live code logs this at
    classification.py:244-252 but proceeds with the regex win (operator
    decision). We surface the conflict on the manifest so the operator
    can decide whether to update the binding.
    """
    if not content or not content.strip():
        return PreviewResult(False, None, "not_a_job")

    # Strip DOM pollution from the body (NOT from_number — that's a phone).
    match_content = _clean_for_match(content)

    # Tier 1: phone lookup (live: line 194). Pass the raw from_number —
    # company_repo.get_by_phone_number normalizes internally to last-10.
    company = await company_repo.get_by_phone_number(db, from_number)

    # Tier 2: job detection gate (live: line 204). Failing the gate
    # short-circuits regardless of whether the phone lookup matched —
    # this matches live behavior at line 204-210.
    gate_passed = _is_job_message(match_content)
    if not gate_passed:
        # Phone lookup ran but its match is discarded by the gate.
        # Operator can spot this via gate_passed=false + match_method=phone.
        return PreviewResult(False, None, "not_a_job", gate_passed=False)

    # Tier 3: regex against all active companies (live: line 214).
    if not company:
        company = await _classify_company_regex(db, match_content)
        regex_winner_name = company.name if company else None
    else:
        regex_winner_name = None

    # Tier 4: phone-binding fallback (live: line 233). Live code gates
    # this on ``source == "openphone"``; backfill is exclusively
    # OpenPhone so we apply it unconditionally.
    sender_e164 = normalize_phone(from_number) if from_number else None
    binding_conflict: str | None = None
    if not company and sender_e164:
        bound = await phone_binding_repo.get_company_by_phone(db, sender_e164)
        if bound is not None:
            company = bound
            match_method = "phone_binding"
        else:
            match_method = "regex" if regex_winner_name else "none"
    else:
        match_method = (
            "phone"
            if company and not regex_winner_name
            else "regex"
            if regex_winner_name
            else "none"
        )
        # Audit: regex matched A but a binding points to B for the same
        # sender. Live code logs this and proceeds with regex. We surface
        # it on the manifest so the operator can audit the binding.
        if (
            regex_winner_name
            and sender_e164
        ):
            bound = await phone_binding_repo.get_company_by_phone(db, sender_e164)
            if bound is not None and bound.name != regex_winner_name:
                binding_conflict = f"regex={regex_winner_name} binding={bound.name}"

    if company is None:
        return PreviewResult(False, None, "none", gate_passed=True)

    return PreviewResult(
        is_job_likely=True,
        matched_company=company.name,
        match_method=match_method,
        gate_passed=True,
        binding_conflict=binding_conflict,
    )


async def _classify_company_regex(db, content: str):
    """Same as JobClassificationService._classify_company_regex — first match wins."""
    companies = await company_repo.get_all_active(db)
    import re

    for company in companies:
        pattern_groups = company.identification_patterns or []
        for group in pattern_groups:
            patterns = group.get("patterns", [])
            if not patterns:
                continue
            # All patterns in a group must match
            if all(re.search(p, content, re.IGNORECASE | re.MULTILINE) for p in patterns):
                return company
    return None


# =============================================================================
# Quo API helpers — minimal httpx client (the existing one is sync-shaped)
# =============================================================================


class QuoClient:
    """Minimal async client for the Quo REST API — only the read paths we need."""

    def __init__(self, api_key: str, base_url: str, dry_run: bool = False) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._dry_run = dry_run

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        if self._dry_run:
            # Synthesize an empty response so the pagination loop terminates
            # cleanly without hitting the network.
            logger.info("[DRY-RUN] GET %s params=%s -> empty", path, params)
            return {"data": [], "nextPageToken": None}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"Authorization": self._api_key},
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def list_phone_numbers(self) -> list[dict[str, Any]]:
        """Return all phone numbers in the workspace (one page; rare to need more)."""
        result = await self._get("/phone-numbers", {"maxResults": 100})
        return result.get("data", [])

    async def list_conversations(
        self,
        phone_number_id: str,
        created_after: str,
        created_before: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "phoneNumbers": phone_number_id,
            "createdAfter": created_after,
            "createdBefore": created_before,
            "maxResults": PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._get("/conversations", params)

    async def list_messages(
        self,
        phone_number_id: str,
        participants: list[str],
        created_after: str,
        created_before: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "phoneNumberId": phone_number_id,
            "participants": participants,
            "createdAfter": created_after,
            "createdBefore": created_before,
            "maxResults": PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._get("/messages", params)


# =============================================================================
# Pipeline
# =============================================================================


async def run_backfill(
    *,
    output_dir: Path,
    dry_run: bool,
) -> None:
    """Run the full backfill pipeline."""

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    log_path = output_dir / "run.log"
    manifest_path = output_dir / "manifest.csv"

    run_log = _RunLog(log_path)
    run_log.info(f"Backfill started at {datetime.now(UTC).isoformat()}")
    run_log.info(f"Date window: {CREATED_AFTER} -> {CREATED_BEFORE}")
    run_log.info(f"Hard-coded numbers ({len(HARDCODED_PHONE_NUMBERS)}): {HARDCODED_PHONE_NUMBERS}")
    run_log.info(f"Output directory: {output_dir.resolve()}")

    quo = QuoClient(
        api_key=settings.OPENPHONE_API_KEY,
        base_url=settings.OPENPHONE_BASE_URL,
        dry_run=dry_run,
    )

    # Step 1 — discover workspace numbers, find Quo-side numbers to fetch from.
    run_log.info("Resolving phone numbers via Quo /v1/phone-numbers ...")
    workspace_numbers = await quo.list_phone_numbers()
    pn_by_e164 = _build_pn_lookup(workspace_numbers, run_log)
    run_log.info(f"Workspace has {len(pn_by_e164)} phone number(s): {sorted(pn_by_e164.keys())}")

    # Quo-side numbers = intersection of workspace with hard-coded list.
    # If none of the hard-coded numbers are in the workspace, fall back to
    # ALL workspace numbers (the API key's owner probably wants to extract
    # from whatever's available).
    quo_side_targets: list[tuple[str, str]] = []
    for e164 in HARDCODED_PHONE_NUMBERS:
        if e164 in pn_by_e164:
            quo_side_targets.append((e164, pn_by_e164[e164]))
    if not quo_side_targets:
        run_log.warning(
            "None of the hard-coded numbers are Quo-side numbers in this workspace. "
            f"Falling back to ALL workspace numbers ({len(pn_by_e164)})."
        )
        quo_side_targets = list(pn_by_e164.items())
    run_log.info(
        f"Quo-side target numbers ({len(quo_side_targets)}): "
        f"{[e for e, _ in quo_side_targets]}"
    )
    participant_filter = set(HARDCODED_PHONE_NUMBERS)
    run_log.info(
        f"Participant filter: keeping conversations whose participants intersect "
        f"{sorted(participant_filter)}"
    )

    # Step 2 + 3 — paginate conversations and messages per Quo-side number.
    manifest_rows: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    skipped_no_match: list[dict[str, Any]] = []

    for e164, pn in quo_side_targets:
        run_log.info(f"--- {e164} ({pn}) ---")
        try:
            await _process_number(
                quo=quo,
                e164=e164,
                pn=pn,
                participant_filter=participant_filter,
                raw_dir=raw_dir,
                manifest_rows=manifest_rows,
                unreachable=unreachable,
                skipped_no_match=skipped_no_match,
                run_log=run_log,
            )
        except Exception:
            run_log.exception(f"Unhandled error processing {e164}; continuing.")
            warning(f"Skipped {e164} after error; see {log_path}")

    # Step 4 — write manifest + sidecars + flush log
    _write_manifest(manifest_path, manifest_rows)
    _write_conversations(output_dir / "conversations.csv", manifest_rows)
    (raw_dir / "_unreachable.json").write_text(
        json.dumps(unreachable, indent=2), encoding="utf-8"
    )
    (raw_dir / "_skipped_no_match.json").write_text(
        json.dumps(skipped_no_match, indent=2), encoding="utf-8"
    )
    run_log.info(f"Wrote manifest: {manifest_path} ({len(manifest_rows)} rows)")
    run_log.info(f"Wrote conversations.csv: {len({r['conversation_id'] for r in manifest_rows})} conversations")
    run_log.info(f"Wrote _unreachable.json: {len(unreachable)} conversations skipped (>10 participants)")
    run_log.info(
        f"Wrote _skipped_no_match.json: {len(skipped_no_match)} conversations skipped "
        f"(no participant in filter)"
    )
    run_log.info(f"Backfill finished at {datetime.now(UTC).isoformat()}")
    run_log.close()

    success(
        f"Backfill complete. Output: {output_dir.resolve()}  "
        f"({len(manifest_rows)} messages from "
        f"{len({r['conversation_id'] for r in manifest_rows})} conversations, "
        f"{len(unreachable)} unreachable, {len(skipped_no_match)} filtered out)"
    )
    info(f"Manifest (Excel):    {manifest_path}")
    info(f"Conversations roll-up: {output_dir / 'conversations.csv'}")


async def _process_number(
    *,
    quo: QuoClient,
    e164: str,
    pn: str,
    participant_filter: set[str],
    raw_dir: Path,
    manifest_rows: list[dict[str, Any]],
    unreachable: list[dict[str, Any]],
    skipped_no_match: list[dict[str, Any]],
    run_log: _RunLog,
) -> None:
    """Paginate conversations + messages for one Quo-side phone number.

    A conversation is only fetched in full (and added to the manifest) if
    at least one of its ``participants`` is in ``participant_filter``.
    Conversations that don't match are recorded in ``skipped_no_match``.
    """
    from app.db.session import get_db_context

    pn_dir = raw_dir / pn
    pn_dir.mkdir(exist_ok=True)
    conversation_count = 0
    conversation_kept = 0
    message_count = 0

    # Paginate /v1/conversations
    page_token: str | None = None
    while True:
        result = await quo.list_conversations(
            phone_number_id=pn,
            created_after=CREATED_AFTER,
            created_before=CREATED_BEFORE,
            page_token=page_token,
        )
        await asyncio.sleep(CALL_DELAY_SECONDS)

        conversations = result.get("data", [])
        for conv in conversations:
            conversation_count += 1
            cn_id = conv.get("id") or "unknown"
            participants = conv.get("participants") or []
            participants_str = [str(p) for p in participants]

            # Filter: only keep conversations whose participants intersect
            # the hard-coded list (after normalizing both sides to E.164).
            normalized_participants = {_to_e164(p) or p for p in participants_str}
            matched_interest = sorted(
                n for n in participant_filter
                if n in normalized_participants or _to_e164(n) in normalized_participants
            )
            if not matched_interest:
                skipped_no_match.append(
                    {
                        "phone_number_id": pn,
                        "phone_number_e164": e164,
                        "conversation_id": cn_id,
                        "participants": participants_str,
                    }
                )
                continue

            # Group threads (>10 participants) — capture + skip message fetch.
            if len(participants_str) > MAX_PARTICIPANTS:
                run_log.warning(
                    f"  conv {cn_id}: {len(participants_str)} participants > {MAX_PARTICIPANTS}; "
                    f"skipping message fetch"
                )
                unreachable.append(
                    {
                        "phone_number_id": pn,
                        "phone_number_e164": e164,
                        "conversation_id": cn_id,
                        "reason": f"participants>{MAX_PARTICIPANTS}",
                        "participant_count": len(participants_str),
                        "matched_interest": matched_interest,
                        "conversation": conv,
                    }
                )
                continue

            conversation_kept += 1
            messages, ms = await _fetch_messages_for_conversation(
                quo=quo, pn=pn, participants=participants_str, run_log=run_log
            )
            message_count += ms

            # Persist raw conversation JSON
            (pn_dir / f"{cn_id}.json").write_text(
                json.dumps(
                    {
                        "phone_number_id": pn,
                        "phone_number_e164": e164,
                        "matched_interest": matched_interest,
                        "conversation": conv,
                        "messages": messages,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Run preview classifier per message and append to manifest
            async with get_db_context() as db:
                for msg in messages:
                    preview = await preview_classify(
                        db,
                        content=msg.get("text"),
                        from_number=msg.get("from"),
                    )
                    manifest_rows.append(
                        _manifest_row(e164, pn, cn_id, matched_interest, msg, preview)
                    )

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    run_log.info(
        f"  -> {conversation_count} total conversations ({conversation_kept} kept, "
        f"{conversation_count - conversation_kept} filtered out), "
        f"{message_count} messages for {e164}"
    )


async def _fetch_messages_for_conversation(
    *,
    quo: QuoClient,
    pn: str,
    participants: list[str],
    run_log: _RunLog,
) -> tuple[list[dict[str, Any]], int]:
    """Paginate /v1/messages for one conversation; return (messages, count)."""
    messages: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        result = await quo.list_messages(
            phone_number_id=pn,
            participants=participants,
            created_after=CREATED_AFTER,
            created_before=CREATED_BEFORE,
            page_token=page_token,
        )
        await asyncio.sleep(CALL_DELAY_SECONDS)

        page_messages = result.get("data", [])
        messages.extend(page_messages)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return messages, len(messages)


# =============================================================================
# Manifest helpers
# =============================================================================


MANIFEST_COLUMNS = [
    "phone_number_id",
    "phone_number_e164",
    "conversation_id",
    "matched_interest",
    "message_id",
    "direction",
    "from_number",
    "to_numbers",
    "sent_at",
    "body_length",
    "body_excerpt",
    "has_media",
    "is_job_likely",
    "matched_company",
    "match_method",
    "gate_passed",
    "binding_conflict",
]


CONVERSATIONS_COLUMNS = [
    "conversation_id",
    "phone_number_id",
    "phone_number_e164",
    "matched_interest",
    "first_msg_at",
    "last_msg_at",
    "msg_count",
    "incoming_count",
    "outgoing_count",
    "job_likely_count",
    "job_likely_pct",
    "gate_passed_count",
    "binding_conflict_count",
    "matched_companies",
]


def _manifest_row(
    e164: str,
    pn: str,
    cn_id: str,
    matched_interest: list[str],
    msg: dict[str, Any],
    preview: PreviewResult,
) -> dict[str, Any]:
    """Map one Quo message + preview result to a manifest row."""
    text = msg.get("text") or ""
    return {
        "phone_number_id": pn,
        "phone_number_e164": e164,
        "conversation_id": cn_id,
        "matched_interest": ",".join(matched_interest),
        "message_id": msg.get("id", ""),
        "direction": msg.get("direction", ""),
        "from_number": msg.get("from", ""),
        "to_numbers": ",".join(msg.get("to") or []),
        "sent_at": msg.get("createdAt", ""),
        "body_length": len(text),
        "body_excerpt": text[:BODY_EXCERPT_LENGTH],
        "has_media": bool(msg.get("media") or msg.get("mediaUrls")),
        "is_job_likely": preview.is_job_likely,
        "matched_company": preview.matched_company or "",
        "match_method": preview.match_method,
        "gate_passed": preview.gate_passed,
        "binding_conflict": preview.binding_conflict or "",
    }


def _write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_conversations(path: Path, rows: list[dict[str, Any]]) -> None:
    """Aggregate manifest rows into a per-conversation roll-up CSV.

    Groups by ``conversation_id`` and emits one row per conversation with
    date range, direction counts, and job-likely stats. Sorts by
    ``first_msg_at`` ascending so the roll-up reads top-to-bottom in time.
    """
    by_conv: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_conv.setdefault(row["conversation_id"], []).append(row)

    aggregates: list[dict[str, Any]] = []
    for cn_id, conv_rows in by_conv.items():
        first = conv_rows[0]
        sent_at_values = [r["sent_at"] for r in conv_rows if r["sent_at"]]
        sent_at_values.sort()
        first_at = sent_at_values[0] if sent_at_values else ""
        last_at = sent_at_values[-1] if sent_at_values else ""
        msg_count = len(conv_rows)
        incoming = sum(1 for r in conv_rows if (r.get("direction") or "").lower() == "incoming")
        outgoing = sum(1 for r in conv_rows if (r.get("direction") or "").lower() == "outgoing")
        job_likely = sum(
            1 for r in conv_rows
            if str(r.get("is_job_likely", "")).lower() == "true"
        )
        pct = round(100.0 * job_likely / msg_count, 1) if msg_count else 0.0
        companies = sorted({
            r["matched_company"]
            for r in conv_rows
            if r.get("matched_company")
        })
        aggregates.append(
            {
                "conversation_id": cn_id,
                "phone_number_id": first.get("phone_number_id", ""),
                "phone_number_e164": first.get("phone_number_e164", ""),
                "matched_interest": first.get("matched_interest", ""),
                "first_msg_at": first_at,
                "last_msg_at": last_at,
                "msg_count": msg_count,
                "incoming_count": incoming,
                "outgoing_count": outgoing,
                "job_likely_count": job_likely,
                "job_likely_pct": pct,
                "gate_passed_count": sum(
                    1 for r in conv_rows
                    if str(r.get("gate_passed", "")).lower() == "true"
                ),
                "binding_conflict_count": sum(
                    1 for r in conv_rows if (r.get("binding_conflict") or "").strip()
                ),
                "matched_companies": ",".join(companies),
            }
        )
    aggregates.sort(key=lambda r: r["first_msg_at"])

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONVERSATIONS_COLUMNS)
        writer.writeheader()
        for row in aggregates:
            writer.writerow(row)


# =============================================================================
# Misc helpers
# =============================================================================


def _build_pn_lookup(
    workspace_numbers: list[dict[str, Any]],
    run_log: _RunLog,
) -> dict[str, str]:
    """Map E.164 -> PN... by matching the ``number`` field in Quo's response."""
    lookup: dict[str, str] = {}
    for n in workspace_numbers:
        pn = n.get("id", "")
        e164 = n.get("number") or ""
        if not pn or not e164:
            continue
        # Normalize to +1XXXXXXXXXX shape for matching.
        normalized = _to_e164(e164)
        if normalized:
            lookup[normalized] = pn
    run_log.info(f"Workspace has {len(lookup)} phone numbers.")
    return lookup


def _to_e164(raw: str) -> str | None:
    """Best-effort coerce ``+1XXXXXXXXXX`` from various Quo formats."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}"
    return None


class _RunLog:
    """Tee logger to both stderr and a run.log file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh = path.open("a", encoding="utf-8")

    def _emit(self, level: str, msg: str) -> None:
        line = f"{datetime.now(UTC).isoformat()} [{level}] {msg}"
        self._fh.write(line + "\n")
        self._fh.flush()
        getattr(logger, level.lower() if level in {"INFO", "WARNING", "ERROR"} else "info")(msg)

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def warning(self, msg: str) -> None:
        self._emit("WARNING", msg)

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def exception(self, msg: str) -> None:
        # Logger.exception writes traceback; mirror to file too.
        tb = sys.exc_info()
        self._emit("ERROR", f"{msg} (exc_type={tb[0].__name__ if tb[0] else '?'})")
        import traceback

        self._fh.write(traceback.format_exc())
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# =============================================================================
# Click command
# =============================================================================


@command("backfill-openphone", help="Backfill 2026 conversation history from hard-coded Quo numbers")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Output directory (created if missing). Flat — re-runs overwrite contents.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Wire up the pipeline but skip Quo API calls (returns empty pages instantly).",
)
def backfill_openphone(output_dir: Path, dry_run: bool) -> None:
    """Backfill 2026 conversations from the 14 hard-coded Quo phone numbers.

    Writes per-conversation JSON, a flat CSV manifest (Excel-friendly), a
    per-conversation roll-up, an unreachable-conversations ledger, and a run
    log to ``--output-dir`` (default ``backfill_output/``).

    No DB writes. No outbound messages. Safe to run alongside a live uvicorn.
    """
    if dry_run:
        info(f"[DRY-RUN] Would write to {output_dir.resolve()}")
    else:
        info(f"Output will go to {output_dir.resolve()}")

    asyncio.run(run_backfill(output_dir=output_dir, dry_run=dry_run))
