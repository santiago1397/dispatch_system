"""Daily statistics snapshots for export and dashboard rendering.

Written by the ``daily-stats`` CLI command (or scheduler) at
end-of-day. Three scopes — ``per_job``, ``per_tech``, ``per_company``
— each row carries a JSONB ``payload`` with the relevant aggregates.

Keeping the payload as JSONB lets the schema evolve without DDL
changes when new metrics are added.
"""

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import Date, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StatsScope(StrEnum):
    PER_JOB = "per_job"
    PER_TECH = "per_tech"
    PER_COMPANY = "per_company"


class DailyStatsSnapshot(Base, TimestampMixin):
    """A daily snapshot row in one of three scopes.

    ``scope_id`` is NULL for ``per_job`` (which already has its own
    id in the payload) and points at a Technician / Company id for
    the other two scopes. The (snapshot_date, scope) index supports
    the dashboard query "give me all stats for date X". The unique
    constraint backs ``repositories/daily_stats.py:upsert_snapshot``'s
    ``ON CONFLICT (snapshot_date, scope, scope_id)`` — note Postgres
    treats NULL != NULL, so it does not dedupe ``per_job`` rows
    (``scope_id`` always NULL there); harmless today since ``per_job``
    is written at most once per date.
    """

    __tablename__ = "daily_stats_snapshots"
    __table_args__ = (
        Index(
            "ix_daily_stats_snapshots_date_scope_idx",
            "snapshot_date",
            "scope",
        ),
        UniqueConstraint(
            "snapshot_date",
            "scope",
            "scope_id",
            name="uq_daily_stats_snapshots_date_scope_scope_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return (
            f"<DailyStatsSnapshot(date={self.snapshot_date}, "
            f"scope={self.scope}, scope_id={self.scope_id})>"
        )
