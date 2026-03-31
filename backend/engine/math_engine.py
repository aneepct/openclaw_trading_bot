"""
OpenClaw Math Engine — strict per spec at https://www.levenstein.net/openclaw

Core formula:
    d2  = (ln(F/K) - 0.5 * σ² * T) / (σ * √T)
    P   = N(d2)  for call   (probability of expiring ITM)
    P   = N(-d2) for put

Two-expiry interpolation:
    w   = (t_poly - T1) / (T2 - T1)
    σ²_interp * t = (1-w) * σ1² * T1 + w * σ2² * T2   (variance-weighted)

Greek time-scaling:
    Delta  → linear interpolation
    Gamma  → scales with 1/√t
    Vega   → scales with √t
    Theta  → scales with 1/√t
    Rho    → linear interpolation
"""
import math
from scipy.stats import norm
from typing import Optional


def calculate_nd2(
    spot: float,
    strike: float,
    sigma: float,       # IV annualised (e.g. 0.80 for 80 %)
    T: float,           # time to expiry in years
    r: float = 0.0,     # risk-free rate (0 for crypto)
    is_call: bool = True,
) -> Optional[float]:
    """N(d2) — risk-neutral probability of expiring ITM."""
    if T <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return None
    try:
        F = spot * math.exp(r * T)
        d2 = (math.log(F / strike) - 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        return float(norm.cdf(d2) if is_call else norm.cdf(-d2))
    except (ValueError, ZeroDivisionError):
        return None


def interpolate_iv(
    sigma1: float, T1: float,
    sigma2: float, T2: float,
    t_target: float,
) -> float:
    """
    Variance-weighted IV interpolation between two Deribit expiries.

    σ²_interp * t_target = (1-w) * σ1² * T1 + w * σ2² * T2
    where w = (t_target - T1) / (T2 - T1)

    Follows the standard options market practice of interpolating
    total variance (σ² * T) linearly in time.
    """
    if T2 <= T1:
        return sigma1
    if t_target <= T1:
        return sigma1
    if t_target >= T2:
        return sigma2

    w = (t_target - T1) / (T2 - T1)
    total_var = (1.0 - w) * sigma1 ** 2 * T1 + w * sigma2 ** 2 * T2
    if total_var <= 0 or t_target <= 0:
        return sigma1
    return math.sqrt(total_var / t_target)


def interpolate_greeks(
    greeks1: dict, T1: float,
    greeks2: dict, T2: float,
    t_target: float,
) -> dict:
    """
    Interpolate Greeks from T1 → T2 at t_target using per-doc time-scaling rules:

      Delta  linear:     Δ = Δ1 + w*(Δ2 - Δ1)
      Gamma  1/√t:       γ·√T is constant → γ_t = normalise then scale
      Vega   √t:         ν/√T is constant → ν_t = normalise then scale
      Theta  1/√t:       θ·√T is constant → θ_t = normalise then scale
      Rho    linear:     same as delta
    """
    if T2 <= T1 or t_target <= 0:
        return greeks1

    w = max(0.0, min(1.0, (t_target - T1) / (T2 - T1)))

    def linear(k):
        v1 = greeks1.get(k) or 0.0
        v2 = greeks2.get(k) or 0.0
        return v1 + w * (v2 - v1)

    def scale_sqrt_up(k):
        """Vega: normalise by /√T then scale by √t_target."""
        v1 = greeks1.get(k) or 0.0
        v2 = greeks2.get(k) or 0.0
        n1 = v1 / math.sqrt(T1) if T1 > 0 else 0.0
        n2 = v2 / math.sqrt(T2) if T2 > 0 else 0.0
        return (n1 + w * (n2 - n1)) * math.sqrt(t_target)

    def scale_sqrt_down(k):
        """Gamma / Theta: normalise by *√T then scale by /√t_target."""
        v1 = greeks1.get(k) or 0.0
        v2 = greeks2.get(k) or 0.0
        n1 = v1 * math.sqrt(T1)
        n2 = v2 * math.sqrt(T2)
        return (n1 + w * (n2 - n1)) / math.sqrt(t_target) if t_target > 0 else 0.0

    return {
        "delta": linear("delta"),
        "gamma": scale_sqrt_down("gamma"),
        "vega":  scale_sqrt_up("vega"),
        "theta": scale_sqrt_down("theta"),
        "rho":   linear("rho"),
    }


def calculate_edge(
    deribit_prob: float,
    polymarket_price: float,
    min_edge_pct: float = 3.0,
) -> dict:
    """
    Edge between Deribit fair probability and Polymarket price.

    Leaderboard selection criteria (per doc):
      - abs_edge >= min_edge_pct
      - asymmetric_payout: Polymarket price < 0.50  →  payout > 2x
    """
    edge = deribit_prob - polymarket_price
    abs_edge = abs(edge)
    direction = "BUY" if edge > 0 else "SELL"
    asymmetric_payout = polymarket_price < 0.50    # payout > 2x per doc
    has_alpha = abs_edge >= (min_edge_pct / 100) and asymmetric_payout

    return {
        "deribit_prob":       round(deribit_prob, 4),
        "polymarket_price":   round(polymarket_price, 4),
        "edge_pct":           round(edge * 100, 2),
        "abs_edge_pct":       round(abs_edge * 100, 2),
        "direction":          direction,
        "payout_ratio":       round(1 / polymarket_price, 2) if polymarket_price > 0 else None,
        "asymmetric_payout":  asymmetric_payout,
        "has_alpha":          has_alpha,
    }


def build_reasoning(
    instrument_t1: str,
    instrument_t2: str,
    spot: float,
    strike: float,
    sigma_t1: float,
    sigma_t2: float,
    sigma_interp: float,
    T1_days: float,
    T2_days: float,
    t_poly_days: float,
    w: float,
    deribit_prob: float,
    polymarket_price: float,
    edge: dict,
) -> str:
    """Human-readable reasoning card — two-expiry interpolation detail."""
    direction   = edge["direction"]
    abs_edge    = edge["abs_edge_pct"]
    payout      = edge["payout_ratio"]

    action = (
        f"Deribit prices this at {deribit_prob*100:.2f}% but Polymarket offers {polymarket_price*100:.2f}%."
    )
    if direction == "BUY":
        recommendation = (
            f"BUY YES on Polymarket — {abs_edge:.1f}% below fair value. "
            f"Payout: {payout:.1f}x"
        )
    else:
        recommendation = (
            f"SELL YES on Polymarket — {abs_edge:.1f}% above fair value. "
            f"Payout: {payout:.1f}x"
        )

    return (
        f"T1: {instrument_t1} ({T1_days:.1f}d, IV={sigma_t1*100:.1f}%) | "
        f"T2: {instrument_t2} ({T2_days:.1f}d, IV={sigma_t2*100:.1f}%)\n"
        f"Interpolation weight w={w:.3f} → σ_interp={sigma_interp*100:.1f}% at {t_poly_days:.1f}d\n"
        f"Spot: ${spot:,.0f} | Strike: ${strike:,.0f}\n"
        f"{action}\n"
        f"→ {recommendation}"
    )
