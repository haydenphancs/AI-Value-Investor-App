"""F19-6 — the equity screen was the fifth asset class with no `change_known`.

`StockOverviewCoreResponse` / `StockOverviewResponse` shipped `price_change: 0.0` with no
flag whenever neither the quote nor the profile carried a day change — FMP
`/stable/profile` legitimately answers `change: null` for a halted/OTC listing, and the
quote leg can fail while the profile lands. iOS `TickerCoreData.isPositive` read that
placeholder as a measured flat day and the header painted "▲ +0.00 (+0.00%)" in green
with a bullish flash. Index / ETF / crypto / commodity all carry the flag; now stock does.

Backend: the flag is derived from PRESENCE across the same (quote, profile) source list
`_first_present_float` scans — `is not None` and finite, never truthiness, so a genuine
0.0 stays KNOWN. iOS (in-lane half): both DTOs decode it as `Bool?` and `TickerCoreData`
gates sign / text on it, mirroring `ETFDetailData`. The `TickerDetailData` /
`TickerDetailView` / `pollQuotePrice` half is another lane's files — see the fix report.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from app.schemas.stock_overview import StockOverviewCoreResponse, StockOverviewResponse
from app.services import stock_overview_service as sos
from app.services.stock_overview_service import (
    StockOverviewService,
    _cache,
    _first_present_float,
    _first_present_or_none,
)
from _price_fakes import PriceFromFMPFake


# ── schema ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [StockOverviewCoreResponse, StockOverviewResponse])
def test_change_known_defaults_true_for_cached_and_older_payloads(model):
    field = model.model_fields["change_known"]
    assert field.default is True, "an older cached payload must keep meaning 'known'"


# ── presence helper ───────────────────────────────────────────────────────────

def test_first_present_or_none_is_three_state():
    q, p = {"change": None}, {"change": None}
    assert _first_present_or_none((q, "change"), (p, "change")) is None
    assert _first_present_or_none((q, "change"), ({"change": 0.0}, "change")) == 0.0
    assert _first_present_or_none(({"change": float("nan")}, "change"), ({"change": 1.5}, "change")) == 1.5
    assert _first_present_or_none(({"change": "abc"}, "change"), ("not-a-dict", "change")) is None
    assert _first_present_or_none(({"change": True}, "change")) is None, "a bool is not a change"
    assert _first_present_or_none(({}, "change")) is None
    assert _first_present_or_none() is None
    # The coercing sibling still delegates: same source order, default only on None.
    assert _first_present_float((q, "change"), (p, "change")) == 0.0
    assert _first_present_float((q, "change"), ({"change": -2.5}, "change")) == -2.5


# ── get_overview_core ─────────────────────────────────────────────────────────

class _FMP:
    def __init__(self, quote, profile):
        self._quote, self._profile = quote, profile

    async def get_stock_price_quote(self, ticker):
        return dict(self._quote)

    async def get_company_profile(self, ticker):
        return dict(self._profile)

    def __getattr__(self, name):
        async def _forbidden(*a, **k):
            raise AssertionError(f"core path must not call fmp.{name}()")
        return _forbidden


def _core_svc(quote, profile):
    _cache.clear()
    svc = StockOverviewService()
    svc.fmp = _FMP(quote, profile)  # type: ignore[assignment]
    svc.price = PriceFromFMPFake(svc.fmp)
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("quote, profile, known", [
    # The live failure: a halted/OTC profile with a price and no change.
    ({}, {"price": 43.08, "change": None, "changePercentage": None, "companyName": "X"}, False),
    ({"price": 43.08, "change": None}, {"change": None}, False),
    ({"price": 43.08}, {"companyName": "X"}, False),
    # A genuine flat day is KNOWN — 0.0 must never be read as "missing".
    ({"price": 43.08, "change": 0.0, "changePercentage": 0.0}, {}, True),
    ({"price": 43.08, "change": 0.0}, {"changePercentage": None}, True),
    # Only the percent present (either spelling) is still a known change.
    ({"price": 43.08, "changesPercentage": 1.25}, {}, True),
    ({"price": 43.08}, {"changePercentage": -0.4}, True),
    ({"price": 43.08, "change": -1.2, "changePercentage": -2.7}, {}, True),
    # Non-finite in the quote, nothing in the profile: unknown, and the wire stays finite.
    ({"price": 43.08, "change": float("inf"), "changePercentage": float("nan")}, {"change": None}, False),
    # Non-finite in the quote, a real value behind it in the profile: known, profile wins.
    ({"price": 43.08, "change": float("nan")}, {"change": 0.55, "changePercentage": 1.3}, True),
])
async def test_core_marks_an_unknown_day_change(quote, profile, known):
    resp = await _core_svc(quote, profile).get_overview_core("XYZ", chart_range="3M")
    assert resp.change_known is known
    assert math.isfinite(resp.price_change) and math.isfinite(resp.price_change_percent)
    if not known:
        assert resp.price_change == 0.0 and resp.price_change_percent == 0.0
    dumped = resp.model_dump(mode="json")
    assert dumped["change_known"] is known


# ── _build_full_response ──────────────────────────────────────────────────────

class _StubBenchmarkLookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        return {}


def _full(monkeypatch, quote, profile):
    _cache.clear()
    svc = StockOverviewService()
    monkeypatch.setattr(sos, "get_sector_benchmark_lookup", lambda: _StubBenchmarkLookup())
    return svc._build_full_response(
        "XYZ",
        {"profile": {"companyName": "Xyz Corp", "sector": "Technology", **profile}},
        {"quote": quote, "chart_data": []},
        "1D", "5min", False,
    )


@pytest.mark.parametrize("quote, profile, known", [
    ({"price": 43.08}, {"change": None, "changePercentage": None}, False),
    ({"price": 43.08, "change": None, "changePercentage": None}, {}, False),
    ({"price": 43.08, "change": 0.0, "changePercentage": 0.0}, {}, True),
    ({"price": 43.08, "change": 2.1, "changePercentage": 5.1}, {}, True),
    ({"price": 43.08}, {"change": 0.0}, True),
    ({"price": 43.08, "change": float("nan")}, {"change": None}, False),
])
def test_full_overview_marks_an_unknown_day_change(monkeypatch, quote, profile, known):
    resp = _full(monkeypatch, quote, profile)
    assert isinstance(resp, StockOverviewResponse)
    assert resp.change_known is known
    assert math.isfinite(resp.price_change) and math.isfinite(resp.price_change_percent)
    if not known:
        assert resp.price_change == 0.0 and resp.price_change_percent == 0.0


def test_core_and_full_builders_agree_on_the_flag(monkeypatch):
    """Same rule in both builders: a change the core call marks unknown must not come
    back 'known' when the full /overview supersedes it a moment later (the header would
    flip from '—' to a green +0.00)."""
    import inspect
    for fn in (StockOverviewService.get_overview_core, StockOverviewService._build_full_response):
        code = inspect.getsource(fn)
        assert "change_known = raw_change is not None or raw_pct is not None" in code, fn.__name__
        assert "change_known=change_known" in code, fn.__name__


# ── iOS: DTOs decode it, TickerCoreData gates on it (source-scan, brace-bound) ──

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_MODELS = _IOS / "Models/StockOverviewResponseModels.swift"


def _strip(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(path: Path, header: str) -> str:
    src = path.read_text()
    start = src.find(header)
    assert start != -1, f"{header!r} not found in {path.name} — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


@pytest.mark.parametrize("dto", ["struct StockOverviewResponseDTO: Decodable",
                                 "struct StockOverviewCoreResponseDTO: Decodable"])
def test_ios_stock_dtos_decode_change_known_as_optional(dto):
    body = _block(_MODELS, dto)
    assert "let changeKnown: Bool?" in body, f"{dto}: not decoded, or made non-optional (older payloads)"
    assert 'case changeKnown = "change_known"' in body, f"{dto}: CodingKey missing"


def test_ios_core_dto_threads_the_flag_into_ticker_core_data():
    body = _block(_MODELS, "struct StockOverviewCoreResponseDTO: Decodable")
    assert "changeKnown: changeKnown ?? true" in body, (
        "toCoreData() drops the flag — the fast-core header renders the placeholder as known"
    )


def test_ios_ticker_core_data_gates_sign_and_text_on_the_flag():
    body = _block(_MODELS, "struct TickerCoreData {")
    assert "var changeKnown: Bool = true" in body
    assert re.search(r"var isPositive: Bool \{ changeKnown && priceChange >= 0 \}", body), (
        "isPositive reads the 0.0 placeholder as a measured flat day"
    )
    change = _block(_MODELS, "var formattedChange: String {")
    assert 'guard changeKnown else { return "—" }' in change
    pct = _block(_MODELS, "var formattedChangePercent: String {")
    assert 'guard changeKnown else { return "" }' in pct


def test_ios_ticker_price_header_still_takes_the_flag():
    """The header this model feeds already has the neutral state (no arrow, no flash);
    `TickerCoreData.changeKnown` is what the screen must hand it."""
    body = _block(_IOS / "Views/Molecules/TickerPriceHeader.swift", "struct TickerPriceHeader: View")
    assert "var changeKnown: Bool = true" in body
    assert "guard changeKnown else { return AppColors.textSecondary }" in body
    assert "if changeKnown {" in body
