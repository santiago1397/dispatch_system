"""OpenPhone service — webhook processing and Quo API proxy."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openphone import openphone_client
from app.core.exceptions import (
    BadRequestError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.db.models.openphone import IncomingMessage
from app.db.models.openphone_thread_label import OpenPhoneThreadLabel
from app.repositories import company_repo, openphone_repo, thread_label_repo
from app.schemas.openphone import MessageWebhookPayload

logger = logging.getLogger(__name__)


class OpenPhoneService:
    """Service for OpenPhone webhook processing and API interactions."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = openphone_client

    # === Webhook Processing ===

    async def process_webhook(self, payload: dict[str, Any]) -> IncomingMessage:
        """Process an incoming webhook payload from Quo.

        Validates the payload, deduplicates by openphone_id, and persists
        the message to the database.

        Args:
            payload: The parsed JSON body from the webhook request.

        Returns:
            The persisted IncomingMessage record.

        Raises:
            BadRequestError: If the payload is invalid.
        """
        try:
            webhook = MessageWebhookPayload(**payload)
        except Exception as e:
            raise BadRequestError(message=f"Invalid webhook payload: {e}") from e

        message_data = webhook.data

        # Deduplicate: check if we already have this message
        existing = await openphone_repo.get_by_openphone_id(self.db, message_data.id)
        if existing:
            logger.info(f"Duplicate webhook ignored for message {message_data.id}")
            return existing

        incoming = await openphone_repo.create_incoming_message(
            self.db,
            openphone_id=message_data.id,
            direction=message_data.direction or "unknown",
            from_number=message_data.from_number or "unknown",
            to_numbers=message_data.to,
            content=message_data.text,
            status=message_data.status,
            event_type=webhook.event,
            phone_number_id=message_data.phone_number_id,
            raw_payload=payload,
        )

        logger.info(
            f"Webhook processed: {webhook.event} for message {message_data.id} "
            f"from {message_data.from_number}"
        )

        return incoming

    # === Technician (Quo dispatch chat) routing ===

    async def resolve_technician_for_message(self, message: IncomingMessage):
        """Return the technician whose Quo chat this message belongs to, or None.

        The tech↔chat relationship is keyed on the technician's phone:
        - inbound (tech→operator): match the sender ``from_number``.
        - outbound (operator→tech): match any recipient in ``to_numbers``.
        """
        from app.repositories import technician as technician_repo

        direction = (message.direction or "").lower()
        if direction == "outgoing":
            for number in message.to_numbers or []:
                tech = await technician_repo.get_by_phone_e164(self.db, number)
                if tech is not None:
                    return tech
            return None
        return await technician_repo.get_by_phone_e164(self.db, message.from_number)

    async def handle_tech_chat_message(
        self,
        message: IncomingMessage,
        technician,
        background_tasks=None,
    ) -> None:
        """Route a message that belongs to a technician's Quo chat.

        - Outbound (operator→tech) → operator-dispatch detection (§dispatch).
        - Inbound that looks like a job (phone + address) → normal
          classification (the tech is ORIGINATING a new job).
        - Inbound otherwise → OpenPhone tech-reply parser (status update),
          run in the background because it makes an LLM call.
        """
        from app.services.classification import JobClassificationService, _clean_for_match

        direction = (message.direction or "").lower()
        if direction == "outgoing":
            await self._handle_operator_dispatch(message, technician)
            return

        match_content = _clean_for_match(message.content or "")
        if JobClassificationService._is_job_message(match_content):
            logger.info(
                "OP_TECH_CHAT stage=new_job tech=%s openphone_id=%s",
                technician.name,
                message.openphone_id,
            )
            svc = JobClassificationService(self.db)
            await svc.classify_message(message)
            return

        logger.info(
            "OP_TECH_CHAT stage=status_reply tech=%s openphone_id=%s",
            technician.name,
            message.openphone_id,
        )
        from app.services.tech_reply_parser import parse_openphone_tech_reply_in_background

        if background_tasks is not None:
            background_tasks.add_task(
                parse_openphone_tech_reply_in_background,
                incoming_message_id=message.id,
            )
        else:
            await parse_openphone_tech_reply_in_background(incoming_message_id=message.id)

    async def _handle_operator_dispatch(self, message: IncomingMessage, technician) -> None:
        """Match an operator's outbound message to a pending Job and dispatch it.

        Mirrors ``services/whatsapp.py:_handle_operator_dispatch`` for the
        OpenPhone channel: parse the address+phone from the body, fuzzy-match
        a pending Job, and transition it to ``dispatched`` (or raise a
        ``dispatch_no_match`` alert). Idempotent on redelivered webhooks.
        """
        from app.db.models.job_lifecycle_event import LifecycleEventSource
        from app.repositories import alert as alert_repo
        from app.repositories import job as job_repo
        from app.repositories import lifecycle_event_repo
        from app.services.address_normalizer import normalize_address, normalize_phone
        from app.services.classification import PHONE_PATTERN
        from app.services.lifecycle import LifecycleService

        openphone_id = message.openphone_id
        if openphone_id and await lifecycle_event_repo.exists_for_openphone_id(
            self.db,
            source=LifecycleEventSource.OPERATOR_OPENPHONE.value,
            openphone_id=openphone_id,
        ):
            logger.info("OP_DISPATCH_DUP openphone_id=%s", openphone_id)
            return

        body = (message.content or "").strip()
        if not body:
            return

        normalized = normalize_address(body)
        phone_match = PHONE_PATTERN.search(body)
        phone_e164 = normalize_phone(phone_match.group(0)) if phone_match else None

        job = await job_repo.find_dispatch_target(
            self.db,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            zip_code=normalized.zip_code,
            customer_phone_e164=phone_e164,
        )
        chat_key = f"openphone:{technician.phone_e164}"
        if job is None:
            await alert_repo.create_or_get_open(
                self.db,
                kind="dispatch_no_match",
                chat_jid=chat_key,
                payload={
                    "openphone_id": openphone_id,
                    "body_preview": body[:120],
                    "street_number": normalized.street_number,
                    "street_name": normalized.street_name,
                    "zip_code": normalized.zip_code,
                    "phone_e164": phone_e164,
                    "technician_id": str(technician.id),
                },
            )
            logger.warning(
                "OP_DISPATCH_NO_MATCH openphone_id=%s tech=%s", openphone_id, technician.name
            )
            return

        try:
            await LifecycleService(self.db).transition(
                job=job,
                to_status="dispatched",
                source=LifecycleEventSource.OPERATOR_OPENPHONE,
                payload={
                    "phone_e164": technician.phone_e164,
                    "openphone_id": openphone_id,
                    "technician_id": str(technician.id),
                },
                at=message.created_at,
            )
        except Exception:
            logger.exception(
                "OP_DISPATCH_TRANSITION_FAILED job_id=%s openphone_id=%s", job.id, openphone_id
            )
            return
        logger.info(
            "OP_DISPATCH_TRANSITIONED job_id=%s tech=%s openphone_id=%s",
            job.id,
            technician.name,
            openphone_id,
        )

    # === Operator reject branch ===

    async def maybe_reject_job(self, message: IncomingMessage) -> bool:
        """Reject the pending job an operator outbound reply declines, if any.

        The OpenPhone twin of ``WhatsappService._maybe_reject_job``. Called
        for outbound (operator→company) messages on the non-tech default
        path. Returns ``True`` when a job was transitioned to ``rejected``.

        Flow: for each counterparty the reply was sent to, resolve which job
        the reply is about, confirm the body is a reject or cancel signal
        (phrase, or a re-paste of the job with a note), and transition the
        job to the terminal ``rejected``/``canceled`` status via the
        lifecycle gate. A re-paste whose note reports the customer wasn't
        there (e.g. "she's not there anymore") or never answered (e.g. "cx
        not answering to me or to new technician I left vm") transitions to
        ``canceled`` instead of ``rejected`` — the job was accepted and
        worked, not declined outright. See
        ``reject_detector.is_cancel_signal``.

        Job resolution prefers the identity the reply itself names (PDL
        code / customer phone / address — see ``services/job_reference.py``)
        and only falls back to "most recent still-``pending`` job from this
        counterparty" when the reply carries no identity keys at all. At
        volume the recency fallback is close to a coin flip: a single broker
        can have well over a hundred jobs open simultaneously.

        Two window rules, which differ by outcome:

        - **Reject** keeps the "within the next two operator outbound
          messages" cutoff. Declining a job is something an operator does
          immediately on intake, so a late "pass" is far more likely to be
          about a different job.
        - **Cancel** is exempt. A cancellation is reported *after* a tech
          has been dispatched and gone to the address, which is routinely
          hours and many messages later. Applying the reject window here is
          what left these jobs sitting at ``pending``.
        """
        from app.db.models.job_lifecycle_event import LifecycleEventSource
        from app.repositories import job as job_repo
        from app.services import reject_detector
        from app.services.job_reference import extract_job_reference
        from app.services.lifecycle import LifecycleService, LifecycleStatus

        body = (message.content or "").strip()
        reply_at = message.created_at
        if not body or reply_at is None:
            return False

        reference = extract_job_reference(body)

        for counterparty in message.to_numbers or []:
            candidate = None
            matched_by = "reference"
            if reference:
                candidate = await job_repo.find_job_by_reference_openphone(
                    self.db,
                    counterparty=counterparty,
                    before=reply_at,
                    reference=reference,
                )
            if candidate is None:
                matched_by = "recency"
                candidate = await job_repo.find_reject_candidate_openphone(
                    self.db, counterparty=counterparty, before=reply_at
                )
            if candidate is None:
                continue
            job, source_body = candidate

            is_cancel = reject_detector.is_cancel_signal(body, source_body)
            if not is_cancel and not reject_detector.is_reject_signal(body, source_body):
                continue

            outbound_count = await openphone_repo.count_outbound_messages_to(
                self.db,
                counterparty=counterparty,
                after=job.first_message_at,
                until=reply_at,
            )
            # The two-message cutoff guards the *reject* path only — see the
            # docstring. A cancel legitimately arrives long after intake.
            if not is_cancel and outbound_count > 2:
                logger.info(
                    "OP_REJECT_TOO_LATE openphone_id=%s job_id=%s outbound=%d",
                    message.openphone_id,
                    job.id,
                    outbound_count,
                )
                continue

            to_status = LifecycleStatus.CANCELED if is_cancel else LifecycleStatus.REJECTED
            source = (
                LifecycleEventSource.OPERATOR_CANCEL
                if is_cancel
                else LifecycleEventSource.OPERATOR_REJECT
            )
            await LifecycleService(self.db).transition(
                job=job,
                to_status=to_status,
                source=source,
                payload={
                    "counterparty": counterparty,
                    "openphone_id": message.openphone_id,
                    "body_preview": body[:120],
                    "operator_msg_index": outbound_count,
                    "matched_by": matched_by,
                    **({"note": body[:120]} if is_cancel else {}),
                },
                at=reply_at,
            )
            logger.info(
                "OP_%s_APPLIED openphone_id=%s job_id=%s counterparty=%s outbound=%d matched_by=%s",
                "CANCEL" if is_cancel else "REJECT",
                message.openphone_id,
                job.id,
                counterparty,
                outbound_count,
                matched_by,
            )
            return True

        return False

    # === Internal CRUD ===

    async def get_incoming_message(self, message_id) -> IncomingMessage:
        """Get a persisted incoming message by ID."""
        message = await openphone_repo.get_incoming_message(self.db, message_id)
        if not message:
            raise NotFoundError(message="Incoming message not found")
        return message

    async def list_incoming_messages(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[IncomingMessage], int]:
        """List persisted incoming messages with total count."""
        messages = await openphone_repo.list_incoming_messages(self.db, skip=skip, limit=limit)
        total = await openphone_repo.count_incoming_messages(self.db)
        return messages, total

    async def list_threads(
        self,
        *,
        phone_number_id: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List OpenPhone conversation threads, most recently active first.

        Enriched with each counterparty's ``OpenPhoneThreadLabel`` (company
        reference and/or free-text label) in one bulk lookup — display-only,
        never affects classification.
        """
        threads = await openphone_repo.list_threads(
            self.db, phone_number_id=phone_number_id, skip=skip, limit=limit
        )
        total = await openphone_repo.count_threads(self.db, phone_number_id=phone_number_id)

        labels = await thread_label_repo.get_by_counterparties(
            self.db, [t.counterparty for t in threads]
        )
        enriched = [self._merge_thread_label(t, labels.get(t.counterparty)) for t in threads]
        return enriched, total

    @staticmethod
    def _merge_thread_label(
        thread_row: Any,
        label: OpenPhoneThreadLabel | None,
    ) -> dict[str, Any]:
        """Combine a raw thread row with its (optional) label row."""
        company = label.company if label is not None else None
        return {
            "counterparty": thread_row.counterparty,
            "last_content": thread_row.last_content,
            "last_direction": thread_row.last_direction,
            "last_created_at": thread_row.last_created_at,
            "message_count": thread_row.message_count,
            "company_id": company.id if company is not None else None,
            "company_name": company.name if company is not None else None,
            "company_display_name": company.display_name if company is not None else None,
            "label": label.label if label is not None else None,
        }

    async def upsert_thread_label(
        self,
        *,
        counterparty: str,
        company_id: UUID | None,
        label: str | None,
        created_by_user_id: UUID | None,
    ) -> OpenPhoneThreadLabel:
        """Set the company reference and/or free-text label for a thread.

        Display-only — never touches ``company_phone_bindings`` or the
        classification pipeline. Raises ``ValidationError`` if both fields
        are empty (use the DELETE endpoint to clear a label) and
        ``NotFoundError`` if ``company_id`` doesn't reference a real company.
        """
        normalized_label = (label or "").strip() or None
        if company_id is None and normalized_label is None:
            raise ValidationError(
                message="At least one of company_id or label must be set.",
            )
        if company_id is not None:
            company = await company_repo.get_by_id(self.db, company_id)
            if company is None:
                raise NotFoundError(
                    message="Company not found", details={"company_id": str(company_id)}
                )

        return await thread_label_repo.upsert(
            self.db,
            counterparty=counterparty,
            company_id=company_id,
            label=normalized_label,
            created_by_user_id=created_by_user_id,
        )

    async def delete_thread_label(self, counterparty: str) -> None:
        """Clear the company reference/label for a thread. No-op if unset."""
        await thread_label_repo.delete(self.db, counterparty)

    async def list_thread_messages(
        self,
        *,
        counterparty: str,
        phone_number_id: str | None = None,
        since=None,
        until=None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[IncomingMessage], int]:
        """List OpenPhone messages exchanged with ``counterparty``, with total count."""
        messages = await openphone_repo.list_thread_messages(
            self.db,
            counterparty=counterparty,
            phone_number_id=phone_number_id,
            since=since,
            until=until,
            skip=skip,
            limit=limit,
        )
        total = await openphone_repo.count_thread_messages(
            self.db,
            counterparty=counterparty,
            phone_number_id=phone_number_id,
            since=since,
            until=until,
        )
        return messages, total

    # === Quo API Proxy Methods ===

    async def list_phone_numbers(self, *, user_id: str | None = None) -> dict[str, Any]:
        """List phone numbers via Quo API."""
        return await self._api_call(self.client.list_phone_numbers, user_id=user_id)

    async def get_phone_number(self, phone_number_id: str) -> dict[str, Any]:
        """Get a phone number via Quo API."""
        return await self._api_call(self.client.get_phone_number, phone_number_id)

    async def list_users(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List users via Quo API."""
        return await self._api_call(
            self.client.list_users, max_results=max_results, page_token=page_token
        )

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """Get a user via Quo API."""
        return await self._api_call(self.client.get_user, user_id)

    async def list_messages(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List messages via Quo API."""
        return await self._api_call(
            self.client.list_messages, max_results=max_results, page_token=page_token
        )

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Get a message via Quo API."""
        return await self._api_call(self.client.get_message, message_id)

    # NOTE: There is intentionally no ``send_message`` method on this service.
    # The Dispatch Chicago system never places outbound customer messages —
    # operators type replies natively in the OpenPhone mobile app. This
    # service exists only to *receive* webhooks (incoming messages) and
    # *read* conversation/message data via the Quo API. If you find
    # yourself wanting to add a send path here, see
    # ``memory/feedback_no_outbound_automation.md`` and confirm with the
    # user first.

    async def list_conversations(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
        phone_numbers: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """List conversations via Quo API."""
        return await self._api_call(
            self.client.list_conversations,
            max_results=max_results,
            page_token=page_token,
            phone_numbers=phone_numbers,
            user_id=user_id,
        )

    async def list_webhooks(self, *, user_id: str | None = None) -> dict[str, Any]:
        """List webhooks via Quo API."""
        return await self._api_call(self.client.list_webhooks, user_id=user_id)

    async def get_webhook(self, webhook_id: str) -> dict[str, Any]:
        """Get a webhook via Quo API."""
        return await self._api_call(self.client.get_webhook, webhook_id)

    async def create_message_webhook(
        self,
        url: str,
        events: list[str] | None = None,
        label: str | None = None,
        resource_ids: list[str] | None = None,
        status: str = "enabled",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a message webhook via Quo API."""
        return await self._api_call(
            self.client.create_message_webhook,
            url=url,
            events=events,
            label=label,
            resource_ids=resource_ids,
            status=status,
            user_id=user_id,
        )

    async def delete_webhook(self, webhook_id: str) -> dict[str, Any]:
        """Delete a webhook via Quo API."""
        return await self._api_call(self.client.delete_webhook, webhook_id)

    async def _api_call(self, func, **kwargs) -> dict[str, Any]:
        """Wrapper for API calls with error handling."""
        try:
            return await func(**kwargs)
        except Exception as e:
            raise ExternalServiceError(
                message=f"OpenPhone API error: {e}",
                details={"error": str(e)},
            ) from e
