"""Unit tests for AlertEngine — stuck-job + closing-missing detection.

Uses ``AsyncMock`` against the alert repo so we don't need a real
Postgres. The scanner's correctness is mostly about candidate-set
filtering (job status + timestamp threshold + dedup against existing
open alerts); that's testable in isolation by stubbing the SQL result
sets per scan pass.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.alert import AlertKind
from app.services.alerts import AlertEngine


def _make_job(*, lifecycle_status: str, changed_minutes_ago: int | None = 240):
    """Build a minimal Job stub with the fields the engine reads."""
    job = MagicMock()
    job.id = uuid4()
    job.lifecycle_status = lifecycle_status
    job.lifecycle_status_changed_at = (
        datetime.now(UTC) - timedelta(minutes=changed_minutes_ago)
        if changed_minutes_ago is not None
        else None
    )
    job.first_message_at = datetime.now(UTC) - timedelta(days=2)
    job.closed_at = None
    return job


def _make_engine_with_db() -> tuple[AlertEngine, AsyncMock]:
    db = AsyncMock()
    # ``db.execute`` returns a result whose ``scalars().all()`` is a
    # list of objects. For SELECTs with multiple columns we use
    # ``result.all()`` returning tuples. The engine distinguishes via
    # ``.scalars()`` vs plain row iteration.
    db.execute = AsyncMock()
    engine = AlertEngine(db)
    return engine, db


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestUndispatched:
    @pytest.mark.anyio
    async def test_creates_alert_when_pending_past_threshold(self):
        engine, db = _make_engine_with_db()

        job = _make_job(lifecycle_status="pending", changed_minutes_ago=None)
        job.first_message_at = datetime.now(UTC) - timedelta(minutes=8)  # > 5

        # Two SQL calls: SELECT pending candidates, then open alert job_ids.
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_undispatched(datetime.now(UTC))

        assert created == 1
        assert already == 0
        kwargs = create.call_args.kwargs
        assert kwargs["kind"] == AlertKind.UNDISPATCHED.value
        assert kwargs["job_id"] == job.id
        assert kwargs["threshold_minutes"] == 5

    @pytest.mark.anyio
    async def test_skips_when_within_threshold(self):
        engine, db = _make_engine_with_db()

        # Fresh pending job (< 5 min) → SQL returns no candidates.
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = []
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, _ = await engine._scan_undispatched(datetime.now(UTC))

        assert created == 0
        create.assert_not_called()

    @pytest.mark.anyio
    async def test_skips_when_alert_already_open(self):
        engine, db = _make_engine_with_db()

        job = _make_job(lifecycle_status="pending", changed_minutes_ago=None)
        job.first_message_at = datetime.now(UTC) - timedelta(minutes=8)

        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        # Second call: this job already has an open undispatched alert.
        open_ids_result = MagicMock()
        open_ids_result.scalars.return_value.all.return_value = [job.id]
        db.execute.side_effect = [candidate_result, open_ids_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_undispatched(datetime.now(UTC))

        assert created == 0
        assert already == 1
        create.assert_not_called()


class TestStuckDispatched:
    @pytest.mark.anyio
    async def test_creates_alert_when_dispatched_past_threshold(self):
        engine, db = _make_engine_with_db()

        job = _make_job(
            lifecycle_status="dispatched",
            changed_minutes_ago=300,  # > 240
        )
        # Two SQL calls per _scan_stuck:
        # 1. SELECT candidates (status, threshold)
        # 2. SELECT open alert job_ids
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        # The second call (open alert job_ids) returns an empty list.
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_stuck_dispatched(datetime.now(UTC))

        assert created == 1
        assert already == 0
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert kwargs["kind"] == AlertKind.STUCK_DISPATCHED.value
        assert kwargs["job_id"] == job.id
        assert kwargs["threshold_minutes"] == 240

    @pytest.mark.anyio
    async def test_skips_when_within_threshold(self):
        engine, db = _make_engine_with_db()

        # 60 minutes < 240 threshold → no candidates returned by SQL.
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = []
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_stuck_dispatched(datetime.now(UTC))

        assert created == 0
        assert already == 0
        create.assert_not_called()


class TestClosingMissing:
    @pytest.mark.anyio
    async def test_flags_non_terminal_job_older_than_grace(self):
        engine, db = _make_engine_with_db()

        job = _make_job(lifecycle_status="in_progress", changed_minutes_ago=10)
        job.first_message_at = datetime.now(UTC) - timedelta(days=2)  # > 24h grace

        # Three SQL calls:
        # 1. SELECT candidates (status in non_terminal)
        # 2. SELECT open alert job_ids
        # 3. SELECT jobs with closing_chat event
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        in_flight_result = MagicMock()
        in_flight_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result, in_flight_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_closing_missing(datetime.now(UTC))

        assert created == 1
        assert already == 0
        kwargs = create.call_args.kwargs
        assert kwargs["kind"] == AlertKind.CLOSING_MISSING.value


class TestClosingUnfiled:
    @pytest.mark.anyio
    async def test_flags_completed_job_past_threshold(self):
        engine, db = _make_engine_with_db()

        # completed 20 min ago (> 15 min SLA) with no closing filed yet.
        job = _make_job(lifecycle_status="completed", changed_minutes_ago=20)

        # Two SQL calls: SELECT completed candidates, then open alert job_ids.
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.side_effect = [candidate_result, empty_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_closing_unfiled(datetime.now(UTC))

        assert created == 1
        assert already == 0
        kwargs = create.call_args.kwargs
        assert kwargs["kind"] == AlertKind.CLOSING_UNFILED.value
        assert kwargs["job_id"] == job.id
        assert kwargs["threshold_minutes"] == 15

    @pytest.mark.anyio
    async def test_skips_when_alert_already_open(self):
        engine, db = _make_engine_with_db()

        job = _make_job(lifecycle_status="completed", changed_minutes_ago=20)

        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [job]
        open_ids_result = MagicMock()
        open_ids_result.scalars.return_value.all.return_value = [job.id]
        db.execute.side_effect = [candidate_result, open_ids_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, already = await engine._scan_closing_unfiled(datetime.now(UTC))

        assert created == 0
        assert already == 1
        create.assert_not_called()


class TestApptTimePassed:
    @pytest.mark.anyio
    async def test_flags_when_appt_iso_is_parseable_and_in_past(self):
        engine, db = _make_engine_with_db()
        job_id = uuid4()
        appt_iso = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

        event = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            created_at=datetime.now(UTC) - timedelta(hours=4),
            payload={"appt_iso": appt_iso},
        )

        # 4 SQL calls: candidates → open alerts → later event for job → done
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [event]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        later_result = MagicMock()
        later_result.scalar_one_or_none.return_value = None  # no later event
        db.execute.side_effect = [
            candidate_result,
            empty_result,
            later_result,
        ]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, _ = await engine._scan_appt_time_passed(datetime.now(UTC))

        assert created == 1
        assert create.call_args.kwargs["kind"] == AlertKind.APPT_TIME_PASSED.value

    @pytest.mark.anyio
    async def test_skips_free_text_appt_iso(self):
        """A tech reply like 'tomorrow 3pm' should not be misflagged."""
        engine, db = _make_engine_with_db()
        job_id = uuid4()
        event = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            created_at=datetime.now(UTC) - timedelta(hours=4),
            payload={"appt_iso": "tomorrow 3pm"},
        )

        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [event]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        later_result = MagicMock()
        later_result.scalar_one_or_none.return_value = None
        db.execute.side_effect = [
            candidate_result,
            empty_result,
            later_result,
        ]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, _ = await engine._scan_appt_time_passed(datetime.now(UTC))

        assert created == 0
        create.assert_not_called()


class TestFollowUpDue:
    @pytest.mark.anyio
    async def test_flags_when_follow_up_at_has_passed(self):
        engine, db = _make_engine_with_db()
        job_id = uuid4()
        follow_up_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        event = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            created_at=datetime.now(UTC) - timedelta(minutes=30),
            payload={"follow_up_at": follow_up_at},
        )

        # candidates → open alert ids → later-event check
        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [event]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        later_result = MagicMock()
        later_result.scalar_one_or_none.return_value = None
        db.execute.side_effect = [candidate_result, empty_result, later_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, _ = await engine._scan_follow_up_due(datetime.now(UTC))

        assert created == 1
        assert create.call_args.kwargs["kind"] == AlertKind.FOLLOW_UP_DUE.value
        assert create.call_args.kwargs["job_id"] == job_id

    @pytest.mark.anyio
    async def test_skips_when_follow_up_still_in_future(self):
        engine, db = _make_engine_with_db()
        follow_up_at = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
        event = SimpleNamespace(
            id=uuid4(),
            job_id=uuid4(),
            created_at=datetime.now(UTC) - timedelta(minutes=1),
            payload={"follow_up_at": follow_up_at},
        )

        candidate_result = MagicMock()
        candidate_result.scalars.return_value.all.return_value = [event]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        later_result = MagicMock()
        later_result.scalar_one_or_none.return_value = None
        db.execute.side_effect = [candidate_result, empty_result, later_result]

        with patch(
            "app.services.alerts.alert_repo.create_or_get_open",
            new=AsyncMock(),
        ) as create:
            created, _ = await engine._scan_follow_up_due(datetime.now(UTC))

        assert created == 0
        create.assert_not_called()
