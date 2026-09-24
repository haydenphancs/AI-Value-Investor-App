"""Pure decision rules of the monthly Emerging Frontiers theme rotation.

Covers `rotation.plan_rotation` (buffer / strike / tenure / anchors / pin + block / forced
removals / pairing / change ceiling / refill / ranking), `scoring` (exposure, keyword
matching, segment share, pool context, the 55/20/15/10 score, eligibility floors) and
`reasons.user_reason` (+ the vocabulary scan of every user-facing line).

Everything here is pure: no network, no Supabase, no Gemini. Inputs are built inline with
the small `_cand` / `_sb` / `_plan` helpers below. A test named `test_regression_*` pins what this
file believes is a real defect in the source; it is EXPECTED to fail until the source is
fixed (see each docstring for the evidence).
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pytest

from app.services.theme_rotation import reasons
from app.services.theme_rotation.definitions import THEME_DEFINITIONS
from app.services.theme_rotation.models import (
    Action,
    Candidate,
    Decision,
    MemberHistory,
    Reason,
    RotationConfig,
    ScoreBreakdown,
    ThemeDefinition,
    ThemePlan,
)
from app.services.theme_rotation.reasons import ALL_USER_TEXT, user_reason
from app.services.theme_rotation.rotation import plan_rotation
from app.services.theme_rotation.scoring import (
    DESCRIPTION_MIN_HITS,
    FAST_TRACK_SESSIONS,
    MIN_PRICE,
    MIN_SESSION_COVERAGE,
    SEASONING_SESSIONS,
    W_ETF,
    W_EXPOSURE,
    W_MARKET,
    W_SIZE,
    PoolContext,
    _log_percentiles,
    build_pool_context,
    count_keyword_hits,
    exposure_of,
    floor_failure,
    is_us_listed,
    keyword_pattern,
    score_candidate,
    segment_theme_share,
)

NAN = float("nan")
INF = float("inf")

# ══════════════════════════════════════════════════════════════════════════════════════
# Builders
# ══════════════════════════════════════════════════════════════════════════════════════

_CFG = RotationConfig()
STRUCK = MemberHistory(struck_last_month=True)            # tenure None → seasoned
OFF = {"fit": "not_related", "etf_holders": 0}            # AI "off theme", no fund backing


def _names(k: int) -> List[str]:
    """k member tickers: three anchors (A1-A3, 100x the market cap) then M04, M05, ..."""
    pool = ["A1", "A2", "A3"] + [f"M{i:02d}" for i in range(4, 200)]
    return pool[:k]


def _outs(k: int, prefix: str = "O") -> List[str]:
    """k outsider tickers. Every outsider name starts with "O"."""
    return [f"{prefix}{i:02d}" for i in range(1, k + 1)]


def _cand(t: str, member: bool, **kw) -> Candidate:
    base = dict(ticker=t, name=t, is_member=member,
                market_cap=1e12 if t.startswith("A") else 1e10,
                price=50.0, exchange="NASDAQ", actively_trading=True,
                fit="core", fit_band="over_50", etf_holders=3, etf_max_weight=2.0)
    base.update(kw)
    return Candidate(**base)


def _sb(total: float, exposure: float = 0.8, source: str = "segments",
        etf_pts: float = 10.0) -> ScoreBreakdown:
    return ScoreBreakdown(total=total, exposure_pts=round(W_EXPOSURE * exposure, 2),
                          etf_pts=etf_pts, market_pts=7.5, size_pts=5.0,
                          exposure=exposure, exposure_source=source)


def _plan(order: Sequence[str], *, current: Optional[Sequence[object]] = None,
          cand: Optional[Mapping[str, dict]] = None, score: Optional[Mapping[str, dict]] = None,
          floors: Optional[Mapping[str, Optional[Reason]]] = None,
          history: Optional[Mapping[str, MemberHistory]] = None,
          pinned=(), blocked=(), cfg: RotationConfig = _CFG,
          missing_score: Sequence[str] = (), check: bool = True
          ) -> Tuple[ThemePlan, Dict[str, Decision]]:
    """`order` is best-first: position i scores 100 - i, so rank == position among the
    ranked pool. Tickers starting with "O" are outsiders, all others members (unless
    `current` is given explicitly)."""
    cand = cand or {}
    score = score or {}
    members = list(current) if current is not None else [t for t in order if not t.startswith("O")]
    member_set = {m.strip().upper() for m in members if isinstance(m, str)}
    candidates: Dict[str, Candidate] = {}
    scores: Dict[str, ScoreBreakdown] = {}
    for i, t in enumerate(order):
        candidates[t] = _cand(t, t in member_set, **cand.get(t, {}))
        if t in missing_score:
            continue
        s = {"total": 100.0 - i}
        s.update(score.get(t, {}))
        scores[t] = _sb(**s)
    plan = plan_rotation(slug="test-theme", current=members, candidates=candidates,
                         scores=scores, floors=dict(floors or {}), history=dict(history or {}),
                         pinned=set(pinned), blocked=set(blocked), cfg=cfg)
    if check:
        _assert_consistent(plan, candidates)
    return plan, {d.ticker: d for d in plan.decisions}


def _assert_consistent(plan: ThemePlan, candidates: Mapping[str, Candidate]) -> None:
    """Invariants every plan must satisfy, whatever the inputs."""
    tickers = [d.ticker for d in plan.decisions]
    assert len(tickers) == len(set(tickers)), "a ticker was decided twice"
    assert set(tickers) == set(plan.before) | set(candidates), "a candidate was not decided"
    assert len(plan.after) == len(set(plan.after)), "duplicate ticker published"
    by = {d.ticker: d for d in plan.decisions}
    before, after = set(plan.before), set(plan.after)
    for t in plan.after:
        if t in before:
            assert by[t].action in (Action.KEPT, Action.DEFERRED), (t, by[t])
        else:
            assert by[t].action in (Action.ADDED, Action.RETURNED), (t, by[t])
    for t in before - after:
        assert by[t].action is Action.REMOVED, (t, by[t])
    assert set(plan.removed) == before - after
    assert sorted(plan.added) == sorted(t for t, d in by.items() if d.action is Action.ADDED)
    assert sorted(plan.returned) == sorted(t for t, d in by.items() if d.action is Action.RETURNED)
    assert sorted(plan.deferred) == sorted(t for t, d in by.items() if d.action is Action.DEFERRED)
    for d in plan.decisions:
        assert d.was_member == (d.ticker in before)
        if d.action is Action.REJECTED:
            assert d.rank is None
    kept = [t for t in plan.before if t in after]
    assert plan.after[:len(kept)] == kept, "members must keep their published order"
    assert plan.change_count == max(len(plan.added) + len(plan.returned), len(plan.removed))


def _twelve_with_entrant(x_kw: Optional[dict] = None, x_hist: Optional[MemberHistory] = None,
                         o_hist: Optional[MemberHistory] = None, o_kw: Optional[dict] = None):
    """12 members; one eligible outsider O (rank 4, inside the entry zone); member X at
    rank 13 (inside the keep zone)."""
    order = _names(3) + ["O"] + _names(11)[3:] + ["X"]
    cand = {}
    if x_kw:
        cand["X"] = x_kw
    if o_kw:
        cand["O"] = o_kw
    history = {}
    if x_hist:
        history["X"] = x_hist
    if o_hist:
        history["O"] = o_hist
    return _plan(order, cand=cand, history=history)


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — sizes, ceiling and clamps
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n, cap", [(12, 3), (15, 4), (16, 4), (18, 5), (21, 6), (24, 7)])
def test_change_ceiling_is_floor_of_30_percent_and_binds(n, cap):
    """Every non-anchor member is off-theme on its second strike and there are plenty of
    entrants: exactly floor(0.30·N) swaps happen, the worst-ranked members leave first.
    Of the rest, those an entrant was waiting for are DEFERRED by the ceiling (with their
    entrant); those with no entrant at all are KEPT_FOR_SIZE — the ceiling did not stop
    them, the missing replacement did."""
    entry = math.floor(0.75 * n)
    names = _names(n)
    anchors, rest = names[:3], names[3:]
    outs = _outs(entry - 3)
    order = anchors + outs + rest
    plan, by = _plan(order, cand={t: OFF for t in rest}, history={t: STRUCK for t in rest})
    assert plan.change_cap == cap
    assert len(plan.removed) == cap
    assert set(plan.removed) == set(rest[-cap:])                  # worst-ranked first
    assert plan.added == outs[:cap]                              # best-ranked entrants
    assert plan.change_count == cap
    assert len(plan.after) == n
    stayed = rest[:-cap]                                         # best-ranked first
    n_deferred = min(len(stayed), len(outs) - cap)               # an entrant was waiting
    deferred, unpaired = stayed[len(stayed) - n_deferred:], stayed[:len(stayed) - n_deferred]
    for t in deferred:
        assert by[t].action is Action.DEFERRED and by[t].reason is Reason.CHANGE_CAP
        assert t in plan.after                                   # a deferred member stays
    for t in unpaired:
        assert by[t].action is Action.KEPT and by[t].reason is Reason.KEPT_FOR_SIZE
    for t in outs[cap:cap + n_deferred]:                         # its entrant waits too
        assert by[t].action is Action.DEFERRED and by[t].reason is Reason.CHANGE_CAP
    assert sorted(plan.deferred) == sorted(deferred + outs[cap:cap + n_deferred])
    for t in anchors:
        assert by[t].reason is Reason.ANCHOR


@pytest.mark.parametrize("n_members", [0, 1, 5, 11])
def test_short_list_clamps_n_to_12(n_members):
    order = _names(n_members) + _outs(20)
    plan, _ = _plan(order, check=False)
    assert plan.change_cap == 3                                  # floor(0.30 · 12)
    assert len(plan.after) == 12                                 # topped up to the minimum
    assert plan.shortfall is False


def test_short_list_of_ten_fills_two_seats_from_the_entry_zone_first():
    order = _outs(3) + _names(10)                                # O01-O03 rank 1-3
    plan, by = _plan(order)
    assert plan.added == ["O01", "O02"]
    assert all(by[t].reason is Reason.ENTERED_TOP_RANKS for t in ("O01", "O02"))
    assert by["O03"].action is Action.BENCH and by["O03"].reason is Reason.NO_OPEN_SLOT
    assert len(plan.after) == 12


def test_short_list_with_no_entrant_is_refilled_from_below_the_entry_zone():
    order = _names(10) + _outs(3)                                # outsiders rank 11-13
    plan, by = _plan(order)
    assert plan.added == ["O01", "O02"]
    assert all(by[t].reason is Reason.REFILL for t in ("O01", "O02"))
    assert by["O03"].reason is Reason.RANKED_BELOW_ENTRY
    assert plan.shortfall is False


def test_empty_list_and_empty_pool_is_a_shortfall():
    plan, _ = _plan([], current=[])
    assert plan.after == [] and plan.shortfall is True and plan.change_cap == 3


def test_long_list_clamps_n_to_24():
    """30 members → N=24: cap 7, keep zone ceil(1.25·24)=30."""
    names = _names(29)
    plan, by = _plan(names + ["X"])
    assert plan.change_cap == 7
    assert by["X"].rank == 30 and by["X"].strike is False
    plan, by = _plan(names + ["OB1", "X"])
    assert by["X"].rank == 31 and by["X"].strike is True
    assert by["X"].reason is Reason.FIRST_STRIKE


def test_no_minimum_number_of_changes():
    """Strong outsiders never displace healthy members: the ceiling is not a target."""
    order = _outs(6) + _names(12)
    plan, by = _plan(order)
    assert plan.after == plan.before and not plan.changed
    assert plan.change_count == 0
    for t in _outs(6):
        assert by[t].action is Action.BENCH and by[t].reason is Reason.NO_OPEN_SLOT


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — buffer zones, strikes, decisive rule, tenure
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n", [12, 15, 16, 24])
def test_keep_zone_boundary_exactly_at_and_one_past(n):
    keep = math.ceil(1.25 * n)
    others = _names(n - 1)
    plan, by = _plan(others + _outs(keep - n, "OB") + ["X"])
    assert by["X"].rank == keep
    assert by["X"].strike is False and by["X"].reason is Reason.STILL_ON_THEME
    plan, by = _plan(others + _outs(keep - n + 1, "OB") + ["X"])
    assert by["X"].rank == keep + 1
    assert by["X"].strike is True
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.FIRST_STRIKE


@pytest.mark.parametrize("n", [12, 15, 16, 24])
def test_entry_zone_boundary_exactly_at_and_one_past(n):
    entry, keep = math.floor(0.75 * n), math.ceil(1.25 * n)
    names = _names(n - 1)
    bench = _outs(keep - n, "OB")
    hist = {"X": STRUCK}
    # O exactly at the entry limit: it takes X's seat.
    plan, by = _plan(names[:entry - 1] + ["O"] + names[entry - 1:] + bench + ["X"], history=hist)
    assert by["O"].rank == entry and by["X"].rank == keep + 1
    assert by["O"].action is Action.ADDED and by["O"].reason is Reason.ENTERED_TOP_RANKS
    assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OUTRANKED
    assert len(plan.after) == n
    # One past the entry limit: no qualifying entrant, so X keeps its seat.
    plan, by = _plan(names[:entry] + ["O"] + names[entry:] + bench + ["X"], history=hist)
    assert by["O"].rank == entry + 1
    assert by["O"].action is Action.BENCH and by["O"].reason is Reason.RANKED_BELOW_ENTRY
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.KEPT_FOR_SIZE
    assert plan.after == plan.before


def test_second_consecutive_strike_removes_first_strike_keeps():
    order = _names(3) + ["O"] + _names(11)[3:] + _outs(3, "OB") + ["X"]      # X rank 16
    plan, by = _plan(order)
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.FIRST_STRIKE
    assert by["X"].strike is True
    plan, by = _plan(order, history={"X": STRUCK})
    assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OUTRANKED
    assert plan.added == ["O"]


def test_strike_last_month_but_not_this_month_resets():
    order = _names(3) + ["O"] + _names(11)[3:] + ["X"]                       # X rank 13
    plan, by = _plan(order, history={"X": STRUCK})
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.STILL_ON_THEME
    assert by["X"].strike is False


@pytest.mark.parametrize("n", [12, 16])
def test_decisive_rank_beyond_2n_removes_on_the_first_strike(n):
    names = _names(n - 1)
    at = names[:3] + ["O"] + names[3:] + _outs(n - 1, "OB") + ["X"]
    plan, by = _plan(at)
    assert by["X"].rank == 2 * n
    assert by["X"].reason is Reason.FIRST_STRIKE
    assert by["O"].action is Action.BENCH and by["O"].reason is Reason.NO_OPEN_SLOT
    past = names[:3] + ["O"] + names[3:] + _outs(n, "OB") + ["X"]
    plan, by = _plan(past)
    assert by["X"].rank == 2 * n + 1
    assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OUTRANKED
    assert by["O"].action is Action.ADDED


def test_decisive_rank_still_respects_tenure():
    names = _names(11)
    past = names[:3] + ["O"] + names[3:] + _outs(12, "OB") + ["X"]
    plan, by = _plan(past, history={"X": MemberHistory(tenure_months=1)})
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.TENURE_PROTECTED


def test_ai_only_off_theme_verdict_needs_two_strikes_even_with_an_entrant():
    plan, by = _twelve_with_entrant(x_kw=OFF)
    assert by["X"].strike is True
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.FIRST_STRIKE
    assert by["O"].action is Action.BENCH
    plan, by = _twelve_with_entrant(x_kw=OFF, x_hist=STRUCK)
    assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OFF_THEME
    assert plan.added == ["O"]


def test_off_theme_member_ranked_beyond_2n_is_removed_for_rank_not_verdict():
    names = _names(11)
    past = names[:3] + ["O"] + names[3:] + _outs(12, "OB") + ["X"]
    plan, by = _plan(past, cand={"X": OFF})
    assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OUTRANKED


@pytest.mark.parametrize("holders, consensus, struck", [
    (2, 2, False),        # default consensus: two seed ETFs shield it
    (5, 2, False),
    (1, 2, True),
    (0, 2, True),
    (2, 3, True),         # a stricter config lets the verdict through
])
def test_fund_consensus_shields_member_from_ai_only_strike(holders, consensus, struck):
    cfg = RotationConfig(fund_consensus=consensus)
    order = _names(3) + ["O"] + _names(11)[3:] + ["X"]
    plan, by = _plan(order, cand={"X": {"fit": "not_related", "etf_holders": holders}},
                     history={"X": STRUCK}, cfg=cfg)
    assert by["X"].strike is struck
    if struck:
        assert by["X"].action is Action.REMOVED and by["X"].reason is Reason.OFF_THEME
    else:
        assert by["X"].action is Action.KEPT and by["X"].reason is Reason.STILL_ON_THEME


@pytest.mark.parametrize("tenure, removed", [(0, False), (1, False), (2, True), (7, True),
                                             (None, True)])
def test_tenure_protects_new_members_and_none_counts_as_seasoned(tenure, removed):
    hist = MemberHistory(tenure_months=tenure, struck_last_month=True)
    plan, by = _twelve_with_entrant(x_kw=OFF, x_hist=hist)
    if removed:
        assert by["X"].action is Action.REMOVED
    else:
        assert by["X"].action is Action.KEPT and by["X"].reason is Reason.TENURE_PROTECTED
        assert by["X"].strike is True                            # the strike is still recorded


def test_member_without_history_is_seasoned_on_first_strike():
    plan, by = _twelve_with_entrant(x_kw=OFF)
    assert by["X"].reason is Reason.FIRST_STRIKE                 # not TENURE_PROTECTED


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — anchors, pins and blocks
# ══════════════════════════════════════════════════════════════════════════════════════

def test_anchors_are_top3_by_exposure_times_cap_and_never_rotated_naturally():
    generic = [f"G{i:02d}" for i in range(1, 9)]
    specials = ["MID3", "MID2", "MID1", "BIG"]                   # worst four ranks
    order = generic[:6] + _outs(3) + generic[6:] + specials
    cand = {
        "BIG": {"market_cap": 1e13, **OFF},    # exposure 0.1 → 1.0e12
        "MID1": {"market_cap": 2e12, **OFF},   # exposure 0.8 → 1.6e12
        "MID2": {"market_cap": 1e12, **OFF},   # exposure 0.9 → 0.9e12
        "MID3": {"market_cap": 1e12, **OFF},   # exposure 0.8 → 0.8e12  (4th: not an anchor)
    }
    score = {"BIG": {"total": 1.0, "exposure": 0.1}, "MID2": {"total": 3.0, "exposure": 0.9}}
    hist = {t: STRUCK for t in specials}
    plan, by = _plan(order, cand=cand, score=score, history=hist)
    for t in ("BIG", "MID1", "MID2"):
        assert by[t].action is Action.KEPT and by[t].reason is Reason.ANCHOR
        assert by[t].strike is True
    assert by["MID3"].action is Action.REMOVED


def test_anchor_ranked_beyond_2n_is_still_kept():
    names = _names(11)
    order = names[1:3] + ["O"] + names[3:] + _outs(13, "OB") + ["A1"]
    plan, by = _plan(order, cand={"A1": OFF}, history={"A1": STRUCK})
    assert by["A1"].rank > 24
    assert by["A1"].action is Action.KEPT and by["A1"].reason is Reason.ANCHOR


def test_forced_out_anchor_hands_the_anchor_role_to_the_next_survivor():
    names = _names(12)
    order = names[:1] + names[1:3] + ["O"] + names[4:] + ["M04"]    # M04 ranked last
    plan, by = _plan(order, floors={"A1": Reason.DELISTED}, cand={"M04": OFF},
                     history={"M04": STRUCK})
    assert by["A1"].action is Action.REMOVED and by["A1"].reason is Reason.DELISTED
    # Equal exposure x cap among the M-names: the tie-break is the ticker, so M04 anchors.
    assert by["M04"].action is Action.KEPT and by["M04"].reason is Reason.ANCHOR


def test_zero_anchors_config_disables_the_anchor_rule():
    plan, by = _twelve_with_entrant(x_kw=OFF, x_hist=STRUCK)
    assert any(d.reason is Reason.ANCHOR for d in plan.decisions)
    order = _names(3) + ["O"] + _names(11)[3:] + ["A9"]
    plan, by = _plan(order, cand={"A9": {"market_cap": 1e14, **OFF}}, history={"A9": STRUCK},
                     cfg=RotationConfig(anchors=0))
    assert not any(d.reason is Reason.ANCHOR for d in plan.decisions)
    assert by["A9"].action is Action.REMOVED


def test_pinned_member_survives_floors_but_not_delisting_or_block():
    members = _names(12)
    floors = {"M10": Reason.BELOW_FLOORS, "M11": Reason.DELISTED}
    plan, by = _plan(members + _outs(3), floors=floors,
                     pinned={"m10", "M11", "M12"}, blocked={"m12"})
    assert by["M10"].action is Action.KEPT and by["M10"].reason is Reason.PINNED
    assert by["M11"].action is Action.REMOVED and by["M11"].reason is Reason.DELISTED
    assert by["M12"].action is Action.REMOVED and by["M12"].reason is Reason.BLOCKED


def test_pinned_member_is_never_rotated_out_naturally():
    names = _names(11)
    past = names[:3] + ["O"] + names[3:] + _outs(12, "OB") + ["X"]
    plan, by = _plan(past, cand={"X": OFF}, history={"X": STRUCK}, pinned={"X"})
    assert by["X"].action is Action.KEPT and by["X"].reason is Reason.PINNED
    assert by["X"].strike is True


def test_unpinned_member_below_floors_is_forced_out():
    plan, by = _plan(_names(12) + _outs(3), floors={"M05": Reason.BELOW_FLOORS})
    assert by["M05"].action is Action.REMOVED and by["M05"].reason is Reason.BELOW_FLOORS


def test_blocked_outsider_is_rejected_even_when_pinned_and_top_ranked():
    order = ["OTOP"] + _names(11)[:3] + ["O"] + _names(11)[3:] + ["X"]
    plan, by = _plan(order, blocked={"otop"}, pinned={"OTOP"}, history={"X": STRUCK},
                     cand={"X": OFF})
    assert by["OTOP"].action is Action.REJECTED and by["OTOP"].reason is Reason.BLOCKED
    assert "OTOP" not in plan.after
    assert plan.added == ["O"]


def test_non_forced_floor_value_on_a_member_does_not_remove_it():
    """Only DELISTED / BELOW_FLOORS are forced; a stray newcomer reason is not."""
    plan, by = _plan(_names(12), floors={"M05": Reason.FAILS_FLOORS, "M06": Reason.NO_DATA})
    assert by["M05"].action is Action.KEPT and by["M06"].action is Action.KEPT


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — forced removals, refill, shortfall
# ══════════════════════════════════════════════════════════════════════════════════════

def test_forced_removals_ignore_the_cap_and_are_refilled_to_min_size():
    members = _names(12)
    forced = members[7:]                                         # M08-M12, five of them
    plan, by = _plan(members + _outs(6), floors={t: Reason.DELISTED for t in forced})
    assert plan.change_cap == 3
    assert plan.removed == forced
    assert plan.change_count == 5                                # beyond the ceiling
    assert by["O01"].reason is Reason.ENTERED_TOP_RANKS          # rank 8, in the zone
    assert by["O02"].reason is Reason.ENTERED_TOP_RANKS          # rank 9
    for t in ("O03", "O04", "O05"):
        assert by[t].action is Action.ADDED and by[t].reason is Reason.REFILL
    assert by["O06"].action is Action.BENCH
    assert len(plan.after) == 12 and plan.shortfall is False


def test_forced_removal_with_dry_pool_publishes_short_with_shortfall():
    ineligible = ["OX1", "OX2", "OX3", "OX4"]
    members = _names(12)
    order = ineligible + members + ["O01"]
    plan, by = _plan(
        order,
        floors={**{t: Reason.DELISTED for t in members[7:]}, "OX3": Reason.FAILS_FLOORS},
        cand={"OX1": {"fit": None}, "OX2": {"fit": "not_related"},
              "OX4": {"fit": "adjacent", "etf_holders": 0}},
    )
    assert plan.after == members[:7] + ["O01"]
    assert plan.shortfall is True
    for t in ineligible:
        assert by[t].action is Action.REJECTED and t not in plan.after


def test_refilled_returning_stock_is_reported_as_returned_with_refill_reason():
    members = _names(12)
    plan, by = _plan(members + _outs(6), floors={t: Reason.DELISTED for t in members[7:]},
                     history={"O04": MemberHistory(removed_recently=True)})
    assert by["O04"].action is Action.RETURNED and by["O04"].reason is Reason.REFILL
    assert "O04" in plan.returned and "O04" not in plan.added


def test_forced_and_natural_removals_share_the_ceiling():
    members = _names(12)                                         # A1-A3, M04-M12
    order = members[:3] + _outs(6) + members[3:]
    floors = {"M11": Reason.BELOW_FLOORS, "M12": Reason.BELOW_FLOORS}
    natural = ["M08", "M09", "M10"]
    plan, by = _plan(order, floors=floors, cand={t: OFF for t in natural},
                     history={t: STRUCK for t in natural})
    assert plan.removed == ["M11", "M12", "M10"]                 # 2 forced + worst natural
    assert by["M10"].reason is Reason.OUTRANKED                  # rank 16 > keep 15
    for t in ("M08", "M09"):
        assert by[t].action is Action.DEFERRED and by[t].reason is Reason.CHANGE_CAP
    assert plan.added == ["O01", "O02", "O03"]
    assert plan.change_count == plan.change_cap == 3


def test_forced_removals_above_the_cap_defer_every_natural_removal():
    members = _names(12)
    order = members[:3] + _outs(6) + members[3:]
    floors = {t: Reason.BELOW_FLOORS for t in ("M09", "M10", "M11", "M12")}
    natural = ["M07", "M08"]
    plan, by = _plan(order, floors=floors, cand={t: OFF for t in natural},
                     history={t: STRUCK for t in natural})
    assert plan.removed == ["M09", "M10", "M11", "M12"]
    assert all(by[t].action is Action.DEFERRED for t in natural)
    assert plan.added == ["O01", "O02", "O03", "O04"]
    assert plan.change_count == 4


def test_forced_seat_above_the_minimum_is_filled_only_from_the_entry_zone():
    """Pinned current behaviour (rotation.py rules 5 + 7): with the list still >= 12 after
    a delisting, a seat is refilled only by an outsider ranked inside the entry zone — an
    eligible outsider below it is not pulled in just to hold the size."""
    members = _names(16)
    plan, by = _plan(members + _outs(4, "OB"), floors={"M16": Reason.DELISTED})
    assert plan.removed == ["M16"]
    assert len(plan.after) == 15 and plan.shortfall is False
    assert all(by[t].reason is Reason.RANKED_BELOW_ENTRY for t in _outs(4, "OB"))


def test_member_missing_all_data_is_never_forced_out():
    order = _names(12) + ["ZZ"]
    plan, by = _plan(order, missing_score=["ZZ"])
    assert by["ZZ"].action is Action.KEPT and by["ZZ"].score is None
    assert "ZZ" in plan.after


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — pairing and the natural-removal ceiling
# ══════════════════════════════════════════════════════════════════════════════════════

def test_natural_removal_without_entrant_is_kept_for_size():
    members = _names(12)
    struck = members[-2:]
    plan, by = _plan(members + _outs(3), cand={t: OFF for t in struck},
                     history={t: STRUCK for t in struck})
    for t in struck:
        assert by[t].action is Action.KEPT and by[t].reason is Reason.KEPT_FOR_SIZE
    assert plan.after == plan.before


def test_natural_removals_are_paired_one_for_one_with_entrants():
    members = _names(12)
    struck = members[-3:]                                        # M10, M11, M12
    order = members[:3] + ["O01"] + members[3:]
    plan, by = _plan(order, cand={t: OFF for t in struck}, history={t: STRUCK for t in struck})
    assert plan.removed == ["M12"]                               # worst-ranked struck member
    assert plan.added == ["O01"]
    assert by["M10"].reason is Reason.KEPT_FOR_SIZE
    assert by["M11"].reason is Reason.KEPT_FOR_SIZE
    assert len(plan.after) == 12


def test_worst_ranked_natural_removal_goes_first_under_the_cap():
    members = _names(12)
    struck = members[-5:]                                        # M08..M12
    order = members[:3] + _outs(5) + members[3:]
    plan, by = _plan(order, cand={t: OFF for t in struck}, history={t: STRUCK for t in struck})
    ranks = sorted(struck, key=lambda t: by[t].rank)
    assert sorted(plan.removed) == sorted(ranks[-3:])
    assert all(by[t].action is Action.DEFERRED for t in ranks[:2])


def test_regression_natural_removals_reported_as_cap_deferred_when_cap_unused():
    """REGRESSION (fixed 2026-09-23). Was: rotation.py:208-212 — a struck member that stays because NO entrant exists is
    classified KEPT_FOR_SIZE only while `i < natural_room`; the rest are recorded as
    DEFERRED / CHANGE_CAP ("the ceiling was reached") although zero changes happened and
    the ceiling (3) was never touched. Input: 12 members, five on a second off-theme
    strike, no outsiders. Expected: all five KEPT_FOR_SIZE, plan.deferred == [].
    Actual: two of them DEFERRED with reason change_cap, plan.deferred == [M09, M08]."""
    members = _names(12)
    struck = members[-5:]
    plan, by = _plan(members, cand={t: OFF for t in struck}, history={t: STRUCK for t in struck})
    assert plan.change_count == 0
    assert plan.deferred == []
    assert all(by[t].reason is Reason.KEPT_FOR_SIZE for t in struck), \
        {t: by[t].reason.value for t in struck}


def test_regression_entrants_blocked_by_the_ceiling_are_benched_not_deferred():
    """REGRESSION (fixed 2026-09-23). Was: rotation.py:217-218 — docstring rule 6: "Extra natural removals and entrants are
    DEFERRED to next month." When the ceiling defers natural removals, the entrants that
    would have taken those seats are recorded BENCH / NO_OPEN_SLOT and left out of
    `plan.deferred`: `deferred_entrants` only looks at `open_slots > add_room`, and
    `open_slots` already excludes the cap-deferred removals. Input: N=12, nine struck
    members, six entrants (ranks 4-9). Expected: the three unplaced entrants DEFERRED /
    change_cap. Actual: BENCH / no_open_slot."""
    names = _names(12)
    rest = names[3:]
    outs = _outs(6)
    plan, by = _plan(names[:3] + outs + rest, cand={t: OFF for t in rest},
                     history={t: STRUCK for t in rest})
    assert plan.added == outs[:3]
    unplaced = outs[3:]
    assert {by[t].action for t in unplaced} == {Action.DEFERRED}, \
        {t: (by[t].action.value, by[t].reason.value) for t in unplaced}
    assert all(by[t].reason is Reason.CHANGE_CAP for t in unplaced)
    assert set(unplaced) <= set(plan.deferred)


def test_regression_short_list_refill_is_recorded_as_deferred():
    """REGRESSION (fixed 2026-09-23). Was: rotation.py:218 + 264-270 — with a list below 12, `deferred_entrants` is
    entrants[add_room:open_slots]; step 7 then refills exactly those tickers (the list is
    still short), and the later `for t in deferred_entrants` loop OVERWRITES their
    ADDED/REFILL decision with DEFERRED/CHANGE_CAP. Input: 8 members, six outsiders in the
    entry zone. Actual: O04 is published in `after` and in `plan.added`, yet its decision
    says DEFERRED and it is also in `plan.deferred`. Consequences: the read model drops it
    from "What changed" (it only reads kept/added/returned/removed), and `build_history`
    treats a non-member DEFERRED row as "not a member", so next month its tenure starts
    from `kept` → None → seasoned, bypassing tenure protection."""
    order = _names(3) + _outs(6) + _names(8)[3:] + _outs(4, "OB")
    plan, by = _plan(order, check=False)
    assert "O04" in plan.after                                   # it IS published
    assert by["O04"].action in (Action.ADDED, Action.RETURNED), by["O04"]
    assert "O04" not in plan.deferred
    new = set(plan.after) - set(plan.before)
    assert not new & set(plan.deferred)


# ══════════════════════════════════════════════════════════════════════════════════════
# plan_rotation — outsider eligibility, returns, ranking
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kw, floor, expected", [
    ({"fit": None}, None, Reason.FIT_UNVERIFIED),
    ({"fit": "maybe"}, None, Reason.FIT_UNVERIFIED),
    ({"fit": "CORE"}, None, Reason.FIT_UNVERIFIED),              # verdicts are exact values
    ({"fit": "not_related"}, None, Reason.NOT_ON_THEME),
    ({"fit": "adjacent", "etf_holders": 0}, None, Reason.WEAK_EVIDENCE),
    ({"fit": "not_related"}, Reason.IPO_SEASONING, Reason.IPO_SEASONING),   # floor first
    ({}, Reason.NOT_US_LISTED, Reason.NOT_US_LISTED),
    ({}, Reason.FAILS_FLOORS, Reason.FAILS_FLOORS),
    ({}, Reason.NO_DATA, Reason.NO_DATA),
    ({}, Reason.DELISTED, Reason.DELISTED),
])
def test_ineligible_outsider_is_rejected_with_its_reason(kw, floor, expected):
    order = ["OT"] + _names(3) + ["O"] + _names(11)[3:] + ["X"]
    floors = {"OT": floor} if floor else {}
    plan, by = _plan(order, cand={"OT": kw, "X": OFF}, floors=floors, history={"X": STRUCK})
    assert by["OT"].action is Action.REJECTED and by["OT"].reason is expected
    assert by["OT"].rank is None
    assert by["A1"].rank == 1                                    # rejected: not in the ranking
    assert plan.added == ["O"]                                   # the seat goes to O


@pytest.mark.parametrize("exposure, source, holders, eligible", [
    (0.5, "industry", 1, True),
    (0.8, "segments", 2, True),
    (0.6, "description", 1, True),
    (0.49, "segments", 3, False),
    (0.5, "industry", 0, False),
    (0.5, "unknown", 3, False),
    (1.0, "segments", 0, False),
])
def test_adjacent_outsider_needs_data_backing(exposure, source, holders, eligible):
    order = _names(3) + ["O"] + _names(11)[3:] + ["X"]
    plan, by = _plan(order, cand={"O": {"fit": "adjacent", "etf_holders": holders}, "X": OFF},
                     score={"O": {"exposure": exposure, "source": source}},
                     history={"X": STRUCK})
    if eligible:
        assert by["O"].action is Action.ADDED
    else:
        assert by["O"].action is Action.REJECTED and by["O"].reason is Reason.WEAK_EVIDENCE


def test_outsider_without_a_score_is_rejected_no_data():
    order = ["OT"] + _names(12)
    plan, by = _plan(order, missing_score=["OT"])
    assert by["OT"].action is Action.REJECTED and by["OT"].reason is Reason.NO_DATA


def test_returning_stock_is_reported_as_returned():
    plan, by = _twelve_with_entrant(x_kw=OFF, x_hist=STRUCK,
                                    o_hist=MemberHistory(removed_recently=True, flips_6m=1))
    assert by["O"].action is Action.RETURNED and by["O"].reason is Reason.RETURNED_TOP_RANKS
    assert plan.returned == ["O"] and plan.added == []
    assert plan.change_count == 1


def test_pingpong_penalty_lowers_an_outsiders_effective_rank():
    order = _names(12) + ["OP", "OQ"]
    score = {"OP": {"total": 50.0}, "OQ": {"total": 47.0}}
    plan, by = _plan(order, score=score, history={"OP": MemberHistory(flips_6m=3)})
    assert (by["OQ"].rank, by["OP"].rank) == (13, 14)
    assert by["OP"].score == 50.0                                # the raw score is recorded
    plan, by = _plan(order, score=score, history={"OP": MemberHistory(flips_6m=2)})
    assert (by["OP"].rank, by["OQ"].rank) == (13, 14)


def test_pingpong_penalty_can_push_an_outsider_out_of_the_entry_zone():
    order = _names(3) + ["OP"] + _names(11)[3:] + ["X"]
    score = {"OP": {"total": 95.5}, "M04": {"total": 96.0}}     # OP rank 5 unpenalised
    plan, by = _plan(order, score=score, cand={"X": OFF}, history={"X": STRUCK})
    assert by["OP"].rank == 5 and by["OP"].action is Action.ADDED          # control
    hist = {"X": STRUCK, "OP": MemberHistory(flips_6m=4)}
    plan, by = _plan(order, score=score, cand={"X": OFF}, history=hist)
    assert by["OP"].rank == 10                                   # 90.5 falls below M09 (91)
    assert by["OP"].action is Action.BENCH and by["OP"].reason is Reason.RANKED_BELOW_ENTRY
    assert by["X"].reason is Reason.KEPT_FOR_SIZE


def test_pingpong_penalty_never_applies_to_members():
    order = _names(12)
    plan_a, by_a = _plan(order)
    plan_b, by_b = _plan(order, history={"M05": MemberHistory(flips_6m=9)})
    assert by_a["M05"].rank == by_b["M05"].rank == 5


def test_rank_tie_breaks_score_then_market_cap_then_ticker():
    tied = ["OZ", "OA", "OB", "OC", "OD"]
    order = _names(12) + tied
    score = {t: {"total": 10.0} for t in tied}
    cand = {"OZ": {"market_cap": 9e9}, "OA": {"market_cap": 1e9}, "OB": {"market_cap": 9e9},
            "OC": {"market_cap": None}, "OD": {"market_cap": NAN}}
    plan, by = _plan(order, cand=cand, score=score)
    assert sorted(tied, key=lambda t: by[t].rank) == ["OB", "OZ", "OA", "OC", "OD"]
    assert sorted(by[t].rank for t in tied) == [13, 14, 15, 16, 17]


def test_current_list_is_normalised_uppercase_deduped_and_cleaned():
    members = _names(12)
    current: List[object] = [m.lower() for m in members] + ["A1", "a1", None, "", "   ", 42]
    plan, by = _plan(members, current=current)
    assert plan.before == members
    assert plan.after == members


def _mixed_inputs(seed: Optional[int] = None) -> dict:
    """A month with every kind of decision in it."""
    members = _names(16)
    outs = _outs(8)
    odd = ["OR1", "OR2", "OR3", "OR4", "OR5", "OR6", "OR7"]
    order = members[:3] + outs[:5] + ["OR7"] + members[3:] + outs[5:] + odd[:6]
    cand_kw = {"M12": OFF, "M11": OFF, "M10": OFF, "OR1": {"fit": None},
               "OR2": {"fit": "not_related"}, "OR3": {"fit": "adjacent", "etf_holders": 0}}
    candidates = {t: _cand(t, t in members, **cand_kw.get(t, {})) for t in order}
    scores = {t: _sb(100.0 - i) for i, t in enumerate(order) if t != "OR6"}
    floors = {"M16": Reason.DELISTED, "M15": Reason.BELOW_FLOORS, "M14": Reason.BELOW_FLOORS,
              "OR4": Reason.IPO_SEASONING}
    history = {"M12": STRUCK, "M11": STRUCK,
               "M10": MemberHistory(tenure_months=1, struck_last_month=True),
               "OR7": MemberHistory(removed_recently=True, flips_6m=3),
               "O03": MemberHistory(flips_6m=5)}
    kwargs = dict(slug="mixed", current=list(members), candidates=candidates, scores=scores,
                  floors=floors, history=history, pinned={"M14"}, blocked={"M13", "OR5"})
    if seed is not None:
        rng = random.Random(seed)
        for key in ("candidates", "scores", "floors", "history"):
            items = list(kwargs[key].items())
            rng.shuffle(items)
            kwargs[key] = dict(items)
    return kwargs


def test_mixed_month_is_consistent_and_every_shown_change_has_user_text():
    kw = _mixed_inputs()
    plan = plan_rotation(**kw)
    _assert_consistent(plan, kw["candidates"])
    by = {d.ticker: d for d in plan.decisions}
    assert by["M16"].reason is Reason.DELISTED
    assert by["M15"].reason is Reason.BELOW_FLOORS
    assert by["M14"].reason is Reason.PINNED
    assert by["M13"].reason is Reason.BLOCKED
    assert by["M10"].reason is Reason.TENURE_PROTECTED
    assert by["OR5"].reason is Reason.BLOCKED
    assert by["OR6"].reason is Reason.NO_DATA
    for d in plan.decisions:
        text = user_reason(d.action, d.reason, d.score_parts)
        if d.action in (Action.ADDED, Action.RETURNED, Action.REMOVED):
            assert isinstance(text, str) and text in ALL_USER_TEXT, d
        else:
            assert text is None


def test_same_inputs_give_identical_plans_regardless_of_dict_order():
    first = plan_rotation(**_mixed_inputs())
    again = plan_rotation(**_mixed_inputs())
    assert first == again
    for seed in (1, 2, 3, 99):
        assert plan_rotation(**_mixed_inputs(seed)) == first


def test_decisions_are_sorted_by_action_then_rank_then_ticker():
    plan = plan_rotation(**_mixed_inputs())
    order = {Action.REMOVED: 0, Action.ADDED: 1, Action.RETURNED: 2, Action.KEPT: 3,
             Action.DEFERRED: 4, Action.BENCH: 5, Action.REJECTED: 6}
    keys = [(order[d.action], d.rank or 10**9, d.ticker) for d in plan.decisions]
    assert keys == sorted(keys)


def test_decision_score_parts_carry_fit_for_the_user_text():
    plan, by = _twelve_with_entrant(x_kw=OFF, x_hist=STRUCK,
                                    o_kw={"fit": "adjacent", "fit_band": "25_50"})
    parts = by["O"].score_parts
    assert parts["fit"] == "adjacent" and parts["fit_band"] == "25_50"
    assert parts["etf_holders"] == 3 and parts["exposure_source"] == "segments"
    assert user_reason(by["O"].action, by["O"].reason, parts) == reasons._ADDED_ADJACENT


@pytest.mark.parametrize("added, returned, removed, expected", [
    (0, 0, 0, 0), (2, 1, 2, 3), (1, 0, 4, 4), (3, 0, 3, 3), (0, 2, 0, 2),
])
def test_theme_plan_change_count_is_max_of_ins_and_outs(added, returned, removed, expected):
    plan = ThemePlan(slug="s", before=[], after=[], decisions=[],
                     added=[f"a{i}" for i in range(added)],
                     returned=[f"r{i}" for i in range(returned)],
                     removed=[f"x{i}" for i in range(removed)],
                     deferred=[], shortfall=False, change_cap=3)
    assert plan.change_count == expected


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — keyword matching
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("keyword, text, hit", [
    ("space", "A space company", True),
    ("space", "SPACE", True),
    ("space", "spaces", True),
    ("space", "An aerospace supplier", False),
    ("space", "AEROSPACE", False),
    ("space", "space-based sensors", True),
    ("gene", "general industrial", False),
    ("gene", "genes and proteins", True),
    ("gene", "a gene", True),
    ("security", "securities brokerage", False),
    ("security", "network security", True),
    ("security", "cybersecurity", False),
    ("robot", "industrial robots", True),
    ("robot", "robotic arms", False),
    ("satellite", "satellites in orbit", True),
    ("chip", "chipset", False),
    ("chip", "chips", True),
    ("launch", "launched", True),
    ("launch", "launching", True),
    ("memory", "memories", False),                              # "ies" is not an ending
    ("glp-1", "GLP-1 agonists", True),
    ("glp-1", "glp-1s", True),
    ("glp-1", "GLP-10 program", False),
    ("glp-1", "GLP 1", False),
    ("gene therapies", "gene therapies", True),
    ("gene therapies", "Gene-therapies", True),
    ("gene therapies", "gene   therapies", True),
    ("gene therapies", "gene\ntherapies", True),
    ("gene therapies", "genetherapies", False),
    ("gene therapy", "gene therapies", False),                   # why both are listed
    ("data center", "data-centers", True),
    ("data center", "datacenter", False),
    ("SPACE", "space", True),                                    # keyword case folded
])
def test_keyword_pattern_whole_word_with_simple_endings(keyword, text, hit):
    assert bool(keyword_pattern(keyword).search(text)) is hit


@pytest.mark.parametrize("text", [None, "", 123, ["chip"], {"chip": 1}, b"chip"])
def test_count_keyword_hits_non_text_is_zero(text):
    assert count_keyword_hits(text, ["chip"]) == 0


def test_count_keyword_hits_counts_distinct_keywords_once():
    assert count_keyword_hits("chip chip chip", ["chip"]) == 1
    assert count_keyword_hits("chip", ["chip", "chip"]) == 1
    assert count_keyword_hits("chips, wafers and a foundry", ["chip", "wafer", "foundry", "gpu"]) == 3
    assert count_keyword_hits("anything", ["", "anything"]) == 1           # blank keyword skipped
    assert count_keyword_hits("chip wafer", (k for k in ["chip", "wafer"])) == 2   # any iterable
    assert count_keyword_hits("chip", []) == 0


def test_definition_keywords_are_clean_lowercase_non_blank():
    """A blank/whitespace keyword compiles to a pattern that matches empty boundaries and
    would count as a hit in almost any text — keep the definitions free of them."""
    assert THEME_DEFINITIONS
    for slug, d in THEME_DEFINITIONS.items():
        assert d.slug == slug and d.seed_etfs and d.industries
        for kw in d.segment_keywords + d.description_keywords:
            assert isinstance(kw, str) and kw.strip() and kw == kw.strip().lower(), (slug, kw)


def test_definition_doc_examples_hold_on_the_real_definitions():
    ff = THEME_DEFINITIONS["final-frontier"]
    assert count_keyword_hits("A leading aerospace components supplier.",
                              ff.description_keywords) == 0
    cyber = THEME_DEFINITIONS["cyber-wars"]
    assert segment_theme_share({"Securities Brokerage": 100.0}, cyber.segment_keywords) == 0.0
    assert segment_theme_share({"Network Security": 60.0, "Other": 40.0},
                               cyber.segment_keywords) == pytest.approx(0.6)


@pytest.mark.parametrize("slug, text", [
    ("robot-workforce", "The company sells robotics."),
    ("hacking-health", "A genomics company."),
    ("silicon-rush", "A maker of photonics."),
])
def test_regression_one_word_counts_as_two_distinct_description_keywords(slug, text):
    """REGRESSION (fixed 2026-09-23). Was: scoring.py:60-67 + definitions.py — the gate "≥2 DISTINCT keyword hits" exists so a
    company that merely mentions one buzzword earns no description credit. But the
    definitions list both forms of a word ("robotic"/"robotics", "genomic"/"genomics",
    "photonic"/"photonics") and `keyword_pattern` lets "robotic" match "robotics" via its
    plural "s" ending, so ONE word counts as TWO distinct keywords. Expected: 1 hit (below
    the gate). Actual: 2 → 0.4 exposure from a single word. (Same family: "optical
    interconnect" alone counts 3 in silicon-rush — "optical", "interconnect" and the
    phrase — reaching the 0.6 description cap.)"""
    defn = THEME_DEFINITIONS[slug]
    assert count_keyword_hits(text, defn.description_keywords) < DESCRIPTION_MIN_HITS


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — segment share
# ══════════════════════════════════════════════════════════════════════════════════════

_SEG_KW = ("robot", "automation")


@pytest.mark.parametrize("segments, expected", [
    (None, None),
    ({}, None),
    ([("Robots", 1.0)], None),                                  # not a dict
    ("Robots", None),
    ({"Robots": 0.0, "Other": 0.0}, None),                     # zero total → unknown
    ({"Robots": -5.0, "Other": -1.0}, None),                   # no positive revenue
    ({"Robots": NAN, "Other": "n/a"}, None),
    ({"Other": 100.0}, 0.0),                                   # present but unmatched → known 0
    ({"Robots": 60.0, "Other": 40.0}, 0.6),
    ({"Industrial Automation": 25.0, "Robots": 25.0, "Other": 50.0}, 0.5),
    ({"Robots": 50.0, "Eliminations": -100.0}, 1.0),           # negative ignored
    ({"Robots": 50.0, "Other": NAN}, 1.0),
    ({"Robots": 50.0, "Other": INF}, 1.0),
    ({"Robots": 50.0, "Other": "abc"}, 1.0),
    ({"Robots": 50.0, "Other": None}, 1.0),
    ({"Robots": 50.0, "Other": True}, 1.0),                    # a bool is not a number
    ({"Robots": "30", "Other": 70}, 0.3),                      # numeric strings count
    ({7: 50.0, "Robots": 50.0}, 0.5),                          # non-str name: total only
    ({"Robotic Systems": 10.0}, 0.0),                          # "robotic" ≠ "robot"
])
def test_segment_theme_share(segments, expected):
    got = segment_theme_share(segments, _SEG_KW)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)
        assert 0.0 <= got <= 1.0


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — exposure
# ══════════════════════════════════════════════════════════════════════════════════════

_DEF = ThemeDefinition(slug="t", label="test theme", seed_etfs=("E1", "E2", "E3"),
                       industries=frozenset({"Semiconductors"}),
                       segment_keywords=("chip", "data center"),
                       description_keywords=("chip", "wafer", "foundry", "gpu"))
_DEF_SMALL = replace(_DEF, small_cap=True)


def _c(member: bool = False, **kw) -> Candidate:
    return Candidate(ticker="T", is_member=member, **kw)


@pytest.mark.parametrize("member, kw, expected", [
    (True, {}, (0.5, "unknown")),
    (False, {}, (0.0, "unknown")),
    (True, {"segment_share": 0.0}, (0.0, "segments")),
    (False, {"segment_share": 0.0}, (0.0, "segments")),
    (True, {"segment_share": 0.0, "industry": "Semiconductors"}, (0.5, "industry")),
    (False, {"industry": "Semiconductors"}, (0.5, "industry")),
    (False, {"industry": "semiconductors"}, (0.0, "unknown")),     # exact FMP string only
    (True, {"industry": None}, (0.5, "unknown")),
    (False, {"segment_share": 0.7, "industry": "Semiconductors"}, (0.7, "segments")),
    (False, {"segment_share": 0.3, "industry": "Semiconductors"}, (0.5, "industry")),
    (False, {"segment_share": 1.7}, (1.0, "segments")),
    (False, {"keyword_hits": 1}, (0.0, "unknown")),
    (False, {"keyword_hits": 2}, (0.4, "description")),
    (False, {"keyword_hits": 3}, (0.6, "description")),
    (False, {"keyword_hits": 9}, (0.6, "description")),            # capped
    (False, {"keyword_hits": -3}, (0.0, "unknown")),
    (False, {"keyword_hits": 2, "fit": "core", "fit_band": "over_50"}, (0.8, "description")),
    (False, {"keyword_hits": 2, "fit": "core", "fit_band": "pre_revenue"}, (0.8, "description")),
    (False, {"keyword_hits": 1, "fit": "core", "fit_band": "over_50"}, (0.0, "unknown")),
    (True, {"keyword_hits": 1, "fit": "core", "fit_band": "over_50"}, (0.5, "unknown")),
    (False, {"keyword_hits": 3, "fit": "adjacent", "fit_band": "over_50"}, (0.6, "description")),
    (False, {"keyword_hits": 3, "fit": "core", "fit_band": "25_50"}, (0.6, "description")),
    (False, {"keyword_hits": 3, "fit": "core", "fit_band": None}, (0.6, "description")),
    (False, {"keyword_hits": 2, "fit": "core", "fit_band": "over_50", "segment_share": 0.95},
     (0.95, "segments")),
])
def test_exposure_of(member, kw, expected):
    exposure, source = exposure_of(_c(member, **kw), _DEF)
    assert (round(exposure, 6), source) == expected


def test_reviewed_pure_play_never_exceeds_80_percent_without_segments():
    c = _c(keyword_hits=50, fit="core", fit_band="over_50", industry="Semiconductors")
    assert exposure_of(c, _DEF)[0] == pytest.approx(0.8)


def test_regression_member_description_evidence_lowers_exposure_below_unknown():
    """REGRESSION (fixed 2026-09-23). Was: scoring.py:102-117 — a MEMBER with no segment/industry evidence scores the neutral
    0.5 when its description mentions the theme 0-1 times, but only 0.4 when it mentions
    it twice: more evidence of relevance LOWERS its exposure (0 hits → 0.5, 2 hits → 0.4,
    3 hits → 0.6), so an otherwise identical member ranks 5.5 points lower for having an
    on-theme description. Expected: exposure(2 hits) ≥ exposure(0 hits)."""
    silent = _c(True, keyword_hits=0)
    corroborated = _c(True, keyword_hits=2)
    assert exposure_of(corroborated, _DEF)[0] >= exposure_of(silent, _DEF)[0]
    ctx = PoolContext(None, None, None, None, {}, {}, 3, None)
    assert score_candidate(corroborated, _DEF, ctx).total >= score_candidate(silent, _DEF, ctx).total


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — pool context and percentiles
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("values, expected", [
    ({}, {}),
    ({"a": None, "b": 0, "c": -5, "d": NAN, "e": INF, "f": True}, {}),
    ({"a": 7.0}, {"a": 1.0}),
    ({"a": 7.0, "b": None}, {"a": 1.0}),
    ({"a": 1.0, "b": 10.0, "c": 100.0}, {"a": 0.0, "b": 0.5, "c": 1.0}),
    ({"a": 5.0, "b": 5.0}, {"a": 0.5, "b": 0.5}),
    ({"a": 5.0, "b": 5.0, "c": 5.0}, {"a": 0.5, "b": 0.5, "c": 0.5}),
    ({"a": 1.0, "b": 2.0, "c": 2.0, "d": 1e12}, {"a": 0.0, "b": 0.5, "c": 0.5, "d": 1.0}),
    ({"a": 1e-300, "b": 1e300}, {"a": 0.0, "b": 1.0}),
])
def test_log_percentiles(values, expected):
    got = _log_percentiles(values)
    assert got.keys() == expected.keys()
    for k, v in expected.items():
        assert got[k] == pytest.approx(v)


def test_build_pool_context_filters_garbage_and_uses_members_only_for_median_cap():
    cands = [
        Candidate("M1", is_member=True, market_cap=1e9, ret_3m=0.1, ret_6m=NAN, adtv_6m=5e6),
        Candidate("M2", is_member=True, market_cap=3e9, ret_3m=0.3, ret_6m=INF),
        Candidate("M3", is_member=True, market_cap=None, ret_3m=None),
        Candidate("M4", is_member=True, market_cap=-5.0, ret_3m=True),       # bool ignored
        Candidate("M5", is_member=True, market_cap=NAN),
        Candidate("N1", is_member=False, market_cap=1e12, ret_3m=0.2, ret_6m=0.05),
    ]
    ctx = build_pool_context(cands, etfs_loaded=3)
    assert ctx.median_3m == pytest.approx(0.2)
    assert ctx.sd_3m == pytest.approx(math.sqrt(((0.1 ** 2) + 0 + (0.1 ** 2)) / 3))
    assert ctx.median_6m == pytest.approx(0.05)
    assert ctx.sd_6m is None                                     # one value → no spread
    assert ctx.median_member_cap == pytest.approx(2e9)           # newcomer's 1e12 excluded
    assert set(ctx.cap_percentile) == {"M1", "M2", "N1"}
    assert ctx.adtv_percentile == {"M1": 1.0}
    assert ctx.etfs_loaded == 3


@pytest.mark.parametrize("raw, expected", [(-2, 0), (0, 0), (3, 3), (2.9, 2), ("4", 4)])
def test_build_pool_context_etfs_loaded_is_a_non_negative_int(raw, expected):
    assert build_pool_context([], etfs_loaded=raw).etfs_loaded == expected


def test_build_pool_context_empty_pool():
    ctx = build_pool_context([], etfs_loaded=0)
    assert ctx.median_3m is None and ctx.sd_3m is None and ctx.median_member_cap is None
    assert ctx.cap_percentile == {} and ctx.adtv_percentile == {}


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — the 55 / 20 / 15 / 10 score
# ══════════════════════════════════════════════════════════════════════════════════════

def _ctx(**kw) -> PoolContext:
    base = dict(median_3m=None, sd_3m=None, median_6m=None, sd_6m=None, cap_percentile={},
                adtv_percentile={}, etfs_loaded=3, median_member_cap=None)
    base.update(kw)
    return PoolContext(**base)


def test_weights_are_55_20_15_10():
    assert (W_EXPOSURE, W_ETF, W_MARKET, W_SIZE) == (55.0, 20.0, 15.0, 10.0)
    assert W_EXPOSURE + W_ETF + W_MARKET + W_SIZE == 100.0


def test_perfect_candidate_scores_100():
    c = _c(segment_share=1.0, etf_holders=3, etf_max_weight=5.0, ret_3m=10.0, ret_6m=10.0)
    ctx = _ctx(median_3m=0.0, sd_3m=0.1, median_6m=0.0, sd_6m=0.1,
               cap_percentile={"T": 1.0}, adtv_percentile={"T": 1.0})
    s = score_candidate(c, _DEF, ctx)
    assert (s.exposure_pts, s.etf_pts, s.market_pts, s.size_pts) == (55.0, 20.0, 15.0, 10.0)
    assert s.total == 100.0


def test_worst_known_candidate_scores_0():
    c = _c(segment_share=0.0, etf_holders=0, etf_max_weight=0.0, ret_3m=-10.0, ret_6m=-10.0)
    ctx = _ctx(median_3m=0.0, sd_3m=0.1, median_6m=0.0, sd_6m=0.1,
               cap_percentile={"T": 0.0}, adtv_percentile={"T": 0.0})
    assert score_candidate(c, _DEF, ctx).total == 0.0


def test_unknown_newcomer_scores_only_the_neutral_market_part():
    s = score_candidate(_c(False), _DEF, _ctx())
    assert (s.exposure_pts, s.etf_pts, s.market_pts, s.size_pts) == (0.0, 0.0, 7.5, 0.0)
    assert s.exposure_source == "unknown"


def test_unknown_member_scores_neutral_everywhere_it_can():
    s = score_candidate(_c(True), _DEF, _ctx())
    assert (s.exposure_pts, s.etf_pts, s.market_pts, s.size_pts) == (27.5, 0.0, 7.5, 5.0)
    assert s.total == 40.0


def test_zero_etfs_loaded_gives_members_half_and_newcomers_nothing():
    ctx = _ctx(etfs_loaded=0)
    assert score_candidate(_c(True, etf_holders=3), _DEF, ctx).etf_pts == 10.0
    assert score_candidate(_c(False, etf_holders=3), _DEF, ctx).etf_pts == 0.0


@pytest.mark.parametrize("holders, weight, expected", [
    (3, 5.0, 20.0),
    (1, 2.5, round(20 * (0.7 / 3 + 0.3 * 0.5), 2)),
    (0, 0.0, 0.0),
    (9, 50.0, 20.0),              # more holders than ETFs loaded / huge weight: capped
    (-2, -1.0, 0.0),              # nonsense negatives floor at zero
    (3, NAN, 14.0),
    (3, INF, 14.0),
])
def test_etf_points(holders, weight, expected):
    s = score_candidate(_c(False, etf_holders=holders, etf_max_weight=weight), _DEF, _ctx())
    assert s.etf_pts == pytest.approx(expected)


@pytest.mark.parametrize("r3, r6, expected", [
    (0.1, None, 7.5),             # at the median
    (0.3, None, 15.0),            # +2σ
    (1e6, None, 15.0),            # outlier clipped at +2σ
    (-1e6, None, 0.0),            # clipped at −2σ
    (0.2, None, 11.25),           # +1σ
    (0.3, -0.1, 7.5),             # +2σ and −2σ average to neutral
    (None, 0.3, 15.0),            # 6-month alone
    (NAN, None, 7.5),             # unknown → neutral
    (INF, -INF, 7.5),
    (None, None, 7.5),
])
def test_market_points_are_clipped_z_of_the_theme_median(r3, r6, expected):
    ctx = _ctx(median_3m=0.1, sd_3m=0.1, median_6m=0.1, sd_6m=0.1)
    s = score_candidate(_c(False, ret_3m=r3, ret_6m=r6), _DEF, ctx)
    assert s.market_pts == pytest.approx(expected)


@pytest.mark.parametrize("sd", [None, 0.0, -0.5, NAN, INF])
def test_market_points_neutral_when_the_spread_is_unusable(sd):
    ctx = _ctx(median_3m=0.1, sd_3m=sd)
    assert score_candidate(_c(False, ret_3m=5.0), _DEF, ctx).market_pts == 7.5


def test_market_points_neutral_when_the_pool_has_no_returns():
    assert score_candidate(_c(False, ret_3m=0.5), _DEF, _ctx()).market_pts == 7.5


def test_size_points_percentiles_with_member_neutral_and_newcomer_zero():
    ctx = _ctx(cap_percentile={"T": 1.0}, adtv_percentile={"T": 0.0})
    assert score_candidate(_c(False), _DEF, ctx).size_pts == 5.0
    ctx = _ctx()
    assert score_candidate(_c(True), _DEF, ctx).size_pts == 5.0
    assert score_candidate(_c(False), _DEF, ctx).size_pts == 0.0


def test_score_breakdown_parts_and_rounding():
    ctx = _ctx(cap_percentile={"T": 1 / 3}, adtv_percentile={"T": 1 / 3})
    s = score_candidate(_c(False, segment_share=1 / 3), _DEF, ctx)
    assert s.exposure == round(1 / 3, 4)
    assert s.exposure_pts == round(55 / 3, 2)
    assert s.total == round(55 / 3 + 7.5 + 10 / 3, 2)
    assert set(s.as_parts()) == {"exposure_pts", "etf_pts", "market_pts", "size_pts",
                                 "exposure", "exposure_source"}


def test_scores_stay_finite_and_bounded_for_hostile_inputs():
    rng = random.Random(20260923)
    pick = rng.choice
    caps = [None, 0, -5.0, 1e6, 1e9, 1e12, NAN, INF]
    rets = [None, NAN, INF, -INF, -0.99, 0.0, 0.1, 5.0, 1e9, -1e9]
    cands = []
    for i in range(300):
        cands.append(Candidate(
            ticker=f"T{i}", is_member=pick([True, False]), market_cap=pick(caps),
            industry=pick([None, "Semiconductors", "Banks"]),
            segment_share=pick([None, 0.0, 0.3, 1.0, 1.7, -0.2, NAN, INF]),
            keyword_hits=pick([-1, 0, 1, 2, 3, 9]), etf_holders=pick([-3, 0, 1, 3, 10]),
            etf_max_weight=pick([0.0, 2.5, 50.0, -1.0, NAN, INF]),
            ret_3m=pick(rets), ret_6m=pick(rets), adtv_6m=pick(caps),
            fit=pick([None, "core", "adjacent", "not_related"]),
            fit_band=pick([None, "over_50", "pre_revenue", "under_25"]),
        ))
    for etfs_loaded in (0, 3):
        ctx = build_pool_context(cands, etfs_loaded=etfs_loaded)
        for c in cands:
            s = score_candidate(c, _DEF, ctx)
            for v, hi in ((s.exposure_pts, 55), (s.etf_pts, 20), (s.market_pts, 15),
                          (s.size_pts, 10), (s.total, 100)):
                assert math.isfinite(v) and 0.0 <= v <= hi + 1e-9, (c, s)


# ══════════════════════════════════════════════════════════════════════════════════════
# scoring — eligibility floors
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("exchange, ok", [
    ("NASDAQ", True), ("nyse", True), (" NYSE ", True), ("AMEX", True),
    ("NYSE American", True), ("NYSEAMERICAN", True),
    ("OTC", False), ("PNK", False), ("LSE", False), ("TSX", False), ("", False),
    (None, False), (123, False), (["NASDAQ"], False),
])
def test_is_us_listed(exchange, ok):
    assert is_us_listed(exchange) is ok


def test_definition_floor_properties():
    assert (_DEF.min_market_cap, _DEF.incumbent_min_market_cap) == (2e9, 0.8e9)
    assert (_DEF.min_adtv, _DEF.incumbent_min_adtv) == (20e6, 10e6)
    assert (_DEF_SMALL.min_market_cap, _DEF_SMALL.incumbent_min_market_cap) == (500e6, 200e6)
    assert (_DEF_SMALL.min_adtv, _DEF_SMALL.incumbent_min_adtv) == (10e6, 5e6)


def _member(**kw) -> Candidate:
    base = dict(ticker="M", is_member=True, market_cap=1e9, price=1.0, exchange="NYSE",
                actively_trading=True, adtv_6m=15e6, session_coverage=0.95)
    base.update(kw)
    return Candidate(**base)


def _newcomer(**kw) -> Candidate:
    base = dict(ticker="N", is_member=False, market_cap=5e9, price=50.0, exchange="NASDAQ",
                actively_trading=True, adtv_6m=50e6, session_coverage=1.0)
    base.update(kw)
    return Candidate(**base)


@pytest.mark.parametrize("kw, expected", [
    ({}, None),
    ({"price": 0.5}, None),                                     # members have NO price floor
    ({"price": None}, None),
    ({"actively_trading": False}, Reason.DELISTED),
    ({"actively_trading": None}, None),
    ({"market_cap": 0.8e9}, None),                              # exactly the incumbent floor
    ({"market_cap": 0.79e9}, Reason.BELOW_FLOORS),
    ({"market_cap": None}, None),                               # unknown never removes
    ({"market_cap": NAN}, None),
    ({"market_cap": 0.0}, None),
    ({"adtv_6m": 10e6}, None),
    ({"adtv_6m": 9.9e6}, Reason.BELOW_FLOORS),
    ({"adtv_6m": 0.0}, Reason.BELOW_FLOORS),
    ({"adtv_6m": None}, None),
    ({"adtv_6m": NAN}, None),
    ({"session_coverage": 0.9}, None),
    ({"session_coverage": 0.89}, Reason.BELOW_FLOORS),
    ({"session_coverage": None}, None),
    # OTC / foreign members are exempt from the liquidity and coverage floors…
    ({"exchange": "OTC", "adtv_6m": 1.0, "session_coverage": 0.1}, None),
    ({"exchange": None, "adtv_6m": 1.0, "session_coverage": 0.1}, None),
    # …and so is a member whose price history is unavailable.
    ({"history_blocked": True, "adtv_6m": 1.0, "session_coverage": 0.1}, None),
    # …but not from the size floor or a delisting.
    ({"exchange": "OTC", "market_cap": 1e8}, Reason.BELOW_FLOORS),
    ({"exchange": "OTC", "actively_trading": False}, Reason.DELISTED),
    ({"market_cap": None, "price": None, "exchange": None, "actively_trading": None,
      "adtv_6m": None, "session_coverage": None}, None),        # nothing known: stays
])
def test_member_floors(kw, expected):
    assert floor_failure(_member(**kw), _DEF) is expected


def test_small_cap_member_floors():
    assert floor_failure(_member(market_cap=2e8, adtv_6m=5e6), _DEF_SMALL) is None
    assert floor_failure(_member(market_cap=1.99e8), _DEF_SMALL) is Reason.BELOW_FLOORS
    assert floor_failure(_member(adtv_6m=4.9e6), _DEF_SMALL) is Reason.BELOW_FLOORS


@pytest.mark.parametrize("kw, expected", [
    ({}, None),
    ({"actively_trading": False, "history_blocked": True}, Reason.DELISTED),
    ({"history_blocked": True, "exchange": "OTC"}, Reason.NO_DATA),
    ({"exchange": "OTC"}, Reason.NOT_US_LISTED),
    ({"exchange": None}, Reason.NOT_US_LISTED),
    ({"exchange": "nasdaq"}, None),
    ({"market_cap": None}, Reason.FAILS_FLOORS),
    ({"market_cap": 2e9}, None),                                # exactly the floor
    ({"market_cap": 1.99e9}, Reason.FAILS_FLOORS),
    ({"market_cap": NAN}, Reason.FAILS_FLOORS),
    ({"market_cap": -1.0}, Reason.FAILS_FLOORS),
    ({"price": MIN_PRICE}, None),
    ({"price": 2.99}, Reason.FAILS_FLOORS),
    ({"price": None}, Reason.FAILS_FLOORS),
    ({"price": NAN}, Reason.FAILS_FLOORS),
    ({"price": INF}, Reason.FAILS_FLOORS),
    ({"price": True}, Reason.FAILS_FLOORS),
    ({"adtv_6m": 20e6}, None),
    ({"adtv_6m": 19.9e6}, Reason.FAILS_FLOORS),
    ({"adtv_6m": None}, Reason.FAILS_FLOORS),
    ({"session_coverage": MIN_SESSION_COVERAGE}, None),
    ({"session_coverage": 0.89}, Reason.FAILS_FLOORS),
    ({"session_coverage": None}, Reason.FAILS_FLOORS),
    ({"session_coverage": NAN}, Reason.FAILS_FLOORS),
    ({"sessions_listed": SEASONING_SESSIONS}, None),
    ({"sessions_listed": SEASONING_SESSIONS - 1}, Reason.IPO_SEASONING),
    ({"sessions_listed": 0}, Reason.IPO_SEASONING),
    ({"sessions_listed": -4}, Reason.IPO_SEASONING),
    ({"sessions_listed": None}, None),
    ({"actively_trading": None}, None),
])
def test_newcomer_floors(kw, expected):
    assert floor_failure(_newcomer(**kw), _DEF, median_member_cap=None) is expected


@pytest.mark.parametrize("sessions, cap, median, expected", [
    (FAST_TRACK_SESSIONS, 5e9, 4e9, None),                      # significant IPO: fast track
    (FAST_TRACK_SESSIONS - 1, 5e9, 4e9, Reason.IPO_SEASONING),
    (FAST_TRACK_SESSIONS, 4e9, 4e9, Reason.IPO_SEASONING),      # must be ABOVE the median
    (FAST_TRACK_SESSIONS, 3e9, 4e9, Reason.IPO_SEASONING),
    (30, 5e9, None, Reason.IPO_SEASONING),                      # no member median known
    (62, 5e9, 1e12, Reason.IPO_SEASONING),
    (63, 3e9, 1e12, None),
])
def test_ipo_seasoning_and_fast_track(sessions, cap, median, expected):
    c = _newcomer(sessions_listed=sessions, market_cap=cap)
    assert floor_failure(c, _DEF, median_member_cap=median) is expected


def test_fast_tracked_ipo_still_needs_coverage():
    c = _newcomer(sessions_listed=20, market_cap=5e9, session_coverage=0.5)
    assert floor_failure(c, _DEF, median_member_cap=1e9) is Reason.FAILS_FLOORS


def test_small_cap_newcomer_floors():
    assert floor_failure(_newcomer(market_cap=5e8, adtv_6m=10e6), _DEF_SMALL) is None
    assert floor_failure(_newcomer(market_cap=4.9e8), _DEF_SMALL) is Reason.FAILS_FLOORS
    assert floor_failure(_newcomer(adtv_6m=9.9e6), _DEF_SMALL) is Reason.FAILS_FLOORS


# ══════════════════════════════════════════════════════════════════════════════════════
# reasons — the user-facing lines
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("action", [Action.KEPT, Action.DEFERRED, Action.BENCH, Action.REJECTED])
def test_user_reason_is_none_for_decisions_users_never_see(action):
    for reason in Reason:
        assert user_reason(action, reason, {"etf_pts": 20.0, "fit": "core"}) is None


@pytest.mark.parametrize("parts, expected", [
    ({"etf_pts": 14.0}, reasons._ADDED_BY_FUNDS),
    ({"etf_pts": 20, "fit": "adjacent"}, reasons._ADDED_BY_FUNDS),     # funds beat adjacent
    ({"etf_pts": 13.99, "exposure_source": "segments"}, reasons._ADDED_BY_SOURCE["segments"]),
    ({"etf_pts": True, "exposure_source": "segments"}, reasons._ADDED_BY_SOURCE["segments"]),
    ({"etf_pts": "20", "exposure_source": "segments"}, reasons._ADDED_BY_SOURCE["segments"]),
    ({"etf_pts": NAN, "exposure_source": "segments"}, reasons._ADDED_BY_SOURCE["segments"]),
    ({"fit": "adjacent", "exposure_source": "segments"}, reasons._ADDED_ADJACENT),
    ({"exposure_source": "description"}, reasons._ADDED_BY_SOURCE["description"]),
    ({"exposure_source": "industry"}, reasons._ADDED_BY_SOURCE["industry"]),
    ({"exposure_source": "unknown"}, reasons._ADDED_BY_SOURCE["industry"]),
    ({"exposure_source": None}, reasons._ADDED_BY_SOURCE["industry"]),
    ({}, reasons._ADDED_BY_SOURCE["industry"]),
    (None, reasons._ADDED_BY_SOURCE["industry"]),
])
def test_user_reason_for_a_natural_entry(parts, expected):
    assert user_reason(Action.ADDED, Reason.ENTERED_TOP_RANKS, parts) == expected


@pytest.mark.parametrize("action, reason", [
    (Action.ADDED, Reason.REFILL),
    (Action.RETURNED, Reason.REFILL),
    (Action.RETURNED, Reason.RETURNED_TOP_RANKS),
    (Action.REMOVED, Reason.BLOCKED),
    (Action.REMOVED, Reason.DELISTED),
    (Action.REMOVED, Reason.BELOW_FLOORS),
    (Action.REMOVED, Reason.OUTRANKED),
    (Action.REMOVED, Reason.OFF_THEME),
])
def test_every_change_the_planner_can_publish_has_a_line(action, reason):
    text = user_reason(action, reason, None)
    assert isinstance(text, str) and text.strip() and text in ALL_USER_TEXT


def test_every_user_reason_output_is_in_the_scanned_set():
    parts_variants = [None, {}, {"etf_pts": 20.0}, {"fit": "adjacent"},
                      {"exposure_source": "segments"}, {"exposure_source": "description"}]
    for action in Action:
        for reason in Reason:
            for parts in parts_variants:
                text = user_reason(action, reason, parts)
                assert text is None or text in ALL_USER_TEXT, (action, reason, parts)


_BANNED = [r"\bpric", r"\breturn", r"\bgain", r"\brall(y|ies|ied)", r"\bmomentum",
           r"perform", r"\bbuy", r"\bsell", r"\bcheap", r"\bexpensive", r"\bvaluation",
           r"\btarget", r"%"]
_IDENTITY = [r"\bgemini\b", r"\bgoogle\b", r"\bopenai\b", r"\bai\b", r"\bllm\b", r"\bgpt",
             r"\bmodel\b", r"\bartificial intelligence\b", r"\bchatbot\b"]


def test_user_text_cites_relevance_never_performance_or_the_model():
    assert len(ALL_USER_TEXT) == len(set(ALL_USER_TEXT)) >= 10
    for text in ALL_USER_TEXT:
        assert isinstance(text, str) and text.strip()
        for pat in _BANNED + _IDENTITY:
            assert not re.search(pat, text, re.IGNORECASE), (pat, text)


def test_vocabulary_scan_is_not_vacuous():
    """The scan must actually catch the words it bans (mutation check on the patterns)."""
    for bad in ("Added after a strong rally.", "Removed: price fell 12%.", "Its momentum",
                "Returns lagged", "Gains", "Underperformed peers", "A buy", "Seller",
                "Cheap valuation", "Price target", "Picked by an AI model", "Gemini said"):
        assert any(re.search(p, bad, re.IGNORECASE) for p in _BANNED + _IDENTITY), bad
    # …and does not trip on the ordinary words the real lines use.
    assert not any(re.search(p, "it again ranks among", re.IGNORECASE) for p in _BANNED)


# ══════════════════════════════════════════════════════════════════════════════════════
# 2026-09-23 fix pass — term counting, member exposure, NaN segment share
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text, keywords, expected", [
    ("robotic arms and robotics software", ("robotic", "robotics"), 1),     # one term, 2 forms
    ("optical interconnect", ("optical", "interconnect", "optical interconnect"), 1),
    ("optical interconnect and optical networking",
     ("optical", "interconnect", "optical interconnect"), 2),              # "optical" again, apart
    ("gene editing", ("gene", "gene editing"), 1),
    ("chip foundry", ("chip", "foundry"), 2),                               # two real terms
    ("chips, chips and more chips", ("chip",), 1),                          # repeats count once
    ("a GPU and wafer maker making chips", ("chip", "wafer", "foundry", "gpu"), 3),
])
def test_one_word_or_phrase_is_one_piece_of_evidence(text, keywords, expected):
    assert count_keyword_hits(text, keywords) == expected


def test_blank_keywords_never_count():
    """A whitespace keyword used to compile to a pattern matching the empty string, i.e.
    a hit in ANY text."""
    # Punctuation next to a space is where an empty pattern finds its "match".
    assert count_keyword_hits("anything — at all, really.", ["  ", "", "\t"]) == 0
    assert count_keyword_hits("chip, and wafer.", ["chip", " ", "wafer"]) == 2


def test_filling_a_short_list_spends_the_ceiling_so_no_swap_rides_on_top():
    """8 members → 4 seats must be filled (the ceiling of 3 never blocks those), and that
    already exceeds the ceiling, so no struck member is swapped out the same month: the
    two an entrant was waiting for are DEFERRED, the rest KEPT_FOR_SIZE."""
    names = _names(8)
    rest = names[3:]
    outs = _outs(6)
    plan, by = _plan(names[:3] + outs + rest, cand={t: OFF for t in rest},
                     history={t: STRUCK for t in rest})
    assert plan.removed == []
    assert plan.added == outs[:4] and len(plan.after) == 12
    assert plan.change_count == 4
    assert sorted(by[t].action.value for t in rest) == ["deferred"] * 2 + ["kept"] * 3
    assert {by[t].action for t in outs[4:]} == {Action.DEFERRED}


def test_member_exposure_never_drops_as_description_evidence_grows():
    values = [exposure_of(_c(True, keyword_hits=n), _DEF)[0] for n in range(0, 7)]
    assert values == sorted(values), values
    assert values[0] == 0.5 and values[-1] == pytest.approx(0.6)


def test_newcomer_description_credit_is_unchanged_by_the_member_floor():
    assert exposure_of(_c(False, keyword_hits=2), _DEF) == (pytest.approx(0.4), "description")


def test_nan_segment_share_is_unknown_not_a_known_zero():
    assert exposure_of(_c(True, segment_share=float("nan")), _DEF) == (0.5, "unknown")
    assert exposure_of(_c(False, segment_share=float("nan")), _DEF) == (0.0, "unknown")


def test_current_list_is_stripped_before_it_is_compared():
    padded = plan_rotation(slug="t", current=[f" {t.lower()} " for t in _names(12)],
                           candidates={}, scores={}, floors={}, history={})
    assert padded.before == _names(12)


def test_no_user_line_claims_a_closeness_the_score_does_not_measure():
    """The rank includes the market and size tie-breakers, so a line may say a company
    RANKS ahead, never that it is more closely tied (2026-09-23 review)."""
    for line in ALL_USER_TEXT:
        assert "closely tied" not in line and "most tied" not in line, line
    assert "two months" not in user_reason(Action.REMOVED, Reason.OUTRANKED)


def test_swap_lines_explain_a_change_by_rank_only():
    """Equal relevance, split only by the market tie-breaker: the lines must not claim
    the entrant is more closely tied than the member it replaced."""
    members = _names(12)
    struck = members[-1]
    plan, by = _plan(members[:3] + ["OHOT"] + members[3:], history={struck: STRUCK},
                     cand={struck: OFF})
    assert plan.added == ["OHOT"] and plan.removed == [struck]        # a swap did happen
    for t in plan.added + plan.removed:
        text = user_reason(by[t].action, by[t].reason, by[t].score_parts) or ""
        assert "tied" not in text, text
