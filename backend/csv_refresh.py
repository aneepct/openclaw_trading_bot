from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config

from csv_signals import refresh_latest_signals

@dataclass
class CsvRefreshConfig:
    interval_seconds: int
    deribit_depth: int

    polymarket_limit: int


async def _run_cmd(cmd: list[str], *, cwd: Path) -> int:
    # Use a thread so we don't block the event loop.
    def _sync() -> int:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        return proc.returncode

    return await asyncio.to_thread(_sync)


async def export_all_csvs(*, cfg: CsvRefreshConfig) -> None:
    # Works both locally (repo/backend/...) and in Docker (build context = backend/,
    # so files are at /app/... with no extra "backend" prefix).
    backend_root = Path(__file__).resolve().parent
    py = sys.executable
    btc_script = backend_root / "deribit_orderbook_data" / "btc.py"
    eth_script = backend_root / "deribit_orderbook_data" / "eth.py"
    poly_script = backend_root / "polymarket_markets_export" / "export_markets.py"

    # Deribit (today-only)
    rc = await _run_cmd(
        [
            py,
            str(btc_script),
            "--depth",
            str(cfg.deribit_depth),
            "--max-instruments-per-day",
            "40",
        ],
        cwd=backend_root,
    )
    if rc != 0:
        print(f"[csv_refresh] BTC deribit export failed (rc={rc})")
        return
    # Note: we rely on the scripts to print useful errors. If one fails,
    # signals will not update, so keep this loop resilient.
    rc = await _run_cmd(
        [
            py,
            str(eth_script),
            "--depth",
            str(cfg.deribit_depth),
            "--max-instruments-per-day",
            "40",
        ],
        cwd=backend_root,
    )
    if rc != 0:
        print(f"[csv_refresh] ETH deribit export failed (rc={rc})")
        return

    # Polymarket markets (today-only, unlimited pages)
    rc = await _run_cmd(
        [
            py,
            str(poly_script),
            "--only-today-utc",
            "--max-pages",
            "0",
            "--limit",
            str(cfg.polymarket_limit),
        ],
        cwd=backend_root,
    )
    if rc != 0:
        print(f"[csv_refresh] Polymarket markets export failed (rc={rc})")
        return

    # Compute the current signals for the frontend matrix/summary.
    await refresh_latest_signals()


async def csv_refresh_loop(*, cfg: CsvRefreshConfig) -> None:
    in_progress = False
    while True:
        if not in_progress:
            in_progress = True
            try:
                await export_all_csvs(cfg=cfg)
            finally:
                in_progress = False
        await asyncio.sleep(cfg.interval_seconds)


def make_default_cfg() -> CsvRefreshConfig:
    return CsvRefreshConfig(
        interval_seconds=getattr(config, "CSV_REFRESH_INTERVAL_SECONDS", config.SCAN_INTERVAL_SECONDS),
        deribit_depth=config.DERIBIT_DEPTH,
        polymarket_limit=500,
    )

