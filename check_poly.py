import json, re
import httpx

pages = [0, 500, 1000]
all_markets = []

for offset in pages:
    url = f"https://gamma-api.polymarket.com/markets?limit=500&active=true&closed=false&offset={offset}"
    r = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    data = r.json()
    all_markets.extend(data)
    print(f"Fetched offset={offset}: {len(data)} markets")

print(f"\nTotal: {len(all_markets)} markets\n")
print("=== BTC/ETH Price Level Markets ===")
for m in all_markets:
    q = m.get('question', '')
    ql = q.lower()
    if ('bitcoin' in ql or 'btc' in ql or 'ethereum' in ql or 'eth' in ql):
        if re.search(r'\$[\d,k]+', ql):
            prices = m.get('outcomePrices', '')
            end = m.get('endDate', '')[:10]
            print(f"  Q: {q}")
            print(f"     Prices: {prices} | End: {end}")
            print()
