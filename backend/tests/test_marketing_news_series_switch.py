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
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

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
#: The spotlight lane's single-company series (2026-11-15): REGISTERED with the lane, SHIPPED with their templates
#: (news-v22) — off in production until the switch lists them, like the 2b series.
SPOTLIGHT = frozenset({"insider_spotlight", "thirteen_f_spotlight"})
#: Every 2a + 2b series, in registry order (the main lane's series).
EVERY_RAW = ",".join(s.id for s in selection.SERIES if s.id in DROP_2A | DROP_2B)
#: Literally every registered id, the spotlight pair included.
REGISTRY_RAW = ",".join(s.id for s in selection.SERIES)
DEFAULT_RAW = "ceo_buys,insider_buys,thirteen_f,money_map"

MON = date(2026, 11, 16)            # a Monday in the 13F season (Q3 2026 due date, rolled to Mon 11-16)
MON_NEXT = date(2026, 11, 23)
THU = date(2026, 11, 19)
TUE_CONGRESS = date(2026, 12, 8)    # the December Congress Count Tuesday, still in the 13F season
SAT = date(2026, 11, 21)
assert (MON.weekday(), MON_NEXT.weekday(), THU.weekday(), TUE_CONGRESS.weekday(), SAT.weekday()) == (
    0, 0, 3, 1, 5)


@pytest.fixture(autouse=True)
def _declared_post_weekdays(monkeypatch):
    """Batch 3: the posting days are the web setting MARKETING_POST_WEEKDAYS, read at CALL time by `plan_for` — pin
    it to its declared default (Mon/Tue/Thu/Sat), so a developer's .env can never move a chain pinned here. A test
    that wants another cadence sets its own."""
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")


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
    # news-v22: the spotlight pair shipped — listing it turns it on, like any shipped series (no ERROR)
    ("insider_spotlight", {"insider_spotlight"}, False),
    ("money_map,thirteen_f_spotlight", {"money_map", "thirteen_f_spotlight"}, False),
    (REGISTRY_RAW, DROP_2A | DROP_2B | SPOTLIGHT, False),
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
    # news-v23 phase 3: the theme posts on Wednesday only, which the default calendar rests — the owner's production
    # calendar lists it (documents/OWNER_TASKS.md), so both calendars are walked
    for weekdays in (None, selection.parse_post_weekdays("mon,tue,wed,thu,sat")):
        day = date(2026, 10, 12)
        while day < date(2028, 10, 12):
            plan = selection.plan_for(day, weekdays)
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
    # …and so is a registered spotlight id before it ships (2026-11-15)
    ("Thirteen_F_Spotlight, insider_spotlight ,money_map", "insider_spotlight,money_map,thirteen_f_spotlight", False),
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
    assert SPOTLIGHT <= set(literals[0])                     # the spotlight pair is known to the deploy-time parse


# ══ the spotlight lane (2026-11-15): the same per-series switch gates both lanes ══════════════════════════


def test_the_spotlight_pair_is_kept_by_the_setting_and_the_kick_time_parse_once_shipped(monkeypatch, caplog):
    """The two spotlight series are known ids (the setting keeps them) and — news-v22 — shipped: the kick-time parse
    keeps them with no ERROR. A rollback that withdraws them (SHIPPED_SERIES narrowed) drops them again with ONE
    ERROR that names them — and names nothing it kept."""
    from app.config import Settings

    assert Settings(MARKETING_NEWS_SERIES="insider_spotlight,money_map").MARKETING_NEWS_SERIES == \
        "insider_spotlight,money_map"
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series("insider_spotlight,thirteen_f_spotlight,money_map") == (
            SPOTLIGHT | {"money_map"})
    assert _errors(caplog, _SELECTION_LOGGER) == []
    monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - SPOTLIGHT)
    with caplog.at_level(logging.ERROR, logger=_SELECTION_LOGGER):
        assert selection.parse_news_series("insider_spotlight,thirteen_f_spotlight,money_map") == {"money_map"}
    (msg,) = _errors(caplog, _SELECTION_LOGGER)
    assert "not shipped" in msg and "insider_spotlight" in msg and "thirteen_f_spotlight" in msg
    assert "money_map" not in msg


def test_the_default_switch_keeps_the_spotlight_pair_off_and_listing_it_runs_it_once_shipped(monkeypatch):
    """The setting's default lists neither spotlight series (one at a time, after a preview — marketing.md §3).
    The pair shipped (news-v22): listing it is what turns it on; the default still keeps it off."""
    from app.config import Settings

    default = Settings.model_fields["MARKETING_NEWS_SERIES"].default
    assert not set(default.split(",")) & SPOTLIGHT
    acf = frozenset({"A", "C", "F"})
    sat_lead = selection.spotlight_plan_for(SAT, (1, 3, 5)).chain
    # owner decisions 2026-11-20: the theme right after the insider list (a quiet Form 4 window posts a theme)
    assert sat_lead == ("thirteen_f_spotlight", "insider_spotlight", "theme_explainer", "money_map", selection.SKIP)
    assert SPOTLIGHT <= selection.SHIPPED_SERIES
    parse = selection.parse_news_series
    assert selection.enabled_chain(sat_lead, acf, shipped=parse(default)) == ("money_map", selection.SKIP)
    assert selection.enabled_chain(sat_lead, acf, shipped=parse(default + ",insider_spotlight")) == (
        "insider_spotlight", "money_map", selection.SKIP)
    assert selection.enabled_chain(sat_lead, acf, shipped=parse(REGISTRY_RAW)) == sat_lead


def test_the_series_kill_switch_gates_the_spotlight_lane_too():
    """There is no separate spotlight series switch: pulling a series from MARKETING_NEWS_SERIES stops it in BOTH
    lanes. With Money Map pulled (and the pair not listed — the default) a spotlight day's chain is the skip alone —
    never the lesson; the main lane on the same Thursday falls back exactly as it always did."""
    acf = frozenset({"A", "C", "F"})
    parse = selection.parse_news_series
    for day in (THU, SAT, date(2026, 11, 14)):
        chain = selection.spotlight_plan_for(day, (1, 3, 5)).chain
        assert selection.enabled_chain(chain, acf, shipped=parse(DEFAULT_RAW)) == ("money_map", selection.SKIP)
        pulled = selection.enabled_chain(chain, acf, shipped=parse("ceo_buys,insider_buys,thirteen_f"))
        assert pulled == (selection.SKIP,) and selection.LESSON not in pulled
    main_thu = selection.plan_for(THU).chain
    assert selection.enabled_chain(main_thu, acf, shipped=parse("ceo_buys,insider_buys,thirteen_f")) == (
        selection.LESSON,)


def test_no_build_budget_is_declared_for_an_unshipped_series():
    """A per-series build budget (script_service.SERIES_BUDGET_SECONDS) is added in the SAME change that
    ships its series (adds it to selection.SHIPPED_SERIES) — never ahead of it. The spotlight pair's budgets
    landed with their collectors (2026-11-15) and the pair shipped with its templates (news-v22): the strict pin
    of before is back."""
    assert set(ss.SERIES_BUDGET_SECONDS) <= set(selection.SHIPPED_SERIES)
    assert SPOTLIGHT <= set(ss.SERIES_BUDGET_SECONDS)


#: The adapter's measured worst case of ONE `candidates()` call, in seconds (its plumbing notes,
#: 2026-10-10; FMP's own 5xx retries included). Each build budget must stay above it.
#: news-v23 phase 3 fix round (2026-11-20): the weekly theme post's worst is ~114 s — 2 profile rounds and each of the
#: FIVE angles' own shares once (`company_news_adapter.theme_worst_seconds`, pinned equal below). The old ~47 s counted
#: two angles and three attempts; with five attempts a slow insider walk plus the Congress walk could spend the whole 60 s
#: before any record and the theme was lost for the week.
#: Phase 4 (2026-11-20): ~119 s — "how they make money" reads every member (two waves of 12 and one wave of older-years
#: reads for the gross stacks, the market-value batch beside the first wave: three rounds, 21 s).
_ADAPTER_WORST_SECONDS = {"congress_count": 30.0, "company_stakes": 5.0, "earnings": 20.0,
                          "theme_explainer": 119.0}


def test_each_2b_series_has_a_build_budget_above_the_adapters_worst_case():
    assert set(_ADAPTER_WORST_SECONDS) == DROP_2B
    for series, worst in _ADAPTER_WORST_SECONDS.items():
        cap = ss.SERIES_BUDGET_SECONDS.get(series)
        assert cap is not None and cap > worst, (series, cap, worst)
        assert ss.SERIES_MIN_START_SECONDS < cap <= ss.TEMPLATE_BUILD_BUDGET_SECONDS, series
    assert ss.SERIES_BUDGET_SECONDS == {"thirteen_f": 150.0, "congress_count": 60.0, "company_stakes": 45.0,
                                        "earnings": 45.0, "theme_explainer": 120.0,
                                        # the spotlight lane (2026-11-15): B decision 7's caps
                                        "insider_spotlight": 60.0, "thirteen_f_spotlight": 120.0}


def test_phase3_the_theme_series_worst_case_is_its_angles_own_shares_and_fits_its_budget():
    """Fix round 2026-11-20: each theme ANGLE runs under its own share of the time (THEME_ANGLE_BUDGET_SECONDS), so the
    call's worst before a record is two profile rounds plus the five shares — pinned equal to the table above, below the
    series' cap, and every share at least the angle's own measured worst at the repo's per-round yardstick (make_money
    and growth_profit two waves, biggest one batch, insiders four waves of one page, Congress the walk's ~30 s)."""
    from app.services.marketing import company_news_adapter as A

    assert set(A.THEME_ANGLE_BUDGET_SECONDS) == set(A.THEME_ANGLE_COLLECTORS)
    assert A.theme_worst_seconds() == pytest.approx(2 * A.THEME_WORST_ROUND_SECONDS
                                                    + sum(A.THEME_ANGLE_BUDGET_SECONDS.values()))
    assert A.theme_worst_seconds() <= _ADAPTER_WORST_SECONDS["theme_explainer"] < ss.SERIES_BUDGET_SECONDS[
        "theme_explainer"]
    # phase 4 (2026-11-20): make_money three rounds — every member (≤ R.THEME_MEMBERS[1]) in two waves of
    # THEME_SEGMENT_CONCURRENCY, then the older-years reads of the gross stacks; the market-value batch rides the first
    rounds = {"make_money": 3, "biggest": 1, "growth_profit": 2, "insiders": 4}
    from app.services.marketing import company_news_rules as R

    assert A.THEME_SEGMENT_LOOKUPS == R.THEME_MEMBERS[1]
    assert -(-A.THEME_SEGMENT_LOOKUPS // A.THEME_SEGMENT_CONCURRENCY) == 2
    for angle, n in rounds.items():
        assert A.THEME_ANGLE_BUDGET_SECONDS[angle] >= n * A.THEME_WORST_ROUND_SECONDS, angle
    assert A.THEME_ANGLE_BUDGET_SECONDS["congress"] >= _ADAPTER_WORST_SECONDS["congress_count"]
    assert A.THEME_MAX_ANGLE_ATTEMPTS == len(A.THEME_ANGLE_BUDGET_SECONDS) == 5


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


def test_phase3_every_wednesday_leaves_earnings_and_money_map_their_whole_budgets_for_ten_years():
    """news-v23 phase 3: the theme leads Wednesday at its worst (its 120 s since the fix round of 2026-11-20) and still
    leaves earnings (in season) and Money Map their whole 45 s each — on the owner's production calendar, every series
    on, ten years of Wednesdays."""
    acf = frozenset({"A", "C", "F"})
    weekdays = selection.parse_post_weekdays("mon,tue,wed,thu,sat")
    seen = {"earnings": 0, "money_map": 0}
    day = date(2026, 1, 7)                                          # a Wednesday
    while day < date(2036, 1, 1):
        chain = selection.enabled_chain(selection.plan_for(day, weekdays).chain, acf)
        assert chain[0] == "theme_explainer", day
        for target in ("earnings", "money_map"):
            if target in chain:
                left = ss.TEMPLATE_BUILD_BUDGET_SECONDS - _worst_spent_before(chain, target)
                assert left >= ss.SERIES_BUDGET_SECONDS.get(target, ss.DEFAULT_SERIES_BUDGET_SECONDS), (day, target)
                seen[target] += 1
        day += timedelta(days=7)
    assert seen["earnings"] >= 50 and seen["money_map"] >= 500         # both happen (anti-vacuity)


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


#: The adapter's worst case of ONE spotlight `candidates()` call (2026-11-15, its notes in script_service): the
#: insider spotlight is ceo_buys' path plus at most one more walk and ≤ 5 breakdowns at once; the 13F spotlight is the
#: roundup's walkers without Berkshire (the club's stored builds, ≤ 2 live registry builds).
_SPOTLIGHT_WORST_SECONDS = {"insider_spotlight": 40.0, "thirteen_f_spotlight": 100.0}


def test_each_spotlight_series_has_a_build_budget_above_its_worst_case():
    assert set(_SPOTLIGHT_WORST_SECONDS) == SPOTLIGHT
    for series, worst in _SPOTLIGHT_WORST_SECONDS.items():
        cap = ss.SERIES_BUDGET_SECONDS.get(series)
        assert cap is not None and cap > worst, (series, cap, worst)
        assert ss.SERIES_MIN_START_SECONDS < cap <= ss.TEMPLATE_BUILD_BUDGET_SECONDS, series
    # B decision 7: the 13F spotlight is capped BELOW the 13F roundup's live-Berkshire budget
    assert ss.SERIES_BUDGET_SECONDS["thirteen_f_spotlight"] == 120.0 < ss.SERIES_BUDGET_SECONDS["thirteen_f"]


def _worst_starts(chain) -> Dict[str, Optional[float]]:
    """Each series of `chain` → the seconds it starts with on the worst path (every earlier series spent its cap, or
    what was left, plus the wait slack), capped at its own cap; None when it never starts (under
    SERIES_MIN_START_SECONDS left)."""
    out: Dict[str, Optional[float]] = {}
    for series in chain:
        if series in selection.CHAIN_TERMINALS:
            break
        left = ss.TEMPLATE_BUILD_BUDGET_SECONDS - _worst_spent_before(chain, series)
        cap = ss.SERIES_BUDGET_SECONDS.get(series, ss.DEFAULT_SERIES_BUDGET_SECONDS)
        out[series] = None if left < ss.SERIES_MIN_START_SECONDS else min(left, cap)
    return out


#: The spotlight lane's worst path by chain shape, every series on (owner decisions 2026-11-20 put the theme, 120 s,
#: right after the insider list: "if it's a quite week - if we don't have enough data, then use different topic like
#: posting the theme"). Off season every series keeps its whole cap (62 + 122 + 45 ≤ 240). The two RECORDED exceptions,
#: both on the worst path only (the theme spent its whole 120 s — every angle slow, its measured worst is 118.4 s): in
#: season the 13F spotlight starts with 56 s of its 120 (the insider list 62 + the theme 122 before it), and on the
#: 13F-lead day the theme starts with 56 s (13F 122 + insider 62 before it); the Money Map after them does not start.
_SPOTLIGHT_WORST_STARTS = {
    "spotlight": {"insider_spotlight": 60.0, "theme_explainer": 120.0, "money_map": 45.0},
    "spotlight_13f": {"insider_spotlight": 60.0, "theme_explainer": 120.0, "thirteen_f_spotlight": 56.0,
                      "money_map": None},
    "spotlight_13f_lead": {"thirteen_f_spotlight": 120.0, "insider_spotlight": 60.0, "theme_explainer": 56.0,
                           "money_map": None},
}


@pytest.mark.parametrize("weekdays", [(1, 3, 5), (0, 2, 4), (5,), (0, 1, 2, 3, 4, 5, 6)],
                         ids=["tue_thu_sat", "mon_wed_fri", "sat_only", "every_day"])
def test_every_spotlight_chain_gives_every_series_its_whole_budget_for_ten_years(weekdays):
    """The spotlight lane's worst path (2026-11-15, B decision 7; owner decisions 2026-11-20 put the theme after the
    insider list): every series of every spotlight chain starts on its worst path with exactly what
    `_SPOTLIGHT_WORST_STARTS` pins for its chain shape — its WHOLE cap but for the two recorded exceptions there — and
    the build never runs past TEMPLATE_BUILD_BUDGET_SECONDS + the wait slack. With the spotlight pair SHIPPED and every
    series listed — the shape the owner switches on. (A Monday or Wednesday spotlight day drops the theme: the main
    lane holds it that day, C0 — `spotlight_chain`; this walks the calendar's plan, every series in it.)"""
    acf = frozenset({"A", "C", "F"})
    shipped = frozenset(selection.SHIPPED_SERIES) | SPOTLIGHT
    seen: Counter = Counter()
    day = date(2026, 1, 1)
    while day < date(2036, 1, 1):
        plan = selection.spotlight_plan_for(day, weekdays)
        if not plan.rest_day:
            chain = selection.enabled_chain(plan.chain, acf, shipped=shipped)
            assert chain[-1] == selection.SKIP and selection.LESSON not in chain, (day, chain)
            assert _worst_starts(chain) == _SPOTLIGHT_WORST_STARTS[plan.reason], (day, chain)
            assert _worst_spent_before(chain, selection.SKIP) <= (ss.TEMPLATE_BUILD_BUDGET_SECONDS
                                                                   + ss.SERIES_WAIT_SLACK_SECONDS), (day, chain)
            seen[plan.reason] += 1
        day += timedelta(days=1)
    # anti-vacuity: every chain shape happened (the 13F-lead day needs the season and the week's last spotlight day;
    # a one-day calendar has only that day, so its in-season days all lead with the 13F)
    assert {"spotlight", "spotlight_13f_lead"} <= set(seen), seen
    assert ("spotlight_13f" in seen) is (len(weekdays) > 1), seen


def test_the_13f_lead_spotlight_day_worst_path_is_pinned():
    """The longest spotlight chain, by its numbers (B decision 7; owner decisions 2026-11-20): 13F spotlight 120 + 2 s
    slack, insider list 60 + 2 s, then the theme starts with the 56 s left (never under SERIES_MIN_START_SECONDS — its
    first angle, make_money, needs 21 s), and the Money Map after it gets nothing on this worst path; the build ends at
    most the wait slack past its budget."""
    chain = ("thirteen_f_spotlight", "insider_spotlight", "theme_explainer", "money_map", selection.SKIP)
    assert _worst_spent_before(chain, "theme_explainer") == 122.0 + 62.0
    left = ss.TEMPLATE_BUILD_BUDGET_SECONDS - _worst_spent_before(chain, "theme_explainer")
    from app.services.marketing import company_news_adapter as A
    assert left == 56.0 >= A.THEME_ANGLE_BUDGET_SECONDS["make_money"] + 2 * A.THEME_WORST_ROUND_SECONDS
    assert _worst_spent_before(chain, selection.SKIP) == 122.0 + 62.0 + 58.0 == (
        ss.TEMPLATE_BUILD_BUDGET_SECONDS + ss.SERIES_WAIT_SLACK_SECONDS)


def test_the_theme_fits_after_the_insider_series_in_both_lanes():
    """Owner decisions 2026-11-20 (a quiet Form 4 week posts the theme): on MONDAY the theme starts with its WHOLE 120 s
    after the CEO and insider roundups at their worst (47 + 47), and the Money Map after it with 24 s of its 45; in the
    spotlight lane, after the insider list (62), the theme keeps its whole 120 s off season and in season — the theme
    budget fits the spotlight lane's build budget (pinned)."""
    acf = frozenset({"A", "C", "F"})
    every = frozenset(selection.SHIPPED_SERIES)
    monday = selection.enabled_chain(selection.plan_for(date(2026, 11, 16), selection.POST_WEEKDAYS).chain, acf,
                                     shipped=every)
    assert monday == ("ceo_buys", "insider_buys", "theme_explainer", "money_map", selection.LESSON)
    assert _worst_starts(monday) == {"ceo_buys": 45.0, "insider_buys": 45.0, "theme_explainer": 120.0,
                                     "money_map": 24.0}
    for shape, starts in _SPOTLIGHT_WORST_STARTS.items():
        if shape != "spotlight_13f_lead":
            assert starts["theme_explainer"] == ss.SERIES_BUDGET_SECONDS["theme_explainer"], shape
    assert (ss.SERIES_BUDGET_SECONDS["insider_spotlight"] + ss.SERIES_BUDGET_SECONDS["theme_explainer"]
            + 2 * ss.SERIES_WAIT_SLACK_SECONDS) <= ss.TEMPLATE_BUILD_BUDGET_SECONDS


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

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
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
    """A run held by the drop-2b worker (it declared it can draw a template, the 2b layouts included) — and, news-v23
    (2026-11-19), the look's earnings table (look_v23: an older holder's chain never holds earnings,
    tests/test_marketing_script_flow.py::test_the_earnings_table_is_kept_off_the_chain_of_a_holder_without_look_v23)."""
    return {"id": str(uuid.uuid4()), "run_date": day.isoformat(),
            "metadata": {"worker_capabilities": ["layouts_2b", "look_v23", "news_templates", "post_image"]}}


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

    async def candidates(self, series, *, run_date, exclude, limit, deadline, **_kw):
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
        ("money_map", "budget"), (selection.LESSON, "chosen")]           # news-v23 phase 3: the theme is Wednesday's
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


#: A day per 2b series' calendar slot, and the chain the build walks there with EVERY series listed. news-v23 phase 3:
#: the theme explainer runs on Wednesday only (a calendar that lists it: the owner's production one).
_2B_DAYS = [
    (TUE_CONGRESS, ["congress_count", "thirteen_f", "company_stakes", "money_map"]),
    (date(2026, 12, 29), ["company_stakes", "money_map"]),                          # Tue, after the 13F season
    (THU, ["earnings", "money_map"]),                                               # Thu, in earnings season
    (THU - timedelta(days=1), ["theme_explainer", "earnings", "money_map"]),        # Wed, in earnings season
]
_PHASE3_WEEKDAYS = "mon,tue,wed,thu,sat"


@pytest.mark.parametrize("day, every", _2B_DAYS)
@pytest.mark.asyncio
async def test_the_default_switch_never_asks_for_a_2b_series_and_listing_them_does(world, monkeypatch, day, every):
    """Owner decision (2026-10-10): the 2b series ship in code but stay off in production through
    MARKETING_NEWS_SERIES. With its default the build never asks the adapter for one; with every series
    listed it walks the day's whole calendar chain."""
    from app.config import Settings

    _, runs = world
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", _PHASE3_WEEKDAYS)
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


# ══ Batch 3 (2026-10-20): the chains at seven posting days a week ══════════════════════════════════
#
# MARKETING_POST_WEEKDAYS (default mon,tue,thu,sat) is read by `plan_for` at call time. At seven days Wednesday and
# Friday get Thursday's calendar (earnings in season, then the Money Map tail) and Sunday the Money Map tail alone —
# earnings leads Wed/Thu/Fri only (owner decision 3). The default cadence's chains do not move.

_SEVEN_DAYS = "mon,tue,wed,thu,fri,sat,sun"
#: The owner's production list at switch-on (plan, owner decision 5): every series except theme_explainer.
PRODUCTION_RAW = "ceo_buys,insider_buys,thirteen_f,money_map,earnings,company_stakes,congress_count"
WED_IN_SEASON, FRI_IN_SEASON, SUN_IN_SEASON = date(2026, 11, 4), date(2026, 11, 6), date(2026, 11, 8)
WED_OFF, FRI_OFF, SUN_OFF = date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 6)
assert [d.weekday() for d in (WED_IN_SEASON, FRI_IN_SEASON, SUN_IN_SEASON, WED_OFF, FRI_OFF, SUN_OFF)] == [
    2, 4, 6, 2, 4, 6]


def _chains(raw_series: str, days, cadence: str, monkeypatch) -> Dict[date, tuple]:
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", cadence)
    shipped = selection.parse_news_series(raw_series)
    return {d: selection.enabled_chain(selection.plan_for(d).chain, frozenset({"A", "C", "F"}), shipped=shipped)
            for d in days}


def _two_years():
    return [date(2026, 10, 19) + timedelta(days=i) for i in range(2 * 365)]


def test_batch3_seven_days_under_the_default_series_list(monkeypatch):
    """The switch's default lists no earnings (a 2b series): Wednesday, Friday and Sunday run Money Map → lesson, in
    season or not; Saturday is the lesson; Monday, Tuesday and Thursday walk exactly the chains of the default
    cadence. And at the default cadence Wednesday, Friday and Sunday stay rest days — nothing is asked for."""
    lesson = selection.LESSON
    seven = _chains(DEFAULT_RAW, _two_years(), _SEVEN_DAYS, monkeypatch)
    four = _chains(DEFAULT_RAW, _two_years(), "mon,tue,thu,sat", monkeypatch)
    for d in (WED_IN_SEASON, FRI_IN_SEASON, SUN_IN_SEASON, WED_OFF, FRI_OFF, SUN_OFF):
        assert seven[d] == ("money_map", lesson) and four[d] == (), d
    for d, chain in seven.items():
        if d.weekday() in (0, 1, 3, 5):
            assert chain == four[d], d
        else:
            assert chain == ("money_map", lesson) and four[d] == (), d
    assert seven[SAT] == (lesson,) == four[SAT]


def test_batch3_seven_days_under_the_owners_production_list(monkeypatch):
    """With earnings listed, it leads Wednesday, Thursday AND Friday in earnings season (and only then), then Money Map;
    Sunday is Money Map → lesson every week of two years — never earnings, even in season."""
    lesson = selection.LESSON
    seven = _chains(PRODUCTION_RAW, _two_years(), _SEVEN_DAYS, monkeypatch)
    for d in (WED_IN_SEASON, THU, FRI_IN_SEASON):
        assert seven[d] == ("earnings", "money_map", lesson), d
    for d in (WED_OFF, FRI_OFF, SUN_OFF, SUN_IN_SEASON):
        assert seven[d] == ("money_map", lesson), d
    earnings_days = Counter()
    for d, chain in seven.items():
        # news-v23 phase 3: Thu/Fri lead by `earnings_leads` (in season, or the Thursday right after a season that
        # ended on its Wednesday, the theme's day); this list holds no theme, so Wednesday leads with earnings in season
        lead = selection.in_earnings_season(d) if d.weekday() == 2 else selection.earnings_leads(d)
        if d.weekday() in (2, 3, 4):
            assert (chain[0] == "earnings") is lead, d
            earnings_days[d.weekday()] += lead
        else:
            assert "earnings" not in chain, d
    assert set(earnings_days) == {2, 3, 4} and min(earnings_days.values()) >= 25, earnings_days     # not vacuous
    # the same list at the default cadence: earnings only ever on a Thursday
    four = _chains(PRODUCTION_RAW, _two_years(), "mon,tue,thu,sat", monkeypatch)
    assert {d.weekday() for d, chain in four.items() if "earnings" in chain} == {3}


@pytest.mark.parametrize("day, asked, plan", [
    (WED_IN_SEASON, ["earnings", "money_map"], "wednesday_earnings"),
    (FRI_OFF, ["money_map"], "friday_off"),
    (SUN_IN_SEASON, ["money_map"], "sunday"),
])
@pytest.mark.asyncio
async def test_batch3_a_seven_day_build_asks_for_the_new_days_series(world, monkeypatch, day, asked, plan):
    """Through `script_service._select`: at seven days a Wednesday / Friday / Sunday is a template day whose build
    walks its new chain (the plan reason recorded in the selection block) and still ends in the lesson; at the
    default cadence the same day is a rest day and the company-news source is never asked."""
    _, runs = world
    monkeypatch.setattr(ss.settings, "MARKETING_NEWS_SERIES", PRODUCTION_RAW)
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", _SEVEN_DAYS)
    news = EmptyNews()
    row = await _select(runs, news, day)
    assert news.calls == asked
    block = row["fact_sheet"]["selection"]
    assert block["plan"] == plan and block["chain"] == [*asked, selection.LESSON]
    assert [t["series"] for t in block["trail"]] == [*asked, selection.LESSON]
    assert row["template_id"] in selection.LESSON_TEMPLATE_IDS
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    news = EmptyNews()
    rest = await _select(runs, news, day + timedelta(days=7))
    assert rest["status"] == "rest_day" and rest.get("source_ref") is None and news.calls == []


#: news-v23 phase 3 (2026-11-20): the production list once the weekly theme post is on (documents/OWNER_TASKS.md).
PHASE3_PRODUCTION_RAW = PRODUCTION_RAW + ",theme_explainer"


def test_phase3_the_production_lists_put_the_theme_on_wednesday_only_and_earnings_after_it(monkeypatch):
    """With theme_explainer listed and Wednesday on the calendar: every Wednesday leads with the theme (earnings right
    after it in season, then Money Map, then the lesson); every Monday asks for it right after the Form 4 pair (owner
    decisions 2026-11-20: a quiet Form 4 week posts a theme); no other day ever asks for a theme, and a Thursday leads
    with earnings in season or right after a season that ended on its Wednesday. Without Wednesday on the calendar the
    theme is only Monday's fallback."""
    lesson = selection.LESSON
    chains = _chains(PHASE3_PRODUCTION_RAW, _two_years(), _PHASE3_WEEKDAYS, monkeypatch)
    weds = 0
    for d, chain in chains.items():
        if d.weekday() == 2:
            weds += 1
            want = ("theme_explainer", "earnings", "money_map", lesson) if selection.in_earnings_season(d) else (
                "theme_explainer", "money_map", lesson)
            assert chain == want, d
        elif d.weekday() == 0:
            assert chain == ("ceo_buys", "insider_buys", "theme_explainer", "money_map", lesson), d
        else:
            assert "theme_explainer" not in chain, d
        if d.weekday() == 3:
            assert (chain[0] == "earnings") is selection.earnings_leads(d), d
    assert weds >= 100
    four = _chains(PHASE3_PRODUCTION_RAW, _two_years(), "mon,tue,thu,sat", monkeypatch)
    assert {d.weekday() for d, chain in four.items() if "theme_explainer" in chain} == {0}
