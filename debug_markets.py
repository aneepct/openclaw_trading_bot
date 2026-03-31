#!/usr/bin/env python3
"""
Debug script to check what BTC/ETH markets are available and why matrix might be empty.
"""
import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from clients.polymarket import get_markets
from engine.scanner import (
    extract_end_date, extract_price_from_question, 
    CURRENCY_KEYWORDS, fetch_crypto_price_markets
)

async def debug_markets():
    print("=== DEBUGGING POLYMARKET MARKETS ===")
    
    # Time bounds for comparison
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = today_start + timedelta(days=2)
    week_end = today_start + timedelta(days=7)
    
    print(f"Current time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Today-Tomorrow filter: {today_start.strftime('%Y-%m-%d')} to {tomorrow_end.strftime('%Y-%m-%d')}")
    print(f"Week filter: {today_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")
    print()
    
    # Fetch some markets
    print("Fetching markets from Polymarket...")
    all_markets = []
    for page in range(3):  # Just check first 3 pages
        batch = await get_markets(limit=500, offset=page * 500, active=True)
        all_markets.extend(batch)
        if len(batch) < 500:
            break
    
    print(f"Total markets fetched: {len(all_markets)}")
    
    # Filter for crypto markets
    crypto_markets = []
    today_tomorrow_markets = []
    this_week_markets = []
    
    for m in all_markets:
        q = m.get("question", "")
        ql = q.lower()
        
        # Check if it mentions crypto
        currency = None
        for cur, keywords in CURRENCY_KEYWORDS.items():
            if any(kw in ql for kw in keywords):
                currency = cur
                break
        
        if not currency:
            continue
            
        # Check if it has a price
        price = extract_price_from_question(q)
        if price is None:
            continue
            
        # Check end date
        end = extract_end_date(m)
        if end is None or end < now:
            continue
            
        market_info = {
            'currency': currency,
            'price': price,
            'end_date': end,
            'question': q[:100] + "..." if len(q) > 100 else q,
            'id': m.get('id') or m.get('conditionId', 'unknown')
        }
        
        crypto_markets.append(market_info)
        
        if end < tomorrow_end:
            today_tomorrow_markets.append(market_info)
        if end < week_end:
            this_week_markets.append(market_info)
    
    print(f"\n=== CRYPTO MARKETS FOUND ===")
    print(f"Total BTC/ETH markets with prices: {len(crypto_markets)}")
    print(f"Resolving today/tomorrow: {len(today_tomorrow_markets)}")
    print(f"Resolving this week: {len(this_week_markets)}")
    
    print(f"\n=== TODAY/TOMORROW MARKETS ===")
    if today_tomorrow_markets:
        for m in sorted(today_tomorrow_markets, key=lambda x: x['end_date'])[:10]:
            days_left = (m['end_date'] - now).total_seconds() / 86400
            print(f"  {m['currency']} ${m['price']:,.0f} - {days_left:.1f} days - {m['question']}")
    else:
        print("  ❌ NO BTC/ETH markets found for today/tomorrow!")
    
    print(f"\n=== THIS WEEK MARKETS (showing first 10) ===")
    if this_week_markets:
        for m in sorted(this_week_markets, key=lambda x: x['end_date'])[:10]:
            days_left = (m['end_date'] - now).total_seconds() / 86400
            print(f"  {m['currency']} ${m['price']:,.0f} - {days_left:.1f} days - {m['question']}")
    else:
        print("  ❌ NO BTC/ETH markets found for this week!")
        
    # Test the actual scanner function
    print(f"\n=== TESTING SCANNER FUNCTION ===")
    try:
        scanner_markets = await fetch_crypto_price_markets()
        print(f"Scanner returned {len(scanner_markets)} markets")
        for m in scanner_markets[:5]:
            end_date = extract_end_date(m)
            days_left = (end_date - now).total_seconds() / 86400 if end_date else 0
            print(f"  {m['_currency']} ${m['_parsed_price']:,.0f} - {days_left:.1f} days - {m.get('question', '')[:80]}...")
    except Exception as e:
        print(f"Scanner error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_markets())