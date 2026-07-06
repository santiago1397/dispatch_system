"""Unit tests for LifecycleService — state-machine guard + transition flow.

These tests use ``AsyncMock`` to avoid a real database; they only verify
that ``transition`` invokes the repositories in the right order with
the right arguments and that the state-machine guard rejects illegal
transitions.

Outbound-message generation is intentionally NOT tested here — the
service no longer produces any. The system is pure observability: the
operator types replies natively in WhatsApp / OpenPhone and we record
what we observe. See ``memory/feedback_no_outbound_automation.md``.

For schema-level coverage of the lifecycle pipeline tables, see
``tests/test_lifecycle_migrations.py``.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import InvalidTransitionError, ValidationError
from app.db.models.job import Job
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.services.lifecycle import LifecycleService, LifecycleStatus


def _make_job(*, lifecycle_status: str = "pending") -> Job:
    """Build a mock Job with the fields LifecycleService reads."""
    job = MagicMock(spec=Job)
    job.id = uuid4()
    job.lifecycle_status = lifecycle_status
    job.lifecycle_status_changed_at = None
    return job


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestTransitionHappyPath:
    @pytest.mark.anyio
    async def test_pending_to_dispatched_writes_event_and_status(self):
        job = _make_job()
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=0)

        event_id = await service.transition(
            job=job,
            to_status=LifecycleStatus.DISPATCHED,
            source=LifecycleEventSource.OPERATOR_WHATSAPP,
            payload={"chat_jid": "123@g.us", "wa_message_id": "abc"},
        )

        assert event_id == event.id
        lifecycle_event_repo.create_event.assert_called_once()
        job_repo.set_lifecycle_status.assert_called_once()
        # Leaving 'pending' clears the undispatched SLA alert — but not the
        # terminal stuck-alert cleanup, which only runs on close/cancel.
        alert_repo.auto_resolve_for_job.assert_called_once_with(
            db,
            job_id=job.id,
            kinds=[alert_repo.AlertKind.UNDISPATCHED.value],
        )

    @pytest.mark.anyio
    async def test_in_progress_advances_state(self):
        job = _make_job(lifecycle_status="dispatched")
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=0)

        event_id = await service.transition(
            job=job,
            to_status=LifecycleStatus.IN_PROGRESS,
            source=LifecycleEventSource.TECH_WHATSAPP,
            payload={"intent": "in_progress"},
        )
        assert event_id == event.id
        # The job's new status is what the repo was called with.
        kwargs = job_repo.set_lifecycle_status.call_args.kwargs
        assert kwargs["status"] == LifecycleStatus.IN_PROGRESS.value


class TestStateMachineGuard:
    @pytest.mark.anyio
    async def test_manual_close_rejected(self):
        """Manual close is forbidden — closing must come via closing_chat."""
        job = _make_job(lifecycle_status="completed")
        db = AsyncMock()
        service = LifecycleService(db)

        with pytest.raises(InvalidTransitionError) as exc_info:
            await service.transition(
                job=job,
                to_status=LifecycleStatus.CLOSED,
                source=LifecycleEventSource.MANUAL,
                user_id=uuid4(),
                payload={"note": "trying to bypass"},
            )
        assert "closing pipeline" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_closing_chat_can_close(self):
        """The closing pipeline IS allowed to set status='closed'."""
        job = _make_job(lifecycle_status="completed")
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=0)

        event_id = await service.transition(
            job=job,
            to_status=LifecycleStatus.CLOSED,
            source=LifecycleEventSource.CLOSING_CHAT,
            payload={
                "closed_total": "350.00",
                "closed_payment_method": "card",
            },
        )
        assert event_id == event.id
        lifecycle_event_repo.create_event.assert_called_once()

    @pytest.mark.anyio
    async def test_manual_cancel_requires_note(self):
        """Operator-initiated cancellation must include a non-empty note."""
        job = _make_job(lifecycle_status="pending")
        db = AsyncMock()
        service = LifecycleService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.transition(
                job=job,
                to_status=LifecycleStatus.CANCELED,
                source=LifecycleEventSource.MANUAL,
                user_id=uuid4(),
                payload={"note": ""},
            )
        assert "note" in str(exc_info.value).lower()

        # Whitespace-only note also rejected
        with pytest.raises(ValidationError):
            await service.transition(
                job=job,
                to_status=LifecycleStatus.CANCELED,
                source=LifecycleEventSource.MANUAL,
                user_id=uuid4(),
                payload={"note": "   "},
            )

    @pytest.mark.anyio
    async def test_manual_cancel_with_note_succeeds(self):
        job = _make_job(lifecycle_status="pending")
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=0)

        event_id = await service.transition(
            job=job,
            to_status=LifecycleStatus.CANCELED,
            source=LifecycleEventSource.MANUAL,
            user_id=uuid4(),
            payload={"note": "customer changed their mind"},
        )
        assert event_id == event.id
        lifecycle_event_repo.create_event.assert_called_once()

    @pytest.mark.anyio
    async def test_unknown_status_rejected(self):
        job = _make_job()
        db = AsyncMock()
        service = LifecycleService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.transition(
                job=job,
                to_status="garbage",
                source=LifecycleEventSource.MANUAL,
            )
        assert "unknown" in str(exc_info.value).lower()


class TestTransitionAutoResolve:
    @pytest.mark.anyio
    async def test_terminal_status_auto_resolves_alerts(self):
        """Closing / canceling should resolve stuck alerts for the job."""
        job = _make_job(lifecycle_status="in_progress")
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=2)

        await service.transition(
            job=job,
            to_status=LifecycleStatus.CLOSED,
            source=LifecycleEventSource.CLOSING_CHAT,
            payload={"closed_total": "100"},
        )
        alert_repo.auto_resolve_for_job.assert_called_once()
        kwargs = alert_repo.auto_resolve_for_job.call_args.kwargs
        assert kwargs["job_id"] == job.id
        assert "stuck_dispatched" in kwargs["kinds"]
        assert "closing_missing" in kwargs["kinds"]

    @pytest.mark.anyio
    async def test_non_terminal_does_not_auto_resolve(self):
        job = _make_job(lifecycle_status="dispatched")
        db = AsyncMock()
        service = LifecycleService(db)

        from app.repositories import (
            alert as alert_repo,
        )
        from app.repositories import (
            job as job_repo,
        )
        from app.repositories import (
            job_lifecycle_event as lifecycle_event_repo,
        )

        event = MagicMock()
        event.id = uuid4()
        lifecycle_event_repo.create_event = AsyncMock(return_value=event)
        job_repo.set_lifecycle_status = AsyncMock(return_value=job)
        alert_repo.auto_resolve_for_job = AsyncMock(return_value=0)

        await service.transition(
            job=job,
            to_status=LifecycleStatus.IN_PROGRESS,
            source=LifecycleEventSource.TECH_WHATSAPP,
            payload={"intent": "on_the_way"},
        )
        alert_repo.auto_resolve_for_job.assert_not_called()
