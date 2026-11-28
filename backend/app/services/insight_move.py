"""Move cards: the ONE contract between the Insights gate, the card writer and the jump search.

WHY (2026-11-27). When a watched stock moved sharply, its Updates Insights card did not say why:
CRWV +22% on 2026-11-23 (Nvidia reinstated its CEO, the whole AI group rose) got a Neutral card
about its backlog, and PLUG +15% on 2026-11-25 (a reversal from −10%) got a Bearish card about the
morning's slide. The fix gives a big mover's card a MOVE MODE: the writer states the cause (company
news, or a market story the code has confirmed) or honestly says that no news explains the move
yet, and the gate holds such a card against a later plain regeneration that would replace it with
a general summary. Owner decisions (2026-11-27): "Say so honestly", "Yes, when code confirms it",
"Also count 10% or more", "Yes, judge the cause".

This module is stdlib-only on purpose: `updates_materiality` (the gate), `updates_insight_sweeper`,
`news_insight_service` (the writer) and `jump_web_search_service` all import it, and none of them
may import another just for these names (import cycles; the materiality module is pure).

THE TAG. A move card's stored `trigger_reason` LEADS with ``move:<direction>:<kind>``, for example
``move:up:company + new_articles (9) + web (3)``. It leads so the 200-character clip never cuts it,
and it is written AFTER every other prefixing step (``web_only + …``) so nothing pushes it back.
The sweeper copies the same string into ``updates_insight_state.last_trigger_reason``.

KINDS, ranked: ``company`` (3, a company development the judge found in the sources) > ``market``
(2, a market story the code confirmed) > ``none`` (1, the honest no-cause card) > an untagged,
general card (0). On the same ET day and in the same direction a card is never replaced by a
lower kind (`is_move_downgrade`); a reversal or a new day always writes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
MOVE_DIRECTIONS: Tuple[str, ...] = (DIRECTION_UP, DIRECTION_DOWN)

KIND_COMPANY = "company"
KIND_MARKET = "market"
KIND_NONE = "none"
MOVE_KINDS: Tuple[str, ...] = (KIND_COMPANY, KIND_MARKET, KIND_NONE)
_RANK = {KIND_NONE: 1, KIND_MARKET: 2, KIND_COMPANY: 3}

MOVE_TAG_PREFIX = "move:"
_TAG_JOINER = " + "
# Leading only; the kind must end at a space, a " + " joiner or the end of the string.
_TAG_RE = re.compile(r"\Amove:(up|down):(company|market|none)(?=\Z|\s)")
# A stored generation time more than this far in the future is a bad clock, never "today".
_FUTURE_SKEW = timedelta(minutes=5)

# Why a scope is in move mode (logs and prompt wording only).
BASIS_SIGMA = "sigma"         # Unusual / Extreme against the stock's own volatility
BASIS_FLOOR = "floor"         # the ≥10% session floor (owner 2026-11-27)
BASIS_BAND = "band"           # the fixed-band fallback (no σ), coins included
BASIS_REVERSAL = "reversal"   # a held move card whose stock turned the other way
BASIS_HELD = "held"           # a held move card whose stock is still moving (≥ Notable)
MOVE_BASES: Tuple[str, ...] = (BASIS_SIGMA, BASIS_FLOOR, BASIS_BAND, BASIS_REVERSAL, BASIS_HELD)

# How the code confirmed that a move was market- or sector-wide (owner decision 2).
MARKET_BASIS_INDEX = "index"      # the S&P 500 (SPY) moved the same way, materially
MARKET_BASIS_BREADTH = "breadth"  # many other watched stocks jumped the same way
MARKET_BASES: Tuple[str, ...] = (MARKET_BASIS_INDEX, MARKET_BASIS_BREADTH)

# The writer's verdict on one move attempt, read by the jump search (Claude trigger 3) and the gate.
VERDICT_SUPPORTED = "supported"      # a company or market cause was written, judged supported
VERDICT_UNSUPPORTED = "unsupported"  # a cause was proposed and refused (judge, refs, lead, …)
VERDICT_NO_CAUSE = "no_cause"        # the sources explain nothing (the model said none, recaps only)
MOVE_VERDICTS: Tuple[str, ...] = (VERDICT_SUPPORTED, VERDICT_UNSUPPORTED, VERDICT_NO_CAUSE)

# The failure-reason prefix of an attempt whose card was NOT written because the stored card is a
# better move card for the same day and direction (`is_move_downgrade`). The sweeper records it as a
# failure (it moves the evidence floor) and re-stamps a kept company/market card as current.
MOVE_KEPT = "move_kept"


@dataclass(frozen=True)
class MoveTag:
    """A parsed move tag: what kind of move card is stored, for which direction."""

    kind: str
    direction: str


def format_move_tag(direction: Any, kind: Any) -> Optional[str]:
    """``move:<direction>:<kind>``, or None for anything outside the closed vocabularies."""
    if direction not in MOVE_DIRECTIONS or kind not in MOVE_KINDS:
        return None
    return f"{MOVE_TAG_PREFIX}{direction}:{kind}"


def parse_move_tag(reason: Any) -> Optional[MoveTag]:
    """The LEADING move tag of a stored reason, or None. Strict and never raises: a tag anywhere but
    the start (``web_only + move:…``), an unknown kind or direction, or a non-string is None."""
    if not isinstance(reason, str):
        return None
    m = _TAG_RE.match(reason)
    if m is None:
        return None
    return MoveTag(kind=m.group(2), direction=m.group(1))


def strip_move_tag(reason: Any) -> str:
    """The reason without its leading move tag (and that tag's joiner); "" for a non-string."""
    if not isinstance(reason, str):
        return ""
    m = _TAG_RE.match(reason)
    if m is None:
        return reason
    rest = reason[m.end():]
    if rest.startswith(_TAG_JOINER):
        rest = rest[len(_TAG_JOINER):]
    return rest.lstrip()


def with_move_tag(reason: Any, tag: Optional[str]) -> str:
    """`reason` led by `tag` (replacing a leading tag it already carries). A None or invalid tag
    returns the reason without any leading tag, so a non-move write never inherits a stale one."""
    rest = strip_move_tag(reason)
    if tag is None or parse_move_tag(tag) is None:
        return rest
    return f"{tag}{_TAG_JOINER}{rest}" if rest else tag


def move_rank(kind: Any) -> int:
    """company 3 > market 2 > none 1 > anything else (a general card, junk) 0. Never raises."""
    if not isinstance(kind, str):
        return 0
    return _RANK.get(kind, 0)


def _aware(when: Any) -> Optional[datetime]:
    if isinstance(when, datetime):
        return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
    if isinstance(when, str) and when.strip():
        try:
            parsed = datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def et_date(when: Any) -> Optional[date]:
    """The ET calendar date of a datetime or ISO string; None when unreadable."""
    aware = _aware(when)
    return aware.astimezone(ET).date() if aware is not None else None


def held_move(reason: Any, generated_at: Any, now: datetime) -> Optional[MoveTag]:
    """The move card a scope HOLDS right now: a valid leading tag on a card written on the same ET
    day as `now`. None for another day, an unreadable or future (beyond clock skew) stamp, or no tag.
    Never raises."""
    tag = parse_move_tag(reason)
    if tag is None:
        return None
    written = _aware(generated_at)
    current = _aware(now)
    if written is None or current is None or written > current + _FUTURE_SKEW:
        return None
    if et_date(written) != et_date(current):
        return None
    return tag


def is_move_downgrade(held: Optional[MoveTag], kind: Any, direction: Any) -> bool:
    """True when writing a card of `kind` (None = a general, untagged card) in `direction` would
    replace a held move card of a HIGHER kind for the SAME direction. `held` must already be
    restricted to the current ET day (`held_move`). A different direction (a reversal) is never a
    downgrade, and neither is the same or a higher kind."""
    if held is None or held.kind not in MOVE_KINDS:
        return False
    if direction != held.direction:
        return False
    return move_rank(kind) < move_rank(held.kind)


def market_card_lost(held: Any, direction: Any, index_against: Any) -> bool:
    """True when `held` is a MARKET card for `direction` and the index now moves the other way
    (`MoveContext.index_against`): its "moves with the broader market" sentence no longer
    stands, so the writer lets a lower kind (the honest no-cause card) replace it and never
    reports the attempt as a kept card (which the sweeper would re-stamp as current). Never
    raises; anything but a real True `index_against` is False."""
    return (
        isinstance(held, MoveTag) and held.kind == KIND_MARKET and held.direction == direction
        and index_against is True
    )


def direction_of(change_percent: Any) -> Optional[str]:
    """"up" / "down" for a finite, non-zero (to 2 decimals) change; None otherwise (bools too)."""
    if isinstance(change_percent, bool) or not isinstance(change_percent, (int, float)):
        return None
    try:
        value = float(change_percent)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(value) or round(value, 2) == 0.0:
        return None
    return DIRECTION_UP if value > 0 else DIRECTION_DOWN


@dataclass(frozen=True)
class MarketContext:
    """A move the CODE confirmed as market- or sector-wide (owner decision 2), handed to the writer.

    `items` are licensed FMP general-news rows (the market scope's own corpus: no web result, ever),
    at most 5, newest first, filler and price-only rows already dropped; they are the only market
    text the writer may cite (as ``[M i]``) and the judge checks. `card_headline` is the stored
    market card's headline, given to the model as context only and never citable (it is our own
    model text, so judging a cause against it would prove nothing). A market STORY may be named
    only on the index basis (`story_allowed`); a breadth-only confirmation gives the confirmed
    no-cause wording, because general news cannot be tied to a group of stocks.
    """

    basis: str
    items: Tuple[Mapping[str, Any], ...] = ()
    card_headline: Optional[str] = None

    def __post_init__(self) -> None:
        if self.basis not in MARKET_BASES:
            raise ValueError(f"unknown market basis {self.basis!r}")

    @property
    def story_allowed(self) -> bool:
        return self.basis == MARKET_BASIS_INDEX and bool(self.items)


@dataclass(frozen=True)
class MoveContext:
    """Everything the writer needs to write a move card for one scope in one attempt.

    `direction` is the live sign of the move. `market` is present ONLY when the code confirmed the
    move (equities only). `recaps` are up to 3 price-recap rows the move corpus dropped (they say
    THAT the stock moved, not why). `held` is the move card the scope held when the sweep started;
    the writer re-reads the stored row before any write and uses that instead when it can.

    `index_against` (review 2026-11-27): the S&P 500's session-stamped change now has the OTHER
    sign from `direction`. A market card ("{name} moves with the broader market after …") held
    for this direction then says something false, so the writer lets the honest no-cause card
    replace it (`market_card_lost`). The sweeper sets it; False — the default — changes nothing.
    SPY merely dropping under the confirmation threshold while still moving the same way is NOT
    against: the held card's sentence is still true.
    """

    direction: str
    basis: str = BASIS_SIGMA
    market: Optional[MarketContext] = None
    recaps: Tuple[Mapping[str, Any], ...] = ()
    held: Optional[MoveTag] = None
    is_crypto: bool = False
    index_against: bool = False

    def __post_init__(self) -> None:
        if self.direction not in MOVE_DIRECTIONS:
            raise ValueError(f"unknown move direction {self.direction!r}")
        if self.basis not in MOVE_BASES:
            raise ValueError(f"unknown move basis {self.basis!r}")
        if not isinstance(self.index_against, bool):
            raise ValueError(
                f"index_against must be a bool, not {type(self.index_against).__name__}"
            )


@dataclass(frozen=True)
class MoveOutcome:
    """What one move-mode attempt did, popped by the sweeper after every attempt (written, kept or
    refused): `kind` the card kind written (None when nothing was written), `verdict` the writer's
    verdict on the cause (None when no judgement could be made: a model or judge outage, a kept
    card), `why` a short code for logs and the search's Claude trigger, `page_urls` the web pages
    the attempt read (so the search can ignore a verdict about an older page set)."""

    kind: Optional[str]
    direction: Optional[str]
    verdict: Optional[str]
    why: str
    page_urls: Tuple[str, ...] = ()
    written: bool = False

    def __post_init__(self) -> None:
        if self.verdict is not None and self.verdict not in MOVE_VERDICTS:
            raise ValueError(f"unknown move verdict {self.verdict!r}")


__all__ = [
    "BASIS_BAND", "BASIS_FLOOR", "BASIS_HELD", "BASIS_REVERSAL", "BASIS_SIGMA",
    "DIRECTION_DOWN", "DIRECTION_UP", "ET", "KIND_COMPANY", "KIND_MARKET", "KIND_NONE",
    "MARKET_BASES", "MARKET_BASIS_BREADTH", "MARKET_BASIS_INDEX", "MOVE_BASES", "MOVE_DIRECTIONS",
    "MOVE_KEPT", "MOVE_KINDS", "MOVE_TAG_PREFIX", "MOVE_VERDICTS", "MarketContext", "MoveContext",
    "MoveOutcome", "MoveTag", "VERDICT_NO_CAUSE", "VERDICT_SUPPORTED", "VERDICT_UNSUPPORTED",
    "direction_of", "et_date", "format_move_tag", "held_move", "is_move_downgrade",
    "market_card_lost", "move_rank", "parse_move_tag", "strip_move_tag", "with_move_tag",
]
