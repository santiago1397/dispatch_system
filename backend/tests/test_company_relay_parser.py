"""Tests for the company-relay "customer no-answer" update detector.

Regression: Job A6E61BD (A1 Locksmith) sat in ``pending`` after the
operator relayed "Na did not call back lef vm" back to the broker — the
pipeline had no mechanism for company-directed operator status updates at
all, so the job silently aged out and tripped a false-positive
``closing_missing`` alert 24h later. See ``services/company_relay_parser.py``.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services import company_relay_parser, reject_detector
from app.services.lifecycle import LifecycleStatus


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _op_msg(body: str, *, ts: datetime, to: str = "+12674855331") -> SimpleNamespace:
    return SimpleNamespace(
        content=body,
        to_numbers=[to],
        created_at=ts,
        openphone_id="OP_ABC",
        direction="outgoing",
    )


# ---------------------------------------------------------------------------
# Pre-filter gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "Na did not call back lef vm",
        "still no answer",
        "left a voicemail",
        "tried again no pickup",
        "cx not picking up",
    ],
)
def test_contact_attempt_gate_positive(body: str) -> None:
    assert reject_detector.mentions_customer_contact_attempt(body) is True


@pytest.mark.parametrize(
    "body",
    [
        "ok",
        "k ty",
        "Lmc",
        "Confirmed",
        "Paid 264$",
        "On way",
    ],
)
def test_contact_attempt_gate_negative(body: str) -> None:
    assert reject_detector.mentions_customer_contact_attempt(body) is False


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_answer_update_transitions_to_needs_follow_up() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), lifecycle_status="pending")

    with (
        patch(
            "app.repositories.job.find_follow_up_candidate_openphone",
            new=AsyncMock(return_value=job),
        ),
        patch(
            "app.services.company_relay_parser._extract_intent",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    intent="no_answer_follow_up",
                    follow_up_at="2026-07-24T00:00:00",
                    notes="left vm",
                )
            ),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())
        result = await company_relay_parser.maybe_apply_no_answer_update(
            AsyncMock(), _op_msg("Na did not call back lef vm", ts=now)
        )

    assert result is True
    kwargs = ls_cls.return_value.transition.await_args.kwargs
    assert kwargs["to_status"] == "needs_follow_up"
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_RELAY
    assert kwargs["job"] is job


@pytest.mark.anyio
async def test_ordinary_ack_never_reaches_llm_or_transition() -> None:
    now = datetime.now(UTC)

    with (
        patch(
            "app.repositories.job.find_follow_up_candidate_openphone",
            new=AsyncMock(),
        ) as find_candidate,
        patch(
            "app.services.company_relay_parser._extract_intent",
            new=AsyncMock(),
        ) as extract,
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        result = await company_relay_parser.maybe_apply_no_answer_update(
            AsyncMock(), _op_msg("ok", ts=now)
        )

    assert result is False
    find_candidate.assert_not_awaited()
    extract.assert_not_awaited()
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_llm_none_intent_does_not_transition() -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(id=uuid4(), lifecycle_status="pending")

    with (
        patch(
            "app.repositories.job.find_follow_up_candidate_openphone",
            new=AsyncMock(return_value=job),
        ),
        patch(
            "app.services.company_relay_parser._extract_intent",
            new=AsyncMock(
                return_value=SimpleNamespace(intent="none", follow_up_at=None, notes=None)
            ),
        ),
        patch("app.services.lifecycle.LifecycleService") as ls_cls,
    ):
        ls_cls.return_value.transition = AsyncMock()
        # "still trying" mentions "trying" but the LLM decides it's not a
        # real no-answer report this time — transition must not fire.
        result = await company_relay_parser.maybe_apply_no_answer_update(
            AsyncMock(), _op_msg("still trying to figure out parts", ts=now)
        )

    assert result is False
    ls_cls.return_value.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_no_candidate_job_short_circuits() -> None:
    now = datetime.now(UTC)

    with (
        patch(
            "app.repositories.job.find_follow_up_candidate_openphone",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.company_relay_parser._extract_intent",
            new=AsyncMock(),
        ) as extract,
    ):
        result = await company_relay_parser.maybe_apply_no_answer_update(
            AsyncMock(), _op_msg("Na did not call back lef vm", ts=now)
        )

    assert result is False
    extract.assert_not_awaited()


# ---------------------------------------------------------------------------
# Alert-scan interaction: needs_follow_up excluded from closing_missing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_closing_missing_scan_excludes_needs_follow_up() -> None:
    from app.services.alerts import AlertEngine

    db = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    # Three SQL calls: candidates, open-alert job_ids, in-flight closing_chat job_ids.
    db.execute.side_effect = [empty_result, empty_result, empty_result]
    engine = AlertEngine(db)

    await engine._scan_closing_missing(datetime.now(UTC))

    query = db.execute.await_args_list[0].args[0]
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "needs_follow_up" not in compiled_query
    # Sanity: other non-terminal statuses are still present.
    assert LifecycleStatus.PENDING.value in compiled_query
