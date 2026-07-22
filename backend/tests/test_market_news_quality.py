"""The deterministic MARKET-news quality filter.

`filter_market_articles` trims the high-volume Market corpus to fewer, higher-
quality rows WITHOUT reordering: it collapses syndicated wire copies, drops
PR-wire/sponsored spam and non-reputable listicle noise, always keeps reputable
wires, and rescues a noisy-looking headline that is materially relevant or about
a ticker trending on Reddit. Pure function — everything here is inline data.
"""

from app.services.market_news_quality import filter_market_articles


def _row(title, publisher="Random Blog", url=None, symbol=None,
         when="2026-07-20 12:00:00", site=""):
    return {
        "title": title, "publisher": publisher, "site": site,
        "url": url or title, "symbol": symbol, "publishedDate": when,
        "text": "body", "image": None,
    }


# ── Source rules ──────────────────────────────────────────────────────────

def test_reputable_source_kept_even_if_listicle_shaped():
    # A wire we trust bypasses the noise gate.
    assert len(filter_market_articles([_row("5 Stocks to Buy Right Now", publisher="Forbes")])) == 1


def test_unknown_source_listicle_is_dropped():
    assert filter_market_articles([_row("5 Stocks to Buy Right Now", publisher="Random Blog")]) == []


def test_mid_tier_house_listicle_is_dropped():
    # Motley Fool is deliberately NOT in the reputable tier, so its "3 stocks to
    # buy" promo is trimmed while its real coverage (non-noise headline) survives.
    assert filter_market_articles([_row("3 Growth Stocks to Buy and Hold Forever",
                                        publisher="The Motley Fool")]) == []
    assert len(filter_market_articles([_row("Apple beats on iPhone revenue, shares rise",
                                            publisher="The Motley Fool")])) == 1


def test_pr_wire_dropped_even_when_material():
    # PR earnings releases are the classic junk — dropped despite "earnings".
    assert filter_market_articles([_row("Acme Corp reports record quarterly earnings",
                                        publisher="GlobeNewswire")]) == []
    assert filter_market_articles([_row("XYZ announces partnership", publisher="PR Newswire")]) == []


def test_plain_unknown_source_news_is_kept():
    # No noise shape → kept by default (conservative; we don't over-filter).
    assert len(filter_market_articles([_row("Oil prices climb as OPEC weighs output cuts",
                                            publisher="Oil Price Daily")])) == 1


# ── Noise rescue (material keyword / trending ticker) ─────────────────────

def test_material_keyword_rescues_a_noisy_headline():
    # "ETFs to buy" is a listicle shape, but it is about the Fed decision → kept.
    assert len(filter_market_articles([_row("5 ETFs to Buy Ahead of the Fed Decision",
                                            publisher="Random Blog")])) == 1


def test_premarket_brief_is_not_treated_as_a_listicle():
    # "N things to know" is the standard pre-market-brief shape, not a promo — kept
    # even from an unknown source. Regression guard for the \d+ ETF false positive.
    rows = [_row("Dow futures jump 277 points: 5 things to know before Wall Street opens",
                 publisher="Invezz"),
            _row("Is the S&P 500 ETF Trust (SPY) a Smart Long-Term Hold?", publisher="Zacks")]
    out = filter_market_articles(rows)
    titles = [r["title"] for r in out]
    assert "Dow futures jump 277 points: 5 things to know before Wall Street opens" in titles


def test_trending_ticker_rescues_a_noisy_headline_only_when_trending():
    rows = [_row("3 Reasons GME Could Squeeze Higher", publisher="Random Blog", symbol="GME")]
    # Trending on Reddit → rescued.
    assert len(filter_market_articles(rows, trending_tickers=frozenset({"GME"}))) == 1
    # Not trending, not material → dropped as noise.
    assert filter_market_articles(rows) == []


# ── Syndication de-dup ────────────────────────────────────────────────────

def test_syndications_collapse_keeping_the_reputable_copy():
    rows = [
        _row("Fed holds rates steady", publisher="Some Blog", url="b1"),
        _row("Fed Holds Rates Steady", publisher="Reuters", url="r1"),  # same story, cased
    ]
    out = filter_market_articles(rows)
    assert len(out) == 1
    assert out[0]["publisher"] == "Reuters"


def test_distinct_stories_are_not_collapsed():
    rows = [_row("Fed holds rates steady", publisher="Reuters", url="a"),
            _row("Fed signals a cut in December", publisher="Reuters", url="b")]
    assert len(filter_market_articles(rows)) == 2


# ── Order preservation ────────────────────────────────────────────────────

def test_order_is_preserved_when_nothing_is_dropped():
    rows = [_row("Reuters macro update", publisher="Reuters", url="a"),
            _row("Bloomberg market wrap", publisher="Bloomberg", url="b"),
            _row("CNBC afternoon report", publisher="CNBC", url="c")]
    assert [r["url"] for r in filter_market_articles(rows)] == ["a", "b", "c"]


# ── Degradation on bad input ──────────────────────────────────────────────

def test_non_list_input_returns_empty():
    assert filter_market_articles(None) == []
    assert filter_market_articles("nope") == []
    assert filter_market_articles({"title": "x"}) == []  # a dict is not a row list


def test_empty_input_returns_empty():
    assert filter_market_articles([]) == []


def test_malformed_rows_are_skipped_without_raising():
    rows = [
        None, "junk", 42,
        {"title": ""},                       # empty title
        {"no_title": 1},                     # missing title
        {"title": "   ", "publisher": "Reuters"},  # whitespace title
        {"title": 123, "publisher": "Reuters"},    # non-str title
        _row("Real market news from Reuters", publisher="Reuters", url="ok"),
    ]
    assert [r["url"] for r in filter_market_articles(rows)] == ["ok"]
