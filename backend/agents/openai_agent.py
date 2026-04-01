import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config


class AgentTrade(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market: str
    action: str
    conviction: str
    edge_pct: float = 0.0
    rationale: str


class AgentSignalAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market: str
    action: str
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
    return {
        "market": signal.get("polymarket_question"),
        "action": action,
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


def _normalize_payload(
    parsed: dict[str, Any] | None,
    *,
    trade_hints: list[dict[str, Any]],
    signal_analyses: list[dict[str, Any]],
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
    return payload.model_dump()


async def build_agent_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        [s for s in signals if s.get("has_alpha")],
        key=lambda item: float(item.get("abs_edge_pct") or 0.0),
        reverse=True,
    )[: config.AGENT_TOP_N_SIGNALS]

    if not ranked:
        return {
            "enabled": False,
            "source": "scanner",
            "model": None,
            "schema_version": "2",
            "summary": "No alpha signals are available for the agent yet.",
            "structural_insight": "",
            "trades": [],
            "signal_analyses": [],
        }

    trade_hints = [_default_trade(signal) for signal in ranked]
    signal_analyses = [_default_signal_analysis(signal) for signal in ranked]

    if not config.OPENAI_API_KEY:
        lines = [
            "OpenAI agent is disabled because `OPENAI_API_KEY` is not set for the backend.",
            "Top scanner trades:",
        ]
        lines.extend(
            f"- {hint['action']}: {hint['market']} ({hint['edge_pct']:.1f}% edge)"
            for hint in trade_hints
        )
        return {
            "enabled": False,
            "source": "scanner",
            "model": None,
            "schema_version": "2",
            **_normalize_payload(
                None,
                trade_hints=trade_hints,
                signal_analyses=signal_analyses,
                fallback_summary="\n".join(lines),
            ),
        }

    user_prompt = (
        "Summarize the highest-conviction Open Claw signals for the frontend. "
        "Return strict JSON with keys: summary, structural_insight, trades, signal_analyses. "
        "`summary` should be a short paragraph. `structural_insight` should be one "
        "sentence. `trades` should be an array of up to 5 objects with keys: market, "
        "action, conviction, edge_pct, rationale. `signal_analyses` should be an array "
        "with one object per signal using keys: market, action, bias, conviction, "
        "trade_type, rationale, risk. Keep rationale and risk under 160 characters.\n\n"
        f"Signals JSON:\n{json.dumps([_compact_signal(s) for s in ranked], indent=2)}"
    )

    request_payload = {
        "model": config.OPENAI_MODEL,
        "reasoning": {"effort": config.OPENAI_REASONING_EFFORT},
        "instructions": config.AGENT_SYSTEM_PROMPT,
        "input": user_prompt,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
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
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "enabled": True,
            "source": "openai",
            "model": config.OPENAI_MODEL,
            "schema_version": "2",
            **_normalize_payload(
                None,
                trade_hints=trade_hints,
                signal_analyses=signal_analyses,
                fallback_summary=raw_text or "OpenAI returned an empty response.",
            ),
            "raw_text": raw_text,
        }

    return {
        "enabled": True,
        "source": "openai",
        "model": config.OPENAI_MODEL,
        "schema_version": "2",
        **_normalize_payload(
            parsed,
            trade_hints=trade_hints,
            signal_analyses=signal_analyses,
            fallback_summary=raw_text or "",
        ),
        "raw_text": raw_text,
    }
