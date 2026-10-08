"""Snapshot cards + Health Check, review round 3 fixes (2026-10-08).

R3-CARDS-4  The Profitability card's quarterly balance-sheet leg (the SECOND negative-equity
            witness for ROE) degraded the build whenever it failed — for almost every
            ticker, since nearly every company shows a ROE beside a positive D/E. A
            degraded card is never persisted, and the report drops it and is not
            shared-cached, so one 429 on this extra call during a rebuild burst cost a full
            report regeneration. Now the leg counts only when it could change the outcome:
            it failed AND ratios-TTM D/E is no witness (missing, unreadable, or exactly 0).
            With a usable D/E the ROE is judged on D/E alone and the missing witness is
            logged at WARNING. A balance sheet that ANSWERS still overrides a positive D/E.
R3-CARDS-5  `_bank_pooled_sector_cell` is PERMANENT: once the producer rebuilds the
            Financial Services sector rows without banks and insurers, what is left of the
            sector for current / quick ratio and interest coverage is shell companies,
            exchanges and developers — still not a peer group.
Rating 0    A gated financial's Financial Health card (rating 0, no weighted_score) keeps
            its scored D/E row on both paths, through both cache tiers and the Overview, and
            nothing formats or divides the None weighted_score.

Hermetic: stubbed FMP, Profit Power, Health Check, Supabase and benchmark rows.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import test_cards_round2_2026_10_07 as r2
import test_health_check_deepcheck as hcd
import test_snapshot_cards_2026_10_07_profitability_health as sph
from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services import health_check_service as hc
from app.services import health_snapshot_service as hsnap
from app.services import profitability_snapshot_service as ps
from app.services import sector_benchmark_lookup as sbl

_GATED = {"interest_coverage", "current_ratio", "quick_ratio"}
_MISSING_WITNESS = "balance-sheet leg FAILED"


def _ratios_without_de(**de_fields: Any) -> Dict[str, Any]:
    ratios = {k: v for k, v in sph._RATIOS.items() if k != "debtToEquityRatioTTM"}
    ratios.update(de_fields)
    return ratios


def _witness_warnings(caplog) -> List[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == ps.__name__ and r.levelno == logging.WARNING
        and _MISSING_WITNESS in r.getMessage()
    ]


# ══════════════════════════════════════════════════════════════════════════════════════
# R3-CARDS-4 — the balance-sheet leg degrades only when it could change the outcome
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    FMPRateLimitException("429"), FMPUnavailableException("503"), RuntimeError("socket"),
])
async def test_a_failed_balance_sheet_beside_a_positive_de_is_not_degradation(
    monkeypatch, caplog, failure,
):
    """D/E 1.2 (positive) is a witness on its own: ROE is judged and scored, the build is
    clean, and the missing second witness is named at WARNING with its identifiers.
    Mutation: the old `bs_needed` (ROE shown and D/E not negative) answers
    ["balance_sheet"] here."""
    svc = sph._prof(monkeypatch, bs=failure)
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []
    roe = sph._m(snap, "roe")
    assert roe.value == "30.00%" and roe.score == 5          # 1.50x the 20% peer median
    assert roe.name == "Return on Equity (ROE) (1.50x sector avg 20.0%)"
    assert snap.rating >= 1 and snap.weighted_score is not None
    warnings = _witness_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "MCD" in warnings[0] and type(failure).__name__ in warnings[0]
    assert "D/E=1.2" in warnings[0], "the deciding witness must be in the log line"


@pytest.mark.asyncio
async def test_the_legacy_de_field_is_a_witness_too(monkeypatch, caplog):
    svc = sph._prof(monkeypatch, ratios=[_ratios_without_de(debtToEquityRatio=0.9)],
                    bs=FMPRateLimitException("429"))
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == [] and sph._m(snap, "roe").score is not None
    assert len(_witness_warnings(caplog)) == 1


@pytest.mark.asyncio
async def test_that_build_is_persisted_and_keeps_the_report_cacheable(monkeypatch):
    """The reviewed failure: a 429 on the balance sheet during the rebuild burst dropped the
    card from a 20-credit report and kept the report out of the shared cache."""
    from app.services.agents.ticker_report_data_collector import _settle_snapshot_result

    svc = sph._prof(monkeypatch, bs=FMPRateLimitException("429"))
    r2._fresh_prof_tiers(monkeypatch, svc)
    written = r2._spy_persist(monkeypatch, svc)
    result = await svc.get_profitability_snapshot_with_status("MCD")
    assert result[1] == [] and written == ["MCD"]

    out = SimpleNamespace(degraded_sections=[], snap_profitability="unset")
    _settle_snapshot_result(out, "snap_profitability", result, "MCD")
    assert out.snap_profitability is result[0], "the card was dropped from the report"
    assert out.degraded_sections == [], "the report would skip the shared cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("label,de_fields", [
    ("absent", {}),
    ("null", {"debtToEquityRatioTTM": None}),
    ("nan_string", {"debtToEquityRatioTTM": "NaN"}),
    ("infinite", {"debtToEquityRatioTTM": float("inf")}),
    ("junk_string", {"debtToEquityRatioTTM": "n/a"}),
    # float(True) == 1.0 would pass for a positive witness (hardened in `_safe_float`).
    ("bool", {"debtToEquityRatioTTM": True}),
    # Zero debt — or FMP's zero-fill — says nothing about the SIGN of equity.
    ("zero", {"debtToEquityRatioTTM": 0.0}),
    ("zero_legacy_only", {"debtToEquityRatio": 0}),
])
async def test_with_no_usable_de_a_failed_balance_sheet_is_degradation(
    monkeypatch, caplog, label, de_fields,
):
    """No other witness: the ROE shown may be sign-flipped, so the build is degraded —
    served for 5 min, never persisted, recorded by the report. Mutations: dropping the
    `!= 0` test passes "zero"; dropping the bool guard passes "bool"."""
    from app.services.agents.ticker_report_data_collector import _settle_snapshot_result

    svc = sph._prof(monkeypatch, ratios=[_ratios_without_de(**de_fields)],
                    bs=FMPRateLimitException("429"))
    r2._fresh_prof_tiers(monkeypatch, svc)
    written = r2._spy_persist(monkeypatch, svc)
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        result = await svc.get_profitability_snapshot_with_status("MCD")
    assert result[1] == ["balance_sheet"], label
    assert written == [], f"{label}: an unwitnessed ROE was pinned in the 24 h tier"
    assert _witness_warnings(caplog) == [], "nothing was judged on D/E alone"
    out = SimpleNamespace(degraded_sections=[], snap_profitability="unset")
    _settle_snapshot_result(out, "snap_profitability", result, "MCD")
    assert out.snap_profitability is None and out.degraded_sections, label


@pytest.mark.asyncio
async def test_a_negative_de_rules_alone_and_logs_no_missing_witness(monkeypatch, caplog):
    svc = sph._prof(monkeypatch, ratios=[dict(sph._RATIOS, debtToEquityRatioTTM=-4.0)],
                    km=[{"returnOnEquityTTM": -2.16, "returnOnAssetsTTM": 0.14}],
                    bs=FMPRateLimitException("429"))
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []
    roe = sph._m(snap, "roe")
    assert roe.value == "N/M" and roe.score is None
    assert _witness_warnings(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("km", [[{"returnOnAssetsTTM": 0.14}], [{"returnOnEquityTTM": None}],
                                [{"returnOnEquityTTM": "NaN"}]])
async def test_no_roe_to_judge_means_no_witness_needed(monkeypatch, caplog, km):
    svc = sph._prof(monkeypatch, ratios=[_ratios_without_de()], km=km,
                    bs=FMPRateLimitException("429"))
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        _snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == [] and _witness_warnings(caplog) == []


@pytest.mark.asyncio
async def test_an_unentitled_balance_sheet_with_no_de_is_not_degradation(monkeypatch):
    """A permanent refusal is never retried into a cacheable build: it does not count."""
    svc = sph._prof(monkeypatch, ratios=[_ratios_without_de()],
                    bs=FMPNotEntitledException("402"))
    _snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("de_fields,equity", [
    ({"debtToEquityRatioTTM": 0.8}, -2.0e9),     # ratios-TTM lags the quarter
    ({"debtToEquityRatioTTM": 0.8}, -1.0),       # barely negative still flips the sign
    ({}, -3.0e9),                                 # no D/E at all
    ({"debtToEquityRatioTTM": 0.0}, -5.0e8),
])
async def test_a_balance_sheet_that_answers_still_overrides_a_positive_de(
    monkeypatch, caplog, de_fields, equity,
):
    """The witness is not weakened when it ANSWERS: negative equity → ROE "N/M",
    unscored, clean build (company state)."""
    svc = sph._prof(monkeypatch, ratios=[_ratios_without_de(**de_fields)],
                    km=[{"returnOnEquityTTM": 1.1, "returnOnAssetsTTM": 0.14}],
                    bs=[{"totalStockholdersEquity": equity}])
    with caplog.at_level(logging.WARNING, logger=ps.__name__):
        snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []
    roe = sph._m(snap, "roe")
    assert roe.value == "N/M" and roe.score is None and roe.peer_level is None
    assert snap.rating >= 1, "the other four rows still rate the card"
    assert _witness_warnings(caplog) == []


@pytest.mark.parametrize("raw", [True, False])
def test_safe_float_refuses_a_bool(raw):
    assert ps._safe_float({"x": raw}, "x") is None
    assert ps._safe_float({"x": 1}, "x") == 1.0           # control: an int is a number


# ══════════════════════════════════════════════════════════════════════════════════════
# R3-CARDS-5 — the Financial Services sector median is never a liquidity peer group
# ══════════════════════════════════════════════════════════════════════════════════════


# The FS sector TTM cell AFTER the gated producer's rebuild: banks, insurers, lenders and
# capital-markets firms left out, so it pools exchanges / data vendors, shell companies,
# real-estate developers (and insurance brokers, for coverage) — n≈33, mature, and
# shell-shaped (a SPAC's trust account is all current assets).
_FS_SECTOR_REBUILT = {"current_ratio": (6.5, 33), "quick_ratio": (6.1, 33),
                      "interest_coverage": (3.0, 33), "debt_to_equity": (1.0, 33),
                      "pe_ratio": (14.0, 33), "roe": (0.10, 33)}


def _rebuilt_rows(industry_n: int) -> Dict[tuple, Dict[str, tuple]]:
    industry = {m: (v * 3, industry_n) for m, (v, _n) in _FS_SECTOR_REBUILT.items()}
    return {("sector", sbl.TTM_PERIOD_TYPE): dict(_FS_SECTOR_REBUILT),
            ("industry", sbl.TTM_PERIOD_TYPE): industry}


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,industry,shown", [
    ("SPGI", "Financial - Data & Stock Exchanges", _GATED),
    ("AON", "Insurance - Brokers", {"interest_coverage"}),
])
async def test_the_rebuilt_shell_and_exchange_sector_median_is_still_refused(
    monkeypatch, symbol, industry, shown,
):
    """The guard does not expire with the producer's rebuild: a mature (n=33) FS sector
    cell that holds no bank is refused exactly like the bank-pooled one. Mutation: making
    `_bank_pooled_sector_cell` answer False compares SPGI's 0.9 current ratio with a
    shell-dominated 6.5 ("well below sector average")."""
    monkeypatch.setattr(sbl, "_cache", {})
    lookup = r2._RowLookup(_rebuilt_rows(industry_n=10))
    cells = lookup.get_current_benchmarks(industry, "Financial Services", sorted(_GATED))
    assert all(c["level"] == "sector" and c["n"] == 33 for c in cells.values()), cells

    monkeypatch.setattr(sbl, "_cache", {})
    resp = await r2._hc_build(monkeypatch, hcd._answers(
        profile=r2._profile("Financial Services", industry, symbol),
        ratios=[dict(r2._EXCHANGE_RATIOS)],
    ), lookup)
    by = r2._by_type(resp)
    assert set(by) & _GATED == shown
    for kind in shown:
        m = by[kind]
        assert m.comparison_value is None and m.peer_level is None, (symbol, kind)
        assert m.status == hc._absolute_status(kind, m.value), (symbol, kind)
        assert "average" not in hcd._rendered(m), hcd._rendered(m)
    # D/E is not a gated metric: the sector median is still its comparison.
    assert by["debt_to_equity"].comparison_value == 1.0
    assert by["debt_to_equity"].peer_level == "sector"
    assert resp.degraded == []
    r2._counts_consistent(resp)


def _comment_block_above(source: str, anchor: str) -> str:
    """The contiguous `#` comment lines directly above the line starting with ``anchor``."""
    lines = source.splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith(anchor))
    block: List[str] = []
    for line in reversed(lines[:idx]):
        if not line.startswith("#"):
            break
        block.append(line)
    return "\n".join(reversed(block))


_EXPIRY_WORDING = re.compile(
    r"remove this rule|until the producer|after the january|temporary", re.IGNORECASE,
)


def test_the_guard_is_documented_as_permanent():
    """Owner decision 2026-10-08: the guard has no removal date. Its comment and docstring
    must not schedule one (OWNER_TASKS item 9 used to say "remove after the January run")."""
    block = _comment_block_above(inspect.getsource(hc), "_BANK_POOLED_SECTORS =")
    assert block, "the guard lost its explanation"
    assert "PERMANENT" in block and "shell" in block.lower()
    assert not _EXPIRY_WORDING.search(block), _EXPIRY_WORDING.search(block)
    doc = hc._bank_pooled_sector_cell.__doc__ or ""
    assert "permanent" in doc.lower() and not _EXPIRY_WORDING.search(doc)
    snapshot_src = inspect.getsource(hsnap)
    assert "PERMANENT rule" in snapshot_src and "bank-pooled, no comparison" not in snapshot_src


# ══════════════════════════════════════════════════════════════════════════════════════
# Rating 0 — a gated financial's Financial Health card keeps its scored D/E row
# ══════════════════════════════════════════════════════════════════════════════════════


class _SnapshotTable:
    """In-memory `snapshot_cache`: the upsert stores the row, the select reads it back."""

    def __init__(self) -> None:
        self.row: Dict[str, Any] = {}

    def upsert(self, payload, on_conflict=None):
        self.row = dict(payload)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[payload]))

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.row] if self.row else [])


def _assert_unrated_with_scored_de(snap, de_score=None) -> None:
    assert snap.category == "Financial Health"
    assert snap.rating == 0 and snap.weighted_score is None
    de = next(m for m in snap.metrics if m.metric_key == "debt_to_equity")
    assert de.value not in (None, "", "—") and de.score is not None
    if de_score is not None:
        assert de.score == de_score
    dumped = snap.model_dump()
    assert dumped["weighted_score"] is None and dumped["rating"] == 0
    json.dumps(dumped, allow_nan=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("de_status,de_score", [("positive", 4), ("neutral", 3),
                                                 ("negative", 2)])
async def test_the_unrated_bank_card_survives_both_cache_tiers(monkeypatch, de_status,
                                                                de_score):
    hs, svc = sph._health_service(monkeypatch, r2._bank_health(de_status), profile=r2._BANK)
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_degraded_by_key", {})
    table = _SnapshotTable()
    svc.supabase = SimpleNamespace(table=lambda name: table)

    # Tier 2 miss → build → write-through (run inline: the real write is fire-and-forget).
    written = r2._spy_persist(monkeypatch, svc)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    snap, degraded = await svc.get_health_snapshot_with_status("C")
    assert degraded == [] and written == ["C"]
    _assert_unrated_with_scored_de(snap, de_score)

    # Tier 1 hit: the same object, the same status.
    again, degraded2 = await svc.get_health_snapshot_with_status("C")
    assert again is snap and degraded2 == []

    # Tier 2 round trip through the REAL writer and reader.
    hs.HealthSnapshotService._upsert_supabase_cache(svc, "C", snap)
    assert table.row["response_json"]["weighted_score"] is None
    assert table.row["response_json"]["rating"] == 0
    read = hs.HealthSnapshotService._check_supabase_cache(svc, "C")
    assert read is not None, "the persisted unrated card did not read back"
    _assert_unrated_with_scored_de(read, de_score)
    assert [m.metric_key for m in read.metrics] == ["debt_to_equity"]


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", [
    "Banks - Diversified", "Banks - Regional", "Insurance - Life",
    "Insurance - Property & Casualty", "Financial - Capital Markets",
    "Financial - Credit Services", "Asset Management", "Financial - Mortgages",
])
async def test_the_fallback_card_of_every_gated_financial_keeps_a_scored_de_row(
    monkeypatch, industry,
):
    bank = dict(r2._BANK, industry=industry)
    _hs, svc = sph._health_service(monkeypatch, RuntimeError("health check exploded"),
                                   profile=bank, bench={"debt_to_equity": (1.5, "industry")})
    snap, degraded = await svc._compute_with_status("C")
    assert [m.metric_key for m in snap.metrics] == ["debt_to_equity"], industry
    _assert_unrated_with_scored_de(snap)
    de = snap.metrics[0]
    assert de.value == "0.20" and de.peer_level == "industry"
    assert de.name == "Debt-to-Equity (vs sector 1.50)"     # shipped iOS strips by "sector"
    assert degraded == ["health_check"]


@pytest.mark.asyncio
async def test_the_overview_serves_the_unrated_bank_card_as_built(monkeypatch):
    """The Overview hands the snapshot service's card through untouched (no re-rating, no
    arithmetic on its None weighted_score), and the whole snapshot list serialises."""
    _hs, svc = sph._health_service(monkeypatch, r2._bank_health("neutral"), profile=r2._BANK)
    card, _ = await svc._compute_with_status("C")
    snaps = r2._overview()._build_snapshots(
        [{}], [{}], [{}], [dict(sph._HC_BS, totalDebt=20.0)], [{}], 100.0, 1e10,
        "Financial Services", health_snapshot=card, industry="Banks - Diversified",
    )
    health = next(s for s in snaps if s.category == "Financial Health")
    assert health is card
    _assert_unrated_with_scored_de(health, 3)
    json.dumps([s.model_dump() for s in snaps], allow_nan=False)


def test_the_card_weighted_consumer_maps_none_to_unmeasured():
    """The one downstream reader of the card's `weighted_score` (the report's vitals)."""
    from app.services.agents.ticker_report_data_collector import _card_weighted_to_score10

    assert _card_weighted_to_score10(None) is None

