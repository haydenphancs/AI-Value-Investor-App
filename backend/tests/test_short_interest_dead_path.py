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


# The autouse fixture below replaces the stale pre-read with a lambda; the two tests that
# exercise the REAL pre-read bind it here, before any fixture runs.
_REAL_STALE_READ = fsi._supabase_cache_get_stale


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


# ── F18-4: a served STALE row must not be pinned for 3 days by one failure ───────────


@pytest.mark.asyncio
async def test_a_stale_row_served_on_failure_gets_the_FAILURE_memo_not_three_days(monkeypatch):
    """One FINRA 5xx on a 48-day-old row (BIGC, prod) used to write that print into the
    memory tier with a fresh `time.time()` stamp — a 3-day memo for a failed fetch — so the
    Overview's Short % of float and the report's Hidden Signals were ~3 cycles old for 72 h
    while FINRA had recovered seconds later.
    """
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale",
                        lambda t: {"shares_short": 1, "settlement_date": "2026-07-01"})
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())

    out = await fsi.get_short_interest("BIGC")
    assert out["shares_short"] == 1, "the stale print still beats N/A"
    ts, memo = fsi._cache["finra_short:BIGC"]
    assert memo is out
    remaining = fsi._CACHE_TTL - (time.time() - ts)
    assert remaining == pytest.approx(fsi._FAILURE_TTL_SECONDS, abs=5), (
        f"a stale row served after a FAILED fetch is memoised for {remaining:.0f}s, "
        f"not the {fsi._FAILURE_TTL_SECONDS}s failure memo"
    )


@pytest.mark.asyncio
async def test_the_next_request_after_the_failure_memo_retries_finra_and_gets_the_new_print(monkeypatch):
    calls = []

    async def _finra(ticker):
        calls.append(ticker)
        return None if len(calls) == 1 else {"shares_short": 2, "settlement_date": "2026-09-15"}

    monkeypatch.setattr(fsi, "_supabase_cache_get_stale",
                        lambda t: {"shares_short": 1, "settlement_date": "2026-07-01"})
    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())

    assert (await fsi.get_short_interest("BIGC"))["shares_short"] == 1
    # Within the memo the stale row is still served without a retry (no hammering).
    assert (await fsi.get_short_interest("BIGC"))["shares_short"] == 1
    assert calls == ["BIGC"]

    # Age the memo past the failure TTL.
    ts, val = fsi._cache["finra_short:BIGC"]
    fsi._cache["finra_short:BIGC"] = (ts - fsi._FAILURE_TTL_SECONDS - 1, val)
    assert (await fsi.get_short_interest("BIGC"))["shares_short"] == 2, \
        "after the cooldown the recovered FINRA print must replace the stale row"
    assert calls == ["BIGC", "BIGC"]
    # and the real print carries the FULL memo
    ts2, _ = fsi._cache["finra_short:BIGC"]
    assert fsi._CACHE_TTL - (time.time() - ts2) == pytest.approx(fsi._CACHE_TTL, abs=5)


@pytest.mark.asyncio
async def test_a_stale_row_served_after_a_204_gets_the_EMPTY_memo(monkeypatch):
    """A 204 is an ANSWER, so the stale row is re-checked on the 900 s cadence, not 60 s."""
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale",
                        lambda t: {"shares_short": 1, "settlement_date": "2026-07-01"})

    async def _answered(ticker):
        return fsi._NO_DATA

    nasdaq = []

    async def _nasdaq(ticker):
        nasdaq.append(ticker)
        return None

    monkeypatch.setattr(fsi, "_fetch_from_finra", _answered)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", _nasdaq)
    assert (await fsi.get_short_interest("DLST"))["shares_short"] == 1
    assert nasdaq == []
    ts, _ = fsi._cache["finra_short:DLST"]
    remaining = fsi._CACHE_TTL - (time.time() - ts)
    assert remaining == pytest.approx(fsi._EMPTY_TTL_SECONDS, abs=5)
    assert remaining < fsi._CACHE_TTL / 10


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [None, {}, {"shares_short": 0}])
async def test_a_missing_or_empty_stale_row_still_takes_the_empty_memo_path(monkeypatch, stale):
    """`{}`/None stale → `{}` memo; a dict with falsy VALUES is still a dict and is served."""
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale", lambda t: stale)
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    out = await fsi.get_short_interest("XYZ")
    if stale:
        assert out == stale
    else:
        assert out == {}
    ts, _ = fsi._cache["finra_short:XYZ"]
    assert fsi._CACHE_TTL - (time.time() - ts) == pytest.approx(fsi._FAILURE_TTL_SECONDS, abs=5)


@pytest.mark.asyncio
async def test_using_stale_is_logged_only_on_use_never_on_the_pre_read(monkeypatch, caplog):
    """Prod showed `using STALE Supabase data for AI` immediately followed by a successful
    FINRA print: the line fired on the pre-read, so the one log that would reveal the
    3-day pin was already misleading."""
    monkeypatch.setattr(fsi, "_supabase_cache_get_stale",
                        lambda t: {"shares_short": 1, "settlement_date": "2026-07-01"})

    async def _finra_ok(ticker):
        return {"shares_short": 9, "settlement_date": "2026-09-15"}

    monkeypatch.setattr(fsi, "_fetch_from_finra", _finra_ok)
    monkeypatch.setattr(fsi, "_fetch_from_nasdaq", lambda t: _none())
    with caplog.at_level("INFO", logger="app.integrations.finra_short_interest"):
        assert (await fsi.get_short_interest("AI"))["shares_short"] == 9
    assert not [r for r in caplog.records if "using STALE" in r.getMessage()]

    fsi._cache.clear()
    caplog.clear()
    monkeypatch.setattr(fsi, "_fetch_from_finra", lambda t: _none())
    with caplog.at_level("INFO", logger="app.integrations.finra_short_interest"):
        assert (await fsi.get_short_interest("AI"))["shares_short"] == 1
    used = [r.getMessage() for r in caplog.records if "using STALE" in r.getMessage()]
    assert len(used) == 1 and "2026-07-01" in used[0] and "AI" in used[0]


def test_the_stale_pre_read_itself_does_not_log_use(monkeypatch, caplog):
    class _Res:
        data = [{"response_json": {"shares_short": 1}}]

    class _Q:
        def select(self, *a): return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def execute(self): return _Res()

    class _SB:
        def table(self, name): return _Q()

    import app.database as db
    monkeypatch.setattr(db, "get_supabase", lambda: _SB())
    with caplog.at_level("INFO", logger="app.integrations.finra_short_interest"):
        assert _REAL_STALE_READ("AI") == {"shares_short": 1}
    assert not [r for r in caplog.records if "using STALE" in r.getMessage()]


def test_the_stale_pre_read_logs_its_own_failure(monkeypatch, caplog):
    class _SB:
        def table(self, name): raise RuntimeError("520 edge")

    import app.database as db
    monkeypatch.setattr(db, "get_supabase", lambda: _SB())
    with caplog.at_level("WARNING", logger="app.integrations.finra_short_interest"):
        assert _REAL_STALE_READ("AI") is None
    assert any("STALE Supabase read failed for AI" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)


# ── F18-7: the token refresh is single-flight ─────────────────────────────────


@pytest.mark.asyncio
async def test_ten_cold_callers_post_credentials_once_and_share_the_token(monkeypatch):
    import asyncio as _aio
    import app.integrations.finra_short_interest as fsi2

    monkeypatch.setattr(fsi2, "_finra_access_token", None)
    monkeypatch.setattr(fsi2, "_finra_token_expiry", 0)
    monkeypatch.setattr(fsi2, "_finra_token_inflight", None)
    monkeypatch.setenv("FINRA_CLIENT_ID", "id")
    monkeypatch.setenv("FINRA_CLIENT_SECRET", "secret")
    posts = {"n": 0}

    class _Resp:
        status_code = 200
        def json(self): return {"access_token": "tok-1"}

    class _Client:
        async def post(self, *a, **k):
            posts["n"] += 1
            await _aio.sleep(0.02)          # long enough for every caller to pile in
            return _Resp()

    async def _client():
        return _Client()
    monkeypatch.setattr(fsi2, "_get_finra_client", _client)

    tokens = await _aio.gather(*(fsi2._fetch_finra_token() for _ in range(10)))
    assert posts["n"] == 1, f"{posts['n']} credential POSTs for one cold burst"
    assert tokens == ["tok-1"] * 10
    assert fsi2._finra_token_inflight is None, "the in-flight slot must clear"


@pytest.mark.asyncio
async def test_a_throttled_refresh_is_shared_not_retried_per_caller(monkeypatch):
    """Followers share the leader's None: retrying serially N times against a throttling
    identity provider is exactly the burst this exists to prevent."""
    import asyncio as _aio
    import app.integrations.finra_short_interest as fsi2

    monkeypatch.setattr(fsi2, "_finra_access_token", None)
    monkeypatch.setattr(fsi2, "_finra_token_expiry", 0)
    monkeypatch.setattr(fsi2, "_finra_token_inflight", None)
    monkeypatch.setenv("FINRA_CLIENT_ID", "id")
    monkeypatch.setenv("FINRA_CLIENT_SECRET", "secret")
    posts = {"n": 0}

    class _Resp:
        status_code = 429
        def json(self): return {}

    class _Client:
        async def post(self, *a, **k):
            posts["n"] += 1
            await _aio.sleep(0.02)
            return _Resp()

    async def _client():
        return _Client()
    monkeypatch.setattr(fsi2, "_get_finra_client", _client)
    tokens = await _aio.gather(*(fsi2._fetch_finra_token() for _ in range(5)))
    assert posts["n"] == 1 and tokens == [None] * 5


# ── W2 security-2 (tidy-up): the leader slot is owned by the TASK, not the awaiter ──


@pytest.mark.asyncio
async def test_a_cancelled_leader_keeps_serving_joiners_and_no_second_post_starts(monkeypatch):
    """`asyncio.shield` keeps the POST running when the leader's caller is cancelled, but
    the awaiter's `finally` nulled the slot at that moment, so the next arrival started a
    second credential POST beside the orphan. The slot now clears when the TASK finishes."""
    import asyncio as _aio
    from app.integrations import finra_short_interest as fsi2
    monkeypatch.setattr(fsi2, "_finra_access_token", None)
    monkeypatch.setattr(fsi2, "_finra_token_expiry", 0.0)
    monkeypatch.setattr(fsi2, "_finra_token_inflight", None)
    gate = _aio.Event()
    posts = {"n": 0}

    async def _once():
        posts["n"] += 1
        await gate.wait()
        return "tok"
    monkeypatch.setattr(fsi2, "_fetch_finra_token_once", _once)

    leader_caller = _aio.create_task(fsi2._fetch_finra_token())
    for _ in range(3):        # the caller registers the slot, then the task runs to its await
        await _aio.sleep(0)
    assert posts["n"] == 1 and fsi2._finra_token_inflight is not None
    leader_caller.cancel()
    with pytest.raises(_aio.CancelledError):
        await leader_caller
    assert fsi2._finra_token_inflight is not None, "the orphaned POST still owns the slot"
    joiners = [_aio.create_task(fsi2._fetch_finra_token()) for _ in range(5)]
    for _ in range(3):
        await _aio.sleep(0)
    assert posts["n"] == 1, "no second POST: the joiners attached to the running leader"
    gate.set()
    assert await _aio.gather(*joiners) == ["tok"] * 5
    for _ in range(3):
        await _aio.sleep(0)
    assert fsi2._finra_token_inflight is None, "released by the task's done callback"
