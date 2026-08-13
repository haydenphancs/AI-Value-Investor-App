"""The shared Supabase transient classifier + the two idempotent retry helpers.

Guards the fix for the production Sentry issue `APIError: JSON could not be
generated`, which was Supabase's Cloudflare edge answering with an HTML
`520: Web server is returning an unknown error` page that postgrest could not parse.

Two rules this file exists to enforce:

1. **Classify on `.code`, never on `str(exc)`.** postgrest 1.1.1 defines `__repr__`
   but not `__str__`, so `str(e)` is the raw dict locally while production renders
   "Error 520:\\nMessage: …". Every error here is therefore built the REAL way —
   through `generate_default_error_message` — so the test cannot drift from the
   installed client.
2. **A 23505 must never be retried.** A Cloudflare 520 says nothing about whether
   Postgres committed, so retry is only admissible for idempotent work; replaying a
   write that already landed is how you turn a blip into truncated data.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import pytest
from postgrest.exceptions import APIError, generate_default_error_message

from app.utils import supabase_errors as se
from app.utils.supabase_errors import (
    is_transient_supabase_error,
    is_unique_violation,
    retry_idempotent_async,
    retry_idempotent_sync,
)


# ── builders: construct errors exactly the way postgrest does ────────────────

class _FakeResponse:
    """Minimal stand-in for httpx.Response, enough for generate_default_error_message."""

    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content


_CLOUDFLARE_520_BODY = (
    b"<!DOCTYPE html>\n<html>\n<head>\n"
    b"<title>supabase.co | 520: Web server is returning an unknown error</title>\n"
    b"</head>\n<body>520</body>\n</html>"
)


def gateway_error(status: int = 520) -> APIError:
    """The real thing: what postgrest raises when the body is not PostgREST JSON."""
    return APIError(
        generate_default_error_message(_FakeResponse(status, _CLOUDFLARE_520_BODY))
    )


def postgrest_error(code: str, message: str = "boom") -> APIError:
    """An ordinary PostgREST JSON error — note `code` is a STR here, not an int."""
    return APIError({"message": message, "code": code, "hint": None, "details": None})


def test_the_builder_really_reproduces_the_production_shape():
    """Non-vacuity guard for every other test in this file.

    If postgrest ever changes `.code` from int to str on this path, the whole
    classifier design premise moves and these tests must be revisited rather than
    silently continuing to pass.
    """
    e = gateway_error(520)
    assert isinstance(e.code, int) and e.code == 520
    assert e.message == "JSON could not be generated"
    assert isinstance(postgrest_error("23505").code, str)


# ── positive: the Cloudflare / gateway family ────────────────────────────────

def test_cloudflare_520_is_transient():
    assert is_transient_supabase_error(gateway_error(520)) is True


@pytest.mark.parametrize(
    "status", [408, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530]
)
def test_whole_gateway_family_is_transient(status):
    assert is_transient_supabase_error(gateway_error(status)) is True


def test_three_digit_string_code_is_read_as_a_status():
    # Defensive: a postgrest patch that stringifies status_code must not regress us.
    assert is_transient_supabase_error(postgrest_error("520")) is True


@pytest.mark.parametrize("sqlstate", ["08000", "08003", "08006", "08P01", "53300"])
def test_connection_lost_sqlstates_are_transient(sqlstate):
    assert is_transient_supabase_error(postgrest_error(sqlstate)) is True


def test_httpx_status_error_uses_response_status():
    # httpx.HTTPStatusError has no `.code`; the status lives on `.response`.
    request = httpx.Request("GET", "https://example.supabase.co/rest/v1/x")
    response = httpx.Response(503, request=request)
    err = httpx.HTTPStatusError("503", request=request, response=response)
    assert is_transient_supabase_error(err) is True


# ── negative controls: THE CRUX ──────────────────────────────────────────────

def test_unique_violation_is_not_transient_but_is_a_unique_violation():
    """The two classifiers must disagree on 23505, deliberately and permanently.

    Transient ⇒ "retry the same write", which for a constraint violation is both
    useless and a correctness hazard. `is_unique_violation` is the separate signal
    callers use to adopt the race winner instead. Pinning both on ONE object stops
    the two from drifting into agreement.
    """
    e = postgrest_error(
        "23505", 'duplicate key value violates unique constraint "portfolios_user_id_name_key"'
    )
    assert is_transient_supabase_error(e) is False
    assert is_unique_violation(e) is True


@pytest.mark.parametrize("code", ["PGRST116", "PGRST204", "PGRST301"])
def test_postgrest_semantic_codes_are_not_transient(code):
    # PGRST116 = no rows, PGRST204 = unknown column (schema drift). Deterministic.
    assert is_transient_supabase_error(postgrest_error(code)) is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_client_4xx_is_not_transient(status):
    assert is_transient_supabase_error(gateway_error(status)) is False


def test_429_is_not_transient():
    """Backpressure. A 0.25s retry makes it worse, so it is excluded on purpose."""
    assert is_transient_supabase_error(gateway_error(429)) is False


def test_bool_code_is_not_read_as_a_status():
    # bool is an int subclass, so `True in {…}` would compare equal to 1 without
    # the explicit guard. Belt-and-braces against a weird payload.
    e = postgrest_error("x")
    e.code = True  # type: ignore[assignment]
    assert is_transient_supabase_error(e) is False


def test_statement_timeout_is_not_transient():
    # 57014: retrying reproduces the same slow query and doubles the load.
    assert is_transient_supabase_error(postgrest_error("57014")) is False


# ── ported from test_sector_benchmark_transient (predicates 3-5 must survive) ─

def test_local_protocol_closed_connection_is_transient():
    e = httpx.LocalProtocolError(
        "Invalid input ConnectionInputs.RECV_HEADERS in state ConnectionState.CLOSED"
    )
    assert is_transient_supabase_error(e) is True


def test_remote_protocol_and_server_disconnect_still_transient():
    assert is_transient_supabase_error(httpx.RemoteProtocolError("Server disconnected")) is True
    assert is_transient_supabase_error(Exception("Server disconnected without response")) is True
    assert is_transient_supabase_error(httpx.ConnectError("conn refused")) is True


def test_genuine_local_protocol_bug_is_not_transient():
    e = httpx.LocalProtocolError("Illegal header value b'bad\\r\\n'")
    assert is_transient_supabase_error(e) is False


def test_ordinary_errors_are_not_transient():
    assert is_transient_supabase_error(ValueError("nope")) is False
    assert is_transient_supabase_error(KeyError("median_value")) is False


def test_h2_stream_keyerror_from_transport_is_transient():
    try:
        exec("raise KeyError(307)", {"__name__": "httpcore._async.http2"})
    except KeyError as e:
        assert is_transient_supabase_error(e) is True


def test_schema_drift_keyerror_from_our_module_stays_error():
    try:
        raise KeyError("metric_name")  # traceback tip is THIS module, not httpcore
    except KeyError as e:
        assert is_transient_supabase_error(e) is False


# ── cause-chain unwrapping ───────────────────────────────────────────────────

def test_explicit_cause_is_unwrapped():
    """`raise WatchlistUnavailableError(...) from api_error` is the real shape.

    Without unwrapping, the wrapper hides the postgrest code and /tracking/assets
    keeps 503-ing users on a blip that should have been retried.
    """
    try:
        try:
            raise gateway_error(520)
        except APIError as inner:
            raise RuntimeError("watchlist read failed") from inner
    except RuntimeError as e:
        assert is_transient_supabase_error(e) is True


def test_implicit_context_is_unwrapped():
    try:
        try:
            raise gateway_error(502)
        except APIError:
            raise RuntimeError("secondary failure")
    except RuntimeError as e:
        assert is_transient_supabase_error(e) is True


def test_unique_violation_is_unwrapped_through_a_wrapper():
    try:
        try:
            raise postgrest_error("23505")
        except APIError as inner:
            raise RuntimeError("claim step failed") from inner
    except RuntimeError as e:
        assert is_unique_violation(e) is True
        assert is_transient_supabase_error(e) is False


def test_cause_chain_is_depth_bounded():
    """The bound must BITE, not merely exist.

    An unrelated bug raised inside `except APIError:` inherits the 520 as its
    __context__. Bounding the walk stops that association from propagating
    indefinitely down a long chain.
    """
    err: BaseException = gateway_error(520)
    for i in range(se.MAX_CAUSE_DEPTH + 3):
        wrapper = RuntimeError(f"layer {i}")
        wrapper.__cause__ = err
        err = wrapper
    assert is_transient_supabase_error(err) is False


def test_shallow_chain_within_the_bound_still_resolves():
    # Complements the test above: proves the bound isn't so tight it breaks the
    # real one-level wrapper that motivated the unwrapping.
    err: BaseException = gateway_error(520)
    for i in range(se.MAX_CAUSE_DEPTH - 2):
        wrapper = RuntimeError(f"layer {i}")
        wrapper.__cause__ = err
        err = wrapper
    assert is_transient_supabase_error(err) is True


def test_self_referential_cause_terminates():
    e = RuntimeError("loop")
    e.__cause__ = e
    assert is_transient_supabase_error(e) is False


def test_two_node_cause_cycle_terminates():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert is_transient_supabase_error(a) is False


# ── retry helpers ────────────────────────────────────────────────────────────

class _Op:
    """Callable that pops a script; Exceptions raise, anything else is returned."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.thread_ids: list[int] = []

    def __call__(self):
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return item


def test_sync_retries_a_520_then_succeeds(monkeypatch):
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    op = _Op([gateway_error(520), "ok"])
    assert retry_idempotent_sync(op, what="test read") == "ok"
    assert op.calls == 2


def test_sync_does_not_retry_a_23505(monkeypatch):
    """The single most important safety test here."""
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    op = _Op([postgrest_error("23505"), "ok"])
    with pytest.raises(APIError):
        retry_idempotent_sync(op, what="test write")
    assert op.calls == 1


def test_sync_does_not_retry_an_ordinary_bug(monkeypatch):
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    op = _Op([KeyError("ticker"), "ok"])
    with pytest.raises(KeyError):
        retry_idempotent_sync(op, what="test read")
    assert op.calls == 1


def test_sync_exhausts_then_reraises_the_last_error(monkeypatch):
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    op = _Op([gateway_error(520)])
    with pytest.raises(APIError) as excinfo:
        retry_idempotent_sync(op, what="test read", attempts=3)
    assert op.calls == 3
    assert excinfo.value.code == 520


def test_sync_logs_warning_on_retry_and_nothing_on_give_up(monkeypatch, caplog):
    """Retries are WARNING; the give-up is silent so the CALLER owns the level.

    If this helper also logged the final failure, every call site would emit two
    records for one failure — which is the duplicate-Sentry-issue bug in miniature.
    """
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    log = logging.getLogger("test_retry_levels")
    op = _Op([gateway_error(520)])
    with caplog.at_level(logging.DEBUG, logger="test_retry_levels"):
        with pytest.raises(APIError):
            retry_idempotent_sync(op, what="test read", attempts=3, logger=log)
    assert len(caplog.records) == 2  # 2 retries, no give-up record
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_sync_backoff_is_monkeypatchable_on_the_module():
    """Pins `time.sleep` being resolved on the module, not captured at def time.

    A `sleep=time.sleep` default argument would bypass every monkeypatch in this
    file and in test_sector_benchmark_transient.py, making the suite wall-clock sleep.
    """
    slept: list[float] = []
    original = se.time.sleep
    se.time.sleep = lambda d: slept.append(d)  # type: ignore[assignment]
    try:
        op = _Op([gateway_error(520), gateway_error(520), "ok"])
        assert retry_idempotent_sync(op, what="t", attempts=3, backoff_seconds=0.5) == "ok"
    finally:
        se.time.sleep = original  # type: ignore[assignment]
    assert slept == [0.5, 1.0]  # linear ramp, and it really went through the patch


@pytest.mark.asyncio
async def test_async_retries_a_520_then_succeeds():
    op = _Op([gateway_error(520), "ok"])
    result = await retry_idempotent_async(op, what="test read", backoff_seconds=0.0)
    assert result == "ok"
    assert op.calls == 2


@pytest.mark.asyncio
async def test_async_runs_the_op_off_the_event_loop():
    """Non-vacuity for `asyncio.to_thread`.

    The whole point on /tracking/assets is that the sync postgrest call stops
    blocking the loop. Asserting the op ran on a DIFFERENT thread is the only way
    to prove `to_thread` is really in the path.
    """
    op = _Op(["ok"])
    await retry_idempotent_async(op, what="test read")
    assert op.thread_ids and op.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_async_does_not_retry_a_23505():
    op = _Op([postgrest_error("23505"), "ok"])
    with pytest.raises(APIError):
        await retry_idempotent_async(op, what="test write", backoff_seconds=0.0)
    assert op.calls == 1


@pytest.mark.asyncio
async def test_async_does_not_block_the_loop_while_backing_off():
    """The backoff must be `await asyncio.sleep`, not `time.sleep`.

    Measured by keeping a heartbeat coroutine ticking during the retry: a blocking
    sleep in an `async def` would starve it.
    """
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        op = _Op([gateway_error(520), "ok"])
        await retry_idempotent_async(op, what="t", backoff_seconds=0.08)
    finally:
        beat.cancel()
    assert ticks >= 3


# ── API-surface guard ────────────────────────────────────────────────────────

def test_no_non_idempotent_retry_entrypoint_exists():
    """The module must expose ONLY idempotent-named retry entry points.

    Adding a generic `retry_supabase(...)` or an `idempotent=False` flag is exactly
    how an unsafe site (the whale trade-group inserts, whose partial commit is made
    permanent by a select-exists skip) would silently acquire retry semantics. The
    name IS the precondition, so the surface is pinned.
    """
    entrypoints = {name for name in dir(se) if name.startswith("retry")}
    assert entrypoints == {"retry_idempotent_sync", "retry_idempotent_async"}


def test_module_has_no_app_imports():
    """`app/utils/` must stay dependency-free so scripts/ can import it cheaply.

    `scripts/hydrate_whales.py` reaches this module via its sys.path shim; pulling
    `app.config`/`app.database` in here would drag .env parsing into the import.
    """
    import inspect

    source = inspect.getsource(se)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "\nfrom app." not in code and "\nimport app." not in code
