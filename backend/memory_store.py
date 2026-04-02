"""
In-memory replacement for the former SQLite layer.
Signals and agent memory are kept in process RAM only (lost on restart).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

_signals: list[dict[str, Any]] = []
_next_signal_id = 0
_leaderboard_cache: list[dict[str, Any]] = []
_agent_memory: dict[str, str] = {}


async def init_db() -> None:
    """Compatibility no-op (no disk database)."""
    return


def _cutoff_iso_hours(hours: int) -> str:
    return (datetime.utcnow() - timedelta(hours=hours)).isoformat()


def _cutoff_iso_days(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _is_has_alpha(row: dict[str, Any]) -> bool:
    v = row.get("has_alpha")
    return v is True or v == 1


async def save_signal(signal: dict[str, Any]) -> None:
    global _next_signal_id
    _next_signal_id += 1
    row = dict(signal)
    row["id"] = _next_signal_id
    if not row.get("raw_json"):
        row["raw_json"] = json.dumps(signal)
    row["has_alpha"] = 1 if signal.get("has_alpha") else 0
    _signals.append(row)


async def cleanup_old_signals(retain_days: int = 30) -> None:
    global _signals
    cutoff = _cutoff_iso_days(retain_days)
    _signals = [s for s in _signals if (s.get("scanned_at") or "") >= cutoff]


def _compute_leaderboard_rows(hours: int, top_n: int) -> list[dict[str, Any]]:
    cutoff = _cutoff_iso_hours(hours)
    filtered = [
        s
        for s in _signals
        if _is_has_alpha(s) and (s.get("scanned_at") or "") >= cutoff
    ]
    by_group: dict[tuple[Any, Any], dict[str, Any]] = {}
    for s in filtered:
        key = (s.get("instrument_t1"), s.get("instrument_t2"))
        sid = int(s.get("id") or 0)
        prev = by_group.get(key)
        if prev is None or int(prev.get("id") or 0) < sid:
            by_group[key] = s
    rows = list(by_group.values())
    rows.sort(key=lambda x: float(x.get("abs_edge_pct") or 0.0), reverse=True)
    return rows[:top_n]


async def refresh_alpha_leaderboard_cache(hours: int = 24, top_n: int = 5) -> None:
    global _leaderboard_cache
    rows = _compute_leaderboard_rows(hours, top_n)
    now = datetime.utcnow().isoformat()
    _leaderboard_cache = []
    for i, r in enumerate(rows, start=1):
        _leaderboard_cache.append(
            {
                "rank": i,
                "polymarket_market_id": r.get("polymarket_market_id"),
                "polymarket_question": r.get("polymarket_question"),
                "instrument_t1": r.get("instrument_t1"),
                "instrument_t2": r.get("instrument_t2"),
                "option_type": r.get("option_type"),
                "abs_edge_pct": r.get("abs_edge_pct"),
                "edge_pct": r.get("edge_pct"),
                "direction": r.get("direction"),
                "payout_ratio": r.get("payout_ratio"),
                "liquidity_usd": r.get("liquidity_usd"),
                "deribit_prob": r.get("deribit_prob"),
                "polymarket_price": r.get("polymarket_price"),
                "reasoning": r.get("reasoning"),
                "scanned_at": r.get("scanned_at"),
                "refreshed_at": now,
            }
        )


async def get_leaderboard(hours: int = 24, top_n: int = 5) -> list[dict[str, Any]]:
    if hours == 24 and top_n == 5 and _leaderboard_cache:
        return [dict(r) for r in _leaderboard_cache]
    return _compute_leaderboard_rows(hours, top_n)


async def get_recent_signals(hours: int = 1) -> list[dict[str, Any]]:
    cutoff = _cutoff_iso_hours(hours)
    rows = [s for s in _signals if (s.get("scanned_at") or "") >= cutoff]
    rows.sort(key=lambda s: s.get("scanned_at") or "", reverse=True)
    return [dict(r) for r in rows]


async def get_agent_memory(key: str, default: str = "") -> str:
    return _agent_memory.get(key, default)


async def set_agent_memory(key: str, value: str) -> None:
    _agent_memory[key] = value
