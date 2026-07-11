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
from app.repositories import openphone_repo, whatsapp_repo
from app.schemas.dispatch_job import TechReplyIntent, TechReplyIntentCode
from app.services import reject_detector
from app.services.app_settings import AppSettingsService
from app.services.lifecycle import LifecycleStatus

# Tech accept/reject only counts when the reply directly quotes the dispatch
# or falls within the technician's next two messages after it.
TECH_DECISION_MAX_MESSAGES = 2

logger = logging.getLogger(__name__)

# Window for fallback attribution when no quote is present. Must cover
# how long a dispatched job can realistically sit before the tech reports
# back (a tech may drive out, find the site a no-go, and reply hours
# later) — matched to ``ALERTS_STUCK_DISPATCHED_MINUTES`` (4h) so a
# same-day update never falls outside the window and silently no-ops.
# Two operators dispatching in the same chat within the window is rare
# but possible; that's what ``ambiguous_attribution`` is for.
ATTRIBUTION_WINDOW_MINUTES = 240

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

    # 2b. Deterministic accept/reject — phrases win over the LLM. Honored
    # when the reply directly quotes the dispatch OR lands within the tech's
    # next two messages; otherwise fall through to the LLM intent parser.
    decision = await _apply_tech_decision_whatsapp(db, wa_message, target_dispatch_event, job)
    if decision is not None:
        return decision

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
    if intent.follow_up_at:
        payload["follow_up_at"] = intent.follow_up_at
    if intent.reason:
        payload["reason"] = intent.reason
    if intent.notes:
        payload["notes"] = intent.notes

    try:
        event_id = await LifecycleService(db).transition(
            job=job,
            to_status=target_status,
            source=LifecycleEventSource.TECH_WHATSAPP,
            payload=payload,
            at=wa_message.timestamp,
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

    await _relay_company_update(db, job=job, intent=intent, event_id=event_id)
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
# OpenPhone (Quo) tech-reply path
# ---------------------------------------------------------------------------
#
# OpenPhone has no "quote" affordance, so a tech reply can only be attributed
# to a dispatch by the time-window fallback: the most-recent operator dispatch
# to this tech's phone within ATTRIBUTION_WINDOW_MINUTES. Operator dispatches
# on OpenPhone write a ``JobLifecycleEvent`` with ``source=OPERATOR_OPENPHONE``
# and ``payload.phone_e164 == <tech phone>`` (see
# ``services/openphone.py:_handle_operator_dispatch``).


async def parse_openphone_tech_reply(
    db: AsyncSession,
    *,
    incoming_message,
    technician,
) -> tuple[str, str | None]:
    """Parse an inbound OpenPhone tech reply into a lifecycle transition.

    Mirrors ``parse_tech_reply`` but resolves the target dispatch via the
    time-window fallback only (no quotes on OpenPhone). Returns
    ``(source, intent_or_None)`` for logging. Failures are logged, not
    raised.
    """
    phone = technician.phone_e164
    target = await _resolve_openphone_target_dispatch(db, technician_phone_e164=phone)

    if target is None:
        logger.warning(
            "OP_TECH_REPLY_NO_TARGET phone=%s openphone_id=%s body=%r",
            phone,
            incoming_message.openphone_id,
            (incoming_message.content or "")[:80],
        )
        return "no_target", None

    # Multiple distinct dispatches to this tech in the window → ambiguous.
    if isinstance(target, list):
        await alert_repo.create_or_get_open(
            db,
            kind="unattributed_reply",
            chat_jid=f"openphone:{phone}",
            payload={
                "openphone_id": incoming_message.openphone_id,
                "body_preview": (incoming_message.content or "")[:120],
                "candidate_event_ids": [str(e.id) for e in target],
                "candidate_job_ids": [str(e.job_id) for e in target],
            },
        )
        logger.warning(
            "OP_TECH_REPLY_AMBIGUOUS phone=%s openphone_id=%s candidates=%d",
            phone,
            incoming_message.openphone_id,
            len(target),
        )
        return "ambiguous_attribution", None

    from app.repositories import job as job_repo

    job = await job_repo.get_job_by_id(db, target.job_id)
    if job is None:
        logger.error(
            "OP_TECH_REPLY_TARGET_JOB_MISSING event_id=%s job_id=%s",
            target.id,
            target.job_id,
        )
        return "no_target", None

    # Deterministic accept/reject — phrases win over the LLM.
    decision = await _apply_tech_decision_openphone(db, incoming_message, phone, target, job)
    if decision is not None:
        return decision

    try:
        intent = await _extract_intent(db, incoming_message.content or "")
    except Exception:
        logger.exception(
            "OP_TECH_REPLY_LLM_FAILED phone=%s openphone_id=%s",
            phone,
            incoming_message.openphone_id,
        )
        return "tech_openphone", None

    target_status = _map_intent_to_status(intent.intent)
    logger.info(
        "OP_TECH_REPLY_PARSED phone=%s openphone_id=%s intent=%s job_id=%s",
        phone,
        incoming_message.openphone_id,
        intent.intent,
        job.id,
    )

    payload = {
        "openphone_id": incoming_message.openphone_id,
        "phone_e164": phone,
        "intent": intent.intent,
    }
    if intent.appt_iso:
        payload["appt_iso"] = intent.appt_iso
    if intent.follow_up_at:
        payload["follow_up_at"] = intent.follow_up_at
    if intent.reason:
        payload["reason"] = intent.reason
    if intent.notes:
        payload["notes"] = intent.notes

    from app.services.lifecycle import LifecycleService

    try:
        event_id = await LifecycleService(db).transition(
            job=job,
            to_status=target_status,
            source=LifecycleEventSource.TECH_OPENPHONE,
            payload=payload,
            at=incoming_message.created_at,
        )
    except Exception:
        logger.exception(
            "OP_TECH_REPLY_TRANSITION_FAILED job_id=%s intent=%s",
            job.id,
            intent.intent,
        )
        return "tech_openphone", intent.intent

    # Point the IncomingMessage at the event it triggered (detail view).
    incoming_message.lifecycle_event_id = event_id
    db.add(incoming_message)

    await _relay_company_update(db, job=job, intent=intent, event_id=event_id)
    return "tech_openphone", intent.intent


async def parse_openphone_tech_reply_in_background(
    *,
    incoming_message_id,
) -> None:
    """Background entry point — opens a fresh session, parses the reply.

    Mirrors ``parse_tech_reply_in_background``. Resolves the technician
    from the message's ``from_number``. Failures are logged, not raised.
    """
    from app.db.session import get_db_context
    from app.repositories import openphone_repo
    from app.repositories import technician as technician_repo

    logger.info("OP_TECH_REPLY_BG_ENTER incoming_id=%s", incoming_message_id)
    try:
        async with get_db_context() as db:
            msg = await openphone_repo.get_incoming_message(db, incoming_message_id)
            if msg is None:
                logger.warning("OP_TECH_REPLY_BG_NOT_FOUND incoming_id=%s", incoming_message_id)
                return
            technician = await technician_repo.get_by_phone_e164(db, msg.from_number)
            if technician is None:
                logger.warning(
                    "OP_TECH_REPLY_BG_NO_TECH incoming_id=%s from=%s",
                    incoming_message_id,
                    msg.from_number,
                )
                return
            source, intent = await parse_openphone_tech_reply(
                db, incoming_message=msg, technician=technician
            )
            await db.commit()
            logger.info(
                "OP_TECH_REPLY_BG_DONE incoming_id=%s source=%s intent=%s",
                incoming_message_id,
                source,
                intent,
            )
    except Exception as exc:
        logger.error(
            "OP_TECH_REPLY_BG_FAILED incoming_id=%s exc_type=%s exc=%r",
            incoming_message_id,
            type(exc).__name__,
            exc,
        )
        logger.exception("Background OpenPhone tech-reply parse failed for %s", incoming_message_id)


async def _resolve_openphone_target_dispatch(
    db: AsyncSession,
    *,
    technician_phone_e164: str | None,
    now: datetime | None = None,
) -> object | list | None:
    """Resolve the OpenPhone dispatch a tech reply is about (window fallback).

    Returns a single ``JobLifecycleEvent`` (one distinct job dispatched in
    the window), a ``list`` of representative events (multiple distinct
    jobs → ambiguous), or ``None`` (no dispatch in the window).
    """
    from app.db.models.job_lifecycle_event import JobLifecycleEvent

    if not technician_phone_e164:
        return None

    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=ATTRIBUTION_WINDOW_MINUTES)
    query = (
        select(JobLifecycleEvent)
        .where(
            and_(
                JobLifecycleEvent.source == LifecycleEventSource.OPERATOR_OPENPHONE,
                JobLifecycleEvent.payload["phone_e164"].astext == technician_phone_e164,
                JobLifecycleEvent.created_at >= cutoff,
            )
        )
        .order_by(JobLifecycleEvent.created_at.desc())
    )
    events = list((await db.execute(query)).scalars().all())
    if not events:
        return None

    # Most-recent event per distinct job (events are desc-ordered, so the
    # first seen for each job_id is the newest).
    distinct: dict = {}
    for ev in events:
        distinct.setdefault(ev.job_id, ev)

    representatives = list(distinct.values())
    if len(representatives) == 1:
        return representatives[0]
    return representatives


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


async def _relay_company_update(db: AsyncSession, *, job, intent, event_id) -> None:
    """Create the pending operator→company relay for a qualifying update.

    Fire-and-log: a relay failure must never break the tech-reply
    transition that already committed the state change.
    """
    from app.services.company_update import RELAYED_UPDATE_KINDS, CompanyUpdateService

    if intent.intent not in RELAYED_UPDATE_KINDS:
        return
    try:
        await CompanyUpdateService(db).create_for_update(
            job=job,
            update_kind=intent.intent,
            lifecycle_event_id=event_id,
            reason=intent.reason,
            notes=intent.notes,
        )
    except Exception:
        logger.exception("COMPANY_UPDATE_FAILED job_id=%s kind=%s", job.id, intent.intent)


async def _apply_tech_decision_whatsapp(
    db: AsyncSession,
    wa_message: WhatsappMessage,
    dispatch_event,
    job,
) -> tuple[str, str] | None:
    """Apply a deterministic tech accept/reject for a WhatsApp reply.

    Returns ``(source, intent)`` when the reply is a standalone accept/reject
    within the window (the transition is already applied), or ``None`` to
    fall through to the LLM intent parser. Reject is checked first so a
    phrase can't be read as both. Accept → ``accepted``; reject →
    ``pending`` (the job becomes re-dispatchable to another tech).
    """
    body = wa_message.body or ""
    is_reject = reject_detector.is_tech_reject(body)
    is_accept = reject_detector.is_tech_accept(body)
    if not (is_reject or is_accept):
        return None

    # A direct quote always counts; otherwise the reply must be within the
    # tech's next two messages after the dispatch.
    if not wa_message.quoted_wa_message_id:
        tech_msgs = await whatsapp_repo.count_tech_messages_between(
            db,
            chat_jid=wa_message.chat_jid,
            after=dispatch_event.created_at,
            until=wa_message.timestamp,
        )
        if tech_msgs > TECH_DECISION_MAX_MESSAGES:
            logger.info(
                "TECH_DECISION_TOO_LATE chat_jid=%s wa_message_id=%s tech_msgs=%d",
                wa_message.chat_jid,
                wa_message.wa_message_id,
                tech_msgs,
            )
            return None

    intent_code = "tech_rejected" if is_reject else "accepted"
    target_status = LifecycleStatus.PENDING if is_reject else LifecycleStatus.ACCEPTED
    payload = {
        "wa_message_id": wa_message.wa_message_id,
        "chat_jid": wa_message.chat_jid,
        "intent": intent_code,
    }

    from app.services.lifecycle import LifecycleService

    try:
        event_id = await LifecycleService(db).transition(
            job=job,
            to_status=target_status,
            source=LifecycleEventSource.TECH_WHATSAPP,
            payload=payload,
            at=wa_message.timestamp,
        )
    except Exception:
        logger.exception("TECH_DECISION_TRANSITION_FAILED job_id=%s intent=%s", job.id, intent_code)
        return "tech_whatsapp", intent_code

    # Stamp the mirror IncomingMessage with the event it triggered.
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
    logger.info(
        "TECH_DECISION chat_jid=%s wa_message_id=%s intent=%s job_id=%s status=%s",
        wa_message.chat_jid,
        wa_message.wa_message_id,
        intent_code,
        job.id,
        target_status.value,
    )
    return "tech_whatsapp", intent_code


async def _apply_tech_decision_openphone(
    db: AsyncSession,
    incoming_message,
    technician_phone_e164: str | None,
    dispatch_event,
    job,
) -> tuple[str, str] | None:
    """OpenPhone twin of :func:`_apply_tech_decision_whatsapp`.

    No quote affordance exists on Quo, so the window is always the tech's
    next two inbound messages after the dispatch.
    """
    body = incoming_message.content or ""
    is_reject = reject_detector.is_tech_reject(body)
    is_accept = reject_detector.is_tech_accept(body)
    if not (is_reject or is_accept):
        return None

    if technician_phone_e164:
        inbound = await openphone_repo.count_inbound_messages_from(
            db,
            from_number=technician_phone_e164,
            after=dispatch_event.created_at,
            until=incoming_message.created_at,
        )
        if inbound > TECH_DECISION_MAX_MESSAGES:
            logger.info(
                "OP_TECH_DECISION_TOO_LATE phone=%s openphone_id=%s inbound=%d",
                technician_phone_e164,
                incoming_message.openphone_id,
                inbound,
            )
            return None

    intent_code = "tech_rejected" if is_reject else "accepted"
    target_status = LifecycleStatus.PENDING if is_reject else LifecycleStatus.ACCEPTED
    payload = {
        "openphone_id": incoming_message.openphone_id,
        "phone_e164": technician_phone_e164,
        "intent": intent_code,
    }

    from app.services.lifecycle import LifecycleService

    try:
        event_id = await LifecycleService(db).transition(
            job=job,
            to_status=target_status,
            source=LifecycleEventSource.TECH_OPENPHONE,
            payload=payload,
            at=incoming_message.created_at,
        )
    except Exception:
        logger.exception(
            "OP_TECH_DECISION_TRANSITION_FAILED job_id=%s intent=%s", job.id, intent_code
        )
        return "tech_openphone", intent_code

    incoming_message.lifecycle_event_id = event_id
    db.add(incoming_message)
    logger.info(
        "OP_TECH_DECISION phone=%s openphone_id=%s intent=%s job_id=%s status=%s",
        technician_phone_e164,
        incoming_message.openphone_id,
        intent_code,
        job.id,
        target_status.value,
    )
    return "tech_openphone", intent_code


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

    now_iso = datetime.now(UTC).isoformat()
    prompt = (
        "You are parsing a short reply from a technician about a dispatched "
        "job. Classify the technician's intent into exactly one of four "
        f"codes.\n\nCurrent time (UTC): {now_iso}\n\n"
        "INTENTS:\n"
        "- in_progress: tech is on the way / heading over now / working it "
        "('on way', 'tech on way', 'in progress', 'omw').\n"
        "- appt_set: an appointment/time was set, or the customer wants a "
        "specific time ('appt for 3pm', 'customer wants tomorrow morning').\n"
        "- needs_follow_up: the deal is still open and the operator must call "
        "the customer back later. Use this for price-shopping, 'gave price, "
        "customer will call back', 'wants a call in a few minutes', 'wants a "
        "call later', and genuinely ambiguous replies.\n"
        "- canceled: the job is dead / no service will happen — 'gave price "
        "and customer refused', 'customer doesn't need service', 'DNS', "
        "'solved it themselves', 'customer not home and won't reschedule'.\n\n"
        "FIELDS:\n"
        "- appt_iso (only for appt_set): the appointment time in ISO-8601 "
        "computed from the current time above (e.g. '2026-07-07T15:00:00'). "
        "If only a date is known, use midnight of that date. If truly "
        "unparseable, copy the tech's phrase verbatim ('tomorrow morning').\n"
        "- follow_up_at (only for needs_follow_up): the ISO-8601 time to call "
        "the customer back, computed from the current time above. Estimate "
        "when not explicit: 'in a few minutes'≈+15min, 'in a bit'≈+30min, "
        "'later'/'this afternoon'≈+2h, price-shopping / 'will call back' with "
        "no time≈+30min. Always produce a concrete ISO time when the intent "
        "is needs_follow_up.\n"
        "- reason (short code, mainly for canceled and needs_follow_up): one "
        "of refused, dns, solved, no_service, priceshopping, will_cb, "
        "callback — or omit if none fit.\n"
        "- notes: any extra detail (ETA, parts, customer unavailable). Omit "
        "fields that don't apply.\n\n"
        f"Reply:\n{body[:2000]}\n"
    )

    return await structured_llm.ainvoke(prompt)
