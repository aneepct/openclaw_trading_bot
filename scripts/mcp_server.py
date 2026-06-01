"""
OpenClaw MCP Server
-------------------
Exposes OpenClaw data as MCP tools so Claude Desktop can call them
directly without any network allowlist issues.

The MCP server runs locally on your machine and proxies requests to
the OpenClaw API, so Claude never needs to reach your domain directly.

Setup (one-time):
    pip install mcp httpx python-dotenv

Add to ~/Library/Application Support/Claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "openclaw": {
          "command": "python",
          "args": ["/full/path/to/scripts/mcp_server.py"],
          "env": {
            "OPENCLAW_API_URL": "https://openclaw-api.aneep.tech"
          }
        }
      }
    }

Then restart Claude Desktop.  Claude will now have access to all tools below.
"""
from __future__ import annotations

import os
import sys
import json

try:
    import httpx
except ImportError:
    sys.exit("Missing dependency: pip install httpx")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit("Missing dependency: pip install mcp")

# Load .env if present (for local dev)
try:
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(_env)
except ImportError:
    pass

OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "http://localhost:8000").rstrip("/")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")

mcp = FastMCP("OpenClaw Trading Bot")


def _headers() -> dict:
    """Build request headers, including x-api-key if configured."""
    h = {}
    if POLYMARKET_API_KEY:
        h["x-api-key"] = POLYMARKET_API_KEY
    return h


def _get(path: str, timeout: int = 90) -> str:
    """Make a GET request to the OpenClaw API and return text content."""
    url = f"{OPENCLAW_API_URL}{path}"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url, headers=_headers())
    r.raise_for_status()
    return r.text


def _get_json(path: str, timeout: int = 90) -> dict:
    """Make a GET request and return parsed JSON."""
    return json.loads(_get(path, timeout))


def _post_json(path: str, body: dict, timeout: int = 90) -> dict:
    """Make a POST request with a JSON body and return parsed JSON."""
    url = f"{OPENCLAW_API_URL}{path}"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=body, headers=_headers())
    r.raise_for_status()
    return r.json()


def _delete_json(path: str, timeout: int = 90) -> dict:
    """Make a DELETE request to the OpenClaw API and return parsed JSON."""
    url = f"{OPENCLAW_API_URL}{path}"
    with httpx.Client(timeout=timeout) as client:
        r = client.delete(url, headers=_headers())
    r.raise_for_status()
    return r.json() if r.content else {}


# ---------------------------------------------------------------------------
# Deribit CSV tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_deribit_btc_today() -> str:
    """
    Fetch the latest Deribit BTC options order book CSV for today.
    Returns live data freshly pulled from Deribit.
    """
    return _get("/download/csv/deribit/btc/today")


@mcp.tool()
def get_deribit_btc_tomorrow() -> str:
    """
    Fetch the latest Deribit BTC options order book CSV for tomorrow's expiry.
    Returns live data freshly pulled from Deribit.
    """
    return _get("/download/csv/deribit/btc/tomorrow")


@mcp.tool()
def get_deribit_eth_today() -> str:
    """
    Fetch the latest Deribit ETH options order book CSV for today.
    Returns live data freshly pulled from Deribit.
    """
    return _get("/download/csv/deribit/eth/today")


@mcp.tool()
def get_deribit_eth_tomorrow() -> str:
    """
    Fetch the latest Deribit ETH options order book CSV for tomorrow's expiry.
    Returns live data freshly pulled from Deribit.
    """
    return _get("/download/csv/deribit/eth/tomorrow")


# ---------------------------------------------------------------------------
# Polymarket CSV tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_polymarket_btc() -> str:
    """
    Fetch the latest Polymarket BTC prediction markets CSV for today.
    Contains market questions, YES prices, liquidity, and end dates.
    """
    return _get("/download/csv/polymarket/btc")


@mcp.tool()
def get_polymarket_eth() -> str:
    """
    Fetch the latest Polymarket ETH prediction markets CSV for today.
    Contains market questions, YES prices, liquidity, and end dates.
    """
    return _get("/download/csv/polymarket/eth")


# ---------------------------------------------------------------------------
# Live matrix & leaderboard
# ---------------------------------------------------------------------------

# @mcp.tool()
# def get_alpha_matrix() -> str:
#     """
#     Get the current live alpha signals matrix.
#     Returns signals where |Deribit implied probability - Polymarket price| >= 3%.
#     Each signal includes: market question, direction (BUY/SELL), edge %, Deribit prob,
#     Polymarket price, spot price, strike, and AI reasoning.
#     """
#     data = _get_json("/matrix")
#     signals = data.get("signals", [])
#     if not signals:
#         return "No alpha signals found in the current scan."
#
#     lines = [f"Alpha Matrix — {len(signals)} signal(s)\n"]
#     for s in signals:
#         lines.append(f"Market   : {s.get('polymarket_question')}")
#         lines.append(f"Direction: {s.get('recommended_action') or s.get('direction')}")
#         lines.append(f"Edge     : {s.get('edge_pct')}%")
#         lines.append(f"Deribit  : {round(float(s.get('deribit_prob', 0)) * 100, 1)}%")
#         lines.append(f"Poly     : {round(float(s.get('polymarket_price', 0)) * 100, 1)}%")
#         lines.append(f"Spot     : ${s.get('spot_price')}  Strike: ${s.get('strike')}")
#         lines.append(f"Reasoning: {(s.get('agent_analysis') or {}).get('rationale') or s.get('reasoning') or '—'}")
#         lines.append("")
#     return "\n".join(lines)


@mcp.tool()
def get_leaderboard() -> str:
    """
    Get the top alpha signals leaderboard ranked by edge magnitude.
    """
    data = _get_json("/leaderboard")
    entries = data.get("entries", [])
    if not entries:
        return "No leaderboard entries."

    lines = [f"Leaderboard — top {len(entries)} signal(s)\n"]
    for e in entries:
        lines.append(f"#{e.get('rank')} {e.get('polymarket_question')}")
        lines.append(f"   Edge: {e.get('abs_edge_pct')}%  Direction: {e.get('direction')}")
        lines.append(f"   Deribit: {round(float(e.get('deribit_prob', 0)) * 100, 1)}%  Poly: {round(float(e.get('polymarket_price', 0)) * 100, 1)}%")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_health() -> str:
    """Check if the OpenClaw API is running and how many signals are loaded."""
    data = _get_json("/health")
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Polymarket trading tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_polymarket_market_info(slug: str) -> str:
    """
    Get Polymarket market info and token IDs for a given market slug.
    Call this BEFORE placing an order to get the correct token_id.

    Args:
        slug: The market URL slug, e.g.
              "will-the-price-of-bitcoin-be-between-76000-78000-on-may-17"

    Returns:
        JSON with token_id, question, outcome (Yes/No), tick_size, min_size
        for each outcome in the market.
    """
    data = _get_json(f"/polymarket/market/{slug}")
    markets = data.get("markets", [])
    if not markets:
        return f"No market found for slug: {slug}"
    lines = [f"Market slug: {slug}", f"Outcomes ({len(markets)}):\n"]
    for m in markets:
        lines.append(f"  Outcome  : {m.get('outcome')}")
        lines.append(f"  token_id : {m.get('token_id')}")
        lines.append(f"  Question : {m.get('question')}")
        lines.append(f"  Tick size: {m.get('tick_size')}  Min size: {m.get('min_size')}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def create_polymarket_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    close_pct: float = None,
) -> str:
    """
    Place a GTC limit order on Polymarket.
    Get the token_id first using get_polymarket_market_info().

    Args:
        token_id:  CLOB token ID for the outcome (from get_polymarket_market_info).
        price:     Limit price between 0 and 1 (e.g. 0.65 = 65 cents).
        size:      Number of shares to buy/sell (minimum is usually 5).
        side:      "BUY" or "SELL".
        close_pct: Optional. If provided, also place a SELL limit order at
                   price * (1 + close_pct / 100).  E.g. close_pct=10 places
                   a take-profit sell 10% above the buy price.
                   Ignored when side="SELL".

    Returns:
        Order confirmation with order_id if successful. If close_pct is given,
        also returns the sell order ID.
    """
    resp = _post_json("/polymarket/orders", {
        "token_id": token_id,
        "price": price,
        "size": size,
        "side": side.upper(),
    })
    if not resp.get("ok"):
        return f"✗ Order failed: {json.dumps(resp, indent=2)}"

    lines = [
        f"✓ Order placed successfully!",
        f"Order ID: {resp.get('order_id')}",
        f"Full response: {json.dumps(resp.get('response', {}), indent=2)}",
    ]

    # Optionally place a take-profit sell order
    if close_pct is not None and side.upper() == "BUY":
        sell_price = round(price * (1 + close_pct / 100), 4)
        # Clamp to valid range
        sell_price = min(max(sell_price, 0.0001), 0.9999)
        sell_resp = _post_json("/polymarket/orders", {
            "token_id": token_id,
            "price": sell_price,
            "size": size,
            "side": "SELL",
        })
        if sell_resp.get("ok"):
            lines.append(f"\n✓ Take-profit SELL order placed at {sell_price} ({close_pct}% above buy price)")
            lines.append(f"Sell Order ID: {sell_resp.get('order_id')}")
        else:
            lines.append(f"\n✗ Take-profit SELL order failed: {json.dumps(sell_resp, indent=2)}")

    return "\n".join(lines)


@mcp.tool()
def close_polymarket_position(token_id: str, size: float, price: float) -> str:
    """
    Close (sell) an existing Polymarket position.
    Places a GTC SELL limit order for the specified size at the given price.
    Use a price slightly below the current market price for a quick fill.

    Args:
        token_id: CLOB token ID of the position to close.
        size:     Number of shares to sell.
        price:    Sell limit price between 0 and 1.

    Returns:
        Order confirmation with order_id if successful.
    """
    resp = _post_json("/polymarket/orders/close", {
        "token_id": token_id,
        "size": size,
        "price": price,
    })
    if resp.get("ok"):
        return f"✓ Close order placed successfully!\nOrder ID: {resp.get('order_id')}\nFull response: {json.dumps(resp.get('response', {}), indent=2)}"
    return f"✗ Close order failed: {json.dumps(resp, indent=2)}"


@mcp.tool()
def get_polymarket_positions() -> str:
    """
    Get all open Polymarket positions for the configured account.
    Returns market title, size, average price, current price, and P&L.
    """
    data = _get_json("/polymarket/positions")
    positions = data.get("positions", [])
    balance = data.get("balance_usdc", 0)

    if not positions:
        return f"No open positions.\nBalance: ${balance:.4f} USDC"

    lines = [f"Balance: ${balance:.4f} USDC", f"Open positions ({len(positions)}):\n"]
    for i, p in enumerate(positions):
        pnl = float(p.get("cashPnl", 0))
        lines.append(f"  [{i}] {p.get('title', '?')}")
        lines.append(f"       token_id : {p.get('asset', '?')}")
        lines.append(f"       Size: {p.get('size')}  Avg: ${float(p.get('avgPrice', 0)):.3f}  Cur: ${float(p.get('curPrice', 0)):.3f}  P&L: ${pnl:+.3f}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def update_closing_order(
    order_id: str,
    token_id: str,
    size: float,
    new_close_pct: float,
    buy_price: float,
) -> str:
    """
    Cancel an existing sell limit order and replace it with a new one at an updated price.

    Use this to adjust a take-profit sell order placed alongside a buy.

    Args:
        order_id:      The sell order ID to cancel (from the original order confirmation).
        token_id:      CLOB token ID of the outcome being sold.
        size:          Number of shares for the new sell order.
        new_close_pct: New take-profit percentage above buy_price.
                       E.g. 15 means sell at buy_price * 1.15.
        buy_price:     The original buy price used as the base for percentage calculation.

    Returns:
        Confirmation with the cancelled order ID and new sell order ID.
    """
    new_price = round(buy_price * (1 + new_close_pct / 100), 4)
    new_price = min(max(new_price, 0.0001), 0.9999)

    resp = _post_json("/polymarket/orders/update-close", {
        "order_id": order_id,
        "token_id": token_id,
        "size": size,
        "new_price": new_price,
    })
    if resp.get("ok"):
        return (
            f"✓ Closing order updated!\n"
            f"Cancelled Order ID : {resp.get('cancelled_order_id')}\n"
            f"New Sell Order ID  : {resp.get('new_order_id')}\n"
            f"New sell price     : {new_price} ({new_close_pct}% above {buy_price})\n"
            f"Full response: {json.dumps(resp.get('response', {}), indent=2)}"
        )
    return f"✗ Update failed: {json.dumps(resp, indent=2)}"


@mcp.tool()
def cancel_polymarket_order(order_id: str) -> str:
    """
    Cancel an open Polymarket order by its order ID.

    Args:
        order_id: The order ID to cancel (from a previous order confirmation).

    Returns:
        Confirmation that the order was cancelled, or an error message.
    """
    resp = _delete_json(f"/polymarket/orders/{order_id}")
    if resp.get("ok") or resp.get("cancelled"):
        return (
            f"✓ Order cancelled successfully!\n"
            f"Order ID: {order_id}\n"
            f"Full response: {json.dumps(resp, indent=2)}"
        )
    return f"✗ Cancel failed: {json.dumps(resp, indent=2)}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
