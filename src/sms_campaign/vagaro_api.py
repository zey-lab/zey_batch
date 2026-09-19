"""Vagaro Enterprise Business API client (customer lookup).

Two things aren't documented anywhere Vagaro publishes:
- The edge (Incapsula) blocks any request without a browser-shaped
  User-Agent -- found 2026-09-19 after Vagaro support suggested trying one
  as a troubleshooting step. It is not optional; every request needs it.
- The {region} path segment is not the generic "us" the docs example
  shows -- it's the specific shard this business's dashboard lives on
  (us04, confirmed against the account's actual customer data).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# Module-level so a token survives across webhook requests within the same
# long-running receiver process instead of spending a fresh one every time.
_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


class VagaroAPIError(RuntimeError):
    """A Vagaro API request failed for a reason other than 'not found'."""


def _base_url() -> str:
    region = os.environ.get("VAGARO_API_REGION", "us04")
    return f"https://api.vagaro.com/{region}/api/v2"


def _post(path: str, body: dict, extra_headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{_base_url()}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
            **(extra_headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise
        detail = exc.read().decode(errors="replace")
        raise VagaroAPIError(f"Vagaro API {path} failed with HTTP {exc.code}: {detail[:300]}") from exc


def get_access_token() -> str:
    """Return a cached token, refreshing shortly before it actually expires."""
    if _token_cache["token"] and time.monotonic() < _token_cache["expires_at"]:
        return _token_cache["token"]  # type: ignore[return-value]

    result = _post("/merchants/generate-access-token", {
        "clientId": os.environ["VAGARO_API_CLIENT_ID"],
        "clientSecretKey": os.environ["VAGARO_API_CLIENT_SECRET"],
        "scope": "read access",
    })
    data = result.get("data", {})
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.monotonic() + max(expires_in - 60, 60)
    return token


def fetch_customer(vagaro_customer_id: str) -> dict | None:
    """Fetch one customer by Vagaro's encrypted id.

    Returns the same field shape as a Vagaro "customer" webhook payload
    (customerFirstName, streetAddress, regionCode, ...), so the result can
    be handed straight to ZeyDataStore.sync_customer_from_webhook. None if
    Vagaro has no such customer.
    """
    token = get_access_token()
    try:
        result = _post(
            "/customers",
            {"businessId": os.environ["VAGARO_BUSINESS_ID"], "customerId": vagaro_customer_id},
            extra_headers={"accessToken": token},
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return result.get("data")
