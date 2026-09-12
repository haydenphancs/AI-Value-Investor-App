"""Small, verified leftovers from the Railway deep check (2026-09-11), pinned so they stay fixed.

L1  `etf_service._get_fundamentals` no longer calls `get_dividend_history` — the endpoint is
    outside the FMP licence and the entitlement pre-flight refused it on every cold build
    (one WARNING per symbol per 12 h for a permanent condition). The slot stays `[]`.
L5  `chart_helper` reads `crypto_service._INTRADAY_RANGES` instead of a duplicate literal.
L6  `Z` / `LB` / `OJ` — roots `asset_class` classifies as commodities — are named in
    `commodity_service._WITHDRAWN_COMMODITIES`, so they raise the contractual
    FMP_NOT_ENTITLED instead of a retry-forever generic error.
L7  `price_alert_service` no longer imports the unused `detect_asset_class`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import app.services.chart_helper as ch
import app.services.commodity_service as cs
import app.services.crypto_service as crypto
import app.services.etf_service as etf
from app.integrations.fmp import FMPNotEntitledException
from app.services.asset_class import _COMMODITY_SYMBOLS

_APP = Path(__file__).resolve().parents[1] / "app"


def _calls_in(path: Path, attr: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


# ── L1 ──────────────────────────────────────────────────────────────────────────────


def test_etf_fundamentals_fan_out_does_not_call_the_unlicensed_dividend_feed():
    src = _APP / "services" / "etf_service.py"
    assert not _calls_in(src, "get_dividend_history"), \
        "etf_service calls get_dividend_history again — /dividends is outside the licence"
    # Anti-vacuity: the detector sees a real call elsewhere in the file.
    assert _calls_in(src, "get_etf_holders")


@pytest.mark.asyncio
async def test_etf_fundamentals_keep_the_five_slot_shape_with_an_empty_dividend_list(monkeypatch):
    class _FMP:
        async def get_company_profile(self, s):
            return {"symbol": s, "companyName": "SPDR S&P 500"}

        async def get_etf_info(self, s):
            return {"expenseRatio": 0.0945}

        async def get_etf_holders(self, s, limit=20):
            return []

        async def get_etf_sector_weightings(self, s):
            return []

        async def get_dividend_history(self, *a, **k):
            raise AssertionError("must not be called")

    svc = etf.ETFService.__new__(etf.ETFService)
    svc.fmp = _FMP()
    etf._cache.clear()
    monkeypatch.setattr(etf.ETFService, "_tier2_get", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(etf.ETFService, "_tier2_put", staticmethod(lambda *a, **k: None))
    bundle = await svc._get_fundamentals("SPY")
    assert bundle.get("dividends") == []
    assert bundle.get("profile", {}).get("companyName") == "SPDR S&P 500"


# ── L5 ──────────────────────────────────────────────────────────────────────────────


def test_chart_helper_reads_the_crypto_services_intraday_table():
    src = (_APP / "services" / "chart_helper.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*$", "", src, flags=re.M)
    assert '{"1D": 1, "1W": 7}' not in body, "the intraday table is duplicated again"
    assert "_INTRADAY_RANGES.get(range_code)" in body
    assert crypto._INTRADAY_RANGES == {"1D": 1, "1W": 7}


# ── L6 ──────────────────────────────────────────────────────────────────────────────


def test_every_commodity_root_asset_class_knows_is_served_or_withdrawn():
    served = set(cs._COMMODITY_PROFILES)
    withdrawn = set(cs._WITHDRAWN_COMMODITIES)
    roots = {s[:-3] for s in _COMMODITY_SYMBOLS}          # strip "USD"
    unhandled = roots - served - withdrawn
    assert not unhandled, f"commodity roots that fall through to a retry-forever generic error: {sorted(unhandled)}"
    assert {"LB", "OJ", "Z"} <= withdrawn


@pytest.mark.parametrize("sym", ["LBUSD", "OJUSD", "ZUSD", "lb"])
def test_withdrawn_roots_raise_the_contractual_error(sym):
    with pytest.raises(FMPNotEntitledException) as ei:
        cs._raise_if_withdrawn(sym)
    assert "no longer covered" in str(ei.value)


def test_served_roots_are_not_withdrawn():
    for sym in ("GCUSD", "SIUSD", "CLUSD", "NGUSD", "PLUSD", "PAUSD"):
        cs._raise_if_withdrawn(sym)   # must not raise


# ── L7 ──────────────────────────────────────────────────────────────────────────────


def test_price_alert_service_has_no_unused_asset_class_import():
    src = (_APP / "services" / "price_alert_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "detect_asset_class" not in imported or "detect_asset_class" in used
    assert "uses_coingecko_price" in imported and "uses_coingecko_price" in used
