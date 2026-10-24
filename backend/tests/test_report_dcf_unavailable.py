"""R2 (dcf-v2, 2026-10-23) — a report built while the Caydex Fair Value Estimate's INPUTS were
unavailable must not be frozen into the shared caches.

On 2026-10-21 FRED failed, so `DcfFairValueService._compute` raised `DcfInputsUnavailableError`
for every ticker. The collector landed that on the plain pass-1 default (None), so a report built
during the outage (RKLB) froze "No Caydex Fair Value Estimate is available for this report" into
ticker_data_cache and the shared report caches until the close.

Pins (`_settle_pass1_result` → `_settle_caydex_dcf_failure`):
  * success → the estimate is stored, nothing is recorded;
  * `DcfInputsUnavailableError` (transient) → None + ``caydex_dcf:DcfInputsUnavailableError`` on
    ``degraded_sections``, so the collection is not stored and the report rides
    `DEGRADED_SECTIONS_KEY` — delivered and billed, never `REPORT_DEGRADED_KEY` (refund);
  * `ValueError` (the DCF's stricter ticker grammar — permanent) → None, NOT recorded: recording
    a permanent state keeps the ticker out of every cache for good;
  * `DcfInputsUnavailableError` marked ``permanent`` (every failed FMP leg was a 402 not-entitled
    or a non-429 4xx — repeats on every rebuild) → None, logged, NOT recorded, for the same
    reason; only a real ``permanent is True`` counts (fail closed);
  * anything else (a bug) → None, logged at ERROR with its stack, NOT recorded.

Hermetic: the DCF service is stubbed at its factory; no FMP / FRED / Supabase / Gemini.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.schemas.dcf_fair_value import DcfFairValueResponse
from app.services import dcf_fair_value_service as dcf
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.agents.ticker_report_data_collector import (
    DEGRADED_SECTIONS_KEY,
    CollectedTickerData,
    _settle_pass1_result,
    report_degraded_sections,
)
from app.services.dcf_fair_value_service import DcfInputsUnavailableError
from app.services.report_degradation import REPORT_DEGRADED_KEY, report_degraded_reason

# The schema-parity fixture builder, and its autouse fixture that stubs every live benchmark /
# moat lookup `assemble_report` reaches (importing an autouse fixture activates it here too).
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)

_SECTION = "caydex_dcf:DcfInputsUnavailableError"


def _permanent_error(msg: str = "DCF inputs unavailable for RKLB: estimates") -> DcfInputsUnavailableError:
    """A `DcfInputsUnavailableError` whose every failed leg was a permanent refusal. Built with
    the constructor's ``permanent=True`` keyword when it has one, else by setting the attribute
    the readers look for (``getattr(e, "permanent", False)``)."""
    try:
        err = DcfInputsUnavailableError(msg, permanent=True)
    except TypeError:
        err = DcfInputsUnavailableError(msg)
        err.permanent = True
    assert getattr(err, "permanent", None) is True
    return err


def _estimate(symbol: str = "RKLB") -> DcfFairValueResponse:
    return DcfFairValueResponse(
        symbol=symbol, status="ok", model_version=dcf.MODEL_VERSION,
        fair_value=21.4, range_low=17.9, range_high=26.2, as_of="2026-10-23",
    )


def _out() -> CollectedTickerData:
    return CollectedTickerData(ticker="RKLB", persona_key="warren_buffett")


def _settle(result, *, out=None) -> CollectedTickerData:
    out = out if out is not None else _out()
    _settle_pass1_result(out, "caydex_dcf", result, None, "RKLB")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# The four arms
# ═══════════════════════════════════════════════════════════════════════════


def test_a_value_is_stored_and_nothing_is_recorded():
    est = _estimate()
    out = _settle(est)
    assert out.caydex_dcf is est
    assert out.degraded_sections == []


def test_a_none_answer_falls_to_the_default_and_nothing_is_recorded():
    out = _settle(None)
    assert out.caydex_dcf is None and out.degraded_sections == []


def test_inputs_unavailable_leaves_no_estimate_and_records_the_section(caplog):
    out = _out()
    out.caydex_dcf = _estimate()        # whatever was there, a failure must not leave it
    with caplog.at_level(logging.WARNING, logger=C.__name__):
        _settle(DcfInputsUnavailableError("10-year Treasury average unavailable for RKLB"), out=out)
    assert out.caydex_dcf is None
    assert out.degraded_sections == [_SECTION]
    (rec,) = [r for r in caplog.records if "[report-dcf-unavailable]" in r.getMessage()]
    assert rec.levelno == logging.WARNING
    assert "RKLB" in rec.getMessage() and "Treasury average" in rec.getMessage()


def test_a_permanent_inputs_refusal_leaves_no_estimate_and_is_never_recorded(caplog):
    """Every failed leg was a permanent refusal (a 402 not-entitled, a non-429 4xx): it repeats
    on every rebuild, so recording it would keep the ticker out of every cache for good and each
    open would regenerate for 20 credits — the holders lesson."""
    out = _out()
    out.caydex_dcf = _estimate()
    with caplog.at_level(logging.WARNING, logger=C.__name__):
        _settle(_permanent_error("DCF inputs unavailable for RKLB: estimates"), out=out)
    assert out.caydex_dcf is None
    assert out.degraded_sections == []
    (rec,) = [r for r in caplog.records if "[report-dcf-unavailable]" in r.getMessage()]
    assert rec.levelno == logging.WARNING
    msg = rec.getMessage()
    assert "RKLB" in msg and "permanent" in msg and "estimates" in msg
    assert "kept out of every shared cache" not in msg


@pytest.mark.parametrize("flag", [False, None, "yes", 1])
def test_only_a_real_true_marks_the_refusal_permanent(flag):
    """Fail closed: an explicit False, or any truthy stand-in that is not True, stays TRANSIENT —
    a report must never be shared by mistake on a mis-typed flag."""
    err = DcfInputsUnavailableError("10-year Treasury average unavailable for RKLB")
    err.permanent = flag
    out = _settle(err)
    assert out.caydex_dcf is None
    assert out.degraded_sections == [_SECTION]


def test_an_error_without_the_attribute_stays_transient():
    """Before (or without) the service's `permanent` marker every inputs error is an outage."""
    err = DcfInputsUnavailableError("10-year Treasury average unavailable for RKLB")
    if "permanent" in vars(err):
        del err.permanent
    assert getattr(err, "permanent", False) is not True
    assert _settle(err).degraded_sections == [_SECTION]


def test_a_ticker_the_dcf_refuses_is_logged_but_never_recorded(caplog):
    """The DCF's ticker grammar (`_TICKER_RE`) is stricter than the report door's: the
    ValueError repeats on every rebuild, so recording it would block every cache for good."""
    with caplog.at_level(logging.WARNING, logger=C.__name__):
        out = _settle(ValueError("Invalid ticker symbol: 'BRK.B'"))
    assert out.caydex_dcf is None
    assert out.degraded_sections == []
    (rec,) = [r for r in caplog.records if "[report-dcf-ticker-refused]" in r.getMessage()]
    assert rec.levelno == logging.WARNING and "BRK.B" in rec.getMessage()


@pytest.mark.parametrize("exc", [
    RuntimeError("in-flight DCF computation was cancelled"),
    TypeError("unsupported operand type(s) for *: 'NoneType' and 'float'"),
    # The PARENT of DcfInputsUnavailableError: only the DCF's own transient type blocks.
    dcf.FMPUnavailableException("raw upstream failure"),
])
def test_an_unexpected_failure_is_logged_with_its_stack_but_never_recorded(caplog, exc):
    with caplog.at_level(logging.WARNING, logger=C.__name__):
        out = _settle(exc)
    assert out.caydex_dcf is None
    assert out.degraded_sections == []
    (rec,) = [r for r in caplog.records if "[report-dcf-failed]" in r.getMessage()]
    assert rec.levelno == logging.ERROR
    assert rec.exc_info is not None and rec.exc_info[1] is exc, "the stack must ride the log"
    assert "RKLB" in rec.getMessage() and type(exc).__name__ in rec.getMessage()


def test_a_cancelled_child_is_never_stored_as_the_estimate():
    """`gather(return_exceptions=True)` hands back a child's CancelledError — a BaseException,
    not an Exception — which the generic arm would have stored on the field as-is (and the
    collection's `model_dump` would then crash)."""
    out = _settle(asyncio.CancelledError())
    assert out.caydex_dcf is None and out.degraded_sections == []


def test_the_transient_type_is_not_a_value_error():
    """The ValueError arm must never swallow the transient one (ordering anti-vacuity)."""
    assert not issubclass(DcfInputsUnavailableError, ValueError)
    assert issubclass(DcfInputsUnavailableError, dcf.FMPUnavailableException)


# ═══════════════════════════════════════════════════════════════════════════
# The real seam: `_caydex_dcf` raising into pass 1's gather
# ═══════════════════════════════════════════════════════════════════════════


class _RaisingDcf:
    def __init__(self, exc):
        self.exc, self.calls = exc, []

    async def get_fair_value(self, ticker):
        self.calls.append(ticker)
        raise self.exc


@pytest.mark.asyncio
@pytest.mark.parametrize("exc, recorded", [
    (DcfInputsUnavailableError("DCF inputs unavailable for RKLB: income_annual"), [_SECTION]),
    (_permanent_error("DCF inputs unavailable for RKLB: estimates"), []),
    (ValueError("Invalid ticker symbol: 'RKLB.W'"), []),
])
async def test_the_fetch_failure_reaches_the_settle_through_the_gather(monkeypatch, exc, recorded):
    # `_caydex_dcf` imports the factory INSIDE the function, so patch the SOURCE module.
    svc = _RaisingDcf(exc)
    monkeypatch.setattr(dcf, "get_dcf_fair_value_service", lambda: svc)
    (result,) = await asyncio.gather(C._caydex_dcf("RKLB"), return_exceptions=True)
    assert result is exc and svc.calls == ["RKLB"]
    out = _settle(result)
    assert out.caydex_dcf is None
    assert out.degraded_sections == recorded


# ═══════════════════════════════════════════════════════════════════════════
# What the section DOES: the collection is not shared; the report is delivered and billed
# ═══════════════════════════════════════════════════════════════════════════


async def _collect_through_the_cache(monkeypatch, out, ticker):
    from app.services import ticker_data_cache as tdc

    writes = []

    async def _get_cached(_t):
        return None

    async def _store(t, _out):
        writes.append(t)

    monkeypatch.setattr(tdc, "get_cached_collection", _get_cached)
    monkeypatch.setattr(tdc, "store_collection", _store)

    async def _fetch():
        return out

    assert await tdc.get_or_collect(ticker, _fetch) is out
    return writes


@pytest.mark.asyncio
async def test_a_collection_without_its_estimate_for_an_outage_is_not_stored(monkeypatch, caplog):
    out = _settle(DcfInputsUnavailableError("10-year Treasury average unavailable for RKLB"))
    with caplog.at_level(logging.WARNING):
        writes = await _collect_through_the_cache(monkeypatch, out, "ZZDCF1")
    assert writes == []
    assert _SECTION in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    _estimate(),
    ValueError("Invalid ticker symbol: 'RKLB.W'"),
    _permanent_error("DCF inputs unavailable for RKLB: estimates"),
    RuntimeError("unexpected"),
])
async def test_a_permanent_or_unexpected_failure_still_stores_the_collection(monkeypatch, result):
    """The passing siblings: only the transient outage keeps the collection out."""
    out = _settle(result)
    assert await _collect_through_the_cache(monkeypatch, out, "ZZDCF2") == ["ZZDCF2"]


def test_the_section_rides_the_report_as_a_partial_never_as_the_refund_marker():
    out = _make_collected_data()
    _settle(DcfInputsUnavailableError("10-year Treasury average unavailable"), out=out)
    report = C.TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    assert report[DEGRADED_SECTIONS_KEY] == [_SECTION]
    assert report_degraded_sections(report) == [_SECTION]
    # Delivered and billed like any other report: the refund marker is untouched.
    assert REPORT_DEGRADED_KEY not in report
    assert report_degraded_reason(report) is None
    # And the card says "no estimate" rather than quoting one.
    assert report["wall_street_consensus"].get("caydex_fair_value") is None


def test_a_permanent_refusal_rides_the_report_with_no_section_and_no_estimate():
    """The passing sibling of the outage case: no estimate on the card, nothing recorded, so the
    report is shared like any other."""
    out = _make_collected_data()
    _settle(_permanent_error("DCF inputs unavailable: estimates"), out=out)
    report = C.TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    assert _SECTION not in report_degraded_sections(report)
    assert REPORT_DEGRADED_KEY not in report
    assert report["wall_street_consensus"].get("caydex_fair_value") is None
