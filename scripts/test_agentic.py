"""
Smoke tests: Open Claw scanner API (no live Deribit/Polymarket scan).
Run from repo root:  python scripts/test_agentic.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import engine.scanner as scanner_mod


async def _noop_ticker():
    await asyncio.sleep(0.01)


scanner_mod.ticker_loop = _noop_ticker

import main as app_main  # noqa: E402


def run():
    from fastapi.testclient import TestClient

    failures = []

    with TestClient(app_main.app) as client:
        r = client.get("/health")
        if r.status_code != 200:
            failures.append(f"/health -> {r.status_code}")
        else:
            print("OK /health", r.json())

        r = client.get("/spec")
        if r.status_code != 200:
            failures.append(f"/spec -> {r.status_code}")
        else:
            j = r.json()
            print("OK /spec spec_version=", j.get("spec_version"), "client=", j.get("client"))

        r = client.get("/matrix")
        if r.status_code != 200:
            failures.append(f"/matrix -> {r.status_code}")
        else:
            print("OK /matrix total=", r.json().get("total"))

        r = client.get("/leaderboard")
        if r.status_code != 200:
            failures.append(f"/leaderboard -> {r.status_code}")
        else:
            print("OK /leaderboard entries=", len(r.json().get("entries") or []))

        r = client.get("/agent/summary")
        if r.status_code != 200:
            failures.append(f"/agent/summary -> {r.status_code}")
        else:
            body = r.json()
            print("OK /agent/summary enabled=", body.get("enabled"), "source=", body.get("source"))

    if failures:
        print("FAIL:", failures)
        sys.exit(1)
    print("\nAll scanner API checks passed.")


if __name__ == "__main__":
    run()
