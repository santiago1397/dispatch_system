"""Company-relay parser — detects an operator's "customer no-answer" update.

When an operator replies to a company/broker's own number (not a
technician's dispatch chat) about a job that's still open, most replies are
plain acks ("ok", "ty") the pipeline correctly ignores. But some are a real
status update the pipeline currently drops on the floor entirely: the
operator reporting they tried the customer and got no answer, e.g. "Na did
not call back lef vm". Left unhandled, the Job sits in whatever status it
was already in (often still ``pending``) and — because nothing ever closes
it — eventually trips the ``closing_missing`` alert as a false positive.

This module is the OpenPhone-only counterpart to ``tech_reply_parser.py``,
but for the *company* side of the conversation rather than the *tech*
side, and scoped to exactly one signal (no-answer/still-trying → push the
Job to ``needs_follow_up``) rather than the tech parser's exhaustive intent
set — most operator-to-company remarks genuinely carry no status
information, so ``none`` here is a real no-op, not a forced guess.

Called from ``OpenPhoneService.maybe_reject_job``'s caller in
``api/routes/v1/openphone.py`` after ``maybe_reject_job`` returns ``False``
(a reject/cancel match, if any, wins first). Deliberately does NOT reuse
that path's two-operator-message "too late" cutoff — a no-answer update is
expected to land hours into a job's life, well past the reject window.
"""

import logging

from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.schemas.dispatch_job import CompanyRelayIntent
from app.services import reject_detector
from app.services.app_settings import AppSettingsService

logger = logging.getLogger(__name__)


async def maybe_apply_no_answer_update(db: AsyncSession, message) -> bool:
    """Detect a "customer no-answer/still trying" update and apply it.

    Returns ``True`` when a Job was transitioned to ``needs_follow_up``.
    Cheap regex pre-filter first (``reject_detector.mentions_customer_contact_attempt``)
    so an ordinary "ok"/"ty" ack never reaches the LLM call. Failures are
    logged, not raised — this must never break message ingestion.
    """
    body = (message.content or "").strip()
    reply_at = message.created_at
    if not body or reply_at is None:
        return False
    if not reject_detector.mentions_customer_contact_attempt(body):
        return False

    from app.repositories import job as job_repo
    from app.services.job_reference import extract_job_reference

    # Prefer the job the reply explicitly names (re-pasted PDL / phone /
    # address) over "most recent open job from this counterparty" — at
    # volume the latter mis-targets badly, since a single broker can have
    # well over a hundred jobs open at once. See ``job_reference``.
    reference = extract_job_reference(body)

    for counterparty in message.to_numbers or []:
        job = None
        matched_by = "reference"
        if reference:
            found = await job_repo.find_job_by_reference_openphone(
                db, counterparty=counterparty, before=reply_at, reference=reference
            )
            if found is not None:
                job = found[0]
        if job is None:
            matched_by = "recency"
            job = await job_repo.find_follow_up_candidate_openphone(
                db, counterparty=counterparty, before=reply_at
            )
        if job is None:
            continue

        try:
            intent = await _extract_intent(db, body)
        except Exception:
            logger.exception(
                "COMPANY_RELAY_LLM_FAILED openphone_id=%s job_id=%s",
                message.openphone_id,
                job.id,
            )
            return False

        if intent.intent != "no_answer_follow_up":
            logger.info(
                "COMPANY_RELAY_NONE openphone_id=%s job_id=%s",
                message.openphone_id,
                job.id,
            )
            return False

        from app.services.lifecycle import LifecycleService

        payload = {
            "counterparty": counterparty,
            "openphone_id": message.openphone_id,
            "body_preview": body[:120],
            "matched_by": matched_by,
        }
        if intent.follow_up_at:
            payload["follow_up_at"] = intent.follow_up_at
        if intent.notes:
            payload["notes"] = intent.notes

        try:
            await LifecycleService(db).transition(
                job=job,
                to_status="needs_follow_up",
                source=LifecycleEventSource.OPERATOR_RELAY,
                payload=payload,
                at=reply_at,
            )
        except Exception:
            logger.exception("COMPANY_RELAY_TRANSITION_FAILED job_id=%s", job.id)
            return False

        logger.info(
            "COMPANY_RELAY_APPLIED openphone_id=%s job_id=%s counterparty=%s matched_by=%s",
            message.openphone_id,
            job.id,
            counterparty,
            matched_by,
        )
        return True

    return False


async def _extract_intent(db: AsyncSession, body: str) -> CompanyRelayIntent:
    """Run the LLM extraction. Mirrors ``tech_reply_parser._extract_intent``."""
    from datetime import UTC, datetime

    llm_config = await AppSettingsService(db).get_llm_config()
    llm = ChatOpenAI(
        model=settings.AI_MODEL,
        temperature=0.0,
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
    )
    structured_llm = llm.with_structured_output(CompanyRelayIntent)

    now_iso = datetime.now(UTC).isoformat()
    prompt = (
        "You are parsing a short reply an operator sent back to a "
        "dispatch company/broker about a job that is still open. Classify "
        "into exactly one of two codes.\n\nCurrent time (UTC): "
        f"{now_iso}\n\n"
        "INTENTS:\n"
        "- no_answer_follow_up: the operator is reporting that the "
        "customer hasn't answered or called back yet, and they left a "
        "message / will keep trying ('na did not call back left vm', "
        "'no answer yet', 'still no answer', 'left a voicemail', 'tried "
        "again no pickup', 'cx not picking up').\n"
        "- none: anything else — a plain acknowledgement ('ok', 'k', "
        "'ty'), an appointment confirmation, a payment/closing relay, or "
        "unrelated chatter. Use this whenever the reply doesn't clearly "
        "report a failed contact attempt.\n\n"
        "FIELDS:\n"
        "- follow_up_at (only for no_answer_follow_up): the ISO-8601 time "
        "to try the customer again, computed from the current time above. "
        "Estimate when not explicit (~+30min default).\n"
        "- notes: any extra detail (e.g. 'left voicemail'). Omit if none.\n\n"
        f"Reply:\n{body[:500]}\n"
    )

    return await structured_llm.ainvoke(prompt)
