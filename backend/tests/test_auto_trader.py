"""
Unit tests for auto_trader.py

Covers:
  - _resolves_today date filter in _scan_and_trade
  - cancel-failure guard (no second order placed)
  - stop-loss at -50 %
  - profit-take at +5 %
  - _resume_if_position_open: open-order path and position path

NOTE: the production code does lazy `from X import Y` inside each function, so
external packages (py_clob_client_v2, pydantic, …) are never needed at import
time.  We inject lightweight `MagicMock` stubs into `sys.modules` before any
auto_trader code is imported so that those lazy imports resolve to our mocks.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy external dependencies before anything imports them
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> MagicMock:
    """Create a MagicMock module and register it (and parent packages) in sys.modules."""
    mod = MagicMock(name=name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    # ensure parent packages exist too
    parts = name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
    return mod


# These modules try to import heavy packages at module level; stub them all.
for _m in [
    "py_clob_client_v2",
    "pydantic",
    "openai",
    "agents.openai_agent",
]:
    if _m not in sys.modules:
        _stub_module(_m)

# Stub clients.polymarket_trading so auto_trader can import it lazily
_pm_trading = _stub_module("clients.polymarket_trading")
# Stub csv_signals
_csv_signals = _stub_module("csv_signals")
# Stub config
_stub_module("config")

# Now it's safe to import auto_trader
from engine.auto_trader import (  # noqa: E402
    _AssetState,
    _monitor_position,
    _resume_if_position_open,
    _scan_and_trade,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(asset: str = "BTC", state: str = "MONITORING") -> _AssetState:
    st = _AssetState(asset=asset)
    st.state = state
    st.active_token_id = "tok_yes_abc"
    st.active_order_id = "order_123"
    st.active_outcome = "YES"
    return st


def _pos(avg: float, cur: float, size: float = 10.0, token: str = "tok_yes_abc") -> dict:
    return {"asset": token, "avgPrice": avg, "curPrice": cur, "size": size}


# ---------------------------------------------------------------------------
# _resolves_today — date-filter helper inside _scan_and_trade
# ---------------------------------------------------------------------------

class TestResolvesToday:
    def _today_iso(self) -> str:
        return datetime.now(timezone.utc).replace(
            hour=16, minute=0, second=0, microsecond=0
        ).isoformat()

    def _tomorrow_iso(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=16, minute=0, second=0, microsecond=0
        ).isoformat()

    @pytest.mark.asyncio
    async def test_skips_when_only_tomorrow_signal(self):
        """No order placed when the only alpha signal resolves tomorrow."""
        st = _AssetState(asset="BTC")
        tomorrow_signal = {
            "has_alpha": True,
            "currency": "BTC",
            "abs_edge_pct": 0.1,
            "edge_pct": 0.1,
            "polymarket_price": 0.6,
            "polymarket_market_id": "mkt_123",
            "polymarket_question": "BTC > 100k tomorrow?",
            "market_resolution_at": self._tomorrow_iso(),
        }
        _csv_signals.get_latest_signals = MagicMock(return_value=[tomorrow_signal])

        result = await _scan_and_trade(st)

        assert result is False
        assert st.state == "SCANNING"

    @pytest.mark.asyncio
    async def test_proceeds_when_today_signal(self):
        """Order placement is attempted when signal resolves today."""
        st = _AssetState(asset="BTC")
        today_signal = {
            "has_alpha": True,
            "currency": "BTC",
            "abs_edge_pct": 0.1,
            "edge_pct": 0.1,
            "polymarket_price": 0.6,
            "deribit_prob": 0.65,
            "polymarket_market_id": "mkt_123",
            "polymarket_question": "BTC > 100k today?",
            "market_resolution_at": self._today_iso(),
        }
        _csv_signals.get_latest_signals = MagicMock(return_value=[today_signal])
        _pm_trading.create_order = MagicMock(return_value={"success": True, "orderID": "order_new_001"})

        with patch("engine.auto_trader._resolve_token_ids", return_value=["tok_yes", "tok_no"]):
            result = await _scan_and_trade(st)

        assert result is True
        assert st.state == "MONITORING"
        assert st.active_order_id == "order_new_001"
        assert st.active_outcome == "YES"  # deribit_prob 0.65 → YES


# ---------------------------------------------------------------------------
# deribit_prob conviction filter
# ---------------------------------------------------------------------------

class TestDeribitProbFilter:
    def _today_iso(self) -> str:
        return datetime.now(timezone.utc).replace(
            hour=16, minute=0, second=0, microsecond=0
        ).isoformat()

    def _signal(self, deribit_prob: float, poly_price: float = 0.6) -> dict:
        return {
            "has_alpha": True,
            "currency": "BTC",
            "abs_edge_pct": 0.1,
            "edge_pct": 0.1,
            "polymarket_price": poly_price,
            "deribit_prob": deribit_prob,
            "polymarket_market_id": "mkt_prob",
            "polymarket_question": "BTC above 100k?",
            "market_resolution_at": self._today_iso(),
        }

    @pytest.mark.asyncio
    async def test_yes_when_deribit_prob_above_51(self):
        """deribit_prob > 0.51 → buys YES token."""
        st = _AssetState(asset="BTC")
        _csv_signals.get_latest_signals = MagicMock(return_value=[self._signal(0.65)])
        _pm_trading.create_order = MagicMock(return_value={"success": True, "orderID": "o1"})

        with patch("engine.auto_trader._resolve_token_ids", return_value=["tok_yes", "tok_no"]):
            result = await _scan_and_trade(st)

        assert result is True
        assert st.active_outcome == "YES"
        assert st.active_token_id == "tok_yes"

    @pytest.mark.asyncio
    async def test_no_when_deribit_prob_below_49(self):
        """deribit_prob < 0.49 → buys NO token."""
        st = _AssetState(asset="BTC")
        _csv_signals.get_latest_signals = MagicMock(return_value=[self._signal(0.30, poly_price=0.65)])
        _pm_trading.create_order = MagicMock(return_value={"success": True, "orderID": "o2"})

        with patch("engine.auto_trader._resolve_token_ids", return_value=["tok_yes", "tok_no"]):
            result = await _scan_and_trade(st)

        assert result is True
        assert st.active_outcome == "NO"
        assert st.active_token_id == "tok_no"

    @pytest.mark.asyncio
    async def test_skips_when_deribit_prob_neutral_band(self):
        """deribit_prob in 0.49–0.51 → no order placed."""
        for prob in [0.49, 0.50, 0.51]:
            st = _AssetState(asset="BTC")
            _csv_signals.get_latest_signals = MagicMock(return_value=[self._signal(prob)])

            with patch("engine.auto_trader._resolve_token_ids", return_value=["tok_yes", "tok_no"]):
                result = await _scan_and_trade(st)

            assert result is False, f"Expected skip for deribit_prob={prob}"
            assert st.state == "SCANNING"


# ---------------------------------------------------------------------------
# _monitor_position — cancel-failure guard
# ---------------------------------------------------------------------------

class TestCancelFailureGuard:
    @pytest.mark.asyncio
    async def test_no_new_order_when_cancel_fails(self):
        """If cancel throws, we must bail out — no new order placed."""
        st = _make_state()
        _pm_trading.fetch_positions = MagicMock(return_value=[])
        _pm_trading.cancel_order = MagicMock(side_effect=RuntimeError("network"))

        with patch("engine.auto_trader._scan_and_trade", new_callable=AsyncMock) as mock_scan:
            await _monitor_position(st)

        mock_scan.assert_not_called()
        # order_id must still be set — we haven't reset state
        assert st.active_order_id == "order_123"

    @pytest.mark.asyncio
    async def test_rescan_when_cancel_succeeds(self):
        """If cancel succeeds and no position found, a re-scan is triggered."""
        st = _make_state()
        _pm_trading.fetch_positions = MagicMock(return_value=[])
        _pm_trading.cancel_order = MagicMock(return_value={"cancelled": True})

        with patch("engine.auto_trader._scan_and_trade", new_callable=AsyncMock) as mock_scan:
            await _monitor_position(st)

        mock_scan.assert_called_once()


# ---------------------------------------------------------------------------
# _monitor_position — stop-loss
# ---------------------------------------------------------------------------

class TestStopLoss:
    @pytest.mark.asyncio
    async def test_stop_loss_triggered_at_50pct(self):
        """Stop-loss fires when P&L <= -50 %."""
        st = _make_state()
        # avg=0.80, cur=0.40 → pnl = -50 %
        _pm_trading.fetch_positions = MagicMock(return_value=[_pos(avg=0.80, cur=0.40)])
        _pm_trading.create_order = MagicMock(return_value={"success": True, "orderID": "sl_001"})

        await _monitor_position(st)

        _pm_trading.create_order.assert_called_once()
        _, _, _, side = _pm_trading.create_order.call_args[0]
        assert side == "SELL"
        assert st.state == "SCANNING"

    @pytest.mark.asyncio
    async def test_no_close_at_25pct_loss(self):
        """No action at -25 % — above stop-loss, below profit target."""
        st = _make_state()
        # avg=0.80, cur=0.60 → pnl = -25 %
        _pm_trading.fetch_positions = MagicMock(return_value=[_pos(avg=0.80, cur=0.60)])
        _pm_trading.create_order = MagicMock()

        await _monitor_position(st)

        _pm_trading.create_order.assert_not_called()
        assert st.state == "MONITORING"


# ---------------------------------------------------------------------------
# _monitor_position — profit take
# ---------------------------------------------------------------------------

class TestProfitTake:
    @pytest.mark.asyncio
    async def test_sell_at_5pct_profit(self):
        """Close order placed when P&L >= +5 %."""
        st = _make_state()
        # avg=0.60, cur=0.63 → pnl = +5 %
        _pm_trading.fetch_positions = MagicMock(return_value=[_pos(avg=0.60, cur=0.63)])
        _pm_trading.create_order = MagicMock(return_value={"success": True, "orderID": "profit_001"})

        await _monitor_position(st)

        _pm_trading.create_order.assert_called_once()
        assert st.state == "SCANNING"

    @pytest.mark.asyncio
    async def test_no_sell_at_4pct_profit(self):
        """No close at +4 % — below the 5 % target."""
        st = _make_state()
        # avg=0.60, cur=0.624 → pnl = +4 %
        _pm_trading.fetch_positions = MagicMock(return_value=[_pos(avg=0.60, cur=0.624)])
        _pm_trading.create_order = MagicMock()

        await _monitor_position(st)

        _pm_trading.create_order.assert_not_called()
        assert st.state == "MONITORING"


# ---------------------------------------------------------------------------
# _resume_if_position_open
# ---------------------------------------------------------------------------

class TestResumeIfPositionOpen:
    @pytest.mark.asyncio
    async def test_resumes_from_open_order(self):
        """An open BUY order for BTC restores MONITORING + order_id."""
        st = _AssetState(asset="BTC")
        _pm_trading.fetch_open_orders = MagicMock(return_value=[
            {"id": "order_existing_001", "asset_id": "tok_btc_yes", "side": "BUY"},
        ])
        _pm_trading.fetch_positions = MagicMock(return_value=[])

        with patch("engine.auto_trader._question_for_token",
                   return_value="Will BTC exceed $100k on May 31?"):
            await _resume_if_position_open(st)

        assert st.state == "MONITORING"
        assert st.active_token_id == "tok_btc_yes"
        assert st.active_order_id == "order_existing_001"

    @pytest.mark.asyncio
    async def test_resumes_from_filled_position(self):
        """A filled position for BTC restores MONITORING, no order_id."""
        st = _AssetState(asset="BTC")
        _pm_trading.fetch_open_orders = MagicMock(return_value=[])
        _pm_trading.fetch_positions = MagicMock(return_value=[
            {"asset": "tok_btc_yes", "avgPrice": 0.65, "curPrice": 0.66, "size": 8}
        ])

        with patch("engine.auto_trader._question_for_token",
                   return_value="BTC above 90k?"):
            await _resume_if_position_open(st)

        assert st.state == "MONITORING"
        assert st.active_token_id == "tok_btc_yes"
        assert st.active_order_id is None

    @pytest.mark.asyncio
    async def test_skips_eth_position_for_btc_loop(self):
        """BTC loop must not resume on an ETH position."""
        st = _AssetState(asset="BTC")
        _pm_trading.fetch_open_orders = MagicMock(return_value=[])
        _pm_trading.fetch_positions = MagicMock(return_value=[
            {"asset": "tok_eth_yes", "avgPrice": 0.55, "curPrice": 0.56, "size": 9}
        ])

        with patch("engine.auto_trader._question_for_token",
                   return_value="Will ETH exceed $5k?"):
            await _resume_if_position_open(st)

        assert st.state == "SCANNING"
        assert st.active_token_id is None

    @pytest.mark.asyncio
    async def test_stays_scanning_when_nothing_open(self):
        """No positions, no orders → stay in SCANNING."""
        st = _AssetState(asset="BTC")
        _pm_trading.fetch_open_orders = MagicMock(return_value=[])
        _pm_trading.fetch_positions = MagicMock(return_value=[])

        await _resume_if_position_open(st)

        assert st.state == "SCANNING"
