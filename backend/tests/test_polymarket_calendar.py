"""Unit tests for UTC today/tomorrow window shared by CSV export and live scanner."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from polymarket_calendar import poly_end_dates_today_tomorrow_utc, utc_today_tomorrow_dates


def test_utc_today_tomorrow_mid_year():
    fixed = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    t, t2 = utc_today_tomorrow_dates(fixed)
    assert t == date(2026, 6, 15)
    assert t2 == date(2026, 6, 16)
    assert t2 == t + timedelta(days=1)


def test_utc_today_tomorrow_year_rollover():
    fixed = datetime(2026, 12, 31, 8, 0, 0, tzinfo=timezone.utc)
    t, t2 = utc_today_tomorrow_dates(fixed)
    assert t == date(2026, 12, 31)
    assert t2 == date(2027, 1, 1)


def test_poly_end_dates_matches_tuple():
    fixed = datetime(2026, 4, 6, 23, 59, 0, tzinfo=timezone.utc)
    t, t2 = utc_today_tomorrow_dates(fixed)
    s = poly_end_dates_today_tomorrow_utc(fixed)
    assert s == {t, t2}
    assert len(s) == 2


def test_export_markets_uses_calendar_module():
    """Ensure CSV export pulls the same helpers (single source)."""
    from polymarket_markets_export.export_markets import utc_today_tomorrow_dates as em_fn

    assert em_fn is utc_today_tomorrow_dates
