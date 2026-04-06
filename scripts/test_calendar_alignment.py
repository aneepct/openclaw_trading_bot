"""
Run quick calendar alignment checks, then backend pytest.

Usage (repo root):
  python scripts/test_calendar_alignment.py

Requires backend deps (pip install -r backend/requirements.txt).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


def _inline_checks() -> None:
    sys.path.insert(0, str(BACKEND))
    from datetime import datetime, timezone

    from polymarket_calendar import poly_end_dates_today_tomorrow_utc, utc_today_tomorrow_dates

    fixed = datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc)
    t, t2 = utc_today_tomorrow_dates(fixed)
    got = poly_end_dates_today_tomorrow_utc(fixed)
    assert got == {t, t2}, (got, t, t2)
    print("OK inline: poly_end_dates_today_tomorrow_utc matches utc_today_tomorrow_dates", flush=True)

    from polymarket_markets_export.export_markets import utc_today_tomorrow_dates as em_utc

    assert em_utc is utc_today_tomorrow_dates
    print("OK export_markets uses polymarket_calendar.utc_today_tomorrow_dates", flush=True)


def main() -> int:
    print("--- Inline calendar checks ---", flush=True)
    try:
        _inline_checks()
    except AssertionError as e:
        print("FAIL inline check:", e, flush=True)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(BACKEND / "tests"),
        "-v",
        "--tb=short",
    ]
    print("\n--- pytest (backend/tests) ---", flush=True)
    print("Running:", " ".join(cmd), flush=True)
    rc = subprocess.call(cmd, cwd=str(BACKEND))
    if rc == 0:
        print("\nAll calendar + pytest checks passed.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
