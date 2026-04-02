from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

CURRENCY_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
}

PUT_KEYWORDS = ["dip", "fall", "drop", "below", "under", "crash", "decline", "sink"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def extract_end_date(market: dict[str, Any]) -> Optional[datetime]:
    end = market.get("endDate") or market.get("endDateIso")
    if not end:
        return None
    try:
        return datetime.strptime(str(end)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def poly_resolution_time(end_date: datetime) -> datetime:
    """Match scanner behavior: treat endDate as settlement at 16:00 UTC."""
    return end_date.replace(
        hour=16,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=timezone.utc,
    )


def detect_currency(question: str) -> Optional[str]:
    q = (question or "").lower()
    for code, keywords in CURRENCY_KEYWORDS.items():
        if any(k in q for k in keywords):
            return code
    return None


def extract_price_from_question(question: str) -> Optional[float]:
    q = (question or "").replace(",", "")
    m = re.search(r"\$([\d.]+)\s*[kK]\b", q, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"\$([\d]{4,})\b", q)
    if m:
        return float(m.group(1))
    m = re.search(r"\b([\d.]+)\s*[kK]\b", q, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"\b([\d]{5,})\b", q)
    if m:
        return float(m.group(1))
    return None


def detect_option_type(question: str) -> str:
    ql = (question or "").lower()
    if any(keyword in ql for keyword in PUT_KEYWORDS):
        return "P"
    return "C"


def _parse_maybe_json_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            loaded = json.loads(v)
            if isinstance(loaded, list):
                return loaded
        except json.JSONDecodeError:
            return []
    return []


def extract_outcome_prices0_raw(market: dict[str, Any]) -> Optional[float]:
    prices = _parse_maybe_json_list(market.get("outcomePrices"))
    if not prices:
        return None
    try:
        return float(prices[0])
    except (TypeError, ValueError, IndexError):
        return None


def extract_yes_price_from_market(market: dict[str, Any]) -> Optional[float]:
    """
    Mirror scanner behavior:
    - prefer outcomes/outcomePrices mapping to the 'yes' outcome
    - fallback to tokens[*] where token.outcome == 'yes'
    """
    outcomes = _parse_maybe_json_list(market.get("outcomes", "[]"))
    prices = _parse_maybe_json_list(market.get("outcomePrices", "[]"))

    if outcomes and prices and len(outcomes) == len(prices):
        for idx, outcome in enumerate(outcomes):
            if str(outcome).lower() == "yes":
                try:
                    return float(prices[idx])
                except (TypeError, ValueError):
                    return None

    for token in market.get("tokens", []) or []:
        if str((token or {}).get("outcome", "")).lower() == "yes":
            price = (token or {}).get("price")
            if price is not None:
                try:
                    return float(price)
                except (TypeError, ValueError):
                    return None
    return None


def extract_outcome_prices0_scaled(market: dict[str, Any]) -> Optional[float]:
    raw0 = extract_outcome_prices0_raw(market)
    return None if raw0 is None else raw0 * 100


def extract_liquidity(market: dict[str, Any]) -> float:
    for key in ["liquidity", "liquidityNum", "volume", "volumeNum"]:
        value = market.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})


async def get_markets(client: httpx.AsyncClient, *, limit: int, offset: int, active: bool) -> list[dict[str, Any]]:
    url = f"{GAMMA_BASE}/markets"
    params = {
        "limit": limit,
        "offset": offset,
        "active": str(active).lower(),
        "closed": "false",
    }
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Export Polymarket /markets to CSV for probability prediction.")
    parser.add_argument("--output-dir", type=str, default=str(Path(__file__).resolve().parent / "output"))
    parser.add_argument("--limit", type=int, default=500, help="Markets per page")
    parser.add_argument("--max-pages", type=int, default=0, help="0 = unlimited until API ends")
    parser.add_argument("--only-today-utc", action="store_true", help="Keep only markets whose endDate is today (UTC).")
    parser.add_argument("--max-markets", type=int, default=0, help="0 = unlimited")
    args = parser.parse_args()

    now = utc_now()
    today = now.date()
    active = True

    out_root = Path(args.output_dir)
    rows_by_currency: dict[str, list[dict[str, Any]]] = {"BTC": [], "ETH": []}
    combined_rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=20) as client:
        page = 0
        total_raw = 0
        while True:
            if args.max_pages and page >= args.max_pages:
                break
            offset = page * args.limit
            batch = await get_markets(client, limit=args.limit, offset=offset, active=active)
            if not batch:
                break
            total_raw += len(batch)

            for market in batch:
                question = market.get("question") or ""
                currency = detect_currency(question)
                if not currency:
                    continue
                end_date = extract_end_date(market)
                if not end_date:
                    continue
                if args.only_today_utc and end_date.date() != today:
                    continue
                # Match scanner behavior: skip already-ended markets
                if poly_resolution_time(end_date) < now:
                    continue

                target_price = extract_price_from_question(question)
                # For this export you only asked for outcomePrices[0] * 100.
                # Keep target_price optional so we can maximize retained markets.

                outcome0_scaled = extract_outcome_prices0_scaled(market)
                if outcome0_scaled is None:
                    continue

                row = {
                    "snapshot_at": now.isoformat(),
                    "market_id": market.get("id") or market.get("conditionId") or market.get("condition_id") or "",
                    "polymarket_question": question,
                    "currency": currency,
                    "option_type": detect_option_type(question),
                    "target_price_from_question": target_price,
                    "end_date_iso": end_date.isoformat(),
                    "liquidity_usd": extract_liquidity(market),
                    # Your requested probability input:
                    "outcomePrices_0_scaled": extract_outcome_prices0_scaled(market),
                    "outcomePrices_0_raw": extract_outcome_prices0_raw(market),
                }

                rows_by_currency[currency].append(row)
                combined_rows.append(row)

                if args.max_markets and len(combined_rows) >= args.max_markets:
                    break

            if args.max_markets and len(combined_rows) >= args.max_markets:
                break

            if len(batch) < args.limit:
                break
            page += 1

    # Write separate files for BTC and ETH
    btc_rows = rows_by_currency["BTC"]
    eth_rows = rows_by_currency["ETH"]

    suffix = "today_utc" if args.only_today_utc else "all"
    btc_path = out_root / "BTC" / f"polymarket_markets_{suffix}.csv"
    eth_path = out_root / "ETH" / f"polymarket_markets_{suffix}.csv"
    combined_path = out_root / f"polymarket_markets_{suffix}_both.csv"

    write_csv(
        btc_path,
        btc_rows,
        fieldnames=[
            "snapshot_at",
            "market_id",
            "polymarket_question",
            "currency",
            "option_type",
            "target_price_from_question",
            "end_date_iso",
            "liquidity_usd",
            "outcomePrices_0_scaled",
            "outcomePrices_0_raw",
        ],
    )
    write_csv(
        eth_path,
        eth_rows,
        fieldnames=[
            "snapshot_at",
            "market_id",
            "polymarket_question",
            "currency",
            "option_type",
            "target_price_from_question",
            "end_date_iso",
            "liquidity_usd",
            "outcomePrices_0_scaled",
            "outcomePrices_0_raw",
        ],
    )
    write_csv(
        combined_path,
        combined_rows,
        fieldnames=[
            "snapshot_at",
            "market_id",
            "polymarket_question",
            "currency",
            "option_type",
            "target_price_from_question",
            "end_date_iso",
            "liquidity_usd",
            "outcomePrices_0_scaled",
            "outcomePrices_0_raw",
        ],
    )

    print(f"Raw markets fetched total: {total_raw}")
    print(f"Wrote BTC rows: {len(btc_rows)} -> {btc_path}")
    print(f"Wrote ETH rows: {len(eth_rows)} -> {eth_path}")
    print(f"Wrote combined rows: {len(combined_rows)} -> {combined_path}")


if __name__ == "__main__":
    asyncio.run(main())

