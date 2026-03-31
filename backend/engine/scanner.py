"""
OpenClaw Scanner — strict per spec at https://www.levenstein.net/openclaw

Core flow:
1. Fetch Polymarket BTC/ETH price-level markets (sorted nearest-first)
2. For each market find the TWO Deribit expiries that bracket t_poly:
       T1 = latest Deribit expiry BEFORE Polymarket resolution
       T2 = earliest Deribit expiry AFTER Polymarket resolution
3. Fetch order books for T1 and T2 (IV, Greeks)
4. Interpolate IV + Greeks at t_poly using doc formulas
5. Calculate N(d2) with interpolated σ
6. Compare with Polymarket price
7. Apply filters:
       a) abs_edge >= MIN_EDGE_PCT
       b) asymmetric payout >2x  (poly_price < 0.50)
       c) liquidity >= MIN_LIQUIDITY
"""
import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from clients.deribit import get_instruments, get_order_book, get_index_price
from clients.polymarket import get_markets
from engine.math_engine import (
    calculate_nd2, interpolate_iv, interpolate_greeks,
    calculate_edge, build_reasoning,
)
from db.database import save_signal, cleanup_old_signals
import db.database as db_module
import config

_latest_signals: list[dict] = []
_scan_lock = asyncio.Lock()

CURRENCY_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_instrument(name: str) -> Optional[dict]:
    m = re.match(r"^(\w+)-(\d{1,2}\w{3}\d{2})-(\d+)-([CP])$", name)
    if not m:
        return None
    return {
        "currency":    m.group(1),
        "expiry_str":  m.group(2),
        "strike":      float(m.group(3)),
        "option_type": m.group(4),
    }


def expiry_str_to_datetime(expiry_str: str) -> Optional[datetime]:
    """'28MAR26' → datetime at 08:00 UTC (Deribit standard)."""
    try:
        dt = datetime.strptime(expiry_str, "%d%b%y")
        return dt.replace(hour=8, minute=0, second=0, tzinfo=timezone.utc)
    except ValueError:
        return None


def poly_resolution_time(end_date: datetime) -> datetime:
    """Polymarket resolves at 16:00 UTC on end_date."""
    return end_date.replace(hour=16, minute=0, second=0, tzinfo=timezone.utc)


def years_until(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    return max((dt - now).total_seconds() / (365.25 * 86400), 0.0)


def days_until(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    return max((dt - now).total_seconds() / 86400, 0.0)


def extract_price_from_question(question: str) -> Optional[float]:
    q = question.replace(",", "")
    for pat in [r"\$([\d.]+)[kK]\b", r"\$([\d]{4,})\b"]:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if "k" in pat.lower():
                val *= 1000
            return val
    return None


# Per spec: detect market direction from question keywords
PUT_KEYWORDS  = ["dip", "fall", "drop", "below", "under", "crash", "decline", "sink"]
CALL_KEYWORDS = ["hit", "reach", "above", "exceed", "rally", "rise", "surpass"]

def detect_option_type(question: str) -> str:
    """
    Returns 'P' for put (bearish/downside) or 'C' for call (bullish/upside).
    Per spec: 'dip, fall, drop' → PUT | bullish phrasing → CALL
    Defaults to CALL if no keywords matched.
    """
    ql = question.lower()
    if any(kw in ql for kw in PUT_KEYWORDS):
        return "P"
    return "C"


def extract_end_date(market: dict) -> Optional[datetime]:
    end = market.get("endDate") or market.get("endDateIso")
    if not end:
        return None
    try:
        dt = datetime.strptime(end[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_polymarket_price(market: dict) -> Optional[float]:
    for val in [market.get("outcomes", "[]"), market.get("outcomePrices", "[]")]:
        try:
            outcomes = json.loads(val) if isinstance(val, str) else val
            prices_raw = market.get("outcomePrices", "[]")
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            for i, o in enumerate(outcomes):
                if str(o).lower() == "yes":
                    return float(prices[i])
        except Exception:
            pass
    for token in market.get("tokens", []):
        if str(token.get("outcome", "")).lower() == "yes":
            p = token.get("price")
            if p is not None:
                return float(p)
    return None


def extract_liquidity(market: dict) -> float:
    """Return Polymarket liquidity in USD."""
    for key in ["liquidity", "liquidityNum", "volume", "volumeNum"]:
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


# ── Core: find bracket expiries T1 and T2 ────────────────────────────────────

def find_bracket_expiries(
    currency: str,
    strike: float,
    t_poly: datetime,
    instruments: list[dict],
    strike_tol: float,
    option_type: str = "C",
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Find T1 (latest expiry BEFORE t_poly) and T2 (earliest expiry AFTER t_poly)
    for the given strike ± strike_tol, filtered to call options only.

    Returns (T1_instrument, T2_instrument) — either may be None.
    """
    before = []   # (expiry_dt, instrument) where expiry_dt <= t_poly
    after  = []   # (expiry_dt, instrument) where expiry_dt >  t_poly

    for inst in instruments:
        parsed = parse_instrument(inst.get("instrument_name", ""))
        if not parsed:
            continue
        if parsed["currency"] != currency:
            continue
        # Filter by detected option type (C for bullish, P for bearish)
        if parsed["option_type"] != option_type:
            continue

        # Strike must be within tolerance
        strike_diff = abs(parsed["strike"] - strike) / strike
        if strike_diff > strike_tol:
            continue

        expiry_dt = expiry_str_to_datetime(parsed["expiry_str"])
        if not expiry_dt:
            continue

        if expiry_dt <= t_poly:
            before.append((expiry_dt, inst))
        else:
            after.append((expiry_dt, inst))

    # T1 = closest before (latest)
    T1 = max(before, key=lambda x: x[0])[1] if before else None
    # T2 = closest after (earliest)
    T2 = min(after,  key=lambda x: x[0])[1] if after  else None

    return T1, T2


# ── Polymarket market fetch ───────────────────────────────────────────────────

async def fetch_crypto_price_markets() -> list[dict]:
    """
    Fetch Polymarket BTC/ETH price-level markets.
    Sorted by end date ascending (nearest resolution first — per doc priority).
    """
    all_markets = []
    for page in range(config.POLYMARKET_PAGES):
        batch = await get_markets(limit=500, offset=page * 500, active=True)
        all_markets.extend(batch)
        if len(batch) < 500:
            break

    results = []
    for m in all_markets:
        q  = m.get("question", "")
        ql = q.lower()

        # Must mention a crypto asset
        currency = None
        for cur, keywords in CURRENCY_KEYWORDS.items():
            if any(kw in ql for kw in keywords):
                currency = cur
                break
        if not currency:
            continue

        # Must contain a price threshold ($X or $Xk)
        price = extract_price_from_question(q)
        if price is None:
            continue

        # Must have a parseable end date
        end = extract_end_date(m)
        if end is None:
            continue

        # Must not have already resolved
        if end < datetime.now(timezone.utc):
            continue

        m["_parsed_price"] = price
        m["_currency"]     = currency
        results.append(m)

    # Nearest resolution date first (core OpenClaw priority)
    results.sort(key=lambda m: extract_end_date(m) or datetime(9999, 1, 1, tzinfo=timezone.utc))
    return results


# ── Main scan pass ────────────────────────────────────────────────────────────

async def scan_once() -> list[dict]:
    signals = []

    poly_markets = await fetch_crypto_price_markets()
    print(f"[Scanner] {len(poly_markets)} Polymarket price-level markets")

    # Pre-fetch Deribit instruments + spot price per currency
    deribit_instruments: dict[str, list] = {}
    spot_prices: dict[str, float] = {}
    for currency in config.ASSETS:
        deribit_instruments[currency] = await get_instruments(currency=currency, kind="option")
        spot_prices[currency] = await get_index_price(f"{currency.lower()}_usd")
        print(f"[Scanner] {currency} spot={spot_prices[currency]:,.2f}  "
              f"options={len(deribit_instruments[currency])}")

    strike_tol = config.STRIKE_TOLERANCE_PCT / 100.0

    for poly in poly_markets:
        currency     = poly["_currency"]
        target_price = poly["_parsed_price"]
        spot         = spot_prices.get(currency)
        if not spot:
            continue

        end_date = extract_end_date(poly)
        if not end_date:
            continue

        # Polymarket resolves at 16:00 UTC
        t_poly_dt = poly_resolution_time(end_date)
        if t_poly_dt < datetime.now(timezone.utc):
            continue

        t_poly_years = years_until(t_poly_dt)
        t_poly_days  = days_until(t_poly_dt)

        # ── Detect option type from question keywords (per spec) ──────────
        option_type = detect_option_type(poly.get("question", ""))
        is_call     = (option_type == "C")

        # ── Find bracket expiries T1 and T2 ──────────────────────────────
        T1_inst, T2_inst = find_bracket_expiries(
            currency    = currency,
            strike      = target_price,
            t_poly      = t_poly_dt,
            instruments = deribit_instruments[currency],
            strike_tol  = strike_tol,
            option_type = option_type,
        )

        if T1_inst is None and T2_inst is None:
            print(f"[Scanner] No bracket found: {poly.get('question')} (${target_price:,.0f})")
            continue

        # ── Fetch order books ─────────────────────────────────────────────
        async def get_book(inst):
            if inst is None:
                return None
            return await get_order_book(inst["instrument_name"], depth=config.DERIBIT_DEPTH)

        book1, book2 = await asyncio.gather(get_book(T1_inst), get_book(T2_inst))

        # Need at least one valid book
        if book1 is None and book2 is None:
            continue

        def get_sigma(book):
            if book is None:
                return None
            iv = book.get("mark_iv")
            return iv / 100.0 if iv else None

        sigma1 = get_sigma(book1)
        sigma2 = get_sigma(book2)

        # Need at least one IV
        if sigma1 is None and sigma2 is None:
            continue

        # ── Compute expiry times ──────────────────────────────────────────
        def get_T_years(inst):
            if inst is None:
                return None
            parsed = parse_instrument(inst["instrument_name"])
            if not parsed:
                return None
            exp_dt = expiry_str_to_datetime(parsed["expiry_str"])
            return years_until(exp_dt) if exp_dt else None

        T1_years = get_T_years(T1_inst)
        T2_years = get_T_years(T2_inst)
        T1_days  = days_until(expiry_str_to_datetime(parse_instrument(T1_inst["instrument_name"])["expiry_str"])) if T1_inst else None
        T2_days  = days_until(expiry_str_to_datetime(parse_instrument(T2_inst["instrument_name"])["expiry_str"])) if T2_inst else None

        # ── Interpolate IV ────────────────────────────────────────────────
        if sigma1 is not None and sigma2 is not None and T1_years is not None and T2_years is not None:
            sigma_interp = interpolate_iv(sigma1, T1_years, sigma2, T2_years, t_poly_years)
            w = (t_poly_years - T1_years) / (T2_years - T1_years) if T2_years != T1_years else 0.5
            method = "interpolated"
        elif sigma2 is not None:
            sigma_interp = sigma2
            w = 1.0
            method = "T2-only"
        else:
            sigma_interp = sigma1
            w = 0.0
            method = "T1-only"

        # ── Interpolate Greeks ────────────────────────────────────────────
        greeks1 = (book1 or {}).get("greeks") or {}
        greeks2 = (book2 or {}).get("greeks") or {}

        if T1_years and T2_years:
            greeks_interp = interpolate_greeks(
                greeks1, T1_years,
                greeks2, T2_years,
                t_poly_years,
            )
        else:
            greeks_interp = greeks2 if greeks2 else greeks1

        # ── N(d2) with interpolated σ ─────────────────────────────────────
        # is_call=True  → N(d2)  for "hit/reach/above" markets (CALL)
        # is_call=False → N(-d2) for "dip/fall/drop" markets (PUT)
        prob = calculate_nd2(
            spot    = spot,
            strike  = target_price,
            sigma   = sigma_interp,
            T       = t_poly_years,
            is_call = is_call,
        )
        if prob is None:
            continue

        # ── Polymarket price + liquidity ──────────────────────────────────
        poly_price = extract_polymarket_price(poly)
        if poly_price is None:
            continue

        liquidity = extract_liquidity(poly)

        # Liquidity filter
        if liquidity < config.MIN_LIQUIDITY_USD:
            print(f"[Skip] Low liquidity ${liquidity:,.0f}: {poly.get('question')}")
            continue

        # ── Edge + filters ────────────────────────────────────────────────
        edge = calculate_edge(prob, poly_price, min_edge_pct=config.MIN_EDGE_PCT)

        # ── Build reasoning card ──────────────────────────────────────────
        t1_name = T1_inst["instrument_name"] if T1_inst else "N/A"
        t2_name = T2_inst["instrument_name"] if T2_inst else "N/A"

        reasoning = build_reasoning(
            instrument_t1 = t1_name,
            instrument_t2 = t2_name,
            spot          = spot,
            strike        = target_price,
            sigma_t1      = sigma1 or sigma_interp,
            sigma_t2      = sigma2 or sigma_interp,
            sigma_interp  = sigma_interp,
            T1_days       = T1_days or 0,
            T2_days       = T2_days or 0,
            t_poly_days   = t_poly_days,
            w             = w,
            deribit_prob  = prob,
            polymarket_price = poly_price,
            edge          = edge,
        )

        signal = {
            # Identification
            "instrument_t1":         t1_name,
            "instrument_t2":         t2_name,
            "option_type":           option_type,   # C or P (per spec keyword detection)
            "interp_method":         method,
            "interp_weight_w":       round(w, 4),
            "polymarket_market_id":  poly.get("id") or poly.get("conditionId", ""),
            "polymarket_question":   poly.get("question", ""),
            # Prices
            "spot_price":            spot,
            "strike":                target_price,
            # Time
            "t_poly_days":           round(t_poly_days, 2),
            "T1_days":               round(T1_days, 2) if T1_days is not None else None,
            "T2_days":               round(T2_days, 2) if T2_days is not None else None,
            # Volatility
            "sigma_t1":              round(sigma1, 4) if sigma1 else None,
            "sigma_t2":              round(sigma2, 4) if sigma2 else None,
            "sigma_interp":          round(sigma_interp, 4),
            # Greeks (interpolated)
            "delta":                 greeks_interp.get("delta"),
            "gamma":                 greeks_interp.get("gamma"),
            "vega":                  greeks_interp.get("vega"),
            "theta":                 greeks_interp.get("theta"),
            "rho":                   greeks_interp.get("rho"),
            # Edge
            "deribit_prob":          edge["deribit_prob"],
            "polymarket_price":      edge["polymarket_price"],
            "edge_pct":              edge["edge_pct"],
            "abs_edge_pct":          edge["abs_edge_pct"],
            "direction":             edge["direction"],
            "payout_ratio":          edge["payout_ratio"],
            "asymmetric_payout":     edge["asymmetric_payout"],
            "has_alpha":             edge["has_alpha"],
            # Liquidity
            "liquidity_usd":         round(liquidity, 2),
            # Meta
            "reasoning":             reasoning,
            "scanned_at":            datetime.utcnow().isoformat(),
        }

        signals.append(signal)

        # Persist all scanned signals to DB (ticker + leaderboard require DB lookback).
        await save_signal(signal)

        if edge["has_alpha"]:
            print(f"[Alpha] {t1_name}<->{t2_name} | "
                  f"w={w:.3f} | σ={sigma_interp*100:.1f}% | "
                  f"Edge:{edge['edge_pct']:+.1f}% | {edge['direction']} | "
                  f"Payout:{edge['payout_ratio']}x | Liq:${liquidity:,.0f}")
        else:
            reasons = []
            if edge["abs_edge_pct"] < config.MIN_EDGE_PCT:
                reasons.append(f"edge {edge['abs_edge_pct']:.1f}% < {config.MIN_EDGE_PCT}%")
            if not edge["asymmetric_payout"]:
                reasons.append(f"payout {edge['payout_ratio']}x < 2x")
            print(f"[No alpha] {t1_name}<->{t2_name} | {', '.join(reasons)}")

    signals.sort(key=lambda x: x["abs_edge_pct"], reverse=True)
    return signals


# ── Background ticker ─────────────────────────────────────────────────────────

async def ticker_loop():
    global _latest_signals

    from db.database import init_db
    await init_db()

    scan_count = 0
    while True:
        try:
            scan_count += 1
            if scan_count % config.DB_CLEANUP_EVERY_N_SCANS == 1:
                await cleanup_old_signals(retain_days=config.DB_RETAIN_DAYS)
                print(f"[DB] Cleanup — keeping last {config.DB_RETAIN_DAYS} days")

            signals = await scan_once()
            async with _scan_lock:
                _latest_signals = signals

            refresh_fn = getattr(db_module, "refresh_alpha_leaderboard_cache", None)
            if refresh_fn:
                await refresh_fn(
                    hours=config.LEADERBOARD_HOURS,
                    top_n=config.LEADERBOARD_TOP_N,
                )

            alpha = [s for s in signals if s["has_alpha"]]
            print(f"[{datetime.utcnow().isoformat()}] Scan done — "
                  f"{len(signals)} signals, {len(alpha)} alpha")

        except Exception as e:
            import traceback
            print(f"[Scanner Error] {e}")
            traceback.print_exc()

        await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)


def get_latest_signals() -> list[dict]:
    return _latest_signals
