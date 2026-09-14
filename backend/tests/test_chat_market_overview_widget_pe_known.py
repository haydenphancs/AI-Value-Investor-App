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
