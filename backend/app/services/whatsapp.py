"""WhatsApp ingestion service.

Mirrors ``app/services/openphone.py`` — class with ``__init__(self, db)``,
sectioned by ``# ===`` headers, ``db.flush()`` not ``commit()`` (commit
happens in the FastAPI dependency), domain exceptions for errors.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_token,
)
from app.db.models.openphone import MessageSource
from app.db.models.user import User
from app.db.models.whatsapp import WhatsappMessage, WhatsappTrackedChat
from app.repositories import openphone_repo, whatsapp_repo
from app.repositories import user_repo as user_repo_module
from app.schemas.whatsapp import (
    WhatsappMessageBatchError,
    WhatsappMessageBatchIngest,
    WhatsappMessageBatchResult,
    WhatsappTrackedChatCreate,
    WhatsappTrackedChatUpdate,
)

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class WhatsappService:
    """Business logic for the WhatsApp Web ingestion module."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # === Ingestion (extension → server) ===

    async def ingest_batch(
        self,
        payload: WhatsappMessageBatchIngest,
        *,
        background_tasks: "BackgroundTasks | None" = None,
        batch_id: str,
    ) -> WhatsappMessageBatchResult:
        """Upsert a batch of messages from the Chrome extension.

        Two-stage pipeline:
        1. Whitelist check — every unique ``chat_jid`` is resolved against
           the tracked-chats table once. Untracked JIDs are rejected with
           per-item errors; the rest of the batch still processes.
        2. Bulk upsert — all surviving messages are sent through
           ``whatsapp_repo.batch_upsert_messages`` in a SINGLE
           ``INSERT ... ON CONFLICT DO UPDATE`` round-trip. The
           timestamp guard lives in SQL (the ``WHERE`` clause on the
           update), so a re-emit of older messages costs nothing
           server-side beyond the unique-constraint check.

        After the bulk upsert succeeds, each new message is mirrored into
        ``incoming_messages`` with ``source='whatsapp'`` and dispatched to
        ``JobClassificationService`` so the dedup pipeline sees WhatsApp
        messages identically to OpenPhone ones. Classification runs in a
        background task when one is available, or inline otherwise.

        The extension re-sends everything on every chat open, so
        ``skipped`` is typically the dominant counter after the first
        pass — which is the whole point of the bulk path: those
        skipped messages no longer cost N round-trips each.

        A bulk-statement failure (e.g. a column-length violation on one
        row) aborts the whole upsert; we report the error against every
        surviving message rather than silently dropping them.
        """
        errors: list[WhatsappMessageBatchError] = []

        # Whitelist check — one lookup per unique JID. We cache the resolved
        # WhatsappTrackedChat rows so the per-message loop below can read
        # ``chat_role`` without re-querying.
        seen_jids: set[str] = set()
        valid_jids: set[str] = set()
        chats_by_jid: dict[str, WhatsappTrackedChat | None] = {}
        for msg in payload.messages:
            if msg.chat_jid in seen_jids:
                continue
            seen_jids.add(msg.chat_jid)
            chat = await whatsapp_repo.get_chat_by_jid(self.db, msg.chat_jid)
            chats_by_jid[msg.chat_jid] = chat
            if chat and chat.is_active:
                valid_jids.add(msg.chat_jid)
        invalid_jids = seen_jids - valid_jids
        if invalid_jids:
            logger.warning(
                "Rejected %d messages for untracked chats: %s",
                sum(1 for m in payload.messages if m.chat_jid in invalid_jids),
                ", ".join(sorted(invalid_jids)),
            )
        logger.info(
            "WHITELIST_RESOLVED batch_id=%s valid=%d invalid=%d invalid_jids=%s",
            batch_id,
            len(valid_jids),
            len(invalid_jids),
            sorted(invalid_jids) or "[]",
        )

        # Partition: invalid → per-item error; valid → bulk upsert.
        # Keep the original index alongside each valid message so we can
        # map bulk errors back to the request's index space.
        valid_items: list[tuple[int, object]] = []
        for idx, msg in enumerate(payload.messages):
            if msg.chat_jid in invalid_jids:
                errors.append(
                    WhatsappMessageBatchError(
                        index=idx,
                        error=f"chat_jid {msg.chat_jid!r} is not in the tracked whitelist",
                    )
                )
                continue
            valid_items.append((idx, msg))

        inserted = updated = skipped = deduplicated = 0
        bulk_succeeded = False
        if valid_items:
            try:
                bulk_result = await whatsapp_repo.batch_upsert_messages(
                    self.db, [m for _, m in valid_items]
                )
                inserted = bulk_result.inserted
                updated = bulk_result.updated
                skipped = bulk_result.skipped
                deduplicated = bulk_result.deduplicated
                bulk_succeeded = True
                logger.info(
                    "BULK_UPSERT_DONE batch_id=%s inserted=%d updated=%d "
                    "skipped=%d deduplicated=%d",
                    batch_id,
                    inserted,
                    updated,
                    skipped,
                    deduplicated,
                )
            except Exception as e:
                # Whole batch failed — surface against every valid message
                # so the extension can re-emit them. The alternative
                # (drop the batch silently) is worse: the extension's
                # BUFFER would grow without bound.
                logger.exception("Bulk upsert of whatsapp messages failed")
                for orig_idx, _ in valid_items:
                    errors.append(WhatsappMessageBatchError(index=orig_idx, error=str(e)))

        # Bump last_scraped_at for each touched chat (best-effort, non-fatal).
        now = datetime.now(UTC)
        for jid in valid_jids:
            chat = await whatsapp_repo.get_chat_by_jid(self.db, jid)
            if chat:
                await whatsapp_repo.update_chat_last_seen(
                    self.db,
                    chat,
                    wa_message_id="",  # batch endpoint doesn't carry this
                    scraped_at=now,
                )

        # Dispatch classification for every surviving message. We mirror
        # into incoming_messages (source='whatsapp') so the dedup pipeline
        # sees OpenPhone and WhatsApp through the same IncomingMessage
        # interface. Errors are caught per-message — a bad classification
        # must not fail the rest of the batch.
        #
        # batch_id is stamped into IncomingMessage.raw_payload so the
        # background classification task (which has no access to the HTTP
        # request) can pull it back out and log with the same correlation id.
        mirrored = 0
        dispatched = 0
        tech_reply_enqueued = 0
        if bulk_succeeded:
            from app.services.classification import CLOSING_CHAT_JID
            from app.services.closing_signal import ClosingSignalService

            for _, msg in valid_items:
                # Closing-signal gate — a tech's payment/closing re-paste in
                # ANY tracked chat (the Dispatch Closing group excepted; it
                # has its own branch in classification) marks the matched Job
                # ``completed`` and short-circuits all downstream handling so
                # the re-pasted address never spawns a linked DispatchJob.
                if msg.chat_jid != CLOSING_CHAT_JID:
                    try:
                        handled = await ClosingSignalService(self.db).detect_and_complete(
                            body=getattr(msg, "body", None) or "",
                            channel="whatsapp",
                            source_meta={
                                "chat_jid": getattr(msg, "chat_jid", None),
                                "wa_message_id": getattr(msg, "wa_message_id", None),
                                "sender_name": getattr(msg, "sender_name", None),
                                "batch_id": batch_id,
                            },
                            at=getattr(msg, "timestamp", None),
                        )
                    except Exception:
                        logger.exception(
                            "Failed closing-signal gate for whatsapp msg %s in %s",
                            getattr(msg, "wa_message_id", "?"),
                            getattr(msg, "chat_jid", "?"),
                        )
                        handled = False
                    if handled:
                        continue

                # Chat-role branch — operator↔tech chatter must NOT be
                # mirrored into incoming_messages (those are customer
                # traffic) and must NOT enter the dedup/classify pipeline.
                # The WhatsAppMessage row was already upserted above, so
                # the downstream parser has a target to look up.
                chat = chats_by_jid.get(msg.chat_jid)
                if chat is not None and chat.chat_role == "tech_dispatch":
                    try:
                        if msg.is_from_me:
                            await self._handle_operator_dispatch(msg, chat, batch_id)
                            dispatched += 1
                        else:
                            await self._handle_tech_reply(msg, chat, batch_id, background_tasks)
                            tech_reply_enqueued += 1
                    except Exception:
                        logger.exception(
                            "Failed to handle dispatch/tech-reply for whatsapp msg %s in %s",
                            getattr(msg, "wa_message_id", "?"),
                            getattr(msg, "chat_jid", "?"),
                        )
                    continue

                # Operator reject branch — an operator reply ("pass",
                # "have it", "<zip> pass", or a re-paste of the job with a
                # short note) in a job-source chat declines the most-recent
                # pending job from that chat. On a hit the job is
                # transitioned to the terminal ``rejected`` status and this
                # message is NOT mirrored/classified (a re-paste would
                # otherwise spawn a spurious linked DispatchJob).
                if getattr(msg, "is_from_me", False):
                    try:
                        if await self._maybe_reject_job(msg, batch_id):
                            continue
                    except Exception:
                        logger.exception(
                            "Failed reject-detection for whatsapp msg %s in %s",
                            getattr(msg, "wa_message_id", "?"),
                            getattr(msg, "chat_jid", "?"),
                        )

                try:
                    incoming = await openphone_repo.create_incoming_message(
                        self.db,
                        openphone_id=None,
                        direction="incoming",
                        from_number=None,
                        to_numbers=[],
                        content=getattr(msg, "body", None) or "",
                        status=None,
                        event_type="whatsapp.received",
                        phone_number_id=None,
                        raw_payload={
                            "wa_message_id": getattr(msg, "wa_message_id", None),
                            "chat_jid": getattr(msg, "chat_jid", None),
                            "sender_name": getattr(msg, "sender_name", None),
                            "batch_id": batch_id,
                            "timestamp": (
                                ts.isoformat() if (ts := getattr(msg, "timestamp", None)) else None
                            ),
                        },
                        source=MessageSource.WHATSAPP.value,
                    )
                    await self.db.flush()
                    mirrored += 1
                    if background_tasks is not None:
                        background_tasks.add_task(self._classify_in_background, incoming.id)
                        logger.info(
                            "CLASSIFY_ENQUEUED batch_id=%s incoming_id=%s mode=bg",
                            batch_id,
                            incoming.id,
                        )
                    else:
                        logger.info(
                            "CLASSIFY_ENQUEUED batch_id=%s incoming_id=%s mode=inline",
                            batch_id,
                            incoming.id,
                        )
                        await self._classify_in_background(incoming.id)
                except Exception:
                    logger.exception(
                        "Failed to enqueue classification for whatsapp msg %s in %s",
                        getattr(msg, "wa_message_id", "?"),
                        getattr(msg, "chat_jid", "?"),
                    )
            logger.info(
                "MIRROR_DONE batch_id=%s mirrored=%d of %d dispatched=%d tech_reply_enqueued=%d",
                batch_id,
                mirrored,
                len(valid_items),
                dispatched,
                tech_reply_enqueued,
            )

            # Commit BEFORE returning so the FastAPI BackgroundTasks queued
            # above can see the mirrored incoming_messages rows. Starlette
            # fires background tasks before the get_db_session dependency
            # reaches its `await session.commit()`, so without this explicit
            # commit the bg task's fresh session sees an empty snapshot and
            # `db.get(IncomingMessage, id)` returns None → classification is
            # silently skipped for every message.
            await self.db.commit()
            logger.info("MIRROR_COMMITTED batch_id=%s", batch_id)

        return WhatsappMessageBatchResult(
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            deduplicated=deduplicated,
            errors=errors,
        )

    @staticmethod
    async def _classify_in_background(incoming_message_id: UUID) -> None:
        """Run classification on a freshly created IncomingMessage.

        Opens a new DB session (the request session may be closed by the
        time the background task runs). Failures are logged, not raised
        — a bad classification must not crash the worker.
        """
        from app.db.session import get_db_context
        from app.services.classification import JobClassificationService

        logger.info("CLASSIFY_BG_ENTER incoming_id=%s", incoming_message_id)
        try:
            async with get_db_context() as db:
                message = await openphone_repo.get_incoming_message(db, incoming_message_id)
                if message is None:
                    logger.warning(
                        "CLASSIFY_BG_NOT_FOUND incoming_id=%s — bg task ran but row not visible "
                        "(parent txn maybe never committed)",
                        incoming_message_id,
                    )
                    return
                # Pull the SW's batch_id back out of the IncomingMessage
                # so classification logs stay correlated with the SW chunk
                # and the route's BATCH_RECEIVED/BATCH_PROCESSED lines.
                batch_id = (message.raw_payload or {}).get("batch_id") or "-"
                content_preview = (message.content or "")[:60]
                logger.info(
                    "CLASSIFY_START batch_id=%s incoming_id=%s content_len=%d preview=%r",
                    batch_id,
                    incoming_message_id,
                    len(message.content or ""),
                    content_preview,
                )
                svc = JobClassificationService(db)
                await svc.classify_message(message)
                await db.commit()
                logger.info(
                    "CLASSIFY_BG_DONE batch_id=%s incoming_id=%s",
                    batch_id,
                    incoming_message_id,
                )
        except Exception as exc:
            # Single-line grep-friendly summary with exception type, plus
            # the full traceback below it for diagnosis.
            logger.error(
                "CLASSIFY_FAILED incoming_id=%s exc_type=%s exc=%r",
                incoming_message_id,
                type(exc).__name__,
                exc,
            )
            logger.exception(
                "Background classification failed for IncomingMessage %s",
                incoming_message_id,
            )

    # === Tech-dispatch chat branch ===
    #
    # When a tracked chat is tagged ``chat_role='tech_dispatch'`` it's the
    # operator↔technician chat for a specific tech (not customer traffic).
    # Messages in those chats short-circuit the customer-facing mirror +
    # classify path and instead drive the lifecycle pipeline:
    #
    # - Outgoing (is_from_me=True) messages are operator dispatches.
    #   Fuzzy-match the body against pending Jobs on address + phone; on
    #   hit, transition the Job to ``dispatched``. On miss, raise an
    #   ``dispatch_no_match`` alert so the operator can correct from
    #   ``/jobs``.
    # - Incoming messages are tech replies. Hand off to the async parser
    #   (LLM intent → lifecycle transition + outbound draft). The parser
    #   runs in a BackgroundTask so the LLM call never blocks the SW ack.

    async def _handle_operator_dispatch(
        self,
        msg: "object",
        chat: WhatsappTrackedChat,
        batch_id: str,
    ) -> None:
        """Parse an outgoing operator message and transition the matching Job.

        Steps:
        1. Normalize the body → ``(street_number, street_name, zip, phone)``.
        2. ``job_repo.find_dispatch_target`` — most-recent pending Job
           matching all provided fields.
        3. Hit → ``LifecycleService.transition(to_status='dispatched', ...)``.
           Miss → ``dispatch_no_match`` alert (operator resolves from the
           dashboard; the Job remains pending).
        """
        from app.db.models.job_lifecycle_event import LifecycleEventSource
        from app.repositories import alert as alert_repo
        from app.repositories import job as job_repo
        from app.repositories import technician as technician_repo
        from app.services.address_normalizer import (
            normalize_address,
            normalize_phone,
        )
        from app.services.classification import PHONE_PATTERN
        from app.services.lifecycle import LifecycleService

        body = (getattr(msg, "body", None) or "").strip()
        if not body:
            logger.warning(
                "DISPATCH_EMPTY_BODY chat_jid=%s wa_message_id=%s batch_id=%s",
                chat.chat_jid,
                getattr(msg, "wa_message_id", "?"),
                batch_id,
            )
            return

        normalized = normalize_address(body)
        phone_match = PHONE_PATTERN.search(body)
        phone_e164 = normalize_phone(phone_match.group(0)) if phone_match else None

        logger.info(
            "DISPATCH_PARSE chat_jid=%s wa_message_id=%s street_number=%s "
            "street_name=%s zip=%s phone=%s",
            chat.chat_jid,
            getattr(msg, "wa_message_id", "?"),
            normalized.street_number,
            normalized.street_name,
            normalized.zip_code,
            phone_e164,
        )

        job = await job_repo.find_dispatch_target(
            self.db,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            zip_code=normalized.zip_code,
            customer_phone_e164=phone_e164,
        )
        if job is None:
            await alert_repo.create_or_get_open(
                self.db,
                kind="dispatch_no_match",
                chat_jid=chat.chat_jid,
                payload={
                    "wa_message_id": getattr(msg, "wa_message_id", None),
                    "body_preview": body[:120],
                    "street_number": normalized.street_number,
                    "street_name": normalized.street_name,
                    "zip_code": normalized.zip_code,
                    "phone_e164": phone_e164,
                },
            )
            logger.warning(
                "DISPATCH_NO_MATCH chat_jid=%s wa_message_id=%s batch_id=%s",
                chat.chat_jid,
                getattr(msg, "wa_message_id", "?"),
                batch_id,
            )
            return

        # Look up the technician tied to this chat (for the draft body
        # and the audit payload). Optional — dispatch proceeds even if
        # the chat isn't yet linked.
        technician = await technician_repo.get_by_chat_jid(self.db, chat.chat_jid)
        tech_name = technician.name if technician is not None else None

        payload = {
            "chat_jid": chat.chat_jid,
            "wa_message_id": getattr(msg, "wa_message_id", None),
            "technician_id": str(technician.id) if technician else None,
        }

        try:
            await LifecycleService(self.db).transition(
                job=job,
                to_status="dispatched",
                source=LifecycleEventSource.OPERATOR_WHATSAPP,
                payload=payload,
                at=getattr(msg, "timestamp", None),
            )
        except Exception:
            logger.exception(
                "DISPATCH_TRANSITION_FAILED job_id=%s chat_jid=%s wa_message_id=%s",
                job.id,
                chat.chat_jid,
                getattr(msg, "wa_message_id", "?"),
            )
            return

        logger.info(
            "DISPATCH_TRANSITIONED job_id=%s chat_jid=%s wa_message_id=%s batch_id=%s tech=%s",
            job.id,
            chat.chat_jid,
            getattr(msg, "wa_message_id", "?"),
            batch_id,
            tech_name,
        )

    async def _handle_tech_reply(
        self,
        msg: "object",
        chat: WhatsappTrackedChat,
        batch_id: str,
        background_tasks: "BackgroundTasks | None",
    ) -> None:
        """Hand a tech reply off to the async parser.

        The LLM call can take seconds and must never block the SW ack, so
        we enqueue a background task that opens a fresh DB session and
        runs ``parse_tech_reply_in_background`` — same pattern as
        ``_classify_in_background``.
        """
        wa_message_id = getattr(msg, "wa_message_id", None)
        if not wa_message_id:
            logger.warning(
                "TECH_REPLY_NO_WA_ID chat_jid=%s batch_id=%s",
                chat.chat_jid,
                batch_id,
            )
            return

        if background_tasks is None:
            # Fallback path: run inline. ``BackgroundTasks`` is None in
            # tests; production always passes one.
            from app.services.tech_reply_parser import parse_tech_reply_in_background

            logger.info(
                "TECH_REPLY_ENQUEUED chat_jid=%s wa_message_id=%s mode=inline",
                chat.chat_jid,
                wa_message_id,
            )
            await parse_tech_reply_in_background(
                wa_message_id=wa_message_id,
                chat_jid=chat.chat_jid,
                batch_id=batch_id,
            )
            return

        from app.services.tech_reply_parser import parse_tech_reply_in_background

        background_tasks.add_task(
            parse_tech_reply_in_background,
            wa_message_id=wa_message_id,
            chat_jid=chat.chat_jid,
            batch_id=batch_id,
        )
        logger.info(
            "TECH_REPLY_ENQUEUED chat_jid=%s wa_message_id=%s mode=bg",
            chat.chat_jid,
            wa_message_id,
        )

    # === Operator reject branch ===

    async def _maybe_reject_job(self, msg: "object", batch_id: str) -> bool:
        """Reject the pending job an operator reply declines, if any.

        Returns ``True`` when a job was transitioned to ``rejected`` (the
        caller then skips mirror/classify for this message). Returns
        ``False`` when the message is not a reject, there is no pending job
        from this chat to reject, or the reply falls outside the
        two-operator-message window.

        The flow is: (1) find the most-recent still-pending job originating
        from this chat before the reply, (2) confirm the reply is a reject
        signal (phrase or a re-paste of that job's body with a note),
        (3) confirm the reply is within the next two operator messages, and
        (4) transition the job to the terminal ``rejected`` status via the
        lifecycle gate (which also auto-resolves any open alerts).
        """
        from app.db.models.job_lifecycle_event import LifecycleEventSource
        from app.repositories import job as job_repo
        from app.services import reject_detector
        from app.services.lifecycle import LifecycleService, LifecycleStatus

        body = (getattr(msg, "body", None) or "").strip()
        chat_jid = getattr(msg, "chat_jid", None)
        timestamp = getattr(msg, "timestamp", None)
        if not body or not chat_jid or timestamp is None:
            return False

        candidate = await job_repo.find_reject_candidate(
            self.db, chat_jid=chat_jid, before=timestamp
        )
        if candidate is None:
            return False
        job, source_body = candidate

        if not reject_detector.is_reject_signal(body, source_body):
            return False

        operator_msg_count = await whatsapp_repo.count_operator_messages_between(
            self.db,
            chat_jid=chat_jid,
            after=job.first_message_at,
            until=timestamp,
        )
        if operator_msg_count > 2:
            logger.info(
                "REJECT_TOO_LATE batch_id=%s chat_jid=%s job_id=%s operator_msgs=%d",
                batch_id,
                chat_jid,
                job.id,
                operator_msg_count,
            )
            return False

        await LifecycleService(self.db).transition(
            job=job,
            to_status=LifecycleStatus.REJECTED,
            source=LifecycleEventSource.OPERATOR_REJECT,
            payload={
                "chat_jid": chat_jid,
                "wa_message_id": getattr(msg, "wa_message_id", None),
                "body_preview": body[:120],
                "operator_msg_index": operator_msg_count,
                "batch_id": batch_id,
            },
            at=timestamp,
        )
        logger.info(
            "REJECT_APPLIED batch_id=%s chat_jid=%s job_id=%s wa_message_id=%s operator_msgs=%d",
            batch_id,
            chat_jid,
            job.id,
            getattr(msg, "wa_message_id", None),
            operator_msg_count,
        )
        return True

    # === Tracked Chats ===

    async def list_tracked_chats(
        self,
        *,
        include_inactive: bool = False,
    ) -> list[WhatsappTrackedChat]:
        """List tracked chats (whitelist)."""
        chats = await whatsapp_repo.list_active_chats(self.db, include_inactive=include_inactive)
        return chats

    async def create_tracked_chat(self, data: WhatsappTrackedChatCreate) -> WhatsappTrackedChat:
        """Add a chat to the whitelist. Idempotent on JID."""
        existing = await whatsapp_repo.get_chat_by_jid(self.db, data.chat_jid)
        if existing:
            # Update display name + re-activate if it was deactivated.
            updated = await whatsapp_repo.update_chat_display_name(
                self.db, existing, display_name=data.display_name
            )
            if not existing.is_active:
                updated = await whatsapp_repo.set_chat_active(self.db, updated, is_active=True)
            return updated
        return await whatsapp_repo.upsert_chat(
            self.db,
            chat_jid=data.chat_jid,
            display_name=data.display_name,
            is_group=data.is_group,
        )

    async def update_tracked_chat(
        self, chat_jid: str, data: WhatsappTrackedChatUpdate
    ) -> WhatsappTrackedChat:
        """Rename, activate/deactivate, or retag a tracked chat."""
        chat = await whatsapp_repo.get_chat_by_jid(self.db, chat_jid)
        if not chat:
            raise NotFoundError(
                message="Tracked chat not found",
                details={"chat_jid": chat_jid},
            )
        if data.display_name is not None:
            chat = await whatsapp_repo.update_chat_display_name(
                self.db, chat, display_name=data.display_name
            )
        if data.is_active is not None:
            chat = await whatsapp_repo.set_chat_active(self.db, chat, is_active=data.is_active)
        if data.chat_role is not None:
            chat = await whatsapp_repo.update_chat_role(self.db, chat, chat_role=data.chat_role)
        return chat

    async def deactivate_tracked_chat(self, chat_jid: str) -> None:
        """Soft-delete a tracked chat (history preserved)."""
        chat = await whatsapp_repo.get_chat_by_jid(self.db, chat_jid)
        if not chat:
            raise NotFoundError(
                message="Tracked chat not found",
                details={"chat_jid": chat_jid},
            )
        await whatsapp_repo.set_chat_active(self.db, chat, is_active=False)

    # === Messages (browse / search) ===

    async def list_messages(
        self,
        *,
        chat_jid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sender: str | None = None,
        contains: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[WhatsappMessage], int]:
        """List messages with optional filters. Returns ``(rows, total)``."""
        messages = await whatsapp_repo.list_messages(
            self.db,
            chat_jid=chat_jid,
            since=since,
            until=until,
            sender=sender,
            contains=contains,
            skip=skip,
            limit=limit,
        )
        total = await whatsapp_repo.count_messages(
            self.db,
            chat_jid=chat_jid,
            since=since,
            until=until,
            sender=sender,
            contains=contains,
        )
        return messages, total

    # === Service-Account Auth ===

    async def exchange_api_key(self, user: User) -> dict:
        """Exchange a verified service-account API key for a JWT pair.

        ``user`` must already be authenticated via ``authenticate_service_key``.
        Returns ``{access_token, refresh_token, token_type, expires_in}``.

        The ``svc: true`` claim is set on both tokens so ``get_service_account``
        can distinguish them from regular user JWTs.
        """
        extra_claims = {"svc": True, "svc_name": user.service_account_name}
        access = create_access_token(user.id, extra_claims=extra_claims)
        refresh = create_refresh_token(user.id, extra_claims=extra_claims)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    async def refresh_service_token(self, refresh_token: str) -> dict:
        """Rotate a service-account refresh token. Returns a new JWT pair.

        The ``svc: true`` claim is preserved across the rotation.
        """
        payload = verify_token(refresh_token)
        if payload is None or payload.get("type") != "refresh":
            raise AuthenticationError(message="Invalid or expired refresh token")
        if payload.get("svc") is not True:
            raise AuthenticationError(message="Not a service-account refresh token")

        user_id = payload.get("sub")
        if user_id is None:
            raise AuthenticationError(message="Invalid token payload")

        user = await user_repo_module.get_by_id(self.db, UUID(user_id))
        if not user or not user.is_active or not user.is_service_account:
            raise AuthenticationError(message="Service account invalid or disabled")

        return await self.exchange_api_key(user)
