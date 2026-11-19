"""The report drill-down names each peer line's OWN group (2026-10-07).

A metric's peer line is one population (`get_benchmark_series`), and that population can
differ from the card-wide `peer_group_level` the drill-down legend used: a P/E line drawn
from the SECTOR (the industry's positive-P/E count is thin) under an "Industry Average"
legend. The collector now carries each line's level to the report
(`DeepDiveMetricResponse.sector_annual_level` / `sector_quarterly_level`, optional), and
iOS reads it before the card word. Hermetic: a fake lookup, no network.

2026-11-13 (owner, "Replace it"): the collector reads exactly two lines, annual and
calendar quarter, and no TTM cell. Every annual line ends at its last served complete
period; a TTM median never becomes a current-year point and never names a line's group.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.ticker_report import DeepDiveMetricResponse
from app.services.agents import ticker_report_data_collector as C
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE as CQ
from app.services.sector_benchmark_lookup import lookup_failed

def _cur_year() -> str:
    """The current calendar year, read when the TEST runs. Until 2026-11-13 the collector
    drew the TTM median as this year's annual peer point; it no longer does (owner decision
    2026-11-13, "Replace it"), so the tests assert this label never appears on a line. Read
    at call time, never at import: an import-time constant broke whenever a full-suite run
    crossed New Year between collection and execution."""
    return str(datetime.now(timezone.utc).year)


def _cell(value, level, n=40):
    return {"value": value, "level": level, "peer_group_name": level, "n": n}


class _Lookup:
    """Series cells per period type. It ALSO serves a TTM cell per metric — what the real
    lookup would answer if asked — and records every read, so a test proves the collector
    never asks for one (a TTM cell must neither become a point nor name a line's group)."""

    def __init__(self, series, ttm):
        self.series, self.ttm = series, ttm
        self.reads: list[tuple[str, str]] = []

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        self.reads.append(("get_benchmark_series", period_type))
        if period_type == "ttm":
            return {m: ({"TTM": dict(self.ttm[m])} if m in self.ttm else {}) for m in metrics}
        src = self.series.get(period_type, {})
        return {m: {k: dict(v) for k, v in src.get(m, {}).items()} for m in metrics}

    def get_benchmarks(self, industry, sector, metrics, period_type):
        self.reads.append(("get_benchmarks", period_type))
        if period_type == "ttm":
            return {m: ({"TTM": dict(self.ttm[m])} if m in self.ttm else {}) for m in metrics}
        src = self.series.get(period_type, {})
        return {m: {k: dict(v) for k, v in src.get(m, {}).items()} for m in metrics}


async def _history(monkeypatch, series, ttm):
    hist, _lookup = await _history_and_lookup(monkeypatch, series, ttm)
    return hist


async def _history_and_lookup(monkeypatch, series, ttm):
    lookup = _Lookup(series, ttm)
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup", lambda: lookup)
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    hist = await coll._fetch_sector_benchmark_history("Beverages - Non-Alcoholic", "Consumer Defensive")
    return hist, lookup


def _assert_two_line_reads_only(lookup):
    """Exactly the annual line and the calendar-quarter line, each through the one-group
    series reader; no TTM read on either reader, no per-period merge."""
    assert sorted(lookup.reads) == sorted(
        [("get_benchmark_series", "annual"), ("get_benchmark_series", CQ)]
    ), lookup.reads


@pytest.mark.asyncio
async def test_each_line_carries_its_own_group(monkeypatch):
    series = {
        "annual": {
            "gross_margin": {"2024": _cell(0.41, "industry"), "2025": _cell(0.42, "industry")},
            "pe_ratio": {"2024": _cell(20.0, "sector"), "2025": _cell(21.0, "sector")},
        },
        CQ: {"gross_margin": {"Q2'26": _cell(0.40, "sector")}},
    }
    hist = await _history(monkeypatch, series, ttm={})
    assert hist["levels"]["annual"]["gross_margin"] == "industry"
    assert hist["levels"]["annual"]["pe_ratio"] == "sector"
    assert hist["levels"]["quarterly"]["gross_margin"] == "sector"


@pytest.mark.asyncio
async def test_an_otherwise_empty_line_gets_no_point_and_no_level_from_a_ttm_cell(monkeypatch):
    """2026-11-13 ("Replace it"): a metric with no served annual or quarterly period draws
    NO peer line, even when a TTM median exists. It used to become a one-point line at the
    current year, labelled with the TTM cell's group."""
    hist, lookup = await _history_and_lookup(
        monkeypatch, {"annual": {}, CQ: {}}, ttm={"pe_ratio": _cell(20.56, "sector", n=140)})
    _assert_two_line_reads_only(lookup)
    assert not lookup_failed(hist), "an empty answer is not a failed read"
    for tag in ("annual", "quarterly"):
        assert hist[tag].get("pe_ratio", {}) == {}, (tag, hist[tag].get("pe_ratio"))
        assert hist["levels"][tag].get("pe_ratio") is None, (tag, hist["levels"][tag])
    assert not any(20.56 in line.values() for line in hist["annual"].values())


@pytest.mark.asyncio
async def test_a_line_ends_at_its_last_complete_year_and_keeps_its_own_group(monkeypatch):
    """2026-11-13: the annual line is exactly its served complete years. A mature TTM cell of
    the line's OWN group (the case the old rule drew as the current year's point) adds no
    point, and one of another group neither adds a point nor relabels the line."""
    series = {
        "annual": {
            "pe_ratio": {"2024": _cell(22.0, "industry"), "2025": _cell(24.0, "industry")},
            "gross_margin": {"2024": _cell(0.41, "industry"), "2025": _cell(0.42, "industry")},
        },
        CQ: {"pe_ratio": {"Q2'26": _cell(23.0, "industry")}},
    }
    ttm = {
        "pe_ratio": _cell(20.56, "industry", n=140),      # same group as the line
        "gross_margin": _cell(0.39, "sector", n=140),     # another group
    }
    hist, lookup = await _history_and_lookup(monkeypatch, series, ttm=ttm)
    _assert_two_line_reads_only(lookup)
    assert not lookup_failed(hist)
    assert hist["annual"]["pe_ratio"] == {"2024": 22.0, "2025": 24.0}
    assert hist["annual"]["gross_margin"] == {"2024": 0.41, "2025": 0.42}
    for metric in ("pe_ratio", "gross_margin"):
        assert _cur_year() not in hist["annual"][metric]
        assert hist["levels"]["annual"][metric] == "industry"
    # The quarterly line is its own read, untouched by the TTM cell too.
    assert hist["quarterly"]["pe_ratio"] == {"Q2'26": 23.0}
    assert hist["levels"]["quarterly"]["pe_ratio"] == "industry"


@pytest.mark.asyncio
async def test_a_line_whose_cells_name_two_groups_names_none(monkeypatch):
    """One peer group per line: cells from two populations give the line no level (the
    legend then falls back to the card word), and no TTM cell is read to break the tie."""
    series = {"annual": {"pe_ratio": {"2024": _cell(22.0, "sector"), "2025": _cell(24.0, "industry")}},
              CQ: {}}
    hist, lookup = await _history_and_lookup(
        monkeypatch, series, ttm={"pe_ratio": _cell(20.56, "industry", n=140)})
    _assert_two_line_reads_only(lookup)
    assert hist["annual"]["pe_ratio"] == {"2024": 22.0, "2025": 24.0}
    assert hist["levels"]["annual"]["pe_ratio"] is None


def test_the_card_metric_carries_the_line_levels():
    snap = type("S", (), {"rating": 4, "metrics": [
        type("M", (), {"name": "Gross Margin (1.1x sector avg 41.0%)", "value": "45%",
                       "metric_key": "gross_margin", "score": 4})(),
    ]})()
    history = {"gross_margin": {
        "unit": "percent", "annual": [{"period": "2025", "value": 45.0}], "quarterly": [],
        "sector_annual": [{"period": "2025", "value": 41.0}], "sector_annual_level": "industry",
    }}
    card = C._snapshot_to_card("Profitability", snap, history_lookup=history, peer_group_level="sector")
    md = card["metrics"][0]
    assert md["sector_annual_level"] == "industry"
    assert "sector_quarterly_level" not in md
    assert card["peer_group_level"] == "sector"          # the card word is unchanged
    assert DeepDiveMetricResponse.model_validate(md).sector_annual_level == "industry"


def test_an_old_report_metric_without_the_fields_still_validates():
    m = DeepDiveMetricResponse.model_validate({"label": "P/E", "value": "20"})
    assert m.sector_annual_level is None and m.sector_quarterly_level is None


# ── iOS half: source scan (comments stripped, scoped to one declaration) ─────────

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _src(rel: str) -> str:
    out = []
    for line in (_IOS / rel).read_text().splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    start = src.index(header)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def test_the_report_dto_decodes_both_levels_optionally():
    dto = _block(_src("Models/TickerReportResponse.swift"), "struct DeepDiveMetricDTO")
    assert "let sectorAnnualLevel: String?" in dto
    assert "let sectorQuarterlyLevel: String?" in dto
    assert 'case sectorAnnualLevel = "sector_annual_level"' in dto
    assert 'case sectorQuarterlyLevel = "sector_quarterly_level"' in dto
    mapper = _src("Models/TickerReportResponse.swift")
    assert "sectorAnnualLevel: m.sectorAnnualLevel" in mapper
    assert "sectorQuarterlyLevel: m.sectorQuarterlyLevel" in mapper


def test_the_drill_down_legend_reads_the_lines_own_level_first():
    sheet = _src("Views/Molecules/FundamentalsHistorySheet.swift")
    word = _block(sheet, "private func peerWord(_ m: DeepDiveMetric) -> String")
    assert "period == .annual ? m.sectorAnnualLevel : m.sectorQuarterlyLevel" in word
    assert "(own ?? card.peerGroupLevel)" in word
    assert "private var peerWord: String" not in sheet, "the card-wide word must not come back"
    assert 'Text("\\(peerWord(m)) Average")' in sheet


def test_the_profitability_sheet_reads_the_selected_tabs_line_level():
    """2026-10-08 (RPT3-4 / PP-LEVEL-2): the backend picks each metric's ANNUAL and
    QUARTERLY line separately, so the sheet's word follows the tab on screen. It used to pin
    the period-blind `roe.sectorAnnualLevel ?? roe.sectorQuarterlyLevel`, under which the
    Quarterly tab named the annual line's group over a sector median. Ported and run in
    tests/test_pp_levels_round4_ios.py; this pins the shape next to its sibling sheet."""
    sheet = _src("Views/Molecules/ProfitabilityChartSheet.swift")
    word = re.sub(r"\s+", " ", _block(sheet, "private var peerWord: String"))
    assert "selectedPeriod == .annual ? line?.annualPeerLevel : line?.quarterlyPeerLevel" in word
    assert "own ?? line?.peerLevel ?? card.peerGroupLevel" in word
    # The period-blind pick (either level for both tabs) must not come back anywhere.
    assert not re.search(r"sectorAnnualLevel\s*\?\?\s*\w+\.sectorQuarterlyLevel", sheet)
    assert not re.search(r"sectorQuarterlyLevel\s*\?\?\s*\w+\.sectorAnnualLevel", sheet)
    series = _src("Models/ProfitabilityChartModels.swift")
    roe = re.sub(r"\s+", " ", _block(series, "func toProfitabilitySeries("))
    assert "annualPeerLevel: sectorAnnualLevel" in roe
    assert "quarterlyPeerLevel: sectorQuarterlyLevel" in roe


def test_an_undrawn_peer_line_is_never_named_in_the_sheet_or_the_tooltip():
    """2026-10-08: the report sheet's dashed-legend entry and the live card's tooltip row
    appear only when the line on screen has a peer value (an undrawn v7 line has no level,
    and a fallback word would name a line that is not there)."""
    def _body_after(src: str, gate: str) -> str:
        # Brace-match from the gate's OWN opening brace (its last character), not from a
        # closure brace inside the condition.
        i = src.index(gate) + len(gate) - 1
        depth, j = 0, i
        while True:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            if depth == 0:
                return src[i:j + 1]
            j += 1

    sheet = _src("Views/Molecules/ProfitabilityChartSheet.swift")
    gate = "if current.contains(where: { $0.sector != nil }) {"
    assert gate in sheet
    assert 'Text("\\(peerWord) Average")' in _body_after(sheet, gate)
    assert sheet.count('Text("\\(peerWord) Average")') == 1, "no ungated copy of the entry"
    tooltip = _src("Views/Molecules/ProfitPowerChartView.swift")
    row_gate = "if dataPoint.sectorAverageNetMargin != nil {"
    assert row_gate in tooltip
    assert 'title: "\\(peerWord) Avg"' in _body_after(tooltip, row_gate)
    assert tooltip.count('title: "\\(peerWord) Avg"') == 1, "no ungated copy of the row"
