"""
Polymarket trading client — wraps py_clob_client_v2 for use by the FastAPI routes.
Credentials are read from config (env vars POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER).
"""
import json
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
# Cancel order
# ---------------------------------------------------------------------------

def cancel_order(order_id: str) -> dict:
    """
    Cancel a resting limit order by its order ID.

    Uses the same L2 authentication as fetch_open_orders.

    Args:
        order_id: The order ID returned when the order was placed.
    Returns:
        The CLOB API response dict.
    """
    client = _get_client()
    body = {"orderID": order_id}
    headers = client._l2_headers("DELETE", "/order", body=body)
    headers["Content-Type"] = "application/json"
    r = httpx.request(
        "DELETE",
        f"{app_config.POLYMARKET_CLOB_API}/order",
        headers=headers,
        content=json.dumps(body),
        timeout=10,
    )
    r.raise_for_status()
    return r.json() if r.content else {"cancelled": True}


# ---------------------------------------------------------------------------
# Resolve market slug → token IDs + metadata
# ---------------------------------------------------------------------------

def resolve_market_slug(slug: str) -> list[dict]:
    """
    Fetch market info for a given Polymarket slug from the Gamma API.

    Tries the /markets/slug/{slug} endpoint first; falls back to
    /events/slug/{slug} and extracts the nested markets array.

    Args:
        slug: URL slug, e.g. "will-the-price-of-bitcoin-be-less-than-72000-on-may-17"

    Returns:
        List of dicts, one per outcome, each with:
            - token_id:  CLOB token ID (use this in create_order / close_position)
            - question:  market question string
            - outcome:   outcome label (e.g. "Yes" / "No")
            - tick_size: minimum price increment
            - min_size:  minimum order size
            - condition_id: on-chain condition ID
    """
    gamma = app_config.POLYMARKET_BASE_URL  # https://gamma-api.polymarket.com

    # ── Try /markets/slug/{slug} first ────────────────────────────────────────
    r = httpx.get(f"{gamma}/markets/slug/{slug}", timeout=10)
    if r.status_code == 200:
        raw = r.json()
        # Endpoint may return a single dict or a list
        markets_raw = raw if isinstance(raw, list) else [raw]
        result = []
        for m in markets_raw:
            ids = m.get("clobTokenIds") or []
            if isinstance(ids, str):
                ids = json.loads(ids)
            outcomes = m.get("outcomes") or []
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            for i, token_id in enumerate(ids):
                result.append({
                    "token_id":     str(token_id),
                    "question":     m.get("question", slug),
                    "outcome":      outcomes[i] if i < len(outcomes) else f"outcome_{i}",
                    "tick_size":    float(m.get("minTickSize") or 0.01),
                    "min_size":     float(m.get("orderMinSize") or 5),
                    "condition_id": m.get("conditionId", ""),
                })
        if result:
            return result

    # ── Fallback: /events/slug/{slug} ─────────────────────────────────────────
    r2 = httpx.get(f"{gamma}/events/slug/{slug}", timeout=10)
    if r2.status_code != 200:
        raise ValueError(
            f"Market slug '{slug}' not found "
            f"(markets→{r.status_code}, events→{r2.status_code})."
        )
    event = r2.json()
    markets_raw = event.get("markets", [])
    result = []
    for m in markets_raw:
        ids = m.get("clobTokenIds") or []
        if isinstance(ids, str):
            ids = json.loads(ids)
        outcomes = m.get("outcomes") or []
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        for i, token_id in enumerate(ids):
            result.append({
                "token_id":     str(token_id),
                "question":     m.get("question", slug),
                "outcome":      outcomes[i] if i < len(outcomes) else f"outcome_{i}",
                "tick_size":    float(m.get("minTickSize") or 0.01),
                "min_size":     float(m.get("orderMinSize") or 5),
                "condition_id": m.get("conditionId", ""),
            })
    if not result:
        raise ValueError(f"No markets found for slug '{slug}'.")
    return result


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------

def get_balance() -> float:
    """Return USDC collateral balance (human-readable, e.g. 12.50)."""
    client = _get_client()
    b = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(b["balance"]) / 1e6
