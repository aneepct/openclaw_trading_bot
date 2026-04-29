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

mcp = FastMCP("OpenClaw Trading Bot")


def _get(path: str, timeout: int = 90) -> str:
    """Make a GET request to the OpenClaw API and return text content."""
    url = f"{OPENCLAW_API_URL}{path}"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
    r.raise_for_status()
    return r.text


def _get_json(path: str, timeout: int = 90) -> dict:
    """Make a GET request and return parsed JSON."""
    return json.loads(_get(path, timeout))


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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
