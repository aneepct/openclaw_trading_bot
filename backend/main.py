import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from agents.openai_agent import build_agent_summary
from db.database import init_db, get_leaderboard, get_recent_signals
import db.database as db_module
from engine.scanner import ticker_loop, get_latest_signals, scan_once, _scan_lock
import engine.scanner as _scanner
import config as app_config
from config import SPEC_CLIENT, SPEC_VERSION, PROJECT_SLUG, PROJECT_DISPLAY_NAME


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB, hydrate leaderboard cache from DB, launch scanner
    await init_db()
    refresh_fn = getattr(db_module, "refresh_alpha_leaderboard_cache", None)
    if refresh_fn:
        await refresh_fn(
            hours=app_config.LEADERBOARD_HOURS,
            top_n=app_config.LEADERBOARD_TOP_N,
        )
    task = asyncio.create_task(ticker_loop())
    yield
    # Shutdown: cancel scanner
    task.cancel()


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
            "deribit": "public/get_order_book (+ instruments, index)",
            "polymarket": "gamma markets API",
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
        entries = await get_leaderboard(hours=hours, top_n=top)
        ranked = []
        for i, entry in enumerate(entries):
            ranked.append({
                "rank":                  entry.get("rank", i + 1),
                "instrument_t1":         entry.get("instrument_t1"),
                "instrument_t2":         entry.get("instrument_t2"),
                "option_type":           entry.get("option_type"),
                "polymarket_question":   entry.get("polymarket_question"),
                "polymarket_market_id":  entry.get("polymarket_market_id"),
                "deribit_prob":          entry.get("deribit_prob"),
                "polymarket_price":      entry.get("polymarket_price"),
                "abs_edge_pct":          entry.get("abs_edge_pct"),
                "direction":             entry.get("direction"),
                "payout_ratio":          entry.get("payout_ratio"),
                "liquidity_usd":         entry.get("liquidity_usd"),
            })
        return {
            "entries": ranked,
            "window_hours": hours,
            "total": len(ranked),
        }
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


@app.get("/agent/summary")
async def get_agent_summary(limit: int = 5):
    """
    Returns an LLM-generated summary of the strongest current alpha signals.
    Falls back to deterministic scanner text if no AI provider is available.
    """
    if limit < 1 or limit > 10:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 10")

    try:
        signals = [s for s in get_latest_signals() if s.get("has_alpha")]
        signals.sort(key=lambda s: float(s.get("abs_edge_pct") or 0.0), reverse=True)
        summary = await build_agent_summary(signals[:limit])
        summary["signal_count"] = len(signals[:limit])
        return summary
    except Exception as e:
        logger.exception("Error in /agent/summary")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scan")
async def trigger_scan():
    """Manually trigger a scan and return results immediately."""
    signals = await scan_once()
    async with _scan_lock:
        _scanner._latest_signals = signals
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
