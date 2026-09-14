"""`change_known` on the crypto, commodity and ETF headers — the index pattern, everywhere.

Only the index core received the three-state flag in the FMP rebuild. The other three
classes still did `change = ... or 0`, so a CoinGecko `price_change_24h: null` (a coin with
<24 h of history, a data gap), a FRED series with one observation, or an ETF profile row
without a change rendered as a green "+$0.00 (+0.00%)" with the dashed baseline drawn on
the live price. The flag is `is not None`, never truthiness: a genuine 0.0 move stays known.
"""
import inspect
import re
from pathlib import Path

import pytest

from app.schemas.commodity import (
    CommodityCoreResponse, CommodityDetailResponse, CommodityQuoteResponse,
)
from app.schemas.crypto import CryptoCoreResponse, CryptoDetailResponse
from app.schemas.etf import ETFCoreResponse, ETFDetailResponse, ETFQuoteResponse
from app.services import commodity_service as cms
from app.services import crypto_service as crs
from app.services import etf_service as ets


@pytest.mark.parametrize("model", [
    CryptoCoreResponse, CryptoDetailResponse, CommodityCoreResponse, CommodityDetailResponse,
    CommodityQuoteResponse, ETFCoreResponse, ETFDetailResponse, ETFQuoteResponse,
])
def test_change_known_defaults_true_for_shipped_builds(model):
    assert model.model_fields["change_known"].default is True


# ── crypto core: runtime ────────────────────────────────────────────────────────────


def _crypto_svc(market_data):
    svc = crs.CryptoService.__new__(crs.CryptoService)

    async def _fund(symbol):
        return {"name": "Testcoin", "market_data": market_data}
    svc._get_coin_fundamentals = _fund
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("md, known", [
    ({"current_price": {"usd": 10.0}, "price_change_24h": None, "price_change_percentage_24h": None}, False),
    ({"current_price": {"usd": 10.0}}, False),
    ({"current_price": {"usd": 10.0}, "price_change_24h": float("nan"), "price_change_percentage_24h": float("inf")}, False),
    ({"current_price": {"usd": 10.0}, "price_change_24h": 0.0, "price_change_percentage_24h": 0.0}, True),
    ({"current_price": {"usd": 10.0}, "price_change_24h": -0.5, "price_change_percentage_24h": -4.76}, True),
    ({"current_price": {"usd": 10.0}, "price_change_percentage_24h": 1.5}, True),
])
async def test_crypto_core_marks_an_unknown_24h_change(md, known):
    out = await _crypto_svc(md).get_crypto_core("TSTUSD")
    assert out.change_known is known
    if not known:
        assert out.price_change == 0 and out.price_change_percent == 0
    assert out.current_price == 10.0


# ── commodity + ETF core: runtime ───────────────────────────────────────────────────


def _commodity_svc(quote):
    svc = cms.CommodityService.__new__(cms.CommodityService)

    async def _q(sym): return dict(quote)
    async def _c(*a, **k): return []
    svc._get_quote = _q
    svc._get_chart = _c
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("quote, known", [
    ({"price": 398.77}, False),                                        # FRED single observation
    ({"price": 398.77, "change": None, "changePercentage": None}, False),
    ({"price": 398.77, "change": 0.0, "changePercentage": 0.0}, True),
    ({"price": 398.77, "change": 2.41, "changesPercentage": 0.61}, True),
    ({"price": 398.77, "changePercentage": 0.0}, True),               # explicit flat pct only
])
async def test_commodity_core_marks_an_unknown_day_change(quote, known):
    out = await _commodity_svc(quote).get_commodity_core("GCUSD")
    assert out.change_known is known
    if not known:
        assert out.price_change == 0 and out.price_change_percent == 0


def _etf_svc(quote):
    svc = ets.ETFService.__new__(ets.ETFService)

    async def _q(sym): return dict(quote)
    async def _c(*a, **k): return []
    svc._get_quote = _q
    svc._get_chart = _c
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("quote, known", [
    ({"price": 764.29, "name": "SPY"}, False),
    ({"price": 764.29, "change": None, "changePercentage": None}, False),
    ({"price": 764.29, "change": 0.0, "changePercentage": 0.0}, True),
    ({"price": 764.29, "change": 6.46, "changesPercentage": 0.85}, True),
    ({"price": 764.29, "change": float("nan"), "changePercentage": float("nan")}, False),
])
async def test_etf_core_marks_an_unknown_day_change(quote, known):
    out = await _etf_svc(quote).get_etf_core("SPY")
    assert out.change_known is known
    if not known:
        assert out.price_change == 0 and out.price_change_percent == 0


def test_the_detail_and_quote_builders_carry_the_flag():
    """The full detail and the 30 s quote projections must not silently drop back to
    'known' — a refresh would flip an honest '—' into a green +0.00%."""
    code = inspect.getsource(crs.CryptoService.get_crypto_detail)
    assert "change_known=change_known" in code
    code = inspect.getsource(cms.CommodityService)
    assert code.count("change_known=change_known") >= 2 and "change_known=full.change_known" in code
    code = inspect.getsource(ets.ETFService)
    assert code.count("change_known=change_known") >= 2 and "change_known=full.change_known" in code


# ── iOS: DTOs decode it, models gate on it (source-scan, brace-bound, comments stripped) ──

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(path: Path, header: str) -> str:
    src = _strip(path.read_text())
    start = src.find(header)
    assert start != -1, f"{header!r} not found in {path.name}"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces")


@pytest.mark.parametrize("file, dto", [
    ("Models/CryptoAPIModels.swift", "struct CryptoDetailResponse: Decodable"),
    ("Models/CryptoAPIModels.swift", "struct CryptoCoreResponseDTO: Decodable"),
    ("Models/CommodityDetailResponseModels.swift", "struct CommodityDetailResponseDTO: Decodable"),
    ("Models/CommodityDetailResponseModels.swift", "struct CommodityCoreResponseDTO: Decodable"),
    ("Models/CommodityDetailResponseModels.swift", "struct CommodityQuoteResponseDTO: Decodable"),
    ("Models/ETFDetailResponseModels.swift", "struct ETFQuoteResponseDTO: Decodable"),
    ("Models/ETFDetailResponseModels.swift", "struct ETFCoreResponseDTO: Decodable"),
])
def test_ios_dtos_decode_change_known_as_optional(file, dto):
    block = _block(_IOS / file, dto)
    assert "let changeKnown: Bool?" in block, dto
    assert 'case changeKnown = "change_known"' in block, dto


@pytest.mark.parametrize("file, model", [
    ("Models/CryptoAPIModels.swift", "struct CryptoCoreData"),
    ("Models/CryptoDetailModels.swift", "struct CryptoDetailData"),
    ("Models/CommodityDetailResponseModels.swift", "struct CommodityCoreData"),
    ("Models/CommodityDetailModels.swift", "struct CommodityDetailData"),
    ("Models/ETFDetailResponseModels.swift", "struct ETFCoreData"),
    ("Models/ETFDetailModels.swift", "struct ETFDetailData"),
])
def test_ios_models_gate_sign_and_change_on_the_flag(file, model):
    block = _block(_IOS / file, model)
    assert "var changeKnown: Bool = true" in block, model
    assert "changeKnown && priceChange >= 0" in block, model
    assert re.search(r'changeKnown \? \w+HeaderFormat\.change\(priceChange\) : "—"', block), model


@pytest.mark.parametrize("file, proto", [
    ("Models/CryptoAPIModels.swift", "extension CryptoHeaderRenderable"),
    ("Models/CommodityDetailResponseModels.swift", "extension CommodityHeaderRenderable"),
    ("Models/ETFDetailResponseModels.swift", "extension ETFHeaderRenderable"),
])
def test_ios_chart_baseline_is_hidden_when_unknown(file, proto):
    block = _block(_IOS / file, proto)
    assert "changeKnown ? previousClose : nil" in block


@pytest.mark.parametrize("screen, model_var", [
    ("Views/Screens/CryptoDetailView.swift", "cryptoData"),
    ("Views/Screens/CommodityDetailView.swift", "commodityData"),
    ("Views/Screens/ETFDetailView.swift", "etfData"),
])
def test_ios_screens_hand_the_flag_to_the_header_and_the_chart(screen, model_var):
    src = _strip((_IOS / screen).read_text())
    assert f"changeKnown: {model_var}.changeKnown" in src
    assert f"isPositive: {model_var}.chartIsPositive" in src
    assert f"previousClose: {model_var}.chartPreviousClose" in src
    assert f"previousClose: {model_var}.previousClose" not in src


@pytest.mark.parametrize("header", [
    "Views/Molecules/CryptoPriceHeader.swift", "Views/Molecules/CommodityPriceHeader.swift",
])
def test_ios_headers_render_neutral_with_no_arrow_when_unknown(header):
    src = _strip((_IOS / header).read_text())
    assert "guard changeKnown else { return AppColors.textSecondary }" in src
    assert re.search(r"if changeKnown \{\s*Image\(systemName: arrowIcon\)", src)
