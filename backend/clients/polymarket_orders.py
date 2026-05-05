"""Polymarket CLOB authenticated order client.

Provides helpers to:
  1. Derive L2 API credentials by signing EIP-712 directly with eth_account.
  2. Build an authenticated ClobClient ready for order placement (L2 auth).
  3. Place a BUY or SELL order for a given CLOB token ID.

Required environment variables (see config.py):
  POLYMARKET_PRIVATE_KEY     — Wallet private key (hex string, 0x prefix optional).
  POLYMARKET_FUNDER_ADDRESS  — Proxy/funder wallet address shown on Polymarket.com.
  POLYMARKET_SIGNATURE_TYPE  — 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE (default 2 when
                               a funder address is set; most Polymarket users need 2).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Optional

import httpx
from eth_account import Account

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"

_DOMAIN_NAME    = "ClobAuthDomain"
_DOMAIN_VERSION = "1"
_AUTH_MESSAGE   = "This message attests that I control the given wallet"

# ---------------------------------------------------------------------------
# In-memory credentials cache
# ---------------------------------------------------------------------------
_cached_creds = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_clob():
    try:
        from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions  # type: ignore
        from py_clob_client_v2.order_builder.constants import BUY, SELL  # type: ignore
        return ClobClient, OrderArgs, PartialCreateOrderOptions, BUY, SELL
    except ImportError as exc:
        raise RuntimeError(
            "py-clob-client-v2 is not installed. Add it to requirements.txt and rebuild."
        ) from exc


def _import_api_creds():
    try:
        from py_clob_client_v2.clob_types import ApiCreds  # type: ignore
        return ApiCreds
    except ImportError as exc:
        raise RuntimeError("py-clob-client-v2 is not installed.") from exc


def _get_config():
    import config as app_config
    return (
        app_config.POLYMARKET_PRIVATE_KEY,
        app_config.POLYMARKET_FUNDER_ADDRESS,
        app_config.POLYMARKET_CHAIN_ID,
        app_config.POLYMARKET_SIGNATURE_TYPE,
    )


def _get_server_timestamp() -> int:
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
    """Build POLY_* L1 authentication headers via EIP-712 (eth-account sign_typed_data)."""
    account = Account.from_key(private_key)
    address = account.address
    ts = _get_server_timestamp()

    signed = Account.sign_typed_data(
        private_key=private_key,
        domain_data={
            "name":    _DOMAIN_NAME,
            "version": _DOMAIN_VERSION,
            "chainId": chain_id,
        },
        message_types={
            "ClobAuth": [
                {"name": "address",   "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce",     "type": "uint256"},
                {"name": "message",   "type": "string"},
            ]
        },
        message_data={
            "address":   address,
            "timestamp": str(ts),
            "nonce":     nonce,
            "message":   _AUTH_MESSAGE,
        },
    )

    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    logger.debug("L1 auth: address=%s ts=%s nonce=%s", address, ts, nonce)

    return {
        "POLY_ADDRESS":   address,
        "POLY_SIGNATURE": sig_hex,
        "POLY_TIMESTAMP": str(ts),
        "POLY_NONCE":     str(nonce),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def derive_api_credentials():
    """Derive Polymarket L2 API credentials via direct EIP-712 REST call.

    Result is cached in-process. Raises ``RuntimeError`` if
    ``POLYMARKET_PRIVATE_KEY`` is not set.
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
            # Key doesn't exist yet — create it
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


def build_authed_client(sig_type_override: int = None):
    """Return an L2-authenticated ClobClient ready for order placement."""
    private_key, funder_address, chain_id, sig_type = _get_config()
    if sig_type_override is not None:
        sig_type = sig_type_override
    ClobClient = _import_clob()[0]
    creds = derive_api_credentials()

    kwargs: dict = {
        "host":            CLOB_HOST,
        "chain_id":        chain_id,
        "key":             private_key,
        "creds":           creds,
        "signature_type":  sig_type,
        "use_server_time": True,
    }
    if funder_address:
        kwargs["funder"] = funder_address

    return ClobClient(**kwargs)


def diagnose_wallet() -> dict:
    """Return diagnostic info about the configured wallet without placing orders.

    Checks:
    - EOA address derived from the private key
    - Configured funder address and signature type
    - Whether credentials can be derived (L1 auth)
    - Which balance endpoint responds (confirms L2 auth + correct funder)
    """
    from eth_account import Account as _Account

    private_key, funder_address, chain_id, sig_type = _get_config()
    if not private_key:
        return {"error": "POLYMARKET_PRIVATE_KEY is not set"}

    eoa_address = _Account.from_key(private_key).address
    result: dict = {
        "eoa_address":      eoa_address,
        "funder_address":   funder_address or "(not set — will use EOA)",
        "chain_id":         chain_id,
        "signature_type":   sig_type,
        "sig_type_note":    {0: "EOA", 1: "POLY_PROXY (Magic Link)", 2: "GNOSIS_SAFE"}.get(sig_type, str(sig_type)),
        "eoa_equals_funder": (funder_address or "").lower() == eoa_address.lower(),
        "auth_test":        None,
        "balance_test":     None,
        "recommendation":   None,
    }

    # Test L1 auth
    try:
        creds = derive_api_credentials()
        result["auth_test"] = f"OK — apiKey prefix: {str(creds.api_key)[:8]}…"
    except Exception as exc:
        result["auth_test"] = f"FAILED: {exc}"
        return result

    # Test each signature type against the balance endpoint (lightweight)
    working_types = []
    for try_type in (0, 1, 2):
        try:
            client = build_authed_client(sig_type_override=try_type)
            balance_resp = client.get_balance_allowance()  # L2 authenticated call, no order
            if balance_resp is not None:
                working_types.append(try_type)
                result[f"type_{try_type}_balance"] = "OK"
        except Exception as exc:
            result[f"type_{try_type}_balance"] = f"FAILED: {str(exc)[:80]}"

    type_names = {0: "EOA", 1: "POLY_PROXY", 2: "GNOSIS_SAFE"}
    if working_types:
        result["balance_test"] = f"Working types: {[type_names[t] for t in working_types]}"
        result["recommendation"] = (
            f"Set POLYMARKET_SIGNATURE_TYPE={working_types[0]} "
            f"({type_names[working_types[0]]}) in docker-compose.yml and .env"
        )
    else:
        result["balance_test"] = "All types failed — check funder address and private key"
        result["recommendation"] = (
            "Verify POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS are correct. "
            "The funder should be the address shown on polymarket.com, not the signing key address."
        )

    return result


async def create_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> dict:
    """Place a BUY or SELL limit order on Polymarket CLOB.

    Args:
        token_id:  Yes-outcome CLOB token ID (from get_yes_clob_token_id).
        price:     Limit price in USDC, 0 < price < 1 (e.g. 0.65 = 65c).
        size:      Order size in USDC (e.g. 100 = $100 notional).
        side:      "BUY" or "SELL" (case-insensitive).
        tick_size: Minimum price increment — "0.01" for most markets.
        neg_risk:  True only for negative-risk markets (rare).
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

    def _place() -> dict:
        global _cached_creds
        try:
            return client.create_and_post_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=order_side,
                ),
                options=PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk),
            )
        except Exception as exc:
            # Clear credential cache on signature rejection so next call re-derives.
            if "invalid signature" in str(exc).lower() or "401" in str(exc) or "403" in str(exc):
                _cached_creds = None
                logger.warning(
                    "Order rejected with signature error — credential cache cleared. "
                    "If this persists, check POLYMARKET_SIGNATURE_TYPE in .env "
                    "(0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE). "
                    "Most Polymarket.com accounts require type 2."
                )
            raise

    result = await asyncio.to_thread(_place)
    logger.info(
        "Order placed — side=%s token=%s... price=%.4f size=%.2f result=%s",
        side_upper, token_id[:16], price, size, result,
    )
    return result
