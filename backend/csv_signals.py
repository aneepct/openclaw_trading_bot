from __future__ import annotations

import csv
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config


_latest_signals: list[dict[str, Any]] = []
_lock = threading.Lock()


def _utc_from_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _expiry_str_to_datetime(expiry_str: str) -> Optional[datetime]:
    # Deribit expiry format: `2APR26` (or `28MAR26`), parse with %d%b%y
    try:
        dt = datetime.strptime(expiry_str.strip().upper(), "%d%b%y")
        return dt.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    except ValueError:
        return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def delta_to_prob(delta: float, option_type: str) -> float:
    # Scanner logic: for calls, delta ~ P(S_T > K).
    # For puts (negative delta), P(S_T > K) = 1 + delta.
    if option_type == "C":
        return _clamp01(delta)
    return _clamp01(1.0 + delta)


def poly_resolution_time(end_date: datetime) -> datetime:
    # Matches scanner: settlement resolution at 16:00 UTC on the endDate day.
    return end_date.replace(hour=16, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _load_deribit_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_closest_in_strike(
    candidates: list[dict[str, Any]],
    *,
    strike: float,
    strike_tol_pct: float,
) -> Optional[dict[str, Any]]:
    # Pick closest strike within tolerance.
    tol = strike_tol_pct / 100.0
    best: Optional[dict[str, Any]] = None
    best_diff = float("inf")
    for c in candidates:
        c_strike = _to_float(c.get("strike"))
        if c_strike is None or strike == 0:
            continue
        strike_diff = abs(c_strike - strike) / strike
        if strike_diff > tol:
            continue
        if strike_diff < best_diff:
            best_diff = strike_diff
            best = c
    return best


def _compute_for_currency(
    *,
    currency: str,
    poly_csv_path: Path,
    deribit_today_csv_path: Path,
    deribit_tomorrow_csv_path: Path,
) -> tuple[list[dict[str, Any]], int]:
    poly_rows: list[dict[str, Any]] = []
    if poly_csv_path.exists():
        with poly_csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            poly_rows = [r for r in reader]

    der_today = _load_deribit_csv(deribit_today_csv_path)
    der_tomorrow = _load_deribit_csv(deribit_tomorrow_csv_path)

    if not poly_rows or (not der_today and not der_tomorrow):
        return [], len(poly_rows)

    # Index deribit rows by option_type; matching happens by strike proximity.
    der_today_by_opt: dict[str, list[dict[str, Any]]] = {"C": [], "P": []}
    der_tom_by_opt: dict[str, list[dict[str, Any]]] = {"C": [], "P": []}

    for r in der_today:
        opt = (r.get("option_type") or "").strip()
        if opt in ("C", "P"):
            der_today_by_opt[opt].append(r)
    for r in der_tomorrow:
        opt = (r.get("option_type") or "").strip()
        if opt in ("C", "P"):
            der_tom_by_opt[opt].append(r)

    # Infer T1/T2 from expiry_str in the CSV (should be uniform for each file).
    # If multiple expiries exist, we compute per-match using the row's expiry_str.
    signals: list[dict[str, Any]] = []

    for m in poly_rows:
        # Required for the probability comparison
        poly_prob = _to_float(m.get("outcomePrices_0_scaled"))
        if poly_prob is None:
            continue
        polymarket_price = poly_prob / 100.0

        try:
            strike = float(m.get("target_price_from_question") or "")
        except ValueError:
            continue
        if not math.isfinite(strike) or strike <= 0:
            continue

        option_type = (m.get("option_type") or "").strip()
        if option_type not in ("C", "P"):
            continue

        end_dt = _utc_from_iso(m.get("end_date_iso"))
        if not end_dt:
            continue
        t_star = poly_resolution_time(end_dt)

        # Match Deribit contexts by closest strike within tolerance.
        der1 = _find_closest_in_strike(
            der_today_by_opt[option_type],
            strike=strike,
            strike_tol_pct=config.STRIKE_TOLERANCE_PCT,
        )
        der2 = _find_closest_in_strike(
            der_tom_by_opt[option_type],
            strike=strike,
            strike_tol_pct=config.STRIKE_TOLERANCE_PCT,
        )

        if not der1 and not der2:
            continue

        # Compute P_T1 and P_T2
        def _context_prob(der_row: dict[str, Any]) -> tuple[Optional[float], Optional[datetime]]:
            delta = _to_float(der_row.get("delta"))
            expiry_str = (der_row.get("expiry_str") or "").strip()
            expiry_dt = _expiry_str_to_datetime(expiry_str) if expiry_str else None
            if delta is None or expiry_dt is None:
                return None, None
            return delta_to_prob(delta, option_type), expiry_dt

        p1, t1 = (None, None)
        p2, t2 = (None, None)
        if der1:
            p1, t1 = _context_prob(der1)
        if der2:
            p2, t2 = _context_prob(der2)

        if p1 is None and p2 is None:
            continue

        if p1 is not None and p2 is not None and t1 and t2 and (t2 - t1).total_seconds() != 0:
            w = (t_star - t1).total_seconds() / (t2 - t1).total_seconds()
            w = _clamp01(w)
            deribit_prob = (1.0 - w) * p1 + w * p2
        elif p1 is not None:
            deribit_prob = p1
        else:
            deribit_prob = p2  # type: ignore[assignment]

        if deribit_prob is None:
            continue

        deribit_prob = _clamp01(float(deribit_prob))
        edge_pct = round((deribit_prob - polymarket_price) * 100.0, 2)
        abs_edge_pct = round(abs(edge_pct), 2)
        has_alpha = abs_edge_pct >= float(config.MIN_EDGE_PCT)

        direction = "BUY" if edge_pct > 0 else "SELL"
        recommended_action = "BUY YES" if direction == "BUY" else "BUY NO"

        # Deribit spot proxy
        spot_price = _to_float(der1.get("index_price") if der1 else der2.get("index_price")) if (der1 or der2) else None

        payout_ratio = round(1.0 / polymarket_price, 2) if polymarket_price and polymarket_price > 0 else None
        liquidity_usd = _to_float(m.get("liquidity_usd")) or 0.0

        reasoning = (
            f"Deribit={deribit_prob * 100:.2f}% interpolated vs Poly={polymarket_price * 100:.2f}%; "
            f"edge {edge_pct:+.2f}%."
        )

        rank_label = (
            "***" if abs_edge_pct >= 10 else "**" if abs_edge_pct >= 5 else "*" if abs_edge_pct >= 2 else "pass"
        )

        instrument_t1_expiry = t1.isoformat() if t1 else None
        instrument_t2_expiry = t2.isoformat() if t2 else None

        signals.append(
            {
                "instrument_t1": der1.get("instrument_name") if der1 else None,
                "instrument_t2": der2.get("instrument_name") if der2 else None,
                "instrument_t1_expiry": instrument_t1_expiry,
                "instrument_t2_expiry": instrument_t2_expiry,
                "polymarket_market_id": m.get("market_id"),
                "polymarket_question": m.get("polymarket_question") or "",
                "option_type": option_type,
                "spot_price": spot_price,
                "strike": strike,
                "direction": direction,
                "recommended_action": recommended_action,
                "deribit_prob": round(deribit_prob, 4),
                "polymarket_price": round(polymarket_price, 4),
                "edge_pct": edge_pct,
                "abs_edge_pct": abs_edge_pct,
                "has_alpha": has_alpha,
                "payout_ratio": payout_ratio,
                "liquidity_usd": round(liquidity_usd, 2),
                "reasoning": reasoning,
                "structural_insight": "",
                "rank_label": rank_label,
                # Fields used by some frontend calculations (ticker/leaderboard style)
                "interp_method": "interpolated" if (der1 and der2) else ("T2-only" if der2 else "T1-only"),
                "scanned_at": datetime.utcnow().isoformat(),
            }
        )

    # Keep only alpha for matrix UI; caller may re-filter.
    signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)
    return signals, len(poly_rows)


async def refresh_latest_signals() -> None:
    # Works both locally and in Docker where backend build context copies files to /app.
    backend_root = Path(__file__).resolve().parent
    depth = getattr(config, "DERIBIT_DEPTH", 1)

    btc_poly = (
        backend_root
        / "polymarket_markets_export"
        / "output"
        / "BTC"
        / "polymarket_markets_today_utc.csv"
    )
    eth_poly = (
        backend_root
        / "polymarket_markets_export"
        / "output"
        / "ETH"
        / "polymarket_markets_today_utc.csv"
    )

    btc_t1 = (
        backend_root
        / "deribit_orderbook_data"
        / "output"
        / "BTC"
        / f"order_book_today_depth{depth}.csv"
    )
    btc_t2 = (
        backend_root
        / "deribit_orderbook_data"
        / "output"
        / "BTC"
        / f"order_book_tomorrow_depth{depth}.csv"
    )

    eth_t1 = (
        backend_root
        / "deribit_orderbook_data"
        / "output"
        / "ETH"
        / f"order_book_today_depth{depth}.csv"
    )
    eth_t2 = (
        backend_root
        / "deribit_orderbook_data"
        / "output"
        / "ETH"
        / f"order_book_tomorrow_depth{depth}.csv"
    )

    btc_signals, _btc_poly_total = _compute_for_currency(
        currency="BTC",
        poly_csv_path=btc_poly,
        deribit_today_csv_path=btc_t1,
        deribit_tomorrow_csv_path=btc_t2,
    )
    eth_signals, _eth_poly_total = _compute_for_currency(
        currency="ETH",
        poly_csv_path=eth_poly,
        deribit_today_csv_path=eth_t1,
        deribit_tomorrow_csv_path=eth_t2,
    )

    signals = [s for s in (btc_signals + eth_signals) if s.get("has_alpha")]
    signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)

    with _lock:
        global _latest_signals
        _latest_signals = signals


def get_latest_signals() -> list[dict[str, Any]]:
    with _lock:
        return list(_latest_signals)

