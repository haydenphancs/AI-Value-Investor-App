"""The test suite must never reach the network.

WHY THIS FILE EXISTS — the rule existed and was quietly ignored for months.

`.claude/rules/testing.md` forbids hitting live FMP / Gemini / CoinGecko / FRED /
Supabase from the suite, but nothing enforced it, and a live call is INVISIBLE in a
pass/fail run: every one of these call sites is fail-safe, so a call that succeeds
against production and a call that is blocked both leave the suite green. Only the first
one couples the result to production data.

Measured by denying `getaddrinfo` and counting: **214 live calls to the production
Supabase project per run**, chiefly

  * test_ticker_report_schema_parity.py — the backend↔iOS contract guard, whose failure
    means a decode crash in production, asserting against whatever rows prod held; and
  * test_chat_credits.py reaching `claim_free_followup`, a SECURITY DEFINER function
    with `row_security = off` that UPDATEs `chat_sessions`. Harmless only because
    `session_id="sess-1"` fails the UUID cast — one edit from mutating production.

The block itself lives in `backend/conftest.py` (it must be installed before any test
module imports). This file is the guard on the guard: if someone removes it, these fail
deterministically instead of the suite silently going back to talking to prod.

Same shape as `test_sentry_inert_in_tests.py` and `test_gc_is_disabled_for_the_session.py`,
the two other conftest-level session guards.
"""

import socket
from pathlib import Path

import pytest

from conftest import BLOCKED_NETWORK_CALLS, NetworkCallInTests


def test_dns_resolution_is_blocked():
    """The primary block: nothing can even resolve an external host."""
    with pytest.raises(NetworkCallInTests) as exc:
        socket.getaddrinfo("guard-selftest.invalid", 443)
    # The message must name the host, or a new offender reads as a generic DNS failure.
    assert "guard-selftest.invalid" in str(exc.value)


def test_socket_connect_is_blocked():
    """Second layer: an IP literal skips DNS entirely, so `connect` is blocked too."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkCallInTests):
            s.connect(("192.0.2.1", 80))
    finally:
        s.close()


def test_loopback_is_still_allowed():
    """Deliberate: blocking loopback would break any future local fixture server and
    buys nothing — the risk is egress to third parties, not to this machine."""
    assert socket.getaddrinfo("127.0.0.1", 0)
    assert socket.getaddrinfo("localhost", 0)


def test_the_block_is_actually_installed_by_conftest():
    """Anti-vacuity. The three tests above would also pass against a stale import if the
    guard were removed from conftest but its symbols left behind, so assert the LIVE
    socket module is the patched one."""
    assert socket.getaddrinfo.__name__ == "_blocked_getaddrinfo", (
        "conftest's network block is not installed — the suite can reach production"
    )
    assert socket.socket.connect.__name__ == "_blocked_connect"


def test_the_run_is_failed_when_anything_was_blocked():
    """RAISING IS NOT ENOUGH, and this is the subtle half.

    `NetworkCallInTests` is a RuntimeError, so the fail-safe `except Exception` blocks
    that wrap nearly every upstream call swallow it — which is deliberate (the point is
    to stop the packet, not to break a degradation path) but means a new offender would
    once again produce a GREEN run. Being swallowed is exactly how the original 214 hid.
    So conftest also RECORDS every block and fails the session in `pytest_sessionfinish`.

    This pins that the record and the fail exist. The three tests above deliberately
    trigger blocks, so the list is non-empty by the time the session ends — which is why
    this file probes RFC 5737 / RFC 2606 reserved addresses, which conftest raises on
    but deliberately does NOT count — otherwise the guard would fail every run on its
    own verification.
    """
    conftest_src = (Path(__file__).resolve().parent.parent / "conftest.py").read_text()
    code = "\n".join(
        line for line in conftest_src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "BLOCKED_NETWORK_CALLS.append" in code, "blocks are not recorded"
    assert "session.exitstatus = 1" in code, "a blocked call does not fail the run"
    assert isinstance(BLOCKED_NETWORK_CALLS, list)


def test_conftest_still_contains_the_block():
    """Source-level companion: catches a removal that a stale interpreter would hide."""
    conftest = (Path(__file__).resolve().parent.parent / "conftest.py").read_text()
    # Comment-stripped, per the repo's source-scan rules — the rationale block above the
    # code mentions every one of these tokens, so an un-stripped scan would pass on prose.
    code = "\n".join(
        line for line in conftest.splitlines() if not line.lstrip().startswith("#")
    )
    assert "socket.getaddrinfo = _blocked_getaddrinfo" in code
    assert "socket.socket.connect = _blocked_connect" in code
