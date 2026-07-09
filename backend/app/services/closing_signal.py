"""Closing-signal gate — detect a tech's payment re-paste and mark the Job ``completed``.

When a job is done, the technician frequently re-pastes the original job
message (same address + phone) into a chat and appends settlement info at
the bottom — "Paid $600 cash", "Tech parts:$36.60", "Close 240 cash",
"4100$cc". These land in *any* tracked chat, on either channel (WhatsApp or
OpenPhone), so this gate runs at the top of both ingest paths, before the
normal ``chat_role`` routing / classification.

When it fires it transitions the matched Job to ``completed`` and the caller
short-circuits all downstream handling — otherwise the re-pasted address
would spawn a spurious linked ``DispatchJob`` via the dedup pipeline.

``completed`` means "work done + payment reported by the tech". It is
distinct from the terminal ``closed``, which only the closing pipeline
(``source='closing_chat'``) may reach when the operator files the totals in
the "Dispatch Closing" WhatsApp group. A Job that is stuck in ``completed``
past ``ALERTS_CLOSING_RELAY_UNSENT_MINUTES`` is what the ``closing_unfiled``
alert watches for.

The gate is deliberately cheap and LLM-free: a regex token check plus a
company-agnostic address+phone lookup. The authoritative amounts still come
from the operator's Dispatch Closing post (``_process_closing_message``); we
only store the raw text + matched tokens on the lifecycle event for audit.
See ``memory/feedback_no_outbound_automation.md``.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.repositories import job as job_repo
from app.services.lifecycle import LifecycleService, LifecycleStatus

logger = logging.getLogger(__name__)

# 14-day window, matching the dedup/closing pipeline.
CLOSING_SIGNAL_WINDOW_DAYS = 14

# Statuses the gate must NOT transition. ``completed`` is skipped for
# idempotency (a re-pasted signal must not reset the 15-min clock); the
# terminal set is skipped because a closed/canceled/rejected job is done —
# a payment token landing on one is noise, not a fresh completion.
_NON_COMPLETABLE = {
    LifecycleStatus.COMPLETED.value,
    LifecycleStatus.CLOSED.value,
    LifecycleStatus.CANCELED.value,
    LifecycleStatus.REJECTED.value,
}

# Settlement vocabulary. A closing signal needs a payment KEYWORD *and* an
# AMOUNT — both are present in every real example, and requiring both keeps
# a bare address re-paste (operator dispatch) from tripping the gate.
# NOTE: "check" (the payment method) is deliberately excluded — it collides
# with the common dispatch verb ("check the meter") and wasn't among the
# operator's real closing examples. The existing-Job match is the real guard,
# but keeping the vocabulary tight avoids needless gate entries.
_KEYWORD_RE = re.compile(
    r"\b(paid|pay|parts?|tip|cash|cc|zelle|venmo|card|charged|collected|total|closed?)\b",
    re.IGNORECASE,
)
# ``$600`` / ``600$`` / ``325.5`` / ``36.60`` / ``4100`` — a currency-marked
# amount, or a bare number of 2+ digits (so a lone "1" doesn't count).
_AMOUNT_RE = re.compile(r"\$\s?\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s?\$|\b\d{2,}(?:[.,]\d+)?\b")


def detect_payment_tokens(body: str) -> list[str] | None:
    """Return the matched settlement tokens, or ``None`` if not a closing signal.

    A message qualifies when it contains at least one payment keyword AND at
    least one amount token. The returned list (keyword + amount matches) is
    stored on the lifecycle event payload for audit.
    """
    text = body or ""
    keywords = _KEYWORD_RE.findall(text)
    if not keywords:
        return None
    amounts = _AMOUNT_RE.findall(text)
    if not amounts:
        return None
    return [*(k.lower() for k in keywords), *amounts]


class ClosingSignalService:
    """Detect a tech payment re-paste and transition the matched Job to ``completed``."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def detect_and_complete(
        self,
        *,
        body: str,
        channel: str,
        source_meta: dict,
    ) -> bool:
        """Detect a closing signal and complete the matched Job.

        Returns ``True`` when the message was a closing signal that the gate
        handled — either by transitioning a non-terminal Job to ``completed``
        or by intentionally dropping a re-paste that matched an
        already-``completed``/terminal Job. In both cases the caller must
        short-circuit (skip tech-reply / reject / classification) so the
        re-paste never spawns a linked ``DispatchJob``.

        Returns ``False`` when the message is not a closing signal, or carries
        no address/phone to attribute, or matches no Job in the window — the
        caller then falls through to today's behavior unchanged (silent
        no-match per the design).

        Never raises — a detection failure must not break ingestion.
        """
        # Lazy import to avoid a module-level cycle (classification imports
        # nothing from here, but keep the boundary clean).
        from app.services.address_normalizer import normalize_address, normalize_phone
        from app.services.classification import PHONE_PATTERN

        tokens = detect_payment_tokens(body)
        if tokens is None:
            return False

        normalized = normalize_address(body)
        phone_match = PHONE_PATTERN.search(body or "")
        phone_e164 = normalize_phone(phone_match.group(0)) if phone_match else None

        has_address = bool(normalized.street_number and normalized.street_name)
        if not has_address and not phone_e164:
            # Payment tokens but nothing to attribute (e.g. a bare "Paid $100"
            # with no re-pasted job) — fall through, don't guess.
            return False

        since = datetime.now(UTC) - timedelta(days=CLOSING_SIGNAL_WINDOW_DAYS)
        job = await job_repo.find_open_by_address_phone(
            self.db,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            customer_phone_e164=phone_e164,
            since=since,
        )
        if job is None:
            return False

        if job.lifecycle_status in _NON_COMPLETABLE:
            # Matched an existing closing/terminal job — this is a duplicate
            # re-paste. Drop it (short-circuit) so classification doesn't
            # re-link it, but write no second transition.
            logger.info(
                "CLOSING_SIGNAL_DROP channel=%s job_id=%s status=%s meta=%s",
                channel,
                job.id,
                job.lifecycle_status,
                source_meta,
            )
            return True

        prev_status = job.lifecycle_status
        try:
            await LifecycleService(self.db).transition(
                job=job,
                to_status=LifecycleStatus.COMPLETED,
                source=LifecycleEventSource.CLOSING_SIGNAL,
                payload={
                    "channel": channel,
                    "tokens": tokens,
                    "raw": (body or "")[:500],
                    **source_meta,
                },
            )
        except Exception:
            logger.exception(
                "CLOSING_SIGNAL_TRANSITION_FAILED channel=%s job_id=%s meta=%s",
                channel,
                job.id,
                source_meta,
            )
            # Treat as handled anyway: the message is a closing re-paste, so
            # letting it fall through to classification would spawn a linked
            # DispatchJob. Short-circuit; the 24h closing_missing alert still
            # backstops a job that never reaches completed.
            return True

        logger.info(
            "CLOSING_SIGNAL_COMPLETED channel=%s job_id=%s from_status=%s tokens=%s meta=%s",
            channel,
            job.id,
            prev_status,
            tokens,
            source_meta,
        )
        return True
