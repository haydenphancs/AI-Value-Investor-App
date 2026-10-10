"""
Company Weekly, Drop 2b part 1 — the PER-SERIES switch (web setting MARKETING_NEWS_SERIES).

A news series runs only when its content class is in MARKETING_CONTENT_CLASSES, its id is listed in
MARKETING_NEWS_SERIES AND its code has shipped (`selection.SHIPPED_SERIES` — every series since drop 2b,
2026-10-10). What is pinned here:

* `selection.parse_news_series` (call time): the setting's ids ∩ SHIPPED_SERIES, the shipped set read per
  call; case and whitespace folded; an unknown id or a series not shipped (a rollback, simulated by
  narrowing SHIPPED_SERIES) is DROPPED and logged at ERROR, bounded; "" = no news series; a non-string =
  no news series (ERROR). Never raises.
* `app.config.Settings.MARKETING_NEWS_SERIES` (boot): the Drop-2a default — so the 2b series, shipped in
  code, stay OFF in production until the owner lists them; normalised to the sorted set; an unknown id
  dropped with an ERROR, never a failed deploy; its inline id literal == `selection.SERIES`.
* `script_service._select` passes the switch to `enabled_chain(shipped=...)`, reads it at CALL time, and
  reads it only while a news class is on — with MARKETING_CONTENT_CLASSES unset every day is Drop 1's,
  byte for byte, whatever the switch says (nothing parsed, nothing logged). With the default switch no
  2b series is ever asked for; listing one turns its days on.
* the per-series build budgets (`script_service.SERIES_BUDGET_SECONDS`) are declared only for shipped
  series, cover each 2b series above the adapter's measured worst case, and leave the 13F series its
  whole budget on a Congress Tuesday in the 13F season.

Hermetic: the in-memory PostgREST fake of test_marketing_script_flow.py and a fake company-news source
whose every series comes up empty (the chain then ends in the lesson). No record is composed here, so these
tests do not depend on the templates.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.services.marketing import content_pool
from app.services.marketing import run_service as mrs
from app.services.marketing import script_service as ss
from app.services.marketing import selection
from test_marketing_script_flow import FakeSB, FakeWriter

_BACKEND = Path(__file__).resolve().parents[1]
_SELECTION_LOGGER = selection.logger.name
_CONFIG_LOGGER = "app.config"

#: The Drop-2a series — the setting's default (production runs these).
DROP_2A = frozenset({"ceo_buys", "insider_buys", "thirteen_f", "money_map"})
#: The Drop-2b series — shipped in code, off in production until the switch lists them.
DROP_2B = frozenset({"congress_count", "company_stakes", "earnings", "theme_explainer"})
EVERY_RAW = ",".join(s.id for s in selection.SERIES)
DEFAULT_RAW = "ceo_buys,insider_buys,thirteen_f,money_map"

MON = date(2026, 11, 16)            # a Monday in the 13F season (Q3 2026 due date, rolled to Mon 11-16)
MON_NEXT = date(2026, 11, 23)
THU = date(2026, 11, 19)
TUE_CONGRESS = date(2026, 12, 8)    # the December Congress Count Tuesday, still in the 13F season
SAT = date(2026, 11, 21)
assert (MON.weekday(), MON_NEXT.weekday(), THU.weekday(), TUE_CONGRESS.weekday(), SAT.weekday()) == (
    0, 0, 3, 1, 5)


def _errors(caplog, logger_name: str) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name and r.levelno >= logging.ERROR]


# ══ selection.parse_news_series — the call-time parse ═════════════════════════════════════════════


@pytest.mark.parametrize("raw, expected, logs_error", [
    (DEFAULT_RAW, DROP_2A, False),
    (" CEO_Buys , money_map ", {"ceo_buys", "money_map"}, False),           # whitespace and case folded
    ("THIRTEEN_F", {"thirteen_f"}, False),
    ("ceo_buys,,  ,money_map,", {"ceo_buys", "money_map"}, False),          # empty tokens skipped
    ("ceo_buys,CEO_BUYS, ceo_buys", {"ceo_buys"}, False),                   # duplicates collapse
    ("", set(), False),                                                      # empty = no news series
    ("   ", set(), False),
    (",,", set(), False),
    ("congress_count", {"congress_count"}, False),                           # a 2b series: shipped since 2b
    ("money_map,congress_count,earnings,company_stakes,theme_explainer", {"money_map"} | DROP_2B, False),
    (EVERY_RAW, DROP_2A | DROP_2B, False),
    (EVERY_RAW.upper(), DROP_2A | DROP_2B, False),
    ("ceo_buys,nope", {"ceo_buys"}, True),                                   # unknown id dropped
    ("lesson", set(), True),                                                 # the lesson is no series
    ("ceo-buys", set(), True),                                               # no fuzzy matching
    ("ceo_buys;money_map", set(), True),                                     # one unknown token, never half-parsed
    (None, set(), True),
    (1, set(), True),
    (True, set(), True),
    (["ceo_buys"], set(), True),
])
def test_parse_news_series_is_the_shipped_set_the_setting_lists(raw, expected, logs_error, caplog):
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        got = selection.parse_news_series(raw)
    assert isinstance(got, frozenset) and got == expected
    assert got <= selection.SHIPPED_SERIES
    errors = _errors(caplog, _SELECTION_LOGGER)
    assert bool(errors) == logs_error, errors
    assert all("MARKETING_NEWS_SERIES" in m for m in errors), errors


def test_a_series_not_shipped_is_ignored_and_named_in_the_error(monkeypatch, caplog):
    """Every series has shipped since drop 2b; a rollback that withdraws some (SHIPPED_SERIES narrowed —
    here back to the 2a four) drops them from a setting that still lists them, by name."""
    monkeypatch.setattr(selection, "SHIPPED_SERIES", DROP_2A)
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        got = selection.parse_news_series("ceo_buys,congress_count,earnings")
    assert got == {"ceo_buys"}
    (msg,) = _errors(caplog, _SELECTION_LOGGER)
    assert "not shipped" in msg and "congress_count" in msg and "earnings" in msg and "ceo_buys" not in msg


def test_an_unknown_id_and_an_unshipped_one_are_reported_apart(monkeypatch, caplog):
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - {"congress_count"})
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series("bogus,congress_count") == frozenset()
    errors = _errors(caplog, _SELECTION_LOGGER)
    assert len(errors) == 2
    assert any("unknown" in m and "bogus" in m and "congress_count" not in m for m in errors), errors
    assert any("not shipped" in m and "congress_count" in m and "bogus" not in m for m in errors), errors


def test_the_shipped_set_is_read_at_call_time(monkeypatch, caplog):
    """A series ships by its id joining SHIPPED_SERIES: a setting that already lists it turns it on with
    no other change (and stops logging it) — and withdrawing it turns it off the same way."""
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - {"congress_count"})
    assert selection.parse_news_series("congress_count,money_map") == {"money_map"}
    caplog.clear()
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES | {"congress_count"})
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series("congress_count,money_map") == {"congress_count", "money_map"}
    assert _errors(caplog, _SELECTION_LOGGER) == []


def test_a_pasted_blob_never_floods_the_log(caplog):
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series(",".join(f"z{i}{'q' * 500}" for i in range(50))) == frozenset()
    (msg,) = _errors(caplog, _SELECTION_LOGGER)
    assert len(msg) < 400


def test_enabled_chain_honours_the_switch_under_the_class_switch():
    acf = frozenset({"A", "C", "F"})
    monday = selection.plan_for(MON).chain
    parse = selection.parse_news_series
    lesson = selection.LESSON
    assert selection.enabled_chain(monday, acf, shipped=parse(DEFAULT_RAW)) == (
        "ceo_buys", "insider_buys", "money_map", lesson)
    assert selection.enabled_chain(monday, acf, shipped=parse("money_map")) == ("money_map", lesson)
    assert selection.enabled_chain(monday, acf, shipped=parse("")) == (lesson,)
    # listed AND shipped, but its class is off: still dropped (Money Map is F)
    assert selection.enabled_chain(monday, frozenset({"A", "C"}), shipped=parse(DEFAULT_RAW)) == (
        "ceo_buys", "insider_buys", lesson)
    # the default switch keeps every 2b series off; listing one (shipped since 2b) turns it on
    tue = selection.plan_for(TUE_CONGRESS).chain
    assert tue[:3] == ("congress_count", "thirteen_f", "company_stakes")
    assert selection.enabled_chain(tue, acf, shipped=parse(DEFAULT_RAW)) == ("thirteen_f", "money_map", lesson)
    assert selection.enabled_chain(tue, acf, shipped=parse("congress_count,thirteen_f")) == (
        "congress_count", "thirteen_f", lesson)
    assert selection.enabled_chain(tue, acf, shipped=parse(EVERY_RAW)) == tue
    # Saturday is the lesson whatever the switch says
    assert selection.enabled_chain(selection.plan_for(SAT).chain, acf, shipped=parse(DEFAULT_RAW)) == (lesson,)


# ══ the setting (app/config.py) ════════════════════════════════════════════════════════════════════


def test_the_setting_is_declared_with_the_2a_series_as_its_default(caplog):
    from app.config import Settings, settings

    assert hasattr(settings, "MARKETING_NEWS_SERIES")
    default = Settings.model_fields["MARKETING_NEWS_SERIES"].default
    assert default == DEFAULT_RAW
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series(default) == DROP_2A
    # every default id is SHIPPED — otherwise the default itself would log an ERROR at every kick
    assert _errors(caplog, _SELECTION_LOGGER) == []
    assert DROP_2A <= selection.SHIPPED_SERIES
    # owner decision (2026-10-10): the 2b series ship in code but stay OFF in production by default
    assert DROP_2B <= selection.SHIPPED_SERIES
    assert not selection.parse_news_series(default) & DROP_2B


def test_the_default_switch_keeps_every_2b_series_off_for_two_years():
    """With every class on and the setting at its default, no posting day's chain holds a 2b series —
    while the same days DO reach them once the switch lists them (anti-vacuity)."""
    from app.config import Settings

    acf = frozenset({"A", "C", "F"})
    on_default = selection.parse_news_series(Settings.model_fields["MARKETING_NEWS_SERIES"].default)
    on_every = selection.parse_news_series(EVERY_RAW)
    reached = set()
    day = date(2026, 10, 12)
    while day < date(2028, 10, 12):
        plan = selection.plan_for(day)
        if not plan.rest_day:
            assert not set(selection.enabled_chain(plan.chain, acf, shipped=on_default)) & DROP_2B, day
            reached |= set(selection.enabled_chain(plan.chain, acf, shipped=on_every))
        day += timedelta(days=1)
    assert DROP_2B <= reached


@pytest.mark.parametrize("raw, expected, logs_error", [
    (" Money_Map , CEO_BUYS ", "ceo_buys,money_map", False),
    (DEFAULT_RAW, "ceo_buys,insider_buys,money_map,thirteen_f", False),
    ("ceo_buys,nope", "ceo_buys", True),
    ("", "", False),
    ("  , ", "", False),
    ("lesson", "", True),
    # shipping is code, not configuration: a known 2b id is KEPT here and dropped at kick time
    ("congress_count,earnings", "congress_count,earnings", False),
])
def test_the_setting_normalises_and_never_fails_the_deploy(monkeypatch, caplog, raw, expected, logs_error):
    from app.config import Settings

    monkeypatch.setenv("MARKETING_NEWS_SERIES", raw)
    with caplog.at_level(logging.ERROR, logger=_CONFIG_LOGGER):
        got = Settings().MARKETING_NEWS_SERIES
    assert got == expected
    errors = _errors(caplog, _CONFIG_LOGGER)
    assert bool(errors) == logs_error, errors
    assert all("MARKETING_NEWS_SERIES" in m for m in errors), errors


@pytest.mark.parametrize("value", [None, 1, ["ceo_buys"]])
def test_a_non_string_setting_is_no_series(caplog, value):
    from app.config import Settings

    with caplog.at_level(logging.ERROR, logger=_CONFIG_LOGGER):
        assert Settings(MARKETING_NEWS_SERIES=value).MARKETING_NEWS_SERIES == ""
    assert any("MARKETING_NEWS_SERIES" in m for m in _errors(caplog, _CONFIG_LOGGER))


def test_the_config_literal_equals_the_series_registry():
    """config.py imports no app module, so its validator carries an inline copy of the series ids. Read
    by AST (the literal itself) and by behaviour (every series survives the validator)."""
    from app.config import Settings

    src = (_BACKEND / "app" / "config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_news_series_fail_closed")
    literals = [ast.literal_eval(a.value) for a in ast.walk(fn)
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "known" for t in a.targets)]
    assert literals == [tuple(s.id for s in selection.SERIES)]
    every = ",".join(s.id for s in selection.SERIES)
    assert Settings(MARKETING_NEWS_SERIES=every).MARKETING_NEWS_SERIES == ",".join(sorted(selection.SERIES_BY_ID))


def test_no_build_budget_is_declared_for_an_unshipped_series():
    """A per-series build budget (script_service.SERIES_BUDGET_SECONDS) is added in the SAME change that
    ships its series (adds it to selection.SHIPPED_SERIES) — never ahead of it."""
    assert set(ss.SERIES_BUDGET_SECONDS) <= set(selection.SHIPPED_SERIES), sorted(ss.SERIES_BUDGET_SECONDS)


#: The adapter's measured worst case of ONE `candidates()` call, in seconds (its plumbing notes,
#: 2026-10-10; FMP's own 5xx retries included). Each build budget must stay above it.
_ADAPTER_WORST_SECONDS = {"congress_count": 30.0, "company_stakes": 5.0, "earnings": 20.0,
                          "theme_explainer": 40.0}


def test_each_2b_series_has_a_build_budget_above_the_adapters_worst_case():
    assert set(_ADAPTER_WORST_SECONDS) == DROP_2B
    for series, worst in _ADAPTER_WORST_SECONDS.items():
        cap = ss.SERIES_BUDGET_SECONDS.get(series)
        assert cap is not None and cap > worst, (series, cap, worst)
        assert ss.SERIES_MIN_START_SECONDS < cap <= ss.TEMPLATE_BUILD_BUDGET_SECONDS, series
    assert ss.SERIES_BUDGET_SECONDS == {"thirteen_f": 150.0, "congress_count": 60.0, "company_stakes": 45.0,
                                        "earnings": 45.0, "theme_explainer": 45.0}


def _worst_spent_before(chain, target: str) -> float:
    """What the series before `target` can spend at worst (each its cap, or what is left, + the wait slack)."""
    spent = 0.0
    for series in chain:
        if series == target:
            return spent
        left = ss.TEMPLATE_BUILD_BUDGET_SECONDS - spent
        if left < ss.SERIES_MIN_START_SECONDS:
            continue
        spent += min(left, ss.SERIES_BUDGET_SECONDS.get(series, ss.DEFAULT_SERIES_BUDGET_SECONDS)) \
            + ss.SERIES_WAIT_SLACK_SECONDS
    raise AssertionError(f"{target} is not in {chain}")


def test_every_13f_day_leaves_the_13f_series_its_whole_budget_for_ten_years():
    """Congress Count runs BEFORE 13F Season on the December Congress Tuesday: at its worst it must still
    leave the 13F series (a live registry build, Berkshire) its whole 150 s. Every chain, every class
    set, every series on."""
    acf = frozenset({"A", "C", "F"})
    cap_13f = ss.SERIES_BUDGET_SECONDS["thirteen_f"]
    seen_after_congress = 0
    day = date(2026, 1, 1)
    while day < date(2036, 1, 1):
        chain = selection.enabled_chain(selection.plan_for(day).chain, acf)
        if "thirteen_f" in chain:
            assert ss.TEMPLATE_BUILD_BUDGET_SECONDS - _worst_spent_before(chain, "thirteen_f") >= cap_13f, day
            seen_after_congress += chain.index("thirteen_f") > 0
        day += timedelta(days=1)
    assert seen_after_congress >= 10        # the Congress-then-13F Tuesday happens (anti-vacuity)


# ══ script_service: the switch decides which series the day's build asks for ══════════════════════


class _NewsUnavailable(Exception):
    def __init__(self, series: str, reason: str = "internal_error", detail: str = "") -> None:
        super().__init__(f"{series}: {reason}")
        self.series, self.reason, self.detail = series, reason, detail


class EmptyNews:
    """The adapter's public surface; every series comes up empty, so the day falls to the lesson."""

    MarketingNewsUnavailable = _NewsUnavailable

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def candidates(self, series, *, run_date, exclude, limit, deadline):
        self.calls.append(series)
        return SimpleNamespace(series=series, records=(), skip_reason=f"{series}_none_qualified", rejections={})

    async def fetch_logo(self, symbol, *, max_bytes, timeout):
        return None


@pytest.fixture
def world(monkeypatch):
    sb = FakeSB()
    runs = mrs.MarketingRunService(supabase=sb)
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", DEFAULT_RAW)
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_ALLOW_URLS", False)
    monkeypatch.setattr(ss.smart_link, "store_state", lambda: "live")
    return sb, runs


def _run(day: date) -> Dict[str, Any]:
    """A run held by the drop-2b worker (it declared it can draw a template, the 2b layouts included)."""
    return {"id": str(uuid.uuid4()), "run_date": day.isoformat(),
            "metadata": {"worker_capabilities": ["layouts_2b", "news_templates", "post_image"]}}


async def _drain(svc: ss.MarketingScriptService) -> None:
    for _ in range(50):
        if not svc._tasks:
            return
        await asyncio.sleep(0)
        await asyncio.gather(*list(svc._tasks), return_exceptions=True)


async def _select(runs, news, day: date) -> Dict[str, Any]:
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    row = await svc._select(_run(day))
    await _drain(svc)
    return row


def _never_load_news():
    raise AssertionError("no news series is on: the company-news source must never be loaded")


class _ClockNews(EmptyNews):
    """Every series hangs for its whole wait (a fake clock moves past its deadline + the slack, then the
    call times out); records the budget each series was OFFERED (`deadline` − now at the call)."""

    def __init__(self, clock: List[float]) -> None:
        super().__init__()
        self.clock = clock
        self.offered: Dict[str, float] = {}

    async def candidates(self, series, *, run_date, exclude, limit, deadline):
        self.calls.append(series)
        self.offered[series] = round(deadline - self.clock[0], 6)
        self.clock[0] = deadline + ss.SERIES_WAIT_SLACK_SECONDS
        raise asyncio.TimeoutError


@pytest.mark.asyncio
async def test_on_a_congress_tuesday_the_13f_series_is_offered_its_whole_budget(world, monkeypatch):
    """The build itself, every series listed and every one hanging: Congress Count is offered 60 s, the
    13F series still its whole 150 s, Company Stakes the remainder, and the rest fall through `budget`
    to the lesson — the day still ends in exactly one row."""
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", EVERY_RAW)
    clock = [1000.0]
    monkeypatch.setattr(ss, "_mono", lambda: clock[0])
    news = _ClockNews(clock)
    row = await _select(runs, news, TUE_CONGRESS)
    total = ss.TEMPLATE_BUILD_BUDGET_SECONDS
    slack = ss.SERIES_WAIT_SLACK_SECONDS
    assert news.offered == {"congress_count": 60.0, "thirteen_f": 150.0,
                            "company_stakes": round(total - (60.0 + slack) - (150.0 + slack), 6)}
    trail = row["fact_sheet"]["selection"]["trail"]
    assert [(t["series"], t["outcome"]) for t in trail] == [
        ("congress_count", "timeout"), ("thirteen_f", "timeout"), ("company_stakes", "timeout"),
        ("theme_explainer", "budget"), ("money_map", "budget"), (selection.LESSON, "chosen")]
    assert row["template_id"] in selection.LESSON_TEMPLATE_IDS


@pytest.mark.asyncio
async def test_the_default_switch_asks_for_every_2a_series_of_the_day(world):
    _, runs = world
    news = EmptyNews()
    row = await _select(runs, news, MON)
    assert news.calls == ["ceo_buys", "insider_buys", "money_map"]
    block = row["fact_sheet"]["selection"]
    assert block["chain"] == ["ceo_buys", "insider_buys", "money_map", selection.LESSON]
    assert [t["series"] for t in block["trail"]] == ["ceo_buys", "insider_buys", "money_map", selection.LESSON]
    assert row["template_id"] in selection.LESSON_TEMPLATE_IDS


@pytest.mark.parametrize("raw, day, asked", [
    ("money_map", MON, ["money_map"]),
    (" MONEY_MAP , Ceo_Buys ", MON, ["ceo_buys", "money_map"]),       # whitespace / case, chain order kept
    ("insider_buys", MON, ["insider_buys"]),
    ("money_map", THU, ["money_map"]),
    ("thirteen_f", TUE_CONGRESS, ["thirteen_f"]),
])
@pytest.mark.asyncio
async def test_a_series_off_in_the_switch_is_never_asked_for(world, monkeypatch, raw, day, asked):
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", raw)
    news = EmptyNews()
    row = await _select(runs, news, day)
    assert news.calls == asked
    assert row["fact_sheet"]["selection"]["chain"] == [*asked, selection.LESSON]


@pytest.mark.asyncio
async def test_a_series_in_the_switch_that_is_not_shipped_is_ignored_and_logged(world, monkeypatch, caplog):
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", "congress_count,thirteen_f,money_map")
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - {"congress_count"})
    news = EmptyNews()
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        row = await _select(runs, news, TUE_CONGRESS)
    assert news.calls == ["thirteen_f", "money_map"]
    assert row["fact_sheet"]["selection"]["chain"] == ["thirteen_f", "money_map", selection.LESSON]
    errors = _errors(caplog, _SELECTION_LOGGER)
    assert any("not shipped" in m and "congress_count" in m for m in errors), errors


@pytest.mark.asyncio
async def test_a_series_listed_ahead_runs_the_day_it_ships(world, monkeypatch):
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", "congress_count,thirteen_f")
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - {"congress_count"})
    news = EmptyNews()
    await _select(runs, news, TUE_CONGRESS)
    assert news.calls == ["thirteen_f"]
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES | {"congress_count"})
    news = EmptyNews()
    await _select(runs, news, TUE_CONGRESS + timedelta(days=7 * 4 + 7))     # the January Congress Tuesday
    assert news.calls[:1] == ["congress_count"]


#: A day per 2b series' calendar slot, and the chain the build walks there with EVERY series listed.
_2B_DAYS = [
    (TUE_CONGRESS, ["congress_count", "thirteen_f", "company_stakes", "theme_explainer", "money_map"]),
    (date(2026, 12, 29), ["company_stakes", "theme_explainer", "money_map"]),       # Tue, after the 13F season
    (THU, ["earnings", "money_map", "theme_explainer"]),                            # Thu, in earnings season
    (MON, ["ceo_buys", "insider_buys", "money_map", "theme_explainer"]),
]


@pytest.mark.parametrize("day, every", _2B_DAYS)
@pytest.mark.asyncio
async def test_the_default_switch_never_asks_for_a_2b_series_and_listing_them_does(world, monkeypatch, day, every):
    """Owner decision (2026-10-10): the 2b series ship in code but stay off in production through
    MARKETING_NEWS_SERIES. With its default the build never asks the adapter for one; with every series
    listed it walks the day's whole calendar chain."""
    from app.config import Settings

    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", Settings.model_fields["MARKETING_NEWS_SERIES"].default)
    news = EmptyNews()
    await _select(runs, news, day)
    assert not set(news.calls) & DROP_2B, news.calls
    assert news.calls == [s for s in every if s in DROP_2A]
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", EVERY_RAW)
    news = EmptyNews()
    row = await _select(runs, news, day + timedelta(days=7 * 52))          # the same weekday, a year on
    want = list(selection.enabled_chain(selection.plan_for(day + timedelta(days=7 * 52)).chain,
                                        frozenset({"A", "C", "F"})))[:-1]
    assert news.calls == want and set(want) & DROP_2B
    assert row["fact_sheet"]["selection"]["chain"] == [*want, selection.LESSON]


@pytest.mark.asyncio
async def test_every_series_listed_on_the_congress_tuesday_walks_the_whole_chain(world, monkeypatch):
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", EVERY_RAW)
    news = EmptyNews()
    row = await _select(runs, news, TUE_CONGRESS)
    assert news.calls == _2B_DAYS[0][1]
    assert [t["series"] for t in row["fact_sheet"]["selection"]["trail"]] == [*_2B_DAYS[0][1], selection.LESSON]


@pytest.mark.parametrize("raw", ["", "   ", "lesson", "bogus, lesson ,"])
@pytest.mark.asyncio
async def test_no_news_series_on_makes_every_day_the_lesson_and_never_loads_the_adapter(world, monkeypatch, raw):
    """An empty switch (or one naming nothing shipped) with the classes on: the chain is the lesson alone,
    selected synchronously with no selection block, and the company-news source is never loaded."""
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", raw)
    for day in (MON, TUE_CONGRESS, THU):
        svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
        monkeypatch.setattr(svc, "_news_source_fn", _never_load_news)
        row = await svc._select(_run(day))
        await _drain(svc)
        assert row["status"] == "selected" and row["template_id"] in selection.LESSON_TEMPLATE_IDS, day
        assert "selection" not in row["fact_sheet"], day


@pytest.mark.asyncio
async def test_the_switch_is_read_at_call_time(world, monkeypatch):
    """One service, two days: a change to the setting between them changes the next day's chain."""
    _, runs = world
    news = EmptyNews()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", "ceo_buys")
    await svc._select(_run(MON))
    await _drain(svc)
    assert news.calls == ["ceo_buys"]
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", "insider_buys,money_map")
    await svc._select(_run(MON_NEXT))
    await _drain(svc)
    assert news.calls == ["ceo_buys", "insider_buys", "money_map"]


# ══ MARKETING_CONTENT_CLASSES unset: byte for byte Drop 1, whatever the switch says ═════════════════


@pytest.mark.parametrize("raw", [DEFAULT_RAW, "", "nope,congress_count", 7])
@pytest.mark.asyncio
async def test_with_the_classes_unset_the_switch_is_never_read_and_every_day_is_drop_1(world, monkeypatch,
                                                                                    caplog, raw):
    """Classes at their default ("A"): every INSERT is exactly what `selection.choose` decides with the
    Drop-1 fact sheet, the adapter is never loaded, the switch is never parsed, and nothing about it is
    logged — not even for a value that would log an ERROR with a news class on."""
    from app.config import Settings

    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_CONTENT_CLASSES",
                        Settings.model_fields["MARKETING_CONTENT_CLASSES"].default)
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", raw)

    def never_parse(_raw):
        raise AssertionError("classes at 'A': the per-series switch must not be read")

    monkeypatch.setattr(selection, "parse_news_series", never_parse)
    pool = content_pool.eligible_keys()
    day, end = date(2026, 9, 21), date(2027, 3, 21)
    seen = 0
    with caplog.at_level(logging.INFO):
        while day < end:
            svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
            monkeypatch.setattr(svc, "_news_source_fn", _never_load_news)
            recent = await runs.recent_source_refs(day, ss.RECENT_LIMIT)
            want = selection.choose(pool, day, recent)
            row = await svc._select({"id": str(uuid.uuid4()), "run_date": day.isoformat()})
            if want.rest_day:
                assert row["status"] == "rest_day" and row.get("source_ref") is None, day
            else:
                item = content_pool.get_item(want.source_ref)
                assert (row["status"], row["source_ref"], row["template_id"]) == (
                    "selected", want.source_ref, want.template_id), day
                assert row["fact_sheet"] == ss._fact_sheet_snapshot(item), day       # no selection block
                seen += 1
            day += timedelta(days=1)
    assert seen > 60
    assert not [r for r in caplog.records if "MARKETING_NEWS_SERIES" in r.getMessage()]
    assert not [r for r in caplog.records if "news_series=" in r.getMessage()]
