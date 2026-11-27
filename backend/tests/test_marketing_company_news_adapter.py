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
* review round 15 (2026-10-10): Money Map's gross and operating profit are the statement's OWN lines
  (never revenue − cost of sales or gross − operatingExpenses), cross-checked to rounding; a theme's
  size counts COMPANIES (GOOG + GOOGL, one CIK, BRK.B / BRK-B are one). Every one mutation-checked.
* Batch 1 (2026-10-15), earnings: a report without ONE plausible revenue pair is refused whole
  (``revenue_implausible`` — Progressive's one-month row), a surprise under 5% is refused (exact at the
  edge), ONE quarterly-statement read places the quarter (Delta → Sep 30; ``period_mismatch`` /
  ``period_unverified``, a lagging statement never dates the figures), the ranking is by market cap across
  every profiled report (a Nurix-like one-off never leads; a larger company in the second profile batch
  call leads), and a kept record is never counted as a rejection. The members-elect window (owner
  decision 4): every Form 4 row is built with no person inside it. Every new guard mutation-checked.
  ``RUN`` sits inside that window, so an autouse fixture switches it off for the named Form 4 tests.
* Batch 1 (2026-10-15), Money Map (plan item 2): a fiscal year that ended 364+ days before the run is
  refused ``money_map_next_year_due`` (Apple FY2025 on Oct 15, 2026; the 363 / 364-day edge; a content
  refusal that never uses up an attempt) — so the Money Map tests run on ``MM_RUN`` (Sep 3, 2026), where
  the fixtures' fiscal 2025 is still the latest year; a label that names nothing (a 1-4 capital code off
  the allow-list — "NXE", "QCT", "CMBU", "PBNA" — or a generic "Product") folds into the rest while AWS
  stays, in a theme's segment fact too; live labels ("UNITED STATES", "Latin America (Segment)") are shown
  as the company writes them; and end to end the record's parts decide the template's wording ("by region"
  only for an all-regional map, "the United States" in running text only). Every new guard mutation-checked.
* The review of 2026-10-16, Money Map: a due year never counts toward the content cap (MM-4: nine due seeds
  ahead of a fresh one), is remembered from its statement read and refused before the fan-out next time, and
  a known due symbol is tried after the rest of its group, least recently checked first (the January
  scenario: one empty run of a new process, then the fresh year at once); McDonald's retired "High-Growth
  Markets" label is its International Operated Markets, for McDonald's only (MM-1); Walmart's stripped labels
  are shown as Walmart writes them (F5); a region acronym ("US") is named, never folded (MM-2).
* Batch 1 (2026-10-15), 13F (plan item 3): a filing whose split lookups the cap trimmed shows no more /
  fewer shares — those moves (``split_unchecked``) and both count keys go, end to end through the template;
  at the cap nothing is dropped; a club filer's stored build is re-checked from its stored books (no read
  for a book of at most 25 positions; a missing or garbage book fails closed); an unreadable
  ``split_unchecked`` fails closed. Pershing's registry CIK is read, built, keyed and named as its successor
  0002026053 (posted under either key → never read again; read once beside a curated successor row);
  Berkshire leads ahead of the club and the rest, whatever its registry place, under ONE live-build budget,
  and a lead filer that is also a club filer is read once. Every new guard mutation-checked.
* The review of 2026-10-16, 13F: EVERY increased / decreased row is re-checked for a split in its quarter (F1:
  the review's 3:2-after-a-20%-rally, 5:4-flat, 2:1-after-60%, 4:1-after-210% and 1:2-after-a-40%-fall shapes
  the shared filter never flags never print "more / fewer shares"; a split the build restated stays, one it kept
  raw goes; an adjustment, a failed lookup, a symbol-less row or more rows than the cap drop both kinds and
  counts; only the quarter's own splits count); a club filer's stored build must be complete (F2) and its
  STORED change rows — filed symbols, CUSIPs, tied to the detail by the counts — are the ones re-checked, which
  also closes F3; Pershing's hand-over quarter (the real 2026-Q1 / Q2 shapes) is refused before any call while
  Q3 against Q2 is kept; a curated-inactive successor is skipped with a WARNING (F4). `run` hands every call a
  hermetic seam (`SplitActions`). Every new guard mutation-checked.
* The round-3 review of 2026-10-20 (RR3-STAKES-TEMPLATE-WALL), company_stakes: the read refuses every stake ROW its
  template must refuse (a source title over 80 characters or with a word over 20, an investee-name word over 15, a
  committed-up-to basis off a commitment, a ``verified_on`` after the run; a local listing the template cannot draw
  is dropped) and the investor's name word cap after its profile — both directions at every bound, through
  `NT.compose`; the review's two probes (five such stakes at the head of the newest-first walk) post the stake behind
  them on the first day; a refusal only the template can make is remembered (`A.remember_template_refusal`) and that
  stake is offered after every other one (least recently refused first; an edit to its row lifts it, a
  re-verification does not). Every new guard mutation-checked.
* The Trillion Club stake finder (migration 205, 2026-10-28), company_stakes: a stake reaches a post only when a person
  reviewed it — ``origin`` seed or proposal, or auto with the owner's Keep stamp (``owner_kept_at``) — through the
  PostgREST filter (the real postgrest-py builder's exact query; the fake reads the ``or`` filter, `postgrest_or_match`)
  and through the strict re-check alone (``sb.honour_or = False``), over every origin / stamp shape; the two layers
  agree on every state the schema allows; the re-check runs after the row cap (a full page with auto rows is still
  ``stakes_truncated``) and before the known words (a left-out row's investee name never vouches for another row's
  title); left-out rows are counted in a log line, never named, never a rejection code; a database without 205 is
  ``club_unavailable``; every selected column is in the schema dump or a migration. Every new guard mutation-checked
  (in memory, 2026-10-28).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import fields as dataclasses_fields
from datetime import date, datetime, timedelta
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
#: Batch 1 (2026-10-15): Pershing Square's registry CIK is read as its SUCCESSOR filer
#: (`R.THIRTEEN_F_SUCCESSORS`; the old CIK filed only a 13F-NT for 2026-Q2).
PERSHING_FILER = "0002026053"


def filer(cik: str) -> str:
    """The EDGAR filer the adapter reads for a registry CIK: its successor, else itself."""
    return R.THIRTEEN_F_SUCCESSORS.get(cik, cik)


#: news-v23: the REAL post bounds (2-4 earnings companies, 2-3 Congress companies), read at import — what every test
#: starts and ends with, whatever `_per_company_bounds` (below) did.
_REAL_BOUNDS = (R.EARNINGS_DAY_COMPANIES, R.CONGRESS_MONTH_COMPANIES)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    A.clear_memo()
    A._registry_order.cache_clear()
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    assert (R.EARNINGS_DAY_COMPANIES, R.CONGRESS_MONTH_COMPANIES) == _REAL_BOUNDS, "a test leaked the post bounds"
    yield
    A.clear_memo()
    leaked = (R.EARNINGS_DAY_COMPANIES, R.CONGRESS_MONTH_COMPANIES) != _REAL_BOUNDS
    R.EARNINGS_DAY_COMPANIES, R.CONGRESS_MONTH_COMPANIES = _REAL_BOUNDS
    assert not leaked, "this test left the post bounds changed"


#: The REAL members-elect window (owner decision 4), kept before the autouse fixture below replaces it.
REAL_MEMBERS_ELECT_WINDOW = R.members_elect_window


@pytest.fixture(autouse=True)
def _names_outside_the_members_elect_window(monkeypatch):
    """`RUN` (Mon Nov 16, 2026) is INSIDE the members-elect window (Election Day Nov 3, 2026 through Jan 3,
    2027), where every Form 4 row is built with no person. The Form 4 tests here pin the NAMED rules
    (render, corroborate, the Congress drop) — what any day outside the window builds — so the window is
    switched off; the members-elect tests restore the real one (`REAL_MEMBERS_ELECT_WINDOW`)."""
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)


#: Owner decisions 2026-11-20 ("3 - 5 companies at once"): the REAL Form 4 minimum (three companies a post), read at
#: import — what `real_min_rows` restores.
REAL_INSIDER_MIN_ROWS = R.INSIDER_MIN_ROWS


@pytest.fixture(autouse=True)
def _form4_row_gates_count_from_one_company(monkeypatch):
    """The Form 4 tests here pin which ROWS survive each gate (amendments, titles, roles, the Congress drop, the
    issuer read) on a week of one or two companies — written before the owner's minimum of three companies a post
    (2026-11-20). Like the earnings / Congress per-company tests (`_per_company_bounds`), they run with the minimum
    lowered to ONE (`R.INSIDER_MIN_ROWS`: the adapter and the records read it at call time). The minimum itself — a
    week or a spotlight window of one or two companies is NO post, with its own skip reason — is pinned with the real
    constant by the tests that request `real_min_rows` (test_min3_*)."""
    monkeypatch.setattr(R, "INSIDER_MIN_ROWS", 1)


@pytest.fixture
def real_min_rows(monkeypatch):
    """The real Form 4 minimum (three companies), for the tests that pin it."""
    monkeypatch.setattr(R, "INSIDER_MIN_ROWS", REAL_INSIDER_MIN_ROWS)
    assert R.INSIDER_MIN_ROWS == 3


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
                 issuer_feed: Optional[Dict[str, Any]] = None, delisted: Any = None,
                 market_caps: Any = None) -> None:
        self.insider = [] if insider is None else insider
        # news-v23 phase 3 (the weekly theme post's "biggest" angle): FMP's market-capitalization batch — the rows
        # (`{symbol, date, marketCap}` and any canary key) answered for the symbols asked, an exception raised, or
        # None: no rows at all (every member without a value — the angle falls through)
        self.market_caps = market_caps
        self.market_cap_requests: List[List[str]] = []
        # news-v23 (the 13F BUY-OUT CHECK): FMP's delisted-companies list, newest first — `delisted` is the list of
        # pages (each a list of rows, or an exception raised for that page); by default one page whose only row is
        # long before any quarter in these tests (nothing delisted, the span covered)
        self.delisted = ([[{"symbol": "ZZOLD", "companyName": "Old Co", "exchange": "NYSE", "ipoDate": "1990-01-02",
                            "delistedDate": "2001-01-02"}]] if delisted is None else delisted)
        self.delisted_pages: List[int] = []
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

    async def get_market_cap_batch(self, symbols):
        self.calls["market_caps"] += 1
        self.market_cap_requests.append(list(symbols))
        if isinstance(self.market_caps, BaseException):
            raise self.market_caps
        # every configured row, as FMP would answer — rows for symbols not asked included (the adapter ignores them)
        return json.loads(json.dumps(self.market_caps or []))

    async def get_delisted_companies(self, page=0, limit=100):
        self.calls["delisted"] += 1
        self.delisted_pages.append(page)
        if page >= len(self.delisted):
            return []
        return self._answer(self.delisted[page])

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


def _or_terms(expr: str) -> List[str]:
    """The top-level terms of a PostgREST logic-tree body: ``a.in.(x,y),b.not.is.null`` → two terms."""
    terms, depth, start = [], 0, 0
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced or-filter {expr!r}")
        elif ch == "," and depth == 0:
            terms.append(expr[start:i])
            start = i + 1
    if depth:
        raise ValueError(f"unbalanced or-filter {expr!r}")
    terms.append(expr[start:])
    return terms


def postgrest_or_match(row: Dict[str, Any], expr: str) -> bool:
    """Does ``row`` pass PostgREST's ``or=(<expr>)``? Only the subset the adapter writes — ``col.in.(a,b)`` and
    ``col.is.null|true|false``, each optionally negated (``col.not.is.null``); anything else RAISES, so a filter
    this fake cannot read never passes (or fails) silently. A key the row lacks reads as NULL, and ``in`` on a NULL
    is SQL's unknown — false, negated or not."""
    hits = []
    for term in _or_terms(expr):
        col, dot, rest = term.partition(".")
        negate = rest.startswith("not.")
        op, dot2, value = (rest[4:] if negate else rest).partition(".")
        if not (col and dot and dot2):
            raise ValueError(f"unreadable or-term {term!r}")
        cell = row.get(col)
        if op == "in":
            if not (value.startswith("(") and value.endswith(")")) or '"' in value:
                raise ValueError(f"unreadable in-list {term!r}")
            if cell is None:
                hits.append(False)
                continue
            hit = isinstance(cell, str) and cell in value[1:-1].split(",")
        elif op == "is" and value in ("null", "true", "false"):
            hit = cell is None if value == "null" else cell is (value == "true")
        else:
            raise ValueError(f"or-term {term!r}: operator {op!r} is not one this fake reads")
        hits.append(hit != negate)
    return any(hits)


class FakeQuery:
    def __init__(self, sb: "FakeSB", table: str) -> None:
        self.sb, self.table, self.filters, self.in_filters, self.or_filters = sb, table, [], [], []

    def select(self, cols):
        self.sb.selects.append((self.table, cols))
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, key, values):
        self.in_filters.append((key, list(values)))
        return self

    def or_(self, filters, reference_table=None):
        if reference_table is not None:
            raise ValueError("the fake reads an or-filter on the queried table only")
        _or_terms(filters)                    # unreadable → raises at build time, as a typo should
        self.sb.ors.append((self.table, filters))
        self.or_filters.append(filters)
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
                and all(r.get(k) in vs for k, vs in self.in_filters)
                and (not self.sb.honour_or or all(postgrest_or_match(r, f) for f in self.or_filters))]
        return SimpleNamespace(data=rows)


class FakeSB:
    def __init__(self, tables=None, errors=None) -> None:
        self.tables = tables or {}
        self.errors = errors or {}
        self.calls: Counter = Counter()
        self.selects: List[Any] = []
        self.ors: List[Any] = []
        #: False plays a PostgREST that answered WITHOUT the ``or`` filter (an edit that dropped it, a proxy that
        #: lost the parameter): the rows it would have refused reach the adapter, whose own strict re-check is then
        #: what the test sees.
        self.honour_or = True

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


class SplitActions:
    """The corporate-actions seam (`NewsDeps.actions`), hermetic: `run` hands every call one with no split and no
    adjustment anywhere unless a test passes its own. The 13F builder's suspect lookups and the adapter's split
    re-check (the review of 2026-10-16, F1: `_builder.split_windows`) both read it. ``splits``: SYMBOL → FMP-shaped
    split rows; ``unclassified`` / ``fail`` / ``boom``: symbols whose adjustment probe answers True / whose split
    lookup answers None (could not derive) / whose split lookup raises. Records every symbol asked, in order."""

    def __init__(self, splits=None, unclassified=(), fail=(), boom=()) -> None:
        self.splits = {k.upper(): list(v) for k, v in (splits or {}).items()}
        self.unclassified, self.fail, self.boom = set(unclassified), set(fail), set(boom)
        self.looked_up: List[str] = []
        self.probed: List[str] = []
        self.windows: List[Any] = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.looked_up.append(t)
        self.windows.append((from_date, to_date))
        if t in self.boom:
            raise FMPUnavailableException("price series down")
        if t in self.fail:
            return None
        return [dict(r) for r in self.splits.get(t, [])]

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *, effective_from=None,
                                          effective_to=None):
        self.probed.append((t, effective_from, effective_to))
        return t in self.unclassified


def split_row(day: str, numerator: int, denominator: int) -> Dict[str, Any]:
    """One stock split in `get_split_rows`' FMP shape."""
    return {"date": day, "numerator": numerator, "denominator": denominator}


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


async def run(series, fmp=None, *, exclude=(), limit=5, left=60.0, run_date=RUN, theme_history=(), theme_angles=None,
              theme_classes=None, **deps):
    deps.setdefault("actions", SplitActions())      # hermetic: the production seam reads FMP and Supabase
    return await A.candidates(series, run_date=run_date, exclude=frozenset(exclude), limit=limit,
                              deadline=time.monotonic() + left, deps=A.NewsDeps(fmp=fmp, **deps),
                              theme_history=theme_history, theme_angles=theme_angles, theme_classes=theme_classes)


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
async def test_at_most_five_rows_the_five_largest_of_a_busy_week():
    """Owner decisions 2026-11-20 — the option he picked: "3 to 5 :: At least 3 companies, at most 5 (the 5 largest
    purchases when more qualify)". Ten qualifying CEOs → ONE record of the five largest totals, largest first (the
    build had kept eight; the final review of 2026-11-26 holds it to the owner's five, so the post's count, picture,
    cover and captions all cover the same five). Mutation-checked by hand: `INSIDER_MAX_ROWS = 8` kept eight."""
    syms = ["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH", "II", "JJ"]
    # One CEO per company (review round 10: one person on two issuers is ONE row — `same_person`).
    rows = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=10_000 * (i + 1), price=25.0)
            for i, s in enumerate(syms)]
    profiles = {s: prof(s, f"{s.title()} Industries Inc.") for s in syms}
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=profiles))
    rows_out = got.records[0].rows
    assert [r.company.symbol for r in rows_out] == ["JJ", "II", "HH", "GG", "FF"]
    assert R.INSIDER_MAX_ROWS == R.INSIDER_PICTURE_ROWS == 5
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


@pytest.mark.parametrize("series", ["ceo_buys", "insider_buys"])
@pytest.mark.asyncio
async def test_a_depositary_line_without_the_adr_flag_is_refused_for_a_form_4_post(series):
    """Batch 1 (2026-10-15, plan item 5): FMP named AgomAb's line "AgomAb Therapeutics N.V. American Depositary
    Shares" with `isAdr` False (the insider_buys preview of 2026-10-12), and the class-tail strip left a clean
    "AgomAb Therapeutics" — a post saying "disclosed buying … of the company's stock" about depositary shares.
    The insider profile gate refuses a non-common NAME now (`non_common_listing`, counted); the other issuer
    stands, and the record still round-trips."""
    profiles = week_profiles()
    profiles["FOX"].update(companyName="Fox Corporation American Depositary Shares")
    rows = week_rows()
    if series == "insider_buys":         # directors and CFOs only: one at each issuer
        rows += [irow(sym="FOX", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0),
                 irow(sym="GME", name="DOE JANE", cik="0000000222", title="director", shares=20_000, price=25.0)]
    got = await run(series, FakeFMP(insider=rows, profiles=profiles))
    assert got.rejections.get("non_common_listing") == 1
    (rec,) = got.records
    assert [r.company.symbol for r in rec.rows] == ["GME"]
    assert R.record_from_fact_sheet(json.loads(json.dumps(R.fact_sheet(rec, rejections=got.rejections,
                                                                      selection={})))) == rec


@pytest.mark.asyncio
async def test_a_vendor_typo_in_the_issuer_name_is_corrected_in_the_record():
    """FMP's NAUT profile reads "Nautilus Biotechnolgy, Inc."; the record (and so every post) names the company
    as it spells itself, through `DISPLAY_OVERRIDES` (plan item 5)."""
    rows = [irow(sym="NAUT", name="MALLICK PARAG", cik="0000000909", shares=100_000, price=3.0)]
    profiles = {"NAUT": prof("NAUT", "Nautilus Biotechnolgy, Inc.", exchange="NASDAQ", price=3.0, cap=3.0e8,
                             ceo="Dr. Parag Mallick")}
    got = await run("ceo_buys", FakeFMP(insider=rows, profiles=profiles))
    (rec,) = got.records
    assert rec.rows[0].company == R.CompanyRef("NAUT", "Nautilus Biotechnology")
    assert "Biotechnolgy" not in json.dumps(R.fact_sheet(rec, rejections=got.rejections, selection={}))


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
    """Every shipped series has a collector and no collector runs ahead of its series (news-v22, 2026-11-15: the
    spotlight pair shipped with its templates — the strict pin of before is back)."""
    assert set(A.COLLECTORS) == set(selection.SHIPPED_SERIES)
    assert set(selection.SPOTLIGHT_SERIES) <= set(selection.SHIPPED_SERIES)


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
    # Keyed by the FILER the adapter reads (Batch 1: a registry CIK's successor, else itself).
    extracts = (extracts if extracts is not None
                else {k: v for w in whales for k, v in book(filer(w["cik"])).items()})
    dates = dates if dates is not None else {filer(w["cik"]): DATES_Q3_Q2 for w in whales}
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
    extracts = {k: v for w in whales[1:] for k, v in book(filer(w["cik"])).items()}
    extracts[(BERKSHIRE, 2026, 3)] = [xrow(BERKSHIRE, 3, "AAPL", "037833100", 1, 1.0)] * 201
    extracts[(BERKSHIRE, 2026, 2)] = book(BERKSHIRE)[(BERKSHIRE, 2026, 2)]
    fmp, sb = registry_world(whales, extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING_FILER, SCION]
    assert got.rejections["book_too_large"] == 1
    assert BRIDGEWATER not in fmp.dates_ciks          # the cap (2 live builds) was reached


@pytest.mark.asyncio
async def test_a_memoized_build_is_not_a_live_build():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital"),
              whale(SCION, "Scion Asset Management")]
    fmp, sb = registry_world(whales)
    first = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in first.records] == [BERKSHIRE, PERSHING_FILER]
    extracts_before = fmp.calls["extract"]
    again = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in again.records] == [BERKSHIRE, PERSHING_FILER, SCION]
    assert fmp.calls["extract"] == extracts_before + 2  # only SCION was built live


@pytest.mark.asyncio
async def test_two_unavailable_filers_raise_and_one_is_counted():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital")]
    down = {(c, 2026, q): FMPUnavailableException("extract down")
            for c in (BERKSHIRE, PERSHING_FILER) for q in (2, 3)}
    fmp, sb = registry_world(whales, extracts=down)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run("thirteen_f", fmp, sb=sb)
    assert err.value.reason == "thirteen_f_unavailable"     # one strike in the lead step, one in the rest

    A.clear_memo()
    extracts = {**book(PERSHING_FILER), (BERKSHIRE, 2026, 3): FMPUnavailableException("down"),
                (BERKSHIRE, 2026, 2): FMPUnavailableException("down")}
    fmp, sb = registry_world(whales, extracts=extracts)
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING_FILER]
    assert got.rejections["filer_unavailable"] == 1


@pytest.mark.asyncio
async def test_a_failed_dates_read_counts_as_unavailable():
    whales = [whale(BERKSHIRE, "Berkshire Hathaway"), whale(PERSHING, "Pershing Square Capital")]
    fmp, sb = registry_world(whales, dates={BERKSHIRE: FMPRateLimitException("429"),
                                            PERSHING_FILER: FMPUnavailableException("down")})
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
    ciks = (rows[0].get("ciks") or ["0001045810"]) if rows else ["0001045810"]
    sb = FakeSB({"trillion_club_companies": rows if rows is not None else [
        {"slug": "nvidia", "ciks": ["0001045810"], "card_kind": "thirteen_f", "use_13f": True,
         "detail_symbol": "NVDA", "published": True}], "whales": [],
        # The stored previous-quarter build an exit is valued on (review round 7: materiality) — and, since
        # the review of 2026-10-16, the stored CURRENT build the more / fewer re-check reads (F1, F2).
        "trillion_club_filings": [{"cik": "0001045810", "period": "2026-Q2",
                                   "holdings": [{"symbol": "RXRX", "value": 35e6}, {"symbol": "GOOG", "value": 1.8e8}]},
                                  stored_current(min(ciks), card["period"], changes, company["change_counts"])]})
    return club, sb


def club_cusip(key: Any) -> str:
    """A deterministic 9-digit CUSIP for a fake club holding (the detail's rows carry none)."""
    return str(int.from_bytes(str(key).encode(), "big") % 10 ** 8).zfill(8) + "7"


def stored_current(cik: str, period: str, changes: Any, counts: Any, *, status: str = "complete",
                   holdings: Optional[List[Dict[str, Any]]] = None, rows: Any = None) -> Dict[str, Any]:
    """The club filer's STORED current build row (`trillion_club_filings`), consistent with its detail: the
    change rows as the builder writes them (filed symbol, CUSIP, both share counts), the same per-kind counts,
    ``build_status``. ``rows`` replaces the change rows (the detail keeps its own)."""
    def stored_row(ch: Any) -> Dict[str, Any]:
        sym = getattr(ch, "symbol", None)
        return {"cusip": club_cusip(sym or getattr(ch, "name", "")), "symbol": sym, "name": getattr(ch, "name", ""),
                "change": getattr(ch, "change", None), "shares": getattr(ch, "shares", None),
                "prev_shares": getattr(ch, "prev_shares", None), "value": getattr(ch, "value", None)}

    counts_d = dict(counts) if isinstance(counts, dict) else dict(vars(counts)) if counts is not None else None
    change_rows = rows if rows is not None else [stored_row(ch) for ch in changes or ()]
    book = holdings if holdings is not None else [
        {"cusip": r["cusip"], "symbol": r["symbol"], "shares": r["shares"], "value": r["value"]}
        for r in change_rows if isinstance(r, dict) and r.get("shares")]
    return {"cik": cik, "period": period, "holdings": book, "build_status": status,
            "changes": {"comparison": "quarter", "prev_period": R.period_label(*_tc_previous(period)),
                        "counts": counts_d, "rows": change_rows}}


def _tc_previous(period: str):
    year, q = int(period[:4]), int(period[-1])
    return (year - 1, 4) if q == 1 else (year, q - 1)


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
    def __init__(self, by_symbol, *, default=None, error=None, years=None):
        self.by_symbol, self.default, self.error = by_symbol, default, error
        self.calls: List[str] = []
        # phase 4 (2026-11-20): `get_segment_years` — {symbol: tuple of years (newest first) or an exception}; a symbol
        # not listed answers () (no older year)
        self.years: Dict[str, Any] = {} if years is None else years
        self.year_calls: List[str] = []

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

    async def get_segment_years(self, sym):
        self.year_calls.append(sym)
        value = self.years.get(sym, ())
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_profit_power(self, sym):
        return await self._get(sym)


def facts_fn(by_symbol=None, default=None):
    calls = []

    async def facts(sym, *, need_executives=True):
        calls.append((sym, need_executives))
        return (by_symbol or {}).get(sym, default or {"ticker": sym, "available": True, "sector": "Technology"})

    facts.calls = calls
    return facts


# The statement's own gross profit and operating income (review round 15: the bars are published
# AS REPORTED, from these lines; profit power's margins in `profit()` are the same lines, rounded).
INCOME_AAPL = [{"fiscalYear": "2025", "date": "2025-09-27", "reportedCurrency": "USD", "revenue": 350e9,
                "grossProfit": 150e9, "operatingIncome": 90e9, "netIncome": 95e9},
               {"fiscalYear": "2024", "date": "2024-09-28", "reportedCurrency": "USD", "revenue": 300e9,
                "grossProfit": 130e9, "operatingIncome": 80e9, "netIncome": 90e9}]


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


#: Batch 1 (2026-10-15): the Money Map tests run on Thu Sep 3, 2026, when Apple's fiscal 2025 (ended Sep 27,
#: 2025 — `INCOME_AAPL`) is still its LATEST year, 341 days on. On `RUN` (Nov 16, 2026) fiscal 2026 has ended
#: and been reported, so the freshness gate refuses fiscal 2025 there (`money_map_next_year_due`, pinned by its
#: own tests below, which name their run dates).
MM_RUN = date(2026, 9, 3)


async def run_mm(fmp=None, **kw):
    """`run("money_map", …)` on `MM_RUN`, unless the test names its own run date."""
    kw.setdefault("run_date", MM_RUN)
    return await run("money_map", fmp, **kw)


@pytest.mark.asyncio
async def test_money_map_happy_path(seed_aapl):
    world = money_world()
    got = await run_mm(world.pop("fmp"), **world)
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
    got = await run_mm(world.pop("fmp"), **world)
    assert got.records == () and got.skip_reason == "money_map_none_qualified"
    assert got.rejections == {reason: 1}


@pytest.mark.asyncio
async def test_money_map_facts_outage_is_unavailable(seed_aapl):
    world = money_world(facts=facts_fn(default={"available": False, "upstream": True}))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run_mm(world.pop("fmp"), **world)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.asyncio
async def test_money_map_breakdown_outage_is_unavailable(seed_aapl):
    world = money_world()
    world["revenue"] = FakeService({}, error=FMPUnavailableException("segmentation down"))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await run_mm(world.pop("fmp"), **world)
    assert err.value.reason == "revenue_unavailable"


@pytest.mark.parametrize("point, gross, operating", [
    ({"gross_margin": 50.0}, None, 90e9),                     # each line is checked on its own: operating
    ({"operating_margin": 30.0}, 150e9, None),                # profit no longer waits on the gross bar
    ({"gross_margin": 50.0, "operating_margin": 30.0}, None, None),
    ({"gross_margin": None, "operating_margin": None}, None, None),
])
@pytest.mark.asyncio
async def test_unverifiable_bars_are_none_never_guessed(seed_aapl, point, gross, operating):
    world = money_world(pp=profit(point=point))
    rec = (await run_mm(world.pop("fmp"), **world)).records[0]
    assert (rec.gross_profit_usd, rec.operating_profit_usd, rec.net_income_usd) == (gross, operating, 95e9)


def _probe_world(*, bd=None, row=None, point=None):
    """Review round 15's probe: revenue $100B, cost of sales $75B, FMP `operatingExpenses` $21B, but
    the statement's own operating income is $3.1B (3.1%) — e.g. a $0.9B restructuring charge FMP
    leaves out of operatingExpenses. gross − operatingExpenses = $4.0B sits 0.9 point from the
    reported margin, inside the old 1-point tolerance, and was published 29% high "as reported"."""
    breakdown_over = dict(revenue_sources=[src("iPhone", 60e9), src("Services", 30e9), src("Mac", 10e9)],
                          cost_of_sales=75e9, operating_expense=21e9, tax=0.5e9, net_income=2e9,
                          reported_revenue=100e9)
    income_row = {"fiscalYear": "2025", "date": "2025-09-27", "reportedCurrency": "USD", "revenue": 100e9,
                  "grossProfit": 25e9, "operatingIncome": 3.1e9, "netIncome": 2e9}
    margins = {"gross_margin": 25.0, "operating_margin": 3.1, "net_margin": 2.0}
    return money_world(bd=breakdown(**{**breakdown_over, **(bd or {})}), income=[{**income_row, **(row or {})}],
                       pp=profit(point={**margins, **(point or {})}))


@pytest.mark.parametrize("bd, row, point, gross, operating", [
    # The probe: the statement's $3.1B, never the derived $4.0B.
    ({}, {}, {}, 25e9, 3.1e9),
    # Only a derived figure exists (no operating income line, or FMP's 0 fill): omitted, even when
    # gross − operatingExpenses would match a margin exactly.
    ({}, {"operatingIncome": None}, {"operating_margin": 4.0}, 25e9, None),
    ({}, {"operatingIncome": 0}, {"operating_margin": 0.0}, 25e9, None),
    ({}, {"operatingIncome": "3.1e9"}, {}, 25e9, None),
    # A reported line that disagrees with profit power beyond rounding (0.3 point): omitted.
    ({}, {}, {"operating_margin": 3.4}, 25e9, None),
    ({}, {"operatingIncome": 3.1234e9}, {"operating_margin": 3.12}, 25e9, 3.1234e9),   # profit power's rounding
    # Larger than what it is carved from (the statement's own gross profit, gross bar not drawn).
    ({"cost_of_sales": None}, {"operatingIncome": 26e9}, {"operating_margin": 26.0}, None, None),
    # A reported operating LOSS is published as reported.
    ({}, {"operatingIncome": -5e9}, {"operating_margin": -5.0}, 25e9, -5e9),
    # Gross profit too: the statement's line, never revenue − cost of sales ($24.5B here) …
    ({"cost_of_sales": 75.5e9}, {}, {}, 25e9, 3.1e9),
    # … omitted when the statement has none — and the operating bar no longer depends on it.
    ({}, {"grossProfit": None}, {}, None, 3.1e9),
    # A company with no cost-of-sales line reports no gross profit (FMP fills revenue − 0).
    ({"cost_of_sales": 0.0}, {"grossProfit": 100e9}, {"gross_margin": 100.0}, None, 3.1e9),
], ids=["probe_reported", "no_line", "zero_fill", "string", "off_by_0_3pt", "rounding", "above_gross",
        "operating_loss", "gross_reported", "no_gross_line", "no_cost_of_sales"])
@pytest.mark.asyncio
async def test_money_map_bars_are_as_reported_never_derived(seed_aapl, bd, row, point, gross, operating):
    world = _probe_world(bd=bd, row=row, point=point)
    got = await run_mm(world.pop("fmp"), **world)
    (rec,) = got.records
    assert (rec.gross_profit_usd, rec.operating_profit_usd, rec.revenue_usd, rec.net_income_usd) == \
        (gross, operating, 100e9, 2e9)


@pytest.mark.asyncio
async def test_a_small_unnamed_gap_folds_into_other(seed_aapl):
    under = money_world(bd=breakdown(revenue_sources=[src("iPhone", 200e9), src("Services", 100e9), src("Mac", 43e9)]))
    rec = (await run_mm(under.pop("fmp"), **under)).records[0]
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
    got = await run_mm(world.pop("fmp"), **world)
    assert got.records == () and got.rejections == {"money_map_inconsistent": 1}


@pytest.mark.parametrize("elim", [None, 0.0, -5.0, float("nan"), "10e9", True])
@pytest.mark.asyncio
async def test_no_eliminations_figure_never_draws_a_bar(seed_aapl, elim):
    world = money_world(bd=breakdown(intersegment_eliminations=elim))
    (rec,) = (await run_mm(world.pop("fmp"), **world)).records
    assert rec.eliminations_usd is None and rec.other_usd == 20e9


@pytest.mark.asyncio
async def test_undrawable_duplicate_and_surplus_segments_fold_into_other(seed_aapl):
    sources = [src("iPhone", 100e9), src("Services", 80e9), src("Mac", 60e9), src("iPad", 40e9),
               src("Wearables", 30e9), src("Accessories (Retail)", 15e9), src("services", 10e9),
               src("Licensing", 5e9), src("Unallocated", 10e9)]
    world = money_world(bd=breakdown(revenue_sources=sources))
    rec = (await run_mm(world.pop("fmp"), **world)).records[0]
    assert [s.name for s in rec.segments] == ["iPhone", "Services", "Mac", "iPad", "Wearables"]
    assert rec.other_usd == pytest.approx(40e9)        # "(…)", the duplicate, the 6th, "Unallocated"


@pytest.mark.asyncio
async def test_a_lenders_interest_row_is_never_a_segment_on_a_post(seed_aapl):
    sources = [src("Interest Paid and Other", 40e9), src("iPhone", 100e9), src("Services", 80e9),
               src("Mac", 60e9), src("iPad", 40e9), src("Wearables", 30e9)]
    world = money_world(bd=breakdown(revenue_sources=sources))
    rec = (await run_mm(world.pop("fmp"), **world)).records[0]
    assert [s.name for s in rec.segments] == ["iPhone", "Services", "Mac", "iPad", "Wearables"]
    assert rec.other_usd == pytest.approx(40e9)


@pytest.mark.asyncio
async def test_a_posted_fiscal_year_is_skipped_before_any_profile_call(seed_aapl):
    world = money_world()
    fmp = world.pop("fmp")
    got = await run_mm(fmp, exclude={"news:money_map:AAPL:2025"}, **world)
    assert got.records == () and got.rejections == {"already_posted": 1}
    assert fmp.calls["profiles"] == 0 and fmp.calls["income"] == 0 and world["profit"].calls == []


@pytest.mark.asyncio
async def test_money_map_pool_order_and_attempt_cap(seed_aapl, monkeypatch):
    """Owner decision 2: the seed first, then club members (cards and also-in-club) — and never-posted symbols before
    posted ones. news-v23 (owner 2026-11-19, "make sure to use big or popular companies only"): the Emerging Frontiers
    theme members are NOT in the pool — the themes table is never even read."""
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
    got = await run_mm(world.pop("fmp"), exclude={"news:money_map:AAPL:2024"}, **world)
    assert world["revenue"].calls == ["AVGO", "TSM", "AAPL"]      # AAPL (posted before) goes last; no theme member
    assert got.rejections == {"money_map_degraded": 2}
    assert "trending_themes" not in {t for t, _cols in world["sb"].selects}


@pytest.mark.asyncio
async def test_news_v23_the_money_map_pool_is_the_seed_and_the_club_only(seed_aapl, monkeypatch):
    """Owner 2026-11-19 ("make sure to use big or popular companies only"): a company that is ONLY an Emerging Frontiers
    theme member is never a Money Map candidate — whatever the themes table holds, and however many seeds and club
    members are already posted (the walk then ends with nothing, never reaching a theme member). Mutation-checked by
    hand (2026-11-20): the theme source put back into `merge_money_map_pool`'s call, IONQ was read (red)."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    world = money_world()
    world["club"] = FakeClub(group=SimpleNamespace(
        companies=[SimpleNamespace(slug="broadcom", detail_symbol="AVGO", card_kind="no_thirteen_f")],
        also_in_club=[]))
    world["sb"] = FakeSB({
        "trillion_club_companies": [],
        "trending_themes": [{"slug": "quantum", "tickers": ["IONQ", "QBTS"], "blocked_tickers": [],
                             "sort_order": 1, "is_active": True}],
    })
    posted = {f"news:money_map:{sym}:2025" for sym in (*R.MONEY_MAP_SEED, "AVGO")}
    got = await run_mm(world.pop("fmp"), exclude=posted, **world)
    assert not {"IONQ", "QBTS"} & set(world["revenue"].calls)
    assert got.records == ()
    assert set(R.merge_money_map_pool(["AVGO"])) == set(R.MONEY_MAP_SEED) | {"AVGO"}
    assert not hasattr(A, "_theme_symbols")


@pytest.mark.asyncio
async def test_a_failed_pool_source_degrades_to_the_seed(seed_aapl, monkeypatch, caplog):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    world = money_world()
    world["club"] = FakeClub(error=RuntimeError("club down"))
    world["sb"] = FakeSB(errors={"trending_themes": RuntimeError("themes down")})
    caplog.set_level(logging.WARNING, logger=A.__name__)
    got = await run_mm(world.pop("fmp"), **world)
    assert len(got.records) == 1
    assert "club members unavailable" in caplog.text
    assert "theme members unavailable" not in caplog.text            # news-v23: the themes are no Money Map source


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
    syms = [f"S{c}" for c in "ABCDEFGHIJKLMN"]           # fourteen symbols, largest first
    # One CEO per company (review round 10: one person on two issuers is ONE row — `same_person`).
    rows = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=100_000 - 1_000 * i)
            for i, s in enumerate(syms)]
    profiles = {s: prof(s, f"{s} Industries Inc.") for s in syms}
    bad = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", tx="J-Other", filed="2026-11-14", form="4/A")
           for i, s in enumerate(syms[:4])]
    feed = {s: [r for r in rows + bad if r["symbol"] == s] for s in syms}
    fmp = FakeFMP(insider=rows, profiles=profiles, symbol_feed=feed)
    got = await run("ceo_buys", fmp)
    # Wave 1: SA..SE (four amended, SE kept); wave 2: three spares (SF, SG, SH) — the bound (five rows a record since
    # the final review of 2026-11-26, so the bound is 5 + 3, as on main).
    assert len(fmp.issuer_calls) == A.INSIDER_AMENDMENT_MAX_SYMBOLS == R.INSIDER_MAX_ROWS + 3 == 8
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
    got = await run_mm(fmp, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert got.rejections == {"money_map_inconsistent": 3}


@pytest.mark.asyncio
async def test_upstream_gaps_still_stop_at_the_attempt_cap(monkeypatch):
    seed = ("IBM", "ORCL", "CSCO", "AAPL")
    fmp, world = _money_pool(monkeypatch, seed, degraded=seed[:3])
    got = await run_mm(fmp, **world)
    assert got.records == () and got.rejections == {"money_map_degraded": 3}
    assert world["revenue"].calls == ["IBM", "ORCL", "CSCO"]          # AAPL is never read


@pytest.mark.asyncio
async def test_content_refusals_stop_at_their_own_cap(monkeypatch):
    gross = tuple(f"G{c}" for c in "ABCDEFGHI")                         # nine refused seeds
    fmp, world = _money_pool(monkeypatch, gross + ("AAPL",), gross=gross)
    got = await run_mm(fmp, **world)
    assert A.MONEY_MAP_MAX_CONTENT_REJECTIONS == 8
    assert got.records == () and got.rejections == {"money_map_inconsistent": 8}
    assert world["revenue"].calls == list(gross[:8])


@pytest.mark.asyncio
async def test_a_normal_money_map_week_still_ends_after_three_records(monkeypatch):
    """Records count as attempts, as before: four publishable seeds and a limit of 5 → 3 records."""
    seed = ("AAPL", "MSFT", "NVDA", "AMZN")
    fmp, world = _money_pool(monkeypatch, seed)
    got = await run_mm(fmp, **world)
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


#: `InsiderPurchase` fields added AFTER round 6 — additive and defaulted (2026-11-15: the filing's own cost per
#: share, owner decision "Captions + picture"). Round 6 never had them, so its byte-for-byte answer is compared
#: without them (each must be present, never silently absent); their values are pinned by their own tests.
_POST_ROUND_6_ROW_FIELDS = ("cost_per_share_min_usd", "cost_per_share_max_usd")


def _round_6_shape(record: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(record))
    for row in out["rows"]:
        for name in _POST_ROUND_6_ROW_FIELDS:
            assert name in row, name
            del row[name]
    return out


@pytest.mark.parametrize("week", ["ceo_week", "ceo_unit_offering", "insider_week"])
@pytest.mark.asyncio
async def test_a_week_with_no_amendment_is_byte_identical_to_round_6(week):
    series, rows, profiles = _no_amendment_weeks()[week]
    fmp = FakeFMP(insider=rows, profiles=profiles)
    got = await run(series, fmp)
    # Round 6 named its per-ticker reads by symbol; since round 9 the same companies are read
    # per ISSUER (by CIK) — named here by their symbols, so the pinned answer stays byte for byte.
    answer = {"records": [_round_6_shape(R.record_to_dict(r)) for r in got.records], "skip": got.skip_reason,
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
    # Batch 1 (2026-10-15): a 29-position book is above the split-lookup cap (25), so the adapter re-derives
    # the cap from the stored CURRENT book too (always there in production: the detail is built from it).
    # Neither book carries a split-shaped share jump here, so nothing was trimmed. The review of 2026-10-16:
    # that stored row also carries the change rows the split re-check reads and its build status (F1, F2).
    current = [{"symbol": s, "shares": sh, "value": v} for s, _n, _c, sh, _p, v in _BRK_Q2_CHANGES if sh]
    # The stored row holds EVERY change row its counts count (7 increased, 6 decreased); the live records kept
    # five of them, so the other eight are filler the re-check finds clean.
    stored = stored_current(BERKSHIRE, "2026-Q2", changes, _BRK_Q2_COUNTS, holdings=current)
    stored["changes"]["rows"] += [
        {"cusip": club_cusip(f"BRKFILL{i}"), "symbol": f"QF{chr(65 + i)}", "change": kind, "shares": sh,
         "prev_shares": p, "value": 1e6}
        for i, (kind, sh, p) in enumerate([("increased", 2.0, 1.0)] * 5 + [("decreased", 1.0, 2.0)] * 3)]
    sb = FakeSB({"trillion_club_companies": [{"slug": "berkshire-hathaway", "ciks": [BERKSHIRE],
                                              "card_kind": "thirteen_f", "use_13f": True,
                                              "detail_symbol": "BRK-A", "published": True}],
                 "trillion_club_filings": [{"cik": BERKSHIRE, "period": "2026-Q1", "holdings": previous}, stored],
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
        # beside the stored CURRENT build row, read first for every club filing with a move (2026-10-19)
        sb.tables["trillion_club_filings"] = previous + [sb.tables["trillion_club_filings"][1]]
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
    got = await run_mm(fmp, **world)
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
    got = await run_mm(fmp, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert got.rejections == {"money_map_mostly_other": 3} and world["revenue"].calls == list(seed)


@pytest.mark.asyncio
async def test_fmp_segment_labels_are_shown_as_the_company_writes_them(monkeypatch):
    sources = [src("Intelligent Cloud", 150e9), src("XBOX", 100e9), src("Linked In Corporation", 80e9),
               src("Service", 60e9), src("Services", 5e9), src("Other", 5e9)]
    fmp, world = _mm_world(monkeypatch, sources, revenue=400e9, net=100e9)
    (rec,) = (await run_mm(fmp, **world)).records
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
    return await A.candidates("money_map", run_date=MM_RUN, exclude=frozenset(), limit=5, deadline=45.0, deps=deps)


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
    got = await run_mm(fmp, **world)
    assert [r.company.symbol for r in got.records] == kept and world["revenue"].calls == reads


def test_every_segment_override_is_drawn_by_the_adapter():
    """Each override value passes the adapter's own segment gates (`_segments` draws it) — Batch 1: the label
    fold included (an override that produced a code or a generic label would be folded right back)."""
    for shown in R.SEGMENT_DISPLAY_OVERRIDES.values():
        assert A._SEGMENT_NAME_RE.fullmatch(shown) and shown.strip().lower() not in A._MONEY_MAP_REST_NAMES
        assert R.segment_fold_reason(shown) is None, shown


# ── Batch 1 (2026-10-15, plan item 2): Money Map freshness, labels, wording ───

from app.services.marketing import news_templates as NT  # noqa: E402  (the end-to-end checks compose)


@pytest.mark.parametrize("run_date, reason", [
    (date(2026, 10, 15), "money_map_next_year_due"),     # the plan's case: Apple FY2025, 383 days on
    (date(2026, 9, 25), None),                           # 363 days: fiscal 2026 has not ended
    (date(2026, 9, 26), "money_map_next_year_due"),      # 364 days (52 weeks): the next year may have ended
    (date(2025, 9, 27), None),                           # the year's last day itself
    (date(2027, 3, 29), "money_map_next_year_due"),      # 548 days: not yet stale, still next-year-due
    (date(2027, 3, 30), "money_map_stale"),              # 549 days: the older gate answers first
    (date(2025, 9, 26), "money_map_stale"),              # a year that has not ended on the run date
], ids=["plan_case_383", "edge_363", "edge_364", "day_0", "548", "549_stale", "future"])
@pytest.mark.asyncio
async def test_a_money_map_waits_for_the_next_fiscal_year_once_it_is_due(seed_aapl, run_date, reason, caplog):
    """A fiscal year that ended `MONEY_MAP_NEXT_YEAR_DUE_DAYS` (364) or more days before the run is refused
    `money_map_next_year_due`: Apple's fiscal 2025 (ended Sep 27, 2025) on Oct 15, 2026 — fiscal 2026 ended Sep 26
    and its 10-K is due — is last year's map. Mutation-checked by hand (2026-10-15): `>=` → `>` turns the 364
    edge red; removing the gate turns every refused row red."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    world = money_world()
    got = await run_mm(world.pop("fmp"), run_date=run_date, **world)
    if reason is None:
        (rec,) = got.records
        assert (rec.fiscal_year, rec.period_end) == ("2025", date(2025, 9, 27)) and got.rejections == {}
        return
    assert got.records == () and got.skip_reason == "money_map_none_qualified"
    assert got.rejections == {reason: 1}
    if reason == "money_map_next_year_due":
        age = (run_date - date(2025, 9, 27)).days
        assert f"AAPL FY2025 ended 2025-09-27, {age} days before the run" in caplog.text


def _fy2026(world, fmp, sym):
    """``sym`` on Apple's FISCAL 2026 (ended Sep 26, 2026): its breakdown, statement row and profit-power year."""
    world["revenue"].by_symbol[sym] = breakdown(symbol=sym, fiscal_year="2026")
    world["profit"].by_symbol[sym] = profit(point={"period": "2026"})
    fmp.income[sym] = [{**INCOME_AAPL[0], "fiscalYear": "2026", "date": "2026-09-26"}]


@pytest.mark.asyncio
async def test_a_next_year_due_map_is_never_an_attempt(monkeypatch):
    """Three seeds still on fiscal 2025 at the head of the pool (Oct 15, 2026) are refused — never an upstream
    attempt, so they never use up the three attempts: the fourth, on fiscal 2026, is read and kept. Mutation-
    checked by hand (2026-10-15): with the code in `_MONEY_MAP_UPSTREAM_GAPS` the fourth is never read."""
    seed = ("QA", "QB", "QC", "AAPL")
    fmp, world = _money_pool(monkeypatch, seed)
    _fy2026(world, fmp, "AAPL")
    got = await run_mm(fmp, run_date=date(2026, 10, 15), **world)
    assert [(r.company.symbol, r.fiscal_year) for r in got.records] == [("AAPL", "2026")]
    assert got.rejections == {"money_map_next_year_due": 3} and world["revenue"].calls == list(seed)
    assert "money_map_next_year_due" in R.REJECTION_REASONS
    assert "money_map_next_year_due" not in A._MONEY_MAP_UPSTREAM_GAPS


# ── the review of 2026-10-16 (MM-4): a due year is a date fact, remembered, and tried last ──────────────

_DUE_DAY = date(2026, 10, 15)          # `INCOME_AAPL`'s fiscal 2025 (ended Sep 27, 2025) is 383 days old: due


def _fan_out_calls(fmp, world):
    """The reads `_money_map_record` fans out per candidate after its breakdown: statements, profit power, facts,
    profiles."""
    return fmp.calls["income"], len(world["profit"].calls), len(world["facts"].calls), fmp.calls["profiles"]


@pytest.mark.asyncio
async def test_due_years_never_use_up_the_content_cap(monkeypatch):
    """Nine seeds on a due year ahead of a fresh one: before, each counted as a content refusal and the eighth
    ended the loop (`MONEY_MAP_MAX_CONTENT_REJECTIONS`) — from about Jan 1 to March the December year-ends at the
    head of the pool closed the series. A date fact now: only the breakdown reads bound it. Mutation-checked by
    hand (2026-10-19): counting it as a refusal again leaves AAPL unread."""
    due = tuple(f"D{c}" for c in "ABCDEFGHJ")                            # nine
    fmp, world = _money_pool(monkeypatch, due + ("AAPL",))
    _fy2026(world, fmp, "AAPL")
    got = await run_mm(fmp, run_date=_DUE_DAY, **world)
    assert [(r.company.symbol, r.fiscal_year) for r in got.records] == [("AAPL", "2026")]
    assert got.rejections == {"money_map_next_year_due": 9}
    assert world["revenue"].calls == [*due, "AAPL"]


@pytest.mark.asyncio
async def test_a_due_year_the_memo_knows_is_refused_before_the_fan_out(monkeypatch, caplog):
    """The year's end is remembered from its statement read: the next run refuses a breakdown still on that year
    without the statement / profit-power / facts / profile reads. Mutation-checked by hand (2026-10-19): with
    `_money_map_known_age` answering None, the fan-out counters move on the second run."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    fmp, world = _money_pool(monkeypatch, ("QA", "QB", "AAPL"))
    _fy2026(world, fmp, "AAPL")
    first = await run_mm(fmp, run_date=_DUE_DAY, **world)
    assert [r.company.symbol for r in first.records] == ["AAPL"]
    assert first.rejections == {"money_map_next_year_due": 2}
    before = _fan_out_calls(fmp, world)
    world["revenue"].calls.clear()
    second = await run_mm(fmp, run_date=_DUE_DAY + timedelta(days=1), exclude={"news:money_map:AAPL:2026"},
                          **world)
    assert second.records == () and second.skip_reason == "money_map_none_qualified"
    assert second.rejections == {"money_map_next_year_due": 2, "already_posted": 1}
    assert world["revenue"].calls == ["QA", "QB", "AAPL"]                 # one breakdown read each
    assert _fan_out_calls(fmp, world) == before                           # and nothing else
    assert "QA is still on FY2025 (ended 2025-09-27, 384 days before the run; last checked 2026-10-15)" in caplog.text


@pytest.mark.asyncio
async def test_known_due_years_go_last_so_the_series_reaches_a_fresh_year(monkeypatch):
    """The review's January scenario: twelve due years ahead of a fresh one use up every breakdown read
    (`MONEY_MAP_MAX_LOOKUPS`) of a NEW process — one run without a map — and the memo then puts them after the
    rest, so the next run reaches the fresh year at once. With a record held, due years count toward the old
    total cap (one more is read, then the loop ends). Mutation-checked by hand (2026-10-19): with the order back
    to plain pool order the second run is empty too; without `due` in the held-record total, every due year is
    read after the record."""
    due = tuple(f"D{c}" for c in "ABCDEFGHJKLM")                         # twelve: every read of a call
    fmp, world = _money_pool(monkeypatch, due + ("DN", "AAPL"))
    _fy2026(world, fmp, "AAPL")
    cold = await run_mm(fmp, run_date=_DUE_DAY, **world)
    assert A.MONEY_MAP_MAX_LOOKUPS == 12
    assert cold.records == () and cold.rejections == {"money_map_next_year_due": 12}
    assert world["revenue"].calls == list(due)
    world["revenue"].calls.clear()
    warm = await run_mm(fmp, run_date=_DUE_DAY + timedelta(days=1), **world)
    assert [r.company.symbol for r in warm.records] == ["AAPL"]
    # DN (unknown: read, due) and AAPL first; then one known-due year (the held-record total reaches 3)
    assert world["revenue"].calls == ["DN", "AAPL", "DA"]
    assert warm.rejections == {"money_map_next_year_due": 2}


@pytest.mark.asyncio
async def test_known_due_years_are_retried_least_recently_checked_first(monkeypatch):
    """A symbol at the back is still re-read when reads are left — the one checked longest ago first, so every
    waiting symbol gets its turn (a new 10-K is found whichever it is)."""
    fmp, world = _money_pool(monkeypatch, ("DA", "DB", "DC"))
    await run_mm(fmp, run_date=_DUE_DAY, **world)                        # learns all three, checked that day
    monkeypatch.setattr(A, "MONEY_MAP_MAX_LOOKUPS", 1)
    for days, expected in ((1, "DA"), (2, "DB"), (3, "DC"), (4, "DA")):
        world["revenue"].calls.clear()
        got = await run_mm(fmp, run_date=_DUE_DAY + timedelta(days=days), **world)
        assert world["revenue"].calls == [expected], days
        assert got.rejections == {"money_map_next_year_due": 1}


@pytest.mark.asyncio
async def test_a_newly_filed_year_is_read_whatever_the_memo_knows(monkeypatch):
    """The memo knows a YEAR, never the symbol's answer: once the breakdown serves the next year, the normal gates
    run and the map is kept."""
    fmp, world = _money_pool(monkeypatch, ("AAPL",))
    first = await run_mm(fmp, run_date=_DUE_DAY, **world)
    assert first.rejections == {"money_map_next_year_due": 1}
    _fy2026(world, fmp, "AAPL")                                           # its fiscal 2026 10-K lands
    second = await run_mm(fmp, run_date=_DUE_DAY + timedelta(days=1), **world)
    assert [(r.company.symbol, r.fiscal_year) for r in second.records] == [("AAPL", "2026")]


@pytest.mark.asyncio
async def test_a_stale_year_the_memo_knows_is_still_a_content_refusal(monkeypatch):
    """The memo applies the statement read's own age rule: past `MONEY_MAP_STALE_DAYS` it is `money_map_stale`
    (reported as it always was), still without the fan-out. Since B3PD-1 (2026-10-20) a memo answer — this one
    included — never counts toward the content cap: it costs one breakdown read."""
    fmp, world = _money_pool(monkeypatch, ("AAPL",))
    await run_mm(fmp, run_date=_DUE_DAY, **world)
    before = _fan_out_calls(fmp, world)
    got = await run_mm(fmp, run_date=date(2027, 3, 30), **world)          # 549 days after Sep 27, 2025
    assert got.rejections == {"money_map_stale": 1} and _fan_out_calls(fmp, world) == before


@pytest.mark.parametrize("junk", ["junk", ("2025",), ("2025", "2025-09-27", date(2026, 10, 15)),
                                  (2025, date(2025, 9, 27), date(2026, 10, 15)), None])
@pytest.mark.asyncio
async def test_a_malformed_memo_entry_is_ignored(monkeypatch, junk):
    """Fail open to the normal read: a malformed entry can only cost a read, never refuse or reorder a map."""
    fmp, world = _money_pool(monkeypatch, ("AAPL",))
    _fy2026(world, fmp, "AAPL")
    A._memo_put(("money_map_fy_end", "AAPL"), junk, 3600.0)
    got = await run_mm(fmp, run_date=_DUE_DAY, **world)
    assert [r.company.symbol for r in got.records] == ["AAPL"]
    assert A._money_map_known("AAPL") == ("2026", date(2026, 9, 26), _DUE_DAY)   # rewritten by the read


def test_the_due_order_puts_every_known_due_year_after_both_groups():
    """Never posted, then posted for an earlier year — and AFTER both groups every symbol KNOWN to be on a due year
    (the re-review of 2026-10-19, MM-4-R: a due year is refused whatever its group, so it never outranks a candidate
    that can publish), least recently checked first, then never posted before posted, then the pool order. An
    unknown or fresh symbol keeps the pool order inside its group."""
    run = _DUE_DAY
    A._memo_put(("money_map_fy_end", "B"), ("2025", date(2025, 9, 27), run), 3600.0)        # due
    A._memo_put(("money_map_fy_end", "D"), ("2025", date(2025, 9, 27), run), 3600.0)        # due (posted)
    A._memo_put(("money_map_fy_end", "C"), ("2026", date(2026, 9, 26), run), 3600.0)        # fresh
    A._memo_put(("money_map_fy_end", "F"), ("2025", date(2025, 9, 27), run - timedelta(days=3)), 3600.0)  # older
    # G: posted for fiscal 2026, and the memo knows it is still on fiscal 2026 — `already_posted` after its read
    A._memo_put(("money_map_fy_end", "G"), ("2026", date(2026, 9, 26), run - timedelta(days=1)), 3600.0)
    posted = {"D": {"2024"}, "E": {"2025"}, "F": {"2024"}, "G": {"2025", "2026"}}
    order = A._money_map_order(["A", "B", "C", "D", "E", "F", "G"], posted, run)
    assert order == ["A", "C", "E", "F", "G", "B", "D"]


@pytest.mark.asyncio
async def test_a_posted_symbols_fresh_year_is_reached_before_fourteen_known_due_years(monkeypatch):
    """The re-review of 2026-10-19 (MM-4-R): fourteen never-posted December year-ends the memo knows to be due, and
    AAPL — posted for fiscal 2025 — whose breakdown now serves fiscal 2026. Before, the never-posted group came
    first whole: twelve breakdown reads (`MONEY_MAP_MAX_LOOKUPS`) went to due years that cannot publish, and the
    run ended `money_map_none_qualified`. AAPL is read first now. Mutation-checked by hand (2026-10-19): with the
    known-due years back inside their group, AAPL was never read (red)."""
    due = tuple(f"D{c}" for c in "ABCDEFGHJKLMNP")                         # fourteen
    fmp, world = _money_pool(monkeypatch, due + ("AAPL",))
    _fy2026(world, fmp, "AAPL")
    for sym in due:
        A._memo_put(("money_map_fy_end", sym), ("2025", date(2025, 9, 27), _DUE_DAY), 3600.0)
    got = await run_mm(fmp, run_date=_DUE_DAY + timedelta(days=1), exclude={"news:money_map:AAPL:2025"}, **world)
    assert [(r.company.symbol, r.fiscal_year) for r in got.records] == [("AAPL", "2026")]
    assert world["revenue"].calls[0] == "AAPL"


# ── B3PD-1 (2026-10-20): a refusal is remembered, so refused seeds never wall the pool off ─────────────────────
#
# Nothing remembered a content refusal: a never-posted seed refused for its current year kept the head of the pool,
# every call spent its content cap on the same refused symbols, and once the publishable seeds ahead of them were
# posted the series never yielded again — the members behind them, and every posted company's NEW fiscal year,
# were never read (the in-memory walk of the review: 68 posts and no new-year re-post over 2027 at seven days a
# week, against 236 and 60 without the refusals). Each read that cannot publish is remembered now
# (`_money_map_remember_refusal`); such a symbol is tried after every candidate that can publish, least recently
# checked first, and a re-read of the same year is answered before the fan-out without counting toward the cap.

_DEC_FY2025, _DEC_FY2026 = ("2025", "2025-12-31"), ("2026", "2026-12-31")


def _dated_pool(monkeypatch, seed, *, refused=(), degraded=(), filed=None):
    """``seed`` as the Money Map pool, every symbol on a December fiscal year. ``refused`` symbols report a gross
    segment stack (`money_map_inconsistent`, a content refusal) whatever their year, ``degraded`` ones a degraded
    breakdown (an upstream gap); ``filed`` maps a symbol to the run day its fiscal 2026 10-K is served (fiscal 2025
    before it). Returns (fmp, world, set_day): ``set_day(day)`` points every fake at what FMP serves that day."""
    monkeypatch.setattr(R, "MONEY_MAP_SEED", tuple(seed))
    world = dict(revenue=FakeService({}), profit=FakeService({}), facts=facts_fn(), sb=FakeSB({"trending_themes": []}))
    fmp = FakeFMP(profiles={s: prof(s, f"{s.title()} Corporation", exchange="NASDAQ") for s in seed}, income={})

    def set_day(day: date) -> None:
        for s in seed:
            fy, end = _DEC_FY2026 if (filed or {}).get(s) is not None and day >= filed[s] else _DEC_FY2025
            extra: Dict[str, Any] = {"intersegment_eliminations": 10e9} if s in refused else {}
            if s in degraded:
                extra["degraded"] = ["segmentation_unavailable"]
            world["revenue"].by_symbol[s] = breakdown(symbol=s, fiscal_year=fy, **extra)
            world["profit"].by_symbol[s] = profit(point={"period": fy})
            fmp.income[s] = [{**INCOME_AAPL[0], "fiscalYear": fy, "date": end}]

    return fmp, world, set_day


async def _mm_days(fmp, world, set_day, days, posted, *, limit=1):
    """Run the walk on each day of ``days`` like the daily build does — one record a day posted to the ledger
    (``posted``, the ledger keys) — and return each day's (records' symbols with their years, rejections, reads)."""
    out = []
    for day in days:
        set_day(day)
        world["revenue"].calls.clear()
        got = await run_mm(fmp, run_date=day, exclude=set(posted), limit=limit, **world)
        for rec in got.records[:1]:
            posted.add(R.ledger_key(rec))
        out.append(([(r.company.symbol, r.fiscal_year) for r in got.records], dict(got.rejections),
                    list(world["revenue"].calls)))
    return out


@pytest.mark.asyncio
async def test_ten_refused_seeds_never_wall_off_the_pool_across_a_year_end(monkeypatch):
    """The review's walk, day by day: ten never-posted seeds refused for their year at the head of the pool, two
    publishable seeds behind them, and a third first read in the Dec 30 – Feb 16 due window. Each refused seed is
    read ONCE and then waits behind every candidate that can publish, so the publishable seeds are reached on the
    next days; through the due window everything is a date fact; and once the fiscal 2026 10-Ks land the walk
    reaches both the never-posted symbol it read while due (QQ) and the posted company's NEW year (PA). Mutation-
    checked (2026-10-20): with `_money_map_remember_refusal` a no-op the same eight refused seeds end every day and
    nothing is ever posted."""
    refused = tuple(f"R{c}" for c in "ABCDEFGHJK")                             # ten
    fmp, world, set_day = _dated_pool(monkeypatch, refused + ("PA", "PB", "QQ"), refused=refused,
                                      filed={"PA": date(2027, 2, 16), "QQ": date(2027, 2, 16)})
    posted: set = set()
    nov2, nov3, nov4, jan5, feb17, feb18 = await _mm_days(fmp, world, set_day, [
        date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2027, 1, 5), date(2027, 2, 17),
        date(2027, 2, 18)], posted)
    # day 1: the first eight refused seeds use up the content cap — the one empty day
    assert nov2 == ([], {"money_map_inconsistent": 8}, list(refused[:8]))
    # day 2: the two refused seeds never read come first, then PA; the eight remembered ones wait at the back
    assert nov3 == ([("PA", "2025")], {"money_map_inconsistent": 2}, ["RJ", "RK", "PA"])
    assert nov4 == ([("PB", "2025")], {}, ["PB"])
    # the due window (FY2025 ended Dec 31, 2025 is due from Dec 30, 2026): QQ, never read, first — then the waiting
    # symbols least recently checked first (the year-end memo answers them: no fan-out); twelve reads, no record
    assert jan5[0] == [] and jan5[1] == {"money_map_next_year_due": 11, "already_posted": 1}
    assert jan5[2] == ["QQ", *refused, "PA"]
    # the 10-Ks land: QQ — never posted, read while due — is reached and kept on fiscal 2026 …
    assert feb17[0] == [("QQ", "2026")] and feb17[2][-1] == "QQ"
    # … and the next day the posted company's NEW fiscal year, read before every symbol checked since
    assert feb18[0] == [("PA", "2026")] and feb18[2] == ["PA"]
    assert posted == {"news:money_map:PA:2025", "news:money_map:PB:2025", "news:money_map:QQ:2026",
                      "news:money_map:PA:2026"}


@pytest.mark.asyncio
async def test_remembered_refusals_are_answered_before_the_fan_out_and_never_use_up_the_content_cap(monkeypatch,
                                                                                                    caplog):
    """Nine symbols whose fiscal 2025 the memo refused yesterday, ahead of AAPL — posted for fiscal 2025, its fiscal
    2026 10-K now served — whose last check is today: each refused year is answered from the memo (one breakdown
    read, no statement / profit-power / facts / profile read) and never counts toward the content cap, so AAPL is
    read and kept. Mutation-checked (2026-10-20): counting the memo's answers as content refusals stops the walk at
    eight and AAPL is never read."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    held = tuple(f"G{c}" for c in "ABCDEFGHJ")                                  # nine
    filed = date(2027, 2, 16)                                                   # every fiscal 2026 10-K
    fmp, world, set_day = _dated_pool(monkeypatch, held + ("AAPL",), refused=held,
                                      filed={s: filed for s in held + ("AAPL",)})
    day = date(2027, 3, 3)
    set_day(day)
    for sym in held:
        A._memo_put(("money_map_refused", sym), ("2026", "money_map_inconsistent", day - timedelta(days=1),
                                                 day - timedelta(days=1)), 3600.0)
    A._memo_put(("money_map_fy_end", "AAPL"), ("2025", date(2025, 12, 31), day), 3600.0)
    before = _fan_out_calls(fmp, world)
    got = await run_mm(fmp, run_date=day, exclude={"news:money_map:AAPL:2025"}, **world)
    assert [(r.company.symbol, r.fiscal_year) for r in got.records] == [("AAPL", "2026")]
    assert got.rejections == {"money_map_inconsistent": 9}
    assert world["revenue"].calls == [*held, "AAPL"]
    # the only fan-out is AAPL's: one statement, one profit power, one facts and one profile read
    after = _fan_out_calls(fmp, world)
    assert tuple(a - b for a, b in zip(after, before)) == (1, 1, 1, 1)
    assert ("GA is still on FY2026, refused money_map_inconsistent when read in full on 2027-03-02 (1 days before "
            "the run)") in caplog.text
    # each answer refreshed the check (the order rotates) and kept the day of the full read
    assert A._money_map_refused("GA") == ("2026", "money_map_inconsistent", day, day - timedelta(days=1))
    assert A._money_map_refused("AAPL") is None                    # a record drops whatever was remembered


@pytest.mark.parametrize("age, read_in_full", [
    (A.MONEY_MAP_REFUSAL_RECHECK_DAYS - 1, False), (A.MONEY_MAP_REFUSAL_RECHECK_DAYS, True), (-1, True)],
    ids=["inside_the_window", "at_the_window", "a_later_read_than_the_run"])
@pytest.mark.asyncio
async def test_a_remembered_refusal_is_read_in_full_again_after_the_recheck_window(monkeypatch, age, read_in_full):
    """A refused year is answered from the memo for `MONEY_MAP_REFUSAL_RECHECK_DAYS` after its last FULL read; then
    it is read in full again (a feed correction, a listing change) — and a read dated after the run (another
    process's clock) is never trusted."""
    fmp, world, set_day = _dated_pool(monkeypatch, ("GA",), refused=("GA",))
    day = date(2026, 11, 3)
    set_day(day)
    read_on = day - timedelta(days=age)
    A._memo_put(("money_map_refused", "GA"), ("2025", "money_map_inconsistent", read_on, read_on), 3600.0)
    before = _fan_out_calls(fmp, world)
    got = await run_mm(fmp, run_date=day, **world)
    assert got.rejections == {"money_map_inconsistent": 1}
    assert (_fan_out_calls(fmp, world) != before) is read_in_full
    assert A._money_map_refused("GA")[2:] == ((day, day) if read_in_full else (day, read_on))


@pytest.mark.asyncio
async def test_a_new_fiscal_year_lifts_a_remembered_refusal(monkeypatch):
    """The memo remembers a YEAR's refusal: once the breakdown serves the next year it is read in full, and kept."""
    fmp, world, set_day = _dated_pool(monkeypatch, ("GA",), filed={"GA": date(2027, 2, 16)})
    day = date(2027, 2, 17)
    set_day(day)
    A._memo_put(("money_map_refused", "GA"), ("2025", "money_map_mostly_other", day, day), 3600.0)
    got = await run_mm(fmp, run_date=day, **world)
    assert [(r.company.symbol, r.fiscal_year) for r in got.records] == [("GA", "2026")]


@pytest.mark.asyncio
async def test_upstream_gaps_wait_at_the_back_but_are_always_read_in_full(monkeypatch):
    """Three seeds whose breakdown is degraded used up the three attempts every day, ahead of AAPL. A gap is
    remembered for the ORDER only: the next day AAPL is read first; and a gap is never answered from the memo —
    each re-read is a full read, so a breakdown that comes back is kept."""
    gaps = ("DA", "DB", "DC")
    degraded = set(gaps)                                    # read by `set_day` each day
    fmp, world, set_day = _dated_pool(monkeypatch, gaps + ("AAPL",), degraded=degraded)
    posted: set = set()
    day1, day2 = await _mm_days(fmp, world, set_day, [date(2026, 11, 2), date(2026, 11, 3)], posted)
    assert day1 == ([], {"money_map_degraded": 3}, list(gaps))
    assert day2[0] == [("AAPL", "2025")] and day2[2][0] == "AAPL"
    assert A._money_map_refused("DA")[:2] == ("2025", "money_map_degraded")
    # DA's breakdown is whole again: the next day it is read in full in its turn (never answered from the memo)
    # and kept
    degraded.discard("DA")
    (day3,) = await _mm_days(fmp, world, set_day, [date(2026, 11, 4)], posted)
    assert day3[0] == [("DA", "2025")] and day3[2][0] == "DA"
    assert A._money_map_refused("DA") is None


@pytest.mark.asyncio
async def test_already_posted_reads_refresh_the_rotation(monkeypatch):
    """Thirteen symbols posted for fiscal 2025, all last checked on the same day, and only the LAST one's fiscal
    2026 10-K served: twelve reads (`MONEY_MAP_MAX_LOOKUPS`) a day. Each `already_posted` read refreshes its check
    (B3PD-1), so the next day starts with the one not yet read and keeps it. Mutation-checked (2026-10-20): without
    the refresh the same twelve are read every day and the new year is never reached."""
    syms = tuple(f"S{c}" for c in "ABCDEFGHJKLMN")                              # thirteen
    fmp, world, set_day = _dated_pool(monkeypatch, syms, filed={"SN": date(2027, 2, 16)})
    old = date(2027, 2, 1)
    for sym in syms:
        A._memo_put(("money_map_fy_end", sym), ("2025", date(2025, 12, 31), old), 3600.0)
    posted = {f"news:money_map:{s}:2025" for s in syms}
    day1, day2 = await _mm_days(fmp, world, set_day, [date(2027, 3, 2), date(2027, 3, 3)], posted)
    assert A.MONEY_MAP_MAX_LOOKUPS == 12
    assert day1 == ([], {"already_posted": 12}, list(syms[:12]))
    assert day2[0] == [("SN", "2026")] and day2[2] == ["SN"]


def test_the_order_holds_back_a_remembered_refusal_only_while_it_is_the_latest_word():
    """A refusal remembered no earlier than the year-end entry holds a symbol back (least recently checked first,
    beside the due and posted years); one older than the year-end entry — a newer year read since — does not; a
    malformed entry is ignored (fail open: it can only cost a read)."""
    run = date(2026, 11, 3)
    A._memo_put(("money_map_refused", "B"), ("2025", "money_map_mostly_other", run, run), 3600.0)
    A._memo_put(("money_map_refused", "C"), ("2025", "money_map_degraded", run - timedelta(days=2),
                                             run - timedelta(days=2)), 3600.0)
    # D: refused fiscal 2025 on Oct 1, its fiscal 2026 read (fresh) since — the older refusal no longer holds it
    A._memo_put(("money_map_refused", "D"), ("2025", "money_map_mostly_other", date(2026, 10, 1),
                                             date(2026, 10, 1)), 3600.0)
    A._memo_put(("money_map_fy_end", "D"), ("2026", date(2026, 9, 26), run - timedelta(days=1)), 3600.0)
    A._memo_put(("money_map_refused", "E"), ("2025", 7, run, run), 3600.0)                  # malformed
    assert A._money_map_refused("E") is None
    order = A._money_map_order(["A", "B", "C", "D", "E", "F"], {"F": {"2024"}}, run)
    assert order == ["A", "D", "E", "F", "C", "B"]


# ── RR2-MM-TEMPLATE-WALL (the round-2 review of 2026-10-20): a record the TEMPLATE refuses never walls the pool ────
#
# B3PD-1 remembered only the read's own refusals. A record the template then refused — a loss larger than revenue
# (`implausible_figures`), a segment named like a web address (`slot_rejected`) — was rebuilt and offered first every
# day: each counted as one of the walk's three attempts and cleared the symbol's remembered refusal, so three
# never-posted ones at the head of the pool ended the series every day (the review's in-memory walk: ten days, the
# same three refused, nothing posted — and no posted company's new fiscal year ever reached). A loss larger than
# revenue is refused at the read now (`money_map_loss_above_revenue`, remembered like any content refusal), and every
# other template refusal is reported back by the script flow (`A.remember_template_refusal` → `template_refused`).

_LOSS_SOURCES = [src("Vaccines", 1.5e9), src("Collaborations", 0.5e9)]


@pytest.mark.parametrize("net, gross, operating, kept_operating, refused", [
    (-1.2, 0.4, -0.9, -0.9, True),      # the net loss alone is larger than revenue (Moderna's fiscal 2024 shape)
    (-0.8, 0.3, -1.2, -1.2, True),      # the operating loss alone (a gain below the operating line)
    (-0.9, -1.2, -1.0, None, True),     # the gross loss alone (the operating line, above the gross loss, is omitted)
    (-1.0, 0.3, -1.0, -1.0, False),     # losses exactly as large as revenue: the template draws them
    (-0.5, 0.4, -0.6, -0.6, False),     # an ordinary loss year
], ids=["net", "operating", "gross", "equal_to_revenue", "ordinary_loss"])
@pytest.mark.asyncio
async def test_a_loss_larger_than_revenue_is_refused_at_the_read_exactly_as_the_template_refuses_it(
        monkeypatch, net, gross, operating, kept_operating, refused):
    """The read refuses exactly what the template's `implausible_figures` gate refuses (|net|, |gross| or |operating|
    above revenue — the record it would build is refused by `NT.compose`), here where the walk remembers it (B3PD-1);
    the rest it builds, and they compose. Mutation-checked (2026-10-20): each of the gate's three conditions removed
    alone turns its own row red."""
    rev = 2.0e9
    fmp, world = _fy2025_world(monkeypatch, "LA", "Lumen Biologics Inc.", _LOSS_SOURCES, revenue=rev, net=net * rev,
                               gross=gross * rev, operating=operating * rev)
    got = await run_mm(fmp, **world)
    company = R.company_from_profile(prof("LA", "Lumen Biologics Inc.", exchange="NASDAQ"), "LA", purpose="listing")
    would_be = R.MoneyMap(series="money_map", company=company, fiscal_year="2025", period_end=date(2025, 12, 31),
                          segments=tuple(R.Segment(s.name, s.value) for s in _LOSS_SOURCES), other_usd=None,
                          eliminations_usd=None, revenue_usd=rev, gross_profit_usd=gross * rev,
                          operating_profit_usd=None if kept_operating is None else kept_operating * rev,
                          net_income_usd=net * rev)
    if refused:
        assert got.records == () and got.rejections == {"money_map_loss_above_revenue": 1}
        assert A._money_map_refused("LA")[:2] == ("2025", "money_map_loss_above_revenue")     # remembered (B3PD-1)
        with pytest.raises(NT.NewsTemplateRefused) as err:
            NT.compose(would_be, run_date=MM_RUN, store_state="live", allow_x_url=False)
        assert err.value.code == "implausible_figures"
    else:
        (rec,) = got.records
        assert rec == would_be and A._money_map_refused("LA") is None
        NT.compose(rec, run_date=MM_RUN, store_state="live", allow_x_url=False)                # composes


@pytest.mark.asyncio
async def test_the_reads_figure_gate_and_the_templates_agree_on_every_profit_and_loss_shape(monkeypatch):
    """Both directions, over a grid of net / gross / operating shapes (multiples of revenue; None = no statement
    line): every record the read builds composes (no `implausible_figures` left for the template — the wall's root),
    and every `money_map_loss_above_revenue` refusal is a record the template would refuse (never an over-block). The
    record the read would build keeps the gross line it was given and drops an operating line above the gross one
    (the read's own omission rule). Mutation-checked (2026-10-20): any one of the gate's conditions removed, or its
    net or operating ``<`` read as ``<=`` (an over-block at a loss exactly as large as revenue), turns it red."""
    rev = 2.0e9
    shapes = 0
    for net in (-1.5, -1.0, -0.6, 0.2):
        for gross in (None, -1.3, -0.4, 0.5):
            for operating in (None, -1.4, -1.0, -0.3, 0.1):
                A.clear_memo()
                fmp, world = _fy2025_world(monkeypatch, "LA", "Lumen Biologics Inc.", _LOSS_SOURCES, revenue=rev,
                                           net=net * rev, gross=None if gross is None else gross * rev,
                                           operating=None if operating is None else operating * rev)
                got = await run_mm(fmp, **world)
                shape = (net, gross, operating)
                if got.records:
                    (rec,) = got.records
                    NT.compose(rec, run_date=MM_RUN, store_state="live", allow_x_url=False)
                    shapes += 1
                    continue
                assert got.rejections == {"money_map_loss_above_revenue": 1}, (shape, got.rejections)
                kept_op = None if operating is None or (gross is not None and operating > gross) else operating
                would_be = R.MoneyMap(
                    series="money_map", company=R.CompanyRef(symbol="LA", name="Lumen Biologics"), fiscal_year="2025",
                    period_end=date(2025, 12, 31), segments=tuple(R.Segment(s.name, s.value) for s in _LOSS_SOURCES),
                    other_usd=None, eliminations_usd=None, revenue_usd=rev,
                    gross_profit_usd=None if gross is None else gross * rev,
                    operating_profit_usd=None if kept_op is None else kept_op * rev, net_income_usd=net * rev)
                with pytest.raises(NT.NewsTemplateRefused) as err:
                    NT.compose(would_be, run_date=MM_RUN, store_state="live", allow_x_url=False)
                assert err.value.code == "implausible_figures", shape
                shapes += 1
    assert shapes == 80


#: Moderna's fiscal 2024 shape: revenue $3.236B, gross profit $1.77B, an operating loss of $3.94B and a net loss of
#: $3.561B — a net and an operating loss larger than revenue.
_MRNA_SHAPE = dict(revenue=3.236e9, gross=1.77e9, operating=-3.94e9, net=-3.561e9)
_WALL_FILED = date(2027, 2, 16)          # every fiscal 2026 10-K is served from this day (fiscal 2025 before it)


def _template_wall_pool(monkeypatch, walled, publishable, posted, *, segment=None):
    """The pool ``walled`` + ``publishable`` + ``posted``, every symbol on a December fiscal year. ``walled`` symbols
    (never posted) build a record the TEMPLATE refuses: by default Moderna's shape (`_MRNA_SHAPE`, a loss larger than
    revenue); with ``segment``, an ordinary year whose largest segment is named ``segment`` (a template-only refusal
    the read cannot see). ``posted`` symbols are the ones the caller's ledger holds for fiscal 2025. Returns (fmp,
    world, set_day): ``set_day(day)`` points every fake at what FMP serves that day."""
    seed = tuple(walled) + tuple(publishable) + tuple(posted)
    monkeypatch.setattr(R, "MONEY_MAP_SEED", seed)
    world = dict(revenue=FakeService({}), profit=FakeService({}), facts=facts_fn(), sb=FakeSB({"trending_themes": []}))
    fmp = FakeFMP(profiles={s: prof(s, f"{s.title()} Corporation", exchange="NASDAQ") for s in seed}, income={})
    m = _MRNA_SHAPE

    def set_day(day: date) -> None:
        fy, end = ("2026", "2026-12-31") if day >= _WALL_FILED else ("2025", "2025-12-31")
        for s in seed:
            if s in walled and segment is None:
                world["revenue"].by_symbol[s] = breakdown(
                    symbol=s, fiscal_year=fy, reported_revenue=m["revenue"], net_income=m["net"],
                    cost_of_sales=m["revenue"] - m["gross"],
                    revenue_sources=[src("Vaccines", 3.0e9), src("Collaborations", 0.236e9)])
                world["profit"].by_symbol[s] = profit(point={
                    "period": fy, "net_margin": 100.0 * m["net"] / m["revenue"],
                    "gross_margin": 100.0 * m["gross"] / m["revenue"],
                    "operating_margin": 100.0 * m["operating"] / m["revenue"]})
                fmp.income[s] = [{"fiscalYear": fy, "date": end, "reportedCurrency": "USD", "revenue": m["revenue"],
                                  "grossProfit": m["gross"], "operatingIncome": m["operating"], "netIncome": m["net"]}]
                continue
            extra: Dict[str, Any] = {}
            if s in walled:
                extra["revenue_sources"] = [src(segment, 200e9), src("Services", 100e9), src("Mac", 30e9),
                                            src("Other", 20e9)]
            world["revenue"].by_symbol[s] = breakdown(symbol=s, fiscal_year=fy, **extra)
            world["profit"].by_symbol[s] = profit(point={"period": fy})
            fmp.income[s] = [{**INCOME_AAPL[0], "fiscalYear": fy, "date": end}]

    return fmp, world, set_day


async def _template_days(fmp, world, set_day, days, posted, *, report):
    """Each day as the script flow builds it (`_build_template_day`): the walk (`MAX_CANDIDATES_PER_SERIES` records),
    then the first record the TEMPLATE composes is posted (its ledger key added to ``posted``); with ``report`` each
    record the template refused is reported back (`A.remember_template_refusal`), as `_report_template_refusal` does.
    Returns each day's (records' symbols with their years, the template's refusals, the posted key or None, the
    breakdown reads, the profit-power reads (the fan-out), the rejections)."""
    from app.services.marketing import script_service as ss

    out = []
    for day in days:
        set_day(day)
        world["revenue"].calls.clear()
        world["profit"].calls.clear()
        got = await run_mm(fmp, run_date=day, exclude=set(posted), limit=ss.MAX_CANDIDATES_PER_SERIES, **world)
        chosen, refusals = None, []
        for rec in got.records:
            try:
                NT.compose(rec, run_date=day, store_state="live", allow_x_url=False)
            except NT.NewsTemplateRefused as e:
                refusals.append((rec.company.symbol, e.code))
                if report:
                    assert A.remember_template_refusal(rec, (e.code,), run_date=day) is True
                continue
            chosen = R.ledger_key(rec)
            break
        if chosen is not None:
            posted.add(chosen)
        out.append(([(r.company.symbol, r.fiscal_year) for r in got.records], refusals, chosen,
                    list(world["revenue"].calls), list(world["profit"].calls), dict(got.rejections)))
    return out


@pytest.mark.asyncio
async def test_three_loss_makers_at_the_head_of_the_pool_no_longer_wall_off_money_map(monkeypatch):
    """The review's walk: three never-posted companies losing more than their revenue head the pool, two publishable
    ones and one posted for fiscal 2025 (its fiscal 2026 10-K now served) behind them. NOTHING is reported back here:
    the read's own refusal must hold them. Each is refused at its first read (a content refusal, so the walk goes on
    and posts the next one the same day), then waits behind every candidate that can publish and is answered from
    the memo; the publishable ones and the posted company's NEW year post on the next days. Mutation-checked
    (2026-10-20): without the read's gate the three are rebuilt every day, the template refuses all three and
    nothing is ever posted."""
    walled = ("LA", "LB", "LC")
    fmp, world, set_day = _template_wall_pool(monkeypatch, walled, ("PA", "PB"), ("QQ",))
    posted = {"news:money_map:QQ:2025"}
    days = [date(2027, 3, 1) + timedelta(days=k) for k in range(4)]
    d1, d2, d3, d4 = await _template_days(fmp, world, set_day, days, posted, report=False)
    # day 1: each loss-maker refused at the read, and the next candidate is posted the same day
    assert d1[3] == ["LA", "LB", "LC", "PA"] and d1[5] == {"money_map_loss_above_revenue": 3}
    assert (d1[0], d1[1], d1[2]) == ([("PA", "2026")], [], "news:money_map:PA:2026")
    # then the loss-makers wait at the back, answered from the memo with no fan-out
    assert d2[3] == ["PB", "QQ", "LA"] and d2[4] == ["PB", "QQ"] and d2[2] == "news:money_map:PB:2026"
    assert d3[3] == ["QQ", "LB", "LC"] and d3[4] == ["QQ"] and d3[2] == "news:money_map:QQ:2026"
    assert d4[2] is None and not set(d4[4]) & set(walled)        # nothing left to post; no loss-maker re-read
    assert posted == {"news:money_map:QQ:2025", "news:money_map:PA:2026", "news:money_map:PB:2026",
                      "news:money_map:QQ:2026"}


@pytest.mark.asyncio
async def test_a_template_refusal_reported_back_never_walls_off_money_map(monkeypatch):
    """A refusal only the TEMPLATE can make — three never-posted companies whose largest segment is named like a web
    address ("Booking.com": a bare link, `slot_rejected`) — reported back as the script flow does: the first day is
    lost (all three records refused, the day falls to the lesson); from the next day they wait behind every candidate
    that can publish, a re-read of their year is answered from the memo (`template_refused`, no fan-out), and the
    publishable companies and the posted company's NEW year are reached. Mutation-checked (2026-10-20): with
    `remember_template_refusal` a no-op the same three records are offered first every day and nothing is posted."""
    walled = ("WA", "WB", "WC")
    fmp, world, set_day = _template_wall_pool(monkeypatch, walled, ("PA", "PB"), ("QQ",), segment="Booking.com")
    posted = {"news:money_map:QQ:2025"}
    days = [date(2027, 3, 1) + timedelta(days=k) for k in range(4)]
    (d1,) = await _template_days(fmp, world, set_day, days[:1], posted, report=True)
    assert d1[0] == [("WA", "2026"), ("WB", "2026"), ("WC", "2026")] and d1[2] is None
    assert d1[1] == [("WA", "slot_rejected"), ("WB", "slot_rejected"), ("WC", "slot_rejected")]
    assert A._money_map_refused("WA") == ("2026", "template_refused", days[0], days[0])
    d2, d3, d4 = await _template_days(fmp, world, set_day, days[1:], posted, report=True)
    assert d2[3] == ["PA", "PB", "QQ"] and d2[2] == "news:money_map:PA:2026"
    assert d3[3] == ["PB", "QQ", "WA"] and d3[4] == ["PB", "QQ"] and d3[5] == {"template_refused": 1}
    assert d3[2] == "news:money_map:PB:2026"
    assert d4[3] == ["QQ", "WB", "WC"] and d4[2] == "news:money_map:QQ:2026"
    # each memo answer refreshed the check (the rotation) and kept the day of the full read
    assert A._money_map_refused("WB") == ("2026", "template_refused", days[3], days[0])
    assert posted == {"news:money_map:QQ:2025", "news:money_map:PA:2026", "news:money_map:PB:2026",
                      "news:money_map:QQ:2026"}


@pytest.mark.asyncio
async def test_a_template_refused_year_is_read_in_full_again_after_the_recheck_window(monkeypatch):
    """Like any remembered refusal, `template_refused` answers a re-read of the SAME year only for
    `MONEY_MAP_REFUSAL_RECHECK_DAYS` after its full read; then the year is read in full, built, and offered again (a
    corrected feed composes) — the record clears the memo."""
    fmp, world, set_day = _template_wall_pool(monkeypatch, ("WA",), (), (), segment="Booking.com")
    first = date(2026, 11, 2)
    set_day(first)
    (rec,) = (await run_mm(fmp, run_date=first, **world)).records
    assert A.remember_template_refusal(rec, ("slot_rejected",), run_date=first) is True
    inside = first + timedelta(days=A.MONEY_MAP_REFUSAL_RECHECK_DAYS - 1)
    got = await run_mm(fmp, run_date=inside, **world)
    assert got.records == () and got.rejections == {"template_refused": 1}
    world["revenue"].by_symbol["WA"] = breakdown(symbol="WA", fiscal_year="2025")     # the feed renames the segment
    after = first + timedelta(days=A.MONEY_MAP_REFUSAL_RECHECK_DAYS)
    (again,) = (await run_mm(fmp, run_date=after, **world)).records
    NT.compose(again, run_date=after, store_state="live", allow_x_url=False)
    assert A._money_map_refused("WA") is None


def test_remember_template_refusal_writes_a_money_map_years_refusal_and_nothing_else():
    """The hook the script flow calls (`MarketingScriptService._report_template_refusal`): a `MoneyMap` record's
    refusal is remembered for its symbol and fiscal year as `template_refused` — a known rejection reason the memo
    answers (never one of `_MONEY_MAP_NOT_REMEMBERED`) — read on the run day (a datetime is its day; a bare string
    is one code); any other record, or a run date that is not a date, writes nothing."""
    day = date(2026, 11, 19)
    rec = R.MoneyMap(series="money_map", company=R.CompanyRef(symbol="COST", name="Costco"), fiscal_year="2025",
                     period_end=date(2025, 8, 31), segments=(R.Segment("United States", 200e9),
                                                             R.Segment("Canada", 38.5e9)),
                     other_usd=None, eliminations_usd=None, revenue_usd=238.5e9, gross_profit_usd=None,
                     operating_profit_usd=None, net_income_usd=8.1e9)
    assert A.remember_template_refusal(rec, ["slot_rejected", "stale_source"], run_date=day) is True
    assert A._money_map_refused("COST") == ("2025", "template_refused", day, day)
    assert A.remember_template_refusal(rec, "implausible_figures", run_date=datetime(2026, 11, 20, 16, 5)) is True
    assert A._money_map_refused("COST") == ("2025", "template_refused", date(2026, 11, 20), date(2026, 11, 20))
    A.clear_memo()
    look_alike = SimpleNamespace(series="money_map", company=SimpleNamespace(symbol="COST"), fiscal_year="2025")
    assert A.remember_template_refusal(look_alike, ["slot_rejected"], run_date=day) is False
    assert A.remember_template_refusal(None, ["slot_rejected"], run_date=day) is False
    assert A.remember_template_refusal(rec, ["slot_rejected"], run_date="2026-11-19") is False
    assert A._MEMO == {}
    assert "template_refused" in R.REJECTION_REASONS and "template_refused" not in A._MONEY_MAP_NOT_REMEMBERED
    assert "money_map_loss_above_revenue" in R.REJECTION_REASONS
    assert "money_map_loss_above_revenue" not in A._MONEY_MAP_NOT_REMEMBERED | A._MONEY_MAP_UPSTREAM_GAPS


#: 2026-10-09 live preview; here it carries the short name Amazon itself uses, "AWS" — the code shape the fold
#: removes, which `R.SEGMENT_ACRONYMS_OK` must keep wherever a feed writes it so.
_AMZN_FY2025 = [src("Online Stores", 270e9), src("Third-Party Seller Services", 170e9), src("AWS", 128e9),
                src("Advertising Services", 69e9), src("Subscription Services", 49e9), src("Physical Stores", 22e9),
                src("Other", 9e9)]


def _fy2025_world(monkeypatch, symbol, name, sources, *, revenue, net, gross=None, operating=None,
                  date_="2025-12-31"):
    """One seed on a December fiscal 2025 (fresh on `MM_RUN`, 246 days on), its statement row carrying
    ``gross`` / ``operating`` as FMP fills them (Amazon reports no gross subtotal: FMP's revenue − cost of
    revenue), profit power's margins the same lines."""
    monkeypatch.setattr(R, "MONEY_MAP_SEED", (symbol,))
    bd = breakdown(symbol=symbol, fiscal_year="2025", revenue_sources=sources, reported_revenue=revenue,
                   net_income=net, cost_of_sales=None if gross is None else revenue - gross)
    margins = {"period": "2025", "net_margin": 100.0 * net / revenue,
               "gross_margin": None if gross is None else 100.0 * gross / revenue,
               "operating_margin": None if operating is None else 100.0 * operating / revenue}
    row = {"fiscalYear": "2025", "date": date_, "reportedCurrency": "USD", "revenue": revenue, "netIncome": net,
           "grossProfit": gross, "operatingIncome": operating}
    fmp = FakeFMP(profiles={symbol: prof(symbol, name, exchange="NASDAQ")}, income={symbol: [row]})
    world = dict(revenue=FakeService({symbol: bd}), profit=FakeService({symbol: profit(point=margins)}),
                 facts=facts_fn(), sb=FakeSB({"trending_themes": []}))
    return fmp, world


@pytest.mark.asyncio
async def test_amazons_aws_stays_named_and_its_fmp_filled_gross_profit_is_explained(monkeypatch):
    """AWS is on `R.SEGMENT_ACRONYMS_OK`: Amazon's best-known line is drawn, never folded into "Other or not
    broken out", wherever a feed writes it "AWS" ("Amazon Web Services", FMP's 2026-10-09 spelling, is no code
    at all). FMP fills Amazon's gross profit as revenue − cost of revenue (no gross subtotal in its statement);
    the record carries the statement's line all the same, and the post calls the figures "annual results" and
    explains gross profit — never "as reported". Mutation-checked by hand (2026-10-15): AWS off the allow-list
    turns the segment list red."""
    assert R.segment_fold_reason("Amazon Web Services") is None
    fmp, world = _fy2025_world(monkeypatch, "AMZN", "Amazon.com, Inc.", _AMZN_FY2025, revenue=717e9, net=77e9,
                               gross=360e9, operating=80e9)
    (rec,) = (await run_mm(fmp, **world)).records
    assert [s.name for s in rec.segments] == ["Online Stores", "Third-Party Seller Services", "AWS",
                                               "Advertising Services", "Subscription Services"]
    assert rec.other_usd == pytest.approx(31e9) and rec.gross_profit_usd == 360e9
    out = NT.compose(rec, run_date=MM_RUN, store_state="live", allow_x_url=False)
    assert out["image_spec"]["subtitle"] == "Fiscal 2025 · annual results"
    assert out["captions"]["linkedin"].endswith(
        "Figures come from the company's annual financial statements for fiscal 2025. Gross profit is revenue "
        "minus the cost of sales.")
    assert "as reported" not in json.dumps(out).lower() and NT.validate_package(out, record=rec) == []


@pytest.mark.parametrize("label", ["NXE", "QCT", "CMBU", "PBNA", "IT", "Product", "Products", "PRODUCTS", "Revenue",
                                   "revenues", "Sales", "Net Sales", " net  sales ", "Total", "Segment", "segments"])
@pytest.mark.asyncio
async def test_a_label_that_names_nothing_folds_into_the_rest(monkeypatch, label):
    """A company code (1-4 capitals off the allow-list) or a generic label names no part of the company a reader
    knows: its revenue joins the rest ("Everything else" since the review of 2026-10-16), never a bar or a line of
    its own. Mutation-checked by hand (2026-10-15): without `R.segment_fold_reason` in `_segments` every row turns
    red."""
    sources = [src("Data Center", 50e9), src("Gaming", 30e9), src(label, 6e9), src("Embedded", 8e9)]
    fmp, world = _mm_world(monkeypatch, sources, revenue=94e9, net=20e9)
    (rec,) = (await run_mm(fmp, **world)).records
    assert [s.name for s in rec.segments] == ["Data Center", "Gaming", "Embedded"]
    assert rec.other_usd == pytest.approx(6e9)


@pytest.mark.parametrize("sources, revenue, reason", [
    # Qualcomm-shaped: every line a code → nothing named at all
    ([src("QCT", 33e9), src("QTL", 5.6e9), src("QSI", 0.3e9)], 38.9e9, "segments_thin"),
    # the largest line a code: the named rest would read as the map — "mostly other", refused
    ([src("QCT", 30e9), src("QTL", 6e9), src("Automotive", 2e9), src("IoT", 1e9)], 39e9, "money_map_mostly_other"),
    # PepsiCo-shaped as FMP answered on 2026-10-15: its business lines labelled by codes (the 2026-10-19 live read
    # carries full names — `_PEP_FY2025` — and composes; this shape stays the codes case)
    ([src("PBNA", 27e9), src("PFNA", 24e9), src("Europe", 14e9), src("Latin America (Segment)", 12e9),
      src("Africa, Middle East and South Asia", 6.6e9), src("IBF", 1e9)], 84.6e9, "money_map_mostly_other"),
], ids=["codes_only", "largest_a_code", "pepsico_codes"])
@pytest.mark.asyncio
async def test_a_map_explained_mostly_by_codes_is_refused(monkeypatch, sources, revenue, reason):
    """Folding never publishes the smaller lines as if they explained the company: the existing gates
    (`segments_thin`, `money_map_mostly_other`) refuse what is left."""
    fmp, world = _mm_world(monkeypatch, sources, revenue=revenue, net=0.2 * revenue)
    got = await run_mm(fmp, **world)
    assert got.records == () and got.rejections == {reason: 1}


def test_live_labels_are_shown_as_the_company_writes_them_and_codes_fold():
    """FMP's labels read live 2026-10-15: McDonald's "UNITED STATES" is shown "United States"; PepsiCo's "Latin
    America (Segment)" — XBRL's suffix, undrawable for its parentheses — is "Latin America"; "XBOX" is "Xbox"
    BEFORE the code check (4 capitals), so it stays; PepsiCo's "PBNA" / "IBF" fold."""
    named, rest = A._segments(breakdown(revenue_sources=[
        src("UNITED STATES", 10e9), src("Latin America (Segment)", 9e9), src("XBOX", 8e9), src("PBNA", 7e9),
        src("IBF", 1e9)]))
    assert [s.name for s in named] == ["United States", "Latin America", "Xbox"] and rest == pytest.approx(8e9)


@pytest.mark.parametrize("sources, revenue, net, regional", [
    # McDonald's fiscal 2025 as FMP labels it (live 2026-10-15): its 2015-2018 "High-Growth Markets" label for what
    # it has reported since 2019 as International Operated Markets (the review of 2026-10-16, MM-1: corrected for
    # McDonald's only) + its home market + a 59-character label (over the cap: folded) — mixed, so neutral
    ([src("High-Growth Markets", 13.63e9), src("UNITED STATES", 10.82e9),
      src("International Developmental Licensed Markets and Corporate", 2.43e9)], 26.88e9, 8.5e9, False),
    # KO-shaped and ALL regional: "by region"
    ([src("North America", 19.2e9), src("Europe, Middle East & Africa", 8.1e9), src("Latin America", 6.9e9),
      src("Asia Pacific", 5.2e9)], 39.4e9, 9.9e9, True),
], ids=["mcdonalds_mixed", "ko_regional"])
@pytest.mark.asyncio
async def test_the_adapters_parts_decide_the_wording(monkeypatch, sources, revenue, net, regional):
    """End to end (adapter record → template): the breakdown reads "by region" only when every named part is a
    region by its exact name; otherwise the neutral "How fiscal 2025 revenue broke down". "the United States" in
    running text only — its bar label stays "United States"."""
    fmp, world = _fy2025_world(monkeypatch, "MCD" if not regional else "KO",
                               "McDonald's Corporation" if not regional else "The Coca-Cola Company",
                               sources, revenue=revenue, net=net)
    (rec,) = (await run_mm(fmp, **world)).records
    out = NT.compose(rec, run_date=MM_RUN, store_state="live", allow_x_url=False)
    assert NT.validate_package(out, record=rec) == []
    paras = out["captions"]["linkedin"].split("\n\n")
    if regional:
        assert [s.name for s in rec.segments][:2] == ["North America", "Europe, Middle East & Africa"]
        assert paras[1].startswith("How fiscal 2025 revenue broke down by region: North America, $19.2 billion; ")
        assert out["image_post"]["paragraphs"][0].startswith("Bars show revenue by region: ")
    else:
        assert [s.name for s in rec.segments] == ["International Operated Markets", "United States"]
        assert rec.other_usd == pytest.approx(2.43e9)
        assert paras[1] == ("How fiscal 2025 revenue broke down: International Operated Markets, $13.6 billion; the "
                            "United States, $10.8 billion; everything else, $2.4 billion.")
        assert out["video_script"][1] == ("International Operated Markets made up 51% of revenue, and the United "
                                          "States made up 40% of revenue.")
        assert [b["label"] for b in out["image_spec"]["segments"]] == [
            "International Operated Markets", "United States", "Everything else"]
        assert out["image_post"]["paragraphs"][0].startswith("Bars show how revenue broke down: ")
        assert "High-Growth" not in json.dumps(out)
    assert "business line" not in json.dumps(out) and "as reported" not in json.dumps(out).lower()
    assert "not broken out" not in json.dumps(out)


def test_a_company_scoped_label_is_corrected_for_that_company_only():
    """The review of 2026-10-16 (MM-1, confirmed on EDGAR): FMP's McDonald's breakdown carries "High-Growth
    Markets", a segment McDonald's retired in 2019, for its International Operated Markets. `_segments` reads the
    breakdown's own symbol, so the correction is McDonald's alone — another company keeps a real segment of that
    name — and a theme's segment fact (`_segment_fact`, which reads `_segments`) gets it too. Mutation-checked by
    hand (2026-10-19): with `_segments` passing no symbol, the McDonald's rows turn red."""
    sources = [src("High-Growth Markets", 13.63e9), src("UNITED STATES", 10.82e9),
               src("International Developmental Licensed Markets and Corporate", 2.43e9)]
    named, rest = A._segments(breakdown(symbol="MCD", revenue_sources=sources))
    assert [s.name for s in named] == ["International Operated Markets", "United States"]
    assert rest == pytest.approx(2.43e9)
    for other in ("YUM", None, "", 7):
        named, _rest = A._segments(breakdown(symbol=other, revenue_sources=sources))
        assert [s.name for s in named] == ["High-Growth Markets", "United States"], other
    fact = A._segment_fact(breakdown(symbol="MCD", revenue_sources=sources, reported_revenue=26.88e9),
                           date(2026, 11, 19))
    assert fact == ("International Operated Markets", pytest.approx(13.63e9 / 26.88e9), "2025")


#: PepsiCo's fiscal 2025 as FMP labels it (live, 2026-10-19): the realigned EMEA under the pre-2025 AMESA name
#: (B1R-MM-PEP-STALE-LABEL), "International Beverage Franchise", "(Segment)" on Latin America, and a 59-character
#: label for Asia Pacific Foods (over the 40-character cap: the rest). The figures are PepsiCo's own Q4 2025 release
#: ($ millions: PBNA 28,197; PFNA 27,528; EMEA 18,025; LatAm 10,549; IB Franchise 4,997; APAC Foods 4,629).
_PEP_FY2025 = [src("PepsiCo Beverages North America", 28.197e9), src("PepsiCo Foods North America", 27.528e9),
               src("Africa, Middle East and South Asia", 18.025e9), src("Latin America (Segment)", 10.549e9),
               src("International Beverage Franchise", 4.997e9),
               src("Asia Pacific, Australia and New Zealand, and China Region", 4.629e9)]


@pytest.mark.asyncio
async def test_pepsicos_realigned_emea_is_never_called_africa_middle_east_and_south_asia(monkeypatch):
    """The re-review of 2026-10-19 (B1R-MM-PEP-STALE-LABEL, checked on EDGAR): FMP's fiscal-2025 breakdown labels
    PepsiCo's $18.0B Europe, Middle East and Africa segment with the retired "Africa, Middle East and South Asia" (a
    $6.2B segment in 2024, beside a $13.9B Europe line). The post would have attributed Europe's revenue to Africa,
    the Middle East and South Asia — a false fact about a named company. Corrected for PepsiCo only
    (`R.SEGMENT_SYMBOL_OVERRIDES`); its 10-K's "International Beverages Franchise" too. Mutation-checked by hand
    (2026-10-19): with the PEP row removed the record's third segment was "Africa, Middle East and South Asia" (red)."""
    fmp, world = _fy2025_world(monkeypatch, "PEP", "PepsiCo, Inc.", _PEP_FY2025, revenue=93.925e9, net=8.24e9)
    (rec,) = (await run_mm(fmp, **world)).records
    assert [s.name for s in rec.segments] == ["PepsiCo Beverages North America", "PepsiCo Foods North America",
                                              "Europe, Middle East and Africa", "Latin America",
                                              "International Beverages Franchise"]
    assert rec.other_usd == pytest.approx(4.629e9)
    out = NT.compose(rec, run_date=MM_RUN, store_state="live", allow_x_url=False)
    assert NT.validate_package(out, record=rec) == []
    text = json.dumps(out)
    assert "South Asia" not in text and "Europe, Middle East and Africa" in text
    assert "Europe, Middle East and Africa" in [b["label"] for b in out["image_spec"]["segments"]]
    assert out["captions"]["linkedin"].split("\n\n")[1].startswith("How fiscal 2025 revenue broke down: ")  # mixed
    # another company keeps a real segment of that name
    named, _rest = A._segments(breakdown(symbol="KO", revenue_sources=_PEP_FY2025))
    assert "Africa, Middle East and South Asia" in [s.name for s in named]


def test_walmarts_stripped_labels_are_shown_as_walmart_writes_them():
    """The review of 2026-10-16 (live preview F5): the feed strips Walmart's punctuation ("Walmart U S", "Sams
    Club" — a misspelled brand); `SEGMENT_DISPLAY_OVERRIDES` shows the company's own names."""
    named, rest = A._segments(breakdown(symbol="WMT", revenue_sources=[
        src("Walmart U S", 462e9), src("Walmart International", 123e9), src("Sams Club", 93e9)]))
    assert [s.name for s in named] == ["Walmart U.S.", "Walmart International", "Sam's Club"] and rest == 0.0


def test_a_region_acronym_is_named_never_folded():
    """The review of 2026-10-16 (MM-2): "US" (and UK, EU, UAE, USA) is a region a reader knows — before, the code
    fold put its revenue in the rest while the map still read "by region". A company code still folds."""
    named, rest = A._segments(breakdown(revenue_sources=[src("Europe", 50e9), src("Asia Pacific", 30e9),
                                                         src("US", 20e9), src("QCT", 5e9)]))
    assert [s.name for s in named] == ["Europe", "Asia Pacific", "US"] and rest == pytest.approx(5e9)
    assert all(R.is_region_name(s.name) for s in named)


@pytest.mark.parametrize("sources, fact", [
    ([src("QCT", 33e9), src("QTL", 5.6e9), src("QSI", 0.3e9)], None),                  # codes only: no line at all
    # a code larger than every named line: the remainder sits above the top — never "Handsets, the largest"
    ([src("QCT", 20e9), src("Handsets", 15e9), src("IoT", 4e9)], None),
    ([src("Products", 70e9), src("Services", 30e9)], None),                             # a generic largest line
    ([src("Handsets", 24.9e9), src("QTL", 5.6e9), src("IoT", 5.4e9), src("Automotive", 2.9e9)],
     ("Handsets", 24.9e9 / 38.9e9, "2025")),
    ([src("AWS", 30e9), src("Online Stores", 8.9e9)], ("AWS", 30e9 / 38.9e9, "2025")),  # AWS is a line
], ids=["codes_only", "code_above_top", "generic_top", "named_top_with_a_code", "aws"])
def test_a_company_code_is_never_a_themes_line(sources, fact):
    """A theme tile's line (`_segment_fact`) reads `_segments`' names, so a code or a generic label is never
    drawn on a tile or narrated, and never hides a larger line behind the named "largest" one."""
    assert A._segment_fact(breakdown(revenue_sources=sources, reported_revenue=38.9e9), date(2026, 11, 19)) == fact


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
    sb.tables["trillion_club_filings"] = [
        {"cik": "0001045810", "period": "2026-Q2", "holdings": previous},
        stored_current("0001045810", "2026-Q3", club.details["nvidia"].changes, counts)]   # F1 / F2 read it
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
async def test_a_record_full_of_new_holdings_keeps_the_largest_changes_by_size(monkeypatch):
    """The ARK shape: many material new holdings and smaller decreases — review 2026-11-20: the record holds the
    filing's LARGEST changes by dollar size, whatever their kinds (the one ranked list), never each kind's largest
    reserved (that drew a decrease smaller than every new holding left out)."""
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
    # 8 slots by size: the new holdings of $109M … $102M; the largest decrease's shares were worth $100M (fewer)
    assert _moves(rec) == [(s, "newly_reported") for s in ("NJ", "NI", "NH", "NG", "NF", "NE", "ND", "NC")]


def _mv(sym, move, size):
    m = {"symbol": sym, "move": move, "value": None, "prev_value": None, "flow": 0.0}
    m[{"newly_reported": "value", "no_longer_reported": "prev_value"}.get(move, "flow")] = size
    return m


def _claims_filing():
    """Review 2026-11-20's example: decreases of $5B / $4B / $3B / $2B / $1B, increases of $100M / $90M, new holdings of
    $80M / $70M, exits of $60M / $50M."""
    return ([_mv(f"D{i}", "decreased", v) for i, v in enumerate((5e9, 4e9, 3e9, 2e9, 1e9), 1)]
            + [_mv(f"I{i}", "increased", v) for i, v in enumerate((100e6, 90e6), 1)]
            + [_mv(f"N{i}", "newly_reported", v) for i, v in enumerate((80e6, 70e6), 1)]
            + [_mv(f"G{i}", "no_longer_reported", v) for i, v in enumerate((60e6, 50e6), 1)])


@pytest.mark.parametrize("limit, picked", [
    (A.THIRTEEN_F_MAX_PROFILED, ["D1", "D2", "D3", "D4", "D5", "I1", "I2", "N1", "N2", "G1"]),
    (R.THIRTEEN_F_MAX_MOVES, ["D1", "D2", "D3", "D4", "D5", "I1", "I2", "N1"]),
    (2, ["D1", "D2"]),
    (0, []),
])
def test_the_13f_cut_is_by_dollar_size_alone_in_both_lanes(limit, picked):
    """Review 2026-11-20 (confirmed): the roundup draws the ONE ranked list (largest dollar change first), so both its
    cuts are by size alone — with each kind's top two reserved (the old `_select_moves`), this filing kept D1, D2, I1,
    I2, N1, N2, G1, G2: the $3B, $2B and $1B decreases in no picture and no caption, a $70M new holding drawn as row
    6. Returned in the record's order (`_move_order`). Mutation-checked by hand: restoring a per-kind reserve turns the
    eight-slot row red."""
    got = A._cut(_claims_filing(), limit)
    assert sorted(m["symbol"] for m in got) == sorted(picked)
    assert got == sorted(got, key=A._move_order)
    assert not hasattr(A, "_select_moves") and not hasattr(A, "THIRTEEN_F_KIND_RESERVE")


def test_the_13f_cut_breaks_ties_deterministically():
    tie = [_mv("B", "increased", 5.0), _mv("A", "decreased", 5.0), _mv("C", "newly_reported", 5.0)]
    assert sorted(m["symbol"] for m in A._cut(tie, 3)) == ["A", "B", "C"]
    assert [m["symbol"] for m in A._cut(tie, 1)] == [m["symbol"] for m in A._cut(list(reversed(tie)), 1)]
    assert A._cut([], 8) == []


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
        self.statement_calls: List[Any] = []

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
        """The earnings statement read (review round 9's currency read; since Batch 1 also the period
        check): a QUARTERLY statement answers from ``currency`` — a currency code (the calendar-derived
        rows of `quarter_rows`, in that currency), a whole answer (a list), or an exception — "USD" for a
        symbol not listed; an annual one is `FakeFMP`'s."""
        if period != "quarter":
            return await super().get_income_statement(sym, period, limit)
        self.calls["statement"] += 1
        self.statement_calls.append((sym, limit))
        spec = self.currency.get(sym, "USD")
        if isinstance(spec, str):
            spec = quarter_rows(self.calendar, sym, currency=spec)[:limit]
        return self._answer(spec)


def _quarter_end_before(day: date) -> date:
    """The calendar quarter end strictly before ``day``."""
    q_month = ((day.month - 1) // 3) * 3
    return date(day.year - 1, 12, 31) if q_month == 0 else date(day.year, q_month + 1, 1) - timedelta(days=1)


def quarter_rows(calendar: Dict[str, Any], sym: str, *, currency: str = "USD") -> List[Dict[str, Any]]:
    """The quarterly statements a company that reported ``sym``'s newest calendar row would have, newest
    first, FMP's shape: the reported quarter (the calendar quarter end before the report day) carrying the
    calendar's own revenueActual — what the period check matches — then three earlier quarters at 90%,
    82% and 75% of it. A row with no usable revenue (or no row at all) gives a statement with no revenue
    on the reported quarter."""
    found = [(day, r) for day, rows in calendar.items() if isinstance(rows, list)
             for r in rows if isinstance(r, dict) and r.get("symbol") == sym]
    day, row = max(found, key=lambda t: t[0]) if found else ("2027-01-12", {})
    ra = row.get("revenueActual")
    usable = isinstance(ra, (int, float)) and not isinstance(ra, bool) and ra == ra and ra > 0
    end = _quarter_end_before(date.fromisoformat(day))
    out = []
    for i, share in enumerate((1.0, 0.90, 0.82, 0.75)):
        out.append({"symbol": sym, "date": end.isoformat(), "period": f"Q{(end.month - 1) // 3 + 1}",
                    "fiscalYear": str(end.year), "reportedCurrency": currency,
                    "revenue": round(ra * share) if usable else None, "eps": 9.8765})
        end = _quarter_end_before(end)
    return out


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


class _Rows:
    """news-v23 (the restyle, owner 2026-11-19): a Congress Count and an earnings post are ONE record of several
    companies (`R.CongressMonth` / `R.EarningsDay`). The per-company gate tests below read the answer ROW by row —
    `.records` are the one record's counts / reports (empty without one), `.record` the record itself, every other
    attribute the answer's — under `_per_company_bounds` (a post of ONE company, up to the call's `limit`), so a gate
    is tested on its company alone; the real bounds (2-3, 2-4) and the bundling are pinned by their own tests
    (`test_news_v23_*`)."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer

    def __getattr__(self, name: str) -> Any:
        return getattr(self.answer, name)

    @property
    def record(self) -> Any:
        return self.answer.records[0] if self.answer.records else None

    @property
    def records(self) -> tuple:
        rec = self.record
        if isinstance(rec, R.EarningsDay):
            return rec.reports
        if isinstance(rec, R.CongressMonth):
            return rec.counts
        return self.answer.records


_BOUNDS_DEPTH = [0]


@contextlib.contextmanager
def _per_company_bounds(limit: int):
    """A post of ONE company up to `limit` (the per-company tests' view), set by the OUTERMOST entry and restored to
    `_REAL_BOUNDS` by the last exit — two concurrent calls (`asyncio.gather(crun(…), crun(…))`) interleave their
    entries and exits, and a save-and-restore per entry leaked the test bounds into every later test."""
    if _BOUNDS_DEPTH[0] == 0:
        R.EARNINGS_DAY_COMPANIES = R.CONGRESS_MONTH_COMPANIES = (1, limit)
    _BOUNDS_DEPTH[0] += 1
    try:
        yield
    finally:
        _BOUNDS_DEPTH[0] -= 1
        if _BOUNDS_DEPTH[0] == 0:
            R.EARNINGS_DAY_COMPANIES, R.CONGRESS_MONTH_COMPANIES = _REAL_BOUNDS


async def crun(fmp, **kw):
    with _per_company_bounds(kw.get("limit", 5)):
        return _Rows(await run("congress_count", fmp, run_date=kw.pop("run_date", CONGRESS_RUN), **kw))


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
    assert isinstance(got.record, R.CongressMonth) and got.record.month == "2026-11"
    assert R.ledger_key(got.record) == CONGRESS_KEY                     # news-v23: the month's ONE key, as before
    # An exchange and a stock option are counted as refused rows; a sale and the rows of other
    # months are not purchases of the month at all.
    assert got.rejections == {"exchange_type": 1, "option_asset": 1}
    # Both chambers walked at 2000 rows, then read once more to confirm.
    assert fmp.congress_calls == [("senate", 2000), ("house", 2000), ("senate", 2000), ("house", 2000)]
    assert fmp.profile_requests == [["AAPL", "MSFT"]]                      # NVDA (1 member) never profiled
    # No identity anywhere: the fact sheet, the log, the memo.
    _no_identity(json.dumps(R.fact_sheet(got.record, rejections=got.rejections, selection={})))
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
#
# news-v23 (the restyle, owner 2026-11-19: "filter for companies that public only. no private" + "add 1-3 more companies
# for the NVIDIA (or the other trillion companies)"): ONE `R.CompanyStakes` per investor — its stakes in PUBLIC companies
# (a kind that can be public, a listed U.S. symbol, a profile the common-stock gate accepts naming that company, and
# ``isActivelyTrading`` exactly True), the investor with the most not-yet-posted public stakes first. A test stake row is
# public by default (`stake`): its symbol is derived from its name (`_sym_of`) and `stakes_world` profiles it.

STAKES_RUN = date(2026, 12, 29)          # the first stakes Tuesday (13F season ends 12-27)


def _sym_of(name: str) -> str:
    """A test investee's listed symbol: four letters derived from its name (stable, distinct per name)."""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"stake-symbol/{name}").bytes
    return "".join(chr(65 + b % 26) for b in digest[:4]).replace("W", "X").replace("Q", "P")


def stake(slug, name, **over):
    row = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/{name}/{over.get('as_of', '')}")),
           "created_at": "2026-09-24T12:00:00+00:00", "company_slug": slug, "kind": "us_listed_off_13f",
           "investee_name": name, "investee_cusip": None, "investee_us_symbol": _sym_of(name), "local_listing": None,
           "ownership_pct": None, "ownership_basis": None, "disclosed_value_usd": 1e9, "value_basis": "invested",
           "as_of": "2026-06-30", "source_title": "NVIDIA 10-Q (quarter ended Jul 26, 2026)",
           "source_url": "https://www.sec.gov/Archives/edgar/ZQXSOURCEURL.htm", "source_confidence": "primary",
           "published": True, "listed_since": None, "background": None, "verified_on": "2026-12-01",
           "sort_order": 1, "material": True, "tied_to_deal": False, "updated_at": "2026-12-01T00:00:00+00:00",
           # migration 205 (the stake finder): every row before it reads as hand-kept (NOT NULL DEFAULT 'seed')
           "origin": "seed", "owner_kept_at": None}
    row.update(over)
    return row


def private_stake(slug, name, **over):
    """A stake in a company with no listing (the catalogue's Nscale, OpenAI …): never a post since news-v23."""
    return stake(slug, name, **{"kind": "private", "investee_us_symbol": None, **over})


def stakes_rows():
    return [
        stake("nvidia", "Intel", investee_us_symbol="INTC", kind="on_13f_note", disclosed_value_usd=5e9,
              as_of="2025-12-26"),
        stake("nvidia", "Synopsys", investee_us_symbol="SNPS", kind="on_13f_note", disclosed_value_usd=2e9,
              as_of="2025-12-01", source_title="Synopsys 8-K (Dec 1, 2025)"),
        stake("amazon", "Rivian Automotive", investee_us_symbol="RIVN", disclosed_value_usd=1e9, as_of="2026-04-23",
              source_title="Amazon 10-Q (quarter ended Jun 30, 2026)"),
        # private: never a post (news-v23)
        private_stake("nvidia", "Nscale", disclosed_value_usd=777399382.46, as_of="2026-03-27",
                      background="Nscale filed an S-1 in September 2026."),
        private_stake("amazon", "OpenAI", disclosed_value_usd=50e9, as_of="2026-07-31",
                      source_title="Amazon 10-Q (quarter ended Jun 30, 2026)"),
        stake("tsmc", "IMS Nanofabrication Global", kind="non_us_listed", investee_us_symbol=None,
              value_basis="fair_value", disclosed_value_usd=432795000.0, ownership_pct=10.0, as_of="2024-12-31",
              source_title="TSMC 2025 Annual Report", local_listing="Austria"),
        # refused
        private_stake("nvidia", "Private companies (not named)", value_basis="carrying_value",
                      disclosed_value_usd=47.9e9),
        stake("nvidia", "Marvell Technology (convertible preferred)", investee_us_symbol="MRVL", as_of="2026-08-01"),
        stake("nvidia", "CoreWeave", investee_us_symbol="CRWV", disclosed_value_usd=None, value_basis=None,
              ownership_pct=10.66, as_of="2026-04-15"),
        stake("samsung", "Corning", investee_us_symbol="GLW", as_of="2026-06-30"),
        stake("amazon", "Anthropic", verified_on="2026-08-01", as_of="2026-06-30"),
        stake("nvidia", "Old Co", as_of="2023-06-30"),
        stake("nvidia", "Bad Source", source_url="http://insecure.example/x"),
    ]


#: Symbols `stakes_world` never profiles (a listing FMP does not know).
_NO_PROFILE = frozenset({"ZZZZ"})


def stakes_world(rows=None, **sb_errors):
    companies = [SimpleNamespace(slug="nvidia", name="NVIDIA", card_kind="thirteen_f", detail_symbol="NVDA"),
                 SimpleNamespace(slug="amazon", name="Amazon", card_kind="thirteen_f", detail_symbol="AMZN"),
                 SimpleNamespace(slug="samsung", name="Samsung", card_kind="non_us", detail_symbol=None)]
    club = FakeClub(group=SimpleNamespace(companies=companies,
                                          also_in_club=[SimpleNamespace(slug="tsmc", name="TSMC")]))
    rows = stakes_rows() if rows is None else rows
    sb = FakeSB({
        "trillion_club_companies": [
            {"slug": "nvidia", "ciks": ["0001045810"], "card_kind": "thirteen_f", "use_13f": True,
             "detail_symbol": "NVDA", "published": True},
            {"slug": "amazon", "ciks": ["0001018724"], "card_kind": "thirteen_f", "use_13f": True,
             "detail_symbol": "AMZN", "published": True},
            {"slug": "samsung", "ciks": [], "card_kind": "non_us", "detail_symbol": None, "published": True},
            {"slug": "tsmc", "ciks": [], "card_kind": "non_us", "detail_symbol": "TSM", "published": True},
        ],
        "trillion_club_stakes": rows,
    }, errors=sb_errors or None)
    profiles = {
        "NVDA": prof("NVDA", "NVIDIA Corporation", exchange="NASDAQ", price=91919.19),
        "AMZN": prof("AMZN", "Amazon.com, Inc.", exchange="NASDAQ"),
        "TSM": prof("TSM", "Taiwan Semiconductor Manufacturing Company Limited", isAdr=True, currency="USD"),
        "INTC": prof("INTC", "Intel Corporation", exchange="NASDAQ"),
        "SNPS": prof("SNPS", "Synopsys, Inc.", exchange="NASDAQ"),
        "RIVN": prof("RIVN", "Rivian Automotive, Inc.", exchange="NASDAQ"),
        "CRWV": prof("CRWV", "CoreWeave, Inc.", exchange="NASDAQ"),
        "GLW": prof("GLW", "Corning Incorporated"),
        "BABA": prof("BABA", "Alibaba Group Holding Limited", isAdr=True),
    }
    # every other listed investee of the rows: a plain U.S. common-stock profile naming it, actively trading
    for r in rows if isinstance(rows, list) else ():
        sym = r.get("investee_us_symbol") if isinstance(r, dict) else None
        if isinstance(sym, str) and R.canonical_symbol(sym) == sym and sym not in profiles and sym not in _NO_PROFILE:
            profiles[sym] = prof(sym, str(r.get("investee_name")), exchange="NASDAQ")
    return club, sb, FakeFMP(profiles=profiles)


async def srun(club, sb, fmp, **kw):
    return await run("company_stakes", fmp, club=club, sb=sb, run_date=kw.pop("run_date", STAKES_RUN), **kw)


def _posts(got) -> List[List[str]]:
    """Each record's stakes, by investee name, in draw order."""
    return [[s.investee_name for s in rec.stakes] for rec in got.records]


def _stake_names(got) -> List[str]:
    """Every stake of every record, by investee name (records in order, rows in draw order)."""
    return [n for post in _posts(got) for n in post]


def _stake_rows(got) -> List[R.CompanyStake]:
    return [s for rec in got.records for s in rec.stakes]


@pytest.fixture
def club_on(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)


@pytest.mark.asyncio
async def test_stakes_are_public_named_figured_and_one_post_per_investor(club_on):
    """news-v23 (owner 2026-11-19): every stake in a post is a PUBLIC company's — Nscale, OpenAI and IMS (private / not
    U.S.-listed) are dropped `stake_not_public` before any profile, never named — and one post holds one investor's
    stakes, the largest figure first, the investor with the most public stakes first."""
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp)
    assert got.skip_reason is None
    assert [(rec.investor.name, [(s.investee_name, s.value_usd, s.value_basis) for s in rec.stakes])
            for rec in got.records] == [
        ("NVIDIA", [("Intel", 5e9, "invested"), ("Synopsys", 2e9, "invested")]),
        ("Amazon", [("Rivian Automotive", 1e9, "invested")]),
    ]
    nvidia, amazon = got.records
    assert [s.investee for s in nvidia.stakes] == [R.CompanyRef("INTC", "Intel"), R.CompanyRef("SNPS", "Synopsys")]
    assert all(s.is_new is False for s in _stake_rows(got))                 # catalogue stakes (decision 1)
    assert R.ledger_key(nvidia) == f"news:company_stakes:{nvidia.stakes[0].stake_id}+{nvidia.stakes[1].stake_id}"
    assert R.ledger_key(amazon) == f"news:company_stakes:{amazon.stakes[0].stake_id}"
    assert got.rejections == {"stake_aggregate": 2, "stake_no_figure": 1, "investor_unlisted": 1,
                              "stake_stale": 1, "stake_too_old": 1, "stake_invalid": 1, "stake_not_public": 3}
    # FMP-free for the data: one profile batch for the CompanyRefs, nothing else — and no private investee asked for
    assert fmp.calls == Counter({"profiles": 1})
    assert ("trillion_club_stakes", A._STAKE_COLUMNS) in sb.selects
    # migration 205: the stakes read (and nothing else) asks only for rows a person reviewed
    assert sb.ors == [("trillion_club_stakes", A._STAKE_POSTABLE_FILTER)]


@pytest.mark.asyncio
async def test_an_investor_whose_stakes_are_all_private_is_skipped(club_on, caplog):
    """The owner's rule ("no private"), whole: an investor whose only figured stakes are private (Amazon's OpenAI and
    Anthropic, NVIDIA's Nscale) gets no post — the series skips `stakes_none_public` and the chain falls through; no
    profile is read, and no line names a private company."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    rows = [private_stake("amazon", "OpenAI", disclosed_value_usd=50e9, as_of="2026-07-31"),
            private_stake("amazon", "Anthropic", disclosed_value_usd=18e9, as_of="2026-06-30"),
            private_stake("nvidia", "Nscale", disclosed_value_usd=777e6, as_of="2026-03-27"),
            stake("nvidia", "Mistral AI", kind="commitment", value_basis="committed_up_to", investee_us_symbol=None)]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert got.records == () and got.skip_reason == "stakes_none_public"
    assert got.rejections == {"stake_not_public": 4} and fmp.calls == Counter()
    assert not any(w in caplog.text for w in ("OpenAI", "Anthropic", "Nscale", "Mistral"))


@pytest.mark.parametrize("over, rejection", [
    ({"investee_us_symbol": "INTC"}, None),                                    # kept: a public company's
    ({"investee_us_symbol": "INTC", "kind": "commitment", "value_basis": "committed_up_to"}, None),
    ({"investee_us_symbol": "INTC", "investee_name": "Intellicheck"}, "stake_listing_unverified"),  # another company
    ({"investee_us_symbol": "BABA", "investee_name": "Alibaba Group"}, "stake_listing_unverified"),  # an ADR
    ({"investee_us_symbol": "NVDA", "investee_name": "NVIDIA"}, "stake_listing_unverified"),         # the investor
    ({"investee_us_symbol": "ZZZZ"}, "stake_listing_unverified"),            # no profile: fail closed
    ({"investee_us_symbol": "BAD!"}, "stake_not_public"),                    # no listed symbol
    ({"investee_us_symbol": None}, "stake_not_public"),
    ({"investee_us_symbol": "INTC", "kind": "private"}, "stake_not_public"),  # a kind that is never public
    ({"investee_us_symbol": "INTC", "kind": "non_us_listed"}, "stake_not_public"),
    ({"investee_us_symbol": "INTC", "_trading": False}, "stake_not_trading"),    # bought out / delisted
    ({"investee_us_symbol": "INTC", "_trading": None}, "stake_not_trading"),     # absent: fail closed
    ({"investee_us_symbol": "INTC", "_trading": "true"}, "stake_not_trading"),   # junk: fail closed
], ids=["public", "commitment_to_public", "names_disagree", "adr", "the_investor", "no_profile", "bad_symbol",
        "no_symbol", "kind_private", "kind_non_us", "not_trading", "trading_absent", "trading_junk"])
@pytest.mark.asyncio
async def test_a_stake_is_posted_only_when_its_company_is_public_and_actively_trading(club_on, caplog, over, rejection):
    """PUBLIC ONLY, fail closed (owner 2026-11-19): a listed U.S. symbol, a kind that can be public, the common-stock
    profile gate naming THIS company, and the profile's ``isActivelyTrading`` exactly True — the 13F buy-out check's
    reading — else the stake is DROPPED with its reason (a WARNING for a company no longer trading), never shown by name
    only. Mutation-checked by hand (2026-11-20): `_public_investee`'s trading check removed, the three not-trading rows
    turned red — still dropped, but as `stake_listing_unverified` by the common-stock profile gate (a second layer:
    fail closed twice), never named by the reason the owner reads."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    over = dict(over)
    trading = over.pop("_trading", True)
    row = stake("nvidia", "Intel", **{"as_of": "2026-06-30", **over})
    club, sb, fmp = stakes_world([row])
    if trading is not True:
        fmp.profiles["INTC"] = {k: v for k, v in fmp.profiles["INTC"].items() if k != "isActivelyTrading"}
        if trading is not None:
            fmp.profiles["INTC"]["isActivelyTrading"] = trading
    got = await srun(club, sb, fmp)
    if rejection is None:
        assert _stake_names(got) == ["Intel"] and got.rejections == {}
        assert got.records[0].stakes[0].investee == R.CompanyRef("INTC", "Intel")
        return
    assert got.records == () and got.rejections == {rejection: 1} and got.skip_reason == "stakes_none_public"
    if rejection == "stake_not_trading":
        assert "dropped as stake_not_trading" in caplog.text and "public companies only" in caplog.text
    assert "Intellicheck" not in caplog.text and "Alibaba" not in caplog.text


@pytest.mark.asyncio
async def test_catalogue_stakes_switched_off_leave_only_new_rows(club_on, monkeypatch):
    monkeypatch.setattr(R, "STAKES_INCLUDE_CATALOGUE", False)
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp)
    assert got.records == () and got.skip_reason == "stakes_none_new" and fmp.calls == Counter()
    rows = stakes_rows()
    rows[0]["created_at"] = "2026-11-20T09:00:00+00:00"                    # Intel: a NEW row
    club, sb, fmp = stakes_world(rows)
    A.clear_memo()
    got = await srun(club, sb, fmp)
    assert [[(s.investee_name, s.is_new) for s in rec.stakes] for rec in got.records] == [[("Intel", True)]]


@pytest.mark.asyncio
async def test_a_post_ranks_its_stakes_by_figure_and_the_investor_with_more_public_stakes_leads(club_on):
    """In a post: the largest figure first, an equal figure the EARLIER as-of first (stakes_nvidia.jpg: Synopsys before
    Coherent), then the stake id. Between posts: the investor with the most not-yet-posted public stakes first — even
    behind newer stakes of another investor — and on a tie the walk's own order (the newest as-of; a new row before a
    catalogue row of the same day)."""
    rows = [stake("nvidia", "Alpha Co", as_of="2026-06-30", disclosed_value_usd=2e9),
            stake("nvidia", "Beta Co", as_of="2026-05-30", disclosed_value_usd=2e9),
            stake("nvidia", "Gamma Co", as_of="2026-07-01", disclosed_value_usd=5e9),
            stake("amazon", "Delta Co", as_of="2026-11-01")]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert _posts(got) == [["Gamma Co", "Beta Co", "Alpha Co"], ["Delta Co"]]
    # a tie (one stake each): the newer stake's investor first …
    rows = [stake("nvidia", "Alpha Co", as_of="2026-06-30"), stake("amazon", "Delta Co", as_of="2026-07-01")]
    A.clear_memo()
    club, sb, fmp = stakes_world(rows)
    assert _posts(await srun(club, sb, fmp)) == [["Delta Co"], ["Alpha Co"]]
    # … and on the same day a NEW row before a catalogue one
    rows = [stake("amazon", "Delta Co", as_of="2026-06-30"),
            stake("nvidia", "Alpha Co", as_of="2026-06-30", created_at="2026-11-30T00:00:00Z")]
    A.clear_memo()
    club, sb, fmp = stakes_world(rows)
    assert _posts(await srun(club, sb, fmp)) == [["Alpha Co"], ["Delta Co"]]


@pytest.mark.asyncio
async def test_a_post_holds_at_most_four_stakes_and_the_rest_wait(club_on, caplog, monkeypatch):
    """`R.STAKES_POST_STAKES` (1-4 — review 2026-11-20: RESTYLE_SPEC §2.5, "one investor per post, 1-4 PUBLIC
    investees"; it was 8): an investor with more public stakes posts its four largest — every one drawn on the picture
    — the rest wait for a later post (an INFO line), and the post's key names exactly the four. The profile stage takes
    `STAKES_PROFILE_CANDIDATES` stakes in ONE batch."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    names = [f"Co {chr(65 + i)} Labs" for i in range(10)]
    rows = [stake("nvidia", n, as_of="2026-06-30", disclosed_value_usd=1e9 + i * 1e6) for i, n in enumerate(names)]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    (rec,) = got.records
    assert R.STAKES_POST_STAKES == (1, 4)
    assert [s.investee_name for s in rec.stakes] == list(reversed(names))[:4]
    assert "holds 10 public stakes — the post takes 4, the rest wait" in caplog.text
    assert R.stake_ref_ids(R.ledger_key(rec)) == [s.stake_id for s in rec.stakes]
    assert fmp.calls == Counter({"profiles": 1}) and A.STAKES_PROFILE_CANDIDATES >= 10
    # the next run: the four posted stakes are `already_posted`, the next four largest make the post
    A.clear_memo()
    club, sb, fmp = stakes_world(rows)
    again = await srun(club, sb, fmp, exclude={R.ledger_key(rec)})
    assert [s.investee_name for s in again.records[0].stakes] == list(reversed(names))[4:8]
    assert again.rejections["already_posted"] == 4
    # past the profile stage's bound, the walk's later stakes wait (logged), still one batch
    monkeypatch.setattr(A, "STAKES_PROFILE_CANDIDATES", 3)
    A.clear_memo()
    caplog.clear()
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert len(got.records[0].stakes) == 3 and "3 of 10 stake(s) with a listed symbol reach the profile stage" in (
        caplog.text)
    assert fmp.calls == Counter({"profiles": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("twin", ["same_symbol_other_kind", "other_spelling"])
async def test_two_rows_of_one_investee_drop_the_smaller_never_the_investors_post(club_on, caplog, twin):
    """Review 2026-11-20: the club table is unique only on (investor, investee name, kind), so an investor can hold two
    postable rows of ONE listed company — an equity stake and a commitment in it, or two spellings of its name. Both
    used to enter one `R.CompanyStakes`, which refuses "two rows of one company": the investor's WHOLE post was skipped
    (`record_invalid`), every run. Now the investor keeps the first row of each issuer — the largest figure — and the
    other is DROPPED (``share_class_overlap``, a WARNING naming both stake ids and the symbol, never a name), counted
    before the investors are ranked (a duplicate never wins its investor the head of the walk). Mutation-checked by hand:
    the dedupe removed from `_stakes_records`, the post was refused and this turned red."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    big = stake("nvidia", "Intel", investee_us_symbol="INTC", kind="on_13f_note", disclosed_value_usd=5e9,
                as_of="2025-12-26")
    small = (stake("nvidia", "Intel", investee_us_symbol="INTC", kind="commitment", value_basis="committed_up_to",
                   disclosed_value_usd=1e9, as_of="2026-01-15", source_title="NVIDIA 10-Q (quarter ended Apr 27, 2026)")
             if twin == "same_symbol_other_kind" else
             stake("nvidia", "Intel Corporation", investee_us_symbol="INTC", disclosed_value_usd=1e9,
                   as_of="2026-01-15", source_title="NVIDIA 10-Q (quarter ended Apr 27, 2026)"))
    synopsys = stake("nvidia", "Synopsys", investee_us_symbol="SNPS", kind="on_13f_note", disclosed_value_usd=2e9,
                     as_of="2025-12-01", source_title="Synopsys 8-K (Dec 1, 2025)")
    rivian = stake("amazon", "Rivian Automotive", investee_us_symbol="RIVN", disclosed_value_usd=1e9,
                   as_of="2026-04-23", source_title="Amazon 10-Q (quarter ended Jun 30, 2026)")
    club, sb, fmp = stakes_world([big, small, synopsys, rivian])
    got = await srun(club, sb, fmp)
    # NVIDIA still leads (two public stakes after the dedupe, against Amazon's one) and posts the LARGER Intel stake
    assert _posts(got)[0] == ["Intel", "Synopsys"] and _posts(got)[1] == ["Rivian Automotive"]
    assert got.records[0].stakes[0].stake_id == big["id"]
    assert got.rejections["share_class_overlap"] == 1 and "record_invalid" not in got.rejections
    assert f"stake {small['id']} in INTC DROPPED as share_class_overlap" in caplog.text
    assert big["id"] in caplog.text and "Intel Corporation" not in caplog.text
    assert R.stake_ref_ids(R.ledger_key(got.records[0])) == [big["id"], synopsys["id"]]


@pytest.mark.asyncio
async def test_posted_stakes_are_dropped_before_any_profile_call(club_on):
    """news-v23: a posted key names every stake its post held (`R.stake_ref_ids`); each one is `already_posted` — and
    so is the stake of a one-stake key of a post before news-v23. A malformed key excludes nothing."""
    club, sb, fmp = stakes_world()
    first = await srun(club, sb, fmp)
    keys = {R.ledger_key(r) for r in first.records}
    assert any("+" in k for k in keys)                                     # a several-stake key among them
    A.clear_memo()
    club, sb, fmp = stakes_world()
    got = await srun(club, sb, fmp, exclude=keys)
    assert got.records == () and got.skip_reason == "stakes_none_public"   # only the private stakes are left
    assert got.rejections["already_posted"] == 3 and fmp.calls == Counter()
    # the one-stake keys of posts before news-v23 read the same
    A.clear_memo()
    club, sb, fmp = stakes_world()
    old_keys = {f"news:company_stakes:{s.stake_id}" for s in _stake_rows(first)}
    got = await srun(club, sb, fmp, exclude=old_keys)
    assert got.records == () and got.rejections["already_posted"] == 3
    # a malformed key excludes nothing
    A.clear_memo()
    club, sb, fmp = stakes_world()
    bad = {k + "+" for k in keys} | {k.upper() for k in keys}
    got = await srun(club, sb, fmp, exclude=bad)
    assert _stake_names(got) == _stake_names(first) and "already_posted" not in got.rejections
    # every stake posted: none unposted
    A.clear_memo()
    rows = [r for r in stakes_rows() if r["investee_name"] in ("Intel", "Synopsys", "Rivian Automotive")]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp, exclude=keys)
    assert got.records == () and got.skip_reason == "stakes_none_unposted" and fmp.calls == Counter()


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
    (rec,) = _stake_rows(await srun(club, sb, fmp))
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


# ── the stake finder's rows (migration 205, plan v3 code change 9, 2026-10-28) ──────────────────────────────────────
#
# The Trillion Club stake finder writes stake rows itself: ``origin`` 'auto' (published with no tap — the owner gets a
# Withdraw / Keep note) or 'proposal' (published by the owner's Publish tap). A PUBLIC post is built only from a row a
# person reviewed: a seed or proposal row, or an auto row the owner KEPT (``owner_kept_at``). The read filters on it in
# PostgREST, and `_postable_stake_rows` re-checks every row it returned, strictly — after the row-cap check (a full page
# may be cut, whatever the check drops) and before the catalogue's known words are built (a left-out row's investee
# name never vouches for another row's source title). ``sb.honour_or = False`` plays a database answer WITHOUT the
# filter, so the strict re-check alone is what a test sees.

#: A Keep tap's stamp as PostgREST writes a TIMESTAMPTZ (Postgres trims the fraction's trailing zeros).
_KEPT_AT = "2026-12-02T15:04:05.12+00:00"
#: Stands for a key the row does not have at all.
_ABSENT = object()


def auto_stake(slug, name, **over):
    """A stake the finder published on its own, shaped as migration 205 lets one be while the owner has NOT kept it:
    a member's own plain-share 13D/13G (`us_listed_off_13f` with its symbol, the cover's percentage and basis, an SEC
    archive source, no figure, no background, not material, sort order 900). ``over`` may give it what a KEPT row may
    carry (``owner_kept_at``, a figure the owner added in Studio)."""
    row = stake(slug, name, kind="us_listed_off_13f", investee_us_symbol="QRVN", ownership_pct=7.2,
                ownership_basis="of Class A", disclosed_value_usd=None, value_basis=None, material=False,
                background=None, sort_order=900, as_of=over.pop("as_of", "2026-11-20"), verified_on="2026-12-01",
                source_title="NVIDIA Schedule 13G (Nov 24, 2026)",
                source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/xslSCHEDULE_13G_X01/"
                           "primary_doc.xml",
                origin="auto", owner_kept_at=None, finding_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"finding/{name}")),
                investee_cik="0001234567", withdrawn_at=None)
    row.update(over)
    return row


def test_the_stakes_read_selects_the_finders_two_columns_and_mirrors_the_contracts_origins():
    """Migration 205's two columns are selected, each once, and the origin vocabulary is `stake_contracts`' own: every
    origin is decided (seed and proposal posted, auto only once kept) — an origin added there fails here until this read
    decides it (the adapter may not import that module). The PostgREST filter is the plan's literal, built from them."""
    from app.services.trillion_club import stake_contracts as SC

    cols = A._STAKE_COLUMNS.split(",")
    assert len(cols) == len(set(cols)) and all(c and c == c.strip() for c in cols)
    assert {"origin", "owner_kept_at"} <= set(cols)
    assert set(A._STAKE_ORIGINS_POSTABLE) == {SC.ORIGIN_SEED, SC.ORIGIN_PROPOSAL}
    assert A._STAKE_ORIGIN_AUTO == SC.ORIGIN_AUTO
    assert {*A._STAKE_ORIGINS_POSTABLE, A._STAKE_ORIGIN_AUTO} == set(SC.ORIGINS)
    assert set(SC.MACHINE_ORIGINS) == {SC.ORIGIN_PROPOSAL, A._STAKE_ORIGIN_AUTO}
    assert A._STAKE_POSTABLE_FILTER == "origin.in.(seed,proposal),owner_kept_at.not.is.null"
    assert A._STAKE_POSTABLE_FILTER == f"origin.in.({','.join(A._STAKE_ORIGINS_POSTABLE)}),owner_kept_at.not.is.null"


_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_ALTER_STAKES_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?trillion_club_stakes\b(.*)", re.I | re.S)
_ADD_COLUMN_RE = re.compile(r"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([a-z_][a-z0-9_]*)\"?", re.I)


def test_every_column_the_stakes_read_selects_is_in_the_schema():
    """The read names its columns: one the schema lacks fails the WHOLE read in production (``club_unavailable`` —
    the series stops), so each must be in the live schema dump's `trillion_club_stakes` or added to it by a migration
    on disk (205's ``origin`` / ``owner_kept_at``, applied by the owner BEFORE the deploy)."""
    root = Path(__file__).resolve().parents[1] / "database"
    snapshot = (root / "schema_snapshot.sql").read_text(encoding="utf-8")
    block = re.search(r"CREATE TABLE public\.trillion_club_stakes \((.*?)\n\);", snapshot, re.S)
    assert block, "the schema dump lost trillion_club_stakes"
    have = {m.group(1) for m in re.finditer(r"^\s{4}([a-z_][a-z0-9_]*) ", block.group(1), re.M)}
    assert {"id", "investee_name", "source_url"} <= have                                    # anti-vacuity
    for path in sorted((root / "migrations").glob("[0-9][0-9][0-9]_*.sql")):
        for statement in _SQL_COMMENT_RE.sub(" ", path.read_text(encoding="utf-8")).split(";"):
            alter = _ALTER_STAKES_RE.search(statement)
            if alter:
                have |= {m.group(1).lower() for m in _ADD_COLUMN_RE.finditer(alter.group(1))}
    missing = [c for c in A._STAKE_COLUMNS.split(",") if c not in have]
    assert missing == [], f"selected but in no schema dump or migration (migration 205?): {missing}"


@pytest.mark.asyncio
async def test_a_database_without_migration_205_makes_the_series_unavailable_never_a_post(club_on):
    """Deployed before 205 is applied (the owner's order is migration first): PostgREST refuses the select naming
    ``origin`` (42703). The series is unavailable — never a stake posted from rows whose origin could not be read —
    and the detail names the column, so the log alone says what is wrong."""
    from postgrest.exceptions import APIError

    missing = APIError({"code": "42703", "message": "column trillion_club_stakes.origin does not exist",
                        "details": None, "hint": None})
    club, sb, fmp = stakes_world(trillion_club_stakes=missing)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await srun(club, sb, fmp)
    assert err.value.reason == "club_unavailable"
    assert "stage=club_stakes" in err.value.detail and "trillion_club_stakes.origin does not exist" in err.value.detail
    assert fmp.calls == Counter()


@pytest.mark.asyncio
async def test_the_stakes_read_is_the_plans_postgrest_query_on_the_real_client(club_on, monkeypatch):
    """The read as the REAL postgrest-py builder sends it — `sb_exec` answers from the fakes' tables, so nothing leaves
    the process: the plan's ``or`` parameter beside the other filters, and the explicit column list. The database
    itself never returns an un-kept auto row."""
    import postgrest

    club, fake, fmp = stakes_world([stake("nvidia", "Nscale", as_of="2026-06-30")])
    sent: List[Any] = []

    async def answer(query):
        sent.append(query)
        return SimpleNamespace(data=[dict(r) for r in fake.tables[query.path.rsplit("/", 1)[-1]]])

    monkeypatch.setattr(A, "sb_exec", answer)
    with postgrest.SyncPostgrestClient("http://127.0.0.1:9/rest/v1") as client:
        got = await srun(club, client, fmp)
    assert _stake_names(got) == ["Nscale"]                           # anti-vacuity
    (read,) = [q for q in sent if q.path == "/trillion_club_stakes"]
    assert read.http_method == "GET"
    assert sorted(read.params.multi_items()) == sorted([
        ("select", A._STAKE_COLUMNS), ("company_slug", "in.(amazon,nvidia,samsung,tsmc)"),
        ("published", "eq.True"), ("source_confidence", "eq.primary"),
        ("or", "(origin.in.(seed,proposal),owner_kept_at.not.is.null)"), ("limit", str(A.STAKES_READ_LIMIT))])


@pytest.mark.parametrize("honour_or", [True, False], ids=["postgrest_filter", "strict_recheck"])
@pytest.mark.parametrize("over, offered", [
    pytest.param({"origin": "seed"}, True, id="seed"),
    pytest.param({"origin": "proposal"}, True, id="proposal"),                        # the owner's Publish tap
    pytest.param({"origin": "auto", "owner_kept_at": _KEPT_AT}, True, id="auto_kept"),  # the owner's Keep tap
    pytest.param({"origin": "auto", "owner_kept_at": "2026-12-02T15:04:05+00:00"}, True, id="auto_kept_whole_second"),
    pytest.param({"origin": "auto"}, False, id="auto_not_kept"),           # published by the finder, never kept
    pytest.param({"origin": "auto", "owner_kept_at": ""}, False, id="auto_stamp_empty"),
    pytest.param({"origin": "auto", "owner_kept_at": "false"}, False, id="auto_stamp_false_text"),
    # a TIMESTAMPTZ can hold 'infinity'; it is no Keep tap
    pytest.param({"origin": "auto", "owner_kept_at": "infinity"}, False, id="auto_stamp_infinity"),
    pytest.param({"origin": "auto", "owner_kept_at": 0}, False, id="auto_stamp_zero"),
    pytest.param({"origin": "auto", "owner_kept_at": True}, False, id="auto_stamp_true"),
    pytest.param({"origin": "Auto", "owner_kept_at": _KEPT_AT}, False, id="Auto_kept"),
    pytest.param({"origin": "Seed"}, False, id="Seed"),
    pytest.param({"origin": " seed"}, False, id="space_seed"),
    pytest.param({"origin": "proposal "}, False, id="proposal_space"),
    pytest.param({"origin": None}, False, id="origin_null"),
    # a stamp never stands in for an origin
    pytest.param({"origin": None, "owner_kept_at": _KEPT_AT}, False, id="origin_null_kept"),
    pytest.param({"origin": _ABSENT}, False, id="origin_absent"),
    # a value a later migration may add
    pytest.param({"origin": "announced", "owner_kept_at": _KEPT_AT}, False, id="origin_unknown_kept"),
    pytest.param({"origin": ["seed"]}, False, id="origin_list"),           # unhashable: refused, never a TypeError
    pytest.param({"origin": {"seed": True}}, False, id="origin_dict"),
    pytest.param({"origin": 1}, False, id="origin_int"),
])
@pytest.mark.asyncio
async def test_a_stake_reaches_a_post_only_when_a_person_reviewed_it(club_on, over, offered, honour_or):
    """The same otherwise-publishable stake under every origin / Keep-stamp shape: offered only when hand-kept, published
    by the owner's tap, or found automatically AND kept — with the database filter applied (as the fake reads it) and
    without it (the strict re-check alone). A left-out row is no candidate: no rejection code, no profile call."""
    row = stake("nvidia", "Quorvane Robotics", as_of="2026-11-20",
                **{k: v for k, v in over.items() if v is not _ABSENT})
    for k in [k for k, v in over.items() if v is _ABSENT]:
        row.pop(k)
    club, sb, fmp = stakes_world([row])
    sb.honour_or = honour_or
    got = await srun(club, sb, fmp)
    if offered:
        assert _stake_names(got) == ["Quorvane Robotics"] and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {} and got.skip_reason == "stakes_none_qualified"
        assert fmp.calls == Counter()


@pytest.mark.parametrize("row, why", [
    pytest.param({"origin": "seed"}, None, id="seed"),
    pytest.param({"origin": "seed", "owner_kept_at": _KEPT_AT}, None, id="seed_stamped"),   # the CHECK forbids it
    pytest.param({"origin": "proposal"}, None, id="proposal"),
    pytest.param({"origin": "auto", "owner_kept_at": _KEPT_AT}, None, id="auto_kept"),
    pytest.param({"origin": "auto", "owner_kept_at": datetime(2026, 12, 2, 15, 4, 5)}, None, id="auto_kept_datetime"),
    pytest.param({"origin": "auto"}, "auto_not_kept", id="auto_no_stamp_key"),
    pytest.param({"origin": "auto", "owner_kept_at": None}, "auto_not_kept", id="auto_stamp_null"),
    pytest.param({"origin": "auto", "owner_kept_at": "2026-13-02T15:04:05+00:00"}, "auto_not_kept",
                 id="auto_stamp_month_13"),
    pytest.param({"origin": "auto", "owner_kept_at": "  "}, "auto_not_kept", id="auto_stamp_blank"),
    pytest.param({"origin": "auto", "owner_kept_at": ["2026-12-02"]}, "auto_not_kept", id="auto_stamp_list"),
    pytest.param({}, "origin_unknown", id="no_origin_key"),
    pytest.param({"origin": ""}, "origin_unknown", id="origin_empty"),
    pytest.param({"origin": "AUTO", "owner_kept_at": _KEPT_AT}, "origin_unknown", id="AUTO_kept"),
    pytest.param({"origin": b"seed"}, "origin_unknown", id="origin_bytes"),
    pytest.param({"origin": ("seed",)}, "origin_unknown", id="origin_tuple"),
    pytest.param({"origin": {"seed"}}, "origin_unknown", id="origin_set"),
])
def test_why_a_stake_row_is_left_out(row, why):
    assert A._stake_left_out(row) == why


def test_the_database_filter_and_the_strict_recheck_agree_on_every_state_the_schema_allows():
    """Over every row migration 205 lets exist — each origin, with and without a Keep stamp where its CHECK allows one
    (``owner_kept_at`` only on an auto row) — the PostgREST filter (as the fake evaluates it) and `_stake_left_out`
    decide the same: neither layer is looser than the other on real data. The fake refuses a filter it cannot read."""
    from app.services.trillion_club import stake_contracts as SC

    states = [{"origin": o, "owner_kept_at": None} for o in SC.ORIGINS]
    states.append({"origin": SC.ORIGIN_AUTO, "owner_kept_at": _KEPT_AT})
    for row in states:
        assert postgrest_or_match(row, A._STAKE_POSTABLE_FILTER) == (A._stake_left_out(row) is None), row
    assert {(r["origin"], r["owner_kept_at"] is not None): postgrest_or_match(r, A._STAKE_POSTABLE_FILTER)
            for r in states} == {("seed", False): True, ("proposal", False): True, ("auto", False): False,
                                 ("auto", True): True}                                  # anti-vacuity
    for bad in ("origin.eq.seed", "origin.in.(seed", "origin.in.(\"seed,x\")", "origin", "origin.not",
                "origin.is.maybe", "origin.in.seed"):
        with pytest.raises(ValueError):
            postgrest_or_match({"origin": "seed"}, bad)


@pytest.mark.asyncio
async def test_a_phase_one_auto_row_is_left_out_before_any_gate_counts_it(club_on, caplog):
    """The finder's own row as phase 1 writes it (no figure) is not a candidate at all while un-kept — never counted
    ``stake_no_figure`` — and its name never reaches a log line; one INFO line counts it. Kept, it is a candidate like
    any other, and its missing figure is what refuses it."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    rows = [auto_stake("nvidia", "Quorvane Robotics"), stake("nvidia", "Nscale", as_of="2026-06-30")]
    club, sb, fmp = stakes_world(rows)
    sb.honour_or = False
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"] and got.rejections == {}
    (line,) = [r for r in caplog.records if "left out" in r.getMessage()]
    assert line.levelno == logging.INFO and "1 of 2 stake row(s) left out" in line.getMessage()
    assert "found automatically by the stake finder and not kept by the owner" in line.getMessage()
    assert not any(w in caplog.text for w in ("Quorvane", "QRVN", "Schedule 13G"))
    A.clear_memo()
    caplog.clear()
    club, sb, fmp = stakes_world([{**rows[0], "owner_kept_at": _KEPT_AT}, rows[1]])
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"] and got.rejections == {"stake_no_figure": 1}
    assert "left out" not in caplog.text


@pytest.mark.parametrize("honour_or", [False, True], ids=["strict_recheck", "postgrest_filter"])
@pytest.mark.asyncio
async def test_a_left_out_auto_rows_investee_name_never_vouches_for_another_rows_source_title(club_on, honour_or):
    """The positive word list (ST-1-R) is built from the catalogue's investee names. A left-out row must not lend its
    name to it: the finder took "Quorvane Robotics" from an SEC filing no person has read, and a seed row whose title
    names "Quorvane" would otherwise compose. So that title is refused (``stake_source_unverified``); once the owner
    keeps the auto row, its name is a reviewed company's and the same title passes. Mutation-checked by hand
    (2026-10-28): `_postable_stake_rows` moved after `R.stake_known_words` turned the ``strict_recheck`` leg red (the
    seed row became a record); the ``postgrest_filter`` leg stays green — the database never returns the row."""
    seed = stake("nvidia", "Nscale", as_of="2026-06-30", source_title="NVIDIA and Quorvane release (Sep 8, 2026)")
    finder = auto_stake("nvidia", "Quorvane Robotics")
    club, sb, fmp = stakes_world([seed, finder])
    sb.honour_or = honour_or
    got = await srun(club, sb, fmp)
    assert got.records == () and got.rejections == {"stake_source_unverified": 1}
    A.clear_memo()
    club, sb, fmp = stakes_world([seed, {**finder, "owner_kept_at": _KEPT_AT}])
    sb.honour_or = honour_or
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"] and got.rejections == {"stake_no_figure": 1}


@pytest.mark.asyncio
async def test_a_full_stakes_page_is_truncated_whatever_the_origin_check_leaves_out(club_on):
    """The row cap counts the page the read RETURNED, before `_postable_stake_rows`: 2,000 rows — all but one an
    un-kept auto row the strict check drops — may still be a cut page, so the series is unavailable, never one stake.
    Mutation-checked by hand (2026-10-28): the check moved before the cap turned this red (one record came back). One
    row fewer is a complete page: the auto rows are left out and the seed row is the one stake."""
    rows = [auto_stake("nvidia", f"Finder Co {i}") for i in range(A.STAKES_READ_LIMIT - 1)]
    rows.append(stake("nvidia", "Nscale", as_of="2026-06-30"))
    club, sb, fmp = stakes_world(rows)
    sb.honour_or = False
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await srun(club, sb, fmp)
    assert err.value.reason == "stakes_truncated"
    A.clear_memo()
    club, sb, fmp = stakes_world(rows[1:])
    sb.honour_or = False
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"] and got.rejections == {}


@pytest.mark.asyncio
async def test_left_out_rows_are_counted_one_line_per_kind_and_never_named(club_on, caplog):
    """Un-kept auto rows: one INFO line with the count. Rows of an origin the read does not know (a value a later
    migration adds, a NULL, a missing key): one WARNING naming the VALUES — a decision is owed in `_stake_left_out` —
    and the count. No line names an investee, a title or a symbol. With the database filter applied, an un-kept auto
    row never reaches the adapter (no line at all for it)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    rows = [auto_stake("nvidia", "Quorvane Robotics"), auto_stake("nvidia", "Zelmark Analytics", as_of="2026-11-21"),
            stake("nvidia", "Pelloran Systems", origin="announced", owner_kept_at=_KEPT_AT),
            stake("nvidia", "Varnex Labs", origin=None),
            stake("nvidia", "Nscale", as_of="2026-06-30")]
    club, sb, fmp = stakes_world(rows)
    sb.honour_or = False
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"] and got.rejections == {}
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if "left out" in r.getMessage()]
    assert [lvl for lvl, _ in lines] == [logging.INFO, logging.WARNING]
    assert "2 of 5 stake row(s) left out — found automatically" in lines[0][1]
    assert "2 of 5 stake row(s) left out — an origin the marketing read does not know ('announced' ×1, None ×1;" \
        in lines[1][1]
    assert not any(w in caplog.text for w in ("Quorvane", "Zelmark", "Pelloran", "Varnex", "QRVN", "Schedule 13G"))
    A.clear_memo()
    caplog.clear()
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert _stake_names(got) == ["Nscale"]
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if "left out" in r.getMessage()]
    # only the kept row of an unknown origin passes the database filter's second branch; the re-check still drops it
    assert lines == [(logging.WARNING, lines[0][1])] and "1 of 2 stake row(s)" in lines[0][1]
    assert "('announced' ×1;" in lines[0][1]


@pytest.mark.parametrize("title", ["Alexandr Wang memo (Jun 12, 2025)", "Jensen keynote (Mar 18, 2026)",
                                   "Elon post on X (Mar 2, 2026)", "Mensch interview", "Intrator letter",
                                   "Volozh interview", "Wang memo"])
@pytest.mark.asyncio
async def test_a_stake_source_naming_someone_off_every_list_is_refused(club_on, caplog, title):
    """The re-review of 2026-10-19 (ST-1-R): the review's probes passed the surname denylist and composed with the
    name drawn on the image, card 4 and every caption. The source title is now held to a POSITIVE list — the club's
    and the catalogue's company names (NVIDIA, Amazon, Samsung, TSMC and every investee here), document, date and
    place words, codes — and the stake is refused (`stake_source_unverified`) before any profile. The words never
    reach a log line. Mutation-checked by hand (2026-10-20): the adapter check removed, every title here was a
    record (red)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    club, sb, fmp = stakes_world([stake("nvidia", "Nscale", source_title=title, as_of="2026-03-27")])
    got = await srun(club, sb, fmp)
    assert got.records == () and got.rejections == {"stake_source_unverified": 1} and fmp.calls == Counter()
    assert "stake_source_unverified" in caplog.text
    assert not any(w in caplog.text for w in ("Alexandr", "Wang", "Jensen", "Elon", "Mensch", "Intrator", "Volozh"))


@pytest.mark.asyncio
async def test_a_stake_source_naming_catalogue_companies_passes_and_an_unknown_local_listing_is_dropped(club_on):
    """Another catalogue company in the title passes (OpenAI is an investee of the catalogue, NVIDIA a club member);
    a local listing with a word on no list is DROPPED (news-v23: a public stake's local listing is never drawn either
    way) — never a reason to refuse the stake."""
    rows = [stake("nvidia", "Nscale", source_title="NVIDIA and OpenAI release (Sep 8, 2026)", as_of="2026-03-27",
                  local_listing="Masayoshi Exchange"),
            stake("amazon", "OpenAI", source_title="Amazon 10-Q (quarter ended Jun 30, 2026)", as_of="2026-07-31",
                  local_listing="Japan")]
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert [(r.investee_name, r.local_listing) for r in _stake_rows(got)] == [("OpenAI", "Japan"), ("Nscale", None)]
    assert got.rejections == {}


@pytest.mark.asyncio
async def test_stake_boundaries_that_pass(club_on):
    rows = [stake("nvidia", "Edge One", verified_on="2026-08-31"),          # exactly 120 days
            stake("nvidia", "Edge Two", as_of="2023-12-30"),                 # exactly 3 × 365 days
            stake("nvidia", "Edge Three", disclosed_value_usd=1e12)]         # exactly the value ceiling
    club, sb, fmp = stakes_world(rows)
    got = await srun(club, sb, fmp)
    assert sorted(_stake_names(got)) == ["Edge One", "Edge Three", "Edge Two"] and len(got.records) == 1


# ── RR3-STAKES-TEMPLATE-WALL (the round-3 review of 2026-10-20): a stake the TEMPLATE refuses never walls the walk ──
#
# The read passed stakes their template always refuses — a source title of 81-120 characters or holding a word over
# 20, an investee-name word of 16-20 characters, a local listing of 33-40 characters, "committed up to" on a kind other
# than a commitment, a `verified_on` after the run — and no template refusal of a stake was remembered: five such
# stakes at the head of the walk were offered and refused every day while every publishable stake behind them waited.
# The read now refuses each of them (a local listing is dropped), and every refusal only the template can make is
# reported back by the script flow and remembered (`A.remember_template_refusal`). news-v23 (one investor's stakes a
# post): the script flow DROPS a refused stake from its post (`NT.drop_refused_rows`, each one reported), a post left
# with none is refused whole (each stake reported), and a remembered stake never wins its investor the head of the walk
# (`A._stakes_records` ranks investors by their UNREFUSED stakes).

_WALL_TITLE = "NVIDIA Quarterly Report on Form 10-Q for the fiscal quarter ended Oct 26, 2026, Notes 8 and 9"
_LONG_WORD_NAMES = ["Microelectronics Alpha", "Nanotechnologies Beta", "Immunotherapeutics Co",
                    "Biopharmaceutical Gamma", "Telecommunications Delta"]
_FIVE_NAMES = ["Nscale", "Ayar Labs", "Lambda", "Crusoe", "Figure"]
#: Behind the five: an older stake the template composes (Amazon's stake in a public company).
_OPENAI_ROW = dict(disclosed_value_usd=50e9, as_of="2026-07-31", source_title="Amazon 10-Q (quarter ended Jun 30, 2026)")
#: Exact-length texts at the template's bounds — lower-case filler after known words, so the positive list
#: (ST-1-R) passes them: an 80-character title whose longest word is 20; a 32-character local listing.
_TITLE_80 = "NVIDIA 10-Q " + "a" * 20 + " " + "b" * 20 + " " + "c" * 20 + " " + "d" * 5
_LOCAL_32 = "Taiwan abcdefghij abcdefghijklmn"


def _would_be_stake(row: Dict[str, Any]) -> R.CompanyStake:
    """The stake ROW the read would build from a row of `stakes_world` (NVIDIA's, its listing verified)."""
    sym = row.get("investee_us_symbol")
    return R.CompanyStake(
        series="company_stakes", stake_id=row["id"], investor=R.CompanyRef("NVDA", "NVIDIA"),
        investee_name=row["investee_name"],
        investee=R.CompanyRef(sym, row["investee_name"]) if isinstance(sym, str) and R.canonical_symbol(sym) == sym
        else None, kind=row["kind"], value_usd=row["disclosed_value_usd"],
        value_basis=row["value_basis"], ownership_pct=None, as_of=date.fromisoformat(row["as_of"]),
        verified_on=date.fromisoformat(row["verified_on"]), source_title=row["source_title"], background=None,
        listed_since=None, local_listing=row["local_listing"], is_new=False)


def _would_be_post(*rows: Dict[str, Any]) -> Any:
    """The one-investor post of `rows` (`_would_be_stake` each) — WITHOUT the record's own validation when a row could
    not stand in one (the template's backstop is what is under test)."""
    stakes_ = tuple(sorted((_would_be_stake(r) for r in rows), key=R.stake_rank_key))
    try:
        return R.CompanyStakes(series="company_stakes", investor=R.CompanyRef("NVDA", "NVIDIA"), stakes=stakes_)
    except ValueError:
        obj = object.__new__(R.CompanyStakes)
        for name, value in (("series", "company_stakes"), ("investor", R.CompanyRef("NVDA", "NVIDIA")),
                            ("stakes", stakes_)):
            object.__setattr__(obj, name, value)
        return obj


def _forget_reads() -> None:
    """Drop every memo entry but the stakes' template refusals (the next call reads the rows afresh)."""
    for key in [k for k in A._MEMO if k[0] != "stake_template_refused"]:
        A._MEMO.pop(key, None)


async def _stake_days(rows, days, posted, *, report=True):
    """Each day as the script flow builds it (`_build_template_day` → `_compose_candidate`): the walk, then each record
    in order — the stakes its template refuses DROPPED (`NT.drop_refused_rows`, each reported back with ``report``), the
    rest composed; the first that composes is posted (its ledger key added to ``posted``); a record refused whole is
    reported back stake by stake (`A.remember_template_refusal`), as `_report_template_refusal` does. Returns each day's
    (each record's stakes by investee name, the template's refusal codes, the posted stakes' names or None, the
    rejections)."""
    out = []
    for day in days:
        _forget_reads()
        club, sb, fmp = stakes_world(rows)
        got = await srun(club, sb, fmp, run_date=day, exclude=frozenset(posted))
        chosen, refusals = None, []
        for rec in got.records:
            try:
                narrowed = NT.drop_refused_rows(rec, day)
                if report:
                    kept = {s.stake_id for s in narrowed.stakes}
                    for st in rec.stakes:
                        if st.stake_id not in kept:
                            assert A.remember_template_refusal(st, ("template_row_refused",), run_date=day) is True
                NT.compose(narrowed, run_date=day, store_state="live", allow_x_url=False)
            except NT.NewsTemplateRefused as e:
                refusals.append(e.code)
                if report:
                    assert A.remember_template_refusal(rec, (e.code,), run_date=day) is True
                continue
            chosen = narrowed
            break
        if chosen is not None:
            posted.add(R.ledger_key(chosen))
        out.append((_posts(got), refusals, [s.investee_name for s in chosen.stakes] if chosen is not None else None,
                    dict(got.rejections)))
    return out


#: Five stakes, NEWER than the Amazon stake, each refused by the template for one gate the read now applies too.
_READ_WALLS = {
    # the review's two probes: a title shared by a batch of catalogue rows; investee names with a 16-18 letter word
    "source_title_too_long": [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}", source_title=_WALL_TITLE)
                              for i, n in enumerate(_FIVE_NAMES)],
    "investee_name_word": [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}") for i, n in enumerate(_LONG_WORD_NAMES)],
    "source_title_word": [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}",
                                source_title="NVIDIA Form 10-Q/A-Exhibit-99.1-Supplement")
                          for i, n in enumerate(_FIVE_NAMES)],
    "committed_up_to_not_a_commitment": [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}",
                                               value_basis="committed_up_to") for i, n in enumerate(_FIVE_NAMES)],
    "verified_after_the_run": [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}", verified_on="2026-12-30")
                               for i, n in enumerate(_FIVE_NAMES)],
}


@pytest.mark.parametrize("shape", list(_READ_WALLS))
@pytest.mark.asyncio
async def test_five_stakes_the_template_cannot_compose_are_refused_at_the_read_and_never_wall_the_walk(club_on, shape):
    """The review's walk (STAKES_RUN and the five days after it): five stakes the template refuses head the walk, a
    publishable one (another investor's) behind them. Each is refused at the read (`stake_invalid`, before any profile),
    so the publishable stake is posted on the FIRST day; nothing is offered after it. Every refused row, as the post the
    read would have built, is refused by `NT.compose`: the read refuses only what the template refuses. Mutation-checked
    (2026-10-20, in memory): each of the five read gates removed alone turns its own row red."""
    walled = _READ_WALLS[shape]
    rows = walled + [stake("amazon", "OpenAI", **_OPENAI_ROW)]
    posted: set = set()
    days = [STAKES_RUN + timedelta(days=k) for k in range(6)]
    d1, *rest = await _stake_days(rows, days, posted)
    assert d1 == ([["OpenAI"]], [], ["OpenAI"], {"stake_invalid": 5})
    if shape == "verified_after_the_run":
        # dated after the run only on the first day: from the next, verified on (or before) the run day, they are
        # ordinary stakes — ONE post holds four (news-v23; review 2026-11-20: at most four), the earlier as-of first on
        # an equal figure, and the fifth makes the next day's post
        assert rest[0][2] == _FIVE_NAMES[:4] and rest[0][1] == []
        assert rest[1] == ([_FIVE_NAMES[4:]], [], _FIVE_NAMES[4:], {"already_posted": 5})
        assert all(d == ([], [], None, {"already_posted": 6}) for d in rest[2:]), rest
    else:
        assert all(d == ([], [], None, {"stake_invalid": 5, "already_posted": 1}) for d in rest), rest
    for row in walled:
        with pytest.raises(NT.NewsTemplateRefused):
            NT.compose(_would_be_post(row), run_date=STAKES_RUN, store_state="live", allow_x_url=False)


@pytest.mark.parametrize("over, kept", [
    ({"source_title": _TITLE_80}, True),                                    # 80 characters, words of 20: drawn
    ({"source_title": _TITLE_80 + "e"}, False),                             # 81
    ({"source_title": "NVIDIA 10-Q " + "q" * 20}, True),                    # a word of 20
    ({"source_title": "NVIDIA 10-Q " + "q" * 21}, False),                   # a word of 21
    ({"source_title": "NVIDIA 10-Q (equity-method-investments note)"}, False),   # a hyphenated word of 25
    ({"investee_name": "Microelectronic Labs"}, True),                      # a 15-letter word
    ({"investee_name": "Microelectronics Labs"}, False),                    # 16
    ({"investee_name": "Sherwin-Williams Ventures"}, False),                # one word of 16 (a hyphen joins it)
    ({"kind": "commitment", "value_basis": "committed_up_to"}, True),
    ({"kind": "private", "value_basis": "committed_up_to"}, False),
    ({"kind": "on_13f_note", "value_basis": "committed_up_to"}, False),
    ({"verified_on": "2026-12-29"}, True),                                  # the run day itself
    ({"verified_on": "2026-12-30"}, False),                                 # the club's one day of ET-vs-UTC grace
])
@pytest.mark.asyncio
async def test_the_reads_stake_gates_and_the_templates_agree_at_every_bound(club_on, over, kept):
    """Both directions, at each bound the read copies from the template: a stake the read keeps composes; a stake it
    refuses (`stake_invalid`, before any profile) is one the template refuses as the post the read would have built."""
    row = stake("nvidia", over.get("investee_name", "Nscale"), as_of="2026-06-30",
                **{k: v for k, v in over.items() if k != "investee_name"})
    club, sb, fmp = stakes_world([row])
    got = await srun(club, sb, fmp)
    if kept:
        (rec,) = got.records
        assert got.rejections == {} and len(rec.stakes) == 1
        NT.compose(rec, run_date=STAKES_RUN, store_state="live", allow_x_url=False)
    else:
        assert got.records == () and got.rejections == {"stake_invalid": 1} and fmp.calls == Counter()
        with pytest.raises(NT.NewsTemplateRefused):
            NT.compose(_would_be_post(row), run_date=STAKES_RUN, store_state="live", allow_x_url=False)


@pytest.mark.parametrize("local, kept", [
    (_LOCAL_32, True),                       # 32 characters: the slot rules pass it
    (_LOCAL_32 + "o", False),                # 33
    ("Taiwan " + "a" * 20, True),            # a word of 20
    ("Taiwan " + "a" * 21, False),           # 21
])
@pytest.mark.asyncio
async def test_a_local_listing_the_template_cannot_take_is_dropped_never_the_stake(club_on, local, kept):
    """A local listing is optional (news-v23: a public stake's is never drawn), so one the template cannot take is
    DROPPED — the stake stays and composes. The listing the read drops is one the template refuses."""
    row = stake("nvidia", "Nscale", as_of="2026-06-30", local_listing=local)
    club, sb, fmp = stakes_world([row])
    (rec,) = (await srun(club, sb, fmp)).records
    assert rec.stakes[0].local_listing == (local if kept else None)
    out = NT.compose(rec, run_date=STAKES_RUN, store_state="live", allow_x_url=False)
    assert local not in json.dumps([out["cards"], out["image_spec"], out["opening_card"]])
    if not kept:
        with pytest.raises(NT.NewsTemplateRefused) as err:
            NT.compose(_would_be_post(row), run_date=STAKES_RUN, store_state="live", allow_x_url=False)
        assert err.value.code == "slot_rejected"


@pytest.mark.parametrize("profile_name, kept", [
    ("Microelectronic Corporation", True),       # "Microelectronic": 15 letters
    ("Microelectronics Corporation", False),     # 16: refused (the template's investor-name rule)
])
@pytest.mark.asyncio
async def test_an_investor_whose_name_the_post_cannot_draw_is_refused_after_its_profile(club_on, profile_name, kept):
    """Only its profile names the investor: a display name with a word over `R.STAKE_PAIR_NAME_WORD_MAX_CHARS` refuses
    its stakes (`company_name_unusable`) — a post the template would refuse."""
    club, sb, fmp = stakes_world([stake("nvidia", "Nscale", as_of="2026-06-30")])
    fmp.profiles["NVDA"] = prof("NVDA", profile_name, exchange="NASDAQ")
    got = await srun(club, sb, fmp)
    if kept:
        (rec,) = got.records
        assert rec.investor.name == "Microelectronic"
        NT.compose(rec, run_date=STAKES_RUN, store_state="live", allow_x_url=False)
    else:
        assert got.records == () and got.rejections == {"company_name_unusable": 1}
        st = dataclasses.replace(_would_be_stake(stake("nvidia", "Nscale", as_of="2026-06-30")),
                                 investor=R.CompanyRef("NVDA", "Microelectronics"))
        would_be = R.CompanyStakes(series="company_stakes", investor=st.investor, stakes=(st,))
        with pytest.raises(NT.NewsTemplateRefused) as err:
            NT.compose(would_be, run_date=STAKES_RUN, store_state="live", allow_x_url=False)
        assert err.value.code == "slot_rejected"


#: Refusals only the TEMPLATE makes (its slot rules: "%" and "#" are never drawn) — the read cannot see them.
_TEMPLATE_ONLY_TITLES = {"percent": "NVIDIA 10-Q (a 9% stake)", "hash": "NVIDIA 10-Q Note #9"}


@pytest.mark.parametrize("title", list(_TEMPLATE_ONLY_TITLES))
@pytest.mark.asyncio
async def test_a_stake_its_template_refused_never_wins_its_investor_the_head_of_the_walk(club_on, title):
    """news-v23: five stakes only the template refuses (one investor's, NVIDIA's) and one publishable stake of another
    investor (Amazon's), reported back as the script flow does. Day 1: NVIDIA leads (five public stakes; its post takes
    four — review 2026-11-20) — every one is DROPPED from its post, so the post is refused whole (the first stake's own
    code) and the next investor's is posted the SAME day. From then on
    NVIDIA's stakes are remembered: never offered first, still offered (never left out: the fifth, then the one refused
    longest ago, take their turns) and refused again. Mutation-
    checked by hand (2026-11-20): with `remember_template_refusal`'s stake branch a no-op this test (the memo read) and
    the ranking test below (NVIDIA's refused three led Amazon's fresh stake) turned red."""
    walled = [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}", source_title=_TEMPLATE_ONLY_TITLES[title])
              for i, n in enumerate(_FIVE_NAMES)]
    rows = walled + [stake("amazon", "OpenAI", **_OPENAI_ROW)]
    posted: set = set()
    days = [STAKES_RUN + timedelta(days=k) for k in range(3)]
    d1, d2, d3 = await _stake_days(rows, days, posted)
    # every stake of NVIDIA's post dropped: the post is refused with the first stake's OWN code (`slot_rejected`: "%" /
    # "#" are never drawn), never a bare "too few" — what the trail and the refusal memo should say
    assert d1 == ([_FIVE_NAMES[:4], ["OpenAI"]], ["slot_rejected"], ["OpenAI"], {})
    assert d2 == ([["Nscale", "Ayar Labs", "Lambda", "Figure"]], ["slot_rejected"], None, {"already_posted": 1})
    assert d3 == ([_FIVE_NAMES[:4]], ["slot_rejected"], None, {"already_posted": 1})
    assert posted == {f"news:company_stakes:{rows[-1]['id']}"}
    # each refusal is remembered for its stake id, read on the day it was last refused
    assert A._stake_template_refused_on(_would_be_stake(walled[0])) == days[2]


@pytest.mark.asyncio
async def test_remembered_stakes_never_rank_their_investor_first_and_wait_behind_fresh_ones(club_on):
    """The investor ranking counts only UNREFUSED public stakes: NVIDIA's three stakes the template refused put it behind
    Amazon's one fresh stake. Inside a post past its four (review 2026-11-20; it was eight), the fresh stakes take the
    places first, then the refused ones, the one refused longest ago first — those that wait take turns."""
    rows = [stake("nvidia", n, as_of=f"2026-11-{10 + i:02d}") for i, n in enumerate(["Alpha Co", "Beta Co", "Gamma Co"])]
    for row, day in ((rows[2], STAKES_RUN), (rows[0], STAKES_RUN - timedelta(days=7)),
                     (rows[1], STAKES_RUN - timedelta(days=3))):
        assert A.remember_template_refusal(_would_be_stake(row), ("slot_rejected",), run_date=day) is True
    club, sb, fmp = stakes_world(rows + [stake("amazon", "Delta Co", as_of="2026-01-05")])
    got = await srun(club, sb, fmp)
    assert _posts(got) == [["Delta Co"], ["Alpha Co", "Beta Co", "Gamma Co"]]
    # five NVIDIA stakes, two of them refused: the post's four are the three fresh and the LONGER-refused one
    A.clear_memo()
    fresh = [stake("nvidia", f"Fresh {chr(65 + i)} Labs", as_of=f"2026-10-{10 + i:02d}") for i in range(3)]
    old_ref, new_ref = stake("nvidia", "Held Old Labs", as_of="2026-11-01"), stake("nvidia", "Held New Labs",
                                                                                   as_of="2026-11-02")
    assert A.remember_template_refusal(_would_be_stake(old_ref), ("x",), run_date=STAKES_RUN - timedelta(days=9))
    assert A.remember_template_refusal(_would_be_stake(new_ref), ("x",), run_date=STAKES_RUN - timedelta(days=1))
    club, sb, fmp = stakes_world(fresh + [old_ref, new_ref])
    (rec,) = (await srun(club, sb, fmp)).records
    assert {s.investee_name for s in rec.stakes} == {r["investee_name"] for r in fresh} | {"Held Old Labs"}


@pytest.mark.asyncio
async def test_an_edit_to_a_refused_stakes_row_lifts_its_hold_a_re_verification_does_not(club_on):
    """A remembered refusal holds while the row still says what was refused: re-verifying the source (a new
    `verified_on`) changes nothing the post says and keeps the stake's investor behind; an edit to what the post says (a
    corrected title) lifts it at once (one fresh stake each: the newer stake's investor first)."""
    old = stake("amazon", "Older Co", as_of="2026-10-01")
    new = stake("nvidia", "Newer Co", as_of="2026-11-20", source_title=_TEMPLATE_ONLY_TITLES["percent"])
    assert A.remember_template_refusal(_would_be_stake(new), ("slot_rejected",), run_date=STAKES_RUN) is True
    for row, order in (
            (new, [["Older Co"], ["Newer Co"]]),
            ({**new, "verified_on": "2026-12-28"}, [["Older Co"], ["Newer Co"]]),                 # re-verified: waits
            ({**new, "source_title": "NVIDIA 10-Q (a nine percent stake)"}, [["Newer Co"], ["Older Co"]])):  # fixed
        _forget_reads()
        club, sb, fmp = stakes_world([old, row])
        got = await srun(club, sb, fmp, run_date=STAKES_RUN + timedelta(days=1))
        assert _posts(got) == order


def test_remember_template_refusal_remembers_a_stake_by_its_id_and_its_rows_fingerprint():
    """The hook the script flow calls: a `CompanyStake` (a stake its template dropped from a post) is remembered under
    its stake id with its row's fingerprint and the run day (a datetime is its day); a `CompanyStakes` (a post refused
    whole) stake by stake. `_stake_template_refused_on` answers for the same row, not for an edited one, and ignores a
    malformed entry. A run date that is not a date, or a record that only looks like a stake, writes nothing."""
    row = stake("nvidia", "Nscale", as_of="2026-06-30")
    rec = _would_be_stake(row)
    assert A.remember_template_refusal(rec, ["slot_rejected", "stale_source"], run_date=STAKES_RUN) is True
    assert A._MEMO[A._stake_refused_key(rec.stake_id)][1] == (A._stake_fingerprint(rec), STAKES_RUN)
    assert A._stake_template_refused_on(rec) == STAKES_RUN
    assert A.remember_template_refusal(rec, "slot_rejected", run_date=datetime(2026, 12, 30, 16, 5)) is True
    assert A._stake_template_refused_on(rec) == date(2026, 12, 30)
    assert A._stake_template_refused_on(dataclasses.replace(rec, source_title="NVIDIA 10-Q (edited)")) is None
    # re-verified (verified_on), a new background or ownership share: still the same post — still held
    assert A._stake_template_refused_on(dataclasses.replace(rec, verified_on=date(2026, 12, 28), background="Note.",
                                                            ownership_pct=4.0)) == date(2026, 12, 30)
    A._memo_put(A._stake_refused_key(rec.stake_id), "junk", 60.0)
    assert A._stake_template_refused_on(rec) is None
    A.clear_memo()
    # news-v23: a whole post refused — every stake it held is remembered
    post = _would_be_post(row, stake("nvidia", "Lambda", as_of="2026-06-30"))
    assert A.remember_template_refusal(post, ["too_few_rows"], run_date=STAKES_RUN) is True
    assert all(A._stake_template_refused_on(st) == STAKES_RUN for st in post.stakes) and len(post.stakes) == 2
    A.clear_memo()
    look_alike = SimpleNamespace(series="company_stakes", stake_id=rec.stake_id, investee_name="Nscale")
    assert A.remember_template_refusal(look_alike, ["slot_rejected"], run_date=STAKES_RUN) is False
    assert A.remember_template_refusal(rec, ["slot_rejected"], run_date="2026-12-29") is False
    assert A._MEMO == {}


# ── earnings ──────────────────────────────────────────────────────────────────

EARN_RUN = date(2027, 1, 14)             # Thursday, in the Q4 season; days 01-06 .. 01-13
#: news-v23 phase 3: a THURSDAY's window is eight days (`R.earnings_window_days`: Wednesday posts the weekly theme
#: first, and Thursday's window takes every report day Wednesday's used to cover)
EARN_DAYS = [f"2027-01-{d:02d}" for d in range(6, 14)]
#: Batch 1 (2026-10-15): every report carries a revenue pair (none → `revenue_implausible`), so a row that
#: does not name one gets this plausible quarter: $1.05B against a $1.0B estimate.
DEFAULT_RA, DEFAULT_RE = 1.05e9, 1.0e9


def erow(sym, day, a, e, ra=DEFAULT_RA, re_=DEFAULT_RE, **kw):
    """A calendar row. ``ra`` / ``re_`` default to a plausible pair; pass None to leave one out."""
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
        # A 2.0x revenue pair: the WHOLE report is refused (Batch 1), never kept without its revenue.
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
    # Batch 1: the LARGEST company leads whatever its gap (BBB: 10%, the smallest gap kept here).
    p["BBB"] = prof("BBB", "Bbb Industries Inc.", cap=50e9)
    p["JJJ"] = prof("JJJ", "Jjj Trust", isEtf=True)
    p["KKK"] = prof("KKK", "Kkk Industries Inc.", cap=1.9e9)
    p["LLL"] = prof("LLL", "Lll Industries Preferred", cap=5e9)
    return p


async def erun(fmp, **kw):
    with _per_company_bounds(kw.get("limit", 5)):
        return _Rows(await run("earnings", fmp, run_date=kw.pop("run_date", EARN_RUN), **kw))


@pytest.mark.asyncio
async def test_earnings_one_company_per_record_ranked_by_market_cap_then_gap():
    """Batch 1: ranked by SIZE — market cap, then the gap, then the symbol — never by the widest gap; every
    record carries its revenue pair and the quarter the statements place it in."""
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    got = await erun(fmp)
    assert [(r.company.symbol, r.report_date.isoformat(), r.eps_actual, r.eps_estimate) for r in got.records] == [
        ("BBB", "2027-01-11", 0.9, 1.0),          # $50B, a 10% gap
        ("III", "2027-01-07", 2.0, 1.0),          # $9B, 100%
        ("AAA", "2027-01-12", 1.5, 1.0),          # $5B, 50%
    ]
    bbb, _iii, aaa = got.records
    assert (aaa.revenue_actual, aaa.revenue_estimate) == (1.1e9, 1.0e9)
    assert (bbb.revenue_actual, bbb.revenue_estimate) == (DEFAULT_RA, DEFAULT_RE)
    assert all(r.period_end == date(2026, 12, 31) for r in got.records)      # placed by the statements
    # news-v23: the day's ONE record carries every company's own key, in draw (size) order
    assert R.ledger_key(got.record) == "news:earnings:BBB:2027-01-11+III:2027-01-07+AAA:2027-01-12"
    assert got.rejections == {"eps_estimate_too_small": 1, "eps_digit_shift": 1, "eps_gap_implausible": 1,
                              "symbol_grammar": 2, "warrant_unit_right": 1, "etf_or_fund": 1, "below_cap_floor": 1,
                              "non_common_listing": 1, "revenue_implausible": 1}
    # ONE ET day per calendar call, the eight days before the (Thursday) run.
    assert sorted(fmp.calendar_calls) == [(d, d) for d in EARN_DAYS] and len(EARN_DAYS) == 8
    assert "OLD" not in fmp.profile_requests[0] and "CCC" not in fmp.profile_requests[0]
    # ONE quarterly-statement read per kept report, four quarters each.
    assert sorted(fmp.statement_calls) == [("AAA", 4), ("BBB", 4), ("III", 4)]


@pytest.mark.parametrize("rows, expected", [
    (3999, "ok"), (4000, "earnings_calendar_truncated"), (4500, "earnings_calendar_truncated"),
])
@pytest.mark.asyncio
async def test_a_day_at_the_row_cap_is_truncated_never_ranked(rows, expected):
    cal = earnings_calendar()
    cal["2027-01-11"] = cal["2027-01-11"] + [erow(f"Z{i}", "2027-01-11", None, 1.0) for i in range(rows - 1)]
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    if expected == "ok":
        assert len((await erun(fmp)).records) == 3
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
    assert len((await erun(fmp)).records) == 3
    assert fmp.calls["calendar"] - calls == 8                              # every day read again


@pytest.mark.asyncio
async def test_earnings_days_and_statements_are_memoized_and_concurrent_calls_share_them():
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    a, b = await asyncio.gather(erun(fmp), erun(fmp))
    assert a.records == b.records and fmp.calls["calendar"] == 8 and fmp.calls["statement"] == 3
    await erun(fmp)
    assert fmp.calls["calendar"] == 8 and fmp.calls["statement"] == 3       # ("quarter_statements", sym)
    assert ("quarter_statements", "AAA") in A._MEMO


@pytest.mark.parametrize("second, expected", [
    # The same result on two days: one report, the NEWEST day.
    (erow("AAA", "2027-01-08", 1.5, 1.0, 1.1e9, 1.0e9), ("2027-01-12", (1.1e9, 1.0e9))),
    # Two different results for one company in one week: which is right is a guess → refused.
    (erow("AAA", "2027-01-08", 1.4, 1.0), "record_invalid"),
    # A refused row beside a clean one: the company's data is not trusted.
    (erow("AAA", "2027-01-08", 12.5, 1.0), "eps_gap_implausible"),
    # Batch 1: revenue that disagrees between the rows refuses the WHOLE report (never the EPS alone) …
    (erow("AAA", "2027-01-08", 1.5, 1.0, 1.2e9, 1.0e9), "revenue_implausible"),
    # … and so does a row without revenue beside one with it: which row is the report is a guess.
    (erow("AAA", "2027-01-08", 1.5, 1.0, None, None), "revenue_implausible"),
])
@pytest.mark.asyncio
async def test_two_rows_of_one_company_in_a_week(second, expected):
    cal = earnings_calendar()
    cal["2027-01-08"] = [second]
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    got = await erun(fmp)
    aaa = [r for r in got.records if r.company.symbol == "AAA"]
    if isinstance(expected, str):
        assert aaa == [] and got.rejections.get(expected) == (2 if expected == "revenue_implausible" else 1)
    else:
        (rec,) = aaa
        assert rec.report_date.isoformat() == expected[0]
        assert (rec.revenue_actual, rec.revenue_estimate) == expected[1]


@pytest.mark.parametrize("ra, re_, kept", [
    (1.4e9, 1e9, True), (1.5e9, 1e9, True), (1.51e9, 1e9, False), (0.5e9, 1e9, True), (0.49e9, 1e9, False),
    (None, 1e9, False), (1e9, None, False), (None, None, False), (-1e9, 1e9, False), (0.0, 1e9, False),
    (1e9, 0.0, False), (1e9, -1e9, False), (float("nan"), 1e9, False), (1e9, float("inf"), False),
    (True, 1e9, False), ("1e9", 1e9, False), (10 ** 400, 1e9, False),
], ids=["1.4x", "1.5x_edge", "1.51x", "0.5x_edge", "0.49x", "no_actual", "no_estimate", "none", "negative",
        "zero_actual", "zero_estimate", "negative_estimate", "nan", "inf", "bool", "string", "huge_int"])
@pytest.mark.asyncio
async def test_a_report_without_one_plausible_revenue_pair_is_refused_whole(ra, re_, kept):
    """Batch 1: the revenue pair is the only check that the row is the quarter the estimate is for. Before,
    an out-of-band or missing pair was DROPPED and the EPS kept (Progressive's one-month row); now the whole
    report is refused (`revenue_implausible`) before any profile, and `revenue_dropped` is never counted."""
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0, ra, re_)]}
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles())
    got = await erun(fmp)
    if kept:
        (rec,) = got.records
        assert (rec.revenue_actual, rec.revenue_estimate) == (ra, re_) and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {"revenue_implausible": 1}
        assert fmp.calls["profiles"] == 0 and fmp.statement_calls == []
    assert "revenue_dropped" not in got.rejections


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


@pytest.mark.parametrize("a, e", [(0.105, 0.10), (-0.5, -0.1), (-9.0, 1.0), (0.0, 0.5), (4.9, 1.0)])
@pytest.mark.asyncio
async def test_eps_boundaries_that_pass(a, e):
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]}, profiles=earnings_profiles())
    assert len((await erun(fmp)).records) == 1


@pytest.mark.parametrize("a, e, kept", [
    (1.05, 1.00, True), (1.0499, 1.00, False), (0.95, 1.00, True), (0.9501, 1.00, False),
    (0.21, 0.20, True),                         # 5.0% exactly: the float quotient is 0.04999…, the figures are not
    (0.2099, 0.20, False), (-0.95, -1.00, True), (-0.951, -1.00, False), (-1.05, -1.00, True),
    (1.00, 1.00, False), (2.10, 2.00, True), (2.0999, 2.00, False),
], ids=["5.0", "4.99", "minus_5.0", "minus_4.99", "0.21_vs_0.20", "0.2099", "neg_est_5.0", "neg_est_4.9",
        "neg_est_more_negative", "equal", "2.10", "2.0999"])
@pytest.mark.asyncio
async def test_a_surprise_under_five_percent_is_refused_and_five_point_zero_is_kept(a, e, kept):
    """Batch 1: |actual − estimate| / |estimate| under `R.EARNINGS_CLEAR_SURPRISE` (5%) is no clear surprise —
    refused before any profile. The boundary is exact on the figures as written: 0.21 vs 0.20 is 5.0%."""
    assert R.EARNINGS_CLEAR_SURPRISE == 0.05
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]}, profiles=earnings_profiles())
    got = await erun(fmp)
    if kept:
        assert [r.company.symbol for r in got.records] == ["AAA"] and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {"surprise_unclear": 1} and fmp.calls["profiles"] == 0


@pytest.mark.asyncio
async def test_a_posted_report_is_dropped_before_its_profile():
    """A posted report is never a candidate: counted ``already_posted`` before the gate and never read. Review
    of 2026-10-16 (ER-3): its listing IS profiled now — after every candidate, as a posted listing — because its
    issuer identity (profile CIK, display name) is what ties a sibling listing to the report already posted."""
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    got = await erun(fmp, exclude={"news:earnings:AAA:2027-01-12"})
    assert "AAA" not in [r.company.symbol for r in got.records] and got.rejections["already_posted"] == 1
    requested = fmp.profile_requests[0]
    assert requested[-1] == "AAA" and requested.count("AAA") == 1             # the posted listing, last
    assert "AAA" not in [s for s, _limit in fmp.statement_calls]
    # a key of another earnings day of THIS window counts too (FMP re-dating a row must not post it twice) — and,
    # since the re-review of 2026-10-19 (EME-1), of a day up to `EARNINGS_POSTED_LOOKBACK_DAYS` BEFORE it …
    assert A.EARNINGS_POSTED_LOOKBACK_DAYS == 14
    assert A._posted_in_window(frozenset({"news:earnings:AAA:2027-01-09"}), []) == set()   # no window, no key
    for key in ("news:earnings:AAA:2027-01-09", "news:earnings:AAA:2027-01-06", "news:earnings:AAA:2026-12-23"):
        A.clear_memo()
        fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
        got = await erun(fmp, exclude={key})
        assert "AAA" not in [r.company.symbol for r in got.records] and got.rejections["already_posted"] == 1, key
    # … a key older than that, of a day after the window, another series' key or a malformed one does not
    for key in ("news:earnings:AAA:2026-12-22", "news:earnings:AAA:2027-01-14", "news:ceo_buys:AAA:2027-01-12",
                "news:earnings:AAA", "news:earnings::2027-01-12", "news:earnings:aaa:2027-01-12",
                "news:earnings:AAA:2027-W02-1", "news:earnings:AAA:2027-02-30", "news:earnings:AAA:20270112"):
        A.clear_memo()
        got = await erun(FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles()), exclude={key})
        assert "AAA" in [r.company.symbol for r in got.records] and "already_posted" not in got.rejections, key


@pytest.mark.asyncio
async def test_a_report_re_dated_across_two_windows_is_never_posted_twice():
    """The re-review of 2026-10-19 (EME-1): the Thu 2027-01-07 run (window Dec 31 - Jan 6) posted AAA under
    news:earnings:AAA:2027-01-06; FMP then dates the same report Jan 7, the first day of the NEXT window. The Jan 14
    run held AAA again (no posted key inside its window) and would have posted a week-old report a second time —
    and so would a twin or another class of the same issuer (BRK-A posted under Jan 6, BRK-B dated Jan 7).
    Mutation-checked by hand (2026-10-19): the lookback narrowed back to the window turned all three red."""
    cal = {"2027-01-07": [erow("AAA", "2027-01-07", 1.5, 1.0, 1.1e9, 1.0e9)]}
    got = await erun(FakeFMP2b(calendar=cal, profiles=earnings_profiles()),
                     exclude={"news:earnings:AAA:2027-01-06"})
    assert got.records == () and got.rejections == {"already_posted": 1}
    # a twin family: GOOG posted in the window before, GOOGL (its curated class line) re-dated into this one
    A.clear_memo()
    alphabet = {"GOOG": _listing("GOOG", "Alphabet Inc.", cap=3.0e12, cik="0001652044"),
                "GOOGL": _listing("GOOGL", "Alphabet Inc.", cap=3.0e12, cik="0001652044")}
    got = await erun(FakeFMP2b(calendar={"2027-01-07": [erow("GOOG", "2027-01-07", 2.87, 2.60, 96.4e9, 95.0e9),
                                                        erow("GOOGL", "2027-01-07", 2.87, 2.60, 96.4e9, 95.0e9)]},
                               profiles=alphabet),
                     exclude={"news:earnings:GOOG:2027-01-05"})
    assert got.records == () and got.rejections == {"already_posted": 2}
    # another class of the issuer (its profile CIK): BRK-A posted the window before, BRK-B dated Jan 7
    A.clear_memo()
    brk = {"BRK-A": _listing("BRK-A", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983"),
           "BRK-B": _listing("BRK-B", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983")}
    fmp = FakeFMP2b(calendar={"2027-01-07": [erow("BRK-B", "2027-01-07", 4.67, 4.0, 95e9, 90e9)]}, profiles=brk)
    got = await erun(fmp, exclude={"news:earnings:BRK-A:2027-01-06"})
    assert got.records == () and got.rejections == {"already_posted": 1} and fmp.statement_calls == []
    # the NEXT quarter's report (77+ days on) is never taken for the posted one
    A.clear_memo()
    got = await erun(FakeFMP2b(calendar=cal, profiles=earnings_profiles()),
                     exclude={"news:earnings:AAA:2026-10-21"})
    assert [r.company.symbol for r in got.records] == ["AAA"] and got.rejections == {}


@pytest.mark.asyncio
async def test_earnings_profiles_the_first_sixty_by_revenue_estimate_in_two_batch_calls():
    """Batch 1: the reports are pre-ordered by their revenue ESTIMATE (the size proxy before any profile is
    read) and the first `A.EARNINGS_MAX_PROFILES` are profiled at once — two batch calls of 50; the rest are
    never profiled and never counted as rejections. All profiles missing → the profile source is down."""
    rows = [erow(f"Q{c}{d}", "2027-01-12", 1.5, 1.0, 1.1e9 * (100 - i), 1.0e9 * (100 - i))
            for i, (c, d) in enumerate((c, d) for c in "BCDEFGHJKL" for d in "BCDEFGHJKL")][:70]
    want = [r["symbol"] for r in rows][:60]                              # the 60 largest revenue estimates
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles={})
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await erun(fmp)
    assert err.value.reason == "profiles_unavailable"
    assert [len(b) for b in fmp.profile_requests] == [50, 10] and A.EARNINGS_MAX_PROFILES == 60
    assert [s for b in fmp.profile_requests for s in b] == want
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
        # Batch 1 (2026-10-15): a line labelled by its code ("QTL") folds into the rest
        # (`R.segment_fold_reason`); the spelled-out lines keep QCOM's fact. A breakdown of codes ONLY
        # ("QCT" / "QTL" / "QSI") gives no fact at all (`test_a_company_code_is_never_a_themes_line`).
        "QCOM": breakdown(symbol="QCOM", fiscal_year="2025", reported_revenue=38.9e9,
                          revenue_sources=[src("Handsets", 24.9e9), src("QTL", 5.6e9), src("IoT", 5.4e9),
                                           src("Automotive", 2.9e9)]),
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
    # news-v23 phase 3: the key names the theme, its ANGLE and the run date (never posted: "how they make money")
    assert (rec.angle, rec.posted_on) == ("make_money", THEME_RUN)
    assert R.ledger_key(rec) == "news:theme_explainer:ai-chips:make_money:2026-11-19"
    assert "INTC segment lookup failed" in caplog.text
    # Names and ticker lists only: never the image, subtitle, category or accent.
    (table, cols), = [s for s in world["sb"].selects if s[0] == "trending_themes"]
    assert not {"image_url", "subtitle", "category", "accent_hex"} & set(cols.split(","))
    assert "ARM" not in world["fmp"].profile_requests[0]


@pytest.mark.parametrize("over, reason", [
    ({"tickers_as_of": "2026-09-09"}, "theme_stale"),             # 71 days
    ({"tickers_as_of": "2026-11-20"}, "theme_stale"),             # after the run
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
    # 2026-11-20 fix round: held to the guard it names — "how they make money" only (a worker offering just that
    # angle). With every angle on, this world has no statements and no Congress feed, so the rotation's later angles
    # are OUTAGES and the series rightly reports "unavailable" (never "nothing qualified" over a source that failed).
    got = await trun(theme_world(breakdowns=breakdowns), theme_angles=("make_money",))
    assert got.records == () and got.rejections.get(reason) == 1
    assert got.skip_reason == "theme_none_qualified"


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
async def test_a_large_theme_keeps_every_member_and_looks_up_every_one():
    syms = [f"S{c}{d}" for c in "AB" for d in "ABCDEFGHIJKL"]                  # 24
    profiles = {s: prof(s, f"Company {s}") for s in syms}
    # Batch 1: "Hardware", not a generic "Products" (folded into the rest, so it would never be a fact)
    good = breakdown(fiscal_year="2026", reported_revenue=100e9,
                     revenue_sources=[src("Hardware", 70e9), src("Services", 30e9)])
    world = theme_world([theme(tickers=syms, blocked_tickers=[])], breakdowns={}, profiles=profiles)
    world["revenue"].default = good
    (rec,) = (await trun(world)).records
    assert len(rec.members) == 24
    # phase 4 (2026-11-20): EVERY member is looked up (the owner's "Arm, Intel and Synopsys": Intel is Silicon Rush's
    # 20th member, past the old first 16) — no market value here, so in the theme's own order
    assert A.THEME_SEGMENT_LOOKUPS == R.THEME_MEMBERS[1] == 24 and world["revenue"].calls == syms
    # ... and the record carries the picture's tiles' facts only (A.THEME_FACT_TILES), the rest named
    assert [m.top_segment for m in rec.members] == ["Hardware"] * A.THEME_FACT_TILES + [None] * 12


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
    # phase 4: the members with a segment fact first (no market values here: the theme's order), then the rest — GOOG
    # (no breakdown in this world) is listed once, after them
    assert [m.company.symbol for m in rec.members] == ["NVDA", "AMD", "AVGO", "QCOM", "GOOG", "MRVL", "INTC"]


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
    cal = {"2027-01-12": [erow(sym, "2027-01-12", 2.38, 2.20, 1.8e9, 1.75e9),
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
    assert sorted(s for s, _l in fmp.statement_calls) == sorted([sym, "AAA"])
    assert all(limit == A.EARNINGS_STATEMENT_ROWS == 4 for _s, limit in fmp.statement_calls)   # ONE read each
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
    [{"reportedCurrency": "USD", "revenue": 1.1e9}],                        # no period end: nothing to date it by
    [{"date": "2026-12-31", "reportedCurrency": "USD", "revenue": 1.1e9},   # two codes on the newest period
     {"date": "2026-12-31", "reportedCurrency": "EUR", "revenue": 1.1e9}],
    [{"date": "2026-09-30", "reportedCurrency": "USD"}, {"date": "2026-12-31", "reportedCurrency": "EUR"}],
], ids=["unavailable", "rate_limited", "not_entitled", "empty", "not_a_list", "none", "not_a_row", "no_currency",
        "blank_currency", "numeric_currency", "no_period_end", "two_codes", "the_newest_row_decides"])
@pytest.mark.asyncio
async def test_an_unreadable_or_non_usd_currency_read_refuses_the_report(answer, caplog):
    """Fail closed: a report whose reporting currency cannot be confirmed as USD is refused; the
    week keeps the rest (a per-candidate read, like the Form 4/A check). Review of 2026-10-16 (ER-2): a read
    that FAILED (an exception, or an answer no dated row with one currency could be read from) is counted
    ``statement_unavailable`` — apart from an answered read in another currency (``non_usd_reporter``)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0), erow("BBB", "2027-01-12", 0.9, 1.0)]}
    fmp = FakeFMP2b(calendar=cal, profiles=earnings_profiles(), currency={"AAA": answer})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["BBB"]
    read_failed = not (isinstance(answer, list) and len(answer) == 2 and answer[0]["date"] != answer[1]["date"])
    assert got.rejections == ({"statement_unavailable": 1} if read_failed else {"non_usd_reporter": 1})
    assert ("AAA quarterly statement unreadable — refused as statement_unavailable" if read_failed
            else "AAA reports in EUR") in caplog.text
    # A failed read is never memoized: the next call reads again and keeps AAA. (A read that
    # answered — the newest row says EUR — is memoized like a calendar day.)
    fmp.currency = {}
    expected = ["BBB", "AAA"] if read_failed else ["BBB"]
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
async def test_statement_reads_are_bounded_by_the_record_count():
    """One read per report that reaches the read stage, in waves of the reports still needed —
    never one per profiled candidate."""
    syms, rows, profiles = _qualifying(8)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == syms[:5]
    assert [s for s, _l in fmp.statement_calls] == syms[:5]               # one wave of five, nothing more
    # Two of the first five refused → one more wave of exactly two.
    A.clear_memo()
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles, currency={syms[1]: "CAD", syms[3]: "EUR"})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == [syms[0], syms[2], syms[4], syms[5], syms[6]]
    assert [s for s, _l in fmp.statement_calls] == syms[:7]
    assert got.rejections == {"non_usd_reporter": 2}


@pytest.mark.asyncio
async def test_statement_reads_stop_at_their_cap():
    syms, rows, profiles = _qualifying(30)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles, currency={s: "EUR" for s in syms})
    got = await erun(fmp)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"
    assert len(fmp.statement_calls) == A.EARNINGS_MAX_STATEMENT_READS == 20
    assert got.rejections == {"non_usd_reporter": 20}
    assert [len(b) for b in fmp.profile_requests] == [30]                 # every report profiled at once


@pytest.mark.asyncio
async def test_a_statement_read_out_of_budget_raises():
    clock = {"now": 0.0}

    class Slow(FakeFMP2b):
        async def get_company_profiles_batch(self, symbols):
            clock["now"] = 59.5
            return await super().get_company_profiles_batch(symbols)

    fmp = Slow(calendar={"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}, profiles=earnings_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and "stage=earnings_statement" in err.value.detail
    assert fmp.statement_calls == []


# ── rr9 #6 → Batch 1: every report the size proxy reaches is profiled at once, ranked by market cap ──

def _sub_floor(n, *, revenue=(1.05e9, 1.0e9)):
    """``n`` reports with a +200% gap from $800M companies (below the $2B earnings floor)."""
    syms = [f"S{a}{b}" for a in "BCDEFGHJKLM" for b in "BCDEFGHJKLM"][:n]
    return ([erow(sym, "2027-01-12", 3.0, 1.0, *revenue) for sym in syms],
            {sym: prof(sym, f"{sym} Industries Inc.", cap=8e8) for sym in syms})


@pytest.mark.asyncio
async def test_sub_floor_gaps_never_crowd_out_a_large_cap():
    """rr9 #6 (CONFIRMED probe): 25 sub-$2B companies with larger gaps rank above MSFT. Batch 1: all 26 are
    profiled in one call, the floor drops the 25, and MSFT is the week."""
    rows, profiles = _sub_floor(25)
    profiles["MSFT"] = prof("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=3e12)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + [erow("MSFT", "2027-01-12", 1.16, 1.0)]}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["MSFT"]
    assert got.rejections == {"below_cap_floor": 25}
    assert [len(b) for b in fmp.profile_requests] == [26] and "MSFT" in fmp.profile_requests[0]


@pytest.mark.asyncio
async def test_the_revenue_estimate_reaches_a_large_cap_behind_seventy_small_reports():
    """Batch 1: 70 sub-floor reports with wider gaps would fill the 60-profile cap if the gap ordered the
    profiles; the revenue ESTIMATE does (the size proxy before any profile), so MSFT ($70B estimate) is
    profiled first and leads, and the ten smallest estimates are never profiled."""
    rows, profiles = _sub_floor(70, revenue=(52e6, 50e6))
    profiles["MSFT"] = prof("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=3e12)
    msft = erow("MSFT", "2027-01-12", 1.16, 1.0, 71e9, 70e9)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + [msft]}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["MSFT"]
    assert [len(b) for b in fmp.profile_requests] == [50, 10] and fmp.profile_requests[0][0] == "MSFT"
    assert got.rejections == {"below_cap_floor": 59}                       # 59 profiled; 11 never reached
    # The same week with MSFT's estimate as small as the rest: it ranks 71st by gap and is never profiled.
    A.clear_memo()
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + [erow("MSFT", "2027-01-12", 1.16, 1.0, 52e6, 50e6)]},
                    profiles=profiles)
    got = await erun(fmp)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"
    assert not any("MSFT" in b for b in fmp.profile_requests)
    assert got.rejections == {"below_cap_floor": 60}


@pytest.mark.asyncio
async def test_the_sub_floor_reports_never_cost_a_statement_read():
    syms, rows, profiles = _qualifying(5)
    sub, sub_profiles = _sub_floor(30)
    fmp = FakeFMP2b(calendar={"2027-01-12": rows + sub}, profiles={**profiles, **sub_profiles})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == syms
    assert [s for s, _l in fmp.statement_calls] == syms                     # the gate refused the rest first
    assert got.rejections == {"below_cap_floor": 30}


@pytest.mark.asyncio
async def test_a_larger_company_profiled_in_the_second_batch_call_outranks_the_first():
    """Batch 1: the market cap decides across EVERY profiled report. ZBIG ($500B) has the smallest revenue
    estimate, so it is profiled in the SECOND batch call — and still leads the fifty $3B companies of the
    first, whose gaps are wider."""
    first = [erow(f"R{a}{b}", "2027-01-12", 1.5, 1.0, 5.5e9, 5.0e9)
             for a, b in ((a, b) for a in "BCDEFGHJ" for b in "BCDEFGHJ")][:50]
    second = [erow(f"S{c}", "2027-01-12", 1.2, 1.0, 0.55e9, 0.5e9) for c in "BCDE"]
    second.append(erow("ZBIG", "2027-01-12", 1.06, 1.0, 0.33e9, 0.3e9))
    profiles = {r["symbol"]: prof(r["symbol"], f"{r['symbol']} Industries Inc.", cap=3e9) for r in first + second}
    profiles["ZBIG"] = prof("ZBIG", "Zbig Industries Inc.", cap=500e9)
    fmp = FakeFMP2b(calendar={"2027-01-12": first + second}, profiles=profiles)
    got = await erun(fmp)
    assert [len(b) for b in fmp.profile_requests] == [50, 5] and "ZBIG" in fmp.profile_requests[1]
    assert [r.company.symbol for r in got.records] == ["ZBIG"] + [r["symbol"] for r in first[:4]]
    assert [s for s, _l in fmp.statement_calls][0] == "ZBIG"               # read first: it ranks first


@pytest.mark.asyncio
async def test_out_of_budget_on_a_later_wave_keeps_the_records_held(caplog):
    """A record already held is fully checked (profile, currency, period): running out of budget on a
    LATER read wave ends the walk with it; with none held the series still raises."""
    clock = {"now": 0.0}
    cal = {"2027-01-12": [erow("AAA", "2027-01-12", 5.0, 1.0), erow("BBB", "2027-01-12", 4.0, 1.0),
                          erow("CCC", "2027-01-12", 3.0, 1.0)]}
    profiles = {s: prof(s, f"{s.title()} Industries Inc.", cap=cap) for s, cap in (("AAA", 9e9), ("BBB", 8e9),
                                                                                       ("CCC", 7e9))}

    class Clocked(FakeFMP2b):
        async def get_income_statement(self, sym, period="annual", limit=10):
            if sym == "AAA":
                clock["now"] = 59.5                                         # the budget is spent here
            return await super().get_income_statement(sym, period, limit)

    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = Clocked(calendar=cal, profiles=profiles, currency={"BBB": "EUR"})
    with _per_company_bounds(2):
        got = _Rows(await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), limit=2, deadline=60.0,
                                       deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"])))
    # wave 1 = AAA (kept) + BBB (EUR, refused); wave 2 (CCC) finds the budget spent
    assert [r.company.symbol for r in got.records] == ["AAA"]
    assert [s for s, _l in fmp.statement_calls] == ["AAA", "BBB"] and got.rejections == {"non_usd_reporter": 1}
    assert "out of budget after 1 record(s)" in caplog.text
    # With nothing held, the same exhaustion (spent by the profile call) raises at the first read wave.
    A.clear_memo()
    clock["now"] = 0.0

    class Late(FakeFMP2b):
        async def get_company_profiles_batch(self, symbols):
            got = await super().get_company_profiles_batch(symbols)
            clock["now"] = 59.5
            return got

    fmp = Late(calendar=cal, profiles=profiles)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await A.candidates("earnings", run_date=EARN_RUN, exclude=frozenset(), deadline=60.0,
                           deps=A.NewsDeps(fmp=fmp, monotonic=lambda: clock["now"]))
    assert err.value.reason == "budget_exhausted" and fmp.statement_calls == []


# ── Batch 1 (2026-10-15): the live findings, the period check, kept never counted ──

_PERIOD_RUN = date(2026, 10, 15)          # Thursday; reports Oct 8 .. 14 (the live preview's week)


def _statements(sym, *quarters, currency="USD"):
    """FMP-shaped quarterly rows, newest first: ``quarters`` = ((period end, revenue), …)."""
    return [{"symbol": sym, "date": d, "period": "Q", "reportedCurrency": currency, "revenue": rev}
            for d, rev in quarters]


_DAL = ("DAL", "Delta Air Lines, Inc.")
_DAL_STATEMENTS = _statements("DAL", ("2026-09-30", 20_186_000_000), ("2026-06-30", 16_650_000_000),
                              ("2026-03-31", 15_854_000_000), ("2025-12-31", 15_560_000_000))


@pytest.mark.asyncio
async def test_progressives_one_month_row_is_refused_never_published_as_the_quarter():
    """Live finding (2026-10-14): PGR's calendar row held ONE MONTH — revenue $7.16B against a $22.6B
    QUARTER estimate, EPS $1.08 against $4.35 — and the old code dropped the revenue line and would have
    published "$1.08 a share … the estimate was $4.35". Batch 1 refuses the whole report, before any
    profile. Even a monthly row whose estimate were monthly too (in band) is refused: the reported
    quarter's statement ($23.18B, ended Sep 30) is not the calendar's $7.16B (`period_mismatch`)."""
    pgr = prof("PGR", "The Progressive Corporation", cap=140e9)
    fmp = FakeFMP2b(calendar={"2026-10-14": [erow("PGR", "2026-10-14", 1.08, 4.35, 7.16e9, 22.6e9)]},
                    profiles={"PGR": pgr})
    got = await erun(fmp, run_date=_PERIOD_RUN)
    assert got.records == () and got.rejections == {"revenue_implausible": 1}
    assert fmp.calls["profiles"] == 0 and fmp.statement_calls == []
    A.clear_memo()
    quarterly = _statements("PGR", ("2026-09-30", 23_180_000_000), ("2026-06-30", 22_100_000_000))
    fmp = FakeFMP2b(calendar={"2026-10-14": [erow("PGR", "2026-10-14", 1.08, 4.35, 7.16e9, 7.5e9)]},
                    profiles={"PGR": pgr}, currency={"PGR": quarterly})
    got = await erun(fmp, run_date=_PERIOD_RUN)
    assert got.records == () and got.rejections == {"period_mismatch": 1}
    assert fmp.statement_calls == [("PGR", 4)]


@pytest.mark.asyncio
async def test_delta_is_kept_with_the_quarter_its_statement_places_it_in():
    """Live (2026-10-15 preview): DAL reported Oct 9; the calendar says revenue $20.2B, the statement of the
    quarter ended Sep 30 says $20.186B (0.07% apart) → ``period_end`` Sep 30, and the post says "the quarter
    that ended September 30th". The EPS pair here is a clear surprise — Delta's REAL pair that day (1.72 vs
    1.77, 2.8%) is refused `surprise_unclear` (owner default: a ≥ 5% surprise), pinned below."""
    from app.services.marketing import news_templates as T

    profiles = {"DAL": prof(*_DAL, cap=38e9)}
    cal = {"2026-10-09": [erow("DAL", "2026-10-09", 1.86, 1.77, 20.2e9, 17_651_850_000.0)]}
    fmp = FakeFMP2b(calendar=cal, profiles=profiles, currency={"DAL": _DAL_STATEMENTS})
    got = await erun(fmp, run_date=_PERIOD_RUN)
    (rec,) = got.records
    assert (rec.company.symbol, rec.report_date, rec.period_end) == ("DAL", date(2026, 10, 9), date(2026, 9, 30))
    assert (rec.revenue_actual, rec.revenue_estimate) == (20.2e9, 17_651_850_000.0) and got.rejections == {}
    assert fmp.statement_calls == [("DAL", 4)]
    # news-v23 (one table of 2-4 companies): the post prints DAL's row — EPS and revenue against the estimates —
    # under the report day; the quarter stays the record's data-quality check, never a drawn string.
    other = dataclasses.replace(rec, company=R.CompanyRef(symbol="UAL", name="United Airlines"), eps_actual=2.6, eps_estimate=2.4)
    out = T.compose(R.EarningsDay(series="earnings", reports=(rec, other)), run_date=_PERIOD_RUN,
                    store_state="live", allow_x_url=False)
    dal = out["image_spec"]["sections"][0]["rows"][0]
    assert [line["cells"] for line in dal["table"]] == [["EPS", "$1.77", "$1.86", "+5.1%"],
                                                        ["Revenue", "$17.7B", "$20.2B", "+14.4%"]]
    assert out["image_spec"]["subtitle"] == "Reported Oct 9, 2026"
    # Delta's REAL EPS pair that day: a 2.8% gap — no clear surprise, refused before any profile.
    A.clear_memo()
    cal = {"2026-10-09": [erow("DAL", "2026-10-09", 1.72, 1.77, 20.2e9, 17_651_850_000.0)]}
    fmp = FakeFMP2b(calendar=cal, profiles=profiles, currency={"DAL": _DAL_STATEMENTS})
    got = await erun(fmp, run_date=_PERIOD_RUN)
    assert got.records == () and got.rejections == {"surprise_unclear": 1} and fmp.calls["profiles"] == 0


@pytest.mark.asyncio
async def test_a_nurix_like_one_off_never_leads_a_larger_company():
    """Live finding (2026-10-15 preview): Nurix's $4.73 vs -$0.60 (an 8.9x gap at a $2.55B cap) led the
    week because the widest gap ranked first. Batch 1 ranks by market cap: Goldman Sachs' smaller (clear)
    surprise leads and the one-off follows. Nurix's REAL revenue pair ($673M vs a $24M estimate) does not
    even qualify (`revenue_implausible`)."""
    profiles = {"NRIX": prof("NRIX", "Nurix Therapeutics, Inc.", exchange="NASDAQ", cap=2.55e9),
                "GS": prof("GS", "The Goldman Sachs Group, Inc.", cap=150e9)}
    rows = [erow("NRIX", "2027-01-12", 4.73, -0.60, 30e6, 25e6), erow("GS", "2027-01-12", 17.01, 12.86, 18.7e9, 16.8e9)]
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["GS", "NRIX"]
    assert abs(4.73 + 0.60) / 0.60 > abs(17.01 - 12.86) / 12.86          # the gap order would put NRIX first
    A.clear_memo()
    rows = [erow("NRIX", "2027-01-12", 4.73, -0.60, 673e6, 24e6), erow("GS", "2027-01-12", 17.01, 12.86, 18.7e9, 16.8e9)]
    got = await erun(FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles))
    assert [r.company.symbol for r in got.records] == ["GS"] and got.rejections == {"revenue_implausible": 1}


@pytest.mark.parametrize("quarters, reason", [
    # the reported quarter (Dec 31) is not in the statement yet: the newest row is Sep 30, 104 days before
    # the report — maybe the quarter BEFORE — so even a revenue that matches it proves nothing
    ((("2026-09-30", 1_050_000_000), ("2026-06-30", 980_000_000)), "period_unverified"),
    # nothing within 120 days of the report
    ((("2026-06-30", 1_050_000_000), ("2026-03-31", 990_000_000)), "period_unverified"),
    # a period ending ON the report day: the answer is not trusted for the reported quarter
    ((("2027-01-12", 1_050_000_000), ("2026-12-31", 1_050_000_000)), "period_unverified"),
    # the reported quarter's revenue is missing / not a number
    ((("2026-12-31", None), ("2026-09-30", 1_000_000_000)), "period_unverified"),
    ((("2026-12-31", "1.05e9"), ("2026-09-30", 1_000_000_000)), "period_unverified"),
    # the reported quarter disagrees with the calendar beyond 3% (a month, a half-year, another basis)
    ((("2026-12-31", 1_100_000_000), ("2026-09-30", 1_050_000_000)), "period_mismatch"),
    # … even when an OLDER row in the window matches: only the newest one is the reported quarter
    ((("2026-12-31", 2_100_000_000), ("2026-09-30", 1_050_000_000)), "period_mismatch"),
    # two rows on the newest period that disagree
    ((("2026-12-31", 1_050_000_000), ("2026-12-31", 1_200_000_000), ("2026-09-30", 1_000_000_000)),
     "period_mismatch"),
    # review of 2026-10-16 (ER-4): the placed row must be a QUARTER — a half-year shape (FMP's "quarters" of a
    # semiannual reporter), a single row, or one period end twice with nothing older shows none
    ((("2026-12-31", 1_050_000_000), ("2026-06-30", 990_000_000)), "period_unverified"),
    ((("2026-12-31", 1_050_000_000),), "period_unverified"),
    ((("2026-12-31", 1_050_000_000), ("2026-12-31", 1_050_000_000)), "period_unverified"),
], ids=["stale_newest_row", "nothing_in_window", "period_on_report_day", "no_revenue", "string_revenue",
        "mismatch", "older_row_matches", "two_rows_disagree", "half_year_shape", "single_row", "one_date_twice"])
@pytest.mark.asyncio
async def test_the_period_check_refuses_what_it_cannot_place(quarters, reason, caplog):
    caplog.set_level(logging.INFO, logger=A.__name__)
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}, profiles=earnings_profiles(),
                    currency={"AAA": _statements("AAA", *quarters)})
    got = await erun(fmp)
    assert got.records == () and got.rejections == {reason: 1}
    assert f"AAA refused as {reason}" in caplog.text


@pytest.mark.parametrize("revenue, period_end", [
    (1_050_000_000, date(2026, 12, 31)), (1_081_500_000, date(2026, 12, 31)),    # equal; 3.0% exactly
    (1_018_500_000, date(2026, 12, 31)), (1_081_600_000, None), (1_018_400_000, None),
])
@pytest.mark.asyncio
async def test_the_reported_quarter_matches_within_three_percent(revenue, period_end):
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}, profiles=earnings_profiles(),
                    currency={"AAA": _statements("AAA", ("2026-12-31", revenue), ("2026-09-30", 990_000_000))})
    got = await erun(fmp)
    if period_end is None:
        assert got.records == () and got.rejections == {"period_mismatch": 1}
    else:
        (rec,) = got.records
        assert rec.period_end == period_end and got.rejections == {}


@pytest.mark.parametrize("stale_revenue, kept", [
    (1_000_000_000, True), (790_000_000, True), (1_390_000_000, True),     # ×1.05, ×1.33, ×0.755: a quarter's scale
    (3_500_000_000, False), (700_000_000, False),                          # ×0.3 (a month), ×1.5
])
@pytest.mark.asyncio
async def test_the_scale_check_contingency_keeps_a_lagging_report_without_a_period(monkeypatch, stale_revenue, kept):
    """`R.EARNINGS_ALLOW_SCALE_CHECK` (off by default): when the statement has not caught up (its newest row
    is the quarter BEFORE), a report whose revenue is a quarter's scale of it is kept WITHOUT a period
    ("reported on <date>") — never with that row's period."""
    assert R.EARNINGS_ALLOW_SCALE_CHECK is False
    quarters = _statements("AAA", ("2026-09-30", stale_revenue), ("2026-06-30", 900_000_000))
    world = dict(calendar={"2027-01-12": [erow("AAA", "2027-01-12", 1.5, 1.0)]}, profiles=earnings_profiles(),
                 currency={"AAA": quarters})
    got = await erun(FakeFMP2b(**world))
    assert got.records == () and got.rejections == {"period_unverified": 1}      # the default: refused
    A.clear_memo()
    monkeypatch.setattr(R, "EARNINGS_ALLOW_SCALE_CHECK", True)
    got = await erun(FakeFMP2b(**world))
    if kept:
        (rec,) = got.records
        assert rec.period_end is None and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {"period_unverified": 1}


@pytest.mark.asyncio
async def test_a_kept_record_is_never_counted_as_a_rejection():
    """Batch 1: the old code counted `revenue_dropped` on a record it then KEPT. Every count is now a
    refusal: three reports kept, one refused per reason, and the counts are exactly those refusals —
    every candidate counted once, a kept one never. Mutation-checked (2026-10-15): a `ctx.reject(...)` on
    the kept path turned it red."""
    rows = [erow("KA", "2027-01-12", 1.5, 1.0), erow("KB", "2027-01-12", 1.4, 1.0), erow("KC", "2027-01-12", 1.3, 1.0),
            erow("RV", "2027-01-12", 1.5, 1.0, 3e9, 1e9),                 # revenue out of band
            erow("SU", "2027-01-12", 1.02, 1.0),                          # a 2% gap
            erow("PM", "2027-01-12", 1.5, 1.0), erow("PU", "2027-01-12", 1.5, 1.0), erow("EU", "2027-01-12", 1.5, 1.0)]
    profiles = {r["symbol"]: prof(r["symbol"], f"{r['symbol']} Industries Inc.", cap=5e9) for r in rows}
    # (review of 2026-10-16: PM's mismatching quarter carries the quarter before it, so it is a quarter)
    currency = {"PM": _statements("PM", ("2026-12-31", 1_300_000_000), ("2026-09-30", 1_000_000_000)),
                "PU": _statements("PU", ("2026-09-30", 1_050_000_000)), "EU": "EUR"}
    fmp = FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles, currency=currency)
    got = await erun(fmp, limit=5)
    assert sorted(r.company.symbol for r in got.records) == ["KA", "KB", "KC"]
    assert got.rejections == {"revenue_implausible": 1, "surprise_unclear": 1, "period_mismatch": 1,
                              "period_unverified": 1, "non_usd_reporter": 1}
    assert sum(got.rejections.values()) + len(got.records) == len(rows)
    assert all(r.period_end == date(2026, 12, 31) for r in got.records)


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


_EIGHT = ["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOG", "GOOGL"]


@pytest.mark.parametrize("tickers, profiles_over, members, size", [
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOGL"], {}, 7, 7),                   # nothing dropped
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "GOOGL"], {"INTC": {"exchange": "OTC"}}, 6, 7),  # rr9 probe
    (["NVDA", " nvda ", "AMD", "AVGO", "QCOM", "MRVL", "INTC"], {}, 6, 6),                   # a duplicate spelling
    # Review round 15: theme_size counts COMPANIES. Two classes of one issuer are one company listed
    # once — "7 of the 8 companies" claimed an eighth that does not exist (the old (7, 8)).
    (_EIGHT, {}, 7, 7),                                         # same display name
    (_EIGHT, {"GOOGL": {"companyName": "Google Inc.", "cik": issuer_cik("GOOG")}}, 7, 7),   # same CIK, other name
    (_EIGHT, {"GOOGL": {"exchange": "OTC"}}, 7, 7),           # the other class fails a gate: still one company
    (_EIGHT, {"GOOGL": {"companyName": "Google Inc."}}, 8, 8),  # another name AND another CIK: two companies
    (["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC", "BRK.B", "BRK-B"],
     {"BRK-B": prof("BRK-B", "Berkshire Hathaway Inc.")}, 7, 7),                             # one ticker, two spellings
], ids=["complete", "otc_member_dropped", "duplicate", "share_classes", "share_classes_same_cik",
        "share_class_gated", "two_companies", "dot_dash_spelling"])
@pytest.mark.asyncio
async def test_theme_size_is_counted_before_any_gate(tickers, profiles_over, members, size):
    profiles = theme_profiles()
    for sym, over in profiles_over.items():
        profiles[sym] = {**profiles.get(sym, {}), **over}
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


# The age rows pin congress_start to the 119th's (2025-01-03), never `_KEEP`: the shipped file's own
# congress_start moves at each new Congress (the January 2027 refresh writes 2027-01-03, later than
# these fetched_on dates, which would refuse the rows for the wrong reason — post-commit check, 2026-10-10).
@pytest.mark.parametrize("fetched_on, congress_start, named, error", [
    ("2026-10-09", "2025-01-03", True, False),    # 38 days before RUN, holding the 119th
    ("2026-05-01", "2025-01-03", True, True),     # 199 days: still named, ERROR (refresh it)
    ("2025-10-12", "2025-01-03", True, True),     # 400 days: the last day it is fresh
    ("2025-10-11", "2025-01-03", False, True),    # 401 days: the series is refused
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


@pytest.mark.parametrize("cik, curated, shown", [
    (SOROS, "Soros Fund", "Soros Fund Management"), (SOROS, "Someone Else Entirely", "Soros Fund Management"),
    (SOROS, "", "Soros Fund Management"),
    # Batch 1 (2026-10-15): Pershing's registry CIK is read as its successor — the subject is THAT filer
    (PERSHING, "Pershing Square Capital", "Pershing Square"), (PERSHING, "", "Pershing Square"),
])
@pytest.mark.asyncio
async def test_the_subject_is_the_edgar_filer_never_the_curated_firm_name(cik, curated, shown):
    fmp, sb = registry_world([whale(cik, curated)])
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
    # the stored previous book, and (since 2026-10-19 read for every club filing with a move) the complete
    # current build row
    sb.tables["trillion_club_filings"] = [{"cik": "0001045810", "period": "2026-Q2", "holdings": previous},
                                          sb.tables["trillion_club_filings"][1]]
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
        # news-v23: a second eligible holding (a 13F post shows at least two)
        {"symbol": "AAPL", "name": "Apple Inc.", "change": "newly_reported", "shares": 2_000_000.0, "value": 300e6},
        exit_row])
    got = await run("thirteen_f", FakeFMP(profiles=_alphabet_profiles()), club=club, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [("AAPL", "newly_reported"),
                                                                           ("CRWV", "newly_reported")]
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
         "newly_listed": True},
        {"symbol": "AAPL", "name": "Apple Inc.", "change": "newly_reported", "shares": 2_000_000.0, "value": 300e6}],
        holdings=holdings)
    profiles = {**thirteen_f_profiles(), "NWS": prof("NWS", "News Corporation", exchange="NASDAQ"),
                "NWSA": prof("NWSA", "News Corporation", exchange="NASDAQ")}
    got = await run("thirteen_f", FakeFMP(profiles=profiles), club=club, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [("AAPL", "newly_reported"),
                                                                           ("CRWV", "newly_reported")]
    assert got.rejections == {"share_class_overlap": 1}
    # Control: with NWSA gone from the current book, the exit is published.
    A.clear_memo()
    club, sb = _alphabet_club_world(previous, [
        {"symbol": "NWS", "name": "NEWS CORP NEW", "change": "no_longer_reported", "prev_shares": 6_000_000.0},
        {"symbol": "CRWV", "name": "CoreWeave", "change": "newly_reported", "shares": 1_000_000.0, "value": 150e6,
         "newly_listed": True},
        {"symbol": "AAPL", "name": "Apple Inc.", "change": "newly_reported", "shares": 2_000_000.0, "value": 300e6}],
        holdings=[])
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
    # news-v23 phase 3: a failed angle source is that angle's turn lost (the next angle is tried); a profile batch or the
    # budget running out still ends the walk with the held record
    assert {"lookups_fail": "space angle make_money unavailable",
            "lookups_slow": "budget exhausted at theme space angle make_money with 1 record(s) held",
            "profiles_fail": "unavailable at theme space with 1 record(s) held"}[variant] in caplog.text
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

        async def recent_source_refs(self, run_date, limit, **kw):
            return []

        async def lane_scripts_between(self, start_date, end_date):   # the cross-lane read (migration 210): none
            return []

        async def insert_script(self, row, **kw):
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
    QS = ["QA", "QB", "QC", "QD"]
    big = [irow(sym=s, name=f"QX{s} RYAN", cik=f"000000{i:04d}", shares=200_000 - 1_000 * i, price=25.0)
           for i, s in enumerate(QS)]
    rows = big + [irow(sym="PNNT", shares=100_000, price=9.0, **_PENN),
                  irow(sym="PFLT", shares=40_000, price=10.0, **_PENN)]
    profiles = {**{s: prof(s, f"{s} Industries Inc.") for s in QS}, **_pennant_profiles()}
    # Wave 1 = QA..QD + PNNT (five: INSIDER_MAX_ROWS); QA's read fails, so a wave 2 opens for the spare — PFLT,
    # Penn's again.
    fmp = FakeFMP(insider=rows, profiles=profiles,
                  issuer_feed={A.normalize_cik(issuer_cik("QA")): FMPUnavailableException("issuer read down")})
    got = await run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == [*QS[1:], "PNNT"]
    assert got.rejections == {"amendment_check_failed": 1, "same_person": 1}
    assert issuer_reads(fmp) == ["PNNT", *QS]                             # PFLT never read


# ── Batch 1 (2026-10-15): the members-elect window (owner decision 4) ─────────


def _insider_world():
    rows = week_rows() + [
        irow(sym="FOX", name="TAYLOR EMMA", cik="0000000555", title="officer: Chief Financial Officer",
             shares=10_000, price=30.0),
        irow(sym="FOX", name="BROWN DAVID", cik="0000000556", title="director", shares=20_000, price=30.0),
    ]
    return FakeFMP(insider=rows, profiles=week_profiles())


@pytest.mark.parametrize("series, named", [("ceo_buys", "Ryan Cohen"), ("insider_buys", "David Brown")])
@pytest.mark.asyncio
async def test_inside_the_members_elect_window_every_form4_row_is_built_role_only(monkeypatch, caplog, series, named):
    """Owner decision 4: `RUN` (Nov 16, 2026) is inside the window (Election Day Nov 3, 2026 through Jan 3,
    2027): the record carries NO person — so neither the post nor its fact sheet can name one — and the
    series still runs. The record composes into a post that names no one anywhere and passes
    validate_package. Mutation-checked (2026-10-15): without the window line in `_insider_row` the name was
    built and this turned red."""
    from app.services.marketing import news_templates as T

    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    assert R.members_elect_window(RUN)
    caplog.set_level(logging.INFO, logger=A.__name__)
    got = await run(series, _insider_world())
    (rec,) = got.records
    assert rec.rows and all(r.person_name is None for r in rec.rows)
    sheet = json.dumps(R.fact_sheet(rec, rejections=got.rejections, selection={})).lower()
    surname = named.split()[-1].lower()
    assert surname not in sheet
    assert f"company news {series}: 2026-11-16 is inside the members-elect window" in caplog.text
    out = T.compose(rec, run_date=RUN, store_state="live", allow_x_url=False)
    assert out["persons"] == [] and surname not in json.dumps(out).lower()
    assert T.validate_package(out, record=rec) == []
    # Control: outside the window the same week names the person.
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)
    A.clear_memo()
    got = await run(series, _insider_world())
    assert named in [r.person_name for r in got.records[0].rows]


@pytest.mark.asyncio
async def test_inside_the_window_a_member_is_still_dropped_never_built_role_only(monkeypatch):
    """The window turns names OFF; it never turns the Congress drop off: a sitting member's row is
    dropped (`congress_name`), not built as an unnamed director."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    rows = week_rows() + [irow(sym="FOX", name="PELOSI NANCY", cik="0000000444", title="director",
                               shares=20_000, price=30.0)]
    got = await run("insider_buys", FakeFMP(insider=rows, profiles=week_profiles()))
    assert got.records == () and got.rejections.get("congress_name") == 1


def test_every_exported_name_exists_once():
    """Batch 1 replaced two earnings constants (`EARNINGS_PROFILE_CANDIDATES`, `EARNINGS_MAX_CURRENCY_READS`)
    and their names lingered in `__all__` with every suite green — a ``from … import *`` would have raised
    AttributeError. Every exported name of the adapter, the rules and the templates must exist, once."""
    from app.services.marketing import news_templates as T

    for mod in (A, R, T):
        assert [n for n in mod.__all__ if not hasattr(mod, n)] == [], mod.__name__
        assert len(mod.__all__) == len(set(mod.__all__)), mod.__name__
    assert {"EARNINGS_MAX_STATEMENT_READS", "EARNINGS_STATEMENT_ROWS"} <= set(A.__all__)
    assert {"members_elect_window", "general_election_day", "earnings_period", "clear_surprise",
            "earnings_revenue_pair", "statement_currency", "EARNINGS_CLEAR_SURPRISE"} <= set(R.__all__)


# ══ Batch 1 (2026-10-15): 13F — the split-lookup cap, Pershing's successor CIK, Berkshire first ══════
#
# Plan item 3. (1) The shared split check looks up at most 25 suspects per filing and the rest keep their
# RAW share diff (`BuiltFiling.split_unchecked`): a filing whose lookups were capped shows no more / fewer
# shares — those moves (`split_unchecked`) and both counts go, newly reported and exits stay; a club filer's
# stored build is re-checked from its stored books. (2) Pershing Square's holdings are filed by CIK
# 0002026053 since its old CIK filed only a 13F-NT: read, built, keyed and named as the successor, in the
# old CIK's registry place. (3) Berkshire leads (before the club and the rest of the registry), under ONE
# live-build budget. Every new guard mutation-checked.

NVIDIA_CIK = "0001045810"


class NoSplitActions:
    """The corporate-actions seam with no split anywhere: every looked-up suspect is confirmed clean, so only
    the CAP can leave one unchecked. Records the tickers looked up."""

    def __init__(self) -> None:
        self.looked_up: List[str] = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.looked_up.append(t)
        return []

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, **kw):
        return False


#: Symbols for the extra positions (no W / U / R ending: never read as a warrant, unit or right).
_SPLIT_SYMS = [f"Z{a}{b}" for a in "ABCD" for b in "ABCDEFGHIJKLMNOP"]
#: news-v23: the 13F list says "Increased to …" / "Reduced to …" and "increased" / "reduced" (the old words kept: a
#: regression to them must still be caught)
_NO_MORE_OR_FEWER = ("more shares", "fewer shares", "Increased to", "Reduced to", "increased", "reduced")


def _split_registry_world(n_extra: int):
    """Berkshire's `book` plus ``n_extra`` positions held in both quarters whose share count rose 50% at the
    same $10M value — a per-share price down a third, the shape of a 3:2 split, so each is a split SUSPECT.
    The seam finds no split, so a looked-up suspect keeps its raw diff ("increased"); past the cap of 25 the
    rest are never looked up."""
    extracts = book(BERKSHIRE)
    syms = _SPLIT_SYMS[:n_extra]
    for i, s in enumerate(syms):
        cusip = f"{900000 + i:06d}AB1"
        extracts[(BERKSHIRE, 2026, 2)].append(xrow(BERKSHIRE, 2, s, cusip, 100_000, 10e6))
        extracts[(BERKSHIRE, 2026, 3)].append(xrow(BERKSHIRE, 3, s, cusip, 150_000, 10e6))
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway")], extracts=extracts)
    fmp.profiles.update({s: prof(s, f"{s.title()} Robotics Inc.") for s in syms})
    return fmp, sb


def _compose_text(rec) -> str:
    from app.services.marketing import news_templates as NT

    return json.dumps(NT.compose(rec, run_date=RUN, store_state="live", allow_x_url=False))


@pytest.mark.asyncio
async def test_a_registry_filing_past_the_split_cap_shows_no_more_or_fewer_shares(caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = _split_registry_world(26)
    acts = NoSplitActions()
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    assert len(acts.looked_up) == 25                         # the cap: one suspect was never checked
    assert [(m.company.symbol, m.move) for m in rec.moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported")]
    # Both kinds' counts are LEFT OUT — never written as 0, a false "no holding grew".
    assert dict(rec.counts) == {"newly_reported": 1, "no_longer_reported": 1}
    assert got.rejections == {"split_unchecked": 27}         # AAPL + the 26 extras, before any other gate
    assert f"cik={BERKSHIRE} 2026-Q3" in caplog.text and "(split_unchecked)" in caplog.text
    text = _compose_text(rec)                                # end to end: the post never says it
    assert not any(p in text for p in _NO_MORE_OR_FEWER)
    assert "CoreWeave" in text


@pytest.mark.asyncio
async def test_a_registry_filing_at_the_split_cap_keeps_its_more_and_fewer_moves():
    fmp, sb = _split_registry_world(25)
    acts = NoSplitActions()
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    # The build looked up its 25 suspects; the review of 2026-10-16 (F1) then re-checked every more / fewer row
    # (the 25 extras and AAPL) — in production the suspects' 25 are cache hits of the build's own derivations.
    assert len(acts.looked_up) == 25 + 26 and sorted(acts.looked_up[25:]) == sorted(["AAPL", *_SPLIT_SYMS[:25]])
    assert "split_unchecked" not in got.rejections and "split_in_window" not in got.rejections
    assert ("AAPL", "increased") in [(m.company.symbol, m.move) for m in rec.moves]
    assert dict(rec.counts) == {"newly_reported": 1, "increased": 26, "decreased": 0, "no_longer_reported": 1}
    assert "Increased to " in _compose_text(rec)                 # news-v23: the 13F list's status word


def _club_split_books(n: int):
    """A club filer's stored CURRENT (2026-Q3) and PREVIOUS (2026-Q2) books: ``n`` positions whose share count
    rose 50% at the same value (split suspects), and RXRX on the previous book (its exit's value)."""
    syms = _SPLIT_SYMS[:n]
    previous = [{"symbol": "RXRX", "shares": 7_000_000.0, "value": 35e6}] + [
        {"symbol": s, "shares": 100_000.0, "value": 10e6} for s in syms]
    current = [{"symbol": s, "shares": 150_000.0, "value": 10e6} for s in syms]
    return current, previous


_CLUB_KEPT = ([("CRWV", "newly_reported"), ("RXRX", "no_longer_reported"), ("AAPL", "increased")],
              {"newly_reported": 3, "increased": 1, "decreased": 1, "no_longer_reported": 1},
              {"etf_or_fund": 1, "move_not_routable": 1, "move_too_small": 1})
_CLUB_DROPPED = ([("CRWV", "newly_reported"), ("RXRX", "no_longer_reported")],
                 {"newly_reported": 3, "no_longer_reported": 1},
                 {"etf_or_fund": 1, "move_not_routable": 1, "split_unchecked": 2})


@pytest.mark.parametrize("position_count, n_suspects, current, expected, why", [
    # suspects are a subset of the positions: a book of at most 25 never reached the cap — no re-derivation
    (8, 26, "rows", _CLUB_KEPT, None),
    (25, 26, "rows", _CLUB_KEPT, None),
    (30, 25, "rows", _CLUB_KEPT, None),        # at the cap, nothing was trimmed
    (30, 26, "rows", _CLUB_DROPPED, "1 split suspect(s) past the lookup cap (ZBJ)"),      # one past the cap
    # (a MISSING current row refuses the whole filer since 2026-10-19:
    # `test_a_club_filer_whose_current_build_cannot_be_read_is_refused`)
    (30, 0, "not json", _CLUB_DROPPED, "its stored holdings or change rows are unreadable"),   # unreadable
], ids=["small_book", "at_the_bound", "at_the_cap", "past_the_cap", "current_garbage"])
@pytest.mark.asyncio
async def test_a_club_filing_is_rechecked_against_the_split_cap_from_its_stored_books(
        monkeypatch, caplog, position_count, n_suspects, current, expected, why):
    """Since the review of 2026-10-16 the stored CURRENT row is read for every filing with a more / fewer move or
    count (its build status and change rows: F1, F2) — two reads, whatever the book's size."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb = club_world(company_over={"position_count": position_count})
    detail = club.details["nvidia"]
    cur, prev = _club_split_books(n_suspects)
    rows = [{"cik": NVIDIA_CIK, "period": "2026-Q2", "holdings": prev}]
    if current is not None:
        rows.append(stored_current(NVIDIA_CIK, "2026-Q3", detail.changes, detail.company.change_counts,
                                   holdings=cur if current == "rows" else current))
    sb.tables["trillion_club_filings"] = rows
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    (rec,) = got.records
    moves, counts, rejections = expected
    assert [(m.company.symbol, m.move) for m in rec.moves] == moves
    assert dict(rec.counts) == counts and got.rejections == rejections
    assert sb.calls["trillion_club_filings"] == 2
    if why is not None:
        assert why in caplog.text and "(split_unchecked)" in caplog.text


@pytest.mark.asyncio
async def test_pershing_is_read_built_keyed_and_named_as_its_successor_filer():
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital", "PSH.L")])
    got = await run("thirteen_f", fmp, sb=sb)
    (rec,) = got.records
    assert (rec.filer_cik, rec.filer_name, rec.filer_symbol) == (PERSHING_FILER, "Pershing Square", None)
    assert R.ledger_key(rec) == f"news:thirteen_f:{PERSHING_FILER}:2026-Q3"
    assert fmp.dates_ciks == [PERSHING_FILER]                       # never the old CIK's dates ...
    assert sorted(fmp.extract_keys) == [(PERSHING_FILER, 2026, 2), (PERSHING_FILER, 2026, 3)]   # ... or book
    again = await run("thirteen_f", fmp, sb=sb)
    assert again.records == got.records and fmp.calls["extract"] == 2    # memoized under the successor


@pytest.mark.parametrize("posted_under", [PERSHING, PERSHING_FILER], ids=["old_key", "successor_key"])
@pytest.mark.asyncio
async def test_a_successor_filing_posted_under_either_key_is_never_read_again(posted_under):
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital")])
    got = await run("thirteen_f", fmp, sb=sb, exclude={f"news:thirteen_f:{posted_under}:2026-Q3"})
    assert got.records == () and got.rejections == {"already_posted": 1} and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_only_the_successors_filing_dates_are_read():
    """The old CIK still answers dates (its 13F-NT era) — the adapter never asks it."""
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital")], dates={PERSHING: DATES_Q3_Q2})
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections == {"filer_not_filed": 1}
    assert fmp.dates_ciks == [PERSHING_FILER] and fmp.calls["extract"] == 0


@pytest.mark.asyncio
async def test_a_successor_missing_from_the_filer_table_is_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(R, "THIRTEEN_F_SUCCESSORS", {SCION: "0009999998"})
    fmp, sb = registry_world([whale(SCION, "Scion Asset Management")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections == {"filer_entity_unknown": 1} and fmp.calls == Counter()


@pytest.mark.asyncio
async def test_a_successor_curated_beside_its_old_cik_is_read_once(monkeypatch):
    order = dict(A._registry_order())
    order[PERSHING_FILER] = order[PERSHING] + 1
    monkeypatch.setattr(A, "_registry_order", lambda: order)
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital"), whale(PERSHING_FILER, "Pershing Square")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING_FILER] and fmp.dates_ciks == [PERSHING_FILER]


def _club_and_registry_world(whales):
    """The club world (NVIDIA, a club 13F filer read from its stored build) beside registry filers."""
    club, sb = club_world()
    fmp, reg_sb = registry_world(whales)
    sb.tables["whales"] = reg_sb.tables["whales"]
    return club, sb, fmp


@pytest.mark.asyncio
async def test_berkshire_leads_ahead_of_the_club_and_the_rest_of_the_registry(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, fmp = _club_and_registry_world([whale(SOROS, "Soros Fund Management"),
                                              whale(BERKSHIRE, "Berkshire Hathaway")])
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert [r.filer_cik for r in got.records] == [BERKSHIRE, NVIDIA_CIK, SOROS]   # was NVIDIA, Berkshire, Soros


@pytest.mark.asyncio
async def test_a_lead_filer_leads_whatever_its_registry_place(monkeypatch):
    order = dict(A._registry_order())
    order[BERKSHIRE] = max(order.values()) + 1                # curated LAST
    monkeypatch.setattr(A, "_registry_order", lambda: order)
    fmp, sb = registry_world([whale(SOROS, "Soros Fund Management"), whale(BERKSHIRE, "Berkshire Hathaway")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [BERKSHIRE, SOROS] and fmp.dates_ciks == [BERKSHIRE, SOROS]


@pytest.mark.asyncio
async def test_the_lead_and_the_rest_share_one_live_build_budget(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, fmp = _club_and_registry_world([whale(BERKSHIRE, "Berkshire Hathaway"),
                                              whale(PERSHING, "Pershing Square Capital"),
                                              whale(SCION, "Scion Asset Management")])
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert [r.filer_cik for r in got.records] == [BERKSHIRE, NVIDIA_CIK, PERSHING_FILER]
    assert fmp.calls["extract"] == 2 * A.MAX_LIVE_13F_BUILDS and SCION not in fmp.dates_ciks


@pytest.mark.asyncio
async def test_a_lead_filer_that_is_also_a_club_filer_is_read_once_as_the_lead(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world(card_over={"slug": "berkshire", "name": "Berkshire Hathaway", "detail_symbol": "BRK-A"},
                          rows=[{"slug": "berkshire", "ciks": [BERKSHIRE], "card_kind": "thirteen_f",
                                 "use_13f": True, "detail_symbol": "BRK-A", "published": True}])
    fmp, reg_sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    sb.tables["whales"] = reg_sb.tables["whales"]
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert [r.filer_cik for r in got.records] == [BERKSHIRE]
    assert club.calls["detail"] == 0 and "filer_unavailable" not in got.rejections


@pytest.mark.parametrize("value", ["missing", None, ["ZAA"], "ZAA", 1], ids=["missing", "none", "list", "str", "int"])
@pytest.mark.asyncio
async def test_a_build_whose_split_unchecked_is_unreadable_fails_closed(monkeypatch, value):
    """Fail closed: anything but a set reads as "the cap trimmed something", never as "all checked"."""
    real = A._build_registry

    async def build(ctx, cik, year, quarter):
        built, cur, prev = await real(ctx, cik, year, quarter)
        if value == "missing":       # a dataclass default is also a class attribute: copy without the field
            built = SimpleNamespace(**{f.name: getattr(built, f.name) for f in dataclasses_fields(built)
                                       if f.name != "split_unchecked"})
        else:
            built.split_unchecked = value
        return built, cur, prev

    monkeypatch.setattr(A, "_build_registry", build)
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    got = await run("thirteen_f", fmp, sb=sb)
    (rec,) = got.records
    assert [(m.company.symbol, m.move) for m in rec.moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported")]
    assert dict(rec.counts) == {"newly_reported": 1, "no_longer_reported": 1}
    assert got.rejections == {"split_unchecked": 1}


@pytest.mark.asyncio
async def test_a_lead_filer_that_fills_the_room_leaves_the_club_unread(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, fmp = _club_and_registry_world([whale(BERKSHIRE, "Berkshire Hathaway"),
                                              whale(SOROS, "Soros Fund Management")])
    got = await run("thirteen_f", fmp, club=club, sb=sb, limit=1)
    assert [r.filer_cik for r in got.records] == [BERKSHIRE]
    assert club.calls == Counter() and fmp.dates_ciks == [BERKSHIRE]


# ══ review of 2026-10-16: 13F — every more / fewer row re-checked for a split (F1), a club filer's degraded
# stored build (F2), F3 closed by F1, the successor's hand-over quarter (live preview F2), a curated-inactive
# successor never silent (F4) ═════════════════════════════════════════════════════════════════════════════
#
# F1: the shared suspect filter only looks up a holding whose per-share price moved 30% or more, so a 3:2 split
# after a 20% rally, a 5:4 split at a flat price, a 2:1 split after a 60% rally or a 1:2 reverse split after a
# 40% fall was never looked up and its raw share diff was published as "reported more / fewer shares". Every
# increased / decreased row is now re-checked through the build's own seam; a split the build did not restate,
# an unnameable adjustment or a failed lookup drops BOTH kinds and their counts (the counts are stated too).
# Every new guard mutation-checked.

#: KO's previous-quarter row in `book`: 1,000,000 shares worth $70M ($70 a share).
_KO_PREV = (1_000_000, 70e6)


def _ko_world(q3_shares, q3_value, *, q2=_KO_PREV):
    """Berkshire's `book` with KO's 2026-Q3 row replaced (``q2`` its 2026-Q2 row): the shapes the shared
    suspect filter passes or flags, depending on KO's per-share price."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = [r for r in extracts[(BERKSHIRE, 2026, 3)] if r["symbol"] != "KO"] + [
        xrow(BERKSHIRE, 3, "KO", "191216100", q3_shares, q3_value)]
    extracts[(BERKSHIRE, 2026, 2)] = [r for r in extracts[(BERKSHIRE, 2026, 2)] if r["symbol"] != "KO"] + [
        xrow(BERKSHIRE, 2, "KO", "191216100", q2[0], q2[1])]
    return registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")], extracts=extracts)


#: Held-through splits the shared filter never flags (each KO price ratio inside (1/1.3, 1.3)): the review's
#: own computed cases, each with its split in the quarter (Aug 14, 2026).
_UNFLAGGED_SPLITS = {
    "3to2_rally_20pct": (1_500_000, 1_500_000 * 56.0, (3, 2), "increased"),
    "5to4_flat": (1_250_000, 70e6, (5, 4), "increased"),
    "2to1_rally_60pct": (2_000_000, 2_000_000 * 56.0, (2, 1), "increased"),
    "4to1_rally_210pct": (4_000_000, 4_000_000 * 54.25, (4, 1), "increased"),
    "1to2_reverse_fall_40pct": (500_000, 500_000 * 84.0, (1, 2), "decreased"),
}


def _more_or_fewer(rec) -> List[str]:
    return [m.company.symbol for m in rec.moves if m.move in ("increased", "decreased")]


@pytest.mark.parametrize("shape", list(_UNFLAGGED_SPLITS))
@pytest.mark.asyncio
async def test_a_held_through_split_the_filter_never_flags_is_never_published_as_more_or_fewer_shares(
        shape, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    shares, value, (num, den), raw_kind = _UNFLAGGED_SPLITS[shape]
    from app.services.thirteen_f_splits import suspicious_split_tickers
    fmp, sb = _ko_world(shares, value)
    cur = [{"symbol": "KO", "shares": shares, "value": value}]
    assert suspicious_split_tickers(cur, [{"symbol": "KO", "shares": _KO_PREV[0], "value": _KO_PREV[1]}]) == []
    acts = SplitActions(splits={"KO": [split_row("2026-08-14", num, den)]})
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    assert [(m.company.symbol, m.move) for m in rec.moves] == [("CRWV", "newly_reported"), ("OXY", "no_longer_reported")]
    assert dict(rec.counts) == {"newly_reported": 1, "no_longer_reported": 1}          # both counts left out
    assert got.rejections == {"split_in_window": 2}                                     # AAPL + KO
    assert "KO:split_not_restated" in caplog.text and "(split_in_window)" in caplog.text
    text = _compose_text(rec)
    assert not any(p in text for p in _NO_MORE_OR_FEWER)
    # news-v23 (the 13F list): the roundup never states its counts as the filing's — "2 of the holding changes in …"
    # says the post shows part of the filing (F13-1's withheld sentence retired with the per-kind counts it qualified)
    assert "2 of the holding changes in Berkshire Hathaway" in text
    # Control: the same filing with no split anywhere in the seam keeps KO's raw move — the re-check is what drops it.
    A.clear_memo()
    fmp, sb = _ko_world(shares, value)
    got = await run("thirteen_f", fmp, sb=sb)
    assert ("KO", raw_kind) in [(m.company.symbol, m.move) for m in got.records[0].moves]
    assert "split_in_window" not in got.rejections


@pytest.mark.asyncio
async def test_every_more_or_fewer_row_is_re_checked_over_its_quarter_and_a_week_after():
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    acts = SplitActions()
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    assert ("AAPL", "increased") in [(m.company.symbol, m.move) for m in got.records[0].moves]
    assert acts.looked_up == ["AAPL"]                  # the only more / fewer row; never a new or exited one
    # `window_for_range`: the fetch window starts 10 days before the previous quarter's end, as the build's; since the
    # re-review of 2026-10-19 (F13-QE-SPLIT) the window and the effective filter run 7 days past the quarter's end
    # (`A.THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS`) — no longer the build's own cache key.
    assert A.THIRTEEN_F_SPLIT_RECHECK_SLACK_DAYS == 7
    assert acts.windows == [("2026-06-20", "2026-10-07")]
    assert acts.probed == [("AAPL", "2026-06-30", "2026-10-07")]


@pytest.mark.parametrize("day, dropped", [
    ("2026-06-30", False),          # on the previous quarter's end: already in the previous book's count
    ("2026-07-01", True),
    ("2026-09-30", True),           # on the quarter's end: in it
    # the re-review of 2026-10-19 (F13-QE-SPLIT): a split paid on the quarter's last session is ex-dated the next
    # business day, and a filer booking on the payable date reports the post-split count — a week past the quarter
    # is checked too (the build never restates it, so the row is `split_in_window`)
    ("2026-10-01", True),
    ("2026-10-07", True),           # the last day of the slack
    ("2026-10-08", False),          # past it
], ids=["prev_end", "first_day", "quarter_end", "next_day", "slack_end", "past_slack"])
@pytest.mark.asyncio
async def test_only_a_split_inside_the_quarter_or_just_after_it_drops_the_moves(day, dropped):
    fmp, sb = _ko_world(1_200_000, 84e6)                 # a real 20% purchase at a flat price
    got = await run("thirteen_f", fmp, sb=sb, actions=SplitActions(splits={"KO": [split_row(day, 2, 1)]}))
    shown = _more_or_fewer(got.records[0])
    assert (shown == []) is dropped and ("split_in_window" in got.rejections) is dropped


@pytest.mark.parametrize("period_end, end", [
    (date(2026, 9, 30), "2026-10-07"),                   # a week past the quarter
    (RUN - timedelta(days=3), RUN.isoformat()),          # never past the run date
    (RUN, RUN.isoformat()),
    (RUN + timedelta(days=2), (RUN + timedelta(days=2)).isoformat()),   # (never in production) the quarter at least
], ids=["week", "run_date_cap", "quarter_end_is_run_date", "never_before_the_quarter_end"])
@pytest.mark.asyncio
async def test_the_re_check_window_ends_a_week_after_the_quarter_never_after_the_run(period_end, end):
    acts = SplitActions()
    got = await A._split_window_problem(_ctx13f(acts), [_r("increased", "AAA")], counts={"increased": 1, "decreased": 0},
                                        prev_shares=dict, cik=BERKSHIRE, period="2026-Q3",
                                        prev_end=period_end - timedelta(days=92), period_end=period_end)
    assert got is None and acts.windows[0][1] == end and acts.probed[0][2] == end


@pytest.mark.parametrize("split_under, dropped", [
    ("OLDKO", True),        # the split only in the OLD symbol's history (FMP keeps a renamed security's old symbol)
    ("KO", True),           # under the new one: as before
    (None, False),          # no split anywhere: a real 2x purchase stays
], ids=["old_symbol_history", "new_symbol_history", "no_split"])
@pytest.mark.asyncio
async def test_a_renamed_holding_is_re_checked_under_its_old_symbol_too(split_under, dropped):
    """The re-review of 2026-10-19 (F13-RENAME): KO's CUSIP sat under OLDKO on the previous extract (a rename in the
    quarter — FMP kept SQ beside XYZ). The re-check looked up only the row's CURRENT symbol, and the build's suspect
    filter never pairs two symbols, so a 2:1 split recorded only in OLDKO's history read as "reported more shares".
    Every symbol the CUSIP carries on either book is looked up now, and the row is clean only when all answer clean.
    Mutation-checked by hand (2026-10-19): without the CUSIP's other symbols, the old-history row published KO as
    increased (red)."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)] = [r for r in extracts[(BERKSHIRE, 2026, 3)] if r["symbol"] != "KO"] + [
        xrow(BERKSHIRE, 3, "KO", "191216100", 2_000_000, 140e6)]
    extracts[(BERKSHIRE, 2026, 2)] = [r for r in extracts[(BERKSHIRE, 2026, 2)] if r["symbol"] != "KO"] + [
        xrow(BERKSHIRE, 2, "OLDKO", "191216100", 1_000_000, 140e6)]
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")], extracts=extracts)
    acts = SplitActions(splits={split_under: [split_row("2026-08-14", 2, 1)]} if split_under else {})
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    assert {"KO", "OLDKO"} <= set(acts.looked_up)
    assert ("KO" not in _more_or_fewer(rec)) is dropped and ("split_in_window" in got.rejections) is dropped
    if dropped:
        assert _more_or_fewer(rec) == [] and not any(p in _compose_text(rec) for p in _NO_MORE_OR_FEWER)


@pytest.mark.asyncio
async def test_a_club_holding_renamed_in_the_quarter_is_re_checked_under_its_old_symbol(monkeypatch):
    """F13-RENAME on the club path: the stored PREVIOUS book carries AAPL's CUSIP under OLDAAPL; the split sits only
    in OLDAAPL's history — both kinds go. Mutation-checked by hand (2026-10-19): the club call without its CUSIP
    symbols kept AAPL as increased (red)."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    old_aapl = {"cusip": club_cusip("AAPL"), "symbol": "OLDAAPL", "shares": 1_000.0, "value": 2.5e5}
    club, sb, acts = _club_with(acts=SplitActions(splits={"OLDAAPL": [split_row("2026-08-14", 2, 1)]}),
                                previous=[{"symbol": "RXRX", "value": 35e6}, old_aapl])
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    (rec,) = got.records
    assert "OLDAAPL" in acts.looked_up and _more_or_fewer(rec) == []
    assert got.rejections["split_in_window"] == 2


def test_symbols_by_cusip_reads_every_book_and_skips_what_is_no_share_position():
    raw_prev = [{"securityCusip": "191216100", "symbol": "oldko "}, {"securityCusip": "191216100", "symbol": "--"},
                {"securityCusip": "037833100", "symbol": "AAPL", "putCallShare": "Call"},
                {"securityCusip": None, "symbol": "XYZ"}, "junk"]
    raw_cur = [{"securityCusip": "191216100", "symbol": "KO"}, {"securityCusip": "037833100", "symbol": "AAPL"}]
    assert A._symbols_by_cusip(raw_cur, raw_prev, cusip_key="securityCusip") == {
        "191216100": {"KO", "OLDKO"}, "037833100": {"AAPL"}}
    assert A._symbols_by_cusip(None, "x", cusip_key="cusip") == {}


@pytest.mark.parametrize("q3_shares, num", [(10_000_000, 10), (2_000_000, 2)], ids=["10to1", "2to1"])
@pytest.mark.asyncio
async def test_a_post_split_count_booked_before_the_ex_date_is_never_more_shares(q3_shares, num):
    """F13-QE-SPLIT (the review's reproduction): KO's 13F already holds the post-split count (a filer that booked
    the shares on the payable date, Sep 30), while the split is ex-dated Oct 1 — outside the quarter the build and
    its suspect lookup read, so the raw multiple was published "reported more shares of Coca-Cola" (10x, no trade).
    The re-check's week of slack finds it: both kinds dropped, nothing about more or fewer shares composed.
    Mutation-checked by hand (2026-10-19): with the slack back to 0 days KO was published as increased (red)."""
    fmp, sb = _ko_world(q3_shares, 70e6)                  # the same $70M position: the count is the split's
    got = await run("thirteen_f", fmp, sb=sb, actions=SplitActions(splits={"KO": [split_row("2026-10-01", num, 1)]}))
    (rec,) = got.records
    assert _more_or_fewer(rec) == [] and got.rejections["split_in_window"] == 2          # AAPL + KO
    assert not any(p in _compose_text(rec) for p in _NO_MORE_OR_FEWER)
    # control: dated in the quarter, the build itself restates it (the suspect lookup) and KO is no move at all
    A.clear_memo()
    fmp, sb = _ko_world(q3_shares, 70e6)
    got = await run("thirteen_f", fmp, sb=sb, actions=SplitActions(splits={"KO": [split_row("2026-09-30", num, 1)]}))
    assert "KO" not in [m.company.symbol for m in got.records[0].moves]


@pytest.mark.asyncio
async def test_a_split_the_build_restated_stays_and_one_it_did_not_restate_goes():
    """The re-check also covers the build's own suspects: a 2:1 split with a 10% purchase is restated by the build
    (1M → 2M previous shares, +10%) and stays; a 3:2 split with a 25% SALE is looked up by the build too, but
    `restate_prev_shares_for_split` keeps the raw diff for it (the count did not move toward the split), which
    reads as "more shares" — the re-check refuses it (`split_not_restated`)."""
    split = SplitActions(splits={"KO": [split_row("2026-08-14", 2, 1)]})
    fmp, sb = _ko_world(2_200_000, 2_200_000 * 38.5)
    got = await run("thirteen_f", fmp, sb=sb, actions=split)
    ko = next(m for m in got.records[0].moves if m.company.symbol == "KO")
    assert (ko.move, ko.shares, ko.prev_shares) == ("increased", 2_200_000.0, 2_000_000.0)   # restated
    assert "split_in_window" not in got.rejections and split.looked_up.count("KO") == 2      # build + re-check

    A.clear_memo()
    fmp, sb = _ko_world(1_125_000, 1_125_000 * 60.0, q2=(1_000_000, 90e6))
    got = await run("thirteen_f", fmp, sb=sb, actions=SplitActions(splits={"KO": [split_row("2026-08-14", 3, 2)]}))
    assert _more_or_fewer(got.records[0]) == [] and got.rejections == {"split_in_window": 2}


@pytest.mark.parametrize("acts, why", [
    (SplitActions(unclassified={"KO"}), "KO:adjustment"),
    (SplitActions(fail={"KO"}), "KO:lookup_failed"),
    (SplitActions(boom={"KO"}), "KO:lookup_failed"),
], ids=["unnameable_adjustment", "lookup_could_not_derive", "lookup_raised"])
@pytest.mark.asyncio
async def test_an_adjustment_or_a_failed_lookup_fails_closed(acts, why, caplog):
    caplog.set_level(logging.WARNING)
    fmp, sb = _ko_world(1_200_000, 84e6)
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    assert _more_or_fewer(rec) == [] and dict(rec.counts) == {"newly_reported": 1, "no_longer_reported": 1}
    assert got.rejections == {"split_in_window": 2} and why in caplog.text


@pytest.mark.asyncio
async def test_a_more_or_fewer_row_with_no_symbol_cannot_be_re_checked():
    """A CUSIP no lookup resolves (digit-first, the ISIN search answers nothing) is a symbol-less row in both
    quarters: its share change is counted, never re-checkable — both kinds go, with no lookup spent."""
    extracts = book(BERKSHIRE)
    extracts[(BERKSHIRE, 2026, 3)].append(xrow(BERKSHIRE, 3, "", "88160R101", 150_000, 9e6))
    extracts[(BERKSHIRE, 2026, 2)].append(xrow(BERKSHIRE, 2, "", "88160R101", 100_000, 6e6))
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")], extracts=extracts)
    acts = SplitActions()
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    (rec,) = got.records
    assert _more_or_fewer(rec) == [] and got.rejections == {"split_unchecked": 2}
    assert acts.looked_up == []


@pytest.mark.asyncio
async def test_more_rows_than_the_re_check_cap_show_no_more_or_fewer_shares(monkeypatch):
    fmp, sb = _ko_world(1_200_000, 84e6)                 # AAPL and KO: two more / fewer symbols
    monkeypatch.setattr(A, "THIRTEEN_F_MAX_SPLIT_CHECKS", 1)
    acts = SplitActions()
    got = await run("thirteen_f", fmp, sb=sb, actions=acts)
    assert _more_or_fewer(got.records[0]) == [] and got.rejections == {"split_unchecked": 2}
    assert acts.looked_up == []
    A.clear_memo()
    monkeypatch.setattr(A, "THIRTEEN_F_MAX_SPLIT_CHECKS", 2)                      # at the cap: checked, kept
    got = await run("thirteen_f", fmp, sb=sb)
    assert sorted(_more_or_fewer(got.records[0])) == ["AAPL", "KO"]


def _ctx13f(acts=None):
    return A._Ctx("thirteen_f", RUN, frozenset(), 5, time.monotonic() + 60.0,
                  A.NewsDeps(actions=acts if acts is not None else SplitActions()))


def _r(move, sym, prev=100.0, cusip=None):
    return {"move": move, "symbol": sym, "cusip": cusip or club_cusip(sym), "prev_shares": prev}


@pytest.mark.parametrize("rows, counts, expected, looked_up", [
    ([_r("increased", "aaa ")], {"increased": 1, "decreased": 0}, None, ["AAA"]),      # as the cache keys it
    ([], {"increased": 0, "decreased": 0}, None, []),
    ([_r("increased", "AAA")], {"increased": 2, "decreased": 0}, "split_unchecked", []),   # a counted row unlisted
    ([_r("increased", "AAA")], {"increased": 0, "decreased": 0}, "split_unchecked", []),   # a listed row uncounted
    ([_r("decreased", "AAA")], {"increased": 0}, None, []),                    # junk count: record_invalid follows
    ([_r("decreased", "AAA")], {"increased": 0, "decreased": True}, None, []),
    ([_r("decreased", "AAA")], {"increased": 0, "decreased": -1}, None, []),
    ([_r("decreased", "AAA")], None, None, []),
    ([_r("increased", None)], {"increased": 1, "decreased": 0}, "split_unchecked", []),
    ([_r("increased", "--")], {"increased": 1, "decreased": 0}, "split_unchecked", []),
    ([_r("increased", 7)], {"increased": 1, "decreased": 0}, "split_unchecked", []),
    ([_r("newly_reported", None), _r("increased", "AAA")], {"increased": 1, "decreased": 0}, None, ["AAA"]),
    ([_r("increased", "AAA"), _r("decreased", "AAA")], {"increased": 1, "decreased": 1}, None, ["AAA"]),
], ids=["checked", "nothing", "count_unlisted", "row_uncounted", "count_missing", "count_bool", "count_negative",
        "counts_junk", "no_symbol", "placeholder_symbol", "symbol_not_text", "new_row_needs_no_symbol",
        "one_lookup_per_symbol"])
@pytest.mark.asyncio
async def test_the_re_check_reads_exactly_the_rows_its_counts_count(rows, counts, expected, looked_up):
    acts = SplitActions()
    got = await A._split_window_problem(_ctx13f(acts), rows, counts=counts, prev_shares=dict, cik=BERKSHIRE,
                                        period="2026-Q3", prev_end=date(2026, 6, 30), period_end=date(2026, 9, 30))
    assert (got[0] if got else None) == expected
    assert acts.looked_up == looked_up                   # no lookup is spent on a refusal or a junk count


@pytest.mark.parametrize("window, prev, book, expected", [
    (None, 100.0, 100.0, "lookup_failed"),
    ("none_none", 100.0, 100.0, "lookup_failed"),
    ("clean_none", 100.0, 100.0, "lookup_failed"),          # the probe failed
    ("clean_int1", 100.0, 100.0, "lookup_failed"),          # a probe answer that is not a bool
    ("nan", 100.0, 100.0, "lookup_failed"),
    ("zero", 100.0, 100.0, "lookup_failed"),
    ("bool_ratio", 100.0, 100.0, "lookup_failed"),
    ("adjustment", 100.0, 100.0, "adjustment"),
    ("clean", 100.0, None, None),                           # no split: nothing to restate
    ("int_one", 100.0, None, None),
    ("split2", 200.0, 100.0, None),                         # restated by the build
    ("split2", 200.4, 100.0, None),                         # within half a share
    ("split2", 201.0, 100.0, "split_not_restated"),
    ("split2", 100.0, 100.0, "split_not_restated"),         # the raw count stands
    ("split2", 200.0, None, "split_not_restated"),          # its CUSIP is not on the previous book once
    ("split2", None, 100.0, "split_not_restated"),
    ("split_and_adjustment", 200.0, 100.0, "adjustment"),
])
def test_split_window_taint(window, prev, book, expected):
    from app.services.thirteen_f_splits import SplitWindow
    w = {None: None, "none_none": SplitWindow(None, None), "clean_none": SplitWindow(1.0, None),
         "clean_int1": SplitWindow(1.0, 1), "nan": SplitWindow(float("nan"), False), "zero": SplitWindow(0.0, False),
         "bool_ratio": SplitWindow(True, False), "adjustment": SplitWindow(1.0, True), "clean": SplitWindow(1.0, False),
         "int_one": SplitWindow(1, False), "split2": SplitWindow(2.0, False),
         "split_and_adjustment": SplitWindow(2.0, True)}[window]
    row = {"cusip": "191216100", "prev_shares": prev}
    books = {} if book is None else {"191216100": book}
    assert A._split_window_taint(w, row, books) == expected


@pytest.mark.parametrize("splits, built", [({}, 0), ({"BBB": [split_row("2026-08-14", 2, 1)]}, 1)],
                         ids=["no_split", "split"])
@pytest.mark.asyncio
async def test_the_previous_book_is_built_only_when_a_split_is_found(splits, built):
    """The registry path normalises the previous extract for the restatement proof — only when a split IS found
    in the quarter (never twice, and never on the common no-split path)."""
    calls = []
    rows = [_r("increased", "AAA", prev=200.0, cusip="037833100"), _r("decreased", "BBB", prev=200.0,
                                                                       cusip="191216100")]
    got = await A._split_window_problem(
        _ctx13f(SplitActions(splits=splits)), rows, counts={"increased": 1, "decreased": 1},
        prev_shares=lambda: calls.append(1) or {"191216100": 100.0}, cik=BERKSHIRE, period="2026-Q3",
        prev_end=date(2026, 6, 30), period_end=date(2026, 9, 30))
    assert got is None and len(calls) == built          # BBB's 2:1 split restated: 100 x 2 is its 200


def test_a_cusip_on_two_rows_of_the_previous_book_proves_no_restatement():
    rows = [{"cusip": "191216100", "shares": 100.0}, {"cusip": "191216100 ", "shares": 50.0},
            {"cusip": "037833100", "shares": 7.0}, {"cusip": "bad", "shares": 1.0}, {"cusip": "594918104", "shares": 0},
            "junk", {"cusip": "023135106", "shares": float("inf")}]
    assert A._shares_by_cusip(rows) == {"037833100": 7.0}
    assert A._shares_by_cusip(None) == {} and A._shares_by_cusip("rows") == {}


# ── F1 and F2 on the club path: the STORED change rows, the stored build status ──


def _club_with(stored_edit=None, *, acts=None, previous=None, company_over=None, changes=None):
    club, sb = club_world(company_over=company_over, changes=changes)
    filings = sb.tables["trillion_club_filings"]
    if previous is not None:
        filings[0]["holdings"] = previous
    if stored_edit is not None:
        stored_edit(filings[1])
    return club, sb, acts if acts is not None else SplitActions()


_CLUB_SPLIT_DROPPED = ([("CRWV", "newly_reported"), ("RXRX", "no_longer_reported")],
                       {"newly_reported": 3, "no_longer_reported": 1})


@pytest.mark.parametrize("status", ["degraded", None, "", "COMPLETE", 7], ids=["degraded", "none", "blank", "case", "int"])
@pytest.mark.asyncio
async def test_a_club_filers_degraded_stored_build_is_refused_whole(monkeypatch, caplog, status):
    """F2 and the owner-delegated decision of 2026-10-19: a club build whose stored status is not exactly "complete"
    refuses the WHOLE filer (`degraded_build`, once) — parity with the registry path's refusal. The row keeps no
    reason: a failed symbol lookup can split a CUSIP re-key into an exit plus an entry (a false "newly reported"),
    a failed split lookup arms only the 50% backstop, and a profiles_missing / unresolved_pending build is refused
    the same way (fail closed). Before any lookup, profile or previous-book read. Mutation-checked by hand
    (2026-10-19): with the F2 drop of the more / fewer kinds only, CRWV and RXRX were published (red)."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb, acts = _club_with(lambda row: row.update(build_status=status))
    fmp = FakeFMP(profiles=thirteen_f_profiles())
    got = await run("thirteen_f", fmp, club=club, sb=sb, actions=acts)
    assert got.records == () and got.rejections == {"degraded_build": 1}
    assert got.skip_reason == "thirteen_f_none_qualified"
    assert acts.looked_up == [] and fmp.calls["profiles"] == 0 and "not complete" in caplog.text
    assert "filer refused, no move shown (degraded_build)" in caplog.text
    assert sb.calls["trillion_club_filings"] == 1                     # the current row only: no previous book read


@pytest.mark.asyncio
async def test_a_club_filers_complete_stored_build_is_re_checked_and_kept(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, acts = _club_with()
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    assert ("AAPL", "increased") in [(m.company.symbol, m.move) for m in got.records[0].moves]
    assert sorted(acts.looked_up) == ["AAPL", "SOUN"]        # every more / fewer row, shown or not (SOUN is small)


@pytest.mark.asyncio
async def test_a_club_split_the_build_did_not_restate_drops_both_kinds(monkeypatch, caplog):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb, acts = _club_with(acts=SplitActions(splits={"AAPL": [split_row("2026-08-14", 2, 1)]}))
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    (rec,) = got.records
    assert ([(m.company.symbol, m.move) for m in rec.moves], dict(rec.counts)) == _CLUB_SPLIT_DROPPED
    assert got.rejections == {"etf_or_fund": 1, "move_not_routable": 1, "split_in_window": 2}
    assert "AAPL:split_not_restated" in caplog.text
    # Restated by the build — AAPL's stored previous count 500 times the 2:1 split is the row's 1,000 — it stays.
    A.clear_memo()
    aapl = {"cusip": club_cusip("AAPL"), "symbol": "AAPL", "shares": 500.0, "value": 1e5}
    club, sb, acts = _club_with(acts=SplitActions(splits={"AAPL": [split_row("2026-08-14", 2, 1)]}),
                                previous=[{"symbol": "RXRX", "value": 35e6}, aapl])
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    assert ("AAPL", "increased") in [(m.company.symbol, m.move) for m in got.records[0].moves]
    assert "split_in_window" not in got.rejections


@pytest.mark.asyncio
async def test_a_club_row_with_no_routable_symbol_is_re_checked_under_its_filed_symbol(monkeypatch):
    """The detail's change rows carry ROUTABLE symbols only: a more / fewer row of a holding that does not route
    (shown nowhere, but counted) is read from the stored row, under the symbol its 13F filed."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)

    def unroutable_soun(row):
        detail_row = next(r for r in row["changes"]["rows"] if r["symbol"] == "SOUN")
        detail_row["symbol"] = "SOUNF"                         # the filed symbol; the detail says None

    club, sb, acts = _club_with(unroutable_soun, acts=SplitActions(splits={"SOUNF": [split_row("2026-07-02", 1, 5)]}))
    next(ch for ch in club.details["nvidia"].changes if ch.symbol == "SOUN").symbol = None
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    assert _more_or_fewer(got.records[0]) == [] and got.rejections["split_in_window"] == 2     # AAPL dropped too
    assert "SOUNF" in acts.looked_up


@pytest.mark.parametrize("edit, why", [
    (lambda row: row["changes"]["counts"].update(increased=2), "not the detail's"),
    (lambda row: row["changes"]["counts"].update(increased=True), "not the detail's"),     # True == 1: refused
    (lambda row: row["changes"]["counts"].update(decreased=1.0), "not the detail's"),
    (lambda row: row["changes"].update(counts=None), "not the detail's"),
    (lambda row: row["changes"].update(rows="junk"), "unreadable"),
    (lambda row: row["changes"].update(comparison="gap"), "unreadable"),
    (lambda row: row.update(changes="not json"), "unreadable"),
    (lambda row: row["changes"].update(rows=[r for r in row["changes"]["rows"] if r["symbol"] != "SOUN"]),
     "are not what its counts count"),                     # a counted decrease the stored rows do not list
], ids=["counts_differ", "count_bool", "count_float", "counts_missing", "rows_junk", "not_a_comparison",
        "changes_garbage", "rows_short"])
@pytest.mark.asyncio
async def test_a_club_stored_row_that_cannot_vouch_for_the_counts_fails_closed(monkeypatch, caplog, edit, why):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb, acts = _club_with(edit)
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    assert ([(m.company.symbol, m.move) for m in got.records[0].moves], dict(got.records[0].counts)) == _CLUB_SPLIT_DROPPED
    assert got.rejections["split_unchecked"] == 2 and why in caplog.text and acts.looked_up == []


@pytest.mark.parametrize("status, kept", [("complete", True), ("degraded", False)])
@pytest.mark.asyncio
async def test_a_club_filing_without_a_more_or_fewer_row_still_needs_a_complete_build(monkeypatch, status, kept):
    """The owner-delegated decision of 2026-10-19: the build status vouches for EVERY move, not only the more / fewer
    kinds — a degraded build with only newly reported and exited moves is refused whole too (those are exactly the
    moves a failed symbol lookup can fake). Its current row is read for every club filing with a move."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    ch = [SimpleNamespace(symbol="CRWV", name="CoreWeave", change="newly_reported", shares=1_000_000.0,
                          prev_shares=None, value=150e6, newly_listed=True),
          SimpleNamespace(symbol="RXRX", name="Recursion", change="no_longer_reported", shares=None,
                          prev_shares=7_000_000.0, value=None, newly_listed=False)]
    counts = SimpleNamespace(newly_reported=1, increased=0, decreased=0, no_longer_reported=1)
    club, sb = club_world(changes=ch, company_over={"change_counts": counts})
    sb.tables["trillion_club_filings"][1]["build_status"] = status
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    if kept:
        assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "RXRX"]
        assert sb.calls["trillion_club_filings"] == 2 and got.rejections == {}     # current row + previous book
    else:
        assert got.records == () and got.rejections == {"degraded_build": 1}
        assert sb.calls["trillion_club_filings"] == 1


@pytest.mark.parametrize("error", [None, RuntimeError("supabase down")], ids=["row_missing", "read_fails"])
@pytest.mark.asyncio
async def test_a_club_filer_whose_current_build_cannot_be_read_is_refused(monkeypatch, caplog, error):
    """The owner-delegated decision of 2026-10-19: a stored current row that is missing or unreadable cannot show
    the build is complete, so the filer is refused (`filer_unavailable`, a WARNING), as for a missing previous book
    — never published with its status unknown (before, only the more / fewer kinds were dropped)."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    caplog.set_level(logging.WARNING, logger=A.__name__)
    club, sb = club_world()
    sb.tables["trillion_club_filings"] = sb.tables["trillion_club_filings"][:1]      # the previous book only
    if error is not None:
        sb.errors["trillion_club_filings"] = error
    fmp = FakeFMP(profiles=thirteen_f_profiles())
    got = await run("thirteen_f", fmp, club=club, sb=sb)
    assert got.records == () and got.rejections == {"filer_unavailable": 1}
    assert "filer refused, no move shown" in caplog.text and fmp.calls["profiles"] == 0


@pytest.mark.asyncio
async def test_f3_a_capped_suspect_the_stored_books_miss_is_still_looked_up(monkeypatch):
    """F3 (closed by F1): the stored re-derivation of the split cap reads the STORED previous book, which can lag
    the live one the build used (a 13F-HR/A re-hashed weekly) — here it says nothing was trimmed, while ZBJ's raw
    +50% share diff was never checked by the build. ZBJ is a more / fewer row, so the re-check looks it up itself;
    its split in the quarter was not restated (no previous-book entry proves it) → both kinds go."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    cur, prev = _club_split_books(25)
    assert A._builder.stored_split_unchecked(cur, prev) == frozenset()       # the re-derivation sees no cap
    zbj = SimpleNamespace(symbol="ZBJ", name="Zbj Robotics", change="increased", shares=150_000.0,
                          prev_shares=100_000.0, value=10e6, newly_listed=False)
    club, sb = club_world(company_over={"position_count": 30, "change_counts": SimpleNamespace(
        newly_reported=3, increased=2, decreased=1, no_longer_reported=1, unchanged=3, corporate_action=1)})
    detail = club.details["nvidia"]
    detail.changes.append(zbj)
    sb.tables["trillion_club_filings"] = [
        {"cik": NVIDIA_CIK, "period": "2026-Q2", "holdings": prev},
        stored_current(NVIDIA_CIK, "2026-Q3", detail.changes, detail.company.change_counts, holdings=cur)]
    acts = SplitActions(splits={"ZBJ": [split_row("2026-08-14", 3, 2)]})
    got = await run("thirteen_f", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb, actions=acts)
    assert _more_or_fewer(got.records[0]) == [] and got.rejections["split_in_window"] == 3     # AAPL, SOUN, ZBJ
    assert "ZBJ" in acts.looked_up


# ── live preview F2: the successor's hand-over quarter, with the real shapes (FMP extracts read 2026-10-16) ──

#: PERSHING SQUARE INC.'s 2026-Q1 book: Howard Hughes alone (its own holding before the hand-over).
_PSI_Q1 = [("HHH", "44267T102", 9_000_000, 9_000_000 * 70.0)]
#: Its 2026-Q2 book, the first full one: 14 positions (Howard Hughes 18,852,064 + 9,000,000 consolidated).
_PSI_Q2 = [("BN", "11271J107", 57_480_000), ("UBER", "90353T100", 34_330_000), ("HHH", "44267T102", 27_852_064),
           ("QSR", "76131D103", 25_820_000), ("HTZ", "42806J700", 15_241_127), ("NFLX", "64110L106", 1_200_000),
           ("AMZN", "023135106", 11_451_981), ("MSFT", "594918104", 5_654_078), ("SEG", "812215200", 5_023_780),
           ("PSUS", "71531C108", 4_000_000), ("V", "92826C839", 3_000_000), ("META", "30303M102", 2_660_861),
           ("SPGI", "78409V104", 1_500_000), ("MA", "57636Q104", 1_400_000)]
#: The OLD CIK's 2026-Q1 book (eleven positions) — never read: the predecessor is not the filer the post names.
_PSCM_Q1 = [("BN", "11271J107", 59_697_208), ("UBER", "90353T100", 29_958_771), ("QSR", "76131D103", 22_645_483),
            ("HHH", "44267T102", 18_852_064), ("HTZ", "42806J700", 15_241_127), ("AMZN", "023135106", 11_451_981),
            ("MSFT", "594918104", 5_654_078), ("SEG", "812215200", 5_023_780), ("META", "30303M102", 2_660_861),
            ("GOOG", "02079K107", 3_000_000), ("GOOGL", "02079K305", 3_000_000)]


def _pershing_handover_world():
    q2 = [xrow(PERSHING_FILER, 2, s, c, n, n * 70.0) for s, c, n in _PSI_Q2]
    q1 = [xrow(PERSHING_FILER, 1, s, c, n, v) for s, c, n, v in _PSI_Q1]
    old = [xrow(PERSHING, 1, s, c, n, n * 70.0) for s, c, n in _PSCM_Q1]
    dates_q2 = [{"year": 2026, "quarter": 2, "date": "2026-06-30"}, {"year": 2026, "quarter": 1, "date": "2026-03-31"}]
    fmp, sb = registry_world(
        [whale(PERSHING, "Pershing Square Capital")],
        extracts={(PERSHING_FILER, 2026, 2): q2, (PERSHING_FILER, 2026, 1): q1, (PERSHING, 2026, 1): old},
        dates={PERSHING_FILER: dates_q2, PERSHING: [{"year": 2026, "quarter": 1, "date": "2026-03-31"}]})
    fmp.profiles.update({s: prof(s, f"{s} Holdings Inc.", exchange="NYSE") for s, _c, _n in _PSI_Q2 + _PSCM_Q1})
    return fmp, sb


@pytest.mark.asyncio
async def test_the_successors_hand_over_quarter_is_never_compared(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=A.__name__)
    fmp, sb = _pershing_handover_world()
    got = await run("thirteen_f", fmp, sb=sb, run_date=date(2026, 8, 18))          # 13F season: 2026-Q2
    assert got.records == () and got.rejections == {"successor_transition": 1}
    assert fmp.calls == Counter()                                                  # refused before any FMP call
    assert "hand-over quarter (first full book 2026-Q2)" in caplog.text
    # Anti-vacuity: let the hand-over through and the same world composes the false post the preview found.
    A.clear_memo()
    monkeypatch.setitem(R.THIRTEEN_F_SUCCESSOR_SINCE, PERSHING, "2026-Q1")
    got = await run("thirteen_f", fmp, sb=sb, run_date=date(2026, 8, 18))
    (rec,) = got.records
    assert dict(rec.counts)["newly_reported"] == 13 and ("HHH", "increased") in [
        (m.company.symbol, m.move) for m in rec.moves]
    assert PERSHING not in fmp.dates_ciks and all(k[0] == PERSHING_FILER for k in fmp.extract_keys)


@pytest.mark.asyncio
async def test_the_quarter_after_the_hand_over_compares_two_full_books():
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital")])       # 2026-Q3 against 2026-Q2
    got = await run("thirteen_f", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [PERSHING_FILER] and "successor_transition" not in got.rejections


@pytest.mark.asyncio
async def test_a_successor_with_no_first_full_book_is_refused_every_quarter(monkeypatch):
    monkeypatch.delitem(R.THIRTEEN_F_SUCCESSOR_SINCE, PERSHING)
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections == {"successor_transition": 1} and fmp.calls == Counter()


@pytest.mark.parametrize("status, warned", [("inactive", True), ("dormant", True), ("", False)])
@pytest.mark.asyncio
async def test_a_curated_inactive_successor_is_skipped_loudly_never_silently(caplog, status, warned):
    """F4: the old CIK stops filing once a successor files its holdings — exactly when the registry sync suggests
    a status. The row is still skipped (the owner's curation rules), but with a WARNING naming both CIKs."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = registry_world([whale(PERSHING, "Pershing Square Capital", lifecycle_status=status),
                              whale(SCION, "Scion Asset Management", lifecycle_status="inactive")])
    got = await run("thirteen_f", fmp, sb=sb)
    msgs = [r.getMessage() for r in caplog.records if "is curated" in r.getMessage()]
    assert len(msgs) == (1 if warned else 0)                                        # Scion: no successor, no noise
    if warned:
        assert PERSHING in msgs[0] and PERSHING_FILER in msgs[0] and got.records == ()


# ══ review of 2026-10-16: earnings — one record per issuer, the printed surprise, the statement reads, the EPS
# basis; Form 4 — the members-elect window over a run's publishing days ══════════════════════════════════════
#
# ER-3 / live preview F3: earnings keyed reports by SYMBOL — two listings of one company (GOOG / GOOGL) were two
# records under two ledger keys, and a NASDAQ preferred line FMP profiles under the issuer's plain name (WAFDP as
# "WaFd") passed the gate with its own calendar row (0.76 against the common's 0.85). ER-2: a failed statement
# read was counted `non_usd_reporter`, so an FMP outage read as "nothing qualified". Live preview F1: Prologis'
# $1.60 Core-FFO-scale actual beside a $0.78 GAAP-scale estimate (GAAP $0.86) composed a false post. ME-1: the
# members-elect window was read on the run date only, while a run's posts can go out two days later.


def _listing(sym, name, *, cap, cik, exchange="NASDAQ", **kw):
    return prof(sym, name, exchange=exchange, cap=cap, cik=cik, **kw)


def _alphabet_world(goog=(2.87, 2.60), googl=(2.87, 2.60), *, extra_rows=(), extra_profiles=None):
    rows = [erow("GOOG", "2027-01-12", *goog, 96.4e9, 95.0e9), erow("GOOGL", "2027-01-12", *googl, 96.4e9, 95.0e9),
            *extra_rows]
    profiles = {"GOOG": _listing("GOOG", "Alphabet Inc.", cap=3.0e12, cik="0001652044"),
                "GOOGL": _listing("GOOGL", "Alphabet Inc.", cap=3.0e12, cik="0001652044"), **(extra_profiles or {})}
    return FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles)


@pytest.mark.asyncio
async def test_two_listings_of_one_company_are_one_record():
    """GOOG and GOOGL with one report: ONE record (the root GOOG), the twin dropped before any profile and counted
    `share_class_overlap` — never two posts of one report under two ledger keys. Two listings that report
    DIFFERENT figures refuse the company: which one is the report is a guess. Mutation-checked by hand
    (2026-10-16): without `_drop_twin_lines` both listings were records (the ER-3 probe's shape)."""
    fmp = _alphabet_world()
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["GOOG"]
    assert got.rejections == {"share_class_overlap": 1}
    assert "GOOGL" not in [s for b in fmp.profile_requests for s in b] and fmp.statement_calls == [("GOOG", 4)]
    A.clear_memo()
    got = await erun(_alphabet_world(googl=(2.95, 2.60)))
    assert got.records == () and got.rejections == {"record_invalid": 1, "share_class_overlap": 1}
    assert got.skip_reason == "earnings_none_qualified"


@pytest.mark.parametrize("posted", ["news:earnings:GOOG:2027-01-12", "news:earnings:GOOGL:2027-01-12",
                                    "news:earnings:GOOG:2027-01-08"])
@pytest.mark.asyncio
async def test_a_posted_listing_makes_its_twin_already_posted(posted):
    """One report, one post: with either listing's key posted in this window (any day of it), the other listing is
    `already_posted` — the Wed/Thu/Fri earnings days of Batch 3 would otherwise post Alphabet twice."""
    got = await erun(_alphabet_world(), exclude={posted})
    assert got.records == () and got.rejections == {"already_posted": 2}


def _class_world(rows, profiles):
    return FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles)


@pytest.mark.asyncio
async def test_class_shares_are_one_issuer_after_the_profiles():
    """Class letters (A / B / C / J / K) and dash classes are never twins of a root: both listings are profiled,
    and the issuer — the profile CIK, else the display name — keeps ONE: the shortest symbol, then the first
    (never the larger FMP market cap). Listings whose figures differ refuse the issuer; unrelated companies are
    untouched. Mutation-checked by hand (2026-10-16): without `_one_listing_per_issuer` LBRDA and LBRDK were
    two records."""
    rows = [erow("LBRDA", "2027-01-12", 1.60, 1.40, 1.05e9, 1.0e9), erow("LBRDK", "2027-01-12", 1.60, 1.40, 1.05e9, 1.0e9),
            erow("GS", "2027-01-12", 17.01, 12.86, 18.7e9, 16.8e9)]
    profiles = {"LBRDA": _listing("LBRDA", "Liberty Broadband Corporation", cap=9e9, cik="0001611983"),
                "LBRDK": _listing("LBRDK", "Liberty Broadband Corporation", cap=11e9, cik="0001611983"),
                "GS": _listing("GS", "The Goldman Sachs Group, Inc.", exchange="NYSE", cap=150e9, cik="0000886982")}
    fmp = _class_world(rows, profiles)
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["GS", "LBRDA"]           # LBRDK's larger cap: not kept
    assert got.rejections == {"share_class_overlap": 1}
    assert {"LBRDA", "LBRDK"} <= {s for b in fmp.profile_requests for s in b}
    assert sorted(s for s, _l in fmp.statement_calls) == ["GS", "LBRDA"]
    # BRK-A / BRK-B report per-class EPS: different figures → the issuer is refused, GS stays
    A.clear_memo()
    rows = [erow("BRK-A", "2027-01-12", 7_000.0, 6_000.0, 95e9, 90e9), erow("BRK-B", "2027-01-12", 4.67, 4.0, 95e9, 90e9),
            erow("GS", "2027-01-12", 17.01, 12.86, 18.7e9, 16.8e9)]
    profiles = {"BRK-A": _listing("BRK-A", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983"),
                "BRK-B": _listing("BRK-B", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983"),
                "GS": profiles["GS"]}
    got = await erun(_class_world(rows, profiles))
    assert [r.company.symbol for r in got.records] == ["GS"]
    assert got.rejections == {"record_invalid": 1, "share_class_overlap": 1}


@pytest.mark.asyncio
async def test_one_display_name_or_one_cik_is_one_issuer():
    """The issuer identity is the CIK OR the display name (as `_one_row_per_issuer`): two listings sharing either
    one are one company, two sharing neither are two."""
    def world(cik_b, name_b):
        rows = [erow("ACMA", "2027-01-12", 1.5, 1.0), erow("ACMB", "2027-01-12", 1.5, 1.0)]
        return _class_world(rows, {"ACMA": _listing("ACMA", "Acme Corp.", cap=9e9, cik="0000000101"),
                                   "ACMB": _listing("ACMB", name_b, cap=8e9, cik=cik_b)})

    for cik_b, name_b, kept in (("0000000101", "Acme Corp.", ["ACMA"]), ("0000000101", "Acme Holdings", ["ACMA"]),
                                ("0000000202", "Acme Corp.", ["ACMA"]), ("0000000202", "Acmb Holdings",
                                                                         ["ACMA", "ACMB"])):
        A.clear_memo()
        got = await erun(world(cik_b, name_b))
        assert [r.company.symbol for r in got.records] == kept, (cik_b, name_b)
        assert got.rejections == ({} if len(kept) == 2 else {"share_class_overlap": 1})


@pytest.mark.asyncio
async def test_a_posted_class_share_makes_its_sibling_already_posted(caplog):
    """BRK-A posted this window → BRK-B (same CIK) is `already_posted`: the posted listing is profiled beside the
    candidates for its issuer identity. A posted listing without a profile cannot be matched — logged at WARNING
    (the sibling then stays a candidate: a second post of one report is the risk, never a wrong figure)."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    rows = [erow("BRK-A", "2027-01-12", 7_000.0, 6_000.0, 95e9, 90e9), erow("BRK-B", "2027-01-12", 4.67, 4.0, 95e9, 90e9)]
    profiles = {"BRK-A": _listing("BRK-A", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983"),
                "BRK-B": _listing("BRK-B", "Berkshire Hathaway Inc.", exchange="NYSE", cap=1e12, cik="0001067983")}
    fmp = _class_world(rows, profiles)
    got = await erun(fmp, exclude={"news:earnings:BRK-A:2027-01-12"})
    assert got.records == () and got.rejections == {"already_posted": 2}
    assert fmp.profile_requests[0] == ["BRK-B", "BRK-A"] and fmp.statement_calls == []
    A.clear_memo()
    fmp = _class_world(rows, {"BRK-B": profiles["BRK-B"]})
    got = await erun(fmp, exclude={"news:earnings:BRK-A:2027-01-12"})
    assert [r.company.symbol for r in got.records] == ["BRK-B"] and got.rejections == {"already_posted": 1}
    assert "posted listing BRK-A has no usable profile" in caplog.text


@pytest.mark.asyncio
async def test_a_nasdaq_preferred_line_is_never_the_company():
    """WaFd (live, 2026-10-15): WAFDP's row read 0.76 against a 0.843 estimate (a 9.8% "miss") beside the common
    WAFD's 0.85 (0.8%: no clear surprise) — and FMP profiles WAFDP as "WaFd, Inc." on NASDAQ. With its root in the
    window the twin is dropped before any profile (`share_class_overlap`); alone, the profile gate refuses its
    preferred shape (`non_common_listing`); with the same figures as its root, the root is the one record."""
    wafd = _listing("WAFD", "WaFd, Inc.", cap=2.4e9, cik="0000936528")
    wafdp = _listing("WAFDP", "WaFd, Inc.", cap=2.188e9 * 1.2, cik="0000936528")
    rows = [erow("WAFD", "2027-01-12", 0.85, 0.843, 0.2204e9, 0.2150e9), erow("WAFDP", "2027-01-12", 0.76, 0.843,
                                                                              0.2204e9, 0.2150e9)]
    fmp = _class_world(rows, {"WAFD": wafd, "WAFDP": wafdp})
    got = await erun(fmp)
    assert got.records == () and got.rejections == {"surprise_unclear": 1, "share_class_overlap": 1}
    assert fmp.profile_requests == []                                           # nothing reached a profile
    A.clear_memo()
    fmp = _class_world(rows[1:], {"WAFDP": wafdp})                              # the preferred line alone
    got = await erun(fmp)
    assert got.records == () and got.rejections == {"non_common_listing": 1} and fmp.statement_calls == []
    A.clear_memo()
    acme = [erow("ACME", "2027-01-12", 0.95, 0.843, 2.1e9, 2.05e9), erow("ACMEP", "2027-01-12", 0.95, 0.843, 2.1e9,
                                                                          2.05e9)]
    got = await erun(_class_world(acme, {"ACME": _listing("ACME", "Acme, Inc.", cap=5e9, cik="0000000303"),
                                         "ACMEP": _listing("ACMEP", "Acme, Inc.", cap=6e9, cik="0000000303")}))
    assert [(r.company.symbol, r.eps_actual) for r in got.records] == [("ACME", 0.95)]
    assert got.rejections == {"share_class_overlap": 1}


@pytest.mark.asyncio
async def test_a_twin_that_outranks_its_root_never_replaces_it():
    """FMP prices a preferred line on the common's share count, so several outrank their own common (HBANL $34.1B
    vs HBAN $31.5B, FITBI $50.6B vs FITB $46.5B). The ROOT is kept, never the larger cap; a twin whose row reports
    other figures refuses the root (FITB 0.83 beside FITBI 0.84); with the root refused for its own row, the twin
    never passes alone (FITB's 1% gap beside FITBI's 9.8%)."""
    def world(root_row, twin_row, *more):
        profiles = {"HBAN": _listing("HBAN", "Huntington Bancshares Incorporated", cap=31.53e9, cik="0000049196"),
                    "HBANL": _listing("HBANL", "Huntington Bancshares Incorporated", cap=34.12e9, cik="0000049196"),
                    "FITB": _listing("FITB", "Fifth Third Bancorp", cap=46.52e9, cik="0000035527"),
                    "FITBI": _listing("FITBI", "Fifth Third Bancorp", cap=50.55e9, cik="0000035527"),
                    "FITBO": _listing("FITBO", "Fifth Third Bancorp", cap=48e9, cik="0000035527")}
        return _class_world([root_row, twin_row, *more], profiles)

    got = await erun(world(erow("HBAN", "2027-01-12", 0.40, 0.36, 2.0e9, 1.95e9),
                           erow("HBANL", "2027-01-12", 0.40, 0.36, 2.0e9, 1.95e9)))
    assert [r.company.symbol for r in got.records] == ["HBAN"] and got.rejections == {"share_class_overlap": 1}
    A.clear_memo()
    got = await erun(world(erow("FITB", "2027-01-12", 0.90, 0.82, 3.26e9, 3.2e9),
                           erow("FITBI", "2027-01-12", 0.91, 0.82, 3.145e9, 3.2e9),
                           erow("FITBO", "2027-01-12", 0.90, 0.82, 3.37e9, 3.2e9)))
    assert got.records == () and got.rejections == {"record_invalid": 1, "share_class_overlap": 2}
    A.clear_memo()
    fmp = world(erow("FITB", "2027-01-12", 0.83, 0.82, 3.26e9, 3.2e9), erow("FITBI", "2027-01-12", 0.90, 0.82, 3.26e9,
                                                                             3.2e9))
    got = await erun(fmp)
    assert got.records == () and got.rejections == {"surprise_unclear": 1, "share_class_overlap": 1}
    assert fmp.profile_requests == []


def test_twin_families():
    listed = {"GOOG", "GOOGL", "WAFD", "WAFDP", "LBRDA", "LBRDK", "LBRD", "FITB", "FITBI", "BRK-A", "BRK-B", "AAPL",
              "SNAP", "SNAPP", "ABCDE",
              # the re-review of 2026-10-19: an OTC foreign line (F / Y) is never a twin — often another company
              "CMPR", "CMPRF", "AXIA", "AXIAY", "TWOD", "TWODF", "TWODY"}
    assert A._twin_families(listed) == {"GOOG": {"GOOGL"}, "WAFD": {"WAFDP"}, "FITB": {"FITBI"}, "SNAP": {"SNAPP"}}
    assert A._twin_families(set()) == {} and A.EARNINGS_CLASS_LETTERS == frozenset("ABCJK")
    # a curated class line makes its root a family's root even when the root has no row in the window (EME-3)
    assert A._twin_families({"GOOGL", "GOOGM"}) == {"GOOG": {"GOOGL", "GOOGM"}}
    assert A._twin_families({"GOOGL"}) == {"GOOG": {"GOOGL"}} and A._twin_families({"HBANL"}) == {}
    assert A.EARNINGS_FOREIGN_LETTERS == frozenset("FY") and A.EARNINGS_CLASS_LINES == frozenset({"GOOGL"})


# ── the re-review of 2026-10-19 (B1R-ER3, EME-3): one issuer, per-ticker consensus figures ──
#
# FMP keys its analyst consensus per TICKER: GOOG's and GOOGL's estimates differ every quarter (live: Feb 2026 2.63 vs
# 2.57; Apr 2.68 vs 2.64; Jul $116.5285B vs $116.5325B revenue estimates; Oct 3.02 vs 2.97 ahead of the report),
# while every Alphabet line carries the SAME actuals. ER-3 refused the issuer on ANY differing figure — Alphabet, the
# largest reporter of its week, was `record_invalid` every quarter. Now only two ACTUALS refuse it, and every
# listing's estimate must leave the surprise clear and pointing the same way.

def _alphabet_live(goog, googl, *more, day="2027-01-12"):
    rows = [erow("GOOG", day, *goog), erow("GOOGL", day, *googl), *more]
    profiles = {s: _listing(s, "Alphabet Inc.", cap=4.16e12, cik="0001652044") for s in ("GOOG", "GOOGL", "GOOGM",
                                                                                        "GOOGN")}
    return FakeFMP2b(calendar={day: rows}, profiles=profiles)


@pytest.mark.parametrize("goog, googl, notes", [
    ((2.82, 2.63, 113.828e9, 111.3184e9), (2.82, 2.57, 113.828e9, 111.3324e9),
     (2.82, 2.63, 113.828e9, 111.3324e9)),                                        # Feb 4, 2026 (live)
    ((5.11, 2.68, 109.896e9, 106.961e9), (5.11, 2.64, 109.896e9, 106.9761e9),
     (5.11, 2.68, 109.896e9, 106.9761e9)),                                        # Apr 29, 2026 (live)
    ((9.11, 2.87, 119.796e9, 116.5285e9), (9.11, 2.87, 119.796e9, 116.5325e9),
     (9.11, 2.88, 119.796e9, 116.3761e9)),                                        # Jul 22, 2026 (live)
], ids=["feb", "apr", "jul"])
@pytest.mark.asyncio
async def test_alphabets_live_rows_are_one_record_with_googs_own_figures(goog, googl, notes):
    """Live-shaped rows (twin_probe, 2026-10-19): GOOG, GOOGL, GOOGM and GOOGN — one actual pair, three consensus
    figures. ONE record, the root GOOG with its OWN row's estimate; the other lines `share_class_overlap`.
    Mutation-checked by hand (2026-10-19): with `_family_problem` comparing every figure again, all three red."""
    fmp = _alphabet_live(goog, googl, erow("GOOGM", "2027-01-12", *notes), erow("GOOGN", "2027-01-12", *notes))
    got = await erun(fmp)
    assert [(r.company.symbol, r.eps_actual, r.eps_estimate, r.revenue_estimate) for r in got.records] == [
        ("GOOG", goog[0], goog[1], goog[3])]
    assert got.rejections == {"share_class_overlap": 3}


@pytest.mark.parametrize("googl, rejections", [
    ((2.87, 2.80), {"surprise_unclear": 2}),                       # GOOGL's consensus: a 2.5% gap — no clear beat
    ((2.87, 3.10), {"surprise_unclear": 1, "share_class_overlap": 1}),     # a clear MISS by GOOGL's consensus
    ((2.95, 2.60), {"record_invalid": 1, "share_class_overlap": 1}),       # two actuals: which is the result
], ids=["unclear_by_googl", "opposite_by_googl", "two_actuals"])
@pytest.mark.asyncio
async def test_a_listing_whose_consensus_does_not_vouch_for_the_surprise_refuses_the_issuer(googl, rejections):
    """GOOG's 2.87 vs 2.60 (+10%) is printed — but a surprise one listing's consensus calls unclear, or a miss, is no
    clear surprise (`surprise_unclear`); two different ACTUALS stay `record_invalid` (FITB 0.83 beside FITBI 0.84).
    Mutation-checked by hand (2026-10-19): the estimate loop of `_family_problem` removed turned the first two red."""
    got = await erun(_alphabet_live((2.87, 2.60, 96.4e9, 95.0e9), (*googl, 96.4e9, 95.0e9)))
    assert got.records == () and got.rejections == rejections


@pytest.mark.asyncio
async def test_googl_stands_for_alphabet_when_goog_has_no_reported_row():
    """EME-3: FMP filled GOOGL's actual before GOOG's — the twin rule dropped GOOGL and GOOG was no candidate, so the
    week's largest reporter was lost. GOOGL is a curated class line (`A.EARNINGS_CLASS_LINES`): with no REPORTED GOOG
    row in the window (unreported, or not listed that day) it stands for the issuer; the family's other reported rows
    (GOOGM) still vouch for it, and a preferred-shaped line is still dropped. Mutation-checked by hand (2026-10-19):
    without the stand-in the record list was empty (red)."""
    googm = erow("GOOGM", "2027-01-12", 2.87, 2.60, 96.4e9, 95.0e9)
    for goog_row in (erow("GOOG", "2027-01-12", None, 2.60, None, 95.0e9),       # listed, not reported yet
                     erow("GOOG", "2027-01-09", None, 2.61)):                   # a stale projected row only
        A.clear_memo()
        fmp = _alphabet_live((2.87, 2.60), (2.87, 2.60, 96.4e9, 95.0e9), googm)
        fmp.calendar["2027-01-12"] = [r for r in fmp.calendar["2027-01-12"] if r["symbol"] != "GOOG"]
        fmp.calendar.setdefault(goog_row["date"], []).append(goog_row)
        got = await erun(fmp)
        assert [(r.company.symbol, r.company.name) for r in got.records] == [("GOOGL", "Alphabet")]
        assert got.rejections == {"share_class_overlap": 1}                       # GOOGM
    # … never when GOOG's own reported row was refused: GOOGL's consensus would decide what GOOG's did not
    A.clear_memo()
    got = await erun(_alphabet_live((2.87, 2.80, 96.4e9, 95.0e9), (2.87, 2.60, 96.4e9, 95.0e9)))
    assert got.records == () and got.rejections == {"surprise_unclear": 1, "share_class_overlap": 1}
    # … and a stand-in is checked like a root: GOOGM's other actual refuses it
    A.clear_memo()
    fmp = _alphabet_live((2.87, 2.60), (2.87, 2.60, 96.4e9, 95.0e9),
                         erow("GOOGM", "2027-01-12", 2.91, 2.60, 96.4e9, 95.0e9))
    fmp.calendar["2027-01-12"] = [r for r in fmp.calendar["2027-01-12"] if r["symbol"] != "GOOG"]
    got = await erun(fmp)
    assert got.records == () and got.rejections == {"record_invalid": 1, "share_class_overlap": 1}


@pytest.mark.asyncio
async def test_an_otc_foreign_line_is_never_the_twin_that_refuses_its_root():
    """Live (2026-04 / 2026-07): CMPRF is Gentera, an OTC line, beside Cimpress' CMPR — another company with other
    figures, and the twin rule refused CMPR `record_invalid`. An F / Y line now stands on its own: the profile gate
    refuses it (OTC), and CMPR is a record. Mutation-checked by hand (2026-10-19): with F / Y twins again, CMPR was
    refused (red)."""
    rows = [erow("CMPR", "2027-01-12", 0.97, 0.80, 0.945e9, 0.92e9), erow("CMPRF", "2027-01-12", 0.55, 0.45,
                                                                         0.886e9, 0.86e9)]
    profiles = {"CMPR": _listing("CMPR", "Cimpress plc", cap=2.4e9, cik="0001262976"),
                "CMPRF": _listing("CMPRF", "Gentera, S.A.B. de C.V.", exchange="OTC", cap=3e9, cik=None)}
    got = await erun(FakeFMP2b(calendar={"2027-01-12": rows}, profiles=profiles))
    assert [r.company.symbol for r in got.records] == ["CMPR"]
    assert got.rejections == {"not_major_exchange": 1}


@pytest.mark.parametrize("lbrdk, kept, rejections", [
    ((1.60, 1.42), ["LBRDA"], {"share_class_overlap": 1}),                   # another consensus, same result: kept
    ((1.60, 1.70), [], {"surprise_unclear": 1, "share_class_overlap": 1}),   # a clear miss by LBRDK's consensus
    ((1.62, 1.40), [], {"record_invalid": 1, "share_class_overlap": 1}),     # two actuals
], ids=["consensus_differs", "opposite", "two_actuals"])
@pytest.mark.asyncio
async def test_class_shares_after_the_profiles_compare_actuals_and_every_estimate(lbrdk, kept, rejections):
    """The issuer dedupe after the profiles (`_one_listing_per_issuer`) runs the same family check over the listings
    it groups: the root LBRDA's own row is published when every grouped listing's consensus vouches for its surprise.
    Recorded residual: a class listing refused BEFORE the profiles (its own row no clear surprise) is not in the
    group, so it cannot refuse its sibling — only a twin family is formed before the profiles."""
    rows = [erow("LBRDA", "2027-01-12", 1.60, 1.40, 1.05e9, 1.0e9), erow("LBRDK", "2027-01-12", *lbrdk, 1.05e9, 1.0e9)]
    profiles = {"LBRDA": _listing("LBRDA", "Liberty Broadband Corporation", cap=9e9, cik="0001611983"),
                "LBRDK": _listing("LBRDK", "Liberty Broadband Corporation", cap=11e9, cik="0001611983")}
    got = await erun(_class_world(rows, profiles))
    assert [(r.company.symbol, r.eps_estimate) for r in got.records] == [(s, 1.40) for s in kept]
    assert got.rejections == rejections


def _all_failing(answer):
    return {s: answer for s in ("AAA", "BBB", "III")}


@pytest.mark.parametrize("answer", [FMPRateLimitException("429"), FMPUnavailableException("503"), [], [7]],
                         ids=["rate_limited", "unavailable", "empty", "no_row"])
@pytest.mark.asyncio
async def test_every_statement_read_failing_is_unavailable_never_nothing_qualified(answer, caplog):
    """ER-2 (the review's own probe): FMP answering 429 to every quarterly-statement read of a Thursday is an
    OUTAGE — `earnings_statements_unavailable` (the chain falls back as "unavailable", the owner's series note
    names it first) — never "nothing qualified" with a count of refused reports. Nothing is memoized: the next
    call reads again and keeps the reports. Mutation-checked by hand (2026-10-16): without the all-failed raise
    the call answered `earnings_none_qualified` with {"statement_unavailable": 3}."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles(), currency=_all_failing(answer))
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await erun(fmp)
    assert err.value.reason == "earnings_statements_unavailable"
    assert "stage=earnings_statement; all 3 statement read(s) failed" in err.value.detail
    assert sorted(s for s, _l in fmp.statement_calls) == ["AAA", "BBB", "III"]
    assert "quarterly statement unreadable — refused as statement_unavailable" in caplog.text
    fmp.currency = {}
    assert [r.company.symbol for r in (await erun(fmp)).records] == ["BBB", "III", "AAA"]


@pytest.mark.asyncio
async def test_a_failed_read_beside_an_answered_one_is_one_refused_report():
    """Only ALL failing is an outage: one failed read beside an answered one is that report's refusal
    (`statement_unavailable`), whether the answered read is kept or refused (EUR)."""
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles(),
                    currency={"BBB": FMPRateLimitException("429"), "III": FMPRateLimitException("429")})
    got = await erun(fmp)
    assert [r.company.symbol for r in got.records] == ["AAA"] and got.rejections["statement_unavailable"] == 2
    A.clear_memo()
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles(),
                    currency={"BBB": FMPRateLimitException("429"), "III": FMPRateLimitException("429"), "AAA": "EUR"})
    got = await erun(fmp)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"
    assert got.rejections["statement_unavailable"] == 2 and got.rejections["non_usd_reporter"] == 1


@pytest.mark.parametrize("a, e", [(0.095, 0.10), (0.1449, 0.1376), (-0.095, -0.10), (0.3049, 0.2895)])
@pytest.mark.asyncio
async def test_a_surprise_the_post_cannot_show_is_refused(a, e):
    """ER-1: clear as the feed wrote it, not as the post prints it ("$0.10 vs $0.10", "$0.30 vs $0.29") —
    refused `surprise_unclear` before any profile."""
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]}, profiles=earnings_profiles())
    got = await erun(fmp)
    assert got.records == () and got.rejections == {"surprise_unclear": 1} and fmp.calls["profiles"] == 0


@pytest.mark.parametrize("with_profile", [False, True], ids=["no_profile", "profiled"])
@pytest.mark.asyncio
async def test_an_absurd_eps_row_never_sinks_the_earnings_series(caplog, with_profile):
    """Hardening of the ER-1 printed-figure gate (2026-10-16): `R.clear_surprise` quantized in the default 28-digit
    Decimal context, so ONE calendar row with an EPS of 1e26 or more raised `decimal.InvalidOperation` — and
    `candidates()` turns any exception into ``internal_error`` for the WHOLE series: the week's real reports were
    lost with it. The gate now computes exactly (`R.EPS_DECIMAL_PRECISION`); the junk row goes on to the other
    gates (unprofiled: ``profile_missing``; profiled: the read stage's gates run on it too, and the template is
    what refuses a figure it cannot print) and the week keeps BBB, III and AAA. Mutation-checked by hand
    (2026-10-16): with ``ctx.prec = 28`` the call raised ``internal_error`` (red)."""
    caplog.set_level(logging.ERROR, logger=A.__name__)
    cal = earnings_calendar()
    cal["2027-01-12"] = cal["2027-01-12"] + [erow("ZZZ", "2027-01-12", 1e30, 9e29),
                                             erow("YYY", "2027-01-12", 1.7976931348623157e308, 1e308)]
    profiles = earnings_profiles()
    if with_profile:
        profiles["ZZZ"] = prof("ZZZ", "Zzz Industries Inc.", cap=4e9)
    got = await erun(FakeFMP2b(calendar=cal, profiles=profiles))
    assert [r.company.symbol for r in got.records][:3] == ["BBB", "III", "AAA"]
    # since the owner-delegated EPS ceiling (2026-10-19) both junk rows stop at the row gate, profile or none
    assert got.rejections["eps_implausible"] == 2 and "profile_missing" not in got.rejections
    assert "INTERNAL ERROR" not in caplog.text


@pytest.mark.parametrize("a, e, kept", [
    (1_000_000.0, 900_000.0, True),             # exactly the ceiling
    (7_089.43, 8_259.23, True),                 # Berkshire Hathaway Class A's live row (2026-03-02): far inside it
    (1_000_000.01, 900_000.0, False),
    (900_000.0, 1_000_000.5, False),            # the estimate above it
    (-1_500_000.0, -1_200_000.0, False),
    (1e20, 1.1e20, False),                      # absurd but self-consistent: no other gate refused it
], ids=["ceiling", "brk_a", "actual_above", "estimate_above", "negative", "1e20_pair"])
@pytest.mark.asyncio
async def test_an_eps_above_a_million_a_share_is_refused_whole(a, e, kept):
    """The owner-delegated EPS ceiling (2026-10-19): |actual| or |estimate| above `R.EPS_MAX_ABS` ($1,000,000 a
    share) is a feed error — the WHOLE report is refused at the row gate (`eps_implausible`), before any profile or
    statement read; Berkshire's Class A EPS stays far inside it. Mutation-checked by hand (2026-10-19): without the
    row gate a refused row went on to a profile read (red); with ``>=`` the row exactly at the ceiling was refused
    (red)."""
    fmp = FakeFMP2b(calendar={"2027-01-12": [erow("AAA", "2027-01-12", a, e)]},
                    profiles={"AAA": prof("AAA", "Aaa Industries Inc.", cap=5e9)})
    got = await erun(fmp)
    if kept:
        assert [(r.company.symbol, r.eps_actual) for r in got.records] == [("AAA", a)] and got.rejections == {}
    else:
        assert got.records == () and got.rejections == {"eps_implausible": 1}
        assert fmp.calls["profiles"] == 0 and fmp.statement_calls == []


_PLD_RUN = date(2026, 10, 22)              # Thursday; reports Oct 15 .. 21 (the live preview's PLD week)


def _quarter_rows(sym, revenue, eps, *, older_revenue, older_eps):
    return [{"symbol": sym, "date": "2026-09-30", "period": "Q3", "reportedCurrency": "USD", "revenue": revenue,
             "eps": eps, "epsDiluted": eps},
            {"symbol": sym, "date": "2026-06-30", "period": "Q2", "reportedCurrency": "USD", "revenue": older_revenue,
             "eps": older_eps, "epsDiluted": older_eps}]


def _pld_world(**profile_over):
    profile = prof("PLD", "Prologis, Inc.", cap=122e9, sector="Real Estate", industry="REIT - Industrial")
    profile.update(profile_over)
    row = erow("PLD", "2026-10-15", 1.60, 0.777, 2_387_518_000, 2_175_508_000)
    statements = _quarter_rows("PLD", 2_387_518_000, 0.86, older_revenue=2_210_000_000, older_eps=1.13)
    return FakeFMP2b(calendar={"2026-10-15": [row]}, profiles={"PLD": profile}, currency={"PLD": statements})


@pytest.mark.asyncio
async def test_prologis_mixed_basis_eps_is_never_published(caplog):
    """Live preview F1 (2026-10-16): Prologis' calendar row — 1.60 against an estimate of 0.777, revenue
    $2,387,518,000 against $2,175,508,000 — passed every gate and composed "Prologis reported EPS of $1.60 vs an
    analyst estimate of $0.78": the actual is a Core-FFO-scale figure (the quarter's GAAP diluted EPS was 0.86),
    the estimate a GAAP-scale one. Its real profile (Real Estate, REIT - Industrial) is refused before any
    statement read (`eps_basis_reit`); the same figures under any other sector are refused after the read
    (`eps_basis_mixed`: the actual 1.86x GAAP while the estimate is 0.90x). Mutation-checked by hand (2026-10-16):
    without the sector gate the first world reached the read; without the basis gate the second was a record."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    fmp = _pld_world()
    got = await erun(fmp, run_date=_PLD_RUN)
    assert got.records == () and got.rejections == {"eps_basis_reit": 1} and fmp.statement_calls == []
    assert "PLD refused as eps_basis_reit" in caplog.text
    for over in ({"sector": "Industrials", "industry": "Integrated Freight & Logistics"},
                 {"sector": "Technology", "industry": None}):
        A.clear_memo()
        fmp = _pld_world(**over)
        got = await erun(fmp, run_date=_PLD_RUN)
        assert got.records == () and got.rejections == {"eps_basis_mixed": 1}
        assert fmp.statement_calls == [("PLD", 4)]
    assert "PLD refused as eps_basis_mixed" in caplog.text
    # an issuer whose sector cannot be read cannot be shown not to be a REIT (fail closed)
    for missing in ({"sector": None}, {"sector": ""}, {"sector": 42}):
        A.clear_memo()
        got = await erun(_pld_world(**missing, industry="Integrated Freight & Logistics"), run_date=_PLD_RUN)
        assert got.records == () and got.rejections == {"eps_basis_reit": 1}


@pytest.mark.parametrize("sym, name, a, e, gaap", [
    ("BLK", "BlackRock, Inc.", 15.93, 14.32, 10.57),       # an adjusted actual far from GAAP, estimate adjusted too
    ("UNH", "UnitedHealth Group Incorporated", 4.69, 4.40, 4.56),
    ("C", "Citigroup Inc.", 2.85, 2.60, 2.85), ("AA", "Alcoa Corporation", 1.82, 1.55, 1.81),
    ("TRV", "The Travelers Companies, Inc.", 9.18, 8.10, 9.04), ("CMC", "Commercial Metals Company", 2.09, 1.80, 1.91),
    ("XLOS", "Xlos Industries Inc.", 1.60, 0.777, -0.40),   # a GAAP loss quarter: the basis gate does not apply
], ids=["blk", "unh", "c", "aa", "trv", "cmc", "gaap_loss"])
@pytest.mark.asyncio
async def test_the_live_previews_held_records_keep_their_eps(sym, name, a, e, gaap):
    """The held records of the 2026-10-16 live preview track their estimate's basis and stay published (the gate
    is narrow: never a GAAP check of the actual alone) — BlackRock's $15.93 is 1.507x its GAAP $10.57, but its
    $14.32 estimate is off the GAAP scale too."""
    profile = prof(sym, name, exchange="NYSE", cap=80e9, sector="Financial Services", industry="Asset Management")
    statements = _quarter_rows(sym, 2_000_000_000, gaap, older_revenue=1_900_000_000, older_eps=gaap)
    fmp = FakeFMP2b(calendar={"2026-10-15": [erow(sym, "2026-10-15", a, e, 2_000_000_000, 1_950_000_000)]},
                    profiles={sym: profile}, currency={sym: statements})
    got = await erun(fmp, run_date=_PLD_RUN)
    (rec,) = got.records
    assert (rec.company.symbol, rec.eps_actual, rec.eps_estimate, rec.period_end) == (sym, a, e, date(2026, 9, 30))
    assert got.rejections == {}


# ── news-v21 (main-session decision of 2026-10-20): revenue is checked, never printed ───────────────────────

#: GE Aerospace's and Travelers' Q3 2025 (FMP, the read-only probe of 2026-10-20): the calendar's revenue ACTUAL is
#: the statement's GAAP total — so the period check places the quarter — and its ESTIMATE the consensus for an
#: ADJUSTED revenue: "revenue of $12.2 billion vs $10.4 billion" held on neither basis. FMP's earlier quarters sat on
#: the statement basis too, so the revenue-basis gate (2026-10-20, removed) could not see it, and no read of the feed
#: can. Such a report is HELD for its EPS — the revenue pair stays the data-quality and quarter check — and its post
#: prints no revenue (`news_templates.EARNINGS_REVENUE_IN_POSTS`). (name, report day, EPS actual / estimate, revenue
#: actual / estimate, the statement's diluted EPS, sector, industry, the four quarterly statement rows.)
_TWO_BASIS_2025 = {
    "GE": ("GE Aerospace", "2025-10-21", 1.66, 1.46, 12.221e9, 10.385e9, 2.16, "Industrials", "Aerospace & Defense",
           (("2025-09-30", 12.221e9), ("2025-06-30", 11.022e9), ("2025-03-31", 9.934e9), ("2024-12-31", 10.811e9))),
    "TRV": ("The Travelers Companies, Inc.", "2025-10-16", 8.14, 6.39, 12.470e9, 11.688e9, 8.24, "Financial Services",
            "Insurance - Property & Casualty",
            (("2025-09-30", 12.470e9), ("2025-06-30", 12.116e9), ("2025-03-31", 11.810e9), ("2024-12-31", 12.013e9))),
}
_TWO_BASIS_RUN = date(2025, 10, 23)          # Thursday; reports Oct 16 .. 22


class _HistoryCountingFMP(FakeFMP2b):
    """Counts any per-symbol earnings-history read (`/stable/earnings?symbol=`, what the removed revenue-basis gate
    read) and answers it with no history — a gate reading it would refuse every report. The earnings path makes none."""

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        self.calls["history"] += 1
        return []


@pytest.mark.asyncio
async def test_a_two_basis_revenue_pair_is_held_and_printed_since_revenue_always():
    """GE Aerospace and Travelers (Q3 2025) are held, the larger first, each placed in its quarter by ONE statement
    read and nothing more. news-v23 (owner 2026-11-19, "Revenue always"): their post prints the revenue pair too —
    the owner ACCEPTS the GE-type risk (a consensus on another basis than the reported figure); the table's note and
    the narration's basis line say the estimate's basis can differ."""
    profiles, calendar, currency = {}, {}, {}
    for sym, (name, day, a, e, ra, re_, gaap, sector, industry, quarters) in _TWO_BASIS_2025.items():
        profiles[sym] = prof(sym, name, cap=200e9 if sym == "GE" else 60e9, sector=sector, industry=industry)
        calendar.setdefault(day, []).append(erow(sym, day, a, e, ra, re_))
        currency[sym] = [{"symbol": sym, "date": d, "period": "Q", "reportedCurrency": "USD", "revenue": rev,
                          "epsDiluted": gaap} for d, rev in quarters]
    fmp = _HistoryCountingFMP(calendar=calendar, profiles=profiles, currency=currency)
    got = await erun(fmp, run_date=_TWO_BASIS_RUN)
    assert [(r.company.symbol, r.period_end, r.revenue_actual, r.revenue_estimate) for r in got.records] == [
        ("GE", date(2025, 9, 30), 12.221e9, 10.385e9), ("TRV", date(2025, 9, 30), 12.470e9, 11.688e9)]
    assert got.rejections == {} and fmp.calls["history"] == 0
    assert sorted(fmp.statement_calls) == [("GE", 4), ("TRV", 4)]
    out = NT.compose(got.record, run_date=_TWO_BASIS_RUN, store_state="live", allow_x_url=False)
    rows = out["image_spec"]["sections"][0]["rows"]
    assert [[line["cells"][0] for line in row["table"]] for row in rows] == [["EPS", "Revenue"], ["EPS", "Revenue"]]
    assert [row["table"][1]["cells"][1:3] for row in rows] == [["$10.4B", "$12.2B"], ["$11.7B", "$12.5B"]]
    assert out["image_spec"]["notes"] == [NT.LEXICON["er.img.note"]]
    assert any(NT.LEXICON["er.m.basis"] == line for line in out["video_script"])


@pytest.mark.asyncio
async def test_the_clear_surprise_and_the_lead_order_never_read_revenue():
    """news-v21 (the decision's point 3): the surprise gate reads the EPS pair alone and the lead order is (market
    cap, EPS gap, symbol) — revenue decides neither: an EQUAL revenue pair beside a clear EPS surprise is held, a
    40% revenue gap beside a 4% EPS gap is refused (`surprise_unclear`), and swapping the revenue pairs of the
    reports changes neither the records nor their order. (The revenue ESTIMATE is only the size proxy that picks
    which `EARNINGS_MAX_PROFILES` reports are profiled at all — never the order of those profiled.)"""
    day = "2027-01-12"
    # revenue pairs of every size and gap inside the band (1.0x, 1.4x, 0.6x, 1.45x), estimates from $1B to $3B
    pairs = {"AAA": (1.0e9, 1.0e9), "BBB": (2.8e9, 2.0e9), "CCC": (0.6e9, 1.0e9), "DDD": (4.35e9, 3.0e9)}
    eps = {"AAA": (1.5, 1.0), "BBB": (1.04, 1.0), "CCC": (1.2, 1.0), "DDD": (1.2, 1.0)}
    caps = {"AAA": 5e9, "BBB": 50e9, "CCC": 20e9, "DDD": 20e9}
    profiles = {s: prof(s, f"{s.title()} Industries Inc.", cap=c) for s, c in caps.items()}
    seen = []
    for swap in (False, True):
        A.clear_memo()
        # the swap: every report gets another's revenue pair (BBB's 4% EPS gap beside a 1.4x, then a 0.6x pair)
        shown = dict(zip(pairs, list(pairs.values())[::-1])) if swap else pairs
        cal = {day: [erow(s, day, *eps[s], *shown[s]) for s in pairs]}
        got = await erun(FakeFMP2b(calendar=cal, profiles=profiles))
        seen.append([(r.company.symbol, r.eps_actual, r.eps_estimate) for r in got.records])
        assert got.rejections == {"surprise_unclear": 1}, swap
    assert seen[0] == seen[1] == [("CCC", 1.2, 1.0), ("DDD", 1.2, 1.0), ("AAA", 1.5, 1.0)]


#: A roster that holds the 120th Congress (sworn in Jan 3, 2027): from that day the shipped 119th roster pauses
#: both Form 4 series (`R.roster_fresh_for`), so the window's January edge is read with the refreshed one.
_ROSTER_120TH = ("2027-01-03", "2027-01-03")


@pytest.mark.parametrize("run_date, roster, named", [
    (date(2026, 10, 31), None, True),     # its posts go out Oct 31 .. Nov 2: before Election Day (Tue Nov 3)
    (date(2026, 11, 1), None, False),     # a post of this run can go out on Election Day: role-only
    (date(2026, 11, 2), None, False),     # the ME-1 case: Monday before Election Day, approved Tuesday night
    (date(2027, 1, 3), _ROSTER_120TH, False),   # the window's last day (the new Congress is sworn in)
    (date(2027, 1, 4), _ROSTER_120TH, True),    # its posts go out Jan 4 .. Jan 6: after the window
], ids=["oct_31", "nov_1", "nov_2", "jan_3", "jan_4"])
@pytest.mark.asyncio
async def test_a_run_whose_posts_can_go_out_in_the_window_is_built_role_only(monkeypatch, tmp_path, run_date, roster,
                                                                             named):
    """ME-1: the members-elect window is judged over every day a run's posts can go out (`R.members_elect_for_run`:
    the run date and the two days after it) — both edges pinned at the adapter, as at the templates and the
    validator (tests/test_marketing_news_templates.py). Mutation-checked by hand (2026-10-16): `_insider_row`
    reading `R.members_elect_window(ctx.run_date)` built the person on Nov 1 and Nov 2 (red); the January
    edge reads the run date's own window (Jan 3 role-only, Jan 4 named)."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    R._congress_state.cache_clear()
    if roster is not None:
        monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, *roster))
    try:
        assert R.roster_fresh_for(run_date)                  # the series runs: the window alone decides the name
        start, end = R.insider_window(run_date)
        filed, traded = (end - timedelta(days=2)).isoformat(), (end - timedelta(days=3)).isoformat()
        rows = [irow(filed=filed, traded=traded)]
        fmp = FakeFMP(insider=rows, profiles=week_profiles())
        got = await run("ceo_buys", fmp, run_date=run_date)
        (rec,) = got.records
        assert (rec.rows[0].person_name == "Ryan Cohen") is named
        assert R.members_elect_for_run(run_date) is (not named)
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


# ══ the spotlight lane (owner decisions 2026-11-15; workstream B2): the two single-company collectors ═══════════════
#
# insider_spotlight: ONE issuer's largest qualifying CEO / CFO / director and up to 4 more of it, over a window from the
# previous spotlight RUN to the day before the newest filing the market-wide walk holds (B decision 5); every Form 4
# gate of the roundups through the SHARED helpers (`_insider_gated_lines`, `_issuer_lines`, `_reporter_rows`) with two
# windows (the 4/A / role / co-officer LOOK-BACK, never shorter than the roundups' week; the PURCHASE window a
# person's row must hold whole); the per-issuer Form 4/A check per PERSON (`_spotlight_checked`); a 14-day issuer
# cooldown by root symbol and display name (B decision 6); the revenue line for the audited pool only (B decision 8).
# thirteen_f_spotlight: ONE filer's ONE holding through the roundup's walkers (B decision 7). The roundups' own answers
# are pinned byte for byte above (`test_a_week_with_no_amendment_is_byte_identical_to_round_6`, every Form 4 shape).

#: Thursday Nov 19, 2026 — a Tue/Thu/Sat spotlight day; the previous spotlight run was Tuesday the 17th.
SPOT_RUN, SPOT_PREV = date(2026, 11, 19), date(2026, 11, 17)
SPOT_SAT, SPOT_NEXT_TUE = date(2026, 11, 21), date(2026, 11, 24)
assert (SPOT_RUN.weekday(), SPOT_PREV.weekday(), SPOT_SAT.weekday(), SPOT_NEXT_TUE.weekday()) == (3, 1, 5, 1)
#: C0 (2026-11-16): the window starts the day after the last window an earlier insider spotlight CHECKED
#: (`spotlight_checked_through`). Tuesday's spotlight ran at 10:00 with Monday's filings the newest, so it checked
#: through Sunday the 15th — Thursday's window starts Monday the 16th. Thursday's checked through Tuesday the 17th
#: (Wednesday's filings the newest at 10:00); Saturday's through Thursday the 19th.
SPOT_CHECKED, SPOT_THU_CHECKED, SPOT_SAT_CHECKED = date(2026, 11, 15), date(2026, 11, 17), date(2026, 11, 19)

_CTSO_CEO = dict(sym="CTSO", name="DOE JOHN", cik="0000001001", title="director, officer: Chief Executive Officer")
_CTSO_DIR = dict(sym="CTSO", name="ROE RICHARD", cik="0000001002", title="director")
_CTSO_CFO = dict(sym="CTSO", name="POE ANNA", cik="0000001003", title="officer: Chief Financial Officer")
_CTSO_ISSUER = A.normalize_cik(issuer_cik("CTSO"))


def _ctso(base, shares, price, filed, traded, **kw):
    """One Contoso Form 4 line of ``base``'s person."""
    return irow(**{**base, "shares": shares, "price": price, "filed": filed, "traded": traded, **kw})


def _marker(filed: str) -> Dict[str, Any]:
    """Another company's purchase filed on ``filed``: the walk's proof the feed passed the day before it."""
    return irow(sym="KO", name="LEE ANNA", cik="0000000333", filed=filed, traded=filed)


def spot_rows(*, marker: Optional[str] = "2026-11-19") -> List[Dict[str, Any]]:
    """The design's worked example (Contoso, CTSO): the CEO's two lines filed Nov 18 (7,000 @ $142.10 + 7,000 @
    $143.85 = $2,001,650), a director filed Nov 17 (2,900 @ $141.38 = $410,002), the CFO filed Nov 18 (1,750 @ $142.86
    = $250,005) — and, unless ``marker`` is None, a run-day filing of another company."""
    rows = [_ctso(_CTSO_CEO, 7_000, 142.10, "2026-11-18", "2026-11-16"),
            _ctso(_CTSO_CEO, 7_000, 143.85, "2026-11-18", "2026-11-17"),
            _ctso(_CTSO_DIR, 2_900, 141.38, "2026-11-17", "2026-11-16"),
            _ctso(_CTSO_CFO, 1_750, 142.86, "2026-11-18", "2026-11-17")]
    return rows + ([_marker(marker)] if marker else [])


def spot_profiles(**over) -> Dict[str, Any]:
    return {"CTSO": prof("CTSO", "Contoso Corporation", price=143.0, cap=50e9, ceo="Mr. John Doe", **over),
            "KO": prof("KO", "The Coca-Cola Company", price=25.0, cap=300e9)}


async def spot(fmp, *, run_date=SPOT_RUN, checked=SPOT_CHECKED, recent=(), exclude=(), limit=5, left=60.0, **deps):
    deps.setdefault("actions", SplitActions())
    return await A.candidates("insider_spotlight", run_date=run_date, exclude=frozenset(exclude), limit=limit,
                              deadline=time.monotonic() + left, deps=A.NewsDeps(fmp=fmp, **deps),
                              spotlight_checked_through=checked, spotlight_recent=tuple(recent))


def _people(rec) -> List[tuple]:
    """The people of the list's LEAD row (owner decisions 2026-11-20: the record is a list of companies; the tests here
    run one issuer's row with the minimum lowered, `_form4_row_gates_count_from_one_company`)."""
    return [(p.role, p.person_name, round(p.amount_usd, 2)) for p in rec.companies[0].people]


def test_the_spotlight_constants_and_caps():
    assert A.SPOTLIGHT_ROLES == ("ceo", "cfo", "director")
    assert A.SPOTLIGHT_PEOPLE_PER_ISSUER == 1 + R.SPOTLIGHT_MAX_OTHERS == 5
    # B decision 5: the cluster variant's own cap, at least every row a call can return times a full cluster
    assert A.SPOTLIGHT_AMENDMENT_MAX_PEOPLE >= R.INSIDER_MAX_ROWS * (1 + R.SPOTLIGHT_MAX_OTHERS)
    assert A.SPOTLIGHT_AMENDMENT_MAX_ISSUERS == A.INSIDER_AMENDMENT_MAX_SYMBOLS
    assert set(A.COLLECTORS) >= set(selection.SPOTLIGHT_SERIES)
    assert A.COLLECTORS["insider_spotlight"] is A._collect_insider_spotlight
    assert A.COLLECTORS["thirteen_f_spotlight"] is A._collect_thirteen_f_spotlight


@pytest.mark.asyncio
async def test_insider_spotlight_builds_one_issuers_cluster_over_its_window():
    """The worked example: window Mon Nov 16 (the day before the previous run) .. Wed Nov 18 (the day before the
    run-day filing); the CEO leads, the director and the CFO follow by amount; every person carries the Form 4 lines'
    OWN cost per share; one per-issuer read serves all three; the walk reads from the run date − 7 (the roundups'
    memo key) — once."""
    fmp = FakeFMP(insider=spot_rows(), profiles=spot_profiles())
    got = await spot(fmp)
    assert got.skip_reason is None and got.rejections == {}
    (rec,) = got.records
    assert isinstance(rec, R.InsiderSpotlight) and rec.series == "insider_spotlight"
    assert (rec.window_start, rec.window_end) == (date(2026, 11, 16), date(2026, 11, 18))
    assert R.ledger_key(rec) == "news:insider_spotlight:CTSO:2026-11-16"
    assert (rec.lead.company.symbol, rec.lead.company.name) == ("CTSO", "Contoso")
    assert _people(rec) == [("ceo", "John Doe", 2_001_650.0), ("director", "Richard Roe", 410_002.0),
                            ("cfo", "Anna Poe", 250_005.0)]
    lead = rec.lead
    assert (lead.shares, lead.purchases, lead.filing_dates) == (14_000.0, 2, (date(2026, 11, 18),))
    assert (lead.earliest_trade_date, lead.latest_trade_date) == (date(2026, 11, 16), date(2026, 11, 17))
    assert (lead.cost_per_share_min_usd, lead.cost_per_share_max_usd) == (142.10, 143.85)
    assert [(p.cost_per_share_min_usd, p.cost_per_share_max_usd) for p in rec.companies[0].others] == [(141.38, 141.38),
                                                                                         (142.86, 142.86)]
    assert len(rec.companies) == 1          # one issuer's row (the minimum lowered for this test); no revenue line
    assert fmp.calls["insider"] == 1 and fmp.insider_args["since"] == "2026-11-12"
    assert issuer_reads(fmp) == ["CTSO"] and fmp.issuer_calls[0]["since"] == "2026-11-12"
    assert fmp.issuer_calls[0]["transaction_type"] is None                  # every code, as the roundups read it


@pytest.mark.parametrize("checked, marker, window, walks", [
    (SPOT_CHECKED, "2026-11-19", ("2026-11-16", "2026-11-18"), ["2026-11-12"]),
    # no checked window within the cooldown: the run date − 7
    (None, "2026-11-19", ("2026-11-12", "2026-11-18"), ["2026-11-12"]),
    (date(2026, 11, 3), "2026-11-19", ("2026-11-12", "2026-11-18"), ["2026-11-12"]),
    # the feed a day behind (its newest filing is Nov 18's): the window ends Nov 17 and the look-back (end − 6 = Nov
    # 11) starts before the roundups' walk, so the walk is read again from it
    (SPOT_CHECKED, None, ("2026-11-16", "2026-11-17"), ["2026-11-12", "2026-11-11"]),
], ids=["checked_through_sunday", "nothing_checked", "checked_window_older_than_a_week", "feed_a_day_behind"])
@pytest.mark.asyncio
async def test_the_window_comes_from_the_last_checked_window_and_the_newest_filing(checked, marker, window, walks):
    fmp = FakeFMP(insider=spot_rows(marker=marker), profiles=spot_profiles())
    seen: List[str] = []
    real = fmp.get_insider_trades_since

    async def spy(since, **kw):
        if kw.get("company_cik") is None:
            seen.append(since)
        return await real(since, **kw)

    fmp.get_insider_trades_since = spy
    got = await spot(fmp, checked=checked)
    (rec,) = got.records
    assert (rec.window_start.isoformat(), rec.window_end.isoformat()) == window
    assert got.checked_window == (rec.window_start, rec.window_end)       # handed back for the trail (C0)
    assert seen == walks
    if marker is None:
        # Nov 18's filings are the next window's: the director (filed Nov 17) leads alone
        assert _people(rec) == [("director", "Richard Roe", 410_002.0)]
        assert {c["since"] for c in fmp.issuer_calls} == {"2026-11-11"}


@pytest.mark.asyncio
async def test_a_saturday_window_ends_on_thursday_and_friday_rolls_into_tuesday():
    """No Saturday filings: a Saturday run's newest filing is Friday's, so its window ends Thursday; Friday's filing
    is the NEXT window's (Tuesday's starts the day after the Thursday Saturday checked through) — never lost, never
    posted twice."""
    rows = [_ctso(_CTSO_DIR, 2_900, 141.38, "2026-11-19", "2026-11-18"),        # Thursday
            _ctso(_CTSO_CEO, 7_000, 142.10, "2026-11-20", "2026-11-19")]        # Friday
    fmp = FakeFMP(insider=rows, profiles=spot_profiles())
    got = await spot(fmp, run_date=SPOT_SAT, checked=SPOT_THU_CHECKED)
    (rec,) = got.records
    assert (rec.window_start, rec.window_end) == (date(2026, 11, 18), date(2026, 11, 19))
    assert got.checked_window == (date(2026, 11, 18), date(2026, 11, 19))
    assert _people(rec) == [("director", "Richard Roe", 410_002.0)]           # Friday's CEO line is not in it
    A.clear_memo()
    fmp = FakeFMP(insider=rows + [_marker("2026-11-24")], profiles=spot_profiles())
    got = await spot(fmp, run_date=SPOT_NEXT_TUE, checked=got.checked_window[1])
    (rec,) = got.records
    assert (rec.window_start, rec.window_end) == (date(2026, 11, 20), date(2026, 11, 23))
    assert _people(rec) == [("ceo", "John Doe", 994_700.0)]                   # Friday's, in Tuesday's window


@pytest.mark.parametrize("rows", [
    # the newest filing is Monday's: the window (from Monday) has not been passed
    [_ctso(_CTSO_DIR, 2_900, 141.38, "2026-11-16", "2026-11-13")],
    # every filing date is after the run date (junk) or unreadable: nothing proves the feed reached any day
    [_ctso(_CTSO_DIR, 2_900, 141.38, "2026-11-20", "2026-11-18"),
     _ctso(_CTSO_CFO, 1_750, 142.86, "2026-11-18", "2026-11-17", filingDate="soon")],
], ids=["behind_by_a_day", "no_usable_filing_date"])
@pytest.mark.asyncio
async def test_a_feed_behind_the_window_is_insider_feed_behind_never_a_skip(rows, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = FakeFMP(insider=rows, profiles=spot_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await spot(fmp)
    assert err.value.reason == "insider_feed_behind" and err.value.series == "insider_spotlight"
    assert "insider_feed_behind" in R.UNAVAILABLE_REASONS
    assert fmp.calls["profiles"] == 0 and fmp.calls["insider_issuer"] == 0
    assert "UNAVAILABLE series=insider_spotlight" in caplog.text and "reason=insider_feed_behind" in caplog.text


@pytest.mark.asyncio
async def test_no_new_day_on_a_current_feed_is_a_quiet_skip_never_an_outage(caplog):
    """C0 (2026-11-16): the Tuesday after Presidents' Day (Tue Feb 17, 2026) at 10:00, no Tuesday filing yet — the
    newest filing is Friday's (Monday is no business day), and Saturday's spotlight already checked through Thursday:
    there is no new day to check. The feed is CURRENT (Friday is one business day back), so the answer is the quiet
    skip ``insider_spotlight_no_new_window`` — nothing checked, no window handed back, no profile or per-issuer read —
    never ``insider_feed_behind``."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    rows = [irow(sym="CTSO", name="ROE RICHARD", cik="0000001002", title="director", shares=2_900, price=141.38,
                 filed="2026-02-13", traded="2026-02-12"), _marker("2026-02-13")]
    fmp = FakeFMP(insider=rows, profiles=spot_profiles())
    got = await spot(fmp, run_date=date(2026, 2, 17), checked=date(2026, 2, 12))
    assert got.records == () and got.skip_reason == "insider_spotlight_no_new_window"
    assert got.checked_window is None
    assert fmp.calls["profiles"] == 0 and fmp.calls["insider_issuer"] == 0
    assert "insider_spotlight_no_new_window" in R.SKIP_REASONS
    assert "no new day to check" in caplog.text and "UNAVAILABLE" not in caplog.text


@pytest.mark.asyncio
async def test_a_stale_feed_with_no_new_day_is_still_insider_feed_behind():
    """The twin: the same Tuesday, but the walk's newest filing is from the Tuesday BEFORE (three business days back):
    the feed is behind — ``insider_feed_behind`` (an outage the digest alerts on), never a quiet skip."""
    rows = [irow(sym="CTSO", name="ROE RICHARD", cik="0000001002", title="director", shares=2_900, price=141.38,
                 filed="2026-02-10", traded="2026-02-09")]
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await spot(FakeFMP(insider=rows, profiles=spot_profiles()), run_date=date(2026, 2, 17),
                   checked=date(2026, 2, 12))
    assert err.value.reason == "insider_feed_behind"


@pytest.mark.asyncio
async def test_the_checked_window_rides_back_when_nothing_qualifies():
    """A window checked with nothing qualifying is still CHECKED: the answer hands it back (the script flow records it,
    so the next spotlight starts after it) — and every other series answers with none."""
    rows = [irow(sym="CTSO", name="ROE RICHARD", cik="0000001002", title="director", shares=10, price=141.38,
                 filed="2026-11-18", traded="2026-11-17"), _marker("2026-11-19")]      # below the dollar floor
    got = await spot(FakeFMP(insider=rows, profiles=spot_profiles()))
    assert got.records == () and got.skip_reason == "insider_spotlight_none_qualified"
    assert got.checked_window == (date(2026, 11, 16), date(2026, 11, 18))
    A.clear_memo()
    other = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=week_profiles()))
    assert other.checked_window is None


def test_a_candidates_window_must_be_a_pair_of_dates():
    for bad in ((date(2026, 11, 18), date(2026, 11, 16)), (date(2026, 11, 16),), ("2026-11-16", "2026-11-18"),
                (datetime(2026, 11, 16), date(2026, 11, 18))):
        with pytest.raises(ValueError, match="checked_window"):
            A.Candidates(series="insider_spotlight", records=(), skip_reason="insider_spotlight_none_qualified",
                         rejections={}, checked_window=bad)


@pytest.mark.asyncio
async def test_an_empty_walk_is_still_insider_feed_empty():
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await spot(FakeFMP(insider=[], profiles=spot_profiles()))
    assert err.value.reason == "insider_feed_empty"


def _sat_world(extra_walk=(), extra_issuer=(), *, ceo_filed="2026-11-19"):
    """Saturday Nov 21 (Thursday's spotlight checked through Tuesday, SPOT_THU_CHECKED): window Wed Nov 18 .. Thu Nov
    19, look-back from Fri Nov 13 (end − 6),
    before the roundups' walk start (Nov 14) — so the walk is read from Nov 13. CTSO's CEO filed ``ceo_filed``; a
    Friday filing of another company proves the feed passed Thursday."""
    rows = [_ctso(_CTSO_CEO, 7_000, 142.10, ceo_filed, "2026-11-17"),
            _ctso(_CTSO_DIR, 2_900, 141.38, "2026-11-19", "2026-11-18"),
            _marker("2026-11-20"), *extra_walk]
    walk = [r for r in rows if r.get("transactionType") == "P-Purchase"]
    issuer = [r for r in rows if A.normalize_cik(r.get("companyCik")) == _CTSO_ISSUER] + list(extra_issuer)
    return FakeFMP(insider=walk, profiles=spot_profiles(), issuer_feed={_CTSO_ISSUER: issuer})


@pytest.mark.parametrize("where", ["walk", "issuer_read"])
@pytest.mark.parametrize("filed, refused", [("2026-11-13", True), ("2026-11-12", False)],
                         ids=["five_days_before_the_window", "before_the_look_back"])
@pytest.mark.asyncio
async def test_a_4a_of_the_issuer_filed_days_before_the_window_refuses_the_company(where, filed, refused):
    """The company-level 4/A rule looks back as far as the roundups do (B decision 5: amend_since = min(window start,
    window end − 6)): a 4/A of the issuer — another reporter, any code — filed Nov 13, five days before the 2-day
    window, refuses every person of Contoso, in the market-wide walk or only in the per-issuer read. Filed Nov 12
    (before the look-back) it does not. Mutation-checked by hand: with the look-back set to the window start, the
    Nov 13 4/A stood (red)."""
    amendment = irow(sym="CTSO", name="ZED ZOE", cik="0000001009", title="director", form="4/A", filed=filed,
                     traded="2026-11-10", tx="P-Purchase" if where == "walk" else "J-Other",
                     shares=500, price=140.0)
    fmp = _sat_world(extra_walk=[amendment] if where == "walk" else [],
                     extra_issuer=[amendment] if where == "issuer_read" else [])
    got = await spot(fmp, run_date=SPOT_SAT, checked=SPOT_THU_CHECKED)
    if refused:
        assert got.records == () and got.skip_reason == "insider_spotlight_none_qualified"
        assert got.rejections == {"amended_filing": 2}                          # one count per person (CEO, director)
    else:
        (rec,) = got.records
        assert _people(rec) == [("ceo", "John Doe", 994_700.0), ("director", "Richard Roe", 410_002.0)]
    assert {c["since"] for c in fmp.issuer_calls} <= {"2026-11-13"}


@pytest.mark.asyncio
async def test_an_earlier_purchase_of_the_person_is_another_windows_never_partial():
    """The PURCHASE window is the spotlight's own two days (B decision 5): the CEO's purchase filed Nov 15 (inside the
    look-back, before the window) is not a missing line of this row — the CEO leads with the window's line alone. A
    purchase the window DOES hold but a gate dropped (an implausible price) still refuses the person
    (``partial_person``), and only that person."""
    earlier = _ctso(_CTSO_CEO, 3_000, 139.0, "2026-11-15", "2026-11-13")
    got = await spot(_sat_world(extra_walk=[earlier]), run_date=SPOT_SAT, checked=SPOT_THU_CHECKED)
    (rec,) = got.records
    assert _people(rec) == [("ceo", "John Doe", 994_700.0), ("director", "Richard Roe", 410_002.0)]
    assert rec.lead.filing_dates == (date(2026, 11, 19),) and "partial_person" not in got.rejections
    A.clear_memo()
    dropped = _ctso(_CTSO_CEO, 100, 5.0, "2026-11-19", "2026-11-18")        # implausible: $5 against $143
    got = await spot(_sat_world(extra_walk=[dropped]), run_date=SPOT_SAT, checked=SPOT_THU_CHECKED)
    (rec,) = got.records
    assert _people(rec) == [("director", "Richard Roe", 410_002.0)]
    assert got.rejections == {"price_implausible": 1, "partial_person": 1}


@pytest.mark.asyncio
async def test_a_co_ceo_drops_the_ceo_lines_and_the_rest_of_the_cluster_stands():
    co = _ctso(dict(sym="CTSO", name="BOND JAMES", cik="0000001004", title="officer: Co-CEO"), 1_000, 142.0,
               "2026-11-18", "2026-11-17")
    fmp = FakeFMP(insider=spot_rows() + [co], profiles=spot_profiles())
    got = await spot(fmp)
    (rec,) = got.records
    assert _people(rec) == [("director", "Richard Roe", 410_002.0), ("cfo", "Anna Poe", 250_005.0)]
    assert got.rejections == {"ambiguous_ceo": 2}            # the co-CEO and the CEO (every CEO line of the issuer)


_CTSO_OTHER = dict(sym="CTSO", name="BOND JAMES", cik="0000001004", title="director")


@pytest.mark.parametrize("person, text, reason", [
    # the CEO's own F-coded row (the P walk never returns it) says "Former CEO"
    (_CTSO_CEO, "officer: Former CEO", "role_uncertain"),
    # ANOTHER person's row, only in the issuer read, is a co-CEO's: "the company's CEO" is one of two
    (_CTSO_OTHER, "officer: Co-Chief Executive Officer", "ambiguous_ceo"),
], ids=["former_ceo", "co_ceo_in_the_issuer_read"])
@pytest.mark.asyncio
async def test_the_issuer_read_refuses_one_person_and_the_cluster_keeps_the_rest(person, text, reason):
    """The per-issuer read runs per PERSON (`_spotlight_checked`): what it shows refuses the CEO alone; the director
    then leads and the CFO follows."""
    rows = spot_rows()
    f_row = _ctso(person, 2_000, 0.0, "2026-11-17", "2026-11-16", tx="F-InKind", title=text)
    issuer = [r for r in rows if A.normalize_cik(r.get("companyCik")) == _CTSO_ISSUER] + [f_row]
    fmp = FakeFMP(insider=rows, profiles=spot_profiles(), issuer_feed={_CTSO_ISSUER: issuer})
    got = await spot(fmp)
    (rec,) = got.records
    assert _people(rec) == [("director", "Richard Roe", 410_002.0), ("cfo", "Anna Poe", 250_005.0)]
    assert got.rejections == {reason: 1}
    assert issuer_reads(fmp) == ["CTSO"]                        # one read for the three people


@pytest.mark.asyncio
async def test_a_member_of_congress_never_leads_and_never_joins_a_cluster(caplog):
    caplog.set_level(logging.DEBUG)
    member_lead = _ctso(dict(sym="CTSO", name="PELOSI NANCY", cik="0000000444", title="director"), 30_000, 142.0,
                        "2026-11-18", "2026-11-17")
    got = await spot(FakeFMP(insider=spot_rows() + [member_lead], profiles=spot_profiles()))
    (rec,) = got.records
    assert _people(rec)[0] == ("ceo", "John Doe", 2_001_650.0) and len(rec.companies[0].others) == 2
    assert got.rejections == {"congress_name": 1}
    A.clear_memo()
    member_small = _ctso(dict(sym="CTSO", name="PELOSI NANCY", cik="0000000444", title="director"), 1_000, 142.0,
                         "2026-11-18", "2026-11-17")
    got = await spot(FakeFMP(insider=spot_rows() + [member_small], profiles=spot_profiles()))
    (rec,) = got.records
    assert [p.person_name for p in rec.companies[0].people] == ["John Doe", "Richard Roe", "Anna Poe"]
    assert got.rejections == {"congress_name": 1}
    assert "pelosi" not in caplog.text.lower() and "nancy" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_inside_the_members_elect_window_no_spotlight_person_is_built(monkeypatch):
    """Owner decision 4: Thu Nov 19, 2026 is inside the window (Election Day Nov 3 .. Jan 3) — every person of the
    cluster is role-only."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    got = await spot(FakeFMP(insider=spot_rows(), profiles=spot_profiles()))
    (rec,) = got.records
    assert [(p.role, p.person_name) for p in rec.companies[0].people] == [("ceo", None), ("director", None),
                                                                         ("cfo", None)]


@pytest.mark.asyncio
async def test_one_listing_per_issuer_the_other_share_class_is_dropped():
    rows = [irow(sym="BF-B", name="DOE JOHN", cik="0000001011", title="director", shares=26_000, price=30.0,
                 filed="2026-11-18", traded="2026-11-17", companyCik=issuer_cik("BF")),
            irow(sym="BF-A", name="ROE RICHARD", cik="0000001012", title="director", shares=14_000, price=30.0,
                 filed="2026-11-18", traded="2026-11-17", companyCik=issuer_cik("BF")),
            _marker("2026-11-19")]
    profiles = {"BF-A": _brown_forman("BF-A"), "BF-B": _brown_forman("BF-B"), **spot_profiles()}
    issuer_feed = {A.normalize_cik(issuer_cik("BF")): rows[:2]}
    got = await spot(FakeFMP(insider=rows, profiles=profiles, issuer_feed=issuer_feed))
    (rec,) = got.records
    assert rec.lead.company.symbol == "BF-B" and rec.companies[0].others == ()
    assert got.rejections == {"share_class_overlap": 1}


@pytest.mark.asyncio
async def test_a_partial_person_drops_that_member_only():
    bad = _ctso(_CTSO_DIR, 100, 5.0, "2026-11-17", "2026-11-16")               # the director's implausible line
    got = await spot(FakeFMP(insider=spot_rows() + [bad], profiles=spot_profiles()))
    (rec,) = got.records
    assert _people(rec) == [("ceo", "John Doe", 2_001_650.0), ("cfo", "Anna Poe", 250_005.0)]
    assert got.rejections == {"price_implausible": 1, "partial_person": 1}


@pytest.mark.asyncio
async def test_one_person_under_two_reporter_keys_is_never_two_people():
    """The director's second line carries no reporting CIK (the extractor keys it by name): two reporter groups of ONE
    person. Each holds a purchase of the person the other does not, so both are refused whole (``partial_person``) —
    never shown as "a director … and another director", never re-summed."""
    twin = _ctso(_CTSO_DIR, 1_000, 141.0, "2026-11-17", "2026-11-16", reportingCik=None)
    got = await spot(FakeFMP(insider=spot_rows() + [twin], profiles=spot_profiles()))
    (rec,) = got.records
    assert _people(rec) == [("ceo", "John Doe", 2_001_650.0), ("cfo", "Anna Poe", 250_005.0)]
    assert got.rejections == {"partial_person": 2}


@pytest.mark.asyncio
async def test_a_person_twice_in_one_issuer_is_kept_once_largest_first():
    """`same_person` inside one issuer's group (the cluster variant of the roundups' one-row-per-person rule): "Arthur
    H. Penn" and "Arthur J. Penn" under two CIKs read as one person to the templates (`_person_keys`, initials
    aside) — the larger stays, the other is dropped, never "a director … and another director"."""
    rows = spot_rows() + [
        _ctso(dict(sym="CTSO", name="PENN ARTHUR H", cik="0000001021", title="director"), 2_000, 142.0, "2026-11-17",
              "2026-11-16"),
        _ctso(dict(sym="CTSO", name="PENN ARTHUR J", cik="0000001022", title="director"), 1_000, 142.0, "2026-11-17",
              "2026-11-16")]
    got = await spot(FakeFMP(insider=rows, profiles=spot_profiles()))
    (rec,) = got.records
    assert [p.person_name for p in rec.companies[0].people] == ["John Doe", "Richard Roe", "Arthur H. Penn",
                                                                 "Anna Poe"]
    assert got.rejections == {"same_person": 1}


@pytest.mark.parametrize("exclude, recent, reason, profiled", [
    # the root symbol, from the ledger: a window that started within 14 days — no profile call for it
    (["news:insider_spotlight:CTSO:2026-11-05"], (), "spotlight_cooldown", False),
    # the same issuer and window already posted
    (["news:insider_spotlight:CTSO:2026-11-16"], (), "already_posted", False),
    # the script flow's run-date-exact list: by symbol (before the profile), by display name (after it)
    ([], (("CTSO", "Contoso"),), "spotlight_cooldown", False),
    ([], (("", "Contoso"),), "spotlight_cooldown", True),
    ([], (("", "CONTOSO"),), "spotlight_cooldown", True),
], ids=["ledger_root_within_14_days", "same_window_posted", "recent_symbol", "recent_name", "recent_name_casefold"])
@pytest.mark.asyncio
async def test_an_issuer_spotlighted_within_14_days_is_refused(exclude, recent, reason, profiled):
    fmp = FakeFMP(insider=spot_rows(), profiles=spot_profiles())
    got = await spot(fmp, exclude=exclude, recent=recent)
    assert got.records == () and got.skip_reason == "insider_spotlight_none_qualified"
    assert got.rejections == {reason: 1}
    assert any("CTSO" in req for req in fmp.profile_requests) is profiled
    assert fmp.calls["insider_issuer"] == 0


@pytest.mark.parametrize("exclude", [["news:insider_spotlight:CTSO:2026-11-04"], ["news:insider_spotlight:CTSOX:junk"],
                                     ["news:insider_buys:2026-11-12"], ["news:insider_spotlight:KO:2026-11-16"]],
                         ids=["window_15_days_old", "malformed_key", "a_roundup_key", "another_issuer"])
@pytest.mark.asyncio
async def test_the_cooldown_is_bounded_and_keyed_on_the_issuer(exclude):
    got = await spot(FakeFMP(insider=spot_rows(), profiles=spot_profiles()), exclude=exclude)
    assert [r.lead.company.symbol for r in got.records] == ["CTSO"] and got.rejections == {}


@pytest.mark.asyncio
async def test_a_share_class_twin_is_cooled_by_its_root_symbol():
    rows = [irow(sym="BF-B", name="DOE JOHN", cik="0000001011", title="director", shares=26_000, price=30.0,
                 filed="2026-11-18", traded="2026-11-17", companyCik=issuer_cik("BF")), _marker("2026-11-19")]
    profiles = {"BF-B": _brown_forman("BF-B"), **spot_profiles()}
    got = await spot(FakeFMP(insider=rows, profiles=profiles), recent=(("BF-A", ""),))
    assert got.records == () and got.rejections == {"spotlight_cooldown": 1}
    A.clear_memo()
    got = await spot(FakeFMP(insider=rows, profiles=profiles), exclude=["news:insider_spotlight:BF-A:2026-11-10"])
    assert got.records == () and got.rejections == {"spotlight_cooldown": 1}


@pytest.mark.parametrize("symbol, root", [
    ("BF-B", "BF"), ("BRK-A", "BRK"), ("GOOGL", "GOOG"), ("LBRDA", "LBRD"), ("WAFDP", "WAFD"), ("CTSO", "CTSO"),
    ("A", "A"), ("CMPRF", "CMPRF"), ("NSRGY", "NSRGY"),
])
def test_the_issuer_root(symbol, root):
    assert A._issuer_root(symbol) == root


@pytest.mark.asyncio
async def test_the_roundup_rows_carry_the_lines_own_cost_per_share():
    """B decision 1 at the roundups too: GameStop's CEO bought at $25.00 and $25.50 (as filed) while the PROFILE says
    $25.20 — the row carries the lines' figures, never the profile's (the leak test plants the profile price as a
    canary)."""
    got = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=week_profiles()))
    gme = got.records[0].rows[0]
    assert (gme.cost_per_share_min_usd, gme.cost_per_share_max_usd) == (25.0, 25.5)
    fox = got.records[0].rows[1]
    assert (fox.cost_per_share_min_usd, fox.cost_per_share_max_usd) == (30.0, 30.0)


def _seed(monkeypatch, *syms):
    monkeypatch.setattr(R, "MONEY_MAP_SEED", tuple(syms))


_CTSO_BD = breakdown(symbol="CTSO", revenue_sources=[src("Cloud Services", 189e9), src("Devices", 110e9),
                                                     src("Other", 51e9)])


@pytest.mark.asyncio
async def test_the_insider_list_reads_no_revenue_line(monkeypatch):
    """Owner decisions 2026-11-20: the one-issuer post's revenue line (B decision 8) is retired with it — the insider
    list makes no revenue read at all, even for a company in the audited pool."""
    _seed(monkeypatch, "AAPL", "CTSO")
    revenue = FakeService({"CTSO": _CTSO_BD})
    got = await spot(FakeFMP(insider=spot_rows(), profiles=spot_profiles()), revenue=revenue)
    (rec,) = got.records
    assert _people(rec)[0] == ("ceo", "John Doe", 2_001_650.0) and revenue.calls == []
    assert not hasattr(A, "_spotlight_segments") and not hasattr(A, "_with_segment")


@pytest.mark.asyncio
async def test_the_issuers_rank_by_their_leads_amount_and_the_limit_holds(real_min_rows):
    """Owner decisions 2026-11-20 ("3 - 5 companies at once"): ONE list record of every issuer, ranked by its row's
    TOTAL (Contoso's three people, $2,661,657, under FOX's one director's $3M); `limit` (the number of RECORDS) never
    cuts the list — it is one record."""
    rows = spot_rows() + [
        irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
             filed="2026-11-17", traded="2026-11-16"),
        irow(sym="GME", name="COHEN RYAN", cik="0001767470", shares=10_000, price=25.0, filed="2026-11-18",
             traded="2026-11-17")]
    profiles = {**spot_profiles(), **week_profiles()}
    got = await spot(FakeFMP(insider=rows, profiles=profiles))
    (rec,) = got.records
    assert [(c.company.symbol, round(c.total_usd)) for c in rec.companies] == [
        ("FOX", 3_000_000), ("CTSO", 2_661_657), ("GME", 250_000)]
    assert R.ledger_key(rec) == "news:insider_spotlight:FOX+CTSO+GME:2026-11-16"
    A.clear_memo()
    got = await spot(FakeFMP(insider=rows, profiles=profiles), limit=1)
    assert [R.record_to_dict(r) for r in got.records] == [R.record_to_dict(rec)]


@pytest.mark.parametrize("kw", [
    {"checked": datetime(2026, 11, 17, 10)}, {"checked": SPOT_RUN}, {"checked": date(2026, 11, 20)},
    {"checked": "2026-11-17"}, {"recent": (("CTSO",),)}, {"recent": (("ctso", "Contoso"),)},
    {"recent": (("", ""),)}, {"recent": ((1, "x"),)}, {"recent": "CTSO"}, {"recent": (("", "x" * 81),)},
    {"recent": tuple(("", f"Co {i}") for i in range(A.SPOTLIGHT_RECENT_MAX + 1))},
])
@pytest.mark.asyncio
async def test_bad_spotlight_inputs_are_value_errors(kw):
    with pytest.raises(ValueError):
        await spot(FakeFMP(insider=spot_rows(), profiles=spot_profiles()), **kw)


@pytest.mark.asyncio
async def test_the_roundups_ignore_the_spotlight_inputs():
    """ceo_buys with the spotlight inputs answers exactly as without them (they are the insider spotlight's only)."""
    base = await run("ceo_buys", FakeFMP(insider=week_rows(), profiles=week_profiles()))
    A.clear_memo()
    got = await A.candidates("ceo_buys", run_date=RUN, exclude=frozenset(), limit=5, deadline=time.monotonic() + 60,
                             deps=A.NewsDeps(fmp=FakeFMP(insider=week_rows(), profiles=week_profiles()),
                                             actions=SplitActions()),
                             spotlight_checked_through=date(2026, 11, 14), spotlight_recent=(("GME", "GameStop"),))
    assert [R.record_to_dict(r) for r in got.records] == [R.record_to_dict(r) for r in base.records]
    assert dict(got.rejections) == dict(base.rejections)


@pytest.mark.asyncio
async def test_the_spotlight_issuer_check_caps_hold(monkeypatch):
    """The cluster variant checks at most `SPOTLIGHT_AMENDMENT_MAX_ISSUERS` issuers: with every check failing, the
    walk stops there (one per-issuer read each) and the series answers nothing qualified."""
    monkeypatch.setattr(A, "SPOTLIGHT_AMENDMENT_MAX_ISSUERS", 2)
    monkeypatch.setattr(A, "SPOTLIGHT_AMENDMENT_MAX_PEOPLE", 2 * A.SPOTLIGHT_PEOPLE_PER_ISSUER)
    syms = ["AAA", "BBB", "CCC", "DDD"]
    rows = [irow(sym=s, name=f"DOE {s}", cik=f"000000{i:04d}", title="director", shares=10_000 * (5 - i), price=25.0,
                 filed="2026-11-18", traded="2026-11-17") for i, s in enumerate(syms)] + [_marker("2026-11-19")]
    profiles = {s: prof(s, f"{s.title()} Industries Inc.") for s in syms}
    issuer_feed = {A.normalize_cik(issuer_cik(s)): FMPUnavailableException("down") for s in syms}
    fmp = FakeFMP(insider=rows, profiles={**profiles, **spot_profiles()}, issuer_feed=issuer_feed)
    got = await spot(fmp)
    assert got.records == () and got.rejections == {"amendment_check_failed": 2}
    assert issuer_reads(fmp) == ["AAA", "BBB"]


# ── thirteen_f_spotlight ──────────────────────────────────────────────────────


def _soros_world(aapl_q3=(1_200_000, 240e6), *, aapl_q2=(1_000_000, 200e6), others=True, whales=None):
    """Soros' 2026-Q3 13F against Q2 (a registry filer, never a lead): AAPL as given, KO unchanged, and — with
    ``others`` — CRWV newly reported ($50M) and OXY no longer reported ($100M the quarter before)."""
    cik = SOROS
    q3 = [xrow(cik, 3, "AAPL", "037833100", *aapl_q3), xrow(cik, 3, "KO", "191216100", 1_000_000, 70e6)]
    q2 = [xrow(cik, 2, "AAPL", "037833100", *aapl_q2), xrow(cik, 2, "KO", "191216100", 1_000_000, 70e6)]
    if others:
        q3.append(xrow(cik, 3, "CRWV", "21873S108", 500_000, 50e6))
        q2.append(xrow(cik, 2, "OXY", "674599105", 2_000_000, 100e6))
    extracts = {(cik, 2026, 3): q3, (cik, 2026, 2): q2}
    whales = whales or [whale(SOROS, "Soros Fund Management")]
    for w in whales:
        if w["cik"] != SOROS:
            extracts.update(book(filer(w["cik"])))
    return registry_world(whales, extracts=extracts)


def _spot_holding_of(rec) -> tuple:
    """The LEAD holding (the largest: its symbol keys the ledger) of a 13F spotlight."""
    mv = rec.moves[0]
    return (mv.company.symbol, mv.move, mv.shares, mv.prev_shares)


@pytest.mark.asyncio
async def test_the_13f_spotlight_picks_one_filers_largest_holding():
    """Soros' book: AAPL +20% (under the spotlight's 25%), CRWV new ($50M), OXY exited ($100M the quarter before) —
    the largest dollar size (`R.thirteen_f_move_flow`) is the pick: OXY."""
    fmp, sb = _soros_world()
    got = await run("thirteen_f_spotlight", fmp, sb=sb)
    (rec,) = got.records
    assert isinstance(rec, R.ThirteenFSpotlight) and rec.series == "thirteen_f_spotlight"
    assert (rec.filer_name, rec.filer_cik, rec.period, rec.period_end, rec.filed_on, rec.amended_on) == (
        "Soros Fund Management", SOROS, "2026-Q3", date(2026, 9, 30), date(2026, 11, 14), None)
    assert _spot_holding_of(rec) == ("OXY", "no_longer_reported", None, 2_000_000.0)
    # owner feedback 2026-11-19: the list — every eligible holding, the largest dollar size first (OXY's $100M exit,
    # CRWV's $50M new holding); the ledger keys the LEAD
    assert [(m.company.symbol, m.move) for m in rec.moves] == [("OXY", "no_longer_reported"),
                                                               ("CRWV", "newly_reported")]
    # the filing's own counts: CRWV new, AAPL more (under 25%: not shown), OXY gone — 3 changes, 2 shown: "2 of …"
    assert rec.eligible_moves == 2 and rec.filing_moves == 3
    assert not R.thirteen_f_spotlight_lists_every_change(rec)
    assert R.ledger_key(rec) == f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:OXY"
    assert R.source_label(rec) == "SEC Form 13F"
    assert got.rejections == {"spotlight_change_too_small": 1}


@pytest.mark.asyncio
async def test_the_13f_spotlight_is_off_season_with_no_call():
    fmp, sb = _soros_world()
    got = await run("thirteen_f_spotlight", fmp, sb=sb, run_date=date(2026, 10, 12))
    assert got.records == () and got.skip_reason == "thirteen_f_off_season"
    assert fmp.calls == Counter() and sb.calls == Counter()


@pytest.mark.asyncio
async def test_the_lead_filer_is_reserved_for_the_main_lane_and_never_read():
    fmp, sb = _soros_world(whales=[whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A"),
                                   whale(SOROS, "Soros Fund Management")])
    got = await run("thirteen_f_spotlight", fmp, sb=sb)
    assert [r.filer_cik for r in got.records] == [SOROS]
    assert fmp.dates_ciks == [SOROS] and all(k[0] != BERKSHIRE for k in fmp.extract_keys)
    assert got.rejections["lead_filer_reserved"] == 1


@pytest.mark.parametrize("posted", [
    f"news:thirteen_f:{SOROS}:2026-Q3",                       # the main lane's roundup posted the filer
    f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:CRWV",       # a spotlight of ANOTHER holding of the filer
], ids=["roundup_posted", "spotlight_posted"])
@pytest.mark.asyncio
async def test_one_13f_post_per_filer_per_quarter_the_spotlight_skips(posted):
    fmp, sb = _soros_world()
    got = await run("thirteen_f_spotlight", fmp, sb=sb, exclude=[posted])
    assert got.records == () and got.skip_reason == "thirteen_f_spotlight_none_qualified"
    assert got.rejections == {"already_posted": 1} and fmp.dates_ciks == []


@pytest.mark.parametrize("posted, skipped", [
    (f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:OXY", True),
    (f"news:thirteen_f_spotlight:{SOROS}:2026-Q2:OXY", False),       # another quarter
    (f"news:thirteen_f_spotlight:{SCION}:2026-Q3:OXY", False),       # another filer
    ("news:thirteen_f_spotlight:junk", False),
], ids=["same_quarter", "another_quarter", "another_filer", "malformed"])
@pytest.mark.asyncio
async def test_the_main_roundup_skips_a_filer_the_spotlight_posted(posted, skipped):
    fmp, sb = _soros_world()
    got = await run("thirteen_f", fmp, sb=sb, exclude=[posted])
    assert ([r.filer_cik for r in got.records] == []) is skipped
    assert (got.rejections.get("already_posted") == 1) is skipped


def test_the_widened_exclude_maps_spotlight_keys_and_leaves_the_rest():
    base = frozenset({f"news:thirteen_f:{BERKSHIRE}:2026-Q3", "news:money_map:AAPL:2025", "junk"})
    assert A._widened_13f_exclude(base) == base                        # no spotlight key: unchanged
    wide = A._widened_13f_exclude(base | {f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:OXY"})
    assert wide - base == {f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:OXY", f"news:thirteen_f:{SOROS}:2026-Q3"}


@pytest.mark.parametrize("aapl_q3, kept", [
    ((1_250_000, 250e6), True), ((1_249_999, 249_999_800.0), False),         # +25% exactly / just under
    ((750_000, 150e6), True), ((750_001, 150_000_200.0), False),             # −25% exactly / just under
], ids=["up_25", "up_under_25", "down_25", "down_under_25"])
@pytest.mark.asyncio
async def test_a_more_or_fewer_holding_needs_a_25_percent_change(aapl_q3, kept):
    """B decision 7 / `R.THIRTEEN_F_SPOTLIGHT_MIN_CHANGE`, exact at the boundary: AAPL's row is on the list at a 25%
    change and off it just under (news-v23: beside CRWV's new holding and OXY's closed-out one — a list holds two to
    eight holdings, never one)."""
    fmp, sb = _soros_world(aapl_q3)
    got = await run("thirteen_f_spotlight", fmp, sb=sb)
    (rec,) = got.records
    aapl = [(m.move, m.shares, m.prev_shares) for m in rec.moves if m.company.symbol == "AAPL"]
    if kept:
        assert aapl == [("increased" if aapl_q3[0] > 1_000_000 else "decreased", float(aapl_q3[0]), 1_000_000.0)]
        assert got.rejections == {}
    else:
        assert aapl == [] and got.rejections == {"spotlight_change_too_small": 1}
    assert {m.company.symbol for m in rec.moves} >= {"CRWV", "OXY"}


@pytest.mark.asyncio
async def test_a_one_move_filing_is_no_13f_post_in_either_lane():
    """news-v23 (owner 2026-11-19, "the range from 2 to 6 are okay"): NO single-holding 13F post anywhere — a filing
    with one eligible holding yields nothing in the roundup and nothing in the spotlight (``too_few_holdings``; the
    walk, then the chain, moves on). Before, the spotlight posted it alone."""
    fmp, sb = _soros_world((1_500_000, 300e6), others=False)
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections.get("too_few_holdings") == 1
    A.clear_memo()
    fmp, sb = _soros_world((1_500_000, 300e6), others=False)
    got = await run("thirteen_f_spotlight", fmp, sb=sb)
    assert got.records == () and got.rejections.get("too_few_holdings") == 1
    assert got.skip_reason == "thirteen_f_spotlight_none_qualified"


@pytest.mark.asyncio
async def test_a_split_restated_holding_is_never_the_spotlight():
    """The content review's major #3: a 2:1 split the BUILD restated (1.0M → 2.0M previous shares, now 1.8M: −10%)
    stays in the roundup ("reported fewer shares" is true) — but "down from 2 million the quarter before" would print
    a count the previous 13F never reported, so the spotlight refuses it (``split_restated``, carried out of band) and
    picks among the rest."""
    split = SplitActions(splits={"AAPL": [split_row("2026-08-14", 2, 1)]})
    fmp, sb = _soros_world((1_800_000, 180e6))
    got = await run("thirteen_f", fmp, sb=sb, actions=split)
    aapl = next(m for m in got.records[0].moves if m.company.symbol == "AAPL")
    assert (aapl.move, aapl.shares, aapl.prev_shares) == ("decreased", 1_800_000.0, 2_000_000.0)    # restated
    A.clear_memo()
    split = SplitActions(splits={"AAPL": [split_row("2026-08-14", 2, 1)]})
    fmp, sb = _soros_world((1_800_000, 180e6))
    got = await run("thirteen_f_spotlight", fmp, sb=sb, actions=split)
    (rec,) = got.records
    assert _spot_holding_of(rec) == ("OXY", "no_longer_reported", None, 2_000_000.0)
    assert got.rejections == {"split_restated": 1}
    assert "R" not in {f.name[0] for f in dataclasses_fields(rec)}               # no restated flag on the record


@pytest.mark.asyncio
async def test_withheld_counts_leave_only_the_new_and_exited_holdings(caplog):
    """A filing whose more / fewer moves were withheld (a failed split lookup: both counts left out) offers only its
    newly reported and exited holdings — AAPL's +50% is never a candidate."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = _soros_world((1_500_000, 300e6))
    got = await run("thirteen_f_spotlight", fmp, sb=sb, actions=SplitActions(fail={"AAPL"}))
    (rec,) = got.records
    assert _spot_holding_of(rec) == ("OXY", "no_longer_reported", None, 2_000_000.0)
    assert got.rejections == {"split_in_window": 1}


def _spot_filing(moves, *, counts=None, amended_on=None):
    """A `ThirteenFFiling` the walkers could have built (Soros, 2026-Q3)."""
    counts = counts if counts is not None else (("newly_reported", 1), ("increased", 1), ("decreased", 0),
                                                ("no_longer_reported", 1))
    return R.ThirteenFFiling(series="thirteen_f", filer_name="Soros Fund Management", filer_cik=SOROS, filer_symbol=None,
                             period="2026-Q3", period_end=date(2026, 9, 30), filed_on=date(2026, 11, 14),
                             amended_on=amended_on, total_value_usd=1e9, position_count=10, moves=tuple(moves),
                             counts=tuple(counts))


def _spot_mv(sym, move, shares=None, prev=None, value=None, prev_value=None):
    return R.ThirteenFMove(company=R.CompanyRef(symbol=sym, name=f"{sym.title()} Co"), move=move, shares=shares,
                           prev_shares=prev, value_usd=value, listed_on=None, prev_value_usd=prev_value)


_BIG_MORE = _spot_mv("AAPL", "increased", 1_500_000.0, 1_000_000.0, 300e6)        # flow $100M
_SMALL_NEW = _spot_mv("CRWV", "newly_reported", 500_000.0, None, 50e6)            # flow $50M
_EXIT = _spot_mv("OXY", "no_longer_reported", None, 2_000_000.0, None, 80e6)      # flow $80M


def _ctx13s(**split):
    ctx = A._Ctx("thirteen_f_spotlight", RUN, frozenset(), 5, time.monotonic() + 60, A.NewsDeps())
    ctx.split_checked.update(split.get("checked", {}))
    ctx.split_restated_any.update(split.get("any", set()))
    return ctx


@pytest.mark.parametrize("checked, any_restated, pick, reason", [
    ({(SOROS, "2026-Q3"): frozenset()}, set(), "AAPL", None),                            # vouched: the largest
    ({}, set(), "OXY", "split_unchecked"),                                               # never re-checked
    ({(SOROS, "2026-Q3"): frozenset({"AAPL"})}, set(), "OXY", "split_restated"),          # restated by the build
    ({(SOROS, "2026-Q3"): frozenset({"AAPL"})}, {(SOROS, "2026-Q2")}, "OXY", "split_restated"),
    ({(SOROS, "2026-Q3"): frozenset({"KO"})}, {(SOROS, "2026-Q3")}, "OXY", "split_restated"),   # a club filing
], ids=["vouched", "unchecked", "restated_symbol", "restated_symbol_other_quarter_any", "club_any_restated"])
def test_the_holding_pick_and_its_split_gates(checked, any_restated, pick, reason):
    ctx = _ctx13s(checked=checked, any=any_restated)
    got = A._spotlight_holding(ctx, _spot_filing([_SMALL_NEW, _BIG_MORE, _EXIT]))
    assert got.moves[0].company.symbol == pick
    assert dict(ctx.rejections) == ({reason: 1} if reason else {})


def test_withheld_counts_never_carry_a_more_or_fewer_move():
    """With both counts withheld the record itself refuses a more / fewer move (`ThirteenFFiling`: a move shown needs
    its count) — the walkers drop those moves first (`_more_fewer_dropped`) — so the spotlight's own `vouched` check is
    the backstop for the one shape a record allows: counts kept but the re-check never recorded (above, `unchecked`)."""
    with pytest.raises(ValueError, match="increased count below"):
        _spot_filing([_SMALL_NEW, _BIG_MORE, _EXIT], counts=(("newly_reported", 1), ("no_longer_reported", 1)))
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    rec = _spot_filing([_SMALL_NEW, _EXIT], counts=(("newly_reported", 1), ("no_longer_reported", 1)))
    assert A._spotlight_holding(ctx, rec).moves[0].company.symbol == "OXY" and dict(ctx.rejections) == {}


def test_the_largest_flow_wins_and_ties_break_by_kind_then_symbol():
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    tied_new = _spot_mv("ZZZ", "newly_reported", 500_000.0, None, 80e6)                 # $80M, as the exit
    assert A._spotlight_holding(ctx, _spot_filing([tied_new, _EXIT])).moves[0].company.symbol == "ZZZ"     # new before exit
    assert A._spotlight_holding(ctx, _spot_filing([_SMALL_NEW, _EXIT])).moves[0].company.symbol == "OXY"
    unknown = _spot_mv("QQQ", "no_longer_reported", None, 1.0, None, None)            # no size: last
    assert A._spotlight_holding(ctx, _spot_filing([unknown, _SMALL_NEW])).moves[0].company.symbol == "CRWV"


# ── owner feedback 2026-11-19: the 13F spotlight LIST — the largest five eligible holdings of the filing ────────────


def _flow_mv(sym, move, flow, i=0):
    """A move of dollar size `flow` (`R.thirteen_f_move_flow`): more / fewer by a third of 3M shares, a new holding of
    its value, an exit of its previous value."""
    if move == "increased":
        return _spot_mv(sym, move, 3_000_000.0, 2_000_000.0, flow * 3)
    if move == "decreased":
        return _spot_mv(sym, move, 2_000_000.0, 3_000_000.0, flow * 2)
    if move == "newly_reported":
        return _spot_mv(sym, move, 1_000_000.0 + i, None, flow)
    return _spot_mv(sym, move, None, 1_000_000.0 + i, None, flow)


_EIGHT = [_flow_mv("AAA", "decreased", 80e6), _flow_mv("BBB", "newly_reported", 70e6),
          _flow_mv("CCC", "increased", 60e6), _flow_mv("DDD", "no_longer_reported", 50e6),
          _flow_mv("EEE", "decreased", 40e6), _flow_mv("FFF", "newly_reported", 30e6, 1),
          _flow_mv("GGG", "increased", 20e6), _flow_mv("HHH", "no_longer_reported", 10e6, 1)]
_EIGHT_COUNTS = (("newly_reported", 2), ("increased", 2), ("decreased", 2), ("no_longer_reported", 2))


def test_the_spotlight_list_is_every_eligible_holding_by_flow_in_the_records_order():
    """news-v23: the record holds up to `R.THIRTEEN_F_SPOTLIGHT_MAX_MOVES` (8) eligible holdings, largest flow first —
    the picture draws six and says "+2 more changes in the caption"; the ledger keys the lead."""
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    rec = A._spotlight_holding(ctx, _spot_filing(list(reversed(_EIGHT)), counts=_EIGHT_COUNTS))
    assert [m.company.symbol for m in rec.moves] == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    assert (rec.eligible_moves, rec.filing_moves) == (8, 8) and dict(ctx.rejections) == {}
    assert R.ledger_key(rec) == f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:AAA"              # the lead's symbol
    assert R.thirteen_f_spotlight_lists_every_change(rec)                                      # 8 of 8


def test_the_spotlight_list_breaks_a_tie_of_size_by_kind_then_symbol():
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    tied = [_flow_mv("ZZZ", "decreased", 50e6), _flow_mv("AAA", "decreased", 50e6), _flow_mv("MMM", "increased", 50e6),
            _flow_mv("QQQ", "no_longer_reported", 50e6), _flow_mv("YYY", "newly_reported", 50e6)]
    rec = A._spotlight_holding(ctx, _spot_filing(tied, counts=(("newly_reported", 1), ("increased", 1),
                                                                 ("decreased", 2), ("no_longer_reported", 1))))
    assert [m.company.symbol for m in rec.moves] == ["YYY", "QQQ", "MMM", "AAA", "ZZZ"]
    assert R.thirteen_f_spotlight_lists_every_change(rec)                                      # 5 of 5


def test_one_gate_drops_one_move_and_the_list_keeps_the_rest():
    """A split the build restated drops THAT holding only (``split_restated``); a change under 25% drops its own
    (``spotlight_change_too_small``); the rest stay, in order."""
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset({"AAA"})})
    small = _spot_mv("SML", "increased", 1_200_000.0, 1_000_000.0, 600e6)                   # +20%, $100M
    rec = A._spotlight_holding(ctx, _spot_filing([*_EIGHT[:4], small], counts=(
        ("newly_reported", 1), ("increased", 2), ("decreased", 1), ("no_longer_reported", 1))))
    assert [m.company.symbol for m in rec.moves] == ["BBB", "CCC", "DDD"]
    assert dict(ctx.rejections) == {"split_restated": 1, "spotlight_change_too_small": 1}
    assert (rec.eligible_moves, rec.filing_moves) == (3, 5)


def test_an_unvouched_filing_lists_only_its_new_and_exited_holdings():
    ctx = _ctx13s()                                                                            # never re-checked
    rec = A._spotlight_holding(ctx, _spot_filing(_EIGHT[:5], counts=(("newly_reported", 1), ("increased", 1),
                                                                     ("decreased", 2), ("no_longer_reported", 1))))
    assert [m.company.symbol for m in rec.moves] == ["BBB", "DDD"]
    assert dict(ctx.rejections) == {"split_unchecked": 3}


def test_withheld_counts_leave_the_filings_change_count_unknown():
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    rec = A._spotlight_holding(ctx, _spot_filing([_EIGHT[1], _EIGHT[3]], counts=(("newly_reported", 1),
                                                                                 ("no_longer_reported", 1))))
    assert [m.company.symbol for m in rec.moves] == ["BBB", "DDD"] and rec.filing_moves is None
    assert not R.thirteen_f_spotlight_lists_every_change(rec)


def test_one_eligible_holding_is_no_post_and_none_is_nothing():
    """news-v23 (owner 2026-11-19): NO single-holding 13F post — one eligible holding is ``too_few_holdings``, none is
    nothing (each record reason counted)."""
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    small = _spot_mv("SML", "increased", 1_200_000.0, 1_000_000.0, 600e6)
    assert A._spotlight_holding(ctx, _spot_filing([_EIGHT[0], small], counts=(
        ("newly_reported", 0), ("increased", 1), ("decreased", 1), ("no_longer_reported", 0)))) is None
    assert ctx.rejections.get("too_few_holdings") == 1
    # none: two holdings, each under the 25% change (a filing record itself holds two to eight — never one)
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    small2 = _spot_mv("SMM", "decreased", 900_000.0, 1_000_000.0, 450e6)
    assert A._spotlight_holding(ctx, _spot_filing([small, small2], counts=(
        ("newly_reported", 0), ("increased", 1), ("decreased", 1), ("no_longer_reported", 0)))) is None
    assert ctx.rejections.get("spotlight_change_too_small") == 2


def test_one_issuer_is_one_row_the_larger_listing_kept():
    """Share-class twins the roundup's CUSIP check could not tie (two listings of one issuer: `R.issuer_root`) are one
    row — the larger kept, the other ``share_class_overlap`` — and so are two holdings under one display name."""
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    goog = _flow_mv("GOOG", "newly_reported", 70e6)
    googl = _flow_mv("GOOGL", "newly_reported", 60e6, 1)
    twin_name = R.ThirteenFMove(company=R.CompanyRef(symbol="XYZ", name="Ccc Co"), move="decreased",
                                shares=2_000_000.0, prev_shares=3_000_000.0, value_usd=20e6, listed_on=None)
    rec = A._spotlight_holding(ctx, _spot_filing([_EIGHT[0], goog, googl, _EIGHT[2], twin_name], counts=(
        ("newly_reported", 2), ("increased", 1), ("decreased", 2), ("no_longer_reported", 0))))
    assert [m.company.symbol for m in rec.moves] == ["AAA", "GOOG", "CCC"]
    assert dict(ctx.rejections) == {"share_class_overlap": 2}


def test_a_list_the_rules_refuse_whole_is_no_post_never_its_lead_alone(monkeypatch, caplog):
    """news-v23 (owner 2026-11-19): a list the record refuses whole is ``record_invalid`` with a WARNING — never tried
    with its lead alone (that was the single-holding post, which no longer exists)."""
    caplog.set_level(logging.WARNING, logger=A.__name__)

    def picky(**kw):
        raise ValueError("ThirteenFSpotlight.moves: refused for the test")

    monkeypatch.setattr(A.R, "ThirteenFSpotlight", picky)
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    rec = A._spotlight_holding(ctx, _spot_filing(_EIGHT[:3], counts=(("newly_reported", 1), ("increased", 1),
                                                                     ("decreased", 1), ("no_longer_reported", 0))))
    assert rec is None and dict(ctx.rejections) == {"record_invalid": 1}
    assert "refused for the test" in caplog.text and "alone" not in caplog.text


def test_an_amended_filing_carries_the_amended_label():
    ctx = _ctx13s(checked={(SOROS, "2026-Q3"): frozenset()})
    rec = A._spotlight_holding(ctx, _spot_filing([_EXIT, _EIGHT[1]], amended_on=date(2026, 11, 17),
                                            counts=(("newly_reported", 1), ("no_longer_reported", 1))))
    assert rec.amended_on == date(2026, 11, 17) and R.source_label(rec) == R.THIRTEEN_F_AMENDED_LABEL


@pytest.mark.asyncio
async def test_a_club_filer_yields_its_largest_holding(monkeypatch):
    """The club path (stored builds, no FMP call for the filing): NVIDIA's stored 2026-Q3 — CRWV new ($150M) is the
    largest; AAPL's +20% and SOUN's −2% are no spotlight."""
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = club_world()
    got = await run("thirteen_f_spotlight", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    (rec,) = got.records
    assert rec.filer_cik == NVIDIA_CIK and rec.moves[0].company.symbol == "CRWV" and rec.moves[0].move == "newly_reported"
    assert rec.moves[0].listed_on == date(2026, 7, 15)
    assert got.rejections.get("spotlight_change_too_small", 0) <= 1


@pytest.mark.asyncio
async def test_the_registry_walk_is_split_out_of_band_and_never_changes_the_roundup():
    """`_split_window_problem` records what it verified on the call context only: the roundup's answer with the
    record-keeping is the answer it gave before (the book's default: CRWV, OXY, AAPL)."""
    fmp, sb = registry_world([whale(BERKSHIRE, "Berkshire Hathaway", "BRK-A")])
    got = await run("thirteen_f", fmp, sb=sb)
    assert [(m.company.symbol, m.move) for m in got.records[0].moves] == [
        ("CRWV", "newly_reported"), ("OXY", "no_longer_reported"), ("AAPL", "increased")]


# ── review of the 13F list (2026-11-19): "every change" is the filing's only when its counts can vouch for it ──────
#
# `filing_moves` sums the four counted kinds; the list states its counts AS the filing's only when that equals the
# rows. A count that leaves a change out must therefore make it unknown (`ctx.counts_partial` → None): an option /
# note row on either book (the builder diffs share rows only; `_option_moves_dropped` even LOWERS a count), a
# `corporate_action` row (a share count the diff could not separate from a split), and — a club filer, whose stored
# builds keep no option row — any excluded row on either stored build.

RXRX_CUSIP = "75629V104"


def _soros_extracts(*, q3_extra=(), q2_extra=(), ko=None):
    """Soros' Q3 / Q2 books with AAPL +50% ($100M of shares), CRWV newly reported ($50M) and OXY exited ($100M): three
    counted changes, all spotlight-eligible — plus ``q3_extra`` / ``q2_extra`` rows; ``ko`` = (Q2, Q3) (shares, value)
    of KO (default: unchanged)."""
    (k2, k3) = ko if ko is not None else ((1_000_000, 70e6), (1_000_000, 70e6))
    q3 = [xrow(SOROS, 3, "AAPL", "037833100", 1_500_000, 300e6), xrow(SOROS, 3, "KO", "191216100", *k3),
          xrow(SOROS, 3, "CRWV", "21873S108", 500_000, 50e6), *q3_extra]
    q2 = [xrow(SOROS, 2, "AAPL", "037833100", 1_000_000, 200e6), xrow(SOROS, 2, "KO", "191216100", *k2),
          xrow(SOROS, 2, "OXY", "674599105", 2_000_000, 100e6), *q2_extra]
    return {(SOROS, 2026, 3): q3, (SOROS, 2026, 2): q2}


async def _soros_spotlight(extracts, **kw):
    fmp, sb = registry_world([whale(SOROS, "Soros Fund Management")], extracts=extracts)
    fmp.profiles["RXRX"] = prof("RXRX", "Recursion Pharmaceuticals, Inc.", exchange="NASDAQ")
    fmp.profiles["SPY"] = prof("SPY", "SPDR S&P 500 ETF Trust", isEtf=True, exchange="AMEX")
    return await run("thirteen_f_spotlight", fmp, sb=sb, **kw)


@pytest.mark.asyncio
async def test_a_clean_registry_filing_whose_rows_are_every_change_says_so():
    """Control: no option row, no corporate action — the three counted changes are the three rows shown, so the list
    may state its counts as the filing's."""
    got = await _soros_spotlight(_soros_extracts())
    (rec,) = got.records
    assert sorted(m.company.symbol for m in rec.moves) == ["AAPL", "CRWV", "OXY"]
    assert rec.filing_moves == 3 and R.thirteen_f_spotlight_lists_every_change(rec)


@pytest.mark.parametrize("q3_extra, q2_extra, rejected", [
    # RXRX newly reported in SHARES while the previous book held RXRX CALLS: the move is dropped
    # (`move_has_options`) and its count lowered 1 → 0, so the counts sum to the three rows shown — but the filing
    # also reports RXRX shares
    ([xrow(SOROS, 3, "RXRX", RXRX_CUSIP, 5_000_000, 60e6)],
     [xrow(SOROS, 2, "RXRX", RXRX_CUSIP, 100_000, 2e6, putCallShare="Call")], {"move_has_options": 1}),
    # a PUT on another issuer and no share move at all: an option position the builder never diffs
    ([xrow(SOROS, 3, "SPY", "78462F103", 10_000, 6e6, putCallShare="Put")], [], {}),
    # a NOTE (PRN) row on the previous book only
    ([], [xrow(SOROS, 2, "RXRX", "75629VAB1", 1_000, 5e6, sharesType="PRN")], {}),
], ids=["option_dropped_new_move", "put_on_another_issuer", "note_on_previous_book"])
@pytest.mark.asyncio
async def test_option_or_note_rows_leave_the_filings_change_count_unknown(q3_extra, q2_extra, rejected):
    got = await _soros_spotlight(_soros_extracts(q3_extra=q3_extra, q2_extra=q2_extra))
    (rec,) = got.records
    assert sorted(m.company.symbol for m in rec.moves) == ["AAPL", "CRWV", "OXY"]
    assert rec.filing_moves is None and not R.thirteen_f_spotlight_lists_every_change(rec)
    assert dict(got.rejections) == rejected


@pytest.mark.asyncio
async def test_a_corporate_action_row_leaves_the_filings_change_count_unknown():
    """KO split 2:1 in the quarter while the filer's count went 1.0M → 2.6M (+30% beyond the split): the diff cannot
    separate the split from the flow and books it `corporate_action` — a share count that changed, counted in none of
    the four kinds, so "the filing's 3 holding changes" would be false."""
    split = SplitActions(splits={"KO": [split_row("2026-08-14", 2, 1)]})
    got = await _soros_spotlight(_soros_extracts(ko=((1_000_000, 70e6), (2_600_000, 91e6))), actions=split)
    (rec,) = got.records
    assert sorted(m.company.symbol for m in rec.moves) == ["AAPL", "CRWV", "OXY"]
    assert rec.filing_moves is None and not R.thirteen_f_spotlight_lists_every_change(rec)
    A.clear_memo()
    # the roundup is unchanged by it (the main lane never reads `counts_partial`)
    split = SplitActions(splits={"KO": [split_row("2026-08-14", 2, 1)]})
    fmp, sb = registry_world([whale(SOROS, "Soros Fund Management")],
                             extracts=_soros_extracts(ko=((1_000_000, 70e6), (2_600_000, 91e6))))
    roundup = await run("thirteen_f", fmp, sb=sb, actions=split)
    assert dict(roundup.records[0].counts) == {"newly_reported": 1, "increased": 1, "decreased": 0,
                                               "no_longer_reported": 1}


def _club_every_world(*, corporate_action=0, current_excluded=0, previous_excluded=0):
    """NVIDIA's stored 2026-Q3: CRWV newly reported and RXRX no longer reported — the only two changes its counts
    report; both stored builds' `excluded_rows` as given (None: the column missing)."""
    ch = lambda **kw: SimpleNamespace(**{"newly_listed": False, "shares": None, "prev_shares": None,  # noqa: E731
                                         "value": None, **kw})
    changes = [ch(symbol="CRWV", name="CoreWeave", change="newly_reported", shares=1_000_000.0, value=150e6,
                  newly_listed=True),
               ch(symbol="RXRX", name="Recursion", change="no_longer_reported", prev_shares=7_000_000.0)]
    counts = SimpleNamespace(newly_reported=1, increased=0, decreased=0, no_longer_reported=1, unchanged=3,
                             corporate_action=corporate_action)
    club, sb = club_world(changes=changes, company_over={"change_counts": counts})
    previous, current = sb.tables["trillion_club_filings"]
    for row, value in ((previous, previous_excluded), (current, current_excluded)):
        if value is not None:
            row["excluded_rows"] = value
    return club, sb


@pytest.mark.parametrize("kw, every", [
    ({}, True),
    ({"corporate_action": 1}, False),
    ({"corporate_action": None}, False),             # unreadable
    ({"current_excluded": 2}, False),                # an option / note row (or another exclusion) this quarter
    ({"previous_excluded": 1}, False),               # ... or the quarter before
    ({"previous_excluded": None}, False),            # the column missing: cannot vouch
], ids=["clean", "corporate_action", "corporate_action_unreadable", "current_excluded", "previous_excluded",
        "previous_excluded_missing"])
@pytest.mark.asyncio
async def test_a_club_filing_states_every_change_only_when_its_stored_builds_can_vouch(monkeypatch, kw, every):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = _club_every_world(**kw)
    got = await run("thirteen_f_spotlight", FakeFMP(profiles=thirteen_f_profiles()), club=club, sb=sb)
    (rec,) = got.records
    assert [(m.company.symbol, m.move) for m in rec.moves] == [("CRWV", "newly_reported"),
                                                               ("RXRX", "no_longer_reported")]
    assert R.thirteen_f_spotlight_lists_every_change(rec) is every
    assert rec.filing_moves == (2 if every else None)


# The spotlight's record is cut by dollar size alone, AFTER its own gates (review 2026-11-19): the roundup's cut kept
# each kind's largest (`_select_moves`, two of each kind when four are present) and the spotlight's 25% floor ran
# after it, so a filing whose largest changes were all reductions listed $10-50M holdings in place of $0.6-0.8B ones,
# and a 10-25% trim held a slot it was then dropped from. Review 2026-11-20: the roundup's cut is by size alone too.

def _price_steady(sym: str, cusip: str, q2: float, q3: float, price: float):
    """A holding at one price a share both quarters (no split suspect): ``q2`` / ``q3`` share counts (0 = absent)."""
    rows = {}
    if q2:
        rows[2] = xrow(SOROS, 2, sym, cusip, q2, q2 * price)
    if q3:
        rows[3] = xrow(SOROS, 3, sym, cusip, q3, q3 * price)
    return rows


def _dominated_book():
    """Two 15% trims worth $1.2B / $1.1B of shares, six 50% reductions of $1.0B → $0.5B, and two each of small new
    holdings ($50M, $40M), exits ($30M, $20M) and 50% increases ($10M, $9M) — every one at a steady price."""
    specs = []
    for i, flow in enumerate((1.2e9, 1.1e9)):                        # −15%: flow = 0.15M × price
        specs.append((f"TR{'AB'[i]}", 1_000_000, 850_000, flow / 150_000))
    for i, flow in enumerate((1.0e9, 0.9e9, 0.8e9, 0.7e9, 0.6e9, 0.5e9)):   # −50%: flow = 1M × price
        specs.append((f"DQ{'ABCDEF'[i]}", 2_000_000, 1_000_000, flow / 1_000_000))
    for i, value in enumerate((50e6, 40e6)):
        specs.append((f"NW{'AB'[i]}", 0, 1_000_000, value / 1_000_000))
    for i, value in enumerate((30e6, 20e6)):
        specs.append((f"XT{'AB'[i]}", 1_000_000, 0, value / 1_000_000))
    for i, flow in enumerate((10e6, 9e6)):                            # +50%: flow = 0.5M × price
        specs.append((f"IN{'AB'[i]}", 1_000_000, 1_500_000, flow / 500_000))
    q3, q2 = [], []
    for j, (sym, prev, cur, price) in enumerate(specs):
        cusip = f"{100000 + j * 1111:06d}10{j % 10}"
        rows = _price_steady(sym, cusip, prev, cur, price)
        if 3 in rows:
            q3.append(rows[3])
        if 2 in rows:
            q2.append(rows[2])
    profiles = {sym: prof(sym, f"{sym.title()} Holdings Corp") for sym, *_ in specs}
    return {(SOROS, 2026, 3): q3, (SOROS, 2026, 2): q2}, profiles


@pytest.mark.asyncio
async def test_the_spotlight_lists_the_filings_largest_eligible_changes_whatever_their_kinds():
    extracts, profiles = _dominated_book()
    fmp, sb = registry_world([whale(SOROS, "Soros Fund Management")], extracts=extracts)
    fmp.profiles = profiles
    got = await run("thirteen_f_spotlight", fmp, sb=sb)
    (rec,) = got.records
    # the largest ELIGIBLE changes (news-v23: up to eight in the record, six on the picture): the 15% trims are under
    # the spotlight's 25% floor, refused before the cut
    assert [(m.company.symbol, m.move) for m in rec.moves][:5] == [(f"DQ{c}", "decreased") for c in "ABCDE"]
    assert len(rec.moves) == 8 and all(m.company.symbol[:2] != "TR" for m in rec.moves)
    assert got.rejections["spotlight_change_too_small"] == 2 and "split_unchecked" not in got.rejections
    assert rec.eligible_moves == 8                              # the record's eight largest eligible moves
    assert not R.thirteen_f_spotlight_lists_every_change(rec)
    assert R.ledger_key(rec) == f"news:thirteen_f_spotlight:{SOROS}:2026-Q3:DQA"
    A.clear_memo()
    # review 2026-11-20 (confirmed): the roundup's cut is by size alone too — its record holds the filing's eight
    # largest changes (the trims kept: its floor is 10%), never each kind's top two ($10-50M rows in place of $0.5-1.0B
    # reductions), and its picture draws the six largest of them, its "+2 more" the next two
    fmp, sb = registry_world([whale(SOROS, "Soros Fund Management")], extracts=extracts)
    fmp.profiles = profiles
    roundup = await run("thirteen_f", fmp, sb=sb)
    (big,) = roundup.records
    assert {m.company.symbol for m in big.moves} == {"TRA", "TRB", "DQA", "DQB", "DQC", "DQD", "DQE", "DQF"}
    assert Counter(m.move for m in big.moves) == {"decreased": 8}
    from app.services.marketing import news_templates as NTPL
    out = NTPL.compose(big, run_date=RUN, store_state="live", allow_x_url=False)
    drawn = [r["chip"] for r in out["image_spec"]["sections"][0]["rows"]]
    assert drawn == ["TRA", "TRB", "DQA", "DQB", "DQC", "DQD"], drawn
    assert out["image_spec"]["sections"][0]["more"] == "+2 more changes in the caption"
    assert not {"NWA", "NWB", "XTA", "XTB", "INA", "INB"} & set(json.dumps(out))


# ── news-v23: the 13F BUY-OUT CHECK and the two-holding floor (owner 2026-11-19, "Closed out + buy-out check") ────
#
# A holding the latest 13F no longer lists is "Closed out" only while its company still trades: a company bought out,
# merged or delisted during or after the quarter is DROPPED before the record's final cut (so the next eligible move
# takes its place) — never called closed out. Two licensed sources ("3 Company Information"), both fail closed: the
# profile's `isActivelyTrading` must be exactly True, and FMP's delisted-companies list must hold no row of the symbol
# dated on or after the quarter's first day (Jul 1, 2026 for 2026-Q3). Soros' book: AAPL +50% ($100M of flow), CRWV
# new ($50M), OXY closed out ($100M the quarter before).


def _delisted_row(sym, day):
    return {"symbol": sym, "companyName": f"{sym} Co", "exchange": "NYSE", "ipoDate": "2000-01-03",
            "delistedDate": day}


def _soros_moves(got):
    return [(m.company.symbol, m.move) for m in got.records[0].moves] if got.records else []


@pytest.mark.parametrize("series", ["thirteen_f", "thirteen_f_spotlight"])
@pytest.mark.asyncio
async def test_a_closed_out_holding_whose_company_still_trades_stays(series):
    fmp, sb = _soros_world((1_500_000, 300e6))
    got = await run(series, fmp, sb=sb)
    assert ("OXY", "no_longer_reported") in _soros_moves(got)
    assert fmp.calls["delisted"] == 1 and fmp.delisted_pages == [0]          # one page reaches past Jul 1
    assert not {k for k in got.rejections if k.startswith("closed_out")}


@pytest.mark.parametrize("series", ["thirteen_f", "thirteen_f_spotlight"])
@pytest.mark.parametrize("day", ["2026-07-01", "2026-09-29", "2026-11-12", "2027-01-15"],
                         ids=["quarter_start", "in_quarter", "after_quarter", "scheduled"])
@pytest.mark.asyncio
async def test_a_closed_out_holding_bought_out_during_or_after_the_quarter_is_dropped(series, day, caplog):
    """FMP's delisted list dates OXY on or after the quarter's first day (a merger closed in the quarter, after it, or
    one scheduled): OXY is dropped (``closed_out_delisted``, WARNING with the filer and the symbol) — the post keeps
    AAPL and CRWV; the day before the quarter (a row of an older delisting under the same symbol) never drops it."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = _soros_world((1_500_000, 300e6))
    fmp.delisted = [[_delisted_row("OXY", day), _delisted_row("ZZOLD", "2001-01-02")]]
    got = await run(series, fmp, sb=sb)
    assert ("OXY", "no_longer_reported") not in _soros_moves(got)
    assert {sym for sym, _k in _soros_moves(got)} == {"AAPL", "CRWV"}
    assert got.rejections.get("closed_out_delisted") == 1
    assert f"cik={SOROS}" in caplog.text and "closed-out OXY" in caplog.text and "(closed_out_delisted)" in caplog.text
    A.clear_memo()
    fmp, sb = _soros_world((1_500_000, 300e6))
    fmp.delisted = [[_delisted_row("OXY", "2026-06-30"), _delisted_row("ZZOLD", "2001-01-02")]]
    assert ("OXY", "no_longer_reported") in _soros_moves(await run(series, fmp, sb=sb))


@pytest.mark.parametrize("trading, reason", [(None, "closed_out_not_trading"), (False, "inactive"),
                                             ("true", "inactive"), (1, "inactive")],
                         ids=["absent", "false", "string", "int"])
@pytest.mark.asyncio
async def test_a_closed_out_holding_whose_profile_does_not_say_it_trades_is_dropped(trading, reason, caplog):
    """The profile's flag must be exactly True: an ABSENT flag — which the shared profile gate lets through for every
    other row — is "cannot vouch" for a closed-out holding (``closed_out_not_trading``, WARNING naming it); False or
    junk is refused earlier by that gate (``inactive``). Either way the row is dropped, fail closed."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = _soros_world((1_500_000, 300e6))
    if trading is None:
        fmp.profiles["OXY"].pop("isActivelyTrading")
    else:
        fmp.profiles["OXY"]["isActivelyTrading"] = trading
    got = await run("thirteen_f", fmp, sb=sb)
    assert _soros_moves(got) == [("CRWV", "newly_reported"), ("AAPL", "increased")]
    assert got.rejections.get(reason) == 1
    if reason == "closed_out_not_trading":
        assert "(closed_out_not_trading)" in caplog.text and "closed-out OXY" in caplog.text
    assert fmp.calls["delisted"] == 0                      # nothing closed out is left to check: no list read


@pytest.mark.parametrize("pages", [
    [FMPUnavailableException("delisted-companies down")],
    [[]],                                                                     # an empty page 0
    [[_delisted_row("ZZNEW", "2026-11-01")]],                                 # a short page that never reaches Jul 1
    [{"error": "not a list"}],
    [[{"symbol": "ZZ", "delistedDate": None}] * 100],                         # no readable date
    [[_delisted_row(f"Z{i:03d}", "2026-10-01") for i in range(100)]] * A.CLOSED_OUT_DELISTED_MAX_PAGES,   # the cap
], ids=["outage", "empty", "short", "not_a_list", "undated", "cap"])
@pytest.mark.asyncio
async def test_when_the_buy_out_check_cannot_run_every_closed_out_holding_is_dropped(pages, caplog):
    """FAIL CLOSED: a delisted-companies list that cannot vouch back to the quarter's first day — an outage, an empty
    or short list, a malformed or undated page, the page cap — drops every closed-out holding of the filing
    (``closed_out_unchecked``, WARNING naming the filer and the symbol); the other holdings stay."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp, sb = _soros_world((1_500_000, 300e6))
    fmp.delisted = pages
    got = await run("thirteen_f", fmp, sb=sb)
    assert _soros_moves(got) == [("CRWV", "newly_reported"), ("AAPL", "increased")]
    assert got.rejections.get("closed_out_unchecked") == 1
    assert "could not be read back" in caplog.text and f"cik={SOROS}" in caplog.text
    assert "(closed_out_unchecked)" in caplog.text
    # a list that ENDS (a short page) before the quarter is never paged past its end
    if isinstance(pages[0], list) and len(pages[0]) == 1:
        assert fmp.delisted_pages == [0]


@pytest.mark.asyncio
async def test_the_delisted_list_is_read_once_and_paged_only_as_far_as_the_quarter():
    """The list is memoized (both 13F series share it) and read newest first only until a page reaches past the
    quarter's first day: two pages here, then the roundup and the spotlight read nothing more."""
    newer = [_delisted_row(f"Z{i:03d}", "2026-10-01") for i in range(100)]
    fmp, sb = _soros_world((1_500_000, 300e6))
    fmp.delisted = [newer, [_delisted_row("ZZOLD", "2026-05-01")]]
    await run("thirteen_f", fmp, sb=sb)
    assert fmp.delisted_pages == [0, 1]
    got = await run("thirteen_f_spotlight", fmp, sb=sb)     # the memo answers the other series: no new read
    assert fmp.delisted_pages == [0, 1] and ("OXY", "no_longer_reported") in _soros_moves(got)


@pytest.mark.asyncio
async def test_a_filing_left_with_one_holding_after_the_buy_out_check_is_no_post():
    """The two-holding floor counts AFTER every gate: Soros with AAPL under the roundup's floor (+5%) keeps CRWV and
    OXY; OXY delisted in the quarter leaves CRWV alone — no record (``too_few_holdings``), never a one-row list."""
    fmp, sb = _soros_world((1_050_000, 210e6))
    fmp.delisted = [[_delisted_row("OXY", "2026-08-20"), _delisted_row("ZZOLD", "2001-01-02")]]
    got = await run("thirteen_f", fmp, sb=sb)
    assert got.records == () and got.rejections.get("too_few_holdings") == 1
    assert got.rejections.get("closed_out_delisted") == 1 and got.skip_reason == "thirteen_f_none_qualified"


# ── company_facts (workstream C's art brief) ───────────────────────────────────


@pytest.mark.asyncio
async def test_company_facts_are_the_profiles_name_free_sector_and_industry():
    fmp = FakeFMP(profiles={"CTSO": prof("CTSO", "Contoso Corporation", industry="Software - Infrastructure")})
    got = await A.company_facts("CTSO", deps=A.NewsDeps(fmp=fmp))
    assert got == {"sector": "Technology", "industry": "Software - Infrastructure"}
    assert fmp.calls["profiles"] == 1
    # the memo a collector filled answers with no call
    again = await A.company_facts("CTSO", deps=A.NewsDeps(fmp=fmp))
    assert again == got and fmp.calls["profiles"] == 1


@pytest.mark.parametrize("profile, want", [
    ({"sector": "Technology", "industry": "https://zqx.example/x"}, {"sector": "Technology"}),
    ({"sector": "", "industry": None}, None),
    ({"sector": 7, "industry": ["x"]}, None),
    ({"sector": "  Consumer   Cyclical ", "industry": "Banks—Regional"},
     {"sector": "Consumer Cyclical", "industry": "Banks—Regional"}),
    ({"sector": "x" * 61}, None),
])
@pytest.mark.asyncio
async def test_company_facts_keep_only_plain_names(profile, want):
    base = prof("CTSO", "Contoso Corporation")
    base.pop("sector", None)
    fmp = FakeFMP(profiles={"CTSO": {**base, **profile}})
    assert await A.company_facts("CTSO", deps=A.NewsDeps(fmp=fmp)) == want


@pytest.mark.parametrize("symbol", ["ctso", "CTSO.W", "", None, 7, "CTSOW"])
@pytest.mark.asyncio
async def test_company_facts_refuse_a_bad_symbol_without_a_call(symbol, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = FakeFMP(profiles={})
    assert await A.company_facts(symbol, deps=A.NewsDeps(fmp=fmp)) is None
    assert fmp.calls["profiles"] == 0 and "company_facts" in caplog.text


@pytest.mark.parametrize("answer", [FMPUnavailableException("down"), {}], ids=["read_failed", "no_profile"])
@pytest.mark.asyncio
async def test_company_facts_never_raise(answer, caplog):
    caplog.set_level(logging.WARNING, logger=A.__name__)
    fmp = FakeFMP(profiles=answer)
    assert await A.company_facts("CTSO", deps=A.NewsDeps(fmp=fmp)) is None
    assert "CTSO" in caplog.text


@pytest.mark.asyncio
async def test_company_facts_time_out_to_none():
    class Slow(FakeFMP):
        async def get_company_profiles_batch(self, symbols):
            await asyncio.sleep(5)
            return []

    assert await A.company_facts("CTSO", timeout=0.05, deps=A.NewsDeps(fmp=Slow())) is None
    await _settle_leads()


# ── the insider spotlight's defensive paths (hardening, 2026-11-15) ────────────


def _person(role, name, amount, filed=date(2026, 11, 18), *, sym="CTSO", company="Contoso"):
    return R.InsiderPurchase(company=R.CompanyRef(symbol=sym, name=company), role=role, person_name=name,
                             amount_usd=amount, shares=amount / 100.0, purchases=1,
                             earliest_trade_date=filed - timedelta(days=1), latest_trade_date=filed - timedelta(days=1),
                             filing_dates=(filed,), holding="direct", amended=False,
                             cost_per_share_min_usd=100.0, cost_per_share_max_usd=100.0)


def test_a_cluster_the_row_refuses_is_tried_with_its_lead_alone(caplog):
    """Defensive (the gates before it make it rare): two CEOs in one group — the company ROW refuses the cluster, so
    the lead is offered ALONE (fewer others is never a false "at least N"); a lead the row refuses even alone (no filed
    cost per share) is ``record_invalid``. Owner decisions 2026-11-20: the window check moved to the LIST."""
    caplog.set_level(logging.WARNING, logger=A.__name__)
    ctx = A._Ctx("insider_spotlight", SPOT_RUN, frozenset(), 5, time.monotonic() + 60, A.NewsDeps())
    people = [_person("ceo", "John Doe", 2e6), _person("ceo", "Jane Roe", 1e6)]
    row = A._spotlight_company(ctx, people)
    assert row.lead.person_name == "John Doe" and row.others == () and dict(ctx.rejections) == {}
    assert "tried with its lead alone" in caplog.text
    no_cost = [dataclasses.replace(_person("ceo", "John Doe", 2e6), cost_per_share_min_usd=None,
                                   cost_per_share_max_usd=None)]
    assert A._spotlight_company(ctx, no_cost) is None
    assert dict(ctx.rejections) == {"record_invalid": 1}


def test_one_person_once_across_the_insider_list(real_min_rows):
    """`_spotlight_list`: a named person already in an earlier (larger) row is dropped from a later one (`same_person`:
    one buyer at two affiliated issuers), the next person of that issuer then leads; rows sorted by TOTAL, at most
    `R.INSIDER_MAX_ROWS`."""
    ctx = A._Ctx("insider_spotlight", SPOT_RUN, frozenset(), 5, time.monotonic() + 60, A.NewsDeps())
    a = [_person("ceo", "John Doe", 2e6, sym="AAA", company="Alpha")]
    b = [_person("director", "John Doe", 1.5e6, sym="BBB", company="Beta"),
         _person("cfo", "Ann Lee", 3e5, sym="BBB", company="Beta")]
    c = [_person("director", None, 9e5, sym="CCC", company="Gamma")]
    rows = A._spotlight_list(ctx, [(None, a), (None, b), (None, c)])
    assert [(r.company.symbol, r.lead.person_name) for r in rows] == [("AAA", "John Doe"), ("CCC", None),
                                                                      ("BBB", "Ann Lee")]
    assert dict(ctx.rejections) == {"same_person": 1}
    many = [(None, [_person("director", None, 1e6 - i, sym=f"S{c}", company=f"Co {c}")]) for i, c in
            enumerate("ABCDEFGHIJ")]
    assert len(A._spotlight_list(ctx, many)) == R.INSIDER_MAX_ROWS == 5           # the five largest (owner's cap)


@pytest.mark.asyncio
async def test_a_failed_per_issuer_read_drops_that_issuer_and_the_next_one_stands():
    """`amendment_check_failed` for every person of an issuer (its read failed) drops the issuer, logged; the next
    issuer in rank order is checked and kept — the call's other records never depend on it."""
    rows = spot_rows() + [irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000,
                               price=30.0, filed="2026-11-17", traded="2026-11-16")]
    feed = {_CTSO_ISSUER: FMPUnavailableException("issuer read down")}
    got = await spot(FakeFMP(insider=rows, profiles={**spot_profiles(), **week_profiles()}, issuer_feed=feed))
    assert [c.company.symbol for c in got.records[0].companies] == ["FOX"]
    assert got.rejections == {"amendment_check_failed": 3}                     # CTSO's three people


# ── news-v23: one post of several companies (the restyle, owner 2026-11-19) ──────────────────────────────────────
#
# "combine 2 to 4 companies at once" (earnings) and "2-3 companies at once" (Congress): the adapter answers ONE record
# — `R.EarningsDay` / `R.CongressMonth` — of the companies that passed every per-company gate, the largest (the most
# members) first; fewer than two is no post, with its own skip reason. The tests above read those records row by row
# under `_per_company_bounds`; these pin the REAL bounds.


def _v23_ctx(series, run_date):
    return A._Ctx(series, run_date, frozenset(), 5, time.monotonic() + 60, A.NewsDeps(fmp=None))


def _v23_report(sym, name, a=1.5, e=1.0):
    return R.EarningsReport(series="earnings", company=R.CompanyRef(symbol=sym, name=name),
                            report_date=date(2027, 1, 12), period_end=date(2026, 12, 31), eps_actual=a,
                            eps_estimate=e, revenue_actual=1.05e9, revenue_estimate=1.0e9)


def _v23_count(sym, name, members):
    return R.CongressCount(series="congress_count", company=R.CompanyRef(symbol=sym, name=name), month="2026-11",
                           members=members, fetched_on=CONGRESS_RUN)


def _v23_calendar(n):
    """``n`` clean reports on 2027-01-12, caps $9B, $8B, … (the larger first), each a clear 50% surprise."""
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"][:n]
    cal = {"2027-01-12": [erow(s, "2027-01-12", 1.5, 1.0) for s in syms]}
    profiles = {s: prof(s, f"{s.title()} Industries Inc.", cap=(9 - i) * 1e9) for i, s in enumerate(syms)}
    return cal, profiles


@pytest.mark.asyncio
async def test_news_v23_an_earnings_call_is_one_post_of_two_to_four_companies():
    fmp = FakeFMP2b(calendar=earnings_calendar(), profiles=earnings_profiles())
    got = await run("earnings", fmp, run_date=EARN_RUN)
    (rec,) = got.records
    assert isinstance(rec, R.EarningsDay) and got.skip_reason is None
    assert [r.company.symbol for r in rec.reports] == ["BBB", "III", "AAA"]          # the largest first
    assert R.ledger_key(rec) == "news:earnings:BBB:2027-01-11+III:2027-01-07+AAA:2027-01-12"
    # Six clean companies: the post holds the four largest; the reads stop once four are held.
    A.clear_memo()
    cal, profiles = _v23_calendar(6)
    fmp = FakeFMP2b(calendar=cal, profiles=profiles)
    got = await run("earnings", fmp, run_date=EARN_RUN)
    (rec,) = got.records
    assert [r.company.symbol for r in rec.reports] == ["AAA", "BBB", "CCC", "DDD"]
    assert R.EARNINGS_DAY_COMPANIES == (2, 4) and len(fmp.statement_calls) == 4


@pytest.mark.asyncio
async def test_news_v23_one_qualifying_earnings_company_is_no_post():
    cal, profiles = _v23_calendar(1)
    fmp = FakeFMP2b(calendar=cal, profiles=profiles)
    got = await run("earnings", fmp, run_date=EARN_RUN)
    assert got.records == () and got.skip_reason == "earnings_too_few_companies"
    assert "earnings_too_few_companies" in R.SKIP_REASONS
    # none at all: the existing reason, never the new one
    A.clear_memo()
    fmp = FakeFMP2b(calendar={"2027-01-12": []}, profiles={})
    got = await run("earnings", fmp, run_date=EARN_RUN)
    assert got.records == () and got.skip_reason == "earnings_none_qualified"


@pytest.mark.asyncio
async def test_news_v23_a_posted_several_company_key_marks_every_company_posted():
    """Each company a posted several-company key names is `already_posted` for its window — and a malformed key is
    skipped whole (never raises, never half-read)."""
    cal, profiles = _v23_calendar(4)
    posted = "news:earnings:AAA:2027-01-12+BBB:2027-01-12"
    fmp = FakeFMP2b(calendar=cal, profiles=profiles)
    got = await run("earnings", fmp, run_date=EARN_RUN, exclude={posted})
    (rec,) = got.records
    assert [r.company.symbol for r in rec.reports] == ["CCC", "DDD"]
    assert got.rejections["already_posted"] == 2
    # Three of the four posted (two keys): one left → no post.
    A.clear_memo()
    fmp = FakeFMP2b(calendar=cal, profiles=profiles)
    got = await run("earnings", fmp, run_date=EARN_RUN, exclude={posted, "news:earnings:CCC:2027-01-12"})
    assert got.records == () and got.skip_reason == "earnings_too_few_companies"
    assert got.rejections["already_posted"] == 3
    # A malformed key (a junk item) marks NONE of its companies.
    assert A._posted_in_window(frozenset({"news:earnings:AAA:2027-01-12+junk"}), [date(2027, 1, 12)]) == set()
    assert A._posted_in_window(frozenset({posted}), [date(2027, 1, 12)]) == {"AAA", "BBB"}


def test_news_v23_one_issuer_is_drawn_once_in_an_earnings_post():
    ctx = _v23_ctx("earnings", EARN_RUN)
    got = A._earnings_day(ctx, [_v23_report("AAA", "Alpha Holdings"), _v23_report("AAB", "alpha holdings"),
                                _v23_report("CCC", "Gamma Corp")])
    (rec,) = got.records
    assert [r.company.symbol for r in rec.reports] == ["AAA", "CCC"]                # same display name: left out
    assert got.rejections == {"share_class_overlap": 1}
    ctx = _v23_ctx("earnings", EARN_RUN)
    got = A._earnings_day(ctx, [_v23_report("BRK-A", "Berkshire Hathaway A"),
                                _v23_report("BRK-B", "Berkshire Hathaway B")])
    assert got.records == () and got.skip_reason == "earnings_too_few_companies"   # same issuer root: one left
    # five held reports: the first four
    ctx = _v23_ctx("earnings", EARN_RUN)
    got = A._earnings_day(ctx, [_v23_report(s, f"{s} Corp") for s in ("AAA", "BBB", "CCC", "DDD", "EEE")])
    assert [r.company.symbol for r in got.records[0].reports] == ["AAA", "BBB", "CCC", "DDD"]


@pytest.mark.asyncio
async def test_news_v23_a_congress_month_is_one_post_of_two_or_three_companies():
    senate = [crow("senate", s, "2026-11-20", f"s{i}") for s in ("AAPL", "MSFT", "NVDA", "AMZN") for i in range(1, 3)]
    senate += [crow("senate", "AAPL", "2026-11-21", "s3"), crow("senate", "AAPL", "2026-11-21", "s4"),
               crow("senate", "MSFT", "2026-11-21", "s3")]
    profiles = {**congress_profiles(), "AMZN": prof("AMZN", "Amazon.com, Inc.", exchange="NASDAQ", cap=2e12)}
    fmp = congress_fmp(senate=cfeed("senate", senate), house=cfeed("house", []), profiles=profiles)
    got = await run("congress_count", fmp, run_date=CONGRESS_RUN)
    (rec,) = got.records
    assert isinstance(rec, R.CongressMonth) and rec.month == "2026-11" and got.skip_reason is None
    # the most members first, then the symbol; the fourth company (2 members, symbol last) is left out
    assert [(c.company.symbol, c.members) for c in rec.counts] == [("AAPL", 4), ("MSFT", 3), ("AMZN", 2)]
    assert R.CONGRESS_MONTH_COMPANIES == (2, 3) and R.ledger_key(rec) == CONGRESS_KEY


@pytest.mark.asyncio
async def test_news_v23_one_qualifying_congress_company_is_no_post():
    senate = [crow("senate", "AAPL", "2026-11-20", "s1"), crow("senate", "AAPL", "2026-11-21", "s2"),
              crow("senate", "MSFT", "2026-11-20", "s1")]                       # MSFT: one member — never counted
    fmp = congress_fmp(senate=cfeed("senate", senate), house=cfeed("house", []))
    got = await run("congress_count", fmp, run_date=CONGRESS_RUN)
    assert got.records == () and got.skip_reason == "congress_too_few_companies"
    assert "congress_too_few_companies" in R.SKIP_REASONS
    ctx = _v23_ctx("congress_count", CONGRESS_RUN)
    got = A._congress_month_record(ctx, "2026-11", [])
    assert got.records == () and got.skip_reason == "congress_none_qualified"
    ctx = _v23_ctx("congress_count", CONGRESS_RUN)
    got = A._congress_month_record(ctx, "2026-11", [_v23_count("BRK-A", "Berkshire Hathaway", 3),
                                                     _v23_count("BRK-B", "Berkshire Hathaway B", 2),
                                                     _v23_count("AAPL", "Apple", 2)])
    assert [c.company.symbol for c in got.records[0].counts] == ["BRK-A", "AAPL"]
    assert got.rejections == {"share_class_overlap": 1}


# ══ news-v23 phase 3 (2026-11-20): the WEEKLY theme post — rotation, angles, the list's date, market values ══════

from app.services.marketing import news_templates as _NT  # noqa: E402


def _themes(n=3, **over):
    """`n` active themes t0..t{n-1}, sort orders 0..n-1, each the default eight-ticker member list."""
    return [theme(slug=f"t{i}", title=f"Theme {i}", sort_order=i, **over) for i in range(n)]


def _caps(syms=("NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"), day="2026-11-18", **over):
    base = {"NVDA": 4.4e12, "AMD": 3.1e11, "AVGO": 1.6e12, "QCOM": 1.8e11, "MRVL": 7.7e10, "INTC": 1.2e11}
    base.update(over)
    return [{"symbol": s, "date": day, "marketCap": base[s]} for s in syms]


def test_phase3_the_angle_tables_agree():
    """ONE list of built angles: the record's (`R.THEME_BUILT_ANGLES`), the adapter's collectors and the templates'
    builders — an angle plugs in to all three or none."""
    assert tuple(A.THEME_ANGLE_COLLECTORS) == R.THEME_BUILT_ANGLES == tuple(_NT._THEME_ANGLE_BUILDERS)
    assert set(R.THEME_BUILT_ANGLES) <= set(R.THEME_ANGLES)


@pytest.mark.parametrize("history, order", [
    ((), ["t0", "t1"]),                                                                     # never posted: sort order
    (("news:theme_explainer:t0:make_money:2026-10-01",), ["t1", "t2"]),                     # t0 posted → last
    (("news:theme_explainer:t0:make_money:2026-10-01", "news:theme_explainer:t1:biggest:2026-09-24",
      "news:theme_explainer:t2:make_money:2026-10-08"), ["t1", "t0"]),                       # the OLDEST post first
    # an old key (before the angles) counts as that theme's post on its date
    (("news:theme_explainer:t0:2026-10-01", "news:theme_explainer:t1:2026-09-01"), ["t2", "t1"]),
])
@pytest.mark.asyncio
async def test_phase3_the_rotation_is_never_posted_first_then_the_oldest_post(history, order):
    world = theme_world(_themes())
    world["fmp"].income = {s: annual(s) for s in _SIX}      # 2026-11-20: a theme past "biggest" posts growth + profit
    got = await trun(world, theme_history=history)
    assert [r.slug for r in got.records] == order


@pytest.mark.asyncio
async def test_phase3_ties_break_by_sort_order_then_slug():
    rows = [theme(slug="zeta", title="Zeta", sort_order=1), theme(slug="alpha", title="Alpha", sort_order=1),
            theme(slug="beta", title="Beta", sort_order=None)]
    got = await trun(theme_world(rows))
    assert [r.slug for r in got.records] == ["alpha", "zeta"]          # beta: no sort order, last


@pytest.mark.parametrize("days, posted", [(27, False), (28, True), (60, True)])
@pytest.mark.asyncio
async def test_phase3_never_the_same_theme_within_four_weeks(days, posted):
    """Owner 2026-11-19: "if they are the same, after at least 4 weeks already, so it's fine" — a theme last posted
    fewer than 28 days before the run is `already_posted` before any read, whatever the angle. Mutation-checked by hand
    (2026-11-20): with `<` changed to `<=`, the 28-day case turned red."""
    last = THEME_RUN - timedelta(days=days)
    world = theme_world([theme()])
    got = await trun(world, theme_history=(f"news:theme_explainer:ai-chips:make_money:{last.isoformat()}",))
    assert bool(got.records) is posted
    if not posted:
        assert got.rejections == {"already_posted": 1} and world["fmp"].calls == Counter()


@pytest.mark.parametrize("last, angle", [
    ("news:theme_explainer:ai-chips:make_money:2026-10-01", "biggest"),
    ("news:theme_explainer:ai-chips:2026-10-01", "biggest"),                               # an old key: make_money
    ("news:theme_explainer:ai-chips:biggest:2026-10-01", "growth_profit"),     # every angle is built (2026-11-20)
    ("news:theme_explainer:ai-chips:growth_profit:2026-10-01", "insiders"),
    ("news:theme_explainer:ai-chips:insiders:2026-10-01", "congress"),
    ("news:theme_explainer:ai-chips:congress:2026-10-01", "make_money"),                    # the rotation wraps
])
@pytest.mark.asyncio
async def test_phase3_a_theme_takes_the_angle_after_its_last_posted_one(last, angle):
    world = angles_world(issuer={s: [ins_line(s, "S-Sale")] for s in _SIX},
                         senate=_senate_purchases({s: 2 for s in _SIX}))
    got = await trun(world, exclude=(last,))                                 # the exclude refs count as history too
    (rec,) = got.records
    assert rec.angle == angle and R.ledger_key(rec) == f"news:theme_explainer:ai-chips:{angle}:2026-11-19"
    assert "theme_angle_not_built" not in got.rejections
    # only the angle's own sources are read (phase 4: "how they make money" also reads the ONE market-value batch
    # "biggest" reads — its tile order)
    sources = {"revenue": world["revenue"].calls != [], "market_caps": world["fmp"].calls["market_caps"] > 0,
               "income": world["fmp"].calls["income"] > 0, "insider_issuer": world["fmp"].calls["insider_issuer"] > 0,
               "congress": world["fmp"].calls["congress"] > 0}
    own = {"make_money": {"revenue", "market_caps"}, "biggest": {"market_caps"}, "growth_profit": {"income"},
           "insiders": {"insider_issuer"}, "congress": {"congress"}}
    assert {src for src, read in sources.items() if read} == own[angle]


@pytest.mark.asyncio
async def test_phase3_an_angle_with_too_few_companies_falls_through_to_the_next_angle():
    """Owner 2026-11-19: "≥ 4 companies with data, else the next angle". Three facts (QCOM degraded) → make_money is
    `theme_facts_thin`, the theme posts "biggest"; no market values either → `theme_values_thin`, the unbuilt angles
    skipped without a read, and the theme gives nothing (the series skips)."""
    bds = {**theme_breakdowns(), "QCOM": breakdown(degraded=["segmentation_unavailable"])}
    world = theme_world(breakdowns=bds)
    world["fmp"].market_caps = _caps()
    (rec,) = (await trun(world)).records
    assert rec.angle == "biggest"
    assert [m.company.symbol for m in rec.members] == ["NVDA", "AVGO", "AMD", "QCOM", "INTC", "MRVL"]
    # 2026-11-20: every angle thin — three facts, no market values, three companies' statements, three with trades,
    # three Congress companies — the theme gives nothing after ONE whole rotation (THEME_MAX_ANGLE_ATTEMPTS = 5) and the
    # series skips (never an outage)
    A.clear_memo()
    world = angles_world(breakdowns=bds, caps=False, income={s: annual(s) for s in ("NVDA", "AMD", "AVGO")},
                         issuer={s: [ins_line(s, "S-Sale")] for s in ("NVDA", "AMD", "AVGO")},
                         senate=_senate_purchases({s: 2 for s in ("NVDA", "AMD", "AVGO")}))
    got = await trun(world)
    assert got.records == () and got.skip_reason == "theme_none_qualified"
    assert {k: got.rejections.get(k) for k in ("theme_facts_thin", "theme_values_thin", "theme_growth_thin",
                                               "theme_insiders_thin", "theme_congress_thin")} == {
        "theme_facts_thin": 1, "theme_values_thin": 1, "theme_growth_thin": 1, "theme_insiders_thin": 1,
        "theme_congress_thin": 1}


@pytest.mark.asyncio
async def test_phase3_a_worker_without_the_capability_is_offered_make_money_only():
    """`theme_angles` (the run's holder: "how they make money" only without WORKER_CAPABILITY_THEME_ANGLES): the
    "biggest" angle is `theme_angle_unsupported` with NO read, and a theme whose make_money is thin gives nothing."""
    world = theme_world([theme()])
    world["fmp"].market_caps = _caps()
    got = await trun(world, theme_history=("news:theme_explainer:ai-chips:make_money:2026-10-01",),
                     theme_angles=("make_money",))
    (rec,) = got.records
    # every angle after "make_money" — biggest, growth_profit, insiders, congress — skipped without a read; the ONE
    # market-value read is make_money's own (phase 4: its tile order), never a "biggest" post
    assert rec.angle == "make_money" and got.rejections.get("theme_angle_unsupported") == 4
    assert world["fmp"].calls["market_caps"] == 1 and world["fmp"].calls["income"] == 0
    assert [m.company.symbol for m in rec.members][:4] == ["NVDA", "AVGO", "AMD", "QCOM"]     # by market value


@pytest.mark.asyncio
async def test_phase3_the_angle_attempts_are_bounded_per_call():
    """At most THEME_MAX_ANGLE_ATTEMPTS angle evaluations that READ per call (THEME_MAX_EVALUATED themes): two themes
    each thin at every angle → 5 evaluations (t0's whole rotation), t1's never made. 2026-11-20: five (was three), so
    one theme's whole rotation fits a call."""
    bds = {s: breakdown(degraded=["x"]) for s in ("NVDA", "AMD", "AVGO", "QCOM", "MRVL")}
    world = angles_world(rows=_themes(2), breakdowns=bds, caps=False, income={},
                         issuer={s: [ins_line(s, "S-Sale")] for s in ("NVDA",)},
                         senate=_senate_purchases({"NVDA": 2}))
    world["fmp"].income = {s: annual(s) for s in ("NVDA",)}
    got = await trun(world)
    assert got.records == () and A.THEME_MAX_ANGLE_ATTEMPTS == 5
    thin = ("theme_facts_thin", "theme_values_thin", "theme_growth_thin", "theme_insiders_thin", "theme_congress_thin")
    assert sum(got.rejections.get(k, 0) for k in thin) == 5 and all(got.rejections.get(k) == 1 for k in thin)
    assert world["fmp"].calls["market_caps"] == 1                       # t0's biggest; t1's never reached
    assert world["revenue"].calls.count("NVDA") == 1


@pytest.mark.parametrize("row", [
    {"tickers_as_of": None, "updated_at": "2026-07-04T12:00:00+00:00"},          # production: last written 139 days ago
    {"tickers_as_of": None, "updated_at": "2026-11-20T12:00:00+00:00"},          # a write after the run
    {"tickers_as_of": None, "updated_at": "yesterday"},
    {"tickers_as_of": None, "updated_at": None},
    {"tickers_as_of": None},                                                      # no `updated_at` at all
])
@pytest.mark.asyncio
async def test_owner_a_theme_with_no_review_date_is_the_apps_live_list_and_counts_as_current(row):
    """OWNER DECISION 2026-11-20 ("Trust the app's lists (Recommended)": "Your app already shows these exact lists to
    users, so when a theme has no review date, posts treat its list as current … Each company still passes its own
    checks (still trading, has data)."). PRODUCTION FACT: `tickers_as_of` is NULL on all 8 active themes and 7 rows were
    last written 2026-07-04. Such a list is NEVER refused `theme_stale`, whatever its `updated_at` says (the column is
    not even read): the record carries the RUN day, flagged `tickers_as_of_from_row` so nothing prints it.
    Mutation-checked by hand (2026-11-20): with the NULL branch aging the list from `updated_at` again, the first case
    turned red (`theme_stale`)."""
    world = theme_world([theme(**row)])
    got = await trun(world)
    (rec,) = got.records
    assert "theme_stale" not in got.rejections
    assert rec.tickers_as_of == THEME_RUN and rec.tickers_as_of_from_row is True
    (_table, cols), = [x for x in world["sb"].selects if x[0] == "trending_themes"]
    assert "updated_at" not in cols.split(",") and "tickers_as_of" in cols.split(",")
    # each company still passes its own checks: the ETF in the list is still refused, the blocked ARM never profiled
    assert got.rejections == {"etf_or_fund": 1} and "ARM" not in world["fmp"].profile_requests[0]


@pytest.mark.parametrize("row", [
    {"tickers_as_of": "2026-09-09", "updated_at": "2026-11-18T12:00:00+00:00"},  # reviewed 71 days ago: stale
    {"tickers_as_of": "2026-11-20", "updated_at": "2026-11-18T12:00:00+00:00"},  # a review dated after the run
    {"tickers_as_of": "2027-01-05"},                                              # far in the future
    {"tickers_as_of": "soon", "updated_at": "2026-11-18T12:00:00+00:00"},        # present but unreadable
    {"tickers_as_of": ""},                                                        # present but empty
    {"tickers_as_of": 20261102},                                                  # present, wrong type
])
@pytest.mark.asyncio
async def test_owner_a_reviewed_list_keeps_the_70_day_rule_and_fails_closed(row):
    """A PRESENT `tickers_as_of` (a list someone reviewed) keeps THEME_STALE_DAYS (70) exactly as before; a present
    value that does not read as a day is a broken row (`theme_stale`), never treated as missing — and `updated_at` never
    rescues it. Refused before any profile call. Mutation-checked by hand (2026-11-20): with a present unreadable
    value treated like NULL, the "soon" / "" / int cases turned red."""
    world = theme_world([theme(**row)])
    got = await trun(world)
    assert got.records == () and got.rejections == {"theme_stale": 1} and world["fmp"].calls == Counter()


@pytest.mark.parametrize("as_of, ok", [("2026-09-10", True), ("2026-09-09", False), ("2026-11-19", True)])
@pytest.mark.asyncio
async def test_owner_the_reviewed_list_boundary_is_70_days_inclusive(as_of, ok):
    got = await trun(theme_world([theme(tickers_as_of=as_of)]))
    assert R.THEME_STALE_DAYS == 70 and (THEME_RUN - date.fromisoformat(as_of)).days in (70, 71, 0)
    if ok:
        (rec,) = got.records
        assert rec.tickers_as_of == date.fromisoformat(as_of) and rec.tickers_as_of_from_row is False
    else:
        assert got.records == () and got.rejections == {"theme_stale": 1}


def test_phase3_a_theme_fact_may_be_four_fiscal_years_old_the_spotlights_stays_two():
    """Owner 2026-11-19 ("for AI CHIPS, i don't see ARM, Intel and SNPS? add for me"): Intel's newest breakdown is fiscal
    2022 — a theme member's fact may be up to R.THEME_TAGGED_FACT_MAX_AGE_YEARS old (the template tags it "FY2022"); the
    insider spotlight's own segment fact keeps its two years."""
    run = date(2026, 11, 19)
    old = breakdown(fiscal_year="2022")
    assert A._segment_fact(old, run) is None
    assert A._segment_fact(old, run, max_age_years=R.THEME_TAGGED_FACT_MAX_AGE_YEARS) == ("iPhone", 200e9 / 350e9, "2022")
    assert A._segment_fact(breakdown(fiscal_year="2021"), run, max_age_years=R.THEME_TAGGED_FACT_MAX_AGE_YEARS) is None


@pytest.mark.asyncio
async def test_phase3_a_theme_members_old_fact_reaches_the_record():
    bds = {**theme_breakdowns(), "AMD": breakdown(symbol="AMD", fiscal_year="2022", reported_revenue=23.6e9,
                                                  revenue_sources=[src("Data Center", 6e9), src("Client", 6.2e9),
                                                                   src("Gaming", 6.8e9), src("Embedded", 4.6e9)])}
    (rec,) = (await trun(theme_world(breakdowns=bds))).records
    facts = {m.company.symbol: m.fiscal_year for m in rec.members}
    assert facts["AMD"] == "2022"


@pytest.mark.asyncio
async def test_phase3_the_biggest_angle_ranks_by_one_batch_of_dated_market_values():
    """Angle 2: ONE market-capitalization batch for the members (memoised), only `symbol` / `date` / `marketCap` read,
    the members ranked largest first (ties: the symbol), every value of ONE day within the week before the run."""
    world = theme_world()
    world["fmp"].market_caps = _caps(QCOM=1.6e12)                    # a tie with AVGO: the symbol breaks it
    hist = ("news:theme_explainer:ai-chips:make_money:2026-10-01",)
    (rec,) = (await trun(world, theme_history=hist)).records
    assert rec.angle == "biggest" and rec.values_as_of == date(2026, 11, 18)
    assert [m.company.symbol for m in rec.members] == ["NVDA", "AVGO", "QCOM", "AMD", "INTC", "MRVL"]
    assert world["fmp"].market_cap_requests == [sorted(["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"])]
    await trun(world, theme_history=hist)                             # the memo answers the second build
    assert world["fmp"].calls["market_caps"] == 1


@pytest.mark.parametrize("rows, left_out", [
    # a missing value, zero, negative, inf, a string, a bool, over the cap: each member left out, never ranked
    (_caps()[:5] + [{"symbol": "INTC", "date": "2026-11-18", "marketCap": None}], {"INTC"}),
    (_caps(INTC=0.0), {"INTC"}), (_caps(INTC=-5e9), {"INTC"}), (_caps(INTC=float("inf")), {"INTC"}),
    (_caps(INTC="1.2e11"), {"INTC"}), (_caps(INTC=True), {"INTC"}), (_caps(INTC=60e12), {"INTC"}),
    # two rows of one member that disagree; a row of another day than the post's (most rows' day)
    (_caps() + [{"symbol": "INTC", "date": "2026-11-18", "marketCap": 9e10}], {"INTC"}),
    (_caps()[:5] + [{"symbol": "INTC", "date": "2026-11-17", "marketCap": 1.2e11}], {"INTC"}),
    # a member with no row; a row for a symbol not asked (ignored); junk rows
    (_caps()[:5], {"INTC"}), (_caps() + [{"symbol": "ZZZZ", "date": "2026-11-18", "marketCap": 9e12}], set()),
    (_caps() + ["junk", {"date": "2026-11-18", "marketCap": 1e9}, {"symbol": 7}], set()),
])
@pytest.mark.asyncio
async def test_phase3_a_bad_market_value_row_leaves_its_member_out(rows, left_out):
    world = theme_world()
    world["fmp"].market_caps = rows
    got = await trun(world, theme_history=("news:theme_explainer:ai-chips:make_money:2026-10-01",))
    (rec,) = got.records
    assert {m.company.symbol for m in rec.members} == {"NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC"} - left_out
    assert got.rejections.get("market_value_unusable", 0) == len(left_out)
    assert "ZZZZ" not in {m.company.symbol for m in rec.members} and rec.theme_size == 8


@pytest.mark.asyncio
async def test_phase3_a_non_usd_listing_or_a_stale_day_gives_no_market_value():
    profiles = theme_profiles()
    profiles["INTC"] = prof("INTC", "Intel Corporation", exchange="NASDAQ", currency="EUR")
    world = theme_world(profiles=profiles)
    world["fmp"].market_caps = _caps()
    hist = ("news:theme_explainer:ai-chips:make_money:2026-10-01",)
    (rec,) = (await trun(world, theme_history=hist)).records
    assert "INTC" not in {m.company.symbol for m in rec.members}
    A.clear_memo()
    world = theme_world()
    world["fmp"].market_caps = _caps(day="2026-11-11")               # 8 days before the run: a stale feed
    got = await trun(world, theme_history=hist)
    # no value is used: "biggest" is thin, and the rotation wraps to "how they make money"
    assert got.rejections.get("theme_values_thin") == 1 and [r.angle for r in got.records] == ["make_money"]


@pytest.mark.asyncio
async def test_phase3_a_failed_market_value_read_is_that_angles_turn_lost():
    world = theme_world()
    world["fmp"].market_caps = FMPUnavailableException("batch down")
    got = await trun(world, theme_history=("news:theme_explainer:ai-chips:make_money:2026-10-01",))
    assert [r.angle for r in got.records] == ["make_money"]          # the next angle (rotation wraps) posted


@pytest.mark.parametrize("kw", [dict(theme_history="news:x"), dict(theme_history=[1]),
                                dict(theme_history=["x"] * (A.THEME_HISTORY_MAX + 1)),
                                dict(theme_angles=("charts",)), dict(theme_angles="make_money")])
@pytest.mark.asyncio
async def test_phase3_bad_theme_inputs_are_a_callers_bug(kw):
    with pytest.raises(ValueError):
        await trun(theme_world(), **kw)


# ══ news-v23 phase 3, the theme's last three angles (2026-11-20): growth + profit, the insider scorecard, Congress ════

#: The default theme's six listed members (ARM blocked, SMH an ETF): NVDA, AMD, AVGO, QCOM, MRVL, INTC.
_SIX = ("NVDA", "AMD", "AVGO", "QCOM", "MRVL", "INTC")
_IN_WINDOW = "2026-11-02"                     # a filing day inside THEME_RUN's 90-day window (Aug 21 .. Nov 18)


def annual(sym, rev=120e9, prior=100e9, net=12e9, *, fy="2025", end="2025-12-31", prior_end="2024-12-31",
           currency="USD", **over):
    """Two ANNUAL income statement rows (newest first), as FMP answers them; `over` edits the newest."""
    newest = {"symbol": sym, "date": end, "fiscalYear": fy, "period": "FY", "reportedCurrency": currency,
              "revenue": rev, "netIncome": net, "eps": 1.0}
    newest.update(over)
    older = {"symbol": sym, "date": prior_end, "fiscalYear": str(int(fy) - 1), "period": "FY",
             "reportedCurrency": currency, "revenue": prior, "netIncome": 1e9, "eps": 0.5}
    return [newest, older]


def ins_line(sym, tx="P-Purchase", shares=1_000, price=100.0, filed=_IN_WINDOW, traded=None, **kw):
    """One Form 4 line of `sym`'s issuer (irow's shape), P or S, common stock."""
    kw.setdefault("acquisitionOrDisposition", "A" if tx.startswith("P") else "D")
    return irow(sym=sym, tx=tx, shares=shares, price=price, filed=filed, traded=traded or filed, **kw)


def angles_world(*, income=None, issuer=None, senate=None, house=None, rows=None, profiles=None, caps=True,
                 breakdowns=None, oldest="2026-09-01"):
    """The default theme with every angle's data: breakdowns (make_money), market values (biggest), annual statements
    (growth + profit), each issuer's Form 4 read (the insider scorecard) and both chambers' feeds (Congress; `oldest`:
    the feeds' last row, before the disclosure month — a covered walk)."""
    fmp = FakeFMP2b(profiles=theme_profiles() if profiles is None else profiles,
                    income={s: annual(s) for s in _SIX} if income is None else income,
                    issuer_feed={A.normalize_cik(issuer_cik(s)): rows_ for s, rows_ in (issuer or {}).items()},
                    senate=cfeed("senate", senate or [], oldest=oldest), house=cfeed("house", house or [], oldest=oldest),
                    market_caps=_caps() if caps else None)
    revenue = FakeService(theme_breakdowns() if breakdowns is None else breakdowns,
                          default=breakdown(degraded=["segmentation_unavailable"]))
    sb = FakeSB({"trending_themes": [theme()] if rows is None else rows})
    return dict(fmp=fmp, revenue=revenue, sb=sb)


async def arun(world, angle_before=None, **kw):
    """One theme build whose last posted angle is `angle_before` (so the NEXT angle is tried first)."""
    hist = (f"news:theme_explainer:ai-chips:{angle_before}:2026-10-01",) if angle_before else ()
    return await trun(world, theme_history=hist, **kw)


def _senate_purchases(counts, day="2026-10-20"):
    """`counts` {symbol: distinct members} as senate purchase rows of October 2026 (THEME_RUN's disclosure month)."""
    return [crow("senate", sym, day, f"m{sym}{i}") for sym, n in counts.items() for i in range(n)]


# ── angle 3: growth + profit ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase3_growth_profit_reads_two_annual_years_and_keeps_the_themes_order():
    """Angle 3: ONE licensed income-statement call per member (memoised), the latest fiscal year's revenue against the
    year before's and its net income — the members in the THEME's order (no ranking), every figure the statement's."""
    world = angles_world(income={**{s: annual(s) for s in _SIX},
                                 "NVDA": annual("NVDA", 130e9, 61e9, 73e9, fy="2026", end="2026-01-25",
                                                prior_end="2025-01-26"),
                                 "MRVL": annual("MRVL", 5.8e9, 5.5e9, -0.9e9, fy="2026", end="2026-02-01",
                                                prior_end="2025-02-01")})
    got = await arun(world, "biggest")
    (rec,) = got.records
    assert rec.angle == "growth_profit" and R.ledger_key(rec) == "news:theme_explainer:ai-chips:growth_profit:2026-11-19"
    assert [m.company.symbol for m in rec.members] == list(_SIX)
    nvda = rec.members[0]
    assert (nvda.fiscal_year, nvda.revenue_usd, nvda.prior_revenue_usd, nvda.net_income_usd) == ("2026", 130e9, 61e9,
                                                                                                   73e9)
    assert R.theme_profit_per_100(rec.members[4]) < 0                    # a loss is kept, as filed
    assert world["fmp"].calls["income"] == 6 and rec.theme_size == 8
    assert world["fmp"].calls["market_caps"] == 0 and world["revenue"].calls == []     # no other angle's read
    await arun(world, "biggest")                                         # the memo answers a second build
    assert world["fmp"].calls["income"] == 6


_RUN = THEME_RUN


@pytest.mark.parametrize("rows, why", [
    ([], "fewer than two"),
    (annual("X")[:1], "fewer than two"),
    ([{"date": "soon", "revenue": 1}, {"date": None}], "fewer than two"),
    (annual("X") + [dict(annual("X")[0], revenue=1.0)], "disagree"),                    # two rows of one year
    (annual("X", end="2025-07-15", prior_end="2024-07-15", fy="2025"), "days before the run"),   # 492 days: stale
    (annual("X", end="2026-11-20", prior_end="2025-11-20", fy="2027"), "days before the run"),   # after the run
    (annual("X", prior_end="2025-06-30"), "apart"),                       # a half year, not a year
    (annual("X", prior_end="2023-12-31"), "apart"),                       # two years apart (a missing year)
    (annual("X", prior_end="2024-12-01"), "apart"),                       # 395 days
    ([dict(annual("X")[0], fiscalYear="20X5"), annual("X")[1]], "consecutive"),
    ([annual("X")[0], dict(annual("X")[1], fiscalYear="2023")], "consecutive"),
    (annual("X", fy="2019", end="2025-12-31", prior_end="2024-12-31"), "near the run"),     # a mislabelled year
    ([annual("X")[0], dict(annual("X")[1], reportedCurrency="EUR")], "currencies"),
    (annual("X", currency=""), "currencies"),
    (annual("X", rev=0.0), "revenue"), (annual("X", rev=-5.0), "revenue"), (annual("X", rev="120e9"), "revenue"),
    (annual("X", rev=True), "revenue"), (annual("X", rev=float("nan")), "revenue"),
    (annual("X", prior=0.0), "revenue"), (annual("X", prior=None), "revenue"),
    (annual("X", net=None), "net income"), (annual("X", net=float("inf")), "net income"),
    (annual("X", rev=601e9, prior=100e9), "plausibility"),               # +501%
    (annual("X", rev=120e9, net=121e9), "above revenue"),
    (annual("X", rev=1e9, prior=1e9, net=-10.5e9), "implausible loss"),
    (annual("X", rev=1e12, prior=1e12, net=4e7), "under a cent"),         # $0.004 per $100: never a "$0.00"
])
def test_phase3_a_growth_fact_is_refused_whenever_the_claim_could_be_wrong(rows, why):
    got = A._theme_growth_fact(rows, _RUN)
    assert isinstance(got, str) and why in got, got


def test_phase3_a_growth_fact_reads_unsorted_rows_and_the_bounds_exactly():
    rows = list(reversed(annual("X", 120e9, 100e9, 12e9) + [dict(annual("X")[1], date="2023-12-31", fiscalYear="2023",
                                                                    revenue=1.0)]))
    assert A._theme_growth_fact(rows, _RUN) == ("2025", 120e9, 100e9, 12e9)          # the two NEWEST years
    assert A._theme_growth_fact(annual("X", rev=600e9, prior=100e9), _RUN)[0] == "2025"     # +500% exactly: kept
    assert A._theme_growth_fact(annual("X", rev=1e9, net=-10e9), _RUN)[3] == -10e9           # a loss of 10x: kept
    assert A._theme_growth_fact(annual("X", rev=1e9, net=0.0), _RUN)[3] == 0.0               # exactly zero: kept
    # a 53-week year (371 days) and a 52-week one (364) are a year; the latest year may end 485 days before the run
    assert not isinstance(A._theme_growth_fact(annual("X", end="2026-01-31", prior_end="2025-01-25", fy="2026"), _RUN),
                          str)
    assert not isinstance(A._theme_growth_fact(annual("X", end="2025-07-22", prior_end="2024-07-22"), _RUN), str)


@pytest.mark.asyncio
async def test_phase3_growth_profit_leaves_out_a_member_it_cannot_be_sure_of_and_reads_no_one_twice(caplog):
    income = {s: annual(s) for s in _SIX}
    income["AMD"] = FMPUnavailableException("statements down")              # a failed read: left out, never zero
    income["QCOM"] = annual("QCOM", rev=900e9, prior=100e9)                  # +800%: implausible, left out
    world = angles_world(income=income)
    got = await arun(world, "biggest")
    (rec,) = got.records
    assert [m.company.symbol for m in rec.members] == ["NVDA", "AVGO", "MRVL", "INTC"]
    assert got.rejections.get("statements_unreadable") == 1 and got.rejections.get("growth_unusable") == 1
    assert "AMD annual statements unreadable" in caplog.text


@pytest.mark.asyncio
async def test_phase3_growth_profit_thin_or_down_is_that_angles_turn_lost():
    # three members with a certain fact → `theme_growth_thin`, the next angle (the insider scorecard) is tried
    income = {s: annual(s) for s in ("NVDA", "AMD", "AVGO")}
    world = angles_world(income=income, issuer={s: [ins_line(s, "S-Sale")] for s in _SIX})
    got = await arun(world, "biggest")
    assert got.rejections.get("theme_growth_thin") == 1 and [r.angle for r in got.records] == ["insiders"]
    # every read failing is the source being down: the angle is lost (the next one tried), never "thin"
    A.clear_memo()
    world = angles_world(income={s: FMPUnavailableException("down") for s in _SIX},
                         issuer={s: [ins_line(s, "S-Sale")] for s in _SIX})
    got = await arun(world, "biggest")
    assert "theme_growth_thin" not in got.rejections and [r.angle for r in got.records] == ["insiders"]


@pytest.mark.asyncio
async def test_phase3_growth_profit_holds_at_most_fourteen_companies_and_reads_at_most_sixteen():
    syms = [f"T{c}" for c in "ABCDEFGHIJKLMNOPQRS"]                          # 19 members
    profiles = {s: prof(s, f"Company {s}", exchange="NASDAQ") for s in syms}
    world = angles_world(rows=[theme(tickers=syms, blocked_tickers=[])], profiles=profiles,
                         income={s: annual(s) for s in syms}, caps=False)
    (rec,) = (await arun(world, "biggest")).records
    assert len(rec.members) == R.THEME_GROWTH_MAX_MEMBERS == 14
    assert [m.company.symbol for m in rec.members] == syms[:14] and rec.theme_size == 19
    assert world["fmp"].calls["income"] == A.THEME_STATEMENT_LOOKUPS == 16


@pytest.mark.asyncio
async def test_phase3_a_name_the_table_cannot_draw_is_left_out_before_any_read():
    """A member whose display name needs more than the table's lines at the floor (`R.theme_table_name_fits`) is left
    out (``company_name_unusable``) BEFORE its statements are read — the template never refuses the record, so a theme
    is never stuck on an angle it cannot draw. Mutation-checked by hand (2026-11-20): with the filter removed the record
    lists the member and the template refuses it (`slot_rejected`)."""
    profiles = theme_profiles()
    long_name = "Wmwmwmwm Wmwmwmwm Wmwmwmwm"                     # three lines of the growth table, one of Congress
    assert not R.theme_table_name_fits("growth_profit", long_name) and R.theme_table_name_fits("congress", long_name)
    profiles["INTC"] = prof("INTC", long_name, exchange="NASDAQ")
    world = angles_world(profiles=profiles)
    got = await arun(world, "biggest")
    (rec,) = got.records
    assert "INTC" not in [m.company.symbol for m in rec.members] and got.rejections.get("company_name_unusable") == 1
    assert world["fmp"].calls["income"] == 5


# ── angle 4: the insider scorecard ─────────────────────────────────────────────────────────────────────────────

_WS, _WE = THEME_RUN - timedelta(days=90), THEME_RUN - timedelta(days=1)
_CIK = issuer_cik("NVDA")


@pytest.mark.parametrize("rows, want", [
    ([], (0.0, 0.0)),
    ([ins_line("NVDA", shares=1000, price=100.0), ins_line("NVDA", "S-Sale", shares=500, price=110.0)],
     (100_000.0, 55_000.0)),
    # an S-Sale+OE line is still a code-S sale of the stock
    ([ins_line("NVDA", "S-Sale+OE", shares=10, price=10.0)], (0.0, 100.0)),
    # not counted: an award, an exercise, a gift, tax withholding; an option / warrant / preferred line; a Form 5
    ([ins_line("NVDA", "A-Award", shares=9e6), ins_line("NVDA", "M-Exempt", shares=9e6),
      ins_line("NVDA", "G-Gift", shares=9e6), ins_line("NVDA", "F-InKind", shares=9e6)], (0.0, 0.0)),
    ([ins_line("NVDA", sec="Stock Option (Right to Buy)"), ins_line("NVDA", "S-Sale", sec="Warrants"),
      ins_line("NVDA", sec="Series A Preferred Stock")], (0.0, 0.0)),
    ([ins_line("NVDA", form="5")], (0.0, 0.0)),
    # filed before the window opened, or on the run day: another window's
    ([ins_line("NVDA", filed="2026-08-20"), ins_line("NVDA", filed="2026-11-19")], (0.0, 0.0)),
    ([ins_line("NVDA", filed="2026-08-21"), ins_line("NVDA", filed="2026-11-18")], (200_000.0, 0.0)),
    # the same line on two filings counts once (the earliest); identical lines on ONE filing are separate fills
    ([ins_line("NVDA", filed="2026-11-02"), ins_line("NVDA", filed="2026-11-05", traded="2026-11-02")],
     (100_000.0, 0.0)),
    ([ins_line("NVDA"), ins_line("NVDA")], (200_000.0, 0.0)),
    # the EARLIEST filing's fills are the ones kept: two fills there, the line repeated once on a later filing → two
    ([ins_line("NVDA"), ins_line("NVDA"), ins_line("NVDA", filed="2026-11-09", traded="2026-11-02")],
     (200_000.0, 0.0)),
    # unsorted rows read the same
    ([ins_line("NVDA", "S-Sale", filed="2026-09-01"), ins_line("NVDA", filed="2026-11-10"),
      ins_line("NVDA", filed="2026-10-01")], (200_000.0, 100_000.0)),
])
def test_phase3_insider_totals_are_open_market_common_stock_dollars_as_filed(rows, want):
    assert A._theme_insider_totals(rows, cik=_CIK, window_start=_WS, window_end=_WE) == pytest.approx(want)


@pytest.mark.parametrize("rows, why", [
    # ANY amendment of the issuer filed since the window opened — any code, any security (an option 4/A too)
    ([ins_line("NVDA"), ins_line("NVDA", "M-Exempt", form="4/A", sec="Stock Option")], "amended_filing"),
    ([ins_line("NVDA", form="4/A")], "amended_filing"),
    ([ins_line("NVDA", form=" 4/a ", filed="2026-11-19")], "amended_filing"),          # filed on the run day too
    # a row of another issuer: the read was not filtered
    ([ins_line("NVDA"), ins_line("AMD")], "insider_feed_unusable"),
    # a P / S common line whose dollars cannot be computed: never a guessed total
    ([ins_line("NVDA", price=0.0)], "insider_feed_unusable"),
    ([ins_line("NVDA", price=None)], "insider_feed_unusable"),
    ([ins_line("NVDA", shares=-5)], "insider_feed_unusable"),
    ([ins_line("NVDA", shares="1000")], "insider_feed_unusable"),
    ([ins_line("NVDA", shares=1e9, price=1e4)], "insider_feed_unusable"),               # past the garbage bound
    ([ins_line("NVDA", sec="")], "insider_feed_unusable"),                               # not known to be stock
    ([ins_line("NVDA", filingDate=None)], "insider_feed_unusable"),
    ([ins_line("NVDA", acquisitionOrDisposition="D")], "insider_feed_unusable"),         # a purchase disposed of
    ([ins_line("NVDA", shares=0.004, price=100.0)], "insider_feed_unusable"),           # $0.40: never a "$0"
])
def test_phase3_insider_totals_fail_closed_for_the_whole_company(rows, why):
    assert A._theme_insider_totals(rows, cik=_CIK, window_start=_WS, window_end=_WE) == why


def test_phase3_an_amendment_before_the_window_is_another_windows():
    rows = [ins_line("NVDA"), ins_line("NVDA", form="4/A", filed="2026-08-20")]
    assert A._theme_insider_totals(rows, cik=_CIK, window_start=_WS, window_end=_WE) == pytest.approx((100_000.0, 0.0))


@pytest.mark.asyncio
async def test_phase3_the_insider_scorecard_lists_the_companies_with_trades_purchases_first(caplog):
    issuer = {"NVDA": [ins_line("NVDA", "S-Sale", shares=100_000)],                   # $10M of sales
              "AMD": [ins_line("AMD", shares=2_000), ins_line("AMD", "S-Sale", shares=500)],
              "AVGO": [ins_line("AVGO", shares=5_000)],                                  # $500K of purchases
              "QCOM": [ins_line("QCOM", "S-Sale", shares=30_000)],
              "MRVL": [ins_line("MRVL", "A-Award", shares=9e6)],                         # checked, nothing listed
              "INTC": [ins_line("INTC", form="4/A")]}                                     # amended: not checked
    world = angles_world(issuer=issuer)
    got = await arun(world, "growth_profit")
    (rec,) = got.records
    assert rec.angle == "insiders" and rec.window_start == date(2026, 8, 21) and rec.window_end == date(2026, 11, 18)
    assert [m.company.symbol for m in rec.members] == ["AVGO", "AMD", "NVDA", "QCOM"]
    assert rec.companies_checked == 5 and got.rejections.get("amended_filing") == 1
    assert world["fmp"].issuer_calls[0] | {} == {"since": "2026-08-21", "transaction_type": None,
                                                  "page_size": A.THEME_INSIDER_PAGE_SIZE,
                                                  "max_pages": A.THEME_INSIDER_MAX_PAGES,
                                                  "company_cik": world["fmp"].issuer_calls[0]["company_cik"]}
    assert (A.THEME_INSIDER_PAGE_SIZE, A.THEME_INSIDER_MAX_PAGES, A.THEME_INSIDER_LOOKUPS) == (1000, 3, 16)
    assert world["fmp"].calls["insider_issuer"] == 6
    # company totals only: no person, role or name of anyone reaches the record or its fact sheet
    sheet = json.dumps(R.fact_sheet(rec, rejections=got.rejections, selection={}))
    assert "COHEN" not in sheet.upper() and "Chief Executive" not in sheet
    await arun(world, "growth_profit")                                    # the memo answers the second build
    assert world["fmp"].calls["insider_issuer"] == 6


@pytest.mark.asyncio
async def test_phase3_an_unreadable_issuer_is_left_out_never_a_zero():
    from app.integrations.fmp import FMPPartialPageException

    issuer = {s: [ins_line(s, "S-Sale")] for s in _SIX}
    issuer["AMD"] = FMPPartialPageException("past 3 pages", endpoint="insider-trading/search", pages_total=3, pages_failed=0)    # the window ran past the page bound
    issuer["AVGO"] = FMPUnavailableException("down")
    profiles = theme_profiles()
    profiles["QCOM"] = {**profiles["QCOM"], "cik": None}                  # no issuer CIK: cannot be read by issuer
    world = angles_world(issuer=issuer, profiles=profiles)
    got = await arun(world, "growth_profit", theme_angles=("insiders",))
    # three left out — never counted as "no trades" — leave three listed: the scorecard is thin, nothing posted
    assert got.records == () and got.rejections.get("insider_feed_unusable") == 3
    assert got.rejections.get("theme_insiders_thin") == 1
    assert world["fmp"].calls["insider_issuer"] == 5                      # QCOM never read (no CIK)


@pytest.mark.asyncio
async def test_phase3_the_insider_scorecard_is_thin_under_four_companies_with_trades():
    issuer = {s: [ins_line(s, "S-Sale")] for s in ("NVDA", "AMD", "AVGO")}
    world = angles_world(issuer=issuer, senate=_senate_purchases({s: 2 for s in _SIX}))
    got = await arun(world, "growth_profit")
    assert got.rejections.get("theme_insiders_thin") == 1 and [r.angle for r in got.records] == ["congress"]


@pytest.mark.asyncio
async def test_phase3_the_filing_angles_need_class_c_on_and_read_nothing_without_it():
    """The insider scorecard and Congress state FILINGS (class C): with MARKETING_CONTENT_CLASSES lacking "C" they are
    skipped (``theme_angle_class_off``) with NO read and the rotation goes on to "how they make money". Mutation-checked by
    hand (2026-11-20): with the class check removed, the insider read was made and the angle posted."""
    world = angles_world(issuer={s: [ins_line(s, "S-Sale")] for s in _SIX},
                         senate=_senate_purchases({s: 2 for s in _SIX}))
    got = await arun(world, "growth_profit", theme_classes=("A", "F"))
    (rec,) = got.records
    assert rec.angle == "make_money" and got.rejections.get("theme_angle_class_off") == 2
    assert world["fmp"].calls["insider_issuer"] == 0 and world["fmp"].calls["congress"] == 0
    A.clear_memo()
    got = await arun(angles_world(issuer={s: [ins_line(s, "S-Sale")] for s in _SIX}), "growth_profit",
                     theme_classes=("A", "C", "F"))
    assert [r.angle for r in got.records] == ["insiders"]


@pytest.mark.parametrize("bad", ["C", b"C", {"C": 1}, [1]])
@pytest.mark.asyncio
async def test_phase3_bad_theme_classes_are_a_callers_bug(bad):
    with pytest.raises(ValueError):
        await trun(theme_world(), theme_classes=bad)


# ── angle 5: Congress ──────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase3_the_congress_angle_counts_two_or_more_distinct_members_the_most_first():
    senate = _senate_purchases({"NVDA": 3, "AMD": 2, "AVGO": 4, "QCOM": 2, "MRVL": 1, "TSLA": 5})
    world = angles_world(senate=senate)
    got = await arun(world, "insiders")
    (rec,) = got.records
    assert rec.angle == "congress" and rec.month == "2026-10"
    # the most members first, then the NAME (AMD "Advanced Micro Devices" before QCOM "QUALCOMM"); MRVL (one member)
    # never; TSLA is not in the theme
    assert [(m.company.symbol, m.congress_members) for m in rec.members] == [
        ("AVGO", 4), ("NVDA", 3), ("AMD", 2), ("QCOM", 2)]
    # never a member's name, party, state or chamber anywhere in the record
    sheet = json.dumps(R.fact_sheet(rec, rejections=got.rejections, selection={}))
    assert "ZQX" not in sheet and "senate" not in sheet.lower()


@pytest.mark.asyncio
async def test_phase3_the_congress_angle_shares_the_congress_counts_one_walk_of_the_feeds():
    """The theme's Congress angle and the congress_count series read the SAME memoised month walk (``("congress",
    month)``): whichever runs first walks both chambers, the other reads none — and the two can never disagree."""
    senate = _senate_purchases({"NVDA": 3, "AMD": 2, "AVGO": 4, "QCOM": 2})
    world = angles_world(senate=senate)
    await arun(world, "insiders")
    walked = world["fmp"].calls["congress"]
    assert walked >= 2
    got = await run("congress_count", world["fmp"], run_date=THEME_RUN)
    assert world["fmp"].calls["congress"] == walked
    assert [(c.company.symbol, c.members) for c in got.records[0].counts] == [("AVGO", 4), ("NVDA", 3), ("AMD", 2)]


@pytest.mark.asyncio
async def test_phase3_the_congress_angle_is_not_due_thin_or_clashing_and_moves_on():
    # within CONGRESS_DUE_DAYS of the month's end: not due, no walk
    world = angles_world(senate=_senate_purchases({s: 2 for s in _SIX}))
    got = await arun(world, "insiders", run_date=date(2026, 11, 6))
    assert got.rejections.get("theme_congress_not_due") == 1 and world["fmp"].calls["congress"] == 0
    # three companies with two members: thin, the rotation wraps to "how they make money"
    A.clear_memo()
    world = angles_world(senate=_senate_purchases({"NVDA": 2, "AMD": 2, "AVGO": 2}))
    got = await arun(world, "insiders")
    assert got.rejections.get("theme_congress_thin") == 1 and [r.angle for r in got.records] == ["make_money"]
    # an in-month purchase under no symbol naming the company: its count could be low, so it is left out
    A.clear_memo()
    senate = _senate_purchases({s: 2 for s in ("NVDA", "AMD", "AVGO", "QCOM", "MRVL")}) + [
        crow("senate", "", "2026-10-21", "mx", desc="Broadcom Inc. common stock")]
    got = await arun(angles_world(senate=senate), "insiders")
    (rec,) = got.records
    assert "AVGO" not in [m.company.symbol for m in rec.members] and got.rejections.get("unmapped_purchase") == 1


# ── the whole rotation, end to end ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase3_one_theme_walks_all_five_angles_in_the_owners_order_and_each_post_composes():
    """End to end for ONE theme: five builds, each four weeks after the last, each fed the previous post's ledger key —
    "how they make money" → "who's biggest" → "growth + profit" → "insider scorecard" → "Congress" → back to the first;
    each record composes into a post its own picture layout draws (the templates' builders, validate_package clean)."""
    from app.services.marketing.script_service import template_picture_outlets

    pictured = template_picture_outlets(True, False, ("bluesky", "facebook", "linkedin", "threads"))
    issuer = {s: [ins_line(s, "S-Sale", filed="2026-12-01"), ins_line(s, filed="2026-12-02")] for s in _SIX}
    history: List[str] = []
    seen = []
    day = date(2026, 12, 16)                       # a Wednesday; each later build four weeks on
    for _ in range(6):
        A.clear_memo()
        senate = _senate_purchases({s: 2 + i for i, s in enumerate(_SIX)},
                                   day=f"{(day.replace(day=1) - timedelta(days=1)):%Y-%m}-10")
        month_start = (day.replace(day=1) - timedelta(days=1)).replace(day=1)
        world = angles_world(issuer={s: [dict(r, filingDate=f"{(day - timedelta(days=20)).isoformat()} 16:05:11",
                                              transactionDate=(day - timedelta(days=20)).isoformat())
                                         for r in rows_] for s, rows_ in issuer.items()},
                             senate=senate, rows=[theme(tickers_as_of=(day - timedelta(days=5)).isoformat())],
                             oldest=(month_start - timedelta(days=20)).isoformat())
        world["fmp"].market_caps = _caps(day=(day - timedelta(days=1)).isoformat())
        world["fmp"].income = {s: annual(s, fy=str(day.year - 1), end=f"{day.year - 1}-12-31",
                                         prior_end=f"{day.year - 2}-12-31") for s in _SIX}
        got = await trun(world, theme_history=tuple(history), run_date=day, theme_classes=("A", "C", "F"))
        (rec,) = got.records
        seen.append(rec.angle)
        out = _NT.compose(rec, run_date=day, store_state="live", allow_x_url=False, picture_outlets=pictured)
        assert out["image_spec"]["layout"] == _NT.THEME_LAYOUT_OF_ANGLE[rec.angle]
        assert _NT.validate_package(out, record=rec) == []
        history.append(R.ledger_key(rec))
        day += timedelta(days=28)
    assert seen == ["make_money", "biggest", "growth_profit", "insiders", "congress", "make_money"]


# ══ news-v23 phase 3, fix round (2026-11-20): who counts as CHECKED, joint filers, the listing gate, the list's date,
#    each angle's own time ════════════════════════════════════════════════════════════════════════════════════════

def _pictured():
    from app.services.marketing.script_service import template_picture_outlets

    return template_picture_outlets(True, False, ("bluesky", "facebook", "linkedin", "threads"))


def _public_text(out) -> str:
    """Every caption field and narration line of a composed package, one string."""
    lines = [v for line in out["video_script"] for v in ([line] if isinstance(line, str) else line)]
    return "\n".join([*out["captions"].values(), *lines, out["image_post"] if isinstance(out.get("image_post"), str)
                      else json.dumps(out.get("image_post"))])


class _Http404Client:
    """The real `FMPClient.get_insider_trades_since` over a transport that answers HTTP 404 on page 0 (FMP's
    "endpoint not on this plan" answer): the client returns ``[]`` with a WARNING, never an exception."""

    async def _make_request(self, path, params=None):
        req = httpx.Request("GET", "https://zqx.example/stable/insider-trading/search")
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))


@pytest.mark.asyncio
async def test_fix_an_empty_or_refused_issuer_read_is_never_counted_as_checked(caplog):
    """A per-issuer Form 4 read that holds NO row of its issuer — a foreign issuer's ([] — it files no Form 4) or one
    FMP refused with a page-0 404 (the client answers [] with a WARNING) — is not a check: the company is left out of
    `companies_checked` (``insider_feed_unusable``), so the post never says "No purchase or sale … was disclosed at the
    other {k} companies checked" or "No insider at the {c} companies checked …" about it; such a read is never
    memoised (a second build reads it again). Mutation-checked by hand (2026-11-20): with the issuer-row rule removed,
    or the read memoised without `keep=bool`, this test turned red."""
    from app.integrations import fmp as fmp_mod

    issuer = {s: [ins_line(s, "S-Sale", shares=10_000 * (i + 1))] for i, s in enumerate(("NVDA", "AMD", "AVGO", "QCOM"))}
    issuer["MRVL"] = []                                                    # a foreign issuer: no Form 4 ever
    world = angles_world(issuer=issuer)
    fmp = world["fmp"]
    intc = A.normalize_cik(issuer_cik("INTC"))
    orig = fmp.get_insider_trades_since

    async def read(since, **kw):
        if A.normalize_cik(kw.get("company_cik")) == intc:                 # INTC: the real client over a 404
            fmp.issuer_calls.append({"company_cik": kw.get("company_cik")})
            return await fmp_mod.FMPClient.get_insider_trades_since(_Http404Client(), since, **kw)
        return await orig(since, **kw)

    fmp.get_insider_trades_since = read
    got = await arun(world, "growth_profit", theme_angles=("insiders",))
    (rec,) = got.records
    assert [m.company.symbol for m in rec.members] == ["QCOM", "AVGO", "AMD", "NVDA"]
    assert rec.companies_checked == 4 and got.rejections.get("insider_feed_unusable") == 2
    assert "held no row of its issuer" in caplog.text and "HTTP 404 on page 0" in caplog.text
    out = _NT.compose(rec, run_date=THEME_RUN, store_state="live", allow_x_url=False, picture_outlets=_pictured())
    assert _NT.validate_package(out, record=rec) == []
    text = _public_text(out)
    assert "companies checked" not in text.replace("4 companies checked", "")        # only the 4 read whole
    assert "No insider at the 4 companies checked disclosed a purchase of the stock." in text
    assert "was disclosed at the other" not in text
    assert "4 of its 8 companies are not included." in text
    assert out["image_spec"]["subtitle"].startswith("4 of its 8 companies")
    # the two empty reads are read again by a second build (never memoised); the four whole ones are not
    before = len(fmp.issuer_calls)
    await arun(world, "growth_profit", theme_angles=("insiders",))
    assert len(fmp.issuer_calls) - before == 2


@pytest.mark.asyncio
async def test_fix_every_issuer_read_empty_is_an_outage_never_thin():
    """A plan or transport that answers every per-issuer read with [] (a page-0 403/404 everywhere) is the source being
    down: ``insider_feed_unavailable`` — the angle's turn lost as an outage, never "thin" over reads that saw nothing.
    Mutation-checked by hand (2026-11-20): with that check removed, this test turned red."""
    world = angles_world(issuer={s: [] for s in _SIX})
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await arun(world, "growth_profit", theme_angles=("insiders",))
    assert err.value.reason == "insider_feed_unavailable" and "held a row of its issuer" in err.value.detail


@pytest.mark.parametrize("over, reason", [
    ({"isAdr": True}, "adr"),                                              # TSM / ARM / NVO style
    ({"currency": "EUR"}, "not_usd"),
    ({"companyName": "QUALCOMM Depositary Shares"}, "non_common_listing"),
])
@pytest.mark.asyncio
async def test_fix_a_foreign_or_non_common_listing_is_never_read_or_checked(over, reason):
    """Before any read, the insider scorecard keeps only US common listings (`purpose="common"`, the Congress Count's
    and the Form 4 posts' gate): an ADR's or a foreign issuer's insiders may file no Form 4, so it is never counted as
    "checked, nothing disclosed". Mutation-checked by hand (2026-11-20): with the gate's purpose set back to
    "listing", all three cases turned red."""
    profiles = theme_profiles()
    profiles["QCOM"] = {**profiles["QCOM"], **over}
    issuer = {s: [ins_line(s, "S-Sale")] for s in _SIX}
    world = angles_world(issuer=issuer, profiles=profiles)
    got = await arun(world, "growth_profit", theme_angles=("insiders",))
    (rec,) = got.records
    assert "QCOM" not in [m.company.symbol for m in rec.members] and rec.companies_checked == 5
    assert got.rejections.get(reason) == 1
    qcom = A.normalize_cik(issuer_cik("QCOM"))
    assert qcom not in {A.normalize_cik(c["company_cik"]) for c in world["fmp"].issuer_calls}


@pytest.mark.asyncio
async def test_fix_the_congress_angle_refuses_what_the_congress_count_refuses():
    """Theme members are "listing" refs (an ADR kept, for the other angles); the Congress angle applies the Congress
    Count's own listing gate (`purpose="common"`), so an ADR with 2+ members is on NEITHER post — the two posts list the
    same companies from the same aggregate. Mutation-checked by hand (2026-11-20): with the gate's purpose set back to
    "listing", this test turned red."""
    profiles = theme_profiles()
    profiles["QCOM"] = {**profiles["QCOM"], "isAdr": True}
    senate = _senate_purchases({"NVDA": 3, "AMD": 2, "AVGO": 4, "QCOM": 5, "MRVL": 2})
    world = angles_world(senate=senate, profiles=profiles)
    got = await arun(world, "insiders")
    (rec,) = got.records
    assert rec.angle == "congress" and got.rejections.get("adr") == 1
    assert [(m.company.symbol, m.congress_members) for m in rec.members] == [
        ("AVGO", 4), ("NVDA", 3), ("AMD", 2), ("MRVL", 2)]
    cc = await run("congress_count", world["fmp"], run_date=THEME_RUN)
    assert "QCOM" not in [c.company.symbol for c in cc.records[0].counts] and cc.rejections.get("adr") == 1


def _joint(sym="NVDA", tx="S-Sale", shares=500_000, price=100.0, owned=5_000_000, form="4", filed=_IN_WINDOW,
           filers=(("0000000101", "ZQX FUND LP"), ("0000000102", "ZQX FUND GP LLC"), ("0000000103", "ZQX ADVISER LLC"))):
    """One Form 4 line as FMP lists a JOINT filing: the same line under each reporting owner."""
    return [ins_line(sym, tx, shares=shares, price=price, filed=filed, traded=_IN_WINDOW, cik=cik, name=name,
                     owned=owned, form=form) for cik, name in filers]


@pytest.mark.parametrize("rows, want", [
    # a fund, its general partner and its adviser report ONE $50M sale: $50M, never $150M
    (_joint(), (0.0, 50_000_000.0)),
    # the same for a purchase, and the joint copies listed in another order
    (list(reversed(_joint(tx="P-Purchase"))), (50_000_000.0, 0.0)),
    # two DIFFERENT people's trades of the same size and price (their own balances differ): both count
    ([ins_line("NVDA", "S-Sale", shares=1_000, cik="0000000201", name="ZQX A", owned=9_000),
      ins_line("NVDA", "S-Sale", shares=1_000, cik="0000000202", name="ZQX B", owned=4_000)], (0.0, 200_000.0)),
    # one reporter's identical fills on one filing stay separate fills, balance or none
    ([ins_line("NVDA", "S-Sale", owned=None), ins_line("NVDA", "S-Sale", owned=None)], (0.0, 200_000.0)),
])
def test_fix_joint_filers_count_one_line_once(rows, want):
    """Joint filers (fix round 2026-11-20): FMP lists one Form 4 line under each reporting owner, so the per-reporter
    dedupe summed every copy. `_insider_common.distinct_trades` (the holders and report paths' rule) now merges them.
    Mutation-checked by hand (2026-11-20): without it the two joint cases turned red."""
    assert A._theme_insider_totals(rows, cik=_CIK, window_start=_WS, window_end=_WE) == pytest.approx(want)


def test_fix_a_joint_copy_that_cannot_be_matched_and_a_co_filers_amendment_fail_closed():
    # the same line under two reporters with no balance on one: a joint copy or a second trade — never a guessed total
    rows = [ins_line("NVDA", "S-Sale", cik="0000000101", name="ZQX FUND LP", owned=None),
            ins_line("NVDA", "S-Sale", cik="0000000102", name="ZQX FUND GP LLC")]
    assert A._theme_insider_totals(rows, cik=_CIK, window_start=_WS, window_end=_WE) == "insider_feed_unusable"
    # a 4/A filed by the SECOND co-filer alone, its line identical to the first co-filer's original: the amendment is
    # read over every raw row BEFORE the joint copies merge (`distinct_trades` has no form type and would drop it).
    # Mutation-checked by hand (2026-11-20): with `distinct_trades` run over the raw rows first — or the unmatched-copy
    # check removed — this test turned red.
    orig, second = _joint(filers=(("0000000101", "ZQX FUND LP"),)), _joint(filers=(("0000000102", "ZQX FUND GP LLC"),),
                                                                           form="4/A", filed="2026-11-05")
    assert A._theme_insider_totals(orig + second, cik=_CIK, window_start=_WS,
                                   window_end=_WE) == "amended_filing"


@pytest.mark.asyncio
async def test_fix_a_list_with_no_review_date_never_says_members_as_of():
    """`tickers_as_of` NULL (every production row): owner decision 2026-11-20 ("Trust the app's lists") — the live list
    counts as current, the record carries the RUN day with `tickers_as_of_from_row`, and no angle's alt text says
    "Members as of <a day>" (the source line alone, `alt.source.bare`) — never the run day, never the row's
    `updated_at`. A list with its own date keeps the line. Mutation-checked by hand (2026-11-20): with
    `_theme_members_as_of` always answering `th.asof`, this test turned red."""
    world = angles_world(rows=[theme(tickers_as_of=None, updated_at="2026-11-03T15:00:00Z")],
                         issuer={s: [ins_line(s, "S-Sale")] for s in _SIX},
                         senate=_senate_purchases({s: 2 for s in _SIX}))
    seen = []
    for before in (None, "make_money", "biggest", "growth_profit", "insiders"):
        A.clear_memo()
        got = await arun(world, before)
        (rec,) = got.records
        assert rec.tickers_as_of == THEME_RUN and rec.tickers_as_of_from_row is True
        out = _NT.compose(rec, run_date=THEME_RUN, store_state="live", allow_x_url=False, picture_outlets=_pictured())
        assert _NT.validate_package(out, record=rec) == []
        alt = json.dumps(out["image_post"])
        assert "Members as of" not in alt and "Nov 3" not in alt and "Source: " in alt
        # the record round-trips through its fact sheet with the flag (revalidate recomposes from it)
        sheet = R.fact_sheet(rec, rejections={}, selection={})
        assert R.record_from_fact_sheet(sheet) == rec and _NT.revalidate(out, fact_sheet=sheet, run_date=THEME_RUN) == []
        seen.append(rec.angle)
    assert seen == list(R.THEME_ANGLES)
    A.clear_memo()
    got = await trun(theme_world([theme(tickers_as_of="2026-11-02")]))
    (rec,) = got.records
    assert rec.tickers_as_of_from_row is False
    out = _NT.compose(rec, run_date=THEME_RUN, store_state="live", allow_x_url=False, picture_outlets=_pictured())
    assert "Members as of Nov 2, 2026" in json.dumps(out["image_post"])
    # an older fact sheet (before the flag) reads back as a list with its own date
    body = R.fact_sheet(rec, rejections={}, selection={})
    body["record"].pop("tickers_as_of_from_row")
    assert R.record_from_fact_sheet(body).tickers_as_of_from_row is False


@pytest.mark.asyncio
async def test_fix_a_slow_angle_loses_its_own_turn_never_the_series(monkeypatch, caplog):
    """Each angle runs under its OWN share of the time (THEME_ANGLE_BUDGET_SECONDS): a theme whose next angles are the
    insider scorecard and Congress, both slow (each read 3 s), on a 5 s series budget — each loses ITS turn when its
    share runs out and "how they make money" still posts. Before: one step per angle with all the time left — the
    insider walk spent the whole budget and the series ended `budget_exhausted` with no post (and the next Wednesday
    repeated it). Mutation-checked by hand (2026-11-20): with the per-angle deadline removed (the series' deadline for
    every angle), this test turned red."""
    monkeypatch.setitem(A.THEME_ANGLE_BUDGET_SECONDS, "insiders", 1.2)
    monkeypatch.setitem(A.THEME_ANGLE_BUDGET_SECONDS, "congress", 1.2)
    world = angles_world(issuer={s: [ins_line(s, "S-Sale")] for s in _SIX},
                         senate=_senate_purchases({s: 2 for s in _SIX}))
    fmp = world["fmp"]
    orig_read, orig_senate = fmp.get_insider_trades_since, fmp.get_senate_latest

    async def slow_read(since, **kw):
        if kw.get("company_cik") is not None:
            await asyncio.sleep(3.0)
        return await orig_read(since, **kw)

    async def slow_senate(limit=1000):
        await asyncio.sleep(3.0)
        return await orig_senate(limit)

    fmp.get_insider_trades_since, fmp.get_senate_latest = slow_read, slow_senate
    started = time.monotonic()
    got = await arun(world, "growth_profit", left=5.0)
    (rec,) = got.records
    assert rec.angle == "make_money" and time.monotonic() - started < 4.5
    assert caplog.text.count("ran out of its own") == 2
    await _settle_leads()
    # the series' OWN deadline still ends the walk: every angle slow and no record → budget_exhausted, raised
    A.clear_memo()
    for angle in A.THEME_ANGLE_BUDGET_SECONDS:
        monkeypatch.setitem(A.THEME_ANGLE_BUDGET_SECONDS, angle, 60.0)
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await arun(world, "growth_profit", left=2.0)
    assert err.value.reason == "budget_exhausted"
    await _settle_leads()


# ══ phase 4 (2026-11-20): "how they make money" reads EVERY member in market-value order, draws the members with a fact
#    LARGEST MARKET VALUE FIRST, and walks a GROSS newest stack back to the newest year that is not — the owner's "for AI
#    CHIPS, i don't see ARM, Intel and SNPS? add for me" (theme_ai_chips_more.jpg) ═══════════════════════════════════

import math  # noqa: E402
from typing import Tuple  # noqa: E402

from app.services import revenue_breakdown_service as _RBS  # noqa: E402

#: The PRODUCTION Silicon Rush member list, in its order (read-only query of `trending_themes`, 2026-11-20 — public app
#: data; `tickers_as_of` NULL there). Company names are the real ones; every FIGURE below is a SAMPLE.
SILICON_RUSH = ("NVDA", "TSM", "AVGO", "ASML", "AMD", "MU", "ARM", "MRVL", "QCOM", "AMAT", "LRCX", "KLAC", "SNPS",
                "CDNS", "ALAB", "CRDO", "MPWR", "COHR", "LITE", "INTC", "GFS")
_SR_NAMES = {"NVDA": "NVIDIA Corporation", "TSM": "Taiwan Semiconductor Manufacturing Company Limited",
             "AVGO": "Broadcom Inc.", "ASML": "ASML Holding N.V.", "AMD": "Advanced Micro Devices, Inc.",
             "MU": "Micron Technology, Inc.", "ARM": "Arm Holdings plc American Depositary Shares",
             "MRVL": "Marvell Technology, Inc.", "QCOM": "QUALCOMM Incorporated", "AMAT": "Applied Materials, Inc.",
             "LRCX": "Lam Research Corporation", "KLAC": "KLA Corporation", "SNPS": "Synopsys, Inc.",
             "CDNS": "Cadence Design Systems, Inc.", "ALAB": "Astera Labs, Inc.",
             "CRDO": "Credo Technology Group Holding Ltd", "MPWR": "Monolithic Power Systems, Inc.",
             "COHR": "Coherent Corp.", "LITE": "Lumentum Holdings Inc.", "INTC": "Intel Corporation",
             "GFS": "GlobalFoundries Inc."}
#: SAMPLE market values (USD) in a REALISTIC order — the size ranking of late 2026, rounded; never real data, never in
#: code. Synopsys is the 14th largest of the 21, Arm the 11th, Intel the 8th.
_SR_CAPS = {"NVDA": 4.40e12, "AVGO": 1.60e12, "TSM": 1.50e12, "ASML": 4.0e11, "AMD": 3.9e11, "MU": 2.5e11,
            "AMAT": 2.1e11, "INTC": 2.0e11, "QCOM": 1.8e11, "LRCX": 1.7e11, "ARM": 1.6e11, "KLAC": 1.5e11,
            "CDNS": 9.0e10, "SNPS": 8.5e10, "MRVL": 8.0e10, "MPWR": 4.5e10, "ALAB": 3.0e10, "CRDO": 2.5e10,
            "COHR": 2.4e10, "LITE": 2.0e10, "GFS": 1.9e10}
P4_RUN = date(2026, 11, 25)                         # a Wednesday (the theme's day)
_P4_ADR = {"TSM", "ASML", "ARM"}


def _sr_profiles(caps=None):
    caps = _SR_CAPS if caps is None else caps
    return {s: prof(s, _SR_NAMES[s], exchange="NYSE" if s in ("TSM", "KLAC", "COHR") else "NASDAQ",
                    cap=caps.get(s, 0.0), isAdr=s in _P4_ADR) for s in SILICON_RUSH}


def _sr_caps_rows(day="2026-11-24", caps=None):
    return [{"symbol": s, "date": day, "marketCap": v} for s, v in (_SR_CAPS if caps is None else caps).items()]


def _seg_bd(sym, fy, lines, revenue=None, **over):
    """A clean (net) newest breakdown: `lines` [(name, value)], revenue their sum unless given."""
    return breakdown(symbol=sym, fiscal_year=fy, reported_revenue=revenue if revenue is not None else
                     math.fsum(v for _n, v in lines), revenue_sources=[src(n, v) for n, v in lines], **over)


#: INTC as FMP files it (the segment names of tests/test_revenue_breakdown_reconciliation.py's INTC rows): fiscal
#: 2023-2025 GROSS (Intel Foundry's sales to Intel's own product groups, an "Intersegment Eliminations" row), fiscal
#: 2022 net. SAMPLE figures.
_INTC_SEG_RAW = [
    {"symbol": "INTC", "fiscalYear": 2025, "date": "2025-12-27", "data": {
        "Client Computing Group": 32.2e9, "Data Center and AI": 16.9e9, "Intel Foundry": 17.8e9, "All Other": 3.6e9,
        "Intersegment Eliminations": -17.6e9}},
    {"symbol": "INTC", "fiscalYear": 2024, "date": "2024-12-28", "data": {
        "Client Computing Group": 30.3e9, "Data Center and AI": 12.8e9, "Intel Foundry": 17.5e9, "All Other": 3.8e9,
        "Intersegment Eliminations": -11.3e9}},
    {"symbol": "INTC", "fiscalYear": 2023, "date": "2023-12-30", "data": {
        "Client Computing Group": 29.3e9, "Data Center and AI": 15.5e9, "Intel Foundry": 18.9e9, "All Other": 3.7e9,
        "Intersegment Eliminations": -13.2e9}},
    {"symbol": "INTC", "fiscalYear": 2022, "date": "2022-12-31", "data": {
        "Client Computing Group": 34.7e9, "Data Center and AI": 19.2e9, "Network and Edge": 8.4e9, "Mobileye": 0.8e9}},
]
_INTC_INCOME_RAW = [{"symbol": "INTC", "fiscalYear": str(y), "date": f"{y}-12-31", "revenue": r}
                    for y, r in ((2025, 52.9e9), (2024, 53.1e9), (2023, 54.2e9), (2022, 63.1e9), (2021, 79.0e9))]


def _intc_years():
    """INTC's older years through the REAL reconciliation (`revenue_breakdown_service.segment_years`)."""
    return _RBS.segment_years("INTC", _INTC_SEG_RAW, _INTC_INCOME_RAW)


def _intc_newest():
    """INTC's newest breakdown as the card's service builds it: fiscal 2025, gross (eliminations 17.6B)."""
    newest = _intc_years()[0]
    return breakdown(symbol="INTC", fiscal_year="2025", reported_revenue=52.9e9,
                     revenue_sources=list(newest.revenue_sources),
                     intersegment_eliminations=newest.intersegment_eliminations)


def _sr_breakdowns(*, with_big_facts=False):
    """SAMPLE newest breakdowns of the 21. TSM and ASML: a single "Total Revenue" bar (the fixture's assumption — no
    product segmentation for these 20-F filers), QCOM: code-only lines ("QCT" / "QTL" / "QSI" fold into the rest) — no
    fact for the three. `with_big_facts`: they DO have one (the other side of the market-value rule)."""
    bds = {
        "NVDA": _seg_bd("NVDA", "2026", [("Data Center", 115e9), ("Gaming", 11e9), ("Other", 4e9)]),
        "TSM": _seg_bd("TSM", "2025", [("Total Revenue", 90e9)]),
        "AVGO": _seg_bd("AVGO", "2025", [("Semiconductor solutions", 30e9), ("Infrastructure software", 21e9)]),
        "ASML": _seg_bd("ASML", "2025", [("Total Revenue", 32e9)]),
        "AMD": _seg_bd("AMD", "2025", [("Data Center", 12.6e9), ("Client", 7e9), ("Gaming", 2.6e9),
                                       ("Embedded", 3.6e9)]),
        "MU": _seg_bd("MU", "2025", [("Compute and Networking", 16e9), ("Mobile", 9e9), ("Storage", 7e9),
                                     ("Embedded", 5e9)]),
        "ARM": _seg_bd("ARM", "2026", [("Royalty", 2.3e9), ("License and other", 1.9e9)]),
        "MRVL": _seg_bd("MRVL", "2026", [("Data center", 4.3e9), ("Enterprise networking", 0.9e9),
                                         ("Carrier infrastructure", 0.6e9)]),
        "QCOM": _seg_bd("QCOM", "2025", [("QCT", 33e9), ("QTL", 5.6e9), ("QSI", 0.3e9)]),
        "AMAT": _seg_bd("AMAT", "2025", [("Semiconductor Systems", 20.8e9), ("Applied Global Services", 6.2e9),
                                         ("Display", 0.9e9)]),
        "LRCX": _seg_bd("LRCX", "2025", [("Systems", 11.6e9), ("Customer support", 6.8e9)]),
        "KLAC": _seg_bd("KLAC", "2025", [("Semiconductor Process Control", 10.2e9),
                                         ("Specialty Semiconductor Process", 0.6e9), ("PCB and Component", 0.4e9)]),
        "SNPS": _seg_bd("SNPS", "2025", [("Electronic Design Automation Software", 5.7e9), ("Design IP", 1.35e9)]),
        "CDNS": _seg_bd("CDNS", "2025", [("Core EDA", 3.4e9), ("Hardware and IP", 1.0e9),
                                         ("System Design and Analysis", 0.5e9)]),
        "ALAB": _seg_bd("ALAB", "2025", [("Connectivity", 0.7e9), ("Other", 0.1e9)]),
        "CRDO": _seg_bd("CRDO", "2026", [("Product sales", 0.8e9), ("IP license", 0.05e9)]),
        "MPWR": _seg_bd("MPWR", "2025", [("Enterprise Data", 1.0e9), ("Automotive", 0.6e9), ("Storage", 0.5e9),
                                         ("Industrial", 0.5e9)]),
        "COHR": _seg_bd("COHR", "2025", [("Datacenter and Communications", 3.6e9), ("Industrial", 2.2e9)]),
        "LITE": _seg_bd("LITE", "2025", [("Cloud and Networking", 1.4e9), ("Industrial Tech", 0.25e9)]),
        "INTC": _intc_newest(),
        "GFS": _seg_bd("GFS", "2025", [("Smart Mobile Devices", 2.7e9), ("Communications Infrastructure", 1.4e9),
                                       ("Automotive", 1.0e9), ("Home and Industrial", 1.5e9)]),
    }
    if with_big_facts:
        bds["TSM"] = _seg_bd("TSM", "2025", [("High Performance Computing", 52e9), ("Smartphone", 38e9)])
        bds["ASML"] = _seg_bd("ASML", "2025", [("Systems", 24e9), ("Installed Base", 8e9)])
        bds["QCOM"] = _seg_bd("QCOM", "2025", [("Handsets", 24.9e9), ("IoT", 5.4e9), ("Automotive", 2.9e9)])
    return bds


def _sr_world(*, breakdowns=None, caps_rows=None, years=None, profiles=None):
    revenue = FakeService(_sr_breakdowns() if breakdowns is None else breakdowns,
                          default=breakdown(degraded=["segmentation_unavailable"]),
                          years={"INTC": _intc_years()} if years is None else years)
    row = theme(slug="silicon-rush", title="Silicon Rush", tickers=list(SILICON_RUSH), blocked_tickers=[],
                tickers_as_of=None, updated_at="2026-11-18T15:00:00+00:00")
    fmp = FakeFMP(profiles=_sr_profiles() if profiles is None else profiles,
                  market_caps=_sr_caps_rows() if caps_rows is None else caps_rows)
    return dict(fmp=fmp, revenue=revenue, sb=FakeSB({"trending_themes": [row]}))


def _sr_compose(rec):
    out = _NT.compose(rec, run_date=P4_RUN, store_state="live", allow_x_url=False, picture_outlets=_pictured())
    assert _NT.validate_package(out, record=rec) == []
    return out, {t["logo"]: t.get("line") for t in out["image_spec"]["tiles"]}


@pytest.mark.asyncio
async def test_p4_the_production_silicon_rush_list_draws_arm_synopsys_and_intel_fy2022():
    """The owner's ask on the REAL Silicon Rush list (21 members, its own order; SAMPLE figures): every member is read
    (Intel is its 20th — never read before), Intel's gross newest stack walks back to fiscal 2022, and the picture's 12
    tiles are the members with a fact, largest market value first — Arm, Intel ("Client Computing, FY2022") and
    Synopsys ("EDA Software") among them, as in theme_ai_chips_more.jpg."""
    world = _sr_world()
    got = await trun(world, run_date=P4_RUN, theme_angles=("make_money",))
    (rec,) = got.records
    assert rec.angle == "make_money" and len(rec.members) == 21 and rec.theme_size == 21
    # every member read, the largest (profile market value) first
    assert world["revenue"].calls == sorted(SILICON_RUSH, key=lambda s: -_SR_CAPS[s])
    assert world["revenue"].year_calls == ["INTC"]                       # the one gross stack: one older-years read
    assert world["fmp"].calls["market_caps"] == 1
    intc = next(m for m in rec.members if m.company.symbol == "INTC")
    assert (intc.top_segment, intc.fiscal_year, intc.latest_fiscal_year) == ("Client Computing Group", "2022", "2025")
    assert intc.top_segment_share == pytest.approx(34.7e9 / 63.1e9)
    out, tiles = _sr_compose(rec)
    assert list(tiles) == ["NVDA", "AVGO", "AMD", "MU", "AMAT", "INTC", "LRCX", "ARM", "KLAC", "CDNS", "SNPS", "MRVL"]
    assert tiles["INTC"] == "Client Computing, FY2022 · 55% of revenue"
    assert tiles["SNPS"] == "EDA Software · 81% of revenue"
    assert tiles["ARM"] == "Royalty · 55% of revenue"
    assert out["image_spec"]["more"] == "+9 more"
    assert out["image_spec"]["subtitle"].endswith("fiscal 2025 or 2026")        # the tagged FY2022 never sets it
    # only the 12 tiles carry their fact (A.THEME_FACT_TILES): 18 facts in the long captions overflowed Instagram,
    # YouTube and Threads — with 12, every outlet keeps the post (none dropped, X / Bluesky as before)
    assert sum(m.top_segment is not None for m in rec.members) == A.THEME_FACT_TILES == len(tiles)
    assert out["dropped_outlets"] == {}
    # the members without a fact (TSM, ASML, QCOM, and the smallest six past the tiles) stay in the post, named
    long_caption = out["captions"]["facebook"]
    for name in ("TSMC", "ASML Holding", "Qualcomm", "Monolithic Power Systems"):
        assert name in long_caption, name
    for name in ("Astera Labs", "Coherent", "Lumentum Holdings", "GlobalFoundries"):
        assert name in long_caption, name
    assert "Monolithic Power Systems:" not in long_caption           # named, never with a fact the post does not carry
    # the old rule's view of the same data (the first 16 in list order): Intel never read, so never drawn
    assert SILICON_RUSH.index("INTC") >= 16


@pytest.mark.asyncio
async def test_p4_the_tiles_are_the_largest_members_with_a_fact_so_synopsys_needs_room():
    """The rule is market value, not a promise of three names: when every larger member has a fact (TSM, ASML and QCOM
    too), the 12 tiles are the 12 largest — Synopsys (14th largest) and Marvell go to "+N more" and keep their caption
    paragraph; Intel (8th) and Arm (11th) stay. Recorded in the release notes as the owner's call."""
    world = _sr_world(breakdowns=_sr_breakdowns(with_big_facts=True))
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    out, tiles = _sr_compose(rec)
    assert list(tiles) == ["NVDA", "AVGO", "TSM", "ASML", "AMD", "MU", "AMAT", "INTC", "QCOM", "LRCX", "ARM", "KLAC"]
    assert "SNPS" not in tiles and "Synopsys" in out["captions"]["facebook"]
    assert "Synopsys:" not in out["captions"]["facebook"]                     # named: its fact is not carried
    # at most TikTok (600 characters for the cover's 17 left-out companies, eight with a fact — the pre-existing limit
    # phase 3 recorded; the old first-16 rule dropped it on this data too); Instagram, YouTube and Threads keep it
    assert set(out["dropped_outlets"]) <= {"tiktok"}


@pytest.mark.asyncio
async def test_p4_market_value_order_with_missing_values_ties_and_a_failed_batch(caplog):
    """The record's order: the members WITH a fact by the batch's market value, the largest first (a tie: the symbol);
    those with no usable value (no row, a non-USD listing, another day's row) after them in the theme's own order; then
    the members without a fact in the theme's own order. The batch failing never costs the angle: the facts keep the
    theme's order (WARNING) — and the post still holds no market value (`ThemeExplainer` refuses one off "biggest")."""
    caps = dict(_SR_CAPS, AMD=_SR_CAPS["AVGO"])                                        # AMD ties AVGO
    rows = [r for r in _sr_caps_rows(caps=caps) if r["symbol"] not in ("MU", "ARM")]    # no row for MU, ARM
    rows = [dict(r, date="2026-11-20") if r["symbol"] == "KLAC" else r for r in rows]   # KLAC: another day's value
    profiles = _sr_profiles()
    profiles["LRCX"] = dict(profiles["LRCX"], currency="EUR")                          # LRCX: not a USD listing
    # twelve members with a fact (the six smallest give none), so every fact is carried
    bds = {s: bd for s, bd in _sr_breakdowns().items() if s not in ("ALAB", "CRDO", "MPWR", "COHR", "LITE", "GFS")}
    world = _sr_world(caps_rows=rows, profiles=profiles, breakdowns=bds)
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    order = [m.company.symbol for m in rec.members]
    with_fact = [m.company.symbol for m in rec.members if m.top_segment is not None]
    assert with_fact == ["NVDA", "AMD", "AVGO", "AMAT", "INTC", "CDNS", "SNPS", "MRVL",   # valued, largest first
                         "MU", "ARM", "LRCX", "KLAC"]              # no usable value: the theme's own order
    assert order == with_fact + ["TSM", "ASML", "QCOM", "ALAB", "CRDO", "MPWR", "COHR", "LITE", "GFS"]
    # the pure order: a tie of values breaks by the symbol (AMD before AVGO)
    assert A._theme_tile_order(SimpleNamespace(refs=tuple(R.CompanyRef(symbol=s, name=s) for s in ("AVGO", "AMD"))),
                               {"AVGO": 1, "AMD": 1}, {"AVGO": 5e9, "AMD": 5e9})[0].symbol == "AMD"
    assert all(m.market_value_usd is None for m in rec.members)
    # the batch down: the angle still posts, the facts in the theme's order
    A.clear_memo()
    caplog.clear()
    world = _sr_world()
    world["fmp"].market_caps = FMPUnavailableException("batch down")
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    assert [m.company.symbol for m in rec.members if m.top_segment is not None] == [
        s for s in SILICON_RUSH if s not in ("TSM", "ASML", "QCOM")][:A.THEME_FACT_TILES]
    assert "no market values for the tile order" in caplog.text


@pytest.mark.asyncio
async def test_p4_a_gross_newest_stack_reads_its_older_years_once_and_a_failed_read_is_no_fact(caplog):
    """Only a GROSS newest stack makes the older-years read (`get_segment_years`), once per symbol (memoised for the
    day's later builds); the read failing — or the service having no such method — is that member's missing fact
    (WARNING), never a share computed from the gross stack, and the post still goes out."""
    world = _sr_world()
    await trun(world, run_date=P4_RUN, theme_angles=("make_money",))
    assert world["revenue"].year_calls == ["INTC"]
    await trun(world, run_date=P4_RUN, theme_angles=("make_money",))
    assert world["revenue"].year_calls == ["INTC"]                          # memoised
    for broken in (FMPUnavailableException("segmentation down"), RuntimeError("boom")):
        A.clear_memo()
        caplog.clear()
        world = _sr_world(years={"INTC": broken})
        (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
        intc = next(m for m in rec.members if m.company.symbol == "INTC")
        assert (intc.top_segment, intc.top_segment_share, intc.latest_fiscal_year) == (None, None, None)
        assert "INTC's newest segment stack (fiscal 2025) includes intersegment sales and its older years could " \
               "not be read" in caplog.text
    # a service without the method (an older deploy's): the same — no fact, no crash
    A.clear_memo()

    class _NoYears(FakeService):
        get_segment_years = None

    world = _sr_world()
    world["revenue"] = _NoYears(_sr_breakdowns(), default=breakdown(degraded=["segmentation_unavailable"]))
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    assert next(m for m in rec.members if m.company.symbol == "INTC").top_segment is None


def _yr(fy, lines=(("Client Computing Group", 34.7e9), ("Data Center and AI", 28.4e9)), *, gross=False,
        sym="INTC", revenue=None, **over):
    total = math.fsum(v for _n, v in lines)
    rev = revenue if revenue is not None else (total * 0.8 if gross else total)
    return SimpleNamespace(symbol=sym, fiscal_year=fy, revenue_sources=[src(n, v) for n, v in lines],
                           reported_revenue=rev, intersegment_eliminations=(total - rev) if gross else None,
                           degraded=over.pop("degraded", []), **over)


_GROSS_2025 = _yr("2025", gross=True)


@pytest.mark.parametrize("years, want", [
    # gross 2025-2023, net 2022 (the bound at a 2026 run): fiscal 2022's fact, tagged with the newest year
    ((_yr("2025", gross=True), _yr("2024", gross=True), _yr("2023", gross=True), _yr("2022")), "2022"),
    # the newest net year decides at once
    ((_yr("2025", gross=True), _yr("2024")), "2024"),
    # gross every year down to the bound — a net year past it is never reached
    ((_yr("2025", gross=True), _yr("2024", gross=True), _yr("2023", gross=True), _yr("2022", gross=True),
      _yr("2021")), None),
    # gross every year the history holds
    ((_yr("2025", gross=True), _yr("2024", gross=True)), None),
    # a year missing on the way is never skipped
    ((_yr("2025", gross=True), _yr("2023")), None),
    # the newest net year gives no fact (a tie for the top) — the walk never goes past it to an older year
    ((_yr("2025", gross=True), _yr("2024", (("A Line", 20e9), ("B Line", 20e9))), _yr("2023")), None),
    # ... nor past a degraded year (no income statement of that year, two records that disagree)
    ((_yr("2025", gross=True), _yr("2024", degraded=["income_unpaired"]), _yr("2023")), None),
    ((_yr("2025", gross=True), _yr("2024", degraded=["year_conflict"]), _yr("2023")), None),
    # one fiscal year twice; another company's year; an unreadable year
    ((_yr("2025", gross=True), _yr("2024"), _yr("2024")), None),
    ((_yr("2025", gross=True), _yr("2024", sym="AMD")), None),
    ((_yr("2025", gross=True), _yr("FY24")), None),
    # the two reads disagree: the history's newest is newer than the card's, or its 2025 is not gross
    ((_yr("2026"), _yr("2025", gross=True), _yr("2024")), None),
    ((_yr("2025"), _yr("2024")), None),
    # nothing read / not a sequence
    ((), None), (None, None),
])
def test_p4_the_walk_back_takes_the_newest_net_year_within_the_bound_or_nothing(years, want):
    """Note on the bound: the walk's range stops at it AND `_segment_fact` refuses a year past it — two checks, so
    breaking one alone stays green (mutation-checked: breaking both turns the "a net year past it" row red)."""
    got = A._walked_back_fact("INTC", _GROSS_2025, years, P4_RUN)
    if want is None:
        assert got is None
    else:
        seg, share, fy, latest = got
        assert (seg, fy, latest) == ("Client Computing Group", want, "2025") and 0 < share <= 1


def test_p4_the_walk_back_starts_only_from_a_gross_newest_stack():
    history = (_yr("2025", gross=True), _yr("2024"))
    assert A._walked_back_fact("INTC", _yr("2025"), history, P4_RUN) is None                       # net: no walk
    assert A._walked_back_fact("INTC", _yr("2025", gross=True, degraded=["x"]), history, P4_RUN) is None
    assert A._walked_back_fact("INTC", _yr("2025", gross=True), history, P4_RUN)[2] == "2024"
    assert not A._stack_is_gross(_yr("2025")) and A._stack_is_gross(_GROSS_2025)
    assert not A._stack_is_gross(SimpleNamespace(**{**vars(_GROSS_2025), "fiscal_year": None}))
    # a newest year dated after the run's is a broken feed: never walked
    assert A._walked_back_fact("INTC", _yr("2027", gross=True), (_yr("2027", gross=True), _yr("2026")), P4_RUN) is None
    # the bound is the run's: at a 2027 run, 2022 is past it
    four = (_yr("2025", gross=True), _yr("2024", gross=True), _yr("2023", gross=True), _yr("2022"))
    assert A._walked_back_fact("INTC", _GROSS_2025, four, P4_RUN)[2] == "2022"
    assert A._walked_back_fact("INTC", _GROSS_2025, four, date(2027, 1, 6)) is None


def test_p4_segment_years_reconciles_every_year_like_the_card():
    """The service's pure year walk (`segment_years`): newest first, one entry per fiscal year, each through the card's
    own reconciliation — INTC's 2023-2025 "gross" (eliminations > 0), 2022 "exact"; a year with no income statement,
    two records of one year that disagree (segments or revenue) — degraded, never guessed; two identical records — one
    year; a record without a readable fiscal year — skipped."""
    years = _RBS.segment_years("INTC", _INTC_SEG_RAW, _INTC_INCOME_RAW)
    assert [(y.fiscal_year, y.outcome) for y in years] == [("2025", "gross"), ("2024", "gross"), ("2023", "gross"),
                                                           ("2022", "exact")]
    assert all(y.intersegment_eliminations > 0 for y in years[:3]) and years[3].intersegment_eliminations is None
    assert years[0].intersegment_eliminations == pytest.approx(70.5e9 - 52.9e9)
    assert A._segment_fact(years[0], P4_RUN, max_age_years=4) is None             # a share is never stated from it
    seg = list(_INTC_SEG_RAW) + [dict(_INTC_SEG_RAW[3])]                           # an identical copy of 2022
    assert len(_RBS.segment_years("INTC", seg, _INTC_INCOME_RAW)) == 4
    seg = list(_INTC_SEG_RAW) + [dict(_INTC_SEG_RAW[3], data={"Client Computing Group": 1e9, "Other": 2e9})]
    assert _RBS.segment_years("INTC", seg, _INTC_INCOME_RAW)[3].degraded == ("year_conflict",)
    income = _INTC_INCOME_RAW + [dict(_INTC_INCOME_RAW[1], revenue=1.0)]          # 2024 twice, disagreeing
    assert _RBS.segment_years("INTC", _INTC_SEG_RAW, income)[1].degraded == ("year_conflict",)
    unpaired = _RBS.segment_years("INTC", _INTC_SEG_RAW, _INTC_INCOME_RAW[:2])
    assert [y.degraded for y in unpaired] == [(), (), ("income_unpaired",), ("income_unpaired",)]
    junk = [{"symbol": "INTC", "date": "", "data": {"X": 1e9}}, "not a row", None] + _INTC_SEG_RAW[:1]
    assert [y.fiscal_year for y in _RBS.segment_years("INTC", junk, _INTC_INCOME_RAW)] == ["2025"]
    assert _RBS.segment_years("INTC", [], _INTC_INCOME_RAW) == ()
    # a thin year (segments under half of revenue) carries no sources: no fact, and it stops a walk
    thin = _RBS.segment_years("INTC", [{"symbol": "INTC", "fiscalYear": 2022, "data": {"Mobileye": 0.8e9}}],
                              _INTC_INCOME_RAW)
    assert thin[0].outcome == "thin" and thin[0].revenue_sources == ()


class _YearsFMP:
    """The two licensed feeds `get_segment_years` reads; any other read is a bug (AttributeError)."""

    def __init__(self, seg=None, income=None):
        self.seg = _INTC_SEG_RAW if seg is None else seg
        self.income = _INTC_INCOME_RAW if income is None else income
        self.calls: List[Tuple[str, Any]] = []

    async def get_revenue_product_segmentation(self, ticker, period="annual", structure="flat"):
        self.calls.append(("segmentation", (ticker, period)))
        if isinstance(self.seg, BaseException):
            raise self.seg
        return json.loads(json.dumps(self.seg)) if isinstance(self.seg, list) else self.seg

    async def get_income_statement(self, ticker, period="annual", limit=10):
        self.calls.append(("income", (ticker, period, limit)))
        if isinstance(self.income, BaseException):
            raise self.income
        return json.loads(json.dumps(self.income)) if isinstance(self.income, list) else self.income


def _years_service(fmp):
    svc = _RBS.RevenueBreakdownService.__new__(_RBS.RevenueBreakdownService)
    svc.fmp, svc.supabase = fmp, None
    return svc


@pytest.mark.asyncio
async def test_p4_get_segment_years_reads_the_cards_two_licensed_feeds_and_caches_only_answers(monkeypatch):
    """`RevenueBreakdownService.get_segment_years`: the card's own two feeds (product segmentation, annual; the income
    statement's last SEGMENT_YEARS_INCOME_ROWS years) — both paths licensed — kept in the in-memory tier; a failed or
    malformed leg RAISES and caches nothing; an empty answer is kept only the degraded TTL. No Supabase tier."""
    from app.integrations import fmp_entitlements

    assert fmp_entitlements.is_entitled("revenue-product-segmentation")
    assert fmp_entitlements.is_entitled("income-statement")
    monkeypatch.setattr(_RBS, "_cache", {})
    monkeypatch.setattr(_RBS, "_inflight", {})
    fmp = _YearsFMP()
    svc = _years_service(fmp)
    years = await svc.get_segment_years("intc")
    assert [y.fiscal_year for y in years] == ["2025", "2024", "2023", "2022"]
    assert fmp.calls == [("segmentation", ("INTC", "annual")), ("income", ("INTC", "annual", 5))]
    assert _RBS.SEGMENT_YEARS_INCOME_ROWS == 5
    assert await svc.get_segment_years("INTC") == years and len(fmp.calls) == 2          # the in-memory tier
    for seg, income, err in ((FMPUnavailableException("seg down"), None, FMPUnavailableException),
                             (None, RuntimeError("income down"), RuntimeError),
                             ({"Error Message": "x"}, None, FMPUnavailableException),
                             (None, {"Error Message": "x"}, FMPUnavailableException)):
        monkeypatch.setattr(_RBS, "_cache", {})
        bad = _years_service(_YearsFMP(seg=seg, income=income))
        with pytest.raises(err):
            await bad.get_segment_years("INTC")
        assert _RBS._cache == {} and _RBS._inflight == {}
    monkeypatch.setattr(_RBS, "_cache", {})
    empty = _years_service(_YearsFMP(seg=[]))
    assert await empty.get_segment_years("INTC") == ()
    stamp, value = _RBS._cache["rev_segment_years:INTC"]
    assert value == () and time.time() - stamp >= _RBS._CACHE_TTL - _RBS._DEGRADED_CACHE_TTL - 1
    with pytest.raises(ValueError):
        await svc.get_segment_years("not a ticker!")


class _SlowService(FakeService):
    """Each breakdown read takes `delay` seconds; `started` records the order the reads BEGIN in."""

    def __init__(self, *a, delay=0.0, **kw):
        super().__init__(*a, **kw)
        self.delay, self.started = delay, []

    async def get_revenue_breakdown(self, sym):
        self.started.append(sym)
        await asyncio.sleep(self.delay)
        return await super().get_revenue_breakdown(sym)


@pytest.mark.asyncio
async def test_p4_a_share_cut_short_leaves_only_the_smallest_unread_and_none_read_is_the_angles_turn_lost(
        monkeypatch, caplog):
    """The budget's worst path. The reads start in MARKET-VALUE order (the theme's own profile batch: the batch that
    orders the tiles runs beside them) through THEME_SEGMENT_CONCURRENCY slots; when the angle's own share runs out the
    reads still going are cancelled and only THOSE members — the smallest — are listed without a fact (WARNING), the
    answers in hand kept. No read answered at all → ``budget_exhausted``: that angle's turn lost (an outage)."""
    monkeypatch.setattr(A, "THEME_SEGMENT_CONCURRENCY", 2)
    monkeypatch.setitem(A.THEME_ANGLE_BUDGET_SECONDS, "make_money", 1.2)
    syms = ("NVDA", "TSM", "AVGO", "ASML", "AMD", "MU", "ARM", "MRVL")
    caps = {s: _SR_CAPS[s] for s in syms}
    bds = _sr_breakdowns(with_big_facts=True)
    revenue = _SlowService({s: bds[s] for s in syms}, delay=0.35)
    row = theme(slug="silicon-rush", title="Silicon Rush", tickers=list(syms), blocked_tickers=[])
    profiles = {s: p for s, p in _sr_profiles().items() if s in syms}
    world = dict(fmp=FakeFMP(profiles=profiles, market_caps=_sr_caps_rows(caps=caps)), revenue=revenue,
                 sb=FakeSB({"trending_themes": [row]}))
    started = time.monotonic()
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    assert time.monotonic() - started < 2.0
    by_cap = sorted(syms, key=lambda s: -caps[s])
    assert revenue.started[:6] == by_cap[:6]                       # the largest first
    with_fact = [m.company.symbol for m in rec.members if m.top_segment is not None]
    assert with_fact == by_cap[:6]                                  # 3 rounds of 2 answered in 1.2 s
    assert "not answered within the angle's" in caplog.text
    await _settle_leads()
    # nothing answered within the share: the angle's turn lost — with no other angle on offer, an outage
    A.clear_memo()
    revenue.delay = 2.0
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await trun(world, run_date=P4_RUN, theme_angles=("make_money",))
    assert err.value.reason == "budget_exhausted"
    await _settle_leads()


def _real_revenue_service(monkeypatch, bds: Dict[str, Any], *, slow: Tuple[str, ...] = (), delay: float = 0.0):
    """The REAL `RevenueBreakdownService.get_revenue_breakdown` — its in-memory tier, its `_inflight` future and its
    cancellation handler — with the Supabase tier answering a miss, the FMP build stubbed (`slow` symbols take `delay`
    s; an exception in `bds` raised) and nothing persisted. `svc.builds` lists every build started, `svc.leaders` the
    name of the task that ran each (the adapter's own leader is named "marketing_news:<key>")."""
    monkeypatch.setattr(_RBS, "_cache", {})
    monkeypatch.setattr(_RBS, "_inflight", {})
    svc = _years_service(None)
    svc.builds, svc.leaders = [], []

    async def build(ticker):
        svc.builds.append(ticker)
        svc.leaders.append(asyncio.current_task().get_name())
        if ticker in slow:
            await asyncio.sleep(delay)
        value = bds[ticker]
        if isinstance(value, BaseException):
            raise value
        return _RBS._RevenueBuild(response=value, next_earnings=None)

    async def not_a_fund(_ticker, _build):
        return False

    monkeypatch.setattr(svc, "_check_supabase_cache", lambda _t: None)
    monkeypatch.setattr(svc, "_build_revenue_breakdown_full", build)
    monkeypatch.setattr(svc, "_is_fund_build", not_a_fund)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *_a, **_k: None)
    return svc


async def _a_user_opens_the_card(svc, sym: str, after: float):
    """A user's revenue card (or a paid report's collector) asking the shared service for `sym` `after` s in — it
    JOINS the build already in flight."""
    await asyncio.sleep(after)
    return await svc.get_revenue_breakdown(sym)


@pytest.mark.asyncio
async def test_final_the_angles_deadline_cancels_only_its_own_wait_never_the_shared_breakdown_build(monkeypatch):
    """Final review 2026-11-20: "how they make money" awaited `get_revenue_breakdown` DIRECTLY, so when its share of
    the time ran out the cancelled read WAS the revenue service's in-flight leader — and the service fails its shared
    future with RuntimeError("in-flight fetch was cancelled"): a user who had opened that ticker's revenue card (or a
    20-credit report collecting it) during the build got an error card and a Sentry event. Each read now runs as the
    adapter's own leader (`_shared`): the angle gives up on its own wait only, the build finishes, the user gets the
    breakdown — one build, shared — and the member the angle could not wait for is still listed without a fact.
    Mutation-checked by hand (2026-11-20): with the read awaited directly again, the user's call raised RuntimeError."""
    bds = theme_breakdowns()
    svc = _real_revenue_service(monkeypatch, bds, slow=("QCOM",), delay=2.0)
    started = time.monotonic()
    ctx = A._Ctx("theme_explainer", THEME_RUN, frozenset(), 5, started + 1.2, A.NewsDeps(revenue=svc))
    refs = [SimpleNamespace(symbol=s) for s in ("NVDA", "AMD", "AVGO", "QCOM")]
    user = asyncio.ensure_future(_a_user_opens_the_card(svc, "QCOM", 0.3))
    facts = await A._theme_facts(ctx, refs)
    assert time.monotonic() - started < 1.8                               # the angle never waited for the build
    assert set(facts) == {"NVDA", "AMD", "AVGO"}                          # QCOM unread when the share ran out
    assert await user is bds["QCOM"]                                      # the joiner got the real breakdown
    assert svc.builds.count("QCOM") == 1                                  # ONE build, shared ...
    assert svc.leaders[svc.builds.index("QCOM")].startswith("marketing_news:")     # ... led by the adapter
    await _settle_leads()
    assert A._memo_get(("theme_breakdown", "QCOM")) is bds["QCOM"]        # and the next angle read finds it


# (owner decisions 2026-11-20: the insider spotlight's revenue line — `_spotlight_segments`, whose step deadline a test
# here pinned — is retired with the one-issuer post; the theme's own step-deadline test above still covers `_breakdown`.)


@pytest.mark.asyncio
async def test_final_a_degraded_breakdown_is_never_memoised_for_the_themes_half_hour():
    """The theme memo kept WHATEVER the service answered for THEME_BREAKDOWN_TTL_SECONDS (30 min) — a degraded build
    (a 429 the service's gather swallowed) included, which the service itself keeps only 60 s so the next read retries
    the feed. A second theme read in the half hour then listed that member without a fact: a cached failure read as an
    answer. Only a complete build is memoised now (`_breakdown_memoisable`); the next read asks again and gets the
    fact. Mutation-checked by hand (2026-11-20): with every build memoised again, the second read kept no fact."""
    good = theme_breakdowns()["AMD"]
    answers = [breakdown(symbol="AMD", degraded=["income_statement_unavailable"]), good]

    class Flaky(FakeService):
        async def get_revenue_breakdown(self, sym):
            self.calls.append(sym)
            return answers.pop(0)

    revenue = Flaky({})
    refs = [SimpleNamespace(symbol="AMD")]

    def ctx():
        return A._Ctx("theme_explainer", THEME_RUN, frozenset(), 5, time.monotonic() + 30, A.NewsDeps(revenue=revenue))

    assert await A._theme_facts(ctx(), refs) == {}                         # degraded: no fact
    assert A._memo_get(("theme_breakdown", "AMD")) is A._MISS            # ... and not memoised
    facts = await A._theme_facts(ctx(), refs)
    assert facts["AMD"][0] == "Data Center" and revenue.calls == ["AMD", "AMD"]
    assert A._memo_get(("theme_breakdown", "AMD")) is good                # a complete build IS memoised
    assert await A._theme_facts(ctx(), refs) == facts and revenue.calls == ["AMD", "AMD"]   # ... and reused
    assert not A._breakdown_memoisable(breakdown(reported_revenue=None))  # no reported revenue: never memoised
    await _settle_leads()


def test_p4_the_record_carries_the_pictures_tiles_facts_only():
    """A.THEME_FACT_TILES is the picture's tile count (the adapter may not import the template's module)."""
    from app.services.marketing import template_onscreen

    assert A.THEME_FACT_TILES == template_onscreen.MAX_TILES == _NT.THEME_PICTURE_TILES


def test_p4_every_member_fits_the_angles_share_at_the_worst_round():
    """Pinned: every member a theme record can hold is read (THEME_SEGMENT_LOOKUPS == R.THEME_MEMBERS[1]) in two waves
    of THEME_SEGMENT_CONCURRENCY, and the older-years reads of the gross stacks are a third round — inside the angle's
    share at the worst measured round (THEME_WORST_ROUND_SECONDS); the market-value batch rides the first round."""
    waves = -(-A.THEME_SEGMENT_LOOKUPS // A.THEME_SEGMENT_CONCURRENCY)
    assert A.THEME_SEGMENT_LOOKUPS == R.THEME_MEMBERS[1] and waves == 2
    assert A.THEME_ANGLE_BUDGET_SECONDS["make_money"] >= (waves + 1) * A.THEME_WORST_ROUND_SECONDS
    assert len(SILICON_RUSH) <= A.THEME_SEGMENT_LOOKUPS
    assert A.THEME_SEGMENT_YEARS_CONCURRENCY >= 1


#: The EIGHT production theme lists (read-only query of `trending_themes`, 2026-11-20 — public app data), each in its
#: own order.
PRODUCTION_THEMES = {
    "silicon-rush": SILICON_RUSH,
    "modern-battlefield": ("LMT", "RTX", "NOC", "GD", "LHX", "PLTR", "HII", "AVAV", "KTOS", "LDOS", "ESLT", "TXT", "CW",
                           "MRCY", "KRMN", "BAH"),
    "the-new-oil": ("MP", "USAR", "ALB", "SQM", "LAC", "PLL", "SGML", "FCX", "SCCO", "TECK", "HBM", "ERO", "CCJ", "UUUU",
                    "UEC", "NXE", "DNN", "CRML", "RIO"),
    "robot-workforce": ("ISRG", "NVDA", "TSLA", "ROK", "TER", "SYM", "CGNX", "ZBRA", "FANUY", "YASKY", "EMR", "NOVT",
                        "PH", "SERV", "RR", "ABBNY"),
    "hacking-health": ("LLY", "NVO", "VRTX", "REGN", "AMGN", "MRNA", "CRSP", "NTLA", "BEAM", "VKTX", "GPCR", "AZN",
                       "NVS", "MRK", "ABBV", "JNJ", "PFE", "ILMN"),
    "cyber-wars": ("CRWD", "PANW", "ZS", "FTNT", "NET", "S", "OKTA", "NTSK", "CHKP", "SAIL", "RBRK", "TENB", "QLYS", "RPD",
                   "VRNS"),
    "powering-machine": ("CEG", "VST", "TLN", "GEV", "NEE", "VRT", "ETN", "PWR", "CCJ", "OKLO", "SMR", "POWL", "BWXT",
                         "LEU", "D", "NNE"),
    "final-frontier": ("SPCX", "RKLB", "ASTS", "LUNR", "FLY", "VOYG", "RDW", "KRMN", "IRDM", "PL", "BKSY", "KTOS", "LMT",
                       "NOC", "LHX", "GSAT", "VSAT"),
}


@pytest.mark.parametrize("slug", sorted(PRODUCTION_THEMES))
@pytest.mark.asyncio
async def test_p4_every_production_list_is_read_whole_largest_first_and_draws_its_largest_members(slug):
    """On each of the eight PRODUCTION member lists (SAMPLE values: every member a clean two-line breakdown, market
    values falling with a fixed shuffle of the list, every third member without a fact): every member is read, in
    market-value order, inside two waves; every member stays in the record; at most THEME_FACT_TILES carry a fact —
    the largest members with one — and the picture's tiles are exactly those, largest first."""
    syms = PRODUCTION_THEMES[slug]
    assert len(syms) <= A.THEME_SEGMENT_LOOKUPS and -(-len(syms) // A.THEME_SEGMENT_CONCURRENCY) <= 2
    shuffled = sorted(syms, key=lambda s: (sum(map(ord, s)) * 7919) % 1009)
    caps = {s: 1e12 / (i + 1) for i, s in enumerate(shuffled)}
    bare = set(syms[2::3])                                                   # every third member: no fact
    bds = {s: (breakdown(degraded=["segmentation_unavailable"]) if s in bare
               else _seg_bd(s, "2025", [("Main Line", 7e9), ("Second Line", 3e9)])) for s in syms}
    world = dict(fmp=FakeFMP(profiles={s: prof(s, f"Company {s}", exchange="NASDAQ", cap=caps[s]) for s in syms},
                             market_caps=_sr_caps_rows(caps=caps)),
                 revenue=FakeService(bds), sb=FakeSB({"trending_themes": [
                     theme(slug=slug, title="Theme Title", tickers=list(syms), blocked_tickers=[])]}))
    (rec,) = (await trun(world, run_date=P4_RUN, theme_angles=("make_money",))).records
    assert world["revenue"].calls == sorted(syms, key=lambda s: -caps[s])          # every member, largest first
    assert sorted(m.company.symbol for m in rec.members) == sorted(syms)            # every member kept
    want = sorted((s for s in syms if s not in bare), key=lambda s: -caps[s])[:A.THEME_FACT_TILES]
    assert [m.company.symbol for m in rec.members if m.top_segment is not None] == want
    out, tiles = _sr_compose(rec)
    assert list(tiles)[:len(want)] == want and all(tiles[s] for s in want)


# ── owner decisions 2026-11-20 ("3 - 5 companies at once"): the real Form 4 minimum ──────────────────────────────────
# "for insider trade, i think we discussed that there is no single company already, each post or each image/video will
# have more than 2,3, ideally are 3 - 5 companies at once." / "i pick option 1: 3-5, but if it's a quite week - if we
# don't have enough data, then use different topic like posting the theme". These run with the REAL minimum
# (`real_min_rows`); mutation-checked by hand (2026-11-20): the adapter's `len(...) < R.INSIDER_MIN_ROWS` test turned
# into `< 1` (both collectors) turned every test here red.


def _low_ceo(**kw) -> Dict[str, Any]:
    """A third company for `week_rows()`: Lowe's CEO, $600K, filed in the week (role-only: no profile CEO)."""
    return irow(**{**dict(sym="LOW", name="ELLISON MARVIN", cik="0000000444", shares=20_000, price=30.0,
                          filed="2026-11-12", traded="2026-11-11"), **kw})


def _low_profile() -> Dict[str, Any]:
    return {"LOW": prof("LOW", "Lowe's Companies, Inc.", price=30.0, cap=150e9)}


@pytest.mark.parametrize("series, extra, few", [
    ("ceo_buys", {}, "ceo_too_few_companies"),
    ("insider_buys", {"title": "director"}, "insider_too_few_companies"),
])
@pytest.mark.asyncio
async def test_min3_a_week_of_fewer_than_three_companies_is_no_post(real_min_rows, series, extra, few):
    rows = [irow(**extra), irow(sym="FOX", name="SMITH JOHN JR", cik="0000000111", shares=40_000, price=30.0,
                                filed="2026-11-10", traded="2026-11-09", **extra)]
    got = await run(series, FakeFMP(insider=rows, profiles=week_profiles()))
    assert got.records == () and got.skip_reason == few                       # two companies
    assert few in R.SKIP_REASONS and few not in R.UNAVAILABLE_REASONS          # a content skip: the chain falls through
    A.clear_memo()
    got = await run(series, FakeFMP(insider=rows[:1], profiles=week_profiles()))
    assert got.records == () and got.skip_reason == few                       # one company
    A.clear_memo()
    noise = [irow(sym="ABCDW", name="DOE JANE", cik="0000000222", shares=100_000, price=10.0, **extra)]
    got = await run(series, FakeFMP(insider=noise, profiles=week_profiles()))
    assert got.records == () and got.skip_reason == f"{series.split('_')[0]}_none_qualified"   # none: never "too few"
    A.clear_memo()
    got = await run(series, FakeFMP(insider=rows + [_low_ceo(**extra)], profiles={**week_profiles(), **_low_profile()}))
    (rec,) = got.records                                                      # three: one post of all three
    assert [r.company.symbol for r in rec.rows] == ["GME", "FOX", "LOW"]


@pytest.mark.asyncio
async def test_min3_a_spotlight_window_of_fewer_than_three_companies_is_no_post(real_min_rows):
    """The insider LIST: Contoso's three people are ONE company — never a post; with FOX and GME it is a 3-company
    list, keyed by EVERY company once (in row order)."""
    got = await spot(FakeFMP(insider=spot_rows(), profiles=spot_profiles()))
    assert got.records == () and got.skip_reason == "insider_spotlight_too_few_companies"
    # review of 2026-11-26: a window whose list fell below three is NOT recorded as checked — its companies stay in reach
    # of the next spotlight (`test_review_1126_two_spotlights_add_a_short_window_up_to_a_list`)
    assert got.checked_window is None
    A.clear_memo()
    rows = spot_rows() + [
        irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
             filed="2026-11-17", traded="2026-11-16")]
    got = await spot(FakeFMP(insider=rows, profiles={**spot_profiles(), **week_profiles()}))
    assert got.records == () and got.skip_reason == "insider_spotlight_too_few_companies"   # two companies
    A.clear_memo()
    rows.append(irow(sym="GME", name="COHEN RYAN", cik="0001767470", shares=10_000, price=25.0, filed="2026-11-18",
                     traded="2026-11-17"))
    got = await spot(FakeFMP(insider=rows, profiles={**spot_profiles(), **week_profiles()}))
    (rec,) = got.records
    assert [c.company.symbol for c in rec.companies] == ["FOX", "CTSO", "GME"]
    assert R.ledger_key(rec) == "news:insider_spotlight:FOX+CTSO+GME:2026-11-16"


@pytest.mark.asyncio
async def test_review_1126_two_spotlights_add_a_short_window_up_to_a_list(real_min_rows):
    """Review of 2026-11-26 (correctness): Thursday's window (Nov 16-18) holds two companies (Contoso, FOX) — no post,
    and NO window recorded. Saturday's spotlight is then told the last checked day is still Sunday the 15th, so its
    window reaches back over Thursday's days (Nov 16-19) and GME, filed on the 19th, makes the three a list. Had
    Thursday's window been recorded (checked through the 18th), Saturday's window would hold GME alone — no post, and
    Contoso's and FOX's filings never seen again. Mutation-checked: recording the window again turns the first
    assertion red."""
    fox = irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
               filed="2026-11-17", traded="2026-11-16")
    gme = irow(sym="GME", name="COHEN RYAN", cik="0001767470", shares=10_000, price=25.0, filed="2026-11-19",
               traded="2026-11-18")
    profiles = {**spot_profiles(), **week_profiles()}
    thu = await spot(FakeFMP(insider=spot_rows() + [fox], profiles=profiles))
    assert thu.records == () and thu.skip_reason == "insider_spotlight_too_few_companies" and thu.checked_window is None
    A.clear_memo()
    sat_rows = spot_rows(marker="2026-11-20") + [fox, gme]
    sat = await spot(FakeFMP(insider=sat_rows, profiles=profiles), run_date=SPOT_SAT, checked=SPOT_CHECKED)
    (rec,) = sat.records
    assert [c.company.symbol for c in rec.companies] == ["FOX", "CTSO", "GME"]
    assert (rec.window_start, rec.window_end) == (date(2026, 11, 16), date(2026, 11, 19)) == sat.checked_window
    assert R.ledger_key(rec) == "news:insider_spotlight:FOX+CTSO+GME:2026-11-16"
    A.clear_memo()
    # the old behaviour: Thursday's window recorded — Saturday sees only GME's day
    lost = await spot(FakeFMP(insider=sat_rows, profiles=profiles), run_date=SPOT_SAT, checked=date(2026, 11, 18))
    assert lost.records == () and lost.skip_reason == "insider_spotlight_too_few_companies"


@pytest.mark.parametrize("ref, reason", [
    ("news:insider_spotlight:FOX+CTSO+GME:2026-11-16", "already_posted"),      # this window: each company posted once
    ("news:insider_spotlight:FOX+CTSO+GME:2026-11-12", "spotlight_cooldown"),  # an earlier window inside the cooldown
])
@pytest.mark.asyncio
async def test_min3_every_company_of_a_posted_list_is_excluded_once(real_min_rows, ref, reason):
    """A posted list's key names EVERY company it drew (owner decisions 2026-11-20): each is excluded — before any
    profile call — so no company of a posted list is posted again in its window or its cooldown."""
    rows = spot_rows() + [
        irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
             filed="2026-11-17", traded="2026-11-16"),
        irow(sym="GME", name="COHEN RYAN", cik="0001767470", shares=10_000, price=25.0, filed="2026-11-18",
             traded="2026-11-17")]
    fmp = FakeFMP(insider=rows, profiles={**spot_profiles(), **week_profiles()})
    got = await spot(fmp, exclude=[ref])
    assert got.records == () and got.rejections == {reason: 3}
    assert not any(sym in req for req in fmp.profile_requests for sym in ("FOX", "CTSO", "GME"))


# ── final double-check of 2026-11-26: the insider LIST passes the roundups' Congress gates ───────────────────────────
# The weekly roundups pin the Congress block-list and roster refusals
# (`test_an_unusable_congress_block_list_refuses_the_form_4_series`,
# `test_a_roster_not_fresh_for_the_run_date_refuses_the_form_4_series`,
# `test_a_roster_from_before_the_sitting_congress_refuses_the_form_4_series`). The spotlight's insider list runs the
# same gate (`_insider_series_open`, the first call of `_collect_insider_spotlight`) and the same whole-person Congress
# drop; these pin both with the REAL minimum of three companies. Mutation-checked by hand (2026-11-26): deleting the
# spotlight's `_insider_series_open(ctx)` call turned every refusal case here red while the roundups' tests stayed
# green; dropping the walk's Congress filter turned the member tests red. The adapter was restored byte for byte.

#: A member of Congress (on the shipped block-list) buying $6M of Home Depot as a director inside the list's window:
#: the window's LARGEST purchase, so a member the gate missed would LEAD the list.
_DC_MEMBER = dict(sym="HD", name="PELOSI NANCY", cik="0000000444", title="director", shares=20_000, price=300.0,
                  filed="2026-11-18", traded="2026-11-17")


def _dc_list_rows(*, member: bool = True, gme: bool = True, shift_days: int = 0) -> List[Dict[str, Any]]:
    """The three-company insider list of `test_the_issuers_rank_by_their_leads_amount_and_the_limit_holds` (FOX's
    director $3M, Contoso's three people, GME's CEO $250K) plus, unless ``member`` is False, `_DC_MEMBER` — every
    filing and trade date moved ``shift_days`` later (a multiple of 7 keeps every weekday)."""
    rows = spot_rows() + [
        irow(sym="FOX", name="SMITH JOHN", cik="0000000111", title="director", shares=100_000, price=30.0,
             filed="2026-11-17", traded="2026-11-16")]
    if gme:
        rows.append(irow(sym="GME", name="COHEN RYAN", cik="0001767470", shares=10_000, price=25.0,
                         filed="2026-11-18", traded="2026-11-17"))
    if member:
        rows.append(irow(**_DC_MEMBER))

    def moved(text: str) -> str:
        return (date.fromisoformat(text[:10]) + timedelta(days=shift_days)).isoformat() + text[10:]

    return [{**r, "filingDate": moved(r["filingDate"]), "transactionDate": moved(r["transactionDate"])} for r in rows]


def _dc_profiles() -> Dict[str, Any]:
    return {**spot_profiles(), **week_profiles(), "HD": prof("HD", "The Home Depot, Inc.", price=300.0, cap=300e9)}


def _assert_list_without_the_member(got, fmp) -> None:
    (rec,) = got.records
    assert isinstance(rec, R.InsiderSpotlight)
    assert [c.company.symbol for c in rec.companies] == ["FOX", "CTSO", "GME"]       # never HD, never led by it
    assert got.rejections == {"congress_name": 1}
    assert fmp.calls["insider"] >= 1 and all("HD" not in req for req in fmp.profile_requests)


@pytest.mark.asyncio
async def test_dc1126_an_unusable_congress_block_list_refuses_the_insider_list(monkeypatch, real_min_rows):
    """Role-only would still point at a member the block-list can no longer recognise — the insider list is refused
    (`internal_error`, the detail naming the block-list) before any FMP read, exactly as the roundups are."""
    fmp = FakeFMP(insider=_dc_list_rows(), profiles=_dc_profiles())
    _assert_list_without_the_member(await spot(fmp), fmp)           # anti-vacuity: a whole block-list posts the list
    A.clear_memo()
    monkeypatch.setattr(R, "person_names_allowed", lambda: False)
    fmp = FakeFMP(insider=_dc_list_rows(), profiles=_dc_profiles())
    with pytest.raises(A.MarketingNewsUnavailable) as err:
        await spot(fmp)
    assert err.value.series == "insider_spotlight" and err.value.reason == "internal_error"
    assert "block-list" in err.value.detail
    assert fmp.calls == Counter(), "refused before any FMP call"


# The age rows are measured from SPOT_RUN (Thu Nov 19, 2026), three days after the roundups' RUN: every row keeps the
# roundup test's meaning (41 / 199 / 400 / 401 days), with congress_start pinned to the 119th's, never `_KEEP`.
@pytest.mark.parametrize("fetched_on, congress_start, named, error", [
    ("2026-10-09", "2025-01-03", True, False),    # 41 days, holding the 119th (the shipped roster)
    ("2026-05-04", "2025-01-03", True, True),     # 199 days: still named, ERROR (refresh it)
    ("2025-10-15", "2025-01-03", True, True),     # 400 days: the last day it is fresh
    ("2025-10-14", "2025-01-03", False, True),    # 401 days: the list is refused
    ("2019-01-01", "2017-01-03", False, True),
    (None, _KEEP, False, True),                   # no fetched_on: its age is unknown
    ("not-a-date", _KEEP, False, True),
    ("2026-10-09", None, False, True),            # no congress_start — which Congress?
    ("2026-10-09", "2023-01-03", False, True),    # holds the 118th while the 119th sits
], ids=["fresh", "warn", "at_the_limit", "past_the_limit", "years_old", "missing", "unreadable",
        "no_congress_start", "earlier_congress"])
@pytest.mark.asyncio
async def test_dc1126_a_roster_not_fresh_for_the_run_date_refuses_the_insider_list(
        monkeypatch, tmp_path, caplog, real_min_rows, fetched_on, congress_start, named, error):
    """A roster that is undated, older than `ROSTER_MAX_AGE_DAYS` (from the RUN date) or not holding the sitting
    Congress cannot know every member: the insider list is REFUSED (`internal_error`, a detail naming the roster, an
    ERROR, no FMP call), never run role-only. A fresh roster posts the list, the member it knows dropped."""
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, fetched_on, congress_start))
    try:
        caplog.set_level(logging.ERROR)
        fmp = FakeFMP(insider=_dc_list_rows(), profiles=_dc_profiles())
        if named:
            _assert_list_without_the_member(await spot(fmp), fmp)
        else:
            with pytest.raises(A.MarketingNewsUnavailable) as err:
                await spot(fmp)
            assert err.value.series == "insider_spotlight" and err.value.reason == "internal_error"
            assert "congress roster" in err.value.detail and "sitting Congress" in err.value.detail
            assert fmp.calls == Counter(), "refused before any FMP call"
            assert any(r.levelno == logging.ERROR and r.name == A.__name__ and "congress roster" in r.getMessage()
                       and "insider_spotlight" in r.getMessage() for r in caplog.records)
        assert any(r.levelno == logging.ERROR and r.name == R.__name__ and "congress roster" in r.getMessage()
                   for r in caplog.records) is error
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


@pytest.mark.parametrize("fetched_on, congress_start, run_date, named", [
    # the shipped roster (fetched 2026-10-09, the 119th Congress)
    ("2026-10-09", "2025-01-03", date(2026, 12, 31), True),
    ("2026-10-09", "2025-01-03", date(2027, 1, 3), False),      # the 120th is sworn in: no grace
    ("2026-10-09", "2025-01-03", date(2027, 1, 7), False),      # the first Thursday spotlight of the 120th
    ("2026-10-09", "2025-01-03", date(2027, 1, 21), False),
    # a January 4 refresh still holding the 119th (the script refuses to write it)
    ("2027-01-04", "2025-01-03", date(2027, 1, 21), False),
    # a roster holding the 120th Congress, refreshed the day it was sworn in
    ("2027-01-03", "2027-01-03", date(2027, 1, 7), True),
    ("2027-01-03", "2027-01-03", date(2027, 1, 21), True),
], ids=["dec31", "sworn_in", "first_thursday", "later", "jan4_refresh", "120th_first_thursday", "120th_later"])
@pytest.mark.asyncio
async def test_dc1126_a_roster_from_before_the_sitting_congress_refuses_the_insider_list(
        monkeypatch, tmp_path, caplog, real_min_rows, fetched_on, congress_start, run_date, named):
    """From the odd-year January 3 a roster holding the previous Congress cannot know the new members: the insider list
    is refused from that day (no grace) until the roster holds the sitting Congress; a roster that does posts the list,
    its member dropped. Rows and the last checked day move with the run date (`_dc_list_rows`)."""
    shift = (run_date - SPOT_RUN).days
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", _roster_aged(tmp_path, fetched_on, congress_start))
    try:
        caplog.set_level(logging.ERROR, logger=R.__name__)
        fmp = FakeFMP(insider=_dc_list_rows(shift_days=shift), profiles=_dc_profiles())
        if named:
            assert shift % 7 == 0
            got = await spot(fmp, run_date=run_date, checked=SPOT_CHECKED + timedelta(days=shift))
            _assert_list_without_the_member(got, fmp)
        else:
            with pytest.raises(A.MarketingNewsUnavailable) as err:
                await spot(fmp, run_date=run_date, checked=SPOT_CHECKED + timedelta(days=shift))
            assert err.value.series == "insider_spotlight" and err.value.reason == "internal_error"
            assert "congress roster" in err.value.detail
            assert fmp.calls == Counter(), "refused before any FMP call"
        assert any(r.levelno == logging.ERROR and "congress roster" in r.getMessage()
                   for r in caplog.records) is (not named)
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


@pytest.mark.asyncio
async def test_dc1126_a_member_of_congress_never_counts_toward_the_insider_list(caplog, real_min_rows):
    """The whole-person Congress drop runs on the list path too: a company whose only buyer is a member is not a
    company of the list — FOX and Contoso with Home Depot's member are TWO companies, no post (never a list of three
    with the member's company in it, role-only or named) — and the member's company is never profiled or logged by
    name."""
    caplog.set_level(logging.DEBUG)
    assert R.is_congress_name("PELOSI NANCY")      # anti-vacuity: the roster is loaded
    fmp = FakeFMP(insider=_dc_list_rows(gme=False), profiles=_dc_profiles())
    got = await spot(fmp)
    assert got.records == () and got.skip_reason == "insider_spotlight_too_few_companies"
    assert got.rejections == {"congress_name": 1}
    assert all("HD" not in req for req in fmp.profile_requests)
    text = caplog.text.lower()
    assert "pelosi" not in text and "nancy" not in text
    A.clear_memo()
    # anti-vacuity: the same Home Depot purchase by a director who is NOT a member makes the three a list, led by it
    not_a_member = irow(**{**_DC_MEMBER, "name": "DOE JANE", "cik": "0000000445"})
    fmp = FakeFMP(insider=_dc_list_rows(member=False, gme=False) + [not_a_member], profiles=_dc_profiles())
    got = await spot(fmp)
    (rec,) = got.records
    assert [c.company.symbol for c in rec.companies] == ["HD", "FOX", "CTSO"] and got.rejections == {}
