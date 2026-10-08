"""The Market feed's relevance rule: market-wide stories stay, single-company ones go —
and a verdict never changes once a row can be read.

TestFlight 1.0 (11), 2026-10-03 — the Updates tab's Market chip led with "Fire, smoke seen
near Aramco facility in Riyadh, witness says". Owner: "Should only be for market!" — keep
macro / index / sector / market-wide news, drop stories about one company.

Adversarial review 2026-10-06 (both upheld 3/3):
  * a verdict that changed after serve made the iOS offset pager skip stories — so the
    verdict now reads only the headline and the scope recorded at insert, never the
    tickers a later summary merges in;
  * macro cashtags ("$DXY") and institution acronyms ("Bank of Korea (BOK)") were read as
    one company and dropped at ingest for good — so only an exchange-qualified citation
    ("(NASDAQ: TSLA)") hides on the headline alone.

`market_news_relevance` is pure, so everything here is inline data shaped like the two row
kinds it sees: a cache row (`headline`, `related_tickers`, `ai_model`) and a raw FMP row
(`title`, comma-separated `symbol`).
"""

from __future__ import annotations

import logging
import time

import pytest

from app.services import market_news_relevance as rel
from app.services.market_news_relevance import (
    DROP_REASONS,
    KEEP_REASONS,
    MARKET_INSTRUMENT_SYMBOLS,
    MIN_MARKET_STORIES,
    MODEL_SCOPES,
    classify_market_story,
    is_market_story,
    model_scope,
    normalize_model_scope,
    select_market_stories,
    stamp_model_scope,
)

MODEL = "gemini-2.5-flash-lite"


def _row(headline, tags=None, **extra):
    """A cache row as `get_cached_bulk` / `_get_cached` return it, never judged."""
    row = {"id": extra.pop("id", headline[:40]), "headline": headline,
           "related_tickers": [] if tags is None else tags}
    row.update(extra)
    return row


def _judged(headline, scope, tags=None, **extra):
    """A Market row judged at insert: its `ai_model` carries the scope stamp."""
    return _row(headline, tags, ai_model=stamp_model_scope(MODEL, scope), **extra)


def _fmp(title, symbol=None):
    """A raw FMP news row as `_fetch_market_raw` sees it (never judged)."""
    return {"title": title, "symbol": symbol, "publisher": "Reuters", "site": "reuters.com",
            "url": f"https://x/{abs(hash(title))}", "text": "body",
            "publishedDate": "2026-10-03 12:00:00"}


# ── The owner's two examples, and the reported screen ─────────────────────────

ARAMCO = "Fire, smoke seen near Aramco facility in Riyadh, witness says"
OIL = "Oil jumps after attack on Saudi facilities"


def test_the_owners_aramco_example_is_hidden_once_judged():
    assert classify_market_story(_judged(ARAMCO, "company")) == "model_company"
    assert not is_market_story(_judged(ARAMCO, "company", ["2222.SR"]))


def test_an_oil_move_after_attacks_on_a_countrys_facilities_stays():
    assert classify_market_story(_judged(OIL, "market")) == "model_market"
    assert classify_market_story(_judged(OIL, "market", ["2222.SR"])) == "model_market"


def test_an_unjudged_row_is_kept_for_its_whole_life():
    """No verdict at insert (the model failed or ran out of time, or the row predates the
    field): fail open — and never judged later, so it cannot vanish from a client."""
    assert classify_market_story(_row(ARAMCO)) == "not_judged"
    assert classify_market_story(_fmp(ARAMCO)) == "not_judged"


@pytest.mark.parametrize("headline", [
    "One Trade, Two Markets, One Hidden Correction",
    "As Treasury yields touch generational highs, investors brace for the market fallout",
])
def test_the_screenshots_market_headlines_stay(headline):
    for scope in ("market", "sector", "unclear"):
        assert is_market_story(_judged(headline, scope))
    assert is_market_story(_row(headline))


# ── The model's scope ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope,expected", [
    ("company", "model_company"), ("market", "model_market"),
    ("sector", "model_sector"), ("unclear", "model_unclear"),
])
def test_each_scope_maps_to_its_verdict(scope, expected):
    assert classify_market_story(_judged("Some story", scope)) == expected


def test_unclear_never_hides():
    """The model's doubt is not evidence — whatever the row's tickers say."""
    row = _judged("Tesla recalls 300,000 vehicles", "unclear", ["TSLA"])
    assert classify_market_story(row) == "model_unclear"
    assert is_market_story(row)


@pytest.mark.parametrize("stamp", [
    None, "", MODEL, f"{MODEL}|scope=", f"{MODEL}|scope=companyish", f"{MODEL}|scope=5",
    f"{MODEL}|scope=COMPANY STORY", 42, ["company"], {"scope": "company"}, "|scope",
])
def test_a_missing_or_malformed_stamp_is_no_verdict(stamp):
    row = _row(ARAMCO, ["TSLA"], ai_model=stamp)
    assert classify_market_story(row) == "not_judged"


def test_a_case_or_space_variant_of_a_valid_scope_is_read():
    assert model_scope({"ai_model": f"{MODEL}|scope= Company "}) == "company"
    assert model_scope({"ai_model": f"{MODEL}|scope=MARKET"}) == "market"


def test_stamping_writes_the_plain_model_name_when_there_is_no_valid_scope():
    assert stamp_model_scope(MODEL, "company") == f"{MODEL}|scope=company"
    assert stamp_model_scope(MODEL, " Sector ") == f"{MODEL}|scope=sector"
    for bad in (None, "", "companyish", 3, ["market"]):
        assert stamp_model_scope(MODEL, bad) == MODEL
    assert stamp_model_scope(f"{MODEL}|scope=market", "company") == f"{MODEL}|scope=company"
    assert stamp_model_scope(None, "company") is None


def test_the_scope_vocabulary_round_trips():
    for scope in MODEL_SCOPES:
        assert normalize_model_scope(scope) == scope
        assert model_scope({"ai_model": stamp_model_scope(MODEL, scope)}) == scope
    assert normalize_model_scope("other") is None and model_scope(None) is None


# ── Stability: what a later summary writes can never move a verdict ──────────

@pytest.mark.parametrize("tags", [
    [], ["TSLA"], ["TSLA", "GM"], ["SPY"], ["SPY", "NVDA"], '["TSLA"]', None, "junk", 7,
])
def test_the_verdict_never_reads_related_tickers(tags):
    """A later summary merges the model's tickers into `related_tickers` of a row a client
    may already hold. Were they read, an unjudged row tagged ["TSLA"] afterwards would
    vanish from under the client's offset (review 2026-10-06, finding 1)."""
    for scope in (None, "company", "market", "unclear"):
        base = _row(ARAMCO, ai_model=stamp_model_scope(MODEL, scope) if scope else None)
        with_tags = dict(base, related_tickers=tags)
        assert classify_market_story(with_tags) == classify_market_story(base)


def test_the_verdict_reads_nothing_but_the_headline_and_the_stamp():
    """Every other column a later write touches — bullets, sentiment, ai_processed — is
    irrelevant to the verdict."""
    base = _judged(ARAMCO, "company")
    noisy = dict(base, summary_bullets=["x"], sentiment="bullish", ai_processed=True,
                 sentiment_confidence=99, summary="Totally about the market.")
    assert classify_market_story(noisy) == classify_market_story(base)


# ── The headline rule: an exchange-qualified single-company citation ──────────

@pytest.mark.parametrize("headline", [
    "Tesla (NASDAQ:TSLA) Stock Sinks As Market Gains: What You Should Know",
    "Shares of Boeing Co (NYSE: BA) slide on delivery delay",
    "Nvidia (NASDAQ: NVDA) drags the Nasdaq lower as chip stocks slide",
    "Alphabet (NASDAQ: GOOGL) and (NASDAQ: GOOG) climb on cloud deal",   # one company
    "Berkshire (NYSE: BRK.A) and (NYSE: BRK.B) hit records",               # one company
    "Shopify (TSX: SHOP) rallies in Toronto",
])
def test_an_exchange_qualified_citation_of_one_company_hides(headline):
    """The must-hide twins of the macro shapes below — at ingest, before any model."""
    for row in (_fmp(headline), _row(headline), _judged(headline, "market")):
        assert classify_market_story(row) == "headline_names_one_company", headline


def test_two_cited_companies_or_an_instrument_citation_is_left_to_the_model():
    two = "Nvidia (NASDAQ:NVDA) vs. AMD (NASDAQ:AMD): which chip stock wins?"
    inst = "SPDR S&P 500 ETF (NYSEARCA: SPY) sees record inflows"
    mixed = "Apple (NASDAQ: AAPL) now 7% of the S&P 500 (NYSEARCA: SPY)"
    for headline in (two, inst, mixed):
        assert classify_market_story(_row(headline)) == "not_judged"
        assert classify_market_story(_judged(headline, "company")) == "model_company"


def test_the_exchange_prefix_is_case_insensitive_but_the_symbol_is_not():
    assert classify_market_story(_row("Tesla (Nasdaq: TSLA) slides")) == \
        "headline_names_one_company"
    assert classify_market_story(_row("Markets wrap (Nasdaq: stocks rally)")) == "not_judged"


# ── Finding 2: macro cashtags and institution acronyms are never "one company" ─

@pytest.mark.parametrize("headline", [
    # The reviewers' shapes, verbatim or close.
    "Dollar index ($DXY) hits two-year high",
    "10-year yield ($TNX) tops 5% for the first time since 2007",
    "S&P 500 ($SPX) closes at a record",
    "Volatility gauge ($VIX) jumps above 30",
    "Crude ($CL) slides as OPEC weighs output",
    "S&P 500 futures ($ES) point higher",
    "European Central Bank (ECB) holds rates",
    "Bank of Japan (BOJ) ends negative rates",
    "International Monetary Fund (IMF) cuts global growth forecast",
    "OPEC+ (OPEC) extends supply cuts",
    "Bank of Korea (BOK) holds benchmark rate steady",
    "Monetary Authority of Singapore (MAS) eases policy",
    "Reserve Bank of New Zealand (RBNZ) cuts official cash rate",
    "World Bank (WB) cuts global growth forecast",
    "Builder sentiment falls, National Association of Home Builders (NAHB) says",
    "Mortgage applications fall, Mortgage Bankers Association (MBA) says",
    "Consumer Confidence Index (CCI) slumps",
    "Platinum (XPT) jumps on supply worries",
    "Conference Board (CB) leading index slips",
])
def test_a_macro_cashtag_or_acronym_is_not_a_company(headline):
    """No exchange prefix, so no headline verdict: kept at ingest, then judged by the
    model like any other row (and kept when it reads them as market)."""
    assert classify_market_story(_fmp(headline)) == "not_judged", headline
    assert is_market_story(_judged(headline, "market"))


@pytest.mark.parametrize("headline", [
    "$NVDA hits a record high",
    "Tesla (TSLA) recalls 300,000 vehicles",
])
def test_a_bare_cashtag_or_parenthesised_ticker_is_left_to_the_model(headline):
    """Their must-hide twin is the model's "company", not the headline shape."""
    assert classify_market_story(_fmp(headline)) == "not_judged"
    assert classify_market_story(_judged(headline, "company")) == "model_company"


def test_the_feed_index_basket_and_macro_codes_are_instruments():
    from app.services.news_cache_service import MARKET_INDEX_SYMBOLS

    for sym in MARKET_INDEX_SYMBOLS.split(","):
        assert sym.strip().upper() in MARKET_INSTRUMENT_SYMBOLS, sym
    for sym in ("DXY", "TNX", "SPX", "VIX", "NDX"):
        assert sym in MARKET_INSTRUMENT_SYMBOLS, sym


# ── Missing, malformed and odd fields ─────────────────────────────────────────

@pytest.mark.parametrize("row", [
    {"id": "r"}, {"id": "r", "headline": ""}, {"id": "r", "headline": "   "},
    {"id": "r", "headline": 123, "related_tickers": ["TSLA"]},
    {"id": "r", "headline": None, "title": None},
])
def test_a_row_without_a_usable_headline_is_left_to_other_layers(row):
    assert classify_market_story(row) == "no_title"
    assert is_market_story(row)


def test_a_raw_row_falls_back_to_its_title():
    assert classify_market_story({"headline": "", "title": "Tesla (NASDAQ: TSLA) sinks",
                                  "symbol": "TSLA"}) == "headline_names_one_company"


@pytest.mark.parametrize("bad", [None, "row", 42, ["TSLA"], ("a",)])
def test_a_non_dict_row_is_not_a_row(bad):
    assert classify_market_story(bad) == "not_a_row"
    assert not is_market_story(bad)


@pytest.mark.parametrize("headline,scope,expected", [
    ("Tesla retira 300.000 vehículos", "company", "model_company"),
    ("Las bolsas caen tras la decisión de la Reserva Federal", "market", "model_market"),
    ("美股收跌", None, "not_judged"),
    ("特斯拉召回30万辆汽车", "company", "model_company"),
    ("Bolsas europeas cierran en rojo; LVMH cae", "market", "model_market"),
])
def test_non_english_headlines_are_judged_by_the_model_not_by_english_words(headline,
                                                                            scope,
                                                                            expected):
    row = _judged(headline, scope) if scope else _row(headline)
    assert classify_market_story(row) == expected


# ── Selection: order, duplicates, empty feed, floor ───────────────────────────

def _company(i):
    return _judged(f"Company story {i}", "company", id=f"c{i}")


def _market(i):
    return _judged(f"Fed decision take {i}", "market", id=f"m{i}")


def test_selection_keeps_order_and_hides_single_company_rows():
    rows = [_market(0), _company(0), _market(1), _company(1), _row("Unjudged", id="u"),
            _market(2)]
    assert [r["id"] for r in select_market_stories(rows)] == ["m0", "m1", "u", "m2"]


@pytest.mark.parametrize("rows", [[], None, "rows", {"headline": "x"}, 7])
def test_an_empty_or_non_list_feed_selects_nothing(rows):
    assert select_market_stories(rows) == []


def test_non_dict_rows_are_dropped_and_never_restored_by_the_floor():
    assert [r["id"] for r in select_market_stories([None, "junk", 3, _company(0)])] == ["c0"]


def test_duplicate_rows_share_a_verdict_and_the_floor_never_duplicates():
    m, c = _market(0), _company(0)
    assert select_market_stories([m, m, c, c, _market(1)]) == [m, m, _market(1)]
    assert select_market_stories([c, c], floor=3) == [c, c]


def test_the_floor_restores_the_newest_hidden_rows_in_feed_order(caplog):
    rows = [_company(0), _company(1), _market(0), _company(2), _company(3)]
    with caplog.at_level(logging.WARNING, logger=rel.__name__):
        out = select_market_stories(rows)
    assert MIN_MARKET_STORIES == 3
    assert [r["id"] for r in out] == ["c0", "c1", "m0"]
    assert "floor" in caplog.text and "model_company" in caplog.text


def test_a_feed_of_only_single_company_stories_is_never_emptied(caplog):
    rows = [_company(i) for i in range(6)]
    with caplog.at_level(logging.WARNING, logger=rel.__name__):
        out = select_market_stories(rows, label="test feed")
    assert [r["id"] for r in out] == ["c0", "c1", "c2"]
    assert "test feed" in caplog.text


def test_the_floor_is_not_used_when_enough_market_stories_exist(caplog):
    rows = [_market(0), _company(0), _market(1), _market(2), _company(1)]
    with caplog.at_level(logging.WARNING, logger=rel.__name__):
        out = select_market_stories(rows)
    assert [r["id"] for r in out] == ["m0", "m1", "m2"]
    assert "floor" not in caplog.text


@pytest.mark.parametrize("floor,expected", [
    (0, []), (-5, []), (1, ["c0"]), (99, ["c0", "c1"]),
    (None, ["c0", "c1"]), ("junk", ["c0", "c1"]),   # unusable → the default floor (3)
])
def test_the_floor_is_clamped(floor, expected):
    rows = [_company(0), _company(1)]
    assert [r["id"] for r in select_market_stories(rows, floor=floor)] == expected


# ── Robustness ────────────────────────────────────────────────────────────────

def test_a_pathological_row_is_classified_quickly():
    """Runs on the hottest read in the app; the headline is capped before any regex."""
    long_title = ("Tesla (NASDAQ: " + "A" * 5 + ")") * 20_000
    row = {"headline": long_title, "related_tickers": [f"T{i}" for i in range(10_000)],
           "ai_model": "x" * 100_000 + "|scope=company"}
    started = time.monotonic()
    assert classify_market_story(row) in KEEP_REASONS | DROP_REASONS
    assert time.monotonic() - started < 2.0


def test_reason_vocabularies_are_disjoint_and_complete():
    assert not (KEEP_REASONS & DROP_REASONS)
    samples = [_market(0), _company(0), _row("x"), _judged("x", "unclear"),
               _judged("x", "sector"), {"id": "r"}, None, _row("Tesla (NASDAQ:TSLA) sinks")]
    for s in samples:
        assert classify_market_story(s) in KEEP_REASONS | DROP_REASONS


def test_the_citation_pattern_is_compiled_at_import():
    """`_fetch_market_raw` catches exceptions and serves the unfiltered corpus, so a
    lazily-compiled bad regex would silently disable the filter in production."""
    assert hasattr(rel._EXCHANGE_CITATION, "search")
