"""Polymarket CLOB authenticated order client.

Provides helpers to:
  1. Derive L2 API credentials by signing EIP-712 directly with eth_account
     (bypasses py-clob-client-v2 auth internals to avoid signing issues).
  2. Build an authenticated ClobClient ready for order placement (L2 auth).
  3. Place a BUY or SELL order for a given CLOB token ID.

Required environment variables (see config.py):
  POLYMARKET_PRIVATE_KEY    — Wallet private key (hex string, 0x prefix optional).
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
import time as _time
from typing import Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"

# EIP-712 constants per Polymarket auth spec
_DOMAIN_NAME    = "ClobAuthDomain"
_DOMAIN_VERSION = "1"
_AUTH_MESSAGE   = "This message attests that I control the given wallet"


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


def _import_api_creds():
    try:
        from py_clob_client_v2.clob_types import ApiCreds  # type: ignore
        return ApiCreds
    except ImportError as exc:
        raise RuntimeError("py-clob-client-v2 is not installed.") from exc


def _get_config():
    """Return Polymarket config values from config.py."""
    import config as app_config  # local import avoids circular dependency
    return (
        app_config.POLYMARKET_PRIVATE_KEY,
        app_config.POLYMARKET_FUNDER_ADDRESS,
        app_config.POLYMARKET_CHAIN_ID,
        app_config.POLYMARKET_SIGNATURE_TYPE,
    )


def _get_server_timestamp() -> int:
    """Fetch current timestamp from Polymarket CLOB server (avoids clock-skew errors)."""
    with httpx.Client(timeout=10) as http:
        resp = http.get(f"{CLOB_HOST}/time")
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        ts = data.get("time") or data.get("timestamp") or _time.time()
    else:
        ts = float(data)
    return int(ts)


def _build_eip712_l1_headers(private_key: str, chain_id: int, nonce: int = 0) -> dict:
    """Build POLY_* L1 authentication headers by signing EIP-712 typed data directly.

    Follows the exact Polymarket auth spec:
    https://docs.polymarket.com/api-reference/authentication#eip-712-signing-example
    """
    account = Account.from_key(private_key)
    address  = account.address
    ts       = _get_server_timestamp()

    # EIP-712 typed data per Polymarket spec
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name",    "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address",   "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce",     "type": "uint256"},
                {"name": "message",   "type": "string"},
            ],
        },
        "primaryType": "ClobAuth",
        "domain": {
            "name":    _DOMAIN_NAME,
            "version": _DOMAIN_VERSION,
            "chainId": chain_id,
        },
        "message": {
            "address":   address,
            "timestamp": str(ts),
            "nonce":     nonce,
            "message":   _AUTH_MESSAGE,
        },
    }

    signable = encode_typed_data(full_message=typed_data)
    signed   = Account.sign_message(signable, private_key=private_key)
    sig_hex  = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    logger.debug("L1 auth: address=%s ts=%s nonce=%s sig=%s…", address, ts, nonce, sig_hex[:12])

    return {
        "POLY_ADDRESS":   address,
        "POLY_SIGNATURE": sig_hex,
        "POLY_TIMESTAMP": str(ts),
        "POLY_NONCE":     str(nonce),
    }


# ---------------------------------------------------------------------------
# In-memory credentials cache — derived once per process lifetime.
# ---------------------------------------------------------------------------
_cached_creds = None  # type: ignore


def derive_api_credentials():
    """Derive Polymarket L2 API credentials via direct EIP-712 auth.

    Calls GET /auth/derive-api-key with a hand-crafted L1 signature so we
    are not reliant on the SDK's internal signing utilities (which can fail
    with ``invalid signature`` due to version-specific EIP-712 encoding
    differences).

    The result is cached in-process; subsequent calls are free.

    Returns an ``ApiCreds`` instance suitable for passing to ``ClobClient``.
    Raises ``RuntimeError`` if ``POLYMARKET_PRIVATE_KEY`` is not set.
    """
    global _cached_creds
    if _cached_creds is not None:
        return _cached_creds

    private_key, _funder, chain_id, _ = _get_config()
    if not private_key:
        raise RuntimeError(
            "POLYMARKET_PRIVATE_KEY is not set. "
            "Set it in your .env file or environment to enable order placement."
        )

    headers = _build_eip712_l1_headers(private_key, chain_id, nonce=0)

    with httpx.Client(timeout=15) as http:
        resp = http.get(f"{CLOB_HOST}/auth/derive-api-key", headers=headers)
        if not resp.is_success:
            # Key may not exist yet — create it
            headers = _build_eip712_l1_headers(private_key, chain_id, nonce=0)
            resp = http.post(f"{CLOB_HOST}/auth/api-key", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    ApiCreds = _import_api_creds()
    creds = ApiCreds(
        api_key=data["apiKey"],
        api_secret=data["secret"],
        api_passphrase=data["passphrase"],
    )
    logger.info("Polymarket API credentials derived (apiKey=%s…)", str(creds.api_key)[:8])
    _cached_creds = creds
    return creds


def build_authed_client():
    """Return an L2-authenticated ClobClient ready for order placement."""
    private_key, funder_address, chain_id, sig_type = _get_config()
    ClobClient = _import_clob()[0]
    creds = derive_api_credentials()

    kwargs: dict = {
        "host":           CLOB_HOST,
        "chain_id":       chain_id,
        "key":            private_key,
        "creds":          creds,
        "signature_type": sig_type,
        "use_server_time": True,
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

    ClobClient = _import_clob()[0]

    # L1 client — no credentials yet, used only to derive them.
    # use_server_time=True fetches the CLOB server timestamp before signing,
    # avoiding "invalid signature" errors caused by local clock skew.
    l1_client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=chain_id,
        key=private_key,
        use_server_time=True,
    )

    creds = l1_client.create_or_derive_api_key()
    # ApiCreds is an object — access via attributes, not dict .get()
    api_key_val = getattr(creds, "api_key", "") or getattr(creds, "apiKey", "")
    logger.info("Polymarket API credentials derived (apiKey=%s…)", str(api_key_val)[:8])
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
        # Must use server time so every order signature uses Polymarket's
        # clock, not the container's — prevents "invalid signature" rejections.
        "use_server_time": True,
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
