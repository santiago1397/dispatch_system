"""Tests for the ``backfill-rejects`` command's safety gate.

The command replays reject detection over historical outbound OpenPhone
messages and writes a *terminal* status in bulk, so the gate matters more
than the happy path. Its first version shipped reference-only and matched
nothing at all — the declines it hunts are bare, and "only dealer" carries
no PDL, phone or address — so the keyless recency path and the conditions
that fence it in are what these tests pin down.

The motivating row is job ``0964f020`` (Always 24/7, PDL HTE27, Melrose
Park, 2023 Ford Transit): the operator replied "only dealer" 39 seconds
after intake and nothing acted on it.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.commands import backfill_rejects as cmd
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services.lifecycle import LifecycleStatus


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


JOB_BODY = """Co: Always 24/7
PDL: HTE27
Ph: 7082241019
Addr: 12 , Melrose Park, IL, 60160
Desc: Ignition
Occu: Locksmith

Notes:
2023 ford transit"""

INTAKE_AT = datetime(2026, 7, 18, 18, 4, 8, tzinfo=UTC)
REPLY_AT = INTAKE_AT + timedelta(seconds=39)
COUNTERPARTY = "+14704714943"


def _message(body: str, *, created_at=REPLY_AT, openphone_id="AC1f89ce"):
    return SimpleNamespace(
        content=body,
        created_at=created_at,
        to_numbers=[COUNTERPARTY],
        openphone_id=openphone_id,
    )


def _job():
    return SimpleNamespace(
        id=uuid4(),
        first_message_at=INTAKE_AT,
        lifecycle_status=LifecycleStatus.PENDING.value,
        address_street_number="12",
        address_street_name="",
    )


async def _run_with(
    message,
    *,
    apply: bool,
    job=None,
    outbound_count: int = 2,
    competing: int = 0,
    already_applied: bool = False,
):
    """Drive ``_run`` against mocked repositories, returning the transition mock."""
    job = job or _job()

    @asynccontextmanager
    async def _fake_ctx():
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [message])
        )
        yield db

    with (
        patch.object(cmd, "get_db_context", _fake_ctx),
        patch.object(cmd, "job_repo") as job_repo,
        patch.object(cmd, "openphone_repo") as op_repo,
        patch.object(cmd, "lifecycle_event_repo") as ev_repo,
        patch.object(cmd, "LifecycleService") as ls_cls,
    ):
        job_repo.find_job_by_reference_openphone = AsyncMock(return_value=None)
        job_repo.find_reject_candidate_openphone = AsyncMock(return_value=(job, JOB_BODY))
        job_repo.count_newer_jobs_openphone = AsyncMock(return_value=competing)
        op_repo.count_outbound_messages_to = AsyncMock(return_value=outbound_count)
        ev_repo.exists_for_openphone_id = AsyncMock(return_value=already_applied)
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())

        await cmd._run(apply=apply, limit=None)

    return ls_cls.return_value.transition


@pytest.mark.anyio
async def test_bare_only_dealer_is_rejected() -> None:
    """The motivating case: a keyless decline, on intake, no competing job."""
    transition = await _run_with(_message("only dealer"), apply=True)

    transition.assert_awaited_once()
    kwargs = transition.await_args.kwargs
    assert kwargs["to_status"] == LifecycleStatus.REJECTED
    assert kwargs["source"] == LifecycleEventSource.OPERATOR_REJECT
    # Backdated to when the operator actually declined, not to now.
    assert kwargs["at"] == REPLY_AT
    assert kwargs["payload"]["matched_by"] == "recency_unambiguous"
    assert kwargs["payload"]["backfill"] is True


@pytest.mark.anyio
async def test_dry_run_writes_nothing() -> None:
    """Default mode reports without transitioning."""
    transition = await _run_with(_message("only dealer"), apply=False)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_competing_job_blocks_the_write() -> None:
    """Another job posted in the interval makes the target ambiguous."""
    transition = await _run_with(_message("only dealer"), apply=True, competing=1)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_keyless_decline_outside_window_is_skipped() -> None:
    """A bare decline many messages later is not trusted to name this job."""
    transition = await _run_with(_message("only dealer"), apply=True, outbound_count=7)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_cancel_yields_to_the_cancel_backfill() -> None:
    """A cancel signal is left for ``backfill-cancels`` to handle."""
    transition = await _run_with(_message("cx said DNS"), apply=True)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_non_decline_is_skipped() -> None:
    """Ordinary chatter must never reject a job."""
    transition = await _run_with(_message("tech is on the way"), apply=True)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_already_applied_is_not_duplicated() -> None:
    """Re-running the backfill does not append a second event."""
    transition = await _run_with(_message("only dealer"), apply=True, already_applied=True)
    transition.assert_not_awaited()


@pytest.mark.anyio
async def test_reference_match_is_preferred_over_recency() -> None:
    """When the body names a job, that match wins and is labelled as such."""
    job = _job()

    @asynccontextmanager
    async def _fake_ctx():
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [_message(JOB_BODY + "\n\nonly dealer")])
        )
        yield db

    with (
        patch.object(cmd, "get_db_context", _fake_ctx),
        patch.object(cmd, "job_repo") as job_repo,
        patch.object(cmd, "openphone_repo") as op_repo,
        patch.object(cmd, "lifecycle_event_repo") as ev_repo,
        patch.object(cmd, "LifecycleService") as ls_cls,
    ):
        job_repo.find_job_by_reference_openphone = AsyncMock(return_value=(job, JOB_BODY))
        job_repo.find_reject_candidate_openphone = AsyncMock(return_value=None)
        job_repo.count_newer_jobs_openphone = AsyncMock(return_value=0)
        op_repo.count_outbound_messages_to = AsyncMock(return_value=1)
        ev_repo.exists_for_openphone_id = AsyncMock(return_value=False)
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())

        await cmd._run(apply=True, limit=None)

    transition = ls_cls.return_value.transition
    transition.assert_awaited_once()
    assert transition.await_args.kwargs["payload"]["matched_by"] == "reference"


@pytest.mark.anyio
async def test_reference_match_survives_a_late_reply() -> None:
    """A body that names its own job is not bound by the keyless window."""
    job = _job()

    @asynccontextmanager
    async def _fake_ctx():
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [_message(JOB_BODY + "\n\nonly dealer")])
        )
        yield db

    with (
        patch.object(cmd, "get_db_context", _fake_ctx),
        patch.object(cmd, "job_repo") as job_repo,
        patch.object(cmd, "openphone_repo") as op_repo,
        patch.object(cmd, "lifecycle_event_repo") as ev_repo,
        patch.object(cmd, "LifecycleService") as ls_cls,
    ):
        job_repo.find_job_by_reference_openphone = AsyncMock(return_value=(job, JOB_BODY))
        job_repo.find_reject_candidate_openphone = AsyncMock(return_value=None)
        job_repo.count_newer_jobs_openphone = AsyncMock(return_value=0)
        op_repo.count_outbound_messages_to = AsyncMock(return_value=9)  # well past the window
        ev_repo.exists_for_openphone_id = AsyncMock(return_value=False)
        ls_cls.return_value.transition = AsyncMock(return_value=uuid4())

        await cmd._run(apply=True, limit=None)

    ls_cls.return_value.transition.assert_awaited_once()
