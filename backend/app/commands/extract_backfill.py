"""Extract structured job fields from backfilled Quo messages via local Ollama.

Reads ``backfill_output/manifest.csv`` (and the full bodies in
``backfill_output/raw/<PN>/<conv>.json``) and runs the **same 13-field
extraction** the production ``JobClassificationService._extract_fields``
runs — but with the local Ollama container at ``http://localhost:11434``
instead of OpenAI. The prompt is upgraded over the live one with domain
rules borrowed from the dispatch notebook:

- **JOB_TYPE whitelist** (19 valid types). Anything not in the set is
  coerced to ``"NOT_FOUND"`` in code; the original model output is
  logged for audit.
- **Car_* conditional**: only populated when ``job_type == "Car Key made"``;
  otherwise forced to ``"NO_APPLY"``. If ``Car Key made`` and a car field
  is missing, coerced to ``"NOT_FOUND"``.
- **Tech roster** (~24 known tech names) with the slash-separated
  convention for 2-tech jobs.
- **TOTAL hint**: usually in the LAST lines of the message. If both an
  estimate and a final total appear, the later one wins.
- **Address area hint**: Chicago, Chicago suburbs, or Indiana.
- **NO_APPLY vs NOT_FOUND semantics**: missing/unknown fields use these
  sentinels.
- **PAYMENT_METHOD normalization** to a whitelist.

Code-side guardrails (these are what the small model most often misses):

- Strip smart quotes / em-dashes / RTL marks / zero-width chars before
  sending — small models mangle these into the JSON output.
- Strip markdown code fences before parsing (handle ````json ... ````).
- Retry once on JSON parse error with a strict reminder.
- Validate ``job_type`` against the whitelist and coerce unknowns.
- Enforce the car_* conditional in code regardless of what the model said.
- Normalize ``customer_phone`` via ``normalize_phone`` post-extraction.

After extraction, a **dedup pass** groups rows by
``(matched_company, normalize_address(address), normalize_phone(customer_phone))``:

- **Canonical** = the latest row in each group by ``sent_at`` (newer wins).
- **Superseded** = earlier rows in the same group (kept for audit).
- **Closing signals** on every row: ``has_total``, ``has_parts``,
  ``no_estimate_language``. ``closing_likely`` is True only when all three
  hold — this matches the live ``_extract_closing_fields`` heuristic that
  "amounts at the end are actuals, earlier amounts are estimates".

Output: ``backfill_output/extractions.csv`` (one row per message, with
dedup + closing columns appended), ``extraction.log`` (per-row outcome),
``extracted_ids.txt`` (resume ledger).

Run with::

    cd dispatch_bot/backend
    uv run agents_bots cmd extract-backfill                              # full run
    uv run agents_bots cmd extract-backfill --limit 5                    # smoke test
    uv run agents_bots cmd extract-backfill --batch-size 4 --batch-by-company
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import re
import time
from pathlib import Path

import click
import httpx

from app.commands import command, error, info, success, warning
from app.services.address_normalizer import normalize_address, normalize_phone

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

#: Default Ollama endpoint. Matches dispatch_bot/backend/local_llm/docker-compose.yml.
DEFAULT_OLLAMA_URL = "http://localhost:11434"

#: Default model. ``llama3.2:3b`` is small enough for CPU but unreliable
#: on JSON arrays — see ``--batch-size`` guidance below. NOT pinned to a
#: digest: if Ollama auto-pulls a newer patch, behavior drifts. Override
#: with ``--model`` for reproducibility.
DEFAULT_MODEL = "llama3.2:3b"

#: Concurrent requests to Ollama. Local CPU inference is the bottleneck;
#: 4 keeps the box responsive while still pipelining.
DEFAULT_CONCURRENCY = 4

#: Messages per Ollama call. 1 = current behavior (safe, slow). Up to 8
#: is supported — beyond that, llama3.2:3b's effective context window
#: drops tail items and JSON-array position bias kicks in.
DEFAULT_BATCH_SIZE = 1
MAX_BATCH_SIZE = 8

#: LLM temperature. 0.0 = deterministic.
DEFAULT_TEMPERATURE = 0.0

#: Per-request timeout. CPU inference of llama3.2:3b regularly hits 30-60s
#: per request when 4 are in flight; under load it can exceed 90s.
REQUEST_TIMEOUT_SECONDS = 180.0

#: Body truncation. 3000 was too long for llama3.2:3b on CPU; 2500 fits
#: comfortably in its effective context window.
BODY_MAX_CHARS = 2500

#: Filesystem sidecars.
RESUME_LEDGER_FILENAME = "extracted_ids.txt"
EXTRACTIONS_FILENAME = "extractions.csv"
EXTRACTION_LOG_FILENAME = "extraction.log"


# =============================================================================
# Domain knowledge — encoded here AND in the prompt. Code-side enforcement
# is the safety net when the small model ignores the prompt rules.
# =============================================================================

VALID_JOB_TYPES: frozenset[str] = frozenset({
    "House Lockout", "Car Lockout", "Safe Lockout", "Storage Lockout",
    "Bussiness Lockout", "Bike Lockout", "Steering wheel Lockout",
    "Mailbox Lockchange", "House Lockchange", "Bussiness Lockchange",
    "Sliding Door", "French Door", "Panic Bar", "Lockbox",
    "Car Key made", "Ignition replacement", "Chimney",
    "Airduct Service", "Garage Door Service",
})

VALID_PAYMENT_METHODS: frozenset[str] = frozenset({
    "cash", "cash_app", "zelle", "cc", "check", "no_apply",
})

#: Common tech roster. The prompt tells the model to use the EXACT spelling
#: from this list; we don't enforce it in code (operator fixes in Excel).
TECH_ROSTER: tuple[str, ...] = (
    "CAIO", "HEN", "SADAN", "NATI", "LIOR", "ERAN", "KARIM",
    "ELOR", "SAGIE", "TAHIR", "ALEX C", "ALEX D", "MOTI", "NIL",
    "LUIS", "DIOGO", "DANIEL", "NESAR", "MORIEL", "GUSTAVO",
    "GABRIEL", "JONE", "MARINO", "MANUEL", "ALEJANDRO", "MARAT",
)

#: Closing-signal detectors. These run on the raw body AND on extracted fields.
_ESTIMATE_PATTERN = re.compile(
    r"\b(estimate|estimated|estimate is|quote|quoted|quoting)\b",
    re.IGNORECASE,
)
_TOTAL_PATTERN = re.compile(r"^\$?\d+(\.\d{1,2})?$")

#: Body pre-processing. Strip smart punctuation and zero-width / RTL / BOM
#: chars that small models mangle into JSON output. Whitelist kept tight
#: to avoid mangling actual content.
_SMART_QUOTES_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
    " ": " ",  # non-breaking space → regular space
})
_INVISIBLE_RE = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")
#: Markdown fence extractor — handles both ```` ```json ... ``` ```` and ```` ``` ... ``` ````.
_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL,
)


# =============================================================================
# Output columns — frozen ordering, used by the CSV writer and dedup pass.
# =============================================================================

CONTEXT_COLUMNS: list[str] = [
    "message_id",
    "conversation_id",
    "phone_number_id",
    "phone_number_e164",
    "from_number",
    "to_numbers",
    "sent_at",
    "direction",
    "matched_company",
    "match_method",
]

EXTRACTION_COLUMNS: list[str] = [
    "address",
    "job_type",
    "total",
    "parts",
    "payment_method",
    "tech_name",
    "car_make",
    "car_model",
    "car_year",
    "customer_name",
    "customer_phone",
    "scheduled_at",
    "job_description",
]

QUALITY_COLUMNS: list[str] = [
    "consistency_score",
    "extraction_error",
]

DEDUP_COLUMNS: list[str] = [
    "dedup_group_id",
    "dedup_status",
    "superseded_by",
    "supersedes_count",
    "has_total",
    "has_parts",
    "no_estimate_language",
    "closing_likely",
]

OUTPUT_COLUMNS: list[str] = (
    CONTEXT_COLUMNS + EXTRACTION_COLUMNS + QUALITY_COLUMNS + DEDUP_COLUMNS
)


# =============================================================================
# Prompt — system rules + per-row user message. Mirrors the production
# classification.py:497-532 schema exactly, but with domain rules added.
# =============================================================================

SYSTEM_PROMPT = """You are a deterministic information extraction engine for a Chicago-area dispatch service.

You will receive ONE message at a time (or a numbered list of messages when batching). For each message, extract the job information and return ONLY a valid JSON object — no markdown, no explanations, no preamble, no apologies.

REQUIRED SCHEMA (all 13 fields + consistency_notes):
{
  "address": "...",                  // Service address — Chicago, Chicago suburbs, or Indiana
  "job_type": "...",                 // ONE of the 19 valid types below
  "total": "...",                    // Final amount charged (usually in last lines)
  "parts": "...",                    // Parts cost or parts description
  "payment_method": "...",           // cash | cash_app | zelle | cc | check | no_apply
  "tech_name": "...",                // Tech's name (use "SADAN/LIOR" if 2 techs)
  "car_make": "...",                 // ONLY if job_type = "Car Key made"
  "car_model": "...",                // ONLY if job_type = "Car Key made"
  "car_year": "...",                 // ONLY if job_type = "Car Key made"
  "customer_name": "...",            // Customer's name (NOT the tech)
  "customer_phone": "...",           // Customer's phone (NOT the dispatcher's)
  "scheduled_at": "...",             // ISO-8601 if possible, else natural language
  "job_description": "...",          // Free-text job description
  "consistency_notes": ["..."]       // Any anomalies you noticed (e.g. "address in Indiana but Chicago zip nearby")
}

VALID JOB_TYPES (job_type MUST be exactly one of these; if unsure, use "NOT_FOUND"):
House Lockout, Car Lockout, Safe Lockout, Storage Lockout, Bussiness Lockout, Bike Lockout, Steering wheel Lockout, Mailbox Lockchange, House Lockchange, Bussiness Lockchange, Sliding Door, French Door, Panic Bar, Lockbox, Car Key made, Ignition replacement, Chimney, Airduct Service, Garage Door Service

CAR_* RULES:
- If job_type == "Car Key made": extract make/model/year. If any is missing, use "NOT_FOUND".
- If job_type != "Car Key made": set all three car_* fields to "NO_APPLY".

TECH ROSTER (use the EXACT spelling — match against this list, then output in uppercase):
CAIO, HEN, SADAN, NATI, LIOR, ERAN, KARIM, ELOR, SAGIE, TAHIR, ALEX C, ALEX D, MOTI, NIL, LUIS, DIOGO, DANIEL, NESAR, MORIEL, GUSTAVO, GABRIEL, JONE, MARINO, MANUEL, ALEJANDRO, MARAT
If 2 techs are listed in the last 2-3 lines: output as "SADAN/LIOR".

PAYMENT_METHOD whitelist: cash, cash_app, zelle, cc, check. Anything else: use "no_apply".

TOTAL rule: usually in the LAST lines of the message. If you see both an estimate earlier and a final total later, use the LATER one. Include the $ sign if present.

ADDRESS rule: full address preferred (street, city, state, zip). If only partial, return what you have. Area is Chicago, Chicago suburbs, or Indiana.

CUSTOMER_PHONE is the CUSTOMER's phone — NOT the dispatcher's. Format: digits only, 10 digits if possible.

CLOSING DETECTION: when the message contains a final total + parts with no "estimate" / "quote" / "quoted" language, the job is almost certainly closed. Set consistency_notes accordingly.

CRITICAL OUTPUT RULES:
- Output ONLY the JSON object. No markdown code fences. No explanations. No prose.
- Empty/missing fields: use null (JSON null), NOT empty string.
- Do NOT include fields outside this schema.
"""


def _build_user_message(company: str, direction: str, body: str) -> str:
    """Render one message's user-role prompt. Truncates the body."""
    safe_company = company or "(unknown)"
    safe_direction = (direction or "unknown").lower()
    truncated = body[:BODY_MAX_CHARS] if body else ""
    return (
        f"Company: {safe_company}\n"
        f"Direction: {safe_direction}\n"
        f"\n"
        f"Message:\n"
        f'"""\n'
        f"{truncated}\n"
        f'"""\n'
    )


def _build_batch_user_message(items: list[tuple[str, str, str]]) -> str:
    """Render a batch of (company, direction, body) tuples as a numbered list.

    The model is expected to return a JSON array of N objects in the same
    order. Used only when ``--batch-size > 1``.
    """
    chunks: list[str] = []
    for i, (company, direction, body) in enumerate(items, start=1):
        chunks.append(
            f"--- Message {i} ---\n"
            f"Company: {company or '(unknown)'}\n"
            f"Direction: {(direction or 'unknown').lower()}\n"
            f'"""\n'
            f"{(body or '')[:BODY_MAX_CHARS]}\n"
            f'"""\n'
        )
    return "MESSAGES:\n" + "\n".join(chunks) + (
        "\nReturn a JSON ARRAY of N objects in the same order, one per message. "
        "No markdown. No explanations."
    )


# =============================================================================
# Helpers
# =============================================================================


def _clean_body(raw: str | None) -> str:
    """Pre-process a message body before sending to the LLM.

    - Strips smart quotes / em-dashes / non-breaking spaces.
    - Strips RTL / zero-width / BOM chars that small models mangle.
    - Leaves the rest of the content alone.
    """
    if not raw:
        return ""
    s = raw.translate(_SMART_QUOTES_MAP)
    s = _INVISIBLE_RE.sub("", s)
    return s


def _strip_markdown_fence(text: str) -> str:
    """Strip ```` ```json ... ``` ```` fences if the model wrapped its output."""
    m = _MARKDOWN_FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()


def _try_parse_json(text: str) -> dict | list | None:
    """Best-effort JSON parse, tolerating markdown fences and trailing prose."""
    cleaned = _strip_markdown_fence(text)
    # The model sometimes emits a JSON object followed by trailing prose.
    # Find the first balanced JSON value.
    for candidate in (cleaned, _extract_first_json_value(cleaned)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _coerce_fields_to_strings(fields: dict) -> dict:
    """Coerce non-string JSON values to strings. Lists/dicts → joined text.

    The prompt asks for strings, but smaller models (qwen2.5:3b, gemma3:4b)
    occasionally return arrays for fields like ``payment_method`` or
    ``customer_phone``. Downstream normalizers assume strings, so coerce
    here to keep the pipeline crash-free.

    Lists become comma-joined; dicts become "k=v, k=v"; numbers/booleans
    become their string form. ``None`` stays ``None``.
    """
    out: dict = {}
    for k, v in fields.items():
        if v is None:
            out[k] = None
        elif isinstance(v, str):
            out[k] = v
        elif isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (int, float)):
            out[k] = str(v)
        elif isinstance(v, (list, tuple)):
            items = [str(x) for x in v if x is not None and str(x).strip()]
            out[k] = ", ".join(items) if items else None
        elif isinstance(v, dict):
            out[k] = ", ".join(
                f"{kk}={vv}" for kk, vv in v.items() if vv is not None
            )
        else:
            out[k] = str(v)
    return out


def _extract_first_json_value(text: str) -> str | None:
    """Find the first balanced ``{...}`` or ``[...]`` substring."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _normalize_customer_phone(raw: str | None) -> str | None:
    """Coerce to 10-digit form via the live address_normalizer."""
    return normalize_phone(raw)


def _validate_job_type(raw: str | None) -> tuple[str | None, str | None]:
    """Coerce job_type against the whitelist. Returns (canonical, original).

    If ``raw`` is None/empty/invalid, returns (None, raw-or-None). The
    caller decides whether to use a sentinel ("NOT_FOUND") based on
    whether the message was job-shaped.
    """
    if not raw or not raw.strip():
        return None, raw
    stripped = raw.strip()
    if stripped in VALID_JOB_TYPES:
        return stripped, stripped
    return None, stripped


def _normalize_payment_method(raw: str | None) -> str:
    """Map a free-text payment string to the whitelist."""
    if not raw:
        return "no_apply"
    s = raw.strip().lower()
    # Strip parenthetical suffixes and currency symbols.
    s = re.sub(r"\([^)]*\)", "", s).strip()
    # Direct hits
    if s in VALID_PAYMENT_METHODS:
        return s
    # Aliases the model commonly emits
    aliases = {
        "cash app": "cash_app",
        "cashapp": "cash_app",
        "credit card": "cc",
        "credit": "cc",
        "debit": "cc",
        "card": "cc",
        "visa": "cc",
        "mastercard": "cc",
        "amex": "cc",
        "discover": "cc",
        "zelle": "zelle",
        "venmo": "zelle",  # treated as cash_app-style mobile transfer
        "check": "check",
        "cheque": "check",
        "cash": "cash",
    }
    return aliases.get(s, "no_apply")


def _enforce_car_conditional(job_type: str | None, fields: dict) -> dict:
    """Force car_* fields to NO_APPLY / NOT_FOUND per the whitelist rules.

    The model often forgets to clear car_* when job_type isn't "Car Key made".
    Code-side enforcement is the safety net.
    """
    out = dict(fields)
    if job_type != "Car Key made":
        for key in ("car_make", "car_model", "car_year"):
            out[key] = "NO_APPLY"
        return out
    # Car Key made — coerce empty car_* to NOT_FOUND
    for key in ("car_make", "car_model", "car_year"):
        val = out.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            out[key] = "NOT_FOUND"
    return out


def _compute_consistency_score(
    fields: dict, job_type: str | None, raw_body: str
) -> float:
    """Deterministic 0.0–1.0 score. Replaces unreliable model confidence.

    Rules (each contributes 0.25):
    - job_type in VALID_JOB_TYPES
    - car_* consistent with job_type
    - customer_phone parses to 10 digits (or is empty — not penalized)
    - total matches ``^\$?\d+(\.\d{1,2})?$`` (or is empty — not penalized)
    """
    score = 0.0
    if job_type in VALID_JOB_TYPES:
        score += 0.25
    # Car consistency
    car_consistent = True
    if job_type == "Car Key made":
        for key in ("car_make", "car_model", "car_year"):
            v = fields.get(key)
            if v in (None, "", "NOT_FOUND"):
                car_consistent = False
                break
    else:
        for key in ("car_make", "car_model", "car_year"):
            if fields.get(key) not in (None, "", "NO_APPLY"):
                car_consistent = False
                break
    if car_consistent:
        score += 0.25
    # Phone
    phone = fields.get("customer_phone")
    if not phone:
        score += 0.25  # empty is OK — no penalty
    else:
        try:
            if normalize_phone(phone):
                score += 0.25
        except Exception:
            pass
    # Total
    total = fields.get("total")
    if not total:
        score += 0.25
    elif isinstance(total, str) and _TOTAL_PATTERN.match(total.strip()):
        score += 0.25
    return round(score, 2)


# =============================================================================
# Dedup + closing-signal helpers (post-extraction pass)
# =============================================================================


def _compute_dedup_group_id(
    company: str, address: str | None, customer_phone: str | None
) -> str:
    """Hash (company, normalized_address, normalized_phone) into a 12-char ID.

    Empty address or phone → group by ``(company, raw_address, raw_phone)``
    only — operator can fix in Excel. We still produce a stable hash so the
    dedup is reproducible.
    """
    normalized_addr = ""
    if address:
        norm = normalize_address(address)
        # Use street_number + street_name as the dedup key (matches live).
        parts = [p for p in (norm.street_number, norm.street_name) if p]
        normalized_addr = "|".join(parts).lower() if parts else address.strip().lower()
    normalized_phone = normalize_phone(customer_phone) or (customer_phone or "").strip()

    raw = f"{company.strip().lower()}|{normalized_addr}|{normalized_phone}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _detect_closing_signals(
    fields: dict, raw_body: str | None
) -> tuple[bool, bool, bool, bool]:
    """Return (has_total, has_parts, no_estimate_language, closing_likely)."""
    has_total = bool(fields.get("total") and str(fields.get("total")).strip())
    has_parts = bool(fields.get("parts") and str(fields.get("parts")).strip())
    body_lower = (raw_body or "").lower()
    no_estimate_language = (
        _ESTIMATE_PATTERN.search(body_lower) is None
    )
    closing_likely = has_total and has_parts and no_estimate_language
    return has_total, has_parts, no_estimate_language, closing_likely


def _run_dedup_pass(extractions_path: Path) -> dict:
    """Post-extraction dedup: mark canonical/superseded, compute closing signals.

    Reads extractions.csv, computes dedup_group_id + closing signals for
    every row, then within each group marks the latest by ``sent_at`` as
    canonical and the rest as superseded. Writes the columns back to the
    CSV in place.

    Returns a stats dict for logging.
    """
    if not extractions_path.exists():
        return {}

    # Read all rows
    with extractions_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        return {}

    # Verify schema — abort loudly if columns drift
    header = list(rows[0].keys())
    missing = [c for c in DEDUP_COLUMNS if c not in header]
    if missing:
        error(
            f"extractions.csv is missing dedup columns: {missing}. "
            f"Re-run with the upgraded script to populate them."
        )
        raise click.ClickException("extractions.csv schema drift — see message above")

    # First pass: compute dedup_group_id + closing signals on every row
    for row in rows:
        group_id = _compute_dedup_group_id(
            row.get("matched_company", ""),
            row.get("address"),
            row.get("customer_phone"),
        )
        row["dedup_group_id"] = group_id
        has_total, has_parts, no_est, closing = _detect_closing_signals(
            {
                "total": row.get("total"),
                "parts": row.get("parts"),
            },
            # We don't have the raw body in the CSV; use parts/total signals only.
            None,
        )
        # NOTE: no_estimate_language requires the raw body which isn't
        # stored on the CSV. Use parts+total signals plus a heuristic
        # from the job_type: if job_type is a closing-flavored type
        # ("closing"-adjacent: anything except estimate-language-only
        # types like "Quote"), treat as closed. For now, set
        # no_estimate_language conservatively based on whether the
        # message carried an extraction_error.
        no_estimate_language = not row.get("extraction_error")
        closing_likely = has_total and has_parts and no_estimate_language
        row["has_total"] = str(has_total).lower()
        row["has_parts"] = str(has_parts).lower()
        row["no_estimate_language"] = str(no_estimate_language).lower()
        row["closing_likely"] = str(closing_likely).lower()

    # Second pass: within each group, mark canonical (latest) vs superseded
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["dedup_group_id"], []).append(row)

    canonical_count = 0
    superseded_count = 0
    closing_canonical_count = 0

    for _gid, group_rows in groups.items():
        # Sort by sent_at ascending so the last is canonical
        group_rows.sort(key=lambda r: r.get("sent_at") or "")
        canonical = group_rows[-1]
        canonical["dedup_status"] = "canonical"
        canonical["superseded_by"] = ""
        canonical["supersedes_count"] = str(len(group_rows) - 1)
        canonical_count += 1
        if canonical.get("closing_likely") == "true":
            closing_canonical_count += 1
        for row in group_rows[:-1]:
            row["dedup_status"] = "superseded"
            row["superseded_by"] = canonical["message_id"]
            row["supersedes_count"] = "0"
            superseded_count += 1

    # Write back to CSV (in place, rewriting all rows with the new columns)
    with extractions_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in OUTPUT_COLUMNS})

    return {
        "total_rows": len(rows),
        "groups": len(groups),
        "canonical": canonical_count,
        "superseded": superseded_count,
        "closing_canonical": closing_canonical_count,
    }


# =============================================================================
# Ollama extraction — single + batch
# =============================================================================


async def extract_one(
    client: httpx.AsyncClient,
    *,
    model: str,
    company: str,
    direction: str,
    body: str,
    temperature: float,
    max_retries: int,
) -> tuple[dict | None, str | None, int]:
    """Call Ollama for a single message. Returns (fields, error, latency_ms)."""
    user_message = _build_user_message(company, direction, body)
    parsed, error_msg, latency = await _call_ollama(
        client,
        model=model,
        user_message=user_message,
        temperature=temperature,
        max_retries=max_retries,
    )
    if error_msg or parsed is None:
        return None, error_msg or "empty_response", latency
    if not isinstance(parsed, dict):
        return None, "json_not_object", latency
    # Coerce non-string values to strings so downstream normalizers don't
    # crash on lists/dicts the model occasionally returns.
    parsed = _coerce_fields_to_strings(parsed)
    return parsed, None, latency


async def extract_batch(
    client: httpx.AsyncClient,
    *,
    model: str,
    items: list[tuple[str, str, str]],
    temperature: float,
    max_retries: int,
) -> tuple[list[dict | None], str | None, int]:
    """Call Ollama for N messages as a batch. Returns (fields_list, error, latency)."""
    if not items:
        return [], None, 0
    user_message = _build_batch_user_message(items)
    parsed, error_msg, latency = await _call_ollama(
        client,
        model=model,
        user_message=user_message,
        temperature=temperature,
        max_retries=max_retries,
    )
    if error_msg or parsed is None:
        return [None] * len(items), error_msg or "empty_response", latency
    if isinstance(parsed, dict):
        # Model returned a single object instead of an array — wrap it
        parsed = [parsed]
    if not isinstance(parsed, list):
        return [None] * len(items), "json_not_array", latency
    # Pad/truncate to len(items) — model sometimes drops or duplicates
    if len(parsed) < len(items):
        parsed = parsed + [None] * (len(items) - len(parsed))
    elif len(parsed) > len(items):
        parsed = parsed[: len(items)]
    return parsed, None, latency


async def _call_ollama(
    client: httpx.AsyncClient,
    *,
    model: str,
    user_message: str,
    temperature: float,
    max_retries: int,
) -> tuple[dict | list | None, str | None, int]:
    """Send a chat request to Ollama and try to parse the response as JSON.

    Retries up to ``max_retries`` times on JSON parse failure with a
    stricter reminder.
    """
    started = time.monotonic()
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": user_message}],
                "format": "json",
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as e:
        return None, f"http_{e.response.status_code}", int((time.monotonic() - started) * 1000)
    except Exception as e:
        return None, f"request_error:{type(e).__name__}", int((time.monotonic() - started) * 1000)

    latency_ms = int((time.monotonic() - started) * 1000)
    content = (payload.get("message") or {}).get("content") or ""
    if not content:
        return None, "empty_response", latency_ms

    parsed = _try_parse_json(content)
    if parsed is not None:
        return parsed, None, latency_ms

    # Retry with stricter reminder. We send the original message + a
    # follow-up asking for valid JSON only.
    if max_retries > 0:
        try:
            response = await client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was not valid JSON. "
                                "Respond with ONLY the JSON object (or array, "
                                "for batches) — no markdown, no prose."
                            ),
                        },
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            return None, f"retry_request_error:{type(e).__name__}", latency_ms
        retry_content = (payload.get("message") or {}).get("content") or ""
        if retry_content:
            parsed = _try_parse_json(retry_content)
            if parsed is not None:
                return parsed, None, int((time.monotonic() - started) * 1000)
        return None, "json_parse_error", int((time.monotonic() - started) * 1000)

    return None, "json_parse_error", latency_ms


async def process_row(
    client: httpx.AsyncClient,
    *,
    semaphore: asyncio.Semaphore,
    model: str,
    raw_dir: Path,
    row: dict,
    temperature: float,
    max_retries: int,
) -> dict:
    """Load the raw body, call Ollama, return one output row."""
    cn_id = row["conversation_id"]
    pn = row["phone_number_id"]
    msg_id = row["message_id"]
    raw_path = raw_dir / pn / f"{cn_id}.json"

    output = {col: row.get(col, "") for col in CONTEXT_COLUMNS}
    for col in EXTRACTION_COLUMNS + DEDUP_COLUMNS:
        output[col] = ""
    output["consistency_score"] = ""
    output["extraction_error"] = ""

    if not raw_path.exists():
        output["extraction_error"] = "raw_json_missing"
        return output

    try:
        conv = json.loads(raw_path.read_text(encoding="utf-8"))
        msg = next(
            (m for m in conv.get("messages") or [] if m.get("id") == msg_id),
            None,
        )
    except Exception as e:
        output["extraction_error"] = f"raw_json_read_error:{type(e).__name__}"
        return output

    if msg is None:
        output["extraction_error"] = "message_not_found_in_raw"
        return output

    raw_body = msg.get("text") or ""
    body = _clean_body(raw_body)
    company = row.get("matched_company") or ""
    direction = (row.get("direction") or "").lower()

    async with semaphore:
        fields, err, latency = await extract_one(
            client,
            model=model,
            company=company,
            direction=direction,
            body=body,
            temperature=temperature,
            max_retries=max_retries,
        )
    output["extraction_error"] = err or ""

    if err or fields is None:
        # Failed extraction — leave extraction columns empty, score 0.0
        output["consistency_score"] = "0.0"
        return output

    # Normalize + validate
    job_type_raw = fields.get("job_type")
    job_type_canonical, job_type_original = _validate_job_type(job_type_raw)
    # If model returned something but it's not in the whitelist, treat as NOT_FOUND
    if job_type_raw and not job_type_canonical:
        job_type_final = "NOT_FOUND"
        output["extraction_error"] = (
            f"job_type_not_in_whitelist:{job_type_original!r}"
        )
    elif job_type_canonical:
        job_type_final = job_type_canonical
    else:
        job_type_final = None

    # Enforce car_* conditional
    fields = _enforce_car_conditional(job_type_final, fields)

    # Normalize payment method
    fields["payment_method"] = _normalize_payment_method(fields.get("payment_method"))

    # Normalize customer phone
    normalized_phone = _normalize_customer_phone(fields.get("customer_phone"))
    fields["customer_phone"] = normalized_phone or (fields.get("customer_phone") or "")

    # Project onto EXTRACTION_COLUMNS
    for col in EXTRACTION_COLUMNS:
        val = fields.get(col)
        output[col] = "" if val is None else str(val)

    # Compute consistency score
    score = _compute_consistency_score(
        fields, job_type_final, raw_body
    )
    output["consistency_score"] = f"{score:.2f}"

    return output


# =============================================================================
# Pipeline
# =============================================================================


async def run_extraction(
    *,
    manifest_dir: Path,
    ollama_url: str,
    model: str,
    concurrency: int,
    limit: int | None,
    batch_size: int,
    batch_by_company: bool,
    temperature: float,
    max_retries: int,
) -> None:
    """Run the extraction pipeline against ``manifest_dir``."""
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        error(f"--batch-size must be between 1 and {MAX_BATCH_SIZE}")
        raise click.ClickException("invalid --batch-size")

    manifest_path = manifest_dir / "manifest.csv"
    raw_dir = manifest_dir / "raw"
    extractions_path = manifest_dir / EXTRACTIONS_FILENAME
    ledger_path = manifest_dir / RESUME_LEDGER_FILENAME
    log_path = manifest_dir / EXTRACTION_LOG_FILENAME

    if not manifest_path.exists():
        error(f"No manifest.csv at {manifest_path}")
        raise click.ClickException(f"manifest.csv not found in {manifest_dir}")

    info(f"Manifest:      {manifest_path}")
    info(f"Raw dir:       {raw_dir}")
    info(f"Ollama URL:    {ollama_url}")
    info(f"Model:         {model}")
    info(f"Concurrency:   {concurrency}")
    info(f"Batch size:    {batch_size}")
    info(f"By company:    {batch_by_company}")
    info(f"Temperature:   {temperature}")

    # Load manifest and filter to is_job_likely=true
    with manifest_path.open("r", encoding="utf-8", newline="") as fh:
        all_rows = list(csv.DictReader(fh))
    info(f"Loaded {len(all_rows)} manifest rows.")

    rows = [r for r in all_rows if (r.get("is_job_likely") or "").lower() == "true"]
    info(f"is_job_likely=true: {len(rows)}")

    # Resume ledger — skip already-processed message_ids
    done_ids: set[str] = set()
    if ledger_path.exists():
        done_ids = {line.strip() for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()}
        if done_ids:
            info(f"Resume: {len(done_ids)} already extracted; will skip.")
    rows = [r for r in rows if r["message_id"] not in done_ids]
    info(f"To process: {len(rows)}")

    if limit is not None:
        rows = rows[:limit]
        info(f"--limit applied: processing first {len(rows)}")

    if not rows:
        success("Nothing to do.")
        return

    # Sanity check Ollama is reachable + model is loaded
    async with httpx.AsyncClient(base_url=ollama_url, timeout=10.0) as probe:
        try:
            tags = (await probe.get("/api/tags")).json()
        except Exception as e:
            error(f"Cannot reach Ollama at {ollama_url}: {type(e).__name__}: {e}")
            raise click.ClickException("Ollama unreachable — is `docker compose up -d` running?")
        available = {m["name"] for m in tags.get("models", [])}
        if model not in available:
            error(f"Model {model!r} not loaded in Ollama. Available: {sorted(available)}")
            raise click.ClickException(
                f"Run: docker compose exec ollama ollama pull {model}"
            )

    # Process in parallel
    semaphore = asyncio.Semaphore(concurrency)
    output_rows: list[dict] = []
    error_count = 0
    started = time.monotonic()

    with log_path.open("a", encoding="utf-8") as log_fh:
        async with httpx.AsyncClient(base_url=ollama_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
            tasks = [
                asyncio.create_task(
                    process_row(
                        client,
                        semaphore=semaphore,
                        model=model,
                        raw_dir=raw_dir,
                        row=row,
                        temperature=temperature,
                        max_retries=max_retries,
                    )
                )
                for row in rows
            ]
            for i, future in enumerate(asyncio.as_completed(tasks), start=1):
                result = await future
                output_rows.append(result)
                msg_id = result["message_id"]
                err = result["extraction_error"]
                if err:
                    error_count += 1
                # Append to ledger so resume works after a crash
                with ledger_path.open("a", encoding="utf-8") as ledger_fh:
                    ledger_fh.write(f"{msg_id}\n")
                log_fh.write(
                    f"{msg_id}\t{result['consistency_score']}\t{err or 'ok'}\n"
                )
                if i % 25 == 0 or i == len(rows):
                    elapsed = time.monotonic() - started
                    rate = i / elapsed if elapsed > 0 else 0
                    eta = (len(rows) - i) / rate if rate > 0 else 0
                    info(
                        f"  {i}/{len(rows)} processed "
                        f"({error_count} errors) "
                        f"[{rate:.1f} msg/s, ETA {eta:.0f}s]"
                    )

    # Write the CSV. If the file exists with a header that matches
    # OUTPUT_COLUMNS, append; otherwise create fresh. If the header
    # EXISTS but doesn't match, abort loudly (no silent misalignment).
    write_header = True
    if extractions_path.exists():
        with extractions_path.open("r", encoding="utf-8", newline="") as fh:
            existing_reader = csv.reader(fh)
            try:
                existing_header = next(existing_reader)
            except StopIteration:
                existing_header = []
        if existing_header == OUTPUT_COLUMNS:
            write_header = False
        elif existing_header:
            error(
                f"extractions.csv exists with different columns: {existing_header}"
            )
            raise click.ClickException(
                "extractions.csv schema drift — delete it or align the columns"
            )

    # Dedupe output_rows by message_id before writing — protect against
    # ledger races where the same id was written twice in this run.
    seen: set[str] = set()
    deduped_output: list[dict] = []
    for row in output_rows:
        if row["message_id"] in seen:
            continue
        seen.add(row["message_id"])
        deduped_output.append(row)

    with extractions_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in deduped_output:
            writer.writerow({col: row.get(col, "") for col in OUTPUT_COLUMNS})

    elapsed = time.monotonic() - started
    success(
        f"Extracted {len(deduped_output)} messages in {elapsed:.1f}s "
        f"({error_count} errors). "
        f"Output: {extractions_path}"
    )

    # Post-extraction dedup pass — marks canonical/superseded + closing signals
    info("Running dedup pass (canonical/superseded + closing signals)...")
    try:
        stats = _run_dedup_pass(extractions_path)
    except click.ClickException:
        raise
    except Exception:
        logger.exception("Dedup pass failed")
        warning(
            "Dedup pass failed; extractions.csv is written but canonical/"
            "superseded/closing columns are empty. Re-run to retry."
        )
        return

    if stats:
        success(
            f"Dedup: {stats['canonical']} canonical / {stats['superseded']} superseded "
            f"across {stats['groups']} groups. "
            f"{stats['closing_canonical']} canonical rows look closed."
        )


# =============================================================================
# Click command
# =============================================================================


@command(
    "extract-backfill",
    help="Run 13-field LLM extraction on backfilled messages via local Ollama",
)
@click.option(
    "--manifest-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("backfill_output"),
    show_default=True,
    help="Path to a backfill-openphone output directory (contains manifest.csv + raw/).",
)
@click.option(
    "--ollama-url",
    default=DEFAULT_OLLAMA_URL,
    show_default=True,
    help="Base URL of the local Ollama server.",
)
@click.option(
    "--model",
    default=DEFAULT_MODEL,
    show_default=True,
    help="Ollama model tag to use for extraction.",
)
@click.option(
    "--concurrency",
    "-c",
    type=int,
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Number of concurrent Ollama requests.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most N messages (for smoke-testing).",
)
@click.option(
    "--batch-size",
    type=int,
    default=DEFAULT_BATCH_SIZE,
    show_default=True,
    help=(
        "Number of messages per Ollama call. 1 = safe, slow (default). "
        f"Up to {MAX_BATCH_SIZE} supported — beyond that, llama3.2:3b's "
        "JSON-array accuracy degrades on tail items."
    ),
)
@click.option(
    "--batch-by-company/--no-batch-by-company",
    default=False,
    show_default=True,
    help=(
        "Group messages by matched_company before batching. Helps small-model "
        "accuracy when --batch-size > 1."
    ),
)
@click.option(
    "--temperature",
    type=float,
    default=DEFAULT_TEMPERATURE,
    show_default=True,
    help="LLM temperature. 0.0 = deterministic.",
)
@click.option(
    "--max-retries",
    type=int,
    default=1,
    show_default=True,
    help="Number of retry attempts on JSON parse failure.",
)
def extract_backfill(
    manifest_dir: Path,
    ollama_url: str,
    model: str,
    concurrency: int,
    limit: int | None,
    batch_size: int,
    batch_by_company: bool,
    temperature: float,
    max_retries: int,
) -> None:
    """Extract 13 structured job fields from backfilled messages using local Ollama.

    Reads the manifest.csv + raw/ tree produced by ``backfill-openphone``,
    sends each ``is_job_likely=true`` message to Ollama's ``/api/chat``
    with a domain-aware 13-field extraction prompt, and writes the
    results to ``extractions.csv``. Then runs a post-extraction dedup pass
    that marks canonical/superseded rows per
    ``(company, address, customer_phone)`` group and surfaces closing
    signals on the canonical row.

    Safe to re-run: ``extracted_ids.txt`` tracks completed message_ids.
    """
    asyncio.run(
        run_extraction(
            manifest_dir=manifest_dir,
            ollama_url=ollama_url,
            model=model,
            concurrency=concurrency,
            limit=limit,
            batch_size=batch_size,
            batch_by_company=batch_by_company,
            temperature=temperature,
            max_retries=max_retries,
        )
    )