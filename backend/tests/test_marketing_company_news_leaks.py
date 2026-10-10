"""What can LEAVE the company-news adapter (contract D15): nothing but the record fields.

The records are the only path from FMP to a public post (the templates read them, the fact sheet
stores them). So:

1. **Exact fields.** `EXPECTED_FIELDS` pins every record type, nested ones included — a new
   field fails here until someone decides, in this file, that it may travel.
2. **Field-name tokens.** No field name carries a price / chart / image / URL / member-identity /
   profile-CEO token (explicit exemptions: ``ownership_pct``, ``top_segment_share``). The one
   person field carries the body-only placement marker.
3. **Canaries.** Every shipped series runs through the REAL adapter against fakes whose every
   input key that is NOT allow-listed holds a unique canary string or number — the profile's
   price, market cap, CEO, image, description; the Form 4 URL, CIKs and free-text title; the 13F
   link, issuer name, class title and CUSIP; the whale row's person name and avatar; the club
   card's market cap and weights; the breakdown's tax line; the facts' description and CEO; the
   theme's image and subtitle. None may appear in ``json.dumps(fact_sheet(record))``, and no
   number in a fact sheet may equal a planted price or market-cap value.
4. **No vendor credit** anywhere in a fact sheet, and no 64-hex string (a hashed identity).
5. **Logs.** A member of Congress dropped from the insider feed is never named in a log line, and
   no canary URL is logged.
6. **Drop 2b (2026-10-10).** Each 2b series runs through the real adapter with canaries in every
   input key that is not allow-listed. Congress Count: every member-identity value of the recorded
   row shape (senateID included) is absent from the records, the fact sheets, the scrubbed
   rows, the memo and every log line, and no salted hash leaves the count.

Hermetic: the fakes come from `test_marketing_company_news_adapter` (sibling import, as
`test_cards_round4_2026_10_08.py` does).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import fields
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

import pytest

import test_marketing_company_news_adapter as T
from app.services.marketing import company_news_adapter as A
from app.services.marketing import company_news_rules as R
from app.services.marketing import selection

# ── 1. exact fields ───────────────────────────────────────────────────────────

EXPECTED_FIELDS = {
    R.CompanyRef: ("symbol", "name"),
    R.InsiderPurchase: ("company", "role", "person_name", "amount_usd", "shares", "purchases",
                        "earliest_trade_date", "latest_trade_date", "filing_dates", "holding", "amended"),
    R.InsiderBuysWeek: ("series", "window_start", "window_end", "rows"),
    # `prev_value_usd` (review round 8, the shared adapter/templates contract): an exit's value on
    # the previous quarter's 13F book — a filed figure, never drawn, used to rank the exit.
    R.ThirteenFMove: ("company", "move", "shares", "prev_shares", "value_usd", "listed_on", "prev_value_usd"),
    R.ThirteenFFiling: ("series", "filer_name", "filer_cik", "filer_symbol", "period", "period_end",
                        "filed_on", "amended_on", "total_value_usd", "position_count", "moves", "counts"),
    R.CongressCount: ("series", "company", "month", "members", "fetched_on"),
    R.CompanyStake: ("series", "stake_id", "investor", "investee_name", "investee", "kind", "value_usd",
                     "value_basis", "ownership_pct", "as_of", "verified_on", "source_title", "background",
                     "listed_since", "local_listing", "is_new"),
    R.EarningsReport: ("series", "company", "report_date", "period_end", "eps_actual", "eps_estimate",
                       "revenue_actual", "revenue_estimate"),
    R.Segment: ("name", "value_usd"),
    R.MoneyMap: ("series", "company", "fiscal_year", "period_end", "segments", "other_usd",
                 "eliminations_usd", "revenue_usd", "gross_profit_usd", "operating_profit_usd",
                 "net_income_usd"),
    R.ThemeMember: ("company", "top_segment", "top_segment_share", "fiscal_year"),
    # `theme_size` (the shared adapter/templates contract, 2026-10-10): the theme's ticker count
    # as the app card shows it, before any gate — a count, no identity, no price.
    R.ThemeExplainer: ("series", "slug", "title", "members", "tickers_as_of", "theme_size"),
}

BANNED_TOKENS = frozenset({
    "price", "prices", "close", "open", "high", "low", "chart", "history", "volume", "beta", "cap",
    "dcf", "avatar", "photo", "image", "url", "link", "first", "last", "firstname", "lastname",
    "office", "senator", "representative", "district", "owner", "party", "state", "chamber",
    "change", "changes", "percent", "pct", "ceo", "description",
})
TOKEN_EXEMPT_FIELDS = frozenset({"ownership_pct", "top_segment_share"})


def test_every_record_type_is_pinned():
    assert set(EXPECTED_FIELDS) == set(R.ALL_RECORD_TYPES)
    assert set(R.RECORD_TYPES) <= set(EXPECTED_FIELDS)


@pytest.mark.parametrize("cls", list(EXPECTED_FIELDS), ids=lambda c: c.__name__)
def test_exact_fields(cls):
    assert tuple(f.name for f in fields(cls)) == EXPECTED_FIELDS[cls]


@pytest.mark.parametrize("cls", list(EXPECTED_FIELDS), ids=lambda c: c.__name__)
def test_no_field_name_carries_a_banned_token(cls):
    for f in fields(cls):
        if f.name in TOKEN_EXEMPT_FIELDS:
            continue
        assert not set(f.name.split("_")) & BANNED_TOKENS, f"{cls.__name__}.{f.name}"


def test_the_only_person_field_is_marked_body_only():
    person = [(c.__name__, f.name) for c in EXPECTED_FIELDS for f in fields(c) if "person" in f.name]
    assert person == [("InsiderPurchase", "person_name")]
    (f,) = [f for f in fields(R.InsiderPurchase) if f.name == "person_name"]
    assert f.metadata.get("placement") == "body_only"


def test_the_token_ban_is_not_vacuous():
    """Mutation stand-in: a record shaped like the ones the plan forbids is caught."""
    for bad in ("last_price", "market_cap", "image_url", "ceo_name", "member_district", "first_name"):
        assert set(bad.split("_")) & BANNED_TOKENS, bad


# ── 3. canaries ───────────────────────────────────────────────────────────────

CANARY_STRINGS = (
    "ZQXSECNAME", "ZQXOTHER", "ZQXURL", "ZQXCOMPANYCIK", "ZQXCEOSUFFIX", "ZQXIMG", "ZQXDESCRIPTION",
    "ZQXSITE", "ZQXPHONE", "ZQXADDRESS", "ZQXCITY", "ZQXINDUSTRY", "ZQXSECTOR", "ZQXRANGE", "ZQXISIN",
    "ZQXLINK", "ZQXFINALLINK", "ZQXISSUER", "ZQXCLASS", "ZQXWHALEPERSON", "ZQXAVATAR", "ZQXWHALEDESC",
    "ZQXCHANGENAME", "ZQXNOTICE", "ZQXWHALEID", "ZQXHOLDING", "ZQXFACTSDESC", "ZQXFACTSCEO",
    "ZQXTHEMEIMG", "ZQXSUBTITLE", "ZQXINCOMELINK", "ZQXREPORTER", "ZQXCOUNTRY", "ZQXSTATE",
    "0004242424", "0004242425", "0004242426",                        # the profiles' (issuer) CIKs
    "0009191919", "037833100", "21873S108", "674599105",            # reporting CIK, CUSIPs
)
CANARY_NUMBERS = (
    25.123456, 8080808080.0, 7777777.0, 1.2345, 3.21, 99.99, 9191919191919.0, 0.123456789,
    15151515151.0, 4242424242.0, 9.8765, 123456789.0, 31313.13,
)


def _canary_profile(sym: str, name: str, **kw) -> Dict[str, Any]:
    # `image` stays the vendor's exact logo URL (fetch_logo needs it): the vendor-name check
    # below is what proves it never travels.
    p = T.prof(sym, name, **kw)
    p.update({
        "description": "ZQXDESCRIPTION about the company", "website": "https://zqx.example/ZQXSITE",
        "phone": "ZQXPHONE", "address": "ZQXADDRESS", "city": "ZQXCITY", "state": "ZQXSTATE",
        "country": "ZQXCOUNTRY", "industry": "ZQXINDUSTRY", "range": "ZQXRANGE", "isin": "ZQXISIN",
        "beta": 1.2345, "changes": -3.21, "changePercentage": 99.99, "volAvg": 31313.13,
        "lastDividend": 9.8765, "cik": "0004242424",
    })
    return p


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _all_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_strings(v)


def _all_numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_numbers(v)


def _assert_clean(sheet: Dict[str, Any]) -> None:
    text = json.dumps(sheet, allow_nan=False)
    low = text.lower()
    for canary in CANARY_STRINGS:
        assert canary.lower() not in low, canary
    for n in _all_numbers(sheet):
        for c in CANARY_NUMBERS:
            assert not (abs(abs(n) - c) <= 1e-9 * max(1.0, c)), (n, c)
    assert not re.search(r"fmp|financial\s*modeling\s*prep|financialmodelingprep", low), text
    assert not re.search(r"\b[0-9a-f]{64}\b", low)
    assert "http" not in low and "www." not in low


def _sheets(got) -> List[Dict[str, Any]]:
    assert got.records, (got.skip_reason, dict(got.rejections))
    return [R.fact_sheet(rec, rejections=got.rejections,
                         selection={"plan": "monday", "chain": [got.series], "trail": []})
            for rec in got.records]


async def _run(series, fmp, **deps):
    return await A.candidates(series, run_date=T.RUN, exclude=frozenset(), limit=5,
                              deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp, **deps))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    A.clear_memo()
    A._registry_order.cache_clear()
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    yield
    A.clear_memo()


#: Each company's issuer CIK — a canary: it keys the line check and the per-issuer Form 4/A read
#: (review round 9, ``companyCik``), and must never travel. A row's ``companyCik`` is its
#: profile's (FMP's issuer index files every row under it), except the dropped member's.
_INSIDER_CIKS = {"GME": "0004242424", "FOX": "0004242425", "HD": "0004242426"}


def _insider_rows() -> List[Dict[str, Any]]:
    canary = dict(securityName="Common Stock ZQXSECNAME", url="https://www.sec.gov/ZQXURL/x.htm",
                  securitiesOwned=7_777_777)
    return [
        # GME's CEO: renders and corroborates → "Ryan Cohen" is ALLOWED to travel.
        T.irow(title="director, officer: Chief Executive Officer, other: ZQXOTHER", reportingCik="0009191919",
               companyCik=_INSIDER_CIKS["GME"], **canary),
        # FOX's CEO: an unrenderable reporting name — it must never travel, nor its CIK.
        T.irow(sym="FOX", name="ZQXREPORTER ZQXREPORTER", cik="0000000111", shares=40_000, price=30.0,
               companyCik=_INSIDER_CIKS["FOX"], **canary),
        # A member of Congress: dropped, never logged (an unreadable issuer CIK, a canary too).
        T.irow(sym="HD", name="PELOSI NANCY", cik="0000000444", shares=20_000, price=300.0,
               companyCik="ZQXCOMPANYCIK", **canary),
        # FOX's director and CFO for insider_buys.
        T.irow(sym="FOX", name="ZQXREPORTER ZQXOTHER", cik="0000000555", title="director, other: ZQXOTHER",
               shares=20_000, price=30.0, companyCik=_INSIDER_CIKS["FOX"], **canary),
        T.irow(sym="FOX", name="TAYLOR EMMA", cik="0000000556", title="officer: Chief Financial Officer",
               shares=10_000, price=30.0, companyCik=_INSIDER_CIKS["FOX"], **canary),
    ]


def _insider_profiles() -> Dict[str, Any]:
    profiles = {
        "GME": _canary_profile("GME", "GameStop Corp.", price=25.123456, cap=8080808080.0,
                               ceo="Mr. Ryan Cohen ZQXCEOSUFFIX"),
        "FOX": _canary_profile("FOX", "Fox Corporation", price=29.5, cap=8080808080.0, exchange="NASDAQ",
                               ceo="ZQXCEOSUFFIX"),
        "HD": _canary_profile("HD", "The Home Depot, Inc.", price=300.0, cap=300e9),
    }
    for sym, cik in _INSIDER_CIKS.items():
        profiles[sym]["cik"] = cik
    return profiles


@pytest.mark.parametrize("series", ["ceo_buys", "insider_buys"])
@pytest.mark.asyncio
async def test_insider_series_leak_nothing_but_their_fields(series, caplog):
    caplog.set_level(logging.DEBUG)
    fmp = T.FakeFMP(insider=_insider_rows(), profiles=_insider_profiles())
    got = await _run(series, fmp)
    for sheet in _sheets(got):
        _assert_clean(sheet)
    rows = got.records[0].rows
    if series == "ceo_buys":
        assert [r.person_name for r in rows] == ["Ryan Cohen", None]     # anti-vacuity
        assert got.rejections["congress_name"] == 1
    else:
        assert {r.role for r in rows} == {"director"}
    assert fmp.issuer_calls, "anti-vacuity: the per-issuer read ran on the canary rows"
    log = caplog.text.lower()
    assert "pelosi" not in log and "nancy" not in log and "zqxreporter" not in log
    assert "zqxurl" not in log and "https://" not in log


def _canary_book(cik: str) -> Dict[Any, Any]:
    out = {}
    for key, rows in T.book(cik).items():
        out[key] = [dict(r, nameOfIssuer="ZQXISSUER " + r["symbol"], titleOfClass="ZQXCLASS",
                         finalLink="https://www.sec.gov/ZQXFINALLINK/x.htm",
                         link=r["link"].replace("index.htm", "ZQXLINK-index.htm"))
                    for r in rows]
    return out


def _thirteen_f_profiles() -> Dict[str, Any]:
    base = T.thirteen_f_profiles()
    return {s: _canary_profile(s, p["companyName"], **{k: v for k, v in p.items()
                                                         if k in ("exchange", "ipoDate", "isEtf")})
            for s, p in base.items()}


@pytest.mark.asyncio
async def test_a_registry_13f_leaks_nothing_but_its_fields():
    whales = [T.whale(T.BERKSHIRE, "Berkshire Hathaway", "BRK-A", name="Warren ZQXWHALEPERSON",
                      avatar_url="https://zqx.example/ZQXAVATAR.png", description="ZQXWHALEDESC",
                      portfolio_value=9191919191919.0)]
    fmp = T.FakeFMP(profiles=_thirteen_f_profiles(), extracts=_canary_book(T.BERKSHIRE),
                    dates={T.BERKSHIRE: T.DATES_Q3_Q2})
    got = await _run("thirteen_f", fmp, sb=T.FakeSB({"whales": whales}))
    for sheet in _sheets(got):
        _assert_clean(sheet)
    assert [m.company.symbol for m in got.records[0].moves] == ["CRWV", "OXY", "AAPL"]   # anti-vacuity


@pytest.mark.asyncio
async def test_a_club_13f_leaks_nothing_but_its_fields(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb = T.club_world(company_over={"market_cap": 9191919191919.0, "notice": "ZQXNOTICE",
                                          "whale_id": "ZQXWHALEID"})
    detail = club.details["nvidia"]
    detail.holdings = [SimpleNamespace(name="ZQXHOLDING", weight=0.123456789)]
    for ch in detail.changes:
        ch.name = "ZQXCHANGENAME"
        ch.weight = 0.123456789
    got = await _run("thirteen_f", T.FakeFMP(profiles=_thirteen_f_profiles()), club=club, sb=sb)
    for sheet in _sheets(got):
        _assert_clean(sheet)
    assert len(got.records[0].moves) == 3


@pytest.mark.asyncio
async def test_a_money_map_leaks_nothing_but_its_fields(monkeypatch):
    monkeypatch.setattr(R, "MONEY_MAP_SEED", ("AAPL",))
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    world = T.money_world(
        bd=T.breakdown(tax=15151515151.0, other_expense=4242424242.0, symbol="AAPL"),
        facts=T.facts_fn(default={"available": True, "sector": "Technology ZQXSECTOR",
                                  "description": "ZQXFACTSDESC", "ceo": "ZQXFACTSCEO",
                                  "employees": 123456789, "website": "https://zqx.example/ZQXSITE"}),
        income=[dict(T.INCOME_AAPL[0], eps=9.8765, link="https://zqx.example/ZQXINCOMELINK",
                     finalLink="https://zqx.example/ZQXINCOMELINK")],
        profiles={"AAPL": _canary_profile("AAPL", "Apple Inc.", exchange="NASDAQ", price=25.123456,
                                          cap=8080808080.0)},
    )
    world["club"] = T.FakeClub(group=SimpleNamespace(
        companies=[SimpleNamespace(slug="apple", detail_symbol="AAPL", market_cap=9191919191919.0)],
        also_in_club=[]))
    world["sb"] = T.FakeSB({
        "trillion_club_companies": [],
        "trending_themes": [{"slug": "q", "tickers": ["AAPL"], "blocked_tickers": [], "sort_order": 1,
                             "is_active": True, "image_url": "https://zqx.example/ZQXTHEMEIMG.png",
                             "subtitle": "ZQXSUBTITLE"}],
    })
    got = await _run("money_map", world.pop("fmp"), **world)
    for sheet in _sheets(got):
        _assert_clean(sheet)


# ── Drop 2b (2026-10-10) ──────────────────────────────────────────────────────

#: Every member-identity value a Congress row carries (the recorded key set, plus the party /
#: state / senator / representative keys the design names): none may reach a record, a fact
#: sheet, a log line or the memo.
CONGRESS_CANARIES = ("ZQXFIRST", "ZQXLAST", "ZQXSENATEID", "ZQXOFFICE", "ZQXDISTRICT", "ZQXOWNER",
                     "ZQXMEMBERLINK", "ZQXCOMMENT", "ZQXPARTY", "ZQXSTATEX", "ZQXSENATOR", "ZQXREP",
                     "ZQXAMOUNT", "ZQXASSETDESC", "ZQXGAINS")


def _congress_row(chamber, sym, day, n, **kw):
    return T.crow(chamber, sym, day, f"m{n}", firstName=f"ZQXFIRST{n}", lastName=f"ZQXLAST{n}",
                  senateID=f"ZQXSENATEID{n}", office=f"ZQXOFFICE{n}", district="ZQXDISTRICT", owner="ZQXOWNER",
                  link=f"https://efdsearch.senate.gov/ZQXMEMBERLINK{n}", comment="ZQXCOMMENT", party="ZQXPARTY",
                  state="ZQXSTATEX", senator=f"ZQXSENATOR{n}", representative=f"ZQXREP{n}",
                  amount="ZQXAMOUNT $1,001 - $15,000", assetDescription=f"{sym} Common Stock ZQXASSETDESC",
                  capitalGainsOver200USD="ZQXGAINS", **kw)


def _assert_no_member(text: str, *, internal: bool = False) -> None:
    """No member canary in ``text``. ``internal`` (the adapter's own scrubbed rows and its memo):
    the ASSET description may be there (it finds a purchase of the same company under another
    symbol; it is no identity and never reaches a record), and the scrubbed rows hold the
    call's salted hashes — which the memo, a record and a log never do."""
    low = text.lower()
    for canary in CONGRESS_CANARIES:
        if internal and canary == "ZQXASSETDESC":
            continue
        assert canary.lower() not in low, canary


@pytest.mark.asyncio
async def test_congress_count_leaks_no_member_anywhere(caplog):
    caplog.set_level(logging.DEBUG)
    senate = T.cfeed("senate", [_congress_row("senate", "AAPL", "2026-11-20", 1),
                                _congress_row("senate", "AAPL", "2026-11-21", 1),
                                _congress_row("senate", "MSFT", "2026-11-05", 2)])
    house = T.cfeed("house", [_congress_row("house", "AAPL", "2026-11-15", 3),
                              _congress_row("house", "MSFT", "2026-11-03", 4),
                              _congress_row("house", "MSFT", "2026-11-04", 5, type="Exchange")])
    profiles = {"AAPL": _canary_profile("AAPL", "Apple Inc.", exchange="NASDAQ", price=25.123456, cap=8080808080.0),
                "MSFT": _canary_profile("MSFT", "Microsoft Corporation", exchange="NASDAQ", cap=8080808080.0)}
    fmp = T.FakeFMP2b(senate=senate, house=house, profiles=profiles)
    got = await A.candidates("congress_count", run_date=T.CONGRESS_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp))
    assert [(r.company.symbol, r.members) for r in got.records] == [("AAPL", 2), ("MSFT", 2)]   # anti-vacuity
    for sheet in _sheets(got):
        _assert_clean(sheet)                       # also: no 64-hex string (a hashed identity)
        _assert_no_member(json.dumps(sheet))
    # Walk the records: no field of a CongressCount (nested included) names an identity key.
    for rec in got.records:
        for f in fields(rec):
            assert f.name not in A.CONGRESS_IDENTITY_FIELDS
            assert not set(f.name.lower().split("_")) & {"first", "last", "office", "district", "owner",
                                                         "party", "state", "senator", "representative", "senateid"}
    # The scrubbed rows carry no identity key and no identity value.
    rows = A._scrub_congress(senate, "senate", "salt")
    assert {f.name for f in fields(A._CongressRow)}.isdisjoint(A.CONGRESS_IDENTITY_FIELDS)
    _assert_no_member(repr(rows), internal=True)
    # The memo (identity-free counts — no member, no salted hash) and every log line.
    memo = json.dumps([v for _exp, v in A._MEMO.values()], default=repr)
    _assert_no_member(memo, internal=True)
    assert not re.search(r"[0-9a-f]{64}", memo.lower())
    assert "zqxassetdesc" in memo.lower()                                     # anti-vacuity: the memo was read
    _assert_no_member(caplog.text)
    assert not re.search(r"[0-9a-f]{64}", caplog.text.lower())


def _stake_rows() -> List[Dict[str, Any]]:
    canary = dict(source_url="https://www.sec.gov/ZQXURL/10q.htm", investee_cusip="ZQXCUSIP1",
                  updated_at="ZQXUPDATED", sort_order=31313)
    return [
        T.stake("nvidia", "Intel", investee_us_symbol="INTC", disclosed_value_usd=5e9, as_of="2025-12-26", **canary),
        T.stake("nvidia", "Nscale", disclosed_value_usd=777399382.46, as_of="2026-03-27", ownership_pct=1.2345,
                ownership_basis="ZQXOWNERSHIPBASIS", **canary),
    ]


@pytest.mark.asyncio
async def test_company_stakes_leak_nothing_but_their_fields(monkeypatch):
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, _fmp = T.stakes_world(_stake_rows())
    for card in club.group.companies:
        card.market_cap, card.logo_symbol, card.notice = 9191919191919.0, "ZQXLOGOSYM", "ZQXNOTICE"
    profiles = {"NVDA": _canary_profile("NVDA", "NVIDIA Corporation", exchange="NASDAQ", price=25.123456,
                                        cap=8080808080.0),
                "INTC": _canary_profile("INTC", "Intel Corporation", exchange="NASDAQ", cap=8080808080.0)}
    got = await A.candidates("company_stakes", run_date=T.STAKES_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0,
                             deps=A.NewsDeps(fmp=T.FakeFMP(profiles=profiles), club=club, sb=sb))
    assert [r.investee_name for r in got.records] == ["Nscale", "Intel"]                 # anti-vacuity
    assert got.records[0].ownership_pct is None                                         # a qualified pct
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcusip", "zqxupdated", "zqxownershipbasis", "zqxlogosym", "zqxnotice"):
            assert canary not in low


@pytest.mark.asyncio
async def test_earnings_leak_nothing_but_their_fields():
    canary = dict(lastUpdated="ZQXUPDATED", fiscalDateEnding="ZQXFISCAL", time="ZQXTIME", epsEstimatedLow=99.99,
                  revenueEstimatedHigh=15151515151.0, numberOfAnalysts=31313.13, url="https://zqx.example/ZQXURL")
    cal = {"2027-01-12": [T.erow("AAA", "2027-01-12", 1.5, 1.0, 1.1e9, 1.0e9, **canary)],
           "2027-01-11": [T.erow("BBB", "2027-01-11", -0.4, -0.5, **canary)]}
    profiles = {s: _canary_profile(s, f"{s.title()} Industries Inc.", price=25.123456, cap=8080808080.0)
                for s in ("AAA", "BBB")}
    # The reporting-currency read (review round 9): only its "USD" decides; nothing of it travels.
    statement = {"date": "2026-12-31", "reportedCurrency": "USD", "revenue": 15151515151.0, "eps": 9.8765,
                 "cik": "0004242424", "link": "https://zqx.example/ZQXINCOMELINK",
                 "finalLink": "https://zqx.example/ZQXINCOMELINK"}
    fmp = T.FakeFMP2b(calendar=cal, profiles=profiles, currency={s: [dict(statement, symbol=s)] for s in profiles})
    got = await A.candidates("earnings", run_date=T.EARN_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp))
    assert [r.company.symbol for r in got.records] == ["AAA", "BBB"]                     # anti-vacuity
    assert sorted(sym for sym, _limit in fmp.currency_calls) == ["AAA", "BBB"]
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        assert "zqxupdated" not in low and "zqxfiscal" not in low and "zqxtime" not in low


@pytest.mark.asyncio
async def test_a_theme_explainer_leaks_nothing_but_its_fields():
    world = T.theme_world([T.theme(image_url="https://zqx.example/ZQXTHEMEIMG.png", subtitle="ZQXSUBTITLE",
                                   category="ZQXCATEGORY", accent_hex="ZQXACCENT", pinned_tickers=["ZQXPIN"])])
    for sym, bd in world["revenue"].by_symbol.items():
        if not isinstance(bd, BaseException):
            bd.tax, bd.other_expense = 15151515151.0, 4242424242.0
    world["fmp"] = T.FakeFMP(profiles={s: _canary_profile(s, p["companyName"], exchange="NASDAQ", price=25.123456,
                                                          cap=8080808080.0, isEtf=p["isEtf"])
                                       for s, p in T.theme_profiles().items()})
    got = await T.trun(world)
    assert len(got.records[0].members) == 6                                              # anti-vacuity
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcategory", "zqxaccent", "zqxpin"):
            assert canary not in low


def test_every_shipped_series_has_a_canary_test_here():
    """A series added to SHIPPED_SERIES must get its canary run in this file — every series whose
    collector exists has one (the 2a four above, the 2b four just above)."""
    covered = {"ceo_buys", "insider_buys", "thirteen_f", "money_map",
               "congress_count", "company_stakes", "earnings", "theme_explainer"}
    assert set(selection.SHIPPED_SERIES) <= covered
    assert set(A.COLLECTORS) == covered


def test_the_canary_check_is_not_vacuous():
    """Mutation stand-in: a sheet that DID carry a planted value is refused by `_assert_clean`."""
    rec = R.CompanyRef(symbol="GME", name="GameStop")
    for leak in ({"x": "Mr. Ryan Cohen ZQXCEOSUFFIX"}, {"x": 25.123456}, {"x": "via FMP"},
                 {"x": "https://images.financialmodelingprep.com/symbol/GME.png"}, {"x": "a" * 64}):
        with pytest.raises(AssertionError):
            _assert_clean({"record": {"company": {"symbol": rec.symbol}}, **leak})
