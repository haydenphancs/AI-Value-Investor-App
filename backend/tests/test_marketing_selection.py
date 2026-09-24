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
"""

from __future__ import annotations

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


def test_choose_never_returns_an_item_outside_the_pool():
    pool = _pool(5) + _pool(3, "money_moves")
    outsiders = ["journey:zzz", "money_moves:zzz"]
    for d in _posting_days(EPOCH, 60):
        s: Optional[str] = choose(pool, d, recent=outsiders + pool[:2]).source_ref
        assert s in pool
