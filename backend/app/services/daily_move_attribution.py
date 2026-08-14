"""
Why did this stock move TODAY — answered from structured data, not from a model.

PURE MODULE — no network, no Supabase, no Gemini, no wall-clock reads except through
injected values. Same posture as `updates_materiality` and `price_volatility`, and for
the same reason: every branch here is a deterministic function of its arguments, so the
whole answer set is exhaustively testable without a fixture or a network.

WHY THIS EXISTS
---------------
The widget asks one question — "which stock moved most today, and why" — and the
existing machinery answers a different one. `price_catalyst_service` is a Gemini
web-search over an arbitrary *window label*, and in practice the report pipeline's
multi-day windows dominate it. Measured 2026-08-14, the only cached ACHR row read:

    tag "Boeing Acquisition & Partnership" · window "Last 15 Days" · +42.7%
    "Archer Aviation's stock surged following the announcement…"

Printing that under a red −5.02% is not a wrong-signed answer. It is a *correct answer
to a different question*, and no threshold, sign guard or prompt tweak turns a 15-day
rally narrative into a daily explanation. Window is the filter that matters, not sign.

THE INSIGHT
-----------
"Why did it move today" has a small, enumerable answer set, and almost none of it needs
a language model:

    earnings today · an analyst acted today · classifiable company news ·
    its industry moved · the market moved · it gapped at the open · nothing

Each is a fact with a date attached. Detection is arithmetic and string matching over
data the app already pays for, so the output cannot hallucinate and is daily by
construction. An LLM, if ever added, would only *phrase* what is established here.

PRECEDENCE
----------
Ordered most-specific first, because a stock that reported earnings AND drifted with its
sector moved on the earnings. `attribute()` returns the single best cause plus the
always-true context (σ multiple, industry delta, gap split), so a caller can render a
headline and a supporting line without re-deriving anything.

WHAT THIS DELIBERATELY WILL NOT DO
----------------------------------
Manufacture a cause. On most days for most tickers the honest answer is
`CauseKind.NONE` — measured against the live corpus, ACHR fell 5% with seven articles in
48h, all bullish, the most recent published hours before the drop. The insight prompt
was *told* about the drop and correctly declined to invent a reason. Returning "no
identifiable cause, and here is how unusual the move was" is the useful answer there,
not a failure to find one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ── Tuning ────────────────────────────────────────────────────────────

# A move counts as "in line with its industry" when it lands within this multiple of the
# industry's own move. Wide on purpose: the claim is "this went with its group", not a
# regression. 0.6-1.7x of a −2% industry day is −1.2% to −3.4%, which is the band a
# reader would call "moved with the sector" without quibbling.
_SECTOR_INLINE_LOW = 0.6
_SECTOR_INLINE_HIGH = 1.7

# Below this the group's own move is noise and cannot explain anything, however well the
# ratio happens to line up. A stock down 0.3% "in line with" an industry down 0.2% is not
# an explanation, it is a coincidence of small numbers.
_MIN_GROUP_MOVE_PCT = 0.75

# A gap is only worth calling out when it carries most of the day's move. Below this the
# stock opened roughly flat and did its work during the session.
_GAP_DOMINANT_SHARE = 0.6
# …and the gap itself has to be big enough to notice.
_MIN_GAP_PCT = 1.0

# Earnings land the evening before or the morning of. Anything older is not today's news.
_EARNINGS_MAX_AGE_DAYS = 1
# An analyst action is "today's" only on the session date itself. A three-month-old
# rating is not why the stock moved this morning — ACHR's most recent action was
# 2026-05-12 while it fell 5% on 08-14, and treating that as causal would be a lie.
_GRADE_MAX_AGE_DAYS = 1


class CauseKind(str, Enum):
    """What we were able to establish. Ordered most specific to least."""

    EARNINGS = "earnings"          # reported today or last night
    ANALYST = "analyst"            # an upgrade/downgrade dated today
    COMPANY_NEWS = "company_news"  # a classifiable catalyst in today's headlines
    SECTOR = "sector"              # moved in line with its industry
    MARKET = "market"              # moved in line with the whole tape
    NONE = "none"                  # nothing identifiable — the honest common case


@dataclass(frozen=True)
class MoveContext:
    """Always-true arithmetic about the move. Never absent, never a guess."""

    change_percent: float
    # |move| / σ_daily. None when σ is unknown — NOT 0.0, which would read as
    # "judged, and perfectly normal" for a ticker we cannot judge at all.
    z: Optional[float] = None
    # The overnight half of the move, when both `open` and `previousClose` were usable.
    gap_percent: Optional[float] = None
    intraday_percent: Optional[float] = None
    # True when the gap carried most of the day — i.e. it moved before anyone could trade.
    gap_dominant: bool = False
    industry_name: Optional[str] = None
    industry_change_percent: Optional[float] = None
    market_change_percent: Optional[float] = None


@dataclass(frozen=True)
class Attribution:
    kind: CauseKind
    # 2-4 word label for the badge ("Earnings Beat", "Analyst Downgrade"). None for NONE.
    tag: Optional[str] = None
    # One punchy sentence. Always non-empty — the NONE case says so explicitly.
    detail: str = ""
    context: Optional[MoveContext] = None
    # Everything we checked and ruled out, for debuggability. Never rendered.
    considered: List[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────


def _finite(value: Any) -> Optional[float]:
    """A usable float, or None. NaN and ±inf are not usable."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_day(value: Any) -> Optional[date]:
    """Leading `YYYY-MM-DD` of a date or datetime string."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _pct(value: float) -> str:
    """1 decimal, matching the app's prose convention (labels use 2 — see
    `PriceChangeLabel`; prose uses 1 — see `deterministic_reason`)."""
    return f"{abs(value):.1f}%"


def _dir_word(value: float) -> str:
    return "rose" if value > 0 else "fell"


def compute_gap(
    open_price: Any, previous_close: Any, change_percent: Any
) -> tuple[Optional[float], Optional[float], bool]:
    """Split today's move into its overnight and intraday halves.

    `open` and `previousClose` are both already on the batch-quote row the sweeper
    fetches every five minutes, and nothing else in the repo reads `open` — so this
    costs nothing and is pure arithmetic, which means it cannot be wrong the way a
    model can be wrong.

    It matters because a gap-down happened while nobody could trade: the cause is
    overnight news, an earnings print, or an analyst note published before the bell. A
    stock that opened flat and slid from 14:00 has a different kind of cause entirely.

    Returns `(gap_pct, intraday_pct, gap_dominant)`; the first two are None when either
    price is unusable.
    """
    op = _finite(open_price)
    pc = _finite(previous_close)
    total = _finite(change_percent)
    if op is None or pc is None or pc <= 0 or total is None:
        return None, None, False

    gap = (op - pc) / pc * 100.0
    intraday = total - gap
    dominant = (
        abs(gap) >= _MIN_GAP_PCT
        and abs(total) > 0
        and abs(gap) / abs(total) >= _GAP_DOMINANT_SHARE
        # Same direction: a +3% gap that faded to −1% is not "the gap explains it".
        and (gap > 0) == (total > 0)
    )
    return gap, intraday, dominant


def _moved_with_group(change: float, group_change: Optional[float]) -> bool:
    """Did this stock go where its group went, by roughly the same amount?

    Requires the same direction, a group move big enough to mean something, and a ratio
    inside the in-line band. ACHR is the worked example of the rejection: Aerospace &
    Defense fell 1.2% and ACHR fell 5.0%, a 4.1x ratio — it did NOT move with its
    industry, it massively underperformed it, and saying "sector selloff" there would be
    false.
    """
    g = _finite(group_change)
    if g is None or abs(g) < _MIN_GROUP_MOVE_PCT:
        return False
    if (g > 0) != (change > 0):
        return False
    ratio = abs(change) / abs(g)
    return _SECTOR_INLINE_LOW <= ratio <= _SECTOR_INLINE_HIGH


# ── Individual detectors (each returns an Attribution or None) ────────


def detect_earnings(
    ticker: str, change: float, earnings_row: Optional[Dict[str, Any]], today: date
) -> Optional[Attribution]:
    """Reported today or last night.

    The row comes from the market-wide `earnings-calendar` — ONE FMP call covering every
    ticker, which the earnings notification sender already makes daily and then throws
    away.
    """
    if not isinstance(earnings_row, dict):
        return None
    when = _parse_day(earnings_row.get("date"))
    if when is None or (today - when).days > _EARNINGS_MAX_AGE_DAYS or when > today:
        return None

    actual = _finite(earnings_row.get("epsActual"))
    estimate = _finite(earnings_row.get("epsEstimated"))
    beat: Optional[bool] = None
    surprise: Optional[float] = None
    if actual is not None and estimate is not None and abs(estimate) > 1e-9:
        surprise = (actual - estimate) / abs(estimate) * 100.0
        beat = actual >= estimate

    when_word = "this morning" if when == today else "after yesterday's close"
    if beat is None:
        tag = "Earnings"
        detail = f"{ticker} reported earnings {when_word}."
    elif beat:
        tag = "Earnings Beat"
        detail = f"{ticker} beat EPS estimates by {_pct(surprise or 0)}, reported {when_word}."
    else:
        tag = "Earnings Miss"
        detail = f"{ticker} missed EPS estimates by {_pct(surprise or 0)}, reported {when_word}."
    return Attribution(kind=CauseKind.EARNINGS, tag=tag, detail=detail)


def detect_analyst_action(
    ticker: str, grade_rows: Optional[Sequence[Dict[str, Any]]], today: date
) -> Optional[Attribution]:
    """An upgrade / downgrade dated today.

    Age-gated hard. A stale rating is the single easiest way to produce a confident lie:
    ACHR's most recent action was 2026-05-12, three months before the 5% drop this is
    meant to explain.
    """
    for row in grade_rows or []:
        if not isinstance(row, dict):
            continue
        when = _parse_day(row.get("date") or row.get("publishedDate"))
        if when is None or (today - when).days >= _GRADE_MAX_AGE_DAYS or when > today:
            continue

        firm = str(row.get("gradingCompany") or "").strip()
        action = str(row.get("action") or "").strip().lower()
        new_grade = str(row.get("newGrade") or "").strip()
        prev = str(row.get("previousGrade") or "").strip()

        # "maintain" is not why a stock moved 5% — it is the absence of a change.
        if action in {"maintain", "maintains", "reiterate", "reiterates", "hold"}:
            continue

        if "downgrade" in action:
            tag = "Analyst Downgrade"
            verb = "downgraded"
        elif "upgrade" in action:
            tag = "Analyst Upgrade"
            verb = "upgraded"
        elif "initiat" in action:
            tag = "New Coverage"
            verb = "initiated coverage on"
        else:
            continue

        who = firm or "An analyst"
        detail = f"{who} {verb} {ticker}"
        if new_grade:
            detail += f" to {new_grade}"
            if prev and prev.lower() != new_grade.lower():
                detail += f" from {prev}"
        detail += " today."
        return Attribution(kind=CauseKind.ANALYST, tag=tag, detail=detail)
    return None


def detect_company_news(
    ticker: str,
    classified: Optional[Sequence[tuple[str, str]]],
) -> Optional[Attribution]:
    """A classifiable catalyst among TODAY's headlines.

    `classified` is `[(tag, headline)]` — the caller runs the existing
    `_classify_news_catalyst` over today's `ticker_news_cache` rows, so the eleven-tag
    vocabulary (FDA Approval, M&A, Guidance Cut, …) stays in one place.
    """
    for entry in classified or []:
        try:
            tag, headline = entry
        except (TypeError, ValueError):
            continue
        tag = str(tag or "").strip()
        headline = str(headline or "").strip()
        if not tag or not headline:
            continue
        return Attribution(
            kind=CauseKind.COMPANY_NEWS, tag=tag, detail=headline.rstrip(".") + "."
        )
    return None


def detect_group_move(
    ticker: str,
    change: float,
    industry_name: Optional[str],
    industry_change: Optional[float],
    market_change: Optional[float],
) -> Optional[Attribution]:
    """It went where its industry, or the whole tape, went."""
    if _moved_with_group(change, industry_change):
        name = (industry_name or "its industry").strip()
        return Attribution(
            kind=CauseKind.SECTOR,
            tag="Sector Move",
            detail=(
                f"{name} {_dir_word(industry_change or 0)} "
                f"{_pct(industry_change or 0)} today; {ticker} moved with it."
            ),
        )
    if _moved_with_group(change, market_change):
        return Attribution(
            kind=CauseKind.MARKET,
            tag="Market Move",
            detail=(
                f"The market {_dir_word(market_change or 0)} "
                f"{_pct(market_change or 0)} today; {ticker} moved with it."
            ),
        )
    return None


def describe_no_cause(
    ticker: str, ctx: MoveContext, had_news: bool
) -> str:
    """The honest answer, and the most common one.

    Says the most informative TRUE things available instead of apologising: how unusual
    the move was for this particular stock, and how it compares with its industry. For
    ACHR that reads "Aerospace & Defense fell 1.2%; ACHR fell far more" — which is
    genuinely useful, and is exactly what a generic PR headline failed to convey.
    """
    parts: List[str] = []

    ind = _finite(ctx.industry_change_percent)
    if ind is not None and ctx.industry_name and abs(ind) >= 0.1:
        same_dir = (ind > 0) == (ctx.change_percent > 0)
        if same_dir and abs(ctx.change_percent) > abs(ind) * _SECTOR_INLINE_HIGH:
            parts.append(
                f"{ctx.industry_name} {_dir_word(ind)} {_pct(ind)}; "
                f"{ticker} moved far more."
            )
        elif not same_dir:
            parts.append(
                f"{ctx.industry_name} {_dir_word(ind)} {_pct(ind)}; "
                f"{ticker} went the other way."
            )

    if ctx.gap_dominant and ctx.gap_percent is not None:
        parts.append(f"Most of it was an overnight gap at the open.")

    parts.append("No company news today." if not had_news else "No clear catalyst in today's news.")
    return " ".join(parts)


# ── The entry point ───────────────────────────────────────────────────


def attribute(
    *,
    ticker: str,
    change_percent: Any,
    today: date,
    sigma_daily: Any = None,
    z: Optional[float] = None,
    open_price: Any = None,
    previous_close: Any = None,
    industry_name: Optional[str] = None,
    industry_change_percent: Any = None,
    market_change_percent: Any = None,
    earnings_row: Optional[Dict[str, Any]] = None,
    grade_rows: Optional[Sequence[Dict[str, Any]]] = None,
    classified_news: Optional[Sequence[tuple[str, str]]] = None,
    had_news: bool = False,
) -> Optional[Attribution]:
    """Best available same-day explanation, plus always-true context.

    Returns None only when the move itself is unreadable — an unusable quote is not a
    flat day and must not be presented as one. Every other path returns an Attribution,
    including `CauseKind.NONE`.
    """
    change = _finite(change_percent)
    if change is None:
        return None

    if z is None:
        # Lazily importing keeps this module free of a services-layer dependency at
        # import time; `move_z` is itself pure.
        from app.services.updates_materiality import move_z

        z = move_z(change, sigma_daily)

    gap, intraday, gap_dominant = compute_gap(open_price, previous_close, change)
    ctx = MoveContext(
        change_percent=change,
        z=z,
        gap_percent=gap,
        intraday_percent=intraday,
        gap_dominant=gap_dominant,
        industry_name=(industry_name or None),
        industry_change_percent=_finite(industry_change_percent),
        market_change_percent=_finite(market_change_percent),
    )

    considered: List[str] = []
    for name, detector in (
        ("earnings", lambda: detect_earnings(ticker, change, earnings_row, today)),
        ("analyst", lambda: detect_analyst_action(ticker, grade_rows, today)),
        ("company_news", lambda: detect_company_news(ticker, classified_news)),
        (
            "group",
            lambda: detect_group_move(
                ticker,
                change,
                ctx.industry_name,
                ctx.industry_change_percent,
                ctx.market_change_percent,
            ),
        ),
    ):
        try:
            hit = detector()
        except Exception as e:  # a malformed upstream row must never break the widget
            logger.warning(
                "daily_move_attribution: %s detector raised for %s (%s: %s)",
                name, ticker, type(e).__name__, e,
            )
            hit = None
        if hit is not None:
            return Attribution(
                kind=hit.kind,
                tag=hit.tag,
                detail=hit.detail,
                context=ctx,
                considered=considered,
            )
        considered.append(name)

    return Attribution(
        kind=CauseKind.NONE,
        tag=None,
        detail=describe_no_cause(ticker, ctx, had_news),
        context=ctx,
        considered=considered,
    )


__all__ = [
    "CauseKind",
    "MoveContext",
    "Attribution",
    "attribute",
    "compute_gap",
    "detect_earnings",
    "detect_analyst_action",
    "detect_company_news",
    "detect_group_move",
    "describe_no_cause",
]
