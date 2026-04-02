# =============================================================
#  Open Claw — Agentic Trading (interpolated scanner service)
#  Spec v2.6 · Client: Levenstein.net · Ticker & Alpha Leaderboard
#  https://www.levenstein.net/openclaw
# =============================================================

SPEC_VERSION = "2.6"
SPEC_CLIENT = "Levenstein.net"

# Naming (align with Levenstein spec vs upstream OpenClaw product)
PROJECT_DISPLAY_NAME = "Open Claw"
PROJECT_SLUG = "open-claw"  # recommended git / folder name; avoids clashing with npm "openclaw"

# ── Assets to scan ───────────────────────────────────────────
ASSETS = ["BTC", "ETH"]

# ── Edge / Alpha threshold ───────────────────────────────────
MIN_EDGE_PCT = 3.0                # Minimum edge % to flag as alpha signal

# ── Asymmetric payout filter (per doc) ───────────────────────
# "Asymmetric payout >2x" means Polymarket YES price < 0.50
# Used by the current agent workflow as a lightweight selection hint.

# ── Liquidity filter (per doc) ───────────────────────────────
MIN_LIQUIDITY_USD = 1000.0        # Skip markets with liquidity below this (USD)

# ── Scanner ──────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 120       # How often the scanner runs (seconds)
CSV_REFRESH_INTERVAL_SECONDS = 120  # How often to refresh CSV snapshots (seconds)
POLYMARKET_PAGES = 8              # Pages of Polymarket markets (500 each = 4000 total)
DERIBIT_DEPTH = 1                 # Order book depth per instrument

# ── Strike tolerance ─────────────────────────────────────────
STRIKE_TOLERANCE_PCT = 5.0        # ±% around target strike to accept a Deribit match
ALLOWED_DERIBIT_EXPIRY_DAYS = 365 # Allow Deribit contracts up to 1 year out (covers all Poly market horizons)

# ── Database ─────────────────────────────────────────────────
DB_RETAIN_DAYS = 30               # Auto-delete signals older than N days
DB_CLEANUP_EVERY_N_SCANS = 1440   # Run cleanup every N scans (1440 ≈ once/day)

# ── Leaderboard ──────────────────────────────────────────────
LEADERBOARD_HOURS = 24            # Look-back window
LEADERBOARD_TOP_N = 5             # Top N unique signals

# ── API Base URLs ─────────────────────────────────────────────
DERIBIT_BASE_URL   = "https://www.deribit.com/api/v2"
POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"

# ── Ports (local dev only — Docker uses its own port mapping) ─
FRONTEND_PORT = 3001
BACKEND_PORT  = 8000

import os
from pathlib import Path

# ── OpenAI agent layer ────────────────────────────────────────


def _read_prompt_file() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "openclaw_system_prompt.txt"
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini"
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low") or "low"
GROK_API_KEY = os.getenv("GROK_API_KEY", "").strip()
GROK_BASE_URL = (os.getenv("GROK_BASE_URL", "https://api.x.ai/v1") or "https://api.x.ai/v1").rstrip("/")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-mini") or "grok-3-mini"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_BASE_URL = (
    os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    or "https://generativelanguage.googleapis.com/v1beta/openai"
).rstrip("/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"
AGENT_TOP_N_SIGNALS = int(os.getenv("AGENT_TOP_N_SIGNALS", "5"))
AGENT_SYSTEM_PROMPT = (
    os.getenv("OPENCLAW_AGENT_SYSTEM_PROMPT", "").strip()
    or _read_prompt_file()
    or (
        "You are the trading agent. Treat Deribit as the professional "
        "probability surface and Polymarket as the retail market to compare against. "
        "Use the provided signal data to explain where Polymarket is underpricing or "
        "overpricing risk. Return concise trading guidance for the frontend with a "
        "clear bias, ranked opportunities, and short reasoning grounded in the given "
        "numbers only. Do not invent market data."
    )
)
