"""DispatchJob schemas for API responses and AI structured output."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# === API Response Schemas ===


class CompanyUpdateRead(BaseModel):
    """A pending status relay the operator should forward to the company."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    update_kind: str
    channel: str
    company_chat_jid: str | None = None
    company_phone: str | None = None
    message_text: str
    sent_at: datetime | None = None
    created_at: datetime


class DispatchJobRead(BaseModel):
    """DispatchJob response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incoming_message_id: UUID
    source: str | None = None
    company_id: UUID | None = None
    company_name: str | None = None
    job_id: UUID | None = None
    classification_status: str
    classification_method: str | None = None
    classification_error: str | None = None
    address: str | None = None
    job_type: str | None = None
    total: str | None = None
    parts: str | None = None
    payment_method: str | None = None
    tech_name: str | None = None
    car_make: str | None = None
    car_model: str | None = None
    car_year: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    scheduled_at: str | None = None
    job_description: str | None = None
    # Closing-flow extras — only set when this DispatchJob carries a
    # closing message (from the "Dispatch closing" WhatsApp group).
    # ``tip`` and ``notes`` are not in the standard extraction columns,
    # so they ride here pulled from extraction_raw.
    closing_tip: str | None = None
    closing_notes: str | None = None
    # Lifecycle pipeline state — denormalized from the parent Job row so
    # the /jobs UI can render the badge + dropdown without a second query.
    lifecycle_status: str | None = None
    lifecycle_status_changed_at: datetime | None = None
    # Tech-update details, also denormalized from the parent Job:
    #   - appt_at: appointment time (when lifecycle_status == 'appt_set').
    #   - follow_up_at: callback reminder time (when 'needs_follow_up').
    #   - reason: last tech reason code (refused / dns / priceshopping / …).
    appt_at: datetime | None = None
    follow_up_at: datetime | None = None
    reason: str | None = None
    # The latest unsent status relay the operator should forward to the
    # company. Populated only on the single-job GET (never in the list, to
    # avoid a per-row query). Null when nothing is pending.
    pending_company_update: CompanyUpdateRead | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DispatchJobList(BaseModel):
    """Paginated list of dispatch jobs."""

    items: list[DispatchJobRead]
    total: int


# === AI Structured Output Schemas (internal use) ===


class JobExtraction(BaseModel):
    """Structured output from AI field extraction."""

    address: str | None = None
    job_type: str | None = None
    total: str | None = None
    parts: str | None = None
    payment_method: str | None = None
    tech_name: str | None = None
    car_make: str | None = None
    car_model: str | None = None
    car_year: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    scheduled_at: str | None = None
    job_description: str | None = None


class CompanyClassification(BaseModel):
    """Structured output from AI company classification."""

    company_name: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasoning: str | None = None


class ClosingExtraction(BaseModel):
    """Structured output from AI closing-message extraction.

    Used by the "Dispatch closing" pipeline branch. ``address`` and
    ``customer_phone`` are matching keys back to the original Job; the
    remaining fields are the closing actuals. ``total/parts/tip`` are
    intentionally the FINAL amounts — any estimates that appeared earlier
    in the same message must be ignored by the extractor prompt.
    """

    address: str | None = None
    customer_phone: str | None = None
    total: str | None = None
    parts: str | None = None
    tip: str | None = None
    payment_method: str | None = None
    notes: str | None = None


# Intent codes for tech replies. Kept in sync with ``LifecycleStatus`` in
# ``app/services/lifecycle.py`` minus the entries a tech reply can never
# set (operator-only / closing-only):
#   - ``pending``           → initial state, never set by a tech
#   - ``dispatched``        → operator-initiated, not a tech reply
#   - ``completed``         → set by closing pipeline only
#   - ``closed``            → set by closing pipeline only
#   - ``canceled``          → operator override only (manual path
#                              rejects "completed" but allows "canceled"
#                              from tech replies — short cancellation
#                              notices like "customer not home" qualify)
#
# Note: ``canceled`` IS permitted from a tech reply ("customer canceled,
# left a key"). It's the only operator-only state that overlaps.
TechReplyIntentCode = Literal[
    "in_progress",
    "appt_set",
    "needs_follow_up",
    "canceled",
]


class TechReplyIntent(BaseModel):
    """Structured output from AI tech-reply parsing.

    ``intent`` is a closed enum — the prompt explicitly instructs the
    model to choose ``needs_follow_up`` whenever ambiguous. Short replies
    like "ok" or "k" therefore produce a draft the operator reviews
    instead of a silent state change.

    ``appt_iso`` is only meaningful when ``intent='appt_set'``; the model
    is asked to render the appointment time in ISO-8601 when possible
    (e.g. ``2026-06-28T15:00:00-05:00``) but free-text fallbacks like
    "tomorrow 3pm" are accepted.

    ``follow_up_at`` is only meaningful when ``intent='needs_follow_up'``:
    the ISO-8601 time the operator should call the customer back
    ("price-shopping", "will call back", "wants a call in a few minutes /
    later"). The model computes it from the current time given in the
    prompt; when no timing is stated it estimates a sensible default. It
    powers the ``follow_up_due`` reminder alert.

    ``reason`` is a short machine code for *why* — mainly for the terminal
    ``canceled`` outcomes so reporting can separate them: ``refused`` (gave
    price, customer declined), ``dns`` / ``solved`` / ``no_service``
    (customer didn't need service), plus follow-up reasons like
    ``priceshopping`` / ``will_cb`` / ``callback``.

    ``notes`` carries any extra detail the tech volunteered (ETA, parts
    needed, customer unavailable, etc.) for the operator timeline.
    """

    intent: TechReplyIntentCode
    appt_iso: str | None = None
    follow_up_at: str | None = None
    reason: str | None = None
    notes: str | None = None


# Intent codes for an operator's relay reply back to a company/broker's own
# number (as opposed to a technician's dispatch chat). ``none`` remains a
# genuine no-op rather than a forced guess: most operator remarks in these
# threads ("ok", "ty") carry no status information at all.
#
# Widened from the original ``no_answer_follow_up | none`` pair because the
# two-value set silently dropped every other outcome an operator reports in
# free text. The observed failure: an operator wrote "Cx answered now, said
# already got help" — an unambiguous cancellation — and the parser had no
# code to express it, so it returned ``none`` and the job sat ``pending``
# until the closing_missing alert fired days later. The regex path could
# not catch it either: ``reject_detector.is_cancel_signal`` only fires on a
# re-paste of the full job block or a bare "DNS" token, and this was a
# standalone follow-up sentence.
#
# ``rejected`` was added for the same class of failure one step earlier in
# the job's life: the operator declines the job outright, in wording the
# deterministic ``reject_detector`` phrase list cannot enumerate. The
# observed case is "only dealer" — locksmith shorthand for "this vehicle
# needs a dealer-only key, we cannot do it" — which shares no vocabulary
# with "pass" / "cant take". Without a code for it the model's only honest
# answer was ``none``, so every decline phrased in domain terms left the job
# ``pending``. This is the intent set's only way to express "we are not
# taking this job at all", as distinct from ``canceled`` ("it was ours, and
# then it fell through").
CompanyRelayIntentCode = Literal[
    "no_answer_follow_up",
    "rejected",
    "canceled",
    "in_progress",
    "appt_set",
    "completed",
    "none",
]


class CompanyRelayIntent(BaseModel):
    """Structured output from AI parsing of an operator→company relay reply.

    Classifies the free-text status updates an operator sends back to a
    dispatch company/broker about a job that is already open. The
    corresponding lifecycle transition is applied by
    ``services/company_relay_parser.py``, which maps each code to a
    ``LifecycleStatus``.

    Intents:

    - ``no_answer_follow_up``: the customer hasn't answered / called back
      yet and the operator is still trying ("na did not call back lef vm").
      → ``needs_follow_up``.
    - ``rejected``: the operator is declining the job outright — this shop
      will not take it, usually because it cannot ("only dealer", "we don't
      have that key", "too far", "no one for that area"). → ``rejected``.
      The job was never ours, so nothing was attempted. Distinguish from
      ``canceled``, where the job *was* taken and then fell through on the
      customer's side.
    - ``canceled``: the job will not be done — the customer no longer needs
      service, got help elsewhere, or called it off ("cx answered now, said
      already got help"). → ``canceled``. This is a *settled outcome*, not
      an in-flight attempt; when the operator is still chasing the customer
      the correct code is ``no_answer_follow_up``.
    - ``in_progress``: a technician is en route or on site ("tech otw",
      "tech is there now"). → ``in_progress``.
    - ``appt_set``: an appointment was scheduled ("cx wants tomorrow 3pm").
      → ``appt_set``.
    - ``completed``: the work is done and payment was reported. →
      ``completed``.
    - ``none``: anything else — acks, questions, chatter, payment relays
      with no completion claim. Used whenever the reply does not clearly
      report one of the outcomes above.

    ``follow_up_at`` (only for ``no_answer_follow_up``): the ISO-8601 time
    to try the customer again, computed the same way as
    ``TechReplyIntent.follow_up_at``.

    ``appt_iso`` (only for ``appt_set``): the appointment time in ISO-8601
    when the reply states one.

    ``reason`` is a short machine code for *why*, mirroring
    ``TechReplyIntent.reason`` so reporting can group both sources with one
    vocabulary: ``solved`` (customer no longer needs service / got help
    elsewhere), ``refused``, ``dns``, ``no_service``, ``priceshopping``,
    ``will_cb``, ``callback``, plus the ``rejected``-only codes
    ``dealer_only`` (the vehicle needs a dealer-supplied key/part),
    ``capability`` (this shop cannot do the work) and ``out_of_area``.
    """

    intent: CompanyRelayIntentCode
    follow_up_at: str | None = None
    appt_iso: str | None = None
    reason: str | None = None
    notes: str | None = None
