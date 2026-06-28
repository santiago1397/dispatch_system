"""Pydantic schemas for DailyStatsSnapshot."""

from datetime import date
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampSchema


class DailyStatsSnapshotRead(TimestampSchema):
    """A daily rollup row in one of three scopes (``per_job`` /
    ``per_tech`` / ``per_company``)."""

    id: UUID
    snapshot_date: date
    scope: str
    scope_id: UUID | None = None
    payload: dict = Field(default_factory=dict)


class DailyStatsList(BaseSchema):
    items: list[DailyStatsSnapshotRead]
    total: int
