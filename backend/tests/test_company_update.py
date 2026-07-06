"""Tests for the operator→company update relay.

Covers:
- Pure composition (``compose_update_line`` / ``compose_relay_text``).
- ``CompanyUpdateService.create_for_update`` — composes + persists for
  relayed kinds, skips non-relayed kinds and missing-origin jobs.
- ``AlertEngine._scan_company_update_unsent`` — marks sent when the
  operator relayed, alerts past the SLA, skips recent relays.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.db.models.alert import AlertKind
from app.services.alerts import AlertEngine
from app.services.company_update import (
    CompanyUpdateService,
    compose_relay_text,
    compose_update_line,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_compose_update_line_in_progress() -> None:
    assert compose_update_line("in_progress") == "Update: technician is on the way."


def test_compose_update_line_appt_set_includes_time() -> None:
    line = compose_update_line("appt_set", appt_at_display="2026-07-07T15:00:00")
    assert "appointment set" in line
    assert "2026-07-07T15:00:00" in line


def test_compose_update_line_follow_up_includes_reason_and_time() -> None:
    line = compose_update_line(
        "needs_follow_up", reason="priceshopping", follow_up_at_display="2026-07-06T18:30:00"
    )
    assert "follow-up" in line
    assert "price-shopping" in line
    assert "2026-07-06T18:30:00" in line


def test_compose_update_line_canceled_includes_reason() -> None:
    line = compose_update_line("canceled", reason="refused")
    assert "canceled" in line
    assert "refused" in line


def test_compose_relay_text_prepends_job_body() -> None:
    assert compose_relay_text("JOB BODY", "Update: x") == "JOB BODY\n\nUpdate: x"
    assert compose_relay_text("", "Update: x") == "Update: x"


# ---------------------------------------------------------------------------
# CompanyUpdateService.create_for_update
# ---------------------------------------------------------------------------


def _job():
    return SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        original_inbound_channel="whatsapp",
        original_inbound_from_number=None,
        appt_at=None,
        follow_up_at=None,
    )


@pytest.mark.anyio
async def test_create_for_update_composes_and_persists() -> None:
    svc = CompanyUpdateService(db=AsyncMock())
    origin = SimpleNamespace(
        content="JOB BODY 123 Main St",
        raw_payload={"chat_jid": "wa-local:acme"},
        source="whatsapp",
        from_number=None,
    )
    with (
        patch(
            "app.services.company_update.job_repo.find_origin_incoming_for_job",
            new=AsyncMock(return_value=origin),
        ),
        patch(
            "app.services.company_update.company_update_repo.create_company_update",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ) as create,
    ):
        result = await svc.create_for_update(job=_job(), update_kind="in_progress")

    assert result is not None
    kwargs = create.await_args.kwargs
    assert kwargs["channel"] == "whatsapp"
    assert kwargs["company_chat_jid"] == "wa-local:acme"
    assert "JOB BODY 123 Main St" in kwargs["message_text"]
    assert "on the way" in kwargs["message_text"]


@pytest.mark.anyio
async def test_create_for_update_skips_non_relayed_kind() -> None:
    svc = CompanyUpdateService(db=AsyncMock())
    with patch(
        "app.services.company_update.company_update_repo.create_company_update",
        new=AsyncMock(),
    ) as create:
        result = await svc.create_for_update(job=_job(), update_kind="accepted")
    assert result is None
    create.assert_not_called()


@pytest.mark.anyio
async def test_create_for_update_skips_when_no_origin() -> None:
    svc = CompanyUpdateService(db=AsyncMock())
    with (
        patch(
            "app.services.company_update.job_repo.find_origin_incoming_for_job",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.company_update.company_update_repo.create_company_update",
            new=AsyncMock(),
        ) as create,
    ):
        result = await svc.create_for_update(job=_job(), update_kind="canceled")
    assert result is None
    create.assert_not_called()


# ---------------------------------------------------------------------------
# AlertEngine._scan_company_update_unsent
# ---------------------------------------------------------------------------


def _relay(*, minutes_ago: int, channel: str = "whatsapp"):
    return SimpleNamespace(
        id=uuid4(),
        job_id=uuid4(),
        channel=channel,
        company_chat_jid="wa-local:acme" if channel == "whatsapp" else None,
        company_phone="+13125550182" if channel == "openphone" else None,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        update_kind="in_progress",
    )


@pytest.mark.anyio
async def test_scan_marks_sent_when_operator_relayed() -> None:
    engine = AlertEngine(AsyncMock())
    relay = _relay(minutes_ago=10)
    with (
        patch(
            "app.services.alerts.company_update_repo.list_unsent",
            new=AsyncMock(return_value=[relay]),
        ),
        patch("app.services.alerts.company_update_repo.mark_sent", new=AsyncMock()) as mark,
        patch(
            "app.services.alerts.whatsapp_repo.count_operator_messages_between",
            new=AsyncMock(return_value=1),
        ),
        patch("app.services.alerts.alert_repo.auto_resolve_for_job", new=AsyncMock()) as resolve,
        patch("app.services.alerts.alert_repo.create_or_get_open", new=AsyncMock()) as create,
        patch.object(AlertEngine, "_open_alert_job_ids", new=AsyncMock(return_value=set())),
    ):
        created, _ = await engine._scan_company_update_unsent(datetime.now(UTC))

    assert created == 0
    mark.assert_awaited_once()
    resolve.assert_awaited_once()
    create.assert_not_called()


@pytest.mark.anyio
async def test_scan_alerts_when_unsent_past_threshold() -> None:
    engine = AlertEngine(AsyncMock())
    relay = _relay(minutes_ago=10)  # > 7-min SLA
    with (
        patch(
            "app.services.alerts.company_update_repo.list_unsent",
            new=AsyncMock(return_value=[relay]),
        ),
        patch("app.services.alerts.company_update_repo.mark_sent", new=AsyncMock()),
        patch(
            "app.services.alerts.whatsapp_repo.count_operator_messages_between",
            new=AsyncMock(return_value=0),
        ),
        patch("app.services.alerts.alert_repo.create_or_get_open", new=AsyncMock()) as create,
        patch.object(AlertEngine, "_open_alert_job_ids", new=AsyncMock(return_value=set())),
    ):
        created, _ = await engine._scan_company_update_unsent(datetime.now(UTC))

    assert created == 1
    assert create.await_args.kwargs["kind"] == AlertKind.COMPANY_UPDATE_UNSENT.value
    assert create.await_args.kwargs["job_id"] == relay.job_id


@pytest.mark.anyio
async def test_scan_skips_recent_unsent_relay() -> None:
    engine = AlertEngine(AsyncMock())
    relay = _relay(minutes_ago=2)  # within the 7-min SLA
    with (
        patch(
            "app.services.alerts.company_update_repo.list_unsent",
            new=AsyncMock(return_value=[relay]),
        ),
        patch("app.services.alerts.company_update_repo.mark_sent", new=AsyncMock()),
        patch(
            "app.services.alerts.whatsapp_repo.count_operator_messages_between",
            new=AsyncMock(return_value=0),
        ),
        patch("app.services.alerts.alert_repo.create_or_get_open", new=AsyncMock()) as create,
        patch.object(AlertEngine, "_open_alert_job_ids", new=AsyncMock(return_value=set())),
    ):
        created, _ = await engine._scan_company_update_unsent(datetime.now(UTC))

    assert created == 0
    create.assert_not_called()
