"""
PDF detailed-analysis report service.

Renders a finished report's frozen ``ticker_report_data`` JSONB into a
professional multi-page PDF and stores it in Supabase Storage. No new LLM/FMP
calls — it is a pure render of data that already exists, so it is cheap and
deterministic.

Pipeline:  build_context()  ->  render_html() (Jinja2)  ->  render_pdf_bytes()
           (WeasyPrint, lazy import)  ->  upload to the private ``research-pdfs``
           bucket.

WeasyPrint is imported lazily INSIDE ``render_pdf_bytes`` so a missing native lib
(cairo/pango) degrades to a caught failure (``pdf_status='failed'``) instead of
crashing app boot. The CPU-bound render + the sync Storage upload are pushed to
``asyncio.to_thread`` so the shared event loop never stalls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import pdf_charts

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "pdf"
_BUCKET = "research-pdfs"

_jinja: Optional[Environment] = None


def _env() -> Environment:
    global _jinja
    if _jinja is None:
        _jinja = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _jinja


# Vital key -> display label, in render order. Keys come from the internal
# `_scoring_inputs` dict that drives the headline quality score (never sent to iOS).
_VITAL_LABELS: list[tuple[str, str]] = [
    ("valuation", "Valuation"),
    ("financial_health", "Financial Health"),
    ("revenue", "Revenue Quality"),
    ("forecast", "Forecast"),
    ("moat", "Competitive Moat"),
    ("wall_street", "Wall Street"),
    ("insider", "Insider Activity"),
    ("capital_allocation", "Capital Allocation"),
    ("macro", "Macro Resilience"),
]


# Analyst-rating legend order + colours (match analyst_consensus_stacked_bar).
_CONSENSUS_ORDER: list[tuple[str, str, str]] = [
    ("strong_buy", "Strong Buy", "#1E3A8A"),
    ("buy", "Buy", "#60A5FA"),
    ("hold", "Hold", "#F59E0B"),
    ("sell", "Sell", "#F87171"),
    ("strong_sell", "Strong Sell", "#B91C1C"),
]


# Persona key/name -> "<Surname> Agent" display name (matches iOS agent tags).
_PERSONA_DISPLAY: dict[str, str] = {
    "warren_buffett": "Buffett Agent",
    "buffett": "Buffett Agent",
    "cathie_wood": "Wood Agent",
    "wood": "Wood Agent",
    "peter_lynch": "Lynch Agent",
    "lynch": "Lynch Agent",
    "bill_ackman": "Ackman Agent",
    "ackman": "Ackman Agent",
    "michael_burry": "Burry Agent",
    "burry": "Burry Agent",
}


def _persona_display(agent: dict) -> str:
    """Map a persona to its '<Surname> Agent' display name."""
    key = str(agent.get("key") or "").strip().lower()
    if key in _PERSONA_DISPLAY:
        return _PERSONA_DISPLAY[key]
    name = str(agent.get("name") or "").strip()
    if not name:
        return "Cay AI Agent"
    low = name.lower()
    for k, v in _PERSONA_DISPLAY.items():
        if v.split()[0].lower() in low:
            return v
    last = name.split()[-1]
    return name if last.lower() == "agent" else f"{last} Agent"


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_MONTH_ABBR = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_month_label(mk: Any) -> str:
    """Smart-money bucket key 'MM/YYYY' -> "Mon 'YY" (e.g. '06/2025' -> "Jun '25").
    Returns the raw value unchanged if it isn't in the expected format."""
    s = str(mk or "").strip()
    if "/" in s:
        mm, _, yyyy = s.partition("/")
        try:
            mi = int(mm)
        except ValueError:
            return s
        if 1 <= mi <= 12 and len(yyyy) >= 2:
            return f"{_MONTH_ABBR[mi]} '{yyyy[-2:]}"
    return s


def _fmt_date_label(d: Any) -> str:
    """ISO date 'YYYY-MM-DD' -> "Mon 'YY" (e.g. '2025-06-15' -> "Jun '25").
    Returns the raw value unchanged if it isn't in the expected format."""
    s = str(d or "").strip()
    parts = s.split("-")
    if len(parts) >= 2 and len(parts[0]) == 4:
        try:
            mi = int(parts[1])
        except ValueError:
            return s
        if 1 <= mi <= 12:
            return f"{_MONTH_ABBR[mi]} '{parts[0][-2:]}"
    return s


def _fmt_owner_pct(v: Any) -> str:
    """Significant-figure ownership-% label matching the iOS officer column
    (e.g. 0.43 -> '0.43%', 0.0083 -> '0.0083%')."""
    n = _num(v)
    if n is None:
        return ""
    if n >= 10:
        return f"{n:.1f}%"
    if n >= 0.1:
        return f"{n:.2f}%"
    if n >= 0.01:
        return f"{n:.3f}%"
    if n >= 0.001:
        return f"{n:.4f}%"
    return "<0.001%"


def _fmt_amount(v: Any, money: bool = False) -> str:
    """Compact magnitude label with K/M/B units (e.g. 124000 -> '124K';
    1_840_000 with money -> '$1.8M'). Returns '—' for None."""
    n = _num(v)
    if n is None:
        return "—"
    a = abs(n)
    pre = "$" if money else ""
    if a >= 1e9:
        return f"{pre}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{pre}{a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{pre}{a / 1e3:.0f}K"
    return f"{pre}{a:.0f}"


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _project_market_dynamics(md: dict) -> dict:
    """Mirror the iOS today-aligned TAM projection so the PDF matches the Deep
    Dive. Phase-A (Census/FRED) TAM is often a year or two stale (e.g. Census
    2023); iOS bumps the current year to today, preserves the (future - current)
    span, and grows both TAM values by the source CAGR. Replicated here so the
    report's TAM years/values line up with what the app shows."""
    md = dict(md or {})
    src = _to_int(md.get("current_year"))
    if src is None:
        return md
    try:
        today = datetime.now(timezone.utc).year
    except Exception:
        return md
    fut = _to_int(md.get("future_year"))
    cagr = _num(md.get("cagr_5yr"))
    years = max(0, today - src)
    mult = (1.0 + cagr / 100.0) ** years if (years > 0 and cagr is not None) else 1.0
    disp_cur = today if years > 0 else src
    md["current_year"] = str(disp_cur)
    if fut is not None:
        md["future_year"] = str(disp_cur + (fut - src))
    cur_tam = _num(md.get("current_tam"))
    fut_tam = _num(md.get("future_tam"))
    # Keep the RAW pre-projection figure + year for source attribution, so the
    # PDF can cite the actual number we calculate from (e.g. Census/FRED 2023).
    md["source_tam"] = cur_tam
    md["source_year"] = str(src)
    md["projected"] = years > 0
    if cur_tam is not None:
        md["current_tam"] = cur_tam * mult
    if fut_tam is not None:
        md["future_tam"] = fut_tam * mult
    return md


def build_context(
    data: dict,
    fair_value_estimate: Optional[float] = None,
) -> dict:
    """Flatten the frozen report JSONB into a flat, template-friendly context and
    pre-render every chart to an SVG string. Tolerant of missing fields."""
    data = data or {}
    scoring = data.get("_scoring_inputs") or {}

    # ── Headline score ────────────────────────────────────────────────────────
    quality = data.get("quality_score")
    if quality is None:
        qr = data.get("quality_rating") or {}
        quality = qr.get("score") if isinstance(qr, dict) else None
    quality = _num(quality) or 0.0
    quality_label = data.get("quality_label") or (
        (data.get("quality_rating") or {}).get("label")
        if isinstance(data.get("quality_rating"), dict)
        else None
    ) or _quality_profile_label(
        quality,
        _LENS_BY_AGENT.get(str(data.get("agent") or "").strip().lower(), ""),
    )

    # ── Fair value / margin of safety ─────────────────────────────────────────
    price_action = data.get("price_action") or {}
    current_price = _num(price_action.get("current_price"))
    # Fair value = Wall Street analyst consensus target (the hero card is labeled
    # "Per Wall Street consensus"). Fall back to the stored estimate only when the
    # consensus has no target.
    ws_target = _num((data.get("wall_street_consensus") or {}).get("target_price"))
    fair_value = ws_target or _num(fair_value_estimate) or _num(data.get("fair_value_estimate"))
    mos_pct = None
    valuation_word = "—"
    valuation_color = pdf_charts.MUTED
    if fair_value and current_price:
        mos_pct = (fair_value - current_price) / current_price * 100.0
        if mos_pct >= 1:
            valuation_word, valuation_color = "Undervalued", pdf_charts._GOOD
        elif mos_pct <= -1:
            valuation_word, valuation_color = "Overvalued", pdf_charts._RED
        else:
            valuation_word, valuation_color = "Fairly Valued", pdf_charts._AMBER

    # ── Vitals ────────────────────────────────────────────────────────────────
    vitals = []
    for key, label in _VITAL_LABELS:
        s = _num(scoring.get(key))
        if s is None:
            continue
        s = max(0.0, min(100.0, s))
        vitals.append(
            {"label": label, "score": int(round(s)), "color": pdf_charts.band_color(s)}
        )

    # ── Wall Street consensus ─────────────────────────────────────────────────
    wsc = data.get("wall_street_consensus") or {}
    consensus_counts = {
        "strong_buy": wsc.get("analyst_strong_buy"),
        "buy": wsc.get("analyst_buy"),
        "hold": wsc.get("analyst_hold"),
        "sell": wsc.get("analyst_sell"),
        "strong_sell": wsc.get("analyst_strong_sell"),
    }
    # Legend rows for the PDF — only rating levels with at least one analyst.
    consensus_legend = [
        {"label": lbl, "color": col, "count": int(consensus_counts.get(k) or 0)}
        for k, lbl, col in _CONSENSUS_ORDER
        if (consensus_counts.get(k) or 0) >= 1
    ]

    # ── Bull / bear thesis ────────────────────────────────────────────────────
    thesis = data.get("core_thesis") or {}
    bull_case = [s for s in (thesis.get("bull_case") or []) if s]
    bear_case = [s for s in (thesis.get("bear_case") or []) if s]

    # ── Persona ───────────────────────────────────────────────────────────────
    agent = data.get("agent") or {}
    if isinstance(agent, str):
        agent = {"name": agent}
    persona_name = _persona_display(agent)
    persona_lens = agent.get("tagline") or agent.get("lens") or ""

    # ── Section data extraction ───────────────────────────────────────────────
    prices = price_action.get("prices") or []
    if prices and isinstance(prices[0], dict):
        prices = [p.get("price") or p.get("close") or p.get("value") for p in prices]

    moat = data.get("moat_competition") or {}
    dims = [d for d in (moat.get("dimensions") or []) if isinstance(d, dict)]
    dim_max = max(
        [_num(d.get("score")) or 0 for d in dims]
        + [_num(d.get("peer_score")) or 0 for d in dims]
        + [0]
    )
    radar_max = 10.0 if 0 < dim_max <= 10 else 100.0

    # Revenue engine — derive % of total + YoY growth per segment.
    engine = data.get("revenue_engine") or {}
    raw_segments = engine.get("segments") or []
    seg_denom = _num(engine.get("total_revenue")) or sum(
        _num(s.get("current_revenue")) or 0.0 for s in raw_segments
    )
    segments = []
    for s in raw_segments:
        cur = _num(s.get("current_revenue")) or 0.0
        prev = _num(s.get("previous_revenue"))
        segments.append({
            "name": s.get("name") or "—",
            "current_revenue": cur,
            "pct": (cur / seg_denom * 100.0) if seg_denom else 0.0,
            "growth": ((cur - prev) / prev * 100.0) if prev else None,
        })

    # Forecast timeline chart (gapless annual; fall back to curated projections).
    forecast = data.get("revenue_forecast") or {}
    timeline = forecast.get("annual_timeline") or forecast.get("projections") or []
    timeline_items = [{
        "label": (p.get("period") or "").replace("FY", "").strip() or p.get("period"),
        "value": _num(p.get("revenue")) or 0.0,
        "value_label": p.get("revenue_label") or "",
        "is_forecast": bool(p.get("is_forecast")),
    } for p in timeline if isinstance(p, dict)]

    # Growth chart — annual Revenue (absolute bars + YoY% line + sector line),
    # mirroring the app's Growth card. Revenue is the most universal growth series
    # and isn't otherwise charted in the PDF (EPS is covered by the earnings
    # timeline). Sign-aware + nil-safe in the chart helper; "" when <2 points.
    growth_chart = data.get("growth_chart") or {}
    growth_metric_label = "Revenue"
    growth_items = [{
        "label": p.get("period") or "",
        "value": _num(p.get("value")),
        "yoy": _num(p.get("yoy_change_percent")),
        "sector": _num(p.get("sector_average_yoy")),
    } for p in (growth_chart.get("revenue_annual") or []) if isinstance(p, dict)]
    # Forecast table: use the timeline's FORECAST years so it matches the chart
    # (which plots annual_timeline through the last analyst year, e.g. 2031).
    # Fall back to the curated 4-year window when no annual timeline exists.
    if forecast.get("annual_timeline"):
        projections_table = [
            p for p in forecast["annual_timeline"]
            if isinstance(p, dict) and p.get("is_forecast")
        ]
    else:
        projections_table = forecast.get("projections") or []

    # Insider flow + dilution.
    insider = data.get("insider_data") or {}
    flow = ((insider.get("insider_flow") or {}).get("flow_data")) or []
    insider_flow_items = [{
        "label": _fmt_month_label(f.get("month")),
        "up": f.get("buy_volume") or 0,
        "down": f.get("sell_volume") or 0,
    } for f in flow if isinstance(f, dict)]
    cap = insider.get("capital_allocation") or {}
    ca_points = [p for p in (cap.get("data_points") or []) if isinstance(p, dict)]
    # Capital returned per quarter (dividend + buyback). Amounts are in $millions;
    # scale to raw dollars so the axis formatter renders $M/$B labels.
    ca_capital_items = [{
        "label": p.get("period") or "",
        "values": [(_num(p.get("dividend_amount")) or 0.0) * 1e6,
                   (_num(p.get("buyback_amount")) or 0.0) * 1e6],
    } for p in ca_points]
    # Shares-outstanding trend (also $millions of shares -> raw count).
    ca_shares_items = [{
        "label": p.get("period") or "",
        "value": (_num(p.get("shares_outstanding")) or 0.0) * 1e6,
    } for p in ca_points if _num(p.get("shares_outstanding"))]
    # Recent insider transactions: change_in_millions is millions of SHARES, and
    # each row carries price_at_transaction — so derive both a precise share count
    # and the dollar value (shares × price), matching the iOS Shares/Value columns.
    recent_tx = []
    for a in (((insider.get("recent_transactions") or {}).get("activities")) or []):
        if not isinstance(a, dict):
            continue
        shares = abs((_num(a.get("change_in_millions")) or 0.0) * 1e6)
        price = _num(a.get("price_at_transaction"))
        value = shares * price if (shares and price) else None
        recent_tx.append({
            **a,
            "shares_label": _fmt_amount(shares) if shares else "—",
            "value_label": _fmt_amount(value, money=True),
        })

    # Hidden signals.
    hidden = data.get("hidden_market_signals") or {}
    congress = hidden.get("congress") if hidden else None
    short_int = hidden.get("short_interest") if hidden else None
    si_history = (short_int or {}).get("history") or []
    # Short-interest trend: shares short per FINRA settlement (bars), dated x-axis.
    si_bar_items = [{
        "label": _fmt_date_label(h.get("settlement_date")),
        "values": [_num(h.get("shares_short")) or 0.0],
    } for h in si_history if isinstance(h, dict) and _num(h.get("shares_short"))]

    # Institutional (13F) flow for the Wall Street section.
    inst_flow = wsc.get("hedge_fund_flow_data") or []
    # HoldersService reports institutional buy/sell volume in millions of shares;
    # scale to raw so diverging_bars' shared y-axis reads M/B like the insider chart.
    inst_flow_items = [{
        "label": _fmt_month_label(f.get("month")),
        "up": (_num(f.get("buy_volume")) or 0.0) * 1e6,
        "down": (_num(f.get("sell_volume")) or 0.0) * 1e6,
    } for f in inst_flow if isinstance(f, dict)]

    # Source citations → one deduped, numbered list for the end-of-report
    # references. Grounded citations (title/uri/publisher) appear on the Recent
    # Price Movement insight and on web-grounded macro risk factors. Each section
    # keeps the reference numbers of its own citations; numbering follows
    # document order (price section first).
    _src_index: dict[str, int] = {}
    sources_list: list[dict] = []

    def _ref_numbers(items: Any) -> list[int]:
        refs: list[int] = []
        for s in (items or []):
            if not isinstance(s, dict):
                continue
            uri = str(s.get("uri") or "").strip()
            if not uri:
                continue
            if uri not in _src_index:
                _src_index[uri] = len(sources_list) + 1
                sources_list.append({
                    "n": _src_index[uri],
                    "title": s.get("title") or "",
                    "uri": uri,
                    "publisher": s.get("publisher") or "",
                })
            if _src_index[uri] not in refs:
                refs.append(_src_index[uri])
        return sorted(refs)

    # Recent Price Movement (section 01) cites first → low reference numbers.
    price_source_refs = _ref_numbers(price_action.get("sources"))
    macro_rfs = (data.get("macro_data") or {}).get("risk_factors") or []
    macro_risk_factors = [
        {**rf, "source_refs": _ref_numbers(rf.get("sources"))} for rf in macro_rfs
    ]

    # ── Charts (pre-rendered SVG) ─────────────────────────────────────────────
    charts = {
        "gauge": pdf_charts.score_gauge(quality, size=140),
        "sparkline": pdf_charts.price_sparkline(prices, width=700, height=120),
        "earnings_timeline": pdf_charts.bars_actuals_forecast(
            timeline_items, width=700, height=150),
        "growth": pdf_charts.growth_bars_line(growth_items, width=700, height=184),
        "insider_flow": pdf_charts.diverging_bars(
            insider_flow_items, width=330, height=118, up_color="#16A34A"),
        "capital_returned": pdf_charts.axed_bars(
            ca_capital_items, colors=["#93C5FD", "#16A34A"], width=300, height=104, fmt="money"),
        "shares_trend": pdf_charts.axed_line(ca_shares_items, width=300, height=94, fmt="num"),
        "short_interest": pdf_charts.axed_bars(
            si_bar_items, colors=["#D97706"], width=320, height=104, fmt="num"),
        "radar": pdf_charts.moat_radar(dims, size=210, max_score=radar_max),
        "consensus": pdf_charts.analyst_consensus_stacked_bar(
            consensus_counts, width=330, height=22),
        "institution_flow": pdf_charts.diverging_bars(inst_flow_items, width=330, height=118),
    }

    return {
        # ── Cover ──
        "symbol": data.get("symbol") or data.get("ticker") or "—",
        "company_name": data.get("company_name") or "—",
        "exchange": data.get("exchange") or "",
        "sector": data.get("sector") or moat.get("industry")
        or (moat.get("market_dynamics") or {}).get("industry") or "",
        "live_date": data.get("live_date") or "",
        "persona_name": persona_name,
        "persona_lens": persona_lens,
        "quality_score": int(round(quality)),
        "quality_label": quality_label,
        "fair_value": fair_value,
        "current_price": current_price,
        "margin_of_safety_pct": mos_pct,
        "valuation_word": valuation_word,
        "valuation_color": valuation_color,
        "target_price": _num(wsc.get("target_price")),
        "low_target": _num(wsc.get("low_target")),
        "high_target": _num(wsc.get("high_target")),
        "consensus_counts": {k: (v or 0) for k, v in consensus_counts.items()},
        "consensus_legend": consensus_legend,
        "consensus_total": sum((v or 0) for v in consensus_counts.values()),
        "price_change_pct": _num(price_action.get("change_pct")),
        "window_label": price_action.get("window_label") or "12M",
        "growth_metric_label": growth_metric_label,
        "vitals": vitals,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "exec_summary": data.get("executive_summary_text") or "",
        # ── Deep-dive sections ──
        "price": {
            "narrative": price_action.get("narrative") or "",
            "source_refs": price_source_refs,
            "tier": price_action.get("tier"),
            "z_score": _num(price_action.get("z_score")),
            "sigma_daily_pct": _num(price_action.get("sigma_daily_pct")),
            "expected_band_pct": _num(price_action.get("expected_band_pct")),
            "event": price_action.get("event"),
            "direction": price_action.get("direction") or "flat",
        },
        "revenue_engine": {
            "segments": segments,
            "total_revenue": _num(engine.get("total_revenue")),
            "revenue_unit": engine.get("revenue_unit") or "",
            "period": engine.get("period") or "",
            "analysis_note": engine.get("analysis_note") or "",
        },
        "fundamentals": {
            "cards": data.get("fundamental_metrics") or [],
            "overall": data.get("overall_assessment") or {},
        },
        "forecast": {
            "cagr": _num(forecast.get("cagr")),
            "eps_growth": _num(forecast.get("eps_growth")),
            "management_guidance": forecast.get("management_guidance") or "",
            "projections": projections_table,
            "track_record": forecast.get("earnings_track_record") or [],
            "beat_summary": forecast.get("beat_summary") or "",
            "guidance_quote": forecast.get("guidance_quote") or "",
            "guidance_speaker": forecast.get("guidance_speaker") or "",
            "guidance_period": forecast.get("guidance_period") or "",
            "forecast_analyst_count": forecast.get("forecast_analyst_count"),
            "insight": forecast.get("insight") or "",
        },
        "insider": {
            "sentiment": insider.get("sentiment") or "",
            "timeframe": insider.get("timeframe") or "",
            "transactions": insider.get("transactions") or [],
            "capital_allocation": cap or None,
            "recent": recent_tx,
            "ownership_note": insider.get("ownership_note") or "",
        },
        "management": {
            "top_holders": [
                h for h in ((data.get("key_management") or {}).get("top_holders") or [])
                if isinstance(h, dict)
            ],
            # Officers gain a formatted ownership-% label (iOS shows "0.43% / 1.0M").
            "officers": [
                {**o, "pct_owned_label": _fmt_owner_pct(o.get("percent_owned"))}
                for o in ((data.get("key_management") or {}).get("officers") or [])
                if isinstance(o, dict)
            ],
            "ownership_insight": (data.get("key_management") or {}).get("ownership_insight") or "",
        },
        "hidden": {
            "congress": congress,
            "short_interest": short_int,
            "insight": (hidden or {}).get("insight") or "",
        },
        "moat": {
            "market_dynamics": _project_market_dynamics(moat.get("market_dynamics") or {}),
            "dimensions": dims,
            "competitors": moat.get("competitors") or [],
            "durability_note": moat.get("durability_note") or "",
            "competitive_insight": moat.get("competitive_insight") or "",
            "radar_max": radar_max,
        },
        "macro": {
            "overall_threat_level": (data.get("macro_data") or {}).get("overall_threat_level") or "",
            "headline": (data.get("macro_data") or {}).get("headline") or "",
            "intelligence_brief": (data.get("macro_data") or {}).get("intelligence_brief") or "",
            "risk_factors": macro_risk_factors,
        },
        "sources": sources_list,
        "wall_street": {
            "rating": (wsc.get("rating") or "").replace("_", " ").title(),
            "valuation_status": wsc.get("valuation_status") or "",
            "discount_percent": _num(wsc.get("discount_percent")),
            "momentum_upgrades": wsc.get("momentum_upgrades") or 0,
            "momentum_downgrades": wsc.get("momentum_downgrades") or 0,
            "momentum_maintains": wsc.get("momentum_maintains") or 0,
            "insight": wsc.get("wall_street_insight") or "",
        },
        "factors": data.get("critical_factors") or [],
        "charts": charts,
        "disclaimer": data.get("disclaimer_text")
        or "Generated by Cay AI for informational purposes only. "
        "Not investment advice. Data reflects a point-in-time snapshot.",
    }


# agent tag / persona key → lens word, mirroring iOS QualityBand.profileLabel.
_LENS_BY_AGENT = {
    "buffett": "Value", "warren_buffett": "Value",
    "ackman": "Value", "bill_ackman": "Value", "dalio": "Value",  # legacy dalio → ackman
    "wood": "Growth", "cathie_wood": "Growth",
    "lynch": "GARP", "peter_lynch": "GARP",
    "burry": "Contrarian", "michael_burry": "Contrarian",
}


def _quality_profile_label(score: float, lens: str = "") -> str:
    """Persona-aware headline label matching the iOS QualityBand gauge cutoffs
    (80/65/48/33) and the "<adjective> <lens> Profile" wording, so the downloaded
    PDF and the in-app gauge never disagree (and the PDF doesn't reintroduce the
    "Quality Business" phrasing the iOS reframe dropped). Empty lens → legacy wording.
    """
    s = round(score)
    if s >= 80:
        adj = "Excellent"
    elif s >= 65:
        adj = "Strong"
    elif s >= 48:
        adj = "Fair"
    elif s >= 33:
        adj = "Weak"
    else:
        adj = "Poor"
    return f"{adj} {lens} Profile" if lens else f"{adj} Quality Business"


def render_html(context: dict) -> str:
    """Render the Jinja2 template to an HTML string."""
    return _env().get_template("report.html").render(**context)


def render_pdf_bytes(html: str) -> bytes:
    """Render HTML -> PDF via WeasyPrint. Imported lazily so a missing native lib
    is a caught failure, not an import-time boot crash."""
    from weasyprint import HTML  # noqa: PLC0415 — intentional lazy import

    return HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()


async def generate_and_store_pdf(
    report_id: str,
    ticker_report_data: dict,
    fair_value_estimate: Optional[float],
    user_id: str,
) -> str:
    """Build -> render -> store. Returns the Storage object path.

    CPU-bound render and the sync Supabase upload run in ``asyncio.to_thread``.
    """
    import asyncio

    from app.database import get_supabase

    context = build_context(ticker_report_data, fair_value_estimate)
    html = render_html(context)
    pdf_bytes = await asyncio.to_thread(render_pdf_bytes, html)

    path = f"reports/{user_id}/{report_id}.pdf"

    def _upload() -> None:
        get_supabase().storage.from_(_BUCKET).upload(
            path,
            pdf_bytes,
            {"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)
    logger.info("Stored detailed-analysis PDF at %s (%d bytes)", path, len(pdf_bytes))
    return path
