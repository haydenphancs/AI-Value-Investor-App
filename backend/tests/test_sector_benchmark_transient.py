"""Transient-error classification + retry for the sector/industry benchmark lookup.

A stale HTTP/2 pooled connection that Supabase closed (GOAWAY / idle timeout)
surfaces on REUSE as `LocalProtocolError('Invalid input ConnectionInputs.
RECV_HEADERS in state ConnectionState.CLOSED')` from the h2 state machine. It's a
connection-reuse race, not a bug — `_fetch_rows` must retry it (a fresh connection
succeeds) and `get_benchmarks` must log it at WARNING, not ERROR (no Sentry page).
"""
from __future__ import annotations

import httpx
import pytest

from app.services import sector_benchmark_lookup as sbl
from app.services.sector_benchmark_lookup import SectorBenchmarkLookup, _is_transient


# ── _is_transient classification ─────────────────────────────────────────────

def test_local_protocol_closed_connection_is_transient():
    e = httpx.LocalProtocolError(
        "Invalid input ConnectionInputs.RECV_HEADERS in state ConnectionState.CLOSED"
    )
    assert _is_transient(e) is True


def test_httpcore_style_repr_string_is_transient():
    # httpx wraps httpcore, so str(e) can be the repr "LocalProtocolError('... CLOSED')".
    e = httpx.LocalProtocolError(
        "LocalProtocolError('Invalid input ConnectionInputs.SEND_HEADERS "
        "in state ConnectionState.CLOSED')"
    )
    assert _is_transient(e) is True


def test_raw_unwrapped_closed_state_string_is_transient():
    # Even if it arrives as a non-httpx type, the closed-state signature still matches.
    assert _is_transient(Exception("... in state ConnectionState.CLOSED")) is True


def test_remote_protocol_and_server_disconnect_still_transient():
    assert _is_transient(httpx.RemoteProtocolError("Server disconnected")) is True
    assert _is_transient(Exception("Server disconnected without response")) is True
    assert _is_transient(httpx.ConnectError("conn refused")) is True


def test_genuine_local_protocol_bug_is_not_transient():
    # A LocalProtocolError WITHOUT the closed-connection signature is a real client
    # bug and must stay ERROR (not silently retried away).
    e = httpx.LocalProtocolError("Illegal header value b'bad\\r\\n'")
    assert _is_transient(e) is False


def test_ordinary_errors_are_not_transient():
    assert _is_transient(ValueError("nope")) is False
    assert _is_transient(KeyError("median_value")) is False


def test_h2_stream_keyerror_from_transport_is_transient():
    # The stale HTTP/2 reuse race can also surface as a BARE KeyError(<stream_id>)
    # raised from deep inside httpcore/h2 (e.g. KeyError(307), the prod issue). It
    # carries no httpx type and str(e) == "307", so only the traceback ORIGIN
    # distinguishes it. Simulate a raise from an httpcore frame via exec globals.
    try:
        exec("raise KeyError(307)", {"__name__": "httpcore._async.http2"})
    except KeyError as e:
        assert _is_transient(e) is True


def test_h2_error_from_h2_package_is_transient():
    try:
        exec("raise ValueError('stream closed')", {"__name__": "h2.connection"})
    except ValueError as e:
        assert _is_transient(e) is True


def test_schema_drift_keyerror_from_our_module_stays_error():
    # A genuine KeyError raised from OUR OWN code (a missing dict column) must NOT be
    # misclassified as transient — it's a real schema-drift bug that should page.
    try:
        raise KeyError("metric_name")  # traceback tip is THIS test module, not httpcore
    except KeyError as e:
        assert _is_transient(e) is False


# ── _fetch_rows retries the stale-connection blip, then succeeds ──────────────

class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    """Fluent Supabase query stub; each .execute() pops the next scripted item
    (an Exception is raised, a list is returned as resp.data)."""

    def __init__(self, script):
        self._script = list(script)
        self.execute_calls = 0

    # fluent builders all return self
    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def execute(self):
        self.execute_calls += 1
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)


def test_fetch_rows_retries_stale_http2_then_succeeds(monkeypatch):
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)  # no backoff wait
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)  # skip get_supabase()

    good_rows = [
        {"metric_name": "pe_ratio", "period_label": "Q4'25", "median_value": 22.5, "sample_size": 40}
    ]
    lk.supabase = _FakeSupabase([
        httpx.LocalProtocolError(
            "Invalid input ConnectionInputs.RECV_HEADERS in state ConnectionState.CLOSED"
        ),
        good_rows,
    ])

    rows = lk._fetch_rows(
        lk._RICH_COLS, "Technology", ["pe_ratio"], "quarterly",
        industry="Software - Infrastructure",
    )
    assert rows == good_rows
    assert lk.supabase.execute_calls == 2  # blipped once, retried, succeeded


def test_fetch_rows_reraises_after_persistent_transient(monkeypatch):
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)
    lk.supabase = _FakeSupabase([
        httpx.LocalProtocolError("in state ConnectionState.CLOSED"),
    ])
    # Persistent transient → exhausts retries and re-raises (caller degrades + logs WARN).
    with pytest.raises(httpx.LocalProtocolError):
        lk._fetch_rows(lk._RICH_COLS, "Technology", ["pe_ratio"], "quarterly")
    assert lk.supabase.execute_calls == sbl._MAX_FETCH_ATTEMPTS


def test_get_benchmarks_degrades_to_empty_and_warns_on_transient(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)
    lk.supabase = _FakeSupabase([
        httpx.LocalProtocolError("in state ConnectionState.CLOSED"),
    ])
    with caplog.at_level(logging.WARNING, logger=sbl.logger.name):
        out = lk.get_benchmarks(
            industry="Software - Infrastructure", sector="Technology",
            metrics=["pe_ratio"], period_type="quarterly",
        )
    assert out == {"pe_ratio": {}}  # degrades to an empty per-metric dict, never raises
    hits = [r for r in caplog.records if "Industry benchmark lookup failed" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits)  # WARNING, not ERROR


# ── Cloudflare 520, via delegation to the shared classifier ──────────────────
# `_is_transient` used to be implemented in this module and could NOT see a
# Cloudflare 520: Supabase's edge returns an HTML error page, postgrest raises
# APIError('JSON could not be generated') with an INT .code, and its traceback
# frames are `postgrest`, not `httpcore`. That produced the 7-event "Industry
# benchmark lookup failed for … Error 520" Sentry issue. The rule now lives in
# app/utils/supabase_errors.py; these tests pin the delegation.

def _gateway_error(status: int = 520):
    """Build the error the REAL way, so the test can't drift from the client.

    Never assert on str(e): postgrest 1.1.1 defines __repr__ but not __str__, so
    str() renders the raw dict locally and "Error 520:\\nMessage: …" in production.
    """
    from postgrest.exceptions import APIError, generate_default_error_message

    class _R:
        status_code = status
        content = b"<!DOCTYPE html><title>520: Web server is returning an unknown error</title>"

    return APIError(generate_default_error_message(_R()))


def _postgrest_error(code: str):
    from postgrest.exceptions import APIError

    return APIError({"message": "boom", "code": code, "hint": None, "details": None})


def test_cloudflare_520_is_transient_via_delegation():
    # Fails against the pre-delegation implementation — non-vacuous by construction.
    assert _is_transient(_gateway_error(520)) is True


def test_is_transient_delegates_to_the_shared_classifier():
    from app.utils.supabase_errors import is_transient_supabase_error

    for case in (
        _gateway_error(520),                    # transient
        httpx.RemoteProtocolError("Server disconnected"),  # transient
        _postgrest_error("23505"),              # NOT — constraint violation
        _postgrest_error("PGRST116"),           # NOT — no rows
        _gateway_error(404),                    # NOT — deterministic
        ValueError("nope"),                     # NOT
    ):
        assert _is_transient(case) is is_transient_supabase_error(case)


def test_fetch_rows_retries_a_520_then_succeeds(monkeypatch):
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)
    good_rows = [{"metric_name": "pe_ratio", "period_label": "Q4'25",
                  "median_value": 22.5, "sample_size": 40}]
    lk.supabase = _FakeSupabase([_gateway_error(520), good_rows])

    rows = lk._fetch_rows(lk._RICH_COLS, "Technology", ["pe_ratio"], "quarterly")
    assert rows == good_rows
    assert lk.supabase.execute_calls == 2


def test_fetch_rows_does_not_retry_a_23505(monkeypatch):
    """A constraint violation is deterministic — retrying it is a correctness hazard.

    Guards the boundary between "gateway blip" and "PostgREST said no": both arrive
    as the same APIError type, and only `.code` tells them apart.
    """
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)
    lk.supabase = _FakeSupabase([_postgrest_error("23505")])

    from postgrest.exceptions import APIError
    with pytest.raises(APIError):
        lk._fetch_rows(lk._RICH_COLS, "Technology", ["pe_ratio"], "quarterly")
    assert lk.supabase.execute_calls == 1  # one attempt, not _MAX_FETCH_ATTEMPTS


def test_get_benchmarks_warns_not_errors_on_a_520(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(sbl.time, "sleep", lambda *_a, **_k: None)
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)
    lk.supabase = _FakeSupabase([_gateway_error(520)])

    with caplog.at_level(logging.WARNING, logger=sbl.logger.name):
        out = lk.get_benchmarks(
            industry="Software - Infrastructure", sector="Technology",
            metrics=["pe_ratio"], period_type="quarterly",
        )
    assert out == {"pe_ratio": {}}
    hits = [r for r in caplog.records if "Industry benchmark lookup failed" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits)
