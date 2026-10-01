"""Round-3 regression (finding P18): Cay AI's Profit Power grounding line must not put a
held-back peer median under the company's latest fiscal year.

Profit Power flattens a THIN latest-year peer cell (n below the mature floor: the newest
fiscal year early in every reporting season, always for an off-calendar filer) to the
latest MATURE median at or before it (`hold_back_thin_benchmarks`). The response carries
only that value, not its year, yet `ChatService._format_profit_summary` labelled it
"(peer group, same year)" — and, for a revenue-gap latest year, "for FY{latest}" — so
Cay AI told users FY2025's median was "the industry's FY2026 average".

Both assertions on the old wording FAIL on the pre-fix code. Hermetic: pure formatting
over schema objects, no service, no network.
"""
from __future__ import annotations

import re

from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services.chat_service import ChatService
from app.services.sector_benchmark_lookup import MATURE_SAMPLE_FLOOR, hold_back_thin_benchmarks


def _held_back_latest_peer_pct() -> float:
    """The FY2026 peer value Profit Power actually serves when FY2026 is thin: FY2025's
    mature median, as a percentage (the chart's scale)."""
    flat = hold_back_thin_benchmarks({
        "net_margin": {
            "2025": {"value": 0.10, "n": MATURE_SAMPLE_FLOOR + 20},
            "2026": {"value": 0.30, "n": MATURE_SAMPLE_FLOOR - 14},
        }
    })
    value = flat["net_margin"]["2026"]
    assert value == 0.10, "fixture precondition: FY2026 must carry FY2025's median"
    return round(value * 100, 1)


def _claims_the_peer_figure_for(text: str, year: str) -> bool:
    """True when the line attributes the peer number to `year` (the defect's two forms)."""
    return ("same year" in text) or bool(
        re.search(rf"(?:avg|average|median) net margin[^.;]*\bfor FY{year}\b", text)
    )


def test_a_held_back_peer_median_is_not_called_the_latest_years():
    peer = _held_back_latest_peer_pct()
    data = ProfitPowerResponse(
        symbol="MSFT", quarterly=[], peer_group_level="industry",
        annual=[
            ProfitPowerDataPointSchema(period="2025", gross_margin=69.0, operating_margin=45.0,
                                       net_margin=36.0, fcf_margin=30.0,
                                       sector_average_net_margin=peer),
            ProfitPowerDataPointSchema(period="2026", gross_margin=69.5, operating_margin=45.5,
                                       net_margin=36.1, fcf_margin=31.0,
                                       sector_average_net_margin=peer),
        ],
    )
    text = ChatService._format_profit_summary("MSFT", data)
    assert not _claims_the_peer_figure_for(text, "2026"), text
    assert text == (
        "Latest annual margins for MSFT (FY2026): Gross 69.5%, Operating 45.5%, Net 36.1%, "
        "FCF 31.0%; Industry peer-group median net margin 10.0% (latest available peer "
        "reading; it may be from a year before FY2026)."
    )


def test_a_gap_latest_year_does_not_date_the_peer_figure_either():
    peer = _held_back_latest_peer_pct()
    data = ProfitPowerResponse(
        symbol="XBIO", quarterly=[], peer_group_level="industry",
        annual=[
            ProfitPowerDataPointSchema(period="2025", gross_margin=60.0, operating_margin=25.0,
                                       net_margin=20.0, sector_average_net_margin=peer),
            ProfitPowerDataPointSchema(period="2026", sector_average_net_margin=peer),
        ],
    )
    text = ChatService._format_profit_summary("XBIO", data)
    assert not _claims_the_peer_figure_for(text, "2026"), text
    assert "XBIO (FY2026): not available" in text
    assert "Most recent year with margins: FY2025: Gross 60.0%, Operating 25.0%, Net 20.0%" in text
    # Still named as a PEER figure, never bare, never the company's.
    assert text.endswith(
        " Industry peer-group median net margin: 10.0% (peers, not XBIO; latest available "
        "peer reading; it may be from a year before FY2026)."
    ), text
    assert "Industry avg net margin 10.0%" not in text


def test_no_peer_value_means_no_peer_sentence():
    data = ProfitPowerResponse(
        symbol="XBIO", quarterly=[],
        annual=[ProfitPowerDataPointSchema(period="2026", net_margin=5.0)],
    )
    text = ChatService._format_profit_summary("XBIO", data)
    assert text == "Latest annual margins for XBIO (FY2026): Net 5.0%."
    assert "peer" not in text.lower()


def test_the_unknown_level_keeps_the_neutral_sector_word():
    data = ProfitPowerResponse(
        symbol="XBIO", quarterly=[],
        annual=[ProfitPowerDataPointSchema(period="2026", net_margin=5.0,
                                           sector_average_net_margin=8.0)],
    )
    text = ChatService._format_profit_summary("XBIO", data)
    assert "Sector peer-group median net margin 8.0%" in text
    assert not _claims_the_peer_figure_for(text, "2026")
