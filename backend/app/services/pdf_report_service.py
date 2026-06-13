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
    ("profitability", "Profitability"),
    ("health", "Financial Health"),
    ("growth", "Growth"),
    ("valuation", "Valuation"),
    ("insider", "Insider Activity"),
    ("macro", "Macro Resilience"),
    ("analyst", "Analyst Sentiment"),
    ("momentum", "Price Momentum"),
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
    ) or _default_quality_label(quality)

    # ── Fair value / margin of safety ─────────────────────────────────────────
    price_action = data.get("price_action") or {}
    current_price = _num(price_action.get("current_price"))
    fair_value = _num(fair_value_estimate) or _num(data.get("fair_value_estimate"))
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

    # ── Charts (pre-rendered SVG) ─────────────────────────────────────────────
    prices = price_action.get("prices") or []
    if prices and isinstance(prices[0], dict):
        prices = [p.get("price") or p.get("close") or p.get("value") for p in prices]
    moat = data.get("moat_competition") or {}

    charts = {
        "gauge": pdf_charts.score_gauge(quality, size=140),
        "sparkline": pdf_charts.price_sparkline(prices, width=700, height=94),
        "consensus": pdf_charts.analyst_consensus_stacked_bar(
            consensus_counts, width=320, height=22
        ),
        "radar": pdf_charts.moat_radar(moat.get("dimensions") or [], size=196),
    }

    return {
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
        "consensus_total": sum((v or 0) for v in consensus_counts.values()),
        "price_change_pct": _num(price_action.get("change_pct")),
        "window_label": price_action.get("window_label") or "12M",
        "vitals": vitals,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "charts": charts,
        "exec_summary": data.get("executive_summary_text") or "",
        "disclaimer": data.get("disclaimer_text")
        or "Generated by Cay AI for informational purposes only. "
        "Not investment advice. Data reflects a point-in-time snapshot.",
    }


def _default_quality_label(score: float) -> str:
    if score >= 80:
        return "High Quality Business"
    if score >= 60:
        return "Fair Quality Business"
    if score >= 40:
        return "Mixed Quality Business"
    return "Low Quality Business"


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
