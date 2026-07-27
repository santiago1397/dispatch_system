"""Dedup candidate ranking, relay-line phones, and reclassify cleanup.

Regression cover for the "4930 W Quincy" defect: two messages about one
address produced four Job rows. Three separate causes, one per section
below.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.job import Job
from app.repositories import job as job_repo
from app.services.classification import _RELAY_LINE_RE

# The real message body, as WhatsApp delivered it.
QUINCY_BODY = """New job #LIPEQU
Brandy Williams

(2037699944 #3512)

4930 W Quincy St, Chicago, Illinois 60644

Car Key Copy"""


class TestRelayLineDetection:
    """A broker's call-tracking line is not a customer identity."""

    def test_matches_the_bracketed_extension_form(self) -> None:
        assert _RELAY_LINE_RE.search(QUINCY_BODY) is not None

    @pytest.mark.parametrize(
        "body",
        [
            "(2037699944 #3512)",
            "( 203-769-9944 #3512 )",
            "(+1 203.769.9944 # 12)",
            "((203) 769-9944 #7)",
        ],
    )
    def test_tolerates_formatting_variants(self, body: str) -> None:
        assert _RELAY_LINE_RE.search(body) is not None

    @pytest.mark.parametrize(
        "body",
        [
            "Ph: 7702868093",
            "call the cx at (203) 769-9944",
            "Addr: 326 Huntington Ln",
            "unit #3512",
        ],
    )
    def test_leaves_ordinary_phones_alone(self, body: str) -> None:
        """A plain customer phone must stay a valid dedup key — otherwise
        dedup loses its only signal for jobs with a vague address."""
        assert _RELAY_LINE_RE.search(body) is None


class TestDedupRanking:
    """Match strength outranks age."""

    @staticmethod
    async def _captured_sql(**overrides) -> str:
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        kwargs = {
            "company_id": uuid4(),
            "street_number": "4930",
            "street_name": "west quincy street",
            "customer_phone_e164": "+12037699944",
            "since": datetime.now(UTC) - timedelta(days=14),
        }
        kwargs.update(overrides)
        await job_repo.find_dedup_candidate(db, **kwargs)
        stmt = db.execute.await_args.args[0]
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    @pytest.mark.anyio
    async def test_orders_by_a_rank_before_age(self) -> None:
        sql = await self._captured_sql()
        order_by = sql.split("ORDER BY", 1)[1]
        assert "CASE" in order_by
        # Age is still the tie-breaker, but only after the rank.
        assert order_by.index("CASE") < order_by.index("first_message_at")

    @pytest.mark.anyio
    async def test_address_outranks_phone(self) -> None:
        """The Quincy bug: an older phone hit on a different street beat
        the exact address hit, and because it belonged to another company
        the caller then created a second Job instead of linking."""
        sql = await self._captured_sql()
        rank = sql.split("ORDER BY", 1)[1].split("END", 1)[0]
        assert rank.index("address_street_name") < rank.index("customer_phone_e164")

    @pytest.mark.anyio
    async def test_no_keys_at_all_short_circuits(self) -> None:
        db = MagicMock()
        db.execute = AsyncMock()
        candidate, is_cross = await job_repo.find_dedup_candidate(
            db,
            company_id=uuid4(),
            street_number=None,
            street_name=None,
            customer_phone_e164=None,
            since=datetime.now(UTC),
        )
        assert (candidate, is_cross) == (None, False)
        db.execute.assert_not_awaited()

    @pytest.mark.anyio
    async def test_address_only_still_queries(self) -> None:
        """With no phone the CASE has no phone branch — it must still
        build a valid statement rather than an empty CASE."""
        sql = await self._captured_sql(customer_phone_e164=None)
        # The column is in every SELECT list; what matters is that it is
        # not used as a matching or ranking key.
        predicate = sql.split("WHERE", 1)[1]
        assert "customer_phone_e164" not in predicate
        assert "CASE" in predicate.split("ORDER BY", 1)[1]


class TestDeleteIfUnreferenced:
    """The guard standing between reclassify and real history."""

    @staticmethod
    def _db(counts: list[int]) -> MagicMock:
        db = MagicMock()
        db.get = AsyncMock(return_value=MagicMock(spec=Job))
        db.execute = AsyncMock(
            side_effect=[MagicMock(scalar_one=MagicMock(return_value=n)) for n in counts]
        )
        db.delete = AsyncMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.anyio
    async def test_deletes_a_fully_inert_row(self) -> None:
        db = self._db([0, 0, 0])
        assert await job_repo.delete_if_unreferenced(db, uuid4()) is True
        db.delete.assert_awaited_once()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("counts", "what"),
        [
            ([1], "a dispatch_job still points at it"),
            ([0, 1], "it has lifecycle history"),
            ([0, 0, 1], "another Job calls it its duplicate_of parent"),
        ],
    )
    async def test_refuses_when_anything_references_it(self, counts, what) -> None:
        db = self._db(counts)
        assert await job_repo.delete_if_unreferenced(db, uuid4()) is False, what
        db.delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_missing_row_is_not_an_error(self) -> None:
        db = MagicMock()
        db.get = AsyncMock(return_value=None)
        db.delete = AsyncMock()
        assert await job_repo.delete_if_unreferenced(db, uuid4()) is False
        db.delete.assert_not_awaited()


class TestReclassifyCleanup:
    """Reclassify must not strand the Job it moved off of."""

    @staticmethod
    def _service(previous_job_id, new_job_id):
        from app.services.dispatch_job import DispatchJobService

        dj = MagicMock()
        dj.id = uuid4()
        dj.incoming_message_id = uuid4()
        dj.job_id = previous_job_id

        db = MagicMock()

        async def _refresh(obj):
            obj.job_id = new_job_id

        db.refresh = AsyncMock(side_effect=_refresh)
        return DispatchJobService(db), dj

    @pytest.mark.anyio
    async def test_removes_the_job_it_moved_off_of(self) -> None:
        previous_job_id, new_job_id = uuid4(), uuid4()
        svc, dj = self._service(previous_job_id, new_job_id)

        with (
            patch("app.services.dispatch_job.dispatch_job_repo") as mock_dj_repo,
            patch("app.services.dispatch_job.openphone_repo") as mock_op_repo,
            patch("app.services.dispatch_job.job_repo") as mock_job_repo,
            patch("app.services.dispatch_job.JobClassificationService") as mock_cls,
        ):
            mock_dj_repo.get_by_id = AsyncMock(return_value=dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=dj)
            mock_op_repo.get_incoming_message = AsyncMock(return_value=MagicMock())
            mock_cls.return_value.classify_message = AsyncMock()
            mock_job_repo.delete_if_unreferenced = AsyncMock(return_value=True)

            await svc.reclassify(dj.id)

        mock_job_repo.delete_if_unreferenced.assert_awaited_once()
        assert mock_job_repo.delete_if_unreferenced.await_args.args[1] == previous_job_id

    @pytest.mark.anyio
    async def test_leaves_it_alone_when_reclassify_lands_on_the_same_job(self) -> None:
        """Nothing was stranded, so nothing should be deleted."""
        same_id = uuid4()
        svc, dj = self._service(same_id, same_id)

        with (
            patch("app.services.dispatch_job.dispatch_job_repo") as mock_dj_repo,
            patch("app.services.dispatch_job.openphone_repo") as mock_op_repo,
            patch("app.services.dispatch_job.job_repo") as mock_job_repo,
            patch("app.services.dispatch_job.JobClassificationService") as mock_cls,
        ):
            mock_dj_repo.get_by_id = AsyncMock(return_value=dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=dj)
            mock_op_repo.get_incoming_message = AsyncMock(return_value=MagicMock())
            mock_cls.return_value.classify_message = AsyncMock()
            mock_job_repo.delete_if_unreferenced = AsyncMock()

            await svc.reclassify(dj.id)

        mock_job_repo.delete_if_unreferenced.assert_not_awaited()

    @pytest.mark.anyio
    async def test_first_ever_classification_has_nothing_to_clean(self) -> None:
        svc, dj = self._service(None, uuid4())

        with (
            patch("app.services.dispatch_job.dispatch_job_repo") as mock_dj_repo,
            patch("app.services.dispatch_job.openphone_repo") as mock_op_repo,
            patch("app.services.dispatch_job.job_repo") as mock_job_repo,
            patch("app.services.dispatch_job.JobClassificationService") as mock_cls,
        ):
            mock_dj_repo.get_by_id = AsyncMock(return_value=dj)
            mock_dj_repo.update_dispatch_job = AsyncMock(return_value=dj)
            mock_op_repo.get_incoming_message = AsyncMock(return_value=MagicMock())
            mock_cls.return_value.classify_message = AsyncMock()
            mock_job_repo.delete_if_unreferenced = AsyncMock()

            await svc.reclassify(dj.id)

        mock_job_repo.delete_if_unreferenced.assert_not_awaited()
