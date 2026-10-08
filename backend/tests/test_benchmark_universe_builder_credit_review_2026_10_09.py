"""The builder names every unclassified member of a MIXED lender industry (2026-10-09).

"Financial - Credit Services" holds card networks beside lenders. A member is gated as a
lender until classified (the safe default), but a new payment or fee business there would
lose its liquidity rows and be compared with lenders, so the build names it at WARNING:
neither a curated non-lender (`NON_LENDER_MEMBERS`) nor a reviewed lender
(`REVIEWED_CREDIT_SERVICES_LENDERS`).
"""
from __future__ import annotations

import logging

import pytest

import scripts.build_benchmark_universe as bu
from app.services import financials_metric_gate as gate

CREDIT = "Financial - Credit Services"


def _r(sym: str, name: str = "Some Co.") -> dict:
    return {"symbol": sym, "companyName": name}


def test_known_members_are_silent():
    rows = {CREDIT: [_r("V"), _r("MA"), _r("AXP"), _r("COF"), _r("TREE")]}
    assert bu._unreviewed_mixed_members(rows) == []


def test_an_unclassified_member_is_named():
    rows = {CREDIT: [_r("V"), _r("NEWPAY", "New Payments Inc.")]}
    assert bu._unreviewed_mixed_members(rows) == [f'NEWPAY "New Payments Inc." [{CREDIT}]']


@pytest.mark.parametrize("industry", [
    "Financial – Credit Services", " financial - credit services ", "Financial—Credit Services",
])
def test_the_industry_spelling_variants_are_mixed_too(industry):
    assert bu._unreviewed_mixed_members({industry: [_r("NEWPAY")]})


def test_other_industries_are_never_reviewed():
    rows = {"Banks - Regional": [_r("NEWBANK")], "Software - Application": [_r("APP")]}
    assert bu._unreviewed_mixed_members(rows) == []


def test_symbol_case_and_whitespace_are_normalised():
    assert bu._unreviewed_mixed_members({CREDIT: [_r(" axp "), _r("v")]}) == []


def test_junk_symbols_never_raise():
    rows = {CREDIT: [{"symbol": None}, {"symbol": ""}, {}]}
    assert bu._unreviewed_mixed_members(rows) == []


def test_the_warning_names_every_unreviewed_member(caplog):
    rows = {CREDIT: [_r("AXP"), _r("NEWPAY"), _r("ZZZ")]}
    with caplog.at_level(logging.INFO, logger=bu.logger.name):
        bu._log_unreviewed_mixed_members(rows)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    msg = warns[0].getMessage()
    assert "NEWPAY" in msg and "ZZZ" in msg and "AXP" not in msg
    assert "REVIEWED_CREDIT_SERVICES_LENDERS" in msg


def test_no_warning_when_everyone_is_classified(caplog):
    with caplog.at_level(logging.INFO, logger=bu.logger.name):
        bu._log_unreviewed_mixed_members({CREDIT: [_r("AXP"), _r("V")]})
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_two_classified_sets_are_disjoint_and_cover_todays_members():
    """Every reviewed lender and every curated non-lender is classified exactly once."""
    assert not set(gate.REVIEWED_CREDIT_SERVICES_LENDERS) & set(gate.NON_LENDER_MEMBERS)
    rows = {CREDIT: [_r(t) for t in (*gate.REVIEWED_CREDIT_SERVICES_LENDERS,
                                     *gate.NON_LENDER_MEMBERS)]}
    assert bu._unreviewed_mixed_members(rows) == []


def test_main_runs_the_review_after_the_twin_pass():
    """Source order inside main(): the review reads the rows that will vote."""
    import inspect
    src = inspect.getsource(bu.main)
    assert src.index("_one_vote_per_statement_set(") < src.index("_log_unreviewed_mixed_members(")
