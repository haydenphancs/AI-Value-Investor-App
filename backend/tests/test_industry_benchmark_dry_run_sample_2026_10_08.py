"""`IndustryBenchmarkService._log_sample` — the medians a `--dry-run` prints.

A `--ttm --dry-run` (the validation step before uploading a rebuilt universe, 2026-10-08)
printed "Median at each metric's most-sampled annual year:" and then nothing: the sample
kept annual rows only, and a TTM run writes only `ttm` rows.
"""
import logging

from app.services.industry_benchmark_service import IndustryBenchmarkService, logger


def _row(metric, period_type, label, median, n):
    return {
        "metric_name": metric, "period_type": period_type, "period_label": label,
        "median_value": median, "sample_size": n,
    }


def _sample_lines(caplog, rows):
    with caplog.at_level(logging.INFO, logger=logger.name):
        IndustryBenchmarkService._log_sample("TTM Technology/Software", rows)
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("    ")]


def test_a_ttm_run_logs_its_medians(caplog):
    lines = _sample_lines(caplog, [
        _row("pe_ratio", "ttm", "TTM", 31.2, 64),
        _row("gross_margin", "ttm", "TTM", 0.71, 70),
    ])
    assert any("pe_ratio" in l and "TTM" in l and "31.2" in l and "n=64" in l for l in lines)
    assert any("gross_margin" in l and "0.71" in l for l in lines)


def test_an_annual_run_still_picks_the_most_sampled_year(caplog):
    lines = _sample_lines(caplog, [
        _row("roe", "annual", "2025", 0.12, 60),
        _row("roe", "annual", "2026", 0.30, 9),      # thin partial year: not shown
        _row("roe", "quarterly", "Q2 2026", 0.05, 70),
    ])
    assert lines == [l for l in lines if "roe" in l] and len(lines) == 1
    assert "2025" in lines[0] and "n=60" in lines[0]


def test_quarterly_rows_alone_print_no_sample(caplog):
    assert _sample_lines(caplog, [_row("roe", "quarterly", "Q2 2026", 0.05, 70)]) == []


def test_the_sample_names_every_served_multiple(caplog):
    """P/FCF, EV/EBITDA, earnings yield and D/E are served on the cards, so a dry run must
    show their n too (a lenders-only Credit Services P/FCF sat at n=25 of a floor of 20)."""
    rows = [_row(m, "ttm", "TTM", 1.0, 25)
            for m in ("pfcf_ratio", "ev_ebitda", "earnings_yield", "debt_to_equity")]
    lines = _sample_lines(caplog, rows)
    for m in ("pfcf_ratio", "ev_ebitda", "earnings_yield", "debt_to_equity"):
        assert any(m in l and "n=25" in l for l in lines), m
