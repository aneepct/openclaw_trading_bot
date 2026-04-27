from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Sequence

import config


def send_csv_report(csv_paths: Sequence[Path]) -> None:
    """Attach the provided CSV files and email them via SMTP.

    Silently skips if SMTP credentials are not configured.
    """
    if not config.EMAIL_HOST_USER or not config.EMAIL_HOST_PASSWORD:
        print("[email] Skipping: EMAIL_HOST_USER or EMAIL_HOST_PASSWORD not configured.")
        return

    existing = [p for p in csv_paths if p.exists()]
    if not existing:
        print("[email] No CSV files found to attach — skipping send.")
        return

    recipients = config.EMAIL_RECIPIENTS or [config.EMAIL_HOST_USER]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    msg = EmailMessage()
    msg["Subject"] = f"OpenClaw CSV Export — {today}"
    msg["From"] = config.DEFAULT_FROM_EMAIL
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Please find attached the latest Deribit and Polymarket CSV exports "
        f"generated on {today} (UTC).\n\n"
        f"Attachments ({len(existing)}):\n"
        + "\n".join(f"  • {p.name}" for p in existing)
        + "\n\nOpenClaw Trading Bot"
    )

    for path in existing:
        msg.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="csv",
            filename=path.name,
        )

    try:
        if config.EMAIL_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(config.EMAIL_HOST_USER, config.EMAIL_HOST_PASSWORD)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT) as smtp:
                smtp.login(config.EMAIL_HOST_USER, config.EMAIL_HOST_PASSWORD)
                smtp.send_message(msg)
        print(f"[email] Sent CSV report ({len(existing)} attachment(s)) to {', '.join(recipients)}")
    except Exception as exc:
        print(f"[email] Failed to send CSV report: {exc}")


def collect_csv_paths(backend_root: Path, deribit_depth: int) -> list[Path]:
    """Collect all CSV files produced by the latest export run."""
    paths: list[Path] = []

    # Deribit order book CSVs — one per expiry day, per asset
    for asset in ("BTC", "ETH"):
        asset_dir = backend_root / "deribit_orderbook_data" / "output" / asset
        if asset_dir.exists():
            paths.extend(sorted(asset_dir.glob("*.csv")))

    # Polymarket CSVs
    poly_out = backend_root / "polymarket_markets_export" / "output"
    if poly_out.exists():
        # Combined file
        combined = poly_out / "polymarket_markets_today_utc_both.csv"
        if combined.exists():
            paths.append(combined)
        # Per-asset files
        for asset in ("BTC", "ETH"):
            per_asset = poly_out / asset / "polymarket_markets_today_utc.csv"
            if per_asset.exists():
                paths.append(per_asset)

    return paths
