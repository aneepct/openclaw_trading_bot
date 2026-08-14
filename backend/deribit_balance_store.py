"""
deribit_balance_store.py

Persist daily Deribit account snapshots for API consumption.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "openclaw_deribit_balances.db"

_DDL = """
CREATE TABLE IF NOT EXISTS deribit_account_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    currency TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    recorded_date_utc TEXT NOT NULL,
    equity REAL,
    margin_balance REAL,
    available_funds REAL,
    available_withdrawal_funds REAL,
    initial_margin REAL,
    maintenance_margin REAL,
    total_pl REAL,
    usd_estimate REAL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(currency, recorded_at_utc)
);
"""

_PERF_DDL = """
CREATE TABLE IF NOT EXISTS deribit_balance_performance (
    currency TEXT PRIMARY KEY,
    label TEXT,
    as_of TEXT,
    twr_net_pct REAL,
    twr_net_annualized_pct REAL,
    irr_money_weighted_pct REAL,
    note TEXT,
    updated_at TEXT NOT NULL
);
"""


def init_balance_db() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(_DDL)
        conn.execute(_PERF_DDL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bal_currency_date ON deribit_account_balances(currency, recorded_date_utc)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bal_currency_ts ON deribit_account_balances(currency, recorded_at_utc DESC)"
        )
        conn.commit()
    logger.info("[deribit_balance_store] DB initialised at %s", _DB_PATH)


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _estimate_usd(account: dict[str, Any]) -> Optional[float]:
    # If Deribit sends a direct USD estimate, trust and store it as-is.
    for key in ("usd_estimate", "margin_balance_usd", "equity_usd"):
        direct = _to_float(account.get(key))
        if direct is not None:
            return direct

    # Prefer margin_balance for dashboard daily balance parity.
    # Fall back to equity when margin_balance is unavailable.
    btc_balance = _to_float(account.get("margin_balance"))
    if btc_balance is None:
        btc_balance = _to_float(account.get("equity"))
    index_price = _to_float(account.get("index_price"))
    if btc_balance is None or index_price is None:
        return None
    return btc_balance * index_price


def record_balance_snapshot(
    currency: str,
    account: dict[str, Any],
    recorded_at_utc: Optional[str] = None,
) -> None:
    ts = recorded_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    day = dt.date().isoformat()
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO deribit_account_balances
            (currency, recorded_at_utc, recorded_date_utc, equity, margin_balance,
             available_funds, available_withdrawal_funds, initial_margin,
             maintenance_margin, total_pl, usd_estimate, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                currency.upper(),
                ts,
                day,
                _to_float(account.get("equity")),
                _to_float(account.get("margin_balance")),
                _to_float(account.get("available_funds")),
                _to_float(account.get("available_withdrawal_funds")),
                _to_float(account.get("initial_margin")),
                _to_float(account.get("maintenance_margin")),
                _to_float(account.get("total_pl")),
                _estimate_usd(account),
                json.dumps(account, separators=(",", ":")),
                created_at,
            ),
        )
        conn.commit()


def get_latest_balance(currency: str = "BTC") -> Optional[dict[str, Any]]:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, currency, recorded_at_utc, recorded_date_utc, equity,
                   margin_balance, available_funds, available_withdrawal_funds,
                   initial_margin, maintenance_margin, total_pl, usd_estimate,
                   raw_json, created_at
            FROM deribit_account_balances
            WHERE currency = ?
            ORDER BY recorded_at_utc DESC, id DESC
            LIMIT 1
            """,
            (currency.upper(),),
        ).fetchone()
        return dict(row) if row else None


def get_daily_balances(currency: str = "BTC", limit: int = 400) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 5000))
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            WITH ranked AS (
              SELECT id, currency, recorded_at_utc, recorded_date_utc, equity,
                     margin_balance, available_funds, available_withdrawal_funds,
                     initial_margin, maintenance_margin, total_pl, usd_estimate,
                     raw_json, created_at,
                     ROW_NUMBER() OVER (
                         PARTITION BY currency, recorded_date_utc
                         ORDER BY recorded_at_utc DESC, id DESC
                     ) AS rn
              FROM deribit_account_balances
              WHERE currency = ?
            )
            SELECT id, currency, recorded_at_utc, recorded_date_utc, equity,
                   margin_balance, available_funds, available_withdrawal_funds,
                   initial_margin, maintenance_margin, total_pl, usd_estimate,
                   raw_json, created_at
            FROM ranked
            WHERE rn = 1
            ORDER BY recorded_date_utc DESC
            LIMIT ?
            """,
            (currency.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_performance(currency: str, performance: dict[str, Any]) -> None:
    updated_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO deribit_balance_performance
                (currency, label, as_of, twr_net_pct, twr_net_annualized_pct,
                 irr_money_weighted_pct, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(currency) DO UPDATE SET
                label = excluded.label,
                as_of = excluded.as_of,
                twr_net_pct = excluded.twr_net_pct,
                twr_net_annualized_pct = excluded.twr_net_annualized_pct,
                irr_money_weighted_pct = excluded.irr_money_weighted_pct,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                currency.upper(),
                performance.get("label"),
                performance.get("as_of"),
                _to_float(performance.get("twr_net_pct")),
                _to_float(performance.get("twr_net_annualized_pct")),
                _to_float(performance.get("irr_money_weighted_pct")),
                performance.get("note"),
                updated_at,
            ),
        )
        conn.commit()


def get_performance(currency: str = "BTC") -> Optional[dict[str, Any]]:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT currency, label, as_of, twr_net_pct, twr_net_annualized_pct,
                   irr_money_weighted_pct, note, updated_at
            FROM deribit_balance_performance
            WHERE currency = ?
            LIMIT 1
            """,
            (currency.upper(),),
        ).fetchone()
        return dict(row) if row else None


def _xirr(cashflows: list[tuple[datetime, float]]) -> Optional[float]:
    """Compute annualized IRR using bisection; returns a ratio (e.g. 0.33 for 33%)."""
    if len(cashflows) < 2:
        return None

    cfs = sorted(cashflows, key=lambda x: x[0])
    d0 = cfs[0][0]

    def npv(rate: float) -> float:
        return sum(
            amount / ((1.0 + rate) ** (((dt - d0).days) / 365.0))
            for dt, amount in cfs
        )

    lo, hi = -0.999, 100.0
    try:
        f_lo = npv(lo)
        f_hi = npv(hi)
    except Exception:
        return None
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:
        return None

    for _ in range(300):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if f_mid > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def compute_performance_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Rows come newest-first from get_daily_balances.
    series: list[tuple[str, float, dict[str, Any]]] = []
    for r in rows:
        val = _to_float(r.get("usd_estimate"))
        if val is None:
            val = _to_float(r.get("margin_balance"))
        if val is None:
            continue
        day = str(r.get("recorded_date_utc") or "")
        if not day:
            continue
        raw: dict[str, Any] = {}
        try:
            loaded = json.loads(r.get("raw_json") or "{}")
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            pass
        series.append((day, val, raw))

    if len(series) < 2:
        return {
            "label": "Net return from available history",
            "as_of": series[0][0] if series else None,
            "twr_net_pct": None,
            "twr_net_annualized_pct": None,
            "irr_money_weighted_pct": None,
            "note": "Insufficient history to compute performance.",
        }

    # Oldest -> newest for chain-linking daily returns.
    chron = list(reversed(series))
    oldest_day, oldest_val, _ = chron[0]
    newest_day, newest_val, _ = chron[-1]
    if oldest_val <= 0 or newest_val <= 0:
        return {
            "label": "Net return from available history",
            "as_of": newest_day,
            "twr_net_pct": None,
            "twr_net_annualized_pct": None,
            "irr_money_weighted_pct": None,
            "note": "Cannot compute returns because one or more values are non-positive.",
        }

    chain = 1.0
    prev = chron[0][1]
    for _, current, _raw in chron[1:]:
        if prev <= 0 or current <= 0:
            continue
        daily_r = current / prev - 1.0
        chain *= (1.0 + daily_r)
        prev = current

    twr_ratio = chain - 1.0
    d0 = datetime.fromisoformat(oldest_day)
    d1 = datetime.fromisoformat(newest_day)
    days = max(1, (d1 - d0).days)
    years = days / 365.0
    twr_annualized_ratio = (1.0 + twr_ratio) ** (1.0 / years) - 1.0

    # Optional IRR from explicit external cash-flow fields (if present in raw_json).
    # Convention: positive flow means contribution into the fund (investor cash out),
    # so we invert sign in IRR cash-flow list and append terminal value as positive.
    flows: list[tuple[datetime, float]] = []
    for day, _val, raw in chron:
        flow = _to_float(raw.get("external_flow_usd"))
        if flow is None or flow == 0:
            continue
        flows.append((datetime.fromisoformat(day), -flow))
    flows.append((d1, newest_val))
    irr_ratio = _xirr(flows) if len(flows) >= 2 else None

    return {
        "label": "Net return from available history",
        "as_of": newest_day,
        "twr_net_pct": round(twr_ratio * 100.0, 2),
        "twr_net_annualized_pct": round(twr_annualized_ratio * 100.0, 2),
        "irr_money_weighted_pct": round(irr_ratio * 100.0, 2) if irr_ratio is not None else None,
        "note": "Daily chain-linked TWR from stored balances. IRR is computed only when external_flow_usd is provided in snapshots.",
    }


def refresh_and_store_performance(currency: str = "BTC", limit: int = 5000) -> dict[str, Any]:
    rows = get_daily_balances(currency, limit)
    perf = compute_performance_from_rows(rows)
    upsert_performance(currency, perf)
    return perf
