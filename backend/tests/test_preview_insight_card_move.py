"""`scripts/preview_insight_card.py` replays MOVE cards (2026-11-27): ``--move up|down``,
``--move-basis`` and ``--market-fixture`` (licensed FMP general-news rows → an index-basis
market context). The pure helpers are pinned here; the replay itself calls FMP and the card
model and is run by hand. Neither web search runs in the preview: both switches are forced off
at import (pinned by tests/test_brave_search_boundary.py and here).

Every row and string below is hand-written.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services.insight_move import (
    BASIS_FLOOR,
    BASIS_SIGMA,
    MARKET_BASIS_INDEX,
    MarketContext,
    MoveContext,
)
from scripts import preview_insight_card as preview


def test_the_preview_never_reaches_either_web_search():
    from app.config import settings

    assert settings.UPDATES_JUMP_WEB_SEARCH_ENABLED is False
    assert settings.UPDATES_JUMP_CLAUDE_FALLBACK_ENABLED is False


def test_the_move_flags_parse_and_default_off():
    plain = preview.parse_args(["--scope", "KLMN"])
    assert plain.move is None and plain.market_fixture is None
    assert plain.move_basis == BASIS_SIGMA
    args = preview.parse_args(["--scope", "KLMN", "--move", "down", "--move-basis", "floor",
                               "--market-fixture", "x.json"])
    assert (args.move, args.move_basis, args.market_fixture) == ("down", BASIS_FLOOR, "x.json")
    for bad in (["--move", "sideways"], ["--move-basis", "vibes"]):
        with pytest.raises(SystemExit):
            preview.parse_args(["--scope", "KLMN", *bad])


@pytest.mark.parametrize("argv, scope, problem", [
    (["--market-fixture", "m.json"], "KLMN", "--market-fixture needs move mode"),
    (["--jump", "--market-fixture", "m.json"], "KLMN", "--market-fixture needs move mode"),
    (["--jump", "--quote-pct", "0.001", "--market-fixture", "m.json"], "KLMN",
     "--market-fixture needs move mode"),                     # a flat quote is no move
    (["--move", "up"], "__MARKET__", "the market scope never writes a move card"),
    (["--move", "up", "--market-fixture", "m.json"], "BTCUSD", "coins get no market confirmation"),
])
def test_move_flags_that_production_never_runs_are_refused(argv, scope, problem):
    args = preview.parse_args(["--scope", scope, *argv])
    assert problem in (preview.move_args_problem(args, scope) or "")


@pytest.mark.parametrize("argv, scope", [
    ([], "KLMN"),
    (["--jump"], "KLMN"),
    (["--move", "up"], "KLMN"),
    (["--move", "down", "--market-fixture", "m.json"], "KLMN"),
    (["--jump", "--quote-pct", "-12.5", "--market-fixture", "m.json"], "KLMN"),
    (["--move", "up"], "BTCUSD"),            # a coin may replay a move card, without a market
])
def test_move_flags_production_runs_pass(argv, scope):
    assert preview.move_args_problem(preview.parse_args(["--scope", scope, *argv]), scope) is None


class _ExplodingFMP:
    def __getattr__(self, name):
        raise AssertionError(f"FMP must not be called for a refused replay (called {name})")


@pytest.mark.asyncio
async def test_a_refused_replay_stops_before_any_fmp_or_model_call(capsys):
    args = preview.parse_args(["--scope", "KLMN", "--market-fixture", "m.json"])
    code = await preview.run_preview(args, fmp=_ExplodingFMP(), gemini=_ExplodingFMP(),
                                     stored=None, secrets=())
    assert code == 2
    assert "--market-fixture needs move mode" in capsys.readouterr().out


def test_the_market_fixture_reads_fmp_rows_and_cache_rows(tmp_path):
    rows = [
        {"title": "Treasury yields ease after a soft jobs report", "text": "Bond markets",
         "url": "https://fmp.example.com/general/1", "publishedDate": "2026-11-23 09:10:00",
         "site": "Example Wire"},
        {"headline": "Chipmakers lead a broad advance in early trade", "summary": "Stocks",
         "article_url": "https://fmp.example.com/general/2",
         "published_at": "2026-11-23T14:20:00+00:00", "related_tickers": []},
    ]
    path = tmp_path / "market.json"
    path.write_text(json.dumps(rows))
    out = preview.load_market_fixture(str(path))
    assert [r["headline"] for r in out] == [
        "Treasury yields ease after a soft jobs report",
        "Chipmakers lead a broad advance in early trade",
    ]
    # FMP's wall clock is ET: 09:10 ET is 14:10 UTC (the production sanitizer).
    assert out[0]["published_at"].startswith("2026-11-23T14:10")
    assert out[0]["article_url"] == "https://fmp.example.com/general/1"
    assert out[0]["related_tickers"] == [], "the market scope records no synthetic ticker"


@pytest.mark.parametrize("payload, message", [
    ({"title": "not a list"}, "expected a JSON list"),
    (["a string"], "every row must be an object"),
    ([{"title": "No link", "url": "http://plain.example.com/x"}], "an FMP row needs an https url"),
    ([{"headline": "No link", "article_url": ""}], "a cache-shape row needs an https article_url"),
    ([{"text": "no title at all", "url": "https://x.example.com"}], "needs a title"),
])
def test_a_malformed_market_fixture_is_refused(tmp_path, payload, message):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=message):
        preview.load_market_fixture(str(path))


@pytest.mark.parametrize("argv, scope, direction", [
    (["--move", "up"], "KLMN", "up"),
    (["--move", "down", "--jump", "--quote-pct", "9.0"], "KLMN", "down"),   # --move wins
    (["--jump", "--quote-pct", "11.2"], "KLMN", "up"),       # a live jumper: move mode
    (["--jump", "--quote-pct", "-7.4"], "BTCUSD", "down"),
    (["--jump", "--quote-pct", "0.001"], "KLMN", None),      # flat: today's jump replay
    (["--jump"], "KLMN", None),                              # no quote: today's jump replay
    (["--quote-pct", "11.2"], "KLMN", None),                 # not a jumper
    (["--jump", "--quote-pct", "3.0"], "__MARKET__", None),  # the market card never moves
    ([], "KLMN", None),
])
def test_the_replay_direction(argv, scope, direction):
    assert preview.move_direction(preview.parse_args(["--scope", scope, *argv]), scope) == direction


def test_the_move_context_is_what_the_sweeper_would_build():
    args = preview.parse_args(["--scope", "KLMN", "--move", "up", "--move-basis", "floor"])
    recaps = [{"headline": f"recap {i}"} for i in range(5)]
    market = MarketContext(basis=MARKET_BASIS_INDEX, items=({"headline": "h"},))
    move = preview.build_move_context(args, "KLMN", "up", market=market, recaps=recaps)
    assert isinstance(move, MoveContext)
    assert (move.direction, move.basis, move.held, move.is_crypto) == ("up", BASIS_FLOOR, None,
                                                                       False)
    assert move.market is market
    assert len(move.recaps) == 3, "at most three recaps, as the move corpus keeps"
    coin = preview.build_move_context(
        preview.parse_args(["--scope", "BTCUSD", "--move", "down"]), "BTCUSD", "down",
        market=None, recaps=[],
    )
    assert coin.is_crypto is True and coin.market is None and coin.direction == "down"


class _Result:
    def __init__(self, kind, verdict, why):
        self.kind, self.verdict, self.why = kind, verdict, why


@pytest.mark.parametrize("direction, result, line", [
    ("up", _Result("company", "supported", "supported"),
     "move card: move:up:company — verdict supported, why supported"),
    ("down", _Result("none", "no_cause", "model_none"),
     "move card: move:down:none — verdict no_cause, why model_none"),
    ("up", _Result(None, None, "model_unavailable"),
     "move card: NO TAG (the sweeper would log this) — verdict None, why model_unavailable"),
    ("up", object(), "move card: NO TAG (the sweeper would log this) — verdict None, why None"),
])
def test_the_printed_move_outcome(direction, result, line):
    assert preview.move_outcome_line(direction, result) == line


def test_the_replay_calls_the_writers_move_methods():
    """The preview replays `generate_and_store`'s move path through the writer's own methods;
    each must exist, so a rename fails here rather than at the owner's terminal."""
    from app.services.news_insight_service import NewsInsightService

    for name in preview._MOVE_WRITER_METHODS:
        assert callable(getattr(NewsInsightService, name, None)), name


def test_the_market_context_comes_from_the_production_selector(monkeypatch):
    """`build_market_context` hands the writer exactly what `select_market_items` keeps (≤ 5,
    newest first), on the index basis — never the raw fixture."""
    import app.services.news_insight_service as insight_mod

    seen = {}

    def _select(rows, now):
        seen["rows"], seen["now"] = list(rows), now
        return tuple(rows[:1])

    monkeypatch.setattr(insight_mod, "select_market_items", _select)
    as_of = datetime(2026, 11, 23, 15, 33, tzinfo=timezone.utc)
    rows = [
        {"headline": "Later than the as-of", "article_url": "https://x.example.com/late",
         "published_at": "2026-11-23T16:00:00+00:00"},
        {"headline": "Before the as-of", "article_url": "https://x.example.com/early",
         "published_at": "2026-11-23T14:00:00+00:00"},
    ]
    ctx = preview.build_market_context(rows, as_of)
    assert ctx.basis == MARKET_BASIS_INDEX
    assert [r["headline"] for r in seen["rows"]] == ["Before the as-of"], "as-of applied first"
    assert seen["now"] == as_of
    assert [r["headline"] for r in ctx.items] == ["Before the as-of"]
