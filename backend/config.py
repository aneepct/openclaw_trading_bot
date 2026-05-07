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
# Intervals in hours (e.g. 2/60 = 2 minutes, 1 = hourly). Seconds are derived for asyncio.
SCAN_INTERVAL_HOURS = 1
SCAN_INTERVAL_SECONDS = int(round(SCAN_INTERVAL_HOURS * 3600))

CSV_REFRESH_INTERVAL_HOURS = 1
CSV_REFRESH_INTERVAL_SECONDS = int(round(CSV_REFRESH_INTERVAL_HOURS * 3600))
POLYMARKET_PAGES = 8              # Pages of Polymarket markets (500 each = 4000 total)
DERIBIT_DEPTH = 1                 # Order book depth per instrument

# ── Strike tolerance ─────────────────────────────────────────
STRIKE_TOLERANCE_PCT = 5.0        # ±% around target strike to accept a Deribit match
ALLOWED_DERIBIT_EXPIRY_DAYS = 365 # Allow Deribit contracts up to 1 year out (covers all Poly market horizons)

# ── Database ─────────────────────────────────────────────────
DB_RETAIN_DAYS = 30               # Auto-delete signals older than N days
DB_CLEANUP_EVERY_N_SCANS = 24     # Run cleanup every N scans (24 ≈ once/day with hourly scans)

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


def _read_provider_prompt(provider: str) -> str:
    prompt_path = Path(__file__).parent / "prompts" / f"{provider}_system_prompt.txt"
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
AGENT_ENABLED = os.getenv("AGENT_ENABLED", "true").strip().lower() not in ("0", "false", "no")
_DEFAULT_SYSTEM_PROMPT = (
    "You are the trading agent. Treat Deribit as the professional "
    "probability surface and Polymarket as the retail market to compare against. "
    "Use the provided signal data to explain where Polymarket is underpricing or "
    "overpricing risk. Return concise trading guidance for the frontend with a "
    "clear bias, ranked opportunities, and short reasoning grounded in the given "
    "numbers only. Do not invent market data."
)
AGENT_SYSTEM_PROMPT = (
    os.getenv("OPENCLAW_AGENT_SYSTEM_PROMPT", "").strip()
    or _DEFAULT_SYSTEM_PROMPT
)
OPENAI_SYSTEM_PROMPT = _read_provider_prompt("openai") or AGENT_SYSTEM_PROMPT
GEMINI_SYSTEM_PROMPT = _read_provider_prompt("gemini") or AGENT_SYSTEM_PROMPT
GROK_SYSTEM_PROMPT   = _read_provider_prompt("grok")   or AGENT_SYSTEM_PROMPT

# ── Email / SMTP ──────────────────────────────────────────────
EMAIL_HOST          = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT          = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS       = os.getenv("EMAIL_USE_TLS", "True").strip().lower() in ("true", "1", "yes")
EMAIL_HOST_USER     = os.getenv("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").strip()
DEFAULT_FROM_EMAIL  = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER).strip()
# Comma-separated list of recipient addresses, e.g. "a@x.com,b@x.com"
EMAIL_RECIPIENTS    = [
    addr.strip()
    for addr in os.getenv("EMAIL_RECIPIENT", EMAIL_HOST_USER).split(",")
    if addr.strip()
]
EMAIL_INTERVAL_HOURS   = float(os.getenv("EMAIL_INTERVAL_HOURS", "1"))
EMAIL_INTERVAL_SECONDS = int(round(EMAIL_INTERVAL_HOURS * 3600))

# ── Polymarket order execution (CLOB) ─────────────────────────────────────────
POLYMARKET_PRIVATE_KEY   = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "").strip()
# Polygon mainnet chain ID (137). Override only for testnet/debugging.
POLYMARKET_CHAIN_ID      = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
# Signature type: 0 = EOA, 1 = gnosis safe / magic-link proxy wallet.
POLYMARKET_SIGNATURE_TYPE = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))
# Builder code for registered Polymarket builders (leave empty for regular users).
POLYMARKET_BUILDER_CODE  = os.getenv("POLYMARKET_BUILDER_CODE", "").strip()
