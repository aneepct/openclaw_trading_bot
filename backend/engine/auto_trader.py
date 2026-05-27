"""
auto_trader.py — Autonomous 60-second trading loop.

State machine:
  SCANNING   → every 60 s: fetch latest alpha signals, pick the highest-edge
               opportunity, place a $5 GTC BUY order. On success → MONITORING.
  MONITORING → every 60 s: check open positions for the active token.
               When curPrice / avgPrice >= 1.05 (5 % profit), place a SELL
               order to close and return to SCANNING.

The loop relies on csv_refresh_loop (already running in main.py) to keep the
Deribit and Polymarket CSVs—and therefore the signal cache—up to date.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

ORDER_USD = 5.0          # target USDC spending per trade
MIN_SHARES = 5.0         # Polymarket CLOB minimum order size
PROFIT_TARGET_PCT = 5.0  # close when P&L % >= this value
SCAN_INTERVAL_S = 60     # seconds between scans / position checks

# ---------------------------------------------------------------------------
# Shared mutable state – only mutated inside the loop coroutine (no races)
# ---------------------------------------------------------------------------

_state: str = "SCANNING"              # "SCANNING" | "MONITORING"
_active_token_id: Optional[str] = None
_active_order_id: Optional[str] = None
_active_outcome: Optional[str] = None  # "YES" or "NO"


# ---------------------------------------------------------------------------
# Gamma API helper – resolve a Gamma market ID to [yes_token_id, no_token_id]
# ---------------------------------------------------------------------------

def _resolve_token_ids(market_id: str) -> list[str]:
    """
    Given a Gamma market ID (integer string) or condition_id (0x…),
    return [yes_token_id, no_token_id].

    Raises ValueError if resolution fails.
    """
    gamma = "https://gamma-api.polymarket.com"

    def _extract_ids(m: dict) -> Optional[list[str]]:
        ids = m.get("clobTokenIds") or []
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except Exception:
                return None
        if isinstance(ids, list) and len(ids) >= 2:
            return [str(ids[0]), str(ids[1])]
        return None

    # Try direct market lookup by ID
    try:
        r = httpx.get(f"{gamma}/markets/{market_id}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            raw = data if isinstance(data, dict) else (data[0] if data else {})
            result = _extract_ids(raw)
            if result:
                return result
    except Exception:
        pass

    # Fallback: search by condition_id
    try:
        r2 = httpx.get(f"{gamma}/markets", params={"condition_id": market_id}, timeout=10)
        if r2.status_code == 200:
            data2 = r2.json()
            markets = data2 if isinstance(data2, list) else data2.get("markets", [])
            if markets:
                result = _extract_ids(markets[0])
                if result:
                    return result
    except Exception:
        pass

    raise ValueError(f"Could not resolve token IDs for market_id={market_id!r}")


# ---------------------------------------------------------------------------
# SCANNING phase
# ---------------------------------------------------------------------------

async def _scan_and_trade() -> bool:
    """
    Find the highest-edge alpha signal and place a $5 BUY order.
    Returns True and transitions to MONITORING if an order is placed.
    """
    global _state, _active_token_id, _active_order_id, _active_outcome

    # Import here to avoid circular imports at module load time
    from csv_signals import get_latest_signals
    from clients.polymarket_trading import create_order as pm_create_order

    signals = get_latest_signals()
    alpha = [s for s in signals if s.get("has_alpha")]
    if not alpha:
        logger.info("[auto_trader] no alpha signals available — will retry")
        return False

    alpha.sort(key=lambda s: float(s.get("abs_edge_pct") or 0), reverse=True)
    best = alpha[0]

    market_id = best.get("polymarket_market_id") or ""
    edge_pct = float(best.get("edge_pct") or 0)
    poly_price = float(best.get("polymarket_price") or 0)
    question = best.get("polymarket_question") or "?"
    abs_edge = float(best.get("abs_edge_pct") or 0)

    if not market_id:
        logger.warning("[auto_trader] best signal has no polymarket_market_id; skipping")
        return False

    # Resolve CLOB token IDs
    try:
        yes_token, no_token = await asyncio.to_thread(_resolve_token_ids, market_id)
    except Exception as exc:
        logger.error("[auto_trader] failed to resolve market %r: %s", market_id, exc)
        return False

    # Decide side:
    #   edge_pct > 0  →  Deribit fair > Poly YES price  →  YES is cheap  →  BUY YES
    #   edge_pct < 0  →  Deribit fair < Poly YES price  →  NO is cheap   →  BUY NO
    if edge_pct > 0:
        token_id = yes_token
        price = poly_price
        outcome = "YES"
    else:
        token_id = no_token
        price = round(1.0 - poly_price, 4)
        outcome = "NO"

    if not (0 < price < 1):
        logger.warning(
            "[auto_trader] invalid price %.4f for %r; skipping", price, market_id
        )
        return False

    # Size: spend ~ORDER_USD, but respect the CLOB minimum share count
    size = round(ORDER_USD / price, 2)
    size = max(MIN_SHARES, size)

    logger.info(
        "[auto_trader] SCAN → BUY %s '%s'  price=%.4f  size=%.2f  edge=%.1f%%",
        outcome,
        question[:70],
        price,
        size,
        abs_edge,
    )

    try:
        resp = await asyncio.to_thread(pm_create_order, token_id, price, size, "BUY")
    except Exception as exc:
        logger.error("[auto_trader] order placement error: %s", exc)
        return False

    if not resp.get("success"):
        logger.error(
            "[auto_trader] order rejected by CLOB: %s", resp.get("errorMsg", resp)
        )
        return False

    order_id = resp.get("orderID")
    logger.info(
        "[auto_trader] order placed id=%s — switching to MONITORING", order_id
    )

    _state = "MONITORING"
    _active_token_id = token_id
    _active_order_id = order_id
    _active_outcome = outcome
    return True


# ---------------------------------------------------------------------------
# MONITORING phase
# ---------------------------------------------------------------------------

async def _monitor_position() -> None:
    """
    Check the open position for the active token. Close when 5 % profit reached.
    Resets to SCANNING when the position is closed (or gone).
    """
    global _state, _active_token_id, _active_order_id, _active_outcome

    from clients.polymarket_trading import (
        fetch_positions as pm_fetch_positions,
        create_order as pm_create_order,
        cancel_order as pm_cancel_order,
    )

    try:
        positions = await asyncio.to_thread(pm_fetch_positions, True)
    except Exception as exc:
        logger.error("[auto_trader] fetch_positions failed: %s", exc)
        return

    pos = next(
        (p for p in positions if p.get("asset") == _active_token_id), None
    )

    if pos is None:
        # Order was placed but not filled within the 60 s window.
        # Cancel it and immediately re-scan for the best current opportunity.
        if _active_order_id:
            logger.info(
                "[auto_trader] order %s not filled after 60s — cancelling and re-placing",
                _active_order_id[:20],
            )
            try:
                await asyncio.to_thread(pm_cancel_order, _active_order_id)
                logger.info("[auto_trader] order cancelled — re-scanning")
            except Exception as exc:
                logger.warning(
                    "[auto_trader] cancel failed (%s) — resetting and re-scanning anyway", exc
                )
        else:
            logger.debug(
                "[auto_trader] no open position for %s",
                (_active_token_id or "")[:20],
            )

        # Reset state then immediately try to place a fresh order.
        _state = "SCANNING"
        _active_token_id = None
        _active_order_id = None
        _active_outcome = None
        await _scan_and_trade()
        return

    avg = float(pos.get("avgPrice") or 0)
    cur = float(pos.get("curPrice") or 0)
    size = float(pos.get("size") or 0)

    if avg <= 0 or size <= 0:
        return

    pnl_pct = (cur - avg) / avg * 100.0
    logger.info(
        "[auto_trader] MONITOR %s: avg=%.4f cur=%.4f size=%.2f pnl=%.2f%%",
        (_active_token_id or "")[:20],
        avg,
        cur,
        size,
        pnl_pct,
    )

    if pnl_pct < PROFIT_TARGET_PCT:
        return  # not yet profitable enough — keep waiting

    # 5 % (or more) profit reached — close position
    # Sell 1 tick below current price for a quick fill
    sell_price = max(0.0001, min(0.9999, round(cur - 0.01, 4)))
    logger.info(
        "[auto_trader] %.1f%% profit reached — closing at %.4f", pnl_pct, sell_price
    )

    try:
        resp = await asyncio.to_thread(
            pm_create_order, _active_token_id, sell_price, size, "SELL"
        )
    except Exception as exc:
        logger.error("[auto_trader] close order error: %s", exc)
        return

    if not resp.get("success"):
        logger.error(
            "[auto_trader] close order rejected: %s", resp.get("errorMsg", resp)
        )
        return

    logger.info(
        "[auto_trader] close order placed id=%s — resuming SCANNING",
        resp.get("orderID"),
    )

    # Reset state
    _state = "SCANNING"
    _active_token_id = None
    _active_order_id = None
    _active_outcome = None


# ---------------------------------------------------------------------------
# Startup helper — resume monitoring if a position was already open
# ---------------------------------------------------------------------------

async def _resume_if_position_open() -> None:
    """
    On server startup, check whether there is already an open position.
    If so, enter MONITORING for the first matching position so we don't
    open a duplicate trade.
    """
    global _state, _active_token_id, _active_outcome

    from clients.polymarket_trading import fetch_positions as pm_fetch_positions

    try:
        positions = await asyncio.to_thread(pm_fetch_positions, True)
    except Exception as exc:
        logger.warning("[auto_trader] startup position check failed: %s", exc)
        return

    if positions:
        pos = positions[0]
        token_id = pos.get("asset", "")
        avg = float(pos.get("avgPrice") or 0)
        logger.info(
            "[auto_trader] found existing position %s avg=%.4f — starting in MONITORING mode",
            token_id[:20],
            avg,
        )
        _state = "MONITORING"
        _active_token_id = token_id
        _active_outcome = "UNKNOWN"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def auto_trader_loop() -> None:
    """
    Entry point — run this as an asyncio task from main.py's lifespan.

    Every SCAN_INTERVAL_S seconds:
      • SCANNING   → try to find and place the best available trade
      • MONITORING → check position P&L and close at 5 % profit
    """
    logger.info("[auto_trader] starting (interval=%ds)", SCAN_INTERVAL_S)
    await _resume_if_position_open()

    while True:
        try:
            if _state == "SCANNING":
                await _scan_and_trade()
            else:
                await _monitor_position()
        except asyncio.CancelledError:
            logger.info("[auto_trader] cancelled — shutting down")
            raise
        except Exception as exc:
            logger.exception("[auto_trader] unexpected error: %s", exc)

        await asyncio.sleep(SCAN_INTERVAL_S)
