from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_str_to_num(m: str) -> Optional[int]:
    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return months.get(m.lower().strip())


def slug_matches_today(slug: str, *, now_utc: Optional[datetime] = None) -> bool:
    """
    For slugs like: `bitcoin-price-on-april-2` / `ethereum-price-on-april-2`
    match `on-<month>-<day>` to today's UTC month/day.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    m = re.search(r"-on-([a-zA-Z]+)-(\d{1,2})\b", slug)
    if not m:
        return False
    month_str = m.group(1)
    day = int(m.group(2))
    month = _month_str_to_num(month_str)
    if not month:
        return False
    return now_utc.month == month and now_utc.day == day


def _parse_outcome_prices(raw: Any) -> list[float]:
    # Gamma may return outcomePrices as a list OR as a JSON-string.
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[float] = []
        for x in raw:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return _parse_outcome_prices(loaded)
        except json.JSONDecodeError:
            # Sometimes it's already comma-separated
            pass
    return []


def _extract_first_market_outcome_prices(event_payload: dict[str, Any]) -> list[float]:
    # Based on your provided endpoints, `outcomePrices` lives at markets[0].outcomePrices
    markets = event_payload.get("markets") or []
    if not isinstance(markets, list) or not markets:
        return []
    market0 = markets[0] or {}
    if not isinstance(market0, dict):
        return []
    return _parse_outcome_prices(market0.get("outcomePrices"))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


async def fetch_event_by_slug(client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
    url = f"{GAMMA_BASE}/events/slug/{slug}"
    resp = await client.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Export Polymarket event outcomePrices to CSV.")
    parser.add_argument(
        "--slugs",
        nargs="*",
        default=["bitcoin-price-on-april-2", "ethereum-price-on-april-2"],
        help="Polymarket event slugs (defaults to the two you provided).",
    )
    parser.add_argument(
        "--only-today",
        action="store_true",
        help="Filter provided slugs to only those matching today's UTC date from the slug (e.g. ...-on-april-2).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(Path(__file__).resolve().parent / "output"),
        help="Folder where CSVs are written.",
    )
    args = parser.parse_args()

    out_root = Path(args.output_dir)
    snapshot_at = utc_now_iso()

    slugs = list(args.slugs)
    if args.only_today:
        slugs = [s for s in slugs if slug_matches_today(s)]
        if not slugs:
            raise SystemExit("No slugs matched today UTC date. Provide correct slugs or remove --only-today.")

    rows_combined: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for slug in slugs:
            payload = await fetch_event_by_slug(client, slug)
            title = payload.get("title") or ""

            outcome_prices = _extract_first_market_outcome_prices(payload)
            if not outcome_prices:
                print(f"[{slug}] No outcomePrices found in markets[0].outcomePrices")
                scaled = ""
            else:
                scaled = float(outcome_prices[0]) * 100

            row = {
                "snapshot_at": snapshot_at,
                "slug": slug,
                "title": title,
                "outcomePrices_0_raw": outcome_prices[0] if outcome_prices else "",
                "outcomePrices_0_scaled": scaled,
            }
            rows_combined.append(row)

            # One CSV per market
            out_csv = out_root / f"{slug}.csv"
            write_csv(
                out_csv,
                rows=[row],
                fieldnames=[
                    "snapshot_at",
                    "slug",
                    "title",
                    "outcomePrices_0_raw",
                    "outcomePrices_0_scaled",
                ],
            )
            print(f"Wrote CSV: {out_csv}")

    combined_csv = out_root / "polymarket_events_both.csv"
    write_csv(
        combined_csv,
        rows=rows_combined,
        fieldnames=[
            "snapshot_at",
            "slug",
            "title",
            "outcomePrices_0_raw",
            "outcomePrices_0_scaled",
        ],
    )
    print(f"Wrote combined CSV: {combined_csv}")


if __name__ == "__main__":
    asyncio.run(main())

