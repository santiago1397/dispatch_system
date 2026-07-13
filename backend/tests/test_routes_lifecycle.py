"""Route tests for Phase 4 + 5 + 6 endpoints.

Uses ``mock_db_session`` + dependency overrides to assert the route
layer correctly delegates to the service / repo layer:

- PATCH /jobs/{id}/lifecycle — manual override (reject 'closed', require
  note on cancel, accept other transitions).
- GET /alerts — open alerts only by default.
- POST /alerts/{id}/resolve — marks resolved.
- GET /stats — snapshot list.
- GET /technicians — admin only.
- POST /technicians — create (rejects duplicate chat_jid).

NOTE: there are intentionally no outbound/send route tests here — the
system never places a customer message. See
``memory/feedback_no_outbound_automation.md``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _user_with_role(role: str = "operator"):
    """Build a User stub with the role-checking helpers."""
    u = MagicMock()
    u.id = uuid4()
    u.role = role
    u.has_role = lambda r: r == role
    return u


# ---------------------------------------------------------------------------
# PATCH /jobs/{id}/lifecycle — manual override
# ---------------------------------------------------------------------------


class TestLifecyclePatch:
    @pytest.mark.anyio
    async def test_rejects_closed_transition(self):
        """to_status='closed' must be rejected (closing flows through
        CLOSING_CHAT_JID, not the manual dropdown)."""
        from app.api.routes.v1.dispatch_jobs import set_lifecycle_status

        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        job = MagicMock()
        job.id = uuid4()
        with patch(
            "app.repositories.job.get_job_by_id",
            new=AsyncMock(return_value=job),
        ):
            from app.core.exceptions import InvalidTransitionError

            with (
                patch(
                    "app.services.lifecycle.LifecycleService.transition",
                    new=AsyncMock(
                        side_effect=InvalidTransitionError(
                            message="Manual close is not allowed",
                            details={"to": "closed"},
                        )
                    ),
                ),
                pytest.raises(InvalidTransitionError),
            ):
                await set_lifecycle_status(
                    job_id=job.id,
                    body_in=SimpleNamespace(to_status="closed", note="n/a"),
                    db=db,
                    user=_user_with_role(),
                )

    @pytest.mark.anyio
    async def test_rejects_cancel_without_note(self):
        """to_status='canceled' with source='manual' REQUIRES a non-empty note."""
        from app.api.routes.v1.dispatch_jobs import set_lifecycle_status

        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        job = MagicMock()
        job.id = uuid4()
        with patch(
            "app.repositories.job.get_job_by_id",
            new=AsyncMock(return_value=job),
        ):
            from app.core.exceptions import ValidationError

            with (
                patch(
                    "app.services.lifecycle.LifecycleService.transition",
                    new=AsyncMock(
                        side_effect=ValidationError(
                            message="Manual cancellation requires a non-empty 'note'",
                            details={"to_status": "canceled"},
                        )
                    ),
                ),
                pytest.raises(ValidationError),
            ):
                await set_lifecycle_status(
                    job_id=job.id,
                    body_in=SimpleNamespace(to_status="canceled", note=None),
                    db=db,
                    user=_user_with_role(),
                )

    @pytest.mark.anyio
    async def test_404_when_job_missing(self):
        from app.api.routes.v1.dispatch_jobs import set_lifecycle_status
        from app.core.exceptions import NotFoundError

        db = AsyncMock()
        with (
            patch(
                "app.repositories.job.get_job_by_id",
                new=AsyncMock(return_value=None),
            ),
            pytest.raises(NotFoundError),
        ):
            await set_lifecycle_status(
                job_id=uuid4(),
                body_in=SimpleNamespace(to_status="needs_follow_up", note=None),
                db=db,
                user=_user_with_role(),
            )


# ---------------------------------------------------------------------------
# GET /alerts + POST /alerts/{id}/resolve
# ---------------------------------------------------------------------------


class TestAlertsList:
    @pytest.mark.anyio
    async def test_default_returns_open_only(self):
        from app.api.routes.v1.alerts import list_alerts

        db = AsyncMock()
        with (
            patch(
                "app.repositories.alert.list_open",
                new=AsyncMock(return_value=[]),
            ) as lo,
            patch(
                "app.repositories.alert.count_open",
                new=AsyncMock(return_value=0),
            ) as co,
            patch(
                "app.repositories.alert.count_unseen",
                new=AsyncMock(return_value=0),
            ) as cu,
        ):
            result = await list_alerts(
                db=db,
                _user=_user_with_role(),
                resolved=False,
                kinds=None,
                search=None,
                limit=100,
                offset=0,
            )

        lo.assert_called_once()
        co.assert_called_once()
        cu.assert_called_once()
        assert result.total == 0
        assert result.unseen == 0

    @pytest.mark.anyio
    async def test_search_with_no_matching_jobs_short_circuits(self):
        """No job's raw message matches ``search`` — skip the alert query
        entirely and return an empty list rather than filtering on an
        empty ``job_ids`` set."""
        from app.api.routes.v1.alerts import list_alerts

        db = AsyncMock()
        with (
            patch(
                "app.repositories.job.search_job_ids_by_message",
                new=AsyncMock(return_value=[]),
            ) as search,
            patch(
                "app.repositories.alert.list_open",
                new=AsyncMock(),
            ) as lo,
        ):
            result = await list_alerts(
                db=db,
                _user=_user_with_role(),
                resolved=False,
                kinds=None,
                search="no hot water",
                limit=100,
                offset=0,
            )

        search.assert_called_once_with(db, "no hot water")
        lo.assert_not_called()
        assert result.items == []
        assert result.total == 0

    @pytest.mark.anyio
    async def test_search_filters_by_matching_job_ids(self):
        """A search term with matching jobs narrows ``list_open``/``count_open``
        to those job ids."""
        from app.api.routes.v1.alerts import list_alerts

        db = AsyncMock()
        job_id = uuid4()
        with (
            patch(
                "app.repositories.job.search_job_ids_by_message",
                new=AsyncMock(return_value=[job_id]),
            ),
            patch(
                "app.repositories.alert.list_open",
                new=AsyncMock(return_value=[]),
            ) as lo,
            patch(
                "app.repositories.alert.count_open",
                new=AsyncMock(return_value=0),
            ) as co,
            patch(
                "app.repositories.alert.count_unseen",
                new=AsyncMock(return_value=0),
            ) as cu,
        ):
            await list_alerts(
                db=db,
                _user=_user_with_role(),
                resolved=False,
                kinds=None,
                search="leak",
                limit=100,
                offset=0,
            )

        lo.assert_called_once_with(db, kinds=None, job_ids=[job_id], limit=100, offset=0)
        co.assert_called_once_with(db, kinds=None, job_ids=[job_id])
        cu.assert_called_once_with(db, kinds=None)


class TestAlertsResolve:
    @pytest.mark.anyio
    async def test_404_when_alert_missing(self):
        from app.api.routes.v1.alerts import resolve_alert
        from app.core.exceptions import NotFoundError

        db = AsyncMock()
        with (
            patch(
                "app.repositories.alert.get_by_id",
                new=AsyncMock(return_value=None),
            ),
            pytest.raises(NotFoundError),
        ):
            await resolve_alert(
                alert_id=uuid4(),
                db=db,
                user=_user_with_role(),
            )


# ---------------------------------------------------------------------------
# POST /technicians — duplicate chat_jid rejection
# ---------------------------------------------------------------------------


class TestTechniciansCreate:
    @pytest.mark.anyio
    async def test_rejects_duplicate_chat_jid(self):
        from app.api.routes.v1.technicians import create_technician
        from app.core.exceptions import AlreadyExistsError

        db = AsyncMock()
        existing = MagicMock()
        existing.id = uuid4()
        body = SimpleNamespace(
            name="Mike",
            phone_e164="+13125550100",
            whatsapp_chat_jid="120363@g.us",
            is_active=True,
            notes=None,
        )
        with (
            patch(
                "app.repositories.technician.get_by_chat_jid",
                new=AsyncMock(return_value=existing),
            ),
            pytest.raises(AlreadyExistsError),
        ):
            await create_technician(
                body_in=body,
                db=db,
                _admin=_user_with_role("admin"),
            )


# ---------------------------------------------------------------------------
# GET /stats — snapshot list (no DB calls beyond list_for_date)
# ---------------------------------------------------------------------------


class TestStatsList:
    @pytest.mark.anyio
    async def test_returns_snapshot_list(self):
        from datetime import date

        from app.api.routes.v1.stats import list_stats

        db = AsyncMock()
        with patch(
            "app.repositories.daily_stats.list_for_date",
            new=AsyncMock(return_value=[]),
        ) as lfd:
            result = await list_stats(
                db=db,
                _user=_user_with_role(),
                snapshot_date=date(2026, 6, 26),
                scope="per_job",
            )

        lfd.assert_called_once()
        assert result.total == 0
