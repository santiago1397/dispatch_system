"""Unit tests for DailyStatsService — per_job / per_tech / per_company rollups.

Stubs out the SQL ``execute`` result sets so we can verify the payload
shape (response times, completion minutes, revenue aggregation) without
spinning up Postgres. The repository ``upsert_snapshot`` is patched at
the source module so we can assert call counts without hitting the DB.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.daily_stats import StatsScope
from app.services.daily_stats import DailyStatsService, _parse_money


class TestParseMoney:
    def test_dollar_prefix(self):
        assert _parse_money("$123.45") == 123.45

    def test_plain_number(self):
        assert _parse_money("123.45") == 123.45

    def test_with_comma(self):
        assert _parse_money("1,234.50") == 1234.5

    def test_free_text_takes_first_token(self):
        assert _parse_money("$100 + tip") == 100.0

    def test_empty_string(self):
        assert _parse_money("") == 0.0

    def test_none(self):
        assert _parse_money(None) == 0.0

    def test_unparseable_returns_zero(self):
        assert _parse_money("TBD") == 0.0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _empty_scalars():
    """Build a MagicMock result whose ``.scalars().all()`` returns [ ]."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = []
    return r


def _scalar_one_none():
    """Build a MagicMock result whose ``.scalar_one_or_none()`` returns None."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    return r


class TestSnapshot:
    @pytest.mark.anyio
    async def test_per_job_payload_shape(self):
        """One Job with a dispatched event → one per_job row."""
        db = AsyncMock()
        service = DailyStatsService(db)

        company_id = uuid4()
        job_id = uuid4()
        event = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            created_at=datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
            from_status="pending",
            to_status="dispatched",
            source="operator_whatsapp",
            payload={"technician_id": None, "chat_jid": "x@g.us"},
        )

        # The per_job pass runs 1 SELECT (events + company_id, phone).
        # The per_tech and per_company passes run their own queries too.
        # Each query is stubbed to return an empty list so the totals
        # beyond per_job are 0.
        row = (event, company_id, "+15555550000")
        rows_result = MagicMock()
        rows_result.all.return_value = [row]

        db.execute = AsyncMock(
            side_effect=[
                rows_result,  # per_job events+company
                _empty_scalars(),  # per_tech dispatch events (none for this date)
                _empty_scalars(),  # per_company candidate jobs (none)
            ]
        )

        with patch(
            "app.services.daily_stats.stats_repo.upsert_snapshot",
            new=AsyncMock(),
        ) as upsert:
            n = await service.snapshot(snapshot_date=date(2026, 6, 26))

        # 1 per_job row + 0 per_tech (no techs) + 0 per_company (no jobs)
        assert n == 1
        upsert.assert_called_once()
        kwargs = upsert.call_args.kwargs
        assert kwargs["scope"] == StatsScope.PER_JOB.value
        assert kwargs["scope_id"] is None
        payload = kwargs["payload"]
        assert payload["job_id"] == str(job_id)
        assert payload["company_id"] == str(company_id)
        assert payload["from_to_sequence"][0]["from"] == "pending"
        assert payload["from_to_sequence"][0]["to"] == "dispatched"
        # dispatched_at == first_event_at (same event), so delta is 0.0
        assert payload["time_to_dispatch_min"] == 0.0

    @pytest.mark.anyio
    async def test_per_company_revenue_sums_closed_totals(self):
        """Two companies, one with a closed job → revenue aggregated correctly."""
        db = AsyncMock()
        service = DailyStatsService(db)

        c1, c2 = uuid4(), uuid4()
        job_closed = SimpleNamespace(
            id=uuid4(),
            company_id=c1,
            first_message_at=datetime(2026, 6, 26, 9, 0, tzinfo=UTC),
            closed_at=datetime(2026, 6, 26, 17, 0, tzinfo=UTC),
            closed_total="$200.00",
            closed_payment_method="card",
        )
        job_open = SimpleNamespace(
            id=uuid4(),
            company_id=c2,
            first_message_at=datetime(2026, 6, 26, 9, 0, tzinfo=UTC),
            closed_at=None,
            closed_total=None,
            closed_payment_method=None,
        )

        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [
            job_closed,
            job_open,
        ]

        db.execute = AsyncMock(
            side_effect=[
                _empty_scalars(),  # per_job events (none for these jobs)
                _empty_scalars(),  # per_tech dispatch events (none)
                rows_result,  # per_company candidates
            ]
        )

        with patch(
            "app.services.daily_stats.stats_repo.upsert_snapshot",
            new=AsyncMock(),
        ) as upsert:
            n = await service.snapshot(snapshot_date=date(2026, 6, 26))

        assert n == 2  # one per company
        calls = upsert.call_args_list
        # Find the per_company call for c1
        c1_call = next(c for c in calls if c.kwargs["scope_id"] == c1)
        c2_call = next(c for c in calls if c.kwargs["scope_id"] == c2)
        assert c1_call.kwargs["payload"]["total_revenue"] == 200.0
        assert c1_call.kwargs["payload"]["jobs_completed"] == 1
        assert c2_call.kwargs["payload"]["jobs_completed"] == 0
        assert c2_call.kwargs["payload"]["total_revenue"] == 0.0
