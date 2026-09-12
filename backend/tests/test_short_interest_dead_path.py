"""The short-interest path must not pay for a failure it already knows about.

Four defects confirmed 2026-09-12 against live Railway logs (findings G03-G06):

* FINRA answers **204** for a symbol it has no rows for — an index, a crypto pair, a fund.
  That was classified as a failure (`FINRA API failed for ^GSPC: 204`, WARNING, every
  cycle) and fell through to the Nasdaq fallback, which covers strictly LESS.
* The Nasdaq fallback is black-holed for this User-Agent: production logs read
  `Nasdaq API error for BTCUSD: ` — an empty message, i.e. a timeout — and it shared the
  15 s FINRA client timeout, so every miss hung a request for 15 s.
* The kill switch that exists to disable a dead source only counted **403s**, so the
  failure mode Nasdaq actually exhibits could never trip it.
* `get_short_interest` returned `{}` **without caching it**, so `/stocks/{t}/overview`
  re-ran the whole attempt on every 120 s cache miss — forever.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

import app.integrations.finra_short_interest as fsi


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    fsi._cache.clear()
    monkeypatch.setattr(fsi, "_nasdaq_kill_switch", False, raising=False)
    monkeypatch.setattr(fsi, "_nasdaq_auth_failures", 0, raising=False)
    monkeypatch.setattr(fsi, "_nasdaq_transient_failures", 0, raising=False)
    monkeypatch.setattr(fsi, "_nasdaq_rate_limited_until", 0, raising=False)
    monkeypatch.setattr(fsi, "_nasdaq_disabled_until", 0, raising=False)
    monkeypatch.setattr(fsi, "_supabase_cache_get", lambda t: None)
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale", lambda t: None)
    monkeypatch.setattr(fsi, "_supabase_cache_set", lambda t, d: None)
    yield
    fsi._cache.clear()


# ── G03: a 204 is an answer, and it must not summon the fallback ────────────────────


@pytest.mark.asyncio
async def test_a_finra_204_skips_the_nasdaq_fallback_entirely(monkeypatch, caplog):
    nasdaq_calls = []

    async def _finra(ticker):
        return fsi._NO_DATA

    async def _nasdaq(ticker):
        nasdaq_calls.append(ticker)
        return None

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", _nasdaq)
    with caplog.at_level("WARNING", logger="app.integrations.finra_short_interest"):
        assert await fsi.get_short_interest("^GSPC") == {}
    assert nasdaq_calls == [], "FINRA said 'no rows'; the narrower source cannot know better"
    assert not [r for r in caplog.records if r.levelno >= 30], \
        "a permanent, correct 'no rows' answer must not WARN every cycle"


@pytest.mark.asyncio
async def test_a_real_finra_failure_still_tries_nasdaq(monkeypatch):
    """Anti-vacuity control: None (a failure) is NOT the same as the 204 sentinel."""
    tried = []

    async def _finra(ticker):
        return None

    async def _nasdaq(ticker):
        tried.append(ticker)
        return {"shares_short": 5}

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", _nasdaq)
    assert (await fsi.get_short_interest("AAPL"))["shares_short"] == 5
    assert tried == ["AAPL"]


# ── G06: the empty answer is memoised, briefly ──────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unanswerable_symbol_is_not_re_attempted_on_every_request(monkeypatch):
    attempts = []

    async def _finra(ticker):
        attempts.append(ticker)
        return None

    async def _nasdaq(ticker):
        attempts.append(ticker)
        return None

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", _nasdaq)
    assert await fsi.get_short_interest("ZZZZ") == {}
    n = len(attempts)
    assert n == 2
    for _ in range(5):
        assert await fsi.get_short_interest("ZZZZ") == {}
    assert len(attempts) == n, "the dead lookup was re-attempted on every call"


@pytest.mark.asyncio
async def test_the_empty_memo_expires_so_an_outage_recovers(monkeypatch):
    calls = []

    async def _finra(ticker):
        calls.append(1)
        return None if len(calls) == 1 else {"shares_short": 42}

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    assert await fsi.get_short_interest("AAPL") == {}
    # Age the memo past _EMPTY_TTL_SECONDS without waiting for it.
    ts, val = fsi._cache["finra_short:AAPL"]
    fsi._cache["finra_short:AAPL"] = (ts - fsi._EMPTY_TTL_SECONDS - 1, val)
    assert (await fsi.get_short_interest("AAPL"))["shares_short"] == 42


@pytest.mark.asyncio
async def test_a_failure_and_a_real_empty_answer_get_DIFFERENT_memo_lifetimes(monkeypatch):
    """`_NO_DATA` exists to separate "FINRA answered: no rows" from "the attempt failed" —
    and `get_short_interest` threw that distinction away three lines after making it, then
    wrote the SAME 15-minute `{}` memo for both.

    That pins a transient as a measured answer. FINRA's own 429 cooldown is 300 s, so after
    a rate-limit the memo outlived it by ten minutes and `/stocks/{t}/overview` plus Home's
    Skeptical Money scan kept reporting "no short interest" long after the upstream was
    healthy. Same rule as `fred._mark_failed`: a failure and a real empty answer must never
    become the same cache entry.
    """
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())

    # (a) FINRA ANSWERED 204 → the long memo.
    async def _answered(ticker):
        return fsi._NO_DATA

    monkeypatch.setattr(fsi, "_fetch_from_finra", _answered)
    assert await fsi.get_short_interest("EMPTY") == {}
    ts_answered, _ = fsi._cache["finra_short:EMPTY"]
    answered_age = time.time() - ts_answered

    # (b) NOTHING was reached → the short memo.
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    assert await fsi.get_short_interest("FAILED") == {}
    ts_failed, _ = fsi._cache["finra_short:FAILED"]
    failed_age = time.time() - ts_failed

    # Both are back-dated so `_mem_cache_get` expires them after their own TTL; a bigger
    # back-date is a SHORTER remaining life.
    assert failed_age > answered_age, (
        "an unanswered lookup is memoised for as long as a measured 'no short interest' — "
        "a transient pinned as a fact"
    )
    remaining_failed = fsi._CACHE_TTL - failed_age
    remaining_answered = fsi._CACHE_TTL - answered_age
    assert remaining_failed == pytest.approx(fsi._FAILURE_TTL_SECONDS, abs=5)
    assert remaining_answered == pytest.approx(fsi._EMPTY_TTL_SECONDS, abs=5)
    assert fsi._FAILURE_TTL_SECONDS < fsi._RATE_LIMIT_COOLDOWN, (
        "the failure memo must not outlive this module's own 429 backoff"
    )


@pytest.mark.asyncio
async def test_the_failure_memo_still_suppresses_the_immediate_retry(monkeypatch):
    """The memo's cost-saving job survives the split: a second request inside the window
    must not re-run the upstream attempt."""
    calls = []

    async def _finra(ticker):
        calls.append(1)
        return None

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    assert await fsi.get_short_interest("ZZZZ") == {}
    assert await fsi.get_short_interest("ZZZZ") == {}
    assert len(calls) == 1, "the failed lookup was re-attempted on the very next request"


@pytest.mark.asyncio
async def test_the_empty_answer_is_never_persisted_to_supabase(monkeypatch):
    """A persisted empty would outlive the outage that produced it."""
    writes = []
    monkeypatch.setattr(fsi, "_supabase_cache_set", lambda t, d: writes.append((t, d)))
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    assert await fsi.get_short_interest("ZZZZ") == {}
    assert writes == []


async def _none():
    return None


# ── G04: the kill switch can finally engage, and the log names the exception ────────


@pytest.mark.asyncio
async def test_a_timeout_counts_toward_the_kill_switch_and_is_named(monkeypatch, caplog):
    import httpx

    class _Client:
        async def get(self, *a, **k):
            raise httpx.ReadTimeout("")      # str() is empty — the real production shape

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    with caplog.at_level("WARNING", logger="app.integrations.finra_short_interest"):
        for _ in range(fsi._MAX_CONSECUTIVE_FAILURES):
            assert await fsi._fetch_from_nasdaq("AAPL") is None
    # A WINDOW, not the permanent latch. `_nasdaq_kill_switch` has no reset anywhere in
    # the module, so latching it on a TIMEOUT meant one ten-second blip — ten concurrent
    # raises, because `_build_shorts` fans out under a semaphore of 10 — cost the fallback
    # until the next deploy. 402/403 keep the permanent latch; these do not.
    assert fsi._nasdaq_kill_switch is False, \
        "a transient timeout must not permanently latch the fallback off"
    assert fsi._nasdaq_disabled_until > time.time(), \
        "a black-holed source must be disabled, not retried forever"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ReadTimeout" in m for m in msgs), \
        "an empty-message timeout must still be identifiable in the log"
    # And while disabled it costs nothing.
    assert await fsi._fetch_from_nasdaq("AAPL") is None
    # …and the window EXPIRES: the fallback comes back on its own.
    fsi._nasdaq_disabled_until = time.time() - 1
    reached = []

    class _Ok:
        async def get(self, *a, **k):
            reached.append(1)
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Ok()))
    await fsi._fetch_from_nasdaq("AAPL")
    assert reached, "the disable never expires — it is a permanent latch by another route"


@pytest.mark.asyncio
async def test_a_403_run_still_latches_permanently(monkeypatch):
    """The credential-shaped condition keeps the hard kill switch — it is not transient."""
    class _Client:
        async def get(self, *a, **k):
            return httpx.Response(403, request=httpx.Request("GET", "https://x"))

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    for _ in range(fsi._MAX_CONSECUTIVE_FAILURES):
        assert await fsi._fetch_from_nasdaq("AAPL") is None
    assert fsi._nasdaq_kill_switch is True, \
        "a persistent 403 is not transient and must stay latched"


async def _client(c):
    return c


@pytest.mark.asyncio
async def test_the_fallback_request_actually_carries_the_short_timeout(monkeypatch):
    """Assert the CALL, not the constant: a constant nothing passes is decoration."""
    seen = {}

    class _Client:
        async def get(self, url, **kw):
            seen.update(kw)
            raise RuntimeError("stop here")

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    await fsi._fetch_from_nasdaq("AAPL")
    assert "timeout" in seen, (
        "the fallback inherited the 15 s FINRA client timeout, so a black-holed source "
        "cost more per call than the primary it was backing up"
    )
    assert seen["timeout"] <= 5.0 and seen["timeout"] == fsi._NASDAQ_TIMEOUT_SECONDS


# ── G03, at the source: the real 204 branch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_from_finra_returns_the_no_data_sentinel_on_204(monkeypatch, caplog):
    class _Resp:
        status_code = 204

        def json(self):                      # pragma: no cover - must not be reached
            raise AssertionError("204 has no body")

    class _Client:
        async def post(self, *a, **k):       # the FINRA API is a POST query
            return _Resp()

    monkeypatch.setattr(fsi, "_get_finra_client", lambda: _client(_Client()))
    monkeypatch.setattr(fsi, "_fetch_finra_token", _token)
    monkeypatch.setattr(fsi, "_finra_kill_switch", False, raising=False)
    monkeypatch.setattr(fsi, "_finra_rate_limited_until", 0, raising=False)
    with caplog.at_level("WARNING", logger="app.integrations.finra_short_interest"):
        out = await fsi._fetch_from_finra("^GSPC")
    assert out is fsi._NO_DATA, (
        "a 204 must be the authoritative 'no rows' sentinel, not None — None sends an "
        "index to a fallback that covers strictly less and logs a WARNING every cycle"
    )
    assert not [r for r in caplog.records if r.levelno >= 30]


@pytest.mark.asyncio
async def test_fetch_from_finra_still_returns_none_on_a_real_error(monkeypatch, caplog):
    """Anti-vacuity control for the test above."""
    class _Resp:
        status_code = 503

    class _Client:
        async def post(self, *a, **k):       # the FINRA API is a POST query
            return _Resp()

    monkeypatch.setattr(fsi, "_get_finra_client", lambda: _client(_Client()))
    monkeypatch.setattr(fsi, "_fetch_finra_token", _token)
    monkeypatch.setattr(fsi, "_finra_kill_switch", False, raising=False)
    monkeypatch.setattr(fsi, "_finra_rate_limited_until", 0, raising=False)
    with caplog.at_level("WARNING", logger="app.integrations.finra_short_interest"):
        out = await fsi._fetch_from_finra("AAPL")
    assert out is None and out is not fsi._NO_DATA
    assert any("503" in r.getMessage() for r in caplog.records)



# ── G05/F13: the Supabase tiers run off the event loop ──────────────────────────────


@pytest.mark.asyncio
async def test_the_supabase_tiers_do_not_block_the_event_loop(monkeypatch):
    threads = []

    def _record(*a, **k):
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(fsi, "_supabase_cache_get", _record)
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale", _record)
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    loop_ident = threading.get_ident()
    await fsi.get_short_interest("AAPL")
    assert threads, "the Supabase tiers were never consulted — the test would be vacuous"
    assert all(t != loop_ident for t in threads), (
        "a blocking PostgREST round trip ran on the loop, on the /overview request path"
    )


@pytest.mark.asyncio
async def test_the_supabase_write_also_runs_off_the_loop(monkeypatch):
    threads = []
    monkeypatch.setattr(fsi, "_supabase_cache_set",
                        lambda t, d: threads.append(threading.get_ident()))
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _some())
    loop_ident = threading.get_ident()
    await fsi.get_short_interest("AAPL")
    assert threads and all(t != loop_ident for t in threads)


async def _some():
    return {"shares_short": 7}


# ── the two failure families must not share a counter ───────────────────────────────


@pytest.mark.asyncio
async def test_transient_failures_cannot_trip_the_PERMANENT_latch(monkeypatch):
    """9 timeouts + 1 403 used to latch Nasdaq off for the life of the process.

    One counter fed two arms with opposite remedies, so the run landed in whichever branch
    happened to see the tenth event — and it logged the false line "Nasdaq 403 x10".
    Strictly easier to hit than before the time-bounded window was added, because the
    exception arm did not increment anything then. `_build_shorts` fans out under
    `asyncio.Semaphore(10)`, so nine concurrent timeouts is ONE network blip.
    """
    calls = {"n": 0}

    class _Client:
        async def get(self, *a, **k):
            calls["n"] += 1
            if calls["n"] <= 9:
                raise httpx.ReadTimeout("")
            return httpx.Response(403, request=httpx.Request("GET", "https://x"))

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    for _ in range(10):
        # The transient window may engage on the 10th timeout; clear it so the 403 is
        # actually issued — the point is the COUNTER, not the window.
        fsi._nasdaq_disabled_until = 0
        await fsi._fetch_from_nasdaq("AAPL")

    assert fsi._nasdaq_kill_switch is False, (
        "nine transient timeouts plus one 403 permanently disabled the Nasdaq fallback — "
        "the counters are shared again"
    )
    assert fsi._nasdaq_auth_failures == 1, fsi._nasdaq_auth_failures


@pytest.mark.asyncio
async def test_a_transient_blip_does_not_wipe_accumulated_auth_failures(monkeypatch):
    """The mirror case. 9x403 + 1 timeout fired the transient arm, which zeroes the
    counter — so a genuinely persistent credential failure could never reach the permanent
    latch as long as one timeout interleaved."""
    calls = {"n": 0}

    class _Client:
        async def get(self, *a, **k):
            calls["n"] += 1
            if calls["n"] <= 9:
                return httpx.Response(403, request=httpx.Request("GET", "https://x"))
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    for _ in range(10):
        fsi._nasdaq_disabled_until = 0
        await fsi._fetch_from_nasdaq("AAPL")

    assert fsi._nasdaq_auth_failures == 9, (
        f"a single timeout wiped the accumulated 403 count ({fsi._nasdaq_auth_failures}) — "
        "a persistent credential failure can now never latch"
    )


@pytest.mark.asyncio
async def test_the_transient_window_FIRING_still_leaves_the_auth_count_alone(monkeypatch):
    """The stronger mirror: the transient arm must not wipe the auth count when it ACTUALLY
    ENGAGES.

    The test above only reaches one timeout, so the `>= _MAX_CONSECUTIVE_FAILURES` branch
    never runs and a stray `_nasdaq_auth_failures = 0` inside it would go unnoticed. Here
    the window really fires with 403s already banked.
    """
    fsi._nasdaq_auth_failures = 7

    class _Client:
        async def get(self, *a, **k):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    for _ in range(fsi._MAX_CONSECUTIVE_FAILURES):
        fsi._nasdaq_disabled_until = 0
        await fsi._fetch_from_nasdaq("AAPL")

    assert fsi._nasdaq_disabled_until > time.time(), "the transient window never engaged"
    assert fsi._nasdaq_auth_failures == 7, (
        f"engaging the transient window reset the 403 count to "
        f"{fsi._nasdaq_auth_failures} — a persistent credential failure can never latch"
    )
    assert fsi._nasdaq_kill_switch is False


@pytest.mark.asyncio
async def test_a_200_clears_both_families(monkeypatch):
    """Control: the source being reachable AND authorised must reset everything, or the
    counters accumulate across unrelated incidents for the process lifetime."""
    fsi._nasdaq_auth_failures = 5
    fsi._nasdaq_transient_failures = 5

    class _Client:
        async def get(self, *a, **k):
            return httpx.Response(
                200,
                json={"data": {"shortInterestTable": {"rows": [
                    {"interest": "1,000", "settlementDate": "09/15/2026", "daysToCover": "1.5"},
                ]}}},
                request=httpx.Request("GET", "https://x"),
            )

    monkeypatch.setattr(fsi, "_get_client", lambda: _client(_Client()))
    out = await fsi._fetch_from_nasdaq("AAPL")
    assert out and out["shares_short"] == 1000
    assert fsi._nasdaq_auth_failures == 0 and fsi._nasdaq_transient_failures == 0


@pytest.mark.asyncio
async def test_a_200_with_an_empty_body_is_an_ANSWER_not_a_failure(monkeypatch):
    """FINRA answering HTTP 200 with `[]` carries the same fact as a 204: it has nothing
    for this symbol.

    Classifying it as a failure cost twice — the caller paid the Nasdaq fallback the 204
    branch exists to skip, and the memo got the 60 s failure TTL instead of the 900 s
    answered-empty one, so an unanswerable symbol was re-attempted 15x more often than
    intended.
    """
    class _Resp:
        status_code = 200

        def json(self):
            return []

    class _Client:
        async def post(self, *a, **k):       # the FINRA API is a POST query
            return _Resp()

    monkeypatch.setattr(fsi, "_get_finra_client", lambda: _client(_Client()))
    monkeypatch.setattr(fsi, "_fetch_finra_token", _token)
    monkeypatch.setattr(fsi, "_finra_kill_switch", False, raising=False)
    monkeypatch.setattr(fsi, "_finra_rate_limited_until", 0, raising=False)

    out = await fsi._fetch_from_finra("ZZZZ")
    assert out is fsi._NO_DATA, (
        "a 200-with-empty-body is reported as a failure, so the symbol pays the Nasdaq "
        "timeout and gets the short failure memo"
    )


@pytest.mark.asyncio
async def test_that_answer_skips_nasdaq_and_gets_the_LONG_memo(monkeypatch):
    """End to end: the classification has to reach both consequences."""
    nasdaq_calls = []

    async def _finra(ticker):
        return fsi._NO_DATA

    async def _nasdaq(ticker):
        nasdaq_calls.append(ticker)
        return None

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", _nasdaq)
    assert await fsi.get_short_interest("ZZZZ") == {}
    assert nasdaq_calls == [], "the dead Nasdaq fallback was summoned for an answered symbol"
    ts, _ = fsi._cache["finra_short:ZZZZ"]
    remaining = fsi._CACHE_TTL - (time.time() - ts)
    assert remaining == pytest.approx(fsi._EMPTY_TTL_SECONDS, abs=5), (
        f"an ANSWERED-empty symbol got a {remaining:.0f}s memo, not the "
        f"{fsi._EMPTY_TTL_SECONDS}s one"
    )


async def _token():
    return "tok"
