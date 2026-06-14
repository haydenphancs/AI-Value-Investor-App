"""
POC: render the full multi-section detailed-analysis report from a realistic
sample. Network-free. Writes HTML; the shell step converts to PDF with headless
Chrome (production uses WeasyPrint) and rasterizes pages with PyMuPDF.

    ./backend/venv/bin/python backend/scripts/render_pdf_poc.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services.pdf_report_service import build_context, render_html  # noqa: E402

_PRICES = [118.4, 124.1, 121.0, 133.7, 142.9, 138.2, 151.5, 149.0,
           158.8, 164.3, 160.1, 169.7, 172.4]
_MONTHS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr"]

SAMPLE = {
    "symbol": "ORCL",
    "company_name": "Oracle Corporation",
    "exchange": "NYSE",
    "sector": "Technology · Software—Infrastructure",
    "live_date": "Jun 13, 2026",
    "agent": "buffett",
    "quality_score": 72,
    "quality_label": "Fair Quality Business",
    "fair_value_estimate": 196.0,
    "_scoring_inputs": {
        "profitability": 84, "health": 58, "growth": 76, "valuation": 47,
        "insider": 52, "macro": 63, "analyst": 81, "momentum": 89,
    },
    "core_thesis": {
        "bull_case": [
            "OCI revenue compounding >50% with a multi-year RPO backlog that locks in "
            "revenue visibility well into the decade.",
            "Mission-critical database and ERP suites create deep switching costs and "
            "durable pricing power.",
            "Operating margins expand as cloud scales; disciplined buybacks steadily "
            "shrink the share count.",
        ],
        "bear_case": [
            "Net leverage stays elevated versus hyperscaler peers, limiting balance-"
            "sheet flexibility.",
            "Valuation already prices in near-flawless cloud execution — little room "
            "for a stumble.",
            "Heavy data-center capex pressures near-term free cash flow conversion.",
        ],
    },
    "executive_summary_text": (
        "Oracle has transformed from a legacy database vendor into a credible "
        "hyperscale cloud contender, with OCI and a fast-growing RPO backlog "
        "underwriting multi-year revenue visibility. Through a value lens it screens "
        "as a fair-quality compounder — deep switching costs and scale support durable "
        "margins, while elevated leverage and a full valuation temper the margin of safety."
    ),
    "price_action": {
        "current_price": 172.40, "prices": _PRICES, "change_pct": 41.2,
        "window_label": "12M", "direction": "up",
        "tier": "Notable", "z_score": 1.4, "sigma_daily_pct": 2.1, "expected_band_pct": 4.2,
        "event": {"tag": "Earnings Beat", "date": "2026-03-10", "index": 9},
        "narrative": (
            "ORCL is up 41% over the trailing year, outpacing the software group, with the "
            "March cloud-backlog beat driving the latest leg higher. Daily moves remain "
            "within a normal ±4.2% band, so the rally has been orderly rather than frothy."
        ),
    },
    "revenue_engine": {
        "segments": [
            {"name": "Cloud Services & License Support", "current_revenue": 44.0, "previous_revenue": 39.4, "total_revenue": 57.4},
            {"name": "Cloud License & On-Premise", "current_revenue": 5.1, "previous_revenue": 5.8, "total_revenue": 57.4},
            {"name": "Services", "current_revenue": 5.3, "previous_revenue": 5.0, "total_revenue": 57.4},
            {"name": "Hardware", "current_revenue": 3.0, "previous_revenue": 3.2, "total_revenue": 57.4},
        ],
        "total_revenue": 57.4, "revenue_unit": "billions", "period": "FY2025",
        "analysis_note": (
            "Cloud Services & License Support is now 77% of revenue and the sole growth "
            "engine, offsetting structural decline in legacy on-premise licensing."
        ),
    },
    "fundamental_metrics": [
        {"title": "Profitability", "star_rating": 4, "quality_label": "Strong, expanding margins", "quality_sentiment": "positive",
         "metrics": [{"label": "Gross Margin", "value": "71%", "trend": "up"}, {"label": "Operating Margin", "value": "30%", "trend": "up"},
                     {"label": "ROIC", "value": "18%", "trend": "flat"}, {"label": "Net Margin", "value": "21%", "trend": "up"}]},
        {"title": "Valuation", "star_rating": 2, "quality_label": "Premium to history", "quality_sentiment": "negative",
         "metrics": [{"label": "P/E (fwd)", "value": "32x", "trend": "up"}, {"label": "EV/EBITDA", "value": "22x", "trend": "up"},
                     {"label": "P/S", "value": "8.1x", "trend": "up"}, {"label": "FCF Yield", "value": "2.4%", "trend": "down"}]},
        {"title": "Growth", "star_rating": 4, "quality_label": "Cloud-led acceleration", "quality_sentiment": "positive",
         "metrics": [{"label": "Revenue YoY", "value": "18%", "trend": "up"}, {"label": "EPS YoY", "value": "22%", "trend": "up"},
                     {"label": "RPO YoY", "value": "41%", "trend": "up"}, {"label": "OCI YoY", "value": "51%", "trend": "up"}]},
        {"title": "Financial Health", "star_rating": 3, "quality_label": "Leveraged but covered", "quality_sentiment": "neutral",
         "metrics": [{"label": "Debt/Equity", "value": "4.8x", "trend": "down"}, {"label": "Interest Cov.", "value": "5.2x", "trend": "up"},
                     {"label": "Current Ratio", "value": "0.9", "trend": "flat"}, {"label": "Net Debt/EBITDA", "value": "3.1x", "trend": "down"}]},
    ],
    "overall_assessment": {
        "text": "Best-in-class profitability and growth offset a full valuation and an above-"
                "average debt load. Quality skews high; the entry price is the swing factor.",
        "average_rating": 3.2, "strong_count": 2, "weak_count": 1,
    },
    "revenue_forecast": {
        "cagr": 13.5, "eps_growth": 16.0, "management_guidance": "raised", "forecast_analyst_count": 32,
        "guidance_quote": "We now expect total cloud revenue to grow more than 40% in fiscal 2026, "
                          "and we expect that growth rate to accelerate.",
        "guidance_speaker": "CFO", "guidance_period": "FY2026",
        "beat_summary": "Beat 5 of 6",
        "insight": "Forward estimates lean on OCI capacity coming online; the raised guide signals "
                   "management confidence that backlog converts to recognized revenue on schedule.",
        "projections": [
            {"period": "FY2024", "revenue": 52.9, "revenue_label": "$52.9B", "revenue_yoy_pct": 6.0, "eps": 5.56, "eps_label": "$5.56", "eps_yoy_pct": 9.0, "eps_analyst_count": None, "is_forecast": False},
            {"period": "FY2025", "revenue": 57.4, "revenue_label": "$57.4B", "revenue_yoy_pct": 8.5, "eps": 6.05, "eps_label": "$6.05", "eps_yoy_pct": 8.8, "eps_analyst_count": None, "is_forecast": False},
            {"period": "FY2026", "revenue": 66.0, "revenue_label": "$66.0B", "revenue_yoy_pct": 15.0, "eps": 7.10, "eps_label": "$7.10", "eps_yoy_pct": 17.4, "eps_analyst_count": 32, "is_forecast": True},
            {"period": "FY2027", "revenue": 77.5, "revenue_label": "$77.5B", "revenue_yoy_pct": 17.4, "eps": 8.40, "eps_label": "$8.40", "eps_yoy_pct": 18.3, "eps_analyst_count": 29, "is_forecast": True},
        ],
        "annual_timeline": [
            {"period": "FY2022", "revenue": 42.4, "revenue_label": "$42B", "is_forecast": False},
            {"period": "FY2023", "revenue": 50.0, "revenue_label": "$50B", "is_forecast": False},
            {"period": "FY2024", "revenue": 52.9, "revenue_label": "$53B", "is_forecast": False},
            {"period": "FY2025", "revenue": 57.4, "revenue_label": "$57B", "is_forecast": False},
            {"period": "FY2026", "revenue": 66.0, "revenue_label": "$66B", "is_forecast": True},
            {"period": "FY2027", "revenue": 77.5, "revenue_label": "$78B", "is_forecast": True},
            {"period": "FY2028", "revenue": 90.0, "revenue_label": "$90B", "is_forecast": True},
        ],
        "earnings_track_record": [
            {"period": "Q1 '25", "surprise_percent": 2.1, "beat": True},
            {"period": "Q2 '25", "surprise_percent": -1.4, "beat": False},
            {"period": "Q3 '25", "surprise_percent": 3.6, "beat": True},
            {"period": "Q4 '25", "surprise_percent": 1.2, "beat": True},
            {"period": "Q1 '26", "surprise_percent": 4.0, "beat": True},
            {"period": "Q2 '26", "surprise_percent": 2.8, "beat": True},
        ],
    },
    "insider_data": {
        "sentiment": "Mildly Bullish", "timeframe": "Last 12 Months",
        "transactions": [
            {"type": "Buys", "count": 4, "shares": "62.0K", "value": "$9.8M"},
            {"type": "Sells", "count": 11, "shares": "880.0K", "value": "$142.0M"},
        ],
        "ownership_note": "Founder Larry Ellison retains a ~42% stake — exceptional owner-operator alignment.",
        "capital_allocation": {
            "buyback_status": "Moderate", "dividend_status": "Fair",
            "dividend_yield": 1.0, "buyback_yield": 1.8, "total_yield": 2.8, "share_count_change": -1.2,
            "data_points": [
                {"period": "Q1 '24", "shares_outstanding": 2760.0},
                {"period": "Q2 '24", "shares_outstanding": 2748.0},
                {"period": "Q3 '24", "shares_outstanding": 2741.0},
                {"period": "Q4 '24", "shares_outstanding": 2735.0},
                {"period": "Q1 '25", "shares_outstanding": 2728.0},
                {"period": "Q2 '25", "shares_outstanding": 2722.0},
            ],
        },
        "insider_flow": {"tab": "insider", "flow_data": [
            {"month": "2025-09", "buy_volume": 2.0, "sell_volume": 14.0},
            {"month": "2025-10", "buy_volume": 5.0, "sell_volume": 6.0},
            {"month": "2025-11", "buy_volume": 1.0, "sell_volume": 22.0},
            {"month": "2025-12", "buy_volume": 8.0, "sell_volume": 3.0},
            {"month": "2026-01", "buy_volume": 0.0, "sell_volume": 18.0},
            {"month": "2026-02", "buy_volume": 4.0, "sell_volume": 9.0},
            {"month": "2026-03", "buy_volume": 12.0, "sell_volume": 2.0},
            {"month": "2026-04", "buy_volume": 3.0, "sell_volume": 7.0},
        ]},
        "recent_transactions": {"activities": [
            {"name": "Safra Catz", "title": "CEO", "date": "2026-04-12", "change_in_millions": -38.4, "transaction_type": "Sell"},
            {"name": "Lawrence Ellison", "title": "CTO, Chairman", "date": "2026-03-05", "change_in_millions": 0.0, "transaction_type": "Hold"},
            {"name": "Jeffrey Henley", "title": "Vice Chairman", "date": "2026-02-22", "change_in_millions": -9.1, "transaction_type": "Sell"},
            {"name": "Edward Screven", "title": "Chief Architect", "date": "2026-01-18", "change_in_millions": 1.6, "transaction_type": "Buy"},
            {"name": "Mary Ann Gilleece", "title": "Director", "date": "2025-12-09", "change_in_millions": 0.4, "transaction_type": "Buy"},
        ]},
    },
    "key_management": {
        "top_holders": [
            {"name": "Lawrence Ellison", "title": "Founder, CTO", "ownership": "", "ownership_value": "$320B", "percent_ownership": 42.0},
            {"name": "Vanguard Group", "title": "Institution", "ownership": "", "ownership_value": "$48B", "percent_ownership": 8.0},
            {"name": "BlackRock", "title": "Institution", "ownership": "", "ownership_value": "$42B", "percent_ownership": 7.0},
        ],
        "officers": [
            {"name": "Safra Catz", "title": "Chief Executive Officer", "ownership": "", "ownership_value": "0.34% / 9.3M"},
            {"name": "Lawrence Ellison", "title": "CTO & Chairman", "ownership": "", "ownership_value": "42% / 1.16B"},
            {"name": "Clay Magouyrk", "title": "EVP, OCI", "ownership": "", "ownership_value": "0.02% / 0.5M"},
            {"name": "Doug Kehring", "title": "EVP, Corp. Ops", "ownership": "", "ownership_value": "0.01% / 0.3M"},
        ],
        "ownership_insight": "Concentrated founder control plus deep institutional ownership; insider "
                            "selling is routine 10b5-1 diversification, not a thesis-changing signal.",
    },
    "moat_competition": {
        "market_dynamics": {
            "industry": "Enterprise Cloud & Database", "concentration": "oligopoly", "cagr_5yr": 12.0,
            "current_tam": 600.0, "future_tam": 1050.0, "current_year": "2025", "future_year": "2030",
            "lifecycle_phase": "secular_growth", "tam_scope": "global",
            "tam_source_label": "Earnings call quote",
            "tam_source_quote": "The cloud infrastructure market is heading toward a trillion dollars "
                                "of annual spend by the end of the decade.",
        },
        "dimensions": [
            {"name": "Switching", "score": 88, "peer_score": 70},
            {"name": "Brand", "score": 64, "peer_score": 72},
            {"name": "Network", "score": 55, "peer_score": 60},
            {"name": "Cost Adv.", "score": 60, "peer_score": 65},
            {"name": "Scale", "score": 82, "peer_score": 78},
            {"name": "Intangibles", "score": 79, "peer_score": 68},
        ],
        "competitors": [
            {"name": "Microsoft Azure", "ticker": "MSFT", "competitive_score": 8.4, "market_share_percent": 24.0, "threat_level": "high"},
            {"name": "Amazon AWS", "ticker": "AMZN", "competitive_score": 8.1, "market_share_percent": 31.0, "threat_level": "high"},
            {"name": "Google Cloud", "ticker": "GOOGL", "competitive_score": 6.6, "market_share_percent": 11.0, "threat_level": "moderate"},
            {"name": "SAP", "ticker": "SAP", "competitive_score": 5.2, "market_share_percent": 6.0, "threat_level": "moderate"},
            {"name": "Salesforce", "ticker": "CRM", "competitive_score": 4.4, "market_share_percent": 4.0, "threat_level": "low"},
        ],
        "durability_note": "Database lock-in and OCI's price-performance edge underpin a widening moat.",
        "competitive_insight": "Oracle is the clear #4 hyperscaler but punches above its share in AI-"
                              "training capacity, where multi-year GPU contracts favor its dedicated regions.",
    },
    "macro_data": {
        "overall_threat_level": "Moderate",
        "headline": "Rate path and AI-capex cycle dominate the macro backdrop.",
        "intelligence_brief": "A higher-for-longer rate environment raises the cost of Oracle's "
            "data-center build-out, but resilient enterprise IT budgets and AI demand cushion the top line.",
        "risk_factors": [
            {"category": "interest_rates", "title": "Elevated financing costs", "impact": 0.6,
             "description": "Debt-funded capex becomes more expensive as the curve stays inverted; refinancing "
                            "$80B+ of debt at higher coupons would pressure EPS.", "trend": "stable", "severity": "elevated",
             "sources": [{"title": "Fed holds rates steady", "uri": "https://www.federalreserve.gov", "publisher": "Federal Reserve"},
                         {"title": "Rates higher for longer", "uri": "https://www.reuters.com/markets/rates", "publisher": "Reuters"}]},
            {"category": "geopolitical", "title": "Data-sovereignty regulation", "impact": 0.4,
             "description": "Tightening EU/APAC data-residency rules could fragment cloud demand and raise "
                            "compliance costs for multinational deployments.", "trend": "worsening", "severity": "elevated",
             "sources": [{"title": "EU data act enters force", "uri": "https://digital-strategy.ec.europa.eu", "publisher": "European Commission"}]},
            {"category": "supply_chain", "title": "GPU supply constraints", "impact": 0.5,
             "description": "AI-region expansion depends on timely accelerator deliveries; allocation shortfalls "
                            "would delay backlog conversion.", "trend": "improving", "severity": "low",
             "sources": [{"title": "Rates higher for longer", "uri": "https://www.reuters.com/markets/rates", "publisher": "Reuters"}]},
        ],
        "last_updated": "2026-06-12",
    },
    "wall_street_consensus": {
        "rating": "buy", "current_price": 172.40,
        "target_price": 205.0, "low_target": 150.0, "high_target": 260.0,
        "valuation_status": "Undervalued", "discount_percent": 15.9,
        "wall_street_insight": "Sell-side targets imply ~19% upside on average; institutions added on the "
            "March beat, and upgrades outnumber downgrades 3:1 — momentum confirms the fundamental story.",
        "hedge_fund_flow_data": [
            {"month": "2025-09", "buy_volume": 30.0, "sell_volume": 22.0},
            {"month": "2025-10", "buy_volume": 18.0, "sell_volume": 27.0},
            {"month": "2025-11", "buy_volume": 41.0, "sell_volume": 12.0},
            {"month": "2025-12", "buy_volume": 35.0, "sell_volume": 19.0},
            {"month": "2026-01", "buy_volume": 22.0, "sell_volume": 30.0},
            {"month": "2026-02", "buy_volume": 48.0, "sell_volume": 14.0},
            {"month": "2026-03", "buy_volume": 55.0, "sell_volume": 10.0},
            {"month": "2026-04", "buy_volume": 33.0, "sell_volume": 21.0},
        ],
        "momentum_upgrades": 6, "momentum_downgrades": 2, "momentum_maintains": 5,
        "analyst_strong_buy": 8, "analyst_buy": 22, "analyst_hold": 12, "analyst_sell": 2, "analyst_strong_sell": 1,
    },
    "hidden_market_signals": {
        "congress": {
            "num_buyers": 3, "num_sellers": 1, "total_buys_in_millions": 2.5, "total_sells_in_millions": 0.5,
            "net_direction": "buy", "period": "Last 12 Months",
            "trades": [
                {"name": "Rep. J. Carper", "role": "House", "date": "2026-03-18", "transaction_type": "Buy"},
                {"name": "Sen. M. Rounds", "role": "Senate", "date": "2026-02-04", "transaction_type": "Buy"},
                {"name": "Rep. K. Khanna", "role": "House", "date": "2026-01-22", "transaction_type": "Buy"},
                {"name": "Rep. D. Moore", "role": "House", "date": "2025-11-30", "transaction_type": "Sell"},
            ],
        },
        "short_interest": {
            "percent_of_float": 1.2, "days_to_cover": 2.3, "shares_short": 28_000_000.0,
            "change_3m": -8.0, "settlement_date": "2026-05-30",
            "history": [
                {"settlement_date": "2025-11-15", "shares_short": 34_000_000.0, "days_to_cover": 3.1},
                {"settlement_date": "2025-12-15", "shares_short": 33_000_000.0, "days_to_cover": 2.9},
                {"settlement_date": "2026-01-15", "shares_short": 31_500_000.0, "days_to_cover": 2.8},
                {"settlement_date": "2026-02-15", "shares_short": 30_000_000.0, "days_to_cover": 2.6},
                {"settlement_date": "2026-03-15", "shares_short": 29_500_000.0, "days_to_cover": 2.5},
                {"settlement_date": "2026-04-15", "shares_short": 29_000_000.0, "days_to_cover": 2.4},
                {"settlement_date": "2026-05-15", "shares_short": 28_000_000.0, "days_to_cover": 2.3},
            ],
        },
        "insight": "Both signals lean constructive: short interest is low and falling, and the few "
                  "congressional trades skew net-buy.",
    },
    "critical_factors": [
        {"title": "OCI backlog conversion", "severity": "high",
         "description": "RPO has ballooned to >$130B; the stock is priced for that backlog converting to "
                        "recognized revenue on schedule.",
         "watch": "Quarterly RPO growth and cloud revenue recognition vs. the raised FY26 guide."},
        {"title": "Leverage & refinancing", "severity": "medium",
         "description": "Net debt/EBITDA near 3x with sizable maturities; higher-for-longer rates raise the "
                        "refinancing bill.",
         "watch": "Interest coverage trend and the blended coupon on upcoming debt rollovers."},
        {"title": "Capex intensity vs. FCF", "severity": "medium",
         "description": "Data-center build-out is compressing free-cash-flow conversion even as margins expand.",
         "watch": "Capex/revenue ratio and free-cash-flow margin over the next two quarters."},
        {"title": "Founder key-person risk", "severity": "low",
         "description": "Larry Ellison's 42% stake and strategic role concentrate influence in one individual.",
         "watch": "Any change to Ellison's role, stake, or 10b5-1 selling cadence."},
    ],
    "disclaimer_text": (
        "Generated by Cay AI for informational purposes only. Not investment advice. "
        "Figures reflect a point-in-time snapshot at report date."
    ),
}


def main() -> None:
    ctx = build_context(SAMPLE, fair_value_estimate=SAMPLE["fair_value_estimate"])
    html = render_html(ctx)
    out = Path(__file__).resolve().parent / "poc_output.html"
    out.write_text(html, encoding="utf-8")
    print(f"HTML written: {out}  ({len(html)} chars)")
    print("charts:", {k: bool(v) for k, v in ctx["charts"].items()})


if __name__ == "__main__":
    main()
