"""Auth rate limits key off an address the CALLER cannot choose.

`railway.toml` and the Dockerfile both start uvicorn with
`--proxy-headers --forwarded-allow-ips='*'`. That sets `ProxyHeadersMiddleware.always_trust`,
and in that mode uvicorn rewrites `scope["client"]` to the **leftmost** `X-Forwarded-For`
entry — which is whatever the caller sent before Railway's edge appended the real peer
address. So `request.client.host` was attacker-controlled, and every per-IP auth limiter keyed
off it:

    login:{ip}      10/min   → unlimited password guesses against a known address
    register:{ip}    5/min   → unlimited Supabase confirmation emails to an arbitrary address
    oauth:/exchange:{ip}     → unlimited
    forgot:ip:/reset:ip:     → the IP half voided (the email half still held)
    live_price _MAX_CONNECTIONS_PER_KEY → the anonymous WebSocket cap voided

Rotating one header per request produced a brand-new bucket every time, so the limiter
returned True on every call and the 429 never fired.

Two changes are pinned here:
  1. `trusted_client_ip` takes the RIGHTMOST entry — the one our own edge appended, the only
     part of the header a caller cannot forge.
  2. `/auth/login` and `/auth/register` gained a per-EMAIL limiter. IP alone is never enough
     on a credential endpoint: an attacker with a pool of real addresses still gets the full
     per-IP budget against one victim.

No network, no Supabase.
"""
from __future__ import annotations

import pytest

from app.core.security import rate_limiter, trusted_client_ip


class _Headers:
    def __init__(self, mapping):
        self._m = {k.lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._m.get(key.lower(), default)


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    """Models what uvicorn hands the handler under --forwarded-allow-ips='*': `client.host`
    has ALREADY been rewritten to the leftmost (spoofed) XFF entry."""

    def __init__(self, xff=None, client_host="10.0.0.9"):
        self.headers = _Headers({"x-forwarded-for": xff} if xff else {})
        self.client = _Client(client_host) if client_host else None


def test_rightmost_entry_wins_over_the_spoofed_leftmost():
    """THE bug: the attacker controls everything left of our edge's appended address."""
    req = _Req(xff="198.51.100.7, 203.0.113.5", client_host="198.51.100.7")
    assert trusted_client_ip(req) == "203.0.113.5"


def test_a_long_forged_chain_still_resolves_to_our_edge():
    forged = ", ".join(f"198.51.100.{i}" for i in range(1, 12))
    req = _Req(xff=f"{forged}, 203.0.113.5", client_host="198.51.100.1")
    assert trusted_client_ip(req) == "203.0.113.5"


def test_rotating_the_spoofed_prefix_does_not_change_the_bucket():
    """The property that actually matters: a rotating header must NOT mint a fresh key."""
    keys = {
        trusted_client_ip(_Req(xff=f"198.51.100.{i}, 203.0.113.5", client_host=f"198.51.100.{i}"))
        for i in range(1, 60)
    }
    assert keys == {"203.0.113.5"}, "rotating X-Forwarded-For still produces distinct buckets"


def test_the_limiter_actually_trips_under_header_rotation():
    """End-to-end over the real limiter, which is what the 429 depends on."""
    rate_limiter._requests.clear()
    allowed = 0
    for i in range(40):
        req = _Req(xff=f"198.51.100.{i}, 203.0.113.5", client_host=f"198.51.100.{i}")
        if rate_limiter.is_allowed(f"login:ip:{trusted_client_ip(req)}", 10, 60):
            allowed += 1
    assert allowed == 10, f"expected the 10/min cap to hold under rotation, got {allowed}"
    rate_limiter._requests.clear()


def test_falls_back_to_client_host_without_the_header():
    """Local dev and any direct-connection deployment must still get a real key."""
    assert trusted_client_ip(_Req(xff=None, client_host="127.0.0.1")) == "127.0.0.1"


@pytest.mark.parametrize("xff", ["", "   ", ",", " , , "])
def test_blank_or_degenerate_headers_fall_back_rather_than_keying_on_empty(xff):
    """An empty key would bucket every caller together — worse than no limiter."""
    assert trusted_client_ip(_Req(xff=xff, client_host="10.1.2.3")) == "10.1.2.3"


def test_no_header_and_no_client_is_a_stable_sentinel_not_a_crash():
    assert trusted_client_ip(_Req(xff=None, client_host=None)) == "unknown"


def test_whitespace_around_entries_is_stripped():
    assert trusted_client_ip(_Req(xff="198.51.100.7 ,  203.0.113.5  ")) == "203.0.113.5"


# ---------------------------------------------------------------------------
# The per-email limiters
# ---------------------------------------------------------------------------


def _auth_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "auth.py"
    ).read_text(encoding="utf-8")


def test_login_has_a_per_email_limiter():
    src = _auth_source()
    assert 'login:email:' in src, (
        "/auth/login is protected by IP alone — an attacker with several addresses still gets "
        "the full per-IP budget against one victim account"
    )
    assert 'login:ip:' in src


def test_register_has_a_per_email_limiter():
    src = _auth_source()
    assert 'register:email:' in src, (
        "/auth/register is capped per IP only, so it mail-bombs whatever address is in the body"
    )


def test_no_auth_handler_reads_client_host_directly():
    """Every site must go through the helper; one straggler re-opens the bypass."""
    src = _auth_source()
    assert "req.client.host" not in src, (
        "an auth handler still derives its rate-limit key from the spoofable client.host"
    )
    assert src.count("trusted_client_ip(req)") >= 7


def test_email_key_is_normalized_so_case_cannot_dodge_the_limit():
    """`Victim@x.com` and `victim@x.com` are one account to Supabase; they must be one bucket."""
    src = _auth_source()
    assert '(request.email or "").strip().lower()' in src


def test_websocket_cap_uses_the_trusted_ip():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "api" / "v1" / "endpoints" / "live_price.py"
    ).read_text(encoding="utf-8")
    assert "trusted_client_ip(websocket)" in src, (
        "the anonymous WebSocket connection cap still keys off a spoofable address"
    )
