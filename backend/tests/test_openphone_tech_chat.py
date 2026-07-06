"""Tests for the OpenPhone (Quo) technician-chat branch.

Covers:
- ``OpenPhoneService.resolve_technician_for_message``: phone match on
  inbound (``from_number``) and outbound (``to_numbers``) sides.
- ``OpenPhoneService.handle_tech_chat_message``: disambiguation by the
  ``_is_job_message`` content gate — inbound job -> normal classify,
  inbound status -> tech reply parser; outbound -> operator dispatch.
- ``_handle_operator_dispatch`` (OpenPhone): happy path transitions to
  ``dispatched``; no match -> ``dispatch_no_match`` alert; duplicate
  OpenPhone message id is a no-op (idempotency).
- ``_resolve_openphone_target_dispatch``: single-dispatch, ambiguous,
  and no-candidates cases.
- ``parse_openphone_tech_reply`` end-to-end: dispatch event -> lifecycle
  transition with ``source=tech_openphone``; ambiguous -> alert.
- LifecycleEventSource enum contains the new ``OPERATOR_OPENPHONE`` and
  ``TECH_OPENPHONE`` values.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.db.models.technician import Technician
from app.schemas.dispatch_job import TechReplyIntent
from app.services.lifecycle import LifecycleStatus
from app.services.openphone import OpenPhoneService
from app.services.tech_reply_parser import (
    _resolve_openphone_target_dispatch,
    parse_openphone_tech_reply,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    *,
    openphone_id: str | None = "OP_msg_1",
    from_number: str | None = "+13125551234",
    to_numbers: list[str] | None = None,
    direction: str = "incoming",
    content: str | None = None,
    event_type: str = "message.received",
) -> MagicMock:
    msg = MagicMock()
    msg.id = uuid4()
    msg.openphone_id = openphone_id
    msg.from_number = from_number
    msg.to_numbers = list(to_numbers or [])
    msg.direction = direction
    msg.content = content
    msg.event_type = event_type
    msg.lifecycle_event_id = None
    return msg


def _make_technician(*, phone_e164: str = "3125551234", name: str = "Mike's Plumbing") -> Technician:
    tech = Technician(name=name, phone_e164=phone_e164, whatsapp_chat_jid=None)
    tech.id = uuid4()
    tech.is_active = True
    return tech


def _query_result(value) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _scalars_result(values) -> MagicMock:
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=list(values))
    res = MagicMock()
    res.scalars = MagicMock(return_value=scalars)
    return res


def _make_event(*, job_id=None, payload=None, source: str = LifecycleEventSource.OPERATOR_OPENPHONE.value) -> MagicMock:
    ev = MagicMock()
    ev.id = uuid4()
    ev.job_id = job_id or uuid4()
    ev.source = source
    ev.payload = payload or {}
    return ev


def _fake_llm_config() -> SimpleNamespace:
    return SimpleNamespace(base_url="https://api.example/v1", api_key="sk-x")


def _patch_lifecycle():
    transition = AsyncMock(return_value=uuid4())
    service = MagicMock()
    service.transition = transition
    return (
        patch("app.services.lifecycle.LifecycleService", return_value=service),
        transition,
    )


@asynccontextmanager
async def _db_context(db):
    yield db


# ---------------------------------------------------------------------------
# LifecycleEventSource enum
# ---------------------------------------------------------------------------


class TestEnum:
    def test_openphone_sources_present(self) -> None:
        assert LifecycleEventSource.OPERATOR_OPENPHONE.value == "operator_openphone"
        assert LifecycleEventSource.TECH_OPENPHONE.value == "tech_openphone"


# ---------------------------------------------------------------------------
# resolve_technician_for_message
# ---------------------------------------------------------------------------


class TestResolveTechnician:
    @pytest.mark.anyio
    async def test_inbound_matches_sender_phone(self):
        tech = _make_technician(phone_e164="3125551234")
        msg = _make_message(from_number="+13125551234")

        db = MagicMock()
        service = OpenPhoneService(db)
        with patch(
            "app.repositories.technician.get_by_phone_e164",
            new=AsyncMock(return_value=tech),
        ) as get_by_phone:
            result = await service.resolve_technician_for_message(msg)

        assert result is tech
        get_by_phone.assert_awaited_once()
        assert get_by_phone.await_args.args[1] == "+13125551234"

    @pytest.mark.anyio
    async def test_outbound_matches_any_recipient(self):
        tech = _make_technician(phone_e164="3125551234")
        msg = _make_message(direction="outgoing", from_number=None, to_numbers=["+13125550000", "+13125551234"])

        db = MagicMock()
        service = OpenPhoneService(db)
        # First recipient doesn't match; second does — first match wins and
        # the loop short-circuits.
        with patch(
            "app.repositories.technician.get_by_phone_e164",
            new=AsyncMock(side_effect=[None, tech]),
        ) as get_by_phone:
            result = await service.resolve_technician_for_message(msg)

        assert result is tech
        assert get_by_phone.await_count == 2

    @pytest.mark.anyio
    async def test_no_match_returns_none(self):
        msg = _make_message(from_number="+13125559999")
        db = MagicMock()
        service = OpenPhoneService(db)
        with patch(
            "app.repositories.technician.get_by_phone_e164",
            new=AsyncMock(return_value=None),
        ):
            assert await service.resolve_technician_for_message(msg) is None


# ---------------------------------------------------------------------------
# handle_tech_chat_message routing
# ---------------------------------------------------------------------------


class TestHandleTechChatMessage:
    @pytest.mark.anyio
    async def test_outbound_routes_to_operator_dispatch(self):
        tech = _make_technician()
        msg = _make_message(
            direction="outgoing",
            from_number=None,
            to_numbers=["+13125551234"],
            content="123 Main St Chicago IL 60601 / 3125551234",
        )
        db = MagicMock()
        service = OpenPhoneService(db)

        dispatch_handler = AsyncMock()
        with patch.object(service, "_handle_operator_dispatch", new=dispatch_handler):
            await service.handle_tech_chat_message(msg, tech)

        dispatch_handler.assert_awaited_once_with(msg, tech)

    @pytest.mark.anyio
    async def test_inbound_with_address_and_phone_classifies_as_new_job(self):
        tech = _make_technician()
        msg = _make_message(
            direction="incoming",
            content=(
                "123 Main St Chicago, IL 60601\n"
                "Lockout, $150\n"
                "Customer: Jane 3125559999"
            ),
        )
        db = MagicMock()
        service = OpenPhoneService(db)

        classify = AsyncMock()

        with (
            patch.object(service, "_handle_operator_dispatch", new=AsyncMock()) as dispatch_handler,
            patch(
                "app.services.classification.JobClassificationService",
            ) as classification_cls,
        ):
            classification_cls.return_value.classify_message = classify
            await service.handle_tech_chat_message(msg, tech)

        dispatch_handler.assert_not_called()
        classify.assert_awaited_once_with(msg)

    @pytest.mark.anyio
    async def test_inbound_terise_routes_to_tech_reply(self):
        tech = _make_technician()
        msg = _make_message(direction="incoming", content="omw")
        db = MagicMock()
        service = OpenPhoneService(db)

        bg = MagicMock()
        with patch(
            "app.services.tech_reply_parser.parse_openphone_tech_reply_in_background",
            new=AsyncMock(),
        ) as parser:
            await service.handle_tech_chat_message(msg, tech, background_tasks=bg)

        bg.add_task.assert_called_once()
        parser.assert_not_called()

    @pytest.mark.anyio
    async def test_inbound_terise_runs_inline_when_no_background_tasks(self):
        tech = _make_technician()
        msg = _make_message(direction="incoming", content="done")
        db = MagicMock()
        service = OpenPhoneService(db)

        with patch(
            "app.services.tech_reply_parser.parse_openphone_tech_reply_in_background",
            new=AsyncMock(),
        ) as parser:
            await service.handle_tech_chat_message(msg, tech, background_tasks=None)

        parser.assert_awaited_once_with(incoming_message_id=msg.id)


# ---------------------------------------------------------------------------
# _handle_operator_dispatch (OpenPhone)
# ---------------------------------------------------------------------------


class TestHandleOperatorDispatch:
    @pytest.mark.anyio
    async def test_happy_path_transitions_to_dispatched(self):
        tech = _make_technician()
        msg = _make_message(
            direction="outgoing",
            openphone_id="OP_dispatch_1",
            content="123 Main St Chicago IL 60601 / 3125551234",
        )
        job = MagicMock()
        job.id = uuid4()

        db = MagicMock()
        service = OpenPhoneService(db)

        lifecycle_patch, transition = _patch_lifecycle()

        with (
            patch(
                "app.repositories.lifecycle_event_repo.exists_for_openphone_id",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(return_value=job),
            ),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=AsyncMock(),
            ) as alert_create,
            lifecycle_patch,
        ):
            await service._handle_operator_dispatch(msg, tech)

        alert_create.assert_not_called()
        transition.assert_awaited_once()
        kwargs = transition.await_args.kwargs
        assert kwargs["job"] is job
        assert kwargs["to_status"] == "dispatched"
        assert kwargs["source"] == LifecycleEventSource.OPERATOR_OPENPHONE
        assert kwargs["payload"]["phone_e164"] == tech.phone_e164
        assert kwargs["payload"]["openphone_id"] == "OP_dispatch_1"
        assert kwargs["payload"]["technician_id"] == str(tech.id)

    @pytest.mark.anyio
    async def test_no_match_raises_dispatch_no_match_alert(self):
        tech = _make_technician()
        msg = _make_message(
            direction="outgoing",
            openphone_id="OP_dispatch_miss",
            content="999 Imaginary Way Chicago IL 60601 / 3125559999",
        )
        db = MagicMock()
        service = OpenPhoneService(db)

        with (
            patch(
                "app.repositories.lifecycle_event_repo.exists_for_openphone_id",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.repositories.alert.create_or_get_open",
                new=AsyncMock(),
            ) as alert_create,
            patch("app.services.lifecycle.LifecycleService"),
        ):
            await service._handle_operator_dispatch(msg, tech)

        alert_create.assert_awaited_once()
        kwargs = alert_create.await_args.kwargs
        assert kwargs["kind"] == "dispatch_no_match"
        assert kwargs["chat_jid"] == f"openphone:{tech.phone_e164}"
        assert kwargs["payload"]["openphone_id"] == "OP_dispatch_miss"
        assert kwargs["payload"]["street_number"] == "999"

    @pytest.mark.anyio
    async def test_duplicate_message_is_a_no_op(self):
        tech = _make_technician()
        msg = _make_message(openphone_id="OP_dup")
        db = MagicMock()
        service = OpenPhoneService(db)

        with (
            patch(
                "app.repositories.lifecycle_event_repo.exists_for_openphone_id",
                new=AsyncMock(return_value=True),
            ) as exists,
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(),
            ) as find_target,
            patch(
                "app.repositories.alert.create_or_get_open",
                new=AsyncMock(),
            ),
        ):
            await service._handle_operator_dispatch(msg, tech)

        exists.assert_awaited_once()
        find_target.assert_not_called()

    @pytest.mark.anyio
    async def test_empty_body_skipped(self):
        tech = _make_technician()
        msg = _make_message(content="")
        db = MagicMock()
        service = OpenPhoneService(db)

        with (
            patch(
                "app.repositories.lifecycle_event_repo.exists_for_openphone_id",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.repositories.job.find_dispatch_target",
                new=AsyncMock(),
            ) as find_target,
        ):
            await service._handle_operator_dispatch(msg, tech)
        find_target.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_openphone_target_dispatch
# ---------------------------------------------------------------------------


class TestResolveOpenPhoneTarget:
    @pytest.mark.anyio
    async def test_single_event_returned(self):
        ev = _make_event(payload={"phone_e164": "3125551234"})
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars_result([ev]))

        result = await _resolve_openphone_target_dispatch(
            db, technician_phone_e164="3125551234"
        )
        assert result is ev

    @pytest.mark.anyio
    async def test_distinct_jobs_collapse_to_latest_per_job(self):
        # Two events for the same job, two for another — the representative
        # is the most recent per job.
        job_a, job_b = uuid4(), uuid4()
        ev_old = _make_event(
            job_id=job_a,
            payload={"phone_e164": "3125551234"},
        )
        ev_old.created_at = datetime.now(UTC) - timedelta(minutes=15)
        ev_new_a = _make_event(
            job_id=job_a,
            payload={"phone_e164": "3125551234"},
        )
        ev_new_a.created_at = datetime.now(UTC) - timedelta(minutes=5)
        ev_b = _make_event(
            job_id=job_b,
            payload={"phone_e164": "3125551234"},
        )
        ev_b.created_at = datetime.now(UTC) - timedelta(minutes=10)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars_result([ev_new_a, ev_b, ev_old]))

        result = await _resolve_openphone_target_dispatch(
            db, technician_phone_e164="3125551234"
        )
        assert isinstance(result, list)
        assert {e.job_id for e in result} == {job_a, job_b}

    @pytest.mark.anyio
    async def test_no_events_returns_none(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars_result([]))

        result = await _resolve_openphone_target_dispatch(
            db, technician_phone_e164="3125551234"
        )
        assert result is None

    @pytest.mark.anyio
    async def test_missing_phone_returns_none(self):
        db = AsyncMock()
        result = await _resolve_openphone_target_dispatch(db, technician_phone_e164=None)
        assert result is None


# ---------------------------------------------------------------------------
# parse_openphone_tech_reply end-to-end
# ---------------------------------------------------------------------------


class TestParseOpenPhoneTechReply:
    @pytest.mark.anyio
    async def test_happy_path_transitions_with_tech_openphone_source(self):
        tech = _make_technician()
        target = _make_event(
            job_id=uuid4(),
            payload={"phone_e164": tech.phone_e164, "openphone_id": "OP_disp_1"},
        )
        job = MagicMock()
        job.id = target.job_id

        incoming = _make_message(
            openphone_id="OP_reply_1",
            from_number="+13125551234",
            content="omw",
        )

        intent = TechReplyIntent(intent="in_progress", appt_iso=None, notes=None)

        db = MagicMock()
        db.execute = AsyncMock(return_value=_query_result(job))
        db.add = MagicMock()

        _lifecycle_patch, transition = _patch_lifecycle()

        with (
            patch(
                "app.services.tech_reply_parser._resolve_openphone_target_dispatch",
                new=AsyncMock(return_value=target),
            ),
            patch(
                "app.services.tech_reply_parser._extract_intent",
                new=AsyncMock(return_value=intent),
            ),
            patch(
                "app.repositories.job.get_job_by_id",
                new=AsyncMock(return_value=job),
            ),
            patch(
                "app.services.lifecycle.LifecycleService",
                return_value=MagicMock(transition=transition),
            ),
        ):
            source, returned_intent = await parse_openphone_tech_reply(
                db, incoming_message=incoming, technician=tech
            )

        assert source == "tech_openphone"
        assert returned_intent == "in_progress"
        transition.assert_awaited_once()
        kwargs = transition.await_args.kwargs
        assert kwargs["job"] is job
        assert kwargs["to_status"] is LifecycleStatus.IN_PROGRESS
        assert kwargs["source"] is LifecycleEventSource.TECH_OPENPHONE
        assert kwargs["payload"]["phone_e164"] == tech.phone_e164
        assert kwargs["payload"]["openphone_id"] == "OP_reply_1"
        assert kwargs["payload"]["intent"] == "in_progress"
        # The IncomingMessage row was linked back to the event it triggered.
        assert incoming.lifecycle_event_id is not None

    @pytest.mark.anyio
    async def test_ambiguous_emits_alert_and_no_transition(self):
        tech = _make_technician()
        incoming = _make_message(openphone_id="OP_reply_amb")
        ev1 = _make_event(payload={"phone_e164": tech.phone_e164})
        ev2 = _make_event(payload={"phone_e164": tech.phone_e164})

        alert_create = AsyncMock()
        with (
            patch(
                "app.services.tech_reply_parser._resolve_openphone_target_dispatch",
                new=AsyncMock(return_value=[ev1, ev2]),
            ),
            patch(
                "app.services.tech_reply_parser._extract_intent",
                new=AsyncMock(),
            ) as extract,
            patch(
                "app.services.tech_reply_parser.alert_repo.create_or_get_open",
                new=alert_create,
            ),
        ):
            source, intent = await parse_openphone_tech_reply(
                db=AsyncMock(), incoming_message=incoming, technician=tech
            )

        assert source == "ambiguous_attribution"
        assert intent is None
        alert_create.assert_awaited_once()
        kwargs = alert_create.await_args.kwargs
        assert kwargs["kind"] == "unattributed_reply"
        assert kwargs["chat_jid"] == f"openphone:{tech.phone_e164}"
        extract.assert_not_called()

    @pytest.mark.anyio
    async def test_no_target_returns_no_target(self):
        tech = _make_technician()
        incoming = _make_message()
        with (
            patch(
                "app.services.tech_reply_parser._resolve_openphone_target_dispatch",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.tech_reply_parser._extract_intent",
                new=AsyncMock(),
            ) as extract,
        ):
            source, intent = await parse_openphone_tech_reply(
                db=AsyncMock(), incoming_message=incoming, technician=tech
            )
        assert source == "no_target"
        assert intent is None
        extract.assert_not_called()

    @pytest.mark.anyio
    async def test_llm_failure_does_not_raise(self):
        tech = _make_technician()
        target = _make_event(payload={"phone_e164": tech.phone_e164})
        incoming = _make_message()

        with (
            patch(
                "app.services.tech_reply_parser._resolve_openphone_target_dispatch",
                new=AsyncMock(return_value=target),
            ),
            patch(
                "app.services.tech_reply_parser._extract_intent",
                new=AsyncMock(side_effect=RuntimeError("LLM exploded")),
            ),
            patch(
                "app.repositories.job.get_job_by_id",
                new=AsyncMock(return_value=MagicMock(id=target.job_id)),
            ),
        ):
            source, intent = await parse_openphone_tech_reply(
                db=AsyncMock(), incoming_message=incoming, technician=tech
            )
        assert source == "tech_openphone"
        assert intent is None
