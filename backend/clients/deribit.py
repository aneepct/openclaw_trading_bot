import asyncio
import itertools
import json
from typing import Any, Optional

import httpx
import websockets

BASE_URL = "https://www.deribit.com/api/v2/public"
DERIBIT_WSS_URL = "wss://www.deribit.com/ws/api/v2"

# Cap concurrent order-book fetches to avoid hitting Deribit's rate limit (HTTP fallback)
_DERIBIT_SEMAPHORE = asyncio.Semaphore(3)
_req_id = itertools.count(1)


class DeribitWSClient:
    """
    Single WebSocket connection to Deribit public API.
    All JSON-RPC calls share one connection, eliminating per-request HTTP
    overhead and 429 rate-limit errors.
    """

    # Deribit allows ~20 non-auth WS requests/sec; cap in-flight to stay safe.
    _CONCURRENCY = 8

    def __init__(self) -> None:
        self._ws: Any = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(self._CONCURRENCY)

    async def __aenter__(self) -> "DeribitWSClient":
        self._ws = await websockets.connect(DERIBIT_WSS_URL, ping_interval=20)
        self._reader_task = asyncio.create_task(self._reader())
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def _reader(self) -> None:
        """Read incoming messages and resolve the matching pending future."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
        except Exception as exc:
            # Connection dropped — reject every pending call
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()

    async def call(self, method: str, params: dict[str, Any], *, _retries: int = 5) -> Any:
        """Send a public JSON-RPC request and return its result.

        Automatically retries on Deribit error 10028 (too_many_requests) with
        exponential backoff so callers never need to handle rate-limit errors.
        """
        backoff = 2.0
        for attempt in range(_retries + 1):
            async with self._sem:
                req_id = next(_req_id)
                payload = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
                loop = asyncio.get_running_loop()
                fut: asyncio.Future = loop.create_future()
                self._pending[req_id] = fut
                await self._ws.send(json.dumps(payload))
            try:
                return await fut
            except RuntimeError as exc:
                raw = str(exc)
                # Deribit returns {"code": 10028, "message": "too_many_requests"}
                if "10028" in raw and attempt < _retries:
                    print(f"[DeribitWS] rate limited on {method}, retrying in {backoff:.1f}s (attempt {attempt + 1}/{_retries})")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                raise



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
