"""Adversarial tests for the Trillion-Dollar Club 13F builder, the shared split helper, the
whale path that now calls it, and the new FMP wrappers.

Written by an independent reviewer, after the build, to attack the inputs the author's own
tests did not picture: three accessions filed out of order, same-day amendments, option and
bond rows mixed into a book, garbage numbers, stale or ambiguous symbols, an unresolved CUSIP
followed across many days of the daily job, a profile batch that half-fails, the 200/201 row
boundary on EITHER quarter, strict FMP failures on both sides at once, and the JSON/CHECK
guard in ``BuiltFiling.as_row``.

The whale-path parity section runs the extracted ``resolve_13f_split_adjustments`` against a
VERBATIM copy of the in-line block it replaced (``whale_service._process_13f_path`` at commit
27e7c629, before the extraction) over a seeded fuzz of quarter pairs and corporate-action
behaviours, comparing outputs AND the exact calls made to the corporate-actions seam — then
drives the real whale request path end to end on the same scenarios.

Tests named ``test_BUG_*`` expose a real defect and are left FAILING on purpose. Once a
defect is fixed its test is renamed ``test_regression_*`` and kept (its docstring opens
with "REGRESSION (fixed <date>). Was: ").

Hermetic: every FMP / corporate-actions / Supabase dependency is a local fake.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.integrations.fmp import (
    FMPClient,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services import thirteen_f_splits as tfs
from app.services import whale_service as wsvc
from app.services._whale_common import MAX_SPLIT_LOOKUPS
from app.services.corporate_actions_service import window_for_range
from app.services.trillion_club import builder as B
from app.services.trillion_club import rules as R
from app.services.whale_service import WhaleService

FIX = Path(__file__).parent / "fixtures" / "trillion_club"
EXTRACTS = json.loads((FIX / "extracts_2026.json").read_text())
EXTRACTS.update(json.loads((FIX / "extracts_berkshire_2023.json").read_text()))
EXTRACTS.pop("_meta", None)
PROFILES = {k: v for k, v in json.loads((FIX / "profiles.json").read_text()).items() if k != "_meta"}
SEARCH = json.loads((FIX / "search.json").read_text())

NVIDIA, ALPHABET, BRK = "0001045810", "0001652044", "0001067983"
TODAY = date(2026, 9, 24)
NV_Q2_TOTAL = 63_439_974_569


def _cusip(first8: str) -> str:
    """A CUSIP with a VALID check digit (so the builder derives a US ISIN for it)."""
    return first8 + str(R.cusip_check_digit(first8))


# ── fakes ─────────────────────────────────────────────────────────────────────────────


def _generic_profile(sym: str) -> Dict[str, Any]:
    return {"symbol": sym, "companyName": f"{sym} Holdings Inc.", "exchange": "NYSE",
            "ipoDate": "1999-01-04", "sector": "Industrials", "isActivelyTrading": True}


class FakeFMP:
    """Stand-in for FMPClient. Every knob breaks exactly one thing; every call is logged."""

    def __init__(self, extracts: Optional[Dict[str, Dict[str, list]]] = None, *,
                 profiles: Optional[Dict[str, dict]] = None, generic_profiles: bool = False,
                 dates: Optional[Dict[str, list]] = None):
        self.extracts = copy.deepcopy(extracts if extracts is not None else EXTRACTS)
        self.profiles = dict(profiles if profiles is not None else PROFILES)
        self.generic_profiles = generic_profiles
        self.dates = dates or {}
        self.extract_raise: Dict[tuple, BaseException] = {}
        self.isin_map: Dict[str, list] = dict(SEARCH["search_isin"])
        self.cusip_map: Dict[str, list] = dict(SEARCH["search_cusip"])
        self.isin_raise: Optional[BaseException] = None
        self.cusip_raise: Optional[BaseException] = None
        self.profile_raise_calls: set = set()       # indices of profile-batch calls that raise
        self.profile_junk: list = []                 # extra junk appended to every batch answer
        self.calls: Dict[str, list] = {"extract": [], "dates": [], "isin": [], "cusip": [], "profiles": []}

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        assert strict is True, "the builder must fetch strictly"
        key = f"{year}-Q{quarter}"
        self.calls["extract"].append((cik, key))
        if (cik, key) in self.extract_raise:
            raise self.extract_raise[(cik, key)]
        return copy.deepcopy(self.extracts.get(cik, {}).get(key, []))

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        assert strict is True
        self.calls["dates"].append(cik)
        return copy.deepcopy(self.dates.get(cik, []))

    async def search_isin(self, isin):
        self.calls["isin"].append(isin)
        if self.isin_raise:
            raise self.isin_raise
        return copy.deepcopy(self.isin_map.get(isin, []))

    async def search_cusip(self, cusip):
        self.calls["cusip"].append(cusip)
        if self.cusip_raise:
            raise self.cusip_raise
        return copy.deepcopy(self.cusip_map.get(cusip, []))

    async def get_company_profiles_batch(self, symbols):
        idx = len(self.calls["profiles"])
        self.calls["profiles"].append(list(symbols))
        assert len(symbols) <= 50, "get_company_profiles_batch silently truncates past 50"
        if idx in self.profile_raise_calls:
            raise FMPRateLimitException("429 on profile batch")
        out: list = []
        for s in symbols:
            p = self.profiles.get(s) or (_generic_profile(s) if self.generic_profiles else None)
            if p:
                out.append(copy.deepcopy(p))
        return out + copy.deepcopy(self.profile_junk)


class FakeActions:
    def __init__(self, splits=None, flags=None):
        self.splits, self.flags = splits or {}, flags or {}
        self.calls: list = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.calls.append(("split", t, from_date, to_date))
        v = self.splits.get(t, [])
        if isinstance(v, BaseException):
            raise v
        return v

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *,
                                          effective_from=None, effective_to=None):
        self.calls.append(("flag", t, from_date, to_date, effective_from, effective_to))
        v = self.flags.get(t, False)
        if isinstance(v, BaseException):
            raise v
        return v


def _build(fmp, cik, y, q, *, prev=True, actions=None, stored=None, today=TODAY, **kw):
    return asyncio.run(B.build_filing(
        fmp, cik, y, q, prev_quarter_available=prev, actions=actions or FakeActions(),
        stored_unresolved=stored or {}, today=today, **kw,
    ))


def _row(cusip, shares, value, acc, filed, *, symbol="ABC", name="ABC CORP", period="2026-06-30",
         cik=NVIDIA, accepted=None, **extra):
    r = {"securityCusip": cusip, "shares": shares, "value": value, "symbol": symbol,
         "nameOfIssuer": name, "titleOfClass": "COM", "putCallShare": "", "sharesType": "SH",
         "date": period, "filingDate": filed, "acceptedDate": accepted or filed, "cik": cik,
         "link": f"https://www.sec.gov/Archives/edgar/data/1/{acc.replace('-', '')}/{acc}-index.htm",
         "finalLink": ""}
    r.update(extra)
    return r


def _holding(bf, cusip):
    return next(h for h in bf.holdings if h["cusip"] == cusip)


def _nv_q2_row(fmp, symbol):
    return next(r for r in fmp.extracts[NVIDIA]["2026-Q2"] if r["symbol"] == symbol)


def _add_symbolless(fmp, cusip, name, *, period="2026-Q2", cik=NVIDIA, shares=1000, value=5_000_000):
    acc = "0001045810-26-000065" if period == "2026-Q2" else "0001045810-26-000042"
    filed = "2026-08-14" if period == "2026-Q2" else "2026-05-15"
    date_ = "2026-06-30" if period == "2026-Q2" else "2026-03-31"
    fmp.extracts[cik][period].append(
        _row(cusip, shares, value, acc, filed, symbol=None, name=name, period=date_, cik=cik))


# ── 1. per-accession amendment semantics ──────────────────────────────────────────────

X, Y, Z = "111111118", "222222226", "333333334"
ACC_A, ACC_C, ACC_B = "0000000001-26-000001", "0000000001-26-000005", "0000000001-26-000009"


def _three_accessions():
    return [
        _row(X, 100, 1000, ACC_A, "2026-08-14"),
        _row(Y, 50, 500, ACC_A, "2026-08-14", symbol="DEF"),
        _row(X, 120, 1200, ACC_C, "2026-09-01"),                    # restatement #1
        _row(Y, 55, 550, ACC_C, "2026-09-01", symbol="DEF"),
        _row(X, 130, 1300, ACC_B, "2026-11-02"),                    # restatement #2 ...
        _row(Z, 10, 100, ACC_B, "2026-11-02", symbol="GHI"),        # ... that also ADDS Z
    ]


def test_three_accessions_in_any_order_the_latest_filing_wins_per_cusip():
    """Every one of the 720 orderings FMP could return must give the same book."""
    raw = _three_accessions()
    want = {X: (130.0, 1300.0), Y: (55.0, 550.0), Z: (10.0, 100.0)}
    for perm in itertools.permutations(raw):
        norm = B.normalize_rows(list(perm))
        assert {r["cusip"]: (r["shares"], r["value"]) for r in norm.rows} == want
        assert norm.accessions == [ACC_A, ACC_C, ACC_B], "oldest first, by filing date"
        assert (norm.filed_on, norm.amended_on) == (date(2026, 8, 14), date(2026, 11, 2))
        assert norm.excluded_rows == 0 and norm.raw_row_count == 6


def test_filing_date_not_accession_text_orders_the_amendments():
    """The accession prefix is the FILER AGENT, not a clock: a restatement filed through
    Donnelley ('0000950170-…') sorts BEFORE the company's own '0001045810-…' as text."""
    raw = [_row(X, 999, 9990, "0000950170-26-000001", "2026-10-01"),     # later filing
           _row(X, 100, 1000, "0001045810-26-000065", "2026-08-14")]
    (r,) = B.normalize_rows(raw).rows
    assert (r["shares"], r["accession"]) == (999.0, "0000950170-26-000001")


def test_same_day_amendments_are_ordered_by_acceptance_time():
    raw = [_row(X, 150, 1500, "0000950170-26-000001", "2026-08-14", accepted="2026-08-14 17:30:00"),
           _row(X, 100, 1000, "0001045810-26-000065", "2026-08-14", accepted="2026-08-14 16:05:00")]
    norm = B.normalize_rows(raw)
    assert norm.rows[0]["shares"] == 150.0, "the later-accepted same-day amendment wins"
    assert norm.accessions == ["0001045810-26-000065", "0000950170-26-000001"]
    assert norm.amended_on == date(2026, 8, 14) == norm.filed_on


def test_a_restatement_with_fewer_rows_replaces_only_what_it_names():
    """The documented limitation, pinned so it cannot silently get worse: the amendment's
    numbers replace X; Y (which it did not repeat) keeps the original row, not summed."""
    raw = [_row(X, 100, 1000, ACC_A, "2026-08-14"), _row(Y, 50, 500, ACC_A, "2026-08-14", symbol="DEF"),
           _row(X, 90, 900, ACC_B, "2026-10-01")]
    got = {r["cusip"]: (r["shares"], r["value"]) for r in B.normalize_rows(raw).rows}
    assert got == {X: (90.0, 900.0), Y: (50.0, 500.0)}


def test_a_confidential_add_never_double_counts_the_original():
    raw = [_row(X, 100, 1000, ACC_A, "2026-08-14"), _row(Y, 50, 500, ACC_A, "2026-08-14", symbol="DEF"),
           _row(Z, 10, 100, ACC_B, "2027-02-10", symbol="GHI")]
    norm = B.normalize_rows(raw)
    assert math.fsum(r["value"] for r in norm.rows) == 1600.0
    assert len(norm.rows) == 3 and norm.amended_on == date(2027, 2, 10)


def test_an_amendment_that_only_adds_an_option_row_does_not_replace_the_share_position():
    raw = [_row(X, 100, 1000, ACC_A, "2026-08-14"),
           _row(X, 5000, 77, ACC_B, "2026-10-01", putCallShare="Put")]
    norm = B.normalize_rows(raw)
    assert [(r["shares"], r["value"]) for r in norm.rows] == [(100.0, 1000.0)]
    assert norm.excluded_rows == 1


def test_duplicate_cusips_are_summed_inside_each_accession_then_the_later_accession_replaces():
    raw = [
        _row(X, 60, 600, ACC_A, "2026-08-14", symbol=None, name=""),
        _row(X, 40, 400, ACC_A, "2026-08-14", symbol="ABC", name="ABC CORP"),
        _row(X, 70, 700, ACC_B, "2026-10-01", symbol=None, name=""),
        _row(X, 80, 800, ACC_B, "2026-10-01", symbol=None, name=""),
    ]
    (r,) = B.normalize_rows(raw).rows
    assert (r["shares"], r["value"]) == (150.0, 1500.0), "B's two rows summed; A's 100 replaced"
    assert r["symbol"] == "ABC" and r["name"] == "ABC CORP", "an identifier B left blank survives"


def test_a_cusip_in_mixed_case_and_padding_is_the_same_position():
    raw = [_row("h1467j104", 1, 10, ACC_A, "2026-08-14"), _row(" H1467J104 ", 2, 20, ACC_A, "2026-08-14")]
    (r,) = B.normalize_rows(raw).rows
    assert (r["cusip"], r["shares"], r["value"]) == ("H1467J104", 3.0, 30.0)


def test_regression_excluded_foreign_rows_stay_out_of_accessions_and_filing_dates():
    """REGRESSION (fixed 2026-09-24). Was: a row excluded for belonging to ANOTHER period
    or ANOTHER CIK still contributed its accession and filing date, so the card read
    'Amended' (the service keys that off ``len(accessions) > 1``) and 'filed May 15' for a
    quarter that ended June 30. normalize_rows now excludes those rows before recording
    either."""
    raw = [
        _row(X, 100, 1000, "0001045810-26-000065", "2026-08-14"),
        _row(Y, 100, 1000, "0001045810-26-000042", "2026-05-15", period="2026-03-31"),  # Q1 row
        _row(Z, 100, 1000, "0000000009-26-000003", "2026-09-30", cik="0000000009"),     # not ours
    ]
    norm = B.normalize_rows(raw, expected_period_end=date(2026, 6, 30), expected_cik=NVIDIA)
    assert [r["cusip"] for r in norm.rows] == [X] and norm.excluded_rows == 2   # (passes today)
    assert norm.accessions == ["0001045810-26-000065"]
    assert norm.filed_on == date(2026, 8, 14)
    assert norm.amended_on is None


def test_regression_a_stray_row_from_another_quarter_never_marks_the_filing_amended():
    """REGRESSION (fixed 2026-09-24). Was: the same leak through ``build_filing`` on
    NVIDIA's real 2026-Q2 extract: one stray
    Q1-dated row from the Q1 accession (FMP mis-filing a row under the wrong quarter) is
    correctly excluded from the book, but the written row says two accessions, filed
    2026-05-15 (six weeks BEFORE the quarter ended) and amended 2026-08-14."""
    fmp = FakeFMP()
    stray = dict(EXTRACTS[NVIDIA]["2026-Q1"][0])            # a real Q1 row: date 2026-03-31
    fmp.extracts[NVIDIA]["2026-Q2"].append(stray)
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.position_count == 8 and bf.total_value == NV_Q2_TOTAL and bf.excluded_rows == 1  # (passes)
    assert (bf.accessions, bf.filed_on, bf.amended_on) == (
        ["0001045810-26-000065"], date(2026, 8, 14), None)


# ── 2. exclusions and garbage numbers ─────────────────────────────────────────────────


def test_call_put_and_prn_rows_never_reach_positions_or_the_total():
    fmp = FakeFMP()
    intc = _nv_q2_row(fmp, "INTC")
    fmp.extracts[NVIDIA]["2026-Q2"] += [
        dict(intc, putCallShare="Call", shares=1_000_000, value=9_000_000),       # same CUSIP
        dict(intc, putCallShare="PUT", shares=2_000_000, value=8_000_000),
        _row("88160R101", 5_000_000, 5_000_000, "0001045810-26-000065", "2026-08-14",
             symbol="TSLA", sharesType="PRN"),                                   # bond principal
        _row("88160R101", 5_000_000, 5_000_000, "0001045810-26-000065", "2026-08-14",
             symbol="TSLA", sharesType=""),                                      # type unknown
    ]
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.total_value == NV_Q2_TOTAL and bf.position_count == 8
    assert bf.excluded_rows == 4
    assert bf.accessions == ["0001045810-26-000065"] and bf.amended_on is None
    assert bf.filed_on == date(2026, 8, 14)
    assert _holding(bf, "458140100")["shares"] == intc["shares"]
    assert not any(h["symbol"] == "TSLA" for h in bf.holdings)
    assert sum(h["weight"] for h in bf.holdings) == pytest.approx(1.0, abs=1e-12)


_GARBAGE = [None, "", "abc", "NaN", "nan", "inf", "-inf", "1e400", "-5", -5, 0, 0.0, -0.0,
            True, False, [], {}, float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("field_", ["shares", "value"])
@pytest.mark.parametrize("bad", _GARBAGE, ids=[repr(g) for g in _GARBAGE])
def test_garbage_numbers_are_excluded_and_counted(field_, bad):
    bad_row = _row(Y, 50, 500, ACC_A, "2026-08-14")
    bad_row[field_] = bad
    raw = [_row(X, 100, 1000, ACC_A, "2026-08-14"), bad_row]
    norm = B.normalize_rows(raw)
    assert [r["cusip"] for r in norm.rows] == [X]
    assert norm.excluded_rows == 1
    assert all(math.isfinite(r["shares"]) and math.isfinite(r["value"]) for r in norm.rows)


def test_numeric_strings_are_numbers():
    (r,) = B.normalize_rows([_row(X, "1e3", " 2500.5 ", ACC_A, "2026-08-14")]).rows
    assert (r["shares"], r["value"]) == (1000.0, 2500.5)


def test_an_overflowing_sum_never_reaches_a_written_row():
    """Two in-range rows whose within-accession SUM overflows to inf: whatever the builder
    does, a non-finite float must not survive to the upsert payload."""
    fmp = FakeFMP()
    intc = _nv_q2_row(fmp, "INTC")
    intc["shares"] = 1.5e308
    fmp.extracts[NVIDIA]["2026-Q2"].append(dict(intc))
    try:
        bf = _build(fmp, NVIDIA, 2026, 2)
    except (B.FilingUnavailable, ValueError):
        return
    with pytest.raises(ValueError, match="non-finite"):
        bf.as_row()


# ── 3. symbol resolution ──────────────────────────────────────────────────────────────

ACME = _cusip("00508X20")


def _acme_fmp(hits):
    fmp = FakeFMP()
    _add_symbolless(fmp, ACME, "ACME ROBOTICS INC")
    fmp.isin_map[R.cusip_to_us_isin(ACME)] = hits
    fmp.profiles.update({
        "ACMR": {"symbol": "ACMR", "companyName": "Acme Robotics, Inc.", "exchange": "NASDAQ",
                 "ipoDate": "2012-05-01", "isActivelyTrading": True},
        "ACMRQ": {"symbol": "ACMRQ", "companyName": "Acme Robotics Inc", "exchange": "OTC",
                  "ipoDate": "1990-01-02", "isActivelyTrading": False},
        "ZZZ": {"symbol": "ZZZ", "companyName": "Zeta Zeta Corp", "exchange": "NYSE",
                "ipoDate": "2001-01-02", "isActivelyTrading": True},
    })
    return fmp


def test_several_isin_hits_pick_the_actively_trading_name_match():
    fmp = _acme_fmp([{"symbol": "ACMRQ", "name": "Acme Robotics Inc (old)"},
                     {"symbol": "ZZZ", "name": "Zeta Zeta Corp"},
                     {"symbol": "ACMR", "name": "Acme Robotics Inc."}])
    bf = _build(fmp, NVIDIA, 2026, 2)
    h = _holding(bf, ACME)
    assert h["symbol"] == "ACMR" and h["routable"] is True and h["name"] == "Acme Robotics, Inc."
    assert fmp.calls["cusip"] == [], "search-isin answered; the fallback is not asked"
    assert bf.build_status == "complete" and bf.unresolved == {}


def test_duplicate_hits_for_one_symbol_are_one_candidate():
    fmp = _acme_fmp([{"symbol": "ACMR", "name": "Acme Robotics Inc."},
                     {"symbol": " acmr ", "name": "Acme Robotics Inc."}, {"symbol": None}, "junk"])
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert _holding(bf, ACME)["symbol"] == "ACMR"
    assert len(fmp.calls["profiles"]) == 1, "one hit needs no disambiguation batch"


def test_two_active_name_matches_stay_unresolved_rather_than_guess():
    fmp = _acme_fmp([{"symbol": "ACMR"}, {"symbol": "ACMRQ"}])
    fmp.profiles["ACMRQ"] = dict(fmp.profiles["ACMRQ"], isActivelyTrading=True, exchange="NYSE")
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert _holding(bf, ACME)["symbol"] is None and _holding(bf, ACME)["routable"] is False
    assert bf.unresolved == {ACME: TODAY.isoformat()} and bf.build_status == "degraded"


def test_no_hits_anywhere_leaves_the_row_kept_named_and_unresolved():
    fmp = FakeFMP()
    _add_symbolless(fmp, ACME, "ACME ROBOTICS INC")
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["isin"] == [R.cusip_to_us_isin(ACME)] and fmp.calls["cusip"] == [ACME]
    h = _holding(bf, ACME)
    assert h["symbol"] is None and h["name"] == "ACME ROBOTICS INC" and h["routable"] is False
    assert bf.position_count == 9 and bf.total_value == NV_Q2_TOTAL + 5_000_000
    assert f"unresolved_pending:{ACME}" in bf.degraded_reasons


def test_a_cins_number_never_asks_search_isin():
    fmp = FakeFMP()
    _add_symbolless(fmp, "G0000000A", "OFFSHORE HOLDINGS LTD")      # letter-first, and a bad digit
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["isin"] == [] and fmp.calls["cusip"] == ["G0000000A"]
    assert _holding(bf, "G0000000A")["symbol"] is None


@pytest.mark.parametrize("which", ["isin", "cusip"])
def test_a_raising_search_degrades_and_keeps_the_row(which):
    fmp = FakeFMP()
    _add_symbolless(fmp, ACME, "ACME ROBOTICS INC")
    setattr(fmp, f"{which}_raise", FMPRateLimitException("429"))
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.build_status == "degraded" and "symbol_lookup_failed" in bf.degraded_reasons
    assert _holding(bf, ACME)["symbol"] is None and bf.position_count == 9
    if which == "isin":
        assert fmp.calls["cusip"] == [], "a failed lookup is not 'no match' — no fallback on it"


def test_a_profile_failure_while_choosing_between_hits_is_a_failed_lookup():
    fmp = _acme_fmp([{"symbol": "ACMRQ"}, {"symbol": "ACMR"}])
    fmp.profile_raise_calls = {0}                        # the disambiguation batch is call #0
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert _holding(bf, ACME)["symbol"] is None
    assert "symbol_lookup_failed" in bf.degraded_reasons


def test_a_stale_fmp_symbol_is_never_routed_or_dressed_as_newly_listed():
    """FMP's own symbol is trusted without a lookup. When it is a dead listing (YNDX is not
    actively trading) the holding must at least not be routable."""
    fmp = FakeFMP()
    _nv_q2_row(fmp, "NBIS")["symbol"] = "YNDX"
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["isin"] == fmp.calls["cusip"] == []
    h = _holding(bf, "N97284108")
    assert h["symbol"] == "YNDX" and h["routable"] is False
    assert not any(r["newly_listed"] for r in bf.changes["rows"] if r["cusip"] == "N97284108")


def test_a_stale_fmp_symbol_with_no_profile_degrades_and_falls_back_to_the_sec_name():
    fmp = FakeFMP()
    _nv_q2_row(fmp, "SNPS")["symbol"] = "SNPSX"
    bf = _build(fmp, NVIDIA, 2026, 2)
    h = _holding(bf, "871607107")
    assert (h["symbol"], h["routable"], h["ipo_date"], h["exchange"]) == ("SNPSX", False, None, None)
    assert h["name"] == _nv_q2_row(fmp, "SNPSX")["nameOfIssuer"]
    assert bf.build_status == "degraded" and "profiles_missing:SNPSX" in bf.degraded_reasons


def test_a_symbolless_previous_only_cusip_is_resolved_or_tracked_for_the_terminal_state():
    """Changed 2026-09-24 (resilience-5): this used to pin that only CURRENT holdings enter
    ``unresolved``. A previous-quarter-only CUSIP is now tracked there too, so a lookup that
    keeps failing for it goes terminal after 7 days instead of degrading N forever. A plain
    "no match" for it still does not degrade the build."""
    gone = _cusip("91234Z10")
    fmp = FakeFMP()
    _add_symbolless(fmp, gone, "GONE AWAY INC", period="2026-Q1")
    fmp.isin_map[R.cusip_to_us_isin(gone)] = [{"symbol": "GONE", "name": "Gone Away Inc."}]
    bf = _build(fmp, NVIDIA, 2026, 2)
    row = next(r for r in bf.changes["rows"] if r["cusip"] == gone)
    assert row["change"] == "no_longer_reported" and row["symbol"] == "GONE"
    assert bf.unresolved == {} and bf.build_status == "complete"

    stuck = FakeFMP()
    _add_symbolless(stuck, gone, "GONE AWAY INC", period="2026-Q1")
    bf2 = _build(stuck, NVIDIA, 2026, 2)
    assert bf2.unresolved == {gone: TODAY.isoformat()}
    assert bf2.build_status == "complete", "no match for an exited row is not a degradation"
    assert gone not in {h["cusip"] for h in bf2.holdings}


# ── 4. the unresolved lifecycle, followed across the daily job ─────────────────────────


def test_an_unresolved_cusip_followed_day_by_day_through_the_job():
    """Feed each day's written ``unresolved`` back in as the next day's stored map, the way
    the daily job does. Lookups run through day 7 and stop from day 8; first-seen never
    moves; the terminal CUSIP stays in the map (dropping it would restart the clock)."""
    stuck = _cusip("55555555")
    stored: Dict[str, str] = {}
    start = date(2026, 9, 1)
    history = []
    for day in range(12):
        today = start + timedelta(days=day)
        fmp = FakeFMP()
        _add_symbolless(fmp, stuck, "MYSTERY HOLDINGS INC")
        bf = _build(fmp, NVIDIA, 2026, 2, stored=stored, today=today)
        history.append((day, len(fmp.calls["isin"]) + len(fmp.calls["cusip"]), bf.build_status))
        assert bf.unresolved == {stuck: start.isoformat()}, f"day {day}: first-seen moved"
        stored = json.loads(json.dumps(bf.as_row()["unresolved"]))     # round-trip like JSONB
    assert history == [(d, 2, "degraded") for d in range(8)] + [(d, 0, "complete") for d in range(8, 12)]


def test_the_terminal_state_is_per_cusip():
    old, new = _cusip("55555555"), _cusip("66666666")
    fmp = FakeFMP()
    _add_symbolless(fmp, old, "MYSTERY HOLDINGS INC")
    _add_symbolless(fmp, new, "NEWCOMER INC")
    bf = _build(fmp, NVIDIA, 2026, 2, stored={old: "2026-09-01"})
    assert fmp.calls["cusip"] == [new], "the terminal CUSIP is skipped, the new one is looked up"
    assert bf.unresolved == {old: "2026-09-01", new: TODAY.isoformat()}
    assert bf.degraded_reasons == [f"unresolved_pending:{new}"]


# ── 5. profiles: chunking and partial failure ─────────────────────────────────────────


def _big_book(n, *, cik=NVIDIA):
    return [_row(_cusip(f"{i:07d}A"), 100 + i, 1_000_000 + i, "0001045810-26-000065", "2026-08-14",
                 symbol=f"S{i:03d}", name=f"S{i:03d} CORP", cik=cik) for i in range(n)]


def test_profiles_come_in_sorted_chunks_and_a_failed_chunk_degrades_exactly_its_symbols():
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": _big_book(120)}}, generic_profiles=True)
    fmp.profile_raise_calls = {1}
    bf = _build(fmp, NVIDIA, 2026, 2, prev=False, older_filing_exists=False)
    wanted = sorted(f"S{i:03d}" for i in range(120))
    assert fmp.calls["profiles"] == [wanted[:50], wanted[50:100], wanted[100:]]
    lost = set(wanted[50:100])
    assert bf.build_status == "degraded"
    assert bf.degraded_reasons == ["profiles_missing:" + ",".join(sorted(lost))]
    for h in bf.holdings:
        assert h["routable"] is (h["symbol"] not in lost)
        assert (h["sector"] is None) is (h["symbol"] in lost)


def test_junk_in_a_profile_answer_is_ignored_not_trusted():
    """None / a bare string / a profile for a symbol nobody asked for: none of it may fill
    a holding. INTC has no real profile, so the build must say so (degraded)."""
    fmp = FakeFMP()
    fmp.profiles.pop("INTC")
    fmp.profile_junk = [None, "INTC", 7,
                        {"symbol": "ZZZZ", "companyName": "Stranger Corp", "exchange": "NYSE",
                         "sector": "Stranger", "isActivelyTrading": True}]
    bf = _build(fmp, NVIDIA, 2026, 2)
    intc = _holding(bf, "458140100")
    assert (intc["routable"], intc["sector"], intc["exchange"]) == (False, None, None)
    assert bf.degraded_reasons == ["profiles_missing:INTC"]
    assert "Stranger" not in json.dumps(bf.as_row())


# ── 6. routable ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("patch, expected", [
    ({"exchange": "nasdaq"}, True),
    ({"exchange": "AMEX"}, True),
    ({"exchange": " NYSE "}, True),
    ({"exchange": "OTC"}, False),
    ({"exchange": "TSX"}, False),
    ({"exchange": "NYSE ARCA"}, False),
    ({"exchange": None}, False),
    ({"exchange": ""}, False),
    ({"isActivelyTrading": False}, False),
    ({"isActivelyTrading": None}, True),
])
def test_routable_needs_a_us_exchange_and_a_live_listing(patch, expected):
    profiles = dict(PROFILES)
    profiles["INTC"] = {**PROFILES["INTC"], **patch}
    bf = _build(FakeFMP(profiles=profiles), NVIDIA, 2026, 2)
    assert _holding(bf, "458140100")["routable"] is expected


# ── 7. the hash ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cik, period", [(ALPHABET, "2026-Q2"), (BRK, "2023-Q3"), (NVIDIA, "2026-Q1")])
def test_hash_and_normalised_book_are_order_independent(cik, period):
    rows = EXTRACTS[cik][period]
    base_hash, base = B.raw_hash_of(rows), B.normalize_rows(rows)
    rng = random.Random(20260924)
    for _ in range(25):
        shuffled = copy.deepcopy(rows)
        rng.shuffle(shuffled)
        assert B.raw_hash_of(shuffled) == base_hash
        again = B.normalize_rows(shuffled)
        assert (again.rows, again.accessions, again.filed_on, again.amended_on) == \
               (base.rows, base.accessions, base.filed_on, base.amended_on)


def test_hash_is_sensitive_to_every_input_that_changes_the_book_and_blind_to_formatting():
    rows = EXTRACTS[NVIDIA]["2026-Q2"]
    base = B.raw_hash_of(rows)
    # formatting that normalises to the same book -> same hash
    for field_, new in [("shares", lambda v: str(v)), ("shares", lambda v: float(v)),
                        ("symbol", lambda v: f"  {v.lower()} ")]:
        m = copy.deepcopy(rows)
        m[0][field_] = new(m[0][field_])
        assert B.raw_hash_of(m) == base, field_
    # everything that changes what is stored -> new hash
    for field_, new in [("symbol", "ZZZZ"), ("symbol", None), ("nameOfIssuer", "Generate Bio"),
                        ("filingDate", "2026-08-15"), ("sharesType", "PRN"),
                        ("putCallShare", "Call"), ("shares", 833326), ("value", 14058194)]:
        m = copy.deepcopy(rows)
        m[0][field_] = new
        assert B.raw_hash_of(m) != base, field_
    # an EXCLUDED row still counts: its change can flip what an amendment means
    with_put = copy.deepcopy(rows) + [dict(rows[0], putCallShare="Put", value=1)]
    with_put2 = copy.deepcopy(rows) + [dict(rows[0], putCallShare="Put", value=2)]
    assert len({base, B.raw_hash_of(with_put), B.raw_hash_of(with_put2)}) == 3
    # a duplicated row is not the same book as one row
    assert B.raw_hash_of(rows + [rows[0]]) != base


def test_regression_hash_covers_the_fields_that_decide_exclusion():
    """REGRESSION (fixed 2026-09-24; HASH_VERSION tc13f-v2). Was: ``normalize_rows``
    excludes a row dated for another period or filed under another CIK,
    but ``raw_hash_of`` hashed neither field. FMP correcting a mis-dated row flipped it from
    excluded to included with the SAME hash, and the job's 'unchanged hash + complete' skip
    then freezes the stale book (the position stays missing) until something else changes."""
    good = [_row(X, 100, 1000, ACC_A, "2026-08-14"), _row(Y, 50, 500, ACC_A, "2026-08-14", symbol="DEF")]
    misdated = copy.deepcopy(good)
    misdated[1]["date"] = "2026-03-31"
    foreign = copy.deepcopy(good)
    foreign[1]["cik"] = "0000000009"
    end = date(2026, 6, 30)
    for variant in (misdated, foreign):
        books_differ = (len(B.normalize_rows(variant, expected_period_end=end, expected_cik=NVIDIA).rows)
                        != len(B.normalize_rows(good, expected_period_end=end, expected_cik=NVIDIA).rows))
        assert books_differ                                                          # (passes today)
        assert B.raw_hash_of(variant) != B.raw_hash_of(good)


# ── 8. MAX_ROWS on either quarter ─────────────────────────────────────────────────────


def test_201_raw_rows_are_refused_before_any_lookup():
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": _big_book(B.MAX_ROWS + 1)}}, generic_profiles=True)
    with pytest.raises(B.FilingRefused, match=f"{B.MAX_ROWS + 1} rows"):
        _build(fmp, NVIDIA, 2026, 2, prev=False, older_filing_exists=False)
    assert fmp.calls["profiles"] == fmp.calls["isin"] == fmp.calls["cusip"] == []


def test_the_row_cap_counts_raw_rows_including_option_rows():
    book = _big_book(B.MAX_ROWS)
    book[0] = dict(book[0], putCallShare="Call")
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": book}}, generic_profiles=True)
    bf = _build(fmp, NVIDIA, 2026, 2, prev=False, older_filing_exists=False)
    assert (bf.position_count, bf.excluded_rows) == (B.MAX_ROWS - 1, 1)

    over = _big_book(B.MAX_ROWS + 1)
    over[0] = dict(over[0], putCallShare="Call")
    over[1] = dict(over[1], putCallShare="Put")                  # 199 share rows, 201 raw
    with pytest.raises(B.FilingRefused):
        _build(FakeFMP(extracts={NVIDIA: {"2026-Q2": over}}, generic_profiles=True),
               NVIDIA, 2026, 2, prev=False, older_filing_exists=False)


def test_an_oversized_previous_quarter_refuses_the_current_build():
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": _big_book(10),
                                     "2026-Q1": [dict(r, date="2026-03-31") for r in _big_book(B.MAX_ROWS + 1)]}},
                  generic_profiles=True)
    with pytest.raises(B.FilingRefused, match="2026-Q1"):
        _build(fmp, NVIDIA, 2026, 2)


# ── 9. strict failures vs empty answers ───────────────────────────────────────────────


def test_when_both_quarters_fail_the_current_quarters_failure_is_reported():
    fmp = FakeFMP()
    fmp.extract_raise[(NVIDIA, "2026-Q2")] = FMPUnavailableException("cur-503")
    fmp.extract_raise[(NVIDIA, "2026-Q1")] = FMPRateLimitException("prev-429")
    with pytest.raises(B.FilingUnavailable, match="2026-Q2.*cur-503"):
        _build(fmp, NVIDIA, 2026, 2)


def test_a_refused_current_quarter_wins_over_an_unavailable_previous_one():
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": _big_book(B.MAX_ROWS + 1), "2026-Q1": []}})
    with pytest.raises(B.FilingRefused):
        _build(fmp, NVIDIA, 2026, 2)


@pytest.mark.parametrize("prev_rows", [
    [],                                                                              # listed, empty
    [dict(r, date="2025-12-31") for r in EXTRACTS[NVIDIA]["2026-Q1"]],              # all mis-dated
    [dict(r, cik="0000000009") for r in EXTRACTS[NVIDIA]["2026-Q1"]],               # all foreign
    [dict(r, putCallShare="Call") for r in EXTRACTS[NVIDIA]["2026-Q1"]],            # all options
])
def test_a_listed_previous_quarter_with_nothing_usable_writes_nothing(prev_rows):
    fmp = FakeFMP()
    fmp.extracts[NVIDIA]["2026-Q1"] = prev_rows
    with pytest.raises(B.FilingUnavailable, match="2026-Q1"):
        _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["profiles"] == [] and fmp.calls["isin"] == [] and fmp.calls["cusip"] == []


def test_regression_an_empty_dates_answer_is_not_trusted_as_a_first_filing():
    """REGRESSION (fixed 2026-09-24). Was: with ``older_filing_exists`` omitted the builder
    asks ``dates``. A dates answer that does not even list the quarter it just fetched rows
    for is not evidence that nothing older exists — yet ``[]`` was read as 'no older
    filing', the build stamped COMPLETE with ``comparison='first_filing'``, and the
    hash-skip then kept that label. ``_older_filing`` now raises FilingUnavailable."""
    for answer in ([], {"Error Message": "x"}, [{"year": "junk"}, None, {"year": True, "quarter": 2}],
                   [{"year": 2026, "quarter": 1}, {"year": 2025, "quarter": 4}]):   # lags: no Q2
        fmp = FakeFMP(dates={NVIDIA: answer})
        with pytest.raises(B.FilingUnavailable, match="do not list 2026-Q2"):
            _build(fmp, NVIDIA, 2026, 2, prev=False)
    # Listing the quarter itself is what makes "nothing older" believable.
    only_q2 = _build(FakeFMP(dates={NVIDIA: [{"year": 2026, "quarter": 2}]}), NVIDIA, 2026, 2, prev=False)
    assert only_q2.changes["comparison"] == "first_filing"
    gap = _build(FakeFMP(dates={NVIDIA: [{"year": "2026", "quarter": "2"}, {"year": 2025, "quarter": 3}]}),
                 NVIDIA, 2026, 2, prev=False)
    assert (gap.changes["comparison"], gap.changes["prev_period"]) == ("gap", "2025-Q3")


# ── 10. the written row ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path, poke", [
    ("holdings[0].shares", lambda bf: bf.holdings[0].__setitem__("shares", float("inf"))),
    ("holdings[5].value", lambda bf: bf.holdings[5].__setitem__("value", float("-inf"))),
    ("changes.rows[0].share_change", lambda bf: bf.changes["rows"][0].__setitem__("share_change", float("nan"))),
    ("changes.counts.unchanged", lambda bf: bf.changes["counts"].__setitem__("unchanged", float("nan"))),
    ("row.total_value", lambda bf: setattr(bf, "total_value", float("inf"))),
    ("unresolved.X", lambda bf: setattr(bf, "unresolved", {"X": float("nan")})),
])
def test_as_row_names_every_non_finite_float(path, poke):
    bf = _build(FakeFMP(), NVIDIA, 2026, 2)
    poke(bf)
    with pytest.raises(ValueError, match="non-finite") as ei:
        bf.as_row()
    assert path.split(".")[-1].split("[")[0] in str(ei.value)


@pytest.mark.parametrize("poke", [
    lambda bf: setattr(bf, "unresolved", {"X": date(2026, 9, 1)}),
    lambda bf: bf.holdings[0].__setitem__("ipo_date", date(1971, 10, 13)),
    lambda bf: bf.changes.__setitem__(7, "int key"),
    lambda bf: setattr(bf, "position_count", -1),
    lambda bf: setattr(bf, "excluded_rows", -3),
    lambda bf: setattr(bf, "total_value", -1.0),
])
def test_as_row_refuses_non_json_and_negative_values(poke):
    bf = _build(FakeFMP(), NVIDIA, 2026, 2)
    poke(bf)
    with pytest.raises(ValueError):
        bf.as_row()


def test_a_degraded_build_with_gone_rows_serialises_strictly():
    fmp = FakeFMP()
    fmp.profile_raise_calls = {0}
    _add_symbolless(fmp, ACME, "ACME ROBOTICS INC")
    fmp.extracts[NVIDIA]["2026-Q2"] = [r for r in fmp.extracts[NVIDIA]["2026-Q2"] if r["symbol"] != "NOK"]
    bf = _build(fmp, NVIDIA, 2026, 2)
    text = json.dumps(bf.as_row(), allow_nan=False)
    gone = next(r for r in bf.changes["rows"] if r["symbol"] == "NOK")
    assert gone["change"] == "no_longer_reported" and gone["shares"] is gone["value"] is gone["weight"] is None
    assert '"build_status":"degraded"' in text.replace(" ", "")


@pytest.mark.parametrize("field_, value", [
    ("cik", "0001045810\n"),          # Python's `$` matches before a trailing newline; Postgres's does not
    ("period", "2026-Q2\n"),
    ("period", " 2026-Q2"),           # parse_period strips; the CHECK regex does not
    ("holdings", {}),                 # CHECK (jsonb_typeof(holdings) = 'array')
    ("changes", []),                  # CHECK (jsonb_typeof(changes) = 'object')
    # added with the fix: the other typed columns
    ("cik", "１０４５８１００００"),         # full-width digits: [0-9] is ASCII, unlike \d
    ("period", "2026-Q5"),
    ("unresolved", []),               # CHECK (jsonb_typeof(unresolved) = 'object')
    ("accessions", "0001045810-26-000065"),   # a str would be written as a list of letters
    ("period_end", "2026-06-30"),
    ("filed_on", "2026-08-14"),
    ("raw_hash", ""),                 # NOT NULL, and an empty hash matches nothing
    ("raw_hash", None),
])
def test_regression_as_row_refuses_values_the_table_checks_reject(field_, value):
    """REGRESSION (fixed 2026-09-24). Was: ``as_row`` promises to raise ValueError for 'a
    value the table CHECKs would reject' so a bad upsert never quietly leaves the old row
    in place, but these all passed it and would fail migration 175's CHECKs
    (``cik ~ '^[0-9]{10}$'``, ``period ~ '^[0-9]{4}-Q[1-4]$'``, ``jsonb_typeof``). It now
    uses ``fullmatch`` on the unstripped values and type-checks the JSONB columns."""
    bf = _build(FakeFMP(), NVIDIA, 2026, 2)
    setattr(bf, field_, value)
    with pytest.raises(ValueError):
        bf.as_row()


def test_is_small_is_strictly_below_one_percent():
    book = [_row(_cusip("10000000"), 1, 2.0, ACC_A, "2026-08-14", symbol="TINY"),     # exactly 1%
            _row(_cusip("20000000"), 1, 197.0, ACC_A, "2026-08-14", symbol="BIG"),
            _row(_cusip("30000000"), 1, 1.0, ACC_A, "2026-08-14", symbol="TINIER")]   # 0.5%
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": book}}, generic_profiles=True)
    bf = _build(fmp, NVIDIA, 2026, 2, prev=False, older_filing_exists=False)
    small = {h["symbol"]: (h["weight"], h["is_small"]) for h in bf.holdings}
    assert small["TINY"] == (0.01, False) and small["TINIER"] == (0.005, True)
    assert small["BIG"][1] is False and bf.total_value == 200.0


# ── 11. split + CUSIP re-key + symbol resolution, together ────────────────────────────

KLAC_OLD, KLAC_NEW = _cusip("48248010"), _cusip("48248020")
ONE_FOR_TEN = [{"date": "2026-05-04", "numerator": 1, "denominator": 10}]


def _rekey_fmp(*, new_symbol: Optional[str]):
    filler_q1 = _row(_cusip("77777777"), 500, 50_000, "0001045810-26-000042", "2026-05-15",
                     symbol="FILL", period="2026-03-31")
    filler_q2 = _row(_cusip("77777777"), 500, 50_000, "0001045810-26-000065", "2026-08-14", symbol="FILL")
    q1 = [filler_q1, _row(KLAC_OLD, 1_000_000, 10_000_000, "0001045810-26-000042", "2026-05-15",
                          symbol="KLAC", name="KLA CORP", period="2026-03-31")]
    q2 = [filler_q2, _row(KLAC_NEW, 100_000, 10_000_000, "0001045810-26-000065", "2026-08-14",
                          symbol=new_symbol, name="KLA CORP")]
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q1": q1, "2026-Q2": q2}}, generic_profiles=True)
    fmp.isin_map[R.cusip_to_us_isin(KLAC_NEW)] = [{"symbol": "KLAC", "name": "KLA Corporation"}]
    return fmp


@pytest.mark.parametrize("new_symbol", ["KLAC", None])
def test_a_reverse_split_that_changed_the_cusip_is_one_unchanged_position(new_symbol):
    """A 1:10 reverse split usually issues a NEW CUSIP. The builder must resolve the new
    CUSIP's symbol (when FMP left it blank), confirm the split through the seam, and join
    the two CUSIPs — not report 'no longer reported' + 'newly reported', nor a 90% cut."""
    fmp = _rekey_fmp(new_symbol=new_symbol)
    acts = FakeActions(splits={"KLAC": ONE_FOR_TEN})
    bf = _build(fmp, NVIDIA, 2026, 2, actions=acts)
    assert bf.changes["counts"] == {"newly_reported": 0, "increased": 0, "decreased": 0,
                                    "no_longer_reported": 0, "unchanged": 2, "corporate_action": 0}
    assert bf.changes["rows"] == []
    assert ("split", "KLAC", "2026-03-21", "2026-06-30") in acts.calls
    assert bf.build_status == "complete"


def test_a_reverse_split_with_a_failed_lookup_is_a_corporate_action_and_degraded():
    fmp = _rekey_fmp(new_symbol="KLAC")
    bf = _build(fmp, NVIDIA, 2026, 2, actions=FakeActions(splits={"KLAC": RuntimeError("429")}))
    (row,) = bf.changes["rows"]
    assert row["change"] == "corporate_action" and row["share_change"] is None
    assert bf.build_status == "degraded" and "split_lookup_failed:KLAC" in bf.degraded_reasons


# ── 12. FMP wrappers ──────────────────────────────────────────────────────────────────


def _client(behavior):
    c = FMPClient()
    seen: list = []

    async def _impl(endpoint, params=None):
        seen.append((endpoint, dict(params or {})))
        out = behavior(endpoint, params or {})
        if isinstance(out, BaseException):
            raise out
        return out
    c._make_request_impl = _impl  # type: ignore[method-assign]
    return c, seen


@pytest.mark.parametrize("method, args", [
    ("get_institutional_holdings", (NVIDIA, 2026, 2)),
    ("get_institutional_filing_dates", (NVIDIA,)),
])
def test_the_whale_default_passes_a_non_list_body_through_unchanged(method, args):
    """Whale parity: before `strict` existed these returned whatever FMP sent. Only the
    strict mode may start raising on a dict body."""
    body = {"Error Message": "Limit Reach"}
    c, _ = _client(lambda e, p: body)
    assert asyncio.run(getattr(c, method)(*args)) == body


@pytest.mark.parametrize("method, args", [
    ("get_institutional_holdings", (NVIDIA, 2026, 2)),
    ("get_institutional_filing_dates", (NVIDIA,)),
])
def test_strict_returns_an_empty_answer_as_empty_so_the_caller_decides(method, args):
    c, seen = _client(lambda e, p: [])
    assert asyncio.run(getattr(c, method)(*args, strict=True)) == []
    assert len(seen) == 1


def test_regression_market_cap_batch_never_iterates_a_bare_string_into_letters():
    """REGRESSION (fixed 2026-09-24). Was: ``symbols: Sequence[str]`` — and a ``str`` IS a
    ``Sequence[str]``, so passing one ticker as a string silently asked FMP for 'A,P,L'.
    ``get_market_cap_batch`` now raises TypeError before any call."""
    c, seen = _client(lambda e, p: [])
    for bare in ("AAPL", b"AAPL"):
        with pytest.raises(TypeError, match="not a single"):
            asyncio.run(c.get_market_cap_batch(bare))
    assert seen == [], "no request is made for a bare string"
    asyncio.run(c.get_market_cap_batch(["aapl", " AAPL ", "", "msft"]))
    assert seen == [("market-capitalization-batch", {"symbols": "AAPL,MSFT"})]


# ── 13. whale-path parity with the pre-extraction block ───────────────────────────────
#
# VERBATIM (logic) copy of the in-line block `whale_service._process_13f_path` ran at commit
# 27e7c629, plus the two helpers it called, so a change to the shared module cannot move
# both sides of the comparison at once.


def _ref_finite_float(value, default=0.0):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _ref_suspicious(current_raw, previous_raw):
    def _map(raw):
        m = {}
        for h in raw or []:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            m[sym] = (_ref_finite_float(h.get("value")),
                      _ref_finite_float(h.get("sharesNumber") or h.get("shares")))
        return m
    cur, prev = _map(current_raw), _map(previous_raw)
    strong, weak = [], []
    for sym in sorted(set(cur) & set(prev)):
        cv, cs = cur[sym]
        pv, ps = prev[sym]
        if cs <= 0 or ps <= 0 or cv <= 0 or pv <= 0:
            continue
        cur_price, prev_price = cv / cs, pv / ps
        if cur_price <= 0 or prev_price <= 0:
            continue
        price_ratio = prev_price / cur_price
        if not (price_ratio >= 1.3 or price_ratio <= 1.0 / 1.3):
            continue
        share_ratio = cs / ps
        if not (0.7 < share_ratio < 1.4) and abs(share_ratio - price_ratio) <= 0.35 * share_ratio:
            strong.append(sym)
        else:
            weak.append(sym)
    return strong + weak


def _ref_split_ratio_in_window(splits, start_excl, end_incl):
    if not splits or not end_incl:
        return 1.0
    ratio = 1.0
    for s in splits:
        d = str(s.get("date") or "")[:10]
        num, den = s.get("numerator"), s.get("denominator")
        if not d or not num or not den:
            continue
        try:
            r = float(num) / float(den)
        except (ValueError, ZeroDivisionError, TypeError):
            continue
        if not math.isfinite(r) or r <= 0:
            continue
        if (start_excl is None or start_excl < d) and d <= end_incl:
            ratio *= r
    return ratio


async def _ref_old_block(current_raw, prev_raw, prev_end, curr_end, actions):
    split_ratios, unclassified_tickers, lookup_failed_tickers = {}, set(), set()
    suspects: List[str] = []
    try:
        suspects = _ref_suspicious(current_raw, prev_raw)
        if len(suspects) > MAX_SPLIT_LOOKUPS:
            suspects = suspects[:MAX_SPLIT_LOOKUPS]
        if suspects:
            from_date, to_date = window_for_range(prev_end, curr_end)
            split_lists = await asyncio.gather(
                *[actions.get_split_rows(t, from_date, to_date) for t in suspects],
                return_exceptions=True,
            )
            flag_results = await asyncio.gather(
                *[actions.has_unclassified_adjustment(t, from_date, to_date,
                                                      effective_from=prev_end, effective_to=curr_end)
                  for t in suspects],
                return_exceptions=True,
            )
            for t, flagged in zip(suspects, flag_results):
                if flagged is True or isinstance(flagged, BaseException):
                    if isinstance(flagged, BaseException):
                        lookup_failed_tickers.add(t)
                    unclassified_tickers.add(t)
            for t, sl in zip(suspects, split_lists):
                if sl is None or isinstance(sl, BaseException):
                    unclassified_tickers.add(t)
                    lookup_failed_tickers.add(t)
                    continue
                r = _ref_split_ratio_in_window(sl, prev_end, curr_end)
                if r and abs(r - 1.0) > 1e-9:
                    split_ratios[t] = r
    except Exception:
        split_ratios = {}
        unclassified_tickers = set(suspects or [])
        lookup_failed_tickers = set(suspects or [])
    return split_ratios, unclassified_tickers, lookup_failed_tickers


class _ScriptedActions:
    """Deterministic seam: behaviour per ticker; logs every call in call order."""

    def __init__(self, split_beh, flag_beh, *, sync_raise=None):
        self.split_beh, self.flag_beh, self.sync_raise = split_beh, flag_beh, sync_raise
        self.log: list = []

    def get_split_rows(self, t, from_date=None, to_date=None):
        self.log.append(("split", t, from_date, to_date))
        if self.sync_raise == "split":
            raise RuntimeError("seam exploded (sync)")
        return self._answer(self.split_beh.get(t, []))

    def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *,
                                    effective_from=None, effective_to=None):
        self.log.append(("flag", t, from_date, to_date, effective_from, effective_to))
        if self.sync_raise == "flag":
            raise RuntimeError("probe exploded (sync)")
        return self._answer(self.flag_beh.get(t, False))

    @staticmethod
    async def _answer(v):
        if isinstance(v, BaseException):
            raise v
        return copy.deepcopy(v)


_SPLIT_BEHAVIOURS = [
    [], None, RuntimeError("429"), [{"date": "2026-06-10", "numerator": 10, "denominator": 1}],
    [{"date": "2026-05-04", "numerator": 1, "denominator": 10}],
    [{"date": "2026-03-25", "numerator": 10, "denominator": 1}],                     # in the lead only
    [{"date": "2026-04-01", "numerator": 3, "denominator": 2}, {"date": "2026-06-01", "numerator": 2, "denominator": 1}],
    [{"date": None, "numerator": 2, "denominator": 1}, {"date": "2026-05-01", "numerator": 0, "denominator": 1}],
    [{"date": "2026-05-01", "numerator": "nan", "denominator": 1}, {"date": "2026-05-01", "numerator": 1, "denominator": 0}],
    [{"date": "2026-06-30", "numerator": 4, "denominator": 1}],                      # on the boundary
    [{"date": "2026-03-31", "numerator": 4, "denominator": 1}],                      # excluded boundary
]
_FLAG_BEHAVIOURS = [False, True, RuntimeError("timeout"), 1, None, "yes", 0]


def _fuzz_value(rng):
    return rng.choice([10_000_000, 1_000_000.0, 55_555.5, 0, -5, float("nan"), None, "abc", "2e6", float("inf")])


def _fuzz_scenario(seed):
    rng = random.Random(seed)
    n = rng.choice([0, 1, 3, 8, 30])
    syms = [f"T{i:02d}" for i in range(n)]
    if n and rng.random() < 0.3:
        syms[-1] = syms[0]                                  # a duplicate symbol
    prev, curr = [], []
    restated_book = rng.random() < 0.15                    # every position split-shaped (the cap)
    for s in syms:
        pv = rng.choice([10_000_000, 2_000_000, 777_777])
        ps = rng.choice([100_000, 1_000_000, 33_333])
        kind = 0.0 if restated_book else rng.random()
        if kind < 0.35:                                      # split-shaped
            k = rng.choice([10, 0.1, 2, 1.5, 4 / 3, 3, 1.31, 1.29, 0.76, 0.78])
            # 0.75: |1 - 1/m| = 0.33 sits inside the strong/weak band edge (0.35 of the share ratio)
            cs, cv = ps * k, pv * rng.choice([1.0, 0.95, 1.1, 0.5, 2.0, 0.75])
        elif kind < 0.6:                                     # a real trade at a flat price
            f = rng.choice([1.3, 0.5, 2.0, 1.0])
            cs, cv = ps * f, pv * f
        elif kind < 0.75:                                    # garbage on one side
            cs, cv = rng.choice([ps, 0, None, "x", float("nan")]), _fuzz_value(rng)
        else:
            cs, cv = ps, pv
        key_sym = rng.choice(["symbol", "symbol", "symbol", "tickercusip"])
        key_sh = rng.choice(["shares", "shares", "sharesNumber"])
        sym_txt = rng.choice([s, s, s.lower(), f"{s}"])
        prev.append({key_sym: sym_txt, "value": pv, key_sh: ps})
        curr.append({key_sym: s, "value": cv, key_sh: cs})
    if rng.random() < 0.1:
        curr.append({"symbol": "--", "value": 1, "shares": 1})
        prev.append({"symbol": "", "value": 1, "shares": 1})
    prev_raw = rng.choice([prev, prev, prev, [], None])
    prev_end = None if prev_raw is None or rng.random() < 0.05 else "2026-03-31"
    uniq = sorted({s.upper() for s in syms})
    split_beh = {s: rng.choice(_SPLIT_BEHAVIOURS) for s in uniq}
    flag_beh = {s: rng.choice(_FLAG_BEHAVIOURS) for s in uniq}
    sync_raise = rng.choice([None] * 12 + ["split", "flag"])
    return curr, prev_raw, prev_end, split_beh, flag_beh, sync_raise


def _run_both(scenario):
    curr, prev_raw, prev_end, split_beh, flag_beh, sync_raise = scenario
    new_seam = _ScriptedActions(split_beh, flag_beh, sync_raise=sync_raise)
    ref_seam = _ScriptedActions(split_beh, flag_beh, sync_raise=sync_raise)
    new = asyncio.run(tfs.resolve_13f_split_adjustments(
        copy.deepcopy(curr), copy.deepcopy(prev_raw), prev_end, "2026-06-30",
        actions=new_seam, log_ctx="adv-fuzz"))
    ref = asyncio.run(_ref_old_block(copy.deepcopy(curr), copy.deepcopy(prev_raw), prev_end,
                                     "2026-06-30", ref_seam))
    return new, ref, new_seam.log, ref_seam.log


def test_the_extracted_helper_matches_the_old_inline_block_on_a_seeded_fuzz():
    coverage = {"cap": 0, "ratio": 0, "failed": 0, "flag_only": 0, "batch": 0, "nothing": 0}
    for seed in range(600):
        scenario = _fuzz_scenario(seed)
        new, ref, new_log, ref_log = _run_both(scenario)
        assert new == ref, f"seed {seed}: outputs differ"
        assert new_log == ref_log, f"seed {seed}: the corporate-actions seam was called differently"
        ratios, unclassified, failed = ref
        coverage["cap"] += len({c[1] for c in ref_log if c[0] == "split"}) == MAX_SPLIT_LOOKUPS
        coverage["ratio"] += bool(ratios)
        coverage["failed"] += bool(failed)
        coverage["flag_only"] += bool(unclassified - failed)
        coverage["batch"] += scenario[5] is not None and bool(ref_log)
        coverage["nothing"] += not ref_log
    assert all(v >= 5 for v in coverage.values()), f"anti-vacuity: the fuzz missed a branch {coverage}"


# The whale request path end to end (fakes as in test_thirteen_f_splits_characterisation).


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __getattr__(self, _name):
        return lambda *a, **k: self


class _SB:
    def table(self, _name):
        return _Query()


async def _sb_exec(_query, *a, **k):
    return _Resp([])


class _WhaleFMP:
    def __init__(self, curr, prev):
        self.curr, self.prev = curr, prev

    async def get_institutional_filing_dates(self, cik):
        dates = [{"date": "2026-06-30", "year": 2026, "quarter": 2}]
        if self.prev is not None:
            dates.append({"date": "2026-03-31", "year": 2026, "quarter": 1})
        return dates

    async def get_institutional_holdings(self, cik, year, quarter):
        return copy.deepcopy(self.curr if (year, quarter) == (2026, 2) else (self.prev or []))

    async def get_institutional_industry_breakdown(self, cik, year=0, quarter=0):
        return []

    async def get_institutional_performance(self, cik):
        return []


def _whale_scenario(seed):
    """Well-formed rows only (the rest of the whale pipeline is not under test here)."""
    rng = random.Random(10_000 + seed)
    syms = [f"W{i:02d}" for i in range(rng.choice([1, 4, 30]))]
    restated_book = len(syms) == 30 and rng.random() < 0.6      # > MAX_SPLIT_LOOKUPS suspects
    prev, curr = [], []
    for s in syms:
        pv, ps = rng.choice([10_000_000, 3_000_000]), rng.choice([100_000, 1_000_000])
        k = rng.choice([10, 0.1, 2] if restated_book else [10, 0.1, 2, 1.0, 1.0, 1.5])
        m = 1.0 if restated_book else rng.choice([1.0, 1.1, 0.5])
        curr.append({"symbol": s, "value": pv * m, "shares": ps * k})
        prev.append({"symbol": s, "value": pv, "shares": ps})
    has_prev = rng.random() > 0.15
    split_beh = {s: rng.choice(_SPLIT_BEHAVIOURS) for s in syms}
    flag_beh = {s: rng.choice(_FLAG_BEHAVIOURS) for s in syms}
    return curr, (prev if has_prev else None), split_beh, flag_beh


@pytest.mark.parametrize("seed", range(40))
def test_the_whale_request_path_hands_the_differ_what_the_old_block_computed(monkeypatch, seed):
    curr, prev, split_beh, flag_beh = _whale_scenario(seed)
    seen: Dict[str, Any] = {}
    original = WhaleService._diff_quarters

    def _spy(self, current_raw, previous_raw, filing_date, total, split_ratios=None,
             unclassified_tickers=None):
        seen["split_ratios"] = dict(split_ratios or {})
        seen["unclassified"] = set(unclassified_tickers or set())
        return original(self, current_raw, previous_raw, filing_date, total,
                        split_ratios, unclassified_tickers)

    async def _enrich(self, holdings, need_sectors=False):
        return holdings, []

    async def _sync(self, *a, **k):
        return None

    monkeypatch.setattr(WhaleService, "_diff_quarters", _spy)
    monkeypatch.setattr(WhaleService, "_enrich_from_profiles", _enrich)
    monkeypatch.setattr(WhaleService, "_sync_to_whale_tables", _sync)
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _SB())
    monkeypatch.setattr(wsvc, "sb_exec", _sb_exec)
    wsvc._filing_dates_cache.clear()
    try:
        svc = WhaleService.__new__(WhaleService)
        svc.fmp = _WhaleFMP(curr, prev)
        svc.corporate_actions = _ScriptedActions(split_beh, flag_beh)
        snap = asyncio.run(svc._process_13f_path("whale-adv", "0000000002"))
    finally:
        wsvc._filing_dates_cache.clear()

    ref_seam = _ScriptedActions(split_beh, flag_beh)
    ratios, unclassified, failed = asyncio.run(_ref_old_block(
        curr, prev or [], "2026-03-31" if prev is not None else None, "2026-06-30", ref_seam))
    assert seen["split_ratios"] == ratios
    assert seen["unclassified"] == unclassified
    assert (snap["raw_hash"] is None) == bool(failed)
    assert svc.corporate_actions.log == ref_seam.log


def test_the_whale_end_to_end_scenarios_cover_every_branch():
    """Anti-vacuity for the parametrized end-to-end parity test above."""
    cov = {"ratio": 0, "failed": 0, "flag_only": 0, "capped": 0, "first_filing": 0}
    for seed in range(40):
        curr, prev, split_beh, flag_beh = _whale_scenario(seed)
        seam = _ScriptedActions(split_beh, flag_beh)
        ratios, unclassified, failed = asyncio.run(_ref_old_block(
            curr, prev or [], "2026-03-31" if prev is not None else None, "2026-06-30", seam))
        cov["ratio"] += bool(ratios)
        cov["failed"] += bool(failed)
        cov["flag_only"] += bool(unclassified - failed)
        cov["capped"] += sum(1 for c in seam.log if c[0] == "split") == MAX_SPLIT_LOOKUPS
        cov["first_filing"] += prev is None
    assert all(v >= 2 for v in cov.values()), cov


def test_every_name_hydrate_whales_imports_from_whale_service_still_exists():
    """`scripts/hydrate_whales.py` imports split helpers BY NAME from `whale_service`; the
    extraction moved them. Parsed, not imported (the script configures logging/env)."""
    import ast

    script = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_whales.py"
    tree = ast.parse(script.read_text())
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             and n.module == "app.services.whale_service" for a in n.names]
    assert "_split_ratio_in_window" in names and "WhaleService" in names, "anti-vacuity"
    missing = [n for n in names if not hasattr(wsvc, n)]
    assert missing == []
    assert callable(WhaleService._suspicious_split_tickers)
    rows_c, rows_p = [{"symbol": "NVDA", "value": 1e7, "shares": 1e6}], [{"symbol": "NVDA", "value": 1e7, "shares": 1e5}]
    assert WhaleService._suspicious_split_tickers(rows_c, rows_p) == _ref_suspicious(rows_c, rows_p) == ["NVDA"]
    assert wsvc._split_ratio_in_window(_SPLIT_BEHAVIOURS[6], "2026-03-31", "2026-06-30") == \
        _ref_split_ratio_in_window(_SPLIT_BEHAVIOURS[6], "2026-03-31", "2026-06-30") == 3.0


# The characterisation file's own fixtures, run through BOTH implementations.

import test_thirteen_f_splits_characterisation as _C  # noqa: E402  (tests/ is on sys.path under pytest)

_KLAC_P, _KLAC_C = [_C._row("KLAC", 10_000_000, 1_000_000)], [_C._row("KLAC", 10_000_000, 100_000)]
_NAMES = [f"T{i:02d}" for i in range(30)]
_CHARACTERISATION = {
    "forward": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(splits={"NVDA": _C.TEN_TO_ONE})),
    "reverse": (_KLAC_C, _KLAC_P, lambda: _C._Actions(splits={"KLAC": _C.ONE_FOR_TEN})),
    "early": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(
        splits={"NVDA": [{"date": "2026-03-25", "numerator": 10, "denominator": 1}]})),
    "unclassified": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(splits={"NVDA": []}, flags={"NVDA": True})),
    "none_lookup": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(splits={"NVDA": None})),
    "raising_lookup": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(splits={"NVDA": RuntimeError("429")})),
    "raising_probe": (_C.CURR_FWD, _C.PREV, lambda: _C._Actions(
        splits={"NVDA": _C.TEN_TO_ONE}, flags={"NVDA": RuntimeError("timeout")})),
    "batch_failure": (_C.CURR_FWD + _KLAC_C, _C.PREV + _KLAC_P, lambda: _C._SyncRaisingActions()),
    "over_cap": ([_C._row(n, 1_000_000, 100_000) for n in _NAMES],
                 [_C._row(n, 1_000_000, 10_000) for n in _NAMES],
                 lambda: _C._Actions(splits={n: _C.TEN_TO_ONE for n in _NAMES})),
    "first_filing": (_C.CURR_FWD, None, lambda: _C._Actions(splits={"NVDA": _C.TEN_TO_ONE})),
    "plain_buy": ([_C._row("NVDA", 13_000_000, 130_000), _C._row("AAPL", 5_000_000, 25_000)], _C.PREV,
                  lambda: _C._Actions(splits={"NVDA": _C.TEN_TO_ONE})),
}


@pytest.mark.parametrize("case", sorted(_CHARACTERISATION))
def test_characterisation_fixtures_run_identically_through_the_helper_and_the_old_block(case):
    curr, prev, make = _CHARACTERISATION[case]
    prev_end = None if prev is None else _C.PREV_END
    new_seam, ref_seam = make(), make()
    new = asyncio.run(tfs.resolve_13f_split_adjustments(
        copy.deepcopy(curr), copy.deepcopy(prev), prev_end, _C.CURR_END, actions=new_seam, log_ctx=case))
    ref = asyncio.run(_ref_old_block(copy.deepcopy(curr), copy.deepcopy(prev), prev_end, _C.CURR_END, ref_seam))
    assert new == ref
    assert getattr(new_seam, "split_calls", None) == getattr(ref_seam, "split_calls", None)
    assert getattr(new_seam, "flag_calls", None) == getattr(ref_seam, "flag_calls", None)
