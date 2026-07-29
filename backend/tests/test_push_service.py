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
