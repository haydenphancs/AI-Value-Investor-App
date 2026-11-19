"""The report's Profitability drill-down gets the two-line legend (owner decision 2026-11-13,
the "Separate task later" after the Valuation / Health sheet in 9c52aba7).

THE BUG: `ProfitabilityChartSheet` said "Current" for the newest CLOSED period on the chart
(the header "Current: 41.20%" and the legend "Current 41.20% · Industry …"), paired the
company with its peer median only on that newest period, and painted the verdict with no
unrated-card gate. Since the TTM peer point left the drill-down lines (9c52aba7, "Replace
it"), the newest closed year of an off-calendar filer (ORCL May, MSFT June, AAPL September,
NVDA January) has no peer value, so ROE / ROA read "Company only".

THE FIX, pinned here:

A. each Profitability-card row carries its own comparison as numbers (`numeric_value`,
   `peer_median`, percentage points) under exactly the rule that prints the median, so the
   ratio agrees with the row's name to the digit; an "N/M", a "—" and a fiscal-year fallback
   (a closed year's figure, never the current one) carry none; payload version 7;
B. `_snapshot_to_card("Profitability", …)` maps them onto the rows as `current_value` /
   `current_peer_median` / `current_score`, and both caches carry them;
C. the iOS sheet: line 1 is the NEWEST period where company and peer line both have a value,
   named by its period ("FY2025", "Q4 '26"); line 2 is the card row's own TTM comparison,
   coloured by the row's score behind the unrated-card gate; "Current" is spelled once and
   never names a closed period; FCF Margin (no card row) draws line 1 only.

Profit Power margins: the four margin LINES come from `profit_power` (fiscal periods and
their per-period peer medians), so line 1 compares those. Line 2 can honestly show only what
the card scored — its TTM Gross / Operating / Net margin, ROE and ROA against the TTM median
`get_current_benchmarks` gave — so FCF Margin, which the card does not show, has no line 2.

Hermetic: every FMP, Profit Power and benchmark read is a local stub (the 2026-10-07
harness, imported, never copied). The iOS scans strip comments and are brace-bound to the
declaration they mean; each was mutation-tested once by hand (testing.md §3).
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.agents.ticker_report_data_collector as C
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import profitability_snapshot_service as ps
from app.services.ticker_data_cache import _deserialize, _serialize

# Reused harnesses (imported, never copied).
from test_drilldown_current_line_2026_11_13 import _current, _history_lookup, _validated
from test_ios_revenue_reconciliation import _decl_body, _strip_comments
from test_snapshot_cards_2026_10_07_profitability_health import (
    _BENCH,
    _RATIOS,
    _Supabase as _SnapshotCacheTable,
    _m,
    _pp,
    _prof,
)
from test_ticker_data_cache import _field_names, _sample


# ═══════════════════════════════════════════════════════════════════════════
# A. The Profitability-card rows carry their own comparison as numbers
# ═══════════════════════════════════════════════════════════════════════════

_RATIO_RE = re.compile(r"\((-?[\d.]+)x sector avg ")
_MEDIAN_RE = re.compile(r"sector avg (-?[\d.]+)%\)$")


def _assert_profitability_row(m: SnapshotMetricResponse) -> None:
    """Every row, whatever the inputs: `peer_median` exactly when the name prints a median,
    `peer_level` implies it, a placeholder has no `numeric_value`, nothing non-finite, and
    the numbers reproduce the label's median and "N.NNx" to the digit."""
    nv, pm = m.numeric_value, m.peer_median
    printed = _MEDIAN_RE.search(m.name)
    assert (pm is None) is (printed is None), m
    if m.peer_level is not None:
        assert m.peer_level in ("industry", "sector") and pm is not None, m
    if m.value in ("—", "N/M"):
        assert nv is None and pm is None, m
    for number in (nv, pm):
        assert number is None or (isinstance(number, float) and math.isfinite(number)), m
    if pm is not None:
        assert nv is not None, "a median is printed only beside a value"
        assert printed.group(1) == f"{pm:.1f}", m
        assert pm > 0, m
    if nv is not None:
        assert m.value == f"{nv:.2f}%", "the TTM figure the row prints"
    ratio = _RATIO_RE.search(m.name)
    # The card prints a multiple exactly for a non-negative value beside a median.
    assert (ratio is not None) is (pm is not None and nv >= 0), m
    if ratio is not None:
        assert ratio.group(1) == f"{nv / pm:.2f}", m


@pytest.mark.parametrize("value", [None, -9999.0, -12.5, -0.01, 0.0, 0.01, 30.0, 154.0, 99999.99])
@pytest.mark.parametrize("median", [
    None, 0.0, -0.3, 0.0004, 0.0005, 0.006, 0.15, 0.08, 2.5, float("nan"), float("inf"),
])
def test_row_numbers_follow_the_rule_that_prints_the_median(value, median):
    """`_row_numbers` against `_label_with_sector` over every value the compared branch can
    hand it (a finite percentage or None) and every median shape: 0, negative, NaN, inf and
    one that prints "0.0%" are no median."""
    got = ps._row_numbers(value, median)
    assert set(got) == {"numeric_value", "peer_median"}
    name = ps._label_with_sector("Net Margin", value, median)
    row = SnapshotMetricResponse(
        name=name, value=ps._fmt_pct(value), metric_key="net_margin", score=3, **got)
    _assert_profitability_row(row)
    assert (got["peer_median"] is not None) is (name != "Net Margin")
    assert got["numeric_value"] == value
    usable = ps._usable_median(median)
    if value is not None and usable is not None:
        assert got["peer_median"] == usable * 100, "unrounded, exactly as the label computes it"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, False])
def test_a_non_number_is_never_a_row_number(value):
    got = ps._row_numbers(value, 0.15)
    assert got == {"numeric_value": None, "peer_median": None}


@pytest.mark.asyncio
async def test_each_compared_row_carries_the_ttm_comparison_the_card_printed(monkeypatch):
    snap, _ = await _prof(monkeypatch)._compute_with_status("MCD")
    assert len(snap.metrics) == 5
    for m in snap.metrics:
        _assert_profitability_row(m)
    gross = _m(snap, "gross_margin")
    assert gross.name == "Gross Margin (1.43x sector avg 40.0%)" and gross.value == "57.00%"
    assert gross.numeric_value == 57.0
    assert gross.peer_median == pytest.approx(40.0, abs=1e-12)
    assert gross.peer_median == 0.40 * 100, "the unrounded median, as the label computes it"
    assert gross.peer_level == "industry" and gross.score == 4
    # Every compared row's numbers come from the TTM legs and the CURRENT medians.
    expected = {"gross_margin": (57.0, 0.40), "operating_margin": (46.0, 0.15),
                "net_margin": (32.0, 0.08), "roe": (30.0, 0.20), "roa": (14.0, 0.06)}
    for key, (value, median) in expected.items():
        row = _m(snap, key)
        assert row.numeric_value == value, key
        assert row.peer_median == median * 100, key
        assert row.peer_level == _BENCH[key][1], key


@pytest.mark.asyncio
async def test_a_loss_makers_negative_figure_stays_beside_the_median(monkeypatch):
    ratios = [dict(_RATIOS, operatingProfitMarginTTM=-0.083, netProfitMarginTTM=-0.125)]
    svc = _prof(monkeypatch, ratios=ratios,
                km=[{"returnOnEquityTTM": -0.144, "returnOnAssetsTTM": -0.05}])
    snap, _ = await svc._compute_with_status("MCD")
    net = _m(snap, "net_margin")
    assert net.name == "Net Margin (sector avg 8.0%)"
    assert (net.numeric_value, net.peer_median, net.score) == (-12.5, 0.08 * 100, 1)
    for m in snap.metrics:
        _assert_profitability_row(m)


@pytest.mark.asyncio
async def test_a_fiscal_year_fallback_carries_no_current_number(monkeypatch):
    """No usable TTM net margin: the card shows the latest FISCAL year's figure, unscored and
    uncompared. That is a closed year — the drill-down must not call it "Current (TTM)" — so
    the row carries no `numeric_value` (and no median: none is printed)."""
    ratios = [dict(_RATIOS, netProfitMarginTTM=None)]
    svc = _prof(monkeypatch, ratios=ratios,
                pp=_pp(gross_margin=55.0, operating_margin=44.0, net_margin=30.0))
    snap, _ = await svc._compute_with_status("MCD")
    net = _m(snap, "net_margin")
    assert net.value == "30.00%" and net.name == "Net Margin" and net.score is None
    assert (net.numeric_value, net.peer_median, net.peer_level) == (None, None, None)
    assert _m(snap, "gross_margin").numeric_value == 57.0, "the TTM rows keep theirs"


@pytest.mark.asyncio
async def test_an_nm_or_missing_row_carries_no_numbers(monkeypatch):
    # McDonald's-shaped negative equity: ROE "N/M". No TTM ROA at all: "—".
    svc = _prof(monkeypatch, ratios=[dict(_RATIOS, debtToEquityRatioTTM=-10.0)],
                km=[{"returnOnEquityTTM": -2.16}], bs=[{"totalStockholdersEquity": -4.0e9}])
    snap, _ = await svc._compute_with_status("MCD")
    roe, roa = _m(snap, "roe"), _m(snap, "roa")
    assert roe.value == "N/M" and roa.value == "—"
    for row in (roe, roa):
        assert (row.numeric_value, row.peer_median, row.peer_level) == (None, None, None)
    for m in snap.metrics:
        _assert_profitability_row(m)


@pytest.mark.asyncio
async def test_a_banks_excluded_gross_margin_carries_no_numbers(monkeypatch):
    profile = {"symbol": "JPM", "sector": "Financial Services",
               "industry": "Banks - Diversified", "mktCap": 6.0e11}
    snap, _ = await _prof(monkeypatch, profile=profile)._compute_with_status("JPM")
    gross = _m(snap, "gross_margin")
    assert gross.value == "N/M"
    assert (gross.numeric_value, gross.peer_median, gross.peer_level) == (None, None, None)
    for m in snap.metrics:
        _assert_profitability_row(m)


@pytest.mark.asyncio
async def test_no_benchmark_keeps_the_value_and_prints_no_median(monkeypatch):
    """An empty lookup (or a peer-free card): every row on absolute bands. The value stays
    — the drill-down header reads "Current (TTM): 57.00%" — but no row carries a median, so
    no line 2 is drawn."""
    snap, _ = await _prof(monkeypatch, bench={})._compute_with_status("MCD")
    for m in snap.metrics:
        _assert_profitability_row(m)
        assert m.peer_median is None and m.peer_level is None, m
    assert _m(snap, "gross_margin").numeric_value == 57.0
    assert _m(snap, "roe").numeric_value == 30.0


# ═══════════════════════════════════════════════════════════════════════════
# B. The card rows and both caches
# ═══════════════════════════════════════════════════════════════════════════

_PROFIT_KEYS = ("gross_margin", "operating_margin", "net_margin", "roe", "roa")


@pytest.mark.asyncio
async def test_the_report_card_rows_carry_the_current_comparison(monkeypatch):
    snap, _ = await _prof(monkeypatch)._compute_with_status("MCD")
    card = C._snapshot_to_card("Profitability", snap,
                               history_lookup=_history_lookup(*_PROFIT_KEYS),
                               peer_group_level="industry")
    by = {md["history_key"]: md for md in card["metrics"]}
    assert set(by) == set(_PROFIT_KEYS), "every card row resolves to its drill-down key"
    for key in _PROFIT_KEYS:
        row = _m(snap, key)
        assert _current(by[key]) == {"current_value": row.numeric_value,
                                     "current_peer_median": row.peer_median,
                                     "current_score": row.score}, key
    dumped = {md["history_key"]: md for md in _validated(card)["metrics"]}
    assert dumped["roe"]["current_peer_median"] == 0.20 * 100
    assert dumped["roe"]["peer_level"] == "sector"


@pytest.mark.asyncio
async def test_a_fallback_or_nm_card_row_draws_no_current_line(monkeypatch):
    svc = _prof(monkeypatch, ratios=[dict(_RATIOS, netProfitMarginTTM=None,
                                          debtToEquityRatioTTM=-10.0)],
                km=[{"returnOnEquityTTM": -2.16, "returnOnAssetsTTM": 0.14}],
                bs=[{"totalStockholdersEquity": -4.0e9}],
                pp=_pp(gross_margin=55.0, operating_margin=44.0, net_margin=30.0))
    snap, _ = await svc._compute_with_status("MCD")
    card = C._snapshot_to_card("Profitability", snap,
                               history_lookup=_history_lookup(*_PROFIT_KEYS))
    by = {md.get("history_key"): md for md in card["metrics"]}
    assert _current(by["net_margin"]) == {}, "a closed fiscal year is never 'Current (TTM)'"
    assert by["roe"]["value"] == "N/M" and _current(by["roe"]) == {}
    for md in card["metrics"]:
        if md["value"] in ("N/M", "—"):
            assert _current(md) == {}, md
    assert _current(by["roa"])["current_value"] == 14.0
    _validated(card)


def _numbered_profitability() -> SnapshotItemResponse:
    return SnapshotItemResponse(
        category="Profitability", rating=4, full_report_available=True, metrics=[
            SnapshotMetricResponse(name="Gross Margin (1.43x sector avg 40.0%)", value="57.00%",
                                   metric_key="gross_margin", score=4, peer_level="industry",
                                   numeric_value=57.0, peer_median=40.0),
            SnapshotMetricResponse(name="Return on Equity (ROE)", value="N/M", metric_key="roe"),
        ])


def test_the_collection_cache_round_trips_the_row_numbers():
    out = _sample()
    out.snap_profitability = _numbered_profitability()
    blob = _serialize(out)
    assert blob is not None
    back = _deserialize(json.loads(json.dumps(blob, allow_nan=False)), _field_names())
    assert back is not None
    assert [(m.numeric_value, m.peer_median) for m in back.snap_profitability.metrics] == [
        (57.0, 40.0), (None, None)]


def test_the_snapshot_cache_round_trips_the_row_numbers_and_rebuilds_v6():
    snap = _numbered_profitability()
    svc = ps.ProfitabilitySnapshotService.__new__(ps.ProfitabilitySnapshotService)
    svc.supabase = _SnapshotCacheTable()
    svc._upsert_supabase_cache("MCD", snap)
    (written,) = svc.supabase.upserts
    stored = json.loads(json.dumps(written["response_json"], allow_nan=False))
    assert stored[ps._VERSION_KEY] == ps._SNAPSHOT_PAYLOAD_VERSION == 7

    svc.supabase = _SnapshotCacheTable([
        {"response_json": dict(stored), "cached_at": written["cached_at"]}])
    got = svc._check_supabase_cache("MCD")
    assert got is not None
    assert [(m.numeric_value, m.peer_median) for m in got.metrics] == [
        (57.0, 40.0), (None, None)]

    # A v6 row (built before the numbers existed) is rebuilt, never served.
    older = {**stored, ps._VERSION_KEY: 6}
    svc.supabase = _SnapshotCacheTable([
        {"response_json": older, "cached_at": written["cached_at"]}])
    assert svc._check_supabase_cache("MCD") is None


# ═══════════════════════════════════════════════════════════════════════════
# C. The iOS sheet
# ═══════════════════════════════════════════════════════════════════════════

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SHEET = _IOS / "Views" / "Molecules" / "ProfitabilityChartSheet.swift"
_MODELS = _IOS / "Models" / "ProfitabilityChartModels.swift"


def _swift(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _sheet() -> str:
    return _decl_body(_swift(_SHEET), "struct ProfitabilityChartSheet: View")


def _flat(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def test_current_is_spelled_once_and_never_names_a_closed_period():
    """"Current" is the TTM figure's word alone, spelled in ONE place; the header says it
    only for the card row's carried TTM figure, and otherwise names the chart's newest
    period; line 1 is named by its period."""
    sheet = _sheet()
    prefix = _decl_body(sheet, "private var currentPrefix: String")
    assert re.fullmatch(r'\{\s*"Current \(TTM\)"\s*\}', prefix), prefix
    assert '"Current' not in sheet.replace(prefix, ""), "a second spelling of Current"
    assert not re.search(r'"[^"\n]*\bCurrent\b[^"\n]*"', sheet.replace(prefix, "")), (
        "Current inside some other string")
    header = _decl_body(sheet, "private var header: some View")
    assert "Text(headerText)" in header
    assert re.search(r'Text\("Last reported: \\\(pct\(v\)\) \(\\\(periodLabel\(lr\.period\)\)\)"\)',
                     header), header
    text = _flat(_decl_body(sheet, "private var headerText: String"))
    # The current word only behind the carried TTM figure, and as the card printed it.
    current = re.search(r'if let row = cardRow, row\.currentValue != nil \{ '
                        r'return "\\\(currentPrefix\): \\\(row\.value\)" \}', text)
    assert current, text
    assert text.count("currentPrefix") == 1, text
    # Otherwise the chart's newest period, by its period label — never the last reported.
    assert re.search(r"guard let p = latestPoint else", text) and "lastReported" not in text
    assert text.count("periodLabel(p.period)") == 2, text
    label = _decl_body(sheet, "private func periodLabel(_ label: String)")
    assert 'selectedPeriod == .annual ? "FY\\(label)" : label' in label
    for gone in ("currentText", "notReportedText", "not reported"):
        assert gone not in sheet, gone


def test_line_1_is_the_newest_period_where_both_have_a_value():
    sheet = _sheet()
    pair = _flat(_decl_body(sheet, "private var sectorPair"))
    assert "for p in current.reversed() {" in pair, pair
    assert "if let c = p.company, let s = p.sector { return (c, s, p.period) }" in pair, pair
    assert "latestPoint" not in pair, "the old latest-only pair (ROE 'Company only' on ORCL)"
    legend = _decl_body(sheet, "private var legendAndDelta: some View")
    flat = _flat(legend)
    assert "Text(deltaText(prefix: periodLabel(pair.period), company: pair.company, " \
           "sector: pair.sector))" in flat, flat
    # The pair's colour, then the company-only line under its own period.
    line1 = flat[flat.index("if let pair = sectorPair {"):flat.index("if let row = cardRow")]
    assert ".foregroundColor(deltaColor)" in line1
    assert re.search(r'else if let lr = lastReported, let v = lr\.company \{ '
                     r'Text\("\\\(periodLabel\(lr\.period\)\) \\\(pct\(v\)\) · Company only"\)',
                     line1), line1
    delta = _decl_body(sheet, "private func deltaText(prefix: String,")
    assert '"Current' not in delta and '"\\(prefix) \\(c) · \\(peer) \\(s) · ' in delta


def test_line_2_is_the_card_rows_own_comparison():
    """The row's carried value and median, coloured by the row's own score behind the
    unrated-card gate, drawn only when the report carries a median, and blind to the tab."""
    sheet = _sheet()
    text = _decl_body(sheet, "private func currentLineText(_ row: DeepDiveMetric)")
    assert "guard let median = row.currentPeerMedian else { return nil }" in text
    assert "row.currentValue" in text and "currentPrefix" in text and "row.value" in text
    assert re.search(r'String\(format: "%\.2f×", value / median\)', text), text
    color = _decl_body(sheet, "private func currentLineColor(_ row: DeepDiveMetric)")
    muted = re.search(r"guard row\.currentValue != nil else \{ return AppColors\.textMuted \}", color)
    gate = re.search(r"guard card\.starRating > 0, let score = row\.currentScore else "
                     r"\{ return AppColors\.textPrimary \}", color)
    assert muted and gate and muted.start() < gate.start(), color
    assert re.search(r"if score >= 4 \{ return AppColors\.gain \}", color), color
    assert re.search(r"if score <= 2 \{ return AppColors\.loss \}", color), color
    word = _decl_body(sheet, "private func currentPeerWord(_ row: DeepDiveMetric)")
    assert "(row.peerLevel ?? card.peerGroupLevel)" in word
    for body in (text, color, word):
        for tab_bound in (r"\bselectedPeriod\b", r"\bcurrent\b(?!\w)", r"\bpeerWord\b",
                          r"sectorPair", r"latestPoint", r"lastReported", r"row\.label",
                          r"\.sector\b", r"\.company\b"):
            assert not re.search(tab_bound, body), (tab_bound, body)
    legend = _flat(_decl_body(sheet, "private var legendAndDelta: some View"))
    assert "if let row = cardRow, let text = currentLineText(row) {" in legend
    assert ".foregroundColor(currentLineColor(row))" in legend
    assert legend.index("if let pair = sectorPair") < legend.index("currentLineText(row)")


def test_each_comparison_stays_on_one_row_as_on_the_valuation_sheet():
    """Owner, 2026-11-13 (on the Valuation / Health sheet, matched here: "Yes, match it"):
    "i don't want to make new line for the second line" — both comparison lines are
    single-line, shrinking no further than the 11pt data floor — and "stress out the line a
    bit to both side of the card": the legend card's side padding is `sm`."""
    sheet = _sheet()
    legend = _decl_body(sheet, "private var legendAndDelta: some View")
    for color in ("deltaColor", "currentLineColor(row)"):
        assert re.search(rf"\.foregroundColor\({re.escape(color)}\)\s*"
                         r"\.multilineTextAlignment\(\.center\)\s*\.lineLimit\(1\)\s*"
                         r"\.minimumScaleFactor\(Self\.legendLineMinScale\)\s*"
                         r"\.allowsTightening\(true\)", legend), color
    scale = re.search(r"static let legendLineMinScale: CGFloat = ([\d.]+)", sheet)
    assert scale and 11 / 14 <= float(scale.group(1)) < 1, "bodySmall is 14pt; the floor is 11pt"
    # The card's own padding (the LAST modifiers of the legend's VStack), not a chip's.
    tail = legend[legend.rindex(".padding(.vertical, AppSpacing.sm)"):]
    assert re.match(r"\.padding\(\.vertical, AppSpacing\.sm\)\s*"
                    r"\.padding\(\.horizontal, AppSpacing\.sm\)", tail), tail[:120]


def test_the_card_row_is_the_selected_metrics_own_row():
    sheet = _sheet()
    row = _flat(_decl_body(sheet, "private var cardRow: DeepDiveMetric?"))
    assert "guard let key = selectedMetric.cardHistoryKey else { return nil }" in row
    assert "return card.metrics.first(where: { $0.historyKey == key })" in row
    keys = _decl_body(_swift(_MODELS), "var cardHistoryKey: String?")
    arms = dict(re.findall(r"case \.(\w+): return (nil|\"\w+\")", keys))
    assert arms == {"grossMargin": '"gross_margin"', "operatingMargin": '"operating_margin"',
                    "netMargin": '"net_margin"', "fcfMargin": "nil", "roe": '"roe"',
                    "roa": '"roa"'}, arms
    # The keys are the backend's: each Profitability card row resolves to exactly one, and
    # the card has no FCF row.
    backend = {ps_key for _, ps_key in C._HISTORY_KEY_BY_LABEL_PREFIX}
    for value in arms.values():
        if value != "nil":
            assert value.strip('"') in backend, value
    labels = ["Gross Margin", "Operating Margin", "Net Margin", "Return on Equity (ROE)",
              "Return on Assets (ROA)"]
    assert [C._resolve_history_key(label) for label in labels] == list(_PROFIT_KEYS)
    assert sorted(v.strip('"') for v in arms.values() if v != "nil") == sorted(_PROFIT_KEYS)


def test_an_unrated_card_paints_no_verdict():
    sheet = _sheet()
    good = _flat(_decl_body(sheet, "private var isCurrentlyGood"))
    assert "guard card.starRating > 0, let pair = sectorPair else { return nil }" in good
    body = _decl_body(sheet, "var body: some View")
    assert re.search(r"ProfitabilityChartView\(points: current, "
                     r"higherIsBetter: card\.starRating > 0 \? true : nil\)", body), body


# ── a Python port of the legend's decisions, bound to the Swift above ──────────


def _port(points: List[Tuple[str, Optional[float], Optional[float]]], annual: bool = True,
          row: Optional[Dict[str, Any]] = None, rated: bool = True):
    """(header, line1, line2) for ``points`` = [(period, company, sector)], oldest first."""
    label = (lambda p: f"FY{p}") if annual else (lambda p: p)

    def pct(v: float) -> str:
        return f"{v:.0f}%" if abs(v) >= 100 else f"{v:.2f}%"

    latest = points[-1] if points else None
    if row and row.get("current_value") is not None:
        header = f"Current (TTM): {row['value']}"
    elif latest is None:
        header = "—"
    elif latest[1] is None:
        header = f"{label(latest[0])}: —"
    else:
        header = f"{label(latest[0])}: {pct(latest[1])}"
    pair = next(((c, s, p) for p, c, s in reversed(points) if c is not None and s is not None),
                None)
    if pair is not None:
        line1 = f"{label(pair[2])} {pct(pair[0])} · Industry {pct(pair[1])}"
    else:
        reported = next(((p, c) for p, c, _ in reversed(points) if c is not None), None)
        line1 = f"{label(reported[0])} {pct(reported[1])} · Company only" if reported else None
    line2 = None
    if row and row.get("current_peer_median") is not None:
        line2 = f"Current (TTM) {row['value']} · Industry {row['current_peer_median']:.2f}%"
    return header, line1, line2


def test_port_orcl_shaped_roe_compares_fy2025_not_company_only():
    """ORCL (fiscal year ends May): FY2026 has closed but no complete peer year exists yet.
    The old sheet read "Current 72.50% · Company only"; now line 1 is FY2025 vs FY2025 and
    line 2 the card's TTM figure."""
    roe = [("2023", 120.4, 17.5), ("2024", 159.4, 19.2), ("2025", 69.1, 20.1),
           ("2026", 72.5, None)]
    card_row = {"value": "68.40%", "current_value": 68.4, "current_peer_median": 19.83}
    header, line1, line2 = _port(roe, row=card_row)
    assert header == "Current (TTM): 68.40%"
    assert line1 == "FY2025 69.10% · Industry 20.10%"
    assert line2 == "Current (TTM) 68.40% · Industry 19.83%"
    # A report generated before 2026-11-13 (no carried numbers): the period, never "Current".
    header, line1, line2 = _port(roe, row={"value": "68.40%"})
    assert header == "FY2026: 72.50%" and line2 is None and "Current" not in line1


def test_port_trailing_revenue_gap_names_its_periods():
    biotech = [("2022", 15.0, 11.0), ("2023", 20.0, 12.0), ("2024", None, 12.0),
               ("2025", None, 12.5)]
    header, line1, line2 = _port(biotech)        # FCF Margin: no card row
    assert header == "FY2025: —"
    assert line1 == "FY2023 20.00% · Industry 12.00%", "the pair names its own year"
    assert line2 is None
    header, line1, _ = _port([("Q1 '26", 4.0, None), ("Q2 '26", 5.5, None)], annual=False)
    assert header == "Q2 '26: 5.50%" and line1 == "Q2 '26 5.50% · Company only"


def test_the_port_is_the_swifts_decision():
    """Bind the port's three choices to the Swift so the port cannot drift from it."""
    sheet = _sheet()
    assert "for p in current.reversed()" in _decl_body(sheet, "private var sectorPair")
    assert "row.currentValue != nil" in _decl_body(sheet, "private var headerText: String")
    assert "row.currentPeerMedian" in _decl_body(
        sheet, "private func currentLineText(_ row: DeepDiveMetric)")
    pct = _decl_body(sheet, "private func pct(_ v: Double)")
    assert re.search(r'abs\(v\) >= 100 \? String\(format: "%\.0f%%", v\) : '
                     r'String\(format: "%\.2f%%", v\)', pct), pct
