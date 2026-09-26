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

from app.config import settings
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import pdf_charts
from app.services.dcf_report_gate import (
    strip_caydex_if_disabled,
    wall_street_insight_is_for_this_card,
)

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
    ("insider", "Insider Activity"),
    ("capital_allocation", "Capital Allocation"),
    ("macro", "Macro Resilience"),
]


# Persona lookup -> style agent label, DERIVED from persona_config so it can never drift.
#
# These labels were real surnames ("Buffett Agent", "Wood Agent", …) printed as the
# persona header of the downloadable PDF. Migration 103 renamed the personas to style
# names to remove right-of-publicity exposure, and this had to move with it or the
# exported document would keep naming real people.
#
# Keyed by BOTH `key` and `agent_tag`: the frozen `report_data.agent` carries the tag
# ("buffett"), so historical reports resolve through the same map.
def _build_persona_display() -> dict[str, str]:
    from app.services.agents.persona_config import (  # noqa: PLC0415 — avoid import cycle
        PERSONA_KEYS,
        get_persona_config,
    )

    out: dict[str, str] = {}
    for key in PERSONA_KEYS:
        cfg = get_persona_config(key)
        out[key] = cfg.agent_label
        out[cfg.agent_tag] = cfg.agent_label
        # The style display name itself, so a report carrying `agent.name` resolves.
        out[cfg.display_name.lower()] = cfg.agent_label
    return out


_PERSONA_DISPLAY: dict[str, str] = _build_persona_display()

# Pre-rename surname fragments -> current label. A report frozen BEFORE the rename has
# `agent.name == "Warren Buffett"`, and without this it would fall through to the generic
# tail below and print the old surname.
_LEGACY_PERSONA_NAME_FRAGMENTS: dict[str, str] = {
    # Style names that have been renamed since. `_PERSONA_DISPLAY` is DERIVED from
    # `persona_config`, so a renamed label stops resolving there the moment it
    # changes, and a report frozen under the old name would fall through to the
    # generic tail and print "Hunter Agent".
    "everyday growth hunter": "GARP Agent",
    "buffett": "Quality Agent",
    "wood": "Disruption Agent",
    "lynch": "GARP Agent",
    "ackman": "Activist Agent",
    "burry": "Contrarian Agent",
}


def _persona_display(agent: dict) -> str:
    """Map a persona to its style agent label (e.g. 'Quality Agent')."""
    key = str(agent.get("key") or "").strip().lower()
    if key in _PERSONA_DISPLAY:
        return _PERSONA_DISPLAY[key]
    name = str(agent.get("name") or "").strip()
    if not name:
        return "Cay AI Agent"
    low = name.lower()
    # Exact style-name / key / tag match.
    if low in _PERSONA_DISPLAY:
        return _PERSONA_DISPLAY[low]
    # Then pre-rename surnames ("Warren Buffett", "Buffett Agent").
    for fragment, label in _LEGACY_PERSONA_NAME_FRAGMENTS.items():
        if fragment in low:
            return label
    last = name.split()[-1]
    return name if last.lower() == "agent" else f"{last} Agent"


def _guidance_for_pdf(value: Any) -> str:
    """The three read stances pass through; "unknown", None or garbage → ""."""
    return value if value in ("raised", "maintained", "lowered") else ""


def _num(v: Any) -> Optional[float]:
    """Coerce to float, or None. NaN/Inf are treated as ABSENT, not as numbers.

    A non-finite value that survives here renders as "$nan" in the PDF and, worse,
    silently defeats the margin-of-safety comparisons below (NaN is truthy, and
    ``nan >= 1`` and ``nan <= -1`` are both False, so it would fall through to
    "Fairly Valued"). Upstream FMP payloads do occasionally carry NaN.
    """
    if isinstance(v, bool):
        return None
    if not isinstance(v, (int, float)):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
    f = float(v)
    return f if math.isfinite(f) else None


def _price_gap(value: Optional[float], price: Optional[float], noun: str) -> tuple[Optional[float], str]:
    """(price-vs-value gap %, neutral words) — "Price 12% below the estimate" / "Price in line
    with the estimate" inside ±0.5 %. Never a verdict (hard rule 4,
    documents/research/dcf-methodology-v1.md §5). (None, "—") when either side is missing or
    not positive."""
    if not (value and price and value > 0 and price > 0):
        return None, "—"
    gap = (price / value - 1) * 100.0
    if abs(gap) < 0.5:
        return gap, f"Price in line with {noun}"
    return gap, f"Price {abs(gap):.0f}% {'below' if gap < 0 else 'above'} {noun}"


def _published_estimate(ws: dict, current_price: Optional[float], symbol: str = "") -> dict:
    """The Caydex Fair Value Estimate as PUBLISHED on the report (model dcf-v1), normalised for
    the PDF. One reading for the hero card and section 09, so the two can never disagree.

    state "ok"      — value AND range (a value is never shown without its range, §5), with
                      the neutral gap against the report's price;
    state "refused" — "Not modelled" + the model's one-sentence reason;
    state "none"    — no block: an analyst-era report, a report built with the switch off, or
                      a block the kill switch stripped (`build_context` applies
                      `strip_caydex_if_disabled` first). A malformed ok block degrades here too.
    """
    block = ws.get("caydex_fair_value") if isinstance(ws, dict) else None
    if not isinstance(block, dict):
        return {"state": "none"}
    status = block.get("status")
    if status == "refused":
        reason = block.get("refusal_reason")
        return {"state": "refused", "refusal_reason": reason.strip() if isinstance(reason, str) else ""}
    if status != "ok":
        logger.warning("pdf: Caydex estimate for %s has unknown status %r — shown as unavailable",
                       symbol, status)
        return {"state": "none"}
    fv = _num(block.get("fair_value"))
    lo, hi = _num(block.get("range_low")), _num(block.get("range_high"))
    if not (fv and lo and hi and 0 < lo <= fv <= hi):
        logger.warning("pdf: Caydex estimate for %s is malformed (value=%r range=%r–%r) — "
                       "shown as unavailable", symbol, fv, lo, hi)
        return {"state": "none"}
    gap_pct, gap_word = _price_gap(fv, current_price, "the estimate")
    as_of = block.get("as_of")
    return {
        "state": "ok",
        "fair_value": fv,
        "range_low": lo,
        "range_high": hi,
        "price_gap_pct": gap_pct,
        "gap_word": gap_word if gap_pct is not None else "",
        "as_of": as_of[:10] if isinstance(as_of, str) else "",
    }


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
    # Kill switch (settings.DCF_ENABLED off): drop a stored Caydex estimate, and on a report BUILT
    # with it the insight that quotes it, before anything below reads the block.
    data = strip_caydex_if_disabled(data or {})
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
    # The hero shows the published Caydex estimate, or — on a report that predates it — the
    # model value that report was built with, labelled as such. Never an analyst price target:
    # those are unlicensed FMP data, removed from every surface on 2026-09-26 (owner), and a
    # target under a "Fair Value" heading is the placement rule inverted.
    ws_block = data.get("wall_street_consensus")
    ws_block = ws_block if isinstance(ws_block, dict) else {}
    # The Caydex Fair Value Estimate as published on the report (model dcf-v1). Only this
    # value may be labelled "Caydex": a bare `fair_value_estimate` without the block is FMP's
    # model (reports generated with DCF_ENABLED off, and every older report).
    estimate = _published_estimate(
        ws_block, current_price, str(data.get("symbol") or data.get("ticker") or ""))
    caydex_value = estimate["fair_value"] if estimate["state"] == "ok" else None
    own_estimate = _num(fair_value_estimate) or _num(data.get("fair_value_estimate"))
    # Reports persisted between the FMP rebuild and 2026-09-12 carry a FABRICATED
    # `fair_value_estimate` equal to the frozen current price (the collector wrote
    # `round(current_price, 2)` whenever the DCF was missing, and `/stable/profile`
    # never carries one). Those rows are immutable, so treat "estimate == price to the
    # cent" as no estimate rather than printing "Fairly Valued +0.0%". A genuine DCF
    # that lands on the price to the cent is not a number worth a hero card either.
    if own_estimate is not None and current_price and abs(own_estimate - current_price) < 0.005:
        own_estimate = None
    # On a report BUILT with the Caydex model, `fair_value_estimate` (the research_reports
    # column production passes in) IS the Caydex value. It may appear only through the
    # published block above, with its range — never re-labelled as a bare "model value" after
    # the kill switch stripped the block, or when the block is malformed.
    if ws_block.get("dcf_source") == "caydex":
        own_estimate = None
    if caydex_value:
        fair_value = caydex_value
        fair_value_basis = ("Caydex Fair Value Estimate · DCF model estimate, not a price target, "
                            "not a recommendation"
                            f" · range ${estimate['range_low']:,.0f}–${estimate['range_high']:,.0f}")
        gap_noun = "the estimate"
    elif own_estimate:
        fair_value = own_estimate
        fair_value_basis = "DCF model value · not a price target"
        gap_noun = "the model value"
    else:
        fair_value = None
        fair_value_basis = ""
        gap_noun = ""
    # NEUTRAL wording only (hard rule 4, documents/research/dcf-methodology-v1.md §5): the
    # hero used to print Undervalued / Overvalued at a ±1 % gap, in green and red — a verdict
    # on a model number. It now states the gap, in a neutral colour.
    valuation_color = pdf_charts.MUTED
    price_gap_pct, valuation_word = _price_gap(fair_value, current_price, gap_noun)
    mos_pct = (
        (fair_value - current_price) / current_price * 100.0 if price_gap_pct is not None else None
    )

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

    # ── Valuation & Institutions (section 09, formerly "Wall Street Consensus") ──
    # The analyst half (ratings, price targets, momentum) is unlicensed FMP data and is gone
    # from the section; it shows the published estimate, the 13F flow and the insight.
    wsc = ws_block
    ws_insight = wsc.get("wall_street_insight")
    ws_insight = ws_insight.strip() if isinstance(ws_insight, str) else ""
    # Only beside the estimate it was written with (same rule as the app and chat).
    if ws_insight and not wall_street_insight_is_for_this_card(wsc):
        ws_insight = ""

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
        "fair_value_basis": fair_value_basis,
        "current_price": current_price,
        "margin_of_safety_pct": mos_pct,
        "price_gap_pct": price_gap_pct,
        "valuation_word": valuation_word,
        "valuation_color": valuation_color,
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
            # "unknown" = no transcript was read; the template's `or "—"` dash is
            # the honest cell, not a capitalised "Unknown" that reads like a stance.
            "management_guidance": _guidance_for_pdf(forecast.get("management_guidance")),
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
            "estimate": estimate,
            "insight": ws_insight,
        },
        "factors": data.get("critical_factors") or [],
        "charts": charts,
        # The fallback must carry the AI-inaccuracy caveat, not just "not investment advice".
        # A PDF is the one surface that LEAVES the app — it gets shared, saved and read months
        # later with no UI around it — and this branch fires exactly when `disclaimer_text` is
        # missing from the report row, i.e. when the agent's own disclaimer never made it in.
        # Every other copy in the product says AI output may be wrong; this one silently did
        # not, which made the most portable artifact the least qualified one.
        "disclaimer": data.get("disclaimer_text")
        or "Generated by Cay AI for educational purposes only. Not investment advice. "
        "AI-generated content may be inaccurate — always do your own research and consult a "
        "qualified financial advisor. Data reflects a point-in-time snapshot.",
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
