"""Async HTTP client for the Quo (OpenPhone) API."""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenPhoneClient:
    """Async client for the Quo (OpenPhone) REST API.

    Uses httpx for async HTTP requests. The API key is sent in the
    Authorization header as a plain value (not Bearer).
    """

    def __init__(self) -> None:
        self._base_url = settings.OPENPHONE_BASE_URL
        self._api_key = settings.OPENPHONE_API_KEY

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the Quo API."""
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    # === Phone Numbers ===

    async def list_phone_numbers(self, *, user_id: str | None = None) -> dict[str, Any]:
        """List phone numbers in the workspace."""
        params = {}
        if user_id:
            params["userId"] = user_id
        return await self._request("GET", "/phone-numbers", params=params)

    async def get_phone_number(self, phone_number_id: str) -> dict[str, Any]:
        """Get a phone number by ID."""
        return await self._request("GET", f"/phone-numbers/{phone_number_id}")

    # === Users ===

    async def list_users(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List users in the workspace."""
        params: dict[str, Any] = {"maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return await self._request("GET", "/users", params=params)

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """Get a user by ID."""
        return await self._request("GET", f"/users/{user_id}")

    # === Messages ===

    async def list_messages(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List messages."""
        params: dict[str, Any] = {"maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return await self._request("GET", "/messages", params=params)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Get a message by ID."""
        return await self._request("GET", f"/messages/{message_id}")

    async def send_message(
        self,
        *,
        content: str,
        from_number: str,
        to: list[str],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message."""
        payload: dict[str, Any] = {
            "content": content,
            "from": from_number,
            "to": to,
        }
        if user_id:
            payload["userId"] = user_id
        return await self._request("POST", "/messages", json=payload)

    # === Conversations ===

    async def list_conversations(
        self,
        *,
        max_results: int = 10,
        page_token: str | None = None,
        phone_numbers: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """List conversations."""
        params: dict[str, Any] = {"maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        if phone_numbers:
            params["phoneNumbers"] = phone_numbers
        if user_id:
            params["userId"] = user_id
        return await self._request("GET", "/conversations", params=params)

    # === Webhooks ===

    async def list_webhooks(self, *, user_id: str | None = None) -> dict[str, Any]:
        """List all webhooks."""
        params = {}
        if user_id:
            params["userId"] = user_id
        return await self._request("GET", "/webhooks", params=params)

    async def get_webhook(self, webhook_id: str) -> dict[str, Any]:
        """Get a webhook by ID."""
        return await self._request("GET", f"/webhooks/{webhook_id}")

    async def create_message_webhook(
        self,
        url: str,
        events: list[str] | None = None,
        label: str | None = None,
        resource_ids: list[str] | None = None,
        status: str = "enabled",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new webhook for message events."""
        payload: dict[str, Any] = {
            "url": url,
            "events": events or ["message.received"],
            "status": status,
        }
        if label:
            payload["label"] = label
        if resource_ids:
            payload["resourceIds"] = resource_ids
        if user_id:
            payload["userId"] = user_id
        return await self._request("POST", "/webhooks/messages", json=payload)

    async def delete_webhook(self, webhook_id: str) -> dict[str, Any]:
        """Delete a webhook by ID."""
        return await self._request("DELETE", f"/webhooks/{webhook_id}")


# Singleton instance
openphone_client = OpenPhoneClient()
