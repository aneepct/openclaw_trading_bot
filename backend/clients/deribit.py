import httpx
import asyncio
from typing import Optional

import config as app_config

BASE_URL = "https://www.deribit.com/api/v2/public"
PRIVATE_BASE_URL = "https://www.deribit.com/api/v2"

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


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """Authenticate with Deribit using client credentials and return a bearer token."""
    if not app_config.DERIBIT_API_KEY or not app_config.DERIBIT_API_SECRET:
        raise RuntimeError("DERIBIT_API_KEY or DERIBIT_API_SECRET is not configured.")

    resp = await client.get(
        f"{PRIVATE_BASE_URL}/public/auth",
        params={
            "grant_type": "client_credentials",
            "client_id": app_config.DERIBIT_API_KEY,
            "client_secret": app_config.DERIBIT_API_SECRET,
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("result") or {}
    token = result.get("access_token")
    if not token:
        raise RuntimeError("Deribit auth succeeded without access_token.")
    return token


async def get_positions(currency: str = "any", kind: str = "future") -> list[dict]:
    """
    Return open Deribit positions.
    Defaults to currency=any and kind=future.
    """
    cc = (currency or "any").strip().lower()
    if cc not in {"btc", "eth", "any"}:
        raise ValueError("currency must be btc, eth, or any")

    kk = (kind or "future").strip().lower()
    if kk not in {"future", "option"}:
        raise ValueError("kind must be future or option")

    async with httpx.AsyncClient(timeout=15) as client:
        token = await _get_access_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        params = {"currency": cc, "kind": kk}
        if app_config.DERIBIT_SUBACCOUNT_ID:
            params["subaccount_id"] = app_config.DERIBIT_SUBACCOUNT_ID

        resp = await client.get(
            f"{PRIVATE_BASE_URL}/private/get_positions",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("result")
        return result if isinstance(result, list) else []


async def get_all_positions(kind: str = "future") -> list[dict]:
    """Return Deribit positions using currency=any."""
    return await get_positions("any", kind)


async def get_account_summary(currency: str = "BTC", extended: bool = True) -> dict:
    """
    Return Deribit account summary for the configured (sub)account.
    Calls private/get_account_summary with optional extended greeks.
    """
    cc = (currency or "BTC").strip().upper()
    if cc not in {"BTC", "ETH", "USDC", "USDT", "SOL"}:
        raise ValueError("currency must be BTC, ETH, USDC, USDT, or SOL")

    async with httpx.AsyncClient(timeout=15) as client:
        token = await _get_access_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        params: dict = {"currency": cc, "extended": str(extended).lower()}
        if app_config.DERIBIT_SUBACCOUNT_ID:
            params["subaccount_id"] = app_config.DERIBIT_SUBACCOUNT_ID

        resp = await client.get(
            f"{PRIVATE_BASE_URL}/private/get_account_summary",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Unexpected response from get_account_summary")
        return result
