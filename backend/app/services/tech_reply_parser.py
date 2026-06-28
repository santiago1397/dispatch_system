"""Tech-reply parser — translates a technician's WhatsApp reply into a lifecycle transition.

When the operator posts a dispatch in a technician's chat (``chat_role='tech_dispatch'``)
and the tech replies, we:

1. Resolve the target Job the reply is about.
   - Prefer ``WhatsappMessage.quoted_wa_message_id`` (the tech used the
     "reply" affordance on the dispatch).
   - Fall back to the most-recent ``is_from_me=True`` dispatch in the
     same chat within the last 60 minutes. If more than one candidate,
     emit ``source='ambiguous_attribution'`` so the alert engine surfaces
     a row the operator resolves from ``/jobs/[id]``.

2. Run an LLM extraction over the reply body with
   ``with_structured_output(TechReplyIntent)``. The prompt explicitly
   asks the model to pick ``needs_follow_up`` when ambiguous so a
   terse "ok" doesn't silently flip state.

3. Map intent → ``LifecycleStatus`` and call
   ``LifecycleService.transition``. The transition writes the audit
   event, stamps ``jobs.lifecycle_status``, and creates the outbound
   draft for the operator to send.

The parser opens a fresh DB session (``parse_tech_reply_in_background``)
mirroring the ``_classify_in_background`` pattern in
``app/services/whatsapp.py`` — the ingest_batch session is already
committed by the time the parser runs.
"""

import logging
from datetime import UTC, datetime, timedelta

from langchain_openai import ChatOpenAI
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.db.models.whatsapp import WhatsappMessage
from app.repositories import alert as alert_repo
from app.schemas.dispatch_job import TechReplyIntent, TechReplyIntentCode
from app.services.app_settings import AppSettingsService
from app.services.lifecycle import LifecycleStatus

logger = logging.getLogger(__name__)

# Window for fallback attribution when no quote is present. Two operators
# dispatching in the same chat back-to-back is rare but possible; 60
# minutes is the cap before we surface ``ambiguous_attribution``.
ATTRIBUTION_WINDOW_MINUTES = 60

# Map from TechReplyIntentCode → LifecycleStatus value. Centralized here
# so the LLM extraction schema and the state machine stay in sync — add
# a new intent in BOTH ``schemas/dispatch_job.py:TechReplyIntentCode``
# AND this dict.
_INTENT_TO_STATUS: dict[str, LifecycleStatus] = {
    "in_progress": LifecycleStatus.IN_PROGRESS,
    "appt_set": LifecycleStatus.APPT_SET,
    "needs_follow_up": LifecycleStatus.NEEDS_FOLLOW_UP,
    "canceled": LifecycleStatus.CANCELED,
}


def _map_intent_to_status(intent: TechReplyIntentCode) -> LifecycleStatus:
    """Map a TechReplyIntent code to its LifecycleStatus.

    The mapping is exhaustive (``TechReplyIntentCode`` is a closed enum),
    so this never raises ``KeyError`` in practice.
    """
    return _INTENT_TO_STATUS[intent]


async def parse_tech_reply(
    db: AsyncSession,
    *,
    wa_message: WhatsappMessage,
) -> tuple[str, str | None]:
    """Parse a tech reply into a lifecycle transition.

    Steps:
    1. Resolve the target dispatch:
       - If ``wa_message.quoted_wa_message_id`` is set, find the operator
         dispatch it points at.
       - Otherwise find the most recent ``is_from_me=True`` message in
         the same chat within ``ATTRIBUTION_WINDOW_MINUTES``.
       - If multiple candidates match the fallback, abort with
         ``ambiguous_attribution`` (the alert engine raises an alert).
    2. Run the LLM to extract ``TechReplyIntent``.
    3. Map intent → ``LifecycleStatus`` and call
       ``LifecycleService.transition``.

    Returns ``(source, intent_or_None)`` for logging:
    - ``source``: ``tech_whatsapp`` or ``ambiguous_attribution``.
    - ``intent``: the extracted intent code, or ``None`` if attribution
      failed.

    Failures are logged, not raised — a bad parse must not crash the
    background task.
    """
    # 1. Resolve the target dispatch.
    target_dispatch_event = await _resolve_target_dispatch(db, wa_message)

    if target_dispatch_event is None:
        logger.warning(
            "TECH_REPLY_NO_TARGET chat_jid=%s wa_message_id=%s body=%r",
            wa_message.chat_jid,
            wa_message.wa_message_id,
            (wa_message.body or "")[:80],
        )
        return "no_target", None

    # If multiple fallback candidates, emit ambiguous_attribution alert and
    # abort the transition. The operator resolves from the dropdown.
    if isinstance(target_dispatch_event, list):
        await alert_repo.create_or_get_open(
            db,
            kind="unattributed_reply",
            chat_jid=wa_message.chat_jid,
            payload={
                "wa_message_id": wa_message.wa_message_id,
                "body_preview": (wa_message.body or "")[:120],
                "candidate_event_ids": [str(e.id) for e in target_dispatch_event],
                "candidate_job_ids": [str(e.job_id) for e in target_dispatch_event],
            },
        )
        logger.warning(
            "TECH_REPLY_AMBIGUOUS chat_jid=%s wa_message_id=%s candidates=%d",
            wa_message.chat_jid,
            wa_message.wa_message_id,
            len(target_dispatch_event),
        )
        return "ambiguous_attribution", None

    # 2. Resolve the target Job.
    from app.repositories import job as job_repo

    job = await job_repo.get_job_by_id(db, target_dispatch_event.job_id)
    if job is None:
        logger.error(
            "TECH_REPLY_TARGET_JOB_MISSING event_id=%s job_id=%s",
            target_dispatch_event.id,
            target_dispatch_event.job_id,
        )
        return "no_target", None

    # 3. Run the LLM.
    try:
        intent = await _extract_intent(db, wa_message.body or "")
    except Exception:
        logger.exception(
            "TECH_REPLY_LLM_FAILED chat_jid=%s wa_message_id=%s",
            wa_message.chat_jid,
            wa_message.wa_message_id,
        )
        return "tech_whatsapp", None

    target_status = _map_intent_to_status(intent.intent)
    logger.info(
        "TECH_REPLY_PARSED chat_jid=%s wa_message_id=%s intent=%s job_id=%s",
        wa_message.chat_jid,
        wa_message.wa_message_id,
        intent.intent,
        job.id,
    )

    # 4. Run the lifecycle transition. The dispatch event's payload is
    # carried through so an audit trail of "tech said this on this msg"
    # is preserved.
    from app.services.lifecycle import LifecycleService

    payload = {
        "wa_message_id": wa_message.wa_message_id,
        "chat_jid": wa_message.chat_jid,
        "intent": intent.intent,
    }
    if intent.appt_iso:
        payload["appt_iso"] = intent.appt_iso
    if intent.notes:
        payload["notes"] = intent.notes

    try:
        event_id = await LifecycleService(db).transition(
            job=job,
            to_status=target_status,
            source=LifecycleEventSource.TECH_WHATSAPP,
            payload=payload,
        )
    except Exception:
        logger.exception(
            "TECH_REPLY_TRANSITION_FAILED job_id=%s intent=%s",
            job.id,
            intent.intent,
        )
        return "tech_whatsapp", intent.intent

    # Stamp the IncomingMessage row that mirrors this WhatsApp message
    # with the resulting lifecycle_event_id so the message detail view
    # shows the transition it triggered.
    from sqlalchemy import update

    from app.db.models.openphone import IncomingMessage

    await db.execute(
        update(IncomingMessage)
        .where(
            and_(
                IncomingMessage.source == "whatsapp",
                IncomingMessage.raw_payload["chat_jid"].astext == wa_message.chat_jid,
                IncomingMessage.raw_payload["wa_message_id"].astext == wa_message.wa_message_id,
            )
        )
        .values(lifecycle_event_id=event_id)
    )

    return "tech_whatsapp", intent.intent


async def parse_tech_reply_in_background(
    *,
    wa_message_id: str,
    chat_jid: str,
    batch_id: str,
) -> None:
    """Background-task entry point — mirrors ``_classify_in_background``.

    Opens a fresh DB session (the request session is already closed by
    the time Starlette fires the background task) and parses the reply.
    Failures are logged, not raised.
    """
    from app.db.session import get_db_context

    logger.info(
        "TECH_REPLY_BG_ENTER chat_jid=%s wa_message_id=%s batch_id=%s",
        chat_jid,
        wa_message_id,
        batch_id,
    )
    try:
        async with get_db_context() as db:
            wa_message = await _fetch_message(db, chat_jid, wa_message_id)
            if wa_message is None:
                logger.warning(
                    "TECH_REPLY_BG_NOT_FOUND chat_jid=%s wa_message_id=%s",
                    chat_jid,
                    wa_message_id,
                )
                return
            source, intent = await parse_tech_reply(db, wa_message=wa_message)
            await db.commit()
            logger.info(
                "TECH_REPLY_BG_DONE chat_jid=%s wa_message_id=%s source=%s intent=%s",
                chat_jid,
                wa_message_id,
                source,
                intent,
            )
    except Exception as exc:
        logger.error(
            "TECH_REPLY_BG_FAILED chat_jid=%s wa_message_id=%s exc_type=%s exc=%r",
            chat_jid,
            wa_message_id,
            type(exc).__name__,
            exc,
        )
        logger.exception(
            "Background tech-reply parse failed for %s in %s",
            wa_message_id,
            chat_jid,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_message(
    db: AsyncSession,
    chat_jid: str,
    wa_message_id: str,
) -> WhatsappMessage | None:
    """Find a single WhatsApp message by its (chat_jid, wa_message_id) key."""
    query = select(WhatsappMessage).where(
        and_(
            WhatsappMessage.chat_jid == chat_jid,
            WhatsappMessage.wa_message_id == wa_message_id,
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _resolve_target_dispatch(
    db: AsyncSession,
    wa_message: WhatsappMessage,
) -> object | list | None:
    """Resolve the dispatch event the tech reply is about.

    Returns:
    - A single ``JobLifecycleEvent`` if attribution succeeds.
    - A ``list`` of events if multiple fallback candidates match (caller
      emits ``ambiguous_attribution`` alert).
    - ``None`` if no candidates at all (caller logs and returns).

    Strategy:
    1. If ``wa_message.quoted_wa_message_id`` is set, look up the
       dispatch event whose ``payload.wa_message_id`` matches.
    2. Otherwise find ``is_from_me=True`` messages in this chat within
       ``ATTRIBUTION_WINDOW_MINUTES`` and look up the dispatch event
       whose ``payload.wa_message_id`` matches each. The most recent
       one wins; multiple candidates → ``ambiguous_attribution``.
    """
    if wa_message.quoted_wa_message_id:
        # Path 1: explicit quote.
        event = await _event_for_dispatch_msg(
            db,
            wa_message.chat_jid,
            wa_message.quoted_wa_message_id,
        )
        return event

    # Path 2: fallback — most recent operator dispatch in this chat.
    cutoff = datetime.now(UTC) - timedelta(minutes=ATTRIBUTION_WINDOW_MINUTES)
    query = (
        select(WhatsappMessage)
        .where(
            and_(
                WhatsappMessage.chat_jid == wa_message.chat_jid,
                WhatsappMessage.is_from_me.is_(True),
                WhatsappMessage.timestamp >= cutoff,
            )
        )
        .order_by(WhatsappMessage.timestamp.desc())
    )
    candidates = list((await db.execute(query)).scalars().all())
    if not candidates:
        return None

    # For each candidate operator message, find the matching dispatch
    # event (whose payload carries that wa_message_id). If exactly one
    # yields an event, use it. If multiple yield events, ambiguous.
    matching_events: list = []
    for candidate_msg in candidates:
        event = await _event_for_dispatch_msg(
            db,
            wa_message.chat_jid,
            candidate_msg.wa_message_id,
        )
        if event is not None:
            matching_events.append(event)

    if len(matching_events) == 1:
        return matching_events[0]
    if len(matching_events) > 1:
        return matching_events
    return None


async def _event_for_dispatch_msg(
    db: AsyncSession,
    chat_jid: str,
    wa_message_id: str,
) -> object | None:
    """Find the JobLifecycleEvent triggered by an operator dispatch msg.

    Operator dispatches write an event with
    ``payload.wa_message_id == <the operator msg id>`` and
    ``payload.chat_jid == <chat>``. We use Postgres JSONB containment
    via ``@>``.
    """
    from app.db.models.job_lifecycle_event import JobLifecycleEvent

    query = select(JobLifecycleEvent).where(
        and_(
            JobLifecycleEvent.source == LifecycleEventSource.OPERATOR_WHATSAPP,
            JobLifecycleEvent.payload["chat_jid"].astext == chat_jid,
            JobLifecycleEvent.payload["wa_message_id"].astext == wa_message_id,
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _extract_intent(db: AsyncSession, body: str) -> TechReplyIntent:
    """Run the LLM extraction.

    Mirrors the ``with_structured_output`` pattern used elsewhere
    (see ``app/services/classification.py:_extract_fields``).
    """
    llm_config = await AppSettingsService(db).get_llm_config()
    llm = ChatOpenAI(
        model=settings.AI_MODEL,
        temperature=0.0,
        base_url=llm_config.base_url,
        api_key=llm_config.api_key,
    )
    structured_llm = llm.with_structured_output(TechReplyIntent)

    prompt = (
        "You are parsing a short WhatsApp reply from a technician about "
        "a dispatched job. Read the reply and classify the technician's "
        "intent.\n\n"
        "Possible intents:\n"
        "- in_progress: technician is on the way / heading to the job now\n"
        "- appt_set: technician set an appointment, includes a specific time\n"
        "- needs_follow_up: ambiguous, partial, or requesting more info; "
        "operator should review\n"
        "- canceled: job is canceled (customer not home, customer declined, "
        "etc.)\n\n"
        "Choose exactly one intent. If the reply is short, partial, or "
        "could mean more than one thing (e.g. 'ok', 'k', 'will do', '?'), "
        "alone), choose 'needs_follow_up' — the operator reviews "
        "ambiguous replies manually instead of silently flipping state.\n\n"
        "For 'appt_set', extract the appointment time into 'appt_iso'. "
        "If you can parse it into ISO-8601 (e.g. '2026-06-28T15:00:00-05:00'), "
        "do so. Otherwise copy the technician's phrase verbatim "
        "(e.g. 'tomorrow 3pm').\n\n"
        "Use 'notes' for any extra detail (ETA, parts needed, customer "
        "unavailable, etc.). Omit fields that aren't present.\n\n"
        f"Reply:\n{body[:2000]}\n"
    )

    return await structured_llm.ainvoke(prompt)
