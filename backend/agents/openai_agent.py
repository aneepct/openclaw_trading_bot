from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
from memory_store import get_agent_memory, set_agent_memory


class AgentTrade(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market: str
    action: str
    conviction: str
    edge_pct: float = 0.0
    rationale: str


class AgentSignalAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market_id: str = ""
    market: str
    action: str
    fair_value_pct: float = 0.0
    bias: str
    conviction: str
    trade_type: str
    rationale: str
    risk: str


class AgentSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    structural_insight: str = ""
    trades: list[AgentTrade] = Field(default_factory=list)
    signal_analyses: list[AgentSignalAnalysis] = Field(default_factory=list)


class AgentGeneratedSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    polymarket_market_id: str
    polymarket_question: str
    option_type: str = "C"
    direction: str
    action: str
    conviction: str
    has_alpha: bool = True
    deribit_prob: float = 0.0
    polymarket_price: float = 0.0
    # edge_pct and abs_edge_pct are NOT parsed from the LLM — they are always
    # computed by the scanner from (deribit_prob - polymarket_price) * 100.
    payout_ratio: float = 0.0
    liquidity_usd: float = 0.0
    reasoning: str
    structural_insight: str = ""
    rank_label: str = "pass"


class AgentScanPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    structural_insight: str = ""
    updated_summary: str = ""
    signals: list[AgentGeneratedSignal] = Field(default_factory=list)


def _ascii_clean(value: Any) -> str:
    text = str(value or "")
    text = text.replace("—", "-").replace("–", "-").replace("→", "to")
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clip_text(value: Any, limit: int) -> str:
    text = _ascii_clean(value)
    if len(text) <= limit:
        return text
    clipped = text[: max(limit - 3, 0)].rstrip(" ,.;:-")
    return f"{clipped}..." if clipped else ""


def _normalize_action(value: Any) -> str:
    text = _ascii_clean(value).upper()
    if "BUY NO" in text:
        return "BUY NO"
    if "BUY YES" in text:
        return "BUY YES"
    if text == "HOLD":
        return "HOLD"
    if "SELL" in text or text == "NO":
        return "BUY NO"
    if "BUY" in text or text == "YES":
        return "BUY YES"
    return "HOLD"


def _normalize_conviction(value: Any) -> str:
    text = _ascii_clean(value).lower()
    if "high" in text or "strong" in text:
        return "high"
    if "medium" in text or "moderate" in text:
        return "medium"
    return "low"


def _normalize_trade_type(value: Any) -> str:
    text = _ascii_clean(value).lower()
    allowed = {"mispricing", "momentum", "hedge", "range", "hold"}
    return text if text in allowed else "mispricing"


def _derived_action_from_probs(fair_value_pct: float, market_price_pct: float | None) -> str:
    if market_price_pct is None:
        return "HOLD"
    diff = fair_value_pct - market_price_pct
    if abs(diff) < 1.0:
        return "HOLD"
    return "BUY YES" if diff > 0 else "BUY NO"


def _normalize_payload_content(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "summary": _clip_text(payload.get("summary", ""), 220),
        "structural_insight": _clip_text(payload.get("structural_insight", ""), 120),
        "trades": [],
        "signal_analyses": [],
    }

    for trade in payload.get("trades", []):
        normalized["trades"].append(
            {
                "market": _ascii_clean(trade.get("market", "")),
                "action": _normalize_action(trade.get("action", "")),
                "conviction": _normalize_conviction(trade.get("conviction", "")),
                "edge_pct": float(trade.get("edge_pct") or 0.0),
                "rationale": _clip_text(trade.get("rationale", ""), 120),
            }
        )

    for signal in payload.get("signal_analyses", []):
        fair_value_pct = float(signal.get("fair_value_pct") or 0.0)
        fair_value_pct = max(0.0, min(100.0, fair_value_pct))
        normalized["signal_analyses"].append(
            {
                "market_id": _ascii_clean(signal.get("market_id", "")),
                "market": _ascii_clean(signal.get("market", "")),
                "action": _normalize_action(signal.get("action", "")),
                "fair_value_pct": fair_value_pct,
                "bias": _clip_text(signal.get("bias", ""), 80),
                "conviction": _normalize_conviction(signal.get("conviction", "")),
                "trade_type": _normalize_trade_type(signal.get("trade_type", "")),
                "rationale": _clip_text(signal.get("rationale", ""), 120),
                "risk": _clip_text(signal.get("risk", ""), 100),
            }
        )

    return normalized


def _enforce_actions_from_market(
    payload: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> dict[str, Any]:
    market_prices_by_id = {
        _ascii_clean(item.get("polymarket_market_id", "")): float(item.get("polymarket_price") or 0.0) * 100
        for item in ranked
        if item.get("polymarket_market_id") is not None
    }
    market_prices_by_question = {
        _ascii_clean(item.get("polymarket_question", "")): float(item.get("polymarket_price") or 0.0) * 100
        for item in ranked
        if item.get("polymarket_question")
    }

    for signal in payload.get("signal_analyses", []):
        market_id = _ascii_clean(signal.get("market_id", ""))
        market_name = _ascii_clean(signal.get("market", ""))
        market_price_pct = market_prices_by_id.get(market_id)
        if market_price_pct is None:
            market_price_pct = market_prices_by_question.get(market_name)
        # Only override when we have a real market price to compare against.
        # If market_price_pct is None (ranked was empty or lookup missed),
        # keep the existing action set by the LLM or _default_signal_analysis.
        if market_price_pct is not None:
            signal["action"] = _derived_action_from_probs(
                float(signal.get("fair_value_pct") or 0.0),
                market_price_pct,
            )

    trade_actions_by_market = {
        _ascii_clean(signal.get("market", "")): signal.get("action", "HOLD")
        for signal in payload.get("signal_analyses", [])
        if signal.get("market")
    }
    for trade in payload.get("trades", []):
        market_name = _ascii_clean(trade.get("market", ""))
        if market_name in trade_actions_by_market:
            trade["action"] = trade_actions_by_market[market_name]

    return payload


def _default_trade(signal: dict[str, Any]) -> dict[str, Any]:
    action = "BUY YES" if signal.get("direction") == "BUY" else "BUY NO"
    edge_pct = float(signal.get("abs_edge_pct") or 0.0)
    if edge_pct >= 10:
        conviction = "high"
    elif edge_pct >= 5:
        conviction = "medium"
    else:
        conviction = "low"
    return {
        "market": signal.get("polymarket_question"),
        "action": action,
        "conviction": conviction,
        "edge_pct": edge_pct,
        "rationale": (
            f"Scanner sees {edge_pct:.1f}% edge with Deribit at "
            f"{float(signal.get('deribit_prob') or 0.0) * 100:.1f}% vs Polymarket at "
            f"{float(signal.get('polymarket_price') or 0.0) * 100:.1f}%."
        ),
    }


def _default_signal_analysis(signal: dict[str, Any]) -> dict[str, Any]:
    deribit_prob = float(signal.get("deribit_prob") or 0.0)
    poly_price = float(signal.get("polymarket_price") or 0.0)
    edge_pct = float(signal.get("abs_edge_pct") or 0.0)
    fair_value_pct = round(deribit_prob * 100, 2)
    # Derive action directly from probabilities — never guess HOLD on large edges
    action = _derived_action_from_probs(fair_value_pct, poly_price * 100)
    return {
        "market_id": signal.get("polymarket_market_id", ""),  # required for ID-based lookup
        "market": signal.get("polymarket_question"),
        "action": action,
        "fair_value_pct": fair_value_pct,
        "bias": "bullish" if signal.get("option_type") == "C" else "bearish",
        "conviction": "high" if edge_pct >= 10 else "medium" if edge_pct >= 5 else "low",
        "trade_type": "mispricing",
        "rationale": (
            f"Deribit implies {deribit_prob * 100:.1f}% while "
            f"Polymarket is at {poly_price * 100:.1f}%."
        ),
        "risk": (
            f"Interpolation method {signal.get('interp_method') or 'n/a'} with "
            f"liquidity ${float(signal.get('liquidity_usd') or 0.0):,.0f}."
        ),
    }


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _compact_signal(signal: dict[str, Any]) -> dict[str, Any]:
    deribit_prob = float(signal.get("deribit_prob") or 0.0)
    poly_price = float(signal.get("polymarket_price") or 0.0)
    abs_edge = float(signal.get("abs_edge_pct") or 0.0)
    # Pre-compute scanner-derived action so the LLM has a ground-truth reference
    scanner_action = _derived_action_from_probs(round(deribit_prob * 100, 2), round(poly_price * 100, 2))
    return {
        "market_id": signal.get("polymarket_market_id"),
        "market": signal.get("polymarket_question"),
        "spot_price": signal.get("spot_price"),
        "strike": signal.get("strike"),
        "days_to_expiry": signal.get("t_poly_days"),
        "option_type": signal.get("option_type"),        # C = call (P(S>K)), P = put
        "deribit_prob_pct": round(deribit_prob * 100, 2),   # ground-truth fair value %
        "polymarket_price_pct": round(poly_price * 100, 2), # retail market price %
        # edge_pct is NOT included — scanner computes it; LLM must not produce it
        "scanner_action": scanner_action,   # what pure math says: BUY YES / BUY NO / HOLD
        "scanner_abs_edge_pct": abs_edge,   # provided for context only, not to be echoed back
        "payout_ratio": signal.get("payout_ratio"),
        "liquidity_usd": signal.get("liquidity_usd"),
        "interp_method": signal.get("interp_method"),
        "sigma_interp": signal.get("sigma_interp"),       # implied vol at interpolated point
        "T1_days": signal.get("T1_days"),
        "T2_days": signal.get("T2_days"),
        "scanner_reasoning": signal.get("reasoning"),
    }


def _market_context(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_id": candidate.get("polymarket_market_id"),
        "polymarket_market_id": candidate.get("polymarket_market_id"),
        "polymarket_question": candidate.get("polymarket_question"),
        "option_type": candidate.get("option_type"),
        "spot_price": candidate.get("spot_price"),
        "strike": candidate.get("strike"),
        "t_poly_days": candidate.get("t_poly_days"),
        "polymarket_price": candidate.get("polymarket_price"),
        "deribit_prob": candidate.get("deribit_prob"),
        "edge_pct": candidate.get("edge_pct"),
        "liquidity_usd": candidate.get("liquidity_usd"),
        "instrument_t1": candidate.get("instrument_t1"),
        "instrument_t2": candidate.get("instrument_t2"),
        "T1_days": candidate.get("T1_days"),
        "T2_days": candidate.get("T2_days"),
        "t1_book": candidate.get("t1_book"),
        "t2_book": candidate.get("t2_book"),
    }


async def _call_openai_json(user_prompt: str) -> tuple[str, dict[str, Any] | None]:
    request_payload = {
        "model": config.OPENAI_MODEL,
        "reasoning": {"effort": config.OPENAI_REASONING_EFFORT},
        "instructions": config.OPENAI_SYSTEM_PROMPT,
        "input": user_prompt,
    }

    max_retries = 3
    base_delay = 5.0  # seconds

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(max_retries + 1):
            response = await client.post(
                f"{config.OPENAI_BASE_URL}/responses",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            if response.status_code == 429:
                if attempt >= max_retries:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
                print(f"[OpenAI] 429 rate limit — retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            break

        payload = response.json()

    raw_text = _extract_output_text(payload)
    try:
        return raw_text, json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text, None


def _scanner_fallback_signals(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build signals directly from scanner-computed deribit_prob without calling OpenAI."""
    signals = []
    for candidate in candidates:
        poly_price = float(candidate.get("polymarket_price") or 0.0)
        dp = candidate.get("deribit_prob")
        deribit_prob = float(dp) if dp is not None else 0.5
        direction = "BUY" if deribit_prob > poly_price else "SELL"
        edge_pct = round((deribit_prob - poly_price) * 100, 2)
        abs_edge_pct = abs(edge_pct)
        signals.append({
            **candidate,
            "direction": direction,
            "recommended_action": "BUY YES" if direction == "BUY" else "BUY NO",
            "deribit_prob": round(deribit_prob, 4),
            "edge_pct": edge_pct,
            "abs_edge_pct": round(abs_edge_pct, 2),
            "payout_ratio": round(1 / poly_price, 2) if poly_price > 0 else None,
            "has_alpha": abs_edge_pct >= config.MIN_EDGE_PCT,
            "asymmetric_payout": poly_price < 0.5,
            "reasoning": f"Deribit delta implies {deribit_prob*100:.1f}% vs Polymarket {poly_price*100:.1f}%. Edge: {edge_pct:+.1f}%.",
            "structural_insight": "",
            "rank_label": "***" if abs_edge_pct >= 10 else "**" if abs_edge_pct >= 5 else "*" if abs_edge_pct >= 2 else "pass",
            "agent_summary": "",
            "agent_structural_insight": "",
        })
    return signals


async def build_agent_signals(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates:
        return [], {
            "summary": "No eligible market contexts were available for the agent.",
            "structural_insight": "",
            "updated_summary": "",
            "source": "scanner",
        }

    if not config.AGENT_ENABLED:
        return _scanner_fallback_signals(candidates), {
            "summary": "Agent disabled — showing Deribit delta probabilities directly.",
            "structural_insight": "",
            "updated_summary": "",
            "source": "scanner",
        }

    if not config.OPENAI_API_KEY:
        return _scanner_fallback_signals(candidates), {
            "summary": "OpenAI key not set — showing Deribit delta probabilities directly.",
            "structural_insight": "",
            "updated_summary": "",
            "source": "scanner",
        }

    history_json = await get_agent_memory("history", "[]")
    rolling_summary = await get_agent_memory("rolling_summary", "")
    user_prompt = (
        "You are generating the live matrix from market context only. "
        "Return strict JSON with keys: summary, structural_insight, updated_summary, signals. "
        "`signals` must be an array covering ALL provided market contexts. Each signal object must include: "
        "polymarket_market_id, polymarket_question, option_type, direction, action, conviction, "
        "has_alpha, deribit_prob, polymarket_price, payout_ratio, "
        "liquidity_usd, reasoning, structural_insight, rank_label. "
        "Do NOT include edge_pct or abs_edge_pct — these are computed by the scanner from the formula "
        "(deribit_prob - polymarket_price) * 100 and will always override any value you provide. "
        "Use BUY when the right trade is BUY YES and SELL when the right trade is BUY NO. "
        "Only output dashboard-ready JSON.\n\n"
        f"Rolling summary:\n{rolling_summary or 'None'}\n\n"
        f"Recent history:\n{history_json}\n\n"
        f"Market contexts:\n{json.dumps([_market_context(c) for c in candidates], indent=2)}"
    )
    try:
        raw_text, parsed = await _call_openai_json(user_prompt)
    except Exception as exc:
        print(f"[OpenAI] Signal generation failed ({exc}), using scanner fallback")
        parsed = None
        raw_text = str(exc)

    if not parsed:
        # Fall back to scanner-computed values when OpenAI is unavailable
        fallback_signals = _scanner_fallback_signals(candidates)
        return fallback_signals, {
            "summary": f"OpenAI unavailable ({raw_text[:120]}). Showing scanner-computed probabilities.",
            "structural_insight": "",
            "updated_summary": rolling_summary,
            "source": "scanner",
        }

    try:
        payload = AgentScanPayload.model_validate(parsed)
    except ValidationError:
        fallback_signals = _scanner_fallback_signals(candidates)
        return fallback_signals, {
            "summary": raw_text or "OpenAI returned an invalid signal schema. Showing scanner-computed probabilities.",
            "structural_insight": "",
            "updated_summary": rolling_summary,
            "source": "scanner",
        }

    by_id = {c.get("polymarket_market_id"): c for c in candidates}
    normalized_signals: list[dict[str, Any]] = []
    for generated in payload.signals:
        base = by_id.get(generated.polymarket_market_id)
        if not base:
            continue
        # Always use scanner-computed deribit_prob — it is the math-correct value from
        # Deribit delta (and optional T1/T2 interpolation). The LLM value is ignored to
        # prevent hallucinated probabilities from corrupting edge calculations.
        deribit_prob = float(base.get("deribit_prob") or 0.0)

        # Polymarket price: prefer scanner base value; use agent value only as fallback.
        poly_price = float(base.get("polymarket_price") or 0.0) or float(generated.polymarket_price)
        # Always recompute edge from the authoritative probs — never accept agent's edge_pct.
        edge_pct = round((deribit_prob - poly_price) * 100, 2)
        abs_edge_pct = round(abs(edge_pct), 2)

        normalized_signals.append(
            {
                **base,
                "direction": generated.direction,
                "recommended_action": generated.action,
                "conviction": generated.conviction,
                "has_alpha": generated.has_alpha or abs_edge_pct >= config.MIN_EDGE_PCT,
                "deribit_prob": round(deribit_prob, 4),
                "polymarket_price": round(poly_price, 4),
                "edge_pct": round(edge_pct, 2),
                "abs_edge_pct": round(abs_edge_pct, 2),
                "payout_ratio": round(float(generated.payout_ratio), 2) if generated.payout_ratio else None,
                "liquidity_usd": round(float(generated.liquidity_usd), 2),
                "reasoning": generated.reasoning,
                "structural_insight": generated.structural_insight,
                "rank_label": generated.rank_label,
                "asymmetric_payout": poly_price < 0.5,
                "agent_summary": payload.summary,
                "agent_structural_insight": payload.structural_insight,
            }
        )

    await set_agent_memory("rolling_summary", payload.updated_summary or payload.summary)
    history = json.loads(history_json) if history_json else []
    history.append(
        {
            "timestamp": datetime.utcnow().isoformat(),
            "summary": payload.summary,
            "top_markets": [s.get("polymarket_question") for s in normalized_signals[:5]],
        }
    )
    history = history[-8:]
    await set_agent_memory("history", json.dumps(history))

    return normalized_signals, {
        "summary": payload.summary,
        "structural_insight": payload.structural_insight,
        "updated_summary": payload.updated_summary,
        "source": "openai",
    }


def _enforce_scanner_edge_in_trades(
    normalized: dict[str, Any],
    trade_hints: list[dict[str, Any]],
) -> None:
    """Replace trade edge_pct values with scanner-computed ones from trade_hints."""
    scanner_edge_by_market = {
        _ascii_clean(hint.get("market", "")): hint.get("edge_pct", 0.0)
        for hint in trade_hints
        if hint.get("market")
    }
    for trade in normalized.get("trades", []):
        market_name = _ascii_clean(trade.get("market", ""))
        if market_name in scanner_edge_by_market:
            trade["edge_pct"] = scanner_edge_by_market[market_name]


def _normalize_payload(
    parsed: dict[str, Any] | None,
    *,
    trade_hints: list[dict[str, Any]],
    signal_analyses: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    fallback_summary: str = "",
    fallback_structural_insight: str = "",
) -> dict[str, Any]:
    try:
        payload = AgentSummaryPayload.model_validate(
            {
                "summary": (parsed or {}).get("summary", fallback_summary),
                "structural_insight": (parsed or {}).get("structural_insight", fallback_structural_insight),
                "trades": (parsed or {}).get("trades", trade_hints) or trade_hints,
                "signal_analyses": (parsed or {}).get("signal_analyses", signal_analyses) or signal_analyses,
            }
        )
    except ValidationError:
        payload = AgentSummaryPayload.model_validate(
            {
                "summary": fallback_summary,
                "structural_insight": fallback_structural_insight,
                "trades": trade_hints,
                "signal_analyses": signal_analyses,
            }
        )
    normalized = _normalize_payload_content(payload.model_dump())
    normalized = _enforce_actions_from_market(normalized, ranked)
    # Always overwrite trade edge_pct with scanner-computed values — never trust LLM.
    _enforce_scanner_edge_in_trades(normalized, trade_hints)
    return normalized


def _provider_stub(
    provider: str,
    *,
    enabled: bool,
    model: str | None,
    status: str,
    summary: str,
    trade_hints: list[dict[str, Any]],
    signal_analyses: list[dict[str, Any]],
    ranked: list[dict[str, Any]] | None = None,
    structural_insight: str = "",
    raw_text: str = "",
) -> dict[str, Any]:
    return {
        "provider": provider,
        "enabled": enabled,
        "source": provider,
        "model": model,
        "status": status,
        "schema_version": "2",
        **_normalize_payload(
            None,
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
            ranked=ranked or [],  # pass through so action enforcement has market prices
            fallback_summary=summary,
            fallback_structural_insight=structural_insight,
        ),
        "raw_text": raw_text,
    }


def _summary_prompt(ranked: list[dict[str, Any]]) -> str:
    return (
        "You are a professional derivatives trader reviewing Deribit vs Polymarket mispricing signals.\n"
        "Return strict JSON with keys: summary, structural_insight, trades, signal_analyses.\n"
        "Use professional tone, plain ASCII only. No emojis, stars, or slang.\n\n"
        "ACTION RULES — follow exactly, no exceptions:\n"
        "  - scanner_action is the mathematically correct action derived from deribit_prob_pct vs polymarket_price_pct.\n"
        "  - Your action MUST agree with scanner_action unless you have a specific structural reason to override.\n"
        "  - abs_edge_pct >= 20: action = scanner_action, conviction = high (large mispricing, act on it).\n"
        "  - abs_edge_pct 5-19: action = scanner_action, conviction = medium.\n"
        "  - abs_edge_pct < 5: use judgment, may HOLD if liquidity or interp quality is poor.\n"
        "  - HOLD is only valid when abs_edge_pct < 5 OR liquidity_usd < 2000 OR interp_method is T2-only with poor fit.\n\n"
        "FIELD RULES:\n"
        "  - market_id: copy EXACTLY from input market_id field.\n"
        "  - market: copy EXACTLY from input market field.\n"
        "  - fair_value_pct: use deribit_prob_pct from input (0-100 scale).\n"
        "  - action: one of BUY YES, BUY NO, HOLD.\n"
        "  - conviction: one of low, medium, high.\n"
        "  - trade_type: one of mispricing, momentum, hedge, range, hold.\n"
        "  - summary: under 220 chars. structural_insight: under 120 chars.\n"
        "  - rationale: under 120 chars. bias: under 80 chars. risk: under 100 chars.\n\n"
        f"Signals (sorted by edge, highest first):\n{json.dumps([_compact_signal(s) for s in ranked], indent=2)}"
    )


async def _call_chat_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, dict[str, Any] | None]:
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    # Retry on 429 (rate limit) and 503/502/504 (transient server errors).
    # Backoff is kept short so the frontend request stays within ~90s total.
    # Retry-After header is respected when the provider sends one.
    _RETRYABLE = {429, 502, 503, 504}
    _BACKOFF = [5, 15, 30]   # 3 retries: 5s → 15s → 30s  (≤ ~55s extra wait)
    max_retries = len(_BACKOFF) + 1  # 4 attempts total
    payload: dict[str, Any] = {}
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            if response.status_code in _RETRYABLE and attempt < max_retries - 1:
                # Respect Retry-After / x-ratelimit-reset-requests when present
                retry_after = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset-requests")
                try:
                    wait = float(retry_after) if retry_after else _BACKOFF[attempt]
                except (TypeError, ValueError):
                    wait = _BACKOFF[attempt]
                wait = min(wait, 30.0)  # cap per-wait at 30s to stay UI-responsive
                print(f"[{model}] HTTP {response.status_code}, retrying in {wait:.0f}s (attempt {attempt + 1}/{max_retries - 1})...")
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            payload = response.json()
            break

    raw_text = (
        (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
        or ""
    ).strip()
    try:
        return raw_text, json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text, None


async def _build_provider_summary(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    ranked: list[dict[str, Any]],
    trade_hints: list[dict[str, Any]],
    signal_analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    if not ranked:
        return _provider_stub(
            provider,
            enabled=False,
            model=model if api_key else None,
            status="no_signals",
            summary="No current alpha signals are available for this provider review.",
            trade_hints=[],
            signal_analyses=[],
        )

    if not api_key:
        return _provider_stub(
            provider,
            enabled=False,
            model=None,
            status="missing_api_key",
            summary=f"{provider.upper()} is not configured for backend summaries.",
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
            ranked=ranked,  # preserve market prices for action enforcement
        )

    _provider_prompt_map = {
        "openai": config.OPENAI_SYSTEM_PROMPT,
        "gemini": config.GEMINI_SYSTEM_PROMPT,
        "grok":   config.GROK_SYSTEM_PROMPT,
    }
    try:
        raw_text, parsed = await _call_chat_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=_provider_prompt_map.get(provider, config.AGENT_SYSTEM_PROMPT),
            user_prompt=_summary_prompt(ranked),
        )
    except Exception as exc:
        return _provider_stub(
            provider,
            enabled=False,
            model=model,
            status="error",
            summary=f"{provider.upper()} summary request failed: {exc}",
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
            ranked=ranked,  # preserve market prices for action enforcement
        )

    normalized = _normalize_payload(
        parsed,
        trade_hints=trade_hints,
        signal_analyses=signal_analyses,
        ranked=ranked,
        fallback_summary=raw_text or f"{provider.upper()} returned an empty response.",
    )

    # Remap `market` to original question using market_id so frontend matching is reliable
    id_to_question = {
        s.get("polymarket_market_id"): s.get("polymarket_question")
        for s in ranked
        if s.get("polymarket_market_id")
    }
    for sa in normalized.get("signal_analyses", []):
        mid = sa.get("market_id", "")
        if mid and mid in id_to_question:
            sa["market"] = id_to_question[mid]

    return {
        "provider": provider,
        "enabled": True,
        "source": provider,
        "model": model,
        "status": "ok",
        "schema_version": "2",
        **normalized,
        "raw_text": raw_text,
    }


async def build_agent_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        [s for s in signals if s.get("has_alpha")],
        key=lambda item: float(item.get("abs_edge_pct") or 0.0),
        reverse=True,
    )

    if not ranked:
        providers = {
            "openai": _provider_stub(
                "openai",
                enabled=False,
                model=config.OPENAI_MODEL if config.OPENAI_API_KEY else None,
                status="no_signals",
                summary="No alpha signals are available for this provider yet.",
                trade_hints=[],
                signal_analyses=[],
            ),
            "grok": _provider_stub(
                "grok",
                enabled=False,
                model=config.GROK_MODEL if config.GROK_API_KEY else None,
                status="no_signals",
                summary="No alpha signals are available for Grok yet.",
                trade_hints=[],
                signal_analyses=[],
            ),
            "gemini": _provider_stub(
                "gemini",
                enabled=False,
                model=config.GEMINI_MODEL if config.GEMINI_API_KEY else None,
                status="no_signals",
                summary="No alpha signals are available for Gemini yet.",
                trade_hints=[],
                signal_analyses=[],
            ),
        }
        return {
            "enabled": False,
            "source": "scanner",
            "model": None,
            "schema_version": "3",
            "providers": providers,
            "preferred_provider": "openai",
            "summary": "No alpha signals are available for AI review yet.",
            "structural_insight": "",
            "trades": [],
            "signal_analyses": [],
        }

    trade_hints = [_default_trade(signal) for signal in ranked]
    signal_analyses = [_default_signal_analysis(signal) for signal in ranked]

    provider_results = await asyncio.gather(
        _build_provider_summary(
            provider="openai",
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            model=config.OPENAI_MODEL,
            ranked=ranked,
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
        ),
        _build_provider_summary(
            provider="grok",
            api_key=config.GROK_API_KEY,
            base_url=config.GROK_BASE_URL,
            model=config.GROK_MODEL,
            ranked=ranked,
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
        ),
        _build_provider_summary(
            provider="gemini",
            api_key=config.GEMINI_API_KEY,
            base_url=config.GEMINI_BASE_URL,
            model=config.GEMINI_MODEL,
            ranked=ranked,
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
        ),
    )
    providers = {item["provider"]: item for item in provider_results}

    # Use first WORKING provider as primary (Grok is fallback when OpenAI rate-limits)
    _preferred_order = ["openai", "grok", "gemini"]
    primary = next(
        (providers[p] for p in _preferred_order if providers.get(p, {}).get("enabled")),
        providers["openai"],  # last resort: use stub even if disabled
    )
    preferred_name = primary.get("provider", "openai")

    # Build per-signal consensus across all providers
    # A signal has consensus when 2+ providers agree on the same action
    vote_map: dict[str, list[str]] = {}
    for prov_data in providers.values():
        for sa in prov_data.get("signal_analyses", []):
            key = sa.get("market_id") or sa.get("market", "")
            if key:
                vote_map.setdefault(key, []).append(sa.get("action", "HOLD"))
    consensus: dict[str, dict[str, Any]] = {}
    for key, votes in vote_map.items():
        counts: dict[str, int] = {}
        for v in votes:
            counts[v] = counts.get(v, 0) + 1
        top_action = max(counts, key=lambda a: counts[a])
        consensus[key] = {
            "action": top_action,
            "agreement": counts[top_action],
            "total": len(votes),
            "strong": counts[top_action] >= 2,  # 2+ of 3 providers agree
        }

    return {
        "enabled": any(item.get("enabled") for item in providers.values()),
        "source": "comparison",
        "model": primary.get("model"),
        "schema_version": "3",
        "providers": providers,
        "preferred_provider": preferred_name,
        "consensus": consensus,
        "summary": primary.get("summary", ""),
        "structural_insight": primary.get("structural_insight", ""),
        "trades": primary.get("trades", []),
        "signal_analyses": primary.get("signal_analyses", []),
    }
