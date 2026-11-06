"""The live-earnings fast path: the company's own 8-K Item 2.02 release, read from SEC.

The one property everything else rests on: A FIGURE THE RELEASE DOES NOT STATE CANNOT REACH
A USER. Gemini returns each figure with a quote; a figure survives only when its quote is a
verbatim span of the release AND the number read back out of that quote is the value. Below,
hallucinated numbers, paraphrased quotes, a wrong scale word, a flipped sign and a foreign
currency are each refused.

The SEC side is pinned too, because its limiter and 600 s throttle circuit are SHARED with
Learn, the buyback check and the Trillion stake finder: the cap on a round, the release-window
cadence, the stop once a filing is found, one extraction per accession, and parking every
symbol on a throttle.

So is the round's timing, because the card reads the record while a round runs: a figure is
handed over the moment it verifies, every symbol of a round comes due again a minute after that
round started, and a filing waiting on its next attempt rests a cadence from the end of the last
round that spent one.
Hermetic: fake SEC client and fake Gemini; `asyncio.sleep` is injected.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.learn_weekly.signals as signals
from app.config import settings
from app.integrations.sec_edgar import SECException, SECRateLimitException
from app.utils.market_hours import ET

REPORT = date(2026, 10, 29)
ACC = "0000050863-26-000123"

RELEASE = (
    "SANTA CLARA, Calif., Oct. 29, 2026 -- Intel Corporation today reported third-quarter "
    "2026 financial results. Third-quarter revenue was $17.1 billion, up 25% year-over-year. "
    "Third-quarter GAAP earnings per share (EPS) attributable to Intel was a loss of $(0.12); "
    "non-GAAP EPS attributable to Intel was $0.44. For the fourth quarter of 2026, Intel "
    "expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48."
)


def _q(start: str, length: int = 0) -> str:
    """A verbatim span of RELEASE that starts at `start` and is at least 40 characters."""
    i = RELEASE.index(start)
    end = i + max(len(start), length or 0, 40)
    return RELEASE[i:min(end, len(RELEASE))]


def _good_answer(**overrides) -> Dict[str, Any]:
    answer = {
        "is_quarterly_results": True,
        "fiscal_period": "Q3 2026",
        "currency": "USD",
        "revenue": {"value": 17.1, "unit": "billion",
                    "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."},
        "eps_adjusted_diluted": {"value": 0.44,
                                 "quote": "non-GAAP EPS attributable to Intel was $0.44."},
        "eps_gaap_diluted": {"value": -0.12,
                             "quote": "attributable to Intel was a loss of $(0.12);"},
        "guidance": {
            "text": "Intel expects fourth-quarter revenue of $17.5 billion to $18.5 billion.",
            "quote": "Intel expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48.",
        },
    }
    answer.update(overrides)
    return answer


def _verify(answer):
    return efs.verify_extraction(answer, RELEASE, accession=ACC, accepted_at=None, url="u")


# ── verification ─────────────────────────────────────────────────────────────


def test_a_correct_answer_survives_whole():
    figures, reasons = _verify(_good_answer())
    assert (figures.revenue, figures.eps_adjusted, figures.eps_gaap) == (17.1e9, 0.44, -0.12)
    assert figures.guidance.startswith("Intel expects")
    assert set(reasons.values()) == {"ok"}


def test_a_hallucinated_number_with_a_real_quote_is_refused():
    answer = _good_answer(revenue={"value": 17.4, "unit": "billion",
                                   "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "value_mismatch"


def test_a_paraphrased_quote_is_refused_even_with_the_right_number():
    answer = _good_answer(revenue={"value": 17.1, "unit": "billion",
                                   "quote": "Revenue for the third quarter came in at $17.1 billion overall."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "not_verbatim"


def test_a_scale_word_the_quote_does_not_print_is_refused():
    """The scale is what a model most often gets wrong — a 1,000x error is a fabricated miss."""
    answer = _good_answer(revenue={"value": 17.1, "unit": "million",
                                   "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "value_mismatch"


def test_a_flipped_sign_is_refused():
    answer = _good_answer(eps_gaap_diluted={"value": 0.12,
                                            "quote": "attributable to Intel was a loss of $(0.12);"})
    figures, reasons = _verify(answer)
    assert figures.eps_gaap is None and reasons["eps_gaap_diluted"] == "value_mismatch"


def test_a_loss_must_read_as_a_loss_in_its_quote():
    assert efs.eps_from_quote("non-GAAP EPS attributable to Intel was $0.44.", -0.44) is None
    assert efs.eps_from_quote("a net loss of $0.46 per diluted share", -0.46) == -0.46
    assert efs.eps_from_quote("diluted EPS of $(0.46)", -0.46) == -0.46


def test_a_quote_that_cuts_a_number_is_refused():
    """`verify_peg_span` refuses "$1" quoted out of "$17.1 billion"."""
    quote = "Third-quarter revenue was $1"
    assert not efs.verify_peg_span(quote + " " * 20, RELEASE)


def test_guidance_with_a_number_its_quote_does_not_print_is_refused():
    answer = _good_answer(guidance={
        "text": "Intel expects fourth-quarter revenue of about $19 billion.",
        "quote": "Intel expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48.",
    })
    figures, reasons = _verify(answer)
    assert figures.guidance is None and reasons["guidance"] == "value_mismatch"


def test_a_foreign_currency_release_is_dropped_whole():
    figures, reasons = _verify(_good_answer(currency="EUR"))
    assert figures is None and reasons == {"answer": "currency_eur"}


def test_a_document_that_is_not_a_results_release_yields_nothing():
    figures, reasons = _verify({"is_quarterly_results": False})
    assert figures is None and reasons == {"answer": "not_results"}


def test_an_off_schema_answer_yields_nothing():
    assert _verify("not a dict") == (None, {"answer": "off_schema"})
    assert efs.unwrap_json({"text": "{not json"}) is None
    assert efs.unwrap_json({"text": json.dumps({"a": 1})}) == {"a": 1}


def test_a_short_suffix_scale_is_read():
    assert efs.revenue_from_quote("revenue of $13.7B in the quarter, up 3% from a year ago", 13.7,
                                  "billion") == 13.7e9


# ── the SEC record ───────────────────────────────────────────────────────────


def _submissions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cols = {k: [] for k in ("accessionNumber", "filingDate", "form", "items", "primaryDocument",
                            "acceptanceDateTime")}
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k, ""))
    return {"filings": {"recent": cols}}


_8K = {"accessionNumber": ACC, "filingDate": "2026-10-29", "form": "8-K", "items": "2.02,9.01",
       "primaryDocument": "intc-20261029.htm", "acceptanceDateTime": "2026-10-29T16:02:11.000Z"}


def test_an_item_202_8k_on_the_report_date_is_found_with_its_et_acceptance_time():
    (f,) = efs.find_results_filings(_submissions([_8K]), REPORT)
    assert f.accession == ACC
    assert f.accepted_at.astimezone(ET).strftime("%H:%M") == "16:02"


def test_a_late_evening_filing_dated_the_next_business_day_still_counts():
    late = dict(_8K, filingDate="2026-10-30")
    assert efs.find_results_filings(_submissions([late]), REPORT)


@pytest.mark.parametrize("change", [
    {"form": "10-Q"}, {"items": "7.01,9.01"}, {"filingDate": "2026-10-27"}, {"form": "8-K/A"},
])
def test_other_filings_are_not_results(change):
    assert efs.find_results_filings(_submissions([dict(_8K, **change)]), REPORT) == []


def test_columns_of_different_lengths_read_as_no_filing():
    data = _submissions([_8K])
    data["filings"]["recent"]["items"].append("2.02")
    assert efs.find_results_filings(data, REPORT) == []


_INDEX = """<table class="tableFile">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/50863/000005086326000123/intc-20261029.htm">intc-20261029.htm</a></td><td>8-K</td><td>30 KB</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/50863/000005086326000123/q3-26earnings.htm">q3-26earnings.htm</a></td><td>EX-99.1</td><td>90 KB</td></tr>
</table>"""


def test_the_ex991_document_is_located_from_the_index_page():
    assert efs.find_ex99_document(_INDEX) == "q3-26earnings.htm"
    assert efs.find_ex99_document("<html>no table</html>") is None


@pytest.mark.parametrize("hm,expected", [
    ((5, 59), None), ((6, 0), 60), ((9, 34), 60), ((12, 0), 300), ((16, 5), 60),
    ((18, 30), 300), ((22, 0), None),
])
def test_the_poll_cadence_follows_the_release_windows(hm, expected):
    now = datetime(2026, 10, 29, *hm, tzinfo=ET)
    assert efs.poll_interval_seconds(now) == expected


# ── the watcher ──────────────────────────────────────────────────────────────


class _SEC:
    def __init__(self, *, filings: Optional[Dict[str, List[dict]]] = None,
                 throttle_on: Optional[str] = None):
        self.filings = filings or {}
        self.throttle_on = throttle_on
        self.calls: List[str] = []

    async def get_submissions(self, cik):
        self.calls.append(f"sub:{cik}")
        if self.throttle_on == cik:
            raise SECRateLimitException("429", status_code=429, retry_after=120.0)
        return _submissions(self.filings.get(cik, []))

    async def get_filing_document(self, cik, acc, document):
        self.calls.append(f"doc:{document}")
        if document.endswith("-index.htm"):
            return _INDEX
        return f"<html><body><p>{RELEASE}</p></body></html>"


class _Gemini:
    def __init__(self, answer=None, fail: int = 0):
        self.answer = answer if answer is not None else _good_answer()
        self.fail = fail
        self.calls = 0
        self.kwargs: Dict[str, Any] = {}

    async def generate_json(self, prompt, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("gemini 503")
        return {"text": json.dumps(self.answer), "model": "m", "tokens_used": 10}


async def _no_sleep(_s):
    return None


@pytest.fixture
def watcher(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test@example.com")
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", True)
    w = efs.EarningsFilingWatcher()
    return w


_CIKS = {"INTC": "0000050863", "AAPL": "0000320193", "MSFT": "0000789019"}
_AT_16 = datetime(2026, 10, 29, 16, 3, tzinfo=ET).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_a_filing_is_found_extracted_once_and_then_never_polled_again(watcher):
    sec = _SEC(filings={"0000050863": [_8K]})
    gem = _Gemini()
    found = await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert found["INTC"].revenue == 17.1e9 and found["INTC"].eps_adjusted == 0.44
    assert gem.calls == 1
    assert gem.kwargs["temperature"] == 0 and gem.kwargs["cache"] is False
    later = datetime(2026, 10, 29, 16, 30, tzinfo=ET).astimezone(timezone.utc)
    again = await watcher.run_once(later, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert again == {} and gem.calls == 1
    assert sec.calls.count("sub:0000050863") == 1, "found → stop polling"


@pytest.mark.asyncio
async def test_no_filing_yet_is_checked_again_only_after_the_window_cadence(watcher):
    sec = _SEC()
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == ["sub:0000050863"], "inside the 60 s cadence nothing is re-read"


@pytest.mark.asyncio
async def test_a_round_keeps_the_most_watched_first(watcher, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_SEC_MAX_SYMBOLS", 2)
    sec = _SEC()
    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193", "sub:0000789019"]


@pytest.mark.asyncio
async def test_a_ticker_between_its_reads_keeps_its_slot_from_the_next_one_down(
        watcher, monkeypatch, clock):
    """The cap comes before the cadence. Cut after it, a ticker waiting out its 5 min would hand
    its slot down the list, and rounds a minute apart would read the tickers the cap keeps off
    the circuit SEC shares with Learn and the stake finder."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_SEC_MAX_SYMBOLS", 1)
    between_the_windows = datetime(2026, 10, 29, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    sec = _SEC()
    for minute in range(4):
        clock.at += 60
        await watcher.run_once(between_the_windows + timedelta(minutes=minute),
                               ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                               sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193"]


@pytest.mark.asyncio
async def test_a_report_off_the_pending_list_frees_its_slot_for_the_next(watcher, monkeypatch):
    """A load limit, not a daily budget: `pending_symbols` drops a report once its figures are
    in, and the cap applies to what is left. So a day can read more tickers than the cap."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_SEC_MAX_SYMBOLS", 1)
    sec = _SEC()
    for pending in (["AAPL", "MSFT"], ["MSFT"]):
        await watcher.run_once(_AT_16, pending, REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193", "sub:0000789019"]


@pytest.mark.asyncio
async def test_a_budget_of_zero_reads_nothing_from_sec(watcher, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_SEC_MAX_SYMBOLS", 0)
    sec = _SEC()
    assert await watcher.run_once(_AT_16, ["AAPL", "MSFT"], REPORT, sec=sec, cik_map=_CIKS,
                                  sleep=_no_sleep) == {}
    assert sec.calls == []


@pytest.mark.asyncio
async def test_a_throttle_parks_every_symbol_and_stops_the_round(watcher):
    sec = _SEC(throttle_on="0000320193")
    await watcher.run_once(_AT_16, ["AAPL", "MSFT"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193"], "MSFT is never asked after the throttle"
    later = datetime(2026, 10, 29, 16, 5, tzinfo=ET).astimezone(timezone.utc)
    assert await watcher.run_once(later, ["MSFT"], REPORT, sec=sec, cik_map=_CIKS,
                                  sleep=_no_sleep) == {}
    assert sec.calls == ["sub:0000320193"], "parked for SEC's Retry-After"


@pytest.mark.asyncio
async def test_our_calls_are_spaced_never_burst(watcher):
    slept = []

    async def _sleep(s):
        slept.append(s)

    sec = _SEC()
    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_sleep)
    assert slept == [efs._CALL_SPACING_SECONDS] * 2


@pytest.mark.asyncio
async def test_a_gemini_outage_is_retried_but_a_verification_miss_is_final(watcher):
    sec = _SEC(filings={"0000050863": [_8K]})
    gem = _Gemini(fail=1)
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem,
                                  cik_map=_CIKS, sleep=_no_sleep) == {}
    later = datetime(2026, 10, 29, 16, 5, tzinfo=ET).astimezone(timezone.utc)
    watcher._watch[("INTC", REPORT)].checked_mono = 0.0      # the 60 s cadence has passed
    found = await watcher.run_once(later, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert found["INTC"].eps_adjusted == 0.44 and gem.calls == 2

    w2 = efs.EarningsFilingWatcher()
    bad = _Gemini(answer={"is_quarterly_results": False})
    sec2 = _SEC(filings={"0000050863": [_8K]})
    assert await w2.run_once(_AT_16, ["INTC"], REPORT, sec=sec2, gemini=bad, cik_map=_CIKS,
                             sleep=_no_sleep) == {}
    w2._watch[("INTC", REPORT)].checked_mono = 0.0
    assert await w2.run_once(later, ["INTC"], REPORT, sec=sec2, gemini=bad, cik_map=_CIKS,
                             sleep=_no_sleep) == {}
    assert bad.calls == 1, "a verification outcome is final for that accession"


@pytest.mark.asyncio
async def test_a_ticker_sec_does_not_list_falls_back_to_fmp_without_a_call(watcher):
    sec = _SEC()
    await watcher.run_once(_AT_16, ["ASML"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_watcher_is_inert_without_a_user_agent(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    sec = _SEC()
    w = efs.EarningsFilingWatcher()
    assert await w.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS,
                            sleep=_no_sleep) == {}
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_watcher_is_off_with_its_flag(watcher, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", False)
    sec = _SEC()
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS,
                                  sleep=_no_sleep) == {}
    assert sec.calls == []


@pytest.mark.asyncio
async def test_nothing_is_read_outside_edgar_hours(watcher):
    sec = _SEC()
    night = datetime(2026, 10, 29, 23, 0, tzinfo=ET).astimezone(timezone.utc)
    await watcher.run_once(night, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == []


# ── the round's timing ───────────────────────────────────────────────────────


class _Clock:
    """A `time` stand-in for the watcher alone: asyncio keeps its own clock."""

    def __init__(self):
        self.at = 1_000_000.0

    def monotonic(self) -> float:
        return self.at


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    held = _Clock()
    monkeypatch.setattr(efs, "time", SimpleNamespace(monotonic=held.monotonic))
    return held


def _later_filing(cik: str) -> dict:
    return dict(_8K, accessionNumber=f"{cik}-26-000001")


@pytest.mark.asyncio
async def test_each_figure_is_delivered_the_moment_it_verifies(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    delivered: List[str] = []
    seen: List[List[str]] = []

    class _Gem(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            if self.calls == 1:
                seen.append(list(delivered))
            return await super().generate_json(prompt, **kwargs)

    found = await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Gem(),
                                   cik_map=_CIKS, sleep=_no_sleep,
                                   on_figures=lambda sym, fig: delivered.append(sym))
    assert seen == [["INTC"]], "the second read starts with the first figure already out"
    assert delivered == ["INTC", "AAPL"]
    assert set(found) == {"INTC", "AAPL"}, "the round still reports what it found"


@pytest.mark.asyncio
async def test_a_delivery_that_raises_stays_inside_the_round_and_says_which_report(watcher,
                                                                                  caplog):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    gem = _Gemini()
    calls: List[str] = []

    def _raises(symbol, figures):
        calls.append(symbol)
        raise ValueError("record refused")

    with caplog.at_level(logging.WARNING, logger=efs.__name__):
        found = await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                                       cik_map=_CIKS, sleep=_no_sleep, on_figures=_raises)
    assert calls == ["INTC", "AAPL"], "one bad delivery must not cost the next symbol its read"
    assert set(found) == {"INTC", "AAPL"} and gem.calls == 2
    rows = [r for r in caplog.records if "not delivered" in r.getMessage()]
    assert len(rows) == 2 and rows[0].exc_info is not None
    assert "INTC" in rows[0].getMessage() and REPORT.isoformat() in rows[0].getMessage()
    assert "ValueError" in rows[0].getMessage() and "record refused" in rows[0].getMessage()


@pytest.mark.asyncio
async def test_a_failed_read_delivers_nothing_and_a_later_one_delivers_once(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    gem = _Gemini(fail=1)
    delivered: List[str] = []
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                                  sleep=_no_sleep,
                                  on_figures=lambda sym, fig: delivered.append(sym)) == {}
    assert delivered == []
    watcher._watch[("INTC", REPORT)].checked_mono = 0.0      # the 60 s cadence has passed
    for _ in range(2):
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep,
                               on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == ["INTC"]


@pytest.mark.asyncio
async def test_a_refused_release_delivers_nothing(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    delivered: List[str] = []
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec,
                           gemini=_Gemini(answer={"is_quarterly_results": False}), cik_map=_CIKS,
                           sleep=_no_sleep, on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == []


@pytest.mark.asyncio
async def test_a_second_ticker_of_one_filing_is_delivered_without_a_second_extraction(watcher):
    """GOOGL and GOOG are one issuer, so one accession: the second ticker's figures come from
    the watcher's own memory, and must still reach its record."""
    ciks = {"GOOGL": "0001652044", "GOOG": "0001652044"}
    sec = _SEC(filings={"0001652044": [_8K]})
    gem = _Gemini()
    delivered: List[str] = []
    await watcher.run_once(_AT_16, ["GOOGL", "GOOG"], REPORT, sec=sec, gemini=gem, cik_map=ciks,
                           sleep=_no_sleep, on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == ["GOOGL", "GOOG"] and gem.calls == 1


@pytest.mark.asyncio
async def test_every_symbol_in_a_round_shares_the_round_start_stamp(watcher, clock):
    async def _spaced(seconds):
        clock.at += seconds

    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=_SEC(), cik_map=_CIKS,
                           sleep=_spaced)
    assert {w.checked_mono for w in watcher._watch.values()} == {1_000_000.0}


@pytest.mark.asyncio
async def test_a_quiet_round_reads_every_symbol_again_a_minute_after_it_started(watcher, clock):
    """Rounds start a minute apart while the reads inside one are spaced out. A stamp per read
    left all but the first symbol a few seconds short at the next round, so each was read
    every other minute."""
    async def _spaced(seconds):
        clock.at += seconds

    sec = _SEC()
    for _ in range(3):
        started = clock.at
        await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                               sleep=_spaced)
        clock.at = started + 60
    assert [sec.calls.count(f"sub:{cik}") for cik in _CIKS.values()] == [3, 3, 3]


@pytest.mark.parametrize("hm,cadence", [((16, 3), 60), ((12, 0), 300)], ids=["fast", "slow"])
def test_a_round_a_moment_early_still_reads_the_symbol(watcher, hm, cadence):
    """A round that wakes a few seconds short must not cost the symbol a whole cadence."""
    now_et = datetime(2026, 10, 29, *hm, tzinfo=ET)
    key = ("INTC", REPORT)
    watcher._watch[key] = efs._Watch(checked_mono=1000.0)
    on_time = 1000.0 + cadence - efs._DUE_SLACK_SECONDS
    assert watcher._due(key, now_et, on_time)
    assert not watcher._due(key, now_et, on_time - 0.001)


@pytest.mark.asyncio
async def test_a_symbol_a_throttle_left_unread_keeps_its_place(watcher, clock):
    watcher._watch[("MSFT", REPORT)] = efs._Watch(checked_mono=999_000.0)
    await watcher.run_once(_AT_16, ["AAPL", "MSFT"], REPORT, sec=_SEC(throttle_on=_CIKS["AAPL"]),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("MSFT", REPORT)].checked_mono == 999_000.0, \
        "it was never read, so the park must not count as a read"


@pytest.mark.asyncio
async def test_a_failed_read_waits_its_cadence_from_where_it_ended(watcher, clock):
    """A stalled model ends a round minutes late and the next one starts a second after. Its
    attempts must go no faster for that: a filing out of attempts is never read again."""
    class _Stalls(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            clock.at += 90
            return await super().generate_json(prompt, **kwargs)

    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    gem = _Stalls(fail=9)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep)

    await _round()
    clock.at += 1
    await _round()
    assert gem.calls == 1, "a second after the stall ended is no new attempt"
    clock.at += 60
    await _round()
    assert gem.calls == 2


@pytest.mark.asyncio
async def test_a_stalled_read_never_delays_the_symbols_behind_it_past_their_own_cadence(watcher,
                                                                                       clock):
    """Only the failed read moves its stamp: its neighbours stay on the round's."""
    class _Stalls(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            clock.at += 90
            return await super().generate_json(prompt, **kwargs)

    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    started = clock.at
    await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Stalls(fail=9),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("AAPL", REPORT)].checked_mono == started
    assert watcher._watch[("INTC", REPORT)].checked_mono == clock.at


class _Model(_Gemini):
    """Takes 90 s on the stand-in clock and fails until the clock reaches `until`, then answers.
    `started` keeps every call as (symbol, when it began)."""

    def __init__(self, clock: _Clock, *, until: float = float("inf")):
        super().__init__()
        self.clock, self.until = clock, until
        self.started: List[Tuple[str, float]] = []

    async def generate_json(self, prompt, **kwargs):
        self.started.append((prompt.split("ticker: ")[1].split(".")[0], self.clock.at))
        if self.clock.at < self.until:
            self.clock.at += 90
            raise RuntimeError("gemini timed out")
        return await super().generate_json(prompt, **kwargs)

    def of(self, symbol: str) -> List[float]:
        return [at for sym, at in self.started if sym == symbol]


def _both_filed() -> Dict[str, List[dict]]:
    return {_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]}


@pytest.mark.asyncio
async def test_every_failed_read_of_a_round_waits_its_cadence_from_the_rounds_end(watcher, clock):
    """Two filings stall one after the other, so the first ended 90 s before the round did and
    the next round starts a second behind it. Its own end would leave that first filing due at
    once, and a filing out of attempts is never read again."""
    sec, gem = _SEC(filings=_both_filed()), _Model(clock)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                               cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    ended = clock.at
    assert gem.of("INTC") == [1_000_000.0] and gem.of("AAPL") == [1_000_090.0]
    assert {w.checked_mono for w in watcher._watch.values()} == {ended}

    clock.at = ended + 1
    await _round()
    assert len(gem.started) == 2, "nothing starts right behind the stall"
    clock.at = ended + 60 - efs._DUE_SLACK_SECONDS - 0.001
    await _round()
    assert len(gem.started) == 2, "a moment short of the cadence and its slack"
    clock.at = ended + 60
    await _round()
    assert [sym for sym, _ in gem.started[2:]] == ["INTC", "AAPL"]


@pytest.mark.asyncio
async def test_a_filing_that_failed_earlier_rests_after_a_later_round_that_spent_an_attempt(
        watcher, clock):
    """Filings rarely land together. Counted from their own rounds, two that landed apart take
    turns, each round right behind the other, and neither ever gets a minute of rest."""
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                               cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    clock.at += 1
    sec.filings[_CIKS["AAPL"]] = [_later_filing(_CIKS["AAPL"])]
    await _round()
    ended = clock.at
    assert [sym for sym, _ in gem.started] == ["INTC", "AAPL"]
    clock.at = ended + 1
    await _round()
    assert len(gem.started) == 2, "INTC failed 92 s ago, but the model failed 1 s ago"
    clock.at = ended + 60
    await _round()
    assert [sym for sym, _ in gem.started[2:]] == ["INTC", "AAPL"]


@pytest.mark.asyncio
async def test_a_round_that_spent_no_attempt_holds_no_retry_back(watcher, clock):
    """The rest is for a failing model. Once it answers, a waiting filing stays on its time."""
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)

    async def _round() -> Dict[str, efs.FilingFigures]:
        return await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                                      cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    failed = clock.at
    gem.until = clock.at
    clock.at += 1
    sec.filings[_CIKS["AAPL"]] = [_later_filing(_CIKS["AAPL"])]
    assert set(await _round()) == {"AAPL"}
    assert watcher._watch[("INTC", REPORT)].checked_mono == failed
    clock.at = failed + 60
    assert set(await _round()) == {"INTC"}


@pytest.mark.asyncio
async def test_a_read_that_spent_no_attempt_keeps_the_rounds_start(watcher, clock):
    """Only what spent an attempt moves: the round's quiet symbols stay on the minute, and a
    filing that verified is done either way."""
    class _SecDown(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["MSFT"]:
                raise SECException("SEC 503")
            return await super().get_submissions(cik)

    started = clock.at
    sec = _SecDown(filings={_CIKS["INTC"]: [_8K]})
    await watcher.run_once(_AT_16, ["INTC", "AAPL", "MSFT"], REPORT, sec=sec, gemini=_Model(clock),
                           cik_map=_CIKS, sleep=_no_sleep)
    stamps = {key[0]: w.checked_mono for key, w in watcher._watch.items()}
    assert stamps == {"INTC": started + 90, "AAPL": started, "MSFT": started}


@pytest.mark.asyncio
async def test_a_throttle_that_ends_a_round_early_still_moves_its_failed_reads(watcher, clock):
    """The park can be shorter than the cadence, and the last stall still counts."""
    class _ThrottledLate(_SEC):
        async def get_submissions(self, cik):
            if cik == self.throttle_on:
                clock.at += 30
            return await super().get_submissions(cik)

    sec = _ThrottledLate(filings={_CIKS["INTC"]: [_8K]}, throttle_on=_CIKS["AAPL"])
    await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Model(clock),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("INTC", REPORT)].checked_mono == clock.at


@pytest.mark.asyncio
async def test_a_round_cut_off_keeps_a_failed_read_on_its_own_end(watcher, clock):
    """The round's end never comes, and a cancel from outside leaves the loop running: without
    its own stamp the failed read would count from the round's start and go straight again."""
    reached = asyncio.Event()

    class _Hangs(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                reached.set()
                await asyncio.Event().wait()
            return await super().get_submissions(cik)

    sec = _Hangs(filings={_CIKS["INTC"]: [_8K]})
    round_ = asyncio.create_task(
        watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Model(clock),
                         cik_map=_CIKS, sleep=_no_sleep))
    await asyncio.wait_for(reached.wait(), 2.0)
    round_.cancel()
    with pytest.raises(asyncio.CancelledError):
        await round_
    assert watcher._watch[("INTC", REPORT)].checked_mono == 1_000_090.0


@pytest.mark.asyncio
async def test_a_filing_out_of_attempts_is_never_read_again(watcher, clock):
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)
    for _ in range(5):
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep)
        clock.at += 120
    assert len(gem.started) == efs._MAX_EXTRACT_ATTEMPTS
    assert watcher.figures_for("INTC", REPORT) is None


# ── the filing tick, on the real watcher ─────────────────────────────────────


def _pending(symbol: str) -> dict:
    return {"symbol": symbol, "date": REPORT.isoformat(), "epsActual": None, "epsEstimated": 0.41,
            "revenueActual": None, "revenueEstimated": 16.6e9,
            "lastUpdated": REPORT.isoformat()}


@pytest.fixture
def live(watcher, monkeypatch):
    """The real live service over the real watcher, with SEC and Gemini faked."""
    svc = els.get_earnings_live_service()
    svc.reset()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    monkeypatch.setattr(efs, "_CALL_SPACING_SECONDS", 0)

    async def _map(*, sec=None):
        return _CIKS

    # `run_once` imports this inside the function, so patch the SOURCE module.
    monkeypatch.setattr(signals, "ticker_cik_map", _map)
    yield svc
    svc.reset()


@pytest.mark.asyncio
async def test_a_figure_is_on_the_record_before_the_next_symbol_is_read(live):
    """The owner's rule: what comes out first is shown right away, not when the round ends."""
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    shown: List[Optional[float]] = []

    class _Reads(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                shown.append(live.snapshot("INTC", REPORT, _AT_16).revenue.actual)
            return await super().get_submissions(cik)

    sec = _Reads(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    stats = await els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec,
                                           gemini=_Gemini())
    assert shown == [17.1e9]
    assert stats["filings"] == 2
    assert live.snapshot("AAPL", REPORT, _AT_16).revenue.actual == 17.1e9


@pytest.mark.asyncio
async def test_a_round_cut_off_at_shutdown_keeps_the_figures_it_already_verified(live):
    """Cancellation lands mid-round on a redeploy: what was read must not go with it."""
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    reached = asyncio.Event()

    class _Hangs(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                reached.set()
                await asyncio.Event().wait()
            return await super().get_submissions(cik)

    sec = _Hangs(filings={_CIKS["INTC"]: [_8K]})
    tick = asyncio.create_task(
        els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec, gemini=_Gemini()))
    await asyncio.wait_for(reached.wait(), 2.0)
    tick.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tick
    assert live.snapshot("INTC", REPORT, _AT_16).revenue.actual == 17.1e9


@pytest.mark.asyncio
async def test_a_figure_the_round_could_not_store_is_logged_and_the_round_goes_on(live, caplog,
                                                                                 monkeypatch):
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    real = live.absorb_filing

    def _absorb(symbol, day, figures, now):
        if symbol == "INTC":
            raise RuntimeError("record refused")
        real(symbol, day, figures, now)

    monkeypatch.setattr(live, "absorb_filing", _absorb)
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    with caplog.at_level(logging.WARNING, logger=efs.__name__):
        await els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec,
                                       gemini=_Gemini())
    assert any("not delivered" in r.getMessage() and "INTC" in r.getMessage()
               for r in caplog.records)
    assert live.snapshot("AAPL", REPORT, _AT_16).revenue.actual == 17.1e9
    assert live.snapshot("INTC", REPORT, _AT_16).revenue.actual is None


# ── a stalled model on the real loop ─────────────────────────────────────────


class _StopLoop(BaseException):
    """Ends `_forever`, which outlives every `Exception`."""


async def _filing_loop_runs(monkeypatch, clock: _Clock, seconds: float, *, symbols: List[str],
                            sec: _SEC, gemini: _Model) -> List[float]:
    """The real `_forever` over the real filing tick, on the stand-in clock, for `seconds`.
    Returns when each round that called the model ended."""
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    stop_at = clock.at + seconds
    ended: List[float] = []

    async def earnings_filing_tick(_now):
        called = len(gemini.started)
        await els.earnings_filing_tick(_AT_16, watched=symbols, sec=sec, gemini=gemini)
        if len(gemini.started) > called:
            ended.append(clock.at)

    async def _rest(wait):
        clock.at += wait
        if clock.at >= stop_at:
            raise _StopLoop

    with pytest.raises(_StopLoop):
        await els._forever(earnings_filing_tick, 60, sleep=_rest)
    return ended


class _FiledOnTheClock(_SEC):
    """A filing is on the record from its own moment: `landing` maps a symbol to seconds after
    the start."""

    def __init__(self, clock: _Clock, landing: Dict[str, float]):
        super().__init__()
        self.clock, self.start = clock, clock.at
        self.landing = {_CIKS[symbol]: at for symbol, at in landing.items()}

    async def get_submissions(self, cik):
        if self.clock.at - self.start >= self.landing.get(cik, float("inf")):
            self.filings[cik] = [_later_filing(cik)]
        return await super().get_submissions(cik)


@pytest.mark.asyncio
@pytest.mark.parametrize("landing", [
    {"INTC": 0, "AAPL": 0, "MSFT": 0},
    {"INTC": 0, "AAPL": 100, "MSFT": 0},
    {"INTC": 0, "AAPL": 100, "MSFT": 200},
    {"INTC": 0, "AAPL": 300, "MSFT": 550},
], ids=["together", "two_then_one", "a_round_apart", "minutes_apart"])
async def test_a_retry_never_starts_within_a_minute_of_a_round_that_spent_an_attempt(
        live, clock, monkeypatch, landing):
    """The loop starts the next round a second behind a long one, so the rest between attempts
    has to come from the stamps, for every filing and however they landed."""
    live.observe_rows(REPORT, [_pending(s) for s in landing], _AT_16)
    gem = _Model(clock)
    ended = await _filing_loop_runs(monkeypatch, clock, 3000, symbols=list(landing),
                                    sec=_FiledOnTheClock(clock, landing), gemini=gem)
    for symbol in landing:
        reads = gem.of(symbol)
        assert len(reads) == efs._MAX_EXTRACT_ATTEMPTS, symbol
        for retry in reads[1:]:
            rests = [retry - at for at in ended if at <= retry]
            assert min(rests) >= 60, (symbol, reads, ended)


@pytest.mark.asyncio
@pytest.mark.parametrize("stall,landing", [
    (480, {"INTC": 0, "AAPL": 0}),
    (660, {"INTC": 0, "AAPL": 0, "MSFT": 0}),
    (480, {"AAPL": 50, "INTC": 0}),
], ids=["two_filings", "three_filings", "two_filings_in_rounds_of_their_own"])
async def test_a_stall_the_filings_can_outlast_loses_none(live, clock, monkeypatch, stall,
                                                          landing):
    """Each attempt spent early is a filing lost to a stall that ends a little later, and the
    first filing is the most-watched."""
    live.observe_rows(REPORT, [_pending(s) for s in landing], _AT_16)
    gem = _Model(clock, until=clock.at + stall)
    await _filing_loop_runs(monkeypatch, clock, 1500, symbols=list(landing),
                            sec=_FiledOnTheClock(clock, landing), gemini=gem)
    for symbol in landing:
        assert live.snapshot(symbol, REPORT, _AT_16).revenue.actual == 17.1e9, symbol
