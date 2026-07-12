"""Tests for the Chicago business-day (5am-cutoff) date helpers."""

from datetime import UTC, date, datetime
from unittest.mock import patch

from app.core.timezone import (
    BUSINESS_TZ,
    business_day_bounds,
    business_day_of,
    business_today,
)


class TestBusinessToday:
    def test_before_5am_is_still_yesterday(self):
        fake_now = datetime(2026, 7, 15, 4, 59, tzinfo=BUSINESS_TZ)
        with patch("app.core.timezone.business_now", return_value=fake_now):
            assert business_today() == date(2026, 7, 14)

    def test_at_5am_is_today(self):
        fake_now = datetime(2026, 7, 15, 5, 0, tzinfo=BUSINESS_TZ)
        with patch("app.core.timezone.business_now", return_value=fake_now):
            assert business_today() == date(2026, 7, 15)

    def test_evening_is_today(self):
        fake_now = datetime(2026, 7, 15, 22, 0, tzinfo=BUSINESS_TZ)
        with patch("app.core.timezone.business_now", return_value=fake_now):
            assert business_today() == date(2026, 7, 15)


class TestBusinessDayBounds:
    def test_cdt_summer_date(self):
        # 2026-07-15 is CDT (UTC-5): 5am Chicago == 10:00 UTC.
        start, end = business_day_bounds(date(2026, 7, 15))
        assert start == datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 16, 10, 0, tzinfo=UTC)

    def test_cst_winter_date(self):
        # 2026-01-15 is CST (UTC-6): 5am Chicago == 11:00 UTC.
        start, end = business_day_bounds(date(2026, 1, 15))
        assert start == datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
        assert end == datetime(2026, 1, 16, 11, 0, tzinfo=UTC)


class TestBusinessDayOf:
    def test_just_before_boundary_is_previous_day(self):
        dt = datetime(2026, 7, 15, 9, 59, tzinfo=UTC)  # 4:59am CDT
        assert business_day_of(dt) == date(2026, 7, 14)

    def test_just_after_boundary_is_same_day(self):
        dt = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)  # 5:00am CDT
        assert business_day_of(dt) == date(2026, 7, 15)
