"""
Unit tests for ip_intel_service + the USPTO / OpenFDA integration sub-
score helpers.

No live USPTO / FDA calls — pure helper tests for the sub-score mapping
+ industry-routing logic. End-to-end fetch testing would require fake
HTTP responses, which are out of scope for v1 (the integrations are
thin HTTP wrappers per project rule).

Self-contained per the project testing rule.
"""

from __future__ import annotations

import pytest

from app.services.ip_intel_service import (
    _is_pharma,
    _normalize_assignee,
    fda_approvals_to_sub_score,
    patents_per_employee_to_sub_score,
)


# ── _is_pharma (industry routing) ──────────────────────────────────────


@pytest.mark.parametrize(
    "profile,expected",
    [
        ({"sector": "Healthcare"}, True),
        ({"sector": "Healthcare", "industry": "Whatever"}, True),
        ({"industry": "Drug Manufacturers - General"}, True),
        ({"industry": "Biotechnology"}, True),
        ({"industry": "Medical Devices"}, True),
        ({"industry": "Pharma Wholesale"}, True),
        # Non-pharma sectors / industries:
        ({"sector": "Technology", "industry": "Software - Infrastructure"}, False),
        ({"sector": "Financial Services", "industry": "Banks - Regional"}, False),
        ({"sector": "Industrials", "industry": "Aerospace & Defense"}, False),
        ({}, False),
    ],
)
def test_is_pharma_routing(profile, expected):
    """FDA lookup only runs for pharma-adjacent tickers — others would
    waste the API call and return zero approvals anyway."""
    assert _is_pharma(profile) == expected


def test_is_pharma_case_insensitive_industry():
    """Industry keyword match is case-insensitive."""
    assert _is_pharma({"industry": "DRUG MANUFACTURERS - GENERAL"}) is True
    assert _is_pharma({"industry": "BiOtEcHnOlOgY"}) is True


# ── _normalize_assignee ────────────────────────────────────────────────


def test_normalize_assignee_strips_whitespace_and_suffix():
    # Strips trailing corporate suffix (Inc., Corp., Ltd., LLC, etc.) so
    # USPTO phrase search hits subsidiary entities too — Oracle's patents
    # are under "Oracle International Corporation" / "Oracle America,
    # Inc.", which a bare "Oracle" prefix matches but a full "Oracle
    # Corporation" phrase does not (live probe: 1 vs 3,787 hits).
    assert _normalize_assignee("  Apple Inc.  ") == "Apple"
    assert _normalize_assignee("Oracle Corporation") == "Oracle"
    assert _normalize_assignee("Microsoft Corp") == "Microsoft"


def test_normalize_assignee_handles_non_string():
    assert _normalize_assignee(None) == ""  # type: ignore[arg-type]
    assert _normalize_assignee(123) == ""    # type: ignore[arg-type]


def test_normalize_assignee_handles_chained_suffix_and_ampersand():
    # "Holdings, Inc." / "& Co." chains collapse cleanly; preserved
    # internal `&` (AT&T) is not mistaken for a trailing connector.
    assert _normalize_assignee("Berkshire Hathaway Inc.") == "Berkshire Hathaway"
    assert _normalize_assignee("JPMorgan Chase & Co.") == "JPMorgan Chase"
    assert _normalize_assignee("AT&T Inc.") == "AT&T"
    assert _normalize_assignee("AstraZeneca PLC") == "AstraZeneca"


# ── patents_per_employee_to_sub_score ──────────────────────────────────


@pytest.mark.parametrize(
    "patents_pe,expected",
    [
        (2.0, 9.5),     # elite (IBM / Qualcomm tier)
        (1.0, 9.5),     # at the cap threshold
        (0.7, 8.5),
        (0.5, 8.5),     # at the >= 0.5 threshold
        (0.3, 7.0),
        (0.2, 7.0),     # at the >= 0.2 threshold
        (0.1, 5.5),
        (0.05, 5.5),    # at the >= 0.05 threshold
        (0.01, 4.0),    # some IP, but low intensity
    ],
)
def test_patents_per_employee_bands(patents_pe, expected):
    assert patents_per_employee_to_sub_score(patents_pe) == expected


def test_patents_per_employee_zero_returns_none():
    """Zero patents → no signal; don't contribute to scoring."""
    assert patents_per_employee_to_sub_score(0) is None
    assert patents_per_employee_to_sub_score(0.0) is None
    assert patents_per_employee_to_sub_score(-0.5) is None
    assert patents_per_employee_to_sub_score(None) is None


# ── fda_approvals_to_sub_score ─────────────────────────────────────────


@pytest.mark.parametrize(
    "fda_active,expected",
    [
        (200, 9.5),    # mega-pharma (PFE, JNJ, MRK tier)
        (50, 9.5),     # at the >= 50 threshold
        (30, 8.5),
        (20, 8.5),     # at the >= 20 threshold
        (15, 7.5),
        (10, 7.5),     # at the >= 10 threshold
        (7, 6.5),
        (5, 6.5),      # at the >= 5 threshold
        (3, 5.5),      # >= 1
        (1, 5.5),      # at the >= 1 threshold
    ],
)
def test_fda_approvals_bands(fda_active, expected):
    assert fda_approvals_to_sub_score(fda_active) == expected


def test_fda_approvals_zero_returns_none():
    """Zero approvals → no signal."""
    assert fda_approvals_to_sub_score(0) is None
    assert fda_approvals_to_sub_score(None) is None
    assert fda_approvals_to_sub_score(-1) is None


# ── End-to-end IP wiring into moat scoring ─────────────────────────────


@pytest.mark.asyncio
async def test_intangible_assets_gains_high_confidence_with_ip_data():
    """When ip_intel supplies patents_per_employee + fda_active_approvals,
    the Intangible Assets pillar reaches HIGH confidence (4 metrics
    resolved: rd_to_rev + intangibles_to_assets + patents + fda).
    """
    from app.services.moat_scoring_service import (
        MoatScoringService, PILLAR_INTANGIBLE,
    )

    class _FakeLookup:
        def get_sector_benchmarks_with_n(self, sector, metrics, period_type):
            return {
                "rd_to_revenue": {"2025": {"median": 8.0, "n": 60}},
                "intangibles_to_assets": {"2025": {"median": 30.0, "n": 60}},
            }

    svc = MoatScoringService()
    svc._lookup = _FakeLookup()  # type: ignore[assignment]

    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={"sector": "Technology"},
        income=[{
            "date": "2024-12-31",
            "revenue": 1_000_000_000,
            "researchAndDevelopmentExpenses": 200_000_000,   # 20% R&D
        }],
        balance=[{
            "date": "2024-12-31",
            "totalAssets": 5_000_000_000,
            "goodwill": 1_000_000_000,
            "intangibleAssets": 500_000_000,                  # 30% intangibles
        }],
        ratios=[],
        industry_tam=None,
        ip_intel={
            "patents_per_employee": 0.5,    # → 8.5 sub-score
            "fda_active_approvals": 0,      # absent — won't contribute
            "patents_recent_5y": 500,
        },
    )
    intang = results[PILLAR_INTANGIBLE]
    # 3 resolved metrics: rd, intangibles, patents (fda was 0 → dropped)
    assert intang.confidence == "high"
    assert intang.score is not None
    # Drivers carry the patents data
    metrics = {d.metric: d for d in intang.drivers}
    assert "patents_per_employee" in metrics
    assert metrics["patents_per_employee"].sub_score == 8.5


@pytest.mark.asyncio
async def test_intangible_assets_pharma_gains_fda_driver():
    from app.services.moat_scoring_service import (
        MoatScoringService, PILLAR_INTANGIBLE,
    )

    class _FakeLookup:
        def get_sector_benchmarks_with_n(self, sector, metrics, period_type):
            return {
                "rd_to_revenue": {"2025": {"median": 18.0, "n": 60}},
                "intangibles_to_assets": {"2025": {"median": 40.0, "n": 60}},
            }

    svc = MoatScoringService()
    svc._lookup = _FakeLookup()  # type: ignore[assignment]

    results = svc.score(
        sector="Healthcare", industry="Drug Manufacturers - General",
        profile={"sector": "Healthcare"},
        income=[{
            "date": "2024-12-31",
            "revenue": 50_000_000_000,
            "researchAndDevelopmentExpenses": 10_000_000_000,
        }],
        balance=[{
            "date": "2024-12-31",
            "totalAssets": 200_000_000_000,
            "goodwillAndIntangibleAssets": 100_000_000_000,
        }],
        ratios=[],
        industry_tam=None,
        ip_intel={
            "patents_per_employee": 0,
            "fda_active_approvals": 25,    # → 8.5 sub-score
        },
    )
    intang = results[PILLAR_INTANGIBLE]
    metrics = {d.metric: d for d in intang.drivers}
    assert "fda_active_approvals" in metrics
    assert metrics["fda_active_approvals"].sub_score == 8.5
