"""
Seed historical CMFDH II daily balances and performance metadata
into openclaw_deribit_balances.db.

Run:
  cd backend
  python seed_deribit_daily_balances.py
"""
from __future__ import annotations

from deribit_balance_store import init_balance_db, record_balance_snapshot, upsert_performance


SEED_PAYLOAD = {
    "fund": "CMFDH II",
    "updated_utc": "2026-08-10T23:20:00Z",
    "performance": {
        "label": "Net return since inception (Jan 2023)",
        "as_of": "2026-03-31",
        "twr_net_pct": 152.3,
        "twr_net_annualized_pct": 33.0,
        "irr_money_weighted_pct": 36.8,
        "note": "Time-weighted, net of fees; nets out all deposits & withdrawals. Updated quarterly from the CMFDH II report (nav/charlie_returns_engine.py). IRR is money-weighted on external investor flows. CMFDH II only - the 52% CAGR marketing figure is CMFDH I+II combined.",
    },
    "rows": [
        {"date": "2026-06-16", "btc": 15.6599, "usd": None},
        {"date": "2026-06-17", "btc": 15.6737, "usd": 1030369, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-19", "btc": 16.03671495, "usd": 1008165, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-22", "btc": 15.93898221, "usd": 1013728, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-23", "btc": 15.90217546, "usd": 1015905, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-24", "btc": 16.08615951, "usd": 1005651, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-25", "btc": 16.34157176, "usd": 992961, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-26", "btc": 16.47226509, "usd": 985322, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-27", "btc": 16.40417586, "usd": 988624, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-29", "btc": 16.57007291, "usd": 981031, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-06-30", "btc": 16.39819824, "usd": 988811, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-01", "btc": 16.66334986, "usd": 975239, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-02", "btc": 16.41734991, "usd": 989335, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-03", "btc": 16.26034768, "usd": 999220, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-04", "btc": 16.16885454, "usd": 1011248, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-05", "btc": 16.09817799, "usd": 1015953, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-06", "btc": 16.05955551, "usd": 1023278, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-07", "btc": 16.08412068, "usd": 1021214, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-08", "btc": 16.16850513, "usd": 1018147, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-10", "btc": 16.07246021, "usd": 1024582, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-13", "btc": 16.12137288, "usd": 1021388, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-15", "btc": 15.95683685, "usd": 1033307, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-17", "btc": 16.05113285, "usd": 1026431, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-18", "btc": 16.06025786, "usd": 1026190, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-20", "btc": 15.95542329, "usd": 1032915, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-21", "btc": 15.91593188, "usd": 1036018, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-23", "btc": 15.8143851, "usd": 1044005, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-26", "btc": 16.00934072, "usd": 1030189, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-29", "btc": 16.09220541, "usd": 1024430, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-30", "btc": 16.07032602, "usd": 1025979, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-07-31", "btc": 16.09220541, "usd": 1042673, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-08-03", "btc": 16.13147779, "usd": 1022199, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-08-05", "btc": 16.02675259, "usd": 1028684, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-08-06", "btc": 16.02675259, "usd": 1034477, "source": "Deribit margin_balance @ 07:00 Bali"},
        {"date": "2026-08-11", "btc": 16.05930483, "usd": 1026953, "source": "Deribit margin_balance @ 07:00 Bali"},
    ],
}


def run_seed() -> None:
    init_balance_db()
    upsert_performance("BTC", SEED_PAYLOAD["performance"])

    for row in SEED_PAYLOAD["rows"]:
        day = row["date"]
        ts = f"{day}T00:00:00+00:00"
        account = {
            "margin_balance": row.get("btc"),
            "equity": row.get("btc"),
            "usd_estimate": row.get("usd"),
            "source": row.get("source", "Deribit margin_balance @ 07:00 Bali"),
        }
        record_balance_snapshot("BTC", account, ts)

    print(f"Seeded {len(SEED_PAYLOAD['rows'])} daily rows + performance metadata.")


if __name__ == "__main__":
    run_seed()
