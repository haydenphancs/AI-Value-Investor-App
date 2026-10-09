"""`_scrub_market_figures` — market figures never reach the model from a web snippet (PLAN A7).

Caydex's licensed feed is the only source of prices, % moves, market caps and index levels. A
title / snippet / extra-snippet SENTENCE that carries one is dropped whole (never half-redacted);
a result left with nothing is removed together with its pill, so the pills stay one per result the
model saw; the pill keeps the publisher's own title. Years and company-fundamental lines stay.
"""

from __future__ import annotations

import logging
import time

import pytest

from app.services import chat_web_search_service as cws

DROPPED = [
    "Apple shares fell 3% on Tuesday after the ruling.",
    "Apple (AAPL) rose 3% on Tuesday.",
    "The dollar rose 0.5% against the yen.",
    "Nvidia's market cap hit $4 trillion.",
    "Its market capitalization of 3 trillion dollars leads the index.",
    "Apple's market value reached $1.2T this week.",
    "The S&P 500 closed at 5,800.",
    "The Dow was up 300 points.",
    "The stock fell to $145.20 from $150.",
    "Analysts raised the price target to $200.",
    "Shares dropped −5.2% today.",          # unicode minus
    "Shares dropped –5.2% today.",          # en dash as a sign
    "The index slid 1.5 per cent.",
    "The index slid 1.5 percent.",
    "Apple is up 3-5% in premarket trading.",
    "Bitcoin traded at 65,000.",
    "Gold climbed 1.2% to a record.",
    "The VIX jumped to 25.",
    "The dollar index (DXY) was at 104.",
    "Shares hit a 52-week high of $199.62.",
    "Tesla stock soared +12.4% after deliveries.",
    "Sales fell 5% while shares fell 3%.",
    "Treasury yields edged up to 4.25%.",
    "It closed the session at 1,234.56.",
    # the STATIC shapes (review 2026-10-09): a quote needs no move verb
    "The S&P 500 ended the day at 5,800.",
    "The Dow finished at 42,000 on Friday.",
    "Bitcoin is now worth $60,000.",
    "Apple shares are priced at $230 after the event.",
    "Nvidia stock sits at $120 ahead of earnings.",
    "EUR/USD stood at 1.0850 on Friday.",
    "Tesla, valued at $800 billion, unveiled a robotaxi.",
    "Nasdaq-listed Arm is now a $150 billion company.",
    "EUR/USD 1.0850 in early London trade.",
    "The Nasdaq Composite ended at 18,500.12.",
    "Apple's stock price is $230.15.",
    "AAPL 230.15 +2.31 (+1.01%)",
    "Bitcoin is at $67,000 this morning.",
    "The S&P 500 stands at 5,800.",
    "USD/JPY 149.50",
    "Gold at $2,400 an ounce.",
    "Tesla stock: $250",
    "Nvidia is now worth $3.4 trillion.",
    "Bitcoin crossed $100,000.",
    "Shares last changed hands at $45.",
    "The yen weakened past 150 per dollar.",
    "The Dow at 42000 is a record.",
    "The IPO priced at $20 a share.",
    "Apple (+1.2%) led the gainers.",
    # a fundamental earlier in the window, the market subject NEAREST the figure: still a quote
    "After the buyback news, Apple shares stood at $230.",
    # the final review's holes (2026-10-09): ETH pairs and rates, "settled at", a colon quote,
    # coins by name, a price verb after a coin
    "ETH/USD 3,450.12", "ETH-USD 3,450", "SOL-USDT 145.20", "1 ETH = 3,450 USD", "ETH is at $3,450.",
    "Gold futures settled at $2,400.10.", "Silver settled at $31.20 an ounce.",
    "Brent crude futures settled at $78.20 a barrel.", "AAPL: $230.15",
    "Solana price is $145 today.", "Cardano (ADA) now costs $0.45.", "Dogecoin jumped 12% on Monday.",
]
KEPT = [
    "Revenue rose 12% to $39.3B in the quarter.",
    "Profit jumped 40% year over year.",
    "Margins rose 2 points on lower costs.",
    "In 2025, shares fell after the recall.",
    "The judge ruled against Apple in the DOJ case.",
    "Apple plans a $1.2T buyback over a decade.",
    "Deliveries climbed 9% in the third quarter.",
    "The company said guidance rose 5% for 2026.",
    "Apple unveiled the iPhone 17 in September 2025.",
    "Earnings per share rose 8% to $1.64.",
    "Market share rose to 30% in Europe.",
    "Revenue rose for a third straight quarter to $39 billion.",
    # twins of the static shapes that are news, not market data (never over-blocked)
    "OpenAI raised $6.6 billion at a $157 billion valuation.",
    "Apple will buy back $90 billion of shares.",
    "Microsoft employs 228,000 people.",
    "The FTC fined Meta $5 billion in 2019.",
    "The Treasury Department fined the bank $5 million.",
    "Apple raised iPhone prices to $1,199.",
    "Exxon will invest $10 billion in oil projects.",
    "The company sold 1.2 million iPhones.",
    "Shell's oil output reached 1.2 million barrels.",
    "EPS of $2.18 per share beat estimates.",
    "The dividend is $0.25 a share.",
    "Microsoft's deal worth $69 billion closed in 2023.",
    "OpenAI, valued at $157 billion after its funding round, hired a CFO.",
    "The euro area grew 0.3% in the quarter.",
    "Apple has 15.4 billion shares outstanding.",
    "Arm listed on the Nasdaq in 2023 and raised $4.9 billion.",
    "Revenue grew +12% year over year.",
    "A 10-15% rise in costs hit margins.",
    "Apple will build a $10 billion plant in Texas.",
    "The bank paid $1.2 billion to settle the case.",
    "The company has 1500 stores.",
    # twins of the final review's shapes (never over-blocked)
    "Revenue came in near $39.3B for the quarter.",
    "Trump signed a $1.2 trillion infrastructure bill.",
    "EPS: $1.52",
    "FCF: $24B for the year.",
    "CEO: $20 million pay package approved.",
    "The case settled at $5 million.",
    "The Ethereum upgrade shipped in 2025.",
    "The iPhone 17 costs $999.",
    "The new plant costs $5 billion.",
]


@pytest.mark.parametrize("sentence", DROPPED)
def test_a_market_figure_sentence_is_dropped(sentence):
    stats = {}
    assert cws._scrub_market_figures(sentence, stats) is None, sentence
    assert stats["scrubbed"] == 1


@pytest.mark.parametrize("sentence", KEPT)
def test_a_fundamental_or_figure_free_sentence_is_kept(sentence):
    stats = {}
    assert cws._scrub_market_figures(sentence, stats) == sentence
    assert stats.get("scrubbed", 0) == 0


def test_only_the_market_sentence_of_a_snippet_goes():
    text = ("Apple raised its guidance for the year. Shares jumped 4% in late trading. "
            "The company also named a new CFO.")
    assert cws._scrub_market_figures(text) == (
        "Apple raised its guidance for the year. The company also named a new CFO.")


def test_decimals_and_abbreviations_do_not_split_a_sentence():
    text = "U.S. regulators fined the firm on Tuesday. The fine was 1.5 billion dollars."
    assert cws._scrub_market_figures(text) == text


@pytest.mark.parametrize("junk", [None, "", 12, b"x"])
def test_empty_and_junk_input(junk):
    assert cws._scrub_market_figures(junk if isinstance(junk, str) else None) is None


def test_an_internal_failure_drops_the_text_never_passes_it_through(monkeypatch):
    monkeypatch.setattr(cws, "_sentence_carries_market_figure", lambda s: 1 / 0)
    stats = {}
    assert cws._scrub_market_figures("Apple shares fell 3%.", stats) is None
    assert stats["scrubbed"] == 1


@pytest.mark.parametrize("payload", [
    "1" * 4000, "$1," * 1300, "shares rose " * 360, "." * 4000, "%" * 4000,
    "rose 1.2% " * 400, ("Apple " * 600) + "rose 3%", "-" * 4000, "1.1." * 1000,
    "rose " + "a " * 1990 + "1%", "rose " * 800, ("fell to " * 400) + "x",
])
def test_adversarial_inputs_scan_in_linear_time(payload):
    started = time.perf_counter()
    cws._scrub_market_figures(payload)
    assert time.perf_counter() - started < 0.3


# ── through the digest: results, pills and the log ────────────────────────────


def _row(host, title, desc, extra=None):
    return {"title": title, "url": f"https://{host}/a/", "description": desc,
            "page_age": "2026-09-30T00:00:00", "extra_snippets": extra or []}


def _stats():
    return {"denied": 0, "invalid": 0}


def test_an_all_market_result_is_removed_with_its_pill_and_the_rest_stay_aligned():
    raw = {"results": [
        _row("www.reuters.com", "Apple sued by DOJ", "The case moved forward."),
        _row("apnews.com", "Apple stock falls 3%", "Shares dropped 3% on Tuesday."),
        _row("www.cnbc.com", "Apple names CFO", "The company named a new CFO."),
    ]}
    stats = _stats()
    out = cws._digest(raw, "Apple DOJ", stats)
    assert [r["publisher"] for r in out.results] == ["Reuters", "CNBC"]
    assert [r["n"] for r in out.results] == [1, 2], "renumbered: no gap where the result was"
    assert [p["detail"] for p in out.pills] == ["Reuters", "CNBC"], "pills stay one per result"
    assert stats["scrub_dropped"] == 1 and stats["scrubbed"] == 2


def test_the_pill_keeps_the_published_title_while_the_model_sees_the_scrubbed_one():
    raw = {"results": [_row("www.reuters.com", "Apple shares slide 4% after DOJ suit",
                            "The Justice Department sued Apple on Tuesday.")]}
    out = cws._digest(raw, "Apple DOJ", _stats())
    (result,) = out.results
    assert result["title"] == "" and result["snippet"].startswith("The Justice Department")
    assert out.pills[0]["title"] == "Apple shares slide 4% after DOJ suit"


def test_extra_snippets_are_scrubbed_too():
    raw = {"results": [_row("www.reuters.com", "Apple DOJ case", "The case moved forward.",
                            extra=["Shares fell 2% on the news.", "A hearing is set for May."])]}
    out = cws._digest(raw, "Apple DOJ", _stats())
    assert out.results[0]["more"] == ["A hearing is set for May."]


def test_nothing_left_anywhere_is_no_results_with_no_pills():
    raw = {"results": [_row("www.reuters.com", "Apple stock jumps 5%", "Shares rose 5% to $250.")]}
    out = cws._digest(raw, "Apple", _stats())
    assert out.status == cws.STATUS_NO_RESULTS and out.results == [] and out.pills == []


def test_no_market_figure_survives_any_model_facing_field():
    raw = {"results": [_row(f"site{i}.example.com", f"Headline {i}: shares rose {i}.5%",
                            f"Ruling {i} issued. The stock fell {i}.2% to ${i}0.10.",
                            extra=[f"Index closed at {i},234.5."]) for i in range(1, 8)]}
    out = cws._digest(raw, "Apple", _stats())
    for r in out.results:
        for field in (r["title"], r["snippet"], *r.get("more", [])):
            assert not cws._sentence_carries_market_figure(field), field
    assert len(out.pills) == len(out.results)


@pytest.mark.asyncio
async def test_the_search_log_counts_scrubbed_sentences(monkeypatch, caplog):
    from app.services import chat_market_tools as cmt

    class _Led:
        def try_claim_turn(self, bucket, limit=None):
            return 1

        def refund_turn(self, bucket):
            pass

    async def _brave(query, **kw):
        return {"results": [_row("www.reuters.com", "Apple DOJ", "Shares fell 3%. The case advanced.")]}

    s = cws.settings
    monkeypatch.setattr(s, "BRAVE_SEARCH_API_KEY", "k")
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_DAILY_CAP", 10)
    monkeypatch.setattr(cws, "_cache", {})
    monkeypatch.setattr(cws, "_inflight", {})
    monkeypatch.setattr(cmt, "get_chat_budget_service", lambda: _Led())
    monkeypatch.setattr(cws.brave_search, "web_search", _brave)
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    out = await cws.run_web_search(cws.WebSearchTurn(user_id="u-1"), "Apple DOJ")
    assert out["results"][0]["snippet"] == "The case advanced."
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("REPORT_WEB_SEARCH "))
    assert "scrubbed=1" in line and "Shares" not in caplog.text


def test_the_reviewers_quote_page_answer_reaches_the_model_with_no_quote():
    """Review 2026-10-09 (HIGH): a fake answer of plain quote lines went through `_digest` with
    `kept=3, scrubbed=0`. Every quote sentence now goes; the news sentence beside it stays."""
    raw = {"results": [
        _row("www.reuters.com", "EUR/USD 1.0850 in early London trade.",
             "The ECB meets next week. EUR/USD 1.0850 in early London trade."),
        _row("apnews.com", "Markets wrap",
             "The Nasdaq Composite ended at 18,500.12. Apple's stock price is $230.15."),
        _row("www.cnbc.com", "AAPL 230.15 +2.31 (+1.01%)", "Bitcoin is at $67,000 this morning."),
    ]}
    stats = _stats()
    out = cws._digest(raw, "markets", stats)
    assert [(r["title"], r["snippet"]) for r in out.results] == [
        ("", "The ECB meets next week."), ("Markets wrap", "")]
    assert [p["detail"] for p in out.pills] == ["Reuters", "AP News"], "pills stay aligned"
    assert stats["scrub_dropped"] == 1 and stats["scrubbed"] == 6


@pytest.mark.parametrize("payload", [
    "shares $1 " * 400, "$1 a share " * 360, "+1% " * 1000, "EUR/USD 1.0 " * 330,
    "AAPL 1.1 +1 " * 330, "worth $1 " * 440, "s&p 1,000.5 " * 330, ("a " * 29 + "$1 ") * 60,
    "9,999 " * 660, "12345 " * 660,
])
def test_the_static_shapes_scan_in_linear_time(payload):
    started = time.perf_counter()
    cws._scrub_market_figures(payload)
    assert time.perf_counter() - started < 0.3
