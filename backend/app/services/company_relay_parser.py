"""Company-relay parser — turns an operator's update to a broker into a transition.

When an operator replies to a company/broker's own number (not a
technician's dispatch chat), most replies are plain acks ("ok", "ty") the
pipeline correctly ignores. But some are a real status update, and until
this module was widened the pipeline dropped all but one kind on the floor.

The failure that motivated the widening, observed on job ``fe12af88``
(326 Huntington Ln, Elmhurst):

    operator → broker, 15:20   <full job re-paste>
                               "Tech was arriving but cx stop answering
                                check if can contact"
    operator → broker, 15:21   "Cx answered now, said already got help"

The second message is an unambiguous cancellation. Nothing acted on it:

- ``reject_detector.is_cancel_signal`` recognised the *wording*
  (``_looks_like_cancel_note`` returns True) but discarded it, because that
  function only fires on a re-paste of the full job block or a bare "DNS"
  token. A standalone follow-up sentence can never reach it.
- This parser's intent enum was ``no_answer_follow_up | none``, so the LLM
  had no code for "already got help" and returned ``none``.

The job stayed ``pending`` and eventually tripped ``closing_missing`` as a
false positive. It was not alone: 205 of the 208 pending OpenPhone jobs had
later operator→broker messages sitting unread by the pipeline.

**Design (see ``CompanyRelayIntent`` for the intent set):**

1. *Pre-filter* — skip only trivial acks (:data:`_TRIVIAL_ACKS`). The old
   gate was a contact-attempt keyword regex, which silently dropped
   cancels, appointments and progress reports before the model ever saw
   them. Outbound volume is ~70/day, so sending nearly everything costs
   very little and removes a whole class of vocabulary blind spots.
2. *Intent* — one LLM call returning a :class:`CompanyRelayIntent`.
   ``none`` short-circuits silently.
3. *Attribution* — the job the update names, preferred strongest-first:
   an explicit reference in the body, else a **sticky reference** inherited
   from a recent earlier message in the same thread
   (:data:`STICKY_REFERENCE_WINDOW_MINUTES`). If neither resolves we
   **refuse to act** and raise ``unattributed_update``. We never fall back
   to "most recent open job from this counterparty" — a broker can hold
   200+ open jobs at once and this path writes terminal statuses.
4. *Transition* — via ``LifecycleService``, which independently enforces
   that an ``operator_relay`` source may only move a job out of a
   non-terminal status.

Called from ``api/routes/v1/openphone.py`` after
``OpenPhoneService.maybe_reject_job`` returns ``False`` — the deterministic
reject/cancel detectors get first refusal, this is the fallback for
free-text updates they cannot express. Deliberately does NOT reuse that
path's two-operator-message cutoff: a status update legitimately arrives
hours into a job's life.

OpenPhone only. The WhatsApp operator path is gated on
``WhatsappMessage.is_from_me``, which is ``false`` on every row currently
ingested, so wiring this in there would be untestable dead code.
"""

import logging
import re
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import AlertKind
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.schemas.dispatch_job import CompanyRelayIntent
from app.services.lifecycle import LifecycleStatus
from app.services.llm import ainvoke_structured

logger = logging.getLogger(__name__)


# How far back to walk the thread looking for a message that names a job,
# when the update itself names none. One hour: long enough to cover an
# operator posting a re-paste and then following up as the situation
# resolves (the motivating pair were 87 seconds apart), short enough that
# an unrelated job posted earlier in a busy thread is unlikely to be
# inherited. Measured over 30 days of traffic, a 60-minute window gives a
# keyed predecessor to ~24% of keyless updates; the rest are refused.
STICKY_REFERENCE_WINDOW_MINUTES = 60

# Bodies that carry no status information at all. Compared after
# normalization, so "OK!", "ok." and "Ok 👍" all reduce to "ok".
_TRIVIAL_ACKS: frozenset[str] = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "ty",
        "thanks",
        "thank you",
        "thx",
        "yes",
        "yep",
        "yeah",
        "no",
        "got it",
        "copy",
        "copy that",
        "will do",
        "sure",
        "great",
        "perfect",
        "good",
        "np",
        "yw",
    }
)

_PUNCT_STRIP_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Map from CompanyRelayIntentCode → LifecycleStatus. ``none`` is absent by
# design: it is a no-op, not a transition. Add a new intent in BOTH
# ``schemas/dispatch_job.py:CompanyRelayIntentCode`` AND this dict.
_INTENT_TO_STATUS: dict[str, LifecycleStatus] = {
    "no_answer_follow_up": LifecycleStatus.NEEDS_FOLLOW_UP,
    "rejected": LifecycleStatus.REJECTED,
    "canceled": LifecycleStatus.CANCELED,
    "in_progress": LifecycleStatus.IN_PROGRESS,
    "appt_set": LifecycleStatus.APPT_SET,
    "completed": LifecycleStatus.COMPLETED,
}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation/emoji, collapse whitespace."""
    lowered = (text or "").lower().strip()
    lowered = _PUNCT_STRIP_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", lowered).strip()


def should_parse(body: str) -> bool:
    """True when ``body`` is worth an LLM call.

    Skips empty bodies, bare acks (:data:`_TRIVIAL_ACKS`) and anything too
    short to carry an outcome. Intentionally permissive: a false positive
    here costs one cheap model call that returns ``none``, whereas a false
    negative silently drops a real status update — the failure this module
    exists to fix.
    """
    normalized = _normalize(body)
    if len(normalized) <= 3:
        return False
    return normalized not in _TRIVIAL_ACKS


async def _resolve_job(
    db: AsyncSession,
    *,
    counterparty: str,
    body: str,
    reply_at,
):
    """Resolve the job an update refers to. Returns ``(job, matched_by)`` or None.

    Explicit reference in the body first; failing that, the most recent
    reference found in this thread within
    :data:`STICKY_REFERENCE_WINDOW_MINUTES`. Never guesses by recency.
    """
    from app.repositories import job as job_repo
    from app.repositories import openphone as openphone_repo
    from app.services.job_reference import extract_job_reference

    reference = extract_job_reference(body)
    if reference:
        found = await job_repo.find_job_by_reference_openphone(
            db, counterparty=counterparty, before=reply_at, reference=reference
        )
        if found is not None:
            return found[0], "reference"

    # Sticky reference — inherit the job named by a recent earlier message
    # in the same thread. Walk newest-first and take the first body that
    # yields a reference resolving to an open job.
    since = reply_at - timedelta(minutes=STICKY_REFERENCE_WINDOW_MINUTES)
    prior_bodies = await openphone_repo.list_recent_outbound_bodies(
        db, counterparty=counterparty, before=reply_at, since=since
    )
    for prior in prior_bodies:
        prior_reference = extract_job_reference(prior)
        if not prior_reference:
            continue
        found = await job_repo.find_job_by_reference_openphone(
            db, counterparty=counterparty, before=reply_at, reference=prior_reference
        )
        if found is not None:
            return found[0], "sticky_reference"

    return None


async def maybe_apply_relay_update(db: AsyncSession, message) -> bool:
    """Detect a status update in an operator→broker reply and apply it.

    Returns ``True`` when a Job was transitioned. Failures are logged, not
    raised — this must never break message ingestion.
    """
    body = (message.content or "").strip()
    reply_at = message.created_at
    if not body or reply_at is None:
        return False
    if not should_parse(body):
        return False

    try:
        intent = await _extract_intent(db, body)
    except Exception:
        logger.exception(
            "COMPANY_RELAY_LLM_FAILED openphone_id=%s",
            message.openphone_id,
        )
        return False

    # A remark carrying no outcome is the common case — drop it silently
    # rather than alerting, so the dashboard only ever shows updates that
    # genuinely needed a human to place them.
    if intent.intent == "none":
        logger.info("COMPANY_RELAY_NONE openphone_id=%s", message.openphone_id)
        return False

    to_status = _INTENT_TO_STATUS[intent.intent]

    for counterparty in message.to_numbers or []:
        resolved = await _resolve_job(db, counterparty=counterparty, body=body, reply_at=reply_at)
        if resolved is None:
            continue
        job, matched_by = resolved

        payload = {
            "counterparty": counterparty,
            "openphone_id": message.openphone_id,
            "body_preview": body[:120],
            "matched_by": matched_by,
            "intent": intent.intent,
        }
        if intent.follow_up_at:
            payload["follow_up_at"] = intent.follow_up_at
        if intent.appt_iso:
            payload["appt_iso"] = intent.appt_iso
        if intent.reason:
            payload["reason"] = intent.reason
        if intent.notes:
            payload["notes"] = intent.notes
        if to_status == LifecycleStatus.CANCELED:
            payload["note"] = (intent.notes or body)[:120]

        from app.services.lifecycle import LifecycleService

        try:
            await LifecycleService(db).transition(
                job=job,
                to_status=to_status,
                source=LifecycleEventSource.OPERATOR_RELAY,
                payload=payload,
                at=reply_at,
            )
        except Exception:
            # Includes InvalidTransitionError when the job has already
            # settled — expected, not exceptional. See
            # ``lifecycle._RELAY_WRITABLE_FROM_STATUSES``.
            logger.info(
                "COMPANY_RELAY_TRANSITION_REFUSED job_id=%s from=%s to=%s",
                job.id,
                job.lifecycle_status,
                to_status.value,
                exc_info=True,
            )
            return False

        logger.info(
            "COMPANY_RELAY_APPLIED openphone_id=%s job_id=%s counterparty=%s "
            "intent=%s status=%s matched_by=%s",
            message.openphone_id,
            job.id,
            counterparty,
            intent.intent,
            to_status.value,
            matched_by,
        )
        return True

    # Actionable, but we could not tell which job it is about. Surface it
    # for the operator instead of guessing against a broker's open book.
    from app.repositories import alert as alert_repo

    counterparties = message.to_numbers or []
    logger.info(
        "COMPANY_RELAY_UNATTRIBUTED openphone_id=%s intent=%s counterparties=%s",
        message.openphone_id,
        intent.intent,
        counterparties,
    )
    for counterparty in counterparties:
        await alert_repo.create_or_get_open(
            db,
            kind=AlertKind.UNATTRIBUTED_UPDATE.value,
            chat_jid=counterparty,
            payload={
                "openphone_id": message.openphone_id,
                "intent": intent.intent,
                "body_preview": body[:120],
            },
            detected_at=reply_at,
        )
    return False


# Backwards-compatible alias. The parser used to handle exactly one intent
# and was named for it; the name survives in older call sites and docs.
maybe_apply_no_answer_update = maybe_apply_relay_update


async def _extract_intent(db: AsyncSession, body: str) -> CompanyRelayIntent:
    """Run the LLM extraction. Mirrors ``tech_reply_parser._extract_intent``."""
    from datetime import UTC, datetime

    now_iso = datetime.now(UTC).isoformat()
    prompt = (
        "You are parsing a short message an operator sent to a dispatch "
        "company/broker about a job that is already open. Classify it into "
        "exactly one intent code.\n\nCurrent time (UTC): "
        f"{now_iso}\n\n"
        "INTENTS:\n"
        "- no_answer_follow_up: the customer hasn't answered or called "
        "back YET and the operator is still trying ('na did not call back "
        "left vm', 'no answer yet', 'still no answer', 'cx not picking "
        "up', 'tech was arriving but cx stop answering check if can "
        "contact'). The situation is unresolved and someone is still "
        "chasing it.\n"
        "- rejected: the OPERATOR is declining the job — this shop will "
        "not take it, usually because it cannot do this particular work "
        "('only dealer', 'dealer only' (the vehicle needs a dealer-only "
        "key), 'we dont have that key', 'cant program that one', 'too "
        "far', 'out of our area', 'no one for that area', 'not our job'). "
        "Nothing was ever attempted for the customer. Use this rather "
        "than canceled whenever the reason the job dies is on OUR side.\n"
        "- canceled: the job will NOT be done. The customer no longer "
        "needs service, already got help elsewhere, changed their mind, or "
        "called it off ('cx answered now, said already got help', 'cx "
        "canceled', 'customer found someone else', 'no longer needs it', "
        "'cx never answered, closing it out'). This is a SETTLED outcome.\n"
        "- in_progress: a technician is en route or working on site ('tech "
        "otw', 'tech is there now', 'tech on the way').\n"
        "- appt_set: an appointment was scheduled for a specific time "
        "('cx wants tomorrow 3pm', 'scheduled for 2pm today').\n"
        "- completed: the work is finished and payment was reported "
        "('done, paid 240 cash', 'job completed').\n"
        "- none: anything else — a plain acknowledgement, a question to "
        "the broker, a request for information, unrelated chatter, or a "
        "message you cannot confidently place. Use this whenever the "
        "message does not clearly report one of the outcomes above.\n\n"
        "KEY DISTINCTION: 'still trying / no answer yet' is "
        "no_answer_follow_up, NOT canceled. Only use canceled when the "
        "message reports that the job is settled and will not happen.\n"
        "KEY DISTINCTION: rejected vs canceled is about WHOSE side ended "
        "it. We declined it (capability, distance, pricing we won't do) "
        "= rejected. The customer ended it or no longer needs it = "
        "canceled. If the message is a bare capability statement with no "
        "customer outcome ('only dealer'), that is rejected.\n\n"
        "FIELDS:\n"
        "- follow_up_at (only for no_answer_follow_up): the ISO-8601 time "
        "to try the customer again, computed from the current time above. "
        "Estimate when not explicit (~+30min default).\n"
        "- appt_iso (only for appt_set): the appointment time in ISO-8601.\n"
        "- reason: a short code for WHY, when one applies — 'solved' "
        "(customer no longer needs service / got help elsewhere), "
        "'refused', 'dns', 'no_service', 'priceshopping', 'will_cb', "
        "'callback', plus the rejected-only codes 'dealer_only' (needs a "
        "dealer key/part), 'capability' (we can't do this work), "
        "'out_of_area'. Omit when none fits.\n"
        "- notes: any extra detail worth keeping. Omit if none.\n\n"
        f"Message:\n{body[:500]}\n"
    )

    return await ainvoke_structured(db, CompanyRelayIntent, prompt, site="company_relay_intent")
