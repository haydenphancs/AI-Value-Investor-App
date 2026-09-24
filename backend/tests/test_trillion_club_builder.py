"""`trillion_club.builder.build_filing` against a fake FMP fed from real fixtures.

Pins the acceptance figures from the 2026-09-24 research (NVIDIA / Alphabet / Amazon / AMD
2026-Q2, Berkshire 2023-Q3 with its 13F-HR/A) and every failure path: a strict raise, an
empty listed quarter, a refused 7,720-row book, a partial profile failure, multi-hit and
never-resolving CUSIPs, split-lookup failure, and the JSON-safety of the written row.

Hermetic: the fake records every call; nothing reaches FMP or Supabase.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException
from app.services.trillion_club import builder as B

FIX = Path(__file__).parent / "fixtures" / "trillion_club"
EXTRACTS = json.loads((FIX / "extracts_2026.json").read_text())
EXTRACTS.update(json.loads((FIX / "extracts_berkshire_2023.json").read_text()))
EXTRACTS.pop("_meta", None)
DATES = json.loads((FIX / "dates.json").read_text())
PROFILES = json.loads((FIX / "profiles.json").read_text())
SEARCH = json.loads((FIX / "search.json").read_text())

NVIDIA, ALPHABET, AMAZON, AMD, BRK = "0001045810", "0001652044", "0001018724", "0000002488", "0001067983"
TODAY = date(2026, 9, 24)


def _generic_profile(sym):
    return {"symbol": sym, "companyName": f"{sym} Inc.", "exchange": "NYSE", "ipoDate": "1990-01-02",
            "sector": "Financial Services", "isActivelyTrading": True}


class FakeFMP:
    """Fixture-backed stand-in for FMPClient. Knobs let each test break one thing."""

    def __init__(self, extracts=None, *, profiles=None, generic_profiles=False, dates=None):
        self.extracts = copy.deepcopy(extracts if extracts is not None else EXTRACTS)
        self.profiles = profiles if profiles is not None else PROFILES
        self.generic_profiles = generic_profiles
        self.dates = dates if dates is not None else DATES
        self.raise_on = {}                 # (cik, "YYYY-Qn") -> exception
        self.dates_raise = None
        self.search_isin_map = dict(SEARCH["search_isin"])
        self.search_cusip_map = dict(SEARCH["search_cusip"])
        self.search_raise = None
        self.missing_profiles = set()
        self.profile_batch_raise = False
        self.calls = {"extract": [], "dates": [], "isin": [], "cusip": [], "profiles": []}

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        assert strict is True, "the builder must fetch strictly"
        key = f"{year}-Q{quarter}"
        self.calls["extract"].append((cik, key))
        if (cik, key) in self.raise_on:
            raise self.raise_on[(cik, key)]
        return copy.deepcopy(self.extracts.get(cik, {}).get(key, []))

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        assert strict is True
        self.calls["dates"].append(cik)
        if self.dates_raise:
            raise self.dates_raise
        return copy.deepcopy(self.dates.get(cik, []))

    async def search_isin(self, isin):
        self.calls["isin"].append(isin)
        if self.search_raise:
            raise self.search_raise
        return copy.deepcopy(self.search_isin_map.get(isin, []))

    async def search_cusip(self, cusip):
        self.calls["cusip"].append(cusip)
        if self.search_raise:
            raise self.search_raise
        return copy.deepcopy(self.search_cusip_map.get(cusip, []))

    async def get_company_profiles_batch(self, symbols):
        self.calls["profiles"].append(list(symbols))
        assert len(symbols) <= 50, "get_company_profiles_batch silently truncates past 50"
        if self.profile_batch_raise:
            raise FMPRateLimitException("429")
        out = []
        for s in symbols:
            if s in self.missing_profiles:
                continue
            p = self.profiles.get(s) or (_generic_profile(s) if self.generic_profiles else None)
            if p:
                out.append(copy.deepcopy(p))
        return out


class FakeActions:
    def __init__(self, splits=None, flags=None):
        self.splits, self.flags = splits or {}, flags or {}
        self.calls = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.calls.append(t)
        return self.splits.get(t, [])

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, **kw):
        return self.flags.get(t, False)


def _build(fmp, cik, y, q, *, prev=True, actions=None, stored=None, today=TODAY, **kw):
    return asyncio.run(B.build_filing(
        fmp, cik, y, q, prev_quarter_available=prev, actions=actions or FakeActions(),
        stored_unresolved=stored or {}, today=today, **kw,
    ))


def _change(bf, symbol):
    return next(r for r in bf.changes["rows"] if r["symbol"] == symbol)


# ── the acceptance fixtures ──────────────────────────────────────────────────────────


def test_nvidia_2026_q2():
    fmp = FakeFMP()
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.build_status == "complete" and bf.degraded_reasons == []
    assert bf.position_count == 8 and bf.total_value == 63_439_974_569
    assert bf.period == "2026-Q2" and bf.period_end == date(2026, 6, 30)
    assert bf.filed_on == date(2026, 8, 14) and bf.amended_on is None
    assert bf.accessions == ["0001045810-26-000065"]
    assert bf.changes["counts"]["newly_reported"] == 1 and bf.changes["counts"]["unchanged"] == 7
    spcx = _change(bf, "SPCX")
    assert spcx["change"] == "newly_reported" and spcx["newly_listed"] is True
    top = [(h["symbol"], round(h["weight"], 4)) for h in bf.holdings[:3]]
    assert top == [("INTC", 0.4727), ("SPCX", 0.3306), ("CRWV", 0.0741)]
    assert bf.holdings[0]["name"] == "Intel Corp." and bf.holdings[2]["name"] == "CoreWeave, Inc."
    assert all(h["routable"] for h in bf.holdings)
    assert [h["symbol"] for h in bf.holdings if h["is_small"]] == ["NBIS", "GENB"]
    # both quarters fetched LIVE, strictly
    assert sorted(fmp.calls["extract"]) == [(NVIDIA, "2026-Q1"), (NVIDIA, "2026-Q2")]
    assert fmp.calls["isin"] == [] and fmp.calls["cusip"] == []


def test_alphabet_ethos_resolves_to_life_through_search_isin():
    fmp = FakeFMP()
    bf = _build(fmp, ALPHABET, 2026, 2)
    assert bf.build_status == "complete"
    ethos = next(h for h in bf.holdings if h["cusip"] == "29765A101")
    assert ethos["symbol"] == "LIFE" and ethos["routable"] is True
    assert ethos["name"] == "Ethos Technologies Inc."
    assert fmp.calls["isin"] == ["US29765A1016"], "one lookup per CUSIP, shared by both quarters"
    assert fmp.calls["cusip"] == [], "search-cusip is only the fallback"
    assert bf.unresolved == {}
    spcx = _change(bf, "SPCX")
    assert spcx["newly_listed"] is True and spcx["weight"] == pytest.approx(0.9505, abs=1e-4)
    assert _change(bf, "LIFE")["change"] == "decreased"


def test_amazon_and_amd():
    amzn = _build(FakeFMP(), AMAZON, 2026, 2)
    assert {r["symbol"] for r in amzn.changes["rows"] if r["change"] == "newly_reported"} == {"XE", "ALGT"}
    assert {r["symbol"] for r in amzn.changes["rows"] if r["change"] == "no_longer_reported"} == {"NAUT"}
    amd = _build(FakeFMP(), AMD, 2026, 2)
    assert {r["symbol"] for r in amd.changes["rows"] if r["change"] == "newly_reported"} == {"SPCX", "NTNX", "CBRS"}
    assert {r["symbol"] for r in amd.changes["rows"] if r["change"] == "no_longer_reported"} == {"MRVL"}
    assert amd.accessions == ["0001193125-26-352454"]


def test_berkshire_2023_q3_keeps_both_accessions_and_adds_the_chubb_amendment():
    fmp = FakeFMP(generic_profiles=True)
    bf = _build(fmp, BRK, 2023, 3, prev=False, older_filing_exists=True)
    assert bf.accessions == ["0000950123-23-011029", "0000950123-24-005653"]
    assert bf.filed_on == date(2023, 11, 16) and bf.amended_on == date(2024, 5, 15)
    assert bf.position_count == 46
    cb = next(h for h in bf.holdings if h["cusip"] == "H1467J104")
    assert cb["symbol"] == "CB" and cb["shares"] == 8_143_530
    assert bf.changes["comparison"] == "gap" and bf.changes["rows"] == []
    assert fmp.calls["dates"] == [], "older_filing_exists was supplied"


def test_berkshire_q4_sees_chubb_as_increased_only_because_q3_folds_the_amendment():
    """Why the builder fetches N-1 LIVE and why an amended period must cascade: without
    the May-2024 amendment, Q4's Chubb row reads "newly reported"."""
    with_amendment = _build(FakeFMP(generic_profiles=True), BRK, 2023, 4)
    cb = _change(with_amendment, "CB")
    assert cb["change"] == "increased" and cb["prev_shares"] == 8_143_530

    before = FakeFMP(generic_profiles=True)
    before.extracts[BRK]["2023-Q3"] = [r for r in before.extracts[BRK]["2023-Q3"]
                                       if "0000950123-24-005653" not in r["link"]]
    stale = _build(before, BRK, 2023, 4)
    assert _change(stale, "CB")["change"] == "newly_reported"
    assert stale.changes != with_amendment.changes


# ── per-accession normalisation ──────────────────────────────────────────────────────


def _row(cusip, shares, value, acc, filed, *, symbol="ABC", name="ABC CORP", period="2026-06-30",
         cik=NVIDIA, **extra):
    r = {"securityCusip": cusip, "shares": shares, "value": value, "symbol": symbol,
         "nameOfIssuer": name, "titleOfClass": "COM", "putCallShare": "", "sharesType": "SH",
         "date": period, "filingDate": filed, "acceptedDate": filed, "cik": cik,
         "link": f"https://www.sec.gov/Archives/edgar/data/1/{acc.replace('-', '')}/{acc}-index.htm",
         "finalLink": ""}
    r.update(extra)
    return r


def test_a_restatement_replaces_and_a_new_holdings_amendment_adds():
    raw = [
        _row("111111118", 100, 1000, "0000000001-26-000001", "2026-08-14"),
        _row("222222226", 50, 500, "0000000001-26-000001", "2026-08-14", symbol="DEF"),
        _row("111111118", 120, 1200, "0000000001-26-000009", "2026-10-01"),                 # restated
        _row("333333334", 10, 100, "0000000001-26-000009", "2026-10-01", symbol="GHI"),     # added
    ]
    norm = B.normalize_rows(raw)
    got = {r["cusip"]: (r["shares"], r["value"]) for r in norm.rows}
    assert got == {"111111118": (120.0, 1200.0), "222222226": (50.0, 500.0), "333333334": (10.0, 100.0)}
    assert norm.accessions == ["0000000001-26-000001", "0000000001-26-000009"]
    assert norm.amended_on == date(2026, 10, 1) and norm.filed_on == date(2026, 8, 14)


def test_rows_within_one_accession_are_summed():
    raw = [_row("111111118", 60, 600, "0000000001-26-000001", "2026-08-14"),
           _row("111111118", 40, 400, "0000000001-26-000001", "2026-08-14")]
    (r,) = B.normalize_rows(raw).rows
    assert (r["shares"], r["value"]) == (100.0, 1000.0)


def test_a_later_accession_without_a_symbol_keeps_the_earlier_one():
    raw = [_row("111111118", 100, 1000, "0000000001-26-000001", "2026-08-14", symbol="ABC"),
           _row("111111118", 120, 1200, "0000000001-26-000009", "2026-10-01", symbol=None, name="")]
    (r,) = B.normalize_rows(raw).rows
    assert r["symbol"] == "ABC" and r["name"] == "ABC CORP" and r["shares"] == 120.0


def test_rows_for_another_period_or_cik_are_excluded():
    raw = [_row("111111118", 100, 1000, "0000000001-26-000001", "2026-08-14"),
           _row("222222226", 100, 1000, "0000000001-26-000001", "2026-08-14", period="2026-03-31"),
           _row("333333334", 100, 1000, "0000000001-26-000001", "2026-08-14", cik="0000000009")]
    norm = B.normalize_rows(raw, expected_period_end=date(2026, 6, 30), expected_cik=NVIDIA)
    assert [r["cusip"] for r in norm.rows] == ["111111118"] and norm.excluded_rows == 2


# ── refusal and unavailability: write nothing ──────────────────────────────────────


def test_a_jpm_shaped_7720_row_book_is_refused_before_any_lookup(caplog):
    fmp = FakeFMP()
    fmp.extracts["0000019617"] = {"2026-Q2": [
        _row(f"{i:08d}0", 100, 1000, "0000019617-26-000001", "2026-08-14", symbol=None, cik="0000019617")
        for i in range(7720)
    ]}
    with caplog.at_level(logging.WARNING), pytest.raises(B.FilingRefused):
        _build(fmp, "0000019617", 2026, 2, prev=False, older_filing_exists=True)
    assert fmp.calls["isin"] == fmp.calls["cusip"] == fmp.calls["profiles"] == []
    assert "7720 rows > 200" in caplog.text


def test_exactly_max_rows_is_accepted():
    rows = [_row(f"{i:08d}0", 100, 1000 + i, "0000000001-26-000001", "2026-08-14", symbol=f"S{i}")
            for i in range(B.MAX_ROWS)]
    fmp = FakeFMP(extracts={NVIDIA: {"2026-Q2": rows}}, generic_profiles=True)
    bf = _build(fmp, NVIDIA, 2026, 2, prev=False, older_filing_exists=False)
    assert bf.position_count == B.MAX_ROWS and bf.changes["comparison"] == "first_filing"
    # profiles in chunks of 50: 200 symbols -> 4 calls
    assert [len(c) for c in fmp.calls["profiles"]] == [50, 50, 50, 50]


@pytest.mark.parametrize("period", ["2026-Q2", "2026-Q1"])
def test_a_strict_fetch_failure_on_either_quarter_is_unavailable(period):
    fmp = FakeFMP()
    fmp.raise_on[(NVIDIA, period)] = FMPUnavailableException("503")
    with pytest.raises(B.FilingUnavailable, match="503"):
        _build(fmp, NVIDIA, 2026, 2)


@pytest.mark.parametrize("period", ["2026-Q2", "2026-Q1"])
def test_an_empty_extract_for_a_listed_quarter_is_unavailable(period):
    fmp = FakeFMP()
    fmp.extracts[NVIDIA][period] = []
    with pytest.raises(B.FilingUnavailable, match="EMPTY"):
        _build(fmp, NVIDIA, 2026, 2)


def test_a_non_list_extract_is_unavailable():
    fmp = FakeFMP()
    fmp.extracts[NVIDIA]["2026-Q2"] = {"Error Message": "limit"}
    with pytest.raises(B.FilingUnavailable):
        _build(fmp, NVIDIA, 2026, 2)


def test_a_filing_whose_every_row_is_excluded_is_unavailable():
    fmp = FakeFMP()
    fmp.extracts[NVIDIA]["2026-Q2"] = [dict(r, putCallShare="Put") for r in fmp.extracts[NVIDIA]["2026-Q2"]]
    with pytest.raises(B.FilingUnavailable, match="excluded"):
        _build(fmp, NVIDIA, 2026, 2)


def test_a_previous_quarter_whose_every_row_is_excluded_is_unavailable():
    """Not a bare ValueError from the diff: the caller must see 'write nothing'."""
    fmp = FakeFMP()
    fmp.extracts[NVIDIA]["2026-Q1"] = [dict(r, sharesType="PRN") for r in fmp.extracts[NVIDIA]["2026-Q1"]]
    with pytest.raises(B.FilingUnavailable, match="2026-Q1"):
        _build(fmp, NVIDIA, 2026, 2)


def test_gap_or_first_filing_is_decided_from_dates_when_not_supplied():
    fmp = FakeFMP()
    gap = _build(fmp, NVIDIA, 2026, 2, prev=False)
    assert gap.changes["comparison"] == "gap" and gap.changes["prev_period"] == "2026-Q1"
    assert fmp.calls["dates"] == [NVIDIA]
    # AMD's first 13F was 2025-Q4: nothing older on file (and a junk dates row is ignored).
    fmp2 = FakeFMP(dates={AMD: [{"date": "2025-12-31", "year": 2025, "quarter": 4}, {"year": "junk"}]})
    fmp2.extracts[AMD]["2025-Q4"] = [dict(r, date="2025-12-31") for r in fmp2.extracts[AMD]["2026-Q1"]]
    first = _build(fmp2, AMD, 2025, 4, prev=False)
    assert first.changes["comparison"] == "first_filing" and first.changes["prev_period"] is None


def test_a_failed_dates_probe_is_unavailable_not_a_first_filing():
    fmp = FakeFMP()
    fmp.dates_raise = FMPRateLimitException("429")
    with pytest.raises(B.FilingUnavailable, match="first filing from a gap"):
        _build(fmp, NVIDIA, 2026, 2, prev=False)


@pytest.mark.parametrize("cik", ["1045810", "000104581O", None, 1045810])
def test_a_bad_cik_is_a_value_error(cik):
    with pytest.raises(ValueError):
        _build(FakeFMP(), cik, 2026, 2)


# ── degraded builds ────────────────────────────────────────────────────────────────────


def test_a_missing_profile_degrades_and_never_invents_newly_listed():
    fmp = FakeFMP()
    fmp.missing_profiles = {"SPCX"}
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.build_status == "degraded"
    assert bf.degraded_reasons == ["profiles_missing:SPCX"]
    spcx = _change(bf, "SPCX")
    assert spcx["change"] == "newly_reported" and spcx["newly_listed"] is False
    h = next(h for h in bf.holdings if h["symbol"] == "SPCX")
    assert h["ipo_date"] is None and h["routable"] is False
    assert h["name"] == "SPACE EXPLORATION TECHN CORP", "falls back to the SEC issuer name"


def test_a_failing_profile_batch_degrades_every_symbol():
    fmp = FakeFMP()
    fmp.profile_batch_raise = True
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert bf.build_status == "degraded" and bf.degraded_reasons[0].startswith("profiles_missing:")
    assert not any(h["routable"] for h in bf.holdings)
    assert not any(r["newly_listed"] for r in bf.changes["rows"])


def test_a_failed_split_lookup_degrades():
    fmp = FakeFMP()
    # Make INTC split-shaped: 10x the shares at the same value.
    for r in fmp.extracts[NVIDIA]["2026-Q2"]:
        if r["symbol"] == "INTC":
            r["shares"] = r["shares"] * 10
    bf = _build(fmp, NVIDIA, 2026, 2, actions=FakeActions(splits={"INTC": None}))
    assert bf.build_status == "degraded"
    assert "split_lookup_failed:INTC" in bf.degraded_reasons
    assert _change(bf, "INTC")["change"] == "corporate_action", "backstop armed, never a 9x 'increase'"


def test_a_confirmed_split_restates_and_stays_complete():
    fmp = FakeFMP()
    for r in fmp.extracts[NVIDIA]["2026-Q2"]:
        if r["symbol"] == "INTC":
            r["shares"] = r["shares"] * 10
    acts = FakeActions(splits={"INTC": [{"date": "2026-05-01", "numerator": 10, "denominator": 1}]})
    bf = _build(fmp, NVIDIA, 2026, 2, actions=acts)
    assert bf.build_status == "complete" and "INTC" in acts.calls
    assert bf.changes["counts"]["unchanged"] == 7, "a held-through 10:1 split is unchanged"


def _with_symbolless(fmp, cusip, name, cik=NVIDIA):
    extra = _row(cusip, 1000, 5_000_000, "0001045810-26-000065", "2026-08-14", symbol=None, name=name)
    fmp.extracts[cik]["2026-Q2"].append(extra)


def _move_real_nbis_row(fmp):
    """NVIDIA's real NBIS row IS CUSIP N97284108; re-key it so the symbol-less test row is
    the only one on that CUSIP (else normalisation merges the two within the accession)."""
    for r in fmp.extracts[NVIDIA]["2026-Q2"]:
        if r["symbol"] == "NBIS":
            r["securityCusip"] = "999999992"


def test_a_cins_with_two_hits_picks_the_actively_trading_name_match():
    fmp = FakeFMP()
    _with_symbolless(fmp, "N97284108", "NEBIUS GROUP N V")
    _move_real_nbis_row(fmp)
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["isin"] == [], "a CINS number never gets a US ISIN"
    assert fmp.calls["cusip"] == ["N97284108"]
    h = next(h for h in bf.holdings if h["cusip"] == "N97284108")
    assert h["symbol"] == "NBIS", "YNDX is not actively trading"


def test_an_inactive_name_match_loses_to_the_active_one():
    """Both names match, only NBIS still trades -> NBIS (the actively-trading filter)."""
    fmp = FakeFMP(profiles={**PROFILES, "YNDX": {**PROFILES["YNDX"], "companyName": "Nebius Legacy N.V."}})
    _with_symbolless(fmp, "N97284108", "NEBIUS GROUP N V")
    _move_real_nbis_row(fmp)
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert next(h for h in bf.holdings if h["cusip"] == "N97284108")["symbol"] == "NBIS"


def test_a_renamed_issuer_resolves_to_the_only_active_candidate():
    """An older filing names the issuer "YANDEX N V"; the identifier is the same security,
    and NBIS is its only live listing."""
    fmp = FakeFMP()
    _with_symbolless(fmp, "N97284108", "YANDEX N V")
    _move_real_nbis_row(fmp)
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert next(h for h in bf.holdings if h["cusip"] == "N97284108")["symbol"] == "NBIS"


def test_a_restatement_accession_replaces_the_position_in_the_build():
    fmp = FakeFMP()
    intc = next(r for r in fmp.extracts[NVIDIA]["2026-Q2"] if r["symbol"] == "INTC")
    restated = dict(intc, shares=intc["shares"] + 1_000, value=intc["value"] + 140_000,
                    filingDate="2026-09-01", acceptedDate="2026-09-01",
                    link=intc["link"].replace("000065", "000070"))
    fmp.extracts[NVIDIA]["2026-Q2"].append(restated)
    bf = _build(fmp, NVIDIA, 2026, 2)
    h = next(h for h in bf.holdings if h["symbol"] == "INTC")
    assert h["shares"] == intc["shares"] + 1_000, "replaced, not summed"
    assert bf.total_value == 63_439_974_569 + 140_000 and bf.position_count == 8
    assert bf.accessions == ["0001045810-26-000065", "0001045810-26-000070"]
    assert bf.amended_on == date(2026, 9, 1)
    assert _change(bf, "INTC")["change"] == "increased"


def test_two_active_name_matching_hits_stay_unresolved():
    fmp = FakeFMP(profiles={**PROFILES, "YNDX": {**PROFILES["YNDX"], "isActivelyTrading": True,
                                                   "companyName": "Nebius Holdings"}})
    _with_symbolless(fmp, "N97284108", "NEBIUS GROUP N V")
    _move_real_nbis_row(fmp)
    bf = _build(fmp, NVIDIA, 2026, 2)
    h = next(h for h in bf.holdings if h["cusip"] == "N97284108")
    assert h["symbol"] is None and h["routable"] is False
    assert bf.unresolved == {"N97284108": TODAY.isoformat()}
    assert bf.build_status == "degraded" and "unresolved_pending:N97284108" in bf.degraded_reasons


def test_a_canadian_cusip_falls_back_to_search_cusip():
    fmp = FakeFMP()
    fmp.search_cusip_map["98390R102"] = [{"symbol": "XNDU", "companyName": "Xanadu Quantum Technologies"}]
    _with_symbolless(fmp, "98390R102", "XANADU QUANTUM TECHNOLOGIES LTD")
    bf = _build(fmp, NVIDIA, 2026, 2)
    assert fmp.calls["isin"] == ["US98390R1023"] and fmp.calls["cusip"] == ["98390R102"]
    assert next(h for h in bf.holdings if h["cusip"] == "98390R102")["symbol"] == "XNDU"


def test_a_never_resolving_cusip_is_retried_for_seven_days_then_terminal():
    stuck = "123456782"      # valid check digit, no FMP mapping
    fresh = FakeFMP()
    _with_symbolless(fresh, stuck, "MYSTERY HOLDINGS INC")
    day0 = _build(fresh, NVIDIA, 2026, 2)
    assert day0.unresolved == {stuck: TODAY.isoformat()} and day0.build_status == "degraded"
    assert fresh.calls["isin"] == ["US1234567824"] and fresh.calls["cusip"] == [stuck]

    seen = (TODAY - timedelta(days=B.UNRESOLVED_RETRY_DAYS)).isoformat()
    day7 = FakeFMP()
    _with_symbolless(day7, stuck, "MYSTERY HOLDINGS INC")
    b7 = _build(day7, NVIDIA, 2026, 2, stored={stuck: seen})
    assert b7.build_status == "degraded" and b7.unresolved == {stuck: seen}, "first-seen is kept"
    assert day7.calls["cusip"] == [stuck], "still retried on day 7"

    old = (TODAY - timedelta(days=B.UNRESOLVED_RETRY_DAYS + 1)).isoformat()
    day8 = FakeFMP()
    _with_symbolless(day8, stuck, "MYSTERY HOLDINGS INC")
    b8 = _build(day8, NVIDIA, 2026, 2, stored={stuck: old})
    assert day8.calls["isin"] == day8.calls["cusip"] == [], "terminal: no more lookups"
    assert b8.build_status == "complete" and b8.unresolved == {stuck: old}
    h = next(h for h in b8.holdings if h["cusip"] == stuck)
    assert h["symbol"] is None and h["name"] == "MYSTERY HOLDINGS INC"


def test_a_resolved_cusip_leaves_the_unresolved_map():
    bf = _build(FakeFMP(), ALPHABET, 2026, 2, stored={"29765A101": "2026-09-20"})
    assert bf.unresolved == {} and bf.build_status == "complete"


def test_a_raising_symbol_lookup_degrades(caplog):
    fmp = FakeFMP()
    fmp.search_raise = FMPRateLimitException("429")
    with caplog.at_level(logging.WARNING):
        bf = _build(fmp, ALPHABET, 2026, 2)
    assert bf.build_status == "degraded" and "symbol_lookup_failed" in bf.degraded_reasons
    assert "symbol lookup for CUSIP 29765A101 failed" in caplog.text
    # the Ethos row is kept, unresolved — not dropped
    assert any(h["cusip"] == "29765A101" and h["symbol"] is None for h in bf.holdings)


def test_unroutable_holdings():
    profiles = {**PROFILES, "GENB": {**PROFILES["GENB"], "exchange": "OTC"},
                "COHR": {**PROFILES["COHR"], "isActivelyTrading": False}}
    bf = _build(FakeFMP(profiles=profiles), NVIDIA, 2026, 2)
    routable = {h["symbol"]: h["routable"] for h in bf.holdings}
    assert routable["GENB"] is False and routable["COHR"] is False and routable["INTC"] is True


# ── the hash ─────────────────────────────────────────────────────────────────────────


def test_the_hash_is_stable_under_reordering_and_sensitive_to_every_field():
    rows = EXTRACTS[ALPHABET]["2026-Q2"]
    base = B.raw_hash_of(rows)
    shuffled = copy.deepcopy(rows)
    random.Random(7).shuffle(shuffled)
    assert B.raw_hash_of(shuffled) == base
    assert B.raw_hash_of([dict(r, shares=float(r["shares"])) for r in rows]) == base, "100 == 100.0"
    for field_, new in [("shares", 1), ("value", 1), ("symbol", "ZZZZ"), ("nameOfIssuer", "RENAMED"),
                        ("securityCusip", "000000000"), ("putCallShare", "Put")]:
        mutated = copy.deepcopy(rows)
        mutated[0][field_] = new
        assert B.raw_hash_of(mutated) != base, field_
    amended = copy.deepcopy(rows) + [dict(rows[0], link=rows[0]["link"].replace("000073", "000099"))]
    assert B.raw_hash_of(amended) != base
    assert base.startswith(B.HASH_VERSION + ":")


def test_a_rebuild_on_the_same_data_has_the_same_hash():
    assert _build(FakeFMP(), NVIDIA, 2026, 2).raw_hash == _build(FakeFMP(), NVIDIA, 2026, 2).raw_hash


# ── the written row ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cik", [NVIDIA, ALPHABET, AMAZON, AMD])
def test_as_row_is_json_safe_and_matches_the_table(cik):
    row = _build(FakeFMP(), cik, 2026, 2).as_row()
    text = json.dumps(row, allow_nan=False)
    assert set(row) == {"cik", "period", "period_end", "filed_on", "amended_on", "accessions",
                        "total_value", "position_count", "holdings", "changes", "excluded_rows",
                        "unresolved", "raw_hash", "build_status", "source"}
    assert row["source"] == "fmp" and row["period_end"] == "2026-06-30"
    assert "degraded_reasons" not in text
    for h in row["holdings"]:
        assert set(h) == {"cusip", "symbol", "name", "title_of_class", "shares", "value", "weight",
                          "is_small", "sector", "ipo_date", "exchange", "routable"}
        assert h["name"], "never an empty name"
    assert set(row["changes"]) == {"comparison", "prev_period", "counts", "rows"}


def test_as_row_refuses_a_non_finite_float():
    bf = _build(FakeFMP(), NVIDIA, 2026, 2)
    bf.holdings[3]["weight"] = float("nan")
    with pytest.raises(ValueError, match=r"holdings\[3\]\.weight"):
        bf.as_row()


@pytest.mark.parametrize("field_, value", [("build_status", "partial"), ("source", "sec"),
                                           ("cik", "123"), ("period", "2026Q2")])
def test_as_row_refuses_values_the_table_checks_would_reject(field_, value):
    bf = _build(FakeFMP(), NVIDIA, 2026, 2)
    setattr(bf, field_, value)
    with pytest.raises(ValueError):
        bf.as_row()


def test_names_match_and_display_name():
    assert B.names_match("SPACE EXPLORATION TECHN CORP", "Space Exploration Technologies Corp.")
    assert B.names_match("NEBIUS GROUP N V", "Nebius Group N.V.")
    assert not B.names_match("NEBIUS GROUP N V", "Yandex N.V.")
    assert not B.names_match("", "Yandex") and not B.names_match(None, None)
    assert B.display_name("Arm Holdings plc American Depositary Shares", "ARM", "ARM") == "Arm Holdings plc"
    assert B.display_name(None, "", "SYM") == "SYM"


# ── the FMP wrappers the builder relies on ────────────────────────────────────────────
#
# `_make_request_impl` is stubbed on the INSTANCE, so `_make_request`'s entitlement gate
# still runs: these also prove search-cusip / market-capitalization-batch pass the licence.

from app.integrations.fmp import FMPClient  # noqa: E402


def _client(behavior):
    c = FMPClient()
    seen = []

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
def test_13f_wrappers_swallow_by_default_and_raise_when_strict(method, args):
    c, _ = _client(lambda e, p: FMPRateLimitException("429"))
    assert asyncio.run(getattr(c, method)(*args)) == [], "the whale default is unchanged"
    with pytest.raises(FMPRateLimitException):
        asyncio.run(getattr(c, method)(*args, strict=True))


@pytest.mark.parametrize("method, args", [
    ("get_institutional_holdings", (NVIDIA, 2026, 2)),
    ("get_institutional_filing_dates", (NVIDIA,)),
])
def test_strict_13f_wrappers_refuse_a_non_list_body(method, args):
    c, _ = _client(lambda e, p: {"Error Message": "Limit Reach"})
    with pytest.raises(FMPUnavailableException):
        asyncio.run(getattr(c, method)(*args, strict=True))
    rows = [{"symbol": "INTC"}]
    c2, seen = _client(lambda e, p: rows)
    assert asyncio.run(getattr(c2, method)(*args, strict=True)) == rows
    assert seen[0][0].startswith("institutional-ownership/")


def test_search_isin_and_cusip_validate_before_calling():
    c, seen = _client(lambda e, p: [])
    for bad in ["", None, "US29765A101", "us29765a1016x", 12345]:
        with pytest.raises(ValueError):
            asyncio.run(c.search_isin(bad))
    for bad in ["", None, "29765A10", "29765-101", 29765101]:
        with pytest.raises(ValueError):
            asyncio.run(c.search_cusip(bad))
    assert seen == [], "no call is made for malformed input"
    asyncio.run(c.search_isin(" us29765a1016 "))
    asyncio.run(c.search_cusip("n97284108"))
    assert seen == [("search-isin", {"isin": "US29765A1016"}), ("search-cusip", {"cusip": "N97284108"})]


@pytest.mark.parametrize("method, arg", [("search_isin", "US29765A1016"), ("search_cusip", "29765A101")])
def test_search_failures_never_read_as_no_match(method, arg):
    c, _ = _client(lambda e, p: FMPRateLimitException("429"))
    with pytest.raises(FMPRateLimitException):
        asyncio.run(getattr(c, method)(arg))
    c2, _ = _client(lambda e, p: {"error": "x"})
    with pytest.raises(FMPUnavailableException):
        asyncio.run(getattr(c2, method)(arg))


def test_market_cap_batch_cleans_symbols_and_skips_an_empty_call():
    c, seen = _client(lambda e, p: [{"symbol": "AMD", "marketCap": 1.0e12}])
    assert asyncio.run(c.get_market_cap_batch([])) == [] and seen == []
    assert asyncio.run(c.get_market_cap_batch(["", None, "  "])) == [] and seen == []
    asyncio.run(c.get_market_cap_batch(["amd", "AMD", " nvda ", None, 5]))
    assert seen == [("market-capitalization-batch", {"symbols": "AMD,NVDA"})]
    c2, _ = _client(lambda e, p: {"error": "x"})
    with pytest.raises(FMPUnavailableException):
        asyncio.run(c2.get_market_cap_batch(["AMD"]))


def test_today_may_be_a_datetime_but_not_garbage():
    from datetime import datetime as _dt
    bf = _build(FakeFMP(), ALPHABET, 2026, 2, today=_dt(2026, 9, 24, 7, 0), stored={"29765A101": "x"})
    assert bf.build_status == "complete"
    with pytest.raises(ValueError, match="today"):
        _build(FakeFMP(), NVIDIA, 2026, 2, today="2026-09-24")


# ── regressions (hardening pass, 2026-09-24) ─────────────────────────────────────────


def test_an_other_period_or_other_cik_row_never_sets_accessions_or_filing_dates():
    """REGRESSION (correctness-excluded-row-sets-filed-and-amended). Was: an excluded
    other-period / other-CIK row still added its accession and filingDate, so a Q2 book
    read 'filed May 15' (before Q2 ended) and 'amended'. An amendment made only of put/call
    or PRN rows from THIS filing is still an accession of this period."""
    own = _row("111111118", 100, 1000, "0001045810-26-000065", "2026-08-14")
    stray_q1 = _row("222222226", 100, 1000, "0001045810-26-000042", "2026-05-15", period="2026-03-31")
    foreign = _row("333333334", 100, 1000, "0000000009-26-000003", "2026-09-30", cik="0000000009")
    options_only_amendment = _row("111111118", 5, 50, "0001045810-26-000080", "2026-10-02",
                                  putCallShare="Call")
    prn_only_amendment = _row("444444442", 5, 50, "0001045810-26-000090", "2026-11-03", sharesType="PRN")
    end = date(2026, 6, 30)

    for order in ([own, stray_q1, foreign], [foreign, stray_q1, own]):
        norm = B.normalize_rows(order, expected_period_end=end, expected_cik=NVIDIA)
        assert (norm.accessions, norm.filed_on, norm.amended_on) == (
            ["0001045810-26-000065"], date(2026, 8, 14), None)
        assert norm.excluded_rows == 2

    norm = B.normalize_rows([own, stray_q1, options_only_amendment, prn_only_amendment],
                            expected_period_end=end, expected_cik=NVIDIA)
    assert norm.accessions == ["0001045810-26-000065", "0001045810-26-000080", "0001045810-26-000090"]
    assert (norm.filed_on, norm.amended_on) == (date(2026, 8, 14), date(2026, 11, 3))
    assert [(r["cusip"], r["shares"]) for r in norm.rows] == [("111111118", 100.0)]


def test_a_previous_only_cusip_whose_lookup_keeps_failing_goes_terminal():
    """REGRESSION (resilience-5). Was: a symbol-less CUSIP that exists only in N-1 was looked
    up on every build but never entered ``unresolved``, so a lookup that kept failing kept
    N 'degraded' forever and the daily job rebuilt it every day. Now it is tracked with its
    first-seen date and goes terminal after UNRESOLVED_RETRY_DAYS, like a current row."""
    gone = "912345672"                      # valid check digit, only in 2026-Q1
    start = date(2026, 9, 1)
    stored: dict = {}
    history = []
    for day in range(11):
        fmp = FakeFMP()
        fmp.extracts[NVIDIA]["2026-Q1"].append(
            _row(gone, 1000, 5_000_000, "0001045810-26-000042", "2026-05-15", symbol=None,
                 name="GONE AWAY INC", period="2026-03-31"))
        fmp.search_raise = FMPRateLimitException("429")
        bf = _build(fmp, NVIDIA, 2026, 2, stored=stored, today=start + timedelta(days=day))
        history.append((day, len(fmp.calls["isin"]) + len(fmp.calls["cusip"]), bf.build_status))
        assert bf.unresolved == {gone: start.isoformat()}, f"day {day}: first-seen moved"
        row = next(r for r in bf.changes["rows"] if r["cusip"] == gone)
        assert row["change"] == "no_longer_reported" and row["symbol"] is None
        assert row["name"] == "GONE AWAY INC"
        assert gone not in {h["cusip"] for h in bf.holdings}
        stored = json.loads(json.dumps(bf.as_row()["unresolved"]))    # round-trip like JSONB
    assert history == ([(d, 1, "degraded") for d in range(B.UNRESOLVED_RETRY_DAYS + 1)]
                       + [(d, 0, "complete") for d in range(B.UNRESOLVED_RETRY_DAYS + 1, 11)])


def test_a_corrected_period_or_cik_or_class_changes_the_hash():
    """REGRESSION (hash blind spot). Was: FMP correcting a mis-dated / mis-CIK'd row flipped
    it between excluded and included with an identical hash, and the job's
    'same hash + complete' skip kept the stale book."""
    rows = EXTRACTS[NVIDIA]["2026-Q2"]
    base = B.raw_hash_of(rows)
    assert base.startswith("tc13f-v2:")
    for field_, new in [("date", "2026-03-31"), ("cik", "0000000009"), ("titleOfClass", "CL B")]:
        mutated = copy.deepcopy(rows)
        mutated[0][field_] = new
        assert B.raw_hash_of(mutated) != base, field_
    # the same values in another spelling are the same book -> the same hash
    same = copy.deepcopy(rows)
    same[0]["date"] = same[0]["date"][:10] + " 00:00:00"
    same[0]["cik"] = same[0]["cik"].lstrip("0")
    assert B.raw_hash_of(same) == base


def test_a_huge_integer_is_a_bad_number_not_a_crash():
    """REGRESSION (OverflowError). ``float(10**400)`` raised out of ``_positive_finite`` and
    ``_num_key`` (a 400-digit JSON integer decodes to an int)."""
    ok = _row("111111118", 100, 1000, "0000000001-26-000001", "2026-08-14")
    for field_ in ("shares", "value"):
        raw = [ok, dict(_row("222222226", 1, 1, "0000000001-26-000001", "2026-08-14"), **{field_: 10 ** 400})]
        norm = B.normalize_rows(raw)
        assert [r["cusip"] for r in norm.rows] == ["111111118"] and norm.excluded_rows == 1
        assert B.raw_hash_of(raw) != B.raw_hash_of([ok])
        assert B.raw_hash_of(raw) != B.raw_hash_of([ok, dict(raw[1], **{field_: 10 ** 401})])
