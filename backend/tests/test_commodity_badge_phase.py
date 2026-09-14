"""An ETF-backed commodity screen badges the FUND's session phase, not a two-state open/closed.

`_commodity_market_status` answered "Market Open" for the whole 04:00–20:00 ET span, so at
07:00 the header said the market was open over a price that was the 16:00 profile print —
while the Trading Hours card on the same screen said "9:30 AM – 4:00 PM ET" and the ETF's
own screen said "Pre-Market". The client already maps the phase strings.
"""
import pytest

from app.services import commodity_service as cms
from app.utils import market_hours as mh


@pytest.mark.parametrize("phase, expected", [
    (mh.SESSION_PREMARKET, "Pre-Market"),
    (mh.SESSION_REGULAR, "Market Open"),
    (mh.SESSION_AFTERHOURS, "After-Hours"),
    (mh.SESSION_CLOSED, "Market Closed"),
    ("something-new", "Market Closed"),          # an unknown phase never claims liveness
])
def test_etf_backed_commodity_badge_follows_the_equity_phase(monkeypatch, phase, expected):
    monkeypatch.setattr(mh, "session_phase", lambda now=None: phase)
    monkeypatch.setattr(cms, "_source_of", lambda s: cms._COMMODITY_SOURCE_ETF)
    monkeypatch.setattr(cms, "_get_meta", lambda s: {"name": "Gold"})
    assert cms._commodity_market_status("GCUSD") == expected


def test_a_fred_backed_commodity_is_always_closed(monkeypatch):
    monkeypatch.setattr(mh, "session_phase", lambda now=None: mh.SESSION_REGULAR)
    monkeypatch.setattr(cms, "_source_of", lambda s: cms._COMMODITY_SOURCE_FRED)
    assert cms._commodity_market_status("CLUSD") == "Market Closed"
