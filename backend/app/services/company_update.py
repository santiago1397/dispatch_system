"""Compose + persist the status relay the operator forwards to the company.

The system never sends: this builds the "original job message + update"
text and stores it as a pending ``CompanyUpdate`` for the operator to relay
natively. The alert engine reminds the operator if it isn't relayed in time.
See ``memory/feedback_no_outbound_automation.md``.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job import Job
from app.repositories import company_update_repo, job_repo

logger = logging.getLogger(__name__)

# Tech-update intents that should be relayed to the company. ``accepted``
# and the tech-reject bounce are internal (no company-facing meaning).
RELAYED_UPDATE_KINDS: frozenset[str] = frozenset(
    {"in_progress", "appt_set", "needs_follow_up", "canceled"}
)


def _reason_phrase(reason: str | None) -> str:
    return {
        "refused": "customer refused the price",
        "dns": "customer did not need service",
        "solved": "customer solved it themselves",
        "no_service": "no service needed",
        "priceshopping": "customer is price-shopping",
        "will_cb": "customer will call back",
        "callback": "customer wants a callback",
    }.get(reason or "", reason or "")


def compose_update_line(
    update_kind: str,
    *,
    appt_at_display: str | None = None,
    follow_up_at_display: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
) -> str:
    """Build the human-readable update line appended under the job body."""
    if update_kind == "in_progress":
        line = "Update: technician is on the way."
    elif update_kind == "appt_set":
        when = f" for {appt_at_display}" if appt_at_display else ""
        line = f"Update: appointment set{when}."
    elif update_kind == "needs_follow_up":
        why = f" ({_reason_phrase(reason)})" if reason else ""
        when = f" Will follow up around {follow_up_at_display}." if follow_up_at_display else ""
        line = f"Update: needs follow-up{why}.{when}"
    elif update_kind == "canceled":
        why = f" — {_reason_phrase(reason)}" if reason else ""
        line = f"Update: job canceled{why}."
    else:
        line = f"Update: {update_kind}."
    if notes:
        line = f"{line} Note: {notes}"
    return line


def compose_relay_text(original_body: str, update_line: str) -> str:
    """Full company relay: the original job message, then the update."""
    body = (original_body or "").strip()
    return f"{body}\n\n{update_line}" if body else update_line


class CompanyUpdateService:
    """Creates the pending operator→company relay for a job update."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_for_update(
        self,
        *,
        job: Job,
        update_kind: str,
        lifecycle_event_id=None,
        reason: str | None = None,
        notes: str | None = None,
    ):
        """Compose and persist a pending relay. Returns it, or ``None``.

        ``None`` when the update kind isn't relayed, or the job's origin
        message (needed for the body + the company's address) can't be
        found. Never raises — a relay failure must not break the tech-reply
        transition that triggered it.
        """
        if update_kind not in RELAYED_UPDATE_KINDS:
            return None

        origin = await job_repo.find_origin_incoming_for_job(self.db, job.id)
        if origin is None:
            logger.warning(
                "COMPANY_UPDATE_NO_ORIGIN job_id=%s kind=%s", job.id, update_kind
            )
            return None

        raw = origin.raw_payload or {}
        channel = origin.source or job.original_inbound_channel or "whatsapp"
        company_chat_jid = raw.get("chat_jid") if channel == "whatsapp" else None
        company_phone = (
            origin.from_number or job.original_inbound_from_number
            if channel == "openphone"
            else None
        )

        appt_display = job.appt_at.isoformat() if job.appt_at else None
        follow_up_display = job.follow_up_at.isoformat() if job.follow_up_at else None
        update_line = compose_update_line(
            update_kind,
            appt_at_display=appt_display,
            follow_up_at_display=follow_up_display,
            reason=reason,
            notes=notes,
        )
        message_text = compose_relay_text(origin.content or "", update_line)

        relay = await company_update_repo.create_company_update(
            self.db,
            job_id=job.id,
            update_kind=update_kind,
            channel=channel,
            message_text=message_text,
            company_id=job.company_id,
            lifecycle_event_id=lifecycle_event_id,
            company_chat_jid=company_chat_jid,
            company_phone=company_phone,
        )
        logger.info(
            "COMPANY_UPDATE_CREATED job_id=%s kind=%s channel=%s relay_id=%s",
            job.id,
            update_kind,
            channel,
            relay.id,
        )
        return relay
