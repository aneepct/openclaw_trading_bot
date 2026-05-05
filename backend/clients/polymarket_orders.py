"""Polymarket CLOB authenticated order client.

Provides helpers to:
  1. Derive/create L2 API credentials from a wallet private key (L1 auth).
  2. Build an authenticated ClobClient ready for order placement (L2 auth).
  3. Place a BUY or SELL order for a given CLOB token ID.

Required environment variables (see config.py):
  POLYMARKET_PRIVATE_KEY    — Wallet private key (hex string, no 0x prefix required).
  POLYMARKET_FUNDER_ADDRESS — Funder/proxy wallet address (leave empty for EOA wallets).

Usage:
  from clients.polymarket_orders import create_order

  result = await create_order(
      token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
      price=0.65,
      size=100.0,
      side="BUY",
  )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import — py_clob_client_v2 is optional at import time so the rest of
# the app still starts up if the package is not installed (e.g. local dev
# without Polygon credentials configured).
# ---------------------------------------------------------------------------

def _import_clob():
    """Return (ClobClient, OrderArgs, PartialCreateOrderOptions, BUY, SELL) or raise."""
    try:
        from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions  # type: ignore
        from py_clob_client_v2.order_builder.constants import BUY, SELL  # type: ignore
        return ClobClient, OrderArgs, PartialCreateOrderOptions, BUY, SELL
    except ImportError as exc:
        raise RuntimeError(
            "py-clob-client-v2 is not installed. "
            "Add it to requirements.txt and rebuild the container."
        ) from exc


def _get_config():
    """Return Polymarket config values from config.py."""
    import config as app_config  # local import avoids circular dependency
    return (
        app_config.POLYMARKET_PRIVATE_KEY,
        app_config.POLYMARKET_FUNDER_ADDRESS,
        app_config.POLYMARKET_CHAIN_ID,
        app_config.POLYMARKET_SIGNATURE_TYPE,
    )


# ---------------------------------------------------------------------------
# In-memory credentials cache — derived once per process lifetime.
# ---------------------------------------------------------------------------
_cached_creds: Optional[dict] = None


def derive_api_credentials() -> dict:
    """Create or derive Polymarket L2 API credentials from the wallet private key.

    The result is cached in-process so the derivation (which involves an HTTP
    call to the CLOB server) only happens once per server restart.

    Returns a dict with keys ``apiKey``, ``secret``, ``passphrase``.
    Raises ``RuntimeError`` if ``POLYMARKET_PRIVATE_KEY`` is not set.
    """
    global _cached_creds
    if _cached_creds is not None:
        return _cached_creds

    private_key, funder_address, chain_id, _ = _get_config()
    if not private_key:
        raise RuntimeError(
            "POLYMARKET_PRIVATE_KEY is not set. "
            "Set it in your .env file or environment to enable order placement."
        )

    ClobClient, *_ = _import_clob()[0:1], *_import_clob()[1:]
    ClobClient = _import_clob()[0]

    # L1 client — no credentials yet, used only to derive them.
    l1_client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=chain_id,
        key=private_key,
    )

    creds = l1_client.create_or_derive_api_key()
    logger.info("Polymarket API credentials derived (apiKey=%s…)", str(creds.get("apiKey", ""))[:8])
    _cached_creds = creds
    return creds


def build_authed_client():
    """Return an L2-authenticated ClobClient ready for order placement."""
    private_key, funder_address, chain_id, sig_type = _get_config()
    ClobClient = _import_clob()[0]
    creds = derive_api_credentials()

    kwargs: dict = {
        "host": "https://clob.polymarket.com",
        "chain_id": chain_id,
        "key": private_key,
        "creds": creds,
        "signature_type": sig_type,
    }
    if funder_address:
        kwargs["funder"] = funder_address

    return ClobClient(**kwargs)


async def create_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> dict:
    """Place a BUY or SELL order on Polymarket CLOB.

    Args:
        token_id:  CLOB token ID for the Yes outcome (from ``get_yes_clob_token_id``).
        price:     Limit price in USDC (0 < price < 1, e.g. 0.65 = 65¢).
        size:      Order size in USDC (e.g. 100 = $100).
        side:      ``"BUY"`` or ``"SELL"`` (case-insensitive).
        tick_size: Minimum price increment — use ``"0.01"`` for most markets.
        neg_risk:  Set ``True`` for negative-risk markets (rare; leave ``False``).

    Returns:
        The raw response dict from the CLOB API, typically containing
        ``orderId``, ``status``, ``price``, ``size``, etc.

    Raises:
        ValueError:   Invalid ``side`` value or ``price``/``size`` out of range.
        RuntimeError: Credentials not configured or SDK not installed.
    """
    side_upper = side.strip().upper()
    if side_upper not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if not (0 < price < 1):
        raise ValueError(f"price must be between 0 and 1 exclusive, got {price}")
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")

    ClobClient, OrderArgs, PartialCreateOrderOptions, BUY, SELL = _import_clob()
    order_side = BUY if side_upper == "BUY" else SELL

    client = build_authed_client()

    # ClobClient methods are synchronous — run in a thread to avoid blocking
    # the asyncio event loop.
    def _place() -> dict:
        return client.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            ),
            options=PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk),
        )

    result = await asyncio.to_thread(_place)
    logger.info(
        "Order placed — side=%s token=%s… price=%.4f size=%.2f result=%s",
        side_upper,
        token_id[:16],
        price,
        size,
        result,
    )
    return result
