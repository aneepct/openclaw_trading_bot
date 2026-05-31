"""
auto_trader.py — Autonomous 60-second trading loop, one per asset (BTC / ETH).

State machine (per asset):
  SCANNING   → every 60 s: fetch latest alpha signals filtered to this asset,
               pick the highest-edge opportunity, place a $5 GTC BUY order.
               On success → MONITORING.
  MONITORING → every 60 s: check open positions for the active token.
               • Not filled yet → cancel and immediately re-scan.
               • Filled and P&L >= 5 % → place SELL to close, return to SCANNING.

Two independent asyncio tasks run in parallel:
  auto_trader_loop("BTC")  and  auto_trader_loop("ETH")

Both are started from main.py's lifespan.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
# Per-asset state — each loop owns one instance; no sharing → no races
# ---------------------------------------------------------------------------

@dataclass
class _AssetState:
    asset: str                        # "BTC" or "ETH"
    state: str = "SCANNING"           # "SCANNING" | "MONITORING"
    active_token_id: Optional[str] = None
    active_order_id: Optional[str] = None
    active_outcome: Optional[str] = None  # "YES" or "NO"
    market_end_date: Optional[str] = None  # endDate ISO datetime from Gamma API (e.g. "2026-06-01T16:00:00Z")

    @property
    def tag(self) -> str:
        return f"[auto_trader/{self.asset}]"

    def reset(self) -> None:
        self.state = "SCANNING"
        self.active_token_id = None
        self.active_order_id = None
        self.active_outcome = None
        self.market_end_date = None


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

async def _scan_and_trade(st: _AssetState) -> bool:
    """
    Find the highest-edge alpha signal for `st.asset` and place a $5 BUY order.
    Returns True and transitions to MONITORING if an order is placed.
    """
    from csv_signals import get_latest_signals
    from clients.polymarket_trading import create_order as pm_create_order

    signals = get_latest_signals()

    # Only trade markets resolving TODAY (UTC). Skip if only tomorrow's market exists.
    today_utc = datetime.now(timezone.utc).date()

    def _resolves_today(s: dict) -> bool:
        raw = s.get("market_resolution_at") or ""
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).date() == today_utc
        except Exception:
            return False

    alpha = [
        s for s in signals
        if s.get("has_alpha")
        and (s.get("currency") or "").upper() == st.asset
        and _resolves_today(s)
    ]
    if not alpha:
        logger.info("%s no alpha signals resolving today (%s) — skipping", st.tag, today_utc)
        return False

    alpha.sort(key=lambda s: float(s.get("abs_edge_pct") or 0), reverse=True)
    best = alpha[0]

    market_id    = best.get("polymarket_market_id") or ""
    edge_pct     = float(best.get("edge_pct") or 0)
    poly_price   = float(best.get("polymarket_price") or 0)
    deribit_prob = float(best.get("deribit_prob") or 0)
    question     = best.get("polymarket_question") or "?"
    abs_edge     = float(best.get("abs_edge_pct") or 0)

    if not market_id:
        logger.warning("%s best signal has no polymarket_market_id; skipping", st.tag)
        return False

    # Require Deribit conviction > 51 % for YES or < 49 % for NO.
    # The 0.49–0.51 band means Deribit has no strong view — skip.
    if deribit_prob > 0.51:
        outcome_target = "YES"
    elif deribit_prob < 0.49:
        outcome_target = "NO"
    else:
        logger.info(
            "%s deribit_prob=%.3f in neutral band (0.49–0.51) — no strong conviction, skipping",
            st.tag, deribit_prob,
        )
        return False

    # Resolve CLOB token IDs
    try:
        yes_token, no_token = await asyncio.to_thread(_resolve_token_ids, market_id)
    except Exception as exc:
        logger.error("%s failed to resolve market %r: %s", st.tag, market_id, exc)
        return False

    # Use deribit_prob to pick side; derive the CLOB entry price accordingly.
    if outcome_target == "YES":
        token_id = yes_token
        price    = poly_price
        outcome  = "YES"
    else:
        token_id = no_token
        price    = round(1.0 - poly_price, 4)
        outcome  = "NO"

    if not (0 < price < 1):
        logger.warning("%s invalid price %.4f for %r; skipping", st.tag, price, market_id)
        return False

    # Size: spend ~ORDER_USD, but respect the CLOB minimum share count
    size = round(ORDER_USD / price, 2)
    size = max(MIN_SHARES, size)

    logger.info(
        "%s SCAN → BUY %s '%s'  price=%.4f  size=%.2f  edge=%.1f%%",
        st.tag, outcome, question[:70], price, size, abs_edge,
    )

    try:
        resp = await asyncio.to_thread(pm_create_order, token_id, price, size, "BUY")
    except Exception as exc:
        logger.error("%s order placement error: %s", st.tag, exc)
        return False

    if not resp.get("success"):
        logger.error("%s order rejected by CLOB: %s", st.tag, resp.get("errorMsg", resp))
        return False

    order_id = resp.get("orderID")
    logger.info("%s order placed id=%s — switching to MONITORING", st.tag, order_id)

    st.state = "MONITORING"
    st.active_token_id = token_id
    st.active_order_id = order_id
    st.active_outcome = outcome
    if not st.market_end_date:
        st.market_end_date = best.get("market_resolution_at") or None
    return True


# ---------------------------------------------------------------------------
# MONITORING phase
# ---------------------------------------------------------------------------

async def _monitor_position(st: _AssetState) -> None:
    """
    Check the open position for the active token. Close when 5 % profit reached.
    If the order is still unfilled, cancel and immediately re-scan.
    """
    from clients.polymarket_trading import (
        fetch_positions as pm_fetch_positions,
        create_order as pm_create_order,
        cancel_order as pm_cancel_order,
    )

    try:
        positions = await asyncio.to_thread(pm_fetch_positions, True)
    except Exception as exc:
        logger.error("%s fetch_positions failed: %s", st.tag, exc)
        return

    # Match position by `asset` (primary key) or `tokenId` (fallback) to be
    # robust against different field names returned by the data API.
    pos = next(
        (
            p for p in positions
            if p.get("asset") == st.active_token_id
            or p.get("tokenId") == st.active_token_id
        ),
        None,
    )

    if pos is None:
        # Order placed but not filled within the 60 s window — cancel and re-scan.
        if st.active_order_id:
            logger.info(
                "%s order %s not filled after 60s — cancelling and re-placing",
                st.tag, st.active_order_id[:20],
            )
            try:
                await asyncio.to_thread(pm_cancel_order, st.active_order_id)
                logger.info("%s order cancelled — re-scanning", st.tag)
            except Exception as exc:
                # Cancel failed — the order may still be live on the CLOB.
                # Do NOT place a new order; wait for the next cycle to retry.
                logger.warning(
                    "%s cancel failed (%s) — will retry next cycle without placing a new order",
                    st.tag, exc,
                )
                return
        else:
            logger.debug("%s no open position for %s", st.tag, (st.active_token_id or "")[:20])

        st.reset()
        await _scan_and_trade(st)
        return

    avg = float(pos.get("avgPrice") or 0)
    cur = float(pos.get("curPrice") or 0)
    size = float(pos.get("size") or 0)

    if avg <= 0 or size <= 0:
        return

    pnl_pct = (cur - avg) / avg * 100.0
    logger.info(
        "%s MONITOR %s: avg=%.4f cur=%.4f size=%.2f pnl=%.2f%%",
        st.tag, (st.active_token_id or "")[:20], avg, cur, size, pnl_pct,
    )

    # Close immediately if market has reached its resolution time
    logger.info(
        "%s expiry check: market_end_date=%s now_utc=%s",
        st.tag, st.market_end_date, datetime.now(timezone.utc).isoformat(),
    )
    if st.market_end_date:
        try:
            end_dt = datetime.fromisoformat(st.market_end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt <= datetime.now(timezone.utc):
                sell_price = max(0.0001, min(0.9999, round(cur - 0.01, 4)))
                logger.warning(
                    "%s market expired at %s — closing position at %.4f (pnl=%.2f%%)",
                    st.tag, st.market_end_date, sell_price, pnl_pct,
                )
                try:
                    resp = await asyncio.to_thread(
                        pm_create_order, st.active_token_id, sell_price, size, "SELL"
                    )
                except Exception as exc:
                    logger.error("%s expiry close order error: %s", st.tag, exc)
                    return
                if not resp.get("success"):
                    logger.error("%s expiry close order rejected: %s", st.tag, resp.get("errorMsg", resp))
                    return
                logger.info(
                    "%s expiry close order placed id=%s — resuming SCANNING",
                    st.tag, resp.get("orderID"),
                )
                st.reset()
                return
        except Exception:
            pass

    # Stop-loss: close immediately if loss exceeds 50 %
    STOP_LOSS_PCT = -50.0
    if pnl_pct <= STOP_LOSS_PCT:
        sell_price = max(0.0001, min(0.9999, round(cur - 0.01, 4)))
        logger.warning(
            "%s STOP-LOSS triggered at %.1f%% — closing at %.4f", st.tag, pnl_pct, sell_price
        )
        try:
            resp = await asyncio.to_thread(
                pm_create_order, st.active_token_id, sell_price, size, "SELL"
            )
        except Exception as exc:
            logger.error("%s stop-loss order error: %s", st.tag, exc)
            return
        if not resp.get("success"):
            logger.error("%s stop-loss order rejected: %s", st.tag, resp.get("errorMsg", resp))
            return
        logger.info(
            "%s stop-loss order placed id=%s — resuming SCANNING", st.tag, resp.get("orderID")
        )
        st.reset()
        return

    if pnl_pct < PROFIT_TARGET_PCT:
        return  # not yet profitable enough — keep waiting

    # 5 % (or more) profit reached — close position
    sell_price = max(0.0001, min(0.9999, round(cur - 0.01, 4)))
    logger.info("%s %.1f%% profit reached — closing at %.4f", st.tag, pnl_pct, sell_price)

    try:
        resp = await asyncio.to_thread(
            pm_create_order, st.active_token_id, sell_price, size, "SELL"
        )
    except Exception as exc:
        logger.error("%s close order error: %s", st.tag, exc)
        return

    if not resp.get("success"):
        logger.error("%s close order rejected: %s", st.tag, resp.get("errorMsg", resp))
        return

    logger.info("%s close order placed id=%s — resuming SCANNING", st.tag, resp.get("orderID"))
    st.reset()


def _market_info_for_token(token_id: str) -> dict:
    """
    Return {'question': str, 'end_date': str|None} for a CLOB token ID.

    end_date is taken directly from the API response (already 16:00 UTC).
    """
    try:
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"clob_token_ids": token_id},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            if markets:
                m = markets[0]
                question = m.get("question") or ""
                end_date = m.get("endDate") or None
                return {"question": question, "end_date": end_date}
    except Exception:
        pass
    return {"question": "", "end_date": None}


def _question_for_token(token_id: str) -> str:
    """Return the Polymarket market question for a CLOB token ID, or '' on failure."""
    return _market_info_for_token(token_id)["question"]


# Mapping from internal asset code to keywords that appear in Polymarket question text.
_ASSET_KEYWORDS: dict[str, list[str]] = {
    "BTC": ["BTC", "Bitcoin"],
    "ETH": ["ETH", "Ethereum"],
}


def _question_matches_asset(question: str, asset: str) -> bool:
    """Return True if any keyword for the asset appears in the question (case-insensitive)."""
    q = question.upper()
    for kw in _ASSET_KEYWORDS.get(asset.upper(), [asset.upper()]):
        if kw.upper() in q:
            return True
    return False


# ---------------------------------------------------------------------------
# Startup helper — resume monitoring if a position is already open
# ---------------------------------------------------------------------------

async def _resume_if_position_open(st: _AssetState) -> None:
    """
    On server startup, check for an existing open order or filled position for
    this asset. If found, enter MONITORING so we don't open a duplicate trade.

    Strategy:
      1. Check open (unfilled) orders via the CLOB API → restores token_id + order_id.
      2. Check filled positions via the Data API → restores token_id only.
      In both cases the market question is looked up via the Gamma API to
      confirm the token belongs to this asset (BTC / ETH).
    """
    from clients.polymarket_trading import (
        fetch_positions as pm_fetch_positions,
        fetch_open_orders as pm_fetch_open_orders,
    )

    # --- Step 1: look for an unfilled BUY order ---
    try:
        open_orders = await asyncio.to_thread(pm_fetch_open_orders)
        buy_orders = [
            o for o in open_orders
            if (o.get("side") or "").upper() == "BUY"
        ]
        for order in buy_orders:
            token_id = order.get("asset_id") or order.get("tokenId") or ""
            if not token_id:
                continue
            info = await asyncio.to_thread(_market_info_for_token, token_id)
            if _question_matches_asset(info["question"] or "", st.asset):
                order_id = order.get("id") or order.get("orderID") or ""
                logger.info(
                    "%s found open BUY order id=%s token=%s — resuming MONITORING",
                    st.tag, order_id[:20], token_id[:20],
                )
                st.state = "MONITORING"
                st.active_token_id = token_id
                st.active_order_id = order_id
                st.active_outcome = "UNKNOWN"
                if not st.market_end_date:
                    st.market_end_date = info["end_date"]
                return
    except Exception as exc:
        logger.warning("%s startup open-order check failed: %s", st.tag, exc)

    # --- Step 2: look for a filled (held) position ---
    try:
        positions = await asyncio.to_thread(pm_fetch_positions, True)
        for pos in positions:
            token_id = pos.get("asset") or pos.get("tokenId") or ""
            if not token_id:
                continue
            info = await asyncio.to_thread(_market_info_for_token, token_id)
            if _question_matches_asset(info["question"] or "", st.asset):
                avg = float(pos.get("avgPrice") or 0)
                logger.info(
                    "%s found existing position token=%s avg=%.4f — resuming MONITORING",
                    st.tag, token_id[:20], avg,
                )
                st.state = "MONITORING"
                st.active_token_id = token_id
                st.active_order_id = None  # already filled; nothing to cancel
                st.active_outcome = "UNKNOWN"
                if not st.market_end_date:
                    st.market_end_date = info["end_date"]
                return
    except Exception as exc:
        logger.warning("%s startup position check failed: %s", st.tag, exc)


# ---------------------------------------------------------------------------
# Per-asset loop
# ---------------------------------------------------------------------------

async def auto_trader_loop(asset: str) -> None:
    """
    Entry point for a single asset loop. Launch two of these — one for "BTC"
    and one for "ETH" — as separate asyncio tasks from main.py's lifespan.

    Every SCAN_INTERVAL_S seconds:
      • SCANNING   → pick best signal for this asset and place a $5 BUY
      • MONITORING → watch P&L; cancel & re-scan if unfilled; close at 5 % profit
    """
    st = _AssetState(asset=asset.upper())
    logger.info("%s starting (interval=%ds)", st.tag, SCAN_INTERVAL_S)
    await _resume_if_position_open(st)

    while True:
        try:
            if st.state == "SCANNING":
                await _scan_and_trade(st)
            else:
                await _monitor_position(st)
        except asyncio.CancelledError:
            logger.info("%s cancelled — shutting down", st.tag)
            raise
        except Exception as exc:
            logger.exception("%s unexpected error: %s", st.tag, exc)

        await asyncio.sleep(SCAN_INTERVAL_S)
