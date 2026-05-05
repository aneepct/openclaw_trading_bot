import asyncio
import io
import logging
import subprocess
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from csv_refresh import csv_refresh_loop, email_scheduler_loop, export_all_csvs, make_default_cfg
from email_sender import collect_csv_paths, send_csv_report

_PROVIDER_PROMPT_FILES = {
    "openai": Path(__file__).parent / "prompts" / "openai_system_prompt.txt",
    "gemini": Path(__file__).parent / "prompts" / "gemini_system_prompt.txt",
    "grok":   Path(__file__).parent / "prompts" / "grok_system_prompt.txt",
}
_PROVIDER_CONFIG_ATTRS = {
    "openai": "OPENAI_SYSTEM_PROMPT",
    "gemini": "GEMINI_SYSTEM_PROMPT",
    "grok":   "GROK_SYSTEM_PROMPT",
}


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
    csv_task   = asyncio.create_task(csv_refresh_loop(cfg=cfg))
    email_task = asyncio.create_task(email_scheduler_loop(cfg=cfg))
    scanner_task = asyncio.create_task(ticker_loop())
    yield
    for task in (csv_task, email_task, scanner_task):
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


@app.get("/agent/system-prompt/{provider}")
async def get_provider_system_prompt(provider: str):
    """Return the system prompt for a specific AI provider."""
    if provider not in _PROVIDER_PROMPT_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    try:
        f = _PROVIDER_PROMPT_FILES[provider]
        if f.exists():
            prompt = f.read_text(encoding="utf-8").strip()
        else:
            prompt = getattr(app_config, _PROVIDER_CONFIG_ATTRS[provider], "")
        return {"provider": provider, "prompt": prompt}
    except Exception as e:
        logger.exception("Error in /agent/system-prompt/%s GET", provider)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/system-prompt/{provider}")
async def update_provider_system_prompt(provider: str, payload: SystemPromptPayload):
    """Persist a provider-specific system prompt and update in-memory config."""
    if provider not in _PROVIDER_PROMPT_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    try:
        f = _PROVIDER_PROMPT_FILES[provider]
        f.parent.mkdir(parents=True, exist_ok=True)
        cleaned = payload.prompt.strip()
        f.write_text(cleaned + "\n", encoding="utf-8")
        setattr(app_config, _PROVIDER_CONFIG_ATTRS[provider], cleaned)
        return {"ok": True, "provider": provider, "prompt_length": len(cleaned)}
    except Exception as e:
        logger.exception("Error in /agent/system-prompt/%s POST", provider)
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
    """Run CSV export scripts, refresh the CSV-derived snapshot, then email all CSVs."""
    cfg = make_default_cfg()
    try:
        await export_all_csvs(cfg=cfg)
    except Exception as e:
        logger.exception("Error in /refresh/csv")
        raise HTTPException(status_code=500, detail=str(e))
    # Email the freshly generated CSVs
    backend_root = Path(__file__).resolve().parent
    csv_paths = collect_csv_paths(backend_root, cfg.deribit_depth)
    await asyncio.to_thread(send_csv_report, csv_paths)
    return {"ok": True, "refreshed_at": datetime.utcnow().isoformat()}


@app.api_route("/scan", methods=["GET", "POST"])
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


@app.get("/download/csvs")
async def download_csvs():
    """Regenerate all Deribit and Polymarket CSVs, then return them as a single ZIP archive."""
    backend_root = Path(__file__).resolve().parent
    cfg = make_default_cfg()

    # Regenerate fresh data before zipping
    try:
        await export_all_csvs(cfg=cfg)
    except Exception as e:
        logger.exception("Error regenerating CSVs for download")
        raise HTTPException(status_code=500, detail=f"CSV regeneration failed: {e}")

    csv_paths = collect_csv_paths(backend_root, cfg.deribit_depth)
    existing = [(p, name) for p, name in csv_paths if p.exists()]
    if not existing:
        raise HTTPException(status_code=404, detail="No CSV files found after regeneration.")

    today = datetime.utcnow().strftime("%Y-%m-%d")
    zip_filename = f"openclaw_csvs_{today}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, name in existing:
            zf.writestr(name, path.read_bytes())
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


# ---------------------------------------------------------------------------
# Individual CSV download endpoints  (always fetch fresh data first)
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent

_CSV_MAP: dict[str, Path] = {
    # Deribit
    "deribit/btc/today":    _BACKEND_ROOT / "deribit_orderbook_data" / "output" / "BTC" / f"order_book_today_depth{app_config.DERIBIT_DEPTH}.csv",
    "deribit/btc/tomorrow": _BACKEND_ROOT / "deribit_orderbook_data" / "output" / "BTC" / f"order_book_tomorrow_depth{app_config.DERIBIT_DEPTH}.csv",
    "deribit/eth/today":    _BACKEND_ROOT / "deribit_orderbook_data" / "output" / "ETH" / f"order_book_today_depth{app_config.DERIBIT_DEPTH}.csv",
    "deribit/eth/tomorrow": _BACKEND_ROOT / "deribit_orderbook_data" / "output" / "ETH" / f"order_book_tomorrow_depth{app_config.DERIBIT_DEPTH}.csv",
    # Polymarket
    "polymarket/btc":       _BACKEND_ROOT / "polymarket_markets_export" / "output" / "BTC" / "polymarket_markets_today_utc.csv",
    "polymarket/eth":       _BACKEND_ROOT / "polymarket_markets_export" / "output" / "ETH" / "polymarket_markets_today_utc.csv",
}

# Scripts that regenerate each data source
_DERIBIT_SCRIPTS: dict[str, Path] = {
    "btc": _BACKEND_ROOT / "deribit_orderbook_data" / "btc.py",
    "eth": _BACKEND_ROOT / "deribit_orderbook_data" / "eth.py",
}
_POLYMARKET_SCRIPT = _BACKEND_ROOT / "polymarket_markets_export" / "export_markets.py"


async def _run_export(script: Path) -> None:
    """Run an export script in a thread so the event loop stays free."""
    def _sync() -> None:
        result = subprocess.run(
            [sys.executable, str(script), "--depth", str(app_config.DERIBIT_DEPTH), "--max-instruments-per-day", "40"],
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info("[csv_export] %s", result.stdout.strip())
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Script exited with code {result.returncode}")

    await asyncio.to_thread(_sync)


async def _run_polymarket_export() -> None:
    """Run the Polymarket export script (no extra CLI args needed)."""
    def _sync() -> None:
        result = subprocess.run(
            [sys.executable, str(_POLYMARKET_SCRIPT)],
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info("[csv_export] %s", result.stdout.strip())
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Script exited with code {result.returncode}")

    await asyncio.to_thread(_sync)


def _serve_csv(path: Path, filename: str) -> StreamingResponse:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"CSV not generated: {filename}")
    return StreamingResponse(
        iter([path.read_bytes()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/download/csv/deribit/{asset}/{day}")
async def download_deribit_csv(asset: str, day: str):
    """
    Fetch the latest Deribit order-book data, then return the CSV.

    asset : btc | eth
    day   : today | tomorrow
    """
    asset_key = asset.lower()
    day_key = day.lower()
    key = f"deribit/{asset_key}/{day_key}"
    path = _CSV_MAP.get(key)
    if path is None:
        raise HTTPException(status_code=400, detail=f"Unknown combination: asset={asset} day={day}. Use btc/eth and today/tomorrow.")

    script = _DERIBIT_SCRIPTS.get(asset_key)
    if script is None:
        raise HTTPException(status_code=400, detail=f"Unknown asset: {asset}. Use btc or eth.")

    try:
        await _run_export(script)
    except Exception as e:
        logger.exception("Deribit export failed for %s", asset_key)
        raise HTTPException(status_code=502, detail=f"Deribit export failed: {e}")

    filename = f"deribit_{asset_key}_{day_key}_depth{app_config.DERIBIT_DEPTH}.csv"
    return _serve_csv(path, filename)


@app.get("/download/csv/polymarket/{asset}")
async def download_polymarket_csv(asset: str):
    """
    Fetch the latest Polymarket markets data, then return the CSV.

    asset : btc | eth
    """
    asset_key = asset.lower()
    key = f"polymarket/{asset_key}"
    path = _CSV_MAP.get(key)
    if path is None:
        raise HTTPException(status_code=400, detail=f"Unknown asset: {asset}. Use btc or eth.")

    try:
        await _run_polymarket_export()
    except Exception as e:
        logger.exception("Polymarket export failed for %s", asset_key)
        raise HTTPException(status_code=502, detail=f"Polymarket export failed: {e}")

    filename = f"polymarket_{asset_key}_today.csv"
    return _serve_csv(path, filename)


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


# ---------------------------------------------------------------------------
# Polymarket order execution endpoints
# ---------------------------------------------------------------------------

from clients.polymarket_orders import build_authed_client, create_order, derive_api_credentials  # noqa: E402


class OrderRequest(BaseModel):
    """Request body for POST /orders."""

    token_id: str
    """CLOB token ID for the Yes outcome — use get_yes_clob_token_id() to obtain it."""

    price: float
    """Limit price in USDC (0 < price < 1).  e.g. 0.65 means 65¢."""

    size: float
    """Order size in USDC.  e.g. 100 means $100 notional."""

    side: str
    """Trade direction: ``"BUY"`` or ``"SELL"``."""

    tick_size: str = "0.01"
    """Minimum price increment for this market (default ``"0.01"``)."""

    neg_risk: bool = False
    """Set ``True`` only for negative-risk markets (uncommon)."""


@app.post("/orders/auth")
async def authenticate_polymarket():
    """Derive or create Polymarket L2 API credentials from the configured wallet.

    This endpoint contacts the CLOB server to derive API credentials from the
    wallet private key set in ``POLYMARKET_PRIVATE_KEY``.  Credentials are
    cached in-process for the lifetime of the server; calling this endpoint
    more than once returns the cached result.

    **Required env var:** ``POLYMARKET_PRIVATE_KEY``

    Returns the ``apiKey`` (truncated for security), confirming that the
    wallet is authorised for L2 order methods.
    """
    try:
        creds = await asyncio.to_thread(derive_api_credentials)
        # ApiCreds is an object — access via attributes, not dict .get()
        api_key = getattr(creds, "api_key", "") or getattr(creds, "apiKey", "")
        return {
            "ok": True,
            "apiKey_prefix": str(api_key)[:8] + "…" if api_key else "",
            "message": "Credentials derived successfully and cached in-process.",
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Error in /orders/auth")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/orders")
async def place_order(payload: OrderRequest):
    """Place a BUY or SELL order on the Polymarket CLOB.

    The ``token_id`` is the Yes-outcome CLOB token for a market.  Obtain it
    via ``GET /orders/token/{market_id}`` or directly from
    ``get_yes_clob_token_id(market)``.

    **Required env vars:** ``POLYMARKET_PRIVATE_KEY``,
    optionally ``POLYMARKET_FUNDER_ADDRESS``.

    Request body example:
    ```json
    {
      "token_id": "71321045679252212594626385532706912750332728571942532289631379312455583992563",
      "price": 0.65,
      "size": 100,
      "side": "BUY"
    }
    ```
    """
    try:
        result = await create_order(
            token_id=payload.token_id,
            price=payload.price,
            size=payload.size,
            side=payload.side,
            tick_size=payload.tick_size,
            neg_risk=payload.neg_risk,
        )
        return {"ok": True, "order": result}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Error in POST /orders")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders/token/{market_id}")
async def get_order_token(market_id: str):
    """Look up the Yes-outcome CLOB token ID for a Polymarket market.

    ``market_id`` is the condition ID (hex string) of the market, as returned
    by the Gamma API and stored in scanner signals as ``polymarket_market_id``.

    Returns ``token_id`` which can be passed directly to ``POST /orders``.
    """
    from clients.polymarket import get_market_by_id, get_yes_clob_token_id

    try:
        market = await get_market_by_id(market_id)
    except Exception as e:
        logger.exception("Error fetching market %s", market_id)
        raise HTTPException(status_code=502, detail=f"Gamma API error: {e}")

    if not market:
        raise HTTPException(status_code=404, detail=f"Market not found: {market_id}")

    token_id = get_yes_clob_token_id(market)
    if not token_id:
        raise HTTPException(status_code=404, detail="No CLOB token IDs found for this market.")

    return {
        "market_id": market_id,
        "token_id": token_id,
        "question": market.get("question"),
    }


@app.get("/orders/tokens")
async def list_clob_tokens():
    """Return Yes-outcome CLOB token IDs for all BTC and ETH daily price markets fetched today.

    Calls the same ``fetch_crypto_price_markets()`` used by the scanner so the
    list is always in sync with what the engine is tracking.

    Response shape:
    ```json
    {
      "date": "2026-05-05",
      "tokens": [
        {
          "currency": "BTC",
          "market_id": "0x...",
          "token_id": "71321...",
          "question": "Will BTC be above $95000 on May 5?"
        },
        ...
      ],
      "total": 22
    }
    ```
    """
    from engine.scanner import fetch_crypto_price_markets
    from clients.polymarket import get_yes_clob_token_id as _get_token

    try:
        markets = await fetch_crypto_price_markets()
    except Exception as e:
        logger.exception("Error fetching markets for /orders/tokens")
        raise HTTPException(status_code=502, detail=f"Polymarket fetch failed: {e}")

    tokens = []
    for m in markets:
        token_id = _get_token(m)
        if not token_id:
            continue
        tokens.append({
            "currency": m.get("_currency"),
            "market_id": m.get("id") or m.get("conditionId", ""),
            "token_id": token_id,
            "question": m.get("question"),
        })

    from datetime import date
    return {
        "date": date.today().isoformat(),
        "tokens": tokens,
        "total": len(tokens),
    }
