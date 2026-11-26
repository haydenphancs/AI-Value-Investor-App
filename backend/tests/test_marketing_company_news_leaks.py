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
7. **The stake finder (migration 205, 2026-10-28).** ``origin`` and ``owner_kept_at`` decide the
   stakes read and never travel: a kept auto row's Keep stamp is a canary, and every text of a row
   the read leaves out (an un-kept auto row, an unknown origin) is absent from the fact sheets and
   the logs — through the database filter and through the strict re-check alone.

Hermetic: the fakes come from `test_marketing_company_news_adapter` (sibling import, as
`test_cards_round4_2026_10_08.py` does).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import fields, replace
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
    # `cost_per_share_min_usd` / `cost_per_share_max_usd` (owner decision 2026-11-15, "Captions + picture"): the
    # lowest and highest per-share cost the person's Form 4 LINES report, as filed — the filing's own figure, never
    # the profile's price. The only per-share dollar figures a filing record carries: `FILED_FIGURE_FIELDS` below.
    R.InsiderPurchase: ("company", "role", "person_name", "amount_usd", "shares", "purchases",
                        "earliest_trade_date", "latest_trade_date", "filing_dates", "holding", "amended",
                        "cost_per_share_min_usd", "cost_per_share_max_usd"),
    R.InsiderBuysWeek: ("series", "window_start", "window_end", "rows"),
    # The spotlight lane: owner decisions 2026-11-20 ("3 - 5 companies at once") — a LIST of 3..8 issuers, each one
    # issuer's lead insider and up to 4 more of the same issuer (the one-issuer record and its revenue line, of
    # 2026-11-15, are retired); ONE 13F filer's holdings — up to five since the owner's feedback of 2026-11-19 (the filer fields of
    # `ThirteenFFiling`, no total; two integers: how many holdings passed the gates, and the filing's own count of
    # every change, which decides whether the list may state its counts as the filing's).
    R.InsiderSpotlight: ("series", "window_start", "window_end", "companies"),
    R.InsiderSpotlightCompany: ("lead", "others"),
    R.ThirteenFSpotlight: ("series", "filer_name", "filer_cik", "filer_symbol", "period", "period_end", "filed_on",
                           "amended_on", "moves", "eligible_moves", "filing_moves"),
    # `prev_value_usd` (review round 8, the shared adapter/templates contract): an exit's value on
    # the previous quarter's 13F book — a filed figure, never drawn, used to rank the exit.
    R.ThirteenFMove: ("company", "move", "shares", "prev_shares", "value_usd", "listed_on", "prev_value_usd"),
    R.ThirteenFFiling: ("series", "filer_name", "filer_cik", "filer_symbol", "period", "period_end",
                        "filed_on", "amended_on", "total_value_usd", "position_count", "moves", "counts"),
    R.CongressCount: ("series", "company", "month", "members", "fetched_on"),
    # news-v23 (owner 2026-11-19): ONE post of 2-3 companies — the month, its read and the counts (no identity field)
    R.CongressMonth: ("series", "month", "fetched_on", "counts"),
    R.CompanyStake: ("series", "stake_id", "investor", "investee_name", "investee", "kind", "value_usd",
                     "value_basis", "ownership_pct", "as_of", "verified_on", "source_title", "background",
                     "listed_since", "local_listing", "is_new"),
    # news-v23 (owner 2026-11-19, "public only"): ONE investor's stakes in public companies — the investor and its rows
    R.CompanyStakes: ("series", "investor", "stakes"),
    R.EarningsReport: ("series", "company", "report_date", "period_end", "eps_actual", "eps_estimate",
                       "revenue_actual", "revenue_estimate"),
    # news-v23 (owner 2026-11-19): ONE post of 2-4 companies — their reports, nothing else
    R.EarningsDay: ("series", "reports"),
    R.Segment: ("name", "value_usd"),
    R.MoneyMap: ("series", "company", "fiscal_year", "period_end", "segments", "other_usd",
                 "eliminations_usd", "revenue_usd", "gross_profit_usd", "operating_profit_usd",
                 "net_income_usd"),
    # news-v23 phase 3 (the weekly theme post's "biggest" angle): a member's MARKET VALUE, from the market-capitalization
    # batch read (owner 2026-09-28: market cap may be shown publicly) — never the profile's cap, never a price
    R.ThemeMember: ("company", "top_segment", "top_segment_share", "fiscal_year", "market_value_usd",
                    # news-v23 phase 3, the last three angles (2026-11-20): two years' revenue and the net income (the
                    # statements: growth and profit per $100 only reach the post), the Form 4 purchase and sale totals
                    # (company totals — no person, role or name), the distinct members' COUNT (no identity)
                    "revenue_usd", "prior_revenue_usd", "net_income_usd", "purchases_usd", "sales_usd",
                    "congress_members",
                    # phase 4 (2026-11-20): the company's newest fiscal year when its segment fact walked back past a
                    # gross one (a year, no figure — it only makes the template tag the fact)
                    "latest_fiscal_year"),
    # `theme_size` (the shared adapter/templates contract, 2026-10-10): the theme's ticker count
    # as the app card shows it, before any gate — a count, no identity, no price.
    R.ThemeExplainer: ("series", "slug", "title", "members", "tickers_as_of", "theme_size",
                       # news-v23 phase 3: the post's angle, its run date (the ledger key) and the market values' date
                       "angle", "posted_on", "values_as_of",
                       # 2026-11-20: the insider scorecard's filing window and its checked count, the Congress month
                       "window_start", "window_end", "companies_checked", "month",
                       # the fix round (2026-11-20): whether the list's date is the row's last write (a bool, no
                       # identity, never printed)
                       "tickers_as_of_from_row"),
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


#: The ONLY per-share dollar figures a filing record may carry (owner decision 2026-11-15, "Captions + picture"):
#: the Form 4 lines' OWN cost per share. marketing.md §1 allows "insider (Form 4) … as filed (the filing's own
#: amounts and prices)" and bars "a market price (current, delayed, end-of-day or intraday), % price moves,
#: price charts" — a filed cost is the first, never the second. It may be shown only in a caption or on the post
#: picture's facts, labelled "as filed", never the word "price", never narrated or drawn on a video (the
#: templates' rule). A new per-share field anywhere must be decided HERE.
FILED_FIGURE_FIELDS = frozenset({"cost_per_share_min_usd", "cost_per_share_max_usd"})
#: Earnings per share is per-share by definition and a reported RESULT (marketing.md §1: "earnings dates and
#: results against estimates"), never a price — pinned apart, on `EarningsReport` only.
EPS_FIELDS = frozenset({("EarningsReport", "eps_actual"), ("EarningsReport", "eps_estimate")})


def _per_share_shaped(name: str) -> bool:
    tokens = set(name.split("_"))
    return bool({"per", "cost", "eps", "pershare", "unit"} & tokens) or {"share", "usd"} <= tokens


def test_the_filed_figure_exemption_is_exact():
    """Across EVERY record type, nested included: the per-share-shaped fields are exactly the filed cost pair
    (on `InsiderPurchase`) and the EPS pair (on `EarningsReport`) — no other record carries a per-share figure."""
    shaped = {(cls.__name__, f.name) for cls in R.ALL_RECORD_TYPES for f in fields(cls) if _per_share_shaped(f.name)}
    assert shaped == {("InsiderPurchase", n) for n in FILED_FIGURE_FIELDS} | EPS_FIELDS
    assert {f.name for f in fields(R.InsiderPurchase)} >= FILED_FIGURE_FIELDS
    # never a price token: the exemption is a cost AS FILED, and the token ban still holds over it
    for name in FILED_FIGURE_FIELDS:
        assert not set(name.split("_")) & BANNED_TOKENS and name not in TOKEN_EXEMPT_FIELDS
    # the shape check is not vacuous: what a price-like field would be called is caught
    for bad in ("share_value_usd", "cost_basis", "per_share_usd", "unit_usd", "eps_ttm", "cost_per_unit"):
        assert _per_share_shaped(bad), bad
    for fine in ("amount_usd", "shares", "prev_shares", "value_usd", "top_segment_share", "revenue_usd"):
        assert not _per_share_shaped(fine), fine


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
    # Batch 1: the earnings statements' own revenue — within 3% of the calendar's (so the period check
    # places the quarter) yet never a travelling figure: only the calendar's revenue and the period end do
    1101234567.0, 1051234567.0,
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


async def _run(series, fmp, *, run_date=None, **deps):
    """The adapter on `T.RUN` — Money Map on `T.MM_RUN`, where the fixtures' Apple fiscal 2025 is still the
    latest year (Batch 1's `money_map_next_year_due` refuses it on `T.RUN`, and `_sheets` needs a record)."""
    if run_date is None:
        run_date = T.MM_RUN if series == "money_map" else T.RUN
    deps.setdefault("actions", T.SplitActions())    # hermetic: the 13F split re-check (review of 2026-10-16, F1)
    return await A.candidates(series, run_date=run_date, exclude=frozenset(), limit=5,
                              deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp, **deps))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    A.clear_memo()
    A._registry_order.cache_clear()
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", False)
    yield
    A.clear_memo()


#: The REAL members-elect window (owner decision 4), kept before the autouse fixture below replaces it.
REAL_MEMBERS_ELECT_WINDOW = R.members_elect_window


@pytest.fixture(autouse=True)
def _names_outside_the_members_elect_window(monkeypatch):
    """`T.RUN` (Nov 16, 2026) is inside the members-elect window, where no person is built at all. The
    insider canary run pins the NAMED path (a rendered, corroborated name is the one person value ALLOWED
    to travel), so the window is off here; `test_inside_the_members_elect_window_no_person_travels`
    restores it."""
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)


#: Each company's issuer CIK — a canary: it keys the line check and the per-issuer Form 4/A read
#: (review round 9, ``companyCik``), and must never travel. A row's ``companyCik`` is its
#: profile's (FMP's issuer index files every row under it), except the dropped member's.
_INSIDER_CIKS = {"GME": "0004242424", "FOX": "0004242425", "HD": "0004242426", "LOW": "0004242427",
                 "NKE": "0004242428"}


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
        # owner decisions 2026-11-20 ("3 - 5 companies at once"): two more issuers, so each week holds three — LOW's and
        # NKE's CEOs (unrenderable reporting names: they must never travel) and LOW's director, NKE's CFO.
        T.irow(sym="LOW", name="ZQXREPORTER ZQXLOWCEO", cik="0000000601", shares=30_000, price=30.0,
               companyCik=_INSIDER_CIKS["LOW"], **canary),
        T.irow(sym="NKE", name="ZQXREPORTER ZQXNKECEO", cik="0000000602", shares=20_000, price=30.0,
               companyCik=_INSIDER_CIKS["NKE"], **canary),
        T.irow(sym="LOW", name="ZQXREPORTER ZQXLOWDIR", cik="0000000603", title="director, other: ZQXOTHER",
               shares=15_000, price=30.0, companyCik=_INSIDER_CIKS["LOW"], **canary),
        T.irow(sym="NKE", name="ZQXREPORTER ZQXNKECFO", cik="0000000604", title="officer: Chief Financial Officer",
               shares=12_000, price=30.0, companyCik=_INSIDER_CIKS["NKE"], **canary),
    ]


def _insider_profiles() -> Dict[str, Any]:
    profiles = {
        "GME": _canary_profile("GME", "GameStop Corp.", price=25.123456, cap=8080808080.0,
                               ceo="Mr. Ryan Cohen ZQXCEOSUFFIX"),
        "FOX": _canary_profile("FOX", "Fox Corporation", price=29.5, cap=8080808080.0, exchange="NASDAQ",
                               ceo="ZQXCEOSUFFIX"),
        "HD": _canary_profile("HD", "The Home Depot, Inc.", price=300.0, cap=300e9),
        "LOW": _canary_profile("LOW", "Lowe's Companies, Inc.", price=30.0, cap=8080808080.0, ceo="ZQXCEOSUFFIX"),
        "NKE": _canary_profile("NKE", "NIKE, Inc.", price=30.0, cap=8080808080.0, ceo="ZQXCEOSUFFIX"),
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
        assert [r.person_name for r in rows] == ["Ryan Cohen", None, None, None]     # anti-vacuity
        assert got.rejections["congress_name"] == 1
    else:
        assert [(r.company.symbol, r.role) for r in rows] == [("FOX", "director"), ("LOW", "director"), ("NKE", "cfo")]
    assert fmp.issuer_calls, "anti-vacuity: the per-issuer read ran on the canary rows"
    log = caplog.text.lower()
    assert "pelosi" not in log and "nancy" not in log and "zqxreporter" not in log
    assert "zqxurl" not in log and "https://" not in log


@pytest.mark.asyncio
async def test_inside_the_members_elect_window_no_person_travels(monkeypatch):
    """Owner decision 4 (Batch 1): inside the window (Election Day through January 3) no person value
    leaves the adapter — not even the rendered, corroborated CEO the named run above lets travel."""
    monkeypatch.setattr(R, "members_elect_window", REAL_MEMBERS_ELECT_WINDOW)
    fmp = T.FakeFMP(insider=_insider_rows(), profiles=_insider_profiles())
    got = await _run("ceo_buys", fmp)
    assert [r.company.symbol for r in got.records[0].rows] == ["GME", "FOX", "LOW", "NKE"]   # anti-vacuity: the rows
    assert all(r.person_name is None for r in got.records[0].rows)
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        assert "cohen" not in low and "ryan" not in low


# ── the spotlight lane (2026-11-15): the insider spotlight and the filing's own cost per share ───────────────

def _spotlight_rows() -> List[Dict[str, Any]]:
    """One issuer (GME) with a cluster, filed Nov 17-18 (the window of Thursday Nov 19's spotlight, the previous run
    Tuesday): the CEO (renders and corroborates — the one person value allowed to travel), a director whose reporting
    name cannot be rendered (it must never travel), the CFO, a member of Congress (dropped, never logged), and a
    run-day filing of another company (the feed's proof). Every line's own price is 25.0 / 25.5 / 26.0; the PROFILE
    price is the canary 25.123456."""
    canary = dict(securityName="Common Stock ZQXSECNAME", url="https://www.sec.gov/ZQXURL/x.htm",
                  securitiesOwned=7_777_777, companyCik=_INSIDER_CIKS["GME"])
    return [
        T.irow(title="director, officer: Chief Executive Officer, other: ZQXOTHER", reportingCik="0009191919",
               filed="2026-11-18", traded="2026-11-17", **canary),
        T.irow(price=25.5, title="officer: Chief Executive Officer", reportingCik="0009191919", filed="2026-11-18",
               traded="2026-11-16", **canary),
        T.irow(name="ZQXREPORTER ZQXREPORTER", cik="0000000555", title="director, other: ZQXOTHER", shares=20_000,
               price=26.0, filed="2026-11-17", traded="2026-11-16", **canary),
        T.irow(name="TAYLOR EMMA", cik="0000000556", title="officer: Chief Financial Officer", shares=10_000,
               filed="2026-11-18", traded="2026-11-17", **canary),
        T.irow(name="PELOSI NANCY", cik="0000000444", title="director", shares=30_000, filed="2026-11-18",
               traded="2026-11-17", **canary),
        T.irow(sym="FOX", name="SMITH JOHN", cik="0000000111", filed="2026-11-19", traded="2026-11-18",
               companyCik=_INSIDER_CIKS["FOX"]),
        # owner decisions 2026-11-20 ("3 - 5 companies at once"): two more issuers inside the window, so the list holds
        # three — LOW's CEO and NKE's director (unrenderable reporting names: they must never travel)
        T.irow(sym="LOW", name="ZQXREPORTER ZQXLOWCEO", cik="0000000601", shares=20_000, price=30.0, filed="2026-11-17",
               traded="2026-11-16", **dict(canary, companyCik=_INSIDER_CIKS["LOW"])),
        T.irow(sym="NKE", name="ZQXREPORTER ZQXNKEDIR", cik="0000000603", title="director, other: ZQXOTHER",
               shares=10_000, price=30.0, filed="2026-11-18", traded="2026-11-17",
               **dict(canary, companyCik=_INSIDER_CIKS["NKE"])),
    ]


async def _run_spotlight(fmp, **kw):
    return await A.candidates("insider_spotlight", run_date=T.SPOT_RUN, exclude=frozenset(), limit=5,
                              deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp, actions=T.SplitActions()),
                              spotlight_checked_through=T.SPOT_CHECKED, **kw)


@pytest.mark.asyncio
async def test_insider_spotlight_leaks_nothing_but_its_fields(caplog):
    caplog.set_level(logging.DEBUG)
    fmp = T.FakeFMP(insider=_spotlight_rows(), profiles=_insider_profiles())
    got = await _run_spotlight(fmp)
    for sheet in _sheets(got):
        _assert_clean(sheet)
    (rec,) = got.records
    assert [c.company.symbol for c in rec.companies] == ["GME", "LOW", "NKE"]                   # anti-vacuity
    people = rec.companies[0].people
    assert [(p.role, p.person_name) for p in people] == [("ceo", "Ryan Cohen"), ("director", None),
                                                         ("cfo", "Emma Taylor")]                # anti-vacuity
    assert [p.person_name for c in rec.companies[1:] for p in c.people] == [None, None]
    assert got.rejections["congress_name"] == 1 and fmp.issuer_calls
    log = caplog.text.lower()
    assert "pelosi" not in log and "nancy" not in log and "zqxreporter" not in log
    assert "zqxurl" not in log and "https://" not in log


@pytest.mark.asyncio
async def test_filed_cost_is_the_form4_lines_own_price(monkeypatch):
    """B decision 1 (owner, "Captions + picture"): the cost per share that travels is the Form 4 LINES' own price —
    25.0 and 25.5 for GameStop's CEO, 26.0 for the director — never the profile's 25.123456 (a canary). Mutation
    stand-in: an `_insider_row` that lets the profile price into the pair (still a valid record: the average stays
    inside it) is caught by the canary check."""
    fmp = T.FakeFMP(insider=_spotlight_rows(), profiles=_insider_profiles())
    got = await _run_spotlight(fmp)
    (rec,) = got.records
    assert [(p.cost_per_share_min_usd, p.cost_per_share_max_usd) for p in rec.companies[0].people] == [
        (25.0, 25.5), (26.0, 26.0), (25.0, 25.0)]
    assert [(p.cost_per_share_min_usd, p.cost_per_share_max_usd) for c in rec.companies[1:] for p in c.people] == [
        (30.0, 30.0), (30.0, 30.0)]
    assert _insider_profiles()["GME"]["price"] == 25.123456
    real = A._insider_row

    def leaky(ctx, company, profile, lines):
        row = real(ctx, company, profile, lines)
        if isinstance(row, str):
            return row
        return replace(row, cost_per_share_max_usd=max(row.cost_per_share_max_usd, float(profile["price"])))

    monkeypatch.setattr(A, "_insider_row", leaky)
    A.clear_memo()
    got = await _run_spotlight(T.FakeFMP(insider=_spotlight_rows(), profiles=_insider_profiles()))
    assert got.records[0].lead.cost_per_share_max_usd == 25.5                      # GME's lead: 25.5 > 25.123456
    with pytest.raises(AssertionError):
        for sheet in _sheets(got):
            _assert_clean(sheet)


@pytest.mark.asyncio
async def test_a_13f_spotlight_leaks_nothing_but_its_fields():
    whales = [T.whale(T.SOROS, "Soros Fund Management", name="George ZQXWHALEPERSON",
                      avatar_url="https://zqx.example/ZQXAVATAR.png", description="ZQXWHALEDESC",
                      portfolio_value=9191919191919.0)]
    fmp = T.FakeFMP(profiles=_thirteen_f_profiles(), extracts=_canary_book(T.SOROS), dates={T.SOROS: T.DATES_Q3_Q2})
    got = await A.candidates("thirteen_f_spotlight", run_date=T.RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0,
                             deps=A.NewsDeps(fmp=fmp, sb=T.FakeSB({"whales": whales}), actions=T.SplitActions()))
    for sheet in _sheets(got):
        _assert_clean(sheet)
    (rec,) = got.records
    # the list (owner feedback 2026-11-19): every holding's fields are the record's own — the lead and the rest
    assert (rec.filer_cik, rec.moves[0].company.symbol, rec.moves[0].move) == (T.SOROS, "OXY", "no_longer_reported")
    assert len(rec.moves) >= 2 and "ZQX" not in json.dumps(R.record_to_dict(rec))


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
    # news-v23: ONE record of the month's companies
    assert [(r.company.symbol, r.members) for r in got.records[0].counts] == [("AAPL", 2), ("MSFT", 2)]
    for sheet in _sheets(got):
        _assert_clean(sheet)                       # also: no 64-hex string (a hashed identity)
        _assert_no_member(json.dumps(sheet))
    # Walk the records: no field of a CongressMonth or its CongressCounts (nested included) names an identity key.
    for rec in [got.records[0], *got.records[0].counts]:
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
                "INTC": _canary_profile("INTC", "Intel Corporation", exchange="NASDAQ", cap=8080808080.0),
                # news-v23: a public stake's investee is profiled too (its listing verified) — canaries and all
                T._sym_of("Nscale"): _canary_profile(T._sym_of("Nscale"), "Nscale", exchange="NASDAQ",
                                                     cap=8080808080.0)}
    got = await A.candidates("company_stakes", run_date=T.STAKES_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0,
                             deps=A.NewsDeps(fmp=T.FakeFMP(profiles=profiles), club=club, sb=sb))
    assert T._stake_names(got) == ["Intel", "Nscale"]                                   # anti-vacuity
    assert T._stake_rows(got)[1].ownership_pct is None                                  # a qualified pct
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcusip", "zqxupdated", "zqxownershipbasis", "zqxlogosym", "zqxnotice"):
            assert canary not in low


#: Migration 205 (the Trillion Club stake finder, 2026-10-28): a KEPT auto row's Keep stamp is a canary — the record
#: has no field for it — and so is every text of the rows the read leaves out (an un-kept auto row, and a row of an
#: origin the read does not know), each otherwise publishable (lower-case canaries pass the source-title word list),
#: so only the origin rule keeps them out.
_KEPT_AT_CANARY = "2026-10-27T13:57:29.813579+00:00"
_FINDER_CANARIES = ("813579", "13:57:29", "2026-10-27", "zqxautoname", "zqxautotitle", "zqxautobackground",
                    "zqxoriginname", "zqxorigintitle", "zqxorigin", "zqxfindingid", "zqxinvesteecik", "zqxcusip",
                    "zqxupdated")


def _stake_finder_rows() -> List[Dict[str, Any]]:
    canary = dict(source_url="https://www.sec.gov/Archives/edgar/data/ZQXURL/primary_doc.xml",
                  investee_cusip="ZQXCUSIP1", updated_at="ZQXUPDATED", finding_id="ZQXFINDINGID",
                  investee_cik="ZQXINVESTEECIK", withdrawn_at=None, sort_order=31313)
    return [
        # kept by the owner (with a figure the owner added in Studio after Keep), and published by the owner's tap
        T.stake("nvidia", "Ayar Labs", origin="auto", owner_kept_at=_KEPT_AT_CANARY, disclosed_value_usd=155e6,
                as_of="2026-09-30", **canary),
        T.stake("nvidia", "Lambda", origin="proposal", disclosed_value_usd=480e6, as_of="2026-08-29", **canary),
        # left out
        T.stake("nvidia", "Zqxautoname Robotics", origin="auto", as_of="2026-10-20",
                source_title="NVIDIA Schedule 13G zqxautotitle", background="Invested in a zqxautobackground round.",
                **canary),
        T.stake("nvidia", "Zqxoriginname Labs", origin="ZQXORIGIN", owner_kept_at=_KEPT_AT_CANARY, as_of="2026-10-21",
                source_title="NVIDIA 10-Q zqxorigintitle", **canary),
    ]


def _all_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _all_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_keys(v)


@pytest.mark.parametrize("honour_or", [True, False], ids=["postgrest_filter", "strict_recheck"])
@pytest.mark.asyncio
async def test_the_stake_finders_columns_and_left_out_rows_leak_nothing(monkeypatch, caplog, honour_or):
    """``origin`` / ``owner_kept_at`` decide the read and never travel: no fact sheet carries either column (nor the
    finder's other columns), an origin value, or the Keep stamp; a row left out leaves no text in a sheet or a log line
    (the unknown origin's VALUE is named in its WARNING, by design). Both through the database filter and through the
    strict re-check alone (the filter missing). Mutation-checked by hand (2026-10-28, in memory): with
    `_postable_stake_rows` removed the ``strict_recheck`` leg is red (both left-out rows became records)."""
    caplog.set_level(logging.INFO, logger=A.__name__)
    monkeypatch.setattr(A.settings, "TRILLION_CLUB_ENABLED", True)
    club, sb, _fmp = T.stakes_world(_stake_finder_rows())
    sb.honour_or = honour_or
    profiles = {"NVDA": _canary_profile("NVDA", "NVIDIA Corporation", exchange="NASDAQ", price=25.123456,
                                        cap=8080808080.0)}
    for name in ("Ayar Labs", "Lambda", "Zqxautoname Robotics", "Zqxoriginname Labs"):
        profiles[T._sym_of(name)] = _canary_profile(T._sym_of(name), name, exchange="NASDAQ", cap=8080808080.0)
    got = await A.candidates("company_stakes", run_date=T.STAKES_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0,
                             deps=A.NewsDeps(fmp=T.FakeFMP(profiles=profiles), club=club, sb=sb))
    assert T._stake_names(got) == ["Lambda", "Ayar Labs"]               # anti-vacuity (news-v23: the larger figure first)
    assert got.rejections == {}
    for sheet in _sheets(got):
        _assert_clean(sheet)
        assert not set(_all_keys(sheet)) & {"origin", "owner_kept_at", "finding_id", "investee_cik", "withdrawn_at"}
        assert not set(_all_strings(sheet)) & {"seed", "auto", "proposal"}
        low = json.dumps(sheet).lower()
        for canary in _FINDER_CANARIES:
            assert canary not in low, canary
    low_log = caplog.text.lower()
    for canary in ("zqxautoname", "zqxautotitle", "zqxautobackground", "zqxoriginname", "zqxorigintitle", "813579",
                   "13:57:29", "zqxurl"):
        assert canary not in low_log, canary
    assert "('ZQXORIGIN' ×1;" in caplog.text                         # the unknown origin was seen and left out
    assert ("found automatically by the stake finder" in caplog.text) is (not honour_or)


@pytest.mark.asyncio
async def test_earnings_leak_nothing_but_their_fields():
    canary = dict(lastUpdated="ZQXUPDATED", fiscalDateEnding="ZQXFISCAL", time="ZQXTIME", epsEstimatedLow=99.99,
                  revenueEstimatedHigh=15151515151.0, numberOfAnalysts=31313.13, url="https://zqx.example/ZQXURL")
    cal = {"2027-01-12": [T.erow("AAA", "2027-01-12", 1.5, 1.0, 1.1e9, 1.0e9, **canary)],
           "2027-01-11": [T.erow("BBB", "2027-01-11", -0.4, -0.5, **canary)]}
    # One canary CIK PER company (review of 2026-10-16): the earnings series now reads a profile's CIK as the
    # issuer's identity (one record per issuer), so a CIK shared by both would rightly make them one company.
    # The sector is a canary too: the REIT gate reads it (`eps_basis_reit`) and it never travels.
    profiles = {s: dict(_canary_profile(s, f"{s.title()} Industries Inc.", price=25.123456, cap=8080808080.0),
                        cik=cik, sector="Technology ZQXSECTOR")
                for s, cik in (("AAA", "0004242425"), ("BBB", "0004242426"))}
    # The quarterly-statement read (review round 9's currency read; Batch 1's period check; the review of
    # 2026-10-16's EPS-basis check): only its "USD" and the period end it places travel — never its revenue (a
    # canary within 3% of the calendar's), its EPS or diluted EPS, CIK, links, or an older quarter's figure.
    statement = {"date": "2026-12-31", "reportedCurrency": "USD", "eps": 9.8765, "epsDiluted": 0.123456789,
                 "cik": "0004242424", "link": "https://zqx.example/ZQXINCOMELINK",
                 "finalLink": "https://zqx.example/ZQXINCOMELINK"}
    quarters = {s: [dict(statement, symbol=s, revenue=rev), dict(statement, symbol=s, date="2026-09-30",
                                                                 revenue=4242424242.0)]
                for s, rev in (("AAA", 1101234567.0), ("BBB", 1051234567.0))}
    fmp = T.FakeFMP2b(calendar=cal, profiles=profiles, currency=quarters)
    got = await A.candidates("earnings", run_date=T.EARN_RUN, exclude=frozenset(), limit=5,
                             deadline=time.monotonic() + 60.0, deps=A.NewsDeps(fmp=fmp))
    # news-v23: ONE record of the day's companies (anti-vacuity)
    assert [r.company.symbol for r in got.records[0].reports] == ["AAA", "BBB"]
    assert all(r.period_end.isoformat() == "2026-12-31" for r in got.records[0].reports)   # the read was used
    assert sorted(sym for sym, _limit in fmp.statement_calls) == ["AAA", "BBB"]
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
    # One canary CIK PER company (review round 15): the theme now reads a profile's CIK as the issuer's
    # identity (share classes of one issuer count once), so a CIK shared by all nine would rightly
    # make them one company.
    ciks = {s: str(4242400 + i).zfill(10) for i, s in enumerate(T.theme_profiles())}
    world["fmp"] = T.FakeFMP(profiles={s: {**_canary_profile(s, p["companyName"], exchange="NASDAQ", price=25.123456,
                                                             cap=8080808080.0, isEtf=p["isEtf"]), "cik": ciks[s]}
                                       for s, p in T.theme_profiles().items()})
    got = await T.trun(world)
    assert len(got.records[0].members) == 6                                              # anti-vacuity
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcategory", "zqxaccent", "zqxpin", *ciks.values()):
            assert canary not in low


@pytest.mark.asyncio
async def test_a_theme_market_value_post_leaks_nothing_but_its_fields():
    """news-v23 phase 3, the "biggest" angle: the market-capitalization batch's rows carry canaries beside the three keys
    the adapter reads (`symbol`, `date`, `marketCap`) — a price, a % change, a volume, a name — and the profiles their own
    canary cap and price: none of them reaches a fact sheet, and the only market values in it are the batch's."""
    world = T.theme_world()
    caps = {"NVDA": 4.4e12, "AMD": 3.1e11, "AVGO": 1.6e12, "QCOM": 1.8e11, "MRVL": 7.7e10, "INTC": 1.2e11}
    world["fmp"] = T.FakeFMP(
        profiles={s: {**_canary_profile(s, p["companyName"], exchange="NASDAQ", price=25.123456, cap=8080808080.0,
                                        isEtf=p["isEtf"]), "cik": str(4242400 + i).zfill(10)}
                  for i, (s, p) in enumerate(T.theme_profiles().items())},
        market_caps=[{"symbol": s, "date": "2026-11-18", "marketCap": v, "price": 25.123456,
                      "changesPercentage": 12.3456, "volume": 7777777, "name": "ZQXCAPNAME"} for s, v in caps.items()])
    hist = frozenset({"news:theme_explainer:ai-chips:make_money:2026-10-01"})
    got = await T.trun(world, theme_history=hist)
    (rec,) = got.records
    assert rec.angle == "biggest" and len(rec.members) == 6                               # anti-vacuity
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcapname", "12.3456", "7777777"):
            assert canary not in low
        nums = [m["market_value_usd"] for m in sheet["record"]["members"]]
        assert sorted(nums, reverse=True) == nums and set(nums) == set(caps.values())


@pytest.mark.asyncio
async def test_a_theme_make_money_post_never_carries_the_market_values_that_order_it():
    """Phase 4 (2026-11-20): "how they make money" reads the market-capitalization batch for its TILE ORDER only — the
    batch's rows carry canaries beside the three keys read and the profiles a canary cap: no market value, price or
    canary reaches the fact sheet (the record refuses a market value off a market-value post), while the members' order
    shows the values were read (anti-vacuity)."""
    world = T.theme_world()
    caps = {"NVDA": 4.4e12, "AMD": 3.1e11, "AVGO": 1.6e12, "QCOM": 1.8e11, "MRVL": 7.7e10, "INTC": 1.2e11}
    world["fmp"] = T.FakeFMP(
        profiles={s: {**_canary_profile(s, p["companyName"], exchange="NASDAQ", price=25.123456, cap=8080808080.0,
                                        isEtf=p["isEtf"]), "cik": str(4242400 + i).zfill(10)}
                  for i, (s, p) in enumerate(T.theme_profiles().items())},
        market_caps=[{"symbol": s, "date": "2026-11-18", "marketCap": v, "price": 25.123456,
                      "changesPercentage": 12.3456, "volume": 7777777, "name": "ZQXCAPNAME"} for s, v in caps.items()])
    got = await T.trun(world)
    (rec,) = got.records
    assert rec.angle == "make_money" and world["fmp"].calls["market_caps"] == 1                # anti-vacuity
    assert [m.company.symbol for m in rec.members][:4] == ["NVDA", "AVGO", "AMD", "QCOM"]       # ordered by them
    for sheet in _sheets(got):
        _assert_clean(sheet)
        low = json.dumps(sheet).lower()
        for canary in ("zqxcapname", "12.3456", "7777777", "8080808080", *(repr(v) for v in caps.values()),
                       *(str(int(v)) for v in caps.values())):
            assert canary.lower() not in low, canary
        assert all(m["market_value_usd"] is None for m in sheet["record"]["members"])


def test_every_shipped_series_has_a_canary_test_here():
    """A series added to SHIPPED_SERIES must get its canary run in this file — every series whose
    collector exists has one (the 2a four above, the 2b four just above, and the spotlight pair of
    2026-11-15, whose collectors exist ahead of their shipping)."""
    covered = {"ceo_buys", "insider_buys", "thirteen_f", "money_map",
               "congress_count", "company_stakes", "earnings", "theme_explainer",
               "insider_spotlight", "thirteen_f_spotlight"}
    assert set(selection.SHIPPED_SERIES) <= covered
    assert set(A.COLLECTORS) == covered


def test_the_canary_check_is_not_vacuous():
    """Mutation stand-in: a sheet that DID carry a planted value is refused by `_assert_clean`."""
    rec = R.CompanyRef(symbol="GME", name="GameStop")
    for leak in ({"x": "Mr. Ryan Cohen ZQXCEOSUFFIX"}, {"x": 25.123456}, {"x": "via FMP"},
                 {"x": "https://images.financialmodelingprep.com/symbol/GME.png"}, {"x": "a" * 64}):
        with pytest.raises(AssertionError):
            _assert_clean({"record": {"company": {"symbol": rec.symbol}}, **leak})
