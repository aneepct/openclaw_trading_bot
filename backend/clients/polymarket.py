import httpx
from typing import Optional

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"


async def get_markets(limit: int = 100, offset: int = 0, active: bool = True) -> list[dict]:
    """Fetch active markets from Polymarket Gamma API."""
    url = f"{GAMMA_BASE}/markets"
    params = {
        "limit": limit,
        "offset": offset,
        "active": str(active).lower(),
        "closed": "false",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_market_by_id(market_id: str) -> Optional[dict]:
    """Fetch a single market by condition ID."""
    url = f"{GAMMA_BASE}/markets/{market_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def get_market_price(condition_id: str) -> Optional[dict]:
    """
    Fetch current best bid/ask prices for a market from CLOB.
    Returns dict with 'best_bid', 'best_ask', 'last_trade_price'.
    """
    url = f"{CLOB_BASE}/midpoint"
    params = {"token_id": condition_id}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            return resp.json()
    return None


async def search_crypto_markets(keyword: str = "bitcoin") -> list[dict]:
    """
    Search Polymarket for crypto-related binary markets.
    Filters by keyword in question text.
    """
    markets = await get_markets(limit=200)
    keyword_lower = keyword.lower()
    results = []
    for m in markets:
        question = m.get("question", "").lower()
        description = m.get("description", "").lower()
        if keyword_lower in question or keyword_lower in description:
            results.append(m)
    return results


async def get_btc_eth_markets() -> list[dict]:
    """Fetch all active BTC and ETH related markets on Polymarket."""
    btc_markets = await search_crypto_markets("bitcoin")
    eth_markets = await search_crypto_markets("ethereum")
    # deduplicate by market id
    seen = set()
    combined = []
    for m in btc_markets + eth_markets:
        mid = m.get("id") or m.get("condition_id")
        if mid and mid not in seen:
            seen.add(mid)
            combined.append(m)
    return combined
