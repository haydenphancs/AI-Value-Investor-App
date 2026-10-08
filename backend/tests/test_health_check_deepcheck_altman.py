"""Altman Z is not applicable to banks, insurers or REITs (finding #6).

The model was built for industrial balance sheets. For a bank, liabilities ARE the
operating funding (deposits) and there is no current-asset / current-liability split, so
JPM-shaped inputs score 0.4 — "Deep distress. Imminent default risk." — on the Financials
tab, 40% of the Overview Financial Health rating, and the report's "Bankruptcy Risk"
verdict. A Realty-Income-shaped REIT scored 1.0.

The gate lives inside the ONE implementation (`health_check_service._compute_z_score`),
keyed on the profile's sector / industry, and every caller must hand those in — the AST
scan at the bottom fails on a caller that does not, so a new surface cannot quietly
publish a bank's Z again.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services.health_check_service import _compute_z_score, altman_z_applicable

_APP = Path(__file__).resolve().parents[1] / "app"

# JPM-shaped: no current split, liabilities ~92% of assets.
_BANK_BS = {"totalAssets": 4.4e12, "totalLiabilities": 4.04e12, "totalCurrentAssets": 0,
            "totalCurrentLiabilities": 0, "retainedEarnings": 380e9,
            "totalStockholdersEquity": 360e9}
_BANK_INC = {"operatingIncome": 75e9, "revenue": 270e9}
_BANK_MCAP = 800e9


def test_the_hazard_the_gate_exists_for():
    """Without a sector the formula still runs — and calls JPM a near-certain default."""
    assert _compute_z_score(_BANK_BS, _BANK_INC, _BANK_MCAP) == pytest.approx(0.4, abs=0.05)
    assert _compute_z_score(_BANK_BS, _BANK_INC, _BANK_MCAP,
                            sector="Financial Services", industry="Banks - Diversified") is None


@pytest.mark.parametrize("sector,industry,applicable", [
    ("Financial Services", "Banks - Diversified", False),
    ("Financials", "", False),                                   # FMP alias, normalised
    ("  Financial Services ", None, False),                      # stray whitespace
    ("Real Estate", "REIT - Retail", False),
    ("Real Estate", "Real Estate - Services", False),
    ("Financial Services", "Insurance - Life", False),
    ("Financial Services", "Financial - Credit Services", False),
    # Misclassified rows: the industry backstop.
    ("Technology", "Insurance - Property & Casualty", False),
    ("", "Banks - Regional", False),
    ("Industrials", "Investment - Banking & Investment Services", False),
    ("", "Asset Management", False),
    ("", "Financial - Capital Markets", False),
    ("", "Financial - Mortgages", False),
    ("", "Mortgage REITs", False),
    # Applicable: ordinary companies, and words that merely CONTAIN a keyword.
    ("Technology", "Software - Infrastructure", True),
    ("Industrials", "Integrated Freight & Logistics", True),   # "fREIght" is not a REIT
    ("Healthcare", "Medical - Care Facilities", True),
    ("Consumer Cyclical", "Auto - Dealerships", True),
    ("", "", True),
    (None, None, True),                                          # unknown → score as before
    (123, ["Banks"], True),                                      # malformed row: unknown, no crash
])
def test_applicability_gate(sector, industry, applicable):
    assert altman_z_applicable(sector, industry) is applicable


# ── the gate reaches every surface ────────────────────────────────────────────


class _FakeFMP:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


def _bank_answers(sector: str, industry: str) -> Dict[str, Any]:
    quarter = {"operatingIncome": _BANK_INC["operatingIncome"] / 4,
               "revenue": _BANK_INC["revenue"] / 4, "interestExpense": 20e9,
               "netIncome": 14e9, "ebitda": 20e9}
    return {
        "get_company_profile": {"symbol": "BANK", "sector": sector, "industry": industry,
                                "mktCap": _BANK_MCAP},
        "get_ratios_ttm": [{"debtToEquityRatioTTM": 1.3, "priceToEarningsRatioTTM": 12.0,
                            "interestCoverageRatioTTM": 1.2}],
        "get_key_metrics_ttm": [{"returnOnEquityTTM": 0.16}],
        "get_balance_sheet": [dict(_BANK_BS)],
        "get_income_statement": [dict(quarter, date=d) for d in
                                 ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")],
        "get_earning_calendar_full": [],
    }


class _Lookup:
    # The health check reads the rich cells (2026-10-07); health_snapshot's local
    # fallback still reads the flat values.
    def get_current_benchmarks(self, industry, sector, metrics):
        return {m: None for m in metrics}

    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: None for m in metrics}


@pytest.mark.asyncio
@pytest.mark.parametrize("sector,industry,has_z", [
    ("Financial Services", "Banks - Diversified", False),
    ("Real Estate", "REIT - Retail", False),
    ("Technology", "Software - Infrastructure", True),    # same numbers, ordinary company
])
async def test_health_check_omits_z_for_financials(monkeypatch, sector, industry, has_z):
    from app.services import health_check_service as hc

    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: _Lookup())
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = _FakeFMP(_bank_answers(sector, industry))

    resp, _ = await svc._build_health_check("BANK")
    z = next((m for m in resp.metrics if m.type == "altman_z_score"), None)
    assert (z is not None) is has_z
    if not has_z:
        assert resp.degraded == [], "not applicable is an answer, not a degradation"
        assert {m.type for m in resp.metrics} >= {"debt_to_equity", "roe"}
        assert all("distress" not in m.insight_text.lower() for m in resp.metrics)
    else:
        assert z.status == "negative", "control: the ungated formula really does say distress"


@pytest.mark.asyncio
@pytest.mark.parametrize("sector,industry,has_z_row", [
    ("Financial Services", "Banks - Diversified", False),
    ("Technology", "Software - Infrastructure", True),
])
async def test_health_snapshot_fallback_omits_z_for_financials(monkeypatch, sector, industry,
                                                                has_z_row):
    from app.services import health_check_service
    from app.services import health_snapshot_service as hs

    class _Failing:
        async def get_health_check(self, ticker):
            raise RuntimeError("health check down")

    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _Failing())
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: _Lookup())
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(_bank_answers(sector, industry))

    snap, _degraded = await svc._compute_with_status("BANK")
    names = [m.name for m in snap.metrics]
    assert ("Altman Z-Score" in names) is has_z_row, names


def test_stock_overview_fallback_card_omits_z_for_financials():
    from app.services.stock_overview_service import StockOverviewService

    svc = StockOverviewService.__new__(StockOverviewService)
    bank = svc._build_health_snapshot(
        _BANK_BS, _BANK_INC, {}, {}, {}, _BANK_MCAP,
        sector="Financial Services", industry="Banks - Diversified",
    )
    # No Z row at all for a bank (2026-10-07, HC-5: it printed a bare "—"), and the card
    # is unrated rather than reading 'distress' (rating 1).
    assert not any(m.name == "Altman Z-Score" for m in bank.metrics)
    assert bank.rating == 0, "a bank's card read 'distress' (rating 1)"

    ungated = svc._build_health_snapshot(_BANK_BS, _BANK_INC, {}, {}, {}, _BANK_MCAP)
    assert next(m for m in ungated.metrics if m.name == "Altman Z-Score").value == (
        f"{_compute_z_score(_BANK_BS, _BANK_INC, _BANK_MCAP)}"
    )


# ── every caller hands the gate its inputs ────────────────────────────────────


def _z_callers() -> List[tuple]:
    """(file, line, keywords) for every call of `_compute_z_score` (or a name it was
    imported AS) anywhere under app/, the definition itself excluded."""
    out = []
    for path in sorted(_APP.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "_compute_z_score" not in src:
            continue
        tree = ast.parse(src)
        names = {"_compute_z_score"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "_compute_z_score" and alias.asname:
                        names.add(alias.asname)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if called in names:
                rel = path.relative_to(_APP.parent)
                out.append((str(rel), node.lineno, {k.arg for k in node.keywords}))
    return out


def test_every_altman_caller_passes_sector_and_industry():
    callers = _z_callers()
    files = {f for f, _, _ in callers}
    # Non-vacuity: the scan must actually see the known surfaces.
    assert {"app/services/health_check_service.py", "app/services/health_snapshot_service.py",
            "app/services/stock_overview_service.py"} <= files, files
    missing = [f"{f}:{line}" for f, line, kw in callers if not {"sector", "industry"} <= kw]
    assert not missing, (
        "these Altman Z callers do not pass sector=/industry=, so a bank's Z would be "
        f"published as 'distress' there: {missing}"
    )
