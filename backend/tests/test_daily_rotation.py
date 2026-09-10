"""Tests for `app/services/daily_rotation` — the daily starter-question walk.

Pure math, no I/O, no stubbing. Every guarantee the module's docstring claims is
asserted here, and the two claims it explicitly REFUSES to make (disjointness below
n >= 2k; ordered inequality at n == 1) are pinned as impossibilities so nobody
"strengthens" them later and spends an afternoon on a red test that cannot go green.
"""

from collections import Counter
from datetime import date, timedelta

import pytest

from app.services.daily_rotation import (
    day_index,
    normalize_pool,
    pick_for_day,
)


def _pool(n: int, prefix: str = "q") -> list[str]:
    return [f"{prefix}{i:04d}?" for i in range(n)]


def _days(start: str, count: int) -> list[str]:
    first = date.fromisoformat(start)
    return [(first + timedelta(days=i)).isoformat() for i in range(count)]


# ── degenerate inputs ────────────────────────────────────────────────────────


@pytest.mark.parametrize("pool, k", [([], 6), (_pool(10), 0), (_pool(10), -3)])
def test_degenerate_inputs_return_empty(pool, k):
    assert pick_for_day(pool, k, "2026-09-10") == []


def test_a_pool_smaller_than_k_returns_everything_rotated():
    pool = _pool(4)
    for day in _days("2026-09-10", 30):
        got = pick_for_day(pool, 6, day)
        assert sorted(got) == sorted(pool), "every item must still be served"
        assert len(got) == len(set(got)), "no duplicates even when n < k"


def test_a_single_question_pool_is_stable_and_does_not_raise():
    # The one case where "consecutive days differ" is impossible. Pinned so the
    # guarantee is never restated without its n > 1 qualifier.
    picks = {tuple(pick_for_day(["only?"], 6, d)) for d in _days("2026-09-10", 10)}
    assert picks == {("only?",)}


# ── the ordered-inequality guarantee ─────────────────────────────────────────


@pytest.mark.parametrize("n, k", [(2, 1), (5, 6), (6, 6), (7, 6), (11, 6), (20, 6), (100, 6)])
def test_consecutive_days_never_repeat_the_same_ordered_list(n, k):
    pool = _pool(n)
    days = _days("2020-01-01", 3000)
    previous = pick_for_day(pool, k, days[0])
    for day in days[1:]:
        current = pick_for_day(pool, k, day)
        assert current != previous, f"n={n} k={k} repeated an ordered list on {day}"
        previous = current


def test_n_just_above_k_cannot_be_disjoint_and_we_do_not_pretend_otherwise():
    """n=7, k=6 — two 6-windows out of 7 items MUST share at least 5.

    This is the trap in the original brief. Asserting disjointness here is
    arithmetically impossible; the module promises ordered inequality instead, and
    this test exists so the weaker claim is recognisably deliberate.
    """
    pool = _pool(7)
    a = pick_for_day(pool, 6, "2026-09-10")
    b = pick_for_day(pool, 6, "2026-09-11")
    assert a != b
    assert len(set(a) & set(b)) >= 5


# ── the disjointness guarantee, where it actually holds ──────────────────────


@pytest.mark.parametrize(
    "n, k",
    [(12, 6), (14, 6), (20, 6), (23, 6), (24, 6), (97, 6), (100, 6), (102, 6), (48, 8), (50, 8)],
)
def test_consecutive_days_are_disjoint_once_the_pool_is_at_least_2k(n, k):
    # Spans BOTH walks on purpose: 12-23 exercise the modular slice, 24+ the
    # seam-repaired cycle walk. n=23/24 straddle the boundary between them, which is
    # where a regime-selection off-by-one would hide.
    assert n >= 2 * k
    pool = _pool(n)
    days = _days("2020-01-01", 3000)          # spans many cycle boundaries
    previous = set(pick_for_day(pool, k, days[0]))
    for day in days[1:]:
        current = set(pick_for_day(pool, k, day))
        assert not (current & previous), (
            f"n={n} k={k} overlapped on {day}: {sorted(current & previous)}"
        )
        previous = current


# ── coverage ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("n, k", [(2, 1), (7, 6), (13, 4), (23, 6), (24, 6), (100, 6)])
def test_every_regime_is_a_pure_function_of_the_day(n, k):
    """No hidden lookback anywhere.

    This is the invariant that replaced a compare-with-yesterday nudge. That nudge had
    to compare against what was SERVED yesterday, which could itself have been nudged,
    and the chain had no bottom — it broke for real at n=2, k=1. Asking for day d
    out of order, or alone, must give the same answer as walking to it.
    """
    pool = _pool(n)
    walked = [pick_for_day(pool, k, d) for d in _days("2026-01-01", 60)]
    isolated = [pick_for_day(pool, k, d) for d in reversed(_days("2026-01-01", 60))]
    assert walked == list(reversed(isolated))


def test_a_full_cycle_serves_every_window_without_repeating():
    n, k = 100, 6
    per_cycle = n // k                        # 16 windows of 6 = 96 of the 100
    pool = _pool(n)
    # Must start ON a cycle boundary. Sixteen arbitrary consecutive days straddle two
    # cycles, where an item recurring is correct behaviour, not a coverage failure.
    start = next(
        d for d in _days("2026-01-01", 40) if day_index(d) % per_cycle == 0
    )
    seen: list[str] = []
    for day in _days(start, per_cycle):
        seen.extend(pick_for_day(pool, k, day))
    assert len(seen) == per_cycle * k
    assert len(set(seen)) == per_cycle * k, "an item was served twice inside one cycle"


def test_the_cycle_remainder_does_not_permanently_starve_any_question():
    """96 of 100 fit in a cycle; the reshuffle is what saves the other 4.

    Without a per-cycle reshuffle the same four questions would sit in the remainder
    forever and never be shown to anyone.
    """
    n, k = 100, 6
    pool = _pool(n)
    served = Counter()
    for day in _days("2026-01-01", (n // k) * 50):
        served.update(pick_for_day(pool, k, day))
    assert len(served) == n, f"never served: {sorted(set(pool) - set(served))}"


def test_no_result_ever_contains_a_duplicate():
    for n in (1, 2, 5, 6, 7, 13, 40, 100, 101):
        for k in (1, 2, 6, 8):
            for day in _days("2026-03-01", 40):
                got = pick_for_day(_pool(n), k, day)
                assert len(got) == len(set(got)), f"dupe at n={n} k={k} {day}"


# ── normalisation ────────────────────────────────────────────────────────────


def test_reordering_the_input_cannot_change_the_result():
    """The whole reason normalize_pool sorts.

    PostgREST does not guarantee row order without an ORDER BY, so two app instances
    can read the same table in different orders. If the schedule depended on position
    they would serve different chips on the same day — indistinguishable from a
    caching bug, and not one.
    """
    pool = _pool(100)
    shuffled = pool[37:] + pool[:37]
    for day in _days("2026-09-10", 20):
        assert pick_for_day(pool, 6, day) == pick_for_day(shuffled, 6, day)


def test_normalize_pool_collapses_case_and_whitespace_variants():
    got = normalize_pool(
        ["Should I buy?", "  Should I buy?  ", "SHOULD I BUY?", "Should  I  buy?", "", None, "  "]
    )
    assert got == ["Should I buy?"]


def test_normalize_pool_keeps_the_first_spelling_of_a_duplicate():
    assert normalize_pool(["What is a P/E?", "what is a p/e?"]) == ["What is a P/E?"]


def test_a_deduped_pool_shrinks_the_effective_n_and_still_rotates():
    pool = ["a?", "A?", "b?", "B?", "c?"]          # collapses to 3
    got = pick_for_day(pool, 6, "2026-09-10")
    assert sorted(got) == ["a?", "b?", "c?"]


# ── day_index ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["garbage", "", None, "2026-02-30", "10/09/2026", 42, []])
def test_day_index_never_raises(bad):
    assert day_index(bad) == 0


@pytest.mark.parametrize("iso", ["0001-01-01", "9999-12-31", "2026-09-10"])
def test_extreme_dates_produce_a_sane_pick(iso):
    got = pick_for_day(_pool(100), 6, iso)
    assert len(got) == 6
    assert len(set(got)) == 6


def test_day_index_is_monotonic_across_a_year_boundary():
    assert day_index("2027-01-01") == day_index("2026-12-31") + 1


# ── determinism ──────────────────────────────────────────────────────────────


def test_the_same_day_is_stable_across_repeated_calls():
    pool = _pool(100)
    first = pick_for_day(pool, 6, "2026-09-10")
    for _ in range(50):
        assert pick_for_day(pool, 6, "2026-09-10") == first


def test_golden_values_pin_the_shuffle_algorithm():
    """Three frozen outputs.

    The regression net for the module's central promise: same inputs, same answer,
    forever. If a refactor swaps SHA-256 for `random.Random` — or changes the seed
    string, the draw width, or the Fisher-Yates direction — this goes red instead of
    silently reshuffling every user's chips mid-day.

    Regenerate ONLY with a deliberate decision to reshuffle everyone.
    """
    pool = _pool(100)
    assert pick_for_day(pool, 6, "2026-09-10") == [
        "q0000?", "q0037?", "q0023?", "q0071?", "q0007?", "q0068?",
    ]
    assert pick_for_day(pool, 6, "2026-09-11") == [
        "q0044?", "q0066?", "q0098?", "q0093?", "q0014?", "q0030?",
    ]
    assert pick_for_day(pool, 4, "2026-09-10", salt="etf") == [
        "q0049?", "q0089?", "q0046?", "q0005?",
    ]


def test_the_salt_separates_two_rotations_that_share_a_date():
    pool = _pool(100)
    picks = {
        scope: tuple(pick_for_day(pool, 4, "2026-09-10", salt=scope))
        for scope in ("ticker", "etf", "crypto", "commodity", "index")
    }
    assert len(set(picks.values())) == len(picks), "two scopes sat at the same phase"


# ── uniformity ───────────────────────────────────────────────────────────────


def test_no_question_is_systematically_favoured_or_starved():
    """Every question is served about as often as every other.

    What this actually catches — verified by mutation — is a shuffle that does not
    reach the whole pool: making Fisher-Yates walk only half the range strands the
    other half in the never-served cycle remainder, and this goes red.

    What it does NOT catch, and an earlier version of this test wrongly claimed it
    did: modulo bias in the draw. With a 32-bit draw and a bound of 100 the bias is
    about one part in 40 million, which no feasible sample size can see. The rejection
    sampling in `_below` is defence in depth against a future narrower draw, not
    something a test can prove — so do not delete it on the strength of this passing.
    """
    n, k, days = 100, 6, 20_000
    served = Counter()
    pool = _pool(n)
    for day in _days("1900-01-01", days):
        served.update(pick_for_day(pool, k, day))
    expected = days * k / n
    worst = max(abs(count - expected) / expected for count in served.values())
    assert len(served) == n, f"never served: {sorted(set(pool) - set(served))}"
    assert worst < 0.05, f"frequency deviates {worst:.1%} from uniform"
