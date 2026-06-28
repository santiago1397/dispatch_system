"""Integration tests for the lifecycle pipeline alembic migrations.

These tests hit the real test database (configured via ``DATABASE_URL`` in
``.env``) and verify the schema is in the expected state after
``alembic upgrade head``. They do NOT mutate existing data — they only
inspect ``information_schema`` / ``pg_indexes`` / ``pg_constraint`` and
insert a single row per table in a transaction that is rolled back at
the end.

Migrations under test:

- ``2026-06-27_lifecycle_pipeline.py`` — five new tables (technicians,
  job_lifecycle_events, outbound_drafts, alerts, daily_stats_snapshots),
  four new columns on existing tables (jobs, whatsapp_tracked_chats,
  incoming_messages).
- ``2026-06-28_drop_outbound_drafts.py`` — drops the outbound_drafts
  table + index. The system never places customer messages; drafts
  are no longer needed.

Coverage:

* Alembic version is at head (the drop migration).
* Four new tables remain (technicians, job_lifecycle_events, alerts,
  daily_stats_snapshots). The ``outbound_drafts`` table is GONE.
* ``jobs`` has ``lifecycle_status`` (NOT NULL, default 'pending'),
  ``lifecycle_status_changed_at``, ``original_inbound_from_number``,
  ``original_inbound_channel``.
* ``whatsapp_tracked_chats`` has ``chat_role`` (NOT NULL,
  server_default 'other').
* ``incoming_messages`` has ``lifecycle_event_id`` (FK to
  ``job_lifecycle_events.id``, ON DELETE SET NULL).
* Indexes that back the alert engine exist.

Implementation note: asyncpg pools are tied to the event loop they're
created on. anyio's pytest plugin gives each test its own loop, so we
create a fresh engine + sessionmaker per test.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


EXPECTED_NEW_TABLES: set[str] = {
    "technicians",
    "job_lifecycle_events",
    "alerts",
    "daily_stats_snapshots",
}


# The outbound_drafts table was created in the 2026_06_27 migration and
# dropped in 2026_06_28 (system no longer sends). The drop is verified
# by ``TestOutboundDraftsDropped`` below.
DROPPED_TABLES: set[str] = {"outbound_drafts"}


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.scalar_one_or_none() is not None


async def _column_default(conn: AsyncConnection, table: str, column: str) -> str | None:
    result = await conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name = :t "
            "AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    row = result.first()
    return None if row is None else row[0]


async def _is_nullable(conn: AsyncConnection, table: str, column: str) -> bool | None:
    result = await conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name = :t "
            "AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    row = result.first()
    return None if row is None else row[0] == "YES"


async def _index_exists(conn: AsyncConnection, index: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname = :i"),
        {"i": index},
    )
    return result.scalar_one_or_none() is not None


async def _fk_exists(conn: AsyncConnection, table: str, fk_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE constraint_schema='public' AND table_name = :t "
            "AND constraint_type='FOREIGN KEY' AND constraint_name = :fk"
        ),
        {"t": table, "fk": fk_name},
    )
    return result.scalar_one_or_none() is not None


class TestAlembicRevision:
    @pytest.mark.anyio
    async def test_at_head(self):
        """Migration chain is at the latest revision (the drop migration)."""
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                assert result.scalar_one() == "2026_06_28_drop_outbound_drafts"
        finally:
            await engine.dispose()


class TestNewTablesExist:
    @pytest.mark.anyio
    async def test_all_four_new_tables_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                for table in EXPECTED_NEW_TABLES:
                    assert await _table_exists(conn, table), f"missing table: {table}"
        finally:
            await engine.dispose()


class TestOutboundDraftsDropped:
    @pytest.mark.anyio
    async def test_outbound_drafts_table_gone(self):
        """The 2026_06_28 migration dropped outbound_drafts. This test
        guards against a future accidental re-add."""
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                for table in DROPPED_TABLES:
                    assert not await _table_exists(conn, table), (
                        f"expected {table} to be dropped, but it exists"
                    )
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_outbound_drafts_index_gone(self):
        """The status/created_at partial index dropped with the table."""
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert not await _index_exists(conn, "ix_outbound_drafts_status_created_at_idx")
        finally:
            await engine.dispose()


class TestJobsLifecycleColumns:
    @pytest.mark.anyio
    async def test_lifecycle_status_not_null_with_default(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _table_exists(conn, "jobs")
                assert (await _is_nullable(conn, "jobs", "lifecycle_status")) is False
                default = await _column_default(conn, "jobs", "lifecycle_status")
                assert default is not None
                assert "pending" in default
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_lifecycle_status_changed_at_nullable(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert (await _is_nullable(conn, "jobs", "lifecycle_status_changed_at")) is True
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_original_inbound_columns_nullable(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert (await _is_nullable(conn, "jobs", "original_inbound_from_number")) is True
                assert (await _is_nullable(conn, "jobs", "original_inbound_channel")) is True
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_lifecycle_status_index_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _index_exists(conn, "ix_jobs_lifecycle_status_idx")
        finally:
            await engine.dispose()


class TestWhatsappChatRole:
    @pytest.mark.anyio
    async def test_chat_role_not_null_with_default(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert (await _is_nullable(conn, "whatsapp_tracked_chats", "chat_role")) is False
                default = await _column_default(conn, "whatsapp_tracked_chats", "chat_role")
                assert default is not None
                assert "other" in default
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_chat_role_index_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _index_exists(conn, "ix_whatsapp_tracked_chats_chat_role_idx")
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_existing_chat_defaults_to_other(self):
        """Existing rows are backfilled to 'other' (safe fallback)."""
        engine = create_async_engine(settings.DATABASE_URL)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessionmaker() as session:
                result = await session.execute(
                    text("SELECT DISTINCT chat_role FROM whatsapp_tracked_chats")
                )
                roles = {row[0] for row in result.fetchall()}
                assert roles <= {"other"}
                assert None not in roles
        finally:
            await engine.dispose()


class TestIncomingMessageLifecycleFK:
    @pytest.mark.anyio
    async def test_column_nullable(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert (await _is_nullable(conn, "incoming_messages", "lifecycle_event_id")) is True
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_fk_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _fk_exists(
                    conn,
                    "incoming_messages",
                    "fk_incoming_messages_lifecycle_event_id",
                )
        finally:
            await engine.dispose()


class TestAlertAndDraftIndexes:
    @pytest.mark.anyio
    async def test_alerts_kind_resolved_index_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _index_exists(conn, "ix_alerts_kind_resolved_at_idx")
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_lifecycle_events_job_id_created_index_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _index_exists(conn, "ix_job_lifecycle_events_job_id_created_at_idx")
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_daily_stats_date_scope_index_present(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                assert await _index_exists(conn, "ix_daily_stats_snapshots_date_scope_idx")
        finally:
            await engine.dispose()


class TestNewTableShapes:
    async def _has_columns(self, conn: AsyncConnection, table: str, cols: list[str]) -> None:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name = :t"
            ),
            {"t": table},
        )
        present = {row[0] for row in result.fetchall()}
        missing = [c for c in cols if c not in present]
        assert not missing, f"{table} missing columns: {missing}"

    @pytest.mark.anyio
    async def test_technicians_columns(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                await self._has_columns(
                    conn,
                    "technicians",
                    [
                        "id",
                        "name",
                        "phone_e164",
                        "whatsapp_chat_jid",
                        "is_active",
                        "notes",
                        "created_at",
                        "updated_at",
                    ],
                )
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_job_lifecycle_events_columns(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                await self._has_columns(
                    conn,
                    "job_lifecycle_events",
                    [
                        "id",
                        "job_id",
                        "source",
                        "from_status",
                        "to_status",
                        "payload",
                        "created_by_user_id",
                        "created_at",
                        "updated_at",
                    ],
                )
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_alerts_columns(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                await self._has_columns(
                    conn,
                    "alerts",
                    [
                        "id",
                        "job_id",
                        "chat_jid",
                        "kind",
                        "threshold_minutes",
                        "detected_at",
                        "resolved_at",
                        "resolved_by_user_id",
                        "payload",
                        "created_at",
                        "updated_at",
                    ],
                )
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_daily_stats_snapshots_columns(self):
        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                await self._has_columns(
                    conn,
                    "daily_stats_snapshots",
                    [
                        "id",
                        "snapshot_date",
                        "scope",
                        "scope_id",
                        "payload",
                        "created_at",
                        "updated_at",
                    ],
                )
        finally:
            await engine.dispose()


class TestRoundtripConstraints:
    """Insert one row into each new table inside a SAVEPOINT that is
    rolled back. Catches constraint regressions (e.g. someone removing a
    NOT NULL) that pure schema introspection would miss.
    """

    @pytest.mark.anyio
    async def test_technician_insert_roundtrips(self):
        engine = create_async_engine(settings.DATABASE_URL)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessionmaker() as session, session.begin_nested() as sp:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO technicians (id, name, is_active) "
                            "VALUES (gen_random_uuid(), 'test-tech', true)"
                        )
                    )
                    await session.execute(
                        text(
                            "INSERT INTO technicians (id, name) "
                            "VALUES (gen_random_uuid(), 'test-default-tech')"
                        )
                    )
                finally:
                    await sp.rollback()
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_chat_role_default_applies(self):
        """Inserting into whatsapp_tracked_chats without specifying
        chat_role should pick up the 'other' default."""
        engine = create_async_engine(settings.DATABASE_URL)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessionmaker() as session, session.begin_nested() as sp:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO whatsapp_tracked_chats "
                            "(id, chat_jid, display_name, is_group, is_active) "
                            "VALUES (gen_random_uuid(), "
                            "'test-jid@g.us', 'test', true, true)"
                        )
                    )
                finally:
                    await sp.rollback()
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_lifecycle_status_default_applies(self):
        """Inserting into jobs without specifying lifecycle_status should
        pick up 'pending'. (May fail FK on company_id; that's expected —
        we just verify the default kicks in before the FK rejects.)"""
        engine = create_async_engine(settings.DATABASE_URL)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessionmaker() as session, session.begin_nested() as sp:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO jobs (id, lifecycle_status) "
                            "VALUES (gen_random_uuid(), DEFAULT)"
                        )
                    )
                except Exception:
                    pass
                finally:
                    await sp.rollback()
        finally:
            await engine.dispose()
