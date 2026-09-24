"""The monthly membership decision for ONE theme. Pure and deterministic.

Rules, in order (the index-provider playbook, adapted to a monthly display list):

1. FORCED removals always apply, cap or no cap: an editor block, a delisting, or a member
   failing the (lower) incumbent floors. Pinned members survive the floors, never a
   delisting or a block. Missing data never forces anyone out.
2. ANCHORS — the top 3 members by exposure × market cap — are never rotated out naturally.
   Readers expect to see the leaders; freshness happens in the tail.
3. RANK every eligible stock by score (ties: bigger market cap, then ticker). A newcomer
   is eligible only if it passes the newcomer floors AND the relevance check confirmed its
   own description is on-theme (`fit` core/adjacent — a failed check means NOT eligible).
   A stock that flipped in and out ≥3 times in 6 months carries a −5 penalty to re-enter.
4. BUFFER: a member ranked within 1.25·N stays. A member ranked beyond it takes a STRIKE.
   It is removed only on a SECOND consecutive strike (or on the first if it ranks beyond
   2·N — decisively off-pace) and only after 2 months of tenure. An off-theme AI verdict
   alone always needs two strikes. A member with no recorded history counts as seasoned.
5. ENTRY + PAIRING: an outsider must rank within 0.75·N, and a natural removal only happens
   when such an outsider takes the seat — one out, one in, so list sizes stay stable.
   Seats opened by forced removals (or a list below 12) are filled first.
6. CEILING: at most floor(0.30·N) replaced slots per month. Forced removals and the seats a
   list below 12 must fill count against it but are never blocked by it. A natural removal
   the ceiling stops is DEFERRED to next month together with the entrant that was waiting
   for its seat; one stopped because no entrant exists is simply KEPT_FOR_SIZE.
7. SIZE: never below 12. Slots opened by forced removals are refilled from the best
   remaining eligible outsiders; if the pool is dry the list publishes short with
   `shortfall` set (and the service logs an error).

There is deliberately NO minimum number of changes (owner decision 2026-09-23: 20-30% is a
ceiling). Relevance beats freshness: an off-theme stock is never added to hit a number.
"""
from __future__ import annotations

import math
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.theme_rotation.models import (
    ADDABLE_FITS,
    Action,
    Candidate,
    Decision,
    Fit,
    MemberHistory,
    Reason,
    RotationConfig,
    ScoreBreakdown,
    ThemePlan,
)

_FORCED_REASONS = (Reason.DELISTED, Reason.BELOW_FLOORS)


def plan_rotation(
    *,
    slug: str,
    current: Sequence[str],
    candidates: Mapping[str, Candidate],
    scores: Mapping[str, ScoreBreakdown],
    floors: Mapping[str, Optional[Reason]],
    history: Mapping[str, MemberHistory],
    pinned: Set[str] = frozenset(),
    blocked: Set[str] = frozenset(),
    cfg: RotationConfig = RotationConfig(),
) -> ThemePlan:
    members = _dedupe([t.strip().upper() for t in current if isinstance(t, str) and t.strip()])
    member_set = set(members)
    pinned = {t.strip().upper() for t in pinned}
    blocked = {t.strip().upper() for t in blocked}

    n_target = max(cfg.min_size, min(cfg.max_size, len(members)))
    change_cap = int(math.floor(cfg.max_change_fraction * n_target + 1e-9))
    keep_limit = int(math.ceil(cfg.keep_rank_fraction * n_target - 1e-9))
    entry_limit = max(1, int(math.floor(cfg.entry_rank_fraction * n_target + 1e-9)))
    decisive_limit = int(math.ceil(cfg.decisive_rank_multiple * n_target - 1e-9))

    decisions: Dict[str, Decision] = {}

    def score_of(t: str) -> Optional[float]:
        s = scores.get(t)
        return s.total if s is not None else None

    def parts_of(t: str) -> Dict[str, object]:
        s = scores.get(t)
        parts: Dict[str, object] = s.as_parts() if s is not None else {}
        c = candidates.get(t)
        if c is not None:
            parts["fit"] = c.fit
            parts["fit_band"] = c.fit_band
            parts["etf_holders"] = c.etf_holders
        return parts

    # 1. Forced removals ------------------------------------------------------------------
    forced: List[Tuple[str, Reason]] = []
    for t in members:
        floor = floors.get(t)
        if t in blocked:
            forced.append((t, Reason.BLOCKED))
        elif floor is Reason.DELISTED:
            forced.append((t, Reason.DELISTED))
        elif floor in _FORCED_REASONS and t not in pinned:
            forced.append((t, floor))
    forced_set = {t for t, _ in forced}
    survivors = [t for t in members if t not in forced_set]

    # 3. Eligible outsiders + ranking -------------------------------------------------------
    rejected: Dict[str, Reason] = {}
    outsiders: List[str] = []
    for t in sorted(candidates):
        if t in member_set:
            continue
        c = candidates[t]
        if t in blocked:
            rejected[t] = Reason.BLOCKED
            continue
        floor = floors.get(t)
        if floor is not None:
            rejected[t] = floor
            continue
        if scores.get(t) is None:
            rejected[t] = Reason.NO_DATA
            continue
        if c.fit == Fit.NOT_RELATED.value:
            rejected[t] = Reason.NOT_ON_THEME
            continue
        if c.fit not in ADDABLE_FITS:
            rejected[t] = Reason.FIT_UNVERIFIED
            continue
        if c.fit == Fit.ADJACENT.value and not _backed_by_data(c, scores.get(t)):
            rejected[t] = Reason.WEAK_EVIDENCE
            continue
        outsiders.append(t)

    def effective_score(t: str) -> float:
        base = score_of(t)
        if base is None:
            return float("-inf")
        h = history.get(t)
        if t not in member_set and h is not None and h.flips_6m >= cfg.pingpong_flips:
            return base - cfg.pingpong_penalty
        return base

    def market_cap(t: str) -> float:
        c = candidates.get(t)
        v = c.market_cap if c is not None else None
        return v if isinstance(v, (int, float)) and math.isfinite(v) else 0.0

    pool = survivors + outsiders
    ranked = sorted(pool, key=lambda t: (-effective_score(t), -market_cap(t), t))
    rank = {t: i + 1 for i, t in enumerate(ranked)}

    # 2. Anchors ----------------------------------------------------------------------------
    def anchor_key(t: str) -> Tuple[float, str]:
        s = scores.get(t)
        exposure = s.exposure if s is not None else 0.0
        return (-(exposure * market_cap(t)), t)

    anchors = set(sorted(survivors, key=anchor_key)[: max(0, cfg.anchors)])

    # 4. Strikes + natural removals --------------------------------------------------------
    strikes: Dict[str, bool] = {}
    natural: List[Tuple[str, Reason]] = []
    kept_reason: Dict[str, Reason] = {}
    for t in survivors:
        c = candidates.get(t)
        # A "not related" verdict on the DESCRIPTION alone never strikes a member most of
        # the theme's funds hold: the market's own judgement outweighs one paragraph of
        # text (Tesla's description is about cars; all three robotics ETFs hold it).
        off_theme = (c is not None and c.fit == Fit.NOT_RELATED.value
                     and c.etf_holders < cfg.fund_consensus)
        outranked = rank.get(t, 10**9) > keep_limit
        strike = outranked or off_theme
        strikes[t] = strike
        if t in pinned:
            kept_reason[t] = Reason.PINNED
            continue
        if t in anchors:
            kept_reason[t] = Reason.ANCHOR
            continue
        if not strike:
            kept_reason[t] = Reason.STILL_ON_THEME
            continue
        h = history.get(t)
        seasoned = h is None or h.tenure_months is None or h.tenure_months >= cfg.min_tenure_months
        if not seasoned:
            kept_reason[t] = Reason.TENURE_PROTECTED
            continue
        second_strike = h is not None and h.struck_last_month
        decisive = outranked and rank.get(t, 10**9) > decisive_limit
        if second_strike or decisive:
            natural.append((t, Reason.OUTRANKED if outranked else Reason.OFF_THEME))
        else:
            kept_reason[t] = Reason.FIRST_STRIKE
    # Worst first, so the cap defers the least clear-cut removals.
    natural.sort(key=lambda item: (-rank.get(item[0], 10**9), item[0]))

    # 5-6. Entrants, pairing and the ceiling -----------------------------------------------
    entrants = [t for t in ranked if t not in member_set and rank[t] <= entry_limit]
    # Seats that MUST be filled regardless of any natural change: those forced removals
    # opened, plus any shortfall below the target size. The ceiling never blocks these —
    # forced removals, or the seats filling a list below 12 — but they DO use it up, so a
    # short list being filled makes no natural swaps on top.
    base_open = max(0, n_target - (len(members) - len(forced)))
    natural_room = max(0, change_cap - max(len(forced), base_open))
    # A natural removal happens only when a stronger eligible outsider takes the seat —
    # one out, one in. Without a replacement the member stays: it is still the best we
    # have, and a list that shrinks a little every month reads as the section decaying.
    pairable = max(0, len(entrants) - base_open)
    n_natural = min(len(natural), natural_room, pairable)
    applied_natural = natural[:n_natural]
    deferred_natural: List[Tuple[str, Reason]] = []
    for i, (t, reason) in enumerate(natural[n_natural:], start=n_natural):
        # Name what ACTUALLY stopped it: an entrant existed for this seat and the ceiling
        # was spent (deferred to next month), or no entrant existed at all (kept for size).
        if i < pairable:
            deferred_natural.append((t, reason))
        else:
            kept_reason[t] = Reason.KEPT_FOR_SIZE

    removed_total = len(forced) + len(applied_natural)
    open_slots = base_open + len(applied_natural)
    additions = entrants[:open_slots]
    # The entrants that would have taken the deferred seats — deferred with them.
    deferred_entrants = entrants[open_slots:open_slots + len(deferred_natural)]

    # 7. Refill below the minimum size -------------------------------------------------------
    refills: List[str] = []
    size_now = len(members) - removed_total + len(additions)
    if size_now < cfg.min_size:
        taken = set(additions)
        for t in ranked:
            if size_now >= cfg.min_size:
                break
            if t in member_set or t in taken:
                continue
            refills.append(t)
            taken.add(t)
            size_now += 1
    shortfall = size_now < cfg.min_size

    # Assemble ------------------------------------------------------------------------------
    removed_set = forced_set | {t for t, _ in applied_natural}
    after = [t for t in members if t not in removed_set] + additions + refills

    added: List[str] = []
    returned: List[str] = []
    for t in additions + refills:
        h = history.get(t)
        (returned if h is not None and h.removed_recently else added).append(t)

    for t, reason in forced:
        decisions[t] = _decision(t, Action.REMOVED, reason, score_of(t), rank.get(t), True,
                                 strikes.get(t, False), parts_of(t))
    for t, reason in applied_natural:
        decisions[t] = _decision(t, Action.REMOVED, reason, score_of(t), rank.get(t), True,
                                 True, parts_of(t))
    for t, reason in deferred_natural:
        decisions[t] = _decision(t, Action.DEFERRED, Reason.CHANGE_CAP, score_of(t), rank.get(t),
                                 True, True, parts_of(t))
    for t in survivors:
        if t in decisions:
            continue
        decisions[t] = _decision(t, Action.KEPT, kept_reason.get(t, Reason.STILL_ON_THEME),
                                 score_of(t), rank.get(t), True, strikes.get(t, False), parts_of(t))
    for t in additions:
        action = Action.RETURNED if t in returned else Action.ADDED
        reason = Reason.RETURNED_TOP_RANKS if t in returned else Reason.ENTERED_TOP_RANKS
        decisions[t] = _decision(t, action, reason, score_of(t), rank.get(t), False, False,
                                 parts_of(t))
    for t in refills:
        action = Action.RETURNED if t in returned else Action.ADDED
        decisions[t] = _decision(t, action, Reason.REFILL, score_of(t), rank.get(t), False,
                                 False, parts_of(t))
    for t in deferred_entrants:
        if t in decisions:      # never overwrite a published addition
            continue
        decisions[t] = _decision(t, Action.DEFERRED, Reason.CHANGE_CAP, score_of(t), rank.get(t),
                                 False, False, parts_of(t))
    for t in outsiders:
        if t in decisions:
            continue
        reason = Reason.RANKED_BELOW_ENTRY if rank[t] > entry_limit else Reason.NO_OPEN_SLOT
        decisions[t] = _decision(t, Action.BENCH, reason, score_of(t), rank.get(t), False, False,
                                 parts_of(t))
    for t, reason in rejected.items():
        decisions[t] = _decision(t, Action.REJECTED, reason, score_of(t), None, False, False,
                                 parts_of(t))

    ordered = sorted(decisions.values(), key=lambda d: (_ACTION_ORDER[d.action], d.rank or 10**9,
                                                        d.ticker))
    return ThemePlan(
        slug=slug, before=members, after=after, decisions=ordered,
        added=added, returned=returned,
        removed=[t for t, _ in forced] + [t for t, _ in applied_natural],
        deferred=[t for t, _ in deferred_natural]
                 + [t for t in deferred_entrants if t not in after],
        shortfall=shortfall, change_cap=change_cap,
    )


_ACTION_ORDER = {Action.REMOVED: 0, Action.ADDED: 1, Action.RETURNED: 2, Action.KEPT: 3,
                 Action.DEFERRED: 4, Action.BENCH: 5, Action.REJECTED: 6}


def _decision(ticker: str, action: Action, reason: Reason, score: Optional[float],
              rank: Optional[int], was_member: bool, strike: bool,
              parts: Dict[str, object]) -> Decision:
    return Decision(ticker=ticker, action=action, reason=reason, score=score, rank=rank,
                    was_member=was_member, strike=strike, score_parts=parts)


def _backed_by_data(c: Candidate, score: Optional[ScoreBreakdown]) -> bool:
    """An "adjacent" newcomer must ALSO show it in the data: at least half its exposure on
    real evidence AND at least one of the theme's funds holding it. Stops a megacap whose
    products merely serve the theme (a chip maker in robotics) from riding its size in."""
    return (score is not None and score.exposure >= 0.5
            and score.exposure_source in ("segments", "industry", "description")
            and c.etf_holders >= 1)


def _dedupe(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(items))
