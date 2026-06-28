"""OpenPhone service — webhook processing and Quo API proxy."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openphone import openphone_client
from app.core.exceptions import BadRequestError, ExternalServiceError, NotFoundError
from app.db.models.openphone import IncomingMessage
from app.repositories import openphone_repo
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
