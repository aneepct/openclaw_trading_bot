import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from agents.openai_agent import build_agent_summary
import memory_store as db_module
from memory_store import get_leaderboard, get_recent_signals, init_db
import engine.scanner as scanner_module
from engine.scanner import scan_once, ticker_loop, _scan_lock
from csv_signals import get_latest_signals as get_csv_pipeline_signals
import config as app_config
from config import SPEC_CLIENT, SPEC_VERSION, PROJECT_SLUG, PROJECT_DISPLAY_NAME
from csv_refresh import csv_refresh_loop, export_all_csvs, make_default_cfg

_PROMPT_FILE = Path(__file__).parent / "prompts" / "openclaw_system_prompt.txt"


class SystemPromptPayload(BaseModel):
    prompt: str


def get_latest_signals():
    """
    Prefer live scanner signals (uses event-slug Polymarket fetch → exact daily markets).
    Falls back to CSV pipeline if scanner has not completed its first run yet.
    """
    out = scanner_module.get_latest_signals()
    if out:
        return out
    return get_csv_pipeline_signals()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cfg = make_default_cfg()
    csv_task = asyncio.create_task(csv_refresh_loop(cfg=cfg))
    scanner_task = asyncio.create_task(ticker_loop())
    yield
    for task in (csv_task, scanner_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Open Claw — Scanner API",
    description=(
        "Open Claw v2.6: AI-driven Deribit vs Polymarket analysis, "
        "live matrix, reasoning, and 24h alpha leaderboard."
    ),
    version=SPEC_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "running",
        "project": PROJECT_DISPLAY_NAME,
        "repository_slug": PROJECT_SLUG,
        "service": "open-claw-scanner",
        "spec_version": SPEC_VERSION,
        "client": SPEC_CLIENT,
        "role": "interpolated_agent_data_plane",
    }


@app.get("/spec")
async def spec_meta():
    """Maps implementation to the published agentic trading specification."""
    return {
        "spec_version": SPEC_VERSION,
        "client": SPEC_CLIENT,
        "mission": "Use AI providers to analyze Deribit and Polymarket market context and rank opportunities",
        "data_sources": {
            "deribit": "public/get_order_book (+ instruments, index); CSV snapshots for matrix",
            "polymarket": "gamma markets API; CSV snapshot (UTC today) for matrix",
            "live_matrix_pipeline": "Deribit + Polymarket CSV → build_agent_signals (LLM) → GET /matrix",
        },
        "ui_surfaces": {
            "live_matrix": "GET /matrix",
            "reasoning": "field `reasoning` on each signal",
            "ticker": "GET /ticker",
            "leaderboard_24h": "GET /leaderboard",
        },
    }


@app.get("/matrix")
async def get_matrix():
    """
    Returns the latest live matrix of alpha signals.
    These are signals where |edge| >= 3%.
    """
    try:
        signals = [s for s in get_latest_signals() if s.get("has_alpha")]
        signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)
        return {
            "signals": signals,
            "total": len(signals),
            "scanned_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.exception("Error in /matrix")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/leaderboard")
async def get_leaderboard_route(hours: int = 24, top: int = 5):
    """
    Returns top N signals by edge magnitude in the last N hours.
    """
    if hours < 1 or hours > 168:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")
    if top < 1 or top > 20:
        raise HTTPException(status_code=400, detail="top must be between 1 and 20")

    try:
        # Live scanner snapshot; `hours` is informational (no historical DB).
        signals = [s for s in get_latest_signals() if s.get("has_alpha")]
        signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)
        ranked = []
        for i, s in enumerate(signals[:top], start=1):
            ranked.append({
                "rank": i,
                "instrument_t1": s.get("instrument_t1"),
                "instrument_t2": s.get("instrument_t2"),
                "option_type": s.get("option_type"),
                "polymarket_question": s.get("polymarket_question"),
                "polymarket_market_id": s.get("polymarket_market_id"),
                "deribit_prob": s.get("deribit_prob"),
                "polymarket_price": s.get("polymarket_price"),
                "abs_edge_pct": s.get("abs_edge_pct"),
                "direction": s.get("direction"),
                "payout_ratio": s.get("payout_ratio"),
                "liquidity_usd": s.get("liquidity_usd"),
            })
        return {"entries": ranked, "window_hours": hours, "total": len(ranked)}
    except Exception as e:
        logger.exception("Error in /leaderboard")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ticker")
async def get_ticker(hours: int = 1):
    """
    Returns all scanned signals from the last N hours (from DB).
    Includes both alpha and non-alpha signals.
    """
    try:
        signals = await get_recent_signals(hours=hours)
        return {
            "signals": signals,
            "total": len(signals),
            "window_hours": hours,
        }
    except Exception as e:
        logger.exception("Error in /ticker")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/system-prompt")
async def get_system_prompt():
    """Return current system prompt text used by the agent."""
    try:
        prompt = _PROMPT_FILE.read_text(encoding="utf-8").strip() if _PROMPT_FILE.exists() else ""
        return {"prompt": prompt}
    except Exception as e:
        logger.exception("Error in /agent/system-prompt GET")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/system-prompt")
async def update_system_prompt(payload: SystemPromptPayload):
    """Persist system prompt and update in-memory config for immediate use."""
    try:
        _PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        cleaned = payload.prompt.strip()
        _PROMPT_FILE.write_text(cleaned + "\n", encoding="utf-8")
        app_config.AGENT_SYSTEM_PROMPT = cleaned
        return {"ok": True, "prompt_length": len(cleaned)}
    except Exception as e:
        logger.exception("Error in /agent/system-prompt POST")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Agent summary in-memory cache
# ---------------------------------------------------------------------------
# AI providers (OpenAI, Grok, Gemini) are rate-limited and expensive.
# We cache the last successful response for 1 hour so repeated refreshes
# never hit the APIs more than once per hour.
#
# Thread-safety: asyncio.Lock() prevents multiple concurrent requests from
# all racing to call the providers simultaneously (stampede protection).
# ---------------------------------------------------------------------------

_SUMMARY_CACHE_TTL = 3600  # 1 hour in seconds


@dataclass
class _SummaryCache:
    data: Any = None          # last successful API response
    fetched_at: float = 0.0   # unix timestamp of last successful fetch
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # stampede guard

    def is_fresh(self) -> bool:
        """Return True if the cached value is still within the TTL window."""
        return self.data is not None and (time.time() - self.fetched_at) < _SUMMARY_CACHE_TTL

    def store(self, data: Any) -> None:
        """Save a new response and record the fetch time."""
        self.data = data
        self.fetched_at = time.time()

    def age_seconds(self) -> float:
        """How many seconds ago the cache was last populated."""
        return time.time() - self.fetched_at if self.fetched_at else float("inf")


_summary_cache = _SummaryCache()


@app.get("/agent/summary")
async def get_agent_summary(limit: int = 22, force: bool = False):
    """
    Returns an LLM-generated summary of the strongest current alpha signals.

    Caching behaviour:
    - Responses are cached for 1 hour (3600 s) in memory.
    - Concurrent requests wait on the same lock so only ONE provider call
      is ever in-flight at a time (stampede protection).
    - Pass ?force=true to bypass the cache and force a fresh AI call.

    Rate-limit protection:
    - Without caching every 30-second frontend poll would call OpenAI /
      Grok / Gemini simultaneously, exhausting free-tier quotas instantly.
    - With this cache the providers are called at most once per hour unless
      the user explicitly forces a refresh.
    """
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")

    # Fast path: return cached data without acquiring the lock
    if not force and _summary_cache.is_fresh():
        cached = dict(_summary_cache.data)
        cached["cached"] = True
        cached["cache_age_seconds"] = int(_summary_cache.age_seconds())
        return cached

    # Slow path: one coroutine calls the providers; the rest wait and reuse
    async with _summary_cache.lock:
        # Re-check after acquiring the lock — another coroutine may have
        # already populated the cache while we were waiting.
        if not force and _summary_cache.is_fresh():
            cached = dict(_summary_cache.data)
            cached["cached"] = True
            cached["cache_age_seconds"] = int(_summary_cache.age_seconds())
            return cached

        try:
            signals = [s for s in get_latest_signals() if s.get("has_alpha")]
            signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)
            summary = await build_agent_summary(signals[:limit])
            summary["signal_count"] = len(signals[:limit])
            summary["cached"] = False
            summary["cache_age_seconds"] = 0
            summary["stale"] = False
            _summary_cache.store(summary)
            return summary
        except Exception as e:
            logger.exception("Error in /agent/summary")
            # If providers all failed but we have a previous response, return it
            # as stale data rather than a 500 — trading must always have a signal.
            if _summary_cache.data is not None:
                stale = dict(_summary_cache.data)
                stale["cached"] = True
                stale["stale"] = True
                stale["stale_reason"] = str(e)
                stale["cache_age_seconds"] = int(_summary_cache.age_seconds())
                logger.warning("All AI providers failed — serving stale cache (age=%ds): %s", int(_summary_cache.age_seconds()), e)
                return stale
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/refresh/csv")
async def trigger_csv_refresh():
    """Run CSV export scripts and refresh the CSV-derived snapshot (same work as the background loop)."""
    cfg = make_default_cfg()
    try:
        await export_all_csvs(cfg=cfg)
    except Exception as e:
        logger.exception("Error in /refresh/csv")
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "refreshed_at": datetime.utcnow().isoformat()}


@app.post("/scan")
async def trigger_scan():
    """Run live Deribit + Polymarket API scan (persists to DB). Matrix uses CSV+LLM via /refresh/csv."""
    async with _scan_lock:
        signals = await scan_once()
    refresh_fn = getattr(db_module, "refresh_alpha_leaderboard_cache", None)
    if refresh_fn:
        await refresh_fn(
            hours=app_config.LEADERBOARD_HOURS,
            top_n=app_config.LEADERBOARD_TOP_N,
        )
    alpha = [s for s in signals if s["has_alpha"]]
    return {
        "signals": signals,
        "total": len(signals),
        "alpha": len(alpha),
        "scanned_at": datetime.utcnow().isoformat(),
    }


@app.get("/health")
async def health():
    try:
        signals = get_latest_signals()
        return {
            "status": "ok",
            "latest_signals": len(signals),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.exception("Error in /health")
        return {"status": "error", "detail": str(e), "timestamp": datetime.utcnow().isoformat()}
