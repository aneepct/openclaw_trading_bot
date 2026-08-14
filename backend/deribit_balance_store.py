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


def init_balance_db() -> None:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(_DDL)
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
    # For BTC account summaries, Deribit commonly reports BTC-denominated equity.
    # Estimate USD so downstream dashboards can mirror EMBEDDED_BALANCES shape.
    equity = _to_float(account.get("equity"))
    index_price = _to_float(account.get("index_price"))
    if equity is None or index_price is None:
        return None
    return equity * index_price


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
