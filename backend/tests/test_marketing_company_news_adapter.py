"""`company_news_adapter` — the ONE FMP exemption of the marketing engine (contract D6, Drop 2a).

Hermetic: every upstream is a fake handed in through `NewsDeps` (FMP, the Trillion Club service,
Supabase, the revenue / profit / facts services, the logo transport). The registry 13F path runs
the REAL `trillion_club.builder.build_filing` against fake extracts, so the share-count cross-check
against `_whale_common.thirteen_f_share_positions` is exercised end to end.

Pinned here:

* the skip-vs-raise contract: every upstream failure (page-0 raise, partial page, not entitled,
  an empty market-wide week, all profiles missing, two unavailable 13F filers, an unreachable
  club) RAISES `MarketingNewsUnavailable` with the right reason; "nothing qualified" is a skip;
* the ledger check runs before any profile call (call counts), and before any 13F build;
* every per-candidate gate counts its reason (price plausibility, the cap share, co-CEOs, members
  of Congress — never logged by name —, warrants, ETFs, the 13F move gates, the Money Map
  consistency gates) and every counted reason is a `REJECTION_REASONS` code;
* the memo: two concurrent calls share one FMP walk, an exception (and an empty feed) is never
  memoized, a walk that outlives the caller's budget still warms the memo;
* the budget: less than one second left → `budget_exhausted`, at the start or mid-chain;
* `fetch_logo`: the exact-URL rule, no redirects, the default image, a streamed size cap, and it
  never raises;
* review round 7: THE amendment rule (every Form 4/A shape of rounds 1-6 refuses the row, a week
  with no 4/A is byte-identical to round 6), the 13F materiality floor, a Money Map that is mostly
  "Other", and FMP segment labels;
* review round 8: the amendment rule is per ISSUER (another reporter, a co-reporter entity, a
  name-only filer, a blank or "N/A" symbol with the issuer CIK, the issuer's other share class —
  in the walk or in the issuer read — all refuse; another issuer changes nothing),
  the issuer read must cover the row's own published lines, a CEO / CFO is published only
  under a sitting officer's title (an allow-list, applied to every row of the person in the walk
  and the issuer read), 13F moves are chosen by size with each kind's largest reserved and an
  exit carries its previous-quarter value, and "The Kroger Co." is shown as "Kroger".
* review round 9 (2026-10-10): the Form 4/A read is per ISSUER (``companyCik`` = the profile's
  CIK, every share class at once; the client parameter itself is tested here), it must hold the
  person's newest-day lines and every walk row of the issuer since, an ``other:`` text naming the
  role (a director's naming the seat) runs through the allow-list, which learned "Pres.", "Exec.",
  PFO / PEO and "Secretary"; an earnings report is kept only when REPORTED in USD, profiled batch
  by batch until ``limit`` qualify; the Congress worst case is 146 requests, counted; a theme
  record carries the app card's ticker count (``theme_size``). Every one mutation-checked.
* Drop 2b (2026-10-10), the four new collectors against recorded-shape fakes:
  - congress_count (the 2026-10-09 probe's exact Senate / House row keys): purchases of stock only,
    DISTINCT members across both chambers (one member with three filings is one; no chamber
    split; feed ids per chamber), refused when the identity fields cannot settle the count either
    way, when another class or an unmapped row may be the same company, or when a purchase may or
    may not be the stock; the walk's fail-closed pager (a failed / partial / empty chamber, a
    rising or unreadable date, the 2000 → 7500 retry, an uncovered month, two reads that
    disagree); no identity in a record, a fact sheet, a log line or the memo;
  - company_stakes: named investees with a disclosed dollar figure, newest ``as_of`` first,
    catalogue stakes on (and off), every gate and its boundary, investee CompanyRefs only for a
    checked US common-stock listing;
  - earnings: one ET day per call, a day at the 4,000-row cap is unavailable, one failed day
    fails the week, the EPS / revenue gates and their boundaries, one report per company;
  - theme_explainer: names and ticker lists only, the full member list, a segment fact only where
    certain, the lookup and evaluation bounds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import fields as dataclasses_fields
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPPartialPageException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services._insider_buys_common import insider_role
from app.services.marketing import company_news_adapter as A
from app.services.marketing import company_news_rules as R
from app.services.marketing import selection

RUN = date(2026, 11, 16)          # a Monday; the 2026-Q3 13F due date; window 11-09 .. 11-15
WEEK = "2026-11-09"
BERKSHIRE, PERSHING, SCION, BRIDGEWATER, SOROS = (
    "0001067983", "0001336528", "0001649339", "0001350694", "0001029160")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    A.clear_memo()
    A._registry_order.cache_clear()
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    yield
    A.clear_memo()


async def _settle_leads() -> None:
    """Let leader tasks that outlived their caller finish inside this test's loop."""
    leads = list(A._LEADS)
    if leads:
        await asyncio.gather(*leads, return_exceptions=True)


# ── fakes ─────────────────────────────────────────────────────────────────────


def issuer_cik(sym: Any) -> str:
    """Each fake issuer's CIK: a Form 4 line's ``companyCik`` and its profile's ``cik`` agree
    (the adapter checks them against each other). Deterministic, never all zeros."""
    return str(int.from_bytes(str(sym).encode(), "big") % 9_999_991 + 7).zfill(10)


def irow(sym="GME", name="COHEN RYAN", cik="0001767470", title="officer: Chief Executive Officer",
         shares=50_000, price=25.0, filed="2026-11-12", traded="2026-11-10", own="D", form="4",
         sec="Common Stock", tx="P-Purchase", owned=5_000_000, **kw) -> Dict[str, Any]:
    row = {
        "symbol": sym, "filingDate": f"{filed} 16:05:11", "transactionDate": traded,
        "reportingCik": cik, "companyCik": issuer_cik(sym), "transactionType": tx,
        "securitiesOwned": owned, "reportingName": name, "typeOfOwner": title,
        "acquisitionOrDisposition": "A", "directOrIndirect": own, "formType": form,
        "securitiesTransacted": shares, "price": price, "securityName": sec,
        "url": "https://www.sec.gov/Archives/edgar/data/1326380/000110465926000001/x.htm",
    }
    row.update(kw)
    return row


def prof(sym, name, *, price=25.0, cap=10e9, ceo=None, exchange="NYSE", ipo="1990-01-02",
         sector="Technology", **kw) -> Dict[str, Any]:
    p = {
        "symbol": sym, "companyName": name, "exchange": exchange, "isEtf": False, "isFund": False,
        "isActivelyTrading": True, "isAdr": False, "currency": "USD", "marketCap": cap,
        "price": price, "ceo": ceo, "image": A.LOGO_URL_TEMPLATE.format(symbol=sym),
        "defaultImage": False, "ipoDate": ipo, "sector": sector, "description": "A company.",
        "cik": issuer_cik(sym),
    }
    p.update(kw)
    return p


def _fake_sym(raw: Any) -> str:
    return raw.strip().upper().replace(".", "-") if isinstance(raw, str) else ""


class FakeFMP:
    def __init__(self, *, insider: Any = None, insider_seq: Optional[List[Any]] = None,
                 profiles: Any = None, dates: Optional[Dict[str, Any]] = None,
                 extracts: Optional[Dict[Any, Any]] = None, income: Optional[Dict[str, Any]] = None,
                 insider_delay: float = 0.0, isin_error: bool = False, on_insider=None,
                 symbol_feed: Optional[Dict[str, Any]] = None,
                 issuer_feed: Optional[Dict[str, Any]] = None) -> None:
        self.insider = [] if insider is None else insider
        # The per-ISSUER feed of the Form 4/A check (review round 9: ``companyCik``), as FMP
        # answers it: every row whose issuer CIK is the one asked for. ``issuer_feed`` maps a
        # normalised CIK to its whole answer (a list or an exception). Otherwise the answer is
        # assembled the way FMP's index holds the rows: each ``symbol_feed`` answer of a symbol
        # whose PROFILE (else `issuer_cik`) is that issuer — an exception or a non-list there is
        # that read's answer — plus the last market-wide answer's rows of every other symbol,
        # then filtered to ``companyCik`` == the issuer (a row of another issuer, or one with no
        # CIK, is never in an issuer read).
        self.symbol_feed = symbol_feed
        self.issuer_feed = issuer_feed
        self.last_insider: Optional[List[Any]] = None
        self.issuer_calls: List[Dict[str, Any]] = []
        self.insider_seq = insider_seq
        self.profiles = profiles if profiles is not None else {}
        self.dates = dates or {}
        self.extracts = extracts or {}
        self.income = income or {}
        self.insider_delay = insider_delay
        self.isin_error = isin_error
        self.on_insider = on_insider
        self.calls: Counter = Counter()
        self.profile_requests: List[List[str]] = []
        self.dates_ciks: List[str] = []
        self.extract_keys: List[Any] = []
        self.insider_args: Dict[str, Any] = {}

    @staticmethod
    def _answer(value: Any) -> Any:
        if isinstance(value, BaseException):
            raise value
        return json.loads(json.dumps(value)) if isinstance(value, (list, dict)) else value

    def issuer_of(self, sym: str) -> Optional[str]:
        """A symbol's issuer CIK (normalised): its profile's ``cik``, else `issuer_cik`."""
        p = self.profiles.get(sym) if isinstance(self.profiles, dict) else None
        return A.normalize_cik(p.get("cik") if isinstance(p, dict) else issuer_cik(sym))

    def _issuer_answer(self, cik: Optional[str]) -> Any:
        if self.issuer_feed is not None and cik in self.issuer_feed:
            return self.issuer_feed[cik]
        feeds = self.symbol_feed or {}
        pool: List[Any] = []
        for sym, answer in feeds.items():
            if self.issuer_of(sym) != cik:
                continue
            if isinstance(answer, BaseException) or not isinstance(answer, list):
                return answer
            pool.extend(answer)
        base = self.last_insider if self.last_insider is not None else self.insider
        pool.extend(r for r in (base if isinstance(base, list) else [])
                    if isinstance(r, dict) and _fake_sym(r.get("symbol")) not in feeds)
        return [r for r in pool if isinstance(r, dict) and A.normalize_cik(r.get("companyCik")) == cik]

    async def get_insider_trades_since(self, since, *, transaction_type=None, symbol=None,
                                       page_size=1000, max_pages=10, company_cik=None):
        if symbol is not None:
            raise AssertionError("the Form 4/A check reads per ISSUER (companyCik), never per symbol")
        if company_cik is not None:
            self.calls["insider_issuer"] += 1
            self.issuer_calls.append({"since": since, "transaction_type": transaction_type,
                                      "page_size": page_size, "max_pages": max_pages, "company_cik": company_cik})
            return self._answer(self._issuer_answer(A.normalize_cik(company_cik)))
        self.calls["insider"] += 1
        self.insider_args = {"since": since, "transaction_type": transaction_type,
                             "page_size": page_size, "max_pages": max_pages, "symbol": symbol}
        if self.on_insider:
            self.on_insider()
        if self.insider_delay:
            await asyncio.sleep(self.insider_delay)
        answer = self.insider_seq.pop(0) if self.insider_seq is not None else self.insider
        if isinstance(answer, list):
            self.last_insider = answer
        return self._answer(answer)

    async def get_company_profiles_batch(self, symbols):
        self.calls["profiles"] += 1
        self.profile_requests.append(list(symbols))
        if isinstance(self.profiles, BaseException):
            raise self.profiles
        return [json.loads(json.dumps(self.profiles[s])) for s in symbols if s in self.profiles]

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        self.calls["dates"] += 1
        self.dates_ciks.append(cik)
        return self._answer(self.dates.get(cik, []))

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        self.calls["extract"] += 1
        self.extract_keys.append((cik, year, quarter))
        return self._answer(self.extracts.get((cik, year, quarter), []))

    async def search_isin(self, isin):
        self.calls["isin"] += 1
        if self.isin_error:
            raise FMPUnavailableException("isin search down")
        return []

    async def search_cusip(self, cusip):
        self.calls["cusip"] += 1
        return []

    async def get_income_statement(self, sym, period="annual", limit=10):
        self.calls["income"] += 1
        return self._answer(self.income.get(sym, []))


class FakeQuery:
    def __init__(self, sb: "FakeSB", table: str) -> None:
        self.sb, self.table, self.filters, self.in_filters = sb, table, [], []

    def select(self, cols):
        self.sb.selects.append((self.table, cols))
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, key, values):
        self.in_filters.append((key, list(values)))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        self.sb.calls[self.table] += 1
        if self.table in self.sb.errors:
            raise self.sb.errors[self.table]
        rows = [dict(r) for r in self.sb.tables.get(self.table, [])
                if all(r.get(k) == v for k, v in self.filters)
                and all(r.get(k) in vs for k, vs in self.in_filters)]
        return SimpleNamespace(data=rows)


class FakeSB:
    def __init__(self, tables=None, errors=None) -> None:
        self.tables = tables or {}
        self.errors = errors or {}
        self.calls: Counter = Counter()
        self.selects: List[Any] = []

    def table(self, name):
        return FakeQuery(self, name)


class FakeClub:
    def __init__(self, group=None, details=None, error: Optional[BaseException] = None) -> None:
        self.group = group if group is not None else SimpleNamespace(companies=[], also_in_club=[])
        self.details = details or {}
        self.error = error
        self.calls: Counter = Counter()

    async def get_group(self, *, force=False):
        self.calls["group"] += 1
        if self.error:
            raise self.error
        return self.group

    async def get_detail(self, slug):
        self.calls["detail"] += 1
        return self.details.get(slug)


def issuer_reads(fmp: FakeFMP) -> List[str]:
    """The per-issuer reads made, each named by the profile symbols of its CIK ("BF-A/BF-B"), or
    by the normalised CIK when no profile has it — sorted."""
    out = []
    for c in fmp.issuer_calls:
        cik = A.normalize_cik(c["company_cik"])
        syms = sorted(s for s in (fmp.profiles if isinstance(fmp.profiles, dict) else {})
                      if fmp.issuer_of(s) == cik)
        out.append("/".join(syms) or str(cik))
    return sorted(out)


async def run(series, fmp=None, *, exclude=(), limit=5, left=60.0, run_date=RUN, **deps):
    return await A.candidates(series, run_date=run_date, exclude=frozenset(exclude), limit=limit,
                              deadline=time.monotonic() + left, deps=A.NewsDeps(fmp=fmp, **deps))


def week_rows() -> List[Dict[str, Any]]:
    return [
        # GME: the CEO twice in one week ($1.25M + $1.275M) — one row, renders, corroborates.
        irow(),
        irow(shares=50_000, price=25.5, traded="2026-11-11", filed="2026-11-13"),
        # FOX: a CEO whose reporting name the renderer refuses (suffix) — shown role-only.
        irow(sym="FOX", name="SMITH JOHN JR", cik="0000000111", shares=40_000, price=30.0,
             own="I", filed="2026-11-10", traded="2026-11-09"),
        # Noise the extractor or the grammar drops.
        irow(sym="ABCDW", name="DOE JANE", cik="0000000222", shares=100_000, price=10.0),
        irow(sym="KO", name="LEE ANNA", cik="0000000333", filed="2026-11-16"),   # filed on run day
        irow(sym="KO", name="LEE ANNA", cik="0000000333", tx="S-Sale"),
    ]


def week_profiles() -> Dict[str, Any]:
    return {
        "GME": prof("GME", "GameStop Corp.", price=25.2, cap=10e9, ceo="Mr. Ryan Cohen"),
        "FOX": prof("FOX", "Fox Corporation", exchange="NASDAQ", price=30.0, cap=20e9,
                    ceo="Mr. John Smith Jr."),
    }


# ── ceo_buys / insider_buys ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ceo_buys_builds_one_week_record_in_rank_order():
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles())
    got = await run("ceo_buys", fmp)
    assert got.skip_reason is None and len(got.records) == 1
    rec = got.records[0]
    assert isinstance(rec, R.InsiderBuysWeek) and rec.series == "ceo_buys"
    assert (rec.window_start, rec.window_end) == (date(2026, 11, 9), date(2026, 11, 15))
    assert R.ledger_key(rec) == f"news:ceo_buys:{WEEK}"
    gme, fox = rec.rows
    assert (gme.company.symbol, gme.company.name, gme.role) == ("GME", "GameStop", "ceo")
    assert gme.amount_usd == pytest.approx(50_000 * 25.0 + 50_000 * 25.5)
    assert gme.person_name == "Ryan Cohen" and gme.holding == "direct" and gme.amended is False
    assert gme.purchases == 2 and gme.filing_dates == (date(2026, 11, 12), date(2026, 11, 13))
    assert (gme.earliest_trade_date, gme.latest_trade_date) == (date(2026, 11, 10), date(2026, 11, 11))
    assert fox.person_name is None and fox.holding == "indirect"
    assert got.rejections == {"warrant_unit_right": 1}
    # The walk asked for exactly the window's purchases, one page budget of three.
    assert fmp.insider_args == {"since": WEEK, "transaction_type": "P-Purchase", "page_size": 1000,
                                "max_pages": 3, "symbol": None}
    assert fmp.profile_requests == [["GME", "FOX"]]
    # The record survives the fact-sheet round trip through JSON; the counts serialise as is.
    sheet = R.fact_sheet(rec, rejections=got.rejections, selection={"plan": "monday"})
    assert R.record_from_fact_sheet(json.loads(json.dumps(sheet))) == rec
    assert json.loads(json.dumps(got.rejections)) == {"warrant_unit_right": 1}


#: Malformed rows of SOMEONE ELSE (a GME director the CEO series never shows, and other issuers).
_JUNK = dict(name="DOE JANE", cik="0000000222", title="director")


@pytest.mark.asyncio
async def test_malformed_feed_rows_are_skipped_not_fatal():
    junk = [None, "junk", 7, {}, irow(price=float("nan"), **_JUNK), irow(shares=True, **_JUNK),
            irow(symbol=None, **_JUNK), irow(price=-5.0, **_JUNK),
            irow(sym="BRK.B", name="ABEL GREG", cik="0000000777", shares=1, price=1e12),
            irow(sym="TOOLONGX"), irow(filed="not-a-date", **_JUNK)]
    got = await run("ceo_buys", FakeFMP(insider=junk + week_rows(), profiles=week_profiles()))
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"]


@pytest.mark.parametrize("args, raw", [
    ({}, {"price": float("nan")}), ({}, {"securitiesTransacted": True}), ({}, {"symbol": None}),
    ({}, {"symbol": ""}), ({}, {"price": -5.0}), ({}, {"price": 0}), ({}, {"securityName": ""}),
    ({"filed": "not-a-date"}, {}), ({"owned": 10}, {}), ({"traded": "2026-10-01"}, {}),
    ({"traded": "2026-11-15"}, {}),
], ids=["nan_price", "bool_shares", "no_symbol", "blank_symbol", "negative_price", "zero_price", "unlabelled",
        "unreadable_filing_day", "over_owned", "past_the_lag", "traded_after_filing"])
@pytest.mark.asyncio
async def test_a_malformed_purchase_of_the_person_refuses_the_person_never_resums(args, raw):
    """Review round 9 (#5 / #21): a purchase of GME's CEO that the extractor or a gate cannot read
    leaves a row stating fewer purchases and dollars than his filings report ("They report 2
    purchases" when they report 3). The person is refused (`partial_person`); FOX stands. The
    same line under ANOTHER issuer, as a sale, of a preferred stock or filed after the window
    changes nothing."""
    bad = irow(**{"shares": 1_000, "filed": "2026-11-13", "traded": "2026-11-12", **args})
    bad.update(raw)
    got = await run("ceo_buys", FakeFMP(insider=week_rows() + [bad], profiles=week_profiles()))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections["partial_person"] == 1
    for other in ({"companyCik": issuer_cik("ZZZ"), "symbol": "ZZZ"}, {"transactionType": "S-Sale"},
                  {"securityName": "Series A Preferred Stock"}, {"filingDate": "2026-11-16 09:00:00"}):
        A.clear_memo()
        got = await run("ceo_buys", FakeFMP(insider=week_rows() + [{**bad, **other}], profiles=week_profiles()))
        assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"], other
        assert "partial_person" not in got.rejections


@pytest.mark.asyncio
async def test_an_unknown_ownership_or_a_missing_trade_date_is_never_guessed():
    rows = [irow(own=""), irow(sym="FOX", name="SMITH JOHN JR", cik="0000000111", shares=40_000, price=30.0,
                               transactionDate="")]
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=week_profiles()))
    assert got.records == () and got.skip_reason == "ceo_none_qualified"
    assert got.rejections["record_invalid"] == 2


@pytest.mark.asyncio
async def test_at_most_five_rows_largest_first():
    syms = ["AA", "BB", "CC", "DD", "EE", "FF", "GG"]
    # One CEO per company (review round 10: one person on two issuers is ONE row — `same_person`).
    rows = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=10_000 * (i + 1), price=25.0)
            for i, s in enumerate(syms)]
    profiles = {s: prof(s, f"{s.title()} Industries Inc.") for s in syms}
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=profiles))
    rows_out = got.records[0].rows
    assert [r.company.symbol for r in rows_out] == ["GG", "FF", "EE", "DD", "CC"]
    assert all(r.person_name is None for r in rows_out)       # no profile CEO corroborates


@pytest.mark.asyncio
async def test_a_ceo_name_needs_the_profile_ceo_to_corroborate_it():
    profiles = week_profiles()
    profiles["GME"]["ceo"] = "Ms. Jane Doe"
    got = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=profiles))
    assert got.records[0].rows[0].person_name is None


@pytest.mark.parametrize("answer, reason", [
    (FMPUnavailableException("page 0 down"), "insider_feed_unavailable"),
    (FMPRateLimitException("429"), "insider_feed_unavailable"),
    (FMPNotEntitledException("not on the order form"), "insider_feed_unavailable"),
    (FMPPartialPageException("page 1 lost", endpoint="insider-trading/search", pages_total=2,
                             pages_failed=1, partial=[irow()]), "insider_feed_unavailable"),
    ({"error": "not a list"}, "insider_feed_unavailable"),
    ([], "insider_feed_empty"),
])
@pytest.mark.asyncio
async def test_insider_feed_failures_raise_the_right_reason(answer, reason):
    fmp = FakeFMP(insider=answer, profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp)
    assert err.value.reason == reason and err.value.series == "ceo_buys"
    assert fmp.calls["profiles"] == 0          # a partial page's rows are never used


@pytest.mark.asyncio
async def test_the_log_lines_carry_identifiers_and_never_a_url_or_key(caplog):
    caplog.set_level(logging.INFO, logger=A.__name__)
    await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=week_profiles()))
    (info,) = [r for r in caplog.records if r.levelno == logging.INFO and "marketing news candidates" in r.getMessage()]
    msg = info.getMessage()
    assert "series=ceo_buys" in msg and "run_date=2026-11-16" in msg and "kept=1" in msg
    assert f"news:ceo_buys:{WEEK}" in msg and "warrant_unit_right" in msg
    caplog.clear()
    A.clear_memo()                       # else the memoized week answers the next call
    leaky = FMPUnavailableException("GET https://financialmodelingprep.com/stable/insider-trading/search"
                                    "?apikey=SECRETKEY123 failed")
    with pytest.raises(A.MarketingNewsUnavailable):
        await run("ceo_buys", FakeFMP(insider=leaky))
    (warn,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "stage=insider_walk" in warn.getMessage() and "FMPUnavailableException" in warn.getMessage()
    assert "https://" not in caplog.text and "SECRETKEY123" not in caplog.text


@pytest.mark.asyncio
async def test_a_posted_week_short_circuits_before_any_call():
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles())
    got = await run("ceo_buys", fmp, exclude={f"news:ceo_buys:{WEEK}"})
    assert got.records == () and got.skip_reason == "ceo_none_qualified"
    assert dict(got.rejections) == {"already_posted": 1}
    assert fmp.calls == Counter()


@pytest.mark.asyncio
async def test_all_profiles_missing_is_unavailable_not_a_skip():
    fmp = FakeFMP(insider=week_rows(), profiles={})
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp)
    assert err.value.reason == "profiles_unavailable"


@pytest.mark.asyncio
async def test_a_failing_profile_batch_is_unavailable():
    fmp = FakeFMP(insider=week_rows(), profiles=FMPUnavailableException("profile down"))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp)
    assert err.value.reason == "profiles_unavailable"


@pytest.mark.asyncio
async def test_price_plausibility_drops_lines_and_a_missing_reference_rejects():
    profiles = week_profiles()
    profiles["GME"]["price"] = 400.0           # every GME line is > ×10 away
    profiles["FOX"]["price"] = None            # no reference → rejected (pinned divergence)
    got = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=profiles))
    assert got.records == () and got.skip_reason == "ceo_none_qualified"
    assert got.rejections["price_implausible"] == 2
    assert got.rejections["price_reference_missing"] == 1
    assert got.rejections["below_dollar_floor"] == 2      # nothing left to sum for either symbol


@pytest.mark.asyncio
async def test_one_implausible_line_refuses_the_person_never_resums():
    """Review round 9 (#5 / #21, was `…_dropped_and_the_row_resummed`): the CEO's third line has a
    unit-error price. Re-summing the other two published "They report 2 purchases, 100,000
    shares" while the filings report 3 and 101,000 — the person is refused instead; FOX stands."""
    rows = week_rows() + [irow(shares=1_000, price=900.0, traded="2026-11-12", filed="2026-11-13")]
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=week_profiles()))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "price_implausible": 1, "partial_person": 1}
    # insider_buys: the refused director's company row goes to the next, WHOLE director.
    A.clear_memo()
    big = dict(name="ROE RICHARD", cik="0000000444", title="director")
    rows = [irow(**big, shares=40_000, price=25.0), irow(**big, shares=1_000, price=900.0, filed="2026-11-13"),
            irow(name="DOE JANE", cik="0000000555", title="director", shares=8_000, price=25.0)]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=week_profiles()))
    (gme,) = got.records[0].rows
    assert (gme.person_name, gme.amount_usd, gme.purchases) == ("Jane Doe", 200_000.0, 1)
    assert got.rejections == {"price_implausible": 1}


@pytest.mark.asyncio
async def test_a_buy_above_ten_percent_of_market_cap_is_refused():
    profiles = week_profiles()
    profiles["GME"]["marketCap"] = 260e6           # above the $250M floor; 10% of it is $26M
    rows = week_rows() + [irow(shares=1_000_000, price=25.0, traded="2026-11-12", filed="2026-11-13")]
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=profiles))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections["over_cap_share"] == 1


@pytest.mark.asyncio
async def test_two_ceos_on_one_symbol_are_ambiguous_and_never_profiled():
    rows = week_rows() + [irow(name="BOND JAMES", cik="0000000999", shares=10_000, price=25.0)]
    fmp = FakeFMP(insider=rows, profiles=week_profiles())
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections["ambiguous_ceo"] == 1
    assert fmp.profile_requests == [["FOX"]]


@pytest.mark.asyncio
async def test_a_member_of_congress_is_dropped_and_never_logged(caplog):
    assert R.is_congress_name("PELOSI NANCY")      # anti-vacuity: the roster is loaded
    rows = week_rows() + [irow(sym="HD", name="PELOSI NANCY", cik="0000000444", shares=20_000,
                               price=300.0)]
    profiles = {**week_profiles(), "HD": prof("HD", "The Home Depot, Inc.", price=300.0, cap=300e9)}
    caplog.set_level(logging.DEBUG)
    fmp = FakeFMP(insider=rows, profiles=profiles)
    got = await run("ceo_buys", fmp)
    assert "HD" not in [r.company.symbol for r in got.records[0].rows]
    assert got.rejections["congress_name"] == 1
    assert all("HD" not in req for req in fmp.profile_requests)
    text = caplog.text.lower()
    assert "pelosi" not in text and "nancy" not in text


@pytest.mark.parametrize("series", ["ceo_buys", "insider_buys"])
@pytest.mark.asyncio
async def test_an_unusable_congress_block_list_refuses_the_form_4_series(monkeypatch, series):
    """Role-only would still point at a member the block-list can no longer recognise."""
    monkeypatch.setattr(R, "person_names_allowed", lambda: False)
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run(series, fmp)
    assert err.value.reason == "internal_error" and "block-list" in err.value.detail
    assert fmp.calls == Counter()


@pytest.mark.asyncio
async def test_insider_buys_takes_cfos_and_directors_and_reuses_the_walk():
    rows = week_rows() + [
        irow(sym="FOX", name="TAYLOR EMMA", cik="0000000555", title="officer: Chief Financial Officer",
             shares=10_000, price=30.0),
        irow(sym="FOX", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0),
        # The CEO who is also a director is the CEO: never classed director.
        irow(sym="GME", title="director, officer: Chief Executive Officer", shares=1_000, price=25.0,
             traded="2026-11-12", filed="2026-11-14"),
    ]
    fmp = FakeFMP(insider=rows, profiles=week_profiles())
    ceo = await run("ceo_buys", fmp)
    other = await run("insider_buys", fmp)
    assert fmp.calls["insider"] == 1                      # the memoized raw rows
    assert R.ledger_key(other.records[0]) == f"news:insider_buys:{WEEK}"
    (fox,) = other.records[0].rows
    # One person per company: the largest reporter (the director, $600K), never a sum.
    assert (fox.role, fox.amount_usd, fox.person_name) == ("director", 600_000.0, "David Brown")
    assert ceo.records[0].rows[0].company.symbol == "GME"


@pytest.mark.asyncio
async def test_small_buys_are_below_the_floor():
    rows = [irow(shares=1_000, price=25.0), irow(sym="FOX", name="SMITH JOHN JR", cik="0000000111",
                                                 shares=100, price=30.0)]
    fmp = FakeFMP(insider=rows, profiles=week_profiles())
    got = await run("ceo_buys", fmp)
    assert got.records == () and got.skip_reason == "ceo_none_qualified"
    assert got.rejections == {"below_dollar_floor": 2}
    assert fmp.calls["profiles"] == 0


@pytest.mark.asyncio
async def test_a_profile_gate_reason_is_counted():
    profiles = week_profiles()
    profiles["FOX"].update(exchange="OTC")
    profiles["GME"].update(isAdr=True)
    got = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=profiles))
    assert got.records == ()
    assert got.rejections["not_major_exchange"] == 1 and got.rejections["adr"] == 1


@pytest.mark.asyncio
async def test_two_concurrent_calls_share_one_walk_and_one_profile_batch():
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles(), insider_delay=0.05)
    a, b = await asyncio.gather(run("ceo_buys", fmp), run("ceo_buys", fmp))
    assert a == b and len(a.records) == 1
    assert fmp.calls["insider"] == 1 and fmp.calls["profiles"] == 1


@pytest.mark.asyncio
async def test_an_exception_is_never_memoized():
    fmp = FakeFMP(insider_seq=[FMPUnavailableException("blip"), week_rows()], profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable):
        await run("ceo_buys", fmp)
    got = await run("ceo_buys", fmp)
    assert len(got.records) == 1 and fmp.calls["insider"] == 2


@pytest.mark.asyncio
async def test_an_empty_feed_is_never_memoized():
    fmp = FakeFMP(insider_seq=[[], week_rows()], profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp)
    assert err.value.reason == "insider_feed_empty"
    assert len((await run("ceo_buys", fmp)).records) == 1 and fmp.calls["insider"] == 2


@pytest.mark.asyncio
async def test_less_than_one_second_left_is_budget_exhausted_before_any_call():
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp, left=0.5)
    assert err.value.reason == "budget_exhausted" and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_the_budget_is_checked_before_every_step():
    clock = {"now": 0.0}

    def jump():
        clock["now"] = 59.5                     # the walk "took" 59.5 s of a 60 s budget

    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles(), on_insider=jump)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("ceo_buys", run_date=RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and "stage=profiles" in err.value.detail
    assert fmp.calls["profiles"] == 0


@pytest.mark.asyncio
async def test_a_walk_that_outlives_the_budget_still_warms_the_memo():
    fmp = FakeFMP(insider=week_rows(), profiles=week_profiles(), insider_delay=1.3)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", fmp, left=1.1)
    assert err.value.reason == "budget_exhausted"
    await _settle_leads()
    got = await run("ceo_buys", fmp)
    assert len(got.records) == 1 and fmp.calls["insider"] == 1


@pytest.mark.parametrize("series", ["nope", "lesson", "CEO_BUYS", ""])
@pytest.mark.asyncio
async def test_an_unknown_or_unshipped_series_is_a_value_error(series):
    with pytest.raises(ValueError):
        await run(series, FakeFMP())


@pytest.mark.parametrize("kw", [
    {"limit": 0}, {"limit": True}, {"run_date": datetime(2026, 11, 16, 12)}, {"run_date": "2026-11-16"},
])
@pytest.mark.asyncio
async def test_bad_arguments_are_value_errors(kw):
    with pytest.raises(ValueError):
        await run("ceo_buys", FakeFMP(), **kw)


@pytest.mark.asyncio
async def test_an_unexpected_error_is_internal_error_with_a_stack(monkeypatch, caplog):
    def boom(*_a, **_kw):
        raise TypeError("a bug")

    monkeypatch.setattr(A, "extract_insider_buys", boom)
    caplog.set_level(logging.ERROR, logger=A.__name__)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=week_profiles()))
    assert err.value.reason == "internal_error" and "TypeError" in err.value.detail
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.ERROR)


def test_the_collectors_are_exactly_the_shipped_series():
    assert set(A.COLLECTORS) == set(selection.SHIPPED_SERIES)
    assert set(A.COLLECTORS) <= set(R.NEWS_SERIES)


def test_candidates_and_the_exception_enforce_their_contract():
    with pytest.raises(ValueError):
        A.Candidates("ceo_buys", (), None, {})                         # empty needs a skip reason
    with pytest.raises(ValueError):
        A.Candidates("ceo_buys", (), "ceo_none_qualified", {"made_up": 1})
    with pytest.raises(ValueError):
        A.Candidates("ceo_buys", (), "already_posted", {})             # not a skip reason
    with pytest.raises(ValueError):
        A.MarketingNewsUnavailable("ceo_buys", "made_up")
    e = A.MarketingNewsUnavailable("x")                                # the classifier walk's shape
    assert e.reason == "internal_error"
    e = A.MarketingNewsUnavailable("ceo_buys", "internal_error",
                                   "GET https://financialmodelingprep.com/stable/x?apikey=SECRET failed")
    assert "http" not in e.detail and "SECRET" not in str(e)


# ── thirteen_f ────────────────────────────────────────────────────────────────

ACC = {3: "0000950123-26-000301", 2: "0000950123-26-000201", 1: "0000950123-26-000101"}
END = {3: "2026-09-30", 2: "2026-06-30", 1: "2026-03-31"}
FILED = {3: "2026-11-14", 2: "2026-08-14", 1: "2026-05-15"}


def xrow(cik, q, sym, cusip, shares, value, **kw):
    acc = ACC[q]
    row = {"date": END[q], "filingDate": FILED[q], "acceptedDate": FILED[q] + " 16:00:00", "cik": cik,
           "securityCusip": cusip, "symbol": sym, "nameOfIssuer": f"{sym} CORP", "shares": shares,
           "titleOfClass": "COM", "sharesType": "SH", "putCallShare": "", "value": value,
           "link": f"https://www.sec.gov/Archives/edgar/data/1/{acc.replace('-', '')}/{acc}-index.htm"}
    row.update(kw)
    return row


def book(cik):
    q3 = [xrow(cik, 3, "AAPL", "037833100", 1_200_000, 240e6), xrow(cik, 3, "CRWV", "21873S108", 500_000, 50e6),
          xrow(cik, 3, "KO", "191216100", 1_000_000, 70e6)]
    q2 = [xrow(cik, 2, "AAPL", "037833100", 1_000_000, 200e6), xrow(cik, 2, "KO", "191216100", 1_000_000, 70e6),
          xrow(cik, 2, "OXY", "674599105", 2_000_000, 100e6)]
    return {(cik, 2026, 3): q3, (cik, 2026, 2): q2}


DATES_Q3_Q2 = [{"year": 2026, "quarter": 3, "date": "2026-09-30"}, {"year": 2026, "quarter": 2, "date": "2026-06-30"}]


def thirteen_f_profiles() -> Dict[str, Any]:
    return {
        "AAPL": prof("AAPL", "Apple Inc.", exchange="NASDAQ"),
        "CRWV": prof("CRWV", "CoreWeave, Inc. Class A Common Stock", exchange="NASDAQ", ipo="2026-07-15"),
        "KO": prof("KO", "The Coca-Cola Company"),
        "OXY": prof("OXY", "Occidental Petroleum Corporation"),
        # A filer's own listing carries the filer's CIK (review round 9: the chip is kept only then).
        "BRK-A": prof("BRK-A", "Berkshire Hathaway Inc.", cik=BERKSHIRE),
        "NVDA": prof("NVDA", "NVIDIA Corporation", exchange="NASDAQ", cik="0001045810"),
        "RXRX": prof("RXRX", "Recursion Pharmaceuticals, Inc.", exchange="NASDAQ"),
        "SPY": prof("SPY", "SPDR S&P 500 ETF Trust", isEtf=True, exchange="AMEX"),
        "ARKK": prof("ARKK", "ARK Innovation ETF", isEtf=True, exchange="AMEX"),
    }


def whale(cik, firm, ticker=None, **kw):
    row = {"cik": cik, "firm_name": firm, "category": "investors", "data_source": "13f",
           "associated_ticker": ticker, "lifecycle_status": ""}
    row.update(kw)
    return row


def registry_world(whales, *, extracts=None, dates=None, **fmp_kw):
    extracts = extracts if extracts is not None else {k: v for w in whales for k, v in book(w["cik"]).items()}
    dates = dates if dates is not None else {w["cik"]: DATES_Q3_Q2 for w in whales}
    fmp = FakeFMP(profiles=thirteen_f_profiles(), extracts=extracts, dates=dates, **fmp_kw)
    sb = FakeSB({"whales": whales})
    return fmp, sb


@pytest.mark.asyncio
async def test_thirteen_f_is_skipped_off_season_with_no_call():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    got = await run("thirteen_f", fmp, run_date=date(2026, 10, 12), sb=sb)
    assert got.records == () and got.skip_reason == "thirteen_f_off_season"
    assert fmp.calls == Counter() and sb.calls == Counter()


@pytest.mark.asyncio
async def test_a_registry_filer_is_built_live_and_its_moves_ranked():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    got = await run("thirteen_f", fmp, sb=sb)
    (rec,) = got.records
    assert (rec.filer_name, rec.filer_cik, rec.filer_symbol, rec.period) == (
        "Berkshire Hathaway", BERKSHIRE, "BRK-A", "2026-Q3")
    assert (rec.period_end, rec.filed_on, rec.amended_on) == (date(2026, 9, 30), date(2026, 11, 14), None)
    assert rec.total_value_usd == 360e6 and rec.position_count == 3
    assert [(m.company.symbol, m.move) for m in rec.moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported"), ("AAPL", "increased")]
    crwv, oxy, aapl = rec.moves
    assert crwv.company.name == "CoreWeave" and crwv.listed_on == date(2026, 7, 15)
    assert (oxy.shares, oxy.prev_shares, oxy.value_usd) == (None, 2_000_000.0, None)
    assert (aapl.shares, aapl.prev_shares) == (1_200_000.0, 1_000_000.0) and aapl.listed_on is None
    assert dict(rec.counts) == {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 1}
    assert R.ledger_key(rec) == f"news:thirteen_f:{BERKSHIRE}:2026-Q3"
    assert sorted(fmp.extract_keys) == [(BERKSHIRE, 2026, 2), (BERKSHIRE, 2026, 3)]


@pytest.mark.asyncio
async def test_an_etf_filer_symbol_is_dropped_not_shown():
    fmp, sb = registry_world([whale("0001697748", "ARK Invest", "ARKK")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records[0].filer_symbol is None


@pytest.mark.parametrize("dates, reason", [
    ([{"year": 2026, "quarter": 3, "date": "2026-09-30"}, {"year": 2026, "quarter": 1, "date": "2026-03-31"}],
     "non_comparable"),                       # a gap: never diffed across a missing quarter
    ([{"year": 2026, "quarter": 3, "date": "2026-09-30"}], "non_comparable"),   # a first filing
    ([{"year": 2026, "quarter": 2, "date": "2026-06-30"}], "filer_not_filed"),
    ([], "filer_not_filed"),
])
@pytest.mark.asyncio
async def test_the_pair_is_chosen_from_the_filing_dates_before_any_build(dates, reason):
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], dates={BERKSHIRE: dates})
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.skip_reason == "thirteen_f_none_qualified"
    assert got.rejections == {reason: 1}
    assert fmp.calls["extract"] == 0


@pytest.mark.asyncio
async def test_a_posted_filer_is_skipped_before_its_dates_are_read():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")])
    got = await run("thirteen_f", fmp, sb=sb, exclude={f"news:thirteen_f:{BERKSHIRE}:2026-Q3"})
    assert got.rejections == {"already_posted": 1} and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_an_unverified_filer_is_skipped_before_any_call_and_a_person_named_entity_is_allowed():
    """Review round 9: the subject comes from `R.THIRTEEN_F_FILERS` (the EDGAR filer), never the
    curated firm_name — a blank curated name no longer matters, a registry CIK the table does
    not list is refused before any call."""
    greenlight = "0001489933"                 # in the registry, not (yet) verified in the table
    assert greenlight in A._registry_order() and greenlight not in R.THIRTEEN_F_FILERS
    fmp, sb = registry_world([whale(greenlight, "Greenlight Capital"), whale(SOROS, "")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.rejections.get("filer_entity_unknown") == 1
    assert fmp.dates_ciks == [SOROS]          # owner decision 5: a filer named after a person is fine
    assert got.records[0].filer_name == "Soros Fund Management"


@pytest.mark.asyncio
async def test_dormant_and_unregistered_filers_are_never_read():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", lifecycle_status="dormant"),
                              whale("0009999999", "Unlisted Capital"),
                              whale(PERSHING, "Pershing Square Capital", category="institutions")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_a_refused_book_does_not_consume_the_build_cap():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital"),
              whale(SCION, "Scion Asset Management"), whale(BRIDGEWATER, "Bridgewater Associates")]
    extracts = {k: v for w in whales[1:] for k, v in book(w["cik"]).items()}
    extracts[(BERKSHIRE, 2026, 3)] = [xrow(BERKSHIRE, 3, "AAPL", "037833100", 1, 1.0)] * 201
    extracts[(BERKSHIRE, 2026, 2)] = book(BERKSHIRE)[(BERKSHIRE, 2026, 2)]
    fmp, sb = registry_world(whales, extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING, SCION]
    assert got.rejections["book_too_large"] == 1
    assert BRIDGEWATER not in fmp.dates_ciks          # the cap (2 live builds) was reached


@pytest.mark.asyncio
async def test_a_memoized_build_is_not_a_live_build():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital"),
              whale(SCION, "Scion Asset Management")]
    fmp, sb = registry_world(whales)
    first = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in first.records] == [BERKSHIRE, PERSHING]
    extracts_before = fmp.calls["extract"]
    again = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in again.records] == [BERKSHIRE, PERSHING, SCION]
    assert fmp.calls["extract"] == extracts_before + 2  # only SCION was built live


@pytest.mark.asyncio
async def test_two_unavailable_filers_raise_and_one_is_counted():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital")]
    down = {(c, 2026, q): FMPUnavailableException("extract down") for c in (BERKSHIRE, PERSHING) for q in (2, 3)}
    fmp, sb = registry_world(whales, extracts=down)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("thirteen_f", fmp, sb=sb)
    assert err.value.reason == "thirteen_f_unavailable"

    A.clear_memo()
    extracts = {**book(PERSHING), (BERKSHIRE, 2026, 3): FMPUnavailableException("down"),
                (BERKSHIRE, 2026, 2): FMPUnavailableException("down")}
    fmp, sb = registry_world(whales, extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING]
    assert got.rejections["filer_unavailable"] == 1


@pytest.mark.asyncio
async def test_a_failed_dates_read_counts_as_unavailable():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital")]
    fmp, sb = registry_world(whales, dates={BERKSHIRE: FMPRateLimitException("429"),
                                            PERSHING: FMPUnavailableException("down")})
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("thirteen_f", fmp, sb=sb)
    assert err.value.reason == "thirteen_f_unavailable"


@pytest.mark.asyncio
async def test_a_degraded_build_is_rejected():
    extracts = book(BERKSHIRE)
    # A symbol-less, digit-first CUSIP whose ISIN lookup FAILS → "symbol_lookup_failed".
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "", "88160R101", 10_000, 3e6)]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts, isin_error=True)
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections == {"degraded_build": 1}


@pytest.mark.asyncio
async def test_a_share_count_the_shared_positions_disagree_with_is_dropped():
    extracts = book(BERKSHIRE)
    # A zero-value row on AAPL's CUSIP: the builder excludes it, the shared share positions
    # (`thirteen_f_share_positions`) count its shares — the two disagree → AAPL is not shown.
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "AAPL", "037833100", 50_000, 0)]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "OXY"]
    assert got.rejections["degraded_build"] == 1


@pytest.mark.asyncio
async def test_an_unknown_listing_date_rejects_a_newly_reported_move():
    profiles = thirteen_f_profiles()
    profiles["CRWV"]["ipoDate"] = None
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")])
    fmp.profiles = profiles
    got = await run("thirteen_f", fmp, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == ["OXY", "AAPL"]
    assert got.rejections["move_unknown_listing"] == 1


def club_world(*, changes=None, card_over=None, company_over=None, rows=None):
    card = dict(slug="nvidia", name="NVIDIA", card_kind="thirteen_f", period="2026-Q3",
                comparison="quarter", detail_symbol="NVDA", filed_on="2026-11-14")
    card.update(card_over or {})
    company = dict(card, total_value=5e9, position_count=8, amended_on=None,
                   change_counts=SimpleNamespace(newly_reported=3, increased=1, decreased=1,
                                                 no_longer_reported=1, unchanged=3, corporate_action=1))
    company.update(company_over or {})
    ch = lambda **kw: SimpleNamespace(**{"newly_listed": False, "shares": None, "prev_shares": None,  # noqa: E731
                                         "value": None, **kw})
    changes = changes if changes is not None else [
        ch(symbol="CRWV", name="CoreWeave", change="newly_reported", shares=1_000_000.0, value=150e6,
           newly_listed=True),
        ch(symbol="SPY", name="SPDR S&P 500", change="newly_reported", shares=10.0, value=50e6),
        ch(symbol=None, name="Private Co", change="newly_reported", shares=10.0, value=5e3),
        ch(symbol="RXRX", name="Recursion", change="no_longer_reported", prev_shares=7_000_000.0),
        ch(symbol="AAPL", name="Apple", change="increased", shares=1_200.0, prev_shares=1_000.0, value=3e5),
        ch(symbol="SOUN", name="SoundHound", change="decreased", shares=98.0, prev_shares=100.0, value=1e3),
        ch(symbol="XYZQ", name="Split Co", change="corporate_action", shares=10.0, prev_shares=1.0, value=1e3),
    ]
    group = SimpleNamespace(companies=[SimpleNamespace(**card)], also_in_club=[])
    detail = SimpleNamespace(company=SimpleNamespace(**company), changes=changes)
    club = FakeClub(group=group, details={"nvidia": detail})
    sb = FakeSB({"trillion_club_companies": rows if rows is not None else [
        {"slug": "nvidia", "ciks": ["0001045810"], "card_kind": "thirteen_f", "use_13f": True,
         "detail_symbol": "NVDA", "published": True}], "whales": [],
        # The stored previous-quarter build an exit is valued on (review round 7: materiality).
        "trillion_club_filings": [{"cik": "0001045810", "period": "2026-Q2",
                                   "holdings": [{"symbol": "RXRX", "value": 35e6}, {"symbol": "GOOG", "value": 1.8e8}]}]})
    return club, sb


@pytest.mark.asyncio
async def test_a_club_filer_comes_from_its_stored_build_with_no_extract_call(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    fmp = FakeFMP(profiles=thirteen_f_profiles())
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    (rec,) = got.records
    assert (rec.filer_name, rec.filer_cik, rec.filer_symbol) == ("NVIDIA", "0001045810", "NVDA")
    assert [(m.company.symbol, m.move) for m in rec.moves] == [
        ("CRWV", "newly_reported"), ("RXRX", "no_longer_reported"), ("AAPL", "increased")]
    assert rec.moves[0].listed_on == date(2026, 7, 15)
    assert got.rejections == {"etf_or_fund": 1, "move_not_routable": 1, "move_too_small": 1}
    assert fmp.calls["extract"] == 0 and fmp.calls["dates"] == 0


@pytest.mark.parametrize("card_over, company_over, reason", [
    ({"period": "2026-Q2"}, {}, "filer_not_filed"),
    ({"comparison": "gap"}, {}, "non_comparable"),
    ({}, {"filed_on": None}, "filer_not_filed"),
    ({}, {"filed_on": "2026-11-20"}, "filer_not_filed"),       # after the run date: not trusted
    ({"name": ""}, {}, "filer_entity_unknown"),
])
@pytest.mark.asyncio
async def test_club_filer_gates(monkeypatch, card_over, company_over, reason):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world(card_over=card_over, company_over=company_over)
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    assert got.records == () and got.rejections.get(reason) == 1


@pytest.mark.asyncio
async def test_thirteen_f_move_caps_and_the_value_bound(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    syms = [f"N{c}" for c in "ABCDEFGHIJKL"]                      # 12 newly reported positions
    ch = [SimpleNamespace(symbol=s, name=s, change="newly_reported", shares=100.0, prev_shares=None,
                          value=10e6 + 1e6 * (i + 1), newly_listed=False) for i, s in enumerate(syms)]
    ch.append(SimpleNamespace(symbol="AAPL", name="Apple", change="newly_reported", shares=1.0, prev_shares=None,
                              value=6e9, newly_listed=False))          # above the filer's $5B total
    counts = SimpleNamespace(newly_reported=13, increased=0, decreased=0, no_longer_reported=0)
    club, sb = club_world(changes=ch, company_over={"change_counts": counts})
    profiles = {**thirteen_f_profiles(), **{s: prof(s, f"{s} Holdings Inc.") for s in syms}}
    fmp = FakeFMP(profiles=profiles)
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    (rec,) = got.records
    assert got.rejections["move_value_exceeds_total"] == 1
    assert dict(rec.counts)["newly_reported"] == 13
    assert len(fmp.profile_requests[0]) == A.THIRTEEN_F_MAX_PROFILED + 1    # + the filer's own ticker
    assert [m.company.symbol for m in rec.moves] == ["NL", "NK", "NJ", "NI", "NH", "NG", "NF", "NE"]


@pytest.mark.asyncio
async def test_a_filing_with_one_small_move_does_not_qualify(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    ch = [SimpleNamespace(symbol="AAPL", name="Apple", change="increased", shares=1_200.0, prev_shares=1_000.0,
                          value=3e5, newly_listed=False)]
    club, sb = club_world(changes=ch)
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    assert got.records == () and got.skip_reason == "thirteen_f_none_qualified"


@pytest.mark.asyncio
async def test_a_newly_listed_flag_the_profile_contradicts_is_refused(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    profiles = thirteen_f_profiles()
    profiles["CRWV"]["ipoDate"] = "2020-01-02"         # the stored build said "newly listed"
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    assert got.rejections["move_unknown_listing"] == 1
    assert "CRWV" not in [m.company.symbol for m in got.records[0].moves]


@pytest.mark.asyncio
async def test_a_club_filer_is_never_rebuilt_from_the_registry(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    sb.tables["whales"] = [whale("0001045810", "NVIDIA")]
    order = dict(A._registry_order())
    order["0001045810"] = 99
    monkeypatch.setattr(A, "_registry_order", lambda: order)
    fmp = FakeFMP(profiles=thirteen_f_profiles())
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert len(got.records) == 1 and fmp.calls["dates"] == 0


@pytest.mark.asyncio
async def test_an_unreachable_club_is_unavailable_and_an_off_switch_never_calls_it(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club = FakeClub(error=RuntimeError("supabase down"))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("thirteen_f", FakeFMP(), club=club, sb=FakeSB({"whales": []}))
    assert err.value.reason == "club_unavailable"

    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    club = FakeClub(error=RuntimeError("never called"))
    got = await run("thirteen_f", FakeFMP(), club=club, sb=FakeSB({"whales": []}))
    assert got.skip_reason == "thirteen_f_none_qualified" and club.calls == Counter()


# ── money_map ─────────────────────────────────────────────────────────────────


def src(name, value):
    return SimpleNamespace(name=name, value=value)


def breakdown(**over):
    bd = dict(symbol="AAPL", fiscal_year="2025",
              revenue_sources=[src("iPhone", 200e9), src("Services", 100e9), src("Mac", 30e9), src("Other", 20e9)],
              cost_of_sales=200e9, operating_expense=60e9, tax=15e9, net_income=95e9, reported_revenue=350e9,
              intersegment_eliminations=None, degraded=[])
    bd.update(over)
    return SimpleNamespace(**bd)


def profit(**over):
    point = dict(period="2025", gross_margin=42.86, operating_margin=25.71, net_margin=27.14)
    point.update(over.pop("point", {}))
    return SimpleNamespace(annual=[SimpleNamespace(**point)], degraded=over.pop("degraded", []))


class FakeService:
    def __init__(self, by_symbol, *, default=None, error=None):
        self.by_symbol, self.default, self.error = by_symbol, default, error
        self.calls: List[str] = []

    async def _get(self, sym):
        self.calls.append(sym)
        if self.error:
            raise self.error
        value = self.by_symbol.get(sym, self.default)
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_revenue_breakdown(self, sym):
        return await self._get(sym)

    async def get_profit_power(self, sym):
        return await self._get(sym)


def facts_fn(by_symbol=None, default=None):
    calls = []

    async def facts(sym, *, need_executives=True):
        calls.append((sym, need_executives))
        return (by_symbol or {}).get(sym, default or {"ticker": sym, "available": True, "sector": "Technology"})

    facts.calls = calls
    return facts


INCOME_AAPL = [{"fiscalYear": "2025", "date": "2025-09-27", "reportedCurrency": "USD", "revenue": 350e9,
                "netIncome": 95e9},
               {"fiscalYear": "2024", "date": "2024-09-28", "reportedCurrency": "USD", "revenue": 300e9,
                "netIncome": 90e9}]


def money_world(*, bd=None, pp=None, facts=None, income=None, profiles=None):
    revenue = FakeService({"AAPL": bd or breakdown()}, default=breakdown(degraded=["segmentation_unavailable"]))
    prof_svc = FakeService({"AAPL": pp or profit()}, default=profit())
    fmp = FakeFMP(profiles=profiles if profiles is not None else {"AAPL": prof("AAPL", "Apple Inc.", exchange="NASDAQ")},
                  income={"AAPL": income if income is not None else INCOME_AAPL})
    return dict(fmp=fmp, revenue=revenue, profit=prof_svc, facts=facts or facts_fn(),
                sb=FakeSB({"trending_themes": []}))


@pytest.fixture
def seed_aapl(monkeypatch):
    monkeypatch.setattr(R, "MONEY_MAP_SEED", ("AAPL",))


@pytest.mark.asyncio
async def test_money_map_happy_path(seed_aapl):
    world = money_world()
    got = await run("money_map", world.pop("fmp"), **world)
    (rec,) = got.records
    assert isinstance(rec, R.MoneyMap) and R.ledger_key(rec) == "news:money_map:AAPL:2025"
    assert (rec.company.name, rec.fiscal_year, rec.period_end) == ("Apple", "2025", date(2025, 9, 27))
    assert [(s.name, s.value_usd) for s in rec.segments] == [("iPhone", 200e9), ("Services", 100e9), ("Mac", 30e9)]
    assert (rec.other_usd, rec.eliminations_usd, rec.revenue_usd) == (20e9, None, 350e9)
    assert (rec.gross_profit_usd, rec.operating_profit_usd, rec.net_income_usd) == (150e9, 90e9, 95e9)


def _with(**over):
    return over


@pytest.mark.parametrize("over, reason", [
    (_with(bd=breakdown(degraded=["segmentation_unavailable"])), "money_map_degraded"),
    (_with(pp=profit(degraded=["quarterly_income"])), "money_map_degraded"),
    (_with(facts=facts_fn(default={"available": True, "sector": "Financial Services"})), "sector_excluded"),
    (_with(facts=facts_fn(default={"available": True}),                 # no sector anywhere
           profiles={"AAPL": prof("AAPL", "Apple Inc.", exchange="NASDAQ", sector=None)}), "sector_excluded"),
    (_with(facts=facts_fn(default={"available": True, "sector": "Technology", "is_etf": True})), "etf_or_fund"),
    (_with(facts=facts_fn(default={"available": False, "not_found": True})), "profile_missing"),
    (_with(income=[{**INCOME_AAPL[0], "reportedCurrency": "EUR"}]), "non_usd_reporter"),
    (_with(income=[{**INCOME_AAPL[0], "revenue": 360e9}]), "revenue_mismatch"),
    (_with(income=[{**INCOME_AAPL[0], "netIncome": 90e9}]), "net_income_mismatch"),
    (_with(income=[{**INCOME_AAPL[0], "date": "2024-09-28"}]), "money_map_stale"),
    (_with(income=[{**INCOME_AAPL[0], "fiscalYear": "2024"}]), "money_map_degraded"),
    (_with(pp=profit(point={"net_margin": 30.0})), "money_map_inconsistent"),
    (_with(pp=profit(point={"period": "2024"})), "money_map_inconsistent"),
    (_with(bd=breakdown(revenue_sources=[src("iPhone", 330e9), src("Other", 20e9)])), "segments_thin"),
    (_with(bd=breakdown(revenue_sources=[src("iPhone", 210e9), src("Services", 110e9), src("Mac", 40e9)])),
     "money_map_inconsistent"),                       # segments above revenue: never published
    (_with(profiles={"AAPL": prof("AAPL", "Apple Inc.", exchange="OTC")}), "not_major_exchange"),
])
@pytest.mark.asyncio
async def test_money_map_gates(seed_aapl, over, reason):
    world = money_world(**over)
    got = await run("money_map", world.pop("fmp"), **world)
    assert got.records == () and got.skip_reason == "money_map_none_qualified"
    assert got.rejections == {reason: 1}


@pytest.mark.asyncio
async def test_money_map_facts_outage_is_unavailable(seed_aapl):
    world = money_world(facts=facts_fn(default={"available": False, "upstream": True}))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("money_map", world.pop("fmp"), **world)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.asyncio
async def test_money_map_breakdown_outage_is_unavailable(seed_aapl):
    world = money_world()
    world["revenue"] = FakeService({}, error=FMPUnavailableException("segmentation down"))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("money_map", world.pop("fmp"), **world)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.asyncio
async def test_unverifiable_bars_are_none_never_guessed(seed_aapl):
    world = money_world(pp=profit(point={"gross_margin": 50.0}))
    rec = (await run("money_map", world.pop("fmp"), **world)).records[0]
    assert rec.gross_profit_usd is None and rec.operating_profit_usd is None and rec.net_income_usd == 95e9


@pytest.mark.asyncio
async def test_a_small_unnamed_gap_folds_into_other(seed_aapl):
    under = money_world(bd=breakdown(revenue_sources=[src("iPhone", 200e9), src("Services", 100e9), src("Mac", 43e9)]))
    rec = (await run("money_map", under.pop("fmp"), **under)).records[0]
    assert rec.other_usd == pytest.approx(7e9) and rec.eliminations_usd is None


@pytest.mark.parametrize("sources, elim", [
    # The breakdown's residual "eliminations" (Σ segments − revenue), with or without a matching
    # FMP row: a feed double-listing a sub-line would read "Sales between its own segments".
    ([src("iPhone", 230e9), src("Services", 100e9), src("Mac", 30e9)], 10e9),
    ([src("Products", 250e9), src("Wearables and Home", 40e9), src("Services", 100e9)], 40e9),
    # A tiny residual inside the sum tolerance is still a derived bar: refused too.
    ([src("iPhone", 201e9), src("Services", 100e9), src("Mac", 30e9), src("Other", 20e9)], 1e9),
])
@pytest.mark.asyncio
async def test_derived_eliminations_are_refused_never_drawn(seed_aapl, sources, elim):
    world = money_world(bd=breakdown(revenue_sources=sources, intersegment_eliminations=elim))
    got = await run("money_map", world.pop("fmp"), **world)
    assert got.records == () and got.rejections == {"money_map_inconsistent": 1}


@pytest.mark.parametrize("elim", [None, 0.0, -5.0, float("nan"), "10e9", True])
@pytest.mark.asyncio
async def test_no_eliminations_figure_never_draws_a_bar(seed_aapl, elim):
    world = money_world(bd=breakdown(intersegment_eliminations=elim))
    (rec,) = (await run("money_map", world.pop("fmp"), **world)).records
    assert rec.eliminations_usd is None and rec.other_usd == 20e9


@pytest.mark.asyncio
async def test_undrawable_duplicate_and_surplus_segments_fold_into_other(seed_aapl):
    sources = [src("iPhone", 100e9), src("Services", 80e9), src("Mac", 60e9), src("iPad", 40e9),
               src("Wearables", 30e9), src("Accessories (Retail)", 15e9), src("services", 10e9),
               src("Licensing", 5e9), src("Unallocated", 10e9)]
    world = money_world(bd=breakdown(revenue_sources=sources))
    rec = (await run("money_map", world.pop("fmp"), **world)).records[0]
    assert [s.name for s in rec.segments] == ["iPhone", "Services", "Mac", "iPad", "Wearables"]
    assert rec.other_usd == pytest.approx(40e9)        # "(…)", the duplicate, the 6th, "Unallocated"


@pytest.mark.asyncio
async def test_a_posted_fiscal_year_is_skipped_before_any_profile_call(seed_aapl):
    world = money_world()
    fmp = world.pop("fmp")
    got = await run("money_map", fmp, exclude={"news:money_map:AAPL:2025"}, **world)
    assert got.records == () and got.rejections == {"already_posted": 1}
    assert fmp.calls["profiles"] == 0 and fmp.calls["income"] == 0 and world["profit"].calls == []


@pytest.mark.asyncio
async def test_money_map_pool_order_and_attempt_cap(seed_aapl, monkeypatch):
    """Owner decision 2: the seed first, then club members (cards and also-in-club), then the
    Emerging Frontiers members minus blocked — and never-posted symbols before posted ones."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    world = money_world()
    world["club"] = FakeClub(group=SimpleNamespace(
        companies=[SimpleNamespace(slug="broadcom", detail_symbol="AVGO", card_kind="no_thirteen_f")],
        also_in_club=[SimpleNamespace(slug="tsmc", name="TSMC")]))
    world["sb"] = FakeSB({
        "trillion_club_companies": [{"slug": "tsmc", "ciks": [], "card_kind": "non_us", "use_13f": False,
                                     "detail_symbol": "TSM", "published": True}],
        "trending_themes": [{"slug": "quantum", "tickers": ["IONQ", "RGTI", "QBTS"], "blocked_tickers": ["RGTI"],
                             "sort_order": 1, "is_active": True}],
    })
    got = await run("money_map", world.pop("fmp"), exclude={"news:money_map:AAPL:2024"}, **world)
    assert world["revenue"].calls == ["AVGO", "TSM", "IONQ"]      # AAPL (posted before) goes last
    assert got.rejections == {"money_map_degraded": 3}


@pytest.mark.asyncio
async def test_a_failed_pool_source_degrades_to_the_seed(seed_aapl, monkeypatch, caplog):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    world = money_world()
    world["club"] = FakeClub(error=RuntimeError("club down"))
    world["sb"] = FakeSB(errors={"trending_themes": RuntimeError("themes down")})
    caplog.set_level(logging.WARNING, logger=A.__name__)
    got = await run("money_map", world.pop("fmp"), **world)
    assert len(got.records) == 1
    assert "club members unavailable" in caplog.text and "theme members unavailable" in caplog.text


# ── fetch_logo ────────────────────────────────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def streamed(status: int, body: bytes = b"", headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    """A response whose body arrives as a NETWORK stream (not pre-read), as a real transport's
    does — `fetch_logo` reads wire bytes (`aiter_raw`), which a pre-read mock cannot give."""
    async def chunks():
        for i in range(0, len(body), 64):
            yield body[i:i + 64]

    return httpx.Response(status, content=chunks(), headers=headers or {})


def logo_deps(handler, profile=None):
    fmp = FakeFMP(profiles={"GME": profile if profile is not None else prof("GME", "GameStop Corp.")})
    seen: List[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return A.NewsDeps(fmp=fmp, http=httpx.MockTransport(wrapped)), seen, fmp


@pytest.mark.asyncio
async def test_fetch_logo_returns_bytes_and_the_header_type_and_memoizes():
    deps, seen, _fmp = logo_deps(lambda r: streamed(200, PNG, {"content-type": "image/png; x=1"}))
    got = await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps)
    assert got == (PNG, "image/png")
    assert [str(r.url) for r in seen] == ["https://images.financialmodelingprep.com/symbol/GME.png"]
    assert seen[0].headers["accept-encoding"] == "identity"
    assert await A.fetch_logo("gme", max_bytes=512_000, timeout=5.0, deps=deps) == got
    assert len(seen) == 1


@pytest.mark.parametrize("profile, why", [
    (prof("GME", "GameStop Corp.", image="https://evil.example/GME.png"), "url_mismatch"),
    (prof("GME", "GameStop Corp.", image="https://images.financialmodelingprep.com/symbol/GME.svg"), "url_mismatch"),
    (prof("GME", "GameStop Corp.", defaultImage=True), "default_image"),
    (prof("GME", "GameStop Corp.", defaultImage=None), "default_image"),
])
@pytest.mark.asyncio
async def test_fetch_logo_refuses_without_requesting(profile, why, caplog):
    deps, seen, _ = logo_deps(lambda r: httpx.Response(200, content=PNG), profile=profile)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert seen == [] and f"reason={why}" in caplog.text and "evil" not in caplog.text


@pytest.mark.parametrize("response, why", [
    (httpx.Response(301, headers={"location": "https://evil.example/x.png"}), "http_301"),
    (httpx.Response(404), "http_404"),
    (httpx.Response(200, content=b"x" * 600_000, headers={"content-type": "image/png"}), "too_large"),
    (streamed(200, b"", {"content-type": "image/png"}), "empty"),
])
@pytest.mark.asyncio
async def test_fetch_logo_refuses_redirects_errors_and_oversize(response, why, caplog):
    deps, seen, _ = logo_deps(lambda r: response)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert len(seen) == 1 and f"reason={why}" in caplog.text
    assert "https://" not in caplog.text


@pytest.mark.asyncio
async def test_fetch_logo_caps_a_streamed_body_without_a_length_header():
    async def stream():
        for _ in range(100):
            yield b"x" * 10_000

    deps, _seen, _ = logo_deps(lambda r: httpx.Response(200, content=stream(), headers={"content-type": "image/png"}))
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None


@pytest.mark.asyncio
async def test_fetch_logo_never_raises(caplog):
    def boom(_request):
        raise httpx.ConnectError("refused")

    deps, _seen, _ = logo_deps(boom)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert "reason=error" in caplog.text
    # The memoized profile of the call above would skip the failing fake below and reach the
    # real network (blocked by the hermetic conftest, but then nothing here is what it claims).
    A.clear_memo()
    deps = A.NewsDeps(fmp=FakeFMP(profiles=FMPUnavailableException("down")))
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert await A.fetch_logo("ABCDW", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert await A.fetch_logo("GME", max_bytes=0, timeout=5.0, deps=deps) is None


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "GZIP ", "identity, gzip", "x-unknown"])
@pytest.mark.asyncio
async def test_fetch_logo_asks_for_identity_and_refuses_an_encoded_body(encoding, caplog):
    """A compressed answer is refused unread: httpx would inflate it per network read with no
    output limit, so ~1 KB of gzip could become ~1 MB (and brotli far more) before any cap."""
    import gzip
    bomb = gzip.compress(b"\x00" * 2_000_000)                       # 2 MB of zeros in ~2 KB
    assert len(bomb) < 10_000
    read: List[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        async def chunks():
            read.append(1)
            yield bomb

        return httpx.Response(200, content=chunks(), headers={"content-type": "image/png",
                                                             "content-encoding": encoding})

    deps, seen, _ = logo_deps(handler)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    assert await A.fetch_logo("GME", max_bytes=512_000, timeout=5.0, deps=deps) is None
    assert seen[0].headers["accept-encoding"] == "identity"
    assert "reason=encoded" in caplog.text and read == []          # never read, never inflated


@pytest.mark.asyncio
async def test_fetch_logo_caps_wire_bytes_and_never_decodes():
    """The cap counts the bytes as they came off the wire (`aiter_raw`), and an
    identity-encoded body is returned byte for byte."""
    deps, _seen, _ = logo_deps(lambda r: streamed(200, PNG, {"content-type": "image/png",
                                                             "content-encoding": "identity"}))
    assert await A.fetch_logo("GME", max_bytes=len(PNG), timeout=5.0, deps=deps) == (PNG, "image/png")
    A.clear_memo()
    deps, _seen, _ = logo_deps(lambda r: streamed(200, PNG, {"content-type": "image/png"}))
    assert await A.fetch_logo("GME", max_bytes=len(PNG) - 1, timeout=5.0, deps=deps) is None


# ── review round 1 (2026-10-09): Form 4 amendments, entities, issuers, share classes ──


# Round 1's Form 4/A shapes are cases of THE amendment rule now (the round-7 section below).


@pytest.mark.parametrize("fund", ["STARBOARD VALUE LP", "TRIAN FUND MANAGEMENT, L.P.", "BAKER BROS. ADVISORS LP",
                                  "ELLIOTT INVESTMENT MANAGEMENT L P"])
@pytest.mark.asyncio
async def test_a_fund_reporting_as_a_director_is_never_published_as_a_director(fund):
    """data:F2 — a fund with a board designee files with the Director box checked; the renderer
    refuses its name, and the role-only fallback then read "A Fox director disclosed buying $90
    million". The fund is skipped; the next person is the row, or none."""
    fund_row = irow(sym="FOX", name=fund, cik="0000000801", title="director, 10 percent owner",
                    shares=3_000_000, price=30.0)
    person = irow(sym="FOX", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0)
    got = await run("insider_buys", FakeFMP(insider=[fund_row, person], profiles=week_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.amount_usd, fox.person_name) == ("director", 600_000.0, "David Brown")

    A.clear_memo()
    got = await run("insider_buys", FakeFMP(insider=[fund_row], profiles=week_profiles()))
    assert got.records == () and got.skip_reason == "insider_none_qualified"
    assert got.rejections == {"reporter_is_entity": 1}


def _brown_forman(sym, name="Brown-Forman Corporation"):
    return prof(sym, name, price=30.0, cap=15e9, ceo="Mr. Ryan Cohen", cik=issuer_cik("BF"))


@pytest.mark.parametrize("line_cik, class_a_name", [
    (issuer_cik("BF"), "Brown-Forman Class A"),     # the lines' issuer CIK ties two different names
    (None, "Brown-Forman Corporation"),             # no CIK on the lines: the display name ties them
], ids=["by_cik", "by_name"])
@pytest.mark.asyncio
async def test_one_issuers_share_classes_are_one_row(line_cik, class_a_name):
    """data:F3 (Form 4) — one CEO buying BF-B and BF-A became two rows both named "Brown-Forman":
    "2 CEOs disclosed buying". The issuer is the unit: its largest class is kept."""
    rows = [irow(sym="BF-B", shares=26_000, price=30.0, companyCik=line_cik),
            irow(sym="BF-A", shares=14_000, price=30.0, companyCik=line_cik)]
    profiles = {"BF-A": _brown_forman("BF-A", class_a_name), "BF-B": _brown_forman("BF-B")}
    # The per-issuer read (by the profile's CIK) holds the lines under the issuer's CIK, as FMP's
    # issuer index does — the walk's copies of the "by_name" case carry none.
    issuer_feed = {A.normalize_cik(issuer_cik("BF")): [dict(r, companyCik=issuer_cik("BF")) for r in rows]}
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=profiles, issuer_feed=issuer_feed))
    # Review round 9: the issuer is the unit for the PERSON too — a row of the larger class alone
    # ("disclosed buying $780,000 of Brown-Forman") would understate what the filings report
    # ($1.2M across both classes): the person is refused (`partial_person`), never re-summed.
    # By CIK the walk ties the classes at once; by name the per-issuer read (both classes, under
    # the issuer's CIK) finds the other class's purchase after the share-class cut.
    assert got.records == () and got.skip_reason == "ceo_none_qualified"
    assert got.rejections == ({"partial_person": 2} if line_cik else {"share_class_overlap": 1, "partial_person": 1})
    # Control: one class bought, the other not — the row stands.
    A.clear_memo()
    issuer_feed = {A.normalize_cik(issuer_cik("BF")): [dict(rows[0], companyCik=issuer_cik("BF"))]}
    got = await run("ceo_buys", FakeFMP(insider=rows[:1], profiles=profiles, issuer_feed=issuer_feed))
    (row,) = got.records[0].rows
    assert (row.company.symbol, row.company.name, row.amount_usd) == ("BF-B", "Brown-Forman", 780_000.0)
    assert got.rejections == {}


@pytest.mark.parametrize("second_cik", [issuer_cik("GME"), None, "0000000000", issuer_cik("ZZZ")],
                         ids=["same_cik", "no_cik", "junk_cik", "other_cik"])
@pytest.mark.asyncio
async def test_two_ceos_on_one_symbol_are_ambiguous_whatever_their_lines_cik(second_cik):
    """The per-issuer grouping must never hide a co-CEO whose line carries no (or another) CIK."""
    rows = week_rows() + [irow(name="BOND JAMES", cik="0000000999", shares=10_000, price=25.0,
                               companyCik=second_cik)]
    fmp = FakeFMP(insider=rows, profiles=week_profiles())
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections["ambiguous_ceo"] == 1


@pytest.mark.asyncio
async def test_co_ceos_on_two_share_classes_are_ambiguous():
    rows = [irow(sym="BF-B", shares=26_000, price=30.0, companyCik=issuer_cik("BF")),
            irow(sym="BF-A", name="BOND JAMES", cik="0000000999", shares=14_000, price=30.0,
                 companyCik=issuer_cik("BF"))]
    fmp = FakeFMP(insider=rows + week_rows(), profiles={**week_profiles(),
                                                       **{s: _brown_forman(s) for s in ("BF-A", "BF-B")}})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"]
    assert got.rejections["ambiguous_ceo"] == 1
    assert fmp.profile_requests == [["GME", "FOX"]]


# ── review round 5: a co-CEO / co-CFO is never "the company's chief executive" ──

_CO_CEO_TITLES = ["officer: Co-CEO", "officer: Co-Chief Executive Officer", "director, officer: Co CEO",
                  "officer: Co-Chairman and Co-Chief Executive Officer", "officer: President and co-CEO",
                  "officer: Co–CEO", "officer: " + "Founder, Chairman, " * 12 + "Co-CEO",  # past 200 chars
                  # integration: a non-breaking (U+2011) or plain Unicode (U+2010) hyphen is a hyphen
                  "officer: Co\u2011CEO", "officer: Co\u2010Chief Executive Officer"]


@pytest.mark.parametrize("title", _CO_CEO_TITLES)
@pytest.mark.asyncio
async def test_a_single_co_ceo_buyer_is_refused(title):
    """rr5 lens 1 #1: one of two co-CEOs buys and the other does not, so the two-CEO grouping sees
    one CEO, and the role-only video would say "the company's chief executive". The shared
    `is_ceo_role` still calls a co-CEO the CEO (the Home card keeps it); the marketing path
    refuses the row, counted `ambiguous_ceo`."""
    from app.services._insider_buys_common import insider_role
    assert insider_role(title) == "ceo"            # anti-vacuity: the extractor keeps the line
    rows = week_rows()
    for r in rows[:2]:                             # GME's CEO, both of his lines
        r["typeOfOwner"] = title
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=week_profiles()))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "ambiguous_ceo": 1}


@pytest.mark.parametrize("title", ["officer: Co-CFO", "officer: Co-Chief Financial Officer",
                                   "director, officer: co CFO"])
@pytest.mark.asyncio
async def test_a_single_co_cfo_buyer_is_refused(title):
    from app.services._insider_buys_common import insider_role
    assert insider_role(title) == "cfo"
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title=title, shares=10_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    assert got.records == () and got.rejections == {"ambiguous_ceo": 1}


@pytest.mark.parametrize("second", [{}, {"sym": "BF-A"}], ids=["same_symbol", "other_class_same_cik"])
@pytest.mark.asyncio
async def test_a_co_ceo_beside_a_plain_ceo_drops_both(second):
    """Dropping only the co- line would leave the other co-CEO — filing as plain "Chief Executive
    Officer" — alone in the two-CEO grouping, published as THE CEO. Every CEO line of the symbol
    and issuer CIK goes (one count per person)."""
    rows = [irow(sym="BF-B", title="officer: Co-CEO", shares=26_000, price=30.0, companyCik=issuer_cik("BF")),
            irow(**{"sym": "BF-B", **second}, name="BOND JAMES", cik="0000000999", shares=14_000, price=30.0,
                 companyCik=issuer_cik("BF"))]
    fmp = FakeFMP(insider=rows + week_rows()[2:], profiles={**week_profiles(),
                                                            **{s: _brown_forman(s) for s in ("BF-A", "BF-B")}})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "ambiguous_ceo": 2}


@pytest.mark.asyncio
async def test_a_co_cfo_drops_the_symbols_cfos_but_never_its_directors():
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="officer: Co-CFO", shares=20_000,
                 price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="ROE RICHARD", cik="0000000444", title="officer: Chief Financial Officer",
                 shares=15_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="DOE JANE", cik="0000000555", title="director", shares=5_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.amount_usd) == ("director", pytest.approx(150_000.0))
    assert got.rejections == {"ambiguous_ceo": 2}


@pytest.mark.asyncio
async def test_a_director_with_a_co_title_is_refused_alone():
    """A former co-CEO on the board is a director line: that person is refused (the title is
    the spec's trigger), but nobody else's director line on the symbol goes with it."""
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director, officer: Former Co-CEO",
                 shares=20_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="DOE JANE", cik="0000000555", title="director", shares=5_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.amount_usd) == ("director", pytest.approx(150_000.0))
    assert got.rejections == {"ambiguous_ceo": 1}


@pytest.mark.parametrize("series, title, role, sitting", [
    ("ceo_buys", "officer: Chief Executive Officer", "ceo", True),
    ("ceo_buys", "officer: CEO, Co Founder & Chairman", "ceo", True),
    ("ceo_buys", "officer: Co-Founder and Chief Executive Officer", "ceo", True),
    ("insider_buys", "director, officer: Corporate Secretary", "director", True),
    ("insider_buys", "director, officer: Co-Chief Operating Officer", "director", True),
    ("insider_buys", "director, officer: Co-COO", "director", True),
    # Round 8: a word outside the sitting-title allow-list refuses the officer as role_uncertain
    # (accepted over-block) — but the co-officer rule still never fires on them.
    ("ceo_buys", "officer: Chief Executive Officer and Corporate Secretary", "ceo", False),
    ("ceo_buys", "officer: Co-Chairman and CEO", "ceo", False),
    ("insider_buys", "officer: Chief Financial Officer and Corporate Controller", "cfo", False),
])
@pytest.mark.asyncio
async def test_a_co_word_that_is_not_a_co_ceo_or_co_cfo_is_untouched(series, title, role, sitting):
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title=title, shares=10_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run(series, FakeFMP(insider=rows, profiles=_fox_profiles()))
    assert "ambiguous_ceo" not in got.rejections
    if sitting:
        (fox,) = got.records[0].rows
        assert fox.role == role and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {"role_uncertain": 1}


@pytest.mark.asyncio
async def test_a_line_filed_for_another_issuer_is_not_this_companys_purchase(caplog):
    """data:F5 — FMP keys the symbol on the filing's EDGAR folder: a line under IEP that names
    CVI's CIK is a purchase of CVI's stock, never "An Icahn Enterprises director disclosed buying"."""
    rows = [irow(sym="IEP", name="BROWN DAVID", cik="0000000556", title="director", shares=100_000, price=20.0,
                 companyCik=issuer_cik("CVI")),
            irow(sym="CVI", name="TAYLOR EMMA", cik="0000000557", title="director", shares=20_000, price=25.0)]
    profiles = {"IEP": prof("IEP", "Icahn Enterprises L.P.", price=20.0, cap=5e9),
                "CVI": prof("CVI", "CVR Energy, Inc.", price=25.0, cap=3e9)}
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=profiles))
    assert [(r.company.symbol, r.person_name) for r in got.records[0].rows] == [("CVI", "Emma Taylor")]
    assert got.rejections["issuer_mismatch"] == 1

    A.clear_memo()
    profiles["IEP"]["cik"] = None              # nothing to check the line against: fail closed
    rows[0]["companyCik"] = issuer_cik("IEP")
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=profiles))
    assert [r.company.symbol for r in got.records[0].rows] == ["CVI"]
    assert got.rejections == {"issuer_unverified": 1}

    A.clear_memo()
    rows[0]["companyCik"] = None               # no CIK on either side: nothing contradicts it …
    fmp = FakeFMP(insider=rows, profiles=profiles)
    got = await run("insider_buys", fmp)
    # … but the Form 4/A check reads the ISSUER by its profile CIK (review round 9), and there is
    # none to read by: fail closed for that row (no read is made for it).
    assert [r.company.symbol for r in got.records[0].rows] == ["CVI"]
    assert got.rejections == {"amendment_check_failed": 1}
    assert issuer_reads(fmp) == ["CVI"]


@pytest.mark.asyncio
async def test_a_member_filing_last_first_middle_is_dropped_never_role_only(caplog):
    """tests:F2 — "LAST FIRST MIDDLE" (the SEC reporting form) is matched only by the ordered-
    pair loop of `is_congress_name`; the renderer refuses the full middle word, so a miss would
    publish the member role-only ("A Home Depot director …")."""
    rows = [irow(sym="HD", name="PELOSI NANCY PATRICIA", cik="0000000444", title="director",
                 shares=20_000, price=300.0),
            irow(sym="FOX", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0)]
    profiles = {**week_profiles(), "HD": prof("HD", "The Home Depot, Inc.", price=300.0, cap=300e9)}
    caplog.set_level(logging.DEBUG)
    fmp = FakeFMP(insider=rows, profiles=profiles)
    got = await run("insider_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"congress_name": 1}
    assert all("HD" not in req for req in fmp.profile_requests)
    text = caplog.text.lower()
    assert "pelosi" not in text and "patricia" not in text


@pytest.mark.parametrize("status, read", [
    ("inactive", False), ("dormant", False), (" INACTIVE ", False), ("retired", False),
    ("", True), (None, True), ("active", True), (" Active ", True),
])
@pytest.mark.asyncio
async def test_only_an_active_registry_filer_is_read(status, read):
    """data:F7 — `whales.lifecycle_status` is curated "" or "inactive" (Scion); the old filter
    skipped only "dormant", a value production never stores, so every inactive filer cost an
    FMP dates call and a strike toward `thirteen_f_unavailable`."""
    fmp, sb = registry_world([whale(SCION, "Scion Asset Management", lifecycle_status=status)])
    got = await run("thirteen_f", fmp, sb=sb)
    assert (fmp.calls["dates"] == 1) is read
    assert bool(got.records) is read


def _alphabet_profiles():
    return {**thirteen_f_profiles(), "GOOG": prof("GOOG", "Alphabet Inc.", exchange="NASDAQ"),
            "GOOGL": prof("GOOGL", "Alphabet Inc.", exchange="NASDAQ")}


GOOGL_CUSIP, GOOG_CUSIP = "02079K305", "02079K107"


@pytest.mark.parametrize("q3_googl, q2_extra, gone", [
    # GOOG left, GOOGL still held UNCHANGED: "No longer reported: Alphabet" alone — false.
    (1_000_000, [("GOOG", GOOG_CUSIP, 500_000, 80e6)], ["GOOG"]),
    # GOOG left and GOOGL grew: the exit (GOOGL on this book) and the increase (GOOG on the last
    # book) each describe one class of one issuer.
    (1_200_000, [("GOOG", GOOG_CUSIP, 500_000, 80e6)], ["GOOG", "GOOGL"]),
], ids=["exit_beside_unchanged_class", "exit_and_increase"])
@pytest.mark.asyncio
async def test_a_registry_move_of_one_share_class_is_dropped(q3_googl, q2_extra, gone):
    """data:F3 (13F) — the CUSIP diff sees two securities; the post must see one issuer. The SEC
    issuer names carry the class ("ALPHABET INC CL C"), so the CUSIP issuer prefix ties them."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "GOOGL", GOOGL_CUSIP, q3_googl, 200e6, nameOfIssuer="ALPHABET INC CL A")]
    extracts[(BERKSHIRE, 2026, 2)] = extracts[(BERKSHIRE, 2026, 2)] + [
        xrow(BERKSHIRE, 2, "GOOGL", GOOGL_CUSIP, 1_000_000, 160e6, nameOfIssuer="ALPHABET INC CL A")] + [
        xrow(BERKSHIRE, 2, sym, cusip, sh, val, nameOfIssuer="ALPHABET INC CL C")
        for sym, cusip, sh, val in q2_extra]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    fmp.profiles = _alphabet_profiles()
    got = await run("thirteen_f", fmp, sb=sb)
    shown = [m.company.symbol for m in got.records[0].moves]
    assert shown == ["CRWV", "OXY", "AAPL"]
    assert not set(gone) & set(shown)
    assert got.rejections == {"share_class_overlap": len(gone)}
    # Dropped before any profile call: the adapter's batch (after the builder's) has no class.
    assert fmp.profile_requests[-1] == ["CRWV", "OXY", "AAPL"]


@pytest.mark.asyncio
async def test_a_newly_reported_class_of_an_issuer_already_held_is_dropped():
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "GOOGL", GOOGL_CUSIP, 1_000_000, 160e6, nameOfIssuer="ALPHABET INC"),
        xrow(BERKSHIRE, 3, "GOOG", GOOG_CUSIP, 500_000, 80e6, nameOfIssuer="ALPHABET INC")]
    extracts[(BERKSHIRE, 2026, 2)] = extracts[(BERKSHIRE, 2026, 2)] + [
        xrow(BERKSHIRE, 2, "GOOGL", GOOGL_CUSIP, 1_000_000, 160e6, nameOfIssuer="ALPHABET INC")]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    fmp.profiles = _alphabet_profiles()
    got = await run("thirteen_f", fmp, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "OXY", "AAPL"]
    assert got.rejections == {"share_class_overlap": 1}


@pytest.mark.asyncio
async def test_a_club_exit_beside_a_class_still_held_is_dropped(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    detail = club.details["nvidia"]
    detail.changes.append(SimpleNamespace(symbol="GOOG", name="Alphabet Inc.", change="no_longer_reported",
                                          newly_listed=False, shares=None, prev_shares=900_000.0, value=None))
    detail.holdings = [SimpleNamespace(symbol="GOOGL", name="Alphabet Inc.", shares=1e6, value=2e8),
                       SimpleNamespace(symbol="CRWV", name="CoreWeave, Inc.", shares=1e6, value=1.5e8)]
    fmp = FakeFMP(profiles=_alphabet_profiles())
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "RXRX", "AAPL"]
    assert got.rejections["share_class_overlap"] == 1
    assert all("GOOG" not in req for req in fmp.profile_requests)


@pytest.mark.asyncio
async def test_two_presented_moves_of_one_display_name_are_both_dropped(monkeypatch):
    """The backstop on profiled names: a club book has no CUSIPs and no previous quarter, so two
    classes the book check cannot tie still never print one company twice."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    detail = club.details["nvidia"]
    detail.changes += [
        SimpleNamespace(symbol="FOX", name="Fox Class B", change="newly_reported", newly_listed=False,
                        shares=1_000_000.0, prev_shares=None, value=40e6),
        SimpleNamespace(symbol="FOXA", name="Fox Class A", change="decreased", newly_listed=False,
                        shares=500_000.0, prev_shares=1_000_000.0, value=20e6)]
    profiles = {**thirteen_f_profiles(), "FOX": prof("FOX", "Fox Corporation", exchange="NASDAQ"),
                "FOXA": prof("FOXA", "Fox Corporation", exchange="NASDAQ")}
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "RXRX", "AAPL"]
    assert got.rejections["share_class_overlap"] == 2


# ── tests:F3 — every branch of the 13F share-count cross-check ─────────────────

def _share_ctx():
    return A._Ctx("thirteen_f", RUN, frozenset(), 5, 1e18, A.NewsDeps())


CUR = book(BERKSHIRE)[(BERKSHIRE, 2026, 3)]      # AAPL 1.2M, CRWV 0.5M, KO 1M
PREV = book(BERKSHIRE)[(BERKSHIRE, 2026, 2)]     # AAPL 1.0M, KO 1M, OXY 2M


def mv(symbol, move, shares=None, prev_shares=None):
    return {"symbol": symbol, "move": move, "shares": shares, "prev_shares": prev_shares}


@pytest.mark.parametrize("move, kept", [
    (mv("CRWV", "newly_reported", 500_000.0), True),
    (mv("CRWV", "newly_reported", 400_000.0), False),          # shares disagree with this quarter
    (mv("AAPL", "newly_reported", 1_200_000.0), False),        # the symbol WAS on last quarter's book
    (mv("OXY", "no_longer_reported", None, 2_000_000.0), True),
    (mv("KO", "no_longer_reported", None, 1_000_000.0), False),  # still held this quarter
    (mv("OXY", "no_longer_reported", None, 1_500_000.0), False),  # previous shares disagree
    (mv("AAPL", "increased", 1_200_000.0, 1_000_000.0), True),
    (mv("AAPL", "increased", 1_300_000.0, 1_000_000.0), False),  # shares disagree with this quarter
    (mv("ZZZZ", "no_longer_reported", None, 5.0), True),       # a symbol the helper lacks: the builder's call
], ids=["new_ok", "new_shares", "new_was_held", "exit_ok", "exit_still_held", "exit_prev_shares",
        "inc_ok", "inc_shares", "unknown_symbol"])
def test_share_check_branches(move, kept):
    ctx = _share_ctx()
    out = A._share_check(ctx, [move], CUR, PREV)
    assert (out == [move]) is kept
    assert ctx.rejections == ({} if kept else {"degraded_build": 1})


@pytest.mark.parametrize("cur, prev", [(None, PREV), (CUR, None), ({"x": 1}, PREV)])
def test_share_check_without_both_raw_extracts_drops_every_move(cur, prev):
    ctx = _share_ctx()
    moves = [mv("CRWV", "newly_reported", 500_000.0), mv("OXY", "no_longer_reported", None, 2_000_000.0)]
    assert A._share_check(ctx, moves, cur, prev) == []
    assert ctx.rejections == {"degraded_build": 2}
    ctx = _share_ctx()
    assert A._share_check(ctx, [], None, None) == [] and ctx.rejections == {}


@pytest.mark.asyncio
async def test_a_moves_own_security_is_never_its_other_class():
    """The previous book is the RAW extract, where FMP may leave a row's symbol empty (the
    builder resolves it from the CUSIP). The same CUSIP is the same security — never "another
    share class" of itself."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 2)] = [dict(r, symbol="") if r["symbol"] == "AAPL" else r
                                      for r in extracts[(BERKSHIRE, 2026, 2)]]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported"), ("AAPL", "increased")]
    assert "share_class_overlap" not in got.rejections


# ── review round 2 (2026-10-09): partial and late Form 4/As, re-codes, Money Map caps, 13F dates ──


# Round 2's partial / late / re-code Form 4/A shapes are now cases of THE amendment rule (the
# round-7 section below). What stays here is the all-code read that rule runs on — per ISSUER
# since review round 9 (``companyCik`` = the profile's CIK, every share class at once).

def _amendment_week(symbol_feed=None, **kw):
    return FakeFMP(insider=week_rows(), profiles=week_profiles(), symbol_feed=symbol_feed, **kw)


@pytest.mark.asyncio
async def test_an_ordinary_week_reads_each_published_issuer_once_with_no_code_filter():
    fmp = _amendment_week()
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"]
    assert got.rejections == {"warrant_unit_right": 1}
    assert issuer_reads(fmp) == ["FOX", "GME"]
    # By the PROFILE's issuer CIK in its 10-digit form, every code, never by symbol.
    assert sorted(c["company_cik"] for c in fmp.issuer_calls) == sorted(
        R.cik10(A.normalize_cik(week_profiles()[s]["cik"])) for s in ("GME", "FOX"))
    assert all(c == {"since": WEEK, "transaction_type": None, "page_size": A.INSIDER_AMENDMENT_PAGE_SIZE,
                     "max_pages": A.INSIDER_AMENDMENT_MAX_PAGES, "company_cik": c["company_cik"]}
               for c in fmp.issuer_calls)
    # Memoized like the walk: a second call (and the other Form 4 series) re-reads nothing.
    await run("ceo_buys", fmp)
    assert fmp.calls["insider_issuer"] == 2 and fmp.calls["insider"] == 1


@pytest.mark.parametrize("answer", [
    FMPPartialPageException("page 1 lost", endpoint="insider-trading/search", pages_total=2,
                            pages_failed=1, partial=[irow()]),
    FMPRateLimitException("429"), FMPUnavailableException("down"), [], {"not": "a list"},
    [irow(sym="ZZZ")],                         # rows, but none of the symbol's
], ids=["partial", "rate_limit", "down", "empty", "not_a_list", "other_symbol"])
@pytest.mark.asyncio
async def test_a_failed_amendment_check_drops_that_row_never_the_week(answer, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    got = await run("ceo_buys", _amendment_week({"GME": answer}))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "amendment_check_failed": 1}
    assert "GME Form 4/A check" in caplog.text and "http" not in caplog.text


@pytest.mark.asyncio
async def test_an_empty_or_failed_amendment_read_is_never_memoized():
    fmp = _amendment_week({"GME": []})
    await run("ceo_buys", fmp)
    fmp.symbol_feed = None
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"]
    assert issuer_reads(fmp).count("GME") == 2


@pytest.mark.asyncio
async def test_the_amendment_check_reads_at_most_the_bound_and_fills_from_spares():
    syms = [f"S{c}" for c in "ABCDEFGHIJ"]               # ten symbols, largest first
    # One CEO per company (review round 10: one person on two issuers is ONE row — `same_person`).
    rows = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=100_000 - 1_000 * i)
            for i, s in enumerate(syms)]
    profiles = {s: prof(s, f"{s} Industries Inc.") for s in syms}
    bad = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", tx="J-Other", filed="2026-11-14", form="4/A")
           for i, s in enumerate(syms[:4])]
    feed = {s: [r for r in rows + bad if r["symbol"] == s] for s in syms}
    fmp = FakeFMP(insider=rows, profiles=profiles, symbol_feed=feed)
    got = await run("ceo_buys", fmp)
    # Wave 1: SA..SE (four amended, SE kept); wave 2: three spares (SF, SG, SH) — the bound.
    assert len(fmp.issuer_calls) == A.INSIDER_AMENDMENT_MAX_SYMBOLS == 8
    assert [r.company.symbol for r in got.records[0].rows] == ["SE", "SF", "SG", "SH"]
    assert got.rejections == {"amended_filing": 4}


@pytest.mark.asyncio
async def test_an_amendment_check_without_budget_left_is_budget_exhausted_not_a_dropped_row():
    """Like every other step: out of budget is the series' answer, not a per-row failure."""
    clock = {"now": 0.0}

    class SlowProfiles(FakeFMP):
        async def get_company_profiles_batch(self, symbols):
            clock["now"] = 59.5                 # the profile batch "took" 59.5 s of a 60 s budget
            return await super().get_company_profiles_batch(symbols)

    fmp = SlowProfiles(insider=week_rows(), profiles=week_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("ceo_buys", run_date=RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and "stage=insider_amendment_check" in err.value.detail
    assert fmp.calls["insider_issuer"] == 0


# Money Map: content refusals have their own cap.

GROSS = ("CMCSA", "INTC", "XOM")


def _money_pool(monkeypatch, seed, *, gross=(), degraded=(), mostly_other=()):
    monkeypatch.setattr(R, "MONEY_MAP_SEED", tuple(seed))
    bds = {}
    for s in seed:
        if s in degraded:
            bds[s] = breakdown(symbol=s, degraded=["segmentation_unavailable"])
        elif s in gross:   # a stack over revenue: the breakdown derives "intersegment eliminations"
            bds[s] = breakdown(symbol=s, intersegment_eliminations=10e9)
        elif s in mostly_other:   # "Other" $120B above the largest segment ($100B)
            bds[s] = breakdown(symbol=s, revenue_sources=[src("iPhone", 100e9), src("Services", 90e9),
                                                          src("Mac", 40e9), src("Other", 120e9)])
        else:
            bds[s] = breakdown(symbol=s)
    world = dict(revenue=FakeService(bds), profit=FakeService({}, default=profit()), facts=facts_fn(),
                 sb=FakeSB({"trending_themes": []}))
    fmp = FakeFMP(profiles={s: prof(s, f"{s.title()} Corporation", exchange="NASDAQ") for s in seed},
                  income={s: INCOME_AAPL for s in seed})
    return fmp, world


@pytest.mark.asyncio
async def test_gross_stacks_at_the_head_of_the_pool_no_longer_starve_money_map(monkeypatch):
    """rr lens 0 #4 probe: CMCSA, INTC and XOM report gross segment stacks (refused every week);
    they used up the three attempts and AAPL was never reached — every week."""
    fmp, world = _money_pool(monkeypatch, GROSS + ("AAPL",), gross=GROSS)
    got = await run("money_map", fmp, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert got.rejections == {"money_map_inconsistent": 3}


@pytest.mark.asyncio
async def test_upstream_gaps_still_stop_at_the_attempt_cap(monkeypatch):
    seed = ("IBM", "ORCL", "CSCO", "AAPL")
    fmp, world = _money_pool(monkeypatch, seed, degraded=seed[:3])
    got = await run("money_map", fmp, **world)
    assert got.records == () and got.rejections == {"money_map_degraded": 3}
    assert world["revenue"].calls == ["IBM", "ORCL", "CSCO"]          # AAPL is never read


@pytest.mark.asyncio
async def test_content_refusals_stop_at_their_own_cap(monkeypatch):
    gross = tuple(f"G{c}" for c in "ABCDEFGHI")                         # nine refused seeds
    fmp, world = _money_pool(monkeypatch, gross + ("AAPL",), gross=gross)
    got = await run("money_map", fmp, **world)
    assert A.MONEY_MAP_MAX_CONTENT_REJECTIONS == 8
    assert got.records == () and got.rejections == {"money_map_inconsistent": 8}
    assert world["revenue"].calls == list(gross[:8])


@pytest.mark.asyncio
async def test_a_normal_money_map_week_still_ends_after_three_records(monkeypatch):
    """Records count as attempts, as before: four publishable seeds and a limit of 5 → 3 records."""
    seed = ("AAPL", "MSFT", "NVDA", "AMZN")
    fmp, world = _money_pool(monkeypatch, seed)
    got = await run("money_map", fmp, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL", "MSFT", "NVDA"]
    assert world["revenue"].calls == ["AAPL", "MSFT", "NVDA"] and got.rejections == {}


# 13F: a listing date after the quarter's end is not this quarter's holding.

def test_the_rules_previous_quarter_end_matches_the_13f_builders():
    for q in (1, 2, 3, 4):
        prev = A._tc_rules.previous_quarter(2026, q)
        assert R.previous_period_end_of(f"2026-Q{q}") == A._tc_rules.quarter_end(*prev)


@pytest.mark.asyncio
async def test_a_listing_after_the_quarter_end_drops_the_move_not_the_filing(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    profiles = thirteen_f_profiles()
    profiles["CRWV"]["ipoDate"] = "2026-10-03"          # after 2026-09-30, the stored build says "newly listed"
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    (rec,) = got.records
    assert "CRWV" not in [m.company.symbol for m in rec.moves]
    assert got.rejections["move_unknown_listing"] == 1
    assert all(m.listed_on is None for m in rec.moves)


# ── review rounds 7-8 (2026-10-09/10): ONE amendment rule ──────────────────────
#
# Rounds 1-6 tied each Form 4/A to the lines it replaces, and every exception (a line-for-line
# restatement, a unit offering's warrant, a relabel, a re-code, a late 4/A, the 30-day lag …)
# opened a new hole. Round 7 refused the amended person; round 8 refuses the amended ISSUER: ANY
# amendment row filed since the window opened, on its symbol or issuer CIK, refuses every row of
# it for the week (`amended_filing`). Every shape those rounds pinned is below, and every one is
# refused; the controls (an amendment of ANOTHER issuer, an original non-P line, a 4/A filed
# before the window) change nothing, byte for byte.


def _fox_profiles():
    return {"FOX": prof("FOX", "Fox Corporation", exchange="NASDAQ", price=30.0, cap=20e9, ceo="Mr. John Smith")}


_FOX_CEO = dict(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director, officer: Chief Executive Officer")
_FOX_DIR = dict(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director")
#: Three common lots (D + D + I): $300,000 + $152,500 + $90,600 = $543,100.
_LOTS = ((10_000, 30.0, "D", 110_000), (5_000, 30.5, "D", 115_000), (3_000, 30.2, "I", 3_000))
_ONE_LOT = ((10_000, 30.0, "D", 110_000),)
_TWO_LOTS = ((10_000, 30.0, "D", 110_000), (5_000, 30.5, "D", 115_000))
_WARRANT = {"securityName": "Warrants (right to buy)", "securitiesTransacted": 10_000, "price": 0.125,
            "securitiesOwned": 10_000}
_PREFERRED = {"securityName": "8.00% Series A Cumulative Preferred Stock", "securitiesTransacted": 2_000,
              "price": 25.0, "securitiesOwned": 2_000}
_ADS = {"securityName": "American Depositary Shares", "securitiesTransacted": 1_000, "price": 30.0}
#: A unit sold 1:1 whose warrant line carries the UNIT price — the common lot's exact size and price.
_UNIT_1TO1 = {"securityName": "Warrants (right to buy)", "securitiesTransacted": 10_000, "price": 30.0,
              "securitiesOwned": 10_000}
_PREFUNDED = {"securityName": "Pre-Funded Warrants", "securitiesTransacted": 10_000, "price": 29.9999}
#: A late original (trade 10-14, filed 11-10: lag 27) beside a 4/A filed 11-15 (lag 32).
_LATE = dict(traded="2026-10-14")


def _filing(form, filed, *, base=_FOX_CEO, traded="2026-11-09", lots=_LOTS, labels=None, extra=()):
    """One Form 4 (or 4/A) as FMP returns it: a row per common lot (``labels[i]`` relabels lot i),
    then one row per ``extra`` override (a warrant / preferred line bought beside the common)."""
    rows = []
    for i, (shares, price, own, owned) in enumerate(lots):
        rows.append(irow(**base, shares=shares, price=price, filed=filed, traded=traded, own=own,
                         form=form, owned=owned, sec=(labels or {}).get(i, "Common Stock")))
    for over in extra:
        r = irow(**base, filed=filed, traded=traded, form=form)
        r.update(over)
        rows.append(r)
    return rows


def _line(base, **kw):
    """One FOX line of ``base``'s person: an original Form 4, 10,000 shares at $30, traded 11-09,
    filed 11-10, unless ``kw`` (any `irow` argument or raw FMP key) says otherwise."""
    args = {**base, "shares": 10_000, "price": 30.0, "filed": "2026-11-10", "traded": "2026-11-09",
            "owned": 110_000}
    args.update(kw)
    return irow(**args)


def _amend(base, **kw):
    """The same person's Form 4/A line, filed 11-12 unless ``kw`` says otherwise."""
    return _line(base, **{"form": "4/A", "filed": "2026-11-12", **kw})


def _survivor(series):
    """A clean GME person who stands beside every shape: the CEO (ceo_buys), a director (insider_buys)."""
    return [irow()] if series == "ceo_buys" else [irow(name="DOE JANE", cik="0000000555", title="director")]


_FOX_ISSUER = A.normalize_cik(issuer_cik("FOX"))


async def _fox_week(series, rows, *, survivor=True):
    """The week of ``rows`` (FOX) plus the GME survivor. The market-wide walk returns the P rows
    only (as FMP's ``transactionType=P-Purchase`` walk does); FOX's per-ISSUER read returns every
    row under FOX's issuer CIK — any symbol (FOXA, "N/A", blank), every code — as FMP's
    ``companyCik`` read does. → (Candidates, published rows, the fake FMP)."""
    A.clear_memo()
    rows = list(rows) + (_survivor(series) if survivor else [])
    walk = [r for r in rows if r.get("transactionType") == "P-Purchase"]
    fmp = FakeFMP(insider=walk, profiles={**week_profiles(), **_fox_profiles()},
                  issuer_feed={_FOX_ISSUER: [r for r in rows if A.normalize_cik(r.get("companyCik")) == _FOX_ISSUER]})
    got = await run(series, fmp)
    return got, (got.records[0].rows if got.records else ()), fmp


def _snapshot(got, fmp):
    return json.dumps({"records": [R.record_to_dict(r) for r in got.records], "skip": got.skip_reason,
                       "rejections": dict(got.rejections), "profiles": fmp.profile_requests}, sort_keys=True)


def _is_amendment(r):
    return "/A" in str(r.get("formType") or "")


#: Every Form 4/A shape review rounds 1-6 handled with an exception, plus the round-6 re-review's
#: probes — each a list of FOX rows of one person (the originals included). P rows reach the
#: market-wide walk; the others only the per-issuer read.
_SHAPES = {
    # round 1: a corrected holding or trade date, an earlier week's Form 4, partial and full
    # restatements
    "holding_corrected": lambda b: [_line(b), _amend(b, own="I")],
    "trade_date_corrected": lambda b: [_line(b), _amend(b, traded="2026-11-06")],
    "earlier_weeks_form_4_restated": lambda b: [_line(b, filed="2026-11-05", traded="2026-11-03"),
                                                _amend(b, traded="2026-11-03")],
    "earlier_weeks_form_4_line_added": lambda b: [_line(b, filed="2026-11-05", traded="2026-11-03"),
                                                  _amend(b, traded="2026-11-04", shares=5_000)],
    "partial_restatement": lambda b: [_line(b), _line(b, shares=2_000), _amend(b, shares=2_000, price=31.0)],
    "neither_shares_nor_price": lambda b: [_line(b), _amend(b, shares=8_000, price=31.0)],
    "full_restatement_as_filed": lambda b: [_line(b), _amend(b)],
    "price_corrected": lambda b: [_line(b), _amend(b, price=30.5)],
    # round 2: one-to-one matching, run-day (late) 4/As, a restated figure inside the window
    "one_line_4a_two_originals": lambda b: [_line(b, price=30.1), _line(b, price=30.3), _amend(b, price=30.15)],
    "same_price_two_sizes": lambda b: [_line(b), _line(b, shares=5_000), _amend(b, shares=12_000)],
    "not_one_to_one": lambda b: [_line(b), _line(b, shares=5_000, price=31.0), _amend(b, price=32.0),
                                 _amend(b, price=33.0)],
    "line_for_line_restatement": lambda b: [_line(b, price=30.1), _line(b, price=30.3), _amend(b, price=30.15),
                                            _amend(b, price=30.3)],
    "filed_twice_restated_once": lambda b: [_line(b), _line(b, filed="2026-11-12"), _amend(b, filed="2026-11-13")],
    "run_day_restated": lambda b: [_line(b), _amend(b, filed="2026-11-16", shares=5_000)],
    "run_day_holding": lambda b: [_line(b), _amend(b, filed="2026-11-16", shares=5_000, own="I")],
    "run_day_no_trade_date": lambda b: [_line(b), _amend(b, filed="2026-11-16", transactionDate="")],
    "run_day_other_date": lambda b: [_line(b), _amend(b, filed="2026-11-16", traded="2026-11-14")],
    "run_day_date_moved_back": lambda b: [_line(b), _amend(b, filed="2026-11-16", traded="2026-11-06")],
    "run_day_date_and_role": lambda b: [_line(b), _amend(b, filed="2026-11-16", traded="2026-11-06",
                                                         title="10 percent owner")],
    "restated_inside_the_window": lambda b: [_line(b), _amend(b, shares=5_000, filed="2026-11-14")],
    # round 2: re-codes the P-only walk never returns (the per-issuer read sees them)
    "recoded_j": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14")],
    "recoded_on_the_run_day": lambda b: [_line(b), _amend(b, tx="M-Exempt", filed="2026-11-16")],
    "recoded_no_trade_date": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", transactionDate="")],
    "recoded_no_holding": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", own="")],
    "recoded_other_date": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", traded="2026-11-12")],
    "recoded_other_holding": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", own="I")],
    "full_restatement_beside_an_f_line": lambda b: [_line(b), _amend(b, filed="2026-11-14"),
                                                    _amend(b, tx="F-InKind", filed="2026-11-14", shares=12_000)],
    "derivative_m_line": lambda b: [_line(b), _amend(b, tx="M-Exempt", filed="2026-11-14",
                                                     sec="Stock Option (Right to Buy)")],
    # round 3: 4/A lines the extractor refuses (role, label, size, price, dates)
    "role_rechecked_10_percent_owner": lambda b: [_line(b), _amend(b, title="10 percent owner")],
    "role_rechecked_director": lambda b: [_line(b), _amend(b, title="director")],
    "role_rechecked_ceo": lambda b: [_line(b), _amend(b, title="director, officer: Chief Executive Officer")],
    "blank_label": lambda b: [_line(b), _amend(b, securityName="")],
    "odd_label": lambda b: [_line(b), _amend(b, securityName="Shares")],
    "above_the_holding": lambda b: [_line(b), _amend(b, securitiesOwned=5_000)],
    "zero_price": lambda b: [_line(b), _amend(b, price=0)],
    "no_price": lambda b: [_line(b), _amend(b, price=None)],
    "no_trade_date": lambda b: [_line(b), _amend(b, securityName="", transactionDate="")],
    "no_filing_date": lambda b: [_line(b), _amend(b, securityName="", filingDate="unknown")],
    "refused_and_filed_after_the_window": lambda b: [_line(b), _amend(b, securityName="",
                                                                      filingDate="2026-11-16 09:00:00")],
    # round 4: unit offerings, relabels, the 30-day lag
    "unit_offering_warrant": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_WARRANT])
                                        + _filing("4/A", "2026-11-12", base=b, extra=[_WARRANT])),
    "unit_offering_preferred": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_PREFERRED])
                                          + _filing("4/A", "2026-11-12", base=b, extra=[_PREFERRED])),
    "unit_offering_depositary": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_ADS])
                                           + _filing("4/A", "2026-11-12", base=b, extra=[_ADS])),
    "unit_offering_warrant_and_preferred": lambda b: (
        _filing("4", "2026-11-10", base=b, extra=[_WARRANT, _PREFERRED])
        + _filing("4/A", "2026-11-12", base=b, extra=[_WARRANT, _PREFERRED])),
    "common_relabelled_warrants": lambda b: (
        _filing("4", "2026-11-10", base=b, extra=[_WARRANT])
        + _filing("4/A", "2026-11-12", base=b, labels={0: "Warrants", 1: "Warrants", 2: "Warrants"},
                  extra=[_WARRANT])),
    "common_relabelled_preferred": lambda b: (
        _filing("4", "2026-11-10", base=b)
        + _filing("4/A", "2026-11-12", base=b, labels={i: "Series A Preferred Stock" for i in range(3)})),
    "common_relabelled_depositary": lambda b: (
        _filing("4", "2026-11-10", base=b)
        + _filing("4/A", "2026-11-12", base=b, labels={i: "American Depositary Shares" for i in range(3)})),
    "one_lot_relabelled_beside_a_warrant": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=_ONE_LOT, extra=[_WARRANT])
        + _filing("4/A", "2026-11-12", base=b, lots=_ONE_LOT, labels={0: "Warrants"}, extra=[_WARRANT])),
    "unit_4a_blank_label": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_WARRANT])
                                      + _filing("4/A", "2026-11-12", base=b, extra=[{**_WARRANT, "securityName": ""}])),
    "unit_4a_missing_label": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_WARRANT])
                                        + _filing("4/A", "2026-11-12", base=b,
                                                  extra=[{**_WARRANT, "securityName": None}])),
    "unit_4a_odd_label": lambda b: (_filing("4", "2026-11-10", base=b, extra=[_WARRANT])
                                    + _filing("4/A", "2026-11-12", base=b,
                                              extra=[{**_WARRANT, "securityName": "Units"}])),
    "late_on_the_lag_unchanged": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE)
                                            + _filing("4/A", "2026-11-15", base=b, **_LATE)),
    "late_on_the_lag_price": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE) + _filing(
        "4/A", "2026-11-15", base=b, lots=(_LOTS[0], (5_000, 31.5, "D", 115_000), _LOTS[2]), **_LATE)),
    "late_on_the_lag_size": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE) + _filing(
        "4/A", "2026-11-15", base=b, lots=(_LOTS[0], (500, 30.5, "D", 115_000), _LOTS[2]), **_LATE)),
    "late_on_the_lag_holding": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE) + _filing(
        "4/A", "2026-11-15", base=b, lots=(_LOTS[0], (5_000, 30.5, "I", 115_000), _LOTS[2]), **_LATE)),
    "late_on_the_lag_lot_dropped": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE) + _filing(
        "4/A", "2026-11-15", base=b, lots=(_LOTS[0], _LOTS[2]), **_LATE)),
    "late_on_the_lag_lot_added": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE) + _filing(
        "4/A", "2026-11-15", base=b, lots=_LOTS + (_LOTS[2],), **_LATE)),
    "a_second_late_4a": lambda b: (_filing("4", "2026-11-10", base=b, **_LATE)
                                   + _filing("4/A", "2026-11-12", base=b, **_LATE)
                                   + _filing("4/A", "2026-11-15", base=b, **_LATE)),
    "straddling_the_lag_bound": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT, **_LATE)
                                           + _filing("4", "2026-11-10", base=b, lots=_ONE_LOT, traded="2026-10-20")
                                           + _filing("4/A", "2026-11-15", base=b, lots=_ONE_LOT, **_LATE)
                                           + _filing("4/A", "2026-11-15", base=b, lots=_ONE_LOT,
                                                     traded="2026-10-20")),
    "late_name_corrected": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT, **_LATE)
                                      + _filing("4/A", "2026-11-15", base={**b, "name": "SMITH JOHN Q"},
                                                lots=_ONE_LOT, **_LATE)),
    "on_time_role_corrected": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                         + _filing("4/A", "2026-11-12", base={**b, "title": "10 percent owner"},
                                                   lots=_ONE_LOT)),
    # round 5: a not-the-stock 4/A line with no common beside it, or beside one of two lots
    "only_common_relabelled_prefunded": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                                   + _filing("4/A", "2026-11-12", base=b, lots=(),
                                                             extra=[_PREFUNDED])),
    "only_common_relabelled_preferred": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
        + _filing("4/A", "2026-11-12", base=b, lots=(), extra=[{"securityName": "Series A Preferred Stock",
                                                                "securitiesTransacted": 10_000, "price": 25.0}])),
    "only_common_relabelled_no_price": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                                  + _filing("4/A", "2026-11-12", base=b, lots=(),
                                                            extra=[{**_PREFUNDED, "price": None}])),
    "one_of_two_relabelled_same_price": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=_TWO_LOTS)
        + _filing("4/A", "2026-11-12", base=b, lots=_TWO_LOTS[1:], extra=[{**_PREFUNDED, "price": 30.0}])),
    "one_of_two_relabelled_offering_price": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=_TWO_LOTS)
        + _filing("4/A", "2026-11-12", base=b, lots=_TWO_LOTS[1:], extra=[_PREFUNDED])),
    "relabelled_and_redated": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                         + _filing("4/A", "2026-11-12", base=b, lots=(),
                                                   extra=[{**_PREFUNDED, "transactionDate": "2026-11-06"}])),
    "re_roled_and_redated": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                       + _filing("4/A", "2026-11-12", base=b, lots=(),
                                                 extra=[{"securitiesTransacted": 10_000,
                                                         "typeOfOwner": "10 percent owner",
                                                         "transactionDate": "2026-11-06"}])),
    "older_trade_past_the_lag": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT)
                                           + _filing("4/A", "2026-11-12", base=b, lots=(),
                                                     extra=[{"securitiesTransacted": 7_000,
                                                             "transactionDate": "2026-09-01"}])),
    # round 6: 1:1 units, re-codes that also move the date or holding, a published size
    "unit_1to1_restated": lambda b: (_filing("4", "2026-11-10", base=b, lots=_ONE_LOT, extra=[_UNIT_1TO1])
                                     + _filing("4/A", "2026-11-12", base=b, lots=_ONE_LOT, extra=[_UNIT_1TO1])),
    "unit_1to1_relabelled": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=_ONE_LOT, extra=[_UNIT_1TO1])
        + _filing("4/A", "2026-11-12", base=b, lots=_ONE_LOT, labels={0: "Warrants (right to buy)"},
                  extra=[_UNIT_1TO1])),
    "recoded_and_redated": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", traded="2026-11-08")],
    "recoded_redated_and_reheld": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14",
                                                              traded="2026-11-08", own="I")],
    "non_p_only_other_size": lambda b: [_line(b), _amend(b, tx="J-Other", filed="2026-11-14", traded="2026-11-08",
                                                         own="I", shares=7_000)],
    "published_size_other_date": lambda b: [_line(b), _amend(b, filed="2026-11-14"),
                                            _amend(b, tx="J-Other", filed="2026-11-14", traded="2026-11-08")],
    "unreadable_size": lambda b: [_line(b), _amend(b, filed="2026-11-14"),
                                  _amend(b, tx="F-InKind", filed="2026-11-14", traded="2026-11-08", shares=None)],
    "full_restatement_beside_a_same_size_f": lambda b: [_line(b), _amend(b, filed="2026-11-14"),
                                                        _amend(b, tx="F-InKind", filed="2026-11-14")],
    "beside_a_p_line_another_size": lambda b: [_line(b), _amend(b, filed="2026-11-14"),
                                               _amend(b, tx="J-Other", filed="2026-11-14", traded="2026-11-08",
                                                      shares=7_000)],
    # the round-6 re-review's probes: derivative-only 4/As under a non-P code, a 1:1 unit in both
    # holdings, a buy then a same-size gift, and FMP's blank derivative-only row
    "derivative_award_only": lambda b: [_line(b), _amend(b, tx="A-Award", sec="Stock Option (Right to Buy)")],
    "warrants_j_only": lambda b: [_line(b), _amend(b, tx="J-Other", sec="Warrants")],
    "preferred_j_only": lambda b: [_line(b), _amend(b, tx="J-Other", sec="Series A Preferred")],
    "convertible_notes_c_only": lambda b: [_line(b), _amend(b, tx="C-Conversion", sec="Convertible Notes")],
    "unit_1to1_both_holdings": lambda b: (
        _filing("4", "2026-11-10", base=b, lots=((10_000, 30.0, "D", 110_000), (10_000, 30.0, "I", 10_000)),
                extra=[_UNIT_1TO1, {**_UNIT_1TO1, "directOrIndirect": "I"}])
        + _filing("4/A", "2026-11-12", base=b, lots=((10_000, 30.0, "D", 110_000), (10_000, 30.0, "I", 10_000)),
                  extra=[_UNIT_1TO1, {**_UNIT_1TO1, "directOrIndirect": "I"}])),
    "buy_then_gift_restated": lambda b: [_line(b), _line(b, tx="G-Gift", acquisitionOrDisposition="D"),
                                         _amend(b), _amend(b, tx="G-Gift", acquisitionOrDisposition="D")],
    "blank_derivative_only_row": lambda b: [_line(b), _amend(b, tx="", sec="", shares=0, price=0, own=None,
                                                             traded="2026-11-03")],
}


#: Shapes whose 4/A gives the CEO a non-officer role ("10 percent owner", "director"). Filed as
#: plain Form 4s beside the CEO's own, that same-week filing says the person is not an officer —
#: round 8 refuses them as `role_uncertain` (the director series has no officer title to check).
_ROLE_CHANGING_SHAPES = frozenset({"run_day_date_and_role", "role_rechecked_10_percent_owner",
                                   "role_rechecked_director", "on_time_role_corrected", "re_roled_and_redated"})
#: Shapes whose 4/A line, filed as a plain Form 4, is a PURCHASE of the person the published row
#: could not hold (a zero / missing price, no trade date, an unlabelled unit line, a trade past
#: the 30-day lag): review round 9 refuses the person (`partial_person`) instead of re-summing.
_PARTIAL_WHEN_PLAIN = frozenset({"zero_price", "no_price", "no_trade_date", "unit_4a_blank_label",
                                 "unit_4a_missing_label", "late_on_the_lag_price", "late_on_the_lag_size",
                                 "late_on_the_lag_holding", "older_trade_past_the_lag", "re_roled_and_redated"})


@pytest.mark.parametrize("series, base", [("ceo_buys", _FOX_CEO), ("insider_buys", _FOX_DIR)],
                         ids=["ceo", "director"])
@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.asyncio
async def test_every_amendment_shape_refuses_the_person(series, base, shape):
    rows = _SHAPES[shape](base)
    assert any(_is_amendment(r) for r in rows)
    got, published, fmp = await _fox_week(series, rows)
    assert [r.company.symbol for r in published] == ["GME"]          # the survivor stands
    assert got.rejections == {"amended_filing": 1}
    assert all(r.amended is False for r in published)
    # Where it was caught: a P-coded amendment in the walk drops the person before any profile
    # call; one under another code only the per-issuer read sees, after the profile batch.
    walk_amended = any(_is_amendment(r) and r.get("transactionType") == "P-Purchase" for r in rows)
    assert ("FOX" in fmp.profile_requests[0]) is not walk_amended
    # Anti-vacuity: the same rows filed as plain Form 4s publish FOX, never amended (or, for a
    # CEO whose other same-week filing says "10 percent owner" / "director", refuse the role).
    plain = [{**r, "formType": "4"} if _is_amendment(r) else r for r in rows]
    got, published, _fmp = await _fox_week(series, plain)
    if series == "ceo_buys" and shape in _ROLE_CHANGING_SHAPES:
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}
    elif shape in _PARTIAL_WHEN_PLAIN:
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"partial_person": 1}
    else:
        assert "FOX" in [r.company.symbol for r in published] and "amended_filing" not in got.rejections
    assert all(r.amended is False for r in published)


#: Representative shapes for the controls (every one of the walk / per-issuer / raw paths).
_CONTROL_SHAPES = ("full_restatement_as_filed", "run_day_restated", "recoded_j", "blank_label",
                   "unit_offering_warrant", "derivative_award_only", "blank_derivative_only_row")


@pytest.mark.parametrize("series, base", [("ceo_buys", _FOX_CEO), ("insider_buys", _FOX_DIR)],
                         ids=["ceo", "director"])
@pytest.mark.parametrize("shape", _CONTROL_SHAPES)
@pytest.mark.asyncio
async def test_another_issuers_amendment_changes_nothing(series, base, shape):
    """The rule is per ISSUER: the same 4/A rows filed by this person for ANOTHER issuer (its own
    symbol and issuer CIK) leave the week byte-identical to the week without them."""
    rows = _SHAPES[shape](base)
    originals = [r for r in rows if not _is_amendment(r)]
    over = {"symbol": "ZZZ", "companyCik": issuer_cik("ZZZ"), "typeOfOwner": "10 percent owner"}
    moved = originals + [{**r, **over} for r in rows if _is_amendment(r)]
    got, published, fmp = await _fox_week(series, moved)
    base_got, base_published, base_fmp = await _fox_week(series, originals)
    assert "FOX" in [r.company.symbol for r in base_published]                     # anti-vacuity
    assert _snapshot(got, fmp) == _snapshot(base_got, base_fmp)


#: Who and where an amendment of FOX's issuer can be filed (review round 8, rr lens 0 #1-#2):
#: never tied to the person any more, so each refuses the FOX row.
_ISSUER_AMENDMENTS = {
    # another reporter on the symbol: a 10% owner, never a candidate
    "another_person": {"reportingCik": "0000000999", "reportingName": "DOE JANE", "typeOfOwner": "10 percent owner"},
    # a joint co-reporter entity with its own CIK (lens 0 #2d/2e: "RC VENTURES LLC")
    "co_reporter_entity": {"reportingCik": "0001822844", "reportingName": "RC VENTURES LLC",
                           "typeOfOwner": "10 percent owner"},
    # the person, no CIK, name in first-last order (lens 0 #2c: a key of "cohen ryan")
    "name_first_last_no_cik": {"reportingCik": None, "reportingName": "John Smith"},
    # an unusable symbol with the issuer's CIK (lens 0 #3: "N/A")
    "symbol_na_issuer_cik": {"symbol": "N/A"},
    # FMP's blank-symbol row with the issuer's CIK
    "blank_symbol_issuer_cik": {"symbol": ""},
}


@pytest.mark.parametrize("series, base", [("ceo_buys", _FOX_CEO), ("insider_buys", _FOX_DIR)],
                         ids=["ceo", "director"])
@pytest.mark.parametrize("who", list(_ISSUER_AMENDMENTS))
@pytest.mark.parametrize("code", ["P-Purchase", "J-Other"], ids=["p_in_the_walk", "j_per_ticker"])
@pytest.mark.asyncio
async def test_any_amendment_of_the_issuer_refuses_its_rows(series, base, who, code):
    """lens 0 #1/#2 probes: an amendment of the ISSUER — whoever filed it, whatever its symbol says,
    when it still names the issuer CIK — refuses the FOX row (the survivor on GME stands). A
    P-coded one is caught in the walk before any profile call; another code by FOX's per-issuer
    read (by CIK, so an "N/A" or blank symbol comes back too — review round 9)."""
    amend = {**_amend(base, tx=code), **_ISSUER_AMENDMENTS[who]}
    rows = [_line(base), amend]
    got, published, fmp = await _fox_week(series, rows)
    assert [r.company.symbol for r in published] == ["GME"]
    # One count per person refused: a P-coded name-only 4/A is itself extracted under its own
    # (name) reporter key, so the walk refuses two "people" of FOX.
    two = who == "name_first_last_no_cik" and code == "P-Purchase"
    assert got.rejections == {"amended_filing": 2 if two else 1}
    assert ("FOX" in fmp.profile_requests[0]) is (code != "P-Purchase")
    # Control: the same row as a plain Form 4 publishes FOX — except that a CEO filing once by
    # CIK and once by name alone reads as two CEOs of FOX (`ambiguous_ceo`, fail closed).
    plain = [_line(base), {**amend, "formType": "4"}]
    got, published, _fmp = await _fox_week(series, plain)
    assert "amended_filing" not in got.rejections
    if two and series == "ceo_buys":
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"ambiguous_ceo": 1}
    elif who in ("symbol_na_issuer_cik", "blank_symbol_issuer_cik") and code == "P-Purchase":
        # Review round 9: a purchase of the person under no usable symbol is one the FOX row
        # cannot hold — the person is refused, never published with the smaller figure.
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"partial_person": 1}
    else:
        assert "FOX" in [r.company.symbol for r in published]


@pytest.mark.parametrize("extra", [
    {"tx": "J-Other", "filed": "2026-11-14"},                       # an ORIGINAL non-P line
    {"form": "4/A", "filed": "2026-11-05", "traded": "2026-11-03"},  # a 4/A filed before the window opened
    {"filed": "2026-11-16", "shares": 5_000},                        # a late ORIGINAL Form 4
], ids=["original_non_p", "before_the_window", "late_original"])
@pytest.mark.parametrize("series, base", [("ceo_buys", _FOX_CEO), ("insider_buys", _FOX_DIR)],
                         ids=["ceo", "director"])
@pytest.mark.asyncio
async def test_rows_that_are_not_an_amendment_since_the_window_change_nothing(series, base, extra):
    got, published, fmp = await _fox_week(series, [_line(base), _line(base, **extra)])
    base_got, _published, base_fmp = await _fox_week(series, [_line(base)])
    assert _snapshot(got, fmp) == _snapshot(base_got, base_fmp)


@pytest.mark.parametrize("form", ["5/A", "3/A", " 4/a "])
@pytest.mark.asyncio
async def test_any_amendment_form_counts(form):
    """No exception: a Form 3/A or 5/A of the same person on the symbol is refused too (accepted
    over-block), and the form is read as the extractor reads it (stripped, any case)."""
    got, published, _fmp = await _fox_week("ceo_buys", [_line(_FOX_CEO), _amend(_FOX_CEO, form=form,
                                                                                 tx="J-Other")])
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"amended_filing": 1}


@pytest.mark.parametrize("ident", [
    {"reportingCik": None},                                          # the 4/A names the person only
    {"reportingCik": "0000000000"},                                  # an all-zero CIK: the name keys it
    {"reportingName": "SMITH JOHN", "reportingCik": "0000000111"},   # control: as filed
], ids=["name_only", "zero_cik", "as_filed"])
@pytest.mark.asyncio
async def test_a_4a_identifying_the_person_differently_still_ties_to_them(ident):
    """The original carries the reporting CIK; a 4/A that carries only the name keys as
    ``name:…``, never ``cik:111``. Since round 8 the rule is per issuer, so no key is matched at
    all — the row is refused whoever the 4/A names."""
    amend = {**_amend(_FOX_CEO, tx="J-Other"), **ident}
    got, published, _fmp = await _fox_week("ceo_buys", [_line(_FOX_CEO), amend])
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"amended_filing": 1}


@pytest.mark.parametrize("walk", [True, False], ids=["p_row_in_the_walk", "non_p_row_per_ticker"])
@pytest.mark.asyncio
async def test_an_amendment_whose_reporter_cannot_be_keyed_refuses_the_whole_symbol(walk):
    """A 4/A naming no one (no CIK, no usable name) cannot be tied to a person, so nobody on its
    symbol is shown that week — the survivor on another symbol stands."""
    anon = {**_amend(_FOX_DIR, tx="P-Purchase" if walk else "J-Other", title="10 percent owner"),
            "reportingCik": None, "reportingName": ""}
    rows = [_line(_FOX_DIR), _line(_FOX_DIR, name="ROE RICHARD", cik="0000000444", shares=5_000), anon]
    got, published, _fmp = await _fox_week("insider_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"]
    assert got.rejections == ({"amended_filing": 2} if walk else {"amended_filing": 1})
    got, published, _fmp = await _fox_week("insider_buys", rows[:2])                 # control
    assert sorted(r.company.symbol for r in published) == ["FOX", "GME"] and got.rejections == {}


def test_the_insider_row_never_publishes_an_amended_line():
    """Unreachable by construction (the rule ran first), pinned at the helper: a 4/A line never
    becomes a published row, and a published row is never `amended`."""
    ws, we = R.insider_window(RUN)
    ctx = A._Ctx("ceo_buys", RUN, frozenset(), 5, 1e18, A.NewsDeps())
    company = R.company_from_profile(_fox_profiles()["FOX"], "FOX", purpose="insider")
    lines = A.extract_insider_buys([_line(_FOX_CEO)], window_start=ws, window_end=we, roles=("ceo",))
    row = A._insider_row(ctx, company, _fox_profiles()["FOX"], lines)
    assert isinstance(row, R.InsiderPurchase) and row.amended is False
    amended = A.extract_insider_buys([_amend(_FOX_CEO)], window_start=ws, window_end=we, roles=("ceo",))
    assert [b.form_type for b in amended] == ["4/A"]
    assert A._insider_row(ctx, company, _fox_profiles()["FOX"], amended) == "amended_filing"


#: Three weeks with no Form 4/A, as the round-6 adapter answered them (records, skip, rejections,
#: profile batches and per-ticker reads; computed once with that adapter and pinned): round 7
#: must answer them byte for byte the same.
def _no_amendment_weeks():
    unit = [r for r in week_rows() if r["symbol"] == "GME"] + _filing("4", "2026-11-10", extra=[_WARRANT])
    insider = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
                    filed="2026-11-10", traded="2026-11-09"),
               irow(sym="GME", name="DOE JANE", cik="0000000555", title="officer: Chief Financial Officer",
                    shares=20_000, price=25.0),
               irow(sym="GME", name="ROE RICHARD", cik="0000000444", title="director", shares=4_000, price=25.0)]
    fox = {**week_profiles(), **_fox_profiles()}
    return {
        "ceo_week": ("ceo_buys", week_rows(), week_profiles()),
        "ceo_unit_offering": ("ceo_buys", unit, fox),
        "insider_week": ("insider_buys", insider, fox),
    }


_ROUND_6_ANSWERS: Dict[str, str] = {
    'ceo_week': (
        '{"profiles": [["GME", "FOX"]], "records": [{"rows": [{"amended": false, "amount_usd": 2525000.0,'
        ' "company": {"name": "GameStop", "symbol": "GME"}, "earliest_trade_date": "2026-11-10", "filing_'
        'dates": ["2026-11-12", "2026-11-13"], "holding": "direct", "latest_trade_date": "2026-11-11", "p'
        'erson_name": "Ryan Cohen", "purchases": 2, "role": "ceo", "shares": 100000.0}, {"amended": false'
        ', "amount_usd": 1200000.0, "company": {"name": "Fox", "symbol": "FOX"}, "earliest_trade_date": "'
        '2026-11-09", "filing_dates": ["2026-11-10"], "holding": "indirect", "latest_trade_date": "2026-1'
        '1-09", "person_name": null, "purchases": 1, "role": "ceo", "shares": 40000.0}], "schema": 1, "se'
        'ries": "ceo_buys", "window_end": "2026-11-15", "window_start": "2026-11-09"}], "rejections": {"w'
        'arrant_unit_right": 1}, "skip": null, "symbol_calls": ["FOX", "GME"]}'
    ),
    'ceo_unit_offering': (
        '{"profiles": [["GME", "FOX"]], "records": [{"rows": [{"amended": false, "amount_usd": 2525000.0,'
        ' "company": {"name": "GameStop", "symbol": "GME"}, "earliest_trade_date": "2026-11-10", "filing_'
        'dates": ["2026-11-12", "2026-11-13"], "holding": "direct", "latest_trade_date": "2026-11-11", "p'
        'erson_name": "Ryan Cohen", "purchases": 2, "role": "ceo", "shares": 100000.0}, {"amended": false'
        ', "amount_usd": 543100.0, "company": {"name": "Fox", "symbol": "FOX"}, "earliest_trade_date": "2'
        '026-11-09", "filing_dates": ["2026-11-10"], "holding": "mixed", "latest_trade_date": "2026-11-09'
        '", "person_name": "John Smith", "purchases": 3, "role": "ceo", "shares": 18000.0}], "schema": 1,'
        ' "series": "ceo_buys", "window_end": "2026-11-15", "window_start": "2026-11-09"}], "rejections":'
        ' {}, "skip": null, "symbol_calls": ["FOX", "GME"]}'
    ),
    'insider_week': (
        '{"profiles": [["FOX", "GME"]], "records": [{"rows": [{"amended": false, "amount_usd": 3000000.0,'
        ' "company": {"name": "Fox", "symbol": "FOX"}, "earliest_trade_date": "2026-11-09", "filing_dates'
        '": ["2026-11-10"], "holding": "direct", "latest_trade_date": "2026-11-09", "person_name": "John '
        'Smith", "purchases": 1, "role": "director", "shares": 100000.0}, {"amended": false, "amount_usd"'
        ': 500000.0, "company": {"name": "GameStop", "symbol": "GME"}, "earliest_trade_date": "2026-11-10'
        '", "filing_dates": ["2026-11-12"], "holding": "direct", "latest_trade_date": "2026-11-10", "pers'
        'on_name": "Jane Doe", "purchases": 1, "role": "cfo", "shares": 20000.0}], "schema": 1, "series":'
        ' "insider_buys", "window_end": "2026-11-15", "window_start": "2026-11-09"}], "rejections": {}, "'
        'skip": null, "symbol_calls": ["FOX", "GME"]}'
    ),
}


@pytest.mark.parametrize("week", ["ceo_week", "ceo_unit_offering", "insider_week"])
@pytest.mark.asyncio
async def test_a_week_with_no_amendment_is_byte_identical_to_round_6(week):
    series, rows, profiles = _no_amendment_weeks()[week]
    fmp = FakeFMP(insider=rows, profiles=profiles)
    got = await run(series, fmp)
    # Round 6 named its per-ticker reads by symbol; since round 9 the same companies are read
    # per ISSUER (by CIK) — named here by their symbols, so the pinned answer stays byte for byte.
    answer = {"records": [R.record_to_dict(r) for r in got.records], "skip": got.skip_reason,
              "rejections": dict(got.rejections), "profiles": fmp.profile_requests,
              "symbol_calls": issuer_reads(fmp)}
    assert json.dumps(answer, sort_keys=True) == _ROUND_6_ANSWERS[week]


# ── review rounds 7-8: a CEO / CFO is published only under a SITTING officer's title ──

_ROLE_WORDS = ("Incoming", "Designate", "Designated", "Designee", "Future", "Successor", "Elect", "Outgoing",
               "Former", "Retired", "Previous", "Past", "Emeritus")
#: prefix, suffix, parenthetical, comma and hyphenated-tail shapes of each word.
_CEO_TITLE_SHAPES = ("officer: {W} CEO", "officer: CEO {W}", "officer: Chief Executive Officer ({W})",
                     "officer: CEO, {W}", "officer: CEO-{W}", "officer: President and CEO ({w})")
_CFO_TITLE_SHAPES = ("officer: {W} CFO", "officer: CFO {W}", "officer: Chief Financial Officer ({W})",
                     "officer: CFO, {W}", "officer: EVP and CFO-{W}", "officer: CFO and Treasurer ({w})")
_EX_TITLES = {
    "ceo_buys": ("officer: Ex-CEO", "officer: Ex CEO", "officer: CEO (ex-CFO)", "officer: CEO, ex\u2011President",
                 "officer: CEO, formerly CFO", "officer: CEO and Retiring Chairman", "officer: CEO\u2011Designate"),
    "insider_buys": ("officer: Ex-CFO", "officer: CFO (ex-Treasurer)", "officer: CFO, formerly Controller",
                     "officer: CFO and Retiring Treasurer"),
}
_SERIES_ROLE = {"ceo_buys": "ceo", "insider_buys": "cfo"}


def _role_cases():
    for series, shapes in (("ceo_buys", _CEO_TITLE_SHAPES), ("insider_buys", _CFO_TITLE_SHAPES)):
        for word in _ROLE_WORDS:
            for shape in shapes:
                yield series, shape.format(W=word, w=word.lower())
        for title in _EX_TITLES[series]:
            yield series, title


@pytest.mark.parametrize("series, title", list(_role_cases()))
@pytest.mark.asyncio
async def test_a_ceo_or_cfo_with_a_transitional_word_anywhere_is_never_published(series, title):
    """A role-only post names the company and the role ("Fox's CEO disclosed buying …"), so an
    incoming, designated, elected or outgoing officer is never shown as THE CEO / CFO. Where the
    shared role rule (Home's, unchanged) already says "not the CEO", the line is never extracted;
    where it keeps the role, the adapter refuses the person (`role_uncertain`)."""
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    assert [r.company.symbol for r in published] == ["GME"]
    kept_by_shared_rule = insider_role(title) == _SERIES_ROLE[series]
    assert got.rejections == ({"role_uncertain": 1} if kept_by_shared_rule else {})


def test_the_adapter_rule_is_what_refuses_the_words_the_shared_rule_keeps():
    """Anti-vacuity: the shared rule keeps the role for many of those titles (incoming,
    designate(d), designee, future, successor, ex- inside the title) — only the adapter's
    sitting-title allow-list refuses them."""
    cases = list(_role_cases())
    assert not any(A._role_text_ok(t, _SERIES_ROLE[s]) for s, t in cases)
    kept = [t for s, t in cases if insider_role(t) == _SERIES_ROLE[s]]
    assert len(kept) >= 50, len(kept)


@pytest.mark.parametrize("series, title, role", [
    ("ceo_buys", "officer: Chief Executive Officer", "ceo"), ("ceo_buys", "officer: President and CEO", "ceo"),
    ("ceo_buys", "officer: Interim CEO", "ceo"), ("ceo_buys", "officer: Acting Chief Executive Officer", "ceo"),
    ("ceo_buys", "director, officer: Chairman & CEO", "ceo"),
    ("insider_buys", "officer: CFO and Treasurer", "cfo"), ("insider_buys", "officer: Interim CFO", "cfo"),
    ("insider_buys", "officer: Chief Financial Officer", "cfo"),
    ("insider_buys", "officer: EVP and Chief Financial Officer", "cfo"),
    # Directors are not affected: a director whose role text names a former or elected officer
    # is published as a director.
    ("insider_buys", "director, other: Former CEO", "director"),
    ("insider_buys", "director, officer: Former CFO", "director"),
    ("insider_buys", "director, other: CEO-Elect", "director"),
])
@pytest.mark.asyncio
async def test_a_sitting_officer_and_any_director_are_published(series, title, role):
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert fox.role == role and got.rejections == {}


@pytest.mark.parametrize("title, words", [
    ("Chief Executive Officer", ["ceo"]),
    ("President & CEO", ["president", "and", "ceo"]),
    ("Chairman, President and Chief Executive Officer", ["chairman", "and", "president", "and", "ceo"]),
    ("Founder, CEO and Director", ["founder", "and", "ceo", "and", "director"]),
    ("Co-Founder & CEO", ["cofounder", "and", "ceo"]), ("CEO, Co Founder", ["ceo", "and", "cofounder"]),
    ("CEO/President", ["ceo", "and", "president"]), ("C.E.O.", ["ceo"]),
    ("Sr. VP & CFO", ["senior", "vice", "president", "and", "cfo"]),
    ("EVP-Chief Financial Officer", ["evp", "cfo"]), ("CEO\u2011Elect", ["ceo", "elect"]),
    ("CEO until 12/31/2026", ["ceo", "until", "12", "and", "31", "and", "2026"]),
    ("Chief Executive Officer (Resigned)", ["ceo", "resigned"]),
    ("CEO, 10 percent owner", ["ceo", "and"]), ("CEO; 10% Owner", ["ceo", "and"]),
    ("Président", ["pr", "sident"]), ("", []), ("x" * 201, None),
])
def test_title_words(title, words):
    assert A._title_words(title) == words


@pytest.mark.parametrize("title, role, sitting", [
    ("CEO", "ceo", True), ("Interim Chief Executive Officer", "ceo", True), ("Acting CEO", "ceo", True),
    ("Executive Chairman and CEO", "ceo", True), ("Chair of the Board and CEO", "ceo", True),
    ("CFO", "cfo", True), ("Principal Accounting Officer and CFO", "cfo", True), ("VP, Finance & CFO", "cfo", True),
    # the role must be named, every word must be on the role's list, and the lists are per role
    ("President", "ceo", False), ("Chairman and Director", "ceo", False), ("Treasurer", "cfo", False),
    ("CEO and CFO", "ceo", False), ("President and CEO", "cfo", False), ("Interim CEO & CFO", "cfo", False),
    ("Chief Executive", "ceo", False), ("Vice Chairman and CEO", "ceo", False), ("CFO of the Company", "cfo", False),
    ("", "ceo", False), (None, "ceo", False), (7, "cfo", False), ("x" * 201 + " CEO", "ceo", False),
])
def test_sitting_title_is_an_allow_list(title, role, sitting):
    assert A._sitting_title(title, role) is sitting


@pytest.mark.parametrize("text, role, ok", [
    ("director, officer: Chief Executive Officer", "ceo", True),
    ("director, officer, other: President & CEO", "ceo", True),          # the title carried in other:
    ("director, officer: CEO, other: Member of 13(d) group", "ceo", True),  # other: free text is not a title
    (None, "ceo", True), ("", "ceo", True), ("   ", "cfo", True), (7, "ceo", True),   # no evidence either way
    ("director", "ceo", False), ("10 percent owner", "ceo", False),       # filed as a non-officer
    ("officer:", "cfo", False), ("director, other: Chief Executive Officer", "ceo", False),
    ("officer: Former CEO", "ceo", False), ("officer: CEO-Designate", "ceo", False),
])
def test_role_text_ok(text, role, ok):
    assert A._role_text_ok(text, role) is ok


def test_the_title_lists_are_the_round_9_decision():
    """The allow-lists, pinned word for word: the round-8 main-session decision, plus round 9's
    measured additions ("directors" for the CEO, "director" for the CFO, "secretary" for both) and
    the director list read from an ``other:`` text that names the seat."""
    assert A.CEO_TITLE_WORDS == {"ceo", "president", "chairman", "chairwoman", "chair", "chairperson", "executive",
                                 "of", "the", "board", "and", "director", "founder", "cofounder", "interim", "acting",
                                 # round 9
                                 "directors", "secretary"}
    assert A.CFO_TITLE_WORDS == {"cfo", "treasurer", "evp", "svp", "executive", "senior", "vice", "president",
                                 "principal", "accounting", "officer", "chief", "and", "interim", "acting", "of",
                                 "finance",
                                 # round 9
                                 "director", "secretary"}
    assert A.DIRECTOR_TITLE_WORDS == {"director", "directors", "independent", "lead", "presiding", "non",
                                      "executive", "chairman", "chairwoman", "chair", "chairperson", "vice", "of",
                                      "the", "board", "and", "member"}
    # Spelling only: every abbreviation and phrase maps onto words already on a list.
    assert A._TITLE_ABBREVIATIONS == {"vp": ("vice", "president"), "sr": ("senior",), "pres": ("president",),
                                      "exec": ("executive",)}
    assert [w for _p, w in A._TITLE_PHRASES] == ["ceo", "cfo", "cfo", "cofounder"]
    assert not hasattr(A, "_TRANSITIONAL_ROLE_RE") and not hasattr(A, "_transitional")   # the denylist is gone


@pytest.mark.asyncio
async def test_one_transitional_line_refuses_the_person_not_only_the_line():
    rows = [_line({**_FOX_DIR, "title": "officer: Chief Executive Officer"}),
            _line({**_FOX_DIR, "title": "officer: Incoming CEO"}, shares=5_000, traded="2026-11-10")]
    got, published, _fmp = await _fox_week("ceo_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}


@pytest.mark.parametrize("ident", [{}, {"reportingCik": None}], ids=["same_cik", "name_only"])
@pytest.mark.asyncio
async def test_a_transitional_title_on_any_raw_row_of_the_person_counts(ident):
    """The extracted line says "Chief Executive Officer"; another walk row of the same person (a
    director line the CEO series never extracts) says "CEO-Elect": the person is refused."""
    other = {**_line({**_FOX_DIR, "title": "director, officer: CEO-Elect"}, shares=5_000, traded="2026-11-10"),
             **ident}
    rows = [_line({**_FOX_DIR, "title": "officer: Chief Executive Officer"}), other]
    got, published, _fmp = await _fox_week("ceo_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}
    got, published, _fmp = await _fox_week("ceo_buys", rows[:1])                   # control
    assert "FOX" in [r.company.symbol for r in published] and got.rejections == {}


@pytest.mark.parametrize("second", ["officer: Incoming CEO", "officer: Joint Chief Executive Officer",
                                    "officer: Dual CEO", "officer: Joint CEO", "officer: Chief Executive Officer (Co-Lead)",
                                    "officer: Co-Chairman and CEO"])
@pytest.mark.asyncio
async def test_a_second_ceo_the_role_rule_refuses_still_makes_the_sitting_one_ambiguous(second):
    """Review round 9 (#2, was `test_a_sitting_ceo_beside_an_incoming_one_is_still_the_ceo`): the
    two-CEO grouping now counts EVERY line the shared rule calls a CEO line, before the
    role_uncertain refusal. A co-CEO whose title the co- pattern does not know ("Joint", "Dual",
    "(Co-Lead)") used to be refused alone, and the other was published as THE CEO. Accepted
    over-block: an incoming CEO beside the sitting one refuses the sitting one too."""
    assert insider_role(second) == "ceo"           # anti-vacuity: a CEO line of the shared rule
    rows = [_line({**_FOX_DIR, "title": "officer: Chief Executive Officer"}),
            _line({**_FOX_DIR, "title": second, "name": "DOE JANE", "cik": "0000000999"}, shares=5_000)]
    got, published, fmp = await _fox_week("ceo_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"]
    assert got.rejections == {"role_uncertain": 1, "ambiguous_ceo": 1}
    assert "FOX" not in fmp.profile_requests[0]          # the WALK grouping caught it (not only the read)
    got, published, _fmp = await _fox_week("ceo_buys", rows[:1])                   # control
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert (fox.role, fox.person_name, fox.amount_usd) == ("ceo", "John Smith", pytest.approx(300_000.0))
    assert got.rejections == {}


# ── review round 7: the 13F materiality floor (live L1) ───────────────────────

#: Berkshire Hathaway's 2026-Q2 13F as the live preview built it (2026-10-09; the
#: live-2026-08-18 records — company names and share counts, no person): the post led with
#: 3,564 D.R. Horton shares ($580,504) in a $299B book and hid Delta, Kroger and Capital One.
_BRK_Q2_TOTAL = 299_253_556_246.0
_BRK_Q2_COUNTS = {"newly_reported": 1, "increased": 7, "decreased": 6, "no_longer_reported": 1}
_BRK_Q2_CHANGES = (
    ("DHI", "D.R. Horton", "newly_reported", 3_564.0, None, 580_504.0),
    ("STZ", "Constellation Brands", "no_longer_reported", None, 632_890.0, None),
    ("DAL", "Delta Air Lines", "increased", 57_320_000.0, 39_809_456.0, 5_368_591_200.0),
    ("M", "Macy's", "increased", 7_347_426.0, 3_038_355.0, 173_031_882.0),
    ("COF", "Capital One Financial", "decreased", 3_000_000.0, 7_150_000.0, 601_860_000.0),
    ("KR", "The Kroger", "decreased", 39_000_000.0, 50_000_000.0, 2_165_670_000.0),
    ("NUE", "Nucor", "decreased", 1_857_752.0, 3_907_075.0, 413_814_258.0),
)


def _brk_world(stz_previous_value):
    """Berkshire as a club 13F filer (its stored 2026-Q2 build) and its stored 2026-Q1 book —
    which holds Constellation Brands at ``stz_previous_value`` (None: not on it)."""
    card = dict(slug="berkshire-hathaway", name="Berkshire Hathaway", card_kind="thirteen_f", period="2026-Q2",
                comparison="quarter", detail_symbol="BRK-A", filed_on="2026-08-14")
    company = dict(card, total_value=_BRK_Q2_TOTAL, position_count=29, amended_on=None,
                   change_counts=SimpleNamespace(**_BRK_Q2_COUNTS))
    changes = [SimpleNamespace(symbol=s, name=n, change=c, shares=sh, prev_shares=p, value=v, newly_listed=False)
               for s, n, c, sh, p, v in _BRK_Q2_CHANGES]
    club = FakeClub(group=SimpleNamespace(companies=[SimpleNamespace(**card)], also_in_club=[]),
                    details={"berkshire-hathaway": SimpleNamespace(company=SimpleNamespace(**company),
                                                                   changes=changes)})
    previous = [{"symbol": "DAL", "value": 2.5e9}]
    if stz_previous_value is not None:
        previous.append({"symbol": "STZ", "value": stz_previous_value})
    sb = FakeSB({"trillion_club_companies": [{"slug": "berkshire-hathaway", "ciks": [BERKSHIRE],
                                              "card_kind": "thirteen_f", "use_13f": True,
                                              "detail_symbol": "BRK-A", "published": True}],
                 "trillion_club_filings": [{"cik": BERKSHIRE, "period": "2026-Q1", "holdings": previous}],
                 "whales": []})
    profiles = {s: prof(s, n) for s, n, *_rest in _BRK_Q2_CHANGES}
    profiles["BRK-A"] = prof("BRK-A", "Berkshire Hathaway Inc.", cik=BERKSHIRE)
    return club, sb, FakeFMP(profiles=profiles)


@pytest.mark.parametrize("stz_previous_value, exit_shown", [
    (None, False),              # Constellation Brands is not on the stored previous book: unknown → below
    (95_000_000.0, False),      # (illustrative) below the $149.6M floor of a $299B book
    (160_000_000.0, True),      # (illustrative) above it
], ids=["exit_value_unknown", "exit_immaterial", "exit_material"])
@pytest.mark.asyncio
async def test_the_berkshire_live_shape_drops_the_immaterial_new_holding(monkeypatch, stz_previous_value, exit_shown):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, fmp = _brk_world(stz_previous_value)
    got = await run("thirteen_f", fmp, club=club, sb=sb, run_date=date(2026, 8, 18))
    (rec,) = got.records
    assert A._material_floor(_BRK_Q2_TOTAL) == pytest.approx(149_626_778.123)
    moves = [(m.company.symbol, m.move) for m in rec.moves]
    assert ("DHI", "newly_reported") not in moves
    assert moves == ([("STZ", "no_longer_reported")] if exit_shown else []) + [
        ("DAL", "increased"), ("M", "increased"), ("COF", "decreased"), ("KR", "decreased"), ("NUE", "decreased")]
    assert got.rejections == {"move_immaterial": 1 if exit_shown else 2}
    assert all("DHI" not in req for req in fmp.profile_requests)            # dropped before any profile
    assert dict(rec.counts) == _BRK_Q2_COUNTS                                 # the filing's own counts


@pytest.mark.parametrize("move, total, kept", [
    # The live ARK Invest 2026-Q2 book ($15.4B: a $7.70M floor).
    ({"symbol": "OCTV", "move": "newly_reported", "shares": 8_897.0, "value": 145_021.0}, 15_402_068_542.0, False),
    ({"symbol": "HONA", "move": "newly_reported", "shares": 23_262.0, "value": 5_142_763.0}, 15_402_068_542.0, False),
    ({"symbol": "SNOW", "move": "newly_reported", "shares": 269_539.0, "value": 68_597_731.0}, 15_402_068_542.0,
     True),
    # The $5M floor of a small book, at the boundary (reaching it counts).
    ({"symbol": "AAA", "move": "newly_reported", "shares": 1.0, "value": 5_000_000.0}, 1e9, True),
    ({"symbol": "AAA", "move": "newly_reported", "shares": 1.0, "value": 4_999_999.0}, 1e9, False),
    # 0.05% of a large book, at the boundary.
    ({"symbol": "AAA", "move": "newly_reported", "shares": 1.0, "value": 15_000_000.0}, 30e9, True),
    ({"symbol": "AAA", "move": "newly_reported", "shares": 1.0, "value": 14_999_999.0}, 30e9, False),
    # An exit is valued on the PREVIOUS book; an unknown or unusable value is below the floor.
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": 15_000_000.0}, 30e9, True),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": 14_999_999.0}, 30e9, False),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": None}, 30e9, False),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": float("nan")}, 30e9, False),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": "15e6"}, 30e9, False),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0, "prev_value": True}, 30e9, False),
    ({"symbol": "AAA", "move": "no_longer_reported", "prev_shares": 9.0}, 30e9, False),
    # An exit's CURRENT value (none) never counts; a new holding's previous value never does.
    ({"symbol": "AAA", "move": "newly_reported", "shares": 1.0, "value": 1e6, "prev_value": 1e9}, 30e9, False),
    # Increases and decreases are not floored (`move_too_small` is their gate).
    ({"symbol": "AAA", "move": "increased", "shares": 200.0, "prev_shares": 100.0, "value": 1e6}, 30e9, True),
    ({"symbol": "AAA", "move": "decreased", "shares": 50.0, "prev_shares": 100.0, "value": 1e6}, 30e9, True),
], ids=["ark_octave", "ark_honeywell_aerospace", "ark_snowflake", "five_million", "under_five_million",
        "share_of_book", "under_share_of_book", "exit_at_floor", "exit_under", "exit_unknown", "exit_nan",
        "exit_string", "exit_bool", "exit_missing", "new_reads_its_own_value", "increase", "decrease"])
def test_the_materiality_floor(move, total, kept):
    ctx = _share_ctx()
    got = A._pre_gate_move(ctx, {"name": "", **move}, total)
    assert (got is not None) is kept
    assert dict(ctx.rejections) == ({} if kept else {"move_immaterial": 1})


def _amended_prev(cik, rows_over):
    """A 13F-HR/A of the previous quarter (a later accession) restating rows of it."""
    acc = "0000950123-26-000202"
    return [xrow(cik, 2, sym, cusip, shares, value, filingDate="2026-08-20", acceptedDate="2026-08-20 16:00:00",
                 link=f"https://www.sec.gov/Archives/edgar/data/1/{acc.replace('-', '')}/{acc}-index.htm")
            for sym, cusip, shares, value in rows_over]


@pytest.mark.parametrize("q2_over, q3_crwv_value, kept, immaterial", [
    ({}, 50e6, ["CRWV", "OXY", "AAPL"], 0),                                   # the default book
    ({"OXY": 4e6}, 50e6, ["CRWV", "AAPL"], 1),                                # the exit was a $4M position
    ({}, 4e6, ["OXY", "AAPL"], 1),                                            # a $4M new holding
    ({"restated_OXY": 4e6}, 50e6, ["CRWV", "AAPL"], 1),                       # a 13F-HR/A restated it to $4M
], ids=["default", "small_exit", "small_new_holding", "previous_quarter_amended"])
@pytest.mark.asyncio
async def test_a_registry_filers_moves_are_valued_on_the_builders_own_books(q2_over, q3_crwv_value, kept, immaterial):
    """An exit's previous value is read from the previous extract by CUSIP, normalised exactly as
    the builder normalised it for its diff (the latest accession wins)."""
    extracts = book(BERKSHIRE)
    q3, q2 = extracts[(BERKSHIRE, 2026, 3)], extracts[(BERKSHIRE, 2026, 2)]
    for r in q3:
        if r["symbol"] == "CRWV":
            r["value"] = q3_crwv_value
    for r in q2:
        if r["symbol"] == "OXY" and "OXY" in q2_over:
            r["value"] = q2_over["OXY"]
    if "restated_OXY" in q2_over:
        q2 += _amended_prev(BERKSHIRE, [("OXY", "674599105", 2_000_000, q2_over["restated_OXY"])])
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")], extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [m.company.symbol for m in got.records[0].moves] == kept
    assert got.rejections.get("move_immaterial", 0) == immaterial


def test_the_previous_values_are_the_builders_normalisation():
    q2 = book(BERKSHIRE)[(BERKSHIRE, 2026, 2)]
    split = [xrow(BERKSHIRE, 2, "KO", "191216100", 400_000, 30e6), xrow(BERKSHIRE, 2, "KO", "191216100", 600_000, 40e6)]
    rows = [r for r in q2 if r["symbol"] != "KO"] + split + [
        xrow(BERKSHIRE, 2, "OXY", "674599105", 9, 9e9, putCallShare="Put"),          # an option row: never a position
        xrow(BERKSHIRE, 2, "ZZZ", "000000AA1", 5, 5e9, date="2025-12-31")]           # another period: excluded
    prev_end = A._tc_rules.quarter_end(2026, 2)
    assert A._previous_values(rows, cik=BERKSHIRE, prev_end=prev_end) == {
        "037833100": 200e6, "191216100": 70e6, "674599105": 100e6}
    assert A._previous_values(None, cik=BERKSHIRE, prev_end=prev_end) == {}
    assert A._previous_values(rows + _amended_prev(BERKSHIRE, [("OXY", "674599105", 2_000_000, 4e6)]),
                              cik=BERKSHIRE, prev_end=prev_end)["674599105"] == 4e6


@pytest.mark.parametrize("previous, error, shown", [
    ([{"cik": "0001045810", "period": "2026-Q2", "holdings": [{"symbol": "RXRX", "value": 35e6}]}], None, True),
    ([{"cik": "0001045810", "period": "2026-Q2",
       "holdings": json.dumps([{"symbol": "RXRX", "value": 20e6}, {"symbol": "RXRX", "value": 15e6}])}],
     None, True),                                                      # JSONB as text; two CUSIPs, one symbol
    ([{"cik": "0001045810", "period": "2026-Q2", "holdings": [{"symbol": "RXRX", "value": 4e6}]}], None, False),
    ([{"cik": "0001045810", "period": "2026-Q1", "holdings": [{"symbol": "RXRX", "value": 35e6}]}], None, False),
    ([], None, False),                                                 # no stored previous build
    ([{"cik": "0001045810", "period": "2026-Q2", "holdings": "not json"}], None, False),
    (None, RuntimeError("supabase down"), False),                      # unreadable: a WARNING, never the series
], ids=["material", "json_text_summed", "immaterial", "other_period", "missing", "garbage", "read_fails"])
@pytest.mark.asyncio
async def test_a_club_exit_is_valued_on_the_stored_previous_build(monkeypatch, caplog, previous, error, shown):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb = club_world()
    if error is not None:
        sb.errors["trillion_club_filings"] = error
    else:
        sb.tables["trillion_club_filings"] = previous
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    unreadable = not previous or previous[0]["period"] != "2026-Q2" or previous[0]["holdings"] == "not json"
    if unreadable:
        # Review round 9 (#8): no previous book → no share-class tie for ANY move → the filer is
        # refused (fail closed), not published with its exits merely unvalued.
        assert got.records == () and got.rejections == {"filer_unavailable": 1}
        assert "filer refused" in caplog.text and "http" not in caplog.text
        return
    assert ("RXRX" in [m.company.symbol for m in got.records[0].moves]) is shown
    assert got.rejections.get("move_immaterial", 0) == (0 if shown else 1)


# ── review round 7: a Money Map that is mostly "Other" (live L2), segment labels (live L3) ──


def _mm_world(monkeypatch, sources, *, revenue, net, symbol="MSFT", fy="2026", date_="2026-06-30"):
    monkeypatch.setattr(R, "MONEY_MAP_SEED", (symbol,))
    bd = breakdown(symbol=symbol, fiscal_year=fy, revenue_sources=sources, reported_revenue=revenue,
                   net_income=net, cost_of_sales=None, operating_expense=None)
    pp = profit(point={"period": fy, "net_margin": 100.0 * net / revenue})
    fmp = FakeFMP(profiles={symbol: prof(symbol, "Microsoft Corporation", exchange="NASDAQ")},
                  income={symbol: [{"fiscalYear": fy, "date": date_, "reportedCurrency": "USD", "revenue": revenue,
                                    "netIncome": net}]})
    world = dict(revenue=FakeService({symbol: bd}), profit=FakeService({symbol: pp}), facts=facts_fn(),
                 sb=FakeSB({"trending_themes": []}))
    return fmp, world


#: Microsoft FY2026 as the live preview drew it: "Other" $143.7B (43% of revenue) above the
#: largest named segment ($129.4B).
_MSFT_FY2026 = [src("Server Products And Cloud Services", 129_425e6), src("XBOX", 21_790e6),
                src("Linked In Corporation", 19_817e6), src("Windows", 17_084e6), src("Other", 143_723e6)]


@pytest.mark.parametrize("sources, revenue, refused", [
    (_MSFT_FY2026, 331_839e6, True),
    # Below the largest segment but over 30% of revenue.
    ([src("A Segment", 400e9), src("B Segment", 200e9), src("C Segment", 90e9), src("Other", 310e9)], 1000e9, True),
    # Under 30% of revenue but above the largest named segment.
    ([src("A Segment", 100e9), src("B Segment", 95e9), src("C Segment", 90e9), src("D Segment", 85e9),
      src("E Segment", 80e9), src("Other", 105e9)], 555e9, True),
    # Unnamed revenue (the gap folded into "Other") counts like an "Other" line.
    ([src("A Segment", 400e9), src("B Segment", 200e9), src("C Segment", 90e9)], 1000e9, True),
    # Controls: 29% and under the largest; exactly the largest is not "larger".
    ([src("A Segment", 400e9), src("B Segment", 200e9), src("C Segment", 110e9), src("Other", 290e9)], 1000e9, False),
    ([src("A Segment", 300e9), src("B Segment", 250e9), src("C Segment", 160e9), src("Other", 290e9)], 1000e9, False),
    ([src("A Segment", 290e9), src("B Segment", 250e9), src("C Segment", 170e9), src("Other", 290e9)], 1000e9, False),
], ids=["msft_fy2026_live", "over_thirty_percent", "over_the_largest", "unnamed_gap", "control",
        "control_close", "equal_to_the_largest"])
@pytest.mark.asyncio
async def test_a_money_map_mostly_other_is_refused(monkeypatch, sources, revenue, refused):
    net = 0.4 * revenue
    fmp, world = _mm_world(monkeypatch, sources, revenue=revenue, net=net)
    got = await run("money_map", fmp, **world)
    if refused:
        assert got.records == () and got.rejections == {"money_map_mostly_other": 1}
    else:
        (rec,) = got.records
        assert rec.other_usd is not None and got.rejections == {}


@pytest.mark.asyncio
async def test_a_mostly_other_map_is_a_content_refusal_for_the_caps(monkeypatch):
    """It never uses up an upstream attempt: three at the head of the pool still let AAPL through."""
    seed = ("QA", "QB", "QC", "AAPL")
    fmp, world = _money_pool(monkeypatch, seed, mostly_other=seed[:3])
    got = await run("money_map", fmp, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert got.rejections == {"money_map_mostly_other": 3} and world["revenue"].calls == list(seed)


@pytest.mark.asyncio
async def test_fmp_segment_labels_are_shown_as_the_company_writes_them(monkeypatch):
    sources = [src("Intelligent Cloud", 150e9), src("XBOX", 100e9), src("Linked In Corporation", 80e9),
               src("Service", 60e9), src("Services", 5e9), src("Other", 5e9)]
    fmp, world = _mm_world(monkeypatch, sources, revenue=400e9, net=100e9)
    (rec,) = (await run("money_map", fmp, **world)).records
    assert [s.name for s in rec.segments] == ["Intelligent Cloud", "Xbox", "LinkedIn", "Services"]
    assert rec.other_usd == pytest.approx(10e9)       # "Services" after "Service" → "Services": a duplicate


# ── review round 6 (2026-10-09): a co- title only in other: ──


@pytest.mark.parametrize("co_title", ["director, other: Co-CEO", "other: Co-Chief Executive Officer",
                                      "director, 10 percent owner, other: co CEO"])
@pytest.mark.parametrize("where", [{}, {"sym": "BF-A"}], ids=["same_symbol", "other_class_same_cik"])
@pytest.mark.asyncio
async def test_a_co_ceo_title_only_in_other_drops_the_symbols_ceo(co_title, where):
    """Round 5 integration probe: a co-CEO whose title sits only in `other:` (no Officer box) is
    never a CEO line, so the partner filing as plain "officer: Chief Executive Officer" was
    published as THE CEO. The walk's raw rows are read for a co- title in ANY role text: the
    symbol's (and issuer CIK's) CEO lines are dropped, and so is the co-titled person."""
    bf = issuer_cik("BF")
    co = irow(**{"sym": "BF-B", **where}, name="BOND JAMES", cik="0000000999", title=co_title, shares=14_000,
              price=30.0, companyCik=bf)
    ceo = irow(sym="BF-B", title="officer: Chief Executive Officer", shares=26_000, price=30.0, companyCik=bf)
    profiles = {**week_profiles(), **{s: _brown_forman(s) for s in ("BF-A", "BF-B")}}
    got = await run("ceo_buys", FakeFMP(insider=[co, ceo] + week_rows()[2:], profiles=profiles))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "ambiguous_ceo": 1}
    # Control: without the co- row the plain CEO is published as the CEO.
    A.clear_memo()
    got = await run("ceo_buys", FakeFMP(insider=[ceo] + week_rows()[2:], profiles=profiles))
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX", "BF-B"]
    assert got.rejections == {"warrant_unit_right": 1}


@pytest.mark.asyncio
async def test_a_co_title_only_in_other_drops_the_person_and_the_same_role_in_insider_buys():
    """The insider_buys side of the probe: "director, other: Co-CEO" is a director line, refused as
    a person; "director, other: co-CFO" also drops the symbol's CFO; another director stays."""
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director, other: Co-CEO",
                 shares=20_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="DOE JANE", cik="0000000555", title="director", shares=5_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.amount_usd, got.rejections) == ("director", pytest.approx(150_000.0), {"ambiguous_ceo": 1})
    A.clear_memo()
    rows = [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director, other: co-CFO",
                 shares=20_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="ROE RICHARD", cik="0000000444", title="officer: Chief Financial Officer",
                 shares=15_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="DOE JANE", cik="0000000555", title="director", shares=5_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.amount_usd, got.rejections) == ("director", pytest.approx(150_000.0), {"ambiguous_ceo": 2})


# Money Map: a record held is never thrown away, and never risked on more reads.

def _slow_money(monkeypatch, seed, *, gross=(), per_lookup):
    """`_money_pool` on a fake clock: each breakdown read (with its record stage) takes ``per_lookup``."""
    fmp, world = _money_pool(monkeypatch, seed, gross=gross)
    clock = {"now": 0.0}
    inner = world["revenue"]

    class Slow:
        calls = inner.calls
        by_symbol = inner.by_symbol

        async def get_revenue_breakdown(self, sym):
            clock["now"] += per_lookup
            return await inner.get_revenue_breakdown(sym)

    world["revenue"] = Slow()
    deps = A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"], **world)
    return deps, world["revenue"]


async def _money_run(deps):
    return await A.candidates("money_map", run_date=RUN, exclude=frozenset(), limit=5, deadline=45.0, deps=deps)


GH = tuple(f"G{c}" for c in "ABCDEFGH")                                  # eight refused seeds


@pytest.mark.asyncio
async def test_a_record_then_refusals_stops_at_the_old_total_on_a_slow_clock(monkeypatch):
    """rr lens 0 #1 probe: AAPL qualifies, then eight gross stacks. The loop kept reading after the
    record (9 breakdowns at 5 s each) and ran out of the 45 s budget — AAPL was thrown away."""
    deps, revenue = _slow_money(monkeypatch, ("AAPL",) + GH + ("MSFT",), gross=GH, per_lookup=5.0)
    got = await _money_run(deps)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert revenue.calls == ["AAPL", "GA", "GB"] and got.rejections == {"money_map_inconsistent": 2}


@pytest.mark.asyncio
async def test_out_of_budget_with_a_record_held_returns_the_record(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    deps, revenue = _slow_money(monkeypatch, ("AAPL",) + GH, gross=GH, per_lookup=20.0)
    got = await _money_run(deps)          # AAPL at 20 s, GA refused at 40 s, GB's record stage: no time
    assert [r.company.symbol for r in got.records] == ["AAPL"] and got.skip_reason is None
    assert revenue.calls == ["AAPL", "GA", "GB"] and got.rejections == {"money_map_inconsistent": 1}
    assert "budget exhausted at GB with 1 record(s) held" in caplog.text and "refusals=1" in caplog.text


@pytest.mark.asyncio
async def test_out_of_budget_or_an_outage_without_a_kept_record_still_raises(monkeypatch):
    deps, _ = _slow_money(monkeypatch, GH, gross=GH, per_lookup=20.0)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await _money_run(deps)
    assert err.value.reason == "budget_exhausted"
    # An upstream failure is the series' answer even with a record held (only the budget is not).
    A.clear_memo()
    deps, revenue = _slow_money(monkeypatch, ("AAPL", "MSFT"), per_lookup=1.0)
    revenue.by_symbol["MSFT"] = FMPUnavailableException("segmentation down")
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await _money_run(deps)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.parametrize("seed, gross, kept, reads", [
    # A record, a refusal, a record: three in all — the old cap, not a fourth read.
    (("AAPL", "GA", "MSFT", "NVDA"), ("GA",), ["AAPL", "MSFT"], ["AAPL", "GA", "MSFT"]),
    # Refusals before the first record are free (round 2); once AAPL is held the total is over.
    (("GA", "GB", "GC", "AAPL", "MSFT"), ("GA", "GB", "GC"), ["AAPL"], ["GA", "GB", "GC", "AAPL"]),
], ids=["record_refusal_record", "gross_head_then_record"])
@pytest.mark.asyncio
async def test_a_held_record_brings_back_the_total_attempt_cap(monkeypatch, seed, gross, kept, reads):
    fmp, world = _money_pool(monkeypatch, seed, gross=gross)
    got = await run("money_map", fmp, **world)
    assert [r.company.symbol for r in got.records] == kept and world["revenue"].calls == reads


def test_every_segment_override_is_drawn_by_the_adapter():
    """Each override value passes the adapter's own segment gates (`_segments` draws it)."""
    for shown in R.SEGMENT_DISPLAY_OVERRIDES.values():
        assert A._SEGMENT_NAME_RE.fullmatch(shown) and shown.strip().lower() not in A._MONEY_MAP_REST_NAMES


# ── review round 8 (2026-10-10): sitting-officer titles (rr lens 0 #3/#4) ─────

#: lens 0 #3's probes and their neighbours: titles the round-7 denylist let through as THE CEO /
#: CFO. The shared rule (Home's) still keeps most of them; the allow-list refuses every one.
_NOT_SITTING_TITLES = {
    "ceo_buys": ("officer: Previously Chief Executive Officer", "officer: Chief Executive Officer (Resigned)",
                 "officer: CEO until 12/31/2026", "officer: Chief Executive Officer (effective 1/1/2027)",
                 "officer: CEO Nominee", "officer: Departing CEO", "officer: CEO through 2026",
                 "officer: CEO, pending approval", "officer: CEO-to-be", "officer: Nominated CEO",
                 "officer: CEO (Resigning)", "officer: Chief Executive Officer as of 11/1/2026",
                 "officer: CEO 2019-2026", "officer: CEO & Corporate Secretary"),
    "insider_buys": ("officer: CFO (previously)", "officer: Chief Financial Officer (Resigned)",
                     "officer: CFO until 12/31/2026", "officer: CFO Nominee", "officer: Departing CFO",
                     "officer: CFO effective 1/1/2027", "officer: CFO & Chief Operating Officer"),
}


def _not_sitting_cases():
    for series, titles in _NOT_SITTING_TITLES.items():
        for title in titles:
            yield series, title


@pytest.mark.parametrize("series, title", list(_not_sitting_cases()))
@pytest.mark.asyncio
async def test_a_title_off_the_sitting_officer_list_is_never_published(series, title):
    got, published, fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    assert [r.company.symbol for r in published] == ["GME"]
    kept_by_shared_rule = insider_role(title) == _SERIES_ROLE[series]
    assert got.rejections == ({"role_uncertain": 1} if kept_by_shared_rule else {})
    assert "FOX" not in fmp.profile_requests[0]               # refused in the walk, before any profile


def test_the_shared_rule_keeps_most_of_those_titles():
    """Anti-vacuity: the probes reach the adapter's check (the shared rule calls them the CEO /
    CFO), so only the allow-list refuses them."""
    kept = [t for s, t in _not_sitting_cases() if insider_role(t) == _SERIES_ROLE[s]]
    assert len(kept) >= 15, kept


#: Real sitting titles (SEC Form 4 officer titles as companies write them): each is published.
_SITTING_TITLES = {
    "ceo_buys": ("officer: President and CEO", "officer: Chairman & Chief Executive Officer",
                 "director, officer: Founder, CEO and Director", "officer: Interim CEO",
                 "officer: Chief Executive Officer", "director, officer: President & Chief Executive Officer",
                 "director, officer: Chairman, President and CEO", "officer: Co-Founder & CEO",
                 "officer: CEO and Chairman of the Board", "officer: Executive Chairman and CEO",
                 "officer: Acting Chief Executive Officer", "officer: CEO & Chairwoman",
                 "director, officer, other: President & CEO", "officer: CEO/President",
                 "officer: Chief Executive Officer and Director", "officer: Chairperson and CEO"),
    "insider_buys": ("officer: EVP & Chief Financial Officer", "officer: CFO and Treasurer",
                     "officer: Chief Financial Officer", "officer: Senior Vice President and Chief Financial Officer",
                     "officer: SVP, Chief Financial Officer", "officer: Interim CFO",
                     "officer: Executive Vice President, CFO and Treasurer",
                     "officer: Chief Financial Officer and Principal Accounting Officer",
                     "officer: Sr. VP & CFO", "officer: EVP, Finance and CFO", "officer: VP and CFO",
                     "director, officer: Chief Financial Officer"),
}


@pytest.mark.parametrize("series, title", [(s, t) for s, ts in _SITTING_TITLES.items() for t in ts])
@pytest.mark.asyncio
async def test_a_sitting_officer_title_is_published(series, title):
    assert insider_role(title) == _SERIES_ROLE[series]               # the line is extracted at all
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert fox.role == _SERIES_ROLE[series] and got.rejections == {}


@pytest.mark.parametrize("other_title, refused", [
    ("officer: Former CEO", True),                         # lens 0 #4: the F-coded Form 4 of the same week
    ("director, officer: CEO (Resigned)", True),
    ("director", True),                                    # a same-week filing as a non-officer
    ("10 percent owner", True),
    ("officer: President", True),                          # an officer title that is not the CEO's
    ("director, officer: Chief Executive Officer", False),  # control: the same title
    (None, False),                                         # no role text: no evidence
])
@pytest.mark.parametrize("ident", [{}, {"reportingCik": None, "reportingName": "John Smith"}],
                         ids=["same_cik", "name_only_first_last"])
@pytest.mark.asyncio
async def test_the_per_issuer_read_checks_every_title_of_the_person(other_title, refused, ident):
    """rr lens 0 #4: the CEO's P lines say "Chief Executive Officer", and FOX's per-issuer read
    also holds his F-coded Form 4 of the same week — which the P-only walk never returns. Every
    title of the person there must be the sitting CEO's, matched on any identity key (a name in
    first-last order included)."""
    f_row = {**_line(_FOX_CEO, tx="F-InKind", filed="2026-11-13", shares=3_000), **ident,
             "typeOfOwner": other_title}
    got, published, fmp = await _fox_week("ceo_buys", [_line(_FOX_CEO), f_row])
    assert fmp.calls["insider_issuer"] == 2                 # the F row reached only the per-issuer read
    if refused:
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}
    else:
        assert "FOX" in [r.company.symbol for r in published] and got.rejections == {}


@pytest.mark.parametrize("row_over, refused", [
    ({"typeOfOwner": "director"}, True),                                  # the same issuer: a non-officer filing
    ({"typeOfOwner": "officer: Former CEO", "symbol": "FOXA"}, True),     # the issuer's other class (by CIK)
    ({"typeOfOwner": "director", "symbol": "ZZZ", "companyCik": issuer_cik("ZZZ")}, False),  # another issuer
    ({"typeOfOwner": "director", "filed": "2026-11-05"}, False),          # filed before the window opened
    ({"typeOfOwner": ""}, False),                                         # no role text
], ids=["same_issuer", "other_class_same_cik", "another_issuer", "before_the_window", "blank"])
@pytest.mark.asyncio
async def test_the_walk_checks_the_persons_other_rows_on_the_issuer(row_over, refused):
    """A P row of the same CEO elsewhere in the walk (another security, another filing): on the
    same issuer it must be a sitting CEO's title too; a seat on ANOTHER company's board is not
    this company's role."""
    over = dict(row_over)
    filed = over.pop("filed", "2026-11-12")
    other = {**_line(_FOX_CEO, filed=filed, shares=2_000, traded=filed if filed < "2026-11-09" else "2026-11-10",
                     sec="Series A Preferred Stock"), **over}
    rows = [_line(_FOX_CEO), other]
    got, published, fmp = await _fox_week("ceo_buys", rows)
    if refused:
        assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}
        assert "FOX" not in fmp.profile_requests[0]           # refused in the walk, before any profile
    else:
        assert "FOX" in [r.company.symbol for r in published] and got.rejections == {}


# ── review rounds 8-9: the issuer read must cover what the walk saw (rr lens 0 #5; rr9 #2) ──

#: GME's CEO lines in `week_rows`: 11-12 (the oldest) and 11-13 (the NEWEST filing day).
_GME_NEWEST = dict(shares=50_000, price=25.5, traded="2026-11-11", filed="2026-11-13")


@pytest.mark.parametrize("feed, covered", [
    ([irow(filed="2026-10-20", traded="2026-10-19")], False),          # lens 0 #5: a stale read, an older row
    ([irow()], False),                                                 # rr9 #2: only the OLDEST published line
    ([irow(**{**_GME_NEWEST, "shares": 49_999})], False),             # the person, another size
    ([irow(**{**_GME_NEWEST, "filed": "2026-11-14"})], False),         # the person, another filing day
    ([irow(**_GME_NEWEST, name="DOE JANE", cik="0000000555")], False),  # the published size and day, another person
    ([irow(**_GME_NEWEST, sym="GMEX", companyCik=issuer_cik("GME"))], False),  # the line under another symbol
    ([irow(tx="F-InKind", shares=7_000, filed="2026-11-14")], False),  # this week's rows, none published
    ([irow(**_GME_NEWEST)], True),                                     # the newest day's line is enough
    ([irow(), irow(**_GME_NEWEST)], True),                             # every published line
    ([irow(**_GME_NEWEST, reportingCik=None, reportingName="Ryan Cohen")], True),   # keyed by name only
    ([irow(**{**_GME_NEWEST, "shares": "50000"})], True),             # a numeric string is a number
], ids=["stale", "only_the_oldest_line", "other_size", "other_day", "other_person", "other_symbol",
        "unpublished_rows", "newest_line", "every_line", "name_only", "string_shares"])
@pytest.mark.asyncio
async def test_an_issuer_read_without_the_persons_newest_lines_is_a_failed_check(feed, covered, caplog):
    """The per-issuer index can lag the market-wide one. An amendment is always filed AFTER the
    line it amends, so a read that stops before the newest line the walk saw cannot hold this
    week's 4/A either — `amendment_check_failed`, never "clean" (rr9 #2: "any one published line"
    accepted a read lagging a day, exactly where a 4/A would sit)."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    got = await run("ceo_buys", _amendment_week({"GME": feed}))
    shown = [r.company.symbol for r in got.records[0].rows]
    if covered:
        assert shown == ["GME", "FOX"] and got.rejections == {"warrant_unit_right": 1}
    else:
        assert shown == ["FOX"] and got.rejections == {"warrant_unit_right": 1, "amendment_check_failed": 1}
        assert "GME Form 4/A check read is missing 1 row(s) the walk saw" in caplog.text


@pytest.mark.parametrize("other, in_read, covered", [
    (dict(filed="2026-11-14"), False, False),       # the issuer's later P row the read stops before
    (dict(filed="2026-11-14"), True, True),
    (dict(filed="2026-11-16"), False, False),       # filed on the run day: the read runs through it
    (dict(filed="2026-11-12"), False, True),        # before the person's newest day: not required
    (dict(filed="2026-11-14", sym="GME-A", companyCik=issuer_cik("GME")), False, False),   # another class, same CIK
    (dict(filed="2026-11-14", companyCik=issuer_cik("ZZZ")), False, True),   # another issuer's row
], ids=["later_row_missing", "later_row_held", "run_day_row_missing", "older_row_missing",
        "other_class_row_missing", "another_issuers_row"])
@pytest.mark.asyncio
async def test_the_issuer_read_must_hold_every_walk_row_of_the_issuer_since_the_newest_day(other, in_read, covered):
    """rr9 #2 ("better still"): every walk row of the ISSUER — any person, any symbol under its CIK
    — filed on or after the person's newest day must be in the read; one missing is a read that
    stopped before what the walk saw."""
    row = irow(name="DOE JANE", cik="0000000555", title="director", shares=1_000, price=25.0, **other)
    walk = week_rows() + [row]
    read = [r for r in walk if A.normalize_cik(r.get("companyCik")) == A.normalize_cik(issuer_cik("GME"))
            and (in_read or r is not row)]
    fmp = FakeFMP(insider=walk, profiles=week_profiles(), issuer_feed={A.normalize_cik(issuer_cik("GME")): read})
    got = await run("ceo_buys", fmp)
    shown = [r.company.symbol for r in got.records[0].rows]
    if covered:
        assert shown == ["GME", "FOX"] and "amendment_check_failed" not in got.rejections
    else:
        assert shown == ["FOX"] and got.rejections["amendment_check_failed"] == 1


@pytest.mark.parametrize("answer, why", [
    ([irow(**_GME_NEWEST), irow(sym="ZZZ", name="DOE JANE", cik="0000000555")], "another issuer's row"),
    ([irow(**_GME_NEWEST, companyCik="0000000001")], "the line itself under another CIK"),
], ids=["foreign_row", "foreign_line"])
@pytest.mark.asyncio
async def test_an_issuer_read_holding_another_issuers_rows_is_a_failed_check(answer, why, caplog):
    """A ``companyCik`` read returns one issuer's rows; a row of another issuer means the filter was
    not applied (a market-wide answer) — nothing in it can vouch for the issuer: fail closed."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = _amendment_week(issuer_feed={A.normalize_cik(issuer_cik("GME")): answer})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"], why
    assert got.rejections == {"warrant_unit_right": 1, "amendment_check_failed": 1}
    assert "GME Form 4/A check read rows of 1 other issuer(s)" in caplog.text
    A.clear_memo()                                                       # control: the issuer's own rows
    fmp = _amendment_week(issuer_feed={A.normalize_cik(issuer_cik("GME")): [irow(**_GME_NEWEST)]})
    assert [r.company.symbol for r in (await run("ceo_buys", fmp)).records[0].rows] == ["GME", "FOX"]


# ── review round 8: an issuer's other share classes (rr lens 0 #1) ─────────────

_BF = issuer_cik("BF")


def _bf_week(*, a_rows=(), b_feed=None, a_feed=None, b_cik=_BF, extra_walk=()):
    """BF-B's CEO buys ($780,000); the walk also shows ``a_rows`` under BF-A. Per-ticker feeds
    default to the walk's rows of the symbol."""
    b = irow(sym="BF-B", shares=26_000, price=30.0, companyCik=b_cik)
    walk = [b, *a_rows, *extra_walk, *week_rows()[2:]]
    feed = {}
    if b_feed is not None:
        feed["BF-B"] = b_feed
    if a_feed is not None:
        feed["BF-A"] = a_feed
    profiles = {**week_profiles(), **{s: _brown_forman(s) for s in ("BF-A", "BF-B")}}
    return FakeFMP(insider=walk, profiles=profiles, symbol_feed=feed or None), b


_BF_A_DIRECTOR = irow(sym="BF-A", name="DOE JANE", cik="0000000555", title="director", shares=4_000, price=30.0,
                      companyCik=_BF)


@pytest.mark.asyncio
async def test_a_4a_under_the_issuers_other_class_in_the_walk_refuses_the_row():
    """lens 0 #1a: the CEO's P line on BF-B and his P-coded 4/A under BF-A (one issuer CIK). Both
    of his symbols are refused (one count per person and symbol)."""
    amend = irow(sym="BF-A", shares=14_000, price=30.0, companyCik=_BF, form="4/A", filed="2026-11-13")
    fmp, _b = _bf_week(a_rows=[amend])
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "amended_filing": 2}
    assert all("BF-B" not in req for req in fmp.profile_requests)       # refused before any profile


_BF_N = A.normalize_cik(_BF)


@pytest.mark.parametrize("walk_shows_class", [True, False], ids=["walk_shows_bf_a", "walk_shows_no_bf_a"])
@pytest.mark.parametrize("amend_symbol", ["BF-A", "", "BFB", "N/A"], ids=["other_class", "blank", "variant", "na"])
@pytest.mark.asyncio
async def test_a_non_p_4a_only_the_issuer_read_holds_refuses_the_row(walk_shows_class, amend_symbol):
    """rr9 #1 (CONFIRMED probe): BF-B's CEO buys $780,000; a J-coded 4/A — the CEO re-coding his
    purchase — is filed under BF-A (or a blank or variant symbol) with Brown-Forman's issuer CIK,
    so only a read of the ISSUER returns it. Round 8 read just the classes the P-only walk showed:
    with no BF-A row in the walk, BF-A was never read and BF-B was published. The read is by the
    profile's CIK now — every class at once — so the row is refused either way."""
    amend = irow(sym=amend_symbol, shares=14_000, price=30.0, companyCik=_BF, form="4/A", tx="J-Other",
                 filed="2026-11-13")
    a_rows = [_BF_A_DIRECTOR] if walk_shows_class else []
    fmp, b = _bf_week(a_rows=a_rows)
    fmp.issuer_feed = {_BF_N: [b, *a_rows, amend]}
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "amended_filing": 1}
    assert issuer_reads(fmp) == ["BF-A/BF-B", "FOX"]            # ONE read for the issuer, every class
    # Control: the same row filed as a plain Form 4 publishes BF-B.
    A.clear_memo()
    fmp, b = _bf_week(a_rows=a_rows)
    fmp.issuer_feed = {_BF_N: [b, *a_rows, {**amend, "formType": "4"}]}
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX", "BF-B"]
    assert got.rejections == {"warrant_unit_right": 1}


@pytest.mark.asyncio
async def test_a_former_ceo_title_under_the_other_class_refuses_the_ceo():
    """rr9 #1 (the same gap, role side): the person's "officer: Former CEO" row filed under the
    issuer's other class, which the walk never shows, is in the issuer read — `role_uncertain`."""
    former = irow(sym="BF-A", shares=1_000, price=30.0, companyCik=_BF, tx="F-InKind", filed="2026-11-13",
                  title="officer: Former CEO")
    fmp, b = _bf_week()
    fmp.issuer_feed = {_BF_N: [b, former]}
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "role_uncertain": 1}


@pytest.mark.parametrize("a_feed", [
    FMPUnavailableException("down"), [],
    [irow(sym="BF-A", name="ROE RICHARD", cik="0000000444", title="director", shares=1.0, companyCik=_BF)],
], ids=["read_fails", "empty", "stale_without_the_walks_row"])
@pytest.mark.asyncio
async def test_a_failed_or_stale_other_class_read_drops_the_row(a_feed, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, _b = _bf_week(a_rows=[_BF_A_DIRECTOR], a_feed=a_feed)
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "amendment_check_failed": 1}
    assert "BF-B Form 4/A check" in caplog.text


@pytest.mark.asyncio
async def test_every_share_class_is_one_issuer_read():
    """Round 8 read each other class the walk showed (at most three; past that the row was
    dropped). The issuer read covers every class at once: four other classes in the walk are one
    read, and the row stands."""
    classes = [f"BF{c}" for c in "CDEF"]
    noise = [irow(sym=sym, name="DOE JANE", cik="0000000555", title="director", shares=4_000, price=30.0,
                  companyCik=_BF) for sym in classes]
    fmp, _b = _bf_week(a_rows=noise)
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX", "BF-B"]
    assert got.rejections == {"warrant_unit_right": 1}
    assert issuer_reads(fmp) == ["BF-A/BF-B", "FOX"]
    assert not hasattr(A, "INSIDER_AMENDMENT_MAX_CLASS_SYMBOLS") and not hasattr(A, "_class_rows")


@pytest.mark.parametrize("amend_symbol", ["BF-A", ""], ids=["other_class", "blank_symbol"])
@pytest.mark.asyncio
async def test_a_row_whose_lines_name_no_cik_is_refused_on_its_profiles_issuer(amend_symbol):
    """BF-B's lines carry no issuer CIK, so the walk cannot tie them to a P-coded 4/A naming only
    the issuer CIK (under BF-A, or with a blank symbol) — the profile's CIK does: the profile stage
    refuses the row (one count), before any issuer read."""
    amend = irow(sym=amend_symbol, name="DOE JANE", cik="0000000555", title="director", shares=4_000, price=30.0,
                 companyCik=_BF, form="4/A")
    fmp, _b = _bf_week(a_rows=[amend], b_cik=None)
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX"]
    assert got.rejections == {"warrant_unit_right": 1, "amended_filing": 1}
    assert any("BF-B" in req for req in fmp.profile_requests)              # caught at the profile stage …
    assert issuer_reads(fmp) == ["FOX"]                                  # … before its issuer read
    A.clear_memo()
    fmp, b = _bf_week(a_rows=[{**amend, "formType": "4"}], b_cik=None)    # control
    # The issuer's own read holds BF-B's line under the issuer CIK (the walk's copy carries none).
    fmp.issuer_feed = {_BF_N: [dict(b, companyCik=_BF), {**amend, "formType": "4"}]}
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["FOX", "BF-B"]


def test_amended_issuers_hop_over_share_classes():
    rows = [irow(sym="BF-A", companyCik=_BF, form="4/A"),                  # the amendment: symbol + CIK
            irow(sym="BF-B", companyCik=_BF),                              # another class, same CIK
            irow(sym="GME", companyCik=None, form="4/A"),                  # symbol only
            irow(sym="GME", companyCik=issuer_cik("GME")),                 # GME's CIK, from another row
            irow(sym="", companyCik=issuer_cik("KO"), form="4/A"),         # blank symbol, CIK only
            irow(sym="KO", companyCik=issuer_cik("KO")),
            irow(sym="", companyCik=None, form="4/A"),                     # names no issuer: no one
            irow(sym="FOX", companyCik=issuer_cik("FOX"), form="4/A", filed="2026-11-05"),   # before the window
            irow(sym="ZZZ", companyCik=issuer_cik("ZZZ"), form="4/A", filingDate="soon")]     # unreadable date
    syms, ciks = A._amended_issuers(rows, date(2026, 11, 9))
    assert syms == {"BF-A", "BF-B", "GME", "KO", "ZZZ"}
    assert ciks == {A.normalize_cik(issuer_cik(s)) for s in ("BF", "GME", "KO", "ZZZ")}


# ── review round 8: 13F moves are chosen by SIZE; an exit carries its value (rr lens 1 #1/#2) ──


def _club_filer(*, total, changes, counts, previous, profiles):
    """One club 13F filer (NVIDIA's slot) with ``changes`` and its stored previous-quarter book."""
    club, sb = club_world(changes=[SimpleNamespace(**{"newly_listed": False, "shares": None, "prev_shares": None,
                                                      "value": None, **c}) for c in changes],
                          company_over={"total_value": total, "change_counts": SimpleNamespace(**counts)})
    sb.tables["trillion_club_filings"] = [{"cik": "0001045810", "period": "2026-Q2", "holdings": previous}]
    return club, sb, FakeFMP(profiles={**thirteen_f_profiles(), **profiles})


def _moves(rec):
    return [(m.company.symbol, m.move) for m in rec.moves]


@pytest.mark.asyncio
async def test_a_large_increase_and_exit_are_never_cut_by_nine_new_holdings(monkeypatch):
    """rr lens 1 #2 (the Tiger Global probe), through the real `_filing_record`: a $20B book, nine
    new positions of $25M-$60M, a 1M → 2M share increase on a $4B position (a $2B move) and a $3B
    exit. Kind order used to fill all 8 slots with new holdings."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    news = [f"T{c}" for c in "ABCDEFGHI"]
    changes = [dict(symbol=s, name=s, change="newly_reported", shares=1_000_000.0, value=25e6 + 4e6 * i)
               for i, s in enumerate(news)]                                   # $25M … $57M
    changes += [dict(symbol="INCR", name="INCR", change="increased", shares=2_000_000.0, prev_shares=1_000_000.0,
                     value=4e9),
                dict(symbol="GONE", name="GONE", change="no_longer_reported", prev_shares=5_000_000.0)]
    counts = dict(newly_reported=9, increased=1, decreased=0, no_longer_reported=1)
    profiles = {s: prof(s, f"{s} Holdings Inc.") for s in news + ["INCR", "GONE"]}
    club, sb, fmp = _club_filer(total=20e9, changes=changes, counts=counts,
                                previous=[{"symbol": "GONE", "value": 3e9}], profiles=profiles)
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    (rec,) = got.records
    assert len(rec.moves) == R.THIRTEEN_F_MAX_MOVES
    assert ("INCR", "increased") in _moves(rec) and ("GONE", "no_longer_reported") in _moves(rec)
    # The six largest new holdings fill the rest; the record keeps its kind order, largest first.
    assert _moves(rec) == [(s, "newly_reported") for s in ("TI", "TH", "TG", "TF", "TE", "TD")] + [
        ("GONE", "no_longer_reported"), ("INCR", "increased")]
    gone = next(m for m in rec.moves if m.company.symbol == "GONE")
    assert (gone.value_usd, gone.prev_value_usd) == (None, 3e9)
    assert all(m.prev_value_usd is None for m in rec.moves if m.move != "no_longer_reported")
    # Both reached the profile batch (10 presented + the filer's own ticker).
    assert {"INCR", "GONE"} <= set(fmp.profile_requests[0]) and len(fmp.profile_requests[0]) == 11


@pytest.mark.asyncio
async def test_a_material_exit_carries_its_previous_value_beside_a_small_increase(monkeypatch):
    """rr lens 1 #1 (the Berkshire-Citigroup probe): a $299B book, a Citigroup exit worth $3B on the
    previous book (above the $149.6M floor) and Occidental 1.0M → 1.15M shares on a $57.5M position
    (a $7.5M move). The exit's size travels in the record, so the template can lead with it."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    changes = [dict(symbol="C", name="Citigroup", change="no_longer_reported", prev_shares=40_000_000.0),
               dict(symbol="OXY", name="Occidental", change="increased", shares=1_150_000.0, prev_shares=1_000_000.0,
                    value=57.5e6)]
    counts = dict(newly_reported=0, increased=1, decreased=0, no_longer_reported=1)
    club, sb, fmp = _club_filer(total=299e9, changes=changes, counts=counts,
                                previous=[{"symbol": "C", "value": 3e9}], profiles={"C": prof("C", "Citigroup Inc.")})
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    (rec,) = got.records
    assert _moves(rec) == [("C", "no_longer_reported"), ("OXY", "increased")]
    citi, oxy = rec.moves
    assert (citi.value_usd, citi.prev_value_usd) == (None, 3e9)
    increase_size = oxy.value_usd * abs(oxy.shares - oxy.prev_shares) / oxy.shares
    assert increase_size == pytest.approx(7.5e6) and citi.prev_value_usd > increase_size
    sheet = R.fact_sheet(rec, rejections=got.rejections, selection={})
    assert R.record_from_fact_sheet(json.loads(json.dumps(sheet))) == rec       # it round-trips


@pytest.mark.asyncio
async def test_a_registry_exit_carries_its_previous_value():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    got = await run("thirteen_f", fmp, sb=sb)
    crwv, oxy, aapl = got.records[0].moves
    assert (oxy.move, oxy.value_usd, oxy.prev_value_usd) == ("no_longer_reported", None, 100e6)
    assert crwv.prev_value_usd is None and aapl.prev_value_usd is None


@pytest.mark.asyncio
async def test_large_decreases_reach_a_record_full_of_new_holdings(monkeypatch):
    """The ARK shape: many material new holdings and large decreases — each kind keeps its largest."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    news = [f"N{c}" for c in "ABCDEFGHIJ"]
    changes = [dict(symbol=s, name=s, change="newly_reported", shares=1e6, value=100e6 + 1e6 * i)
               for i, s in enumerate(news)]
    changes += [dict(symbol=f"D{c}", name=f"D{c}", change="decreased", shares=1e6, prev_shares=3e6, value=v)
                for c, v in (("A", 50e6), ("B", 40e6), ("C", 30e6), ("D", 20e6))]
    counts = dict(newly_reported=10, increased=0, decreased=4, no_longer_reported=0)
    profiles = {s: prof(s, f"{s} Holdings Inc.") for s in news + ["DA", "DB", "DC", "DD"]}
    club, sb, fmp = _club_filer(total=15e9, changes=changes, counts=counts, previous=[], profiles=profiles)
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    (rec,) = got.records
    # 8 slots, two kinds: each kind's top 3 reserved (new $109M/$108M/$107M; shares sold worth
    # $100M/$80M/$60M), then two more by size: new $106M and $105M beat the fourth decrease's $40M.
    assert _moves(rec) == [(s, "newly_reported") for s in ("NJ", "NI", "NH", "NG", "NF")] + [
        ("DA", "decreased"), ("DB", "decreased"), ("DC", "decreased")]


def _mv(sym, move, size):
    m = {"symbol": sym, "move": move, "value": None, "prev_value": None, "flow": 0.0}
    m[{"newly_reported": "value", "no_longer_reported": "prev_value"}.get(move, "flow")] = size
    return m


@pytest.mark.parametrize("limit, picked", [
    # 4 kinds × 4 moves; every kind's largest first, then every kind's second, …, then by size.
    (4, ["N1", "X1", "I1", "D1"]),
    (8, ["N1", "X1", "I1", "D1", "N2", "X2", "I2", "D2"]),
    (10, ["N1", "X1", "I1", "D1", "N2", "X2", "I2", "D2", "N3", "X3"]),
    (13, ["N1", "X1", "I1", "D1", "N2", "X2", "I2", "D2", "N3", "X3", "I3", "D3", "N4"]),
    (2, ["N1", "X1"]),                                  # fewer slots than kinds: the largest tops
    (0, []),
])
def test_select_moves_reserves_each_kinds_largest_then_fills_by_size(limit, picked):
    sizes = {"newly_reported": 100.0, "no_longer_reported": 90.0, "increased": 80.0, "decreased": 70.0}
    tag = {"newly_reported": "N", "no_longer_reported": "X", "increased": "I", "decreased": "D"}
    moves = [_mv(f"{tag[k]}{i}", k, base - i) for k, base in sizes.items() for i in range(1, 5)]
    got = A._select_moves(moves, limit)
    assert sorted(m["symbol"] for m in got) == sorted(picked)
    sizes_out = [A._move_size(m) for m in got]
    assert sizes_out == sorted(sizes_out, reverse=True)                 # returned largest first


def test_select_moves_fills_by_size_once_each_kind_is_reserved():
    big_increase = _mv("INC", "increased", 2e9)
    news = [_mv(f"N{i}", "newly_reported", 60e6 - i) for i in range(9)]
    exit_ = _mv("EXIT", "no_longer_reported", 3e9)
    got = [m["symbol"] for m in A._select_moves(news + [big_increase, exit_], 8)]
    assert got == ["EXIT", "INC", "N0", "N1", "N2", "N3", "N4", "N5"]
    assert A._select_moves([], 8) == []
    # Ties are deterministic: kind order, then symbol.
    tie = [_mv("B", "increased", 5.0), _mv("A", "decreased", 5.0), _mv("C", "newly_reported", 5.0)]
    assert [m["symbol"] for m in A._select_moves(tie, 3)] == ["C", "B", "A"]


@pytest.mark.asyncio
async def test_exits_are_ordered_by_their_previous_value(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    changes = [dict(symbol=s, name=s, change="no_longer_reported", prev_shares=1e6) for s in ("XA", "XB", "XC")]
    counts = dict(newly_reported=0, increased=0, decreased=0, no_longer_reported=3)
    club, sb, fmp = _club_filer(total=5e9, changes=changes, counts=counts,
                                previous=[{"symbol": "XA", "value": 10e6}, {"symbol": "XB", "value": 90e6},
                                          {"symbol": "XC", "value": 40e6}],
                                profiles={s: prof(s, f"{s} Holdings Inc.") for s in ("XA", "XB", "XC")})
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert [(m.company.symbol, m.prev_value_usd) for m in got.records[0].moves] == [
        ("XB", 90e6), ("XC", 40e6), ("XA", 10e6)]


# ── review round 8: a leading "The" (live L1 Kroger) ───────────────────────────

@pytest.mark.asyncio
async def test_the_kroger_co_is_shown_as_kroger(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, fmp = _brk_world(None)
    fmp.profiles["KR"] = prof("KR", "The Kroger Co.")
    got = await run("thirteen_f", fmp, club=club, sb=sb, run_date=date(2026, 8, 18))
    names = {m.company.symbol: m.company.name for m in got.records[0].moves}
    assert names["KR"] == "Kroger"


# ══ Drop 2b (2026-10-10): congress_count, company_stakes, earnings, theme_explainer ══════════


class FakeFMP2b(FakeFMP):
    """`FakeFMP` plus the Congress feeds and the earnings calendar. A chamber's answer is a list,
    an exception, or ``fn(limit, call_no)``; ``calendar`` maps an ISO day to its rows (or an
    exception); a day not listed answers ``[]``."""

    def __init__(self, *, senate: Any = None, house: Any = None,
                 calendar: Optional[Dict[str, Any]] = None, currency: Optional[Dict[str, Any]] = None,
                 **kw) -> None:
        super().__init__(**kw)
        self.senate, self.house = senate, house
        self.calendar = calendar or {}
        self.currency = currency or {}
        self.congress_calls: List[Any] = []
        self.calendar_calls: List[Any] = []
        self.currency_calls: List[Any] = []

    def _congress(self, chamber: str, spec: Any, limit: int) -> Any:
        n = sum(1 for c, _l in self.congress_calls if c == chamber)
        self.congress_calls.append((chamber, limit))
        self.calls["congress"] += 1
        return self._answer(spec(limit, n) if callable(spec) else spec)

    async def get_senate_latest(self, limit=1000):
        return self._congress("senate", self.senate, limit)

    async def get_house_latest(self, limit=1000):
        return self._congress("house", self.house, limit)

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls["calendar"] += 1
        self.calendar_calls.append((from_date, to_date))
        return self._answer(self.calendar.get(from_date, []))

    async def get_income_statement(self, sym, period="annual", limit=10):
        """The earnings currency read (review round 9): a QUARTERLY statement answers from
        ``currency`` — a currency code (one row in it), a whole answer (a list), or an exception —
        "USD" for a symbol not listed; an annual one is `FakeFMP`'s."""
        if period != "quarter":
            return await super().get_income_statement(sym, period, limit)
        self.calls["currency"] += 1
        self.currency_calls.append((sym, limit))
        spec = self.currency.get(sym, "USD")
        if isinstance(spec, str):
            spec = [{"symbol": sym, "date": "2026-12-31", "period": "Q4", "reportedCurrency": spec}]
        return self._answer(spec)


# ── congress_count ────────────────────────────────────────────────────────────

CONGRESS_RUN = date(2026, 12, 8)          # the December Congress Tuesday; disclosure month 2026-11
CONGRESS_KEY = "news:congress_count:2026-11"
# The recorded row shapes (2026-10-09 probe, `company-weekly-congress-probe/*_scrubbed.json`): the
# exact key sets and order of each chamber's `*-latest` rows.
_SENATE_KEYS = ("symbol", "senateID", "disclosureDate", "transactionDate", "firstName", "lastName", "office",
                "district", "owner", "assetDescription", "assetType", "type", "amount", "comment", "link")
_HOUSE_KEYS = _SENATE_KEYS[:13] + ("capitalGainsOver200USD",) + _SENATE_KEYS[13:]
#: Canary identities: none may reach a record, a fact sheet, a log line or the memo.
_MEMBERS = {
    "s1": ("ZQXFIRSTA", "ZQXLASTA"), "s2": ("ZQXFIRSTB", "ZQXLASTB"), "s3": ("ZQXFIRSTC", "ZQXLASTC"),
    "h1": ("ZQXFIRSTD", "ZQXLASTD"), "h2": ("ZQXFIRSTE", "ZQXLASTE"), "h3": ("ZQXFIRSTF", "ZQXLASTF"),
    "f1": ("ZQXFIRSTG", "ZQXLASTG"), "f2": ("ZQXFIRSTH", "ZQXLASTH"),
}
_IDENTITY_CANARIES = ("zqxfirst", "zqxlast", "zqxid", "zqxdistrict", "zqxcomment", "zqxlink", "zqxoffice",
                      "spouse", "efdsearch", "disclosures-clerk")


def crow(chamber, sym, disclosed, member, *, kind="Purchase", asset="Stock", desc=None, **over):
    first, last = _MEMBERS.get(member, ("ZQXFIRSTX", member))
    vals = {"symbol": sym, "senateID": f"ZQXID-{member}", "disclosureDate": disclosed,
            "transactionDate": disclosed, "firstName": first, "lastName": last,
            "office": f"ZQXOFFICE {first} {last}", "district": "ZQXDISTRICT", "owner": "Spouse",
            "assetDescription": desc if desc is not None else f"{sym} Common Stock", "assetType": asset,
            "type": kind, "amount": "$1,001 - $15,000", "comment": "ZQXCOMMENT",
            "link": ("https://efdsearch.senate.gov/ZQXLINK" if chamber == "senate"
                     else "https://disclosures-clerk.house.gov/ZQXLINK"),
            "capitalGainsOver200USD": "False"}
    vals.update(over)
    keys = _HOUSE_KEYS if chamber == "house" else _SENATE_KEYS
    return {**{k: vals[k] for k in keys}, **{k: v for k, v in over.items() if k not in keys}}


def cfeed(chamber, rows, *, oldest="2026-10-20"):
    """A chamber's feed as FMP orders it (disclosure date, newest first), ending on a sale dated
    ``oldest`` — before 2026-10-25, so a 2026-11 walk is covered."""
    out = sorted(rows, key=lambda r: r["disclosureDate"], reverse=True)
    out.append(crow(chamber, "KO", oldest, "f1" if chamber == "senate" else "f2", kind="Sale"))
    return out


def congress_rows():
    senate = [
        # One member, three filings of the same stock: ONE member.
        crow("senate", "AAPL", "2026-11-20", "s1"), crow("senate", "AAPL", "2026-11-21", "s1"),
        crow("senate", "AAPL", "2026-11-22", "s1", desc="Apple Inc. - Common Stock"),
        crow("senate", "MSFT", "2026-11-05", "s2"),
        crow("senate", "AAPL", "2026-11-10", "s2", kind="Exchange"),          # never counted
        crow("senate", "AAPL", "2026-11-12", "s3", kind="Sale (Partial)"),    # a sale: not counted
        crow("senate", "AAPL", "2026-12-01", "s3"),                           # next month
        crow("senate", "AAPL", "2026-10-31", "s3"),                           # previous month
    ]
    house = [
        crow("house", "AAPL", "2026-11-15", "h1"), crow("house", "AAPL", "2026-11-16", "h2"),
        crow("house", "MSFT", "2026-11-03", "h1"),
        crow("house", "NVDA", "2026-11-04", "h2"),                            # one member only
        crow("house", "MSFT", "2026-11-06", "h3", asset="Stock Option"),      # an option: not stock
    ]
    return senate, house


def congress_profiles():
    return {"AAPL": prof("AAPL", "Apple Inc.", exchange="NASDAQ", cap=3e12, price=91919.19),
            "MSFT": prof("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=3e12),
            "NVDA": prof("NVDA", "NVIDIA Corporation", exchange="NASDAQ"),
            "BRK-B": prof("BRK-B", "Berkshire Hathaway Inc.", cap=9e11),
            "SPY": prof("SPY", "SPDR S&P 500 ETF Trust", isEtf=True),
            "TSM": prof("TSM", "Taiwan Semiconductor Manufacturing Company Limited", isAdr=True)}


def congress_fmp(senate=None, house=None, **kw):
    s, h = congress_rows()
    return FakeFMP2b(senate=cfeed("senate", s) if senate is None else senate,
                     house=cfeed("house", h) if house is None else house,
                     profiles=kw.pop("profiles", congress_profiles()), **kw)


async def crun(fmp, **kw):
    return await run("congress_count", fmp, run_date=kw.pop("run_date", CONGRESS_RUN), **kw)


def _no_identity(text: str) -> None:
    low = text.lower()
    for canary in _IDENTITY_CANARIES:
        assert canary not in low, canary
    assert not re.search(r"\b[0-9a-f]{64}\b", low)


@pytest.mark.asyncio
async def test_congress_counts_distinct_purchasers_across_both_chambers(caplog):
    caplog.set_level(logging.DEBUG)
    fmp = congress_fmp()
    got = await crun(fmp)
    assert got.skip_reason is None
    assert [(r.company.symbol, r.company.name, r.members) for r in got.records] == [
        ("AAPL", "Apple", 3), ("MSFT", "Microsoft", 2)]
    rec = got.records[0]
    assert isinstance(rec, R.CongressCount) and rec.month == "2026-11" and rec.fetched_on == CONGRESS_RUN
    assert R.ledger_key(rec) == CONGRESS_KEY
    # An exchange and a stock option are counted as refused rows; a sale and the rows of other
    # months are not purchases of the month at all.
    assert got.rejections == {"exchange_type": 1, "option_asset": 1}
    # Both chambers walked at 2000 rows, then read once more to confirm.
    assert fmp.congress_calls == [("senate", 2000), ("house", 2000), ("senate", 2000), ("house", 2000)]
    assert fmp.profile_requests == [["AAPL", "MSFT"]]                      # NVDA (1 member) never profiled
    # No identity anywhere: the fact sheet, the log, the memo.
    for r in got.records:
        _no_identity(json.dumps(R.fact_sheet(r, rejections=got.rejections, selection={})))
    _no_identity(caplog.text)
    _no_identity(json.dumps([v for _exp, v in A._MEMO.values()], default=repr))


def test_the_source_scrub_keeps_no_identity_field():
    row = crow("senate", "AAPL", "2026-11-20", "s1", party="ZQXPARTY", state="ZQXSTATE",
               senator="ZQXSENATOR", representative="ZQXREP", bioguide="ZQXBIO")
    (scrubbed,) = A._scrub_congress([row], "senate", "salt")
    dumped = repr(scrubbed).lower()
    for canary in _IDENTITY_CANARIES + ("zqxparty", "zqxstate", "zqxsenator", "zqxrep"):
        assert canary not in dumped, canary
    assert [f.name for f in dataclasses_fields(A._CongressRow)] == [
        "symbol", "disclosed", "kind", "asset", "description", "ids", "last"]
    # Every identity key of the recorded shapes is named in the drop list (decision 3, senateID too).
    assert {"firstName", "lastName", "office", "district", "owner", "link", "comment", "senateID",
            "party", "state", "senator", "representative"} <= A.CONGRESS_IDENTITY_FIELDS
    # The hashes are salted per call: the same member never hashes the same way twice.
    (again,) = A._scrub_congress([row], "senate", "another salt")
    assert scrubbed.last != again.last and set(scrubbed.ids).isdisjoint(again.ids)


@pytest.mark.asyncio
async def test_congress_counts_purchases_only_never_exchanges_or_sales():
    s = [crow("senate", "AAPL", "2026-11-20", "s1"), crow("senate", "AAPL", "2026-11-21", "s2", kind="Exchange"),
         crow("senate", "AAPL", "2026-11-22", "s3", kind="Sale (Full)"), crow("senate", "AAPL", "2026-11-23", "f1",
                                                                              kind="Sale")]
    h = [crow("house", "AAPL", "2026-11-15", "h1", kind="Sale"), crow("house", "AAPL", "2026-11-16", "h2",
                                                                      kind="purchase ")]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", h)))
    # s1 and h2 purchased ("purchase " folds to the same type); the exchange and the sales are not purchases.
    assert [(r.company.symbol, r.members) for r in got.records] == [("AAPL", 2)]


@pytest.mark.parametrize("asset", ["Stock Option", "ETF", "Corporate Bond", "Mutual Fund", "Non-Public Stock",
                                   "Municipal Security", "Government Securities", "Cryptocurrency"])
@pytest.mark.asyncio
async def test_only_stock_purchases_are_counted(asset):
    s = [crow("senate", "AAPL", "2026-11-20", "s1"), crow("senate", "AAPL", "2026-11-21", "s2", asset=asset)]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert got.records == () and got.skip_reason == "congress_none_qualified"
    assert got.rejections == {"option_asset": 1}
    # ... and that purchase never makes the stock count uncertain.
    s.append(crow("senate", "AAPL", "2026-11-22", "s3"))
    A.clear_memo()
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert [(r.company.symbol, r.members) for r in got.records] == [("AAPL", 2)]


@pytest.mark.parametrize("over", [
    {"asset": ""}, {"asset": None}, {"asset": "Other"}, {"asset": "Other Securities"}, {"asset": "REIT"},
    {"desc": "Apple Inc call options"}, {"desc": "Apple Inc. 4.5% Notes due 2030"}, {"desc": "Apple Inc Warrants"},
])
@pytest.mark.asyncio
async def test_a_purchase_that_may_or_may_not_be_the_stock_refuses_the_count(over):
    # Two certain stock purchasers, and a third purchase of the symbol that may be the stock (an
    # unclear asset type) or may not be (a "Stock" row describing another instrument).
    s = [crow("senate", "AAPL", "2026-11-20", "s1"), crow("senate", "AAPL", "2026-11-21", "s2"),
         crow("senate", "AAPL", "2026-11-22", "s3", **over)]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert got.records == () and got.rejections.get("asset_uncertain") == 1, got.rejections


@pytest.mark.asyncio
async def test_one_member_with_three_filings_is_one_member():
    s = [crow("senate", "GME", f"2026-11-{d}", "s1", desc="GameStop Corp") for d in (10, 11, 12)]
    fmp = congress_fmp(senate=cfeed("senate", s), house=cfeed("house", []),
                       profiles={"GME": prof("GME", "GameStop Corp.")})
    got = await crun(fmp)
    assert got.records == () and got.skip_reason == "congress_none_qualified"
    assert fmp.profile_requests == []                                      # 1 member: never a candidate


@pytest.mark.asyncio
async def test_a_member_filing_under_two_name_spellings_is_still_one_member():
    # Same feed id and office, a nickname on one row: the strict keys tie them → ONE member.
    s = [crow("senate", "AAPL", "2026-11-10", "s1"),
         crow("senate", "AAPL", "2026-11-11", "s1", firstName="ZQXNICKA"),
         crow("senate", "AAPL", "2026-11-12", "s2")]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert [(r.company.symbol, r.members) for r in got.records] == [("AAPL", 2)]


@pytest.mark.parametrize("rows, reason", [
    # Two people, one surname (two senators named Scott): the count could be 1 or 2.
    ([crow("senate", "AAPL", "2026-11-10", "s1"),
      crow("senate", "AAPL", "2026-11-11", "s2", lastName="ZQXLASTA")], "member_ambiguous"),
    # A senator and a representative with one surname: the unscoped surname refuses too (fail closed).
    ([crow("senate", "AAPL", "2026-11-10", "s1"),
      crow("senate", "AAPL", "2026-11-11", "s2"),
      crow("house", "AAPL", "2026-11-11", "h1", lastName="ZQXLASTB")], "member_ambiguous"),
    # One feed id under two surnames: the strict keys merge two last names.
    ([crow("senate", "AAPL", "2026-11-10", "s1"),
      crow("senate", "AAPL", "2026-11-11", "s2", senateID="ZQXID-s1")], "member_ambiguous"),
    # ... and a third member under the first surname: two groups, two surnames — the counts agree
    # (2 = 2), yet one group holds two surnames, so who is who is not settled.
    ([crow("senate", "AAPL", "2026-11-10", "s1"),
      crow("senate", "AAPL", "2026-11-11", "s2", senateID="ZQXID-s1"),
      crow("senate", "AAPL", "2026-11-12", "s3", lastName="ZQXLASTA")], "member_ambiguous"),
    # A purchase with no last name cannot be told apart from the others.
    ([crow("senate", "AAPL", "2026-11-10", "s1"), crow("senate", "AAPL", "2026-11-11", "s2"),
      crow("senate", "AAPL", "2026-11-12", "s3", lastName="", firstName="")], "member_unidentifiable"),
    ([crow("senate", "AAPL", "2026-11-10", "s1"), crow("senate", "AAPL", "2026-11-11", "s2"),
      crow("senate", "AAPL", "2026-11-12", "s3", lastName=None)], "member_unidentifiable"),
])
@pytest.mark.asyncio
async def test_a_count_the_identity_fields_cannot_settle_is_refused(rows, reason):
    senate = cfeed("senate", [r for r in rows if r.get("capitalGainsOver200USD") is None])
    house = cfeed("house", [r for r in rows if r.get("capitalGainsOver200USD") is not None])
    fmp = congress_fmp(senate=senate, house=house)
    got = await crun(fmp)
    assert got.records == () and got.rejections.get(reason) == 1, got.rejections
    assert fmp.profile_requests == []                                      # refused before any profile


@pytest.mark.parametrize("extra, reason", [
    # An in-month purchase of the same company under NO usable symbol (FMP left it blank).
    (crow("senate", "", "2026-11-14", "s3", desc="Common stock (AAPL)"), "unmapped_purchase"),     # its ticker tag
    (crow("senate", "", "2026-11-14", "s3", desc="AAPL - Class A common"), "unmapped_purchase"),
    (crow("senate", "", "2026-11-14", "s3", desc="Apple Inc."), "unmapped_purchase"),             # its name
    (crow("senate", "", "2026-11-14", "s3", desc="APPLE INC COMMON STOCK"), "unmapped_purchase"),
    (crow("senate", "AAPL.WS", "2026-11-14", "s3", desc="Apple warrant"), "unmapped_purchase"),
    # ... or under another usable symbol whose description names it (another class).
    (crow("senate", "APLX", "2026-11-14", "s3", desc="Apple Inc Class B"), "share_class_overlap"),
])
@pytest.mark.asyncio
async def test_an_in_month_purchase_of_the_same_company_elsewhere_refuses_the_count(extra, reason):
    s, h = congress_rows()
    got = await crun(congress_fmp(senate=cfeed("senate", s + [extra]), house=cfeed("house", h)))
    assert [r.company.symbol for r in got.records] == ["MSFT"]
    assert got.rejections.get(reason) == 1


@pytest.mark.asyncio
async def test_another_share_class_of_the_root_refuses_the_count():
    # The other class's description does not name the company: the shared root alone refuses.
    s = [crow("senate", "BRK.B", "2026-11-10", "s1", desc="Berkshire Hathaway Inc"),
         crow("senate", "BRK-B", "2026-11-11", "s2", desc="Berkshire Hathaway Inc"),
         crow("senate", "BRK.A", "2026-11-12", "s3", desc="Class A shares")]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert got.records == () and got.rejections.get("share_class_overlap") == 1


@pytest.mark.asyncio
async def test_two_candidates_that_resolve_to_one_company_are_both_refused():
    # GOOG and GOOGL, two members each, descriptions that name neither: the profiled NAME ties them.
    s = [crow("senate", sym, f"2026-11-{d:02d}", m, desc="ZZ")
         for sym, d0 in (("GOOG", 10), ("GOOGL", 20)) for d, m in ((d0, "s1"), (d0 + 1, "s2"))]
    profiles = {"GOOG": prof("GOOG", "Alphabet Inc."), "GOOGL": prof("GOOGL", "Alphabet Inc.")}
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", []), profiles=profiles))
    assert got.records == () and got.rejections == {"share_class_overlap": 2}


@pytest.mark.asyncio
async def test_an_unrelated_purchase_does_not_refuse_the_count():
    s, h = congress_rows()
    s.append(crow("senate", "", "2026-11-14", "s3", desc="GS Managed Structured Note Strategy S&P 500 Linked Note"))
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", h)))
    assert [r.company.symbol for r in got.records] == ["AAPL", "MSFT"]
    assert got.rejections == {"exchange_type": 1, "option_asset": 1, "symbol_grammar": 1}


@pytest.mark.parametrize("sym, reason", [
    ("SPY", "etf_or_fund"), ("TSM", "adr"), ("ABCDW", "warrant_unit_right"), ("WFC-PY", "symbol_grammar"),
])
@pytest.mark.asyncio
async def test_congress_companies_are_us_common_stock_only(sym, reason):
    s = [crow("senate", sym, "2026-11-10", "s1", desc="ZZ"), crow("senate", sym, "2026-11-11", "s2", desc="ZZ")]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", [])))
    assert got.records == () and got.rejections.get(reason) == 1


@pytest.mark.asyncio
async def test_congress_keeps_the_top_five_and_profiles_at_most_ten():
    syms = [f"C{c}" for c in "ABCDEFGHIJKL"]                               # 12 candidates
    s = []
    for i, sym in enumerate(syms):
        for j in range(2 + (i % 3)):                                       # 2, 3 or 4 members
            s.append(crow("senate", sym, f"2026-11-{10 + j:02d}", f"m{i}-{j}", desc="ZZ"))
    profiles = {sym: prof(sym, f"Company {sym}") for sym in syms}
    fmp = congress_fmp(senate=cfeed("senate", s), house=cfeed("house", []), profiles=profiles)
    got = await crun(fmp)
    assert len(fmp.profile_requests[0]) == A.CONGRESS_PROFILE_CANDIDATES
    # members desc, then symbol
    assert [(r.company.symbol, r.members) for r in got.records] == [
        ("CC", 4), ("CF", 4), ("CI", 4), ("CL", 4), ("CB", 3)]


@pytest.mark.parametrize("answer", [
    FMPPartialPageException("page 3 lost", endpoint="senate-latest", pages_total=8, pages_failed=1),
    FMPUnavailableException("503"),
    FMPRateLimitException("429"),
    [],                                                       # every page 403/404, or a swallowed outage
    {"error": "x"},
])
@pytest.mark.asyncio
async def test_a_failed_partial_or_empty_chamber_is_unavailable(answer):
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await crun(congress_fmp(senate=answer))
    assert err.value.reason == "congress_feed_unavailable"


@pytest.mark.asyncio
async def test_an_uncovered_walk_retries_at_7500_then_counts():
    s, h = congress_rows()
    short = sorted(s, key=lambda r: r["disclosureDate"], reverse=True)     # ends inside the month
    full = cfeed("senate", s)
    fmp = congress_fmp(senate=lambda limit, _n: short if limit == 2000 else full)
    got = await crun(fmp)
    assert [r.company.symbol for r in got.records] == ["AAPL", "MSFT"]
    # Only the uncovered chamber is re-walked; each confirm read uses the covering size.
    assert fmp.congress_calls == [("senate", 2000), ("house", 2000), ("senate", 7500),
                                  ("senate", 7500), ("house", 2000)]


@pytest.mark.asyncio
async def test_a_walk_that_never_covers_the_month_is_unavailable():
    s, _h = congress_rows()
    short = sorted(s, key=lambda r: r["disclosureDate"], reverse=True)
    # The last row must be BEFORE the month's first day minus the margin: 10-26 is not.
    near = cfeed("senate", s, oldest="2026-10-26")
    for answer in (short, near):
        A.clear_memo()
        fmp = congress_fmp(senate=answer)
        with pytest.raises(A.MarketingNewsUnavailable) as err:
            await crun(fmp)
        assert err.value.reason == "congress_window_uncovered"
        assert [lim for c, lim in fmp.congress_calls if c == "senate"] == [2000, 7500]


@pytest.mark.parametrize("mutate", ["rise", "bad_date", "missing_date"])
@pytest.mark.asyncio
async def test_a_feed_out_of_order_is_unavailable(mutate):
    s, h = congress_rows()
    feed = cfeed("senate", s)
    if mutate == "rise":
        feed[2], feed[3] = feed[3], feed[2]           # one rising step anywhere
        assert feed[2]["disclosureDate"] < feed[3]["disclosureDate"]
    elif mutate == "bad_date":
        feed[1]["disclosureDate"] = "11/21/2026"
    else:
        feed[1]["disclosureDate"] = None
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await crun(congress_fmp(senate=feed))
    assert err.value.reason == "congress_feed_unordered"


@pytest.mark.asyncio
async def test_two_reads_that_disagree_on_the_month_are_unavailable():
    s, h = congress_rows()
    first, moved = cfeed("senate", s), cfeed("senate", s[1:])            # a row lost on the second read
    fmp = congress_fmp(senate=lambda _limit, n: first if n == 0 else moved)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await crun(fmp)
    assert err.value.reason == "congress_feed_unavailable" and "between two reads" in err.value.detail
    # A change OUTSIDE the month (a new December disclosure) is not a disagreement.
    A.clear_memo()
    newer = cfeed("senate", s + [crow("senate", "KO", "2026-12-07", "f1")])
    fmp = congress_fmp(senate=lambda _limit, n: first if n == 0 else newer)
    assert [r.company.symbol for r in (await crun(fmp)).records] == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_congress_is_not_due_before_the_month_has_settled_and_a_posted_month_reads_nothing():
    fmp = congress_fmp()
    got = await crun(fmp, run_date=date(2026, 12, 5))                       # Nov 30 + 7 = Dec 7
    assert got.skip_reason == "congress_not_due" and fmp.calls == Counter()
    got = await crun(fmp, run_date=date(2026, 12, 7))
    assert got.records                                                     # due exactly on day 7
    A.clear_memo()
    fmp = congress_fmp()
    got = await crun(fmp, exclude={CONGRESS_KEY})
    assert got.records == () and got.rejections == {"already_posted": 1} and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_congress_counts_are_memoized_shared_and_an_error_is_not():
    fmp = congress_fmp()
    a, b = await asyncio.gather(crun(fmp), crun(fmp))
    assert a.records == b.records and fmp.calls["congress"] == 4               # one walk + one confirm
    await crun(fmp)
    assert fmp.calls["congress"] == 4
    A.clear_memo()
    calls = {"n": 0}

    def flaky(_limit, _n):
        calls["n"] += 1
        return FMPUnavailableException("down") if calls["n"] == 1 else cfeed("senate", congress_rows()[0])

    fmp = congress_fmp(senate=flaky)
    with pytest.raises(A.MarketingNewsUnavailable):
        await crun(fmp)
    assert len((await crun(fmp)).records) == 2


@pytest.mark.asyncio
async def test_all_congress_profiles_missing_is_unavailable():
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await crun(congress_fmp(profiles={}))
    assert err.value.reason == "profiles_unavailable"


@pytest.mark.asyncio
async def test_member_ids_are_per_chamber():
    # The recorded feeds number their members per chamber (both start at "id-001"): a senator and a
    # representative sharing an id VALUE are two different members — never one, never "ambiguous".
    s = [crow("senate", "AAPL", "2026-11-10", "s1", senateID="id-001")]
    h = [crow("house", "AAPL", "2026-11-11", "h1", senateID="id-001")]
    got = await crun(congress_fmp(senate=cfeed("senate", s), house=cfeed("house", h)))
    assert [(r.company.symbol, r.members) for r in got.records] == [("AAPL", 2)]


def test_the_members_count_is_union_find_over_the_strict_keys():
    rows = A._scrub_congress([crow("senate", "X", "2026-11-01", "s1"), crow("senate", "X", "2026-11-02", "s1"),
                              crow("house", "X", "2026-11-03", "h1")], "senate", "s")
    assert A._members(rows) == (2, None)
    assert A._members([]) == (0, None)
    # A member whose rows share NO strict key with each other (id, name and office all differ)
    # but share the surname: the count would be 2 by keys and 1 by surname → refused.
    odd = A._scrub_congress([crow("senate", "X", "2026-11-01", "s1"),
                             crow("senate", "X", "2026-11-02", "s1", senateID="other", firstName="Q",
                                  office="other office")], "senate", "s")
    assert A._members(odd) == (0, "member_ambiguous")


_PROBE = Path.home() / ".claude/plans/company-weekly-congress-probe"


@pytest.mark.skipif(not (_PROBE / "senate_latest_scrubbed.json").exists(), reason="the recorded probe is local")
def test_the_recorded_feeds_are_ordered_and_scrub_to_nothing_identifying():
    for chamber in ("senate", "house"):
        raw = json.loads((_PROBE / f"{chamber}_latest_scrubbed.json").read_text())
        rows = A._scrub_congress(raw, chamber, "salt")
        assert len(rows) == len(raw) == 500
        # Verified ordering: disclosure date never rises in the recorded order.
        assert A._feed_problem(rows, date(2026, 10, 1)) in (None, "uncovered")
        agg = A._count_month(rows, date(2026, 9, 1), date(2026, 9, 30))
        text = json.dumps(agg).lower()
        assert "member-" not in text and "id-0" not in text
        assert all(n >= 1 for n in agg["counts"].values())


# ── company_stakes ────────────────────────────────────────────────────────────

STAKES_RUN = date(2026, 12, 29)          # the first stakes Tuesday (13F season ends 12-27)


def stake(slug, name, **over):
    row = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/{name}/{over.get('as_of', '')}")),
           "created_at": "2026-09-24T12:00:00+00:00", "company_slug": slug, "kind": "private",
           "investee_name": name, "investee_cusip": None, "investee_us_symbol": None, "local_listing": None,
           "ownership_pct": None, "ownership_basis": None, "disclosed_value_usd": 1e9, "value_basis": "invested",
           "as_of": "2026-06-30", "source_title": "NVIDIA 10-Q (quarter ended Jul 26, 2026)",
           "source_url": "https://www.sec.gov/Archives/edgar/ZQXSOURCEURL.htm", "source_confidence": "primary",
           "published": True, "listed_since": None, "background": None, "verified_on": "2026-12-01",
           "sort_order": 1, "material": True, "tied_to_deal": False, "updated_at": "2026-12-01T00:00:00+00:00"}
    row.update(over)
    return row


def stakes_rows():
    return [
        stake("nvidia", "Intel", investee_us_symbol="INTC", kind="on_13f_note", disclosed_value_usd=5e9,
              as_of="2025-12-26"),
        stake("nvidia", "Nscale", disclosed_value_usd=777399382.46, as_of="2026-03-27",
              background="Nscale filed an S-1 in September 2026."),
        stake("amazon", "OpenAI", disclosed_value_usd=50e9, as_of="2026-07-31",
              source_title="Amazon 10-Q (quarter ended Jun 30, 2026)"),
        stake("tsmc", "IMS Nanofabrication Global", value_basis="fair_value", disclosed_value_usd=432795000.0,
              ownership_pct=10.0, as_of="2024-12-31", source_title="TSMC 2025 Annual Report",
              local_listing="Austria"),
        # refused
        stake("nvidia", "Private companies (not named)", value_basis="carrying_value", disclosed_value_usd=47.9e9),
        stake("nvidia", "Marvell Technology (convertible preferred)", investee_us_symbol="MRVL", as_of="2026-08-01"),
        stake("nvidia", "CoreWeave", investee_us_symbol="CRWV", disclosed_value_usd=None, value_basis=None,
              ownership_pct=10.66, as_of="2026-04-15"),
        stake("samsung", "Corning", investee_us_symbol="GLW", as_of="2026-06-30"),
        stake("amazon", "Anthropic", verified_on="2026-08-01", as_of="2026-06-30"),
        stake("nvidia", "Old Co", as_of="2023-06-30"),
        stake("nvidia", "Bad Source", source_url="http://insecure.example/x"),
    ]


def stakes_world(rows=None, **sb_errors):
    companies = [SimpleNamespace(slug="nvidia", name="NVIDIA", card_kind="thirteen_f", detail_symbol="NVDA"),
                 SimpleNamespace(slug="amazon", name="Amazon", card_kind="thirteen_f", detail_symbol="AMZN"),
                 SimpleNamespace(slug="samsung", name="Samsung", card_kind="non_us", detail_symbol=None)]
    club = FakeClub(group=SimpleNamespace(companies=companies,
                                          also_in_club=[SimpleNamespace(slug="tsmc", name="TSMC")]))
    sb = FakeSB({
        "trillion_club_companies": [
            {"slug": "nvidia", "ciks": ["0001045810"], "card_kind": "thirteen_f", "use_13f": True,
             "detail_symbol": "NVDA", "published": True},
            {"slug": "amazon", "ciks": ["0001018724"], "card_kind": "thirteen_f", "use_13f": True,
             "detail_symbol": "AMZN", "published": True},
            {"slug": "samsung", "ciks": [], "card_kind": "non_us", "detail_symbol": None, "published": True},
            {"slug": "tsmc", "ciks": [], "card_kind": "non_us", "detail_symbol": "TSM", "published": True},
        ],
        "trillion_club_stakes": stakes_rows() if rows is None else rows,
    }, errors=sb_errors or None)
    profiles = {
        "NVDA": prof("NVDA", "NVIDIA Corporation", exchange="NASDAQ", price=91919.19),
        "AMZN": prof("AMZN", "Amazon.com, Inc.", exchange="NASDAQ"),
        "TSM": prof("TSM", "Taiwan Semiconductor Manufacturing Company Limited", isAdr=True, currency="USD"),
        "INTC": prof("INTC", "Intel Corporation", exchange="NASDAQ"),
        "CRWV": prof("CRWV", "CoreWeave, Inc.", exchange="NASDAQ"),
        "GLW": prof("GLW", "Corning Incorporated"),
        "BABA": prof("BABA", "Alibaba Group Holding Limited", isAdr=True),
    }
    return club, sb, FakeFMP(profiles=profiles)


async def srun(club, sb, fmp, **kw):
    return await run("company_stakes", fmp, club=club, sb=sb, run_date=kw.pop("run_date", STAKES_RUN), **kw)


@pytest.fixture
def club_on(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)


@pytest.mark.asyncio
async def test_stakes_are_named_figured_and_newest_first(club_on):
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp)
    assert got.skip_reason is None
    assert [(r.investor.name, r.investee_name, r.value_usd, r.value_basis) for r in got.records] == [
        ("Amazon", "OpenAI", 50e9, "invested"),
        ("NVIDIA", "Nscale", 777399382.46, "invested"),
        ("NVIDIA", "Intel", 5e9, "invested"),
        ("TSMC", "IMS Nanofabrication Global", 432795000.0, "fair_value"),
    ]
    openai, nscale, intel, ims = got.records
    assert intel.investee == R.CompanyRef("INTC", "Intel") and intel.kind == "on_13f_note"
    assert nscale.investee is None and openai.investee is None             # private: name only
    assert nscale.background == "Nscale filed an S-1 in September 2026."
    assert ims.ownership_pct == 10.0 and ims.local_listing == "Austria"
    assert all(r.is_new is False for r in got.records)                     # catalogue stakes (decision 1)
    assert R.ledger_key(intel) == f"news:company_stakes:{intel.stake_id}"
    assert got.rejections == {"stake_aggregate": 2, "stake_no_figure": 1, "investor_unlisted": 1,
                              "stake_stale": 1, "stake_too_old": 1, "stake_invalid": 1}
    # FMP-free for the data: one profile batch for the CompanyRefs, nothing else.
    assert fmp.calls == Counter({"profiles": 1})
    assert ("trillion_club_stakes", A._STAKE_COLUMNS) in sb.selects


@pytest.mark.asyncio
async def test_catalogue_stakes_switched_off_leave_only_new_rows(club_on, monkeypatch):
    monkeypatch.setattr(R, "STAKES_INCLUDE_CATALOGUE", False)
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp)
    assert got.records == () and got.skip_reason == "stakes_none_new" and fmp.calls == Counter()
    rows = stakes_rows()
    rows[1]["created_at"] = "2026-11-20T09:00:00+00:00"                    # Nscale: a NEW row
    club, sb, fmp = stakes_world(rows)
    A.clear_memo()
    got = await srun(club, sb, fmp)
    assert [(r.investee_name, r.is_new) for r in got.records] == [("Nscale", True)]


@pytest.mark.asyncio
async def test_a_new_row_ranks_before_a_catalogue_row_of_the_same_day(club_on):
    rows = [stake("nvidia", "Alpha Co", as_of="2026-06-30"),
            stake("nvidia", "Beta Co", as_of="2026-06-30", created_at="2026-11-30T00:00:00Z"),
            stake("nvidia", "Gamma Co", as_of="2026-07-01")]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert [r.investee_name for r in got.records] == ["Gamma Co", "Beta Co", "Alpha Co"]


@pytest.mark.asyncio
async def test_posted_stakes_are_dropped_before_any_profile_call(club_on):
    club, sb, fmp = stakes_world()
    first = await srun(club, sb, fmp)
    keys = {R.ledger_key(r) for r in first.records}
    A.clear_memo()
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp, exclude=keys)
    assert got.records == () and got.skip_reason == "stakes_none_unposted"
    assert got.rejections["already_posted"] == 4 and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_stakes_with_the_club_off_read_nothing(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp)
    assert got.skip_reason == "stakes_feature_off" and club.calls == Counter() and sb.calls == Counter()


@pytest.mark.asyncio
async def test_a_full_stakes_page_is_truncated_never_a_partial_catalogue(club_on):
    rows = [stake("nvidia", f"Company {i}", as_of="2026-06-30") for i in range(A.STAKES_READ_LIMIT)]
    club, sb, fmp = stakes_world(rows)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await srun(club, sb, fmp)
    assert err.value.reason == "stakes_truncated"


@pytest.mark.parametrize("where", ["group", "stakes"])
@pytest.mark.asyncio
async def test_an_unreachable_club_is_unavailable_for_stakes(club_on, where):
    if where == "group":
        club, sb, fmp = stakes_world()
        club.error = RuntimeError("supabase down")
    else:
        club, sb, fmp = stakes_world(trillion_club_stakes=RuntimeError("supabase down"))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await srun(club, sb, fmp)
    assert err.value.reason == "club_unavailable"


@pytest.mark.parametrize("over, investee", [
    ({"investee_us_symbol": "INTC"}, "INTC"),
    ({"investee_us_symbol": "INTC", "investee_name": "Intellicheck"}, None),   # the listing names another company
    ({"investee_us_symbol": "BABA", "investee_name": "Alibaba Group"}, None),  # an ADR is not US common stock
    ({"investee_us_symbol": "NVDA", "investee_name": "NVIDIA"}, None),         # the investor itself
    ({"investee_us_symbol": "ZZZZ"}, None),                                    # no profile
    ({"investee_us_symbol": "BAD!"}, None),
])
@pytest.mark.asyncio
async def test_an_investee_is_a_company_ref_only_when_its_us_listing_checks_out(club_on, over, investee):
    row = stake("nvidia", "Intel", **{"as_of": "2026-06-30", **over})
    club, sb, fmp = stakes_world([row])
    got = await srun(club, sb, fmp)
    (rec,) = got.records
    assert (rec.investee.symbol if rec.investee else None) == investee


@pytest.mark.parametrize("over, field, expected", [
    ({"ownership_pct": 25.0}, "ownership_pct", 25.0),
    ({"ownership_pct": 25.0, "ownership_basis": "voting"}, "ownership_pct", None),   # a qualified pct
    ({"ownership_pct": 25.0, "ownership_basis": " "}, "ownership_pct", None),
    ({"background": "Invested in a 2025 round; see https://x.example"}, "background", None),
    ({"background": "Invested in a Series B round in 2025."}, "background", "Invested in a Series B round in 2025."),
    ({"background": "Data from FMP"}, "background", None),
    ({"local_listing": "Taiwan"}, "local_listing", "Taiwan"),
    ({"local_listing": "www.twse.com.tw"}, "local_listing", None),
    ({"listed_since": "2024-05-01"}, "listed_since", date(2024, 5, 1)),
])
@pytest.mark.asyncio
async def test_optional_stake_texts_travel_only_when_clean(club_on, over, field, expected):
    club, sb, fmp = stakes_world([stake("nvidia", "Nscale", **over)])
    (rec,) = (await srun(club, sb, fmp)).records
    assert getattr(rec, field) == expected


@pytest.mark.parametrize("over, reason", [
    ({"listed_since": "2027-03-01"}, "stake_invalid"),                    # "listed since" a future date
    ({"source_title": "Reuters.com report"}, "stake_invalid"),            # autolinks
    ({"source_title": "FMP stake data"}, "stake_invalid"),                # the vendor
    ({"investee_name": "A" * 40}, "stake_invalid"),                       # over the drawable cap
    ({"investee_name": "Rafineria Gdańska"}, "stake_invalid"),
    ({"id": "not-a-uuid"}, "stake_invalid"),
    ({"id": None}, "stake_invalid"),
    ({"kind": "loan"}, "stake_invalid"),                                  # stake_problem: unknown kind
    ({"source_url": "https://"}, "stake_invalid"),                        # stake_problem: no source
    ({"value_basis": None}, "stake_invalid"),                             # stake_problem: value without basis
    ({"disclosed_value_usd": None, "value_basis": "invested"}, "stake_no_figure"),
    ({"as_of": "2027-01-15"}, "stake_invalid"),                           # in the future
    ({"disclosed_value_usd": 1.0000001e12}, "stake_invalid"),             # a unit error
    ({"as_of": "2026-12-15", "verified_on": "2026-12-10"}, "stake_invalid"),
    ({"investee_name": "Investment commitments (not named)"}, "stake_aggregate"),
    ({"investee_name": "Oura (SAFE)"}, "stake_aggregate"),
    ({"verified_on": "2026-08-30"}, "stake_stale"),                       # 121 days before the run
    ({"as_of": "2023-12-28"}, "stake_too_old"),
])
@pytest.mark.asyncio
async def test_stake_gates(club_on, over, reason):
    club, sb, fmp = stakes_world([stake("nvidia", "Nscale", **over)])
    got = await srun(club, sb, fmp)
    assert got.records == () and got.rejections == {reason: 1} and got.skip_reason == "stakes_none_qualified"
    assert fmp.calls == Counter()


@pytest.mark.asyncio
async def test_unpublished_and_secondary_stakes_are_never_read(club_on):
    rows = [stake("nvidia", "Draft Co", published=False), stake("nvidia", "Rumour Co", source_confidence="secondary")]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert got.records == () and got.rejections == {} and got.skip_reason == "stakes_none_qualified"


@pytest.mark.asyncio
async def test_stake_boundaries_that_pass(club_on):
    rows = [stake("nvidia", "Edge One", verified_on="2026-08-31"),          # exactly 120 days
            stake("nvidia", "Edge Two", as_of="2023-12-30"),                 # exactly 3 × 365 days
            stake("nvidia", "Edge Three", disclosed_value_usd=1e12)]         # exactly the value ceiling
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert sorted(r.investee_name for r in got.records) == ["Edge One", "Edge Three", "Edge Two"]


# ── earnings ──────────────────────────────────────────────────────────────────

EARN_RUN = date(2027, 1, 14)             # Thursday, in the Q4 season; days 01-07 .. 01-13
EARN_DAYS = [f"2027-01-{d:02d}" for d in range(7, 14)]


def erow(sym, day, a, e, ra=None, re_=None, **kw):
    row = {"symbol": sym, "date": day, "epsActual": a, "epsEstimated": e, "revenueActual": ra,
           "revenueEstimated": re_, "lastUpdated": day}
    row.update(kw)
    return row


def earnings_calendar():
    return {
        "2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0, 1.1e9, 1.0e9),
                       erow("JJJ", "2027-01-12", 1.3, 1.0), erow("KKK", "2027-01-12", 1.3, 1.0),
                       erow("LLL", "2027-01-12", 1.3, 1.0),
                       erow("OLD", "2027-01-05", 9.0, 1.0)],            # another day's row: never read here
        "2027-01-11": [erow("BBB", "2027-01-11", 0.9, 1.0)],
        "2027-01-10": [erow("CCC", "2027-01-10", -0.5, -0.25, 3e9, 1.5e9)],
        "2027-01-09": [erow("DDD", "2027-01-09", 0.12, 0.05), erow("EEE", "2027-01-09", 0.169, 1.69)],
        "2027-01-08": [erow("FFF", "2027-01-08", 12.0, 1.0), erow("GGG.L", "2027-01-08", 2.0, 1.0),
                       erow("TECK.B", "2027-01-08", 2.0, 1.0),     # foreign, though "TECK-B" would parse
                       erow("HHH", "2027-01-08", None, 1.0)],
        "2027-01-13": [erow("ABCDW", "2027-01-13", 1.0, 0.5)],
        "2027-01-07": [erow("III", "2027-01-07", 2.0, 1.0)],
    }


def earnings_profiles():
    p = {s: prof(s, f"{s.title()} Industries Inc.", cap=5e9, price=91919.19)
         for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "III", "JJJ", "KKK", "LLL", "OLD")}
    p["III"] = prof("III", "Iii Industries Inc.", cap=9e9)
    p["JJJ"] = prof("JJJ", "Jjj Trust", isEtf=True)
    p["KKK"] = prof("KKK", "Kkk Industries Inc.", cap=1.9e9)
    p["LLL"] = prof("LLL", "Lll Industries Preferred", cap=5e9)
    return p


async def erun(fmp, **kw):
    return await run("earnings", fmp, run_date=kw.pop("run_date", EARN_RUN), **kw)


@pytest.mark.asyncio
async def test_earnings_one_company_per_record_ranked_by_gap_then_cap():
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    got = await erun(fmp)
    assert [(r.company.symbol, r.report_date.isoformat(), r.eps_actual, r.eps_estimate) for r in got.records] == [
        ("III", "2027-01-07", 2.0, 1.0),          # gap 100%, cap $9B
        ("CCC", "2027-01-10", -0.5, -0.25),       # gap 100%, cap $5B
        ("AAA", "2027-01-12", 1.5, 1.0),          # 50%
        ("BBB", "2027-01-11", 0.9, 1.0),          # 10%
    ]
    iii, ccc, aaa, _bbb = got.records
    assert (aaa.revenue_actual, aaa.revenue_estimate) == (1.1e9, 1.0e9)
    assert (ccc.revenue_actual, ccc.revenue_estimate) == (None, None)    # 2.0x: outside [0.5, 1.5], dropped
    assert all(r.period_end is None for r in got.records)
    assert R.ledger_key(aaa) == "news:earnings:AAA:2027-01-12"
    assert got.rejections == {"eps_estimate_too_small": 1, "eps_digit_shift": 1, "eps_gap_implausible": 1,
                              "symbol_grammar": 2, "warrant_unit_right": 1, "etf_or_fund": 1, "below_cap_floor": 1,
                              "non_common_listing": 1, "revenue_dropped": 1}
    # ONE ET day per calendar call, the seven days before the run.
    assert sorted(fmp.calendar_calls) == [(d, d) for d in EARN_DAYS]
    assert "OLD" not in fmp.profile_requests[0]


@pytest.mark.parametrize("rows, expected", [
    (3999, "ok"), (4000, "earnings_calendar_truncated"), (4500, "earnings_calendar_truncated"),
])
@pytest.mark.asyncio
async def test_a_day_at_the_row_cap_is_truncated_never_ranked(rows, expected):
    cal = earnings_calendar()
    cal["2027-01-11"] = cal["2027-01-11"] + [erow(f"Z{i}", "2027-01-11", None, 1.0) for i in range(rows - 1)]
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    if expected == "ok":
        assert len((await erun(fmp)).records) == 4
    else:
        with pytest.raises(A.MarketingNewsUnavailable) as err:
            await erun(fmp)
        assert err.value.reason == expected


@pytest.mark.parametrize("answer", [FMPUnavailableException("503"), FMPRateLimitException("429"), {"x": 1}, None])
@pytest.mark.asyncio
async def test_one_failed_day_fails_the_week_and_nothing_is_memoized(answer):
    cal = earnings_calendar()
    cal["2027-01-09"] = answer
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await erun(fmp)
    assert err.value.reason == "earnings_calendar_unavailable"
    fmp.calendar = earnings_calendar()
    calls = fmp.calls["calendar"]
    assert len((await erun(fmp)).records) == 4
    assert fmp.calls["calendar"] - calls == 7                              # every day read again


@pytest.mark.asyncio
async def test_earnings_days_are_memoized_and_concurrent_calls_share_them():
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    a, b = await asyncio.gather(erun(fmp), erun(fmp))
    assert a.records == b.records and fmp.calls["calendar"] == 7
    await erun(fmp)
    assert fmp.calls["calendar"] == 7


@pytest.mark.parametrize("second, expected", [
    # The same result on two days: one report, the NEWEST day.
    (erow("AAA", "2027-01-08", 1.5, 1.0, 1.1e9, 1.0e9), ("2027-01-12", (1.1e9, 1.0e9))),
    # Two different results for one company in one week: which is right is a guess → refused.
    (erow("AAA", "2027-01-08", 1.4, 1.0), "record_invalid"),
    # A refused row beside a clean one: the company's data is not trusted.
    (erow("AAA", "2027-01-08", 12.5, 1.0), "eps_gap_implausible"),
    # Revenue that disagrees between the rows: dropped, the EPS stays.
    (erow("AAA", "2027-01-08", 1.5, 1.0, 1.2e9, 1.0e9), ("2027-01-12", (None, None))),
])
@pytest.mark.asyncio
async def test_two_rows_of_one_company_in_a_week(second, expected):
    cal = earnings_calendar()
    cal["2027-01-08"] = [second]
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    got = await erun(fmp)
    aaa = [r for r in got.records if r.company.symbol == "AAA"]
    if isinstance(expected, str):
        assert aaa == [] and got.rejections.get(expected) == 1
    else:
        (rec,) = aaa
        assert rec.report_date.isoformat() == expected[0]
        assert (rec.revenue_actual, rec.revenue_estimate) == expected[1]


@pytest.mark.parametrize("ra, re_, kept", [
    (1.4e9, 1e9, True), (1.5e9, 1e9, True), (1.51e9, 1e9, False), (0.5e9, 1e9, True), (0.49e9, 1e9, False),
    (None, 1e9, False), (1e9, None, False), (-1e9, 1e9, False), (0.0, 1e9, False), (1e9, 0.0, False),
    (float("nan"), 1e9, False), (True, 1e9, False), ("1e9", 1e9, False),
])
@pytest.mark.asyncio
async def test_revenue_is_shown_only_inside_the_band_and_never_guessed(ra, re_, kept):
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0, ra, re_)]}
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    got = await erun(fmp)
    (rec,) = got.records
    assert (rec.revenue_actual, rec.revenue_estimate) == ((ra, re_) if kept else (None, None))
    assert got.rejections.get("revenue_dropped") == (None if kept else 1)


@pytest.mark.parametrize("a, e, reason", [
    (0.12, 0.09, "eps_estimate_too_small"), (0.12, -0.09, "eps_estimate_too_small"),
    (1.69, 0.169, "eps_digit_shift"), (-16.9, -1.69, "eps_digit_shift"),
    (15.0, 1.0, "eps_gap_implausible"), (-9.5, 1.0, "eps_gap_implausible"),
    (11.0, 1.0, "eps_digit_shift"),             # a power-of-ten ratio is a glitch before it is a gap
])
@pytest.mark.asyncio
async def test_implausible_eps_rows_are_refused(a, e, reason):
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]}, profiles=earnings_profiles())
    got = await erun(fmp)
    assert got.records == () and got.rejections == {reason: 1} and fmp.calls["profiles"] == 0


@pytest.mark.parametrize("a, e", [(0.10, 0.10), (-0.5, -0.1), (-9.0, 1.0), (0.0, 0.5), (4.9, 1.0)])
@pytest.mark.asyncio
async def test_eps_boundaries_that_pass(a, e):
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]}, profiles=earnings_profiles())
    assert len((await erun(fmp)).records) == 1


@pytest.mark.asyncio
async def test_a_posted_report_is_dropped_before_its_profile():
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    got = await erun(fmp, exclude={"news:earnings:AAA:2027-01-12"})
    assert "AAA" not in [r.company.symbol for r in got.records] and got.rejections["already_posted"] == 1
    assert "AAA" not in fmp.profile_requests[0]


@pytest.mark.asyncio
async def test_earnings_profile_stage_is_bounded_and_needs_one_profile():
    cal = {"2027-01-12": [erow(f"A{c}{d}", "2027-01-12", 1.0 + i / 100, 1.0)
                          for i, (c, d) in enumerate((c, d) for c in "BCDEF" for d in "BCDEF")]}
    fmp = FakeFMP2b(calendar=cal, profiles={})
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await erun(fmp)
    assert err.value.reason == "profiles_unavailable"
    assert len(fmp.profile_requests[0]) == A.EARNINGS_PROFILE_CANDIDATES
    empty = FakeFMP2b(calendar={}, profiles={})
    A.clear_memo()
    got = await erun(empty)
    assert got.skip_reason == "earnings_none_qualified" and empty.calls["profiles"] == 0


# ── theme_explainer ───────────────────────────────────────────────────────────

THEME_RUN = date(2026, 11, 19)


def theme(slug="ai-chips", title="AI Chips", tickers=None, **over):
    row = {"slug": slug, "title": title,
           "tickers": tickers if tickers is not None else ["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "ARM", "INTC", "SMH"],
           "blocked_tickers": ["ARM"], "tickers_as_of": "2026-11-02", "sort_order": 1, "is_active": True,
           "image_url": "https://zqx.example/ZQXTHEMEIMG.png", "subtitle": "ZQXSUBTITLE",
           "category": "ZQXCATEGORY", "accent_hex": "22D3EE", "pinned_tickers": [], "rotation_enabled": True}
    row.update(over)
    return row


def theme_breakdowns():
    return {
        "NVDA": breakdown(symbol="NVDA", fiscal_year="2026", reported_revenue=130e9,
                          revenue_sources=[src("Data Center", 115e9), src("Gaming", 11e9), src("Other", 4e9)]),
        "AMD": breakdown(symbol="AMD", fiscal_year="2025", reported_revenue=25.8e9,
                         revenue_sources=[src("Data Center", 12.6e9), src("Client", 7e9), src("Gaming", 2.6e9),
                                          src("Embedded", 3.6e9)]),
        "AVGO": breakdown(symbol="AVGO", fiscal_year="2025", reported_revenue=51e9,
                          revenue_sources=[src("Semiconductor solutions", 30e9), src("Infrastructure software", 21e9)]),
        "QCOM": breakdown(symbol="QCOM", fiscal_year="2025", reported_revenue=38.9e9,
                          revenue_sources=[src("QCT", 33e9), src("QTL", 5.6e9), src("QSI", 0.3e9)]),
        "MRVL": breakdown(symbol="MRVL", fiscal_year="2025", reported_revenue=5.8e9, intersegment_eliminations=1e9,
                          revenue_sources=[src("Data center", 4e9), src("Enterprise", 2.8e9)]),
        "INTC": RuntimeError("breakdown build failed"),
    }


def theme_profiles():
    return {s: prof(s, n, exchange="NASDAQ", price=91919.19) for s, n in (
        ("NVDA", "NVIDIA Corporation"), ("AMD", "Advanced Micro Devices, Inc."), ("AVGO", "Broadcom Inc."),
        ("QCOM", "QUALCOMM Incorporated"), ("MRVL", "Marvell Technology, Inc."), ("INTC", "Intel Corporation"),
        ("ARM", "Arm Holdings plc"), ("GOOG", "Alphabet Inc."), ("GOOGL", "Alphabet Inc."))} | {
        "SMH": prof("SMH", "VanEck Semiconductor ETF", isEtf=True)}


def theme_world(rows=None, *, breakdowns=None, profiles=None):
    revenue = FakeService(theme_breakdowns() if breakdowns is None else breakdowns,
                          default=breakdown(degraded=["segmentation_unavailable"]))
    sb = FakeSB({"trending_themes": [theme()] if rows is None else rows})
    return dict(fmp=FakeFMP(profiles=theme_profiles() if profiles is None else profiles), revenue=revenue, sb=sb)


async def trun(world, **kw):
    return await run("theme_explainer", world["fmp"], revenue=world["revenue"], sb=world["sb"],
                     run_date=kw.pop("run_date", THEME_RUN), **kw)


@pytest.mark.asyncio
async def test_a_theme_lists_every_member_and_the_largest_segment_where_certain(caplog):
    world = theme_world()
    got = await trun(world)
    (rec,) = got.records
    assert (rec.slug, rec.title, rec.tickers_as_of) == ("ai-chips", "AI Chips", date(2026, 11, 2))
    assert [m.company.symbol for m in rec.members] == ["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"]
    facts = {m.company.symbol: (m.top_segment, m.top_segment_share, m.fiscal_year) for m in rec.members}
    assert facts["NVDA"] == ("Data Center", 115e9 / 130e9, "2026")
    assert facts["AVGO"] == ("Semiconductor solutions", 30e9 / 51e9, "2025")
    assert facts["MRVL"] == (None, None, None)          # segments include intersegment sales
    assert facts["INTC"] == (None, None, None)          # a failed lookup: listed, no fact
    assert got.rejections == {"etf_or_fund": 1}         # SMH; ARM is blocked, never profiled
    assert R.ledger_key(rec) == "news:theme_explainer:ai-chips:2026-11-02"
    assert "INTC segment lookup failed" in caplog.text
    # Names and ticker lists only: never the image, subtitle, category or accent.
    (table, cols), = [s for s in world["sb"].selects if s[0] == "trending_themes"]
    assert not {"image_url", "subtitle", "category", "accent_hex"} & set(cols.split(","))
    assert "ARM" not in world["fmp"].profile_requests[0]


@pytest.mark.parametrize("over, reason", [
    ({"tickers_as_of": "2026-09-09"}, "theme_stale"),             # 71 days
    ({"tickers_as_of": "2026-11-20"}, "theme_stale"),             # after the run
    ({"tickers_as_of": None}, "theme_stale"),
    ({"tickers_as_of": "soon"}, "theme_stale"),
    ({"title": "Signal Hill Picks"}, "theme_title_unusable"),
    ({"title": "Breaking: AI"}, "theme_title_unusable"),
    ({"title": "Données"}, "theme_title_unusable"),
    ({"title": "x" * 41}, "theme_title_unusable"),
    ({"tickers": [f"T{c}{d}" for c in "ABCDE" for d in "ABCDE"]}, "theme_too_large"),     # 25
    ({"tickers": ["NVDA", "AMD", "AVGO", "QCOM", "ARM"]}, "theme_members_thin"),
    ({"slug": "Bad Slug"}, "record_invalid"),
    ({"tickers": "NVDA,AMD"}, "record_invalid"),
])
@pytest.mark.asyncio
async def test_theme_gates_before_any_profile_call(over, reason):
    world = theme_world([theme(**over)])
    got = await trun(world)
    assert got.records == () and got.skip_reason == "theme_none_qualified"
    assert got.rejections.get(reason) == 1 and world["fmp"].calls == Counter()


@pytest.mark.parametrize("breakdowns, reason", [
    # Only three members with a certain fact (QCOM degraded): too few to explain the theme.
    ({**theme_breakdowns(), "QCOM": breakdown(degraded=["segmentation_unavailable"])}, "theme_facts_thin"),
])
@pytest.mark.asyncio
async def test_a_theme_with_too_few_facts_is_refused(breakdowns, reason):
    got = await trun(theme_world(breakdowns=breakdowns))
    assert got.records == () and got.rejections.get(reason) == 1


@pytest.mark.asyncio
async def test_a_theme_left_thin_by_the_company_gate_is_refused():
    profiles = theme_profiles()
    profiles["INTC"] = prof("INTC", "Intel Corporation", exchange="OTC")
    got = await trun(theme_world(profiles=profiles))
    assert got.records == () and got.rejections == {"etf_or_fund": 1, "not_major_exchange": 1,
                                                    "theme_members_thin": 1}


@pytest.mark.asyncio
async def test_every_segment_lookup_failing_is_unavailable():
    world = theme_world(breakdowns={})
    world["revenue"].error = RuntimeError("revenue service down")
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await trun(world)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.asyncio
async def test_a_posted_theme_is_skipped_before_its_profiles():
    world = theme_world([theme(), theme(slug="space", title="Space Economy", sort_order=2)])
    got = await trun(world, exclude={"news:theme_explainer:ai-chips:2026-11-02"})
    assert got.rejections["already_posted"] == 1
    assert world["fmp"].profile_requests[0][0] == "NVDA" and len(world["fmp"].profile_requests) == 1


@pytest.mark.asyncio
async def test_a_large_theme_keeps_every_member_but_looks_up_only_the_first_twelve():
    syms = [f"S{c}{d}" for c in "AB" for d in "ABCDEFGHIJKL"]                  # 24
    profiles = {s: prof(s, f"Company {s}") for s in syms}
    good = breakdown(fiscal_year="2026", reported_revenue=100e9,
                     revenue_sources=[src("Products", 70e9), src("Services", 30e9)])
    world = theme_world([theme(tickers=syms, blocked_tickers=[])], breakdowns={}, profiles=profiles)
    world["revenue"].default = good
    (rec,) = (await trun(world)).records
    assert len(rec.members) == 24
    assert world["revenue"].calls == syms[:A.THEME_SEGMENT_LOOKUPS]
    assert [m.top_segment for m in rec.members] == ["Products"] * 12 + [None] * 12


@pytest.mark.asyncio
async def test_at_most_two_themes_are_evaluated_per_call():
    rows = [theme(slug=f"t{i}", title=f"Theme {i}", sort_order=i) for i in range(4)]
    world = theme_world(rows)
    got = await trun(world)
    assert [r.slug for r in got.records] == ["t0", "t1"] and len(world["fmp"].profile_requests) <= 2


@pytest.mark.asyncio
async def test_two_share_classes_in_a_theme_are_one_member():
    world = theme_world([theme(tickers=["GOOG", "GOOGL", "NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"])])
    (rec,) = (await trun(world)).records
    assert [m.company.symbol for m in rec.members] == ["GOOG", "NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"]


@pytest.mark.parametrize("over, fact", [
    ({}, ("iPhone", 200e9 / 350e9, "2025")),
    ({"degraded": ["x"]}, None),
    ({"fiscal_year": "2023"}, None),                                 # older than two years before the run
    ({"fiscal_year": "2027"}, None),                                 # after the run's year
    ({"fiscal_year": "FY25"}, None),
    ({"intersegment_eliminations": 5e9}, None),
    ({"reported_revenue": None}, None),
    ({"reported_revenue": 0.0}, None),
    ({"revenue_sources": [src("iPhone", 200e9)]}, None),             # one segment: no "largest"
    ({"revenue_sources": [src("iPhone", 100e9), src("Mac", 100e9)]}, None),        # a tie
    ({"revenue_sources": [src("iPhone", 50e9), src("Mac", 40e9), src("Other", 260e9)]}, None),   # rest > top
    ({"revenue_sources": [src("iPhone", 300e9), src("Mac", 60e9)]}, None),         # Σ above revenue
    ({"revenue_sources": [src("iPhone", 200e9), src("Other", 150e9)]}, ("iPhone", 200e9 / 350e9, "2025")),
])
def test_segment_fact(over, fact):
    assert A._segment_fact(breakdown(**over), date(2026, 11, 19)) == fact


# ══ review round 9 (2026-10-10) ══════════════════════════════════════════════

# ── rr9 #1: FMPClient.get_insider_trades_since(company_cik=…) — the per-issuer feed ──

def _fmp_client(pages: Dict[int, Any]):
    """A real `FMPClient` whose HTTP layer answers ``pages[page]`` (the licence pre-flight and the
    failure counter still run, as in `test_fmp_insider_trades_since`)."""
    from app.integrations.fmp import FMPClient

    client, calls = FMPClient(), []

    async def _impl(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        out = pages.get((params or {}).get("page"), [])
        if isinstance(out, BaseException):
            raise out
        return out

    client._make_request_impl = _impl  # type: ignore[method-assign]
    return client, calls


def _feed_rows(n: int, filed: str) -> List[Dict[str, Any]]:
    return [irow(shares=1_000 + i, filed=filed) for i in range(n)]


@pytest.mark.asyncio
async def test_the_client_sends_company_cik_and_reads_one_issuer_like_one_ticker():
    short = _feed_rows(3, "2026-11-12")                     # a short page still inside the window
    client, calls = _fmp_client({0: short})
    rows = await client.get_insider_trades_since(WEEK, company_cik="0001326380", page_size=1000, max_pages=2)
    assert len(rows) == 3                                   # a per-ISSUER short page is the feed's end …
    assert calls == [("insider-trading/search", {"page": 0, "limit": 1000, "companyCik": "0001326380"})]
    # … while the same short page market-wide is a lost tail (unchanged).
    client, calls = _fmp_client({0: short})
    with pytest.raises(FMPPartialPageException):
        await client.get_insider_trades_since(WEEK, page_size=1000, max_pages=2)
    assert "companyCik" not in calls[0][1]
    # With a transaction filter and a CIK, both are sent; the CIK is sent as given (stripped).
    client, calls = _fmp_client({0: short})
    await client.get_insider_trades_since(WEEK, transaction_type="P-Purchase", company_cik=" 0000014693 ")
    assert calls[0][1] == {"page": 0, "limit": 1000, "transactionType": "P-Purchase", "companyCik": "0000014693"}


@pytest.mark.parametrize("cik", ["", "  ", "abc", "0000000000", "12345678901", "１２３", "-123", 1326380, True, 0.5])
@pytest.mark.asyncio
async def test_the_client_refuses_a_cik_that_is_not_one_before_any_request(cik):
    """Silently dropping a junk CIK would turn an issuer read into a market-wide one."""
    client, calls = _fmp_client({0: _feed_rows(3, "2026-11-12")})
    with pytest.raises(ValueError):
        await client.get_insider_trades_since(WEEK, company_cik=cik)
    assert calls == []


@pytest.mark.asyncio
async def test_the_client_issuer_read_keeps_the_fail_closed_pager(caplog):
    caplog.set_level(logging.WARNING)
    req = httpx.Request("GET", "https://financialmodelingprep.com/stable/insider-trading/search")
    client, _calls = _fmp_client({0: httpx.HTTPStatusError("403", request=req,
                                                            response=httpx.Response(403, request=req))})
    assert await client.get_insider_trades_since(WEEK, company_cik="0001326380") == []
    assert "companyCik=0001326380" in caplog.text                       # the log names the scope
    client, _calls = _fmp_client({0: _feed_rows(2, "2026-11-12"), 1: FMPUnavailableException("down")})
    with pytest.raises(FMPPartialPageException):                        # a lost later page
        await client.get_insider_trades_since(WEEK, company_cik="0001326380", page_size=2, max_pages=3)
    client, _calls = _fmp_client({0: _feed_rows(2, "2026-11-12"), 1: _feed_rows(2, "2026-11-11")})
    with pytest.raises(FMPPartialPageException):                        # the page cap inside the window
        await client.get_insider_trades_since(WEEK, company_cik="0001326380", page_size=2, max_pages=2)


# ── rr9 #3: an `other:` text naming the role runs through the same allow-list ──

#: (series, the P line's role text) — each the sitting role in its officer title or Director box,
#: and NOT in its `other:` text (the lens's probes and their neighbours).
_OTHER_TEXT_NOT_SITTING = [
    ("ceo_buys", "director, officer: Chief Executive Officer, other: CEO until 12/31/2026"),
    ("ceo_buys", "director, officer: Chief Executive Officer, other: Former CEO"),
    ("ceo_buys", "director, officer: Chief Executive Officer, other: Retiring CEO"),
    ("ceo_buys", "officer: CEO, other: Chief Executive Officer (Resigned)"),
    ("ceo_buys", "officer: CEO, other: Former Principal Executive Officer"),
    ("insider_buys", "officer: Chief Financial Officer, other: Former CFO"),
    ("insider_buys", "officer: CFO, other: Chief Financial Officer until 12/31/2026"),
    ("insider_buys", "director, other: Former Director"),
    ("insider_buys", "director, other: Director until 11/14/2026"),
    ("insider_buys", "director, other: Director Nominee"),
]

@pytest.mark.parametrize("series, title", _OTHER_TEXT_NOT_SITTING)
@pytest.mark.asyncio
async def test_an_other_text_naming_the_role_must_be_the_sitting_role(series, title):
    """rr9 #3 (CONFIRMED probe): "officer: Chief Executive Officer, other: Former CEO" was published
    as Fox's CEO — the allow-list read the officer title only. The role is the post's one
    identifying claim, so an `other:` text that names it must be the sitting role's too; a
    director's naming the seat must be a sitting director's."""
    role = insider_role(title)
    assert role in ("ceo", "cfo", "director")                        # the shared rule extracts the line
    got, published, fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    assert [r.company.symbol for r in published] == ["GME"]
    assert got.rejections == {"role_uncertain": 1}
    assert "FOX" not in fmp.profile_requests[0]                       # refused in the walk


@pytest.mark.parametrize("series, base, other_title", [
    ("ceo_buys", _FOX_CEO, "director, officer: Chief Executive Officer, other: Former CEO effective 11/14/2026"),
    ("insider_buys", _FOX_DIR, "director, other: Former Director"),
], ids=["ceo", "director"])
@pytest.mark.asyncio
async def test_an_other_text_on_a_row_only_the_issuer_read_holds_counts_too(series, base, other_title):
    """The same person's F-coded Form 4 (which the P walk never returns) in the per-issuer read."""
    f_row = {**_line(base, tx="F-InKind", filed="2026-11-13", shares=3_000), "typeOfOwner": other_title}
    got, published, fmp = await _fox_week(series, [_line(base), f_row])
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"role_uncertain": 1}
    assert "FOX" in fmp.profile_requests[0]                           # caught by the issuer read, after profiles


@pytest.mark.parametrize("series, title, role", [
    ("ceo_buys", "director, officer: Chief Executive Officer, other: Member of 13(d) group", "ceo"),
    ("ceo_buys", "officer: CEO, other: Chairman and CEO", "ceo"),
    ("ceo_buys", "officer: CEO, other: Former CFO", "ceo"),                       # names another role
    ("insider_buys", "director, other: Lead Independent Director", "director"),
    ("insider_buys", "director, other: Member of the Board of Directors", "director"),
    ("insider_buys", "director, 10 percent owner, other: Non-Executive Director", "director"),
    ("insider_buys", "director, other: Former CEO", "director"),                  # names another role
    ("insider_buys", "director, other: Spouse is a member of 13(d) group", "director"),
])
@pytest.mark.asyncio
async def test_an_other_text_that_is_sitting_or_names_no_role_changes_nothing(series, title, role):
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert fox.role == role and got.rejections == {}


@pytest.mark.parametrize("text, role, ok", [
    ("director, officer: Chief Executive Officer, other: Former CEO", "ceo", False),
    ("officer: CFO, other: Former Chief Financial Officer", "cfo", False),
    ("director, other: Former Director", "director", False),
    ("director, other: CEO-Elect, Director Nominee", "director", False),
    ("officer: CEO, other: " + "Founder " * 30 + "CEO", "ceo", False),            # too long to read whole
    ("director, other: Lead Independent Director", "director", True),
    ("director, other: Former CEO", "director", True),
    ("officer: CEO, other: Former CFO", "ceo", True),
    ("director", "director", True), ("10 percent owner", "director", True), (None, "director", True),
    ("director, officer: CEO, other: Member of 13(d) group", "ceo", True),
])
def test_role_text_ok_reads_the_other_text_when_it_names_the_role(text, role, ok):
    assert A._role_text_ok(text, role) is ok


@pytest.mark.parametrize("title, role, sitting", [
    ("Lead Independent Director", "director", True), ("Member of the Board of Directors", "director", True),
    ("Non-Executive Director", "director", True), ("Director", "director", True),
    ("Former Director", "director", False), ("Director Nominee", "director", False),
    ("Chairman", "director", False),                                   # the seat is not named
    ("Director until 11/14/2026", "director", False),
])
def test_sitting_title_has_a_director_list(title, role, sitting):
    assert A._sitting_title(title, role) is sitting


# ── rr9 #4: the allow-list learned the common spellings of SITTING titles ──

_ROUND_9_SITTING_TITLES = {
    "ceo_buys": ("officer: Pres. & CEO", "director, officer: Pres., CEO & Director",
                 "officer: Executive Chair, CEO & Pres.",
                 "officer: Chief Executive Officer (Principal Executive Officer)",
                 "officer: Principal Executive Officer & CEO",
                 "officer: Chairman of the Board of Directors and CEO", "officer: CEO and Secretary",
                 "officer: President, CEO and Secretary"),
    "insider_buys": ("officer: Chief Financial Officer & Secretary", "officer: CFO, Treasurer and Secretary",
                     "officer: EVP, CFO and Secretary", "officer: Chief Financial Officer (Principal Financial Officer)",
                     "officer: Principal Financial Officer & CFO", "officer: Chief Financial and Accounting Officer",
                     "officer: Chief Financial & Principal Accounting Officer", "officer: Exec. VP & CFO",
                     "director, officer: Chief Financial Officer and Director", "officer: CFO & Director"),
}


@pytest.mark.parametrize("series, title", [(s, t) for s, ts in _ROUND_9_SITTING_TITLES.items() for t in ts])
@pytest.mark.asyncio
async def test_a_round_9_sitting_title_is_published(series, title):
    assert insider_role(title) == _SERIES_ROLE[series]               # the line is extracted at all
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert fox.role == _SERIES_ROLE[series] and got.rejections == {}


#: The new words never let a transitional one through: the role is still named and every OTHER
#: word must still be on the list.
_ROUND_9_STILL_REFUSED = {
    "ceo_buys": ("officer: Former Pres. & CEO", "officer: Pres. & CEO-Elect", "officer: CEO and Corporate Secretary",
                 "officer: Principal Executive Officer (former)", "officer: Exec. Chair & Incoming CEO",
                 "officer: CEO & Secretary until 12/31/2026"),
    "insider_buys": ("officer: CFO & Secretary (Resigned)", "officer: Exec. VP & CFO until 12/31/2026",
                     "officer: Incoming Chief Financial and Accounting Officer", "officer: CFO & Director Nominee",
                     "officer: Principal Financial Officer (former)", "officer: Chief Financial Officer & Secretary-Elect"),
}


@pytest.mark.parametrize("series, title", [(s, t) for s, ts in _ROUND_9_STILL_REFUSED.items() for t in ts])
@pytest.mark.asyncio
async def test_a_transitional_word_beside_the_round_9_words_is_still_refused(series, title):
    assert not A._role_text_ok(title, _SERIES_ROLE[series])
    got, published, _fmp = await _fox_week(series, [_line({**_FOX_DIR, "title": title})])
    assert [r.company.symbol for r in published] == ["GME"]
    kept_by_shared_rule = insider_role(title) == _SERIES_ROLE[series]
    assert got.rejections == ({"role_uncertain": 1} if kept_by_shared_rule else {})


def test_the_round_9_refusals_reach_the_adapters_check():
    """Anti-vacuity: the shared rule extracts most of those lines, so the allow-list refuses them."""
    kept = [t for s, ts in _ROUND_9_STILL_REFUSED.items() for t in ts if insider_role(t) == _SERIES_ROLE[s]]
    assert len(kept) >= 7, kept


@pytest.mark.parametrize("title, words", [
    ("Pres. & CEO", ["president", "and", "ceo"]),
    ("Exec. VP & CFO", ["executive", "vice", "president", "and", "cfo"]),
    ("Principal Exec. Officer", ["ceo"]),                               # abbreviations first, then phrases
    ("Principal Financial Officer", ["cfo"]),
    ("Chief Financial & Accounting Officer", ["cfo"]),
    ("Principal Financial and Principal Accounting Officer", ["cfo"]),
    ("Principal Accounting Officer", ["principal", "accounting", "officer"]),   # never the CFO by itself
    ("Chief Executive", ["chief", "executive"]),
])
def test_title_words_round_9(title, words):
    assert A._title_words(title) == words


# ── rr9 #5 [HIGH]: an earnings report is kept only when it is REPORTED in USD ──

#: USD-traded ordinary shares (isAdr False, a USD profile currency — the TRADING currency) whose
#: statements are in another currency: the calendar's EPS and revenue are CAD / EUR figures.
_NON_USD_REPORTERS = [
    ("RY", "Royal Bank of Canada", "CA", "CAD"),
    ("RACE", "Ferrari N.V.", "IT", "EUR"),
    ("SPOT", "Spotify Technology S.A.", "LU", "EUR"),
]


def _non_usd_world(sym, name, country, currency):
    cal = {"2027-01-12": [erow(sym, "2027-01-12", 2.38, 2.30, 1.8e9, 1.75e9),
                          erow("AAA", "2027-01-12", 1.5, 1.0, 1.1e9, 1.0e9)]}
    profiles = {sym: prof(sym, name, cap=100e9, country=country), "AAA": prof("AAA", "Aaa Industries Inc.", cap=5e9)}
    return FakeFMP2b(calendar=cal, profiles=profiles, currency={sym: currency})


@pytest.mark.parametrize("sym, name, country, currency", _NON_USD_REPORTERS, ids=[r[0] for r in _NON_USD_REPORTERS])
@pytest.mark.asyncio
async def test_a_usd_traded_non_usd_reporter_is_refused(sym, name, country, currency, caplog):
    """rr9 #5 (HIGH): the profile gate checks only the TRADING currency, so RY / RACE / SPOT
    passed every gate and the template printed their CAD / EUR EPS and revenue with "$"."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    fmp = _non_usd_world(sym, name, country, currency)
    assert isinstance(R.company_from_profile(fmp.profiles[sym], sym, purpose="earnings"), R.CompanyRef)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["AAA"]
    assert got.rejections == {"non_usd_reporter": 1}
    assert sorted(s for s, _l in fmp.currency_calls) == sorted([sym, "AAA"])
    assert all(limit == 1 for _s, limit in fmp.currency_calls)        # the latest quarter only
    assert f"{sym} reports in {currency}, not USD" in caplog.text
    # Control: the same company reporting in USD is published.
    A.clear_memo()
    fmp = _non_usd_world(sym, name, country, "USD")
    assert sorted(r.company.symbol for r in (await erun(fmp)).records) == sorted([sym, "AAA"])


@pytest.mark.parametrize("answer", [
    FMPUnavailableException("503"), FMPRateLimitException("429"), FMPNotEntitledException("402"),
    [], {"x": 1}, None, [7],
    [{"date": "2026-12-31"}],                                               # no reportedCurrency
    [{"date": "2026-12-31", "reportedCurrency": ""}],
    [{"date": "2026-12-31", "reportedCurrency": 840}],
    [{"date": "2026-09-30", "reportedCurrency": "USD"}, {"date": "2026-12-31", "reportedCurrency": "EUR"}],
], ids=["unavailable", "rate_limited", "not_entitled", "empty", "not_a_list", "none", "not_a_row", "no_currency",
        "blank_currency", "numeric_currency", "the_newest_row_decides"])
@pytest.mark.asyncio
async def test_an_unreadable_or_non_usd_currency_read_refuses_the_report(answer, caplog):
    """Fail closed: a report whose reporting currency cannot be confirmed as USD is refused; the
    week keeps the rest (a per-candidate read, like the Form 4/A check)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0), erow("BBB", "2027-01-12", 0.9, 1.0)]}
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles(), currency={"AAA": answer})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["BBB"]
    assert got.rejections == {"non_usd_reporter": 1}
    read_failed = not (isinstance(answer, list) and len(answer) == 2)
    assert ("AAA reporting currency unreadable" if read_failed else "AAA reports in EUR") in caplog.text
    # A failed read is never memoized: the next call reads again and keeps AAA. (A read that
    # answered — the newest row says EUR — is memoized like a calendar day.)
    fmp.currency = {}
    expected = ["AAA", "BBB"] if read_failed else ["BBB"]
    assert [r.company.symbol for r in (await erun(fmp)).records] == expected


@pytest.mark.parametrize("code", ["USD", "usd", " USD "])
@pytest.mark.asyncio
async def test_a_usd_reporter_is_kept_however_the_code_is_written(code):
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles(), currency={"AAA": code})
    assert [r.company.symbol for r in (await erun(fmp)).records] == ["AAA"]


def _qualifying(n, *, prefix="Q", ratio_base=1.0):
    """``n`` qualifying reports (distinct gap ratios, rank order = list order) and their profiles."""
    syms = [f"{prefix}{a}{b}" for a in "BCDEFGHJ" for b in "BCDEFGHJ"][:n]
    rows = [erow(sym, "2027-01-12", 1.0 + ratio_base - i / 1000, 1.0) for i, sym in enumerate(syms)]
    return syms, rows, {sym: prof(sym, f"{sym} Industries Inc.", cap=5e9) for sym in syms}


@pytest.mark.asyncio
async def test_currency_reads_are_bounded_by_the_record_count():
    """One read per report that reaches the record stage, in waves of the reports still needed —
    never one per profiled candidate."""
    syms, rows, profiles = _qualifying(8)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == syms[:5]
    assert [s for s, _l in fmp.currency_calls] == syms[:5]               # one wave of five, nothing more
    # Two of the first five refused → one more wave of exactly two.
    A.clear_memo()
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles, currency={syms[1]: "CAD", syms[3]: "EUR"})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == [syms[0], syms[2], syms[4], syms[5], syms[6]]
    assert [s for s, _l in fmp.currency_calls] == syms[:7]
    assert got.rejections == {"non_usd_reporter": 2}


@pytest.mark.asyncio
async def test_currency_reads_stop_at_their_cap():
    syms, rows, profiles = _qualifying(30)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles, currency={s: "EUR" for s in syms})
    got = await erun(fmp)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"
    assert len(fmp.currency_calls) == A.EARNINGS_MAX_CURRENCY_READS == 20
    assert got.rejections == {"non_usd_reporter": 20}
    assert sum(len(b) for b in fmp.profile_requests) == 20               # no batch profiled past the cap


@pytest.mark.asyncio
async def test_a_currency_read_out_of_budget_raises():
    clock = {"now": 0.0}

    class Slow(FakeFMP2b):
        async def get_company_profiles_batch(self, symbols):
            clock["now"] = 59.5
            return await super().get_company_profiles_batch(symbols)

    fmp = Slow(calendar={"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}, profiles=earnings_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and "stage=earnings_currency" in err.value.detail
    assert fmp.currency_calls == []


# ── rr9 #6: earnings profiles are walked in batches until ``limit`` reports qualify ──

def _sub_floor(n):
    """``n`` reports with a +200% gap from $800M companies (below the $2B earnings floor)."""
    syms = [f"S{a}{b}" for a in "BCDEFGHJKLM" for b in "BCDEFGHJKLM"][:n]
    return ([erow(sym, "2027-01-12", 3.0, 1.0) for sym in syms],
            {sym: prof(sym, f"{sym} Industries Inc.", cap=8e8) for sym in syms})


@pytest.mark.asyncio
async def test_sub_floor_gaps_never_crowd_out_a_large_cap():
    """rr9 #6 (CONFIRMED probe): 25 sub-$2B companies with larger gaps rank above MSFT; only the top
    20 were profiled, every one fell below the floor, and the week skipped."""
    rows, profiles = _sub_floor(25)
    profiles["MSFT"] = prof("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=3e12)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + [erow("MSFT", "2027-01-12", 1.16, 1.0)]}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["MSFT"]
    assert got.rejections == {"below_cap_floor": 25}
    assert [len(b) for b in fmp.profile_requests] == [20, 6]              # batches of 20, in rank order
    assert "MSFT" in fmp.profile_requests[1]


@pytest.mark.asyncio
async def test_profiling_stops_at_the_hard_cap():
    rows, profiles = _sub_floor(70)
    profiles["MSFT"] = prof("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=3e12)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + [erow("MSFT", "2027-01-12", 1.16, 1.0)]}, profiles=profiles)
    got = await erun(fmp)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"
    assert [len(b) for b in fmp.profile_requests] == [20, 20, 20] and A.EARNINGS_MAX_PROFILES == 60
    assert not any("MSFT" in b for b in fmp.profile_requests)
    assert got.rejections == {"below_cap_floor": 60}


@pytest.mark.asyncio
async def test_batches_stop_once_limit_reports_qualify():
    syms, rows, profiles = _qualifying(5)
    sub, sub_profiles = _sub_floor(30)                                      # rank BELOW the five
    sub = [dict(r, epsActual=1.001) for r in sub]
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + sub}, profiles={**profiles, **sub_profiles})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == syms
    assert len(fmp.profile_requests) == 1                                   # the second batch is never read


@pytest.mark.asyncio
async def test_a_gap_tie_across_two_batches_is_still_ordered_by_market_cap():
    """ZZA ($5B) and ZZB ($9B) tie at +100% on ranks 20 and 21, so they land in two batches; the
    kept reports are ordered by the full rank key — ZZB ranks first."""
    rows, profiles = _sub_floor(19)
    rows = [dict(r, epsActual=3.0) for r in rows]
    tied = [erow("ZZA", "2027-01-12", 2.0, 1.0), erow("ZZB", "2027-01-12", 2.0, 1.0)]
    profiles.update({"ZZA": prof("ZZA", "Zza Industries Inc.", cap=5e9), "ZZB": prof("ZZB", "Zzb Industries Inc.", cap=9e9)})
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + tied}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["ZZB", "ZZA"]
    assert [len(b) for b in fmp.profile_requests] == [20, 1]


@pytest.mark.asyncio
async def test_out_of_budget_on_a_later_batch_keeps_the_records_held(caplog):
    """A record already held is fully checked (profile and currency): running out of budget on a
    LATER batch ends the walk with it; with none held the series still raises."""
    clock = {"now": 0.0}
    sub, sub_profiles = _sub_floor(25)
    rows = [erow("AAA", "2027-01-12", 5.0, 1.0)] + sub                      # AAA ranks first (+400%)
    profiles = {**sub_profiles, "AAA": prof("AAA", "Aaa Industries Inc.", cap=5e9)}

    class Clocked(FakeFMP2b):
        async def get_income_statement(self, sym, period="annual", limit=10):
            clock["now"] = 59.5                                             # the budget is spent here
            return await super().get_income_statement(sym, period, limit)

    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = Clocked(calendar={"2027-01-12": rows}, profiles=profiles)
    got = await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), deadline=60.0,
                             deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert [r.company.symbol for r in got.records] == ["AAA"]
    assert len(fmp.profile_requests) == 1 and "out of budget after 1 record(s)" in caplog.text
    # With nothing held, the same exhaustion (spent by the first batch) raises at the second.
    A.clear_memo()
    clock["now"] = 0.0

    class Late(FakeFMP2b):
        async def get_company_profiles_batch(self, symbols):
            got = await super().get_company_profiles_batch(symbols)
            clock["now"] = 59.5
            return got

    fmp = Late(calendar={"2027-01-12": sub}, profiles=sub_profiles)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and len(fmp.profile_requests) == 1


# ── rr9 #7: the congress worst case, counted in page requests ──

@pytest.mark.asyncio
async def test_the_congress_worst_case_is_146_requests_counted_through_the_client():
    """Both chambers uncovered at 2,000 rows → each re-walked at 7,500 → each confirmed at 7,500,
    plus one profile call per candidate symbol: 2×8 + 2×30 + 2×30 = 136 page requests + 10 = 146
    (the budget comment used to say 107). Counted through the REAL client's pager."""
    from app.integrations.fmp import FMPClient

    syms = [f"C{c}" for c in "ABCDEFGHIJKL"]                                # 12 candidates, 10 profiled
    feeds = {}
    for chamber in ("senate", "house"):
        buys = [crow(chamber, sym, f"2026-11-{10 + j:02d}", f"{chamber}{i}-{j}", desc="ZZ")
                for i, sym in enumerate(syms) for j in range(2)]
        # 2,100 in-month sales: the first 2,000 rows never reach past the month (uncovered).
        pad = [crow(chamber, "KO", f"2026-11-{1 + (i % 28):02d}", f"{chamber}p{i}", kind="Sale")
               for i in range(2_100)]
        feeds[f"{chamber}-latest"] = cfeed(chamber, buys + pad)
    profiles = {sym: prof(sym, f"Company {sym}") for sym in syms}
    client, calls = FMPClient(), Counter()

    async def _impl(endpoint, params=None):
        calls[endpoint] += 1
        if endpoint in feeds:
            page, size = params["page"], params["limit"]
            return json.loads(json.dumps(feeds[endpoint][page * size:(page + 1) * size]))
        if endpoint == "profile":
            p = profiles.get(params["symbol"])
            return [json.loads(json.dumps(p))] if p else []
        raise AssertionError(f"unexpected FMP call {endpoint}")

    client._make_request_impl = _impl  # type: ignore[method-assign]
    got = await crun(client)
    assert len(got.records) == 5                                            # anti-vacuity: it counted
    assert calls == {"senate-latest": 8 + 30 + 30, "house-latest": 8 + 30 + 30, "profile": 10}
    assert sum(calls.values()) == 2 * 8 + 2 * 30 + 2 * 30 + A.CONGRESS_PROFILE_CANDIDATES == 146
    assert A.CONGRESS_WALK_LIMITS == (2000, 7500) and A.CONGRESS_PROFILE_CANDIDATES == 10


# ── rr9 #8 (the shared contract): the theme's size travels with the record ──

@pytest.mark.asyncio
async def test_a_theme_record_carries_its_full_ticker_count():
    """The card counts 8 tickers (ARM blocked, SMH an ETF — both still on it); 6 members pass the
    gates. `theme_size` is 8 so the copy says "6 of its 8 companies", never a complete list."""
    got = await trun(theme_world())
    (rec,) = got.records
    assert (len(rec.members), rec.theme_size) == (6, 8)
    assert R.record_to_dict(rec)["theme_size"] == 8


@pytest.mark.parametrize("tickers, profiles_over, members, size", [
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOGL"], {}, 7, 7),                   # nothing dropped
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOGL"], {"INTC": {"exchange": "OTC"}}, 6, 7),  # rr9 probe
    (["NVDA", " nvda ", "AMD", "AVGO", "QCOM", "MRVL", "INTC"], {}, 6, 6),                   # a duplicate spelling
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOG", "GOOGL"], {}, 7, 8),             # two classes, one member
], ids=["complete", "otc_member_dropped", "duplicate", "share_classes"])
@pytest.mark.asyncio
async def test_theme_size_is_counted_before_any_gate(tickers, profiles_over, members, size):
    profiles = theme_profiles()
    for sym, over in profiles_over.items():
        profiles[sym] = {**profiles[sym], **over}
    got = await trun(theme_world([theme(tickers=tickers, blocked_tickers=[])], profiles=profiles))
    (rec,) = got.records
    assert (len(rec.members), rec.theme_size) == (members, size)
    assert rec.theme_size >= len(rec.members)


# ── review round 9 (2026-10-10): Congress legal names, the whole person, the issuer read ──

#: EDGAR reporting names (LAST FIRST [MIDDLE]) of members the roster lists only by the name they
#: go by — "Josh" Gottheimer, "Tommy" Tuberville, "Ted" Cruz, "Mitt" Romney, "Mike" Johnson … —
#: and that the exact keys missed (the r9 probe published "Joshua S. Gottheimer, a director of Fox").
_LEGAL_NAMES = ("GOTTHEIMER JOSHUA S", "TUBERVILLE THOMAS H", "CRENSHAW DANIEL", "KHANNA ROHIT",
                "CRUZ RAFAEL EDWARD", "MANCHIN JOSEPH", "COTTON THOMAS B", "ROMNEY WILLARD M",
                "JOHNSON JAMES MICHAEL", "SCOTT TIMOTHY E", "SCOTT RICHARD L", "MORENO BERNARDO",
                "JOHNSON WILLIAM L", "HILL JAMES FRENCH")


@pytest.mark.parametrize("name", _LEGAL_NAMES)
@pytest.mark.asyncio
async def test_a_member_filing_under_a_legal_name_is_dropped_never_named_or_role_only(name, caplog):
    caplog.set_level(logging.DEBUG)
    rows = [_line({**_FOX_DIR, "name": name, "cik": "0000000444"}, shares=20_000)]
    got, published, fmp = await _fox_week("insider_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"]
    assert got.rejections == {"congress_name": 1}
    assert "FOX" not in fmp.profile_requests[0]                     # dropped in the walk
    assert name.lower() not in caplog.text.lower()                  # never logged by name
    # Control: an unrelated legal name of the same shape is published (named).
    got, published, _fmp = await _fox_week("insider_buys", [_line({**_FOX_DIR, "cik": "0000000444"}, shares=20_000)])
    (fox,) = [r for r in published if r.company.symbol == "FOX"]
    assert fox.person_name == "John Smith" and got.rejections == {}


@pytest.mark.parametrize("where", ["same_symbol", "other_symbol", "walk_row_only"])
@pytest.mark.asyncio
async def test_a_member_matched_on_one_spelling_drops_every_line_of_the_person(where, caplog):
    """r9 #1: one reporting CIK, two spellings. "PELOSI NANCY" matched and only THAT line was
    dropped; the "PELOSI" line (no first name: never a match, never rendered) stayed and was
    published role-only — "A Fox director disclosed buying $150,000" about a member. The whole
    person goes, on every symbol."""
    caplog.set_level(logging.DEBUG)
    member = {**_FOX_DIR, "cik": "0000000444"}
    assert R.is_congress_name("PELOSI NANCY") and not R.is_congress_name("PELOSI")    # anti-vacuity
    if where == "same_symbol":
        rows = [_line({**member, "name": "PELOSI NANCY"}, shares=2_000),          # $60,000
                _line({**member, "name": "PELOSI"}, shares=5_000)]                # $150,000
        got, published, fmp = await _fox_week("insider_buys", rows)
        assert [r.company.symbol for r in published] == ["GME"]
        assert "FOX" not in fmp.profile_requests[0]
    elif where == "other_symbol":
        rows = [_line({**member, "name": "PELOSI NANCY"}, shares=2_000),
                irow(sym="GME", name="PELOSI", cik="0000000444", title="director", shares=100_000, price=25.0)]
        got, published, fmp = await _fox_week("insider_buys", rows)
        (gme,) = published                    # the GME director survivor, not the member's $2.5M
        assert (gme.company.symbol, gme.person_name) == ("GME", "Jane Doe")
    else:
        # The matching spelling is on a walk row the extractor never keeps (a preferred stock):
        # only the walk-wide scan ties the person.
        rows = [_line({**member, "name": "PELOSI NANCY"}, shares=2_000, sec="Series A Preferred Stock"),
                _line({**member, "name": "PELOSI"}, shares=5_000)]
        got, published, fmp = await _fox_week("insider_buys", rows)
        assert [r.company.symbol for r in published] == ["GME"]
        assert "FOX" not in fmp.profile_requests[0]
    assert got.rejections == {"congress_name": 1}
    assert "pelosi" not in caplog.text.lower() and "nancy" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_a_member_named_only_in_the_issuer_read_is_dropped():
    """The walk holds only the "PELOSI" P line; the per-issuer read (every code) also holds the
    person's F-coded row filed as "PELOSI NANCY" — the row is refused (`congress_name`)."""
    member = {**_FOX_DIR, "cik": "0000000444"}
    rows = [_line({**member, "name": "PELOSI"}, shares=5_000),
            _line({**member, "name": "PELOSI NANCY"}, shares=1_000, tx="F-InKind")]
    got, published, fmp = await _fox_week("insider_buys", rows)
    assert [r.company.symbol for r in published] == ["GME"] and got.rejections == {"congress_name": 1}
    assert "FOX" in fmp.profile_requests[0]                     # caught by the issuer read
    got, published, _fmp = await _fox_week("insider_buys", rows[:1])                 # control
    assert "FOX" in [r.company.symbol for r in published] and got.rejections == {}


_FOX_CFO = dict(sym="FOX", name="SMITH JOHN", cik="0000000111", title="officer: Chief Financial Officer")
_OTHER = dict(name="DOE JANE", cik="0000000999")


@pytest.mark.parametrize("series, base, other, refused", [
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "officer: Co-Chief Executive Officer"}, True),
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "officer: Chief Executive Officer"}, True),
    ("ceo_buys", _FOX_CEO, {"tx": "A-Award", "title": "director, other: Co-CEO"}, True),
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "officer: Joint CEO"}, True),
    ("ceo_buys", _FOX_CEO, {"tx": "M-Exempt", "title": "officer: President and Chief Executive Officer"}, True),
    ("insider_buys", _FOX_CFO, {"tx": "F-InKind", "title": "officer: Co-Chief Financial Officer"}, True),
    ("insider_buys", _FOX_CFO, {"tx": "F-InKind", "title": "officer: Chief Financial Officer"}, True),
    # controls: another role, a former officer, the person's own other row
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "director"}, False),
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "officer: Chief Financial Officer"}, False),
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "officer: Former CEO"}, False),
    ("insider_buys", _FOX_CFO, {"tx": "F-InKind", "title": "officer: Chief Executive Officer"}, False),
    ("ceo_buys", _FOX_CEO, {"tx": "F-InKind", "title": "director, officer: Chief Executive Officer",
                            "name": "SMITH JOHN", "cik": "0000000111"}, False),
], ids=["f_co_ceo", "f_second_ceo", "a_other_co_ceo", "f_joint_ceo", "m_president_ceo", "f_co_cfo",
        "f_second_cfo", "director", "cfo_beside_ceo", "former_ceo", "ceo_beside_cfo", "own_row"])
@pytest.mark.asyncio
async def test_the_issuer_read_finds_a_co_officer_or_a_second_one(series, base, other, refused):
    """r9 #3: the co-officer and two-officer checks ran on the P walk only; a co-CEO (or a second
    CEO / CFO) whose same-week rows are tax withholding or grants — codes the walk never returns
    — left the buyer published as THE CEO. The per-issuer read now refuses the row
    (`ambiguous_ceo`)."""
    second = _line(base, **{**_OTHER, "shares": 1_000, "filed": "2026-11-11", **other})
    got, published, fmp = await _fox_week(series, [_line(base), second])
    if refused:
        assert [r.company.symbol for r in published] == ["GME"]          # the survivor stands
        assert got.rejections == {"ambiguous_ceo": 1}
        assert "FOX" in fmp.profile_requests[0]              # the walk saw one officer; the read two
    else:
        assert "FOX" in [r.company.symbol for r in published] and got.rejections == {}


@pytest.mark.parametrize("second_cik", [issuer_cik("FOX"), None, "0000000000"], ids=["same_cik", "no_cik", "junk_cik"])
@pytest.mark.asyncio
async def test_two_cfos_on_one_symbol_drop_its_cfo_lines_never_its_directors(second_cik):
    """r9 #4: two people filing as plain "Chief Financial Officer" published one as "Fox's CFO".
    CFOs are grouped like CEOs — only the symbol's CFO lines go; its director stands."""
    rows = [irow(**_FOX_CFO, shares=15_000, price=30.0, filed="2026-11-10", traded="2026-11-09"),
            irow(sym="FOX", name="ROE RICHARD", cik="0000000444", title="officer: Chief Financial Officer",
                 shares=5_000, price=30.0, filed="2026-11-10", traded="2026-11-09", companyCik=second_cik),
            irow(sym="FOX", name="DOE JANE", cik="0000000555", title="director", shares=5_000, price=30.0,
                 filed="2026-11-10", traded="2026-11-09")]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=_fox_profiles()))
    (fox,) = got.records[0].rows
    assert (fox.role, fox.person_name, fox.amount_usd) == ("director", "Jane Doe", pytest.approx(150_000.0))
    assert got.rejections == {"ambiguous_ceo": 1}
    A.clear_memo()
    got = await run("insider_buys", FakeFMP(insider=[rows[0], rows[2]], profiles=_fox_profiles()))   # control
    (fox,) = got.records[0].rows
    assert (fox.role, fox.person_name) == ("cfo", "John Smith") and got.rejections == {}


@pytest.mark.asyncio
async def test_a_purchase_only_the_issuer_read_holds_refuses_the_person():
    """r9 #5: the walk's pages can stop short of one of the person's lines; the per-issuer read
    holds it — the row would understate the filings, so the person is refused."""
    line = _line(_FOX_CEO)
    extra = _line(_FOX_CEO, shares=3_000, price=30.2, filed="2026-11-11", traded="2026-11-10")
    profiles = {**week_profiles(), **_fox_profiles()}
    got = await run("ceo_buys", FakeFMP(insider=[line, irow()], profiles=profiles,
                                         issuer_feed={_FOX_ISSUER: [line, extra]}))
    assert [r.company.symbol for r in got.records[0].rows] == ["GME"] and got.rejections == {"partial_person": 1}
    A.clear_memo()
    got = await run("ceo_buys", FakeFMP(insider=[line, irow()], profiles=profiles,
                                         issuer_feed={_FOX_ISSUER: [line, {**extra, "transactionType": "S-Sale"}]}))
    assert "FOX" in [r.company.symbol for r in got.records[0].rows] and got.rejections == {}


_KEEP = object()


def _roster_aged(tmp_path, fetched_on, congress_start=_KEEP):
    """A copy of the shipped roster (its members) with ``fetched_on`` / ``congress_start`` set (None
    removes the key; ``_KEEP`` keeps the shipped value)."""
    doc = json.loads(Path(R.CONGRESS_ROSTER_PATH).read_text(encoding="utf-8"))
    for key, value in (("fetched_on", fetched_on), ("congress_start", congress_start)):
        if value is None:
            doc.pop(key)
        elif value is not _KEEP:
            doc[key] = value
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _hd_member_week():
    rows = week_rows() + [irow(sym="HD", name="PELOSI NANCY", cik="0000000444", shares=20_000, price=300.0)]
    profiles = {**week_profiles(), "HD": prof("HD", "The Home Depot, Inc.", price=300.0, cap=300e9)}
    return rows, profiles


@pytest.mark.parametrize("fetched_on, congress_start, named, error", [
    ("2026-10-09", _KEEP, True, False),           # the shipped roster: 38 days before RUN, the 119th
    ("2026-05-01", _KEEP, True, True),            # 199 days: still named, ERROR (refresh it)
    ("2025-10-12", _KEEP, True, True),            # 400 days: the last day it is fresh
    ("2025-10-11", _KEEP, False, True),           # 401 days: the series is refused
    ("2019-01-01", "2017-01-03", False, True),
    (None, _KEEP, False, True),                   # no fetched_on: its age is unknown
    ("not-a-date", _KEEP, False, True),
    ("2026-10-09", None, False, True),            # rr11: no congress_start — which Congress?
    ("2026-10-09", "2023-01-03", False, True),    # rr11: holds the 118th while the 119th sits
], ids=["fresh", "warn", "at_the_limit", "past_the_limit", "years_old", "missing", "unreadable",
        "no_congress_start", "earlier_congress"])
@pytest.mark.asyncio
async def test_a_roster_not_fresh_for_the_run_date_refuses_the_form_4_series(monkeypatch, tmp_path, caplog,
                                                                             fetched_on, congress_start, named, error):
    """r9 #27 → rr11 #2 (main-session decision 2026-10-10): a roster that is undated, older than
    `ROSTER_MAX_AGE_DAYS` (from the RUN date, never the clock) or not holding the sitting Congress
    cannot know every member, and role-only would still point at one it misses — so both Form 4
    series are REFUSED (`internal_error`, a detail naming the roster, ERROR, no FMP call), never run
    role-only. A fresh roster names people; members it knows are still dropped."""
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, fetched_on, congress_start))
    try:
        caplog.set_level(logging.ERROR)
        rows, profiles = _hd_member_week()
        for series in ("ceo_buys", "insider_buys"):
            caplog.clear()
            A.clear_memo()
            fmp = FakeFMP(insider=rows, profiles=profiles)
            if named:
                got = await run(series, fmp)
                assert fmp.calls["insider"] == 1
                if series == "ceo_buys":
                    gme = got.records[0].rows[0]
                    assert gme.company.symbol == "GME" and gme.person_name == "Ryan Cohen"
                    assert got.rejections["congress_name"] == 1
            else:
                with pytest.raises(A.MarketingNewsUnavailable) as err:
                    await run(series, fmp)
                assert err.value.series == series and err.value.reason == "internal_error"
                assert "congress roster" in err.value.detail and "sitting Congress" in err.value.detail
                assert fmp.calls == Counter(), "refused before any FMP call"
                assert any(r.levelno == logging.ERROR and r.name == A.__name__ and "congress roster" in r.getMessage()
                           for r in caplog.records)
            assert any(r.levelno == logging.ERROR and r.name == R.__name__ and "congress roster" in r.getMessage()
                       for r in caplog.records) is error
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


# ── review round 9: 13F options / notes, the filing entity, the club books ────

OXY_CUSIP = "674599105"


@pytest.mark.parametrize("q3_extra, q2_change, moves, counts, dropped", [
    # shares in Q2, CALLS only in Q3: "No longer reported: Occidental" while the 13F lists OXY calls
    ([xrow(BERKSHIRE, 3, "OXY", OXY_CUSIP, 500_000, 100e6, putCallShare="Call")], None,
     [("CRWV", "newly_reported"), ("AAPL", "increased")],
     {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 0}, 1),
    # a PRN (convertible note) row of the same issuer prefix on the current book
    ([xrow(BERKSHIRE, 3, "OXY", "674599AB1", 1_000, 5e6, sharesType="PRN")], None,
     [("CRWV", "newly_reported"), ("AAPL", "increased")],
     {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 0}, 1),
    # calls in Q2, SHARES in Q3: "Newly reported: Occidental" while the previous 13F listed OXY calls
    ([xrow(BERKSHIRE, 3, "OXY", OXY_CUSIP, 2_000_000, 100e6)], "oxy_calls_only",
     [("CRWV", "newly_reported"), ("AAPL", "increased")],
     {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 0}, 1),
    # a PUT on AAPL in Q2: the increase is dropped too, its count stays (still true of shares)
    ([], "aapl_put", [("CRWV", "newly_reported"), ("OXY", "no_longer_reported")],
     {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 1}, 1),
    # control: an option on ANOTHER issuer changes nothing
    ([xrow(BERKSHIRE, 3, "SPY", "78462F103", 10_000, 6e6, putCallShare="Put")], None,
     [("CRWV", "newly_reported"), ("OXY", "no_longer_reported"), ("AAPL", "increased")],
     {"newly_reported": 1, "increased": 1, "decreased": 0, "no_longer_reported": 1}, 0),
], ids=["shares_to_calls", "notes_beside_exit", "calls_to_shares", "put_beside_increase", "other_issuer"])
@pytest.mark.asyncio
async def test_a_move_whose_issuer_has_options_or_notes_is_dropped(q3_extra, q2_change, moves, counts, dropped):
    """r9 #7: the builder diffs SHARE rows only. A move whose issuer has a put / call / PRN row on
    the current or previous book is dropped (`move_has_options`), and a dropped newly / no-longer
    reported move leaves its kind's count ("no longer reports 1 holding" was false)."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + q3_extra
    if q2_change == "oxy_calls_only":
        extracts[(BERKSHIRE, 2026, 2)] = [r for r in extracts[(BERKSHIRE, 2026, 2)] if r["symbol"] != "OXY"] + [
            xrow(BERKSHIRE, 2, "OXY", OXY_CUSIP, 400_000, 80e6, putCallShare="Call")]
    elif q2_change == "aapl_put":
        extracts[(BERKSHIRE, 2026, 2)] = extracts[(BERKSHIRE, 2026, 2)] + [
            xrow(BERKSHIRE, 2, "AAPL", "037833100", 100_000, 20e6, putCallShare="Put")]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")], extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    (rec,) = got.records
    assert [(m.company.symbol, m.move) for m in rec.moves] == moves
    assert dict(rec.counts) == counts
    assert got.rejections.get("move_has_options", 0) == dropped


@pytest.mark.asyncio
async def test_a_person_filed_13f_is_refused_and_a_listed_companys_chip_never_rides_on_it():
    """r9 #9 / #23: CIK 0000921669 is labelled "Icahn Enterprises" (IEP) in the registry, but the
    13F is filed by Carl C. Icahn in his own name; "Icahn Enterprises' latest 13F" under IEP's
    logo attributed a person's filing to a listed company. A natural-person filer is refused
    before any FMP call (`filer_is_person`)."""
    icahn = "0000921669"
    assert R.THIRTEEN_F_FILERS[icahn] is None and icahn in A._registry_order()
    fmp, sb = registry_world([whale(icahn, "Icahn Enterprises", "IEP"), whale(SOROS, "Soros Fund Management")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.rejections == {"filer_is_person": 1}
    assert fmp.dates_ciks == [SOROS]
    assert [r.filer_name for r in got.records] == ["Soros Fund Management"]


@pytest.mark.parametrize("curated, shown", [("Pershing Square Capital", "Pershing Square Capital Management"),
                                            ("Someone Else Entirely", "Pershing Square Capital Management"),
                                            ("", "Pershing Square Capital Management")])
@pytest.mark.asyncio
async def test_the_subject_is_the_edgar_filer_never_the_curated_firm_name(curated, shown):
    fmp, sb = registry_world([whale(PERSHING, curated)])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records[0].filer_name == shown


@pytest.mark.parametrize("profile_cik, chip", [("0001096343", "MKL"), (issuer_cik("MKL"), None), (None, None)],
                         ids=["the_filers_own_listing", "another_entitys_listing", "no_profile_cik"])
@pytest.mark.asyncio
async def test_the_filer_chip_is_kept_only_when_its_profile_cik_is_the_filer(profile_cik, chip):
    markel = "0001096343"
    fmp, sb = registry_world([whale(markel, "Markel Group", "MKL")])
    fmp.profiles["MKL"] = prof("MKL", "Markel Group Inc.", cik=profile_cik)
    got = await run("thirteen_f", fmp, sb=sb)
    (rec,) = got.records
    assert (rec.filer_name, rec.filer_symbol) == ("Markel Group", chip)


def _alphabet_club_world(previous, changes, holdings=None):
    ch = [SimpleNamespace(**{"newly_listed": False, "shares": None, "prev_shares": None, "value": None, **c})
          for c in changes]
    club, sb = club_world(changes=ch, company_over={"total_value": 5e9, "change_counts": SimpleNamespace(
        newly_reported=sum(1 for c in changes if c["change"] == "newly_reported"), increased=0, decreased=0,
        no_longer_reported=sum(1 for c in changes if c["change"] == "no_longer_reported"))})
    club.details["nvidia"].holdings = holdings or []
    sb.tables["trillion_club_filings"] = [{"cik": "0001045810", "period": "2026-Q2", "holdings": previous}]
    return club, sb


@pytest.mark.parametrize("exit_row", [
    {"symbol": "GOOG", "name": "ALPHABET INC CL C", "change": "no_longer_reported", "prev_shares": 30_000.0},
    {"symbol": None, "name": "ALPHABET INC CL C", "change": "no_longer_reported", "prev_shares": 900_000.0},
], ids=["immaterial_exit", "unroutable_exit"])
@pytest.mark.asyncio
async def test_a_club_class_held_last_quarter_is_never_newly_reported(monkeypatch, exit_row):
    """r8/r9 #8 (a): the club path passed NO previous book — "Newly reported: Alphabet" (GOOGL)
    was published while the filer held class C last quarter, whenever the GOOG exit itself was
    dropped earlier (below the floor, or with no symbol). The stored previous build is the book."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    previous = [{"cusip": "02079K107", "symbol": "GOOG", "name": "Alphabet Inc.", "value": 3e6 if exit_row["symbol"] else 1.8e8},
                {"cusip": "21873S108", "symbol": "RXRX", "name": "Recursion", "value": 35e6}]
    club, sb = _alphabet_club_world(previous, [
        {"symbol": "GOOGL", "name": "Alphabet Inc.", "change": "newly_reported", "shares": 300_000.0, "value": 50e6},
        {"symbol": "CRWV", "name": "CoreWeave", "change": "newly_reported", "shares": 1_000_000.0, "value": 150e6,
         "newly_listed": True},
        exit_row])
    got = await run("thirteen_f", FakeFMP(profiles=_alphabet_profiles()), club=club, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [("CRWV", "newly_reported")]
    assert got.rejections.get("share_class_overlap") == 1


@pytest.mark.asyncio
async def test_a_club_exit_named_the_sec_way_beside_a_class_still_held_is_dropped(monkeypatch):
    """r9 #8 (b): an exit's change row carries the SEC issuer name ("NEWS CORP NEW") while the club's
    current book shows the display name ("News Corporation"): no name tie, and "No longer
    reported: News" was published while NWSA was still held. The PROFILED name ties them."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    previous = [{"cusip": "65249B208", "symbol": "NWS", "name": "News Corporation", "value": 180e6},
                {"cusip": "65249B109", "symbol": "NWSA", "name": "News Corporation", "value": 200e6}]
    holdings = [SimpleNamespace(symbol="NWSA", name="News Corporation", shares=1e6, value=2e8)]
    club, sb = _alphabet_club_world(previous, [
        {"symbol": "NWS", "name": "NEWS CORP NEW", "change": "no_longer_reported", "prev_shares": 6_000_000.0},
        {"symbol": "CRWV", "name": "CoreWeave", "change": "newly_reported", "shares": 1_000_000.0, "value": 150e6,
         "newly_listed": True}], holdings=holdings)
    profiles = {**thirteen_f_profiles(), "NWS": prof("NWS", "News Corporation", exchange="NASDAQ"),
                "NWSA": prof("NWSA", "News Corporation", exchange="NASDAQ")}
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [("CRWV", "newly_reported")]
    assert got.rejections == {"share_class_overlap": 1}
    # Control: with NWSA gone from the current book, the exit is published.
    A.clear_memo()
    club, sb = _alphabet_club_world(previous, [
        {"symbol": "NWS", "name": "NEWS CORP NEW", "change": "no_longer_reported", "prev_shares": 6_000_000.0},
        {"symbol": "CRWV", "name": "CoreWeave", "change": "newly_reported", "shares": 1_000_000.0, "value": 150e6,
         "newly_listed": True}], holdings=[])
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    assert ("NWS", "no_longer_reported") in [(m.company.symbol, m.move) for m in got.records[0].moves]


@pytest.mark.parametrize("name", ["ICAHN CARL C", "Leon G. Cooperman"])
@pytest.mark.asyncio
async def test_a_club_filer_that_reads_as_a_person_is_refused(monkeypatch, name):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world(card_over={"name": name})
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    assert got.records == () and got.rejections == {"filer_is_person": 1}


@pytest.mark.parametrize("sym, name, desc, refused", [
    ("JPM", "JPMorgan Chase & Co.", "JP Morgan Chase & Co. Common Stock", True),     # the 10-09 probe's wording
    ("XOM", "Exxon Mobil Corporation", "ExxonMobil Corp", True),
    ("UNH", "UnitedHealth Group Incorporated", "United Health Group Inc", True),
    # honest twins: the words line up only across a word boundary, or a name inside another word
    ("XOM", "Exxon Mobil Corporation", "Exxon Mobilization Trust", False),
    ("V", "Visa Inc.", "Advisa Holdings Common Stock", False),
    ("META", "Meta Platforms, Inc.", "Metals Acquisition Corp", False),
], ids=["jpm", "xom", "unh", "xom_twin", "visa_inside_a_word", "meta_inside_a_word"])
@pytest.mark.asyncio
async def test_a_ticker_less_purchase_naming_the_company_another_way_refuses_the_count(sym, name, desc, refused):
    """r9 #11: two House members buy JPM; a third (Senate) member's purchase is filed with no
    ticker as "JP Morgan Chase & Co." — the whole-word check missed it and the post said "2
    members" when 3 did. A word-aligned run-together match refuses the company (`count_uncertain`)."""
    house = [crow("house", sym, "2026-11-15", "h1"), crow("house", sym, "2026-11-16", "h2")]
    senate = [crow("senate", "", "2026-11-14", "s3", desc=desc)]
    profiles = {**congress_profiles(), sym: prof(sym, name, cap=5e11)}
    got = await crun(congress_fmp(senate=cfeed("senate", senate), house=cfeed("house", house), profiles=profiles))
    if refused:
        assert got.records == () and got.rejections.get("count_uncertain") == 1
    else:
        assert [(r.company.symbol, r.members) for r in got.records] == [(sym, 2)]
        assert "count_uncertain" not in got.rejections


def test_compact_named_is_word_aligned():
    assert A._compact_named(["jpmorgan", "chase"], "jp morgan chase & co. common stock")
    assert A._compact_named(["exxon", "mobil"], "exxonmobil corp")
    assert not A._compact_named(["visa"], "advisa holdings")
    assert not A._compact_named(["exxon", "mobil"], "exxon mobilization trust")
    assert not A._compact_named([], "anything") and not A._compact_named(["a"], "a b")
    assert not A._compact_named(["word"], "w " * 50_000)                  # linear


class _SpaceRevenue(FakeService):
    """ai-chips' lookups answer; every 'space' member's raises ``error`` or sleeps ``delay``."""

    def __init__(self, *, error=None, delay=0.0):
        super().__init__(theme_breakdowns(), default=breakdown(degraded=["segmentation_unavailable"]))
        self.space_error, self.delay = error, delay

    async def get_revenue_breakdown(self, sym):
        if sym in _SPACE:
            if self.delay:
                await asyncio.sleep(self.delay)
            raise self.space_error
        return await super().get_revenue_breakdown(sym)


_SPACE = ("RKLB", "ASTS", "LUNR", "PL", "IRDM", "VSAT")


class _SpaceProfilesDown(FakeFMP):
    async def get_company_profiles_batch(self, symbols):
        if set(symbols) & set(_SPACE):
            raise FMPUnavailableException("profile batch down")
        return await super().get_company_profiles_batch(symbols)


@pytest.mark.parametrize("variant", ["lookups_fail", "lookups_slow", "profiles_fail"])
@pytest.mark.asyncio
async def test_a_held_theme_survives_the_next_themes_failed_lookups(variant, caplog):
    """r9 #10: theme 1 qualified; theme 2's lookups failed (429s), ran out of budget, or its
    profile batch failed — and the series raised, throwing the fully checked theme 1 away. The
    held record is returned (WARNING); with nothing held the series still raises."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    profiles = {**theme_profiles(), **{s: prof(s, f"{s} Space Corp.", exchange="NASDAQ") for s in _SPACE}}
    rows = [theme(), theme(slug="space", title="Space", tickers=list(_SPACE), blocked_tickers=[], sort_order=2)]
    revenue = _SpaceRevenue(error=FMPRateLimitException("429 Too Many Requests"),
                            delay=5.0 if variant == "lookups_slow" else 0.0)
    fmp = (_SpaceProfilesDown if variant == "profiles_fail" else FakeFMP)(profiles=profiles)
    world = dict(fmp=fmp, revenue=revenue, sb=FakeSB({"trending_themes": rows}))
    got = await trun(world, left=2.5 if variant == "lookups_slow" else 60.0)
    assert [r.slug for r in got.records] == ["ai-chips"]
    assert "unavailable at theme space with 1 record(s) held" in caplog.text
    # Nothing held (theme 2 alone): the series raises, as before.
    A.clear_memo()
    world = dict(fmp=(_SpaceProfilesDown if variant == "profiles_fail" else FakeFMP)(profiles=profiles),
                 revenue=_SpaceRevenue(error=FMPRateLimitException("429 Too Many Requests"),
                                       delay=5.0 if variant == "lookups_slow" else 0.0),
                 sb=FakeSB({"trending_themes": rows[1:]}))
    with pytest.raises(A.MarketingNewsUnavailable):
        await trun(world, left=2.5 if variant == "lookups_slow" else 60.0)
    await _settle_leads()


# ── review round 10: one person per row, a new Congress, the rename tie, club options ──

_PENN = dict(name="PENN ARTHUR H", cik="0001234560")
_PENNANT_CEO = "Mr. Arthur Howard Penn"


def _pennant_profiles():
    return {"PNNT": prof("PNNT", "PennantPark Investment Corp", price=9.0, cap=600e6, ceo=_PENNANT_CEO),
            "PFLT": prof("PFLT", "PennantPark Floating Rate Corp.", price=10.0, cap=900e6, ceo=_PENNANT_CEO)}


def _pennant_week(series, second):
    """PennantPark-shaped: one person (CEO of both externally managed funds, or a director of both)
    buying at PNNT ($900k) and PFLT ($400k) in one week, beside the week's other rows."""
    title = "officer: Chief Executive Officer" if series == "ceo_buys" else "director"
    rows = week_rows() + [
        irow(sym="PFLT", shares=40_000, price=10.0, title=title, **second),
        irow(sym="PNNT", shares=100_000, price=9.0, title=title, **_PENN),
        irow(sym="KO", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0),
    ]
    profiles = {**week_profiles(), **_pennant_profiles(),
                "KO": prof("KO", "The Coca-Cola Company", price=30.0, cap=300e9)}
    return rows, profiles


@pytest.mark.parametrize("series", ["ceo_buys", "insider_buys"])
@pytest.mark.parametrize("second, one_person", [
    (_PENN, True),                                                     # the same CIK and name
    (dict(name="PENN ARTHUR", cik="0001234560"), True),                # the CIK alone ties them
    (dict(name="PENN ARTHUR H", cik=None), True),                      # no CIK: the name ties them
    # Another CIK, the name differing only by its middle initial: the template's person key drops
    # the initial and would refuse the WHOLE week (`record_invalid`), so the adapter ties them first.
    (dict(name="PENN ARTHUR J", cik="0001234599"), True),
    (dict(name="QXOTHER JANE", cik="0009999990"), False),              # control: two people
], ids=["same_cik_and_name", "same_cik", "same_name_no_cik", "middle_initial_only", "two_people"])
@pytest.mark.asyncio
async def test_one_person_buying_at_two_issuers_is_one_row(series, second, one_person):
    """rr10 lens-1 #1: the week counts PEOPLE ("At least 2 CEOs disclosed buying …"), but rows were
    deduped by issuer only, so one CEO of PNNT and PFLT was two CEOs — and the caption named the one
    person twice. One row per person across issuers (`_person_keys`: the reporting CIK, else the
    name), the person's largest row kept (`same_person`). The week the adapter returns is one the
    template counts as distinct people (`news_templates._distinct_people`), never one it refuses."""
    from app.services.marketing import news_templates as NT

    rows, profiles = _pennant_week(series, second)
    got = await run(series, FakeFMP(insider=rows, profiles=profiles))
    assert NT._distinct_people(got.records[0].rows) == len(got.records[0].rows)
    shown = [r.company.symbol for r in got.records[0].rows]
    expected = {"ceo_buys": ["GME", "FOX", "PNNT", "PFLT"], "insider_buys": ["PNNT", "KO", "PFLT"]}[series]
    if one_person:
        expected = [s for s in expected if s != "PFLT"]
    assert shown == expected
    assert got.rejections.get("same_person", 0) == (1 if one_person else 0)
    if one_person and series == "ceo_buys":
        (pnnt,) = [r for r in got.records[0].rows if r.company.symbol == "PNNT"]
        assert pnnt.person_name == "Arthur H. Penn" and pnnt.amount_usd == 900_000.0


@pytest.mark.asyncio
async def test_the_persons_largest_row_that_passes_the_check_is_the_one_kept():
    """rr10 lens-1 #1: the person's row is chosen AFTER the per-issuer Form 4/A check — when the
    larger row (PNNT) fails it, the person's next row (PFLT) is the week's, never neither."""
    rows, profiles = _pennant_week("ceo_buys", _PENN)
    fmp = FakeFMP(insider=rows, profiles=profiles,
                  issuer_feed={A.normalize_cik(issuer_cik("PNNT")): FMPUnavailableException("issuer read down")})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX", "PFLT"]
    assert got.rejections.get("amendment_check_failed") == 1 and "same_person" not in got.rejections


def test_person_keys_tie_a_cik_or_a_name_and_never_an_empty_key():
    line = lambda reporter, name: SimpleNamespace(reporter=reporter, name_raw=name)  # noqa: E731
    a = A._person_keys([line("cik:0001234560", "PENN ARTHUR H")])
    assert a & A._person_keys([line("cik:0001234560", "Arthur Penn")])            # the CIK
    assert a & A._person_keys([line("name:arthur h penn", "Penn, Arthur H")])     # the name's words
    assert not a & A._person_keys([line("cik:0009999990", "DOE JANE")])
    assert a & A._person_keys([line("cik:0001234599", "PENN ARTHUR J")])         # initials aside
    assert a & A._person_keys([line("cik:0001234599", "Arthur Penn")])
    # A single multi-letter word is too broad to tie on: "SMITH J" and "SMITH K" stay two people.
    assert not A._person_keys([line("cik:1", "SMITH J")]) & A._person_keys([line("cik:2", "SMITH K")])
    assert "" not in A._person_keys([line("", None)])
    assert not A._person_keys([line("", None)]) & A._person_keys([line("", "")])


def _week_on(day_traded, day_filed):
    rows = [irow(traded=day_traded, filed=day_filed),
            irow(sym="FOX", name="SMITH JOHN JR", cik="0000000111", shares=40_000, price=30.0,
                 traded=day_traded, filed=day_filed),
            irow(sym="HD", name="PELOSI NANCY", cik="0000000444", shares=20_000, price=300.0,
                 traded=day_traded, filed=day_filed)]
    profiles = {**week_profiles(), "HD": prof("HD", "The Home Depot, Inc.", price=300.0, cap=300e9)}
    return rows, profiles


@pytest.mark.parametrize("fetched_on, congress_start, run_date, traded, filed, named", [
    # the shipped roster (fetched 2026-10-09, the 119th Congress)
    ("2026-10-09", "2025-01-03", date(2026, 12, 31), "2026-12-28", "2026-12-29", True),
    ("2026-10-09", "2025-01-03", date(2027, 1, 3), "2026-12-30", "2026-12-31", False),   # the 120th sits
    ("2026-10-09", "2025-01-03", date(2027, 1, 16), "2027-01-12", "2027-01-13", False),  # round 10 named here
    ("2026-10-09", "2025-01-03", date(2027, 1, 18), "2027-01-12", "2027-01-13", False),  # round 10: role-only
    # rr11 #1: a January 4 refresh still holding the 119th (the script now refuses to write it)
    ("2027-01-04", "2025-01-03", date(2027, 1, 18), "2027-01-12", "2027-01-13", False),
    # a roster holding the 120th Congress, refreshed the day it was sworn in
    ("2027-01-03", "2027-01-03", date(2027, 1, 3), "2026-12-30", "2026-12-31", True),
    ("2027-01-03", "2027-01-03", date(2027, 1, 18), "2027-01-12", "2027-01-13", True),
], ids=["dec31", "sworn_in", "old_grace", "after_old_grace", "jan4_refresh", "120th_on_the_day", "120th_later"])
@pytest.mark.asyncio
async def test_a_roster_from_before_the_sitting_congress_refuses_the_form_4_series(
        monkeypatch, tmp_path, caplog, fetched_on, congress_start, run_date, traded, filed, named):
    """rr10 #1 → rr11 #1 / #2: from the odd-year January 3 a new Congress is sworn in, a roster
    holding the previous one cannot know the new members — round 10 still NAMED them for 14 days
    (a new member's Form 4 buy published by name) and then ran role-only, which still points at
    them. Now both Form 4 series are refused from that day — no grace — until the roster holds the
    sitting Congress; a member the list knows is still dropped while it is fresh."""
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, fetched_on, congress_start))
    try:
        caplog.set_level(logging.ERROR, logger=R.__name__)
        rows, profiles = _week_on(traded, filed)
        for series in ("ceo_buys", "insider_buys"):
            A.clear_memo()
            fmp = FakeFMP(insider=rows, profiles=profiles)
            if named:
                got = await run(series, fmp, run_date=run_date)
                assert fmp.calls["insider"] == 1
                if series == "ceo_buys":
                    assert got.rejections["congress_name"] == 1
                    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX"]
                    assert got.records[0].rows[0].person_name == "Ryan Cohen"
            else:
                with pytest.raises(A.MarketingNewsUnavailable) as err:
                    await run(series, fmp, run_date=run_date)
                assert err.value.reason == "internal_error" and "congress roster" in err.value.detail
                assert fmp.calls == Counter()
        assert any(r.levelno == logging.ERROR and "congress roster" in r.getMessage()
                   for r in caplog.records) is (not named)
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


@pytest.mark.asyncio
async def test_a_stale_roster_day_falls_back_along_the_chain(monkeypatch, tmp_path, caplog):
    """rr11 #2, end to end through the REAL template chain (`MarketingScriptService._build_template_day`)
    over the REAL adapter: with a roster holding an earlier Congress, ceo_buys and insider_buys are
    each refused as `unavailable(internal_error)` and the day falls to the next step of the chain
    (here the lesson) — no Form 4 record, no FMP call."""
    from app.services.marketing import script_service as ss

    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, "2026-10-09", "2023-01-03"))
    rows, profiles = _hd_member_week()
    fmp = FakeFMP(insider=rows, profiles=profiles)

    async def candidates(series, *, run_date, exclude, limit, deadline):
        return await A.candidates(series, run_date=run_date, exclude=exclude, limit=limit, deadline=deadline,
                                  deps=A.NewsDeps(fmp=fmp))

    async def no_logo(symbol, *, max_bytes, timeout):
        raise AssertionError("no record, no logo")

    news = SimpleNamespace(MarketingNewsUnavailable=A.MarketingNewsUnavailable, candidates=candidates,
                           fetch_logo=no_logo)

    class Runs:
        async def get_script(self, run_id):
            return None

        async def recent_source_refs(self, run_date, limit):
            return []

        async def insert_script(self, row):
            raise AssertionError("no Form 4 template row may be written")

    lessons = []

    async def lesson(run, run_date, recent, *, classes, selection_block):
        lessons.append(selection_block)
        return {"status": "accepted", "template_id": "lesson", "fact_sheet": {"selection": selection_block}}

    svc = ss.MarketingScriptService(Runs(), news=news)
    monkeypatch.setattr(svc, "_select_lesson", lesson)
    chain = ("ceo_buys", "insider_buys", selection.LESSON)
    try:
        caplog.set_level(logging.WARNING)
        row = await svc._build_template_day({"id": "run-1"}, RUN, selection.DayPlan(False, chain, "monday"),
                                            chain, frozenset({"A", "C", "F"}))
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()
    assert row["template_id"] == "lesson" and len(lessons) == 1
    assert lessons[0]["trail"][:2] == [
        {"series": "ceo_buys", "outcome": "unavailable", "reason": "internal_error"},
        {"series": "insider_buys", "outcome": "unavailable", "reason": "internal_error"}]
    assert fmp.calls == Counter()
    assert any("congress roster" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


BLOCK_CUSIP = "852234103"


@pytest.mark.asyncio
async def test_a_renamed_tickers_move_is_not_tied_to_its_own_old_symbol():
    """rr10 #2: the profiled-name tie dropped a move whose display name matched a book entry under
    another symbol without comparing CUSIPs — so after the SQ → XYZ rename, "Increased: Block" was
    dropped as another class of ITSELF (the previous raw extract still carries "SQ"). The move's own
    CUSIP is exempt, as in `_class_overlaps`."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "XYZ", BLOCK_CUSIP, 1_300_000, 130e6, nameOfIssuer="BLOCK INC")]
    extracts[(BERKSHIRE, 2026, 2)] = extracts[(BERKSHIRE, 2026, 2)] + [
        xrow(BERKSHIRE, 2, "SQ", BLOCK_CUSIP, 1_000_000, 100e6, nameOfIssuer="BLOCK INC")]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    fmp.profiles = {**thirteen_f_profiles(), "XYZ": prof("XYZ", "Block, Inc.")}
    got = await run("thirteen_f", fmp, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported"), ("AAPL", "increased"), ("XYZ", "increased")]
    assert "share_class_overlap" not in got.rejections


@pytest.mark.asyncio
async def test_another_class_under_its_own_cusip_is_still_tied_by_the_profiled_name(caplog):
    """Control for the rename exemption: a DIFFERENT CUSIP (another issuer prefix, another SEC name)
    under another symbol with the same PROFILED name is still another class — the exit is dropped
    by the profiled-name tie (`share_class_overlap`)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = extracts[(BERKSHIRE, 2026, 3)] + [
        xrow(BERKSHIRE, 3, "NWSA", "65249B109", 1_000_000, 200e6, nameOfIssuer="NEWS CORP NEW")]
    extracts[(BERKSHIRE, 2026, 2)] = extracts[(BERKSHIRE, 2026, 2)] + [
        xrow(BERKSHIRE, 2, "NWSA", "65249B109", 1_000_000, 200e6, nameOfIssuer="NEWS CORP NEW"),
        xrow(BERKSHIRE, 2, "NWS", "99999X208", 6_000_000, 180e6, nameOfIssuer="NWS HLDGS CL B")]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    fmp.profiles = {**thirteen_f_profiles(), "NWS": prof("NWS", "News Corporation", exchange="NASDAQ"),
                    "NWSA": prof("NWSA", "News Corporation", exchange="NASDAQ")}
    got = await run("thirteen_f", fmp, sb=sb)
    assert "NWS" not in [m.company.symbol for m in got.records[0].moves]
    assert got.rejections.get("share_class_overlap") == 1
    assert "no_longer_reported NWS — its company is on the book under another class" in caplog.text


@pytest.mark.asyncio
async def test_a_club_filers_unchecked_options_rule_is_logged_once(monkeypatch, caplog):
    """rr10 #5 (a RECORDED residual, no behaviour change): the stored club builds keep share rows
    only (put / call / PRN rows are gone, only an `excluded_rows` count is kept), so the options /
    notes rule cannot run on the club path. Each club filer says so once, at INFO."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.INFO, logger=A.__name__)
    club, sb = club_world()
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [
        ("CRWV", "newly_reported"), ("RXRX", "no_longer_reported"), ("AAPL", "increased")]
    lines = [r for r in caplog.records if "options rule (move_has_options) is not applied" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "cik=0001045810" in lines[0].getMessage() and "move_has_options" not in got.rejections


@pytest.mark.asyncio
async def test_a_queued_row_of_a_kept_person_is_never_read_again():
    """rr10 lens-1 #1: a spare whose person already holds a kept row is dropped BEFORE its issuer
    read (`same_person`) — the per-issuer reads are bounded (`INSIDER_AMENDMENT_MAX_SYMBOLS`)."""
    big = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=200_000 - 1_000 * i, price=25.0)
           for i, s in enumerate(["QA", "QB", "QC", "QD"])]
    rows = big + [irow(sym="PNNT", shares=100_000, price=9.0, **_PENN),
                  irow(sym="PFLT", shares=40_000, price=10.0, **_PENN)]
    profiles = {**{s: prof(s, f"{s} Industries Inc.") for s in ("QA", "QB", "QC", "QD")}, **_pennant_profiles()}
    # Wave 1 = QA..QD + PNNT; QA's read fails, so a wave 2 opens for the spare — PFLT, Penn's again.
    fmp = FakeFMP(insider=rows, profiles=profiles,
                  issuer_feed={A.normalize_cik(issuer_cik("QA")): FMPUnavailableException("issuer read down")})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["QB", "QC", "QD", "PNNT"]
    assert got.rejections == {"amendment_check_failed": 1, "same_person": 1}
    assert issuer_reads(fmp) == ["PNNT", "QA", "QB", "QC", "QD"]          # PFLT never read
