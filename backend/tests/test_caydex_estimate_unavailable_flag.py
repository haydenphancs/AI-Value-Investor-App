"""R3 (dcf-v2 plan, 2026-10-23): the Analysis tab's "temporarily unavailable" Caydex row — backend half.

On 2026-10-21 FRED failed, every Caydex Fair Value Estimate raised `DcfInputsUnavailableError`,
and the Valuation Meter drew NO row at all: an outage read exactly like "this company has no
estimate". The served Price card now carries `caydex_estimate_unavailable`:

* True only when the estimate's inputs could not be fetched for this serve
  (`DcfInputsUnavailableError` — the leader's, an in-flight follower's or the failure memo's);
* None otherwise — an ok estimate, a refusal (an answer, carried in `caydex_estimate`), the
  service's stricter ticker regex (ValueError) or any other error. Never False;
* set at SERVE time only: never in the 24 h `snapshot_cache` row, never on the cached object,
  and never part of the multiples build's degraded status.

Ask Cay AI's financials tool states the same outage instead of omitting the fair value. The iOS
half (DTO, row, parity) is pinned elsewhere. Hermetic: the DCF service and Supabase are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List

import pytest

import app.services.chat_financials_tool as cft
import app.services.dcf_fair_value_service as dcf_mod
import app.services.valuation_snapshot_service as vss
from app.config import settings
from app.schemas.dcf_fair_value import DcfFairValueResponse
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    StockOverviewResponse,
)

_OK = DcfFairValueResponse(symbol="AAPL", status="ok", model_version="dcf-v2", fair_value=250.0,
                           range_low=210.0, range_high=290.0, currency="USD")
_REFUSED = DcfFairValueResponse(symbol="AAPL", status="refused", model_version="dcf-v2",
                                refusal_code="financial_company",
                                refusal_reason=dcf_mod.REFUSAL_REASONS["financial_company"])
_UNAVAILABLE = dcf_mod.DcfInputsUnavailableError("10-year Treasury average unavailable for AAPL")


def _permanent_error(msg: str = "DCF inputs unavailable for AAPL: estimates"):
    """An inputs error whose every failed FMP leg was a permanent refusal (402 / non-429 4xx).
    The constructor's ``permanent=True`` keyword when it has one, else the attribute the reader
    looks for (``getattr(e, "permanent", False)``)."""
    try:
        err = dcf_mod.DcfInputsUnavailableError(msg, permanent=True)
    except TypeError:
        err = dcf_mod.DcfInputsUnavailableError(msg)
        err.permanent = True
    assert getattr(err, "permanent", None) is True
    return err


def _flagged(flag: Any):
    """A TRANSIENT-built inputs error with ``permanent`` forced to ``flag``."""
    err = dcf_mod.DcfInputsUnavailableError("10-year Treasury average unavailable for AAPL")
    err.permanent = flag
    return err


_PERMANENT = _permanent_error()


class _StubDcf:
    def __init__(self, result: Any) -> None:
        self.result, self.calls = result, 0

    async def get_fair_value(self, ticker: str):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _use(monkeypatch, result: Any) -> _StubDcf:
    stub = _StubDcf(result)
    # function-scoped import inside caydex_estimate_with_status → patch the SOURCE module
    monkeypatch.setattr(dcf_mod, "get_dcf_fair_value_service", lambda: stub)
    return stub


def _built() -> SnapshotItemResponse:
    return SnapshotItemResponse(
        category="Price", rating=3, weighted_score=3.0,
        metrics=[SnapshotMetricResponse(name="P/E", value="25.0x", metric_key="pe", score=3)],
    )


# ── caydex_estimate_with_status: only DcfInputsUnavailableError is "unavailable" ──────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("result, estimate, unavailable", [
    (_OK, _OK, False),
    (_REFUSED, _REFUSED, False),                               # a refusal is an answer
    (_UNAVAILABLE, None, True),
    # Every failed leg refused for good (402 / non-429 4xx): not temporary, so no such row.
    (_PERMANENT, None, False),
    # Only a real True is permanent: anything else stays a transient outage (fail closed).
    (_flagged(False), None, True),
    (_flagged(None), None, True),
    (_flagged("yes"), None, True),
    (_flagged(1), None, True),
    (ValueError("Invalid ticker symbol: 'BRK.B'"), None, False),  # the DCF's stricter regex
    (RuntimeError("in-flight DCF computation was cancelled"), None, False),
    (dcf_mod.FMPUnavailableException("parent class only"), None, False),
])
async def test_only_an_inputs_outage_counts_as_unavailable(monkeypatch, result, estimate, unavailable):
    _use(monkeypatch, result)
    got = await vss.caydex_estimate_with_status("AAPL")
    assert got == (estimate, unavailable)
    assert got[1] is unavailable, "a real bool, never a truthy stand-in"


@pytest.mark.asyncio
async def test_the_wrapper_keeps_its_old_contract(monkeypatch):
    for result, expected in ((_OK, _OK), (_REFUSED, _REFUSED), (_UNAVAILABLE, None),
                             (_PERMANENT, None),
                             (ValueError("odd"), None), (RuntimeError("boom"), None)):
        _use(monkeypatch, result)
        assert await vss.caydex_estimate_or_none("AAPL") == expected, result


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed(monkeypatch):
    _use(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await vss.caydex_estimate_with_status("AAPL")


@pytest.mark.asyncio
async def test_both_failures_are_logged_with_the_ticker_and_the_error(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=vss.logger.name)
    _use(monkeypatch, _UNAVAILABLE)
    await vss.caydex_estimate_with_status("AAPL")
    _use(monkeypatch, RuntimeError("boom"))
    await vss.caydex_estimate_with_status("MSFT")
    lines = [r.getMessage() for r in caplog.records if "[caydex-dcf-unavailable]" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "AAPL" in lines[0] and "DcfInputsUnavailableError" in lines[0] and "temporarily" in lines[0]
    assert "MSFT" in lines[1] and "RuntimeError: boom" in lines[1] and "temporarily" not in lines[1]


@pytest.mark.asyncio
async def test_a_permanent_refusal_is_logged_but_never_called_temporary(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=vss.logger.name)
    _use(monkeypatch, _permanent_error("DCF inputs unavailable for NVDA: estimates"))
    assert await vss.caydex_estimate_with_status("NVDA") == (None, False)
    (line,) = [r.getMessage() for r in caplog.records if "[caydex-dcf-unavailable]" in r.getMessage()]
    assert "NVDA" in line and "DcfInputsUnavailableError" in line and "estimates" in line
    assert "permanent" in line and "temporarily" not in line


@pytest.mark.asyncio
async def test_the_real_services_failure_memo_and_inflight_join_count_as_unavailable(monkeypatch):
    """Not just the leader's raise: a follower awaiting the shared future, and every caller within
    the failure memo, receive `DcfInputsUnavailableError` too — all three say "temporarily"."""
    monkeypatch.setattr(dcf_mod.settings, "DCF_ENABLED", True)
    dcf_mod._cache.clear()
    dcf_mod._failed_at.clear()
    dcf_mod._inflight.clear()
    svc = dcf_mod.DcfFairValueService.__new__(dcf_mod.DcfFairValueService)
    svc._read_stored = lambda *a, **k: None
    svc._write_stored = lambda *a, **k: pytest.fail("an outage must never be stored")
    svc._append_history = lambda *a, **k: pytest.fail("an outage must never be recorded")
    gate = asyncio.Event()
    computes: List[str] = []

    async def _compute(ticker):
        computes.append(ticker)
        await gate.wait()
        raise dcf_mod.DcfInputsUnavailableError(f"10-year Treasury average unavailable for {ticker}")

    svc._compute = _compute
    monkeypatch.setattr(dcf_mod, "get_dcf_fair_value_service", lambda: svc)
    try:
        leader = asyncio.ensure_future(vss.caydex_estimate_with_status("AAPL"))
        await asyncio.sleep(0.01)
        follower = asyncio.ensure_future(vss.caydex_estimate_with_status("AAPL"))
        await asyncio.sleep(0.01)
        gate.set()
        assert await leader == (None, True)
        assert await follower == (None, True)
        assert await vss.caydex_estimate_with_status("AAPL") == (None, True)   # the memo
        assert computes == ["AAPL"], "one computation; the follower and the memo re-raise it"
    finally:
        dcf_mod._cache.clear()
        dcf_mod._failed_at.clear()
        dcf_mod._inflight.clear()


# ── the served Price card ────────────────────────────────────────────────────────────────────

def _svc(monkeypatch, *, enabled: bool = True, shadow: bool = False,
         degraded: List[str] | None = None):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", enabled)
    monkeypatch.setattr(vss.settings, "DCF_SHADOW", shadow)
    vss._cache.clear()
    vss._inflight.clear()
    vss._degraded_by_key.clear()
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.fmp = None
    svc.supabase = None
    built = _built()

    async def _compute(ticker):
        return built, list(degraded or [])

    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    monkeypatch.setattr(svc, "_compute_with_status", _compute)
    monkeypatch.setattr(svc, "_upsert_supabase_cache", lambda *a, **k: None)
    return svc, built


@pytest.fixture(autouse=True)
def _clean_caches():
    yield
    vss._cache.clear()
    vss._inflight.clear()
    vss._degraded_by_key.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("result, expected_estimate, expected_flag", [
    (_UNAVAILABLE, None, True),
    (_PERMANENT, None, None),                       # refused for good: no row, never True
    (_OK, _OK, None),
    (_REFUSED, _REFUSED, None),
    (ValueError("Invalid ticker symbol: 'AAPL'"), None, None),
    (RuntimeError("boom"), None, None),
])
async def test_the_served_card_carries_true_or_none_never_false(
    monkeypatch, result, expected_estimate, expected_flag,
):
    svc, built = _svc(monkeypatch)
    _use(monkeypatch, result)
    snap = await svc.get_valuation_snapshot("AAPL")
    assert snap.caydex_estimate == expected_estimate
    assert snap.caydex_estimate_unavailable is expected_flag
    assert snap.caydex_estimate_unavailable is not False
    assert snap.dcf is None and snap.metrics == built.metrics, "the meter still renders"


@pytest.mark.asyncio
async def test_the_flag_is_never_part_of_the_degraded_status(monkeypatch):
    """It describes the estimate, not the multiples build: a clean build stays [] (and so still
    persists), and a degraded one reports only its own slices."""
    for degraded in ([], ["ratios_ttm"]):
        svc, _ = _svc(monkeypatch, degraded=degraded)
        _use(monkeypatch, _UNAVAILABLE)
        snap, status = await svc.get_valuation_snapshot_with_status("AAPL")
        assert snap.caydex_estimate_unavailable is True
        assert status == degraded, status
        again, status2 = await svc.get_valuation_snapshot_with_status("AAPL")   # tier-1 hit
        assert again.caydex_estimate_unavailable is True and status2 == degraded


@pytest.mark.asyncio
async def test_the_cached_object_is_never_flagged(monkeypatch):
    """The flag rides a COPY: the tier-1 entry (served to the next viewer and the next report)
    stays clean, so a recovered service is shown at once."""
    svc, built = _svc(monkeypatch)
    stub = _use(monkeypatch, _UNAVAILABLE)
    served = await svc.get_valuation_snapshot("AAPL")
    assert served is not built and served.caydex_estimate_unavailable is True
    cached = vss._cache_get("val_snapshot:AAPL")
    assert cached is built and cached.caydex_estimate_unavailable is None
    stub.result = _OK
    recovered = await svc.get_valuation_snapshot("AAPL")
    assert recovered.caydex_estimate == _OK and recovered.caydex_estimate_unavailable is None


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [False, True])
async def test_switch_off_never_flags_and_never_asks(monkeypatch, shadow):
    svc, built = _svc(monkeypatch, enabled=False, shadow=shadow)
    stub = _use(monkeypatch, _UNAVAILABLE)
    snap = await svc.get_valuation_snapshot("AAPL")
    await asyncio.sleep(0.01)                       # let a shadow recording run
    assert snap is built, "off = the cached object itself, no serve-time fields"
    assert snap.caydex_estimate is None and snap.caydex_estimate_unavailable is None
    assert stub.calls == (1 if shadow else 0)


# ── the 24 h snapshot_cache row ─────────────────────────────────────────────────────────────

class _RecordingSupabase:
    def __init__(self) -> None:
        self.upserts: List[Dict[str, Any]] = []

    def table(self, _):
        return self

    def upsert(self, row, **_k):
        self.upserts.append(row)
        return self

    def execute(self):
        return type("R", (), {"data": []})()


@pytest.mark.parametrize("enabled", [True, False])
def test_the_stored_row_never_carries_a_serve_time_field(monkeypatch, enabled):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", enabled)
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = _RecordingSupabase()
    flagged = _built().model_copy(update={"caydex_estimate": _OK, "caydex_estimate_unavailable": True})
    svc._upsert_supabase_cache("AAPL", flagged)
    (row,) = svc.supabase.upserts
    stored = row["response_json"]
    assert "caydex_estimate_unavailable" not in stored, "a stored flag would last 24 h"
    assert "caydex_estimate" not in stored
    assert stored["category"] == "Price" and stored["metrics"], "the multiples themselves persist"
    assert stored[vss._VERSION_KEY] == vss._SNAPSHOT_PAYLOAD_VERSION
    assert stored[vss._DCF_SOURCE_KEY] is enabled
    # …and the stored row revives as an unflagged card.
    revived = SnapshotItemResponse(**{k: v for k, v in stored.items() if not k.startswith("_")})
    assert revived.caydex_estimate_unavailable is None


# ── the wire shape iOS decodes ──────────────────────────────────────────────────────────────

def test_the_field_is_optional_and_none_by_default():
    assert SnapshotItemResponse.model_fields["caydex_estimate_unavailable"].default is None
    assert SnapshotItemResponse(category="Price", rating=3, metrics=[]).caydex_estimate_unavailable is None
    # A legacy cached row (no key) still validates.
    legacy = SnapshotItemResponse.model_validate({"category": "Price", "rating": 3, "metrics": []})
    assert legacy.caydex_estimate_unavailable is None


@pytest.mark.parametrize("flag", [True, None])
def test_json_carries_the_snake_case_key(flag):
    snap = SnapshotItemResponse(category="Price", rating=3, metrics=[],
                                caydex_estimate_unavailable=flag)
    wire = json.loads(snap.model_dump_json())
    assert "caydex_estimate_unavailable" in wire and wire["caydex_estimate_unavailable"] is flag
    assert "caydexEstimateUnavailable" not in wire
    # The overview response (the Analysis tab's source) carries it at the snapshot level.
    fields = StockOverviewResponse.model_fields["snapshots"]
    assert "SnapshotItemResponse" in str(fields.annotation)
    assert snap.model_dump(mode="json")["caydex_estimate_unavailable"] is flag


# ── Ask Cay AI's financials tool says the same thing ────────────────────────────────────────

_VENDORS = re.compile(r"fmp|financial ?modeling ?prep|\bfred\b|treasury|\bgemini|\bgoogle", re.I)


def _price_card(metrics=None, **over):
    return SnapshotItemResponse(
        category="Price", rating=3,
        metrics=metrics if metrics is not None else [
            SnapshotMetricResponse(name="P/E", value="35.90"),
            SnapshotMetricResponse(name="P/S", value="9.10"),
        ],
        computed_at="2026-10-23T12:00:00Z", **over,
    )


_KF = {"ticker": "AAPL", "company_name": "Apple Inc.", "rows": {}, "unavailable": [],
       "live_price_ok": True, "statement_currency": "USD", "price_currency": "USD",
       "is_fund": False, "balance_sheet": None, "short_interest_settlement_date": None}


def test_chat_states_the_outage_and_never_a_refusal(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    fv = cft._fair_value(_price_card(caydex_estimate_unavailable=True), _KF)
    assert fv["status"] == "temporarily unavailable" and "value" not in fv
    assert "temporarily unavailable" in fv["reason"]
    assert fv["status"] != "no estimate for this company"
    assert _VENDORS.search(json.dumps(fv)) is None
    # No flag (None) = no fair value at all, exactly as before; a cached dict payload too.
    assert cft._fair_value(_price_card(), _KF) is None
    assert cft._fair_value(_price_card().model_dump(), _KF) is None
    assert cft._fair_value({**_price_card().model_dump(), "caydex_estimate_unavailable": True},
                           _KF)["status"] == "temporarily unavailable"
    # Only a real True counts.
    assert cft._fair_value({**_price_card().model_dump(), "caydex_estimate_unavailable": "yes"},
                           _KF) is None


def test_chat_ignores_the_flag_while_the_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    assert cft._fair_value(_price_card(caydex_estimate_unavailable=True), _KF) is None


def test_chat_valuation_block_with_multiples_carries_the_outage(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    block = cft._valuation_block(cft._WithStatus(_price_card(caydex_estimate_unavailable=True), []), _KF)
    assert block["multiples"] and block["fair_value"]["status"] == "temporarily unavailable"
    assert "incomplete" not in block


@pytest.mark.parametrize("degraded", [[], ["ratios_ttm"]])
def test_chat_an_outage_alone_is_a_card_that_did_not_load(monkeypatch, degraded):
    """No multiples and an unavailable estimate is NOT an answer: it must stay a missing block,
    so the doors' refund gate (`shown == 0 and upstream_missing`) still sees nothing usable."""
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    empty = _price_card(metrics=[SnapshotMetricResponse(name="P/E", value="—")],
                        caydex_estimate_unavailable=True)
    with pytest.raises(cft._Incomplete):
        cft._valuation_block(cft._WithStatus(empty, degraded), _KF)
    # Without the flag the old answers stand: degraded → did not load, clean → nothing to show.
    plain = _price_card(metrics=[SnapshotMetricResponse(name="P/E", value="—")])
    if degraded:
        with pytest.raises(cft._Incomplete):
            cft._valuation_block(cft._WithStatus(plain, degraded), _KF)
    else:
        assert cft._valuation_block(cft._WithStatus(plain, degraded), _KF) is None


@pytest.fixture
def _chat_env(monkeypatch):
    cft._inflight.clear()
    cft._source_tasks.clear()
    monkeypatch.setattr(cft, "_BLOCK_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000)
    yield
    cft._inflight.clear()
    cft._source_tasks.clear()


def _loader(value: Any):
    async def _load(sym):
        if isinstance(value, BaseException):
            raise value
        return value
    return _load


@pytest.mark.asyncio
async def test_chat_end_to_end_the_tool_result_names_the_outage(monkeypatch, _chat_env):
    monkeypatch.setattr(cft, "_load_key_facts", _loader(dict(_KF)))
    monkeypatch.setattr(cft, "_load_valuation", _loader(
        cft._WithStatus(_price_card(caydex_estimate_unavailable=True), [])))
    out = await cft.fetch_company_financials("AAPL", "valuation")
    assert out["valuation"]["fair_value"]["status"] == "temporarily unavailable"
    assert "error" not in out


@pytest.mark.asyncio
async def test_chat_end_to_end_an_outage_alone_still_fails_the_tool(monkeypatch, _chat_env):
    monkeypatch.setattr(cft, "_load_key_facts", _loader(RuntimeError("upstream 503")))
    monkeypatch.setattr(cft, "_load_valuation", _loader(cft._WithStatus(
        _price_card(metrics=[SnapshotMetricResponse(name="P/E", value="—")],
                    caydex_estimate_unavailable=True), [])))
    out = await cft.fetch_company_financials("AAPL", "valuation")
    assert out.get("upstream") is True and "error" in out, out
    assert any(u.startswith("valuation: did not load") for u in out["unavailable"]), out
