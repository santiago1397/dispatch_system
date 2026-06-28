"""Unit tests for the tech-reply parser.

Covers:
- ``_resolve_target_dispatch``: explicit quote path, recent-dispatch
  fallback, ambiguous-fallback (multiple candidates), no-candidates.
- ``_map_intent_to_status``: every ``TechReplyIntentCode`` maps to a
  valid ``LifecycleStatus``.
- ``_extract_intent``: LLM invocation shape.
- ``parse_tech_reply``: end-to-end happy path (LLM extracted intent →
  ``LifecycleService.transition`` invoked with the right target status
  + payload); no-target short-circuit; ambiguous fallback emits an
  ``unattributed_reply`` alert and does NOT transition.
- ``parse_tech_reply_in_background``: opens a fresh DB session and is
  resilient to a missing message row + unexpected exceptions.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.schemas.dispatch_job import TechReplyIntent
from app.services.lifecycle import LifecycleStatus
from app.services.tech_reply_parser import (
    _extract_intent,
    _map_intent_to_status,
    _resolve_target_dispatch,
    parse_tech_reply,
    parse_tech_reply_in_background,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wa_message(
    *,
    chat_jid: str = "tech-chat@g.us",
    wa_message_id: str = "wamid.1",
    body: str | None = "on the way",
    is_from_me: bool = False,
    quoted_wa_message_id: str | None = None,
    timestamp: datetime | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.chat_jid = chat_jid
    msg.wa_message_id = wa_message_id
    msg.body = body
    msg.is_from_me = is_from_me
    msg.quoted_wa_message_id = quoted_wa_message_id
    msg.timestamp = timestamp or datetime.now(UTC)
    return msg


def _make_event(*, job_id=None, payload=None) -> MagicMock:
    event = MagicMock()
    event.id = uuid4()
    event.job_id = job_id or uuid4()
    event.payload = payload or {}
    return event


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


def _fake_llm_config():
    return SimpleNamespace(base_url="https://api.example/v1", api_key="sk-x")


def _patch_app_settings(service_get_llm):
    """Patch AppSettingsService as imported into tech_reply_parser so
    ``AppSettingsService(db).get_llm_config()`` returns ``service_get_llm``."""
    service_instance = MagicMock()
    service_instance.get_llm_config = AsyncMock(return_value=service_get_llm)
    service_cls = MagicMock(return_value=service_instance)
    return patch("app.services.tech_reply_parser.AppSettingsService", new=service_cls)


def _patch_lifecycle_service_returning(event_id=None):
    """Patch LifecycleService at its source module — the parser imports it
    lazily inside the function body, so patching
    ``app.services.tech_reply_parser.LifecycleService`` doesn't work
    (the name only exists in the function's local scope)."""
    transition = AsyncMock(return_value=(event_id or uuid4(), []))
    service = MagicMock()
    service.transition = transition
    return (
        patch("app.services.lifecycle.LifecycleService", return_value=service),
        transition,
    )


def _patch_chat_openai(intent: TechReplyIntent):
    """Patch the ChatOpenAI import; the structured LLM returns ``intent``."""
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=intent)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return patch("app.services.tech_reply_parser.ChatOpenAI", return_value=llm)


@asynccontextmanager
async def _db_context(db):
    yield db


# ---------------------------------------------------------------------------
# _resolve_target_dispatch
# ---------------------------------------------------------------------------


class TestResolveTargetDispatch:
    @pytest.mark.anyio
    async def test_quoted_wa_message_id_returns_matching_event(self):
        event = _make_event(payload={"chat_jid": "tech-chat@g.us", "wa_message_id": "wamid.0"})
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_query_result(event))

        msg = _make_wa_message(quoted_wa_message_id="wamid.0")
        result = await _resolve_target_dispatch(db, msg)
        assert result is event

    @pytest.mark.anyio
    async def test_quoted_wa_message_id_no_match_returns_none(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_query_result(None))

        msg = _make_wa_message(quoted_wa_message_id="wamid.does_not_exist")
        result = await _resolve_target_dispatch(db, msg)
        assert result is None

    @pytest.mark.anyio
    async def test_fallback_returns_most_recent_within_window(self):
        operator_msg = _make_wa_message(
            wa_message_id="wamid.dispatch.1",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )
        event = _make_event(
            payload={"chat_jid": "tech-chat@g.us", "wa_message_id": "wamid.dispatch.1"}
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalars_result([operator_msg]),  # operator messages in window
                _query_result(event),  # event for the matched candidate
            ]
        )

        msg = _make_wa_message(quoted_wa_message_id=None)
        result = await _resolve_target_dispatch(db, msg)
        assert result is event

    @pytest.mark.anyio
    async def test_fallback_ambiguous_returns_list(self):
        op1 = _make_wa_message(
            wa_message_id="wamid.dispatch.A",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )
        op2 = _make_wa_message(
            wa_message_id="wamid.dispatch.B",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=5),
        )
        event_a = _make_event(payload={"wa_message_id": "wamid.dispatch.A"})
        event_b = _make_event(payload={"wa_message_id": "wamid.dispatch.B"})

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalars_result([op2, op1]),  # descending order
                _query_result(event_b),
                _query_result(event_a),
            ]
        )

        msg = _make_wa_message()
        result = await _resolve_target_dispatch(db, msg)
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.anyio
    async def test_fallback_no_candidates_returns_none(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars_result([]))

        msg = _make_wa_message()
        result = await _resolve_target_dispatch(db, msg)
        assert result is None

    @pytest.mark.anyio
    async def test_fallback_operator_msgs_but_no_dispatch_events(self):
        """Operator msgs in the window but none produced a dispatch event
        (e.g. all matched ``dispatch_no_match`` alerts) → ``None``. We
        must not surface an ambiguous list in this case."""
        op1 = _make_wa_message(
            wa_message_id="wamid.dispatch.A",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalars_result([op1]),
                _query_result(None),
            ]
        )

        msg = _make_wa_message()
        result = await _resolve_target_dispatch(db, msg)
        assert result is None


# ---------------------------------------------------------------------------
# _map_intent_to_status
# ---------------------------------------------------------------------------


class TestMapIntentToStatus:
    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            ("in_progress", LifecycleStatus.IN_PROGRESS),
            ("appt_set", LifecycleStatus.APPT_SET),
            ("needs_follow_up", LifecycleStatus.NEEDS_FOLLOW_UP),
            ("canceled", LifecycleStatus.CANCELED),
        ],
    )
    def test_exhaustive_mapping(self, intent: str, expected: LifecycleStatus) -> None:
        assert _map_intent_to_status(intent) is expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_intent
# ---------------------------------------------------------------------------


class TestExtractIntent:
    @pytest.mark.anyio
    async def test_extract_intent_invokes_structured_llm(self):
        expected = TechReplyIntent(intent="in_progress", appt_iso=None, notes=None)

        with _patch_app_settings(_fake_llm_config()), _patch_chat_openai(expected) as llm_ctor:
            result = await _extract_intent(AsyncMock(), "on the way")

        assert result is expected
        llm_ctor.assert_called_once()
        # with_structured_output was called with the schema class.
        llm_ctor.return_value.with_structured_output.assert_called_once_with(TechReplyIntent)


# ---------------------------------------------------------------------------
# parse_tech_reply end-to-end
# ---------------------------------------------------------------------------


class TestParseTechReply:
    @pytest.mark.anyio
    async def test_happy_path_quote_triggers_transition(self):
        event = _make_event(
            payload={"chat_jid": "tech-chat@g.us", "wa_message_id": "wamid.dispatch.1"}
        )
        job = MagicMock()
        job.id = uuid4()

        msg = _make_wa_message(
            quoted_wa_message_id="wamid.dispatch.1",
            body="on the way",
        )
        intent = TechReplyIntent(intent="in_progress", appt_iso=None, notes=None)

        db = AsyncMock()
        # 1. _resolve_target_dispatch: quoted lookup
        # 2. job_repo.get_job_by_id
        # 3. IncomingMessage UPDATE — empty scalars result is fine
        db.execute = AsyncMock(
            side_effect=[
                _query_result(event),
                _query_result(job),
                _scalars_result([]),
            ]
        )

        lifecycle_patch, transition = _patch_lifecycle_service_returning()

        with (
            _patch_app_settings(_fake_llm_config()),
            _patch_chat_openai(intent),
            lifecycle_patch,
            patch("app.repositories.job.get_job_by_id", new=AsyncMock(return_value=job)),
        ):
            source, returned_intent = await parse_tech_reply(db, wa_message=msg)

        assert source == "tech_whatsapp"
        assert returned_intent == "in_progress"

        transition.assert_awaited_once()
        kwargs = transition.await_args.kwargs
        assert kwargs["job"] is job
        assert kwargs["to_status"] is LifecycleStatus.IN_PROGRESS
        assert kwargs["source"] is LifecycleEventSource.TECH_WHATSAPP
        assert kwargs["payload"]["intent"] == "in_progress"
        assert kwargs["payload"]["wa_message_id"] == "wamid.1"
        assert kwargs["payload"]["chat_jid"] == "tech-chat@g.us"

    @pytest.mark.anyio
    async def test_happy_path_carries_appt_iso_and_notes(self):
        event = _make_event(
            payload={"chat_jid": "tech-chat@g.us", "wa_message_id": "wamid.dispatch.1"}
        )
        job = MagicMock()
        job.id = uuid4()
        msg = _make_wa_message(quoted_wa_message_id="wamid.dispatch.1")
        intent = TechReplyIntent(
            intent="appt_set",
            appt_iso="2026-06-28T15:00:00-05:00",
            notes="customer needs water heater",
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _query_result(event),
                _query_result(job),
                _scalars_result([]),
            ]
        )

        lifecycle_patch, transition = _patch_lifecycle_service_returning()

        with (
            _patch_app_settings(_fake_llm_config()),
            _patch_chat_openai(intent),
            lifecycle_patch,
            patch("app.repositories.job.get_job_by_id", new=AsyncMock(return_value=job)),
        ):
            await parse_tech_reply(db, wa_message=msg)

        payload = transition.await_args.kwargs["payload"]
        assert payload["appt_iso"] == "2026-06-28T15:00:00-05:00"
        assert payload["notes"] == "customer needs water heater"

    @pytest.mark.anyio
    async def test_no_target_returns_no_target_source(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars_result([]))

        msg = _make_wa_message(quoted_wa_message_id=None)
        source, intent = await parse_tech_reply(db, wa_message=msg)
        assert source == "no_target"
        assert intent is None

    @pytest.mark.anyio
    async def test_ambiguous_fallback_emits_alert_and_aborts(self):
        op1 = _make_wa_message(
            wa_message_id="wamid.dispatch.A",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=10),
        )
        op2 = _make_wa_message(
            wa_message_id="wamid.dispatch.B",
            is_from_me=True,
            timestamp=datetime.now(UTC) - timedelta(minutes=5),
        )
        event_a = _make_event(payload={"wa_message_id": "wamid.dispatch.A"})
        event_b = _make_event(payload={"wa_message_id": "wamid.dispatch.B"})

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalars_result([op2, op1]),
                _query_result(event_b),
                _query_result(event_a),
            ]
        )

        alert_create = AsyncMock()
        msg = _make_wa_message()

        with patch(
            "app.services.tech_reply_parser.alert_repo.create_or_get_open",
            new=alert_create,
        ):
            source, intent = await parse_tech_reply(db, wa_message=msg)

        assert source == "ambiguous_attribution"
        assert intent is None
        alert_create.assert_awaited_once()
        kwargs = alert_create.await_args.kwargs
        assert kwargs["kind"] == "unattributed_reply"
        assert kwargs["chat_jid"] == msg.chat_jid
        assert len(kwargs["payload"]["candidate_event_ids"]) == 2

    @pytest.mark.anyio
    async def test_llm_failure_does_not_raise(self):
        event = _make_event(payload={"wa_message_id": "wamid.dispatch.1"})
        job = MagicMock()
        job.id = uuid4()
        msg = _make_wa_message(quoted_wa_message_id="wamid.dispatch.1")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _query_result(event),
                _query_result(job),
            ]
        )

        with (
            _patch_app_settings(_fake_llm_config()),
            patch("app.services.tech_reply_parser.ChatOpenAI") as llm_ctor,
        ):
            structured = MagicMock()
            structured.ainvoke = AsyncMock(side_effect=RuntimeError("LLM exploded"))
            llm_ctor.return_value.with_structured_output.return_value = structured

            source, intent = await parse_tech_reply(db, wa_message=msg)

        assert source == "tech_whatsapp"
        assert intent is None


# ---------------------------------------------------------------------------
# parse_tech_reply_in_background
# ---------------------------------------------------------------------------


class TestParseTechReplyInBackground:
    @pytest.mark.anyio
    async def test_missing_message_returns_without_raising(self):
        """If the message row vanished (e.g. cleanup), the bg task
        logs and exits — never crashes the worker."""
        fake_db = AsyncMock()
        fake_db.execute = AsyncMock(return_value=_query_result(None))

        with patch(
            "app.db.session.get_db_context",
            new=lambda: _db_context(fake_db),
        ):
            # Should NOT raise.
            await parse_tech_reply_in_background(
                wa_message_id="wamid.gone",
                chat_jid="tech-chat@g.us",
                batch_id="batch-1",
            )

    @pytest.mark.anyio
    async def test_unexpected_parse_failure_does_not_raise(self):
        """An unexpected exception in parse_tech_reply is logged but
        does NOT propagate out of the background task."""
        fake_db = AsyncMock()
        # The ``_fetch_message`` lookup succeeds; parse_tech_reply raises.
        msg = _make_wa_message()
        fake_db.execute = AsyncMock(return_value=_query_result(msg))

        with (
            patch(
                "app.db.session.get_db_context",
                new=lambda: _db_context(fake_db),
            ),
            patch(
                "app.services.tech_reply_parser.parse_tech_reply",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            # Should NOT raise.
            await parse_tech_reply_in_background(
                wa_message_id="wamid.1",
                chat_jid="tech-chat@g.us",
                batch_id="batch-1",
            )
