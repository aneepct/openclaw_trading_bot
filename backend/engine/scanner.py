"""
Open Claw scanner.

Collects Polymarket + Deribit market context, filters to today/tomorrow
Deribit expiries, and passes compact context to the LLM agent layer.
"""
import asyncio
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.openai_agent import build_agent_signals
from clients.deribit import get_index_price, get_instruments, get_order_book
from clients.polymarket import get_daily_event_markets, get_yes_clob_token_id, get_market_price
import memory_store as db_module
from memory_store import cleanup_old_signals, save_signal
import config

_latest_signals: list[dict] = []
_scan_lock = asyncio.Lock()

CURRENCY_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
}

PUT_KEYWORDS = ["dip", "fall", "drop", "below", "under", "crash", "decline", "sink"]


def parse_instrument(name: str) -> Optional[dict]:
    match = re.match(r"^(\w+)-(\d{1,2}\w{3}\d{2})-(\d+)-([CP])$", name)
    if not match:
        return None
    return {
        "currency": match.group(1),
        "expiry_str": match.group(2),
        "strike": float(match.group(3)),
        "option_type": match.group(4),
    }


def expiry_str_to_datetime(expiry_str: str) -> Optional[datetime]:
    try:
        dt = datetime.strptime(expiry_str, "%d%b%y")
        return dt.replace(hour=8, minute=0, second=0, tzinfo=timezone.utc)
    except ValueError:
        return None


def poly_resolution_time(end_date: datetime) -> datetime:
    return end_date.replace(hour=16, minute=0, second=0, tzinfo=timezone.utc)


def days_until(dt: datetime) -> float:
    return max((dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.0)


def extract_price_from_question(question: str) -> Optional[float]:
    q = question.replace(",", "")
    # Dollar sign + k suffix: $68k, $68.5k
    m = re.search(r"\$([\d.]+)\s*[kK]\b", q, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1000
    # Dollar sign + raw digits: $68000, $3500
    m = re.search(r"\$([\d]{4,})\b", q)
    if m:
        return float(m.group(1))
    # No dollar, k suffix: 68k, 3.5K
    m = re.search(r"\b([\d.]+)\s*[kK]\b", q, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1000
    # No dollar, 5+ digit number (avoids matching years like 2025)
    m = re.search(r"\b([\d]{5,})\b", q)
    if m:
        return float(m.group(1))
    return None


def extract_price_range_from_question(question: str) -> tuple[Optional[float], Optional[float]]:
    """Return (low, high) for range questions like 'between $68k and $70k',
    or (price, None) for single-price questions."""
    q = question.replace(",", "")

    def _parse_amount(digits: str, k_suffix: Optional[str]) -> float:
        v = float(digits)
        if k_suffix:
            v *= 1000
        return v

    m = re.search(
        r"between\s+\$?([\d.]+)\s*([kK])?\s+and\s+\$?([\d.]+)\s*([kK])?",
        q, re.IGNORECASE
    )
    if m:
        low = _parse_amount(m.group(1), m.group(2))
        high = _parse_amount(m.group(3), m.group(4))
        return low, high

    return extract_price_from_question(question), None


def is_less_than_question(question: str) -> bool:
    """Return True for 'less than K' / 'below K' market questions."""
    ql = question.lower()
    return bool(re.search(r"\b(less\s+than|lower\s+than|below|under)\b", ql))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erfc — no external dependency."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def bsm_call_prob(spot: float, strike: float, sigma: float, t_years: float) -> float:
    """P(S_T > K) at Polymarket resolution time under log-normal (r=0 approximation).

    For near-expiry crypto options the carry term is negligible, so we use
    the undiscounted forward (F = S).  sigma is Deribit mark IV (annualised).
    """
    if t_years <= 0 or sigma <= 0:
        return 1.0 if spot > strike else 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * t_years) / (sigma * math.sqrt(t_years))
    return _norm_cdf(d1)


def compute_deribit_prob(
    book1: Optional[dict],
    book2: Optional[dict],
    spot: float,
    strike: float,
    t_poly_dt: datetime,
    t1_expiry: Optional[datetime],
    t2_expiry: Optional[datetime],
) -> Optional[float]:
    """Compute P(S > K) at Polymarket resolution time using BSM repriced from Deribit IV.

    When two Deribit expiry brackets are available (T1 <= T_poly <= T2) we
    interpolate total variance and derive an effective sigma at T_poly, then
    reprice using Black-Scholes.  This corrects the time-horizon mismatch
    between Deribit (T_deribit) and Polymarket (T_poly < T_deribit).
    """
    now = datetime.now(timezone.utc)
    t_poly_years = max((t_poly_dt - now).total_seconds(), 60) / 31_536_000  # floor at 1 min

    def _sigma_from_book(book: Optional[dict]) -> Optional[float]:
        if not book:
            return None
        iv = (book.get("mark_iv") or 0)
        return float(iv) / 100.0 if iv else None

    sigma1 = _sigma_from_book(book1)
    sigma2 = _sigma_from_book(book2)

    # Interpolate total variance (sigma^2 * T) linearly between the two brackets
    if sigma1 is not None and sigma2 is not None and t1_expiry and t2_expiry:
        t1_years = max((t1_expiry - now).total_seconds(), 60) / 31_536_000
        t2_years = max((t2_expiry - now).total_seconds(), 60) / 31_536_000
        span = t2_years - t1_years
        if span > 1e-9:
            w = max(0.0, min(1.0, (t_poly_years - t1_years) / span))
            total_var = (1 - w) * sigma1 ** 2 * t1_years + w * sigma2 ** 2 * t2_years
        else:
            total_var = sigma1 ** 2 * t_poly_years
        eff_sigma = math.sqrt(max(total_var, 0.0) / t_poly_years)
        return round(bsm_call_prob(spot, strike, eff_sigma, t_poly_years), 4)

    # Single bracket: use available sigma directly at T_poly
    sigma = sigma1 or sigma2
    if sigma is not None:
        return round(bsm_call_prob(spot, strike, sigma, t_poly_years), 4)

    return None


def detect_option_type(question: str) -> str:
    ql = question.lower()
    if any(keyword in ql for keyword in PUT_KEYWORDS):
        return "P"
    return "C"


def extract_end_date(market: dict) -> Optional[datetime]:
    end = market.get("endDate") or market.get("endDateIso")
    if not end:
        return None
    try:
        return datetime.strptime(end[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_polymarket_price(market: dict) -> Optional[float]:
    try:
        outcomes_raw = market.get("outcomes", "[]")
        prices_raw = market.get("outcomePrices", "[]")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        for idx, outcome in enumerate(outcomes or []):
            if str(outcome).lower() == "yes":
                return float(prices[idx])
    except Exception:
        pass

    for token in market.get("tokens", []):
        if str(token.get("outcome", "")).lower() == "yes":
            price = token.get("price")
            if price is not None:
                return float(price)
    return None


def extract_liquidity(market: dict) -> float:
    for key in ["liquidity", "liquidityNum", "volume", "volumeNum"]:
        value = market.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0


def instrument_expiry_iso(inst: Optional[dict]) -> Optional[str]:
    if inst is None:
        return None
    parsed = parse_instrument(inst.get("instrument_name", ""))
    if not parsed:
        return None
    expiry_dt = expiry_str_to_datetime(parsed["expiry_str"])
    return expiry_dt.isoformat() if expiry_dt else None


def allowed_deribit_expiry_dates(now: Optional[datetime] = None) -> set:
    current = now or datetime.now(timezone.utc)
    start_date = current.date()
    return {
        start_date + timedelta(days=offset)
        for offset in range(config.ALLOWED_DERIBIT_EXPIRY_DAYS + 1)
    }


def find_bracket_expiries(
    currency: str,
    strike: float,
    t_poly: datetime,
    instruments: list[dict],
    strike_tol: float,
    option_type: str = "C",
) -> tuple[Optional[dict], Optional[dict]]:
    allowed_dates = allowed_deribit_expiry_dates()

    # Collect candidates: (expiry_dt, strike_diff, inst)
    before: list[tuple[datetime, float, dict]] = []
    after: list[tuple[datetime, float, dict]] = []

    for inst in instruments:
        parsed = parse_instrument(inst.get("instrument_name", ""))
        if not parsed:
            continue
        if parsed["currency"] != currency or parsed["option_type"] != option_type:
            continue
        strike_diff = abs(parsed["strike"] - strike) / strike
        if strike_diff > strike_tol:
            continue
        expiry_dt = expiry_str_to_datetime(parsed["expiry_str"])
        if not expiry_dt or expiry_dt.date() not in allowed_dates:
            continue
        if expiry_dt <= t_poly:
            before.append((expiry_dt, strike_diff, inst))
        else:
            after.append((expiry_dt, strike_diff, inst))

    # T1 = latest expiry before t_poly; among ties pick the closest-in-strike instrument
    t1: Optional[dict] = None
    if before:
        latest_dt = max(item[0] for item in before)
        candidates_t1 = [item for item in before if item[0] == latest_dt]
        t1 = min(candidates_t1, key=lambda item: item[1])[2]

    # T2 = earliest expiry after t_poly; among ties pick the closest-in-strike instrument
    t2: Optional[dict] = None
    if after:
        earliest_dt = min(item[0] for item in after)
        candidates_t2 = [item for item in after if item[0] == earliest_dt]
        t2 = min(candidates_t2, key=lambda item: item[1])[2]

    return t1, t2


async def fetch_crypto_price_markets() -> list[dict]:
    """Fetch today's daily price markets directly from Polymarket event slugs.
    e.g. bitcoin-price-on-april-7, ethereum-price-on-april-7
    Guarantees exactly the right daily markets (11 BTC + 11 ETH).
    """
    today = datetime.now(timezone.utc).date()
    results = []
    for currency in config.ASSETS:
        markets = await get_daily_event_markets(currency, today)
        for market in markets:
            question = market.get("question", "")
            target_price, target_price_high = extract_price_range_from_question(question)
            if target_price is None:
                continue
            market["_parsed_price"] = target_price
            market["_parsed_price_high"] = target_price_high
            market["_is_less_than"] = is_less_than_question(question)
            results.append(market)
    return results


async def scan_once() -> list[dict]:
    candidates: list[dict] = []
    poly_markets = await fetch_crypto_price_markets()

    # Fetch live CLOB midpoint prices for all markets in parallel.
    # These are real-time and more accurate than the cached outcomePrices
    # returned by the Gamma API.
    clob_prices: dict[str, float] = {}
    clob_token_ids: dict[str, str] = {}

    async def _fetch_clob_price(poly: dict) -> None:
        token_id = get_yes_clob_token_id(poly)
        if not token_id:
            return
        market_id = poly.get("id") or poly.get("conditionId", "")
        clob_token_ids[market_id] = token_id
        result = await get_market_price(token_id)
        mid = (result or {}).get("mid")
        if mid is not None:
            clob_prices[market_id] = float(mid)

    await asyncio.gather(*[_fetch_clob_price(p) for p in poly_markets])

    deribit_instruments: dict[str, list[dict]] = {}
    spot_prices: dict[str, float] = {}
    for currency in config.ASSETS:
        deribit_instruments[currency] = await get_instruments(currency=currency, kind="option")
        spot_prices[currency] = await get_index_price(f"{currency.lower()}_usd")

    strike_tol = config.STRIKE_TOLERANCE_PCT / 100.0
    for poly in poly_markets:
        currency = poly["_currency"]
        target_price = poly["_parsed_price"]
        spot = spot_prices.get(currency)
        if not spot:
            continue

        end_date = extract_end_date(poly)
        if not end_date:
            continue
        t_poly_dt = poly_resolution_time(end_date)
        if t_poly_dt < datetime.now(timezone.utc):
            continue

        strike_high = poly.get("_parsed_price_high")
        is_range = strike_high is not None
        is_less_than = poly.get("_is_less_than", False)

        # Range markets → call spread: P(K_low < S <= K_high) = Delta(K_low) - Delta(K_high)
        # Less-than markets → P(S < K) = 1 - Delta(K)  (always use a call, flip at the end)
        # Greater-than / above markets → P(S > K) = Delta(K)
        option_type = "C" if (is_range or is_less_than) else detect_option_type(poly.get("question", ""))

        t1_inst, t2_inst = find_bracket_expiries(
            currency=currency,
            strike=target_price,
            t_poly=t_poly_dt,
            instruments=deribit_instruments[currency],
            strike_tol=strike_tol,
            option_type=option_type,
        )
        if t1_inst is None and t2_inst is None:
            continue

        if is_range:
            t1_inst_high, t2_inst_high = find_bracket_expiries(
                currency=currency,
                strike=strike_high,
                t_poly=t_poly_dt,
                instruments=deribit_instruments[currency],
                strike_tol=strike_tol,
                option_type="C",
            )
        else:
            t1_inst_high = t2_inst_high = None

        async def get_book(inst: Optional[dict]):
            if inst is None:
                return None
            return await get_order_book(inst["instrument_name"], depth=config.DERIBIT_DEPTH)

        if is_range:
            book1, book2, book1_high, book2_high = await asyncio.gather(
                get_book(t1_inst), get_book(t2_inst),
                get_book(t1_inst_high), get_book(t2_inst_high),
            )
        else:
            book1, book2 = await asyncio.gather(get_book(t1_inst), get_book(t2_inst))
            book1_high = book2_high = None

        if book1 is None and book2 is None:
            continue

        _market_id = poly.get("id") or poly.get("conditionId", "")
        poly_price = clob_prices.get(_market_id) or extract_polymarket_price(poly)
        if poly_price is None:
            continue
        liquidity = extract_liquidity(poly)
        if liquidity < config.MIN_LIQUIDITY_USD:
            continue

        sigma1 = ((book1 or {}).get("mark_iv") or 0) / 100 if book1 else None
        sigma2 = ((book2 or {}).get("mark_iv") or 0) / 100 if book2 else None
        if sigma1 is None and sigma2 is None:
            continue

        t1_expiry = expiry_str_to_datetime(parse_instrument(t1_inst["instrument_name"])["expiry_str"]) if t1_inst else None
        t2_expiry = expiry_str_to_datetime(parse_instrument(t2_inst["instrument_name"])["expiry_str"]) if t2_inst else None
        method = "interpolated" if (t1_inst and t2_inst) else ("T2-only" if t2_inst else "T1-only")

        if is_range:
            t1_expiry_high = expiry_str_to_datetime(parse_instrument(t1_inst_high["instrument_name"])["expiry_str"]) if t1_inst_high else None
            t2_expiry_high = expiry_str_to_datetime(parse_instrument(t2_inst_high["instrument_name"])["expiry_str"]) if t2_inst_high else None
            prob_low = compute_deribit_prob(book1, book2, spot, target_price, t_poly_dt, t1_expiry, t2_expiry)
            prob_high = compute_deribit_prob(book1_high, book2_high, spot, strike_high, t_poly_dt, t1_expiry_high, t2_expiry_high)
            if prob_low is None:
                prob_low = 1.0 if target_price < spot else 0.0
            if prob_high is None:
                prob_high = 1.0 if strike_high < spot else 0.0
            deribit_prob = round(max(0.0, min(1.0, prob_low - prob_high)), 4)
        else:
            deribit_prob = compute_deribit_prob(book1, book2, spot, target_price, t_poly_dt, t1_expiry, t2_expiry)
            # P(S < K) = 1 - P(S > K)
            if is_less_than and deribit_prob is not None:
                deribit_prob = round(1.0 - deribit_prob, 4)
        edge_pct = round((float(deribit_prob) - float(poly_price)) * 100, 2) if deribit_prob is not None else None
        abs_edge_pct = abs(edge_pct) if edge_pct is not None else None
        has_alpha = abs_edge_pct is not None and abs_edge_pct >= config.MIN_EDGE_PCT

        # Compute interpolation weight for storage/debugging
        interp_w = None
        if t1_expiry and t2_expiry:
            span = (t2_expiry - t1_expiry).total_seconds()
            if span > 0:
                interp_w = round(max(0.0, min(1.0, (t_poly_dt - t1_expiry).total_seconds() / span)), 4)

        candidates.append(
            {
                "instrument_t1": t1_inst["instrument_name"] if t1_inst else "N/A",
                "instrument_t2": t2_inst["instrument_name"] if t2_inst else "N/A",
                "instrument_t1_expiry": instrument_expiry_iso(t1_inst),
                "instrument_t2_expiry": instrument_expiry_iso(t2_inst),
                "option_type": option_type,
                "interp_method": method,
                "interp_weight_w": interp_w,
                "interp_confidence": "high" if t1_inst and t2_inst else "reduced",
                "interp_confidence_rank": 2 if t1_inst and t2_inst else 1,
                "interp_note": "Two live Deribit contexts available." if t1_inst and t2_inst else "Single-context fallback used.",
                "polymarket_market_id": poly.get("id") or poly.get("conditionId", ""),
                "polymarket_question": poly.get("question", ""),
                "market_resolution_at": t_poly_dt.isoformat(),
                "spot_price": round(float(spot), 2),
                "strike": target_price,
                "strike_high": strike_high,
                "t_poly_days": round(days_until(t_poly_dt), 2),
                "T1_days": round(days_until(t1_expiry), 2) if t1_expiry else None,
                "T2_days": round(days_until(t2_expiry), 2) if t2_expiry else None,
                "sigma_t1": round(sigma1, 4) if sigma1 is not None else None,
                "sigma_t2": round(sigma2, 4) if sigma2 is not None else None,
                "sigma_interp": round(sigma1 or sigma2 or 0.0, 4),
                "delta": ((book1 or {}).get("greeks") or {}).get("delta"),
                "gamma": ((book1 or {}).get("greeks") or {}).get("gamma"),
                "vega": ((book1 or {}).get("greeks") or {}).get("vega"),
                "theta": ((book1 or {}).get("greeks") or {}).get("theta"),
                "rho": ((book1 or {}).get("greeks") or {}).get("rho"),
                "polymarket_price": round(float(poly_price), 4),
                "deribit_prob": deribit_prob,
                "edge_pct": edge_pct,
                "abs_edge_pct": abs_edge_pct,
                "has_alpha": has_alpha,
                "liquidity_usd": round(liquidity, 2),
                "yes_clob_token_id": clob_token_ids.get(poly.get("id") or poly.get("conditionId", "")),
                "t1_book": {
                    "mark_iv": (book1 or {}).get("mark_iv"),
                    "delta": ((book1 or {}).get("greeks") or {}).get("delta"),
                    "bid_price": (book1 or {}).get("best_bid_price"),
                    "ask_price": (book1 or {}).get("best_ask_price"),
                    "mark_price": (book1 or {}).get("mark_price"),
                } if book1 else None,
                "t2_book": {
                    "mark_iv": (book2 or {}).get("mark_iv"),
                    "delta": ((book2 or {}).get("greeks") or {}).get("delta"),
                    "bid_price": (book2 or {}).get("best_bid_price"),
                    "ask_price": (book2 or {}).get("best_ask_price"),
                    "mark_price": (book2 or {}).get("mark_price"),
                } if book2 else None,
                "scanned_at": datetime.utcnow().isoformat(),
                "quoted_at": datetime.utcnow().isoformat(),
                "live_data": True,
            }
        )

    signals, metadata = await build_agent_signals(candidates)
    for signal in signals:
        await save_signal(signal)

    signals.sort(
        key=lambda item: (
            int(item.get("interp_confidence_rank") or 0),
            float(item.get("abs_edge_pct") or 0.0),
        ),
        reverse=True,
    )
    if metadata.get("summary"):
        print(f"[Agent Summary] {metadata['summary'][:220]}")
    return signals


async def ticker_loop():
    from memory_store import init_db

    await init_db()
    scan_count = 0
    while True:
        try:
            scan_count += 1
            if scan_count % config.DB_CLEANUP_EVERY_N_SCANS == 1:
                await cleanup_old_signals(retain_days=config.DB_RETAIN_DAYS)

            signals = await scan_once()
            async with _scan_lock:
                global _latest_signals
                _latest_signals = signals

            refresh_fn = getattr(db_module, "refresh_alpha_leaderboard_cache", None)
            if refresh_fn:
                await refresh_fn(
                    hours=config.LEADERBOARD_HOURS,
                    top_n=config.LEADERBOARD_TOP_N,
                )
        except Exception as exc:
            import traceback
            print(f"[Scanner Error] {exc}")
            traceback.print_exc()

        await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)


def get_latest_signals() -> list[dict]:
    return _latest_signals
