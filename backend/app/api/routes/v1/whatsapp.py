"""WhatsApp ingestion routes — service-token auth, batch ingest, tracked-chat CRUD.

Mirrors ``app/api/routes/v1/openphone.py`` — sectioned by ``# ===`` headers,
service-injection style ``service: WhatsappSvc`` plus auth dependencies.
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    CurrentServiceAccount,
    CurrentUser,
    CurrentUserOrService,
    WhatsappSvc,
    authenticate_service_key,
)
from app.db.models.user import User
from app.schemas.whatsapp import (
    WhatsappMessageBatchIngest,
    WhatsappMessageBatchResult,
    WhatsappMessageList,
    WhatsappMessageRead,
    WhatsappTrackedChatCreate,
    WhatsappTrackedChatList,
    WhatsappTrackedChatRead,
    WhatsappTrackedChatUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Service-Account Auth (no JWT)
# =============================================================================


@router.post(
    "/auth/service-token",
    status_code=status.HTTP_200_OK,
    summary="Exchange a service-account API key for a JWT pair",
)
async def exchange_service_token(
    user: Annotated[User, Depends(authenticate_service_key)],
    service: WhatsappSvc,
):
    """Exchange ``X-Service-Api-Key`` for an access + refresh JWT pair.

    The access token has a 30-min TTL, the refresh token 7 days. Both
    carry the ``svc: true`` claim so ``get_service_account`` can
    distinguish them from regular user JWTs.
    """
    return await service.exchange_api_key(user)


@router.post(
    "/auth/refresh",
    status_code=status.HTTP_200_OK,
    summary="Rotate a service-account refresh token",
)
async def refresh_service_token(
    service: WhatsappSvc,
    x_refresh_token: Annotated[str | None, Header(alias="X-Refresh-Token")] = None,
):
    """Rotate the refresh token and return a new JWT pair."""
    if not x_refresh_token:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing X-Refresh-Token header"},
        )
    return await service.refresh_service_token(x_refresh_token)


# =============================================================================
# Message Ingestion (service-account JWT)
# =============================================================================


@router.post(
    "/messages/batch",
    response_model=WhatsappMessageBatchResult,
    status_code=status.HTTP_200_OK,
    summary="Upsert a batch of WhatsApp messages from the Chrome extension",
)
async def ingest_message_batch(
    payload: WhatsappMessageBatchIngest,
    background_tasks: BackgroundTasks,
    service: WhatsappSvc,
    _svc: CurrentServiceAccount,
    request: Request,
):
    """Receive a batch of messages from the extension and upsert.

    Per-message: INSERT if new, UPDATE if existing-with-newer-timestamp,
    SKIP if existing-with-older-timestamp. Messages for chats not in
    the whitelist are rejected with per-item errors (the rest of the
    batch still processes).

    After upsert, each new message is mirrored into ``incoming_messages``
    and dispatched to ``JobClassificationService`` in a background task
    so the dedup pipeline sees WhatsApp messages identically to OpenPhone.
    The route returns 200 immediately; classification completes async.
    """
    # The SW sends a per-chunk UUID as X-Request-ID; RequestIDMiddleware
    # exposes it on request.state.request_id. Fall back to a fresh uuid4
    # so the log line never lacks a correlation id (e.g. when curl-testing
    # the endpoint without the header).
    batch_id = getattr(request.state, "request_id", None) or str(uuid4())
    unique_jids = {m.chat_jid for m in payload.messages}
    logger.info(
        "BATCH_RECEIVED batch_id=%s total=%d unique_jids=%d",
        batch_id,
        len(payload.messages),
        len(unique_jids),
    )
    result = await service.ingest_batch(
        payload, background_tasks=background_tasks, batch_id=batch_id
    )
    logger.info(
        "BATCH_PROCESSED batch_id=%s inserted=%d updated=%d skipped=%d deduplicated=%d errors=%d",
        batch_id,
        result.inserted,
        result.updated,
        result.skipped,
        result.deduplicated,
        len(result.errors),
    )
    return result


# =============================================================================
# Message Browse (admin/operator JWT)
# =============================================================================


@router.get(
    "/messages",
    response_model=WhatsappMessageList,
    summary="Search and list persisted WhatsApp messages",
)
async def list_messages(
    service: WhatsappSvc,
    _user: CurrentUser,
    chat_jid: str | None = Query(default=None, description="Filter by WhatsApp JID"),
    since: datetime | None = Query(default=None, description="Lower bound (timestamp >=)"),
    until: datetime | None = Query(default=None, description="Upper bound (timestamp <=)"),
    sender: str | None = Query(default=None, description="Filter by sender JID or display name"),
    contains: str | None = Query(default=None, description="Case-insensitive substring on body"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Browse the persisted message history. Default order: timestamp DESC."""
    messages, total = await service.list_messages(
        chat_jid=chat_jid,
        since=since,
        until=until,
        sender=sender,
        contains=contains,
        skip=skip,
        limit=limit,
    )
    return WhatsappMessageList(
        items=[WhatsappMessageRead.model_validate(m) for m in messages],
        total=total,
    )


# =============================================================================
# Tracked Chats (admin/operator for write, service-account for read)
# =============================================================================


@router.get(
    "/tracked-chats",
    response_model=WhatsappTrackedChatList,
    summary="List tracked WhatsApp chats (the whitelist)",
)
async def list_tracked_chats(
    service: WhatsappSvc,
    _user: CurrentUserOrService,
    include_inactive: bool = Query(default=False),
):
    """List all tracked chats. By default only active ones are returned.

    Accepts either a regular user JWT or a service-account JWT — the
    extension's SW needs to read its own whitelist with the service
    token, while a human operator also reads it with their user token.
    """
    chats = await service.list_tracked_chats(include_inactive=include_inactive)
    return WhatsappTrackedChatList(
        items=[WhatsappTrackedChatRead.model_validate(c) for c in chats],
        total=len(chats),
    )


@router.post(
    "/tracked-chats",
    response_model=WhatsappTrackedChatRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a chat to the tracked whitelist (idempotent on JID)",
)
async def create_tracked_chat(
    data: WhatsappTrackedChatCreate,
    service: WhatsappSvc,
    _user: CurrentUser,
):
    """Add a WhatsApp chat to the whitelist. Idempotent on ``chat_jid`` —
    POSTing the same JID re-activates the row and updates its display name.
    """
    chat = await service.create_tracked_chat(data)
    return WhatsappTrackedChatRead.model_validate(chat)


@router.patch(
    "/tracked-chats/{chat_jid}",
    response_model=WhatsappTrackedChatRead,
    summary="Update a tracked chat (rename, enable/disable)",
)
async def update_tracked_chat(
    chat_jid: str,
    data: WhatsappTrackedChatUpdate,
    service: WhatsappSvc,
    _user: CurrentUser,
):
    """Update display name and/or active flag for a tracked chat."""
    chat = await service.update_tracked_chat(chat_jid, data)
    return WhatsappTrackedChatRead.model_validate(chat)


@router.delete(
    "/tracked-chats/{chat_jid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a tracked chat (history is preserved)",
)
async def deactivate_tracked_chat(
    chat_jid: str,
    service: WhatsappSvc,
    _user: CurrentUser,
):
    """Deactivate a tracked chat. Existing messages are not deleted; the
    extension will stop scraping new messages from this chat."""
    await service.deactivate_tracked_chat(chat_jid)
    return None
