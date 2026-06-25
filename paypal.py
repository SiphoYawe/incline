"""paypal.py — PayPal Orders v2 (sandbox) client (S-E4-3).

The "money" pillar. This is a thin, synchronous client over the PayPal
Checkout Orders v2 REST API. The integrator wires `/pay/create` and
`/pay/capture` in app.py around these three calls:

    1. POST /pay/create  → create_order(amount, currency, return_url, cancel_url)
                            → return approval_url to redirect the buyer to.
    2. buyer approves in the PayPal sandbox UI.
    3. GET/POST /pay/capture?token=<order_id>
                            → capture_order(order_id)  (expect status COMPLETED)
                            → ledger.record_sale(order_id, tool) inline.

We capture **synchronously** from the return URL (architecture §6.4) for demo
reliability — the live counter must tick on a real sale inside the ~2-minute
window with nobody at the keyboard. **Webhook verification is P1 only**; the
synchronous capture in `/pay/capture` is the demo path, so no webhook is wired
here.

Env: PAYPAL_CLIENT_ID, PAYPAL_SECRET, PAYPAL_BASE
(defaults to https://api-m.sandbox.paypal.com — sandbox only for the P0 demo).
No secrets in code; everything comes from the environment.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import requests

_TIMEOUT = 15  # seconds — every call is bounded so an endpoint never hangs.


def _base() -> str:
    return os.environ.get("PAYPAL_BASE", "https://api-m.sandbox.paypal.com").rstrip("/")


class PayPalError(RuntimeError):
    """Any PayPal API / network failure, wrapped so the endpoint can handle it.

    Carries the HTTP status and response body when available so the caller can
    log a useful audit line instead of a bare stack trace.
    """

    def __init__(self, message: str, *, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


# ── OAuth token (client-credentials) ─────────────────────────────────────
# Optional in-process cache: tokens live ~9h; we refresh a minute early.
_token_cache: dict[str, float | str] = {"value": "", "expires_at": 0.0}


def get_access_token() -> str:
    """Fetch (and briefly cache) a bearer token via client-credentials grant.

    POST {BASE}/v1/oauth2/token with HTTP Basic auth (client_id, secret) and
    body grant_type=client_credentials. Returns the access_token string.
    """
    now = time.monotonic()
    cached = _token_cache.get("value")
    if cached and now < float(_token_cache.get("expires_at", 0.0)):
        return str(cached)

    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    secret = os.environ.get("PAYPAL_SECRET")
    if not client_id or not secret:
        raise PayPalError("PAYPAL_CLIENT_ID / PAYPAL_SECRET not set in environment")

    try:
        resp = requests.post(
            f"{_base()}/v1/oauth2/token",
            auth=(client_id, secret),
            headers={"Accept": "application/json"},
            data={"grant_type": "client_credentials"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else None
        status = exc.response.status_code if exc.response is not None else None
        raise PayPalError(f"OAuth token request failed: {exc}", status=status, body=body) from exc
    except requests.RequestException as exc:
        raise PayPalError(f"OAuth token network error: {exc}") from exc

    token = data.get("access_token")
    if not token:
        raise PayPalError("OAuth response missing access_token", body=str(data))

    # Cache with a 60s safety margin before the reported expiry.
    expires_in = float(data.get("expires_in", 0) or 0)
    _token_cache["value"] = token
    _token_cache["expires_at"] = now + max(expires_in - 60, 0)
    return token


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
    }


# ── create order ─────────────────────────────────────────────────────────
def create_order(amount, currency: str, return_url: str, cancel_url: str) -> dict:
    """Create a CAPTURE-intent order and return its id + approval URL.

    POST {BASE}/v2/checkout/orders. Returns:
        {"id": <order id>, "approval_url": <href where the buyer approves>}

    The approval_url is the link in the response whose rel is "approve"
    (or "payer-action") — that is where /pay/create redirects the buyer.
    """
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"amount": {"currency_code": currency, "value": f"{float(amount):.2f}"}}
        ],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "user_action": "PAY_NOW",
            "brand_name": "Incline",
        },
    }

    try:
        resp = requests.post(
            f"{_base()}/v2/checkout/orders",
            headers=_auth_headers(),
            json=body,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        order = resp.json()
    except requests.HTTPError as exc:
        b = exc.response.text if exc.response is not None else None
        s = exc.response.status_code if exc.response is not None else None
        raise PayPalError(f"create_order failed: {exc}", status=s, body=b) from exc
    except requests.RequestException as exc:
        raise PayPalError(f"create_order network error: {exc}") from exc

    order_id = order.get("id")
    if not order_id:
        raise PayPalError("create_order response missing order id", body=str(order))

    approval_url = None
    for link in order.get("links", []) or []:
        if link.get("rel") in ("approve", "payer-action"):
            approval_url = link.get("href")
            break
    if not approval_url:
        raise PayPalError(
            "create_order response missing approve/payer-action link", body=str(order)
        )

    return {"id": order_id, "approval_url": approval_url}


# ── capture order ────────────────────────────────────────────────────────
def capture_order(order_id: str) -> dict:
    """Capture an approved order and return the parsed JSON.

    POST {BASE}/v2/checkout/orders/{order_id}/capture. The caller checks
    `status == "COMPLETED"` before calling ledger.record_sale(...).
    """
    try:
        resp = requests.post(
            f"{_base()}/v2/checkout/orders/{order_id}/capture",
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        b = exc.response.text if exc.response is not None else None
        s = exc.response.status_code if exc.response is not None else None
        raise PayPalError(f"capture_order failed: {exc}", status=s, body=b) from exc
    except requests.RequestException as exc:
        raise PayPalError(f"capture_order network error: {exc}") from exc
