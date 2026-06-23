"""PDF growth chart + earnings-timeline sign-safety (detailed-analysis PDF).

The PDF is a separate server-side SVG renderer (WeasyPrint) from the iOS charts.
These pin the parity work:
  • growth_bars_line — the new Growth card mirror (sign-aware bars + YoY/sector
    overlay lines, broken at nil gaps).
  • bars_actuals_forecast — the earnings-timeline must not draw broken
    negative-height bars for a loss-making company (the same bug class as the iOS
    GrowthChartView fix).
Pure data -> SVG transforms; no network, no WeasyPrint rasterization needed.
"""

from app.services import pdf_charts as pc
from app.services import pdf_report_service as svc


def _valid_svg(s: str) -> None:
    """An SVG fragment must be well-formed and carry no broken/illegal geometry."""
    assert s.startswith("<svg") and s.endswith("</svg>")
    # A negative-height rect = the loss-maker clipping/inversion bug.
    assert 'height="-' not in s
    # Non-finite must never leak into coordinates.
    assert "nan" not in s.lower() and "inf" not in s.lower()


# ── growth_bars_line ───────────────────────────────────────────────────────────

def test_growth_chart_empty_and_single_point_render_nothing():
    assert pc.growth_bars_line([]) == ""
    assert pc.growth_bars_line([{"label": "2024", "value": 1e8}]) == ""


def test_growth_chart_all_negative_series_renders_without_crash():
    """The iOS crash case (all-negative → inverted domain). The PDF must still
    produce a valid chart with downward red bars, not an empty/garbled frame."""
    items = [{"label": str(y), "value": -(1e8 * (i + 1))}
             for i, y in enumerate((2021, 2022, 2023))]
    svg = pc.growth_bars_line(items)
    _valid_svg(svg)
    assert pc._RED in svg  # loss bars colored red


def test_growth_chart_mixed_sign_and_nil_overlays():
    items = [
        {"label": "2021", "value": 5e8, "yoy": 33.0, "sector": 22.0},
        {"label": "2022", "value": -3e8, "yoy": None, "sector": 12.0},  # loss yr, nil YoY
        {"label": "2023", "value": 4e8, "yoy": None, "sector": None},    # nil both
        {"label": "2024", "value": 7e8, "yoy": 75.0, "sector": 15.0},
    ]
    svg = pc.growth_bars_line(items)
    _valid_svg(svg)
    assert pc.ACCENT in svg and pc._RED in svg  # positive bars blue, loss bar red


def test_growth_chart_skips_non_finite_values():
    items = [
        {"label": "a", "value": float("nan"), "yoy": 1.0},
        {"label": "b", "value": 2.0, "yoy": 1.0},
        {"label": "c", "value": 3.0, "yoy": 2.0},
    ]
    _valid_svg(pc.growth_bars_line(items))


def test_growth_chart_breaks_line_at_nil_gap():
    """A contiguous YoY run draws ONE polyline; a gap splits it so no path bridges
    the undefined period. With sector all-nil, the only <path> elements are the
    YoY line segments, so the count cleanly reflects the break."""
    contiguous = [
        {"label": "2022", "value": 1e8, "yoy": 10.0, "sector": None},
        {"label": "2023", "value": 2e8, "yoy": 20.0, "sector": None},
        {"label": "2024", "value": 3e8, "yoy": 30.0, "sector": None},
    ]
    gapped = [
        {"label": "2022", "value": 1e8, "yoy": 10.0, "sector": None},
        {"label": "2023", "value": 2e8, "yoy": None, "sector": None},  # gap
        {"label": "2024", "value": 3e8, "yoy": 30.0, "sector": None},
    ]
    # Contiguous → one connecting path; gapped → two isolated points, no path.
    assert pc.growth_bars_line(contiguous).count("<path") == 1
    assert pc.growth_bars_line(gapped).count("<path") == 0


# ── bars_actuals_forecast (earnings timeline) sign-safety ───────────────────────

def test_earnings_timeline_negative_eps_no_broken_bar():
    items = [
        {"label": "FY22", "value": -1.5, "is_forecast": False, "value_label": "-1.5"},
        {"label": "FY23", "value": 2.0, "is_forecast": False, "value_label": "2.0"},
        {"label": "FY24", "value": 3.5, "is_forecast": True, "value_label": "3.5"},
    ]
    svg = pc.bars_actuals_forecast(items)
    _valid_svg(svg)
    assert pc._RED in svg  # the loss year draws red, downward


def test_earnings_timeline_all_negative_renders():
    items = [
        {"label": "FY22", "value": -2.0, "is_forecast": False},
        {"label": "FY23", "value": -1.0, "is_forecast": False},
    ]
    _valid_svg(pc.bars_actuals_forecast(items))


# ── build_context wiring ────────────────────────────────────────────────────────

def test_build_context_wires_growth_chart_from_revenue_annual():
    data = {
        "symbol": "TEST", "agent": {"name": "Warren Buffett"},
        "growth_chart": {
            "symbol": "TEST",
            "revenue_annual": [
                {"period": "2022", "value": 4e8, "yoy_change_percent": 5.0, "sector_average_yoy": 4.0},
                {"period": "2023", "value": -3e8, "yoy_change_percent": None, "sector_average_yoy": None},
                {"period": "2024", "value": 7e8, "yoy_change_percent": 75.0, "sector_average_yoy": 6.0},
            ],
        },
    }
    ctx = svc.build_context(data)
    assert ctx["growth_metric_label"] == "Revenue"
    _valid_svg(ctx["charts"]["growth"])
    html = svc.render_html(ctx)
    assert "Revenue Growth — Annual" in html
    assert ctx["charts"]["growth"][:40] in html  # SVG actually embedded


def test_build_context_legacy_report_without_growth_chart_hides_block():
    ctx = svc.build_context({"symbol": "OLD", "agent": {"name": "x"}})
    assert ctx["charts"]["growth"] == ""
    assert "Revenue Growth — Annual" not in svc.render_html(ctx)
