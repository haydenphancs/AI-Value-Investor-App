"""Control-flow tests for TickerReportView charge-on-fresh-generation + refund.

Guards the invariant: a cache HIT is free; a cache MISS charges exactly once; ANY
non-delivery (generation error, ticker-not-found, schema drift) refunds exactly
once; insufficient credits never generates; a transient charge failure fails
retryably without generating. Pure control flow — CreditService, the cache
lookups, generation, and schema validation are mocked, so no Supabase / FMP /
Gemini. Mirrors tests/test_research_credits.py's mock style.
"""

import asyncio
import json
import re
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.endpoints.ticker_report as tr
from app.services.credit_service import CreditServiceUnavailable

# The endpoint is account-only, so this is the only caller shape there is.
USER = {"id": "authed-user-1"}

# The direct door's per-generation charge ref (2026-10-12): `TICKER:persona:{uuid4hex}`.
_DIRECT_REF = re.compile(r"^AAPL:warren_buffett:[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _clean_single_flight():
    """The per-user single-flight map and the admission counter are module-global; a test
    that left either dirty would silently change every later test's path."""
    tr._USER_GENERATIONS.clear()
    tr._INFLIGHT_REPORTS = 0
    yield
    tr._USER_GENERATIONS.clear()
    tr._INFLIGHT_REPORTS = 0


def _install_fakes(
    monkeypatch,
    *,
    cached=None,
    legacy=None,
    charge_return=100,
    charge_raises=False,
    generate_return=None,
    generate_raises=None,
    validate_ok=True,
):
    """Wire the endpoint's collaborators to controllable fakes.

    Returns (fake_credit_class, fake_service) so tests can assert on
    precharge / refund / generate_fresh_report.
    """

    async def _fake_legacy(ticker, persona):
        return legacy

    async def _fake_cached(ticker, persona):
        return cached

    monkeypatch.setattr(tr, "_check_legacy_report_cache", _fake_legacy)
    monkeypatch.setattr(tr, "get_cached_report", _fake_cached)

    gen = AsyncMock()
    if generate_raises is not None:
        gen.side_effect = generate_raises
    else:
        gen.return_value = generate_return if generate_return is not None else {"ok": True}
    fake_service = MagicMock()
    fake_service.generate_fresh_report = gen
    monkeypatch.setattr(tr, "TickerReportService", lambda: fake_service)

    # Bypass the real (heavy) schema: success → (report, None), drift → (None, err).
    def _fake_validate(report, ticker, persona):
        if validate_ok:
            return report, None
        return None, MagicMock(name="error_response")

    monkeypatch.setattr(tr, "_validate_report", _fake_validate)

    precharge = MagicMock()
    if charge_raises:
        precharge.side_effect = CreditServiceUnavailable("transient")
    else:
        precharge.return_value = charge_return
    refund_ledgered = MagicMock(return_value=80)

    class FakeCreditService:
        DEEP_RESEARCH_COST = 20

        def __init__(self):
            pass

    # Class-attr MagicMocks (not descriptors) → instance access returns them
    # unbound, so calls record exactly the endpoint's args (no self).
    FakeCreditService.precharge = precharge
    FakeCreditService.refund_ledgered = refund_ledgered
    monkeypatch.setattr(tr, "CreditService", FakeCreditService)

    # Guests now claim against the per-install monthly budget (migration 106).
    # Stub it so this file's guest test never reaches live Supabase.
    return FakeCreditService, fake_service


@pytest.mark.asyncio
async def test_cache_hit_is_free(monkeypatch):
    credit, service = _install_fakes(monkeypatch, cached={"ok": True})
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()
    service.generate_fresh_report.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_cache_hit_is_free(monkeypatch):
    credit, service = _install_fakes(monkeypatch, legacy={"ticker_report_data": 1})
    # patch_wall_street_consensus_live / patch_legacy_price_action run on this
    # path; stub them so we don't hit FMP.
    async def _noop_ws(payload, ticker):
        return payload
    monkeypatch.setattr(tr, "patch_wall_street_consensus_live", _noop_ws)
    monkeypatch.setattr(tr, "patch_legacy_price_action", lambda p: p)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_not_called()
    service.generate_fresh_report.assert_not_called()


@pytest.mark.asyncio
async def test_miss_success_charges_once_no_refund(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_return={"ok": True}, charge_return=100
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_miss_generation_failure_refunds_once(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_raises=RuntimeError("gemini down"),
        charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_ticker_not_found_refunds(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_raises=ValueError("no profile"),
        charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_schema_drift_refunds(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_return={"bad": True},
        validate_ok=False, charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_insufficient_credits_does_not_generate(monkeypatch):
    credit, service = _install_fakes(monkeypatch, cached=None, charge_return=None)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_not_called()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_charge_unavailable_is_retryable_no_generate(monkeypatch):
    """A transport failure on the precharge is NOT proof nothing was debited (a 520 after the
    commit lands here too). This used to pin `refund_ledgered.assert_not_called()`; since
    2026-10-12 it is compensated with exactly ONE quiet refund on the SAME unique ref — never
    the non-delivery refund too, and never on a shared `TICKER:persona` ref, where a
    compensation would reverse an earlier DELIVERED report's debit and mint 20 credits."""
    from app.services.credit_service import ChargeRef

    credit, service = _install_fakes(monkeypatch, cached=None, charge_raises=True)
    resp = await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_not_called()
    assert resp.status_code == 409
    assert json.loads(resp.body)["error_code"] == "SYSTEM_BUSY"
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    charge_ref = credit.precharge.call_args.kwargs["ref_id"]
    assert kw["reason"] == "report_precharge_unconfirmed"
    assert kw["quiet_no_match"] is True
    assert kw["ref_id"] == charge_ref and isinstance(kw["ref_id"], ChargeRef)
    assert _DIRECT_REF.match(kw["ref_id"]), kw["ref_id"]


@pytest.mark.asyncio
async def test_every_caller_is_charged_now(monkeypatch):
    """Replaces `test_guest_is_not_charged`.

    The guest sentinel used to be a credit no-op that still generated a report — free AI,
    metered only by a per-install allowance keyed on the client-supplied `X-Guest-Id`. Rotating
    that header made it unmetered outright, so the endpoint is account-only and every caller is
    charged.
    """
    credit, service = _install_fakes(monkeypatch, cached=None, generate_return={"ok": True})
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()


# ── 2026-10-12: unique refs, single-flight, settle-then-answer, off the loop ──────────


@pytest.mark.asyncio
async def test_the_charge_and_the_refund_share_one_unique_ref(monkeypatch):
    """The charge, and the non-delivery refund in `finally`, carry ONE ref — and two
    generations never share one (the old `f"{ticker}:{persona}"` made every AAPL/Buffett
    report's debit pairable by any later refund)."""
    from app.services.credit_service import ChargeRef

    refs = []
    for _ in range(2):
        credit, _service = _install_fakes(
            monkeypatch, cached=None, generate_raises=RuntimeError("gemini down"),
        )
        await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
        charge = credit.precharge.call_args.kwargs["ref_id"]
        refund = credit.refund_ledgered.call_args.kwargs
        assert refund["ref_id"] == charge and isinstance(charge, ChargeRef)
        assert refund["reason"] == "report_refund"
        assert "quiet_no_match" not in refund, "a non-delivery refund must page on a no-match"
        assert _DIRECT_REF.match(charge), charge
        refs.append(charge)
    assert refs[0] != refs[1], "two generations shared one charge ref"


class _GatedService:
    """`generate_fresh_report` that blocks until the test opens the gate."""

    calls = 0

    def __init__(self, gate: asyncio.Event, *, result=None, raises=None):
        self._gate, self._result, self._raises = gate, result, raises

    async def generate_fresh_report(self, ticker, persona):
        type(self).calls += 1
        await self._gate.wait()
        if self._raises is not None:
            raise self._raises
        return self._result if self._result is not None else {"ok": True, "t": ticker}


def _gated(monkeypatch, gate, **kw):
    credit, _ = _install_fakes(monkeypatch, cached=None)
    _GatedService.calls = 0
    svc = _GatedService(gate, **kw)
    monkeypatch.setattr(tr, "TickerReportService", lambda: svc)
    return credit


async def _until(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        assert loop.time() < deadline, "condition never became true"
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_a_same_user_retry_while_generating_joins_and_is_not_charged_twice(monkeypatch):
    """iOS re-sends a slow generation (Retry after its 120 s timeout, or the auto-retry after
    a bare edge 5xx). The re-send used to precharge 20 again and join the same agent run:
    40 credits for one report. Now it joins the leader: one charge, one generation."""
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    first = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)
    second = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await asyncio.sleep(0.01)
    assert not second.done(), "the follower answered before the leader finished"
    gate.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)

    assert credit.precharge.call_count == 1, "the re-send was charged a second time"
    assert _GatedService.calls == 1, "the re-send ran a second generation"
    credit.refund_ledgered.assert_not_called()
    assert a == b and a is not b, "the follower must get its own copy of the leader's report"
    assert tr._USER_GENERATIONS == {}, "the single-flight entry leaked"
    assert tr._INFLIGHT_REPORTS == 0


@pytest.mark.asyncio
async def test_a_joined_follower_gets_the_leaders_error_and_no_second_refund(monkeypatch):
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate, raises=RuntimeError("gemini down"))
    first = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)
    second = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await asyncio.sleep(0.01)
    gate.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)

    assert a.status_code == b.status_code and a is not b
    assert json.loads(a.body)["error_code"] == json.loads(b.body)["error_code"]
    assert credit.precharge.call_count == 1
    assert credit.refund_ledgered.call_count == 1, "the follower re-ran the billable path"
    assert tr._USER_GENERATIONS == {}


@pytest.mark.asyncio
async def test_different_users_are_not_deduped(monkeypatch):
    """The key carries the user id: another user's generation is theirs to pay for."""
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    a = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user={"id": "u-a"}))
    b = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user={"id": "u-b"}))
    await _until(lambda: _GatedService.calls == 2)
    gate.set()
    await asyncio.wait_for(asyncio.gather(a, b), 2)
    assert credit.precharge.call_count == 2


@pytest.mark.asyncio
async def test_another_persona_is_its_own_generation(monkeypatch):
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    a = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    b = asyncio.create_task(tr.get_ticker_report("AAPL", "cathie_wood", user=USER))
    await _until(lambda: _GatedService.calls == 2)
    gate.set()
    await asyncio.wait_for(asyncio.gather(a, b), 2)
    assert credit.precharge.call_count == 2


@pytest.mark.asyncio
async def test_a_cancelled_leader_releases_its_followers_with_system_busy(monkeypatch):
    """A leader cancelled mid-generation (a deploy's graceful-shutdown deadline) must not
    strand its followers on a future nobody resolves, and must still hand its charge back —
    detached, because a cancelled frame cannot await."""
    from app.services import credit_service as cs

    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    leader = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)
    follower = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await asyncio.sleep(0.01)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    resp = await asyncio.wait_for(follower, 2)
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["error_code"] == "SYSTEM_BUSY" and body["details"]["step"] == "joined_generation"
    assert tr._USER_GENERATIONS == {} and tr._INFLIGHT_REPORTS == 0
    assert await cs.drain_detached_settlements(2.0) == 0
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["reason"] == "report_refund"
    assert kw["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]
    assert credit.precharge.call_count == 1, "the follower was charged"


@pytest.mark.asyncio
async def test_a_cancelled_follower_does_not_cancel_the_leader(monkeypatch):
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    leader = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)
    follower = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await asyncio.sleep(0.01)
    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    gate.set()
    result = await asyncio.wait_for(leader, 2)
    assert result == {"ok": True, "t": "AAPL"}
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,returned", [
    ({"outcome": "refunded", "refunded": 20, "spendable": 80}, True),
    ({"outcome": "already_refunded", "refunded": 0, "spendable": 80}, True),
    (None, False),                                                # transport fault
    ({"outcome": "no_matching_debit", "refunded": 0}, False),
    ({"outcome": "partial", "refunded": 5, "requested": 20}, False),
    ({"outcome": "capped_to_zero", "refunded": 0}, False),
])
async def test_a_degraded_report_is_refunded_before_the_answer_and_the_copy_tells_the_truth(
    monkeypatch, outcome, returned,
):
    """The degraded copy used to say "You have not been charged" — after the precharge had
    debited, with the refund still to come in `finally` (and possibly failing). It now
    settles first and words the copy from the REAL outcome; `finally` never refunds again."""
    credit, _ = _install_fakes(monkeypatch, cached=None, generate_return={"_degraded": "stage_a_X"})
    credit.refund_ledgered.return_value = outcome
    resp = await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    body = json.loads(resp.body)
    assert resp.status_code == 503 and body["error_code"] == "GEMINI_UNAVAILABLE"
    assert "not been charged" not in body["user_message"]
    if returned:
        assert "Your credits were returned" in body["user_message"]
    else:
        assert "credit" not in body["user_message"].lower(), body["user_message"]
    credit.refund_ledgered.assert_called_once()
    assert credit.refund_ledgered.call_args.kwargs["reason"] == "report_refund"


@pytest.mark.asyncio
async def test_direct_door_money_calls_leave_the_loop_thread(monkeypatch):
    """Invariant #6: the precharge and the refund are sync Supabase round trips."""
    loop_thread = threading.get_ident()
    seen = []
    credit, _ = _install_fakes(monkeypatch, cached=None, generate_raises=RuntimeError("x"))
    credit.precharge.side_effect = lambda *a, **k: seen.append(("charge", threading.get_ident())) or 100
    credit.refund_ledgered.side_effect = lambda *a, **k: seen.append(("refund", threading.get_ident())) or {"outcome": "refunded"}
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    assert [k for k, _ in seen] == ["charge", "refund"]
    assert all(t != loop_thread for _, t in seen), "a money RPC ran on the event loop thread"


@pytest.mark.asyncio
async def test_insufficient_credits_never_compensates(monkeypatch):
    """`None` from the precharge is a definite answer (no debit) — nothing to compensate."""
    credit, _ = _install_fakes(monkeypatch, cached=None, charge_return=None)
    resp = await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    assert json.loads(resp.body)["error_code"] == "INSUFFICIENT_CREDITS"
    credit.refund_ledgered.assert_not_called()
    assert tr._USER_GENERATIONS == {}


@pytest.mark.asyncio
async def test_a_charge_that_lands_after_a_deploy_cancel_is_refunded(monkeypatch):
    """Offloading the precharge opened a window the inline call never had: a cancel during the
    round trip raised at the await, `charged` stayed False, and a debit that landed in the
    worker thread was never refunded. `charge_off_loop` hands it back."""
    from app.services import credit_service as cs

    gate = threading.Event()
    credit, service = _install_fakes(monkeypatch, cached=None)

    def _slow_charge(*a, **k):
        gate.wait(timeout=5)
        return 80
    credit.precharge.side_effect = _slow_charge
    task = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: credit.precharge.call_count == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    assert await cs.drain_detached_settlements(2.0) == 0
    service.generate_fresh_report.assert_not_called()
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["reason"] == "report_refund"
    assert kw["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]
    assert tr._USER_GENERATIONS == {} and tr._INFLIGHT_REPORTS == 0
