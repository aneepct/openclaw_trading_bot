import httpx
import json
from datetime import date
from typing import Optional

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

CURRENCY_SLUGS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
}


def build_daily_event_slug(currency: str, target_date: date) -> str:
    """Build Polymarket event slug for daily price markets.
    e.g. BTC on April 7 -> 'bitcoin-price-on-april-7'
    """
    asset = CURRENCY_SLUGS.get(currency, currency.lower())
    month = target_date.strftime("%B").lower()  # 'april'
    day = str(target_date.day)                  # '7' (no leading zero)
    return f"{asset}-price-on-{month}-{day}"


async def get_daily_event_markets(currency: str, target_date: date) -> list[dict]:
    """Fetch all markets from the daily price event slug for a given currency and date."""
    slug = build_daily_event_slug(currency, target_date)
    url = f"{GAMMA_BASE}/events/slug/{slug}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        event = resp.json()
        markets = event.get("markets") or []
        for m in markets:
            m["_currency"] = currency
        return markets


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


def get_yes_clob_token_id(market: dict) -> Optional[str]:
    """Extract the Yes-outcome CLOB token ID from a Gamma market dict.
    clobTokenIds follows the same order as outcomes, so index 0 = Yes token.
    """
    raw = market.get("clobTokenIds")
    if not raw:
        return None
    try:
        ids = json.loads(raw) if isinstance(raw, str) else raw
        return str(ids[0]) if ids else None
    except Exception:
        return None


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
