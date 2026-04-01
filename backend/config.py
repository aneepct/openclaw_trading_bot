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
# Enforced automatically in calculate_edge() — do not change
# PAYOUT_MIN_RATIO = 2.0  (hardcoded in math_engine.py)

# ── Liquidity filter (per doc) ───────────────────────────────
MIN_LIQUIDITY_USD = 1000.0        # Skip markets with liquidity below this (USD)

# ── Scanner ──────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 60        # How often the scanner runs (seconds)
POLYMARKET_PAGES = 8              # Pages of Polymarket markets (500 each = 4000 total)
DERIBIT_DEPTH = 1                 # Order book depth per instrument

# ── Strike tolerance ─────────────────────────────────────────
STRIKE_TOLERANCE_PCT = 2.0        # ±% around target strike to accept a Deribit match

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

# ── OpenAI agent layer ────────────────────────────────────────
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini"
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low") or "low"
AGENT_TOP_N_SIGNALS = int(os.getenv("AGENT_TOP_N_SIGNALS", "5"))
AGENT_SYSTEM_PROMPT = (
    os.getenv(
    "OPENCLAW_AGENT_SYSTEM_PROMPT",
    (
        "You are the Open Claw trading agent. Treat Deribit as the professional "
        "probability surface and Polymarket as the retail market to compare against. "
        "Use the provided signal data to explain where Polymarket is underpricing or "
        "overpricing risk. Return concise trading guidance for the frontend with a "
        "clear bias, ranked opportunities, and short reasoning grounded in the given "
        "numbers only. Do not invent market data."
    ),
)
    or (
        "You are the Open Claw trading agent. Treat Deribit as the professional "
        "probability surface and Polymarket as the retail market to compare against. "
        "Use the provided signal data to explain where Polymarket is underpricing or "
        "overpricing risk. Return concise trading guidance for the frontend with a "
        "clear bias, ranked opportunities, and short reasoning grounded in the given "
        "numbers only. Do not invent market data."
    )
)
