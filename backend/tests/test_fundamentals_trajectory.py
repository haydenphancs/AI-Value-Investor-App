"""Deterministic trajectory digest fed to the section "✨ Insight" prompt.

The digest is built purely from the per-metric history already frozen into the
report shell. These cases pin direction, vs-peer stance, gap widening/narrowing,
formatting, and the degraded paths.
"""

from app.services.agents.narrative_prompts import (
    _clean_metric_label,
    _fmt_trend_value,
    _fundamentals_trajectory_block,
    _metric_trajectory_line,
    _trend_direction,
)


def _pts(*pairs):
    return [{"period": p, "value": v} for (p, v) in pairs]


def test_fmt_and_direction_and_label():
    assert _fmt_trend_value(18.0, "percent") == "18%"
    assert _fmt_trend_value(5.2, "percent") == "5.2%"
    assert _fmt_trend_value(26.5, "x") == "26.5×"
    assert _fmt_trend_value(1.8, "score") == "1.8"

    assert _trend_direction(18, 25) == "rising"
    assert _trend_direction(30, 17) == "falling"
    assert _trend_direction(20, 20.5) == "flat"   # <5% relative
    assert _trend_direction(0, 3) == "rising"

    assert _clean_metric_label("Net Margin (1.6x sector avg 15.0%)") == "Net Margin"
    assert _clean_metric_label("Return on Equity (ROE)") == "Return on Equity"
    assert _clean_metric_label("Revenue Growth (YoY)") == "Revenue Growth"
    assert _clean_metric_label("Operating Income Growth") == "Operating Income Growth"


def test_rising_metric_above_industry_gap_widening():
    m = {
        "label": "Net Margin (1.6x sector avg 15.0%)",
        "history_unit": "percent",
        "annual_history": _pts(("2021", 18.0), ("2022", 20.0), ("2023", 22.0), ("2024", 25.0)),
        "sector_annual_history": _pts(("2021", 15.0), ("2024", 15.0)),
    }
    line = _metric_trajectory_line(m, "industry")
    assert line == "Net Margin 18%→25% over 3y (rising), above industry 15% (gap widening)"


def test_falling_metric_below_industry_gap_narrowing():
    m = {
        "label": "P/E",
        "history_unit": "x",
        "annual_history": _pts(("2022", 40.0), ("2023", 30.0), ("2024", 26.0)),
        "sector_annual_history": _pts(("2022", 30.0), ("2024", 28.0)),
    }
    line = _metric_trajectory_line(m, "industry")
    # 40→26 falling; now 26 below sector 28; gap |26-28|=2 < |40-30|=10*0.85 → narrowing
    assert line == "P/E 40.0×→26.0× over 2y (falling), below industry 28.0× (gap narrowing)"


def test_no_sector_series_company_only():
    m = {
        "label": "Revenue Growth (YoY)",
        "history_unit": "percent",
        "annual_history": _pts(("2021", 30.0), ("2024", 17.0)),
    }
    assert _metric_trajectory_line(m, "industry") == "Revenue Growth 30%→17% over 3y (falling)"


def test_in_line_with_peer():
    m = {
        "label": "ROA",
        "history_unit": "percent",
        "annual_history": _pts(("2023", 10.0), ("2024", 15.1)),
        "sector_annual_history": _pts(("2024", 15.0)),
    }
    line = _metric_trajectory_line(m, "sector")
    assert "in line with sector 15%" in line


def test_altman_z_score_unit_no_peer():
    m = {
        "label": "Altman Z-Score",
        "history_unit": "score",
        "annual_history": _pts(("2021", 2.5), ("2024", 1.8)),
    }
    assert _metric_trajectory_line(m, "sector") == "Altman Z-Score 2.5→1.8 over 3y (falling)"


def test_too_few_points_skipped():
    m = {"label": "ROE", "history_unit": "percent", "annual_history": _pts(("2024", 40.0))}
    assert _metric_trajectory_line(m, "industry") is None
    assert _metric_trajectory_line({"label": "X", "annual_history": []}, "industry") is None


def test_full_block_groups_by_card_and_skips_empty():
    shell = {
        "fundamental_metrics": [
            {
                "title": "Profitability",
                "peer_group_level": "industry",
                "metrics": [
                    {"label": "Net Margin (x)", "history_unit": "percent",
                     "annual_history": _pts(("2021", 18.0), ("2024", 25.0))},
                    {"label": "ROE", "history_unit": "percent",
                     "annual_history": _pts(("2024", 40.0))},  # skipped (<2)
                ],
            },
            {  # all metrics skipped → card omitted entirely
                "title": "Growth",
                "peer_group_level": "industry",
                "metrics": [{"label": "EPS Growth", "history_unit": "percent",
                             "annual_history": _pts(("2024", 5.0))}],
            },
        ]
    }
    block = _fundamentals_trajectory_block(shell)
    assert block.startswith("TRAJECTORY (recent multi-year path, company vs peer):")
    assert "Profitability — Net Margin 18%→25% over 3y (rising)." in block
    assert "Growth" not in block  # no chartable metric → not listed
    assert "ROE" not in block


def test_empty_shell_returns_blank():
    assert _fundamentals_trajectory_block({}) == ""
    assert _fundamentals_trajectory_block({"fundamental_metrics": []}) == ""
