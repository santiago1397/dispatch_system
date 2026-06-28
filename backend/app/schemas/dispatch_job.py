"""DispatchJob schemas for API responses and AI structured output."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# === API Response Schemas ===


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

    ``notes`` carries any extra detail the tech volunteered (ETA, parts
    needed, customer unavailable, etc.) for the operator timeline.
    """

    intent: TechReplyIntentCode
    appt_iso: str | None = None
    notes: str | None = None
