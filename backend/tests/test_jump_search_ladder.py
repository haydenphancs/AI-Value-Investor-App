"""The jump search's ladder, pacing, digest and fallback triggers (2026-11-27).

Owner decisions 2026-11-27 ("do Brave 80 a day, and use Claude API … as fallback"): on 2026-11-23
eighteen jumpers spent the whole 40-search bucket by 10:07 ET, and the pages that explained CRWV
appeared at ~12:00 with nothing left; on 2026-11-25 PLUG's "up" search kept pages about the
morning's slide. Pinned here, with Brave, the budget store and the fallback module replaced at the
bindings the service uses (no network):

* the ladder per (scope, direction): 0, 20, 45, 90 min, 3 h, 5 h; one step per sweep; missed steps
  skipped, never burst; at most 8 counted searches per scope per ET day; a flip starts the other
  direction at once and each direction keeps its pages; steps skipped while a company cause is held;
* failures: not_run uses no step (retry after 5 min); an auth/plan refusal turns the search off for
  the ET day with ONE ERROR; a 429 holds every scope; a timeout or billed error uses the step;
* pacing (`paced_limit`) and its never-latching refusal; fewest-searches-first ordering;
* the digest: newest first, opposite-direction and price-only drops, the merge; the direction
  lexicon's idioms ("falls short", "sinks $2 billion into", another subject's fall before the name)
  and names ending in a noun it ignores ("Target", "Barrick Gold") (review 2026-11-27);
* the stamp (review 2026-11-27): `refreshed_at` moves only when the set gains an unread page —
  never when a page ages out — and never past the `now` of the sweep that read it; pages the card
  found no cause in rank after every other page, so a fallback page can replace them;
* the fallback triggers T1/T2/T3 and their guards; the detached task's logging and `aclose`.

Every page text and company here is HAND-WRITTEN — never copied from a web page or from a card
written from one.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.integrations import brave_search
from app.services import jump_web_search_service as jws
from app.services.chat_budget_service import ChatBudgetUnavailable
from app.services.insight_move import MoveOutcome
from app.services.jump_web_search_service import (
    LADDER_OFFSETS_SECONDS,
    MAX_KEPT,
    MAX_SEARCHES_PER_REFRESH,
    MAX_SEARCHES_PER_SCOPE_PER_DAY,
    JumpCandidate,
    JumpWebSearchService,
    WebPage,
    describes_opposite_move,
    digest,
    merge_pages,
    paced_limit,
)

# Wed 2026-11-25 (a full trading day): 10:00 ET in the session, 16:30 ET after the close.
SESSION = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)
NAME = "Plug Power Inc."


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row(url, title="Plug Power signs a hydrogen supply agreement with a regional utility",
         snippet="The agreement covers deliveries over three years, the company said.",
         at=None):
    return {"url": url, "title": title, "description": snippet,
            "page_age": _iso(at) if at is not None else None}


class _Budget:
    def __init__(self):
        self.claims, self.refunds = [], []
        self.used = 0
        self.raise_exc = None

    def try_claim_turn(self, bucket, limit):
        self.claims.append(limit)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.used >= limit:
            return -1
        self.used += 1
        return self.used

    def refund_turn(self, bucket):
        self.refunds.append(bucket)
        self.used = max(0, self.used - 1)


class _Fallback:
    """A fake fallback module: `enabled()` and `lookup(...)` as the service calls them."""

    def __init__(self):
        self.on = False          # the `fb` fixture turns it on
        self.calls = []
        self.rows = []
        self.gate = None
        self.raise_exc = None

    def enabled(self):
        return self.on

    async def lookup(self, *, scope, query_name, direction, now, trigger):
        self.calls.append({"scope": scope, "query_name": query_name, "direction": direction,
                           "now": now, "trigger": trigger})
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(rows=[dict(r) for r in self.rows], status="ok",
                               counts=Counter({"web_searches": 1}))


@pytest.fixture
def env(monkeypatch):
    state = {"queries": [], "payload": None, "raise": None, "budget": _Budget(),
             "fallback": _Fallback()}

    async def _search(query, *, count=10, freshness=None, extra_snippets=False):
        state["queries"].append(query)
        if state["raise"] is not None:
            raise state["raise"]
        payload = state["payload"]
        return payload(query) if callable(payload) else (payload or {"results": []})

    monkeypatch.setattr(jws.brave_search, "web_search", _search)
    monkeypatch.setattr(jws.brave_search, "is_configured", lambda: True)
    monkeypatch.setattr(jws, "get_chat_budget_service", lambda: state["budget"])
    monkeypatch.setattr(jws, "_fallback_module", lambda: state["fallback"])
    monkeypatch.setattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "UPDATES_JUMP_WEB_SEARCH_DAILY_CAP", 80)
    return state


@pytest.fixture
def fb(env):
    """`env` with the fallback provider switched on."""
    env["fallback"].on = True
    return env


def _plug(pct=12.0, score=30.0, held_kind=None, scope="PLUG", name=NAME):
    return JumpCandidate(scope=scope, name=name, change_percent=pct, score=score,
                         held_kind=held_kind)


def _run(svc, candidates, now):
    return asyncio.run(svc.refresh(candidates, now))


async def _sweep(svc, candidates, now):
    counts = await svc.refresh(candidates, now)
    await asyncio.gather(*list(svc._fallback_tasks), return_exceptions=True)
    return counts


def _searched_at(env, svc, candidate, start, minutes):
    """Sweep every 5 minutes for ``minutes``; return the minute offsets that called Brave."""
    hits = []
    for m in range(0, minutes + 1, 5):
        before = len(env["queries"])
        _run(svc, [candidate], start + timedelta(minutes=m))
        if len(env["queries"]) > before:
            hits.append(m)
    return hits


# ── the ladder ─────────────────────────────────────────────────────────────

def test_the_ladder_searches_at_0_20_45_90_180_300_minutes(env):
    svc = JumpWebSearchService()
    assert LADDER_OFFSETS_SECONDS == (0, 1200, 2700, 5400, 10800, 18000)
    assert _searched_at(env, svc, _plug(), SESSION, 330) == [0, 20, 45, 90, 180, 300]
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=335)) == {"ladder_done": 1}


def test_missed_steps_are_skipped_never_burst(env):
    svc = JumpWebSearchService()
    assert _run(svc, [_plug()], SESSION) == {"no_results": 1}
    # Away for 100 minutes (a restart window, a pause in candidacy): steps 20/45/90 are all due.
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=100)) == {"no_results": 1}
    assert len(env["queries"]) == 2
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=105)) == {"not_due": 1}
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=180)) == {"no_results": 1}


def test_a_flip_starts_a_new_episode_at_once_and_keeps_the_old_pages(env):
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://news-one.example.com/a", at=AFTER)]}
    _run(svc, [_plug(pct=12.0)], AFTER)
    env["payload"] = {"results": [_row(
        "https://news-two.example.com/b",
        title="Plug Power delays its Texas plant opening",
        snippet="The company cited permitting, it said on Wednesday.", at=AFTER)]}
    later = AFTER + timedelta(minutes=5)
    assert _run(svc, [_plug(pct=-9.0)], later) == {"ok": 1}
    assert env["queries"][-1] == "why is plug power stock down today"
    up, _ = svc.context("PLUG", later, direction="up")
    down, _ = svc.context("PLUG", later, direction="down")
    assert [p.url for p in up] == ["https://news-one.example.com/a"]
    assert [p.url for p in down] == ["https://news-two.example.com/b"]


def test_a_reflip_resumes_the_earlier_episode(env):
    svc = JumpWebSearchService()
    _run(svc, [_plug(pct=12.0)], SESSION)                                    # up, step 0
    _run(svc, [_plug(pct=-9.0)], SESSION + timedelta(minutes=5))             # down, step 0
    # Back up at +25: the UP episode's own +20 step is due by its own clock.
    assert _run(svc, [_plug(pct=11.0)], SESSION + timedelta(minutes=25)) == {"no_results": 1}
    assert _run(svc, [_plug(pct=11.0)], SESSION + timedelta(minutes=30)) == {"not_due": 1}
    # Down again at +30: the DOWN episode's +20 step (from +5) is due.
    assert _run(svc, [_plug(pct=-8.0)], SESSION + timedelta(minutes=30)) == {"no_results": 1}
    assert env["queries"] == ["why is plug power stock up today", "why is plug power stock down today",
                              "why is plug power stock up today", "why is plug power stock down today"]


def test_at_most_eight_counted_searches_per_scope_per_day(env):
    svc = JumpWebSearchService()
    plan = [(0, 12.0), (5, -9.0), (20, 12.0), (25, -9.0), (45, 12.0), (50, -9.0),
            (90, 12.0), (95, -9.0), (180, 12.0), (185, -9.0), (300, 12.0)]
    statuses = [next(iter(_run(svc, [_plug(pct=p)], SESSION + timedelta(minutes=m))))
                for m, p in plan]
    assert MAX_SEARCHES_PER_SCOPE_PER_DAY == 8
    assert len(env["queries"]) == 8
    assert statuses[8:] == ["day_limit", "day_limit", "day_limit"]


def test_not_run_failures_do_not_use_a_step_and_retry_after_five_minutes(env):
    svc = JumpWebSearchService()
    env["raise"] = brave_search.BraveSearchUnavailableException("connect", not_run=True)
    assert _run(svc, [_plug()], SESSION) == {"error": 1}
    assert len(env["budget"].refunds) == 1 and svc._days["PLUG"].searches == 0
    env["raise"] = None
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=2)) == {"retry_wait": 1}
    # Step 0 again (it was never used), five minutes later.
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=5)) == {"no_results": 1}
    assert svc._days["PLUG"].searches == 1 and len(env["queries"]) == 2


@pytest.mark.parametrize("exc", [
    brave_search.BraveSearchAuthException("401", not_run=True, status=401),
    brave_search.BraveSearchRequestException("422", not_run=True, status=422),
])
def test_an_auth_or_plan_refusal_turns_the_search_off_for_the_day_with_one_error(env, caplog, exc):
    caplog.set_level(logging.INFO, logger=jws.__name__)
    svc = JumpWebSearchService()
    env["raise"] = exc
    assert _run(svc, [_plug()], AFTER) == {"refused": 1}
    assert env["budget"].refunds                                   # never billed
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    assert _run(svc, [other], AFTER + timedelta(minutes=1)) == {"provider_off": 1}
    assert _run(svc, [other], AFTER + timedelta(minutes=21)) == {"provider_off": 1}
    assert len(env["queries"]) == 1
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "OFF for the rest of the ET day" in errors[0].getMessage()
    # The next ET day searches again.
    env["raise"] = None
    assert _run(svc, [other], AFTER + timedelta(days=1)) == {"no_results": 1}


def test_a_429_holds_every_scope_for_its_retry_after(env):
    svc = JumpWebSearchService()
    env["raise"] = brave_search.BraveSearchRateLimitException("429", retry_after=120.0)
    assert _run(svc, [_plug()], AFTER) == {"rate_limited": 1}
    env["raise"] = None
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    assert _run(svc, [other], AFTER + timedelta(minutes=1)) == {"rate_limited_wait": 1}
    assert _run(svc, [other], AFTER + timedelta(minutes=3)) == {"no_results": 1}
    # The refused scope itself waits its own five minutes (the step was not used).
    assert _run(svc, [_plug()], AFTER + timedelta(minutes=3)) == {"retry_wait": 1}
    assert _run(svc, [_plug()], AFTER + timedelta(minutes=5)) == {"no_results": 1}


@pytest.mark.parametrize("kind", ["timeout", "billed"])
def test_timeout_and_billed_errors_use_the_step(env, monkeypatch, kind):
    svc = JumpWebSearchService()
    if kind == "timeout":
        async def _slow(query, **kw):
            await asyncio.sleep(10)

        monkeypatch.setattr(jws.brave_search, "web_search", _slow)
        monkeypatch.setattr(settings, "BRAVE_SEARCH_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(jws, "_HARD_BOUND_SLACK", 0.01)
        assert _run(svc, [_plug()], AFTER) == {"timeout": 1}
    else:
        env["raise"] = brave_search.BraveSearchUnavailableException("502", not_run=False)
        assert _run(svc, [_plug()], AFTER) == {"error": 1}
    assert env["budget"].refunds == [] and svc._days["PLUG"].searches == 1
    assert _run(svc, [_plug()], AFTER + timedelta(minutes=5)) == {"not_due": 1}


def test_a_held_company_cause_skips_steps_and_a_market_cause_keeps_searching(env):
    svc = JumpWebSearchService()
    assert _run(svc, [_plug(held_kind="company")], AFTER) == {"skipped_company": 1}
    assert env["queries"] == [] and env["budget"].claims == []
    # The skipped step is used: the next one is +20.
    assert _run(svc, [_plug(held_kind="market")], AFTER + timedelta(minutes=5)) == {"not_due": 1}
    assert _run(svc, [_plug(held_kind="market")], AFTER + timedelta(minutes=20)) == {
        "no_results": 1}


def test_a_company_cause_written_for_the_other_direction_never_stops_this_search(env):
    svc = JumpWebSearchService()
    _run(svc, [_plug(pct=12.0)], AFTER)
    svc.note_move_outcome("PLUG", AFTER, MoveOutcome(
        kind="company", direction="up", verdict="supported", why="judged", written=True))
    # The stock reverses; the gate still reports the held card's kind.
    assert _run(svc, [_plug(pct=-9.0, held_kind="company")], AFTER + timedelta(minutes=5)) == {
        "no_results": 1}
    assert env["queries"][-1] == "why is plug power stock down today"


def test_first_searches_go_first_on_a_broad_day(env):
    svc = JumpWebSearchService()
    cands = [JumpCandidate(f"Z{i:02d}", f"Zephyr{i:02d} Corp", 9.0, score=10.0 + i)
             for i in range(18)]
    by_rank = sorted(cands, key=lambda c: -c.score)
    first = _run(svc, cands, AFTER)
    assert first == {"no_results": MAX_SEARCHES_PER_REFRESH, "sweep_limit": 10}
    assert set(env["queries"]) == {f"why is zephyr{c.scope[1:]} stock up today"
                                   for c in by_rank[:8]}
    env["queries"].clear()
    # At +20 the first eight are due their SECOND search; the ten never searched go first.
    _run(svc, cands, AFTER + timedelta(minutes=20))
    assert set(env["queries"]) == {f"why is zephyr{c.scope[1:]} stock up today"
                                   for c in by_rank[8:16]}


def test_coins_are_never_searched(env):
    svc = JumpWebSearchService()
    coin = JumpCandidate("BTCUSD", "Bitcoin", 15.0, 40.0)
    assert _run(svc, [coin], AFTER) == {"not_equity": 1}
    assert env["queries"] == [] and env["budget"].claims == [] and env["fallback"].calls == []
    assert svc.episode_exhausted("BTCUSD", AFTER, direction="up") is True


# ── pacing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("when,cap,expected", [
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), 80, 12),    # 09:00 ET: 15%
    (datetime(2026, 11, 25, 14, 30, tzinfo=timezone.utc), 80, 24),   # 09:30 ET: 30%
    (datetime(2026, 11, 25, 15, 30, tzinfo=timezone.utc), 80, 32),   # 10:30 ET
    (datetime(2026, 11, 25, 17, 0, tzinfo=timezone.utc), 80, 45),    # 12:00 ET
    (datetime(2026, 11, 25, 20, 59, tzinfo=timezone.utc), 80, 79),   # 15:59 ET
    (datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc), 80, 80),    # 16:00 ET: the close
    (datetime(2026, 11, 27, 16, 15, tzinfo=timezone.utc), 80, 52),   # half day 11:15 ET: 65%
    (datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc), 80, 80),    # half day 13:00 ET: closed
    (datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc), 80, 80),    # Saturday
    (datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc), 80, 80),    # Thanksgiving
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), 0, 0),
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), 1, 1),
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), -3, 0),
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), "x", 0),
    (datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc), True, 0),
])
def test_paced_limit_values(when, cap, expected):
    assert paced_limit(cap, when) == expected


def test_paced_refusal_never_latches_the_day(env):
    svc = JumpWebSearchService()
    env["budget"].used = 28                                       # 10:00 ET: paced limit 28
    assert _run(svc, [_plug()], SESSION) == {"paced": 1}
    assert env["budget"].claims == [28] and svc._capped_day is None
    # The same limit is not asked again; another scope is paced without a store round trip.
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    assert _run(svc, [other], SESSION + timedelta(minutes=1)) == {"paced": 1}
    assert env["budget"].claims == [28]
    # The limit grows through the session: the step that waited runs.
    assert _run(svc, [_plug()], SESSION + timedelta(minutes=30)) == {"no_results": 1}
    assert env["budget"].claims[-1] == 32 and len(env["queries"]) == 1


def test_a_refusal_at_the_full_cap_latches_the_day(env):
    svc = JumpWebSearchService()
    env["budget"].used = 80
    assert _run(svc, [_plug()], AFTER) == {"capped": 1}
    assert svc._capped_day is not None and env["budget"].claims == [80]
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    assert _run(svc, [other], AFTER) == {"capped": 1}
    assert env["budget"].claims == [80]


def test_a_budget_outage_keeps_the_step_due(env):
    svc = JumpWebSearchService()
    env["budget"].raise_exc = ChatBudgetUnavailable("rpc down")
    assert _run(svc, [_plug()], AFTER) == {"budget_unavailable": 1}
    env["budget"].raise_exc = None
    assert _run(svc, [_plug()], AFTER + timedelta(minutes=5)) == {"no_results": 1}


# ── the digest ─────────────────────────────────────────────────────────────

def _digest(rows, direction=None, now=AFTER, **kw):
    return digest({"results": rows}, "PLUG", NAME, now, direction=direction, **kw)


def test_survivors_are_ranked_newest_first_not_by_rank():
    rows = [_row("https://one.example.com/a", at=AFTER - timedelta(hours=5)),
            _row("https://two.example.com/b", at=AFTER - timedelta(hours=1)),
            _row("https://three.example.com/c", at=AFTER - timedelta(minutes=10))]
    kept, _ = _digest(rows)
    assert [p.url for p in kept] == ["https://three.example.com/c", "https://two.example.com/b",
                                     "https://one.example.com/a"]


@pytest.mark.parametrize("title,snippet,direction,kept", [
    # The opposite move beside the company's own name: dropped.
    ("Plug Power and peers fell sharply in morning trade",
     "The company also delayed its plant opening.", "up", False),
    ("Why Plug Power Stock Is Down Today", "The company cut its outlook for the year.", "up",
     False),
    ("Plug Power stock soars after it wins a large hydrogen order", "", "down", False),
    # Twins kept: a rebound after a slide is an up page; another company's fall is not this one's.
    ("Plug Power rebounds after early slide", "", "up", True),
    ("Plug Power wins a hydrogen supply deal while rival FuelCell Energy fell after cutting its "
     "outlook", "", "up", True),
    # A page saying both ways beside the name is kept for either move.
    ("Plug Power rebounds after slide", "The company signed a supply agreement with a utility.",
     "up", True),
    ("Plug Power rebounds after slide", "The company signed a supply agreement with a utility.",
     "down", True),
    # A group's move with the company only named further off is not a direction claim about it.
    ("Hydrogen shares retreat as the tech rally fades",
     "Analysts said Plug Power and its peers face a slower order cycle.", "up", True),
])
def test_an_opposite_direction_page_is_dropped_and_its_twins_kept(title, snippet, direction, kept):
    pages, drops = _digest([_row("https://news.example.com/x", title=title, snippet=snippet,
                                 at=AFTER - timedelta(hours=1))], direction=direction)
    assert (len(pages) == 1) is kept, drops
    if not kept:
        assert drops == {"opposite_direction": 1}


@pytest.mark.parametrize("text,direction,expected", [
    ("Plug Power drops lawsuit against a former supplier", "up", False),    # news, not a move
    ("Plug Power drops 9% in early trading", "up", True),
    ("Plug Power gains approval for a Texas plant", "down", False),
    ("Plug Power revenue falls but losses narrow", "up", False),            # a metric falls
    ("Plug Power sees a rise in electrolyzer orders", "down", False),       # a noun
    ("Plug Power rally fades", "up", True),
    ("PLUG stock up 12% after a new order", "down", True),
    ("Shares of Plug Power tumbled", "up", True),
    ("Plug Power plans a new depot; Northwind Hydrogen shares fall", "up", False),  # another clause
])
def test_the_direction_lexicon_reads_only_moves_beside_the_name(text, direction, expected):
    terms = jws._page_terms("PLUG", NAME)
    assert describes_opposite_move(text, direction, terms) is expected


SNOW = ("SNOW", "Snowflake Inc.")
TGT = ("TGT", "Target Corporation")
GOLD = ("GOLD", "Barrick Gold Corporation")


@pytest.mark.parametrize("company,text,direction,expected", [
    # Review 2026-11-27: an earnings idiom, an investment verb and a peer's fall before the name
    # read as THIS company's fall, and the page explaining a rally was dropped. Must keep:
    (SNOW, "Snowflake falls short of profit estimates; shares jump on AI demand", "up", False),
    (SNOW, "Snowflake falls short of revenue forecast but stock soars on upbeat guidance", "up",
     False),
    (SNOW, "Snowflake fell well short of the profit estimate", "up", False),
    (SNOW, "Snowflake sinks $2 billion into an AI partnership with a model maker", "up", False),
    (SNOW, "Snowflake sinks $500M into a data center venture", "up", False),
    (SNOW, "Datadog falls as Snowflake unveils a new AI agent", "up", False),
    (SNOW, "Peers slide while Snowflake wins a large contract", "up", False),
    # ...and their must-drop twins: a real fall, a % move "into the close", the stock as subject.
    (SNOW, "Snowflake falls 12% after it misses profit estimates", "up", True),
    (SNOW, "Snowflake plunges 20% into the close", "up", True),
    (SNOW, "Shares tumble as Snowflake cuts its outlook", "up", True),
    (SNOW, "Snowflake sinks 15% into the weekend", "up", True),
    # A name ENDING in a noun the lexicon ignores ("target", "gold") was blind to its own move.
    (TGT, "Target plunges after weak holiday forecast", "up", True),
    (TGT, "Shares of Target fell 8%", "up", True),
    (TGT, "Target down 8% after it cuts its outlook", "up", True),
    (GOLD, "Barrick Gold tumbles on output miss", "up", True),
    # ...and the noun phrases stay a non-move.
    (TGT, "Analysts' price target rises for Target (TGT)", "down", False),
    (TGT, "Target (TGT) price target falls to $120 at Citi", "up", False),
    (TGT, "Target's sales target falls for the year", "up", False),
])
def test_the_direction_lexicon_for_other_names(company, text, direction, expected):
    terms = jws._page_terms(*company)
    assert describes_opposite_move(text, direction, terms) is expected


@pytest.mark.parametrize("title", [
    "Snowflake falls short of profit estimates; shares jump on AI demand",
    "Snowflake falls short of revenue forecast but stock soars on upbeat guidance",
    "Snowflake sinks $2 billion into an AI partnership with a model maker",
    "Datadog falls as Snowflake unveils a new AI agent",
])
def test_a_page_explaining_a_rally_is_kept_for_the_rally(title):
    pages, drops = digest({"results": [_row(
        "https://news.example.com/x", title=title,
        snippet="Snowflake Inc. reported its quarter on Wednesday, the company said.",
        at=AFTER - timedelta(hours=1))]}, *SNOW, AFTER, direction="up")
    assert len(pages) == 1 and drops == {}


def test_a_price_only_page_is_dropped_and_its_cause_twin_kept():
    only, drops = _digest([_row("https://news.example.com/a",
                                title="Plug Power stock soars in heavy trading", snippet="",
                                at=AFTER - timedelta(hours=1))], direction="up")
    assert only == [] and drops == {"price_only": 1}
    cause, drops = _digest([_row("https://news.example.com/a",
                                 title="Plug Power stock soars after it wins a large hydrogen order",
                                 snippet="", at=AFTER - timedelta(hours=1))], direction="up")
    assert len(cause) == 1 and drops == {}


def _page(url, at, *, precise=True, snippet="A neutral hand-written line.", provider="primary"):
    return WebPage(title="Plug Power names a new plant manager", snippet=snippet, url=url,
                   publisher="x", published_at=at, provider=provider, published_precise=precise)


def test_new_pages_merge_one_per_host_newest_wins_at_most_three():
    old = (_page("https://a.example.com/1", AFTER - timedelta(hours=3)),
           _page("https://b.example.com/1", AFTER - timedelta(hours=4)),
           _page("https://c.example.com/1", AFTER - timedelta(hours=5)))
    new = [_page("https://a.example.com/2", AFTER - timedelta(hours=1)),     # same host, newer
           _page("https://d.example.com/1", AFTER - timedelta(hours=6))]     # oldest of all
    merged = merge_pages(old, new, AFTER)
    assert [p.url for p in merged] == ["https://a.example.com/2", "https://b.example.com/1",
                                       "https://c.example.com/1"]
    assert len(merged) == MAX_KEPT


def test_merge_prefers_the_better_copy_of_one_url_and_ages_out_pages_older_than_24h():
    stale = _page("https://old.example.com/1", AFTER - timedelta(hours=25))
    vague = _page("https://same.example.com/1", AFTER - timedelta(hours=2), precise=False,
                  snippet="x" * 50)
    exact = _page("https://same.example.com/1", AFTER - timedelta(hours=2), snippet="short")
    merged = merge_pages((stale, vague), [exact], AFTER)
    assert merged == (exact,)
    assert merge_pages((stale,), [], AFTER) == ()


def test_a_page_with_only_a_date_ranks_after_every_page_with_a_time():
    # 22:00 ET yesterday (inside 24 h) is older than 00:00 ET today — and still ranks first.
    rows = [{"url": "https://dated.example.com/a", "title": "Plug Power opens a new plant",
             "description": "The plant is in New York, the company said.",
             "page_age": "November 25, 2026"},
            {"url": "https://timed.example.com/b", "title": "Plug Power names a new auditor",
             "description": "The board approved the change on Tuesday.",
             "page_age": "2026-11-25T03:00:00"}]
    kept, _ = _digest(rows, provider="fallback")
    assert [p.url for p in kept] == ["https://timed.example.com/b", "https://dated.example.com/a"]
    assert [p.published_precise for p in kept] == [True, False]
    assert all(p.provider == "fallback" for p in kept)
    assert kept[1].published_at == datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc)  # 00:00 ET


def test_refreshed_at_moves_only_when_the_merged_set_changes(env):
    svc = JumpWebSearchService()
    three = [_row(f"https://h{i}.example.com/a", at=AFTER - timedelta(minutes=10 + i))
             for i in range(3)]
    env["payload"] = {"results": three}
    _run(svc, [_plug()], AFTER)
    # The next step returns the same three plus an OLDER page from a new host: the set is the same.
    env["payload"] = {"results": three + [_row("https://h9.example.com/a",
                                               at=AFTER - timedelta(hours=3))]}
    _run(svc, [_plug()], AFTER + timedelta(minutes=20))
    pages, refreshed = svc.context("PLUG", AFTER + timedelta(minutes=20), direction="up")
    assert len(pages) == 3 and refreshed == AFTER
    # A newer page displaces the oldest: the set changed.
    env["payload"] = {"results": [_row("https://h8.example.com/a", at=AFTER + timedelta(minutes=40))]}
    _run(svc, [_plug()], AFTER + timedelta(minutes=45))
    pages, refreshed = svc.context("PLUG", AFTER + timedelta(minutes=45), direction="up")
    assert pages[0].url == "https://h8.example.com/a" and refreshed == AFTER + timedelta(minutes=45)


# Review 2026-11-27: a page aging past 24 h shrank the set, which moved `refreshed_at` — the gate's
# one-shot "new pages" trigger, which also breaks a held move card's hold — on nothing new.
AGING = _row("https://aging.example.com/a", at=AFTER - timedelta(hours=23, minutes=50))
FRESH = _row("https://fresh.example.com/b", title="Plug Power names a new plant manager",
             at=AFTER - timedelta(hours=1))


@pytest.mark.parametrize("answer", [[FRESH], []], ids=["subset", "empty"])
def test_a_page_aging_out_never_moves_refreshed_at(env, answer):
    svc = JumpWebSearchService()
    env["payload"] = {"results": [AGING, FRESH]}
    _run(svc, [_plug()], AFTER)
    later = AFTER + timedelta(minutes=20)
    env["payload"] = {"results": list(answer)}
    _run(svc, [_plug()], later)
    pages, refreshed = svc.context("PLUG", later, direction="up")
    assert [p.url for p in pages] == ["https://fresh.example.com/b"]
    assert refreshed == AFTER


@pytest.mark.asyncio
async def test_a_no_cause_verdict_survives_a_page_aging_out(fb):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [AGING, FRESH]}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc)                                       # no cause in {aging, fresh}
    env["payload"] = {"results": []}
    await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))   # aging is gone: {fresh}
    assert svc.episode_exhausted("PLUG", AFTER + timedelta(minutes=20), direction="up") is False
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=60))
    assert counts.get("fallback_started") == 1
    assert env["fallback"].calls[0]["trigger"] == "t3_verdict"


def test_no_cause_now_reads_the_exact_verdict_first_then_the_judged_pages():
    a, b, c = (_page(f"https://h{i}.example.com/a", AFTER - timedelta(hours=1 + i))
               for i in range(3))
    ep = jws._Episode(direction="up", anchor=AFTER, pages=(a, b),
                      rejected_urls=frozenset({a.url, b.url}))
    assert jws._no_cause_now(ep) is True                     # a regrouping of judged pages
    ep.verdict_urls, ep.verdict = frozenset({a.url, b.url}), None
    assert jws._no_cause_now(ep) is False                    # an outage on THIS set never asks
    ep.verdict = "supported"
    assert jws._no_cause_now(ep) is False
    ep.pages = (a, c)                                        # an unjudged page: not causeless
    assert jws._no_cause_now(ep) is False
    ep.pages = ()
    assert jws._no_cause_now(ep) is False                    # a verdict on another set
    # A card written from the news alone, with no page kept, found no cause: that counts.
    ep.verdict_urls, ep.verdict = frozenset(), "no_cause"
    assert jws._no_cause_now(ep) is True


def test_merge_ranks_judged_pages_after_every_other_page():
    judged = (_page("https://h0.example.com/a", AFTER - timedelta(hours=2)),
              _page("https://h1.example.com/a", AFTER - timedelta(hours=3)),
              _page("https://h2.example.com/a", AFTER - timedelta(hours=4)))
    urls = frozenset(p.url for p in judged)
    dated = _page("https://wire.example.com/order", AFTER - timedelta(hours=16), precise=False,
                  provider="fallback")
    same_host = _page("https://h1.example.com/later", AFTER - timedelta(hours=16), precise=False,
                      provider="fallback")
    # Unjudged, a page with only a date can never displace a full set of pages with a time.
    assert merge_pages(judged, [dated, same_host], AFTER) == judged
    # Judged, the new pages win — and win a judged page's host; the result is ranked as usual.
    merged = merge_pages(judged, [dated, same_host], AFTER, judged=urls)
    assert [p.url for p in merged] == ["https://h0.example.com/a", "https://wire.example.com/order",
                                       "https://h1.example.com/later"]
    # Judged pages found again never swap the kept ones out, even when they are newer.
    newer = _page("https://h3.example.com/a", AFTER - timedelta(minutes=5))
    assert merge_pages(merged, [newer, *judged], AFTER,
                       judged=urls | {newer.url}) == merged


# ── the fallback provider: triggers and guards ─────────────────────────────

def _outcome(svc, scope="PLUG", direction="up", verdict="no_cause", urls=None, when=AFTER):
    if urls is None:
        pages, _ = svc.context(scope, when, direction=direction)
        urls = tuple(p.url for p in pages)
    svc.note_move_outcome(scope, when, MoveOutcome(
        kind="none", direction=direction, verdict=verdict, why="test", page_urls=urls,
        written=True))


@pytest.mark.asyncio
async def test_t1_a_later_step_refused_at_the_full_cap(fb):
    env = fb
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    env["budget"].used = 80
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert counts == {"capped": 1, "fallback_started": 1}
    [call] = env["fallback"].calls
    assert call["trigger"] == "t1_capped" and call["direction"] == "up"
    assert call["query_name"] == "plug power" and call["scope"] == "PLUG"


@pytest.mark.asyncio
async def test_t1_never_at_step_zero(fb):
    env = fb
    svc = JumpWebSearchService()
    env["budget"].used = 80
    assert await _sweep(svc, [_plug()], AFTER) == {"capped": 1}
    assert env["fallback"].calls == []


@pytest.mark.asyncio
async def test_t1_a_later_step_paced_for_twenty_minutes(fb):
    env = fb
    svc = JumpWebSearchService()
    env["budget"].used = 28
    assert await _sweep(svc, [_plug()], SESSION) == {"paced": 1}             # 10:00, limit 28
    env["budget"].used = 31
    counts = await _sweep(svc, [_plug()], SESSION + timedelta(minutes=20))   # 10:20, limit 31
    assert counts == {"paced": 1, "fallback_started": 1}
    assert env["fallback"].calls[0]["trigger"] == "t1_paced"
    assert svc._capped_day is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["billed", "not_run"])
async def test_t2_a_later_step_failed(fb, failure):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    env["raise"] = brave_search.BraveSearchUnavailableException("x", not_run=failure == "not_run")
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert counts["fallback_started"] == 1
    assert env["fallback"].calls[0]["trigger"] == "t2_failed"


@pytest.mark.asyncio
async def test_t2_never_at_step_zero_or_on_a_budget_outage(fb):
    env = fb
    svc = JumpWebSearchService()
    env["raise"] = brave_search.BraveSearchUnavailableException("x", not_run=False)
    await _sweep(svc, [_plug()], AFTER)
    env["raise"] = None
    env["budget"].raise_exc = ChatBudgetUnavailable("down")
    assert await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20)) == {
        "budget_unavailable": 1}
    assert env["fallback"].calls == []


@pytest.mark.asyncio
async def test_t2_a_later_step_kept_nothing(fb):
    env = fb
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert counts == {"no_results": 1, "fallback_started": 1}
    assert env["fallback"].calls[0]["trigger"] == "t2_empty"


@pytest.mark.asyncio
async def test_t3_a_no_cause_verdict_an_hour_into_the_episode_59_vs_60_minutes(fb):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc)
    assert "fallback_started" not in await _sweep(svc, [_plug()], AFTER + timedelta(minutes=59))
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=60))
    assert counts == {"not_due": 1, "fallback_started": 1}
    assert env["fallback"].calls[0]["trigger"] == "t3_verdict"


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,urls", [
    (None, None),                                    # a judge or model outage never triggers
    ("supported", None),
    ("no_cause", ("https://another.example.com/z",)),  # a verdict about another page set
])
async def test_t3_needs_a_no_cause_verdict_on_the_current_set(fb, verdict, urls):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc, verdict=verdict, urls=urls)
    assert "fallback_started" not in await _sweep(svc, [_plug()], AFTER + timedelta(minutes=65))
    assert env["fallback"].calls == []


@pytest.mark.asyncio
async def test_at_most_two_lookups_an_episode_an_hour_apart(fb):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc)
    for minutes in (60, 90, 120, 180, 240):
        await _sweep(svc, [_plug()], AFTER + timedelta(minutes=minutes))
    assert [c["now"] for c in env["fallback"].calls] == [AFTER + timedelta(minutes=60),
                                                        AFTER + timedelta(minutes=120)]


@pytest.mark.asyncio
async def test_one_lookup_in_flight_per_process_and_one_started_per_sweep(fb):
    env = fb
    svc = JumpWebSearchService()
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    env["payload"] = lambda q: {"results": [
        _row("https://a.example.com/1", at=AFTER),
        _row("https://b.example.com/1", title="Northwind Hydrogen Corp opens a depot",
             snippet="The depot is in Ohio, the company said.", at=AFTER)]}
    await _sweep(svc, [_plug(), other], AFTER)
    _outcome(svc)
    _outcome(svc, scope="NWHC")
    env["fallback"].gate = asyncio.Event()
    counts = await svc.refresh([_plug(), other], AFTER + timedelta(minutes=60))
    assert counts["fallback_started"] == 1
    await asyncio.sleep(0)
    # Still in flight: the other scope waits.
    counts = await svc.refresh([_plug(), other], AFTER + timedelta(minutes=65))
    assert "fallback_started" not in counts and len(env["fallback"].calls) == 1
    env["fallback"].gate.set()
    await asyncio.gather(*list(svc._fallback_tasks))
    counts = await _sweep(svc, [_plug(), other], AFTER + timedelta(minutes=70))
    assert counts["fallback_started"] == 1
    assert {c["scope"] for c in env["fallback"].calls} == {"PLUG", "NWHC"}


@pytest.mark.asyncio
async def test_never_while_a_company_cause_is_held(fb):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc)
    env["budget"].used = 80
    await _sweep(svc, [_plug(held_kind="company")], AFTER + timedelta(minutes=60))
    assert env["fallback"].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("et_minute_at_trigger,started", [
    ((15, 57), True), ((15, 58, 30), False), ((15, 59, 59), False),
])
async def test_never_in_the_two_minutes_before_the_close(fb, et_minute_at_trigger, started):
    env = fb
    h, m, *s = et_minute_at_trigger
    trigger_at = datetime(2026, 11, 25, h + 5, m, s[0] if s else 0, tzinfo=timezone.utc)
    start = trigger_at - timedelta(minutes=60)
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://a.example.com/1", at=start)]}
    await _sweep(svc, [_plug()], start)
    _outcome(svc, when=start)
    counts = await _sweep(svc, [_plug()], trigger_at)
    assert ("fallback_started" in counts) is started


@pytest.mark.asyncio
async def test_never_when_the_fallback_is_disabled(fb):
    env = fb
    env["fallback"].on = False
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    env["budget"].used = 80
    await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert env["fallback"].calls == []


@pytest.mark.asyncio
async def test_a_missing_fallback_module_means_no_lookup(fb, monkeypatch):
    monkeypatch.setattr(jws, "_fallback_module", lambda: None)
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    assert await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20)) == {"no_results": 1}


THREE_TIMED = [_row(f"https://h{i}.example.com/a", at=AFTER - timedelta(hours=2 + i))
               for i in range(3)]


@pytest.mark.asyncio
async def test_a_fallback_page_replaces_a_full_set_the_card_found_no_cause_in(fb, monkeypatch):
    """Review 2026-11-27: a lookup started on a full set of pages with a time (T3) returned rows
    with only a date, which ranked after every kept page — paid for, thrown away, and T3 fired
    again an hour later on the same set."""
    env = fb
    landed = AFTER + timedelta(minutes=60, seconds=30)
    monkeypatch.setattr(jws, "_wall", lambda: landed)
    svc = JumpWebSearchService()
    env["payload"] = {"results": THREE_TIMED}
    await _sweep(svc, [_plug()], AFTER)
    _outcome(svc)                                                   # no cause in the full set
    env["payload"] = {"results": []}
    env["fallback"].rows = [
        {"url": "https://wire.example.com/order", "title": "Plug Power lands a large electrolyzer order",
         "description": "A steelmaker placed the order, the company said.",
         "page_age": "November 25, 2026"},
        {"url": "https://h1.example.com/later", "title": "Plug Power names a new finance chief",
         "description": "The appointment takes effect next month.",
         "page_age": "November 25, 2026"},
    ]
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=60))
    assert counts["fallback_started"] == 1
    pages, refreshed = svc.context("PLUG", landed, direction="up")
    assert [(p.url, p.provider) for p in pages] == [
        ("https://h0.example.com/a", "primary"),
        ("https://wire.example.com/order", "fallback"),
        ("https://h1.example.com/later", "fallback"),
    ]
    assert refreshed == landed
    # The next step finds the judged pages again: they never push the new ones out, the stamp
    # stays, and no verdict on this set exists yet, so T3 does not buy a second lookup.
    env["payload"] = {"results": THREE_TIMED}
    later = AFTER + timedelta(minutes=120)
    counts = await _sweep(svc, [_plug()], later)
    assert "fallback_started" not in counts and len(env["fallback"].calls) == 1
    assert svc.context("PLUG", later, direction="up") == (pages, landed)


@pytest.mark.asyncio
@pytest.mark.parametrize("kept,judged,started", [
    (3, False, False),        # a full set of unjudged pages with a time: nothing to displace
    (2, False, True),         # room left
    (3, True, True),          # judged causeless: a lookup's pages replace them
])
async def test_t1_skips_a_full_set_no_lookup_page_could_displace(fb, kept, judged, started):
    env = fb
    svc = JumpWebSearchService()
    env["payload"] = {"results": THREE_TIMED[:kept]}
    await _sweep(svc, [_plug()], AFTER)
    if judged:
        _outcome(svc)
    env["budget"].used = 80
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert ("fallback_started" in counts) is started
    assert len(env["fallback"].calls) == int(started)


# Review 2026-11-27: a lookup stamps its pages with the WALL clock (`_wall`), later than the
# sweep's `now`; one landing before that sweep read the context fed its card, yet the next sweep
# saw `refreshed_at` newer than the card and paid for a second card from the same pages.
FALLBACK_ROW = {"url": "https://wire.example.com/order",
                "title": "Plug Power lands a large electrolyzer order",
                "description": "A steelmaker placed the order, the company said.",
                "page_age": "November 25, 2026"}


@pytest.mark.asyncio
async def test_a_lookup_landing_before_the_read_is_stamped_with_that_sweep(fb, monkeypatch):
    env = fb
    t2 = AFTER + timedelta(minutes=20)
    landed = t2 + timedelta(seconds=8)
    monkeypatch.setattr(jws, "_wall", lambda: landed)
    slow = {"on": False}

    async def _search(query, **kw):
        env["queries"].append(query)
        if "northwind" in query and slow["on"]:
            await asyncio.sleep(0.2)            # another jumper's search is still running
        return {"results": []}

    monkeypatch.setattr(jws.brave_search, "web_search", _search)
    other = JumpCandidate("NWHC", "Northwind Hydrogen Corp", 11.0, 20.0)
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug(), other], AFTER)
    env["fallback"].rows = [dict(FALLBACK_ROW)]
    slow["on"] = True
    counts = await svc.refresh([_plug(), other], t2)        # PLUG: T2 (kept nothing) → lookup
    assert counts["fallback_started"] == 1
    assert svc._days["PLUG"].episodes["up"].pages              # it landed inside `refresh`
    pages, refreshed = svc.context("PLUG", t2, direction="up")
    assert [p.url for p in pages] == ["https://wire.example.com/order"] and refreshed == t2
    # Stored, not only returned: the next sweep reads the sweep's stamp, not the wall clock.
    assert svc.context("PLUG", t2 + timedelta(minutes=15), direction="up")[1] == t2


@pytest.mark.asyncio
async def test_a_lookup_landing_after_the_read_keeps_its_later_stamp(fb, monkeypatch):
    env = fb
    t2 = AFTER + timedelta(minutes=20)
    landed = t2 + timedelta(seconds=8)
    monkeypatch.setattr(jws, "_wall", lambda: landed)
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    env["fallback"].rows = [dict(FALLBACK_ROW)]
    env["fallback"].gate = asyncio.Event()
    counts = await svc.refresh([_plug()], t2)
    assert counts["fallback_started"] == 1
    assert svc.context("PLUG", t2, direction="up") == ((), None)   # the sweep read nothing
    env["fallback"].gate.set()
    await asyncio.gather(*list(svc._fallback_tasks))
    # The next sweep sees pages newer than any card: its gate fires.
    assert svc.context("PLUG", t2 + timedelta(minutes=5), direction="up")[1] == landed


@pytest.mark.asyncio
async def test_fallback_rows_go_through_the_same_digest_and_merge(fb, monkeypatch, caplog):
    env = fb
    caplog.set_level(logging.INFO, logger=jws.__name__)
    landed = AFTER + timedelta(minutes=21)
    monkeypatch.setattr(jws, "_wall", lambda: landed)
    svc = JumpWebSearchService()
    env["payload"] = {"results": [_row("https://primary.example.com/1", at=AFTER)]}
    await _sweep(svc, [_plug()], AFTER)
    env["budget"].used = 80                  # T1 at the +20 step
    env["fallback"].rows = [
        {"url": "https://second.example.com/order", "title": "Plug Power lands a large electrolyzer order",
         "description": "A European steelmaker placed the order, the company said.",
         "page_age": "November 25, 2026"},
        {"url": "https://third.example.com/cfo", "title": "Plug Power names a new finance chief",
         "description": "The appointment takes effect next month.", "page_age": "2 hours ago"},
        {"url": "https://www.reddit.com/r/stocks/plug", "title": "Plug Power thread",
         "description": "Talk about the company.", "page_age": "1 hour ago"},
        {"url": "https://fourth.example.com/old", "title": "Plug Power opens a depot",
         "description": "The depot opened in Ohio.", "page_age": "November 20, 2026"},
        {"url": "https://fifth.example.com/fall", "title": "Plug Power shares slump",
         "description": "The company did not comment.", "page_age": "1 hour ago"},
    ]
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert counts["fallback_started"] == 1
    pages, refreshed = svc.context("PLUG", landed, direction="up")
    assert [(p.url, p.provider, p.published_precise) for p in pages] == [
        ("https://primary.example.com/1", "primary", True),
        ("https://third.example.com/cfo", "fallback", True),
        ("https://second.example.com/order", "fallback", False),
    ]
    assert refreshed == landed
    line = next(r.getMessage() for r in caplog.records
                if "status=fallback_ok" in r.getMessage())
    assert "provider=fallback" in line and "changed=True refreshed=True" in line
    assert "kept=3" in line and "'denied': 1" in line and "'no_age': 1" in line
    assert "'opposite_direction': 1" in line
    for text in ("electrolyzer", "second.example", "reddit", "Plug Power", "plug power"):
        assert text not in line


@pytest.mark.asyncio
async def test_a_lookup_failure_is_logged_never_swallowed(fb, caplog):
    env = fb
    caplog.set_level(logging.INFO, logger=jws.__name__)
    env["fallback"].raise_exc = RuntimeError("boom")
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    counts = await _sweep(svc, [_plug()], AFTER + timedelta(minutes=20))
    assert counts["fallback_started"] == 1
    warn = [r for r in caplog.records if "status=error unexpected=RuntimeError" in r.getMessage()]
    assert warn and warn[0].levelno == logging.WARNING and warn[0].exc_info
    assert svc._days["PLUG"].episodes["up"].fallback_inflight is False


@pytest.mark.asyncio
async def test_the_done_callback_logs_a_task_that_failed(caplog):
    caplog.set_level(logging.INFO, logger=jws.__name__)
    svc = JumpWebSearchService()

    async def _boom():
        raise ValueError("escaped")

    task = asyncio.get_running_loop().create_task(_boom())
    svc._fallback_tasks.add(task)
    task.add_done_callback(functools.partial(svc._fallback_done, "PLUG"))
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and "status=task_failed error=ValueError" in errors[0].getMessage()
    assert "escaped" not in errors[0].getMessage() and not svc._fallback_tasks


@pytest.mark.asyncio
async def test_aclose_cancels_a_lookup_in_flight(fb, caplog):
    env = fb
    caplog.set_level(logging.INFO, logger=jws.__name__)
    env["fallback"].gate = asyncio.Event()
    svc = JumpWebSearchService()
    await _sweep(svc, [_plug()], AFTER)
    await svc.refresh([_plug()], AFTER + timedelta(minutes=20))
    [task] = list(svc._fallback_tasks)
    await asyncio.sleep(0)
    await svc.aclose()
    assert task.cancelled() and not svc._fallback_tasks
    assert svc._days["PLUG"].episodes["up"].fallback_inflight is False
    assert any("status=cancelled" in r.getMessage() for r in caplog.records)
    await svc.aclose()                                   # nothing in flight: a no-op


# ── episode_exhausted ──────────────────────────────────────────────────────

def test_episode_exhausted_follows_the_ladder_the_limit_and_the_fallback(env, monkeypatch):
    svc = JumpWebSearchService()
    assert svc.episode_exhausted("PLUG", AFTER, direction="up") is True     # never searched
    env["payload"] = {"results": [_row("https://a.example.com/1", at=AFTER)]}
    _run(svc, [_plug()], AFTER)
    assert svc.episode_exhausted("PLUG", AFTER, direction="up") is False    # the ladder goes on
    assert svc.episode_exhausted("PLUG", AFTER, direction=None) is True
    for minutes in (20, 45, 90, 180, 300):
        _run(svc, [_plug()], AFTER + timedelta(minutes=minutes))
    done = AFTER + timedelta(minutes=300)
    env["fallback"].on = False
    assert svc.episode_exhausted("PLUG", done, direction="up") is True
    # A fallback lookup still due keeps it open — until both are used.
    _outcome(svc, when=done)
    env["fallback"].on = True
    assert svc.episode_exhausted("PLUG", done, direction="up") is False
    svc._days["PLUG"].episodes["up"].fallback_lookups = 2
    assert svc.episode_exhausted("PLUG", done, direction="up") is True


def test_an_episode_whose_scope_stopped_being_a_candidate_is_exhausted(env):
    svc = JumpWebSearchService()
    _run(svc, [_plug()], AFTER)
    assert svc.episode_exhausted("PLUG", AFTER + timedelta(minutes=20), direction="up") is False
    assert svc.episode_exhausted("PLUG", AFTER + timedelta(minutes=21), direction="up") is True


def test_the_scope_limit_or_a_switched_off_search_exhausts_it(env, monkeypatch):
    svc = JumpWebSearchService()
    _run(svc, [_plug()], AFTER)
    svc._days["PLUG"].searches = MAX_SEARCHES_PER_SCOPE_PER_DAY
    env["fallback"].on = False
    assert svc.episode_exhausted("PLUG", AFTER, direction="up") is True
    svc._days["PLUG"].searches = 1
    monkeypatch.setattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", False)
    assert svc.episode_exhausted("PLUG", AFTER, direction="up") is True


def test_note_move_outcome_ignores_junk_and_other_days(env):
    svc = JumpWebSearchService()
    _run(svc, [_plug()], AFTER)
    svc.note_move_outcome("PLUG", AFTER, "not an outcome")
    svc.note_move_outcome("NOPE", AFTER, MoveOutcome(kind=None, direction="up", verdict=None,
                                                     why="x"))
    svc.note_move_outcome("PLUG", AFTER + timedelta(days=1), MoveOutcome(
        kind="none", direction="up", verdict="no_cause", why="x"))
    assert svc._days["PLUG"].episodes["up"].verdict is None
    assert svc._days["PLUG"].last_written is None
