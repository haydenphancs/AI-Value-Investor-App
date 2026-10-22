"""Industry concentration from REVENUE shares (owner decision 2026-10-22, "Revenue-share HHI").

Market-cap shares let one richly valued listing decide an industry's label: SpaceX's $1.9T cap
was 54% of US-listed Aerospace & Defense (cap HHI ~3,100 → "oligopoly") while its ~$31B revenue
is under 5% of the industry's $633B (revenue HHI ~867 → "fragmented"). These tests pin
`_revenue_concentration`, the dossier fields that carry its basis and the growth window, and the
recompute rule that a universe file without revenues KEEPS the stored concentration.

Hermetic: Supabase, the universe and `_compute_one` are faked.
"""

from __future__ import annotations

import logging
import math
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app.services.industry_dossier_service as ids
from app.services.industry_dossier_service import (
    IndustryDossier,
    IndustryDossierService,
    _compute_hhi,
    _revenue_concentration,
    classify_concentration,
)

LOGGER = "app.services.industry_dossier_service"

# A&D-like constituents, 2026-10-22 (revenue = cap ÷ P/S, $B): the seven leaders plus a tail.
_AD_REVENUE_B = {"RTX": 96.0, "BA": 94.0, "LMT": 79.1, "GD": 54.9, "GE": 52.6, "NOC": 43.6,
                 "SPCX": 31.2, "HWM": 8.0, "TDG": 8.8, "LHX": 21.9, "HEI": 4.3, "TXT": 14.2}
_AD_CAP_B = {"RTX": 249.0, "BA": 148.4, "LMT": 117.2, "GD": 89.3, "GE": 317.1, "NOC": 68.8,
             "SPCX": 1906.8, "HWM": 89.0, "TDG": 60.9, "LHX": 44.1, "HEI": 41.2, "TXT": 12.6}


def _b(d: Dict[str, float]) -> Dict[str, float]:
    return {k: v * 1e9 for k, v in d.items()}


def test_revenue_shares_not_caps_decide_the_label():
    tickers = sorted(_AD_CAP_B)
    conc = _revenue_concentration("Aerospace & Defense", tickers, _b(_AD_CAP_B), _b(_AD_REVENUE_B))
    assert conc["concentration_basis"] == "revenue_share"
    assert conc["concentration_label"] == "fragmented"
    assert conc["hhi"] < 1500
    assert conc["top1_share_pct"] == round(96.0 / sum(_AD_REVENUE_B.values()) * 100, 2)
    assert conc["constituent_count"] == len(_AD_REVENUE_B)
    # The same constituents on market cap: SpaceX alone forces "oligopoly".
    caps = sorted(_AD_CAP_B.values(), reverse=True)
    cap_top1 = caps[0] / sum(caps) * 100
    assert classify_concentration(cap_top1, (caps[0] + caps[1]) / sum(caps) * 100,
                                  _compute_hhi(caps)) == "oligopoly"


def test_fewer_than_three_positive_revenues_is_unmeasured_and_zeros_count_for_coverage():
    caps = _b({"A": 10.0, "B": 10.0, "C": 10.0})
    conc = _revenue_concentration("X", list(caps), caps, _b({"A": 5.0, "B": 3.0, "C": 0.0}))
    assert all(v is None for v in conc.values())          # two positive revenues: unmeasured
    # Zeros cover their cap: three positives + one pre-revenue zero is measured.
    caps4 = _b({"A": 10.0, "B": 10.0, "C": 10.0, "Z": 70.0})
    conc4 = _revenue_concentration("X", list(caps4), caps4,
                                   _b({"A": 5.0, "B": 3.0, "C": 2.0, "Z": 0.0}))
    assert conc4["concentration_label"] is not None and conc4["constituent_count"] == 3


def test_low_coverage_is_unmeasured_and_names_the_missing(caplog):
    caps = _b({"BIG": 900.0, "A": 30.0, "B": 30.0, "C": 40.0})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        conc = _revenue_concentration("X", list(caps), caps, _b({"A": 5.0, "B": 3.0, "C": 2.0}))
    assert all(v is None for v in conc.values())
    assert "BIG" in caplog.text and "cover" in caplog.text


def test_a_corrupt_leader_ps_is_unmeasured(caplog):
    caps = _b({"A": 1.0, "B": 10.0, "C": 10.0})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        conc = _revenue_concentration("X", list(caps), caps, _b({"A": 500.0, "B": 3.0, "C": 2.0}))
    assert all(v is None for v in conc.values())
    assert "corrupt" in caplog.text and "A" in caplog.text


@pytest.mark.parametrize("bad", [-5e9, float("nan"), float("inf"), "4e9", True, None])
def test_negative_nan_and_string_revenues_are_ignored(bad):
    caps = _b({"A": 10.0, "B": 10.0, "C": 10.0, "D": 0.5})
    revs: Dict[str, Any] = _b({"A": 5.0, "B": 3.0, "C": 2.0})
    revs["D"] = bad
    conc = _revenue_concentration("X", list(caps), caps, revs)
    assert conc["constituent_count"] == 3
    assert math.isfinite(conc["hhi"])


def test_tickers_outside_the_caps_never_count():
    caps = _b({"A": 10.0, "B": 10.0, "C": 10.0})
    revs = _b({"A": 5.0, "B": 3.0, "C": 2.0, "GHOST": 900.0})
    assert _revenue_concentration("X", ["A", "B", "C", "GHOST"], caps, revs)["constituent_count"] == 3


def test_never_monopoly_or_duopoly():
    assert classify_concentration(100.0, 100.0, 10000.0) == "oligopoly"
    caps = _b({"A": 10.0, "B": 10.0, "C": 10.0})
    conc = _revenue_concentration("X", list(caps), caps, _b({"A": 98.0, "B": 1.0, "C": 1.0}))
    assert conc["concentration_label"] == "oligopoly"


# ── The dossier row carries the basis and the growth window ─────────────────


def _row(**over) -> Dict[str, Any]:
    row = {
        "industry": "Aerospace & Defense", "sector": "Industrials",
        "current_tam_b": 342.8, "future_tam_b": 359.3, "current_year": "2024", "future_year": "2029",
        "cagr_5y_pct": 0.9, "lifecycle_phase": "mature", "hhi": 866.8, "top1_share_pct": 15.17,
        "top2_share_pct": 30.01, "concentration_label": "fragmented", "constituent_count": 86,
        "source_grain": "industry", "source_label": "US Census AIES — Aerospace & defense manufacturing",
        "tam_scope": "us", "cagr_start_year": 2017, "concentration_basis": "revenue_share",
    }
    row.update(over)
    return row


def test_from_db_row_reads_the_window_and_the_basis():
    d = IndustryDossier.from_db_row(_row())
    assert d.cagr_start_year == 2017 and d.concentration_basis == "revenue_share"
    assert d.to_db_row()["cagr_start_year"] == 2017
    assert d.to_db_row()["concentration_basis"] == "revenue_share"


@pytest.mark.parametrize("year,want", [("2017", 2017), (2018, 2018), ("x", None), (True, None),
                                       (1800, None), (None, None), (2200, None)])
def test_from_db_row_year_is_guarded(year, want):
    assert IndustryDossier.from_db_row(_row(cagr_start_year=year)).cagr_start_year == want


def test_from_db_row_unknown_basis_and_pre_migration_rows_read_none():
    assert IndustryDossier.from_db_row(_row(concentration_basis="caps")).concentration_basis is None
    pre = _row()
    del pre["cagr_start_year"], pre["concentration_basis"]
    d = IndustryDossier.from_db_row(pre)
    assert d.cagr_start_year is None and d.concentration_basis is None


def test_a_stored_label_with_no_hhi_reads_unmeasured():
    """The old '<3 caps → fragmented' default stored a label with NULL hhi."""
    assert IndustryDossier.from_db_row(_row(hhi=None)).concentration_label is None
    assert IndustryDossier.from_db_row(_row()).concentration_label == "fragmented"


def test_merge_live_tam_takes_the_live_window_and_keeps_the_stored_basis():
    stored = IndustryDossier.from_db_row(_row(current_tam_b=0.0, future_tam_b=0.0, cagr_5y_pct=None,
                                              cagr_start_year=None, concentration_basis="market_cap"))
    live = IndustryDossier(current_tam=342.8, future_tam=359.3, current_year="2024", future_year="2029",
                           source_label="US Census AIES", cagr_5y_pct=0.9, cagr_start_year=2017,
                           source_grain="industry")
    merged = ids._merge_live_tam(stored, live)
    assert merged.cagr_start_year == 2017
    assert merged.concentration_basis == "market_cap" and merged.concentration_label == "fragmented"
    no_rate = IndustryDossier(current_tam=342.8, future_tam=342.8, current_year="2024", future_year="2029",
                              source_label="US Census AIES", cagr_5y_pct=None, cagr_start_year=2017)
    assert ids._merge_live_tam(stored, no_rate).cagr_start_year is None


def test_withdraw_grounded_tam_clears_the_window():
    d = ids._withdraw_grounded_tam(IndustryDossier.from_db_row(_row(tam_scope="global")))
    assert d.cagr_start_year is None and d.current_tam == 0.0


@pytest.mark.asyncio
async def test_compute_one_without_revenues_offers_no_concentration(monkeypatch):
    import app.services.industry_tam_service as its

    async def _census(industry):
        return its.IndustryTAM(current_tam=342.8, future_tam=359.3, current_year="2024",
                               future_year="2029", source_label="US Census AIES", cagr_5y_pct=0.9,
                               cagr_start_year=2017)
    monkeypatch.setattr(ids, "_try_census_tam", _census)
    svc = IndustryDossierService()
    caps = _b(_AD_CAP_B)
    d = await svc._compute_one("Aerospace & Defense", "Industrials", sorted(caps), caps)
    assert d.concentration_label is None and d.hhi is None and d.concentration_basis is None
    assert d.cagr_start_year == 2017
    d2 = await svc._compute_one("Aerospace & Defense", "Industrials", sorted(caps), caps,
                                revenues_by_ticker=_b(_AD_REVENUE_B))
    assert d2.concentration_label == "fragmented" and d2.concentration_basis == "revenue_share"


# ── recompute_all keeps a legacy universe entry's stored concentration ───────


class _Query:
    def __init__(self, sb: "_SB") -> None:
        self.sb, self.op, self.payload = sb, "select", None

    def select(self, *_a, **_k):
        self.sb.select_cols.append(_a[0] if _a else "*")
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.payload = "upsert", rows
        return self

    def execute(self):
        if self.op == "upsert":
            self.sb.upserts.append(self.payload)
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[dict(r) for r in self.sb.rows])


class _SB:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows, self.upserts, self.select_cols = rows, [], []

    def table(self, _name):
        return _Query(self)


def _install(monkeypatch, universe: List[Dict[str, Any]], stored: List[Dict[str, Any]]) -> _SB:
    import app.integrations.census as census_mod
    import app.integrations.fred as fred_mod

    sb = _SB(stored)
    monkeypatch.setattr(ids, "get_supabase", lambda: sb)
    monkeypatch.setattr(ids, "_load_universe", lambda: universe)
    monkeypatch.setattr(fred_mod, "get_fred_client", lambda: SimpleNamespace(is_configured=True))
    monkeypatch.setattr(census_mod, "get_census_client", lambda: SimpleNamespace(is_configured=True))

    async def compute(self, industry, sector, tickers, caps_by_ticker, revenues_by_ticker=None):
        conc = (ids._revenue_concentration(industry, tickers, caps_by_ticker, revenues_by_ticker)
                if revenues_by_ticker is not None else ids._unmeasured_concentration())
        return IndustryDossier(
            current_tam=342.8, future_tam=359.3, current_year="2024", future_year="2029",
            source_label="US Census AIES", cagr_5y_pct=0.9, cagr_start_year=2017,
            industry=industry, sector=sector, source_grain="industry",
            hhi=conc["hhi"], top1_share_pct=conc["top1_share_pct"], top2_share_pct=conc["top2_share_pct"],
            concentration_label=conc["concentration_label"], constituent_count=conc["constituent_count"],
            concentration_basis=conc["concentration_basis"],
        )
    monkeypatch.setattr(IndustryDossierService, "_compute_one", compute)
    return sb


_CONC_KEYS = {"hhi", "top1_share_pct", "top2_share_pct", "concentration_label",
              "constituent_count", "concentration_basis"}


@pytest.mark.asyncio
async def test_recompute_keeps_stored_concentration_for_a_legacy_universe_entry(monkeypatch, caplog):
    caps = _b(_AD_CAP_B)
    universe = [
        {"industry": "Aerospace & Defense", "sector": "Industrials", "tickers": sorted(caps),
         "market_caps": caps},                                            # built before 10-22
        {"industry": "Trucking", "sector": "Industrials", "tickers": ["A", "B", "C"],
         "market_caps": _b({"A": 10.0, "B": 10.0, "C": 10.0}),
         "revenues": _b({"A": 5.0, "B": 3.0, "C": 2.0})},
    ]
    stored = [_row(hhi=770.88, concentration_label="fragmented", concentration_basis=None,
                   cagr_5y_pct=-0.2),
              _row(industry="Trucking", hhi=1200.0, concentration_label="fragmented")]
    sb = _install(monkeypatch, universe, stored)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = await IndustryDossierService().recompute_all()

    rows = [r for batch in sb.upserts for r in batch]
    by = {r["industry"]: r for r in rows}
    assert not (_CONC_KEYS & set(by["Aerospace & Defense"])), "legacy row must not carry concentration"
    assert by["Trucking"]["concentration_basis"] == "revenue_share"
    for batch in sb.upserts:                                          # PostgREST: uniform keys per batch
        assert len({frozenset(r) for r in batch}) == 1
    assert result["concentration_kept"] == 1 and result["rows_upserted"] == 2
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "carry no 'revenues'" in errors[0].getMessage()
    # The pre-read selects pre-migration columns only.
    assert all("cagr_start_year" not in c and "concentration_basis" not in c for c in sb.select_cols)


@pytest.mark.asyncio
async def test_recompute_writes_revenue_share_basis_and_logs_label_changes(monkeypatch, caplog):
    caps = _b(_AD_CAP_B)
    universe = [{"industry": "Aerospace & Defense", "sector": "Industrials", "tickers": sorted(caps),
                 "market_caps": caps, "revenues": _b(_AD_REVENUE_B)}]
    stored = [_row(hhi=770.88, concentration_label="fragmented", concentration_basis="market_cap",
                   cagr_5y_pct=-1.5)]
    sb = _install(monkeypatch, universe, stored)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = await IndustryDossierService().recompute_all()
    row = sb.upserts[0][0]
    assert row["concentration_basis"] == "revenue_share" and row["concentration_label"] == "fragmented"
    assert row["cagr_start_year"] == 2017
    assert result["concentration_kept"] == 0 and result["label_changes"] == 2
    line = next(r.getMessage() for r in caplog.records if "label changes vs stored" in r.getMessage())
    assert "fragmented→fragmented hhi 770.88→" in line and "lifecycle declining→mature" in line
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


@pytest.mark.asyncio
async def test_a_revenues_map_covering_none_of_the_tickers_keeps_the_stored_concentration(monkeypatch, caplog):
    caps = _b(_AD_CAP_B)
    universe = [{"industry": "Aerospace & Defense", "sector": "Industrials", "tickers": sorted(caps),
                 "market_caps": caps, "revenues": {"ZZZ": 1e9}}]
    sb = _install(monkeypatch, universe, [_row(hhi=770.88, concentration_basis="market_cap")])
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await IndustryDossierService().recompute_all()
    row = sb.upserts[0][0]
    assert not (_CONC_KEYS & set(row)) and result["concentration_kept"] == 1
    assert "covers none of its" in caplog.text
