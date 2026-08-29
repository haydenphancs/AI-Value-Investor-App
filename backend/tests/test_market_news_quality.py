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


# ── Off-topic / lifestyle gate ────────────────────────────────────────────
#
# TestFlight, Market Insights sheet: a CNBC-style consumer feature was cited as a
# source for the market roll-up. The cause was structural — pass 1 ran the noise
# gate only for NON-reputable sources, so the whole top tier had no title gate at
# all and anything from a trusted wire passed untouched.
#
# The gate closing that hole is the one rule a reputable outlet cannot bypass, so
# it is held to a much higher bar than the noise patterns: measured over 3,204
# unique live `news/general-latest` + index rows it removed exactly ONE row (the
# reported headline) and nothing else.

import pytest


def test_reputable_lifestyle_feature_is_dropped():
    """The verbatim headline from the TestFlight report, from the outlet that
    actually carried it — Yahoo Finance, which is in REPUTABLE_SOURCE_MARKERS and
    therefore used to bypass every title check."""
    out = filter_market_articles(
        [_row("A 158-year-old lawn company says it's a lifestyle brand now",
              publisher="Yahoo Finance")]
    )
    assert out == []


@pytest.mark.parametrize("publisher", ["Reuters", "CNBC", "Bloomberg", "Random Blog"])
def test_the_offtopic_gate_applies_to_every_tier(publisher):
    """The point of the gate: source reputation no longer buys a bypass."""
    out = filter_market_articles(
        [_row("Our new lifestyle brand is here", publisher=publisher)]
    )
    assert out == [], f"{publisher} bypassed the off-topic gate"


def test_material_keyword_rescues_an_offtopic_shape():
    """Materiality RESCUES and never drops — an off-topic shape wrapped around
    real market news survives."""
    out = filter_market_articles(
        [_row("Gift guide sales lift Amazon holiday earnings", publisher="Reuters")]
    )
    assert len(out) == 1


@pytest.mark.parametrize("title", [
    # Every one of these was killed by an "obvious" broader pattern during
    # measurement. They are the regression suite for the gate's narrowness.
    "Wall Street's massive bet against long-term bonds is a recipe for a painful bearish unwind",
    "What a 125-Year-Old Bull Market Says About Today's Trading Craze",
    "Review & Preview: Vacation's Over",
    "Pizza Hut makes surprising change to iconic name ahead of NFL season",
    # Near-misses on the two-token anchors.
    "Booking Holdings says travel demand is slowing",
    "Carnival reports its best cruise season ever",
    "Amazon Prime Day sales jump 12%",
    "The best places to invest in 2027",
    "Here's how much the Fed cut rates",
    # Same anchor, but with NO material keyword to rescue it — so this one
    # actually exercises the `(i|we)` first-person anchor rather than passing
    # through the material rescue, which is what "the Fed cut rates" does.
    "Here's how much Nvidia's valuation has climbed",
    "Warsh Delivers",
])
def test_real_market_headlines_survive_the_offtopic_gate(title):
    assert filter_market_articles([_row(title, publisher="Reuters")]) == [
        _row(title, publisher="Reuters")
    ], f"the off-topic gate wrongly dropped: {title!r}"


def test_offtopic_syndications_collapse_to_nothing():
    """Pass 1 evaluates each copy independently, so identical titles share a
    verdict — no half-collapsed remnant survives into pass 2."""
    title = "A 158-year-old lawn company says it's a lifestyle brand now"
    out = filter_market_articles([
        _row(title, publisher="Reuters", url="https://a"),
        _row(title, publisher="Random Blog", url="https://b"),
    ])
    assert out == []


def test_offtopic_gate_does_not_disturb_tier_preference():
    """A real story syndicated across tiers still resolves to the reputable copy."""
    title = "Fed signals a possible rate hike"
    out = filter_market_articles([
        _row(title, publisher="Random Blog", url="https://b"),
        _row(title, publisher="Reuters", url="https://a"),
    ])
    assert len(out) == 1 and out[0]["publisher"] == "Reuters"


def test_a_bad_offtopic_regex_fails_at_import_not_at_runtime():
    """The patterns are compiled at module scope on purpose: `_fetch_market_raw`
    catches exceptions and serves the UNFILTERED corpus, so a lazily-compiled bad
    regex would silently disable the entire quality filter in production."""
    from app.services.market_news_quality import _OFFTOPIC_PATTERNS
    assert _OFFTOPIC_PATTERNS and all(hasattr(p, "search") for p in _OFFTOPIC_PATTERNS)
