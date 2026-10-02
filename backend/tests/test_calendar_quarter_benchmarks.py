"""Quarterly sector/industry benchmarks are keyed by the CALENDAR quarter a period ENDS in.

Financials-tab deep check 2026-09-30, finding #34. The quarterly benchmark rows were keyed
"<FISCAL quarter number>'<calendar year of the period end>", and Growth / Profit Power / the
report drill-down joined a company's quarter to that key, so for an off-calendar company the
peer line came from the wrong quarter:

  * Microsoft fiscal Q1 '26 (Jul-Sep 2025)  → "Q1'25" = peers' Jan-Mar 2025 (6 months stale)
  * Nvidia Q4 FY25 (Nov 2024-Jan 2025)      → "Q4'25" = peers' Oct-Dec 2025 (~10 months AHEAD)
  * Apple's Dec quarter (fiscal Q1)         → peers' Jan-Mar

and the medians themselves pooled mismatched quarters. The rows now live under their own
period_type (`CALENDAR_QUARTER_PERIOD_TYPE`) keyed by `period_labels.calendar_quarter_label`,
and every reader joins on the same helper. Hermetic: inline FMP-shaped rows, a fake table.
"""

from __future__ import annotations

import logging
import re

import pytest
from postgrest.exceptions import APIError

from app.services import industry_benchmark_service as ibs
from app.services import sector_benchmark_service as sbs
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE
from app.utils import period_labels as pl
from app.utils.supabase_errors import is_check_violation

_LABEL_RE = re.compile(r"^Q[1-4]'\d{2}$")


# ── Company calendars (real period-end dates, FMP /stable row shape) ─────────────────

def _q(date: str, period: str, fiscal_year: int, **fields) -> dict:
    return {"date": date, "period": period, "fiscalYear": str(fiscal_year), **fields}


# Microsoft: FY ends Jun 30 → fiscal Q1 = Jul-Sep.
MSFT = [
    ("2024-09-30", "Q1", 2025), ("2024-12-31", "Q2", 2025), ("2025-03-31", "Q3", 2025),
    ("2025-06-30", "Q4", 2025), ("2025-09-30", "Q1", 2026), ("2025-12-31", "Q2", 2026),
]
# Apple: FY ends the last Saturday of September → fiscal Q1 = the December quarter.
AAPL = [
    ("2024-12-28", "Q1", 2025), ("2025-03-29", "Q2", 2025), ("2025-06-28", "Q3", 2025),
    ("2025-09-27", "Q4", 2025), ("2025-12-27", "Q1", 2026),
]
# Nvidia: FY ends the last Sunday of January → Q4 = Nov-Jan.
NVDA = [
    ("2024-04-28", "Q1", 2025), ("2024-07-28", "Q2", 2025), ("2024-10-27", "Q3", 2025),
    ("2025-01-26", "Q4", 2025), ("2025-04-27", "Q1", 2026), ("2025-07-27", "Q2", 2026),
    ("2025-10-26", "Q3", 2026), ("2026-01-25", "Q4", 2026),
]
# Coca-Cola: calendar quarters (fiscal Q == calendar Q) — the control case.
KO = [
    ("2024-03-29", "Q1", 2024), ("2024-06-28", "Q2", 2024), ("2024-09-27", "Q3", 2024),
    ("2024-12-31", "Q4", 2024), ("2025-03-28", "Q1", 2025), ("2025-06-27", "Q2", 2025),
    ("2025-09-26", "Q3", 2025), ("2025-12-31", "Q4", 2025),
]


@pytest.mark.parametrize("record, expected", [
    # Off-calendar: the key follows the period END, not the fiscal quarter number.
    (_q("2025-09-30", "Q1", 2026), "Q3'25"),   # MSFT fiscal Q1 '26 = Jul-Sep 2025
    (_q("2025-01-26", "Q4", 2025), "Q1'25"),   # NVDA Q4 FY25 ends Jan 2025 (old key: Q4'25)
    (_q("2024-12-28", "Q1", 2025), "Q4'24"),   # AAPL's December quarter (fiscal Q1)
    (_q("2025-10-26", "Q3", 2026), "Q4'25"),   # NVDA Aug-Oct 2025 quarter
    # Calendar filer: unchanged.
    (_q("2025-03-28", "Q1", 2025), "Q1'25"),   # KO
    (_q("2025-12-31", "Q4", 2025), "Q4'25"),   # KO
    # 52/53-week filers: an end on day 1-7 belongs to the previous month — and year.
    (_q("2026-01-03", "Q4", 2025), "Q4'25"),   # Snap-on/Cadence-shaped FY2025 Q4
    (_q("2025-04-05", "Q1", 2025), "Q1'25"),
])
def test_join_key_is_the_calendar_quarter_of_the_period_end(record, expected):
    assert pl.calendar_quarter_label(record) == expected
    # The producer's storage key is the SAME helper — the join cannot drift from it.
    assert sbs._quarterly_period_label(record) == expected


@pytest.mark.parametrize("bad", [
    {"date": None, "period": "Q1"},
    {"date": "", "period": "Q1"},
    {"date": "garbage", "period": "Q2"},
    {"date": "2025-02-30", "period": "Q1"},   # not a real day
    {"period": "Q3"},
    {"date": 20250331, "period": "Q1"},       # wrong type
    None,
    "Q1'25",
])
def test_unusable_date_gives_no_key_rather_than_an_invented_one(bad):
    # The old label was "<period>'<year>" and came out as "Q1'" for a null date — a key
    # that was then stored as a real benchmark period.
    assert pl.calendar_quarter_label(bad) == ""
    if isinstance(bad, dict):
        assert sbs._quarterly_period_label(bad) == ""


def test_previous_calendar_quarter_wraps_the_year():
    assert pl.previous_calendar_quarter((2026, 1)) == (2025, 4)
    assert pl.previous_calendar_quarter((2025, 3)) == (2025, 2)
    assert pl.format_calendar_quarter((2009, 4)) == "Q4'09"


# ── Pooling: each company's quarter lands with the peers' SAME calendar quarter ──────

_COMPANIES = {"MSFT": MSFT, "AAPL": AAPL, "NVDA": NVDA, "KO": KO}


def _company_data(field_source: str, field: str, value_of) -> list:
    """One company-data dict per company; `value_of(ticker, date)` gives each row's value."""
    out = []
    for ticker, cal in _COMPANIES.items():
        rows = [_q(d, p, fy, **{field: value_of(ticker, d)}) for d, p, fy in cal]
        out.append({f"{field_source}_quarterly": rows, f"{field_source}_annual": []})
    return out


def _svc() -> sbs.SectorBenchmarkService:
    # `_collect_metric_values` needs no FMP client or database.
    return sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)


def _metric(name: str) -> dict:
    return next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == name)


def _encode(ticker: str, date: str) -> float:
    """A value that names its own company and period end, so a test can see where it went."""
    return float(f"{list(_COMPANIES).index(ticker) + 1}{date.replace('-', '')}")


def test_direct_metric_pools_every_row_under_its_own_calendar_quarter():
    data = _company_data("ratios", "grossProfitMargin", _encode)
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "quarterly")

    where = {v: label for label, vals in pooled.items() for v in vals}
    for ticker, cal in _COMPANIES.items():
        for date, _period, _fy in cal:
            assert where[_encode(ticker, date)] == pl.calendar_quarter_label({"date": date}), (
                f"{ticker} {date} was pooled under {where[_encode(ticker, date)]}"
            )
    assert all(_LABEL_RE.match(label) for label in pooled)


def test_microsoft_jul_sep_sits_with_peers_jul_sep_not_jan_mar():
    data = _company_data("ratios", "grossProfitMargin", _encode)
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "quarterly")

    msft_jul_sep_25 = _encode("MSFT", "2025-09-30")       # fiscal Q1 '26
    ko_jul_sep_25 = _encode("KO", "2025-09-26")
    assert {msft_jul_sep_25, ko_jul_sep_25} <= set(pooled["Q3'25"])
    # The old key put it at "Q1'25" — peers' Jan-Mar 2025, six months stale.
    assert msft_jul_sep_25 not in pooled.get("Q1'25", [])


def test_nvidia_nov_jan_quarter_is_not_pooled_ten_months_ahead():
    data = _company_data("ratios", "grossProfitMargin", _encode)
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "quarterly")

    q4_fy25 = _encode("NVDA", "2025-01-26")                # Nov 2024 - Jan 2025
    assert q4_fy25 in pooled["Q1'25"]
    # The old key ("Q4" + end year 2025) filed it with peers' Oct-Dec 2025.
    assert q4_fy25 not in pooled["Q4'25"]
    # ...where Nvidia's own Aug-Oct 2025 quarter now belongs, next to KO's Oct-Dec.
    assert {_encode("NVDA", "2025-10-26"), _encode("KO", "2025-12-31")} <= set(pooled["Q4'25"])


def test_apple_december_quarter_sits_with_peers_oct_dec():
    data = _company_data("ratios", "grossProfitMargin", _encode)
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "quarterly")
    assert {_encode("AAPL", "2024-12-28"), _encode("KO", "2024-12-31")} <= set(pooled["Q4'24"])
    assert _encode("AAPL", "2024-12-28") not in pooled.get("Q1'24", [])


def test_annual_rows_are_untouched():
    # Only the QUARTERLY key moved; annual stays the end-date year.
    data = [{"ratios_annual": [{"date": "2025-06-30", "grossProfitMargin": 0.69},
                               {"date": "2024-06-30", "grossProfitMargin": 0.70}],
             "ratios_quarterly": []}]
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "annual")
    assert pooled == {"2025": [0.69], "2024": [0.70]}


# ── Same-quarter YoY / sequential QoQ use calendar-quarter lookups ───────────────────

def _rev_rows(cal, revenues):
    return [_q(d, p, fy, revenue=r) for (d, p, fy), r in zip(cal, revenues)]


def test_quarterly_yoy_compares_the_same_calendar_quarter_a_year_earlier():
    rows = _rev_rows(MSFT, [100, 110, 120, 130, 125, 150])
    yoy = sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=True)
    # Jul-Sep 2025 vs Jul-Sep 2024, Oct-Dec 2025 vs Oct-Dec 2024 — labelled by calendar.
    assert yoy == {"Q3'25": 25.0, "Q4'25": pytest.approx(36.36, abs=0.01)}


def test_nvidia_yoy_labels_follow_the_calendar_not_the_fiscal_quarter():
    rows = _rev_rows(NVDA, [26, 30, 35, 39, 44, 47, 57, 68])
    yoy = sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=True)
    assert set(yoy) == {"Q2'25", "Q3'25", "Q4'25", "Q1'26"}
    assert yoy["Q1'26"] == pytest.approx((68 - 39) / 39 * 100, abs=0.01)   # Q4 FY26 vs Q4 FY25


def test_yoy_finds_the_prior_year_for_a_52_53_week_q4_closing_on_jan_3():
    rows = [
        _q("2024-12-28", "Q4", 2024, revenue=100.0),
        _q("2025-03-29", "Q1", 2025, revenue=90.0),
        _q("2026-01-03", "Q4", 2025, revenue=110.0),   # 53-week year
    ]
    yoy = sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=True)
    # The old (fiscal period, end-year) key looked for a "Q4 2025" row keyed 2024: no YoY.
    assert yoy == {"Q4'25": 10.0}


def test_qoq_requires_the_adjacent_calendar_quarter():
    rows = [
        _q("2024-12-31", "Q4", 2024, revenue=100.0),
        _q("2025-03-31", "Q1", 2025, revenue=110.0),
        # Q2 2025 missing from FMP's history
        _q("2025-09-30", "Q3", 2025, revenue=200.0),
        _q("2025-12-31", "Q4", 2025, revenue=220.0),
    ]
    qoq = sbs._compute_qoq_for_records(rows, "revenue")
    # Q3 has no Q2 to compare with; the old sequential walk filed a 6-month change as Q3's.
    assert qoq == {"Q1'25": 10.0, "Q4'25": 10.0}


def test_qoq_crosses_the_year_boundary_and_handles_52_53_week_ends():
    rows = [
        _q("2025-09-27", "Q3", 2025, revenue=100.0),
        _q("2026-01-03", "Q4", 2025, revenue=120.0),   # spill: calendar Q4 2025
        _q("2026-04-04", "Q1", 2026, revenue=90.0),    # spill: calendar Q1 2026
    ]
    qoq = sbs._compute_qoq_for_records(rows, "revenue")
    assert qoq == {"Q4'25": 20.0, "Q1'26": -25.0}


@pytest.mark.parametrize("prev, cur", [(0.0, 5.0), (None, 5.0), (float("nan"), 5.0), (5.0, None)])
def test_growth_with_a_zero_missing_or_nan_base_is_skipped(prev, cur):
    rows = [_q("2024-09-30", "Q3", 2024, revenue=prev), _q("2025-09-30", "Q3", 2025, revenue=cur)]
    assert sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=True) == {}


def test_malformed_and_undated_rows_never_crash_or_invent_a_key():
    rows = [None, "x", 7, {"period": "Q1", "revenue": 5.0},
            _q("2024-09-30", "Q3", 2024, revenue=100.0),
            _q("2025-09-30", "Q3", 2025, revenue=150.0)]
    assert sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=True) == {"Q3'25": 50.0}
    assert sbs._compute_qoq_for_records(rows, "revenue") == {}


# ── One vote per company per calendar quarter ────────────────────────────────────────

def test_two_rows_in_one_calendar_quarter_count_once_newest_wins():
    # A fiscal-year-end change leaves a stub period ending May 31 next to the Jun 30
    # quarter; FMP also repeats rows now and then. Both used to vote in the median.
    rows = [
        {"date": "2025-05-31", "period": "Q4", "grossProfitMargin": 0.30},
        {"date": "2025-06-30", "period": "Q1", "grossProfitMargin": 0.50},
        {"date": "2025-06-30", "period": "Q1", "grossProfitMargin": 0.50},
    ]
    data = [{"ratios_quarterly": list(reversed(rows)), "ratios_annual": []}]  # FMP: newest first
    pooled = _svc()._collect_metric_values(data, _metric("gross_margin"), "quarterly")
    assert pooled == {"Q2'25": [0.50]}


def test_computed_ratio_joins_statements_by_calendar_quarter():
    # fcf_margin joins cash flow to income; rows of one quarter meet on the calendar key.
    income = [_q(d, p, fy, revenue=100.0) for d, p, fy in MSFT]
    cash = [_q(d, p, fy, freeCashFlow=25.0) for d, p, fy in MSFT]
    data = [{"income_quarterly": income, "cashflow_quarterly": cash}]
    pooled = sbs._compute_ratio_values(data, "fcf_margin", "quarterly")
    assert pooled == {pl.calendar_quarter_label({"date": d}): [0.25] for d, _p, _fy in MSFT}
    assert "Q1'26" not in pooled          # the fiscal label of the Jul-Sep 2025 quarter


# ── Storage: quarterly rows go under the calendar-quarter period_type ───────────────

@pytest.mark.asyncio
async def test_industry_rows_are_stored_under_the_calendar_quarter_period_type(monkeypatch):
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._sb = _svc()
    companies = []
    for ticker, cal in _COMPANIES.items():
        rows = [_q(d, p, fy, grossProfitMargin=0.5, revenue=100.0) for d, p, fy in cal]
        companies.append({
            "income_quarterly": rows, "ratios_quarterly": rows,
            "income_annual": [], "ratios_annual": [],
        })

    async def fake_fetch(_tickers, _al, _ql):
        return companies + companies[:1]   # 5 companies → clears MIN_SAMPLE_SIZE somewhere

    monkeypatch.setattr(svc, "_fetch_batched", fake_fetch)
    values = await svc._industry_value_lists([("T", 1.0)], 1, 1)

    period_types = {pt for (_m, pt, _label) in values}
    assert CALENDAR_QUARTER_PERIOD_TYPE in period_types
    assert "quarterly" not in period_types, "a fiscal-keyed 'quarterly' row would be written"
    assert all(_LABEL_RE.match(label) for (_m, pt, label) in values
               if pt == CALENDAR_QUARTER_PERIOD_TYPE)

    rows = svc._rows_from_values("Technology", "Software", values, "2026-09-30T00:00:00+00:00")
    q3 = [r for r in rows if r["metric_name"] == "gross_margin" and r["period_label"] == "Q3'25"]
    assert q3 and q3[0]["period_type"] == CALENDAR_QUARTER_PERIOD_TYPE
    assert q3[0]["sample_size"] == 5


def test_the_retired_sector_path_maps_to_the_same_period_types():
    assert sbs.STORED_PERIOD_TYPE == {"annual": "annual", "quarterly": CALENDAR_QUARTER_PERIOD_TYPE}


# ── Code-before-migration: a refused period_type degrades, it doesn't abort ─────────

_CHECK_MSG = ('new row for relation "sector_benchmarks" violates check constraint '
              '"sector_benchmarks_period_type_check"')


class _Table:
    def __init__(self, db):
        self.db = db

    def upsert(self, batch, on_conflict):
        self.batch = batch
        return self

    def execute(self):
        self.db.calls.append([r["period_type"] for r in self.batch])
        if self.db.fail is not None and any(
            r["period_type"] == CALENDAR_QUARTER_PERIOD_TYPE for r in self.batch
        ):
            raise self.db.fail
        bad = [r for r in self.batch if r["period_type"] not in self.db.allowed]
        if bad:
            raise APIError({"code": "23514", "message": _CHECK_MSG,
                            "details": "Failing row contains (...).", "hint": None})
        self.db.rows.extend(self.batch)
        return self


class _DB:
    def __init__(self, allowed, fail=None):
        self.allowed, self.fail = set(allowed), fail
        self.rows, self.calls = [], []

    def table(self, _name):
        return _Table(self)


def _rows(n_annual=3, n_cq=3):
    mk = lambda pt, i: {"sector": "S", "industry": "", "metric_name": "gross_margin",
                        "period_type": pt, "period_label": str(i), "median_value": 1.0,
                        "sample_size": 5, "computed_at": "now"}
    # Interleaved, the way `_rows_from_values` emits them.
    out = []
    for i in range(max(n_annual, n_cq)):
        if i < n_annual:
            out.append(mk("annual", i))
        if i < n_cq:
            out.append(mk(CALENDAR_QUARTER_PERIOD_TYPE, i))
    return out


def _industry_svc(db):
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc.supabase = db
    svc._calendar_quarter_blocked = False
    return svc


_PRE_184 = {"annual", "quarterly", "ttm"}
_POST_184 = _PRE_184 | {CALENDAR_QUARTER_PERIOD_TYPE}


def test_without_migration_184_annual_rows_still_land_and_the_run_says_so(caplog):
    db = _DB(_PRE_184)
    svc = _industry_svc(db)
    with caplog.at_level(logging.ERROR, logger=ibs.logger.name):
        written = svc._upsert(_rows())
    assert written == 3
    assert {r["period_type"] for r in db.rows} == {"annual"}
    assert svc._calendar_quarter_blocked is True
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "184" in errors[0].getMessage()

    # The rest of the run does not hammer the refusing table with calendar-quarter rows.
    db.calls.clear()
    assert svc._upsert(_rows()) == 3
    assert all(CALENDAR_QUARTER_PERIOD_TYPE not in call for call in db.calls)


def test_with_migration_184_every_row_is_written():
    db = _DB(_POST_184)
    svc = _industry_svc(db)
    assert svc._upsert(_rows()) == 6
    assert svc._calendar_quarter_blocked is False
    # Calendar-quarter rows go last, in their own batches.
    assert db.calls[0] == ["annual"] * 3
    assert db.calls[-1] == [CALENDAR_QUARTER_PERIOD_TYPE] * 3


@pytest.mark.parametrize("failure", [
    APIError({"code": "23505", "message": "duplicate key", "details": None, "hint": None}),
    APIError({"code": "23514", "message": 'violates check constraint "sector_benchmarks_sample_size_nonneg"',
              "details": None, "hint": None}),
    APIError({"code": 520, "message": "JSON could not be generated", "details": None, "hint": None}),
    RuntimeError("Server disconnected"),
])
def test_any_other_failure_still_aborts_the_sector(failure):
    svc = _industry_svc(_DB(_POST_184, fail=failure))
    with pytest.raises(type(failure)):
        svc._upsert(_rows())
    assert svc._calendar_quarter_blocked is False


@pytest.mark.asyncio
async def test_every_run_re_probes_after_the_migration_is_applied(monkeypatch):
    svc = _industry_svc(_DB(_POST_184))
    svc._calendar_quarter_blocked = True            # left over from a pre-184 run
    # A one-sector universe whose sector writes one row: an EMPTY universe, or a run that
    # writes nothing, raises `IndustryBenchmarkRecomputeSkipped`
    # (test_benchmark_empty_universe_unsettled.py, test_benchmark_nothing_written_unsettled.py).
    monkeypatch.setattr(svc, "_load_universe", lambda: [("Technology", [("Software", [("AAA", 1.0)])])])

    async def _one_row(*_a, **_k):
        return 1

    monkeypatch.setattr(svc, "_compute_sector", _one_row)
    summary = await svc.recompute_all()
    assert summary["calendar_quarter_blocked"] is False
    assert svc._calendar_quarter_blocked is False


def test_check_violation_classifier():
    named = APIError({"code": "23514", "message": _CHECK_MSG, "details": None, "hint": None})
    assert is_check_violation(named)
    assert is_check_violation(named, "sector_benchmarks_period_type_check")
    assert not is_check_violation(named, "some_other_check")
    # An int code is an edge/origin status, never a Postgres SQLSTATE.
    assert not is_check_violation(APIError({"code": 23514, "message": _CHECK_MSG}))
    try:
        try:
            raise named
        except APIError as inner:
            raise RuntimeError("upsert failed") from inner
    except RuntimeError as wrapped:
        assert is_check_violation(wrapped, "sector_benchmarks_period_type_check")
