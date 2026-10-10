"""
`app/services/marketing/selection.py` — cadence, rotation and template choice (§12.5).

Written independently of the module, adversarially. The properties pinned here are the ones a
public brand feed depends on, each checked against a brute-force oracle or over long ranges:

* **The posting-day ordinal** equals a brute-force count of posting days from `EPOCH`, across
  week boundaries, a leap day, dates before the epoch, the non-leap year 2100 and the extremes
  of `datetime.date` — and consecutive posting days are numbered consecutively, so rest days do
  not burn picks.
* **Rotation**: every item once per cycle (no repeat inside a cycle of `len(pool)` posting days);
  any `2n - 1` consecutive posting days cover the whole pool; and with the caller feeding
  `recent` back (the production loop), ANY `n` consecutive posts are all different.
* **`recent`**: a recent item is never chosen while an unrecent one exists; an all-recent pool
  still gets an item; stale refs are harmless.
* **Templates**: a Journey item never gets `case_story`; every eligible template appears within
  a bounded number of posting days; the item and template rotations are independent (a shared
  salt would pin each item to one template forever — shown by flipping the salt).
* **Determinism**: same inputs, same output, whatever the pool order or duplicates.
* **Template wording** (2026-10-05, shorter videos): every template opens on the hook; none asks
  for a fourth "opening card" or for "things to understand" (the primer of the 09-26 study-verb
  hooks); the counted templates forbid a count, the question-shaped ones a yes/no hook; only a
  money_moves-only template tells the hook to name the company; and rewriting a template's TEXT
  (or reordering the list) can never move the public schedule — only the set of ids can.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

import pytest

from app.services.marketing import content_pool
from app.services.marketing import selection as sel
from app.services.marketing.selection import (
    EPOCH,
    POST_WEEKDAYS,
    RECENT_WINDOW,
    TEMPLATES,
    Selection,
    choose,
    choose_item,
    choose_template,
    is_posting_day,
    posting_ordinal,
)


def _pool(n: int, kind: str = "journey") -> List[str]:
    return [f"{kind}:item{i:03d}" for i in range(n)]


def _brute_ordinal(d: date) -> int:
    """Posting days in [EPOCH, d) — or minus the posting days in [d, EPOCH) before the epoch."""
    if d >= EPOCH:
        return sum(1 for i in range((d - EPOCH).days) if (EPOCH + timedelta(i)).weekday() in POST_WEEKDAYS)
    return -sum(1 for i in range((EPOCH - d).days) if (d + timedelta(i)).weekday() in POST_WEEKDAYS)


def _posting_days(start: date, count: int) -> List[date]:
    out, d = [], start
    while len(out) < count:
        if is_posting_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _eligible_ids(kind: str) -> List[str]:
    return [t.id for t in TEMPLATES if kind in t.kinds]


# ── constants ─────────────────────────────────────────────────────────────────


def test_cadence_constants():
    assert POST_WEEKDAYS == (0, 1, 3, 5)
    assert len(set(POST_WEEKDAYS)) == len(POST_WEEKDAYS) and all(0 <= w <= 6 for w in POST_WEEKDAYS)
    assert EPOCH.weekday() == 0, "posting_ordinal's divmod assumes EPOCH is a Monday"
    assert sel.ITEM_SALT != sel.TEMPLATE_SALT
    assert {t.id for t in TEMPLATES} == {
        "myth_vs_fact", "three_takeaways", "case_story", "question_hook", "checklist"}
    assert set(sel.TEMPLATES_BY_ID) == {t.id for t in TEMPLATES}
    for t in TEMPLATES:
        assert t.kinds and t.kinds <= {"money_moves", "journey"}, t
        assert t.instructions.strip() and t.name.strip()
    assert sel.TEMPLATES_BY_ID["case_story"].kinds == frozenset({"money_moves"})
    # Every kind has at least two templates, or "rotation" would be a constant.
    for kind in ("money_moves", "journey"):
        assert len(_eligible_ids(kind)) >= 2


# ── template wording: every template opens on the hook (2026-10-05) ───────────


def test_every_template_opens_on_the_hook():
    """The hook is the first line a viewer hears, so the opener is the first thing each template
    asks for. "Opening card" asked for a fourth card against the prompt's exactly three; "things
    to understand" primed 5 of the 6 study-verb hooks ("Understand…") of the 2026-09-26 run."""
    for t in sel.TEMPLATES:
        assert t.instructions.startswith("Open"), (t.id, t.instructions[:40])
        low = t.instructions.lower()
        assert "opening card" not in low, t.id
        assert "to understand" not in low, t.id


@pytest.mark.parametrize("template_id", ["three_takeaways", "checklist"])
def test_the_counted_templates_forbid_a_count_in_the_hook(template_id):
    """"Three lessons from…" / "5 things to check" summarises instead of opening on a tension, and
    a number in the hook breaks the prompt's rule 5. These two are the templates built on a count."""
    low = sel.TEMPLATES_BY_ID[template_id].instructions.lower()
    assert re.search(r"\bnever\b.{0,80}?\bcount\b", low), low


@pytest.mark.parametrize("template_id", ["question_hook", "myth_vs_fact"])
def test_the_question_shaped_openers_rule_out_a_yes_no_hook(template_id):
    """The hook asks how, why or what; a yes/no myth question belongs in the script, never the hook."""
    low = sel.TEMPLATES_BY_ID[template_id].instructions.lower()
    assert re.search(r"\bnever (?:as )?a yes/no question\b", low), low


def test_only_a_case_study_template_tells_the_hook_to_name_the_company():
    """An investing lesson's hook names no company (the writer prompt's HOOK AND TITLES), so a
    template whose opener names the company must never be eligible for a Journey lesson."""
    naming = {t.id for t in sel.TEMPLATES if "naming the company" in t.instructions.lower()}
    assert naming == {"case_story"}, naming
    for tid in naming:
        assert sel.TEMPLATES_BY_ID[tid].kinds == frozenset({"money_moves"}), tid


def test_three_takeaways_matches_the_prompts_card_and_line_asks():
    """Three lessons, one per card, two script lines each: the writer prompt's exactly-3-cards and
    6-line ask. It used to add "an opening card" — four cards against the prompt's three."""
    from app.services.marketing import writer_prompts as wp

    low = sel.TEMPLATES_BY_ID["three_takeaways"].instructions.lower()
    assert "exactly three lessons" in low and "one lesson each" in low, low
    assert "two script lines each" in low, low
    assert (wp._ASK_CARD_COUNT, wp._LINES_PER_CARD) == (3, 2)


# ── posting ordinal ───────────────────────────────────────────────────────────


def test_posting_ordinal_matches_brute_force_over_six_years_around_the_epoch():
    """Every day from 2024 to 2030: before and after the epoch, every week boundary, the 2028
    leap day. Incremental oracle (one pass), independent of the module's divmod arithmetic."""
    start, end = date(2024, 1, 1), date(2030, 12, 31)
    expected = _brute_ordinal(start)
    d = start
    while d <= end:
        assert posting_ordinal(d) == expected, d
        if d.weekday() in POST_WEEKDAYS:
            expected += 1
        d += timedelta(days=1)


@pytest.mark.parametrize("d", [
    EPOCH, EPOCH - timedelta(days=1), EPOCH - timedelta(days=2), EPOCH + timedelta(days=6),
    EPOCH + timedelta(days=7), date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1),
    date(2000, 2, 29), date(1999, 12, 31), date(1970, 1, 1), date(2100, 2, 28),
    date(2100, 3, 1), date(2100, 12, 31),
])
def test_posting_ordinal_spot_dates(d):
    assert posting_ordinal(d) == _brute_ordinal(d)


def test_known_ordinals_around_the_epoch():
    # Mon 9/21 = 0, Tue = 1, Wed (rest) = 2, Thu = 2, Fri (rest) = 3, Sat = 3, Sun (rest) = 4.
    got = [posting_ordinal(EPOCH + timedelta(days=i)) for i in range(8)]
    assert got == [0, 1, 2, 2, 3, 3, 4, 4]
    # The day before the epoch is a Sunday (rest): nothing lies in [Sun, Mon) → 0; Sat → -1.
    assert posting_ordinal(EPOCH - timedelta(days=1)) == 0
    assert posting_ordinal(EPOCH - timedelta(days=2)) == -1


def test_leap_day_and_2100_are_ordinary_days():
    # 2028-02-29 is a Tuesday — a posting day that exists; the next day continues the count.
    feb29 = date(2028, 2, 29)
    assert is_posting_day(feb29)
    assert posting_ordinal(date(2028, 3, 1)) == posting_ordinal(feb29) + 1
    assert posting_ordinal(feb29) == posting_ordinal(date(2028, 2, 28)) + 1  # Mon Feb 28 posts
    # 2100 is not a leap year: Sun Feb 28 → Mon Mar 1, no posting day in between.
    assert date(2100, 2, 28) + timedelta(days=1) == date(2100, 3, 1)
    assert posting_ordinal(date(2100, 3, 1)) == posting_ordinal(date(2100, 2, 28))


def test_every_week_has_exactly_four_posting_days_and_the_ordinal_advances_by_four():
    for d in (date(1, 1, 8), date(1970, 1, 5), EPOCH - timedelta(days=700), EPOCH,
              date(2100, 6, 1), date.max - timedelta(days=8)):
        week = [d + timedelta(days=i) for i in range(7)]
        assert sum(is_posting_day(x) for x in week) == 4
        assert posting_ordinal(d + timedelta(days=7)) - posting_ordinal(d) == 4


def test_consecutive_posting_days_have_consecutive_ordinals():
    """Rest days do not consume picks: posting day N+1 is ordinal(N) + 1, across the epoch."""
    days = _posting_days(EPOCH - timedelta(days=400), 700)
    ords = [posting_ordinal(d) for d in days]
    assert ords == list(range(ords[0], ords[0] + len(ords)))
    assert 0 in ords and ords[0] < 0


def test_extreme_dates_never_raise():
    for d in (date.min, date.min + timedelta(days=1), date.max, date.max - timedelta(days=1),
              date(2100, 1, 4)):
        s = choose(_pool(7), d)
        assert isinstance(s, Selection)
        if is_posting_day(d):
            assert s.source_ref in _pool(7) and s.template_id in _eligible_ids("journey")


# ── rest days ─────────────────────────────────────────────────────────────────


def test_rest_days_select_nothing_and_posting_days_select_something():
    pool = _pool(9) + _pool(6, "money_moves")
    for i in range(-30, 120):
        d = EPOCH + timedelta(days=i)
        s = choose(pool, d)
        if d.weekday() in POST_WEEKDAYS:
            assert not s.rest_day and s.source_ref in pool and s.template_id
            assert s.posting_ordinal == posting_ordinal(d)
        else:
            assert s == Selection(rest_day=True), (d, s)


def test_rest_days_do_not_burn_picks():
    """The Nth posting day gets the pick for ordinal N, whatever calendar gap precedes it."""
    pool = _pool(11)
    for d in _posting_days(EPOCH - timedelta(days=60), 120):
        assert choose(pool, d).source_ref == choose_item(pool, posting_ordinal(d))


# ── rotation over the pool ────────────────────────────────────────────────────


def _rotation(pool: Sequence[str], ordinals: range) -> Dict[int, str]:
    return {o: choose_item(pool, o) for o in ordinals}


@pytest.mark.parametrize("n", [4, 5, 7, 12, 36])
def test_no_item_repeats_within_a_cycle_of_len_pool_posting_days(n):
    """Posting ordinals map one-to-one onto rotation days (`EPOCH.toordinal() + ordinal`), and
    `pick_for_day` walks the pool in aligned cycles of `n` days. Over 2n+ posting days on each
    side of the epoch, every complete cycle is a permutation of the pool."""
    pool = _pool(n)
    picks = _rotation(pool, range(-3 * n, 3 * n))
    cycles: Dict[int, List[str]] = {}
    for o, key in picks.items():
        cycles.setdefault((EPOCH.toordinal() + o) // n, []).append(key)
    full = [c for c in cycles.values() if len(c) == n]
    assert len(full) >= 4
    for c in full:
        assert sorted(c) == sorted(pool), c


@pytest.mark.parametrize("n", [2, 3, 4, 5, 9, 35])
def test_any_2n_minus_1_consecutive_posting_days_cover_the_pool_and_no_back_to_back(n):
    pool = _pool(n)
    picks = [choose_item(pool, o) for o in range(-4 * n, 6 * n)]
    for i in range(len(picks) - 1):
        assert picks[i] != picks[i + 1], (i, picks[i])
    if n >= 4:  # below 4 the walk is the modular window, which covers in n consecutive days
        for i in range(len(picks) - (2 * n - 1) + 1):
            assert set(picks[i:i + 2 * n - 1]) == set(pool), i


def test_the_real_eligible_pool_rotates_through_every_item():
    pool = content_pool.eligible_keys()
    assert len(pool) >= 25
    n = len(pool)
    seen = [choose(pool, d).source_ref for d in _posting_days(EPOCH, 2 * n)]
    assert set(seen) == set(pool)


def _simulate(pool: Sequence[str], start: date, count: int) -> List[str]:
    """The production loop: each run passes the previous source_refs, MOST RECENT FIRST."""
    history: List[str] = []
    for d in _posting_days(start, count):
        s = choose(pool, d, recent=list(reversed(history)))
        assert s.source_ref is not None
        history.append(s.source_ref)
    return history


@pytest.mark.parametrize("n", [2, 3, 4, 7, 35, 61])
def test_with_recent_fed_back_any_n_consecutive_posts_are_all_different(n):
    pool = _pool(n)
    history = _simulate(pool, EPOCH - timedelta(days=30), 4 * n)
    for i in range(len(history) - n + 1):
        window = history[i:i + n]
        assert len(set(window)) == n, (i, window)


def test_recent_window_is_capped_for_large_pools():
    """Above RECENT_WINDOW + 1 items only the last RECENT_WINDOW picks are avoided — still no
    repeat inside any RECENT_WINDOW + 1 consecutive posts."""
    n = RECENT_WINDOW + 5
    history = _simulate(_pool(n), EPOCH, 3 * n)
    w = RECENT_WINDOW + 1
    for i in range(len(history) - w + 1):
        assert len(set(history[i:i + w])) == w, i


# ── recent-skip ───────────────────────────────────────────────────────────────


def test_a_recent_item_is_never_chosen_while_an_unrecent_one_exists():
    pool = _pool(8)
    for d in _posting_days(EPOCH - timedelta(days=20), 40):
        o = posting_ordinal(d)
        plain = choose_item(pool, o)
        # The rotation's own pick is recent → something else, and not recent either.
        s = choose(pool, d, recent=[plain])
        assert s.source_ref != plain and s.source_ref in pool
        # Exactly one unrecent item → that item, whichever it is.
        for keep in pool:
            recent = [k for k in pool if k != keep]          # n - 1 entries = the full window
            assert choose(pool, d, recent=recent).source_ref == keep, (d, keep)
            assert choose_item(pool, o, recent) == keep


def test_an_all_recent_pool_still_returns_an_item():
    pool = _pool(3)
    d = EPOCH
    # choose_item with EVERY item recent returns the rotation's own pick.
    assert choose_item(pool, 0, recent=pool) == choose_item(pool, 0)
    # choose() honours only the last n-1 refs, so the least recent of the three is picked.
    assert choose(pool, d, recent=["journey:item000", "journey:item001", "journey:item002"]).source_ref \
        == "journey:item002"
    # A one-item pool has a zero-length window: the item is chosen even though it is recent.
    assert choose(["journey:only"], d, recent=["journey:only"]).source_ref == "journey:only"


def test_stale_and_duplicate_recent_refs_are_harmless():
    pool = _pool(6)
    d = EPOCH + timedelta(days=1)
    base = choose(pool, d)
    assert choose(pool, d, recent=["money_moves:deleted", "journey:gone"]) == base
    s = choose(pool, d, recent=[base.source_ref] * 50)
    assert s.source_ref != base.source_ref
    assert choose(pool, d, recent=iter([base.source_ref])) == s  # any iterable


def test_an_empty_pool_selects_nothing_but_is_still_a_posting_day():
    for d in _posting_days(EPOCH, 4):
        s = choose([], d, recent=["journey:x"])
        assert s == Selection(rest_day=False, source_ref=None, template_id=None,
                              posting_ordinal=posting_ordinal(d))
    assert choose([], EPOCH + timedelta(days=2)) == Selection(rest_day=True)
    assert choose_item([], 0) is None and choose_item([], 5, ["x"]) is None


# ── templates ─────────────────────────────────────────────────────────────────


def test_a_journey_item_never_gets_case_story():
    for o in range(-500, 500):
        t = choose_template("journey", o)
        assert t != "case_story" and t in _eligible_ids("journey"), o
    pool = _pool(9, "journey")
    for d in _posting_days(EPOCH - timedelta(days=100), 300):
        assert choose(pool, d).template_id != "case_story"


def test_money_moves_items_do_get_case_story_sometimes():
    got = {choose_template("money_moves", o) for o in range(0, 50)}
    assert "case_story" in got


def test_the_template_matches_the_chosen_items_kind_in_a_mixed_pool():
    pool = _pool(7, "journey") + _pool(7, "money_moves")
    for d in _posting_days(EPOCH, 200):
        s = choose(pool, d)
        kind = s.source_ref.split(":", 1)[0]
        assert kind in sel.TEMPLATES_BY_ID[s.template_id].kinds, s


@pytest.mark.parametrize("kind", ["money_moves", "journey"])
def test_every_eligible_template_appears_within_a_bounded_number_of_posting_days(kind):
    ids = _eligible_ids(kind)
    bound = 2 * len(ids) - 1
    for start in range(-200, 400):
        window = {choose_template(kind, o) for o in range(start, start + bound)}
        assert window == set(ids), (kind, start, window)


def test_unknown_kind_has_no_template_rather_than_a_wrong_one():
    """A pool key with an unrecognised kind prefix has no eligible template; the module raises
    IndexError there. Pinned so a future kind is added deliberately, not by accident."""
    with pytest.raises(IndexError):
        choose_template("podcast", 0)


def _pairings(pool: Sequence[str], count: int) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for d in _posting_days(EPOCH, count):
        s = choose(pool, d)
        out.setdefault(s.source_ref, set()).add(s.template_id)
    return out


def test_item_and_template_rotations_are_independent(monkeypatch):
    """Five Money Moves items and five templates: with independent salts each item meets
    several templates over 40 cycles. The same pool under a SHARED salt pins every item to one
    template forever — asserted below, which is what proves this test can fail."""
    pool = _pool(5, "money_moves")
    assert len(_eligible_ids("money_moves")) == len(pool)
    pairs = _pairings(pool, 200)
    assert set(pairs) == set(pool)
    assert all(len(ts) >= 3 for ts in pairs.values()), pairs

    monkeypatch.setattr(sel, "TEMPLATE_SALT", sel.ITEM_SALT)
    locked = _pairings(pool, 200)
    assert all(len(ts) == 1 for ts in locked.values()), locked


# ── determinism ───────────────────────────────────────────────────────────────


def test_selection_is_a_pure_function_of_the_set_of_inputs():
    pool = _pool(12) + _pool(5, "money_moves")
    shuffled = list(reversed(pool)) + pool[:4]        # other order + duplicates
    for d in _posting_days(EPOCH - timedelta(days=10), 30):
        a = choose(pool, d, recent=pool[:3])
        assert a == choose(pool, d, recent=pool[:3])
        assert a == choose(shuffled, d, recent=list(pool[:3]))
        assert a == choose(tuple(pool), d, recent=tuple(pool[:3]))


#: Golden picks. Changing EPOCH, POST_WEEKDAYS, a salt or the template list reshuffles the
#: public schedule for every future day — update these only when that is the intent.
_GOLDEN_POOL = [f"journey:item{i:02d}" for i in range(10)] + [f"money_moves:case{i:02d}" for i in range(5)]
_GOLDEN = {
    date(2026, 9, 21): ("journey:item09", "question_hook", 0),
    date(2026, 9, 22): ("money_moves:case01", "three_takeaways", 1),
    date(2026, 9, 24): ("journey:item02", "myth_vs_fact", 2),
    date(2026, 9, 26): ("journey:item06", "three_takeaways", 3),
    date(2027, 1, 4): ("money_moves:case04", "question_hook", 60),
    date(2025, 12, 29): ("journey:item06", "myth_vs_fact", -152),
}


@pytest.mark.parametrize("d", sorted(_GOLDEN))
def test_golden_schedule(d):
    ref, template, ordinal = _GOLDEN[d]
    assert choose(_GOLDEN_POOL, d) == Selection(False, ref, template, ordinal)


def test_template_wording_and_order_never_move_the_schedule(monkeypatch):
    """Rewriting a template's TEXT (2026-10-05: every opener) must not reshuffle the public
    schedule: only the SET of template ids per kind feeds the rotation (`pick_for_day` sorts its
    pool). Proved with every name and instruction blanked and the list reversed — and, so this
    can fail, by dropping one id, which does move it."""
    days = _posting_days(EPOCH - timedelta(days=200), 400)
    before = [choose(_GOLDEN_POOL, d) for d in days]
    blank = tuple(dataclasses.replace(t, name="x", instructions="x") for t in reversed(TEMPLATES))
    monkeypatch.setattr(sel, "TEMPLATES", blank)
    assert [choose(_GOLDEN_POOL, d) for d in days] == before
    for d, (ref, template, ordinal) in _GOLDEN.items():
        assert choose(_GOLDEN_POOL, d) == Selection(False, ref, template, ordinal), d
    monkeypatch.setattr(sel, "TEMPLATES", tuple(t for t in blank if t.id != "checklist"))
    assert [choose(_GOLDEN_POOL, d) for d in days] != before


def test_choose_never_returns_an_item_outside_the_pool():
    pool = _pool(5) + _pool(3, "money_moves")
    outsiders = ["journey:zzz", "money_moves:zzz"]
    for d in _posting_days(EPOCH, 60):
        s: Optional[str] = choose(pool, d, recent=outsiders + pool[:2]).source_ref
        assert s in pool


# ══ Drop 2 ("Company Weekly"): the class registry and the weekly series calendar ══════════════

from app.schemas import marketing as mschemas  # noqa: E402
from app.services.trillion_club import rules as tc_rules  # noqa: E402

#: APPEND-ONLY. Every template id a `marketing_scripts` row may hold, with the class it decides.
#: A stored row keeps its template_id for good, so an id that stops resolving (renamed, removed,
#: re-classed) turns an accepted day into a post with no gate. Add rows; never edit or drop one.
KNOWN_TEMPLATE_IDS = {
    "myth_vs_fact": "A", "three_takeaways": "A", "case_story": "A", "question_hook": "A", "checklist": "A",
    "ceo_buys": "C", "insider_buys": "C", "thirteen_f": "C", "congress_count": "C",
    "company_stakes": "F", "earnings": "F", "money_map": "F", "theme_explainer": "F",
}


def test_every_known_template_id_still_resolves_to_its_class():
    for template_id, klass in KNOWN_TEMPLATE_IDS.items():
        assert sel.content_class_of(template_id) == klass, template_id
    # The registry holds nothing the pin does not know (a new id is added to the pin with it).
    assert set(sel.TEMPLATES_BY_ID) | set(sel.SERIES_BY_ID) == set(KNOWN_TEMPLATE_IDS)


def test_lesson_and_series_ids_are_disjoint_and_series_are_news_classes():
    assert not set(sel.TEMPLATES_BY_ID) & set(sel.SERIES_BY_ID)
    assert sel.LESSON not in sel.TEMPLATES_BY_ID and sel.LESSON not in sel.SERIES_BY_ID
    ids = [s.id for s in sel.SERIES]
    assert len(ids) == len(set(ids)) and set(ids) == set(sel.SERIES_BY_ID)
    for s in sel.SERIES:
        assert s.content_class in mschemas.NEWS_CLASSES, s
        assert s.name.strip() and s.id == s.id.strip().lower(), s
    assert {s.content_class for s in sel.SERIES} == set(mschemas.NEWS_CLASSES)
    assert set(mschemas.CONTENT_CLASSES) == {"A"} | {s.content_class for s in sel.SERIES}


@pytest.mark.parametrize("bad", [None, "", "lesson", "CEO_BUYS", " ceo_buys", "ceo_buys ", "news:ceo_buys",
                                 "x", 7, True, ["ceo_buys"]])
def test_content_class_of_anything_else_is_none(bad):
    assert sel.content_class_of(bad) is None


#: Drop 2a's series (the per-series switch's production default) and drop 2b's.
DROP_2A = frozenset({"ceo_buys", "insider_buys", "thirteen_f", "money_map"})
DROP_2B = frozenset({"congress_count", "company_stakes", "earnings", "theme_explainer"})


def test_every_series_has_shipped_since_drop_2b():
    """Drop 2b ships the last four series in CODE: SHIPPED_SERIES is now every series. Production still
    runs only what the per-series switch lists (its default is the 2a four — test_marketing_news_series_
    switch.py pins that the 2b days stay off with it)."""
    assert sel.SHIPPED_SERIES == DROP_2A | DROP_2B == frozenset(sel.SERIES_BY_ID)
    assert not DROP_2A & DROP_2B
    assert isinstance(sel.SHIPPED_SERIES, frozenset)


def test_lesson_constants_match_the_content_pool_and_the_template_registry():
    assert sel.LESSON_KINDS == (content_pool.MONEY_MOVES, content_pool.JOURNEY)
    assert set(sel.LESSON_TEMPLATE_IDS) <= set(sel.TEMPLATES_BY_ID)
    for kind in sel.LESSON_KINDS:
        fits = [t for t in sel.LESSON_TEMPLATE_IDS if kind in sel.TEMPLATES_BY_ID[t].kinds]
        assert len(fits) >= 2, (kind, fits)  # or the weekly lesson's template would be a constant


# ── 13F season ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("due, quarter", [
    (date(2026, 11, 16), (2026, 3)),   # Nov 14 is a Saturday → Monday
    (date(2027, 2, 16), (2026, 4)),    # Sun Feb 14, then Washington's Birthday Mon Feb 15
    (date(2027, 5, 17), (2027, 1)),    # Sat May 15 → Monday
    (date(2026, 8, 14), (2026, 2)),    # a Friday: not rolled
])
def test_thirteen_f_season_edges(due, quarter):
    assert tc_rules.sec_13f_due_date(*quarter) == due
    assert sel.thirteen_f_season(due) == quarter
    assert sel.thirteen_f_season(due + timedelta(days=41)) == quarter
    assert sel.thirteen_f_season(due + timedelta(days=42)) is None
    assert sel.thirteen_f_season(due - timedelta(days=1)) is None


def test_thirteen_f_season_matches_a_brute_force_oracle_over_ten_years():
    seasons = []
    for year in range(2024, 2037):
        for q in (1, 2, 3, 4):
            due = tc_rules.sec_13f_due_date(year, q)
            seasons.append(((year, q), due, due + timedelta(days=sel.THIRTEEN_F_SEASON_DAYS)))
    d = date(2025, 1, 1)
    while d <= date(2035, 12, 31):
        expected = [yq for yq, lo, hi in seasons if lo <= d < hi]
        assert len(expected) <= 1, (d, expected)  # seasons never overlap
        assert sel.thirteen_f_season(d) == (expected[0] if expected else None), d
        d += timedelta(days=1)


def test_the_first_13f_tuesdays_of_the_q3_2026_wave():
    tuesdays = [d for d in (date(2026, 11, 16) + timedelta(days=i) for i in range(60))
                if d.weekday() == 1 and sel.thirteen_f_season(d)]
    assert tuesdays == [date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 1), date(2026, 12, 8),
                        date(2026, 12, 15), date(2026, 12, 22)]


@pytest.mark.parametrize("d", [date.min, date(1, 3, 31), date(1900, 6, 1), date(9999, 1, 2), date.max])
def test_the_calendar_never_raises_at_the_extremes(d):
    assert sel.thirteen_f_season(d) is None
    plan = sel.plan_for(d)
    assert plan.rest_day == (not is_posting_day(d))
    assert isinstance(sel.in_earnings_season(d), bool)


# ── Congress Count Tuesday and disclosure month ───────────────────────────────


@pytest.mark.parametrize("d", [date(2026, 11, 10), date(2026, 12, 8), date(2027, 1, 12)])
def test_known_congress_count_days(d):
    assert sel.is_congress_count_day(d)
    assert not sel.is_congress_count_day(d - timedelta(days=7))
    assert not sel.is_congress_count_day(d + timedelta(days=7))
    assert not sel.is_congress_count_day(d + timedelta(days=1))


def test_exactly_one_congress_tuesday_a_month_for_ten_years():
    per_month: Dict[tuple, List[date]] = {}
    d = date(2026, 1, 1)
    while d < date(2036, 1, 1):
        if sel.is_congress_count_day(d):
            per_month.setdefault((d.year, d.month), []).append(d)
        d += timedelta(days=1)
    assert len(per_month) == 120
    for days in per_month.values():
        assert len(days) == 1 and days[0].weekday() == 1 and 8 <= days[0].day <= 14
        month_end = days[0].replace(day=1) - timedelta(days=1)
        assert (days[0] - month_end).days >= 7  # at least 7 days after the disclosure month ended


@pytest.mark.parametrize("d, month", [
    (date(2026, 12, 8), "2026-11"), (date(2027, 1, 12), "2026-12"), (date(2026, 3, 10), "2026-02"),
    (date(2028, 3, 14), "2028-02"), (date(2026, 11, 1), "2026-10"),
])
def test_congress_disclosure_month_is_the_calendar_month_before(d, month):
    assert sel.congress_disclosure_month(d) == month


# ── earnings season ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("quarter_end", [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30),
                                         date(2026, 12, 31), date(2028, 3, 31)])
def test_earnings_season_bounds(quarter_end):
    def at(n: int) -> bool:
        return sel.in_earnings_season(quarter_end + timedelta(days=n))

    assert (at(0), at(9), at(10), at(56), at(57)) == (False, False, True, True, False)


def test_earnings_season_rolls_over_january_and_the_named_thursdays():
    assert not sel.in_earnings_season(date(2027, 1, 9)) and sel.in_earnings_season(date(2027, 1, 10))
    assert sel.in_earnings_season(date(2027, 2, 25)) and not sel.in_earnings_season(date(2027, 2, 26))
    assert sel.in_earnings_season(date(2026, 11, 19)) and not sel.in_earnings_season(date(2026, 11, 26))
    assert not sel.in_earnings_season(date(2026, 10, 8)) and sel.in_earnings_season(date(2026, 10, 15))
    assert sel.in_earnings_season(date(2027, 1, 14))


# ── plan_for and enabled_chain ────────────────────────────────────────────────


def test_the_named_days_plans():
    assert sel.plan_for(date(2026, 12, 8)).chain[:2] == ("congress_count", "thirteen_f")
    assert sel.plan_for(date(2026, 12, 8)).reason == "tuesday_congress_13f"
    assert sel.plan_for(date(2026, 11, 17)).chain[0] == "thirteen_f"
    assert sel.plan_for(date(2026, 11, 10)).chain[:2] == ("congress_count", "company_stakes")
    assert sel.plan_for(date(2026, 10, 13)).reason == "tuesday_congress"
    assert sel.plan_for(date(2026, 10, 20)) == sel.DayPlan(
        False, ("company_stakes", "theme_explainer", "money_map", sel.LESSON), "tuesday")
    assert sel.plan_for(date(2026, 11, 16)) == sel.DayPlan(
        False, ("ceo_buys", "insider_buys", "money_map", "theme_explainer", sel.LESSON), "monday")
    assert sel.plan_for(date(2026, 11, 19)).chain[0] == "earnings"
    assert sel.plan_for(date(2026, 11, 26)) == sel.DayPlan(
        False, ("money_map", "theme_explainer", sel.LESSON), "thursday_off")
    assert sel.plan_for(date(2026, 11, 21)) == sel.DayPlan(False, (sel.LESSON,), "saturday")
    for rest in (date(2026, 11, 18), date(2026, 11, 20), date(2026, 11, 22)):
        assert sel.plan_for(rest) == sel.DayPlan(True)


def test_every_chain_ends_in_the_lesson_without_duplicates_for_ten_years():
    d = date(2026, 1, 1)
    while d < date(2036, 1, 1):
        plan = sel.plan_for(d)
        if not is_posting_day(d):
            assert plan == sel.DayPlan(True), d
        else:
            assert not plan.rest_day and plan.chain and plan.chain[-1] == sel.LESSON, (d, plan)
            assert plan.chain.count(sel.LESSON) == 1 and len(set(plan.chain)) == len(plan.chain), (d, plan)
            assert set(plan.chain) - {sel.LESSON} <= set(sel.SERIES_BY_ID), (d, plan)
            assert plan.reason != "lesson_only", d
            for classes in ({"A"}, {"A", "C"}, {"A", "F"}, {"A", "C", "F"}):
                chain = sel.enabled_chain(plan.chain, frozenset(classes))
                assert chain[-1] == sel.LESSON and len(set(chain)) == len(chain), (d, classes, chain)
                assert all(sel.SERIES_BY_ID[s].content_class in classes and s in sel.SHIPPED_SERIES
                           for s in chain[:-1]), (d, classes, chain)
        d += timedelta(days=1)


def test_enabled_chain_filters_by_class_shipping_and_order():
    tue = sel.plan_for(date(2026, 12, 8)).chain
    assert tue == ("congress_count", "thirteen_f", "company_stakes", "theme_explainer", "money_map", sel.LESSON)
    acf = frozenset({"A", "C", "F"})
    # every series has shipped: the default shipped set keeps the whole chain
    assert sel.enabled_chain(tue, acf) == tue
    assert sel.enabled_chain(tue, frozenset({"A", "C"})) == ("congress_count", "thirteen_f", sel.LESSON)
    assert sel.enabled_chain(tue, frozenset({"A", "F"})) == (
        "company_stakes", "theme_explainer", "money_map", sel.LESSON)
    assert sel.enabled_chain(tue, frozenset({"A"})) == (sel.LESSON,)
    # the shipped set (production passes the per-series switch) filters, keeping the chain's order
    assert sel.enabled_chain(tue, acf, shipped=DROP_2A) == ("thirteen_f", "money_map", sel.LESSON)
    assert sel.enabled_chain(tue, frozenset({"A", "C"}), shipped=DROP_2A) == ("thirteen_f", sel.LESSON)
    assert sel.enabled_chain(tue, acf, shipped=frozenset()) == (sel.LESSON,)
    monday = sel.plan_for(date(2026, 11, 16)).chain
    assert sel.enabled_chain(monday, acf, shipped=DROP_2A) == ("ceo_buys", "insider_buys", "money_map", sel.LESSON)
    assert sel.enabled_chain(monday, acf) == monday
    # unknown, duplicate and post-lesson steps are dropped; the lesson stays whatever the classes say
    assert sel.enabled_chain(("money_map", "nope", "money_map", sel.LESSON, "ceo_buys"), acf) == (
        "money_map", sel.LESSON)
    assert sel.enabled_chain(("ceo_buys", sel.LESSON), frozenset()) == (sel.LESSON,)
    assert sel.enabled_chain((), acf) == ()


def test_enabled_chain_reads_the_shipped_set_at_call_time(monkeypatch):
    """A series ships by its id joining SHIPPED_SERIES (and is withdrawn by leaving it); the default is
    read per call, never frozen at import."""
    tue = sel.plan_for(date(2026, 12, 8)).chain
    acf = frozenset({"A", "C", "F"})
    assert sel.enabled_chain(tue, acf)[:2] == ("congress_count", "thirteen_f")
    monkeypatch.setattr(sel, "SHIPPED_SERIES", sel.SHIPPED_SERIES - {"congress_count"})
    assert sel.enabled_chain(tue, acf)[:2] == ("thirteen_f", "company_stakes")


def test_classes_a_alone_is_todays_selection_byte_for_byte_for_two_years():
    """With MARKETING_CONTENT_CLASSES unset every posting day's chain is the lesson alone, and the
    lesson path (`lesson_refs` filter, no template narrowing) picks exactly what `choose` picks."""
    classes = mschemas.parse_content_classes("A")
    pool = _pool(9) + _pool(6, "money_moves")
    history: List[str] = []
    for d in _posting_days(date(2026, 10, 12), 2 * 52 * 4):
        assert sel.enabled_chain(sel.plan_for(d).chain, classes) == (sel.LESSON,), d
        recent = list(reversed(history))
        assert sel.lesson_refs(recent) == recent
        today = choose(pool, d, recent)
        assert choose(pool, d, sel.lesson_refs(recent), templates=None) == today, d
        history.append(today.source_ref)


# ── lesson_refs: news refs never starve the lesson rotation ───────────────────


def test_lesson_refs_keeps_only_lesson_refs_in_order():
    recent = ["news:ceo_buys:2026-11-09", "journey:a", "money_moves:b", "news:money_map:AAPL:FY2025",
              "journey", "journeys:x", None, 7, "money_moves:", "JOURNEY:c", "journey:d"]
    assert sel.lesson_refs(recent) == ["journey:a", "money_moves:b", "money_moves:", "journey:d"]
    assert sel.lesson_refs(iter(["journey:a"])) == ["journey:a"]
    assert sel.lesson_refs([]) == []


def _news_week_history(pool: Sequence[str], lessons: int, *, read_limit: int, use_filter: bool) -> List[str]:
    """The production loop once three news posts share the ledger with every lesson: each lesson
    day reads the newest `read_limit` refs of every kind (newest first), filters them (or not), and
    picks. Returns the lessons in order."""
    ledger: List[str] = []
    picked: List[str] = []
    days = _posting_days(date(2026, 11, 16), lessons * 4)
    for i, d in enumerate(days):
        if i % 4 != 3:      # Mon/Tue/Thu: a news post
            ledger.append(f"news:ceo_buys:{d.isoformat()}")
            continue
        recent = list(reversed(ledger))[:read_limit]
        s = choose(pool, d, sel.lesson_refs(recent) if use_filter else recent)
        picked.append(s.source_ref)
        ledger.append(s.source_ref)
    return picked


def test_34_consecutive_lessons_are_distinct_with_three_news_refs_per_lesson():
    """The real pool has 34 items; `choose` avoids the last min(33, 60) refs it is given. With news
    refs in the ledger the script service reads 240 rows (RECENT_WINDOW × 4 posting days) and
    filters them to lessons — remove either and the lesson feed repeats within 34 lessons."""
    pool = _pool(20) + _pool(14, "money_moves")
    news_read = sel.RECENT_WINDOW * len(POST_WEEKDAYS)
    assert news_read == 240
    picked = _news_week_history(pool, 70, read_limit=news_read, use_filter=True)
    for i in range(len(picked) - 34 + 1):
        assert len(set(picked[i:i + 34])) == 34, i

    def repeats(seq: List[str]) -> bool:
        return any(len(set(seq[i:i + 34])) < 34 for i in range(len(seq) - 34 + 1))

    # The test can fail: a 60-row read, or no filter, repeats lessons inside 34.
    assert repeats(_news_week_history(pool, 70, read_limit=sel.RECENT_WINDOW, use_filter=True))
    assert repeats(_news_week_history(pool, 70, read_limit=news_read, use_filter=False))


# ── the weekly lesson's template narrowing ────────────────────────────────────


@pytest.mark.parametrize("kind", ["money_moves", "journey"])
def test_choose_template_narrowed_to_the_lesson_templates(kind):
    got = {choose_template(kind, o, sel.LESSON_TEMPLATE_IDS) for o in range(-200, 200)}
    assert got == set(sel.LESSON_TEMPLATE_IDS)
    # None, or every eligible id, is the unchanged rotation.
    for o in range(-300, 300):
        assert choose_template(kind, o, None) == choose_template(kind, o) \
            == choose_template(kind, o, _eligible_ids(kind) + ["not_a_template"])


def test_choose_template_falls_back_when_nothing_allowed_fits(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger=sel.logger.name):
        assert choose_template("journey", 3, ["case_story"]) == choose_template("journey", 3)
        assert choose_template("journey", 3, []) == choose_template("journey", 3)
    assert sum("no allowed template" in r.getMessage() for r in caplog.records) == 2
    assert choose_template("journey", 5, "question_hook") == "question_hook"   # one id as a str
    with pytest.raises(IndexError):
        choose_template("podcast", 0, sel.LESSON_TEMPLATE_IDS)


def test_narrowing_the_templates_never_moves_the_item_rotation():
    pool = _pool(7, "journey") + _pool(7, "money_moves")
    for d in _posting_days(EPOCH - timedelta(days=100), 300):
        plain = choose(pool, d, pool[:3])
        narrow = choose(pool, d, pool[:3], templates=sel.LESSON_TEMPLATE_IDS)
        assert (narrow.rest_day, narrow.source_ref, narrow.posting_ordinal) == \
            (plain.rest_day, plain.source_ref, plain.posting_ordinal)
        assert narrow.template_id in sel.LESSON_TEMPLATE_IDS
    assert choose(pool, EPOCH + timedelta(days=2), templates=sel.LESSON_TEMPLATE_IDS) == Selection(rest_day=True)
