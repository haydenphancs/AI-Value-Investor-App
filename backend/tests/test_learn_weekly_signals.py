"""`app/services/learn_weekly/signals.py` — the topic step's ranking inputs (plan §1.5), hermetic.

Pins: news counted per 7-day ET week ending on the run date (not an ISO week — a Thursday run
would compare 3.75 days to 7), the pagination stop rules (short page, past the window, a repeated
page, the page cap → SATURATED with only the fully covered weeks in the baseline), EVERY raised
exception and every `fetch_failed` answer → MISSING (never zero), the size check returned as a
bool only, SEC's ticker → CIK map (and `get_company_tickers` in the thin client), the 8-K weekly
counts, the earnings flag, the universe order and cap, and the eligible-theme rule.

No network: FMP and SEC are fakes, Supabase and the ledger are patched at the binding the module
uses (`signals.get_supabase`, `ledger.list_pieces`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.integrations import sec_edgar
from app.integrations.fmp import (
    EmptyAfterFailure,
    FMPAuthException,
    FMPException,
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.integrations.sec_edgar import (
    COMPANY_TICKERS_URL,
    MAX_COMPANY_TICKERS_BYTES,
    SECConfigException,
    SECEdgarClient,
    SECEdgarDocumentTooLargeException,
    SECRateLimitException,
    SECUnavailableException,
)
from app.services.learn_weekly import case_library, ledger, sec_facts, signals
from app.services.learn_weekly.contracts import TickerSignal

UTC = timezone.utc
NOW = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)   # Thursday 18:00 ET (EDT)
TODAY = date(2026, 10, 22)
UA = "Caydex Test learn-signals@example.com"
LOGGER = "app.services.learn_weekly.signals"


# ── fakes ─────────────────────────────────────────────────────────────────────────────────────


def articles(ticker, per_week, *, prefix="a"):
    """News rows, newest first: `per_week[k]` articles in week k, on days TODAY-7k-(i % 7)."""
    rows = []
    for k, n in enumerate(per_week):
        for i in range(n):
            day = TODAY - timedelta(days=7 * k + (i % 7))
            rows.append({"symbol": ticker, "publishedDate": f"{day.isoformat()} 12:00:00",
                         "title": f"{prefix} {k}-{i}", "url": f"https://news.example/{ticker}/{prefix}{k}-{i}"})
    rows.sort(key=lambda r: r["publishedDate"], reverse=True)
    return rows


class FakeFMP:
    def __init__(self, news=None, profiles=None, calendar=None):
        self.news = news or {}
        self.profiles = profiles or {}
        self.calendar = calendar or {}
        self.calls = []

    async def get_stock_news(self, ticker=None, limit=10, from_date=None, to_date=None, page=0):
        self.calls.append(("news", ticker, page, from_date, to_date, limit))
        value = self.news.get(ticker, [])
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(page, limit)
            if isinstance(value, BaseException):
                raise value
            return value
        if isinstance(value, list):
            return value[page * limit:(page + 1) * limit]
        return value

    async def get_company_profile(self, ticker):
        self.calls.append(("profile", ticker))
        value = self.profiles.get(ticker, profile(ticker))
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append(("calendar", from_date, to_date))
        value = self.calendar.get(from_date, [])
        if isinstance(value, BaseException):
            raise value
        return value


def profile(ticker, cap=50e9, exchange="NASDAQ", **over):
    data = {"symbol": ticker, "marketCap": cap, "exchange": exchange, "isEtf": False,
            "isFund": False, "isActivelyTrading": True, "companyName": f"{ticker} Holdings Inc"}
    data.update(over)
    return data


def submissions(name, filings=(), *, forms_extra=(), tickers=("X",), sic="7374"):
    """filings: (form, filing_date, items, accession, primary_document)."""
    rows = list(filings) + [(f, "2026-01-05", "", f"0000000001-26-{900000 + i:06d}", "x.htm")
                            for i, f in enumerate(forms_extra)]
    return {
        "name": name, "tickers": list(tickers), "exchanges": ["Nasdaq"] * len(tickers), "sic": sic,
        "sicDescription": "Services-Computer Programming",
        "filings": {"recent": {
            "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
            "items": [r[2] for r in rows], "accessionNumber": [r[3] for r in rows],
            "primaryDocument": [r[4] for r in rows],
        }},
    }


class FakeSEC:
    def __init__(self, tickers=None, subs=None, docs=None):
        self.tickers = tickers if tickers is not None else {}
        self.subs = subs or {}
        self.docs = docs or {}
        self.calls = []

    async def get_company_tickers(self):
        self.calls.append(("tickers",))
        if isinstance(self.tickers, BaseException):
            raise self.tickers
        return self.tickers

    async def get_submissions(self, cik):
        self.calls.append(("submissions", cik))
        value = self.subs.get(cik)
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_filing_document(self, cik, accession, document):
        self.calls.append(("document", cik, accession, document))
        value = self.docs.get((accession, document))
        if isinstance(value, BaseException):
            raise value
        return value


def cik_table(**tickers):
    return {str(i): {"cik_str": int(cik), "ticker": t, "title": f"{t} title"}
            for i, (t, cik) in enumerate(tickers.items())}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    signals.clear_caches()
    sec_facts.clear_caches()
    yield
    signals.clear_caches()
    sec_facts.clear_caches()


async def collect(tickers, *, fmp=None, sec=None):
    fmp = fmp if fmp is not None else FakeFMP()
    sec = sec if sec is not None else FakeSEC(tickers=cik_table(**{t: 1000 + i for i, t in enumerate(tickers)}))
    out = await signals.collect_signals(NOW, tickers, fmp=fmp, sec=sec)
    return {s.ticker: s for s in out}, fmp, sec


# ── week arithmetic ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("delta,week", [
    (0, 0), (6, 0), (7, 1), (13, 1), (14, 2), (62, 8), (63, None), (400, None), (-1, 0), (-2, None),
])
def test_week_index_counts_7_et_days_ending_today(delta, week):
    assert signals.week_index(TODAY, TODAY - timedelta(days=delta)) == week


def test_the_window_is_nine_weeks():
    assert signals.WEEKS == 9 and signals.WINDOW_DAYS == 63


# ── news counting ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_news_is_counted_per_week_with_the_baseline_oldest_first():
    per_week = [9, 1, 2, 3, 4, 5, 6, 7, 8]
    fmp = FakeFMP(news={"CRWV": articles("CRWV", per_week)})
    sigs, fmp, _ = await collect(["CRWV"], fmp=fmp)
    s = sigs["CRWV"]
    assert s.news_week == 9
    assert s.news_baseline == (8, 7, 6, 5, 4, 3, 2, 1)
    assert s.news_saturated is False
    news_calls = [c for c in fmp.calls if c[0] == "news"]
    assert len(news_calls) == 1
    _, ticker, page, frm, to, limit = news_calls[0]
    assert (ticker, page, limit) == ("CRWV", 0, signals.NEWS_PAGE_SIZE)
    assert frm == (TODAY - timedelta(days=62)).isoformat() and to == (TODAY + timedelta(days=1)).isoformat()


@pytest.mark.asyncio
async def test_a_partial_iso_week_is_not_how_this_week_is_counted():
    # 5 articles on the Friday and Saturday BEFORE this ISO week's Monday still count in week 0:
    # the counting week is the 7 days ending Thursday, so a Thursday run compares 7 days to 7.
    rows = [{"symbol": "AMD", "publishedDate": "2026-10-16 09:00:00", "url": f"u{i}"} for i in range(3)]
    rows += [{"symbol": "AMD", "publishedDate": "2026-10-17 09:00:00", "url": f"v{i}"} for i in range(2)]
    sigs, _, _ = await collect(["AMD"], fmp=FakeFMP(news={"AMD": rows}))
    assert sigs["AMD"].news_week == 5


@pytest.mark.asyncio
async def test_duplicate_articles_count_once_and_out_of_window_rows_not_at_all():
    rows = articles("NVDA", [2, 1])
    rows += [dict(rows[0])]                                     # a repeated article
    rows += [{"symbol": "NVDA", "publishedDate": "2026-08-01 10:00:00", "url": "old"}]
    rows += [{"symbol": "NVDA", "publishedDate": "2026-10-30 10:00:00", "url": "future"}]
    sigs, _, _ = await collect(["NVDA"], fmp=FakeFMP(news={"NVDA": rows}))
    assert sigs["NVDA"].news_week == 2 and sigs["NVDA"].news_baseline[-1] == 1


@pytest.mark.asyncio
async def test_pagination_stops_on_a_short_page(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    fmp = FakeFMP(news={"ORCL": articles("ORCL", [4])})
    sigs, fmp, _ = await collect(["ORCL"], fmp=fmp)
    assert [c[2] for c in fmp.calls if c[0] == "news"] == [0]
    assert sigs["ORCL"].news_week == 4 and not sigs["ORCL"].news_saturated


@pytest.mark.asyncio
async def test_pagination_stops_once_a_page_reaches_past_the_window(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    rows = articles("ORCL", [4]) + [{"symbol": "ORCL", "publishedDate": "2026-07-01 10:00:00",
                                      "url": "ancient"}] + articles("ORCL", [0, 0, 0, 0, 5], prefix="b")
    fmp = FakeFMP(news={"ORCL": rows})
    sigs, fmp, _ = await collect(["ORCL"], fmp=fmp)
    assert [c[2] for c in fmp.calls if c[0] == "news"] == [0]
    assert sigs["ORCL"].news_saturated is False


@pytest.mark.asyncio
async def test_pagination_walks_full_pages_until_the_feed_runs_out(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    rows = articles("MSFT", [3, 3, 3, 3])          # 12 rows: pages of 5, 5, 2
    fmp = FakeFMP(news={"MSFT": rows})
    sigs, fmp, _ = await collect(["MSFT"], fmp=fmp)
    assert [c[2] for c in fmp.calls if c[0] == "news"] == [0, 1, 2]
    s = sigs["MSFT"]
    assert s.news_week == 3 and s.news_baseline == (0, 0, 0, 0, 0, 3, 3, 3) and not s.news_saturated


@pytest.mark.asyncio
async def test_the_page_cap_saturates_and_keeps_only_fully_covered_weeks(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    monkeypatch.setattr(signals, "NEWS_MAX_PAGES", 3)
    # 3 a week on days 0, -1, -2 of each week: 15 rows fetched = weeks 0-4; the oldest fetched day
    # is TODAY-30, so weeks 0-3 are complete and week 4 (days -28..-34) is cut — never a 0.
    fmp = FakeFMP(news={"NVDA": articles("NVDA", [3] * 9)})
    sigs, fmp, _ = await collect(["NVDA"], fmp=fmp)
    s = sigs["NVDA"]
    assert [c[2] for c in fmp.calls if c[0] == "news"] == [0, 1, 2]
    assert s.news_saturated is True
    assert s.news_week == 3
    assert s.news_baseline == (3, 3, 3)


@pytest.mark.asyncio
async def test_a_page_cap_inside_this_week_leaves_no_baseline(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    monkeypatch.setattr(signals, "NEWS_MAX_PAGES", 2)
    rows = [{"symbol": "META", "publishedDate": f"{TODAY.isoformat()} 0{i % 9}:00:00", "url": f"m{i}"}
            for i in range(40)]
    sigs, _, _ = await collect(["META"], fmp=FakeFMP(news={"META": rows}))
    s = sigs["META"]
    assert s.news_saturated and s.news_week == 10 and s.news_baseline == ()


@pytest.mark.asyncio
async def test_a_repeated_page_stops_the_walk_and_reads_as_a_lower_bound(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    page0 = articles("AVGO", [5])
    fmp = FakeFMP(news={"AVGO": lambda page, limit: list(page0)})   # the page param is ignored
    sigs, fmp, _ = await collect(["AVGO"], fmp=fmp)
    assert [c[2] for c in fmp.calls if c[0] == "news"] == [0, 1]
    assert sigs["AVGO"].news_saturated is True and sigs["AVGO"].news_week == 5


@pytest.mark.parametrize("exc", [
    FMPRateLimitException("429", retry_after="60"), FMPAuthException("401"),
    FMPUnavailableException("503"), FMPNotEntitledException("402"), FMPException("x"),
    httpx.ConnectTimeout("t"), httpx.HTTPStatusError("400", request=httpx.Request("GET", "https://x"),
                                                     response=httpx.Response(400)),
    ValueError("bad"), RuntimeError("boom"), KeyError("k"),
])
@pytest.mark.asyncio
async def test_every_raised_exception_makes_the_news_count_missing_never_zero(exc):
    sigs, _, _ = await collect(["CEG"], fmp=FakeFMP(news={"CEG": exc}))
    s = sigs["CEG"]
    assert s.news_week is None and s.news_baseline == () and s.news_saturated is False


@pytest.mark.parametrize("answer", [EmptyAfterFailure("ReadTimeout: x"), {"Error Message": "x"}, None, "x"])
@pytest.mark.asyncio
async def test_a_failed_or_malformed_answer_is_missing(answer):
    sigs, _, _ = await collect(["VST"], fmp=FakeFMP(news={"VST": lambda p, l: answer}))
    assert sigs["VST"].news_week is None


@pytest.mark.asyncio
async def test_a_genuinely_empty_feed_is_zero_not_missing():
    sigs, _, _ = await collect(["VST"], fmp=FakeFMP(news={"VST": []}))
    assert sigs["VST"].news_week == 0 and sigs["VST"].news_baseline == (0,) * 8


@pytest.mark.asyncio
async def test_a_failure_on_a_later_page_makes_the_whole_count_missing(monkeypatch):
    monkeypatch.setattr(signals, "NEWS_PAGE_SIZE", 5)
    rows = articles("AMZN", [3, 3, 3])

    def pages(page, limit):
        return rows[:5] if page == 0 else FMPRateLimitException("429 on page 1")

    sigs, _, _ = await collect(["AMZN"], fmp=FakeFMP(news={"AMZN": pages}))
    assert sigs["AMZN"].news_week is None


@pytest.mark.asyncio
async def test_a_feed_of_mostly_unusable_rows_is_missing_but_a_few_are_skipped():
    bad = [{"symbol": "GOOGL", "publishedDate": "N/A", "url": f"b{i}"} for i in range(6)]
    bad += [{"symbol": "OTHER", "publishedDate": f"{TODAY} 10:00:00", "url": f"o{i}"} for i in range(2)]
    bad += ["not a row", None]
    good = articles("GOOGL", [3])
    sigs, _, _ = await collect(["GOOGL"], fmp=FakeFMP(news={"GOOGL": good + bad}))
    assert sigs["GOOGL"].news_week is None
    few = good + articles("GOOGL", [2], prefix="z") + bad[:1]
    sigs, _, _ = await collect(["GOOGL"], fmp=FakeFMP(news={"GOOGL": few}))
    assert sigs["GOOGL"].news_week == 5


# ── size ──────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prof,ok,excluded", [
    (profile("T"), True, None),
    (profile("T", cap=2_000_000_000), True, None),
    (profile("T", cap=1_999_999_999), False, None),
    (profile("T", exchange="OTC"), False, None),
    (profile("T", exchange="NYSE"), True, None),
    (profile("T", exchange="AMEX"), True, None),
    (profile("T", exchange=None, exchangeShortName="NYSE"), True, None),
    (profile("T", cap=None, mktCap=9e9), True, None),
    (profile("T", cap=float("nan")), False, None),
    (profile("T", cap=float("inf")), False, None),
    (profile("T", cap=True), False, None),
    (profile("T", cap="50000000000"), False, None),
    (profile("T", isEtf=True), False, "etf_or_fund"),
    (profile("T", isFund="true"), False, "etf_or_fund"),
    (profile("T", isActivelyTrading=False), False, "inactive"),
    ({}, False, None),
])
@pytest.mark.asyncio
async def test_size_ok_is_a_bool_decided_from_the_profile(prof, ok, excluded):
    sigs, _, _ = await collect(["T"], fmp=FakeFMP(profiles={"T": prof}))
    assert sigs["T"].size_ok is ok
    assert sigs["T"].excluded_reason == excluded


@pytest.mark.parametrize("bad", [FMPRateLimitException("429"), FMPAuthException("401"), RuntimeError("x"),
                                 ["not", "a", "dict"], profile("OTHER")])
@pytest.mark.asyncio
async def test_a_failed_profile_read_is_unknown_never_too_small(bad):
    sigs, _, _ = await collect(["T"], fmp=FakeFMP(profiles={"T": bad}))
    assert sigs["T"].size_ok is None and sigs["T"].excluded_reason is None


# ── SEC: the CIK map and the 8-K counts ───────────────────────────────────────────────────────


def test_parse_company_tickers_keeps_valid_rows_and_the_first_of_a_conflict(caplog):
    data = {
        "0": {"cik_str": 1769628, "ticker": "crwv", "title": "CoreWeave"},
        "1": {"cik_str": "0001341439", "ticker": "ORCL", "title": "Oracle"},
        "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"},
        "3": {"cik_str": 999, "ticker": "CRWV", "title": "an impostor row"},
        "4": {"cik_str": True, "ticker": "BAD"},
        "5": {"cik_str": 12, "ticker": ""},
        "6": "not a row",
        "7": {"cik_str": 0, "ticker": "ZERO"},
    }
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = signals.parse_company_tickers(data)
    assert out == {"CRWV": "0001769628", "ORCL": "0001341439", "BRK-B": "0001067983"}
    assert any("two CIKs" in r.getMessage() for r in caplog.records)
    assert signals.cik_for("BRK.B", out) == "0001067983"
    assert signals.cik_for(" crwv ", out) == "0001769628"
    assert signals.cik_for("NOPE", out) is None
    assert signals.parse_company_tickers(None) == {} and signals.parse_company_tickers([1]) == {}


@pytest.mark.asyncio
async def test_the_cik_map_is_read_once_and_cached():
    sec = FakeSEC(tickers=cik_table(CRWV=1769628))
    assert await signals.ticker_cik_map(sec=sec) == {"CRWV": "0001769628"}
    assert await signals.ticker_cik_map(sec=sec) == {"CRWV": "0001769628"}
    assert sec.calls == [("tickers",)]


@pytest.mark.parametrize("answer", [SECRateLimitException("429"), SECConfigException("no ua"),
                                    SECUnavailableException("503"), None, {}, {"0": "junk"}])
@pytest.mark.asyncio
async def test_no_cik_map_means_signals_unavailable_before_any_fmp_call(answer):
    fmp = FakeFMP()
    with pytest.raises(signals.SignalsUnavailable):
        await signals.collect_signals(NOW, ["CRWV"], fmp=fmp, sec=FakeSEC(tickers=answer))
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_8k_counts_per_week_and_this_weeks_items_only():
    filings = [
        ("8-K", "2026-10-21", "1.01,9.01", "0001769628-26-000101", "a.htm"),
        ("8-K", "2026-10-16", "2.02", "0001769628-26-000100", "b.htm"),     # week 0 (6 days ago)
        ("8-K", "2026-10-15", "5.02", "0001769628-26-000099", "c.htm"),     # week 1
        ("8-K/A", "2026-10-20", "1.01", "0001769628-26-000098", "d.htm"),   # an amendment: not counted
        ("10-Q", "2026-10-20", "", "0001769628-26-000097", "e.htm"),
        ("8-K", "2026-08-21", "8.01", "0001769628-26-000090", "f.htm"),     # 62 days ago: week 8
        ("8-K", "2026-08-20", "8.01", "0001769628-26-000089", "g.htm"),     # 63: outside the window
    ]
    sec = FakeSEC(tickers=cik_table(CRWV=1769628),
                  subs={"0001769628": submissions("CoreWeave, Inc.", filings, tickers=("CRWV",))})
    sigs, _, _ = await collect(["CRWV"], sec=sec)
    s = sigs["CRWV"]
    assert s.cik == "0001769628"
    assert s.filings_8k_week == 2
    assert s.filings_8k_baseline == (1, 0, 0, 0, 0, 0, 0, 1)
    assert s.items_8k == ("1.01", "2.02", "9.01")
    assert s.foreign_filer is False


@pytest.mark.asyncio
async def test_a_20f_filer_is_foreign_and_a_failed_sec_read_is_missing():
    sec = FakeSEC(tickers=cik_table(TSM=1046179, ASML=937966),
                  subs={"0001046179": submissions("TAIWAN SEMICONDUCTOR", forms_extra=("20-F",)),
                        "0000937966": SECRateLimitException("403")})
    sigs, _, _ = await collect(["TSM", "ASML"], sec=sec)
    assert sigs["TSM"].foreign_filer is True and sigs["TSM"].filings_8k_week == 0
    a = sigs["ASML"]
    assert a.foreign_filer is None and a.filings_8k_week is None and a.filings_8k_baseline == ()
    assert a.news_week is not None   # one source failing never blanks the others


@pytest.mark.asyncio
async def test_a_ticker_sec_does_not_list_has_no_cik_and_no_sec_read():
    sec = FakeSEC(tickers=cik_table(ORCL=1341439))
    sigs, _, sec = await collect(["NEWCO"], sec=sec)
    assert sigs["NEWCO"].cik is None and sigs["NEWCO"].filings_8k_week is None
    assert [c for c in sec.calls if c[0] == "submissions"] == []


@pytest.mark.asyncio
async def test_an_8k_list_at_the_summary_cap_baselines_only_the_weeks_it_covers(monkeypatch):
    monkeypatch.setattr(sec_facts, "RECENT_8K_MAX", 3)
    filings = [("8-K", (TODAY - timedelta(days=d)).isoformat(), "8.01", f"0001769628-26-{d:06d}", "x.htm")
               for d in (0, 8, 16, 30, 40)]
    sec = FakeSEC(tickers=cik_table(CRWV=1769628), subs={"0001769628": submissions("CoreWeave", filings)})
    sigs, _, _ = await collect(["CRWV"], sec=sec)
    # kept: days 0, 8, 16 → oldest day 16 → weeks 0 and 1 fully read (week 2 starts at day 20)
    assert sigs["CRWV"].filings_8k_week == 1 and sigs["CRWV"].filings_8k_baseline == (1,)


# ── earnings ──────────────────────────────────────────────────────────────────────────────────


def calendar_for(rows_by_day):
    return {d.isoformat(): rows for d, rows in rows_by_day.items()}


@pytest.mark.asyncio
async def test_earnings_this_week_comes_from_the_calendar_one_call_per_day():
    cal = calendar_for({TODAY - timedelta(days=2): [{"symbol": "NOW", "date": (TODAY - timedelta(days=2)).isoformat()}],
                        TODAY: [{"symbol": "ibm", "date": TODAY.isoformat()}]})
    fmp = FakeFMP(calendar=cal)
    sigs, fmp, _ = await collect(["NOW", "IBM", "ORCL"], fmp=fmp)
    assert sigs["NOW"].earnings_this_week and sigs["IBM"].earnings_this_week
    assert not sigs["ORCL"].earnings_this_week
    days = sorted(c[1] for c in fmp.calls if c[0] == "calendar")
    assert days == [(TODAY - timedelta(days=6 - i)).isoformat() for i in range(7)]
    assert all(c[1] == c[2] for c in fmp.calls if c[0] == "calendar")


@pytest.mark.asyncio
async def test_a_failed_calendar_day_gives_nobody_the_bonus_but_signals_still_flow(caplog):
    fmp = FakeFMP(calendar={TODAY.isoformat(): FMPRateLimitException("429")})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        sigs, _, _ = await collect(["IBM"], fmp=fmp)
    assert sigs["IBM"].earnings_this_week is False and sigs["IBM"].news_week == 0
    assert any("earnings calendar" in r.getMessage() for r in caplog.records)


# ── collect_signals: shape, logging, no FMP text out ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_come_back_in_input_order_deduped_and_bad_symbols_dropped(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        sigs = await signals.collect_signals(NOW, ["orcl", "MSFT", "ORCL", "", None, "bad symbol!"],
                                             fmp=FakeFMP(), sec=FakeSEC(tickers=cik_table(ORCL=1, MSFT=2)))
    assert [s.ticker for s in sigs] == ["ORCL", "MSFT"]
    assert all(isinstance(s, TickerSignal) for s in sigs)
    summary = [r.getMessage() for r in caplog.records if "FMP calls" in r.getMessage()]
    assert summary and "FMP calls" in summary[-1]


@pytest.mark.asyncio
async def test_concurrency_is_bounded(monkeypatch):
    monkeypatch.setattr(signals, "SIGNAL_CONCURRENCY", 2)
    live = {"now": 0, "peak": 0}

    async def slow(page, limit):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1
        return []

    tickers = [f"T{i}" for i in range(8)]
    fmp = FakeFMP()

    async def news(ticker=None, limit=10, from_date=None, to_date=None, page=0):
        return await slow(page, limit)

    fmp.get_stock_news = news
    await signals.collect_signals(NOW, tickers, fmp=fmp,
                                  sec=FakeSEC(tickers=cik_table(**{t: i + 1 for i, t in enumerate(tickers)})))
    assert live["peak"] <= 2


# ── universe ──────────────────────────────────────────────────────────────────────────────────


class FakeSupabase:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc = rows, exc

    def table(self, name):
        assert name == "trending_themes"
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self.exc:
            raise self.exc
        return SimpleNamespace(data=self.rows)


THEMES = [
    {"slug": "silicon-rush", "title": "Silicon Rush", "tickers": ["nvda", "AMD", "AVGO", "AMD"], "is_active": True},
    {"slug": "powering-machine", "title": "Powering the Machine", "tickers": ["VST", "CEG"], "is_active": True},
    {"slug": "empty", "title": "Empty", "tickers": [], "is_active": True},
]


@pytest.mark.asyncio
async def test_universe_orders_themes_then_owner_then_calendar_and_dedupes(monkeypatch, tmp_path):
    monkeypatch.setattr(signals, "get_supabase", lambda: FakeSupabase(THEMES))
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"version": 1, "tickers": ["CRWV", "NVDA", "orcl"]}))
    day = TODAY - timedelta(days=1)
    cal = calendar_for({day: [
        {"symbol": "BIG", "date": day.isoformat(), "revenueEstimated": 9e9},
        {"symbol": "MID", "date": day.isoformat(), "revenueEstimated": 3e8},
        {"symbol": "SMALL", "date": day.isoformat(), "revenueEstimated": 2e8},     # under the proxy
        {"symbol": "NOEST", "date": day.isoformat(), "revenueEstimated": None, "revenueActual": 1e9},
        {"symbol": "BRK.B", "date": day.isoformat(), "revenueEstimated": 9e10},    # not a plain symbol
        {"symbol": "AMD", "date": day.isoformat(), "revenueEstimated": 7e9},       # already a member
    ]})
    out = await signals.universe(NOW, fmp=FakeFMP(calendar=cal), candidates_path=cands)
    assert out == ["NVDA", "AMD", "AVGO", "VST", "CEG", "CRWV", "ORCL", "BIG", "NOEST", "MID"]


@pytest.mark.asyncio
async def test_universe_is_capped_with_a_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(signals, "UNIVERSE_CAP", 4)
    monkeypatch.setattr(signals, "get_supabase", lambda: FakeSupabase(THEMES))
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"version": 1, "tickers": ["CRWV"]}))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = await signals.universe(NOW, fmp=FakeFMP(), candidates_path=cands)
    assert out == ["NVDA", "AMD", "AVGO", "VST"]
    assert any("CUT" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_failed_calendar_only_drops_the_calendar_names(monkeypatch, tmp_path):
    monkeypatch.setattr(signals, "get_supabase", lambda: FakeSupabase(THEMES[:1]))
    fmp = FakeFMP(calendar={TODAY.isoformat(): FMPUnavailableException("503")})
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"version": 1, "tickers": ["CRWV"]}))
    assert await signals.universe(NOW, fmp=fmp, candidates_path=cands) == ["NVDA", "AMD", "AVGO", "CRWV"]


@pytest.mark.parametrize("sb", [FakeSupabase(exc=RuntimeError("down")), FakeSupabase(rows=None)])
@pytest.mark.asyncio
async def test_an_unreadable_theme_table_is_signals_unavailable(monkeypatch, sb):
    monkeypatch.setattr(signals, "get_supabase", lambda: sb)
    with pytest.raises(signals.SignalsUnavailable):
        await signals.universe(NOW, fmp=FakeFMP())


def test_the_real_candidates_file_loads():
    got = signals.load_learn_candidates()
    assert got[:3] == ("CRWV", "ORCL", "MSFT") and len(got) == 11
    raw = json.loads(signals.CANDIDATES_PATH.read_text())
    assert raw["version"] == 1 and "note" in raw


@pytest.mark.parametrize("content,expected", [
    (None, ()),
    ("{not json", ()),
    (json.dumps({"version": 2, "tickers": ["A"]}), ()),
    (json.dumps({"version": 1, "tickers": "A"}), ()),
    (json.dumps({"version": 1, "tickers": ["A", "", 5, "bad tick", "a", "B.C"]}), ("A", "B.C")),
])
def test_a_bad_candidates_file_never_raises(tmp_path, content, expected):
    path = tmp_path / "c.json"
    if content is not None:
        path.write_text(content)
    assert signals.load_learn_candidates(path) == expected


# ── theme_inputs ──────────────────────────────────────────────────────────────────────────────


def lib_entry(eid, theme, kind="mechanics", verified=True):
    return {"id": eid, "kind": kind, "title": "t", "themes": [theme], "category_fit": ["blueprints"],
            "companies": [], "sic": None, "era": None, "status": None, "llm_eligible": True,
            "facts": [{"id": "f1", "text": "A data centre needs power.",
                       "source": {"title": "10-K", "url": "https://www.sec.gov/x"}}],
            "verified_by": "Owner" if verified else None, "verified_at": "2026-10-20" if verified else None}


@pytest.mark.asyncio
async def test_theme_inputs_marks_eligibility_and_last_covered(monkeypatch, caplog):
    monkeypatch.setattr(signals, "get_supabase", lambda: FakeSupabase(THEMES))
    library = case_library.parse_library({"version": 1, "entries": [
        lib_entry("a", "silicon-rush"), lib_entry("b", "silicon-rush", kind="company"),
        lib_entry("c", "powering-machine"), lib_entry("d", "powering-machine", verified=False),
        lib_entry("e", "powering-machine", kind="case"),
    ]})

    async def pieces(*, statuses=None, kind=None, limit=50):
        assert kind == "deep_dive" and set(statuses) == set(ledger.RECENT_STATUSES)
        return [
            {"period_key": "2026-W40:deep_dive", "scheduled_publish_at": "2026-10-03T03:00:00+00:00",
             "fact_sheet": {"theme_slug": "silicon-rush"}},
            {"period_key": "2026-W41:deep_dive", "scheduled_publish_at": "2026-10-10T03:00:00+00:00",
             "fact_sheet": None, "topic": {"chosen_index": 0, "candidates": [{"kind": "theme", "key": "silicon-rush"}]}},
            {"period_key": "2026-W42:deep_dive", "scheduled_publish_at": "garbage",
             "fact_sheet": {"theme_slug": "powering-machine"}},
        ]

    monkeypatch.setattr(ledger, "list_pieces", pieces)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = await signals.theme_inputs(library=library)
    by = {t.slug: t for t in out}
    assert set(by) == {"silicon-rush", "powering-machine"}          # "empty" has no members
    assert by["silicon-rush"].eligible is True and by["powering-machine"].eligible is False
    assert by["silicon-rush"].members == ("NVDA", "AMD", "AVGO")
    # 2026-10-10T03:00Z is Friday 23:00 ET on 10-09: the ET date, never the UTC one.
    assert by["silicon-rush"].last_covered == "2026-10-09"
    assert by["powering-machine"].last_covered is None
    assert any("unreadable scheduled_publish_at" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("row,slug", [
    ({"fact_sheet": {"theme_slug": " ai-cloud "}}, "ai-cloud"),
    ({"topic": {"chosen_index": 1, "candidates": [{"kind": "theme", "key": "a"}, {"kind": "theme", "key": "b"}]}}, "b"),
    ({"topic": {"chosen_index": 0, "candidates": [{"kind": "ticker", "key": "CRWV"}]}}, None),
    ({"topic": {"chosen_index": None, "candidates": [{"kind": "theme", "key": "a"}]}}, None),
    ({"topic": {"chosen_index": True, "candidates": [{"kind": "theme", "key": "a"}]}}, None),
    ({"topic": {"chosen_index": 5, "candidates": [{"kind": "theme", "key": "a"}]}}, None),
    ({"topic": "junk"}, None),
    ({}, None),
])
def test_theme_of_piece(row, slug):
    assert signals.theme_of_piece(row) == slug


# ── the thin client: get_company_tickers ──────────────────────────────────────────────────────


async def _run_client(monkeypatch, respond):
    reqs = []

    def handler(request):
        reqs.append(request)
        return respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        try:
            return await SECEdgarClient().get_company_tickers(), reqs
        except Exception as e:
            return e, reqs


@pytest.mark.asyncio
async def test_get_company_tickers_reads_secs_file_with_the_user_agent(monkeypatch):
    body = cik_table(CRWV=1769628)
    out, reqs = await _run_client(monkeypatch, lambda r: httpx.Response(200, json=body))
    assert out == body
    assert str(reqs[0].url) == COMPANY_TICKERS_URL == "https://www.sec.gov/files/company_tickers.json"
    assert reqs[0].headers["user-agent"] == UA


@pytest.mark.asyncio
async def test_get_company_tickers_outcomes(monkeypatch):
    out, _ = await _run_client(monkeypatch, lambda r: httpx.Response(404, text="nope"))
    assert out is None
    for bad in (httpx.Response(200, text="<html>"), httpx.Response(200, json=[1, 2]),
                httpx.Response(503, text="down"), httpx.Response(200, content=b"\xff\xfe{")):
        out, _ = await _run_client(monkeypatch, lambda r, bad=bad: bad)
        assert isinstance(out, SECUnavailableException), out


@pytest.mark.asyncio
async def test_get_company_tickers_throttle_opens_the_shared_circuit(monkeypatch):
    out, reqs = await _run_client(monkeypatch, lambda r: httpx.Response(429, text="slow down"))
    assert isinstance(out, SECRateLimitException) and len(reqs) == 1
    out, reqs = await _run_client(monkeypatch, lambda r: httpx.Response(200, json={}))
    assert isinstance(out, SECRateLimitException) and reqs == []   # no request while open


@pytest.mark.asyncio
async def test_get_company_tickers_refuses_an_oversized_body(monkeypatch):
    big = httpx.Response(200, headers={"content-length": str(MAX_COMPANY_TICKERS_BYTES + 1)}, content=b"{}")
    out, _ = await _run_client(monkeypatch, lambda r: big)
    assert isinstance(out, SECEdgarDocumentTooLargeException)
    monkeypatch.setattr(sec_edgar, "MAX_COMPANY_TICKERS_BYTES", 10)
    out, _ = await _run_client(monkeypatch, lambda r: httpx.Response(200, content=b'{"0": {"a": 123456789}}'))
    assert isinstance(out, SECEdgarDocumentTooLargeException)


@pytest.mark.asyncio
async def test_get_company_tickers_without_a_user_agent_makes_no_request(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    out, reqs = await _run_client(monkeypatch, lambda r: httpx.Response(200, json={}))
    assert isinstance(out, SECConfigException) and reqs == []
