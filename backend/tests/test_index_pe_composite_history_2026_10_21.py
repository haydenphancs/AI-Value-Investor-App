"""The index P/E is ONE US-market composite, compared only with its OWN history.

Found by the read-only benchmark check of 2026-10-20: `_compute_index_pe_from_sectors`
returns one symbol-independent figure (the mean of the sector TTM P/E medians, 21.17 on
2026-10-18), and each index screen set it beside that INDEX's hard-coded 10-year average
(21x / 25x / 18x). The same number therefore read "a premium" on the S&P 500 and the Dow
and "below the 10-year average — suggesting potential value" on the Nasdaq, and chat's
`get_market_overview` handed the model the same mismatched pairs.

Owner decision 2026-10-21 ("Composite vs own history"): every screen and chat names the
figure the US market composite and compares it only with the SAME composite's own 10-year
average, built from the stored ANNUAL sector medians (22.14x for 2016-2025). Within ±5% the
sentence says "in line with", else "above" / "below"; no verdict words; fewer than 8 usable
years → no comparison. The valuation badge stays.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.index_service as idx
from app.schemas.chat import MARKET_PE_BASIS, MarketOverviewWidget
from app.schemas.index import IndexSnapshotsDataResponse, ValuationSnapshotResponse

# The per-year composite means the read-only prod probe found on 2026-10-21.
_PROD_YEARS = {
    2010: 18.49, 2011: 17.08, 2012: 18.07, 2013: 20.81, 2014: 21.59, 2015: 21.59,
    2016: 23.44, 2017: 23.21, 2018: 19.42, 2019: 24.23, 2020: 25.15, 2021: 22.54,
    2022: 18.43, 2023: 21.36, 2024: 22.09, 2025: 21.53,
}
_NOW = idx.datetime(2026, 10, 21, 3, 0, tzinfo=idx.timezone.utc)
_SECTORS = [f"Sector{i}" for i in range(11)]


def _row(sector, value, label, *, period_type="annual", n=40, computed="2026-10-21T02:00:00+00:00",
         industry=""):
    return {"sector": sector, "industry": industry, "metric_name": "pe_ratio",
            "period_type": period_type, "period_label": label, "median_value": value,
            "sample_size": n, "computed_at": computed}


def _year(year, value, *, sectors=_SECTORS, **kw):
    """Every sector at `value`, so the year's composite IS `value`."""
    return [_row(s, value, str(year), **kw) for s in sectors]


def _prod_annual():
    return [r for year, v in _PROD_YEARS.items() for r in _year(year, v)]


def _ttm(value=21.17, computed="2026-10-18T05:00:00+00:00"):
    return [_row(s, value, "TTM", period_type="ttm", computed=computed) for s in _SECTORS]


def _mean(values):
    return round(sum(values) / len(values), 2)


# ── 1. the history itself (pure) ───────────────────────────────────────────────────


def test_the_history_is_the_newest_ten_usable_years_of_the_same_composite():
    h = idx._index_pe_history(_prod_annual())
    assert h == {"avg": 22.14, "years": 10, "first_year": 2016, "last_year": 2025}


def test_a_year_with_too_few_mature_sectors_is_not_a_year():
    """2025 with 7 mature sectors (4 thin) is not usable, so the window is 2015-2024 —
    never a "2025" composite averaged from a handful of sectors."""
    rows = [r for r in _prod_annual() if r["period_label"] != "2025"]
    rows += _year(2025, 99.0, sectors=_SECTORS[:7])
    rows += _year(2025, 99.0, sectors=_SECTORS[7:], n=5)
    h = idx._index_pe_history(rows)
    assert h["last_year"] == 2024 and h["first_year"] == 2015
    assert h["avg"] == _mean([_PROD_YEARS[y] for y in range(2015, 2025)])


def test_gaps_inside_the_window_shrink_it_and_never_reach_further_back():
    rows = [r for r in _prod_annual() if r["period_label"] not in ("2018", "2019")]
    h = idx._index_pe_history(rows)
    assert h["years"] == 8 and h["first_year"] == 2016 and h["last_year"] == 2025
    assert h["avg"] == _mean([_PROD_YEARS[y] for y in range(2016, 2026) if y not in (2018, 2019)])


@pytest.mark.parametrize("usable, expect_none", [(7, True), (8, False)])
def test_fewer_than_eight_usable_years_is_no_comparison(usable, expect_none):
    rows = [r for y in range(2025 - usable + 1, 2026) for r in _year(y, 20.0)]
    h = idx._index_pe_history(rows)
    assert (h is None) is expect_none


@pytest.mark.parametrize("rows", [[], [_row("S", 20.0, "2025")]])
def test_an_empty_or_thin_table_is_none(rows):
    assert idx._index_pe_history(rows) is None


def test_labels_that_are_not_years_are_skipped():
    junk = []
    for label in ("TTM", "Q1'25", "FY2025", "2025A", "20255", None, 2024, ""):
        junk += [_row(s, 400.0, label) for s in _SECTORS]
    h = idx._index_pe_history(_prod_annual() + junk)
    assert h["avg"] == 22.14


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0, 0, True, "abc", None])
def test_a_malformed_median_does_not_count_as_a_sector(bad):
    """Four malformed sectors leave 7 → the newest year is not usable."""
    rows = [r for r in _prod_annual() if r["period_label"] != "2025"]
    rows += _year(2025, 99.0, sectors=_SECTORS[:7])
    rows += [_row(s, bad, "2025") for s in _SECTORS[7:]]
    assert idx._index_pe_history(rows)["last_year"] == 2024


def test_a_duplicate_sector_row_is_resolved_to_the_newest_computed():
    rows = [r for r in _prod_annual() if r["period_label"] != "2025"]
    rows += _year(2025, 21.53)
    rows += _year(2025, 400.0, computed="2026-01-01T00:00:00+00:00")   # older duplicate
    assert idx._index_pe_history(rows)["avg"] == 22.14


# ── 2. the basis read ───────────────────────────────────────────────────────────────


class _Stub:
    """Chainable PostgREST stub: records filters, serves ordered pages."""

    def __init__(self, rows):
        self.rows, self.eq_calls, self.in_calls, self.orders = rows, [], [], []

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    def in_(self, col, vals):
        self.in_calls.append((col, list(vals)))
        return self

    def order(self, col, desc=False):
        self.orders.append(col)
        return self

    def range(self, a, b):
        self._slice = (a, b)
        return self

    def execute(self):
        rows = [r for r in self.rows if all(r.get(c) == v for c, v in self.eq_calls)]
        rows = [r for r in rows if all(r.get(c) in vs for c, vs in self.in_calls)]
        rows = sorted(rows, key=lambda r: r["id"])
        a, b = self._slice
        return SimpleNamespace(data=rows[a:b + 1])


def _stub(monkeypatch, rows):
    for n, r in enumerate(rows):
        r["id"] = n
    stub = _Stub(rows)
    monkeypatch.setattr(idx, "get_supabase", lambda: stub)
    return stub


def test_the_basis_reads_both_period_types_in_one_paged_ordered_read(monkeypatch):
    industry = [_row(s, 400.0, "2025", industry="Some Industry") for s in _SECTORS]
    stub = _stub(monkeypatch, _ttm() + _prod_annual() + industry)
    basis = idx._compute_index_pe_basis(now=_NOW)
    assert basis == {"sectors": 11,
                     "history": {"avg": 22.14, "years": 10, "first_year": 2016, "last_year": 2025}}
    assert ("industry", "") in stub.eq_calls and ("metric_name", "pe_ratio") in stub.eq_calls
    assert stub.in_calls == [("period_type", ["ttm", "annual"])]
    assert stub.orders and set(stub.orders) == {"id"}


def test_an_incomplete_annual_year_is_held_back_by_the_serving_gate(monkeypatch):
    """FY2026 rows computed 2026-10-04 belong to a year that has not ended: a full set of
    mature sectors at 40x must not become the newest year and shift the window."""
    _stub(monkeypatch, _ttm() + _prod_annual()
          + _year(2026, 40.0, computed="2026-10-04T02:00:00+00:00"))
    history = idx._compute_index_pe_basis(now=_NOW)["history"]
    assert history["last_year"] == 2025 and history["avg"] == 22.14


def test_a_stale_ttm_set_names_no_sector_count_but_keeps_the_history(monkeypatch):
    _stub(monkeypatch, _ttm(computed="2026-09-01T00:00:00+00:00") + _prod_annual())
    basis = idx._compute_index_pe_basis(now=_NOW)
    assert basis["sectors"] is None and basis["history"]["avg"] == 22.14


def test_a_failed_read_degrades_to_no_basis_and_says_so(monkeypatch, caplog):
    def _boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(idx, "get_supabase", _boom)
    with caplog.at_level("WARNING", logger=idx.logger.name):
        assert idx._compute_index_pe_basis(now=_NOW) == {"sectors": None, "history": None}
    assert any("index P/E basis" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("basis, ttl", [
    ({"sectors": 11, "history": {"avg": 22.14}}, idx._DERIVED_TTL),
    ({"sectors": 11, "history": None}, idx._CACHE_TTL_SECONDS),
    ({"sectors": None, "history": None}, idx._CACHE_TTL_SECONDS),
])
async def test_only_a_complete_basis_is_held_for_twelve_hours(monkeypatch, basis, ttl):
    monkeypatch.setattr(idx, "_compute_index_pe_basis", lambda: basis)
    idx._cache.pop("idx:pe_basis", None)
    svc = idx.IndexService.__new__(idx.IndexService)
    assert await svc._get_pe_basis() == basis
    assert idx._cache["idx:pe_basis"][2] == ttl
    idx._cache.pop("idx:pe_basis", None)


# ── 3. the sentence (owner-approved wording) ────────────────────────────────────────


def _resolve(template, pe="21.2x", avg="22x"):
    """What the device shows: `IndexValuationSnapshot.resolvedStory`."""
    return (template.replace("{PE_RATIO}", pe).replace("{HISTORICAL_PERIOD}", "10-year")
            .replace("{HISTORICAL_AVG_PE}", avg))


def test_the_sentence_resolves_exactly_as_the_owner_previewed():
    story = idx._valuation_story(pe=21.17, pe_sectors=11, pe_history={"avg": 22.14})
    assert _resolve(story) == (
        "The US market composite (the average of 11 sector median P/Es, the same on every "
        "index screen) trades at 21.2x earnings, in line with its own 10-year average of "
        "about 22x."
    )


@pytest.mark.parametrize("pe, avg, relation", [
    (21.17, 22.14, "in line with"),
    (23.20, 22.14, "in line with"),    # +4.8%
    (23.30, 22.14, "above"),           # +5.2%
    (21.00, 22.14, "below"),           # -5.1%
    (21.00, 20.00, "in line with"),    # exactly +5%: the band is inclusive
    (19.00, 20.00, "in line with"),    # exactly -5%
    (45.00, 22.14, "above"),
    (8.00, 22.14, "below"),
])
def test_the_relation_follows_the_five_percent_band(pe, avg, relation):
    story = idx._valuation_story(pe=pe, pe_sectors=11, pe_history={"avg": avg})
    assert f", {relation} its own {{HISTORICAL_PERIOD}} average of about" in story


@pytest.mark.parametrize("history", [None, {}, {"avg": 0}, {"avg": -3.0}, {"avg": float("nan")},
                                     {"avg": float("inf")}, {"avg": "22.1"}, {"avg": None}])
def test_without_a_usable_history_there_is_no_comparison(history):
    story = idx._valuation_story(pe=21.17, pe_sectors=11, pe_history=history)
    assert story.endswith("trades at {PE_RATIO} earnings.")
    assert "{HISTORICAL_AVG_PE}" not in story and "average of about" not in story


def test_an_unknown_sector_count_is_not_invented():
    story = idx._valuation_story(pe=21.17, pe_sectors=None, pe_history={"avg": 22.14})
    assert "(the average of the sector median P/Es, the same on every index screen)" in story
    assert not re.search(r"\d+ sector median", story)


_VERDICTS = ("premium", "potential value", "pricing in", "discount", "cheap", "expensive",
             "bargain", "overvalued", "undervalued", "opportunit", "should", "buy", "sell",
             "{VALUATION_LABEL}", "{FORWARD_PE}", "S&P", "Nasdaq", "Dow")


@pytest.mark.parametrize("pe", [0, None, 8.0, 21.17, 40.0])
@pytest.mark.parametrize("history", [None, {"avg": 22.14}, {"avg": float("nan")}])
@pytest.mark.parametrize("sectors", [None, 8, 11])
def test_no_branch_carries_a_verdict_or_an_index_name(pe, history, sectors):
    story = idx._valuation_story(pe=pe, pe_sectors=sectors, pe_history=history).lower()
    for word in _VERDICTS:
        assert word.lower() not in story, f"{word!r} in {story!r}"


# ── 4. the wire: one valuation for every index ──────────────────────────────────────


async def _snapshots(symbol, basis, pe=21.17):
    svc = idx.IndexService.__new__(idx.IndexService)

    async def _no_ai(**_kw):
        return "sector story", "macro story", []

    svc._generate_ai_stories = _no_ai
    return await svc._build_snapshots(
        symbol=symbol, pe=pe, forward_pe=0.0, earnings_yield=4.72, sector_raw=[],
        index_name=symbol, pe_basis=basis,
    )


_BASIS = {"sectors": 11, "history": {"avg": 22.14, "years": 10, "first_year": 2016,
                                     "last_year": 2025}}


@pytest.mark.asyncio
async def test_every_index_gets_the_same_valuation():
    """The finding itself: the same composite produced three different verdicts."""
    vals = [(await _snapshots(s, _BASIS)).valuation.model_dump() for s in ("^GSPC", "^IXIC", "^DJI")]
    assert vals[0] == vals[1] == vals[2]
    v = vals[0]
    assert v["historical_avg_pe"] == 22.1 and v["historical_avg_pe_known"] is True
    assert v["historical_period"] == "10-year"
    assert "in line with" in v["story_template"]


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", [None, {}, {"sectors": 11, "history": None},
                                   {"sectors": 11, "history": {"avg": float("nan")}}])
async def test_no_history_is_the_zero_sentinel_and_a_false_flag(basis):
    out = await _snapshots("^IXIC", basis)
    v = out.valuation
    assert v.historical_avg_pe == 0.0 and v.historical_avg_pe_known is False
    assert "{HISTORICAL_AVG_PE}" not in v.story_template
    # Parity: the response round-trips and serialises with no NaN token (iOS decode).
    json.dumps(out.model_dump(), allow_nan=False)
    IndexSnapshotsDataResponse.model_validate(out.model_dump())


def test_the_flag_fails_closed_and_the_floats_stay_non_optional():
    fields = ValuationSnapshotResponse.model_fields
    assert fields["historical_avg_pe_known"].default is False
    assert fields["historical_avg_pe"].annotation is float, (
        "shipped iOS builds decode historical_avg_pe as a plain Double"
    )


def test_no_profile_carries_a_per_index_pe_anchor():
    for symbol, meta in idx._INDEX_PROFILES.items():
        assert "historical_avg_pe" not in meta, symbol


# ── 5. chat's get_market_overview ───────────────────────────────────────────────────


def _detail(pe, pe_known, avg, avg_known=None):
    val = SimpleNamespace(pe_ratio=pe, pe_known=pe_known, forward_pe=0.0, earnings_yield=4.72,
                          historical_avg_pe=avg)
    if avg_known is not None:
        val.historical_avg_pe_known = avg_known
    sp = SimpleNamespace(sectors=[SimpleNamespace(sector="Technology", change_percent=0.8)])
    macro = SimpleNamespace(indicators=[SimpleNamespace(title="GDP", signal="neutral")])
    return SimpleNamespace(snapshots_data=SimpleNamespace(
        valuation=val, sector_performance=sp, macro_forecast=macro))


@pytest.mark.asyncio
@pytest.mark.parametrize("pe, pe_known, avg, avg_known, want_avg_known, want_basis", [
    (21.2, True, 22.1, True, True, True),
    (21.2, True, 22.1, None, False, True),     # a valuation without the flag: unknown
    (21.2, True, 0.0, True, False, True),      # a stale flag cannot vouch for a 0
    (21.2, True, float("nan"), True, False, True),
    (0.0, False, 22.1, True, True, False),     # no P/E → no basis line for the model
])
async def test_the_chat_card_tells_the_model_what_the_numbers_are(
    monkeypatch, pe, pe_known, avg, avg_known, want_avg_known, want_basis,
):
    from app.services import chat_service as cs

    svc = cs.ChatService.__new__(cs.ChatService)
    fake = SimpleNamespace(get_index_detail=AsyncMock(
        return_value=_detail(pe, pe_known, avg, avg_known)))
    monkeypatch.setattr("app.services.index_service.get_index_service", lambda: fake)
    out = await svc._fetch_market_overview_data("^IXIC")
    assert "error" not in out, out
    assert out["historical_avg_pe_known"] is want_avg_known
    assert out["pe_basis"] == (MARKET_PE_BASIS if want_basis else None)
    assert math.isfinite(out["historical_avg_pe"])
    json.dumps(out, allow_nan=False)


def test_the_chat_schema_fails_closed_and_the_basis_names_the_composite():
    fields = MarketOverviewWidget.model_fields
    assert fields["historical_avg_pe_known"].default is False
    assert fields["pe_basis"].default is None
    for phrase in ("US market composite", "same figure for every index",
                   "not this index's own P/E", "own 10-year average"):
        assert phrase in MARKET_PE_BASIS


# ── 6. the iOS half ─────────────────────────────────────────────────────────────────

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _swift(path):
    return "\n".join(re.sub(r"//.*$", "", line)
                     for line in (_IOS / path).read_text(encoding="utf-8").splitlines())


def _body(src, decl):
    i = src.index("{", src.index(decl))
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    raise AssertionError(decl)


def test_ios_decodes_the_flag_as_optional_and_maps_nil_to_the_old_behaviour():
    dto = _swift("Models/IndexDetailResponseModels.swift")
    assert "let historicalAvgPeKnown: Bool?" in dto
    assert 'case historicalAvgPeKnown = "historical_avg_pe_known"' in dto
    assert "historicalAvgKnown: snapshotsData.valuation.historicalAvgPeKnown ?? true" in dto


def test_ios_gates_the_ten_year_pill_and_the_story_token():
    model = _swift("Models/IndexDetailModels.swift")
    body = _body(model, "var historicalAvgDisplay: String")
    assert "historicalAvgKnown && historicalAvgPE > 0" in body and '"—"' in body
    story = _body(model, "var resolvedStory: String")
    assert '"{HISTORICAL_AVG_PE}", with: historicalAvgDisplay' in story
    view = _swift("Views/Organisms/IndexDetailSnapshotsSection.swift")
    card = _body(view, "struct ValuationSnapshotCard: View")
    assert 'ValuationMetricPill(label: "Market P/E (TTM)", value: valuation.peDisplay)' in card
    assert 'ValuationMetricPill(label: "10Y Avg", value: valuation.historicalAvgDisplay)' in card
    assert "valuation.historicalAvgPE)" not in card, "the pill formats the raw sentinel again"


def test_ios_info_sheet_makes_no_value_claim_about_the_comparison():
    view = _swift("Views/Organisms/IndexDetailSnapshotsSection.swift")
    sheet = _body(view, "struct IndexSnapshotsInfoSheet: View")
    for gone in ("value opportunities", "cheap or expensive", "index P/E ratio against"):
        assert gone not in sheet, gone
    assert "the same on every index screen" in sheet


def test_ios_chat_card_gates_its_ten_year_pill():
    dto = _swift("Models/ChatConversationModels.swift")
    assert "let historicalAvgPeKnown: Bool?" in dto
    assert 'case historicalAvgPeKnown = "historical_avg_pe_known"' in dto
    assert "(historicalAvgPeKnown ?? true) && historicalAvgPe > 0" in dto
    view = _swift("Views/Molecules/ChatMarketOverviewWidget.swift")
    metrics = _body(view, "private var valuationMetrics: some View")
    assert "data.hasKnownHistoricalAvg" in metrics
    assert 'value: String(format: "%.0fx", data.historicalAvgPe))' not in metrics


def test_ios_analyst_context_names_the_composite():
    vm = _swift("ViewModels/IndexDetailViewModel.swift")
    assert "not this index's own P/E" in vm
    assert "Composite's own" in vm
    assert 'String(format: "%.0f", val.historicalAvgPE)' not in vm
