"""Integration tests for OpenPhone conversation-thread repository queries.

Counterparty grouping relies on Postgres-specific SQL (JSONB indexing +
window functions) a mocked session can't exercise, so these hit the real
test database (``DATABASE_URL``). Rows are inserted inside a transaction
that's rolled back at the end of each test — same pattern as
``test_lifecycle_migrations.py``.

``created_at`` is set explicitly on each row rather than relying on the
``server_default=func.now()`` on ``TimestampMixin`` — Postgres freezes
``now()`` for the whole transaction, so every row inserted in one test
would otherwise get an identical timestamp and ordering couldn't be
verified.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models.company import Company
from app.db.models.openphone import IncomingMessage, MessageSource
from app.repositories import openphone as openphone_repo
from app.repositories import openphone_thread_label as thread_label_repo
from app.services.openphone import OpenPhoneService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session bound to a connection whose transaction is rolled back after the test."""
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session_factory = async_sessionmaker(bind=conn, expire_on_commit=False)
            async with session_factory() as session:
                yield session
            await trans.rollback()
    finally:
        await engine.dispose()


def _make_message(
    *,
    source: str = MessageSource.OPENPHONE.value,
    direction: str | None,
    from_number: str | None,
    to_numbers: list[str] | None = None,
    content: str = "hi",
    created_at: datetime,
) -> IncomingMessage:
    return IncomingMessage(
        source=source,
        openphone_id=None,
        direction=direction,
        from_number=from_number,
        to_numbers=to_numbers or [],
        content=content,
        status=None,
        event_type="message.received",
        phone_number_id=None,
        raw_payload={},
        created_at=created_at,
    )


NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)
NUM_A = "+15551110000"
NUM_B = "+15552220000"
NUM_C = "+15553330000"


class TestListThreads:
    @pytest.mark.anyio
    async def test_groups_by_counterparty_both_directions(self, db_session: AsyncSession):
        db_session.add_all(
            [
                _make_message(
                    direction="incoming",
                    from_number=NUM_A,
                    content="need a locksmith",
                    created_at=NOW - timedelta(minutes=10),
                ),
                _make_message(
                    direction="outgoing",
                    from_number=None,
                    to_numbers=[NUM_A],
                    content="on our way",
                    created_at=NOW - timedelta(minutes=5),
                ),
                _make_message(
                    direction="incoming",
                    from_number=NUM_B,
                    content="hello",
                    created_at=NOW - timedelta(minutes=20),
                ),
                # A WhatsApp-sourced row sharing a number — must be excluded.
                _make_message(
                    source=MessageSource.WHATSAPP.value,
                    direction="incoming",
                    from_number=NUM_C,
                    content="wa noise",
                    created_at=NOW,
                ),
            ]
        )
        await db_session.flush()

        threads = await openphone_repo.list_threads(db_session, limit=10)
        total = await openphone_repo.count_threads(db_session)

        assert total == 2
        by_counterparty = {t.counterparty: t for t in threads}
        assert set(by_counterparty) == {NUM_A, NUM_B}

        thread_a = by_counterparty[NUM_A]
        assert thread_a.message_count == 2
        assert thread_a.last_content == "on our way"
        assert thread_a.last_direction == "outgoing"

        # Most recently active thread (A) first.
        assert [t.counterparty for t in threads] == [NUM_A, NUM_B]

    @pytest.mark.anyio
    async def test_empty_when_no_openphone_messages(self, db_session: AsyncSession):
        db_session.add(
            _make_message(
                source=MessageSource.WHATSAPP.value,
                direction="incoming",
                from_number=NUM_C,
                created_at=NOW,
            )
        )
        await db_session.flush()

        threads = await openphone_repo.list_threads(db_session, limit=10)
        total = await openphone_repo.count_threads(db_session)

        assert threads == []
        assert total == 0


class TestListThreadMessages:
    @pytest.mark.anyio
    async def test_returns_both_directions_newest_first(self, db_session: AsyncSession):
        db_session.add_all(
            [
                _make_message(
                    direction="incoming",
                    from_number=NUM_A,
                    content="first",
                    created_at=NOW - timedelta(minutes=10),
                ),
                _make_message(
                    direction="outgoing",
                    from_number=None,
                    to_numbers=[NUM_A],
                    content="second",
                    created_at=NOW - timedelta(minutes=5),
                ),
                # Different counterparty — must not leak in.
                _make_message(
                    direction="incoming",
                    from_number=NUM_B,
                    content="unrelated",
                    created_at=NOW,
                ),
            ]
        )
        await db_session.flush()

        messages = await openphone_repo.list_thread_messages(db_session, counterparty=NUM_A)
        total = await openphone_repo.count_thread_messages(db_session, counterparty=NUM_A)

        assert total == 2
        assert [m.content for m in messages] == ["second", "first"]

    @pytest.mark.anyio
    async def test_since_until_filter(self, db_session: AsyncSession):
        db_session.add_all(
            [
                _make_message(
                    direction="incoming",
                    from_number=NUM_A,
                    content="old",
                    created_at=NOW - timedelta(days=1),
                ),
                _make_message(
                    direction="incoming",
                    from_number=NUM_A,
                    content="recent",
                    created_at=NOW,
                ),
            ]
        )
        await db_session.flush()

        messages = await openphone_repo.list_thread_messages(
            db_session, counterparty=NUM_A, since=NOW - timedelta(hours=1)
        )

        assert [m.content for m in messages] == ["recent"]


async def _make_company(db_session: AsyncSession, name: str) -> Company:
    company = Company(name=name, display_name=name.title())
    db_session.add(company)
    await db_session.flush()
    return company


class TestThreadLabelRepository:
    @pytest.mark.anyio
    async def test_upsert_creates_then_updates(self, db_session: AsyncSession):
        company = await _make_company(db_session, "acme-locksmith")

        created = await thread_label_repo.upsert(
            db_session,
            counterparty=NUM_A,
            company_id=company.id,
            label=None,
            created_by_user_id=None,
        )
        assert created.company_id == company.id
        assert created.label is None

        updated = await thread_label_repo.upsert(
            db_session,
            counterparty=NUM_A,
            company_id=None,
            label="Mike's cell",
            created_by_user_id=None,
        )
        assert updated.id == created.id
        assert updated.company_id is None
        assert updated.label == "Mike's cell"

    @pytest.mark.anyio
    async def test_get_by_counterparties_bulk(self, db_session: AsyncSession):
        await thread_label_repo.upsert(
            db_session, counterparty=NUM_A, company_id=None, label="A label", created_by_user_id=None
        )
        await thread_label_repo.upsert(
            db_session, counterparty=NUM_B, company_id=None, label="B label", created_by_user_id=None
        )

        found = await thread_label_repo.get_by_counterparties(db_session, [NUM_A, NUM_B, NUM_C])

        assert set(found) == {NUM_A, NUM_B}
        assert found[NUM_A].label == "A label"

    @pytest.mark.anyio
    async def test_delete(self, db_session: AsyncSession):
        await thread_label_repo.upsert(
            db_session, counterparty=NUM_A, company_id=None, label="temp", created_by_user_id=None
        )

        assert await thread_label_repo.delete(db_session, NUM_A) is True
        assert await thread_label_repo.get_by_counterparty(db_session, NUM_A) is None
        assert await thread_label_repo.delete(db_session, NUM_A) is False


class TestThreadLabelService:
    @pytest.mark.anyio
    async def test_list_threads_enriched_with_label_and_company(self, db_session: AsyncSession):
        company = await _make_company(db_session, "bobs-towing")
        db_session.add(
            _make_message(direction="incoming", from_number=NUM_A, content="hi", created_at=NOW)
        )
        db_session.add(
            _make_message(direction="incoming", from_number=NUM_B, content="hey", created_at=NOW)
        )
        await db_session.flush()

        await thread_label_repo.upsert(
            db_session,
            counterparty=NUM_A,
            company_id=company.id,
            label="dispatch line",
            created_by_user_id=None,
        )

        service = OpenPhoneService(db_session)
        threads, total = await service.list_threads(limit=10)

        assert total == 2
        by_counterparty = {t["counterparty"]: t for t in threads}
        labeled = by_counterparty[NUM_A]
        assert labeled["company_id"] == company.id
        assert labeled["company_name"] == "bobs-towing"
        assert labeled["label"] == "dispatch line"

        unlabeled = by_counterparty[NUM_B]
        assert unlabeled["company_id"] is None
        assert unlabeled["label"] is None

    @pytest.mark.anyio
    async def test_upsert_thread_label_requires_company_or_label(self, db_session: AsyncSession):
        service = OpenPhoneService(db_session)
        with pytest.raises(ValidationError):
            await service.upsert_thread_label(
                counterparty=NUM_A, company_id=None, label=None, created_by_user_id=None
            )
        with pytest.raises(ValidationError):
            await service.upsert_thread_label(
                counterparty=NUM_A, company_id=None, label="   ", created_by_user_id=None
            )

    @pytest.mark.anyio
    async def test_upsert_thread_label_rejects_unknown_company(self, db_session: AsyncSession):
        service = OpenPhoneService(db_session)
        with pytest.raises(NotFoundError):
            await service.upsert_thread_label(
                counterparty=NUM_A,
                company_id=uuid4(),
                label=None,
                created_by_user_id=None,
            )

    @pytest.mark.anyio
    async def test_delete_thread_label_is_a_noop_when_unset(self, db_session: AsyncSession):
        service = OpenPhoneService(db_session)
        await service.delete_thread_label(NUM_A)  # must not raise
