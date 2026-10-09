"""The chat market-overview card carries `pe_known`, like the index screen it is built from.

`/stable/quote` is unlicensed, so the market P/E comes only from the sector-benchmark
composite; a thin recompute leaves it 0 and the index service says `pe_known=False`. The
chat widget dropped that flag, so the card printed "P/E (TTM) 0.0x · Yield 0.0%" under a
badge reading "Unknown". iOS now renders "—" for both pills when the flag is false OR the
multiple is the 0 sentinel.
"""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.chat import MarketOverviewWidget
from app.services import chat_service as cs


def _detail(pe, known, ey=0.0):
    val = SimpleNamespace(pe_ratio=pe, pe_known=known, forward_pe=0.0, earnings_yield=ey,
                          historical_avg_pe=21.0)
    sp = SimpleNamespace(sectors=[SimpleNamespace(sector="Technology", change_percent=0.8)])
    macro = SimpleNamespace(indicators=[SimpleNamespace(title="10Y", signal="neutral")])
    return SimpleNamespace(snapshots_data=SimpleNamespace(valuation=val, sector_performance=sp,
                                                          macro_forecast=macro))


@pytest.mark.asyncio
@pytest.mark.parametrize("pe, known, ey, expected", [
    (0.0, False, 0.0, False),      # the thin-benchmark state
    (0.0, True, 0.0, False),       # a stale flag cannot make a 0 multiple "known"
    (21.4, True, 4.68, True),
    (21.4, False, 4.68, False),
])
async def test_the_widget_carries_pe_known(monkeypatch, pe, known, ey, expected):
    svc = cs.ChatService.__new__(cs.ChatService)
    fake = SimpleNamespace(get_index_detail=AsyncMock(return_value=_detail(pe, known, ey)))
    monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
    out = await svc._fetch_market_overview_data("^GSPC")
    assert "error" not in out, out
    assert out["pe_known"] is expected
    assert out["pe_ratio"] == pe


def test_the_schema_defaults_known_for_older_rows():
    assert MarketOverviewWidget.model_fields["pe_known"].default is True
    w = MarketOverviewWidget(pe_ratio=0.0, forward_pe=0.0, valuation_level="Unknown",
                             earnings_yield=0.0, historical_avg_pe=21.0)
    assert w.pe_known is True


# ── iOS: the pills are gated ───────────────────────────────────────────────────

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src):
    return "\n".join(re.sub(r"\s//.*$", "", l) for l in src.splitlines() if not l.strip().startswith("//"))


def test_ios_decodes_pe_known_as_optional_and_gates_both_pills():
    dto = _strip((_IOS / "Models/ChatConversationModels.swift").read_text())
    assert "let peKnown: Bool?" in dto and 'case peKnown = "pe_known"' in dto
    assert "var hasKnownPE: Bool { (peKnown ?? true) && peRatio > 0 }" in dto
    view = _strip((_IOS / "Views/Molecules/ChatMarketOverviewWidget.swift").read_text())
    start = view.index("private var valuationMetrics")
    block = view[start:start + 1500]
    assert 'value: data.hasKnownPE ? String(format: "%.1fx", data.peRatio) : "—"' in block
    assert "data.hasKnownPE && data.earningsYield > 0" in block
    assert 'metricPill(label: "P/E (TTM)", value: String(format: "%.1fx", data.peRatio))' not in block


# ── forward_pe_known + macro_indicators_basis (2026-10-08) ─────────────────────
#
# The index pipeline has no forward-multiple source and writes forward_pe=0.0; the widget is
# also the tool result the chat model reads, so `forward_pe_known=False` says the 0 is not a
# multiple. The macro "signals" are labels the index pipeline writes with the model —
# `macro_indicators_basis` says so beside them. Both additive; the 0 sentinel stays because
# iOS decodes `forwardPe` as a non-optional Double.

import math  # noqa: E402
import json  # noqa: E402

from app.schemas.chat import MACRO_INDICATORS_BASIS  # noqa: E402


def _detail_with(forward_pe, forward_known=None, macro=True):
    d = _detail(21.4, True, 4.68)
    d.snapshots_data.valuation.forward_pe = forward_pe
    if forward_known is not None:
        d.snapshots_data.valuation.forward_pe_known = forward_known
    if not macro:
        d.snapshots_data.macro_forecast.indicators = []
    return d


@pytest.mark.asyncio
@pytest.mark.parametrize("forward_pe, flag, expected", [
    (0.0, None, False),                 # today's index pipeline: no flag, a 0 placeholder
    (19.5, None, False),                # a number with no vouching flag is still not known
    (19.5, True, True),
    (0.0, True, False),                 # a stale flag cannot make a 0 known
    (-4.0, True, False),
    (float("nan"), True, False),
    (float("inf"), True, False),
    (19.5, "yes", False),               # only a real True vouches
])
async def test_forward_pe_is_known_only_when_vouched_for_and_positive(monkeypatch, forward_pe,
                                                                      flag, expected):
    svc = cs.ChatService.__new__(cs.ChatService)
    fake = SimpleNamespace(get_index_detail=AsyncMock(return_value=_detail_with(forward_pe, flag)))
    monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
    out = await svc._fetch_market_overview_data("^GSPC")
    assert "error" not in out, out
    assert out["forward_pe_known"] is expected
    if math.isfinite(forward_pe):
        assert out["forward_pe"] == forward_pe, "the wire sentinel is unchanged"
    else:
        # NaN / inf never reach the wire (invalid JSON tokens): the 0 placeholder instead.
        assert out["forward_pe"] == 0.0


@pytest.mark.asyncio
async def test_the_macro_basis_travels_with_macro_items_only(monkeypatch):
    svc = cs.ChatService.__new__(cs.ChatService)
    for macro, expected in ((True, MACRO_INDICATORS_BASIS), (False, None)):
        fake = SimpleNamespace(get_index_detail=AsyncMock(
            return_value=_detail_with(0.0, macro=macro)))
        monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
        out = await svc._fetch_market_overview_data("^GSPC")
        assert out["macro_indicators_basis"] == expected


def test_the_new_fields_are_additive_with_safe_defaults():
    w = MarketOverviewWidget(pe_ratio=0.0, forward_pe=0.0, valuation_level="Unknown",
                             earnings_yield=0.0, historical_avg_pe=21.0)
    assert w.forward_pe_known is False and w.macro_indicators_basis is None
    assert MarketOverviewWidget.model_fields["forward_pe"].is_required()
    assert "Cay AI" in MACRO_INDICATORS_BASIS and "not measured data" in MACRO_INDICATORS_BASIS
    for vendor in ("gemini", "google", "fmp", "model"):
        assert vendor not in MACRO_INDICATORS_BASIS.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
async def test_a_non_finite_multiple_never_reaches_the_wire(monkeypatch, bad):
    svc = cs.ChatService.__new__(cs.ChatService)
    d = _detail(bad, True, bad)
    d.snapshots_data.valuation.historical_avg_pe = bad
    d.snapshots_data.valuation.forward_pe = bad
    fake = SimpleNamespace(get_index_detail=AsyncMock(return_value=d))
    monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
    out = await svc._fetch_market_overview_data("^GSPC")
    assert "error" not in out, out
    json.dumps(out, allow_nan=False)        # raises on any NaN/inf left in the card
    assert out["pe_known"] is False and out["valuation_level"] == "Unknown"
    assert out["forward_pe_known"] is False


# ── earnings_yield_known (final review 2026-10-09) ─────────────────────────────
#
# The yield is 1/PE; the index pipeline writes 0 whenever the P/E is unknown, and the model read
# "earnings_yield: 0.0" with no flag beside it. Additive, fail-closed; the 0 sentinel stays.


@pytest.mark.asyncio
@pytest.mark.parametrize("pe, known, ey, expected", [
    (0.0, False, 0.0, False),                   # the P/E is unknown → the 0 yield is no figure
    (21.4, True, 4.68, True),
    (21.4, False, 4.68, False),                 # a stale yield beside an unknown P/E
    (21.4, True, 0.0, False),                   # a 0 yield is the placeholder, never "0%"
    (21.4, True, float("nan"), False),          # NaN never reaches the wire
])
async def test_the_widget_flags_its_earnings_yield(monkeypatch, pe, known, ey, expected):
    svc = cs.ChatService.__new__(cs.ChatService)
    fake = SimpleNamespace(get_index_detail=AsyncMock(return_value=_detail(pe, known, ey)))
    monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
    out = await svc._fetch_market_overview_data("^GSPC")
    assert "error" not in out, out
    assert out["earnings_yield_known"] is expected
    assert out["earnings_yield"] == (ey if expected else out["earnings_yield"])
    assert out["earnings_yield"] == out["earnings_yield"], "never NaN"


def test_the_earnings_yield_flag_defaults_closed():
    assert MarketOverviewWidget.model_fields["earnings_yield_known"].default is False
