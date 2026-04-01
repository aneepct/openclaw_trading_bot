import asyncio
import json
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
from db.database import get_agent_memory, set_agent_memory


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
    edge_pct: float = 0.0
    abs_edge_pct: float = 0.0
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
    action = "BUY YES" if signal.get("direction") == "BUY" else "BUY NO"
    edge_pct = float(signal.get("abs_edge_pct") or 0.0)
    fair_value_pct = round(float(signal.get("deribit_prob") or 0.0) * 100, 2)
    return {
        "market": signal.get("polymarket_question"),
        "action": action,
        "fair_value_pct": fair_value_pct,
        "bias": "bullish" if signal.get("option_type") == "C" else "bearish",
        "conviction": "high" if edge_pct >= 10 else "medium" if edge_pct >= 5 else "low",
        "trade_type": "mispricing",
        "rationale": (
            f"Deribit implies {float(signal.get('deribit_prob') or 0.0) * 100:.1f}% while "
            f"Polymarket is at {float(signal.get('polymarket_price') or 0.0) * 100:.1f}%."
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
    return {
        "market_id": signal.get("polymarket_market_id"),
        "market": signal.get("polymarket_question"),
        "direction": signal.get("direction"),
        "option_type": signal.get("option_type"),
        "deribit_prob": signal.get("deribit_prob"),
        "polymarket_price": signal.get("polymarket_price"),
        "edge_pct": signal.get("edge_pct"),
        "abs_edge_pct": signal.get("abs_edge_pct"),
        "payout_ratio": signal.get("payout_ratio"),
        "liquidity_usd": signal.get("liquidity_usd"),
        "instrument_t1": signal.get("instrument_t1"),
        "instrument_t2": signal.get("instrument_t2"),
        "interp_method": signal.get("interp_method"),
        "interp_weight_w": signal.get("interp_weight_w"),
        "sigma_interp": signal.get("sigma_interp"),
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
        "instructions": config.AGENT_SYSTEM_PROMPT,
        "input": user_prompt,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{config.OPENAI_BASE_URL}/responses",
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        )
        response.raise_for_status()
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
        deribit_prob = float(candidate.get("deribit_prob") or 0.5)
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
        "has_alpha, deribit_prob, polymarket_price, edge_pct, abs_edge_pct, payout_ratio, "
        "liquidity_usd, reasoning, structural_insight, rank_label. "
        "Use the provided deribit_prob and edge_pct as your starting point; refine if needed. "
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
        # Use scanner-computed deribit_prob as fallback when agent returns 0
        scanner_deribit_prob = float(base.get("deribit_prob") or 0.0)
        agent_deribit_prob = float(generated.deribit_prob)
        deribit_prob = agent_deribit_prob if agent_deribit_prob > 0 else scanner_deribit_prob

        poly_price = float(generated.polymarket_price) or float(base.get("polymarket_price") or 0.0)
        edge_pct = float(generated.edge_pct) if generated.edge_pct else round((deribit_prob - poly_price) * 100, 2)
        abs_edge_pct = float(generated.abs_edge_pct) if generated.abs_edge_pct else abs(edge_pct)

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
    return _enforce_actions_from_market(normalized, ranked)


def _provider_stub(
    provider: str,
    *,
    enabled: bool,
    model: str | None,
    status: str,
    summary: str,
    trade_hints: list[dict[str, Any]],
    signal_analyses: list[dict[str, Any]],
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
            ranked=[],
            fallback_summary=summary,
            fallback_structural_insight=structural_insight,
        ),
        "raw_text": raw_text,
    }


def _summary_prompt(ranked: list[dict[str, Any]]) -> str:
    return (
        "Summarize the Open Claw signals for the frontend. "
        "Return strict JSON with keys: summary, structural_insight, trades, signal_analyses. "
        "Use a professional tone and plain ASCII only. "
        "`summary` must be a short paragraph under 220 characters. `structural_insight` must be one "
        "sentence under 120 characters. "
        "`conviction` must be one of low, medium, high. "
        "sentence. `trades` should be an array of objects with keys: market, "
        "action, conviction, edge_pct, rationale. `signal_analyses` should be an array "
        "with one object per signal using keys: market_id, market, action, fair_value_pct, bias, conviction, "
        "trade_type, rationale, risk. "
        "IMPORTANT: `market_id` must be copied EXACTLY from the input market_id field. "
        "`market` must be copied EXACTLY from the input market field. "
        "`fair_value_pct` must be a numeric probability from 0 to 100 (e.g. 35.5 means 35.5%). "
        "`action` must be one of BUY YES, BUY NO, HOLD. "
        "`trade_type` must be one of mispricing, momentum, hedge, range, hold. "
        "Keep trade rationale under 120 characters, bias under 80 characters, signal rationale under 120 characters, and risk under 100 characters. "
        "Do not use stars, arrows, emojis, or slang.\n\n"
        f"Signals JSON:\n{json.dumps([_compact_signal(s) for s in ranked], indent=2)}"
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
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        )
        response.raise_for_status()
        payload = response.json()

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
        )

    try:
        raw_text, parsed = await _call_chat_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=config.AGENT_SYSTEM_PROMPT,
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
    primary = providers["openai"]
    return {
        "enabled": any(item.get("enabled") for item in providers.values()),
        "source": "comparison",
        "model": primary.get("model"),
        "schema_version": "3",
        "providers": providers,
        "preferred_provider": "openai",
        "summary": primary.get("summary", ""),
        "structural_insight": primary.get("structural_insight", ""),
        "trades": primary.get("trades", []),
        "signal_analyses": primary.get("signal_analyses", []),
    }
