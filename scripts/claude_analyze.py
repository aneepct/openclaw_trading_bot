"""
Claude CSV Analysis Bridge
--------------------------
Fetches live CSVs from the OpenClaw API, then sends the data to
Anthropic's API for analysis.  Run locally — no egress restrictions.

Usage:
    python scripts/claude_analyze.py
    python scripts/claude_analyze.py --asset btc
    python scripts/claude_analyze.py --asset eth
    python scripts/claude_analyze.py --question "What is the best trade right now?"

Requirements:
    pip install anthropic httpx
    export ANTHROPIC_API_KEY=sk-ant-...
    export OPENCLAW_API_URL=https://openclaw-api.aneep.tech   # or http://localhost:8000
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    import httpx
except ImportError:
    sys.exit("Missing dependency: pip install httpx")

OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "http://localhost:8000").rstrip("/")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")


# ---------------------------------------------------------------------------
# Fetch CSVs from your API
# ---------------------------------------------------------------------------

def fetch_csv(path: str, timeout: int = 60) -> str:
    url = f"{OPENCLAW_API_URL}{path}"
    print(f"  Fetching {url} ...", end=" ", flush=True)
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
    r.raise_for_status()
    print(f"{len(r.text)} chars")
    return r.text


def fetch_all_csvs(asset: str) -> dict[str, str]:
    asset = asset.lower()
    data: dict[str, str] = {}
    endpoints = {
        f"deribit_{asset}_today":    f"/download/csv/deribit/{asset}/today",
        f"deribit_{asset}_tomorrow": f"/download/csv/deribit/{asset}/tomorrow",
        f"polymarket_{asset}":       f"/download/csv/polymarket/{asset}",
    }
    for label, path in endpoints.items():
        try:
            data[label] = fetch_csv(path)
        except Exception as e:
            print(f"  WARNING: could not fetch {path}: {e}")
    return data


# ---------------------------------------------------------------------------
# Send to Anthropic
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a professional crypto options analyst.
You will be given live CSV data from Deribit (order book) and Polymarket (prediction markets).
Your job is to identify mispricings between the two markets and recommend actionable trades.

Format your response as:
1. Brief market summary (2-3 sentences)
2. Top opportunities with: Market, Direction, Edge %, Reasoning
3. Risk notes
"""


def analyze_with_claude(csv_data: dict[str, str], question: str) -> str:
    if not ANTHROPIC_API_KEY:
        sys.exit("Set ANTHROPIC_API_KEY environment variable first.")

    # Build the message content
    sections = []
    for label, content in csv_data.items():
        # Truncate very large CSVs to stay within context limits
        lines = content.splitlines()
        if len(lines) > 300:
            truncated = "\n".join(lines[:300])
            sections.append(f"### {label} (first 300 rows of {len(lines)})\n```csv\n{truncated}\n```")
        else:
            sections.append(f"### {label}\n```csv\n{content}\n```")

    user_message = "\n\n".join(sections)
    if question:
        user_message += f"\n\n**Specific question:** {question}"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print(f"\nSending to Claude ({MODEL}) ...")
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze OpenClaw CSV data with Claude")
    parser.add_argument("--asset", choices=["btc", "eth", "both"], default="both",
                        help="Which asset to fetch (default: both)")
    parser.add_argument("--question", default="",
                        help="Optional specific question to ask Claude about the data")
    parser.add_argument("--api-url", default=OPENCLAW_API_URL,
                        help="OpenClaw API base URL")
    args = parser.parse_args()

    global OPENCLAW_API_URL
    OPENCLAW_API_URL = args.api_url.rstrip("/")

    print(f"OpenClaw API: {OPENCLAW_API_URL}")
    print("Fetching live CSVs ...\n")

    csv_data: dict[str, str] = {}
    assets = ["btc", "eth"] if args.asset == "both" else [args.asset]
    for asset in assets:
        csv_data.update(fetch_all_csvs(asset))

    if not csv_data:
        sys.exit("No CSV data fetched. Check that the API is running.")

    result = analyze_with_claude(csv_data, args.question)
    print("\n" + "=" * 60)
    print(result)
    print("=" * 60)


if __name__ == "__main__":
    main()
