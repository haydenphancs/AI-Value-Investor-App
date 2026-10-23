"""
ONE send pacer and ONE flood back-off for every sender to the owner's review chat (plan 2026-10-22, §1.9).

Why one: Telegram's limit is per CHAT, not per caller — the Bot API FAQ: "In a single chat, avoid sending
more than one message per second" — and its flood control (a 429 with `retry_after`) closes the chat for
every sender at once. Four sweeps send to that one chat from the web process: the marketing review sweep
(`review_service.review_cycle`), the publish feed (`publish_feed.feed_cycle`), the run-health / weekly digest
reports (`digest_service`) and the Learn weekly review (`app/services/learn_weekly/`, wave B). Until
2026-10-22 each built its OWN `_Pacer`, so the feed's first message could follow the review sweep's last one
at once, and a Learn sweep running beside them would not have been spaced at all. The flood back-off was
already one module global (`review_service._rate_limited_until`) read by all three; it lives here now, and
`review_service` FORWARDS that old name (a property on its module class), so every existing reader, writer
and test of `review_service._rate_limited_until` / `review_service.SEND_SPACING_SECONDS` keeps working and
acts on THIS state.

State, and what it is shared across:
  * `SEND_SPACING_SECONDS` — read at CALL time (a test may zero it; a junk value is logged at ERROR and the
    default spacing is used — sending faster than Telegram allows is the failure, never waiting longer).
  * the flood back-off — a `time.monotonic()` instant before which nobody sends: PROCESS-global, exactly
    what `review_service._rate_limited_until` was.
  * the last-send stamp and the `asyncio.Lock` that serialises `wait_turn` — one per RUNNING EVENT LOOP. The
    web process (one uvicorn worker, `tests/test_deploy_command_parity.py`) runs one loop for its whole life,
    so in production this is exactly one pacer. Per loop because an `asyncio.Lock` binds to the first loop
    that contends for it and raises in any other, and a stamp from a loop that no longer runs (a test's,
    a script's `asyncio.run`) is not a send this loop must wait for. The state holds a STRONG reference to
    its loop (compared by identity), so a new loop can never be mistaken for the old one by a reused id.

Semantics a single sweep sees — unchanged from the per-sweep `_Pacer` it replaces: its first send goes at
once (unless another sender's last send was under SEND_SPACING_SECONDS ago — the point of sharing), every
later send waits out SEND_SPACING_SECONDS since the previous one. New, and only visible when two senders
run together: `wait_turn` raises `TelegramFloodControlActive` (a `TelegramRateLimitException`) when the
back-off is in force — checked before and again after the wait — so a 429 in one sweep stops the other at
its next send, through the `except TelegramRateLimitException` every sweep already has. A sweep checks the
back-off itself before its first read too (`is_rate_limited`), as it always did, so no claim or ledger read
is spent while the chat is closed.

Pure apart from the integration's exception types: no Supabase, no settings, no HTTP.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Optional

from app.integrations.telegram import TelegramFloodControlActive

logger = logging.getLogger(__name__)

#: Telegram FAQ: "In a single chat, avoid sending more than one message per second" (+ 0.1 s margin).
DEFAULT_SEND_SPACING_SECONDS = 1.1
#: Read at call time by `wait_turn`. `review_service.SEND_SPACING_SECONDS` forwards here (get and set).
SEND_SPACING_SECONDS: float = DEFAULT_SEND_SPACING_SECONDS
#: The back-off a 429 sets when Telegram named no `retry_after` (the sweeps' long-standing default).
DEFAULT_RETRY_AFTER_SECONDS = 60
#: Ceiling on one back-off — the integration's `_retry_after` caps Telegram's value at a day too.
MAX_RETRY_AFTER_SECONDS = 86_400

#: The flood back-off: `time.monotonic()` before which nobody sends to the review chat. 0.0 = none.
_rate_limited_until: float = 0.0


class _LoopState:
    """The pacer of ONE event loop: its lock and the monotonic stamp of the last send it released."""

    __slots__ = ("loop", "lock", "last_send")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop                    # strong reference, compared by identity (module docstring)
        self.lock = asyncio.Lock()
        self.last_send: Optional[float] = None


_STATE: Optional[_LoopState] = None


# ── the flood back-off ─────────────────────────────────────────────────────────────────────────


def _is_real(value: Any) -> bool:
    """A real number — `True` is an int to Python and is refused, like a string."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def rate_limited_until() -> float:
    """The monotonic instant the back-off ends (0.0 / a past instant = none in force)."""
    return _rate_limited_until


def set_rate_limited_until(value: float) -> None:
    """OVERWRITE the back-off — the old module global's plain-assignment semantics, which the sweeps and
    the tests rely on (a test resets it to 0.0; `review_service._rate_limited_until = x` lands here).
    New code that reacts to a 429 calls `note_rate_limit` instead, which never shortens a back-off.

    A bool, a non-number, NaN or ±inf is refused LOUDLY (TypeError / ValueError): NaN would compare False
    against every clock reading and silently disable the back-off; +inf would close the chat forever."""
    global _rate_limited_until
    if not _is_real(value):
        raise TypeError(f"telegram pacing: the flood back-off must be a monotonic instant, got "
                        f"{type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"telegram pacing: the flood back-off must be finite, got {value!r}")
    _rate_limited_until = float(value)


def seconds_left(now: Optional[float] = None) -> float:
    """Seconds until the back-off ends; 0.0 when none is in force."""
    current = time.monotonic() if now is None else now
    return max(0.0, _rate_limited_until - current)


def is_rate_limited(now: Optional[float] = None) -> bool:
    """True while a 429's back-off is in force: send nothing to the review chat, claim nothing."""
    return seconds_left(now) > 0.0


def note_rate_limit(retry_after: Any) -> float:
    """Record a 429 (Telegram's `retry_after`, seconds; None = it named none → DEFAULT_RETRY_AFTER_SECONDS):
    the back-off becomes `now + retry_after`, but NEVER ends earlier than one already in force — a short
    answer to one sender must not reopen a chat another sender's 429 closed for longer. Returns the
    seconds actually recorded from now.

    `retry_after` comes typed from the integration (an int 0..86,400 or None); anything else (a bool, a
    string, NaN, a negative number) is logged at WARNING and replaced by the default — waiting a minute is
    harmless, sending into flood control prolongs it. A finite value over a day is capped at a day."""
    if retry_after is None:
        wait = float(DEFAULT_RETRY_AFTER_SECONDS)
    elif not _is_real(retry_after) or not math.isfinite(retry_after) or retry_after < 0:
        logger.warning("telegram pacing: unusable retry_after %r (%s) — backing off the default %d s",
                       retry_after, type(retry_after).__name__, DEFAULT_RETRY_AFTER_SECONDS)
        wait = float(DEFAULT_RETRY_AFTER_SECONDS)
    else:
        wait = float(min(retry_after, MAX_RETRY_AFTER_SECONDS))
    now = time.monotonic()
    set_rate_limited_until(max(_rate_limited_until, now + wait))
    return _rate_limited_until - now


# ── the pacer ──────────────────────────────────────────────────────────────────────────────────


def _spacing() -> float:
    """SEND_SPACING_SECONDS, read now. A junk value (set straight on this module, bypassing the
    review_service setter's check) is an ERROR, and the DEFAULT spacing is used — never no spacing."""
    value = SEND_SPACING_SECONDS
    if _is_real(value) and math.isfinite(value) and value >= 0:
        return float(value)
    logger.error("telegram pacing: SEND_SPACING_SECONDS is unusable (%r, %s) — spacing sends %.1f s apart",
                 value, type(value).__name__, DEFAULT_SEND_SPACING_SECONDS)
    return DEFAULT_SEND_SPACING_SECONDS


def set_send_spacing(value: float) -> None:
    """Set SEND_SPACING_SECONDS (the forwarding target of `review_service.SEND_SPACING_SECONDS = x`).
    Refuses a bool / non-number (TypeError) and a negative, NaN or infinite value (ValueError)."""
    global SEND_SPACING_SECONDS
    if not _is_real(value):
        raise TypeError(f"telegram pacing: SEND_SPACING_SECONDS must be a number, got {type(value).__name__}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"telegram pacing: SEND_SPACING_SECONDS must be finite and >= 0, got {value!r}")
    SEND_SPACING_SECONDS = value


def _loop_state() -> _LoopState:
    """This loop's pacer, created on first use (module docstring: why per loop)."""
    global _STATE
    loop = asyncio.get_running_loop()
    state = _STATE
    if state is None or state.loop is not loop:
        state = _STATE = _LoopState(loop)
    return state


def _refuse_while_backing_off() -> None:
    left = seconds_left()
    if left > 0.0:
        wait = math.ceil(left)
        raise TelegramFloodControlActive(
            f"telegram: not sent — flood control from an earlier 429 is still in force for {wait} s",
            method="paced_send", retry_after=wait,
        )


async def wait_turn() -> None:
    """Wait for this chat's next send slot, then take it: SEND_SPACING_SECONDS after the previous send
    of ANY sender in this process (this loop). Callers send right after it returns.

    Raises `TelegramFloodControlActive` (a `TelegramRateLimitException`, so every sweep's existing 429
    handling stops it) when the flood back-off is in force — checked on entering and again after the
    wait, because another sender's 429 can land while this one waits. The lock is held across the wait,
    so two concurrent senders queue instead of both waking at once. A cancelled wait takes no slot."""
    state = _loop_state()
    async with state.lock:
        _refuse_while_backing_off()
        if state.last_send is not None:
            delay = _spacing() - (time.monotonic() - state.last_send)
            if delay > 0:
                await asyncio.sleep(delay)
                _refuse_while_backing_off()
        state.last_send = time.monotonic()


class SharedPacer:
    """A handle on the ONE pacer: every instance waits on the same state (`wait_turn`). It keeps the
    `.wait()` shape of the per-sweep `_Pacer` it replaces (`review_service._Pacer` is this class), so a
    sweep — or a test's recording stand-in — passes a pacer around exactly as before."""

    __slots__ = ()

    async def wait(self) -> None:
        await wait_turn()


#: The handle the sweeps use (`review_service`, `publish_feed`, `digest_service._PACER`, the Learn sweep).
PACER = SharedPacer()
