"""CoinGecko auth must follow the base URL — the two cannot be configured independently.

WHY THIS EXISTS (a live production defect, 2026-08-22).

`COINGECKO_BASE_URL` was a setting and the auth header was the string literal
`"x-cg-demo-api-key"` in `_make_request`. After upgrading to a paid plan, the paid key was
put on Railway but the base URL stayed `api.coingecko.com`, so every request presented a
PRO key in the DEMO header to the DEMO host.

What made it invisible for as long as it was:

  * Key-required endpoints answered 400 `error_code 10010` ("If you are using Pro API key,
    please change your root URL ... to pro-api.coingecko.com").
  * But the endpoints the app actually calls — `/coins/{id}`, `/search`, `/simple/price` —
    are PUBLIC. CoinGecko ignored the unusable header and served them anonymously with
    HTTP 200. Measured: the same request with no key at all, with the mismatched key, and
    correctly against pro-api all returned 200.

So the logs were clean, the data was right, and production was silently running on the free
public tier with the paid key doing nothing — subject to shared-IP public limits, and not
attributing usage to the plan whose commercial licence is the reason it was bought.

Deriving the header from the host removes the drift: one variable moves both.
"""
from __future__ import annotations

import pytest

from app.integrations.coingecko import CoinGeckoClient


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://api.coingecko.com/api/v3", "x-cg-demo-api-key"),
        ("https://pro-api.coingecko.com/api/v3", "x-cg-pro-api-key"),
        ("https://pro-api.coingecko.com/api/v3/", "x-cg-pro-api-key"),
    ],
)
def test_the_auth_header_follows_the_base_url(base_url, expected):
    client = CoinGeckoClient()
    client.base_url = base_url
    assert client._auth_header == expected


def test_the_header_is_never_hardcoded_at_the_call_site():
    """The defect was a literal at the point of the HTTP call. If one comes back, the
    property above can be correct and still never be used — which is exactly what shipped.

    Scans the WHOLE request path, not one function: `_make_request` grew a retry/dedup
    wrapper and the actual `client.get(...)` moved down into `_request_once`. Pinning the
    scan to a single method name is how a guard like this goes vacuous — it would keep
    passing while the header literal sat one frame away.
    """
    import inspect

    path = "\n".join(
        inspect.getsource(fn)
        for fn in (CoinGeckoClient._make_request, CoinGeckoClient._request_once)
    )
    stripped = "\n".join(
        ln for ln in path.splitlines() if not ln.lstrip().startswith("#")
    )
    for literal in ("x-cg-demo-api-key", "x-cg-pro-api-key"):
        assert literal not in stripped, (
            f"the request path hardcodes {literal!r} again — it must read "
            "`self._auth_header`, or the header and the base URL can drift apart the way "
            "they did in production."
        )
    assert "_auth_header" in stripped, "the request path no longer derives the header"

    # Anti-vacuity: prove the scan actually reaches the code that makes the call.
    assert "client.get(" in stripped, "the scan no longer covers the HTTP call site"


def test_the_rate_limit_is_configurable_and_bounds_the_window(monkeypatch):
    """The limiter was pinned at 25/min for the free Demo plan's 30/min ceiling. On a paid
    plan that is a self-imposed throttle, so it has to be a setting — and the sliding window
    must actually be sized from it, or raising the setting changes nothing.

    ⚠️ Asserting against the DEFAULT would be vacuous: a hardcoded `maxlen=25` equals the
    default 25 and passes. Mutation-tested — this failed to catch exactly that until the
    setting was moved off its default first.
    """
    from app.integrations import coingecko as mod

    monkeypatch.setattr(mod.settings, "COINGECKO_MAX_CALLS_PER_MINUTE", 137)
    client = CoinGeckoClient()
    assert client._max_calls_per_minute == 137, "the limit ignores the setting"
    assert client._rate_window.maxlen == 137, (
        "the deque is sized independently of the limit, so raising the setting throttles "
        "anyway — the window is what the limiter actually reads"
    )
