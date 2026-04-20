import httpx
import asyncio
from typing import Optional

BASE_URL = "https://www.deribit.com/api/v2/public"

# Cap concurrent order-book fetches to avoid hitting Deribit's rate limit
_DERIBIT_SEMAPHORE = asyncio.Semaphore(3)


async def get_instruments(currency: str = "BTC", kind: str = "option") -> list[dict]:
    """Fetch all active options for a given currency."""
    url = f"{BASE_URL}/get_instruments"
    params = {"currency": currency, "kind": kind, "expired": False}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])


async def get_order_book(instrument_name: str, depth: int = 1) -> Optional[dict]:
    """Fetch order book for a specific instrument, retrying on 429."""
    url = f"{BASE_URL}/get_order_book"
    params = {"instrument_name": instrument_name, "depth": depth}
    backoff = 2.0
    for attempt in range(4):
        async with _DERIBIT_SEMAPHORE:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("result")
    return None


async def get_index_price(index_name: str = "btc_usd") -> Optional[float]:
    """Fetch current index price (spot price)."""
    url = f"{BASE_URL}/get_index_price"
    params = {"index_name": index_name}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        return result.get("index_price")


async def get_options_for_expiry(currency: str, expiry_date: str) -> list[dict]:
    """
    Fetch all options for a specific expiry date.
    expiry_date format: e.g. '28MAR26'
    Returns list of order books for each instrument.
    """
    instruments = await get_instruments(currency=currency)
    target = []
    for inst in instruments:
        name = inst.get("instrument_name", "")
        # e.g. BTC-28MAR26-62000-C
        parts = name.split("-")
        if len(parts) == 4 and parts[1] == expiry_date:
            target.append(name)

    results = []
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [_fetch_book(client, name) for name in target]
        books = await asyncio.gather(*tasks, return_exceptions=True)
        for book in books:
            if isinstance(book, dict):
                results.append(book)
    return results


async def _fetch_book(client: httpx.AsyncClient, instrument_name: str) -> Optional[dict]:
    url = f"{BASE_URL}/get_order_book"
    params = {"instrument_name": instrument_name, "depth": 1}
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result")
