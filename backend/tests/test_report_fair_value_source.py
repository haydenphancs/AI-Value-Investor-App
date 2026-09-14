"""The report's fair value comes from FMP's DCF model, or it is UNKNOWN — never the price.

The legacy v3 ``/profile`` carried ``dcf`` inline and the collector read it from there.
``/stable/profile`` does not, so after the FMP rebuild ``c["fair_value"]`` was None for every
ticker and the valuation vital's "no DCF" branches wrote ``fair_value = round(current_price, 2)``
— EVERY report's persisted ``fair_value_estimate`` was the live price (a prod AAPL report on
2026-09-12: 332.27 == 332.27, PDF hero "Margin of Safety +0.0% Fairly Valued" next to a bear
case saying "no margin of safety"). Now: a dedicated ``get_dcf`` fetch on the entitled
``discounted-cash-flow`` path, the legacy profile field as a back-compat read of old cached
rows, and None end to end when neither has a positive number.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations import fmp_entitlements
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
)


def _computed(out):
    svc = TickerReportDataCollector.__new__(TickerReportDataCollector)
    svc._compute_metrics(out)
    return out.computed or {}


def _out(*, dcf_row, profile_dcf=None, price=100.0):
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    # A `/stable/profile` shape: NO `dcf` key unless the legacy field is being simulated.
    out.profile = {"symbol": "AAPL", "companyName": "Apple Inc.", "price": price,
                   "marketCap": 4.8e12, "sector": "Technology"}
    if profile_dcf is not None:
        out.profile["dcf"] = profile_dcf
    out.quote = {"price": price, "previousClose": price}
    out.dcf = dcf_row
    return out


def test_the_dcf_row_is_the_fair_value_source():
    c = _computed(_out(dcf_row={"symbol": "AAPL", "date": "2026-09-11", "dcf": 150.25, "Stock Price": 100.0}))
    assert c["fair_value"] == 150.25
    assert c["upside_pct"] == 50.2  # rounded to one decimal by _safe_pct_change


def test_a_stable_profile_without_a_dcf_row_yields_no_fair_value():
    """The exact prod shape since the rebuild: stable profile, no dcf anywhere."""
    c = _computed(_out(dcf_row={}))
    assert c["fair_value"] is None
    assert c["upside_pct"] is None


@pytest.mark.parametrize("row", [
    {"dcf": 0}, {"dcf": -12.5}, {"dcf": None}, {"dcf": "n/a"}, {"dcf": float("nan")},
    {"dcf": float("inf")}, {"Stock Price": 100.0}, "not-a-dict", None, [],
])
def test_a_junk_or_non_positive_dcf_is_unknown_not_the_price(row):
    c = _computed(_out(dcf_row=row))
    assert c["fair_value"] is None, row


def test_the_legacy_profile_dcf_is_only_a_fallback():
    """Old cached collections (v3 era) still carry `profile.dcf`; the row wins when present."""
    assert _computed(_out(dcf_row={}, profile_dcf=120.0))["fair_value"] == 120.0
    assert _computed(_out(dcf_row={"dcf": 150.0}, profile_dcf=120.0))["fair_value"] == 150.0


def test_the_collector_fetches_the_dcf_as_a_first_pass_task():
    """A cached collection from before this field existed deserialises with the default
    (empty dict) and reads as unknown — never as the price."""
    import inspect
    src = inspect.getsource(TickerReportDataCollector)
    assert '("dcf", self.fmp.get_dcf(ticker), {})' in src
    assert CollectedTickerData(ticker="X", persona_key="p").dcf == {}


def test_discounted_cash_flow_is_an_entitled_path():
    assert fmp_entitlements.entitlement_error("discounted-cash-flow") is None


@pytest.mark.asyncio
async def test_get_dcf_returns_the_first_row_or_an_empty_dict():
    from app.integrations.fmp import FMPClient
    client = FMPClient.__new__(FMPClient)
    for payload, expected in [
        ([{"symbol": "AAPL", "dcf": 150.25}], {"symbol": "AAPL", "dcf": 150.25}),
        ([], {}), ({}, {}), (None, {}), (["junk"], {}),
    ]:
        client._make_request = AsyncMock(return_value=payload)
        assert await client.get_dcf("aapl") == expected
        client._make_request.assert_awaited_once_with(
            "discounted-cash-flow", params={"symbol": "AAPL"}
        )
