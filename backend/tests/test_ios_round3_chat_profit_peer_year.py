"""Cay AI's Profit Power grounding line names the peer figure honestly (findings P18,
2026-09-30, and the 2026-10-07 benchmark rework).

History: "(peer group, same year)" put a held-back FY2025 median under FY2026 (P18), so
the line then said "latest available peer reading; it may be from a year before FY2026".
Since 2026-10-07 each period carries its OWN peer median and a period that is not fully
reported carries none (`sector_benchmark_lookup.merge_peer_cells` /
`servable_benchmark_rows`), so the figure is the peers' median for the same period, and
the line says so. It is still never written as "the industry's FY2026 average" — the
company's fiscal-year label and the peers' period are matched by period end, not by name.

Hermetic: pure formatting over schema objects, no service, no network.
"""
from __future__ import annotations

import re

from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services.chat_service import ChatService


def _claims_the_peer_figure_for(text: str, year: str) -> bool:
    """True when the line attributes the peer number to `year` (the defect's two forms)."""
    return ("same year" in text) or bool(
        re.search(rf"(?:avg|average|median) net margin[^.;]*\bfor FY{year}\b", text)
    )


def test_the_peer_median_is_named_as_the_same_periods_peer_figure():
    peer = 10.0
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
        "FCF 31.0%; Industry peer-group median net margin 10.0% (peers' median for the "
        "same period)."
    )


def test_a_gap_latest_year_does_not_date_the_peer_figure_either():
    peer = 10.0
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
        " Industry peer-group median net margin: 10.0% (peers, not XBIO; peers' median for "
        "the FY2026 period)."
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
