"""Guards for the market-awareness chat tools, and above all for the ONE paid path.

WHY THIS FILE EXISTS. Ask Cay AI could not answer "why". Asked why NAVN was down 22% it
restated the price and volume; asked why Basic Materials was lagging — a question the app
itself suggests — it replied that its tools only cover individual companies. Both were
accurate descriptions of a two-tool surface with no news and no sector data.

`chat_market_tools` closes that with an escalation ladder whose third tier is a grounded
Google Search at roughly $0.035 a call. Three things below are therefore load-bearing rather
than decorative:

  1. The `"today"` window label. It is a cache-identity component shared with the Updates
     sweeper AND the guard against answering a daily question with a 15-day narrative.
  2. The escalation gates. Every one of them is money.
  3. Cache-before-budget ordering, so a row someone already paid for is free to reuse.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_market_tools as cmt
from app.services.daily_move_attribution import Attribution, CauseKind, MoveContext



def _explanation(*, tier: str, kind: CauseKind, change: float = -22.0, tag: str | None = None):
    return SimpleNamespace(
        ticker="NAVN", company_name="Navan, Inc.", change_percent=change, price=20.26,
        tier=tier, z=4.1, industry_name="Software", industry_change_percent=-0.4,
        market_change_percent=-0.2,
        attribution=Attribution(
            kind=kind, tag=tag, detail="detail",
            context=MoveContext(change_percent=change, z=4.1),
            considered=[],
        ),
    )


@pytest.fixture
def no_news(monkeypatch):
    """Tier 2 stubbed out. Every test here is about tier 1 and tier 3."""
    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": []}),
    )


# ── Degradation: silence is never a finding ──────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreadable_move_is_reported_as_unreadable_not_flat(monkeypatch, no_news):
    """`attribute_ticker_move` returns None ONLY for an unreadable quote. Describing that as
    an unchanged day is the `describe_no_cause` lie in a different costume — and this repo has
    shipped the "NaN reaching max() and winning" version of it more than once."""
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=None)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False
    assert "unchanged" not in out["note"].lower() or "rather than" in out["note"]
    assert "change_percent" not in out


@pytest.mark.asyncio
async def test_a_failed_news_read_says_so_rather_than_reporting_no_news(monkeypatch):
    """An empty list and a failed call are different claims. `_MarketContext` carries
    `*_available` flags for exactly this reason: asserting a negative nobody checked is a lie
    on a credit-charged turn."""
    class _Boom:
        async def get_ticker_news(self, *a, **kw):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(
        "app.services.news_cache_service.get_news_cache_service", lambda: _Boom()
    )
    out = await cmt.fetch_ticker_news("NAVN")
    assert out["news_available"] is False
    assert "no news" in out["note"].lower()
    assert "articles" not in out


@pytest.mark.asyncio
async def test_market_snapshot_omits_a_failed_leg_rather_than_zeroing_it(monkeypatch):
    """Every leg degrades independently. A sector list of zeroes built out of an outage would
    let the model report a flat market that never happened."""
    class _Movers:
        async def get_sector_performance(self):
            raise RuntimeError("screener down")

        async def get_industry_performance(self):
            return [{"industry": "Steel", "sector": "Basic Materials",
                     "changesPercentage": -2.4}]

        async def get_scanner_inputs(self):
            raise RuntimeError("universe down")

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert "sectors" not in out, "a failed leg must be ABSENT, never an empty/zero list"
    assert out["leading_industries"][0]["industry"] == "Steel"
    assert "top_gainers" not in out


# ── The earnings direction conflict ──────────────────────────────────────────

def test_the_beat_and_miss_tags_match_the_attribution_module():
    """COUPLING GUARD. `_direction_conflict` matches on two tag strings owned by
    `daily_move_attribution`. A reword there would silently disable the note — the tool would
    keep working, keep returning a cause, and quietly go back to telling the model a stock
    fell because it beat estimates. Pin the strings so the reword fails here instead."""
    import inspect

    from app.services import daily_move_attribution as dma

    src = inspect.getsource(dma.detect_earnings)
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert f'tag = "{cmt._BEAT_TAG}"' in body
    assert f'tag = "{cmt._MISS_TAG}"' in body


@pytest.mark.parametrize(
    "tag,change,expect",
    [
        ("Earnings Beat", -21.7, "BEAT"),      # measured live: NAVN, 2026-09-10
        ("Earnings Miss", +8.4, "MISSED"),
        ("Earnings Beat", +8.4, None),          # agrees — no note
        ("Earnings Miss", -8.4, None),          # agrees — no note
        ("Earnings", -21.7, None),              # in-line print, neither beat nor miss
        (None, -21.7, None),
        ("Earnings Beat", None, None),          # unreadable move
    ],
)
def test_a_contradicting_earnings_reaction_is_flagged(tag, change, expect):
    note = cmt._direction_conflict(tag, change)
    if expect is None:
        assert note is None
    else:
        assert note is not None and expect in note
        assert "Do not say" in note


@pytest.mark.asyncio
async def test_the_conflict_note_reaches_the_tool_result(monkeypatch, no_news):
    """Wiring, not just the leaf. Testing a correct helper that nothing CALLS is the recurring
    failure mode in this repo — five mutations in one session survived by removing the call
    rather than the logic."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="extreme", kind=CauseKind.EARNINGS, change=-21.7, tag="Earnings Beat")
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert "BEAT" in out["important"]
    assert out["cause"] == "Earnings Beat"


@pytest.mark.asyncio
async def test_the_unusualness_note_never_claims_a_sigma_it_did_not_compute(monkeypatch, no_news):
    """σ is precomputed for the top-200 watchlist only, so a long-tail ticker legitimately has
    none and `classify_move` falls back to the fixed price band. Printing "4.1x its typical
    daily swing" from a band label would be a fabricated statistic."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="extreme", kind=CauseKind.NONE)
    exp.z = None                                   # no σ row — the fixed-band path
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert "judged on price alone" in out["how_unusual"]
    assert "x its typical" not in out["how_unusual"]

    # And the σ path DOES report the multiple — anti-vacuity for the assertion above.
    exp.z, exp.tier = 4.1, "Extreme"
    out = await cmt.explain_price_move("NAVN")
    assert "4.1x its typical daily swing" in out["how_unusual"]


# ── Outliers: a NaN must never reach the model ───────────────────────────────

def test_num_refuses_every_non_finite_value():
    """`x or 0.0` DOES NOT catch NaN — NaN is truthy, so `nan or 0.0` is `nan`. It then
    reaches `json.dumps`, which emits the bare token `NaN` (invalid JSON) into a tool result
    the model has to parse."""
    for bad in (float("nan"), float("inf"), float("-inf"), None, "", "abc", {}, []):
        assert cmt._num(bad) is None, bad
    # Anti-vacuity: real numbers survive, including a signed zero and a negative.
    assert cmt._num(-2.5349) == -2.53
    assert cmt._num(0.0) == 0.0
    assert cmt._num("3.5") == 3.5


@pytest.mark.asyncio
async def test_a_nan_sector_is_dropped_not_zeroed(monkeypatch):
    """`market_movers_service._group_performance` gates on `change is not None`, NOT on
    isfinite — so ONE malformed universe row makes a whole sector's mean NaN. Reporting that
    sector as 0.00% would be a fabricated flat day; the honest move is to omit it."""
    class _Movers:
        async def get_sector_performance(self):
            return [
                {"sector": "Technology", "changesPercentage": float("nan"), "constituents": 618},
                {"sector": "Energy", "changesPercentage": -0.33, "constituents": 237},
            ]

        async def get_industry_performance(self):
            return [{"industry": "Steel", "sector": "Basic Materials",
                     "changesPercentage": float("inf")}]

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert [s["sector"] for s in out["sectors"]] == ["Energy"]
    assert "leading_industries" not in out, "an infinite industry change must not be emitted"

    # The whole payload must be valid JSON with NO NaN token — this is what actually reaches
    # Gemini, via `json.dumps(result)[:8000]` in `stream_agentic`.
    import json

    encoded = json.dumps(out, allow_nan=False)
    assert "NaN" not in encoded and "Infinity" not in encoded


@pytest.mark.asyncio
async def test_the_move_payload_is_json_safe(monkeypatch, no_news):
    """Same guarantee on the other tool. A NaN industry delta arriving from a degraded
    universe must not turn the whole tool result into invalid JSON."""
    import json

    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    exp.industry_change_percent = float("nan")
    exp.market_change_percent = float("inf")
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    out = await cmt.explain_price_move("NAVN")

    assert "industry_change_percent" not in out
    assert "market_change_percent" not in out
    assert out["industry"] == "Software", "the industry NAME is still useful without its %"
    json.dumps(out, allow_nan=False)


# ── Never a dead end ─────────────────────────────────────────────────────────
#
# From the user, after a follow-up chip Cay AI had PROPOSED came back "I don't have specific
# information on what caused copper to drop today": *"i need all answer should be a reason for
# it. or at least, if there is no reason, then say #ticker move normally in a range like a
# normal up and down. or no clear catalyst news."*

@pytest.mark.asyncio
async def test_an_ordinary_move_still_gets_an_answer(monkeypatch):
    """A calm day is not an absence of an answer — it IS the answer, and it must be stated."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=0.32)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": [{"headline": "x"}]}),
    )
    out = await cmt.explain_price_move("KO")
    assert out["no_single_catalyst"] is True
    line = out["bottom_line"]
    assert line and "0.3% today" in line
    assert "No single catalyst" in line
    assert "inside its normal daily range" in out["how_unusual"]


@pytest.mark.asyncio
async def test_no_news_at_all_is_said_differently_from_a_failed_read(monkeypatch):
    """Three distinct states, three distinct sentences. Collapsing "we checked and there was
    nothing" into "we could not check" (or the reverse) is how a confident negative gets
    asserted out of an outage."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=0.32)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )

    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": []}),
    )
    assert "No company news was published today" in (await cmt.explain_price_move("KO"))["bottom_line"]

    monkeypatch.setattr(
        cmt, "fetch_ticker_news", AsyncMock(return_value={"news_available": False}),
    )
    line = (await cmt.explain_price_move("KO"))["bottom_line"]
    assert "could not be checked" in line
    assert "do not say there was none" in line


@pytest.mark.asyncio
async def test_a_real_cause_is_never_talked_over_by_the_fallback(monkeypatch, no_news):
    """The bottom line is the answer of LAST resort. Emitting it beside a found cause would
    let the model close a correct earnings explanation with "no catalyst is visible"."""
    from app.services.daily_move_attribution import CauseKind

    for kind in (CauseKind.EARNINGS, CauseKind.COMPANY_NEWS, CauseKind.SECTOR, CauseKind.MARKET):
        exp = _explanation(tier="Extreme", kind=kind, tag="Q3 Earnings")
        monkeypatch.setattr(
            "app.services.widget_movers_service.get_widget_movers_service",
            lambda exp=exp: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
        )
        out = await cmt.explain_price_move("NAVN")
        assert "bottom_line" not in out, kind
        assert "no_single_catalyst" not in out, kind


@pytest.mark.asyncio
async def test_an_industry_outside_the_top_and_bottom_five_is_still_visible(monkeypatch):
    """The copper case, pinned. Five of ~150 industries told the day's story but left every
    other named industry unanswerable — the tool knew the number and could not show it."""
    # Copper MID-PACK, deliberately: with it at either extreme this test passes through
    # `lagging_industries` and proves nothing about the extra list. (The first version of
    # this fixture did exactly that and survived a mutation deleting the feature.)
    rows = [{"industry": f"Up{i}", "sector": "S", "changesPercentage": 10.0 - i * 0.4}
            for i in range(20)]
    rows.append({"industry": "Copper", "sector": "Basic Materials", "changesPercentage": -6.4})
    rows += [{"industry": f"Down{i}", "sector": "S", "changesPercentage": -7.0 - i * 0.4}
             for i in range(20)]
    rows.sort(key=lambda r: -r["changesPercentage"])

    class _Movers:
        async def get_sector_performance(self):
            return [{"sector": "Basic Materials", "changesPercentage": -2.5, "constituents": 280}]

        async def get_industry_performance(self):
            return rows

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    edges = {r["industry"] for r in
             out.get("leading_industries", []) + out.get("lagging_industries", [])}
    assert "Copper" not in edges, "fixture is wrong — Copper must be mid-pack to test anything"
    extra = {r["industry"] for r in out.get("other_industries_that_moved", [])}
    assert "Copper" in extra


@pytest.mark.asyncio
async def test_a_flat_industry_is_not_padded_into_the_list(monkeypatch):
    """Anti-vacuity, and a cap guard: an industry that did not move needs no row — its honest
    answer is "it moved normally" — and 150 of them would blow the tool-result cap."""
    rows = [{"industry": f"Flat{i}", "sector": "S", "changesPercentage": 0.01} for i in range(60)]
    rows.append({"industry": "Copper", "sector": "Basic Materials", "changesPercentage": -6.4})

    class _Movers:
        async def get_sector_performance(self):
            return []

        async def get_industry_performance(self):
            return rows

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert not any(r["industry"].startswith("Flat")
                   for r in out.get("other_industries_that_moved", []))
    import json
    assert len(json.dumps(out)) < 8000, "the tool result must fit inside stream_agentic's cap"


# ── 2026-09-16: the session word travels; crashes are counted ──

@pytest.mark.asyncio
async def test_explain_price_move_carries_the_session_and_words_the_bottom_line_with_it(monkeypatch):
    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=-1.2)
    exp.session_word = "on Fri"
    exp.session_date = "2026-09-11"

    class _WM:
        async def attribute_ticker_move(self, sym):
            return exp
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    monkeypatch.setattr(cmt, "fetch_ticker_news", AsyncMock(return_value={"news_available": True, "articles": []}))
    out = await cmt.explain_price_move("NAVN")
    assert out["session"] == "on Fri" and out["session_date"] == "2026-09-11"
    assert "on Fri" in out["bottom_line"]
    # The WHOLE line, not just the arithmetic head: the news clause used to say "today"
    # under a move worded "on Fri" — a cross-session sentence handed to the model (F07-9).
    assert "today" not in out["bottom_line"].lower(), out["bottom_line"]
    assert "No company news was published on Fri." in out["bottom_line"]


@pytest.mark.parametrize("news, expect", [
    ({"news_available": False}, "Friday's news could not be checked"),
    ({"news_available": True, "articles": []}, "No company news was published on Fri."),
    ({"news_available": True, "articles": [{"title": "x"}]}, "stands out in Friday's news."),
])
def test_every_news_clause_of_the_bottom_line_names_the_session(news, expect):
    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=-1.2)
    exp.session_word = "on Fri"
    line = cmt._bottom_line(exp, news)
    assert expect in line, line
    assert "today" not in line.lower(), line


@pytest.mark.parametrize("news, expect", [
    ({"news_available": False}, "Today's news could not be checked"),
    ({"news_available": True, "articles": []}, "No company news was published today."),
    ({"news_available": True, "articles": [{"title": "x"}]}, "stands out in today's news."),
])
def test_the_today_wording_is_byte_identical_to_before(news, expect):
    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=-1.2)
    exp.session_word = "today"
    assert expect in cmt._bottom_line(exp, news)


@pytest.mark.asyncio
async def test_an_attribution_crash_is_an_error_result_not_an_unreadable_quote(monkeypatch):
    """The stream door counts a tool as failed only when its result carries `error`; a crash
    upstream used to come back in the same shape as a genuinely unreadable move, so the turn
    was charged while the identical outage on `get_market_snapshot` was refunded."""
    class _WM:
        async def attribute_ticker_move(self, sym):
            raise RuntimeError("supabase 520")
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False and out["error"].startswith("RuntimeError")


@pytest.mark.asyncio
async def test_an_unreadable_move_without_a_crash_carries_no_error(monkeypatch):
    class _WM:
        async def attribute_ticker_move(self, sym):
            return None
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False and "error" not in out


@pytest.mark.asyncio
async def test_a_news_feed_crash_is_an_error_result(monkeypatch):
    class _NC:
        async def get_ticker_news(self, *a, **k):
            raise RuntimeError("fmp 503")
    monkeypatch.setattr("app.services.news_cache_service.get_news_cache_service", lambda: _NC())
    out = await cmt.fetch_ticker_news("AAPL")
    assert out["news_available"] is False and out["error"].startswith("RuntimeError")


# ── Which session the snapshot describes (F7-4) ───────────────────────────────
#
# At 07:00 ET on a Monday the screener still reports Friday's close, so every sector,
# industry and mover percentage is FRIDAY's move. The universe stamps that on every row
# and the widget refuses to say "today" about it; this tool used to drop the stamp and
# the model said "Technology is up 0.8% today" about a session that ended three days
# earlier.


def _snapshot_movers(*, sector_date=None, industry_date=None, universe=None):
    class _Movers:
        async def get_sector_performance(self):
            row = {"sector": "Technology", "changesPercentage": 0.8, "constituents": 300}
            if sector_date:
                row["date"] = sector_date
            return [row]

        async def get_industry_performance(self):
            row = {"industry": "Semiconductors", "sector": "Technology",
                   "changesPercentage": 1.9}
            if industry_date:
                row["date"] = industry_date
            return [row]

        async def get_scanner_inputs(self):
            if universe is None:
                return ({}, {})
            change_map = {s: r["changePercentage"] for s, r in universe.items()}
            return (universe, change_map)
    return _Movers()


def _quality_row(symbol, change, session=None):
    row = {"symbol": symbol, "companyName": symbol, "price": 100.0, "marketCap": 5e10,
           "volume": 1e7, "averageVolume": 1e7, "changePercentage": change,
           "exchange": "NASDAQ", "isEtf": False, "isFund": False,
           "sector": "Technology", "industry": "Semiconductors"}
    if session:
        row["changeSession"] = session
    return row


def _stub_cards(monkeypatch):
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
async def test_a_prior_session_snapshot_is_worded_on_fri_not_today(monkeypatch):
    from datetime import date
    universe = {"NVDA": _quality_row("NVDA", 4.2, "2026-09-11"),
                "INTC": _quality_row("INTC", -3.1, "2026-09-11")}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11",
                                 universe=universe),
    )
    _stub_cards(monkeypatch)
    # Monday pre-market: the live session is the 14th, the numbers are Friday the 11th's.
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"] == {
        "date": "2026-09-11", "word": "on Fri",
        "note": out["as_of_session"]["note"],
    }
    assert "today" not in out["as_of_session"]["word"]
    assert '"today"' in out["as_of_session"]["note"], "the note must forbid the word"
    assert out["sectors"][0]["session_date"] == "2026-09-11"
    assert out["leading_industries"][0]["session_date"] == "2026-09-11"
    assert out["top_gainers"][0]["session_date"] == "2026-09-11"
    assert out["top_losers"][0]["session_date"] == "2026-09-11"


@pytest.mark.asyncio
async def test_a_current_session_snapshot_says_today(monkeypatch):
    from datetime import date
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-14", industry_date="2026-09-14"),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"]["word"] == "today"
    assert out["as_of_session"]["date"] == "2026-09-14"


@pytest.mark.asyncio
async def test_a_saturday_snapshot_of_fridays_session_is_worded_on_fri(monkeypatch):
    from datetime import date
    """On a weekend the LIVE session is Friday and every stamp is Friday — but it is not
    today. The model said "Technology is up 0.8% today" all weekend."""
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11"),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 11))          # Friday
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 12))                   # Saturday
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"] == {"date": "2026-09-11", "word": "on Fri",
                                    "note": out["as_of_session"]["note"]}


@pytest.mark.asyncio
async def test_an_unstamped_snapshot_carries_no_session_claim(monkeypatch):
    """Older-shape rows (no `date`, no `changeSession`) must not INVENT a session: the
    key is absent, and no row carries a `session_date`."""
    universe = {"NVDA": _quality_row("NVDA", 4.2)}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(universe=universe),
    )
    _stub_cards(monkeypatch)
    out = await cmt.fetch_market_snapshot()
    assert "as_of_session" not in out
    assert "session_date" not in out["sectors"][0]
    assert "session_date" not in out["top_gainers"][0]


@pytest.mark.asyncio
async def test_the_snapshot_session_is_the_mode_not_the_newest_stamp(monkeypatch):
    """One early premarket print that has ticked into Monday must not relabel Friday's
    whole snapshot as today's — and the odd row keeps its own, differing, date."""
    from datetime import date
    universe = {"NVDA": _quality_row("NVDA", 4.2, "2026-09-11"),
                "AMD": _quality_row("AMD", 3.0, "2026-09-11"),
                "EARLY": _quality_row("EARLY", 2.0, "2026-09-14")}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11",
                                 universe=universe),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"]["date"] == "2026-09-11"
    assert out["as_of_session"]["word"] == "on Fri"
    by_sym = {r["symbol"]: r for r in out["top_gainers"]}
    assert by_sym["EARLY"]["session_date"] == "2026-09-14"
    assert by_sym["NVDA"]["session_date"] == "2026-09-11"


@pytest.mark.asyncio
async def test_a_garbage_session_stamp_never_crashes_the_snapshot(monkeypatch):
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="not-a-date", industry_date="not-a-date"),
    )
    _stub_cards(monkeypatch)
    out = await cmt.fetch_market_snapshot()
    assert "as_of_session" not in out
    assert out["sectors"][0]["change_percent"] == 0.8


def test_as_of_session_words_a_prior_session_by_weekday(monkeypatch):
    from collections import Counter
    from datetime import date
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 16))
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 16))
    assert cmt._as_of_session(Counter({"2026-09-15": 3}))["word"] == "on Tue"
    assert cmt._as_of_session(Counter({"2026-09-16": 3}))["word"] == "today"
    # A stamp AHEAD of the live session (clock skew, a stale `session_trading_date`
    # patch) is still "today", never a future weekday.
    assert cmt._as_of_session(Counter({"2026-09-17": 3}))["word"] == "today"
    assert cmt._as_of_session(Counter()) is None


# ── A quote-source OUTAGE is an upstream failure; an unquotable symbol is not (2026-09-17) ──
#
# `price_service.get_quotes` folds a universe failure into `{}`, so `attribute_ticker_move`
# saw the same `ranked == []` for "FMP is down" and "this symbol has no usable row" — and
# `explain_price_move` answered both as `move_readable: False` with no `error`, so an
# outage on the only tool was charged in full while the identical outage on
# `get_ticker_news` was refunded. The discriminator is the index band that rides on every
# batch: no SPY/QQQ/DIA row at all means the SOURCE failed.


def _movers_with_quotes(monkeypatch, quotes_result):
    from app.services import widget_movers_service as wm
    svc = wm.WidgetMoversService()
    if isinstance(quotes_result, BaseException):
        async def _quotes(self, symbols):
            raise quotes_result
    else:
        async def _quotes(self, symbols):
            return dict(quotes_result)
    monkeypatch.setattr(wm.WidgetMoversService, "_quotes", _quotes)
    # `widget_movers_service` imports this factory at MODULE level, so the binding it calls
    # is its own — patching `volatility_cache_service` left the real σ read (a Supabase
    # SELECT on `ticker_volatility_cache`) on the path.
    monkeypatch.setattr(wm, "get_volatility_cache_service",
                        lambda: SimpleNamespace(get_sigmas_bulk=AsyncMock(return_value={})))
    monkeypatch.setattr("app.services.news_insight_service.get_news_insight_service",
                        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})))
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: svc)
    return svc


@pytest.mark.asyncio
async def test_a_quote_source_that_raises_is_an_upstream_error(monkeypatch):
    _movers_with_quotes(monkeypatch, RuntimeError("fmp 503"))
    out = await cmt.explain_price_move("NVDA")
    assert out["move_readable"] is False
    assert out["upstream"] is True and "QuoteSourceUnavailable" in out["error"]


@pytest.mark.asyncio
async def test_an_empty_batch_with_no_index_band_is_an_outage(monkeypatch):
    """`get_quotes` swallowed the failure into `{}`: the band is the tell."""
    _movers_with_quotes(monkeypatch, {})
    out = await cmt.explain_price_move("NVDA")
    assert out["move_readable"] is False
    assert out.get("upstream") is True and "index band empty" in out["error"]


@pytest.mark.asyncio
async def test_a_symbol_missing_from_a_healthy_batch_is_unreadable_but_not_an_error(monkeypatch):
    """The band came back, the symbol did not: the model's miss (or a delisted name) —
    answered as unreadable and CHARGED, exactly as before."""
    from app.services.widget_movers_service import _INDEX_SYMBOLS
    band = {s: {"symbol": s, "price": 500.0, "changePercentage": 0.4, "changeSession": "2026-09-16"}
            for s, _ in _INDEX_SYMBOLS}
    _movers_with_quotes(monkeypatch, band)
    out = await cmt.explain_price_move("ZZZZ")
    assert out["move_readable"] is False
    assert "error" not in out and "upstream" not in out


# ── report chat's web search: the three-way claim ──
#
# `chat_web_search_service` meters its Brave searches through the same `chat_usage_budget` RPC,
# but must tell a CAP ("the daily limit is reached" — the turn stays charged) from an OUTAGE
# (an upstream failure). `_claim_bucket_status` says which. (explain_price_move's own paid
# escalation, which a web turn used to skip, was retired with Google Search grounding; the
# handler still passes `web_escalation=False`, now an accepted no-op.)


@pytest.mark.asyncio
async def test_claim_bucket_status_distinguishes_cap_from_outage(monkeypatch):
    def _svc(fn):
        monkeypatch.setattr(cmt, "get_chat_budget_service", lambda: SimpleNamespace(try_claim_turn=fn))

    _svc(lambda *a, **k: 3)
    assert await cmt._claim_bucket_status("b", 10, "t") == "ok"
    _svc(lambda *a, **k: -1)
    assert await cmt._claim_bucket_status("b", 10, "t") == "capped"

    def _down(*a, **k):
        raise cmt.ChatBudgetUnavailable("db down")
    _svc(_down)
    assert await cmt._claim_bucket_status("b", 10, "t") == "unavailable"

    def _bug(*a, **k):
        raise KeyError("surprise")
    _svc(_bug)
    assert await cmt._claim_bucket_status("b", 10, "t") == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", [None, "NAVN"])
async def test_the_handler_map_skips_the_paid_escalation_only_on_a_web_turn(screen):
    from app.services.agents.chat_tools import build_chat_tool_handlers

    seen: list = []

    class _Svc:
        @staticmethod
        def _chat_symbol(raw):
            return raw

        async def _fetch_price_move_data(self, ticker, is_crypto=None, user_id=None, **kw):
            seen.append((ticker, is_crypto, user_id, kw))
            return {"ok": True}

    svc = _Svc()
    plain = build_chat_tool_handlers(svc, screen_symbol=screen, screen_asset_type="STOCK", user_id="u1")
    web = build_chat_tool_handlers(svc, screen_symbol=screen, screen_asset_type="STOCK", user_id="u1",
                                   web_turn=object())
    await plain["explain_price_move"]({"ticker": "NAVN"})
    await web["explain_price_move"]({"ticker": "NAVN"})
    equity = False if screen else None
    assert seen[0] == ("NAVN", equity, "u1", {}), "an ordinary turn is unchanged"
    assert seen[1] == ("NAVN", equity, "u1", {"web_escalation": False})


def test_the_service_forwards_the_flag_only_when_it_is_off():
    """Source-scan, comment-free: the default call stays byte-identical (the pin above), and the
    web turn's call names `web_escalation=False` explicitly."""
    import inspect
    import re

    from app.services.chat_service import ChatService

    src = "\n".join(l for l in inspect.getsource(ChatService._fetch_price_move_data).splitlines()
                    if not l.strip().startswith("#"))
    assert re.search(r"explain_price_move\(ticker, is_crypto=is_crypto, user_id=user_id,\s*"
                     r"web_escalation=False\)", src)
    assert "explain_price_move(ticker, is_crypto=is_crypto, user_id=user_id)" in src


# ── Google Search grounding retired (2026-10-02): the ladder has no paid tier ──
#
# The third tier was a grounded Google Search through `price_catalyst_service`, shared across
# users through a 24 h cache — which the Grounding terms forbid. An extreme, unexplained move —
# exactly the input that used to unlock it — now ends at the deterministic bottom line.

@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{}, {"user_id": "u1"}, {"web_escalation": False},
                                {"user_id": "u1", "web_escalation": True}])
async def test_an_extreme_unexplained_move_ends_at_the_bottom_line_with_no_web_tier(monkeypatch, no_news, kw):
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE, change=-22.0)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    out = await cmt.explain_price_move("NAVN", **kw)
    assert "web_research" not in out
    assert out["no_single_catalyst"] is True and out["bottom_line"]


def test_the_market_tools_hold_no_grounded_tier():
    """Comment-free source scan: no path back to the retired service or its budget."""
    import inspect

    src = "\n".join(l for l in inspect.getsource(cmt).splitlines() if not l.strip().startswith("#"))
    code_only = src.split('"""', 2)[-1]  # drop the module docstring, which tells the history
    for name in ("price_catalyst_service", "get_catalyst", "_maybe_web_catalyst",
                 "_claim_web_search", "CHAT_WEB_SEARCH_"):
        assert name not in code_only, name
