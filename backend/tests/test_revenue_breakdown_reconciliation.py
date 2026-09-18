"""The revenue-breakdown stack is reconciled to REPORTED revenue (2026-09-17).

FMP's product segmentation is a mix hint, not the revenue. Surveyed live across 22 large
caps on 2026-09-17: INTC's four segments sum to 134% of revenue (an explicit
"Intersegment Eliminations" row of −17.68B that `_extract_segments` dropped as negative),
AMD 111% ("Gaming" listed inside "Client and Gaming"), CAT 172% (a "Reportable
Subsegments" total line + a bogus negative "Power & Energy"); KO covers 78%, BA 46%,
Ford 7% ("Ford Credit" only). The card stacked whatever it was given, so INTC's revenue
bar towered over its costs in a loss year (the TestFlight screenshot) and Ford's shrank
to a sliver. Every fixture below is one of those live shapes.

Hermetic: FMP is faked at the service boundary; Supabase is never touched.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List

import pytest

import app.services.revenue_breakdown_service as rb
from app.schemas.revenue_breakdown import RevenueBreakdownResponse, RevenueSourceSchema
from app.services.revenue_breakdown_service import (
    RevenueBreakdownService,
    _MIN_SEGMENT_COVERAGE,
    _RB_PAYLOAD_VERSION,
    _RECONCILE_TOLERANCE,
    _VERSION_KEY,
    _explicit_eliminations,
    _extract_segments,
    _reconcile_segments,
)

B = 1e9


def _src(**kv) -> List[RevenueSourceSchema]:
    return [RevenueSourceSchema(name=k, value=v) for k, v in kv.items()]


# ── unit: _reconcile_segments ────────────────────────────────────────────────

def test_a_stack_within_tolerance_is_left_alone():
    # LMT: 74.4B of segments vs 75.06B reported (−0.9%) — the case the denominator fix
    # was written for; XOM −2.1% is the same. Neither gets a filler slice.
    for reported in (75.06 * B, 74.4 * B / 0.979):
        out, elim, outcome = _reconcile_segments(_src(A=29 * B, B=17.4 * B, C=14.7 * B, D=13.3 * B), reported)
        assert outcome == "exact" and elim is None
        assert [s.name for s in out] == ["A", "B", "C", "D"]


def test_intc_shape_is_gross_and_the_derived_gap_matches_fmps_eliminations_row():
    srcs = _src(**{"Client Computing Group": 32.228 * B, "Intel Foundry Services": 17.826 * B,
                   "Data Center Group": 16.919 * B, "Other Segments": 3.563 * B})
    out, elim, outcome = _reconcile_segments(srcs, 52.853 * B, explicit_eliminations=17.683 * B, ticker="INTC")
    assert outcome == "gross"
    assert elim == pytest.approx(17.683 * B, rel=1e-6)
    # The segments are sent AS REPORTED — never rescaled, never dropped.
    assert [(s.name, s.value) for s in out] == [(s.name, s.value) for s in srcs]
    assert sum(s.value for s in out) - elim == pytest.approx(52.853 * B)


def test_a_gross_stack_without_an_explicit_row_still_reconciles_by_the_derived_gap(caplog):
    out, elim, outcome = _reconcile_segments(_src(A=60 * B, C=20 * B), 70 * B, explicit_eliminations=0.0)
    assert outcome == "gross" and elim == pytest.approx(10 * B)


def test_an_explicit_eliminations_row_that_disagrees_is_logged_but_the_derived_gap_wins(caplog):
    caplog.set_level("WARNING")
    out, elim, _ = _reconcile_segments(_src(A=60 * B, C=20 * B), 70 * B, explicit_eliminations=4 * B, ticker="X")
    assert elim == pytest.approx(10 * B), "the derived gap is what makes the card add up"
    assert any("[revenue-seg-eliminations-mismatch]" in r.message for r in caplog.records)


def test_amd_shape_drops_the_sub_line_instead_of_inventing_eliminations():
    srcs = _src(**{"Data Center": 16.64 * B, "Client and Gaming": 14.55 * B, "Gaming": 3.91 * B, "Embedded": 3.45 * B})
    out, elim, outcome = _reconcile_segments(srcs, 34.62 * B, ticker="AMD")
    assert outcome == "subline" and elim is None
    assert [s.name for s in out] == ["Data Center", "Client and Gaming", "Embedded"]
    assert sum(s.value for s in out) == pytest.approx(34.62 * B, rel=_RECONCILE_TOLERANCE)


def test_a_sub_line_is_only_dropped_when_dropping_it_reconciles():
    # "Services" inside "Financial Services" is a real pair of segments here: dropping it
    # would NOT land on revenue, so it must survive and the stack goes the gross route.
    srcs = _src(**{"Financial Services": 50 * B, "Services": 30 * B, "Products": 40 * B})
    out, elim, outcome = _reconcile_segments(srcs, 100 * B)
    assert outcome == "gross" and len(out) == 3 and elim == pytest.approx(20 * B)


def test_ko_shape_gets_an_explicit_unallocated_segment():
    srcs = _src(**{"North America": 18.5 * B, "EMEA": 8.2 * B, "Latin America": 6.6 * B, "Asia Pacific": 3.4 * B})
    out, elim, outcome = _reconcile_segments(srcs, 47.06 * B, ticker="KO")
    assert outcome == "unallocated" and elim is None
    assert out[-1].name == "Unallocated"
    assert out[-1].value == pytest.approx(47.06 * B - 36.7 * B)
    assert sum(s.value for s in out) == pytest.approx(47.06 * B)


@pytest.mark.parametrize("name,value,reported", [
    ("Ford Credit", 13.27 * B, 185.0 * B),            # 7%
    ("Commercial Airplanes Segment", 41.49 * B, 89.4 * B),  # 46%
])
def test_thin_coverage_is_not_a_breakdown(name, value, reported, caplog):
    caplog.set_level("WARNING")
    out, elim, outcome = _reconcile_segments(_src(**{name: value}), reported, ticker="T")
    assert outcome == "thin" and out == [] and elim is None
    assert any("[revenue-seg-thin]" in r.message for r in caplog.records)


def test_the_coverage_floor_is_a_boundary_not_a_cliff():
    # Exactly 50% is still a breakdown (gets Unallocated); a hair under is thin.
    out, _, outcome = _reconcile_segments(_src(A=50 * B), 100 * B)
    assert outcome == "unallocated"
    out, _, outcome = _reconcile_segments(_src(A=100 * B * (_MIN_SEGMENT_COVERAGE - 0.001)), 100 * B)
    assert outcome == "thin"


@pytest.mark.parametrize("reported", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_no_usable_reported_revenue_means_no_reconciliation(reported):
    srcs = _src(A=60 * B, C=20 * B)
    out, elim, outcome = _reconcile_segments(srcs, reported)
    assert outcome == "unreconciled" and elim is None and out is srcs


def test_empty_or_zero_sources_are_passed_through():
    assert _reconcile_segments([], 10 * B) == ([], None, "unreconciled")
    zero = _src(A=0.0)
    assert _reconcile_segments(zero, 10 * B)[2] == "unreconciled"


def test_reconciliation_never_returns_a_negative_or_non_finite_source():
    for reported in (52.853 * B, 47.06 * B, 100 * B):
        out, elim, _ = _reconcile_segments(_src(A=32.2 * B, C=17.8 * B, D=16.9 * B, E=3.6 * B), reported)
        assert all(s.value > 0 and math.isfinite(s.value) for s in out)
        assert elim is None or (elim > 0 and math.isfinite(elim))


# ── unit: row classification ─────────────────────────────────────────────────

def test_explicit_eliminations_reads_only_negative_elimination_rows():
    rec = {"data": {"Client": 32 * B, "Intersegment Eliminations": -17.683 * B,
                    "Power & Energy": -5.06 * B, "Corporate items": -1 * B, "junk": "x"}}
    assert _explicit_eliminations(rec) == pytest.approx(18.683 * B)
    assert _explicit_eliminations({"data": {"Client": 32 * B}}) == 0.0
    assert _explicit_eliminations({"Client": 32 * B, "Eliminations": -2 * B, "date": "2025-01-01"}) == pytest.approx(2 * B)


def test_total_like_rows_are_dropped_but_a_segment_that_starts_with_revenue_is_not():
    rec = {"data": {"Reportable Subsegments": 73.95 * B, "Construction Industries": 25.06 * B,
                    "Total Revenue": 99 * B, "Revenue": 99 * B, "Net Revenue": 99 * B,
                    "Revenue from Services": 10 * B, "Resource Industries": 12.47 * B}}
    names = {s.name for s in _extract_segments(rec)}
    assert names == {"Construction Industries", "Resource Industries", "Revenue from Services"}


# ── the real builder, end to end ─────────────────────────────────────────────

class _FMP:
    def __init__(self, seg, inc):
        self._seg, self._inc = seg, inc

    async def get_revenue_product_segmentation(self, *a, **k):
        return self._seg

    async def get_income_statement(self, *a, **k):
        return self._inc

    async def get_earning_calendar_full(self, *a, **k):
        return []


def _build(ticker: str, data: Dict[str, Any], revenue: float, net_income: float = 1.0) -> RevenueBreakdownResponse:
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = _FMP(
        [{"fiscalYear": 2025, "date": "2025-12-27", "data": data}],
        [{"fiscalYear": 2025, "date": "2025-12-27", "revenue": revenue, "costOfRevenue": 34.478 * B,
          "operatingExpenses": 18.398 * B, "incomeTaxExpense": 1.531 * B, "netIncome": net_income}],
    )
    resp, _ = asyncio.run(svc._build_revenue_breakdown(ticker))
    return resp


def test_builder_intc_stack_is_gross_with_eliminations_and_the_costs_stay_paired():
    r = _build("INTC", {"Client Computing Group": 32.228 * B, "Data Center Group": 16.919 * B,
                        "Intel Foundry Services": 17.826 * B, "Other Segments": 3.563 * B,
                        "Intersegment Eliminations": -17.683 * B}, 52.853 * B, net_income=-0.267 * B)
    assert r.intersegment_eliminations == pytest.approx(17.683 * B, rel=1e-6)
    assert sum(s.value for s in r.revenue_sources) == pytest.approx(70.536 * B, rel=1e-6)
    assert r.reported_revenue == pytest.approx(52.853 * B)
    assert r.net_income == pytest.approx(-0.267 * B)
    assert r.fiscal_year == "2025" and r.cost_of_sales == pytest.approx(34.478 * B)
    # what iOS will draw: stack − eliminations == revenue, and the waterfall closes on net income
    net_rev = sum(s.value for s in r.revenue_sources) - r.intersegment_eliminations
    assert net_rev == pytest.approx(r.reported_revenue)
    assert net_rev - r.cost_of_sales - r.operating_expense - r.tax - r.other_expense == pytest.approx(r.net_income)


def test_builder_thin_feed_falls_back_to_total_revenue_without_the_year_mismatch_warning(caplog):
    caplog.set_level("INFO")
    r = _build("F", {"Ford Credit": 13.27 * B}, 185.0 * B)
    assert [(s.name, s.value) for s in r.revenue_sources] == [("Total Revenue", 185.0 * B)]
    assert r.intersegment_eliminations is None
    assert r.fiscal_year == "2025", "the income record must still be the paired year"
    assert not any("have no matching income statement" in rec.message for rec in caplog.records)


def test_builder_cat_shape_drops_the_total_row_and_the_bogus_negative_then_fills_unallocated():
    r = _build("CAT", {"Power & Energy": -5.06 * B, "Construction Industries": 25.06 * B,
                       "Reportable Subsegments": 73.95 * B, "Resource Industries": 12.47 * B,
                       "Other Segments": 0.33 * B, "Financial Products": 4.22 * B}, 64.8 * B)
    names = [s.name for s in r.revenue_sources]
    assert "Reportable Subsegments" not in names and "Power & Energy" not in names
    assert names[-1] == "Unallocated"
    assert sum(s.value for s in r.revenue_sources) == pytest.approx(64.8 * B)
    assert r.intersegment_eliminations is None


def test_builder_aapl_shape_is_untouched():
    data = {"iPhone": 209.6 * B, "Services": 109.2 * B, "Mac": 33.7 * B, "iPad": 28.4 * B, "Wearables": 35.1 * B}
    r = _build("AAPL", data, 416.0 * B)
    assert {s.name for s in r.revenue_sources} == set(data)
    assert r.intersegment_eliminations is None


# ── wire + cache ─────────────────────────────────────────────────────────────

def test_the_new_field_is_optional_and_defaults_none_for_old_rows():
    old_row = {"symbol": "X", "fiscal_year": "2024", "revenue_sources": [{"name": "A", "value": 1.0}],
               "cost_of_sales": 1.0, "operating_expense": 1.0, "tax": 0.0}
    r = RevenueBreakdownResponse(**old_row)
    assert r.intersegment_eliminations is None
    assert "intersegment_eliminations" in r.model_dump()


def test_cache_read_refuses_rows_without_the_current_payload_version():
    class _Result:
        def __init__(self, data): self.data = data

    class _Table:
        def __init__(self, row): self._row = row
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return _Result([self._row])

    class _SB:
        def __init__(self, row): self._row = row
        def table(self, name): return _Table(self._row)

    from datetime import datetime, timezone
    fresh = datetime.now(timezone.utc).isoformat()
    body = {"symbol": "INTC", "fiscal_year": "2025", "revenue_sources": [{"name": "A", "value": 1.0}],
            "cost_of_sales": 1.0, "operating_expense": 1.0, "tax": 0.0}
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)

    svc.supabase = _SB({"response_json": body, "cached_at": fresh, "next_earnings_date": None})
    assert svc._check_supabase_cache("INTC") is None, "a pre-reconciliation row must rebuild"

    svc.supabase = _SB({"response_json": {**body, _VERSION_KEY: _RB_PAYLOAD_VERSION - 1}, "cached_at": fresh, "next_earnings_date": None})
    assert svc._check_supabase_cache("INTC") is None

    svc.supabase = _SB({"response_json": {**body, _VERSION_KEY: _RB_PAYLOAD_VERSION}, "cached_at": fresh, "next_earnings_date": None})
    assert svc._check_supabase_cache("INTC") is not None


def test_cache_write_stamps_the_payload_version():
    captured = {}

    class _Table:
        def upsert(self, row, **k):
            captured.update(row)
            return self
        def execute(self): return None

    class _SB:
        def table(self, name): return _Table()

    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.supabase = _SB()
    resp = RevenueBreakdownResponse(symbol="INTC", fiscal_year="2025", revenue_sources=[RevenueSourceSchema(name="A", value=1.0)],
                                    cost_of_sales=1.0, operating_expense=1.0, tax=0.0, intersegment_eliminations=2.0)
    svc._upsert_supabase_cache_safe("INTC", resp, None)
    assert captured["response_json"][_VERSION_KEY] == _RB_PAYLOAD_VERSION
    assert captured["response_json"]["intersegment_eliminations"] == 2.0


# ── hardening after the adversarial pass (2026-09-17) ────────────────────────

def test_a_positively_signed_eliminations_row_is_never_stacked_as_revenue():
    """Some feeds book the row as +17.68B. Stacked, it would DOUBLE the amount it removes."""
    rec = {"data": {"Client Computing Group": 32.228 * B, "Data Center Group": 16.919 * B,
                    "Intel Foundry Services": 17.826 * B, "Other Segments": 3.563 * B,
                    "Intersegment Eliminations": +17.683 * B}}
    names = {s.name for s in _extract_segments(rec)}
    assert "Intersegment Eliminations" not in names
    assert _explicit_eliminations(rec) == pytest.approx(17.683 * B)
    r = _build("INTC", rec["data"], 52.853 * B, net_income=-0.267 * B)
    assert r.intersegment_eliminations == pytest.approx(17.683 * B, rel=1e-6)
    assert sum(s.value for s in r.revenue_sources) == pytest.approx(70.536 * B, rel=1e-6)


@pytest.mark.parametrize("name,kept", [
    ("Total Revenue", False), ("Total", False), ("Revenue", False), ("Revenues", False),
    ("Net Revenue", False), ("Reportable Subsegments", False), ("Segment Total", False),
    ("Consolidated Total", False), ("Consolidated Revenue", False),
    ("Revenue from Services", True), ("Revenue Cycle Management", True),
    ("Consolidated Edison Company of New York", True), ("Totally Bananas Snacks", False),
])
def test_total_like_regex_boundaries(name, kept):
    # "Totally Bananas Snacks" is the accepted cost of `^total\b`? No — `\b` after "total"
    # does not match inside "Totally", so it is KEPT. Pin the actual behaviour either way.
    rec = {"data": {name: 10 * B, "Anchor": 90 * B}}
    names = {s.name for s in _extract_segments(rec)}
    assert (name in names) == (kept or name == "Totally Bananas Snacks")


def test_a_thin_newest_year_falls_through_to_an_older_year_with_real_coverage():
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = _FMP(
        [{"fiscalYear": 2025, "date": "2025-12-27", "data": {"Ford Credit": 13.27 * B}},
         {"fiscalYear": 2024, "date": "2024-12-28", "data": {"Ford Blue": 100 * B, "Ford Pro": 67 * B, "Ford Credit": 12 * B}}],
        [{"fiscalYear": 2025, "date": "2025-12-27", "revenue": 185.0 * B, "costOfRevenue": 1.0, "netIncome": 1.0},
         {"fiscalYear": 2024, "date": "2024-12-28", "revenue": 185.0 * B, "costOfRevenue": 2.0, "netIncome": 2.0}],
    )
    r, _ = asyncio.run(svc._build_revenue_breakdown("F"))
    assert r.fiscal_year == "2024", "the older, real breakdown wins over a single bar"
    assert {s.name for s in r.revenue_sources} >= {"Ford Blue", "Ford Pro", "Ford Credit"}
    assert r.cost_of_sales == 2.0, "costs stay paired with the year actually shown"


def test_every_year_thin_still_labels_the_fallback_with_the_newest_year():
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = _FMP(
        [{"fiscalYear": 2025, "date": "2025-12-27", "data": {"Ford Credit": 13.27 * B}},
         {"fiscalYear": 2024, "date": "2024-12-28", "data": {"Ford Credit": 12 * B}}],
        [{"fiscalYear": 2025, "date": "2025-12-27", "revenue": 185.0 * B, "costOfRevenue": 1.0, "netIncome": 1.0},
         {"fiscalYear": 2024, "date": "2024-12-28", "revenue": 180.0 * B, "costOfRevenue": 2.0, "netIncome": 2.0}],
    )
    r, _ = asyncio.run(svc._build_revenue_breakdown("F"))
    assert r.fiscal_year == "2025" and [s.name for s in r.revenue_sources] == ["Total Revenue"]
    assert r.revenue_sources[0].value == 185.0 * B and r.cost_of_sales == 1.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -5 * B, 0.0, "x", None])
def test_garbage_segment_values_never_reach_the_stack_or_the_eliminations(bad):
    rec = {"data": {"Good": 90 * B, "Weird": bad, "Intersegment Eliminations": bad}}
    out = _extract_segments(rec)
    assert [s.name for s in out] == ["Good"]
    e = _explicit_eliminations(rec)
    assert math.isfinite(e) and e >= 0
    # and reconciliation stays finite/positive whatever the eliminations magnitude
    srcs, elim, _ = _reconcile_segments(out, 100 * B, explicit_eliminations=e)
    assert all(s.value > 0 and math.isfinite(s.value) for s in srcs) and (elim is None or math.isfinite(elim))


def test_tolerance_band_is_three_percent_either_side():
    # Inside the band (LMT −0.9%, XOM −2.1% live) is left alone; just outside it is not.
    for ratio in (1.029, 0.971):
        _, elim, outcome = _reconcile_segments(_src(A=100 * B * ratio), 100 * B)
        assert outcome == "exact" and elim is None, ratio
    _, elim, outcome = _reconcile_segments(_src(A=100 * B * 1.031), 100 * B)
    assert outcome == "gross" and elim == pytest.approx(3.1 * B)
    _, _, outcome = _reconcile_segments(_src(A=100 * B * 0.969), 100 * B)
    assert outcome == "unallocated"


def test_sub_line_rule_ignores_empty_names_and_is_case_and_space_insensitive():
    srcs = _src(**{"": 5 * B, "Client and Gaming ": 14.55 * B, "gaming": 3.91 * B, "Data Center": 16.64 * B, "Embedded": 3.45 * B})
    out, elim, outcome = _reconcile_segments(srcs, 34.62 * B + 5 * B)
    assert outcome == "subline" and "gaming" not in [s.name for s in out]
    assert "" in [s.name for s in out], "an empty name is never treated as a substring of everything"


def test_other_and_unallocated_can_coexist_and_mean_different_things():
    rec = {"data": {"Big": 80 * B, "tiny1": 1 * B, "tiny2": 1 * B}}
    out, _, outcome = _reconcile_segments(_extract_segments(rec), 100 * B)
    assert outcome == "unallocated"
    assert [s.name for s in out] == ["Big", "Other", "Unallocated"]
    assert sum(s.value for s in out) == pytest.approx(100 * B)
