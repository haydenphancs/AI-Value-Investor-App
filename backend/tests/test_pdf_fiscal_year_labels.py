"""
Fiscal-year labels on the PDF's Future Forecast (2026-10-24).

The Earnings Timeline's columns are FISCAL years, so Planet Labs' "2027" (Feb 2026 – Jan 2027)
read as a calendar year still a quarter away while it was the year in progress. An off-calendar
filer's columns now read "FY2027", its forecast rows carry their months, and a caption names the
year end; a December filer, the curated projections and reports without `period_end` keep bare
years. The helpers mirror iOS `FiscalYearLabel` (Models/TickerReportModels.swift), so the cases
below are the ones that iOS logic was checked against.
"""

import copy

import pytest

from app.services.pdf_report_service import build_context, render_html
from app.utils.period_labels import (
    fiscal_year_months,
    fiscal_year_note,
    is_off_calendar_fiscal_year,
    nominal_fiscal_year_end,
)


# ── nominal_fiscal_year_end ───────────────────────────────────────────────────

@pytest.mark.parametrize("period_end, expected", [
    ("2027-01-31", (2027, 1)),            # Planet Labs
    ("2026-02-01", (2026, 1)),            # Home Depot: a first-week close names the month before
    ("2022-01-01", (2021, 12)),           # a December 52/53-week filer stays December
    ("2021-12-25", (2021, 12)),
    ("2025-09-27", (2025, 9)),            # Apple
    ("2026-10-03", (2026, 9)),            # a Sep-ish 52/53-week close
    ("2026-02-08", (2026, 2)),            # day 8 is past the spill window
    ("2027-01-31T00:00:00", (2027, 1)),   # a timestamp suffix is ignored
])
def test_nominal_end_names_the_month_the_company_does(period_end, expected):
    assert nominal_fiscal_year_end(period_end) == expected


@pytest.mark.parametrize("bad", [
    None, "", "2025-01", "2025-13-01", "2025-01-00", "2025-02-30", "abcd-ef-gh", "20250131xx",
    20270131, ["2027-01-31"],
])
def test_nominal_end_refuses_unusable_input(bad):
    assert nominal_fiscal_year_end(bad) is None


# ── is_off_calendar_fiscal_year ───────────────────────────────────────────────

@pytest.mark.parametrize("ends, expected", [
    (["2025-01-31", "2026-01-31", "2027-01-31"], True),     # Planet Labs
    (["2024-12-31", "2025-12-31"], False),                   # December filer
    (["2021-12-25", "2022-12-31", "2024-01-01"], False),     # December 52/53-week filer
    (["2025-01-31", None, "2027-01-31"], False),             # one column without a period end
    (["2025-01-31", "bad"], False),
    ([], False),
    ([None, None], False),
    (["2023-06-30", "2024-12-31"], False),                   # moved Jun → Dec: the latest rules
    (["2023-12-31", "2024-09-30"], True),                    # moved Dec → Sep
])
def test_off_calendar_needs_every_end_and_reads_the_latest(ends, expected):
    assert is_off_calendar_fiscal_year(ends) is expected


def test_off_calendar_accepts_a_generator():
    assert is_off_calendar_fiscal_year(e for e in ["2026-01-31", "2027-01-31"]) is True


# ── fiscal_year_months ────────────────────────────────────────────────────────

@pytest.mark.parametrize("end, prev, expected", [
    ("2027-01-31", "2026-01-31", "Feb 2026 – Jan 2027"),   # Planet Labs FY2027
    ("2027-01-31", None, "Feb 2026 – Jan 2027"),           # first column: 12 months back
    ("2026-02-01", "2025-02-02", "Feb 2025 – Jan 2026"),   # Home Depot FY2025
    ("2026-09-26", "2025-09-27", "Oct 2025 – Sep 2026"),   # Apple FY2026
    ("2026-05-31", "2025-05-31", "Jun 2025 – May 2026"),   # Oracle
    ("2027-06-30", "2026-06-30", "Jul 2026 – Jun 2027"),   # Microsoft
    ("2024-12-31", "2024-06-30", "Jul 2024 – Dec 2024"),   # year-end change: the stub's months
    ("2024-12-31", "2024-11-30", "Dec 2024"),              # a one-month stub
    ("2024-01-31", "2022-01-31", "Feb 2023 – Jan 2024"),   # gap year → 12 months
    ("2024-01-31", "2025-01-31", "Feb 2023 – Jan 2024"),   # previous after this one → 12 months
    ("2027-01-31", "bad", "Feb 2026 – Jan 2027"),
    ("2025-12-31", "2024-12-31", "Jan 2025 – Dec 2025"),
])
def test_months_span_the_fiscal_year(end, prev, expected):
    assert fiscal_year_months(end, prev) == expected


def test_months_without_an_end_is_none():
    assert fiscal_year_months(None, "2026-01-31") is None
    assert fiscal_year_months("garbage") is None


# ── fiscal_year_note ──────────────────────────────────────────────────────────

def _row(period, end, forecast, **extra):
    """An `annual_timeline` row with every key `_build_annual_timeline` writes (the template
    renders under StrictUndefined, so a partial row would not be a real report)."""
    return {
        "period": period, "period_end": end, "is_forecast": forecast,
        "revenue": 0.0, "revenue_label": "N/A", "revenue_yoy_pct": None,
        "eps": 0.0, "eps_label": "N/A", "eps_yoy_pct": None,
        "eps_basis": "consensus" if forecast else "gaap",
        "revenue_analyst_count": None, "eps_analyst_count": None,
        **extra,
    }


def _planet_timeline():
    return [
        _row("2025", "2025-01-31", False, revenue=244.35, revenue_label="$244.4M"),
        _row("2026", "2026-01-31", False, revenue=308.0, revenue_label="$308.0M"),
        _row("2027", "2027-01-31", True, revenue=438.0, revenue_label="$438.0M",
             revenue_yoy_pct=42.2, eps_label="-$0.05", eps_analyst_count=5),
        _row("2028", "2028-01-31", True, revenue=571.0, revenue_label="$571.0M"),
    ]


def test_note_anchors_on_the_year_in_progress():
    assert fiscal_year_note(_planet_timeline()) == (
        "Fiscal years end in January: FY2027 runs Feb 2026 – Jan 2027."
    )


def test_note_without_forecasts_uses_the_latest_column():
    rows = [_row("2025", "2025-09-27", False), _row("2026", "2026-09-26", False)]
    assert fiscal_year_note(rows) == (
        "Fiscal years end in September: FY2026 runs Oct 2025 – Sep 2026."
    )


def test_note_with_a_forecast_first_column():
    assert fiscal_year_note([_row("2027", "2027-01-31", True)]) == (
        "Fiscal years end in January: FY2027 runs Feb 2026 – Jan 2027."
    )


@pytest.mark.parametrize("rows", [
    [_row("2025", "2025-12-31", False), _row("2026", "2026-12-31", True)],   # December filer
    [_row("2025", None, False), _row("2026", None, True)],                   # older report
    [],
    None,
    [_row("2026", "2026-01-31", False), _row("FY27", "2027-01-31", True)],   # non-numeric period
    ["not a row", 7],
])
def test_note_is_none_when_it_cannot_be_honest(rows):
    assert fiscal_year_note(rows) is None


# ── The PDF ───────────────────────────────────────────────────────────────────

def _report(timeline, **forecast):
    return {
        "symbol": "PL",
        "company_name": "Planet Labs PBC",
        "revenue_forecast": {"annual_timeline": timeline, "cagr": 24.0, **forecast},
    }


def test_pdf_labels_an_off_calendar_filer_by_fiscal_year():
    ctx = build_context(_report(_planet_timeline()), None)
    fc = ctx["forecast"]
    assert fc["fiscal_year_note"] == "Fiscal years end in January: FY2027 runs Feb 2026 – Jan 2027."
    assert [(p["period_label"], p["months"]) for p in fc["projections"]] == [
        ("FY2027", "Feb 2026 – Jan 2027"),   # its months open after the last ACTUAL year
        ("FY2028", "Feb 2027 – Jan 2028"),
    ]
    svg = ctx["charts"]["earnings_timeline"]
    for label in ("FY2025", "FY2026", "FY2027", "FY2028"):
        assert f">{label}</text>" in svg

    html = render_html(ctx)
    assert "Fiscal years end in January: FY2027 runs Feb 2026 – Jan 2027." in html
    assert "FY2027 <span class=\"muted\">· Feb 2026 – Jan 2027</span>" in html


def test_pdf_leaves_a_december_filer_as_it_was():
    rows = [
        _row("2025", "2025-12-31", False, revenue=10.0, revenue_label="$10.0B"),
        _row("2026", "2026-12-31", True, revenue=12.0, revenue_label="$12.0B"),
        _row("2027", "2027-12-31", True, revenue=14.0, revenue_label="$14.0B"),
    ]
    ctx = build_context(_report(rows), None)
    fc = ctx["forecast"]
    assert fc["fiscal_year_note"] == ""
    assert all("period_label" not in p and "months" not in p for p in fc["projections"])
    svg = ctx["charts"]["earnings_timeline"]
    assert ">2026</text>" in svg and "FY" not in svg
    html = render_html(ctx)
    assert "Fiscal years end in" not in html
    assert "<td class=\"name\">2026 <span class=\"muted\">(est)</span></td>" in html


def test_pdf_report_without_period_ends_keeps_bare_years():
    rows = [{k: v for k, v in r.items() if k != "period_end"} for r in _planet_timeline()]
    ctx = build_context(_report(rows), None)
    assert ctx["forecast"]["fiscal_year_note"] == ""
    assert "FY" not in ctx["charts"]["earnings_timeline"]
    assert "Fiscal years end in" not in render_html(ctx)


def test_pdf_curated_projections_fallback_is_untouched():
    projections = [
        {"period": "2027", "revenue": 438.0, "revenue_label": "$438.0M", "is_forecast": True},
        {"period": "2028", "revenue": 571.0, "revenue_label": "$571.0M", "is_forecast": True},
    ]
    report = {"symbol": "PL", "revenue_forecast": {"projections": projections}}
    ctx = build_context(report, None)
    assert ctx["forecast"]["fiscal_year_note"] == ""
    assert ctx["forecast"]["projections"] == projections
    assert "FY" not in ctx["charts"]["earnings_timeline"]


def test_pdf_months_open_after_the_previous_column_on_a_year_end_change():
    # Dec → Sep: the transition year is nine months. Months counted 12 back from the end
    # ("Oct 2023 – Sep 2024") would overlap the reported 2023 year.
    rows = [
        _row("2023", "2023-12-31", False, revenue=1.0, revenue_label="$1.0B"),
        _row("2024", "2024-09-30", True, revenue=1.1, revenue_label="$1.1B"),
        _row("2025", "2025-09-30", True, revenue=1.2, revenue_label="$1.2B"),
    ]
    ctx = build_context(_report(rows), None)
    assert [(p["period_label"], p["months"]) for p in ctx["forecast"]["projections"]] == [
        ("FY2024", "Jan 2024 – Sep 2024"),
        ("FY2025", "Oct 2024 – Sep 2025"),
    ]
    assert ctx["forecast"]["fiscal_year_note"] == (
        "Fiscal years end in September: FY2024 runs Jan 2024 – Sep 2024."
    )


def test_pdf_never_mutates_the_frozen_report():
    report = _report(_planet_timeline())
    before = copy.deepcopy(report)
    build_context(report, None)
    assert report == before


def test_pdf_skips_malformed_timeline_rows():
    rows = ["junk", None, *_planet_timeline(), 42]
    ctx = build_context(_report(rows), None)
    assert ctx["forecast"]["fiscal_year_note"] == (
        "Fiscal years end in January: FY2027 runs Feb 2026 – Jan 2027."
    )
    assert [p["period_label"] for p in ctx["forecast"]["projections"]] == ["FY2027", "FY2028"]


def test_pdf_one_row_without_a_period_end_turns_the_labels_off():
    rows = _planet_timeline()
    rows[1] = {k: v for k, v in rows[1].items() if k != "period_end"}
    ctx = build_context(_report(rows), None)
    assert ctx["forecast"]["fiscal_year_note"] == ""
    assert "FY" not in ctx["charts"]["earnings_timeline"]
