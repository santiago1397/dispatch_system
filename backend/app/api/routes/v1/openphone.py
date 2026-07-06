"""OpenPhone (Quo API) routes — webhook receiver and API proxy."""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, OpenPhoneSvc
from app.core.config import settings
from app.core.webhook import (
    LEGACY_SIGNATURE_HEADER,
    verify_webhook_from_headers,
)
from app.schemas.openphone import (
    IncomingMessageList,
    IncomingMessageRead,
)
from app.services.openphone import OpenPhoneService

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Public Webhook Endpoint (no JWT auth)
# =============================================================================


@router.get("/webhooks/openphone/ping", include_in_schema=False)
async def webhook_ping():
    """Public reachability probe. Hit this from a browser to confirm Quo can reach this host."""
    logger.info("OpenPhone webhook PING received")
    return {
        "ok": True,
        "expected_webhook_url_suffix": "/api/v1/openphone/webhooks/openphone",
        "secret_configured": bool(settings.OPENPHONE_WEBHOOK_SECRET),
        "environment": settings.ENVIRONMENT,
    }


@router.post(
    "/webhooks/openphone",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def receive_openphone_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive webhook events from Quo (OpenPhone).

    This endpoint is public — no JWT authentication required.
    Verifies the webhook signature using the configured secret.
    Persists incoming messages to the database.

    Full path: POST /api/v1/openphone/webhooks/openphone
    """
    client_host = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    content_type = request.headers.get("content-type", "")
    logger.info(
        "OpenPhone webhook HIT: from=%s ua=%s content_type=%s",
        client_host,
        user_agent,
        content_type,
    )

    body = await request.body()
    secret_configured = bool(settings.OPENPHONE_WEBHOOK_SECRET)
    legacy_sig = request.headers.get(LEGACY_SIGNATURE_HEADER)
    sig_preview = (legacy_sig[:8] + "...") if legacy_sig else "<missing>"
    logger.info(
        "OpenPhone webhook: body_bytes=%d legacy_sig=%s secret_configured=%s",
        len(body),
        sig_preview,
        secret_configured,
    )

    # Verify signature (supports legacy OpenPhone + Quo beta Svix-style).
    if not verify_webhook_from_headers(body, request.headers, settings.OPENPHONE_WEBHOOK_SECRET):
        logger.warning(
            "OpenPhone webhook REJECTED (bad signature) from=%s sig=%s secret_configured=%s",
            client_host,
            sig_preview,
            secret_configured,
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid webhook signature"},
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning(
            "OpenPhone webhook REJECTED (bad JSON) from=%s body_preview=%r", client_host, body[:200]
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid JSON payload"},
        )

    logger.info(
        "OpenPhone webhook ACCEPTED: event=%s data_keys=%s",
        payload.get("event"),
        list((payload.get("data") or {}).keys()),
    )

    # Process webhook in background to respond quickly
    from app.db.session import get_db_context

    async def _process():
        async with get_db_context() as db:
            service = OpenPhoneService(db)
            try:
                message = await service.process_webhook(payload)
                await db.commit()
            except Exception:
                logger.exception("Failed to process OpenPhone webhook")
                await db.rollback()
                return

            # Technician (Quo dispatch chat) routing — runs BEFORE the
            # generic classify path so an inbound job message from a tech
            # is classified via the same pipeline (decided upstream), and
            # an outbound operator dispatch goes to ``dispatched`` instead
            # of being treated as chat noise.
            try:
                tech = await service.resolve_technician_for_message(message)
            except Exception:
                logger.exception(
                    "Failed to resolve technician for OpenPhone message %s", message.id
                )
                tech = None

            if tech is not None:
                try:
                    await service.handle_tech_chat_message(message, tech)
                    await db.commit()
                except Exception:
                    logger.exception(
                        "Failed to handle OpenPhone tech-chat message %s", message.id
                    )
                    await db.rollback()
                return

            # Operator outbound reply on a non-tech conversation. We never
            # classify outbound as a new job, but it may be a job REJECTION
            # ("pass"/"have it"/"<zip> pass"/re-paste with a note) aimed at
            # the company that texted the job in — decline the matching
            # pending Job so the alert engine never flags it.
            if (message.direction or "").lower() == "outgoing":
                try:
                    from app.repositories import openphone as openphone_repo

                    async with get_db_context() as reject_db:
                        fresh = await openphone_repo.get_incoming_message(reject_db, message.id)
                        if fresh is not None:
                            await OpenPhoneService(reject_db).maybe_reject_job(fresh)
                            await reject_db.commit()
                except Exception:
                    logger.exception("Failed reject-detection for OpenPhone message %s", message.id)
                return

            # Default path: inbound from a non-tech sender goes through the
            # normal classification pipeline. Outbound to a non-tech number
            # is otherwise ignored (the operator types customer replies in
            # the OpenPhone mobile app — we never send outbound messages).
            if (
                message.content
                and (message.event_type or "").startswith("message.received")
            ):
                try:
                    from app.services.classification import JobClassificationService

                    async with get_db_context() as classify_db:
                        # Re-load in the fresh session; `message` was
                        # committed in the parent session so classify_db
                        # sees the row.
                        from app.repositories import openphone as openphone_repo

                        fresh = await openphone_repo.get_incoming_message(classify_db, message.id)
                        if fresh is not None:
                            classification_svc = JobClassificationService(classify_db)
                            await classification_svc.classify_message(fresh)
                            await classify_db.commit()
                except Exception:
                    logger.exception("Failed to classify message %s", message.id)

    background_tasks.add_task(_process)

    return Response(status_code=status.HTTP_200_OK)


# =============================================================================
# Proxy Endpoints (JWT auth required) — Quo API
# =============================================================================


@router.get("/phone-numbers")
async def list_phone_numbers(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    user_id: str | None = None,
):
    """List phone numbers in the OpenPhone workspace."""
    return await service.list_phone_numbers(user_id=user_id)


@router.get("/phone-numbers/{phone_number_id}")
async def get_phone_number(
    phone_number_id: str,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Get a phone number by ID."""
    return await service.get_phone_number(phone_number_id)


@router.get("/users")
async def list_users(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    max_results: int = 10,
    page_token: str | None = None,
):
    """List users in the OpenPhone workspace."""
    return await service.list_users(max_results=max_results, page_token=page_token)


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Get a user by ID."""
    return await service.get_user(user_id)


@router.get("/messages")
async def list_messages(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    max_results: int = 10,
    page_token: str | None = None,
):
    """List messages via OpenPhone API."""
    return await service.list_messages(max_results=max_results, page_token=page_token)


@router.get("/messages/{message_id}")
async def get_message(
    message_id: str,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Get a message by ID from OpenPhone API."""
    return await service.get_message(message_id)


@router.get("/conversations")
async def list_conversations(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    max_results: int = 10,
    page_token: str | None = None,
    phone_numbers: str | None = None,
    user_id: str | None = None,
):
    """List conversations via OpenPhone API."""
    pn_list = phone_numbers.split(",") if phone_numbers else None
    return await service.list_conversations(
        max_results=max_results,
        page_token=page_token,
        phone_numbers=pn_list,
        user_id=user_id,
    )


@router.get("/webhooks")
async def list_webhooks(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    user_id: str | None = None,
):
    """List webhooks in the OpenPhone workspace."""
    return await service.list_webhooks(user_id=user_id)


@router.get("/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Get a webhook by ID."""
    return await service.get_webhook(webhook_id)


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    request: Request,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Create a new message webhook via OpenPhone API."""
    payload = await request.json()
    return await service.create_message_webhook(**payload)


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Delete a webhook by ID."""
    await service.delete_webhook(webhook_id)


# =============================================================================
# Internal CRUD — Persisted Incoming Messages (JWT auth required)
# =============================================================================


@router.get("/incoming", response_model=IncomingMessageList)
async def list_incoming_messages(
    service: OpenPhoneSvc,
    _user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
):
    """List persisted incoming messages from webhooks."""
    messages, total = await service.list_incoming_messages(skip=skip, limit=limit)
    return IncomingMessageList(
        items=[IncomingMessageRead.model_validate(m) for m in messages],
        total=total,
    )


@router.get("/incoming/{message_id}", response_model=IncomingMessageRead)
async def get_incoming_message(
    message_id: UUID,
    service: OpenPhoneSvc,
    _user: CurrentUser,
):
    """Get a persisted incoming message by ID."""
    message = await service.get_incoming_message(message_id)
    return IncomingMessageRead.model_validate(message)
