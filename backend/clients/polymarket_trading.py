"""
Polymarket trading client — wraps py_clob_client_v2 for use by the FastAPI routes.
Credentials are read from config (env vars POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER).
"""
import httpx
from functools import lru_cache

from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType
from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
from py_clob_client_v2.order_utils.model.side import Side

import config as app_config


# ---------------------------------------------------------------------------
# Client factory (cached per-process so we don't re-derive the API key on
# every request — key derivation is deterministic so this is always safe)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_client() -> ClobClient:
    key    = app_config.POLYMARKET_PRIVATE_KEY
    funder = app_config.POLYMARKET_FUNDER or None
    if not key:
        raise RuntimeError(
            "POLYMARKET_PRIVATE_KEY env var is not set. "
            "Add it to your .env or docker-compose environment."
        )
    client = ClobClient(
        app_config.POLYMARKET_CLOB_API,
        key=key,
        chain_id=137,
        signature_type=3,
        funder=funder,
    )
    creds = client.derive_api_key()
    client.set_api_creds(creds)
    return client


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def fetch_positions(open_only: bool = True) -> list[dict]:
    """Return positions for the configured funder address."""
    funder = app_config.POLYMARKET_FUNDER
    if not funder:
        raise RuntimeError("POLYMARKET_FUNDER env var is not set.")
    r = httpx.get(
        f"{app_config.POLYMARKET_DATA_API}/positions",
        params={"user": funder, "sizeThreshold": 0.01, "limit": 100},
        timeout=10,
    )
    r.raise_for_status()
    positions = r.json()
    if open_only:
        positions = [p for p in positions if not p.get("redeemable")]
    return positions


# ---------------------------------------------------------------------------
# Open orders
# ---------------------------------------------------------------------------

def fetch_open_orders() -> list[dict]:
    """Return all open (resting) orders for the authenticated account."""
    client = _get_client()
    headers = client._l2_headers("GET", "/data/orders")
    r = httpx.get(
        f"{app_config.POLYMARKET_CLOB_API}/data/orders",
        headers=headers,
        params={"limit": 100},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("data", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# Create order
# ---------------------------------------------------------------------------

def create_order(token_id: str, price: float, size: float, side: str) -> dict:
    """
    Place a GTC limit order.

    Args:
        token_id: CLOB token ID for the outcome (YES or NO token).
        price:    Limit price in USDC (0 < price < 1).
        size:     Number of shares.
        side:     "BUY" or "SELL" (case-insensitive).
    Returns:
        The CLOB API response dict.
    """
    if not (0 < price < 1):
        raise ValueError("price must be between 0 and 1 (exclusive)")
    if size <= 0:
        raise ValueError("size must be positive")

    side_enum = Side.BUY if side.upper() == "BUY" else Side.SELL
    client    = _get_client()
    order     = OrderArgsV2(token_id=token_id, price=price, size=size, side=side_enum)
    signed    = client.create_order(order)
    resp      = client.post_order(signed, OrderType.GTC)
    return resp if isinstance(resp, dict) else vars(resp)


# ---------------------------------------------------------------------------
# Close position (sell all shares)
# ---------------------------------------------------------------------------

def close_position(token_id: str, size: float, price: float) -> dict:
    """
    Sell `size` shares of `token_id` at the given limit price.

    Args:
        token_id: CLOB token ID of the outcome to sell.
        size:     Number of shares to sell.
        price:    Sell limit price in USDC.
    Returns:
        The CLOB API response dict.
    """
    return create_order(token_id=token_id, price=price, size=size, side="SELL")


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------

def get_balance() -> float:
    """Return USDC collateral balance (human-readable, e.g. 12.50)."""
    client = _get_client()
    b = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(b["balance"]) / 1e6
