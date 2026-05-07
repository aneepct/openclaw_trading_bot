from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from clients.deribit import DeribitWSClient
from deribit_orderbook_data.util import (
    build_arg_parser,
    deribit_expiry_str_from_date,
    parse_instrument_name,
    utc_date,
    write_csv,
    write_json,
)


async def _fetch_order_book(
    ws: DeribitWSClient,
    *,
    instrument_name: str,
    depth: int,
) -> tuple[str, dict[str, Any] | None]:
    result = await ws.call(
        "public/get_order_book",
        {"instrument_name": instrument_name, "depth": depth},
    )
    return instrument_name, result if isinstance(result, dict) else None


async def main() -> None:
    parser = build_arg_parser("Deribit BTC order book export")
    args = parser.parse_args()

    currency = "BTC"
    index_name = "btc_usd"

    output_root = Path(args.output_dir) / currency
    now = datetime.now(timezone.utc).isoformat()

    async with DeribitWSClient() as ws:
        instruments = await ws.call(
            "public/get_instruments",
            {"currency": currency, "kind": "option", "expired": False},
        ) or []

        index_result = await ws.call("public/get_index_price", {"index_name": index_name})
        index_price = (index_result or {}).get("index_price")

        expiry_today = utc_date(0)
        expiry_tomorrow = utc_date(1)
        expiry_map = {
            "today": deribit_expiry_str_from_date(expiry_today),
            "tomorrow": deribit_expiry_str_from_date(expiry_tomorrow),
        }
        if getattr(args, "only_today", False):
            expiry_map = {"today": expiry_map["today"]}

        for day_label, expiry_str in expiry_map.items():
            expiry_instruments: list[str] = []
            expiry_meta: dict[str, dict[str, Any]] = {}
            for inst in instruments:
                name = inst.get("instrument_name") or ""
                meta = parse_instrument_name(name)
                if meta.get("currency") != currency:
                    continue
                if meta.get("expiry_str") != expiry_str:
                    continue
                if meta.get("strike") is None:
                    continue
                expiry_instruments.append(name)
                expiry_meta[name] = meta

            if not expiry_instruments:
                print(f"[{currency} {day_label}] No instruments found for expiry {expiry_str}")
                continue

            # Pick closest strikes to reduce API load.
            if index_price is not None:
                expiry_instruments.sort(
                    key=lambda n: abs((expiry_meta[n].get("strike") or 0.0) - float(index_price))
                )
            else:
                expiry_instruments.sort()

            if args.max_instruments_per_day and args.max_instruments_per_day > 0:
                expiry_instruments = expiry_instruments[: args.max_instruments_per_day]
            print(f"[{currency} {day_label}] Fetching {len(expiry_instruments)} instruments (depth={args.depth})")

            tasks = [
                _fetch_order_book(
                    ws,
                    instrument_name=name,
                    depth=args.depth,
                )
                for name in expiry_instruments
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            rows: list[dict[str, Any]] = []
            raw_books: dict[str, Any] = {}
            for r in results:
                if isinstance(r, Exception):
                    continue
                instrument_name, book = r
                if not isinstance(book, dict):
                    continue

                bids = book.get("bids") or []
                asks = book.get("asks") or []
                best_bid = bids[0] if bids else [None, None]
                best_ask = asks[0] if asks else [None, None]

                greeks = book.get("greeks") or {}
                meta = expiry_meta.get(instrument_name) or {}

                row = {
                    "snapshot_at": now,
                    "currency": currency,
                    "day_label": day_label,
                    "instrument_name": instrument_name,
                    "expiry_str": meta.get("expiry_str"),
                    "option_type": meta.get("option_type"),
                    "strike": meta.get("strike"),
                    "index_price": index_price,
                    "depth": args.depth,
                    "mark_iv": book.get("mark_iv"),
                    "delta": greeks.get("delta"),
                    "gamma": greeks.get("gamma"),
                    "vega": greeks.get("vega"),
                    "theta": greeks.get("theta"),
                    "rho": greeks.get("rho"),
                    "mark_price": book.get("mark_price"),
                    "underlying_price": book.get("underlying_price"),
                    "settlement_price": book.get("settlement_price"),
                    "best_bid_price": best_bid[0],
                    "best_bid_amount": best_bid[1],
                    "best_ask_price": best_ask[0],
                    "best_ask_amount": best_ask[1],
                }
                rows.append(row)
                if args.include_json:
                    raw_books[instrument_name] = book

            # Stable CSV field order
            fieldnames = [
                "snapshot_at",
                "currency",
                "day_label",
                "instrument_name",
                "expiry_str",
                "option_type",
                "strike",
                "index_price",
                "depth",
                "mark_iv",
                "delta",
                "gamma",
                "vega",
                "theta",
                "rho",
                "mark_price",
                "underlying_price",
                "settlement_price",
                "best_bid_price",
                "best_bid_amount",
                "best_ask_price",
                "best_ask_amount",
            ]

            out_csv = output_root / f"order_book_{day_label}_depth{args.depth}.csv"
            write_csv(out_csv, rows, fieldnames=fieldnames)
            print(f"[{currency} {day_label}] Wrote {len(rows)} rows -> {out_csv}")

            if args.include_json:
                out_json = output_root / f"order_book_{day_label}_depth{args.depth}.json"
                write_json(out_json, raw_books)
                print(f"[{currency} {day_label}] Wrote raw JSON -> {out_json}")


if __name__ == "__main__":
    asyncio.run(main())

