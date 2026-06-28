"""Tests for the chat-role branch in WhatsApp ingestion (Phase 3).

Covers:
- ``ingest_batch``: messages in ``chat_role='tech_dispatch'`` chats
  short-circuit the customer IncomingMessage mirror and route to the
  dispatch handlers instead. Non-tech chats continue to mirror + classify.
- ``_handle_operator_dispatch``: parses the operator's body for address
  + phone, fuzzy-matches a pending Job, transitions it to ``dispatched``
  on hit, or raises a ``dispatch_no_match`` alert on miss.
- ``_handle_tech_reply``: enqueues ``parse_tech_reply_in_background`` on
  the provided ``BackgroundTasks`` (or runs inline when none was passed).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.whatsapp import WhatsappTrackedChat
from app.schemas.whatsapp import (
    WhatsappMessageBatchIngest,
    WhatsappMessageCreate,
)
from app.services.whatsapp import WhatsappService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chat(
    *, chat_jid: str = "tech-chat@g.us", chat_role: str = "tech_dispatch"
) -> WhatsappTrackedChat:
    chat = WhatsappTrackedChat(chat_jid=chat_jid, display_name="Tech Chat")
    chat.is_active = True
    chat.chat_role = chat_role
    return chat


def _query_result(return_value) -> MagicMock:
    """Mock ``Result`` whose ``scalar_one_or_none`` returns ``return_value``."""

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=return_value)
    return result


def _scalars_result(values) -> MagicMock:
    """Mock ``Result`` whose ``scalars().all()`` returns ``values``."""

    scalars = MagicMock()
    scalars.all = MagicMock(return_value=list(values))
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


# ---------------------------------------------------------------------------
# ingest_batch: chat-role branch routing
# ---------------------------------------------------------------------------


class TestIngestBatchChatRoleBranch:
    @pytest.mark.anyio
    async def test_tech_dispatch_outgoing_msg_routes_to_operator_dispatch(self):
        """An outgoing (``is_from_me=True``) message in a tech-dispatch
        chat must NOT mirror to incoming_messages — it goes to
        ``_handle_operator_dispatch`` instead."""
        tech_chat = _make_chat(chat_role="tech_dispatch")
        msg = WhatsappMessageCreate(
            wa_message_id="wamid.disp.1",
            chat_jid=tech_chat.chat_jid,
            timestamp=datetime.now(UTC),
            is_from_me=True,
            body="123 Main St Chicago IL 60601 / 3125551234",
        )
        fake_bulk = MagicMock(inserted=1, updated=0, skipped=0, deduplicated=0)

        db = MagicMock()
        db.execute = AsyncMock(return_value=_query_result(tech_chat))
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        handle_dispatch = AsyncMock()
        handle_reply = AsyncMock()
        bg_tasks = MagicMock()

        service = WhatsappService(db)
        with (
            patch(
                "app.services.whatsapp.whatsapp_repo.batch_upsert_messages",
                new=AsyncMock(return_value=fake_bulk),
            ),
            patch.object(service, "_handle_operator_dispatch", new=handle_dispatch),
            patch.object(service, "_handle_tech_reply", new=handle_reply),
            patch(
                "app.services.whatsapp.openphone_repo.create_incoming_message",
                new=AsyncMock(),
            ) as create_incoming,
        ):
            result = await service.ingest_batch(
                WhatsappMessageBatchIngest(messages=[msg]),
                background_tasks=bg_tasks,
                batch_id="batch-1",
            )

        assert result.inserted == 1
        handle_dispatch.assert_awaited_once()
        # The first positional arg is the message; the chat is the second.
        args = handle_dispatch.await_args.args
        assert args[0].wa_message_id == "wamid.disp.1"
        assert args[1].chat_jid == tech_chat.chat_jid
        # Customer mirror must NOT have been called.
        create_incoming.assert_not_called()
        handle_reply.assert_not_called()

    @pytest.mark.anyio
    async def test_tech_dispatch_incoming_msg_routes_to_tech_reply(self):
        """An incoming (``is_from_me=False``) message in a tech-dispatch
        chat must NOT mirror — it goes to ``_handle_tech_reply`` instead,
        which enqueues a background task."""
        tech_chat = _make_chat(chat_role="tech_dispatch")
        msg = WhatsappMessageCreate(
            wa_message_id="wamid.reply.1",
            chat_jid=tech_chat.chat_jid,
            timestamp=datetime.now(UTC),
            is_from_me=False,
            body="on the way",
        )
        fake_bulk = MagicMock(inserted=1, updated=0, skipped=0, deduplicated=0)

        db = MagicMock()
        db.execute = AsyncMock(return_value=_query_result(tech_chat))
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        handle_dispatch = AsyncMock()
        handle_reply = AsyncMock()
        bg_tasks = MagicMock()

        service = WhatsappService(db)
        with (
            patch(
                "app.services.whatsapp.whatsapp_repo.batch_upsert_messages",
                new=AsyncMock(return_value=fake_bulk),
            ),
            patch.object(service, "_handle_operator_dispatch", new=handle_dispatch),
            patch.object(service, "_handle_tech_reply", new=handle_reply),
            patch(
                "app.services.whatsapp.openphone_repo.create_incoming_message",
                new=AsyncMock(),
            ) as create_incoming,
        ):
            await service.ingest_batch(
                WhatsappMessageBatchIngest(messages=[msg]),
                background_tasks=bg_tasks,
                batch_id="batch-1",
            )

        handle_reply.assert_awaited_once()
        handle_dispatch.assert_not_called()
        create_incoming.assert_not_called()

    @pytest.mark.anyio
    async def test_non_tech_chat_still_mirrors_to_incoming_messages(self):
        """Regression guard: a chat tagged ``chat_role='other'`` keeps
        going through the customer-facing mirror + classify path."""
        other_chat = _make_chat(chat_role="other")
        msg = WhatsappMessageCreate(
            wa_message_id="wamid.cust.1",
            chat_jid=other_chat.chat_jid,
            timestamp=datetime.now(UTC),
            is_from_me=False,
            body="customer message",
        )
        fake_bulk = MagicMock(inserted=1, updated=0, skipped=0, deduplicated=0)
        incoming = MagicMock()
        incoming.id = uuid4()

        db = MagicMock()
        db.execute = AsyncMock(return_value=_query_result(other_chat))
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        handle_dispatch = AsyncMock()
        handle_reply = AsyncMock()
        bg_tasks = MagicMock()
        classify_bg = AsyncMock()

        service = WhatsappService(db)
        with (
            patch(
                "app.services.whatsapp.whatsapp_repo.batch_upsert_messages",
                new=AsyncMock(return_value=fake_bulk),
            ),
            patch.object(service, "_handle_operator_dispatch", new=handle_dispatch),
            patch.object(service, "_handle_tech_reply", new=handle_reply),
            patch(
                "app.services.whatsapp.openphone_repo.create_incoming_message",
                new=AsyncMock(return_value=incoming),
            ) as create_incoming,
            patch.object(service, "_classify_in_background", new=classify_bg),
        ):
            await service.ingest_batch(
                WhatsappMessageBatchIngest(messages=[msg]),
                background_tasks=bg_tasks,
                batch_id="batch-1",
            )

        create_incoming.assert_awaited_once()
        handle_dispatch.assert_not_called()
        handle_reply.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_operator_dispatch
# ---------------------------------------------------------------------------


class TestHandleOperatorDispatch:
    @pytest.mark.anyio
    async def test_happy_path_transitions_matched_job_to_dispatched(self):
        chat = _make_chat()
        msg = MagicMock()
        msg.body = "123 Main St Chicago IL 60601 / 3125551234"
        msg.wa_message_id = "wamid.disp.1"

        job = MagicMock()
        job.id = uuid4()

        transition = AsyncMock(return_value=(uuid4(), []))
        service = MagicMock()
        service.transition = transition
        lifecycle_cls = MagicMock(return_value=service)

        db = AsyncMock()
        service_inst = WhatsappService(db)

        with (
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(return_value=job),
            ),
            patch(
                "app.repositories.technician.get_by_chat_jid",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=AsyncMock(),
            ) as alert_create,
            patch("app.services.lifecycle.LifecycleService", new=lifecycle_cls),
        ):
            await service_inst._handle_operator_dispatch(msg, chat, batch_id="batch-1")

        alert_create.assert_not_called()
        transition.assert_awaited_once()
        kwargs = transition.await_args.kwargs
        assert kwargs["job"] is job
        assert kwargs["to_status"] == "dispatched"
        assert kwargs["source"] == "operator_whatsapp"
        assert kwargs["payload"]["chat_jid"] == chat.chat_jid
        assert kwargs["payload"]["wa_message_id"] == "wamid.disp.1"
        assert kwargs["payload"]["technician_id"] is None

    @pytest.mark.anyio
    async def test_no_match_creates_dispatch_no_match_alert(self):
        chat = _make_chat()
        msg = MagicMock()
        msg.body = "999 Imaginary Way Chicago IL 60601 / 3125559999"
        msg.wa_message_id = "wamid.disp.miss"

        db = AsyncMock()
        service = WhatsappService(db)

        alert_create = AsyncMock()

        with (
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=alert_create,
            ),
        ):
            await service._handle_operator_dispatch(msg, chat, batch_id="batch-1")

        alert_create.assert_awaited_once()
        kwargs = alert_create.await_args.kwargs
        assert kwargs["kind"] == "dispatch_no_match"
        assert kwargs["chat_jid"] == chat.chat_jid
        assert kwargs["payload"]["wa_message_id"] == "wamid.disp.miss"
        assert kwargs["payload"]["street_number"] == "999"

    @pytest.mark.anyio
    async def test_empty_body_returns_without_action(self):
        chat = _make_chat()
        msg = MagicMock()
        msg.body = ""
        msg.wa_message_id = "wamid.disp.empty"

        db = AsyncMock()
        service = WhatsappService(db)

        find_target = AsyncMock()
        alert_create = AsyncMock()

        with (
            patch("app.repositories.job.find_dispatch_target", new=find_target),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=alert_create,
            ),
        ):
            await service._handle_operator_dispatch(msg, chat, batch_id="batch-1")

        find_target.assert_not_called()
        alert_create.assert_not_called()

    @pytest.mark.anyio
    async def test_linked_technician_is_carried_in_payload(self):
        chat = _make_chat()
        msg = MagicMock()
        msg.body = "123 Main St Chicago IL 60601 / 3125551234"
        msg.wa_message_id = "wamid.disp.1"

        job = MagicMock()
        job.id = uuid4()

        technician = MagicMock()
        technician.id = uuid4()
        technician.name = "Mike's Plumbing"

        transition = AsyncMock(return_value=uuid4())
        service_mock = MagicMock()
        service_mock.transition = transition
        lifecycle_cls = MagicMock(return_value=service_mock)

        db = AsyncMock()
        service = WhatsappService(db)

        with (
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(return_value=job),
            ),
            patch(
                "app.repositories.technician.get_by_chat_jid",
                new=AsyncMock(return_value=technician),
            ),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=AsyncMock(),
            ),
            patch("app.services.lifecycle.LifecycleService", new=lifecycle_cls),
        ):
            await service._handle_operator_dispatch(msg, chat, batch_id="batch-1")

        kwargs = transition.await_args.kwargs
        # The technician id is stamped on the event payload for audit —
        # the operator types the actual reply natively in WhatsApp, so
        # there is no draft body to populate with the tech name anymore.
        assert kwargs["payload"]["technician_id"] == str(technician.id)
        assert "tech_name" not in kwargs


# ---------------------------------------------------------------------------
# _handle_tech_reply
# ---------------------------------------------------------------------------


class TestHandleTechReply:
    @pytest.mark.anyio
    async def test_enqueues_background_task_when_provided(self):
        chat = _make_chat()
        msg = MagicMock()
        msg.wa_message_id = "wamid.reply.1"
        bg_tasks = MagicMock()

        db = AsyncMock()
        service = WhatsappService(db)

        with patch(
            "app.services.tech_reply_parser.parse_tech_reply_in_background",
            new=AsyncMock(),
        ) as parser:
            await service._handle_tech_reply(
                msg, chat, batch_id="batch-1", background_tasks=bg_tasks
            )

        bg_tasks.add_task.assert_called_once()
        # Verify the task function + kwargs were passed through unchanged.
        args = bg_tasks.add_task.call_args
        assert args.args[0] is parser
        assert args.kwargs["wa_message_id"] == "wamid.reply.1"
        assert args.kwargs["chat_jid"] == chat.chat_jid
        assert args.kwargs["batch_id"] == "batch-1"

    @pytest.mark.anyio
    async def test_runs_inline_when_no_background_tasks(self):
        """In tests (and anywhere BackgroundTasks isn't passed) we fall
        back to running the parser inline rather than silently dropping
        the reply."""
        chat = _make_chat()
        msg = MagicMock()
        msg.wa_message_id = "wamid.reply.2"

        db = AsyncMock()
        service = WhatsappService(db)

        with patch(
            "app.services.tech_reply_parser.parse_tech_reply_in_background",
            new=AsyncMock(),
        ) as parser:
            await service._handle_tech_reply(msg, chat, batch_id="batch-1", background_tasks=None)

        parser.assert_awaited_once_with(
            wa_message_id="wamid.reply.2",
            chat_jid=chat.chat_jid,
            batch_id="batch-1",
        )

    @pytest.mark.anyio
    async def test_no_wa_message_id_returns_silently(self):
        """Defensive: a message without ``wa_message_id`` cannot be
        resolved later; we log and skip rather than enqueue a doomed task."""
        chat = _make_chat()
        msg = MagicMock()
        msg.wa_message_id = None
        bg_tasks = MagicMock()

        db = AsyncMock()
        service = WhatsappService(db)

        with patch(
            "app.services.tech_reply_parser.parse_tech_reply_in_background",
            new=AsyncMock(),
        ) as parser:
            await service._handle_tech_reply(
                msg, chat, batch_id="batch-1", background_tasks=bg_tasks
            )

        parser.assert_not_called()
        bg_tasks.add_task.assert_not_called()
