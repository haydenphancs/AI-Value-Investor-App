"""Tests for APNs host routing (per-token environment).

Pure function only — no network, no Supabase. Guards the bug where every token
was routed to one global host and valid mismatched-environment tokens got pruned.
"""

from app.services.push_service import host_for_environment, _HOSTS


def test_prod_token_routes_to_prod_host():
    assert host_for_environment("production", "sandbox") == _HOSTS["production"]


def test_sandbox_token_routes_to_sandbox_host_even_when_server_default_is_prod():
    # The core bug: a sandbox token must NOT be sent to the prod host just because
    # the server's global APNS_ENV is production.
    assert host_for_environment("sandbox", "production") == _HOSTS["sandbox"]


def test_none_environment_falls_back_to_server_default():
    assert host_for_environment(None, "production") == _HOSTS["production"]
    assert host_for_environment(None, "sandbox") == _HOSTS["sandbox"]


def test_unknown_environment_falls_back_to_sandbox():
    # Never crash / never route somewhere invalid on a bad value.
    assert host_for_environment("weird", "also-weird") == _HOSTS["sandbox"]


def test_case_insensitive():
    assert host_for_environment("PRODUCTION", "sandbox") == _HOSTS["production"]


# ── Every Supabase call inside the async send path must be off-thread ─────────

def test_send_to_user_never_blocks_the_event_loop_on_supabase():
    """`send_to_user` is `async def` but the Supabase SDK is synchronous (CLAUDE.md invariant
    #5 — no ORM). It runs inside the Updates sweeper's loop, so a blocking DB round-trip here
    stalls the WHOLE event loop — every in-flight request in the process — once per recipient
    of every alert.

    `push_dispatch_service.py` wraps all four of its Supabase calls in `asyncio.to_thread`;
    this module did not, which is invariant #6 ("never block the event loop with sync I/O").
    """
    import inspect

    from app.services.push_service import PushService

    src = inspect.getsource(PushService.send_to_user)

    for helper in ("_device_tokens_for", "_prune_token"):
        assert helper in src, f"{helper} should still be called from send_to_user"
        call_site = src[src.index(helper) - 60: src.index(helper) + len(helper)]
        assert "asyncio.to_thread" in call_site, (
            f"{helper} does synchronous Supabase I/O — it must be reached via "
            f"`await asyncio.to_thread(...)` from this async method"
        )

    # And no bare `self.supabase.` in the async body, which is the shape that regresses.
    assert "self.supabase.table(" not in src, (
        "a direct Supabase call appeared in the async send path — wrap it in "
        "asyncio.to_thread like the two helpers"
    )
