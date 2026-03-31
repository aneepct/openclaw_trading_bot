"""
Diagnostic: Polymarket + Deribit matching (run from repo root).
  python scripts/debug_scan.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from clients.deribit import get_instruments, get_order_book, get_index_price
from engine.scanner import (
    fetch_crypto_price_markets,
    find_bracket_expiries,
    extract_polymarket_price,
    extract_end_date,
    parse_instrument,
    expiry_str_to_datetime,
    poly_resolution_time,
    days_until,
    detect_option_type,
)
from engine.math_engine import calculate_nd2, calculate_edge


async def main():
    print("=== STEP 1: Polymarket crypto price markets ===")
    poly = await fetch_crypto_price_markets()
    print(f"Found {len(poly)} markets:")
    for m in poly:
        print(f"  [{m['_currency']}] ${m['_parsed_price']:,.0f} | {m.get('question')} | YES={extract_polymarket_price(m)} | end={m.get('endDate','')[:10]}")

    print("\n=== STEP 2: Deribit BTC instruments (sample) ===")
    btc_insts = await get_instruments("BTC", "option")
    print(f"Total BTC options: {len(btc_insts)}")
    strikes = sorted(set(float(i['instrument_name'].split('-')[2]) for i in btc_insts if len(i['instrument_name'].split('-')) == 4))
    print(f"Strikes available: {strikes[:20]} ...")

    print("\n=== STEP 3: BTC spot price ===")
    spot = await get_index_price("btc_usd")
    print(f"BTC spot: ${spot:,.2f}")

    print("\n=== STEP 4: Bracket matching (T1/T2 interpolation) ===")
    for m in poly:
        currency = m['_currency']
        target_price = m['_parsed_price']
        end_date = extract_end_date(m)
        if not end_date:
            continue
        t_poly = poly_resolution_time(end_date)
        option_type = detect_option_type(m.get('question', ''))
        insts = btc_insts if currency == "BTC" else []

        T1, T2 = find_bracket_expiries(
            currency=currency,
            strike=target_price,
            t_poly=t_poly,
            instruments=insts,
            strike_tol=0.20,
            option_type=option_type,
        )
        print(f"\n  Poly: {m.get('question')}")
        print(f"  Target: ${target_price:,.0f} | Resolution: {t_poly.date()} | Type: {option_type}")
        print(f"  T1 (before resolution): {T1['instrument_name'] if T1 else 'NONE'}")
        print(f"  T2 (after  resolution): {T2['instrument_name'] if T2 else 'NONE'}")

        for label, inst in [("T1", T1), ("T2", T2)]:
            if not inst:
                continue
            name = inst['instrument_name']
            book = await get_order_book(name, depth=1)
            sigma = (book.get('mark_iv') or 0) / 100
            parsed = parse_instrument(name)
            expiry_dt = expiry_str_to_datetime(parsed['expiry_str'])
            T = days_until(expiry_dt) / 365
            prob = calculate_nd2(spot, parsed['strike'], sigma, T)
            poly_price = extract_polymarket_price(m)
            edge = calculate_edge(prob, poly_price) if prob else None
            if edge:
                print(f"  {label}: IV={sigma*100:.1f}% | N(d2)={prob*100:.2f}% | Poly YES={poly_price*100:.2f}% | Edge={edge['edge_pct']:+.1f}%")
            else:
                print(f"  {label}: IV={sigma*100:.1f}% | could not compute edge")


if __name__ == "__main__":
    asyncio.run(main())
