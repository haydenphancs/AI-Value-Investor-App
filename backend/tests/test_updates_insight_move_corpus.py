"""The MOVE corpus and the market items (news_insight_service, 2026-11-27).

`select_move_corpus` is the one corpus a move-mode scope is fingerprinted, searched and written
from (the sweeper builds it once per sweep — the same-corpus rule). Its window starts at the
regular close BEFORE the session that moved, and is at least 24 h (a Monday move reads the
weekend's news; a midweek move reads a day). Price recaps — "Why X stock is soaring today" — say
THAT the stock moved, never why, and a corpus of them only loops on the price guard: they are
dropped and up to 3 come back as `recaps`. `select_market_items` gives a confirmed market move at
most 5 recent general-news rows that name something beyond the tape.

Every headline here is hand-written. Category 1 (pure).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_insight_service import (
    MAX_CORPUS_ARTICLES,
    page_has_news,
    select_market_items,
    select_move_corpus,
)

# Mon 2026-11-23 10:33 ET (the previous regular close: Fri 2026-11-20 16:00 ET = 21:00 UTC).
MONDAY = datetime(2026, 11, 23, 15, 33, tzinfo=timezone.utc)
# Tue 2026-11-24 10:33 ET (previous close Mon 16:00 ET, 18.5 h back: the 24 h floor wins).
TUESDAY = datetime(2026, 11, 24, 15, 33, tzinfo=timezone.utc)


def _row(headline, when, summary="", tags=("PLUG",), url=None):
    return {
        "headline": headline, "summary": summary, "related_tickers": list(tags),
        "published_at": when.isoformat() if isinstance(when, datetime) else when,
        "article_url": url or f"https://fmp.example/{abs(hash(headline)) % 10**8}",
    }


def _heads(rows):
    return [r["headline"] for r in rows]


def test_a_monday_move_reads_the_weekend_but_not_fridays_session():
    rows = [
        _row("Plug Power names a new finance chief", datetime(2026, 11, 22, 18, tzinfo=timezone.utc)),
        _row("Plug Power wins a hydrogen supply order", datetime(2026, 11, 20, 22, tzinfo=timezone.utc)),
        _row("Plug Power presents at an energy forum", datetime(2026, 11, 20, 19, tzinfo=timezone.utc)),
    ]
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    # Friday 17:00 ET (after the close) is in; Friday 14:00 ET (during the session) is out
    assert _heads(news) == ["Plug Power names a new finance chief",
                            "Plug Power wins a hydrogen supply order"]
    assert recaps == []


def test_a_midweek_move_reads_at_least_24_hours():
    rows = [
        _row("Plug Power names a new finance chief", TUESDAY - timedelta(hours=23, minutes=30)),
        _row("Plug Power wins a hydrogen supply order", TUESDAY - timedelta(hours=24, minutes=30)),
    ]
    news, _ = select_move_corpus(rows, TUESDAY, "PLUG", "Plug Power Inc.")
    assert _heads(news) == ["Plug Power names a new finance chief"]


def test_after_a_holiday_monday_the_window_reaches_back_to_friday():
    # Tue 2026-09-08, after Labor Day: the previous close is Fri 2026-09-04 16:00 ET.
    now = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)
    rows = [_row("Plug Power wins a hydrogen supply order",
                 datetime(2026, 9, 5, 15, tzinfo=timezone.utc))]
    news, _ = select_move_corpus(rows, now, "PLUG", "Plug Power Inc.")
    assert len(news) == 1


def test_a_coin_move_is_a_rolling_24_hours():
    rows = [
        _row("Ethereum developers schedule a network upgrade", MONDAY - timedelta(hours=20),
             tags=("ETHUSD",)),
        _row("Ethereum foundation names a new director", MONDAY - timedelta(hours=30),
             tags=("ETHUSD",)),
    ]
    news, _ = select_move_corpus(rows, MONDAY, "ETHUSD")
    assert _heads(news) == ["Ethereum developers schedule a network upgrade"]


@pytest.mark.parametrize("headline,summary,recap", [
    ("Why Plug Power Stock Is Soaring Today", "Shares of Plug Power jumped 9% on Friday.", True),
    ("Plug Power shares slide in heavy trading", "The stock fell 8%.", True),
    # a cause clause keeps the row
    ("Plug Power stock jumps after DOE loan closes", "Shares rose.", False),
    # a price headline over a news first sentence keeps it too
    ("Plug Power Stock Soars 9%",
     "Plug Power said it closed a $1.6 billion loan from the Energy Department. Shares rose.", False),
    ("Plug Power names a new finance chief", "", False),
])
def test_price_recaps_are_dropped_and_their_twins_kept(headline, summary, recap):
    rows = [_row(headline, MONDAY - timedelta(hours=1), summary)]
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    assert (len(recaps), len(news)) == ((1, 0) if recap else (0, 1))


def test_only_the_first_sentence_of_the_summary_counts():
    row = _row("Why Plug Power Stock Is Soaring Today", MONDAY - timedelta(hours=1),
               "Shares of Plug Power jumped 9%. The company also said it signed a supply order.")
    news, recaps = select_move_corpus([row], MONDAY, "PLUG", "Plug Power Inc.")
    assert news == [] and len(recaps) == 1


# Review 2026-11-27: the period of "Inc." ended the "first sentence", so a wire summary that names
# the cause right after the legal name ("Plug Power Inc. (NASDAQ:PLUG) shares are trading lower
# after the company announced an offering") read as "Plug Power Inc." — no news — and the one
# article that explained the move never reached the card.
@pytest.mark.parametrize("headline,summary", [
    ("Plug Power Shares Are Trading Lower: What's Going On?",
     "Plug Power Inc. (NASDAQ:PLUG) shares are trading lower after the company announced a "
     "$300 million public offering of common stock."),
    ("Plug Power stock drops",
     "Shares of Plug Power Inc. fell 12% after the company priced a $300 million stock offering."),
    ("Plug Power stock drops",
     "Shares of Plug Power fell 12% in U.S. trading after the company priced a stock offering."),
    ("Plug Power stock drops",
     "Plug Power Corp. Ltd. shares fell after the company priced a stock offering."),
])
def test_an_abbreviation_never_ends_the_first_sentence(headline, summary):
    rows = [_row(headline, MONDAY - timedelta(hours=1), summary)]
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    assert (len(news), len(recaps)) == (1, 0)


@pytest.mark.parametrize("summary", [
    "Shares of Plug Power Inc. jumped 9% on Monday.",
    "Plug Power Inc. (NASDAQ:PLUG) shares fell 8% on Monday.",
])
def test_a_true_recap_with_a_legal_name_is_still_a_recap(summary):
    rows = [_row("Why Plug Power Stock Is Soaring Today", MONDAY - timedelta(hours=1), summary)]
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    assert (len(news), len(recaps)) == (0, 1)


@pytest.mark.parametrize("text,want", [
    ("Acme Inc. signed a deal. Shares rose.", "Acme Inc. signed a deal."),
    ("J. Smith joined Acme. Shares rose.", "J. Smith joined Acme."),
    ("The U.S. unit of Acme N.V. was sold. Shares rose.", "The U.S. unit of Acme N.V. was sold."),
    ("Shares rose! Acme signed a deal.", "Shares rose!"),
    ("Acme raised $1.5 billion. Shares rose.", "Acme raised $1.5 billion."),
    ("No period at all", "No period at all"),
    ("", ""),
    (None, ""),
])
def test_first_sentence_skips_abbreviations_and_initials(text, want):
    from app.services.news_insight_service import _first_sentence

    assert _first_sentence(text) == want


def test_first_sentence_is_linear_on_hostile_input():
    import time

    from app.services.news_insight_service import _first_sentence

    started = time.monotonic()
    out = _first_sentence("Inc. " * 100_000)
    assert time.monotonic() - started < 2.0 and len(out) <= 300


def test_filler_and_other_companies_stories_are_filtered_first():
    rows = [
        _row("Example Capital LLC Acquires 1,200 Shares of Plug Power Inc. $PLUG",
             MONDAY - timedelta(hours=1)),
        _row("FuelCell Energy Sinks 8%, Bloom Energy Falls 3%, Plug Power Drops 3%: Hydrogen Wrap",
             MONDAY - timedelta(hours=2), tags=("FCEL", "BE", "PLUG")),
        _row("Plug Power names a new finance chief", MONDAY - timedelta(hours=3)),
    ]
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    assert _heads(news) == ["Plug Power names a new finance chief"] and recaps == []


def test_rows_come_newest_first_capped_and_recaps_at_most_three():
    news_rows = [_row(f"Plug Power signs supply order number {i}", MONDAY - timedelta(minutes=10 * i))
                 for i in range(30)]
    recap_rows = [_row(f"Why Plug Power Stock Is Soaring Today {i}",
                       MONDAY - timedelta(minutes=7 * i + 3), "Shares of Plug Power jumped 9%.")
                  for i in range(5)]
    rows = list(reversed(news_rows)) + recap_rows
    news, recaps = select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")
    assert len(news) == MAX_CORPUS_ARTICLES
    stamps = [r["published_at"] for r in news]
    assert stamps == sorted(stamps, reverse=True)
    assert news[0]["headline"].endswith("number 0")
    assert len(recaps) == 3 and recaps[0]["headline"].endswith("Today 0")


@pytest.mark.parametrize("rows", [None, [], ["junk", 5, None], [{"headline": ""}],
                                  [{"headline": "Plug Power names a CFO", "published_at": None}],
                                  [{"headline": "Plug Power names a CFO", "published_at": "garbage"}],
                                  [{"headline": "Plug Power names a CFO",
                                    "published_at": (MONDAY + timedelta(hours=5)).isoformat()}]])
def test_odd_rows_never_raise_and_give_nothing(rows):
    assert select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.") == ([], [])


def test_a_naive_now_is_utc():
    rows = [_row("Plug Power names a new finance chief", MONDAY - timedelta(hours=1))]
    naive = MONDAY.replace(tzinfo=None)
    assert select_move_corpus(rows, naive, "PLUG", "Plug Power Inc.")[0] == \
        select_move_corpus(rows, MONDAY, "PLUG", "Plug Power Inc.")[0]


def test_the_public_page_news_name_is_the_same_function():
    from app.services import news_insight_service as nis

    assert page_has_news is nis._page_has_news


# ── market items ────────────────────────────────────────────────────────────────────────────

def test_market_items_are_recent_newsy_and_at_most_five():
    rows = [
        _row("Acme Chips reinstates its founder as chief executive", MONDAY - timedelta(hours=1), tags=()),
        _row("Wall Street closes higher", MONDAY - timedelta(hours=2), tags=()),
        _row("S&P 500 hits a record high", MONDAY - timedelta(hours=2), tags=()),
        _row("Nasdaq extends gains for a third session", MONDAY - timedelta(hours=3), tags=()),
        _row("Central bank holds rates steady", MONDAY - timedelta(hours=26), tags=()),
        _row("ROSEN, A LEADING LAW FIRM, Encourages Example Corp Investors to Secure Counsel",
             MONDAY - timedelta(hours=1), tags=()),
        *[_row(f"Regulator opens a review of payment networks {i}", MONDAY - timedelta(hours=4 + i),
               tags=()) for i in range(6)],
    ]
    items = select_market_items(rows, MONDAY)
    assert len(items) == 5
    assert items[0]["headline"] == "Acme Chips reinstates its founder as chief executive"
    heads = _heads(items)
    assert not any(h.startswith(("Wall Street", "S&P", "Nasdaq", "Central", "ROSEN")) for h in heads)
    stamps = [r["published_at"] for r in items]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.parametrize("rows", [None, [], ["junk"], [{"headline": "x", "published_at": None}]])
def test_market_items_odd_input(rows):
    assert select_market_items(rows, MONDAY) == ()
