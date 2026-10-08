"""The Overview's degraded fallback snapshot cards (stock_overview_service) carry their build
time and follow the Health Check's financials gate (2026-10-07).

- iOS now shows the OLDEST `computed_at` of the snapshot items as "Updated on …" (it used to
  print today's date whatever the data's age), so every card the overview can serve must
  carry one — including these four inline fallbacks.
- Interest coverage means nothing for a bank, insurer, capital-markets firm, asset manager
  or lender; the Health Check omits the row for them (`liquidity_ratios_applicable`), and
  the fallback Financial Health card must not show it either.
Hermetic: the builders are pure over FMP-shaped dicts.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.stock_overview_service import StockOverviewService


def _svc() -> StockOverviewService:
    return StockOverviewService.__new__(StockOverviewService)


_BS = {"totalAssets": 100.0, "totalLiabilities": 60.0, "totalCurrentAssets": 30.0,
       "totalCurrentLiabilities": 20.0, "retainedEarnings": 10.0, "totalDebt": 20.0,
       "cashAndCashEquivalents": 8.0, "totalStockholdersEquity": 40.0}
_INC = {"revenue": 50.0, "operatingIncome": 6.0, "ebit": 6.0, "netIncome": 4.0}
_CF = {"freeCashFlow": 5.0}
_FR = {"interestCoverage": 0.52, "assetTurnover": 0.4}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@pytest.mark.parametrize("industry", ["Banks - Diversified", "Banks - Regional",
                                      "Insurance - Life", "Financial - Capital Markets"])
def test_a_financials_fallback_health_card_has_no_interest_coverage_row(industry):
    card = _svc()._build_health_snapshot(_BS, _INC, _CF, _FR, {}, 1e9,
                                         sector="Financial Services", industry=industry)
    names = [m.name for m in card.metrics]
    assert "Interest Coverage" not in names
    assert "Altman Z-Score" not in names          # the model does not apply (HC-5)
    assert {"Cash to Debt", "Free Cash Flow Margin", "Asset Turnover"} <= set(names)
    assert card.rating == 0


@pytest.mark.parametrize("industry", ["Semiconductors", "", "Railroads"])
def test_everyone_else_keeps_the_interest_coverage_row(industry):
    card = _svc()._build_health_snapshot(_BS, _INC, _CF, _FR, {}, 1e9,
                                         sector="Technology", industry=industry)
    names = [m.name for m in card.metrics]
    assert names[0] == "Altman Z-Score"
    assert names.index("Interest Coverage") == 1   # same position as before


def test_every_inline_fallback_card_carries_its_build_time():
    before = datetime.now(timezone.utc).replace(microsecond=0)
    svc = _svc()
    cards = [
        svc._build_health_snapshot(_BS, _INC, _CF, _FR, {}, 1e9, sector="Technology",
                                   industry="Semiconductors"),
        svc._build_ownership_snapshot({}),
        svc._build_profitability_snapshot({}, {"operatingProfitMargin": 0.12}, _INC),
        svc._build_growth_snapshot(_INC, {"revenue": 40.0, "operatingIncome": 5.0}, _CF,
                                   {"freeCashFlow": 4.0}, {}, []),
    ]
    for card in cards:
        assert card.computed_at, card.category
        stamp = _parse(card.computed_at)
        assert stamp.tzinfo is not None and stamp >= before, (card.category, card.computed_at)
