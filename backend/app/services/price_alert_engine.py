"""Price-alert evaluation — pure decision function, no I/O.

Kept pure so every case below is testable with no database, no clock and no network:
the oscillation case, the gap open, the cold start, and every shape of garbage a live
quote can carry.

THE FOUR DECISIONS, and why each is what it is:

  1. **Crossing, not level.** `price_above` fires when the price moves from BELOW the
     threshold to at-or-above it. Firing on "price >= threshold" would fire on every
     cycle for as long as the condition held — hundreds of times an afternoon.

  2. **Cold start SEEDS, never fires.** `last_price is None` means we have never observed
     this rule. Treating "unknown" as "below" would fire immediately on an alert set
     above a price the stock is already trading at — a notification about nothing, one
     second after the user pressed Save.

  3. **Hysteresis.** A crossing rule still fires repeatedly on a stock ticking
     249.99 / 250.01 / 249.99, because each tick IS a genuine crossing. The `armed` latch
     drops on fire and only re-arms once the price retreats past a band on the far side.

  4. **Garbage never overwrites good state.** A missing, NaN or non-positive price means
     "no observation this cycle", so `last_price` is left alone. Writing it would destroy
     the baseline the next crossing is measured against — and the alert would then miss
     the move it exists for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

KIND_ABOVE = "price_above"
KIND_BELOW = "price_below"
KIND_PERCENT = "percent_move"

VALID_KINDS = frozenset({KIND_ABOVE, KIND_BELOW, KIND_PERCENT})

REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"

VALID_REPEAT_MODES = frozenset({REPEAT_ONCE, REPEAT_DAILY})


def finite_price(value: Any) -> Optional[float]:
    """A usable, strictly positive price, or None.

    FMP emits NaN/Infinity JSON tokens on thin and just-listed names, and Python's
    `json` parses them into real float NaN/inf. Feeding NaN into a comparison answers
    False for EVERY branch — which silently disables a gate rather than tripping it, a
    failure mode already on record in this repo.

    A price of zero or below is not a delisting signal we can act on; it is an unusable
    reading.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def finite_percent(value: Any) -> Optional[float]:
    """A usable percent-change reading, or None. Zero and negatives are legitimate."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class AlertDecision:
    """What to do with one rule this cycle."""

    fire: bool
    # The value to persist as the new baseline. Equal to the old one when this cycle
    # produced no usable observation — see decision 4 in the module docstring.
    new_last_price: Optional[float]
    new_armed: bool
    # True for a one-shot rule that just fired: `is_active` goes False so it stops being
    # evaluated at all, rather than relying on the dedup key to suppress it forever.
    deactivate: bool
    reason: str


def evaluate_alert(
    *,
    kind: str,
    threshold: Any,
    repeat_mode: str,
    armed: bool,
    last_price: Any,
    price: Any,
    change_percent: Any = None,
    rearm_pct: float = 0.005,
) -> AlertDecision:
    """Decide whether one rule fires against one quote.

    Every input is treated as untrusted: `threshold` and `last_price` come from the
    database (where `NUMERIC` legally holds NaN) and `price`/`change_percent` come from
    FMP.
    """
    prev = finite_price(last_price)
    now_price = finite_price(price)
    limit = finite_price(threshold) if kind != KIND_PERCENT else finite_percent(threshold)

    def _hold(reason: str) -> AlertDecision:
        """No fire, no state change — the safe outcome for every unusable input."""
        return AlertDecision(False, prev, armed, False, reason)

    if kind not in VALID_KINDS:
        return _hold(f"unknown_kind:{kind}")
    if limit is None or (kind == KIND_PERCENT and (limit <= 0 or not math.isfinite(limit))):
        # A NaN threshold survives the DB CHECK only if the constraint was dropped, but
        # this function is also called from the create endpoint's validation path.
        return _hold("unusable_threshold")

    # ── percent_move ─────────────────────────────────────────────────
    # Reads `changePercentage` straight off the quote, which is already relative to the
    # previous close — so no baseline column is needed and the measure resets itself at
    # each day roll. The dedup key carries the trading date, which is what bounds it to
    # one fire per day; no latch is required.
    if kind == KIND_PERCENT:
        move = finite_percent(change_percent)
        if move is None:
            return _hold("no_percent_reading")
        if abs(move) >= limit:
            return AlertDecision(
                fire=True,
                new_last_price=now_price if now_price is not None else prev,
                new_armed=True,
                deactivate=(repeat_mode == REPEAT_ONCE),
                reason=f"percent_move:{move:.2f}",
            )
        return AlertDecision(
            False, now_price if now_price is not None else prev, True, False, "below_threshold"
        )

    # ── price_above / price_below ────────────────────────────────────
    if now_price is None:
        # Delisted mid-session, halted, or simply absent from this cycle's bulk quote.
        # Hold the baseline: overwriting it with nothing would lose the reference the
        # next crossing is measured against.
        return _hold("no_price")

    if prev is None:
        # COLD START. Seed and stay silent. See decision 2.
        return AlertDecision(False, now_price, armed, False, "seeded")

    if not armed:
        # Latched off after a previous fire. Re-arm only once the price has retreated
        # past a band on the far side of the threshold — a stock ticking 249.99/250.01
        # forty times must produce exactly one notification.
        if kind == KIND_ABOVE:
            rearmed = now_price <= limit * (1 - rearm_pct)
        else:
            rearmed = now_price >= limit * (1 + rearm_pct)
        return AlertDecision(False, now_price, rearmed, False,
                             "rearmed" if rearmed else "latched")

    # A CROSSING: previously on one side, now on the other. A gap open (prev is
    # yesterday's close, price is today's open) is a genuine crossing and fires — which
    # is exactly right, and is why the rule is not "price crossed during this tick".
    if kind == KIND_ABOVE:
        crossed = prev < limit <= now_price
    else:
        crossed = prev > limit >= now_price

    if crossed:
        return AlertDecision(
            fire=True,
            new_last_price=now_price,
            new_armed=False,               # latch, so the oscillation cannot re-fire
            deactivate=(repeat_mode == REPEAT_ONCE),
            reason=f"crossed:{kind}",
        )

    return AlertDecision(False, now_price, True, False, "no_crossing")
