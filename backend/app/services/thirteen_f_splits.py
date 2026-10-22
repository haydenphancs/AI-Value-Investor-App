"""Stock-split restatement inputs for a 13F quarter pair — shared by every 13F differ.

Extracted verbatim (2026-09-24) from ``whale_service._process_13f_path`` so the
Trillion-Dollar Club builder and the whale request path run ONE implementation. The
module docstring of ``_whale_common`` records why a second copy is not an option: three
13F diff copies drifted apart, and the annual-return formula did it a second time.
``tests/test_thirteen_f_splits_characterisation.py`` pinned the whale path's outputs on
fixtures BEFORE the move and still drives the whale request path end to end.

What it answers, per quarter pair:

* ``split_ratios`` — ``{symbol: ratio}`` for holdings whose share count jumped like a
  split AND whose derived corporate actions confirm one inside ``(prev_end, curr_end]``.
  FMP's 13F ``extract`` share counts are RAW, so a held-through 10:1 split otherwise
  fabricates a 9x "purchase".
* ``unclassified`` — symbols the magnitude backstop (``is_implausible_share_flow``) may
  suppress: an adjustment the classifier could not name (a spin-off, an out-of-range
  reverse split), or a lookup that FAILED — "could not check" is never "nothing there".
* ``lookup_failed`` — the subset that failed transiently. A snapshot built on it is
  DEGRADED: the whale path persists it without ``raw_hash`` and the club builder marks it
  ``build_status='degraded'``, so the next run re-derives instead of freezing the hole.
* ``capped`` (optional, keyword-only, filled in place — Batch 1, 2026-10-15) — the suspects
  the lookup cap (`_whale_common.MAX_SPLIT_LOOKUPS`) TRIMMED: never looked up, never armed,
  so they keep their raw share diff (the cap fails open, by design — see below). That is
  what the in-app whale and club screens have always shown, but a PUBLIC post must not
  print "reported more / fewer shares" over a split it never checked, so the marketing
  adapter drops the filing's increased / decreased moves when the set is non-empty. It is
  never a degraded state: re-running cannot check more suspects than the cap allows.

And one helper no differ calls (the review of 2026-10-16, F1): ``split_windows`` — for ANY
symbols, the split ratio and the unnameable-adjustment flag of the quarter pair's window,
through the same seam, fetch window and effective filter as the suspect lookups (so a
derivation they already made is a cache hit). The suspect filter above is a COST bound for
the app's screens: a 5:4 split at a flat price, a 3:2 split after a 15% rally or a 2:1 split
after a 54% rally moves the per-share price by less than ``SPLIT_PRICE_FACTOR`` and is never
looked up — so the marketing adapter, which must not publish "reported more shares" over a
split, re-checks every increased / decreased row of a filing itself. The whale request path
and the club builder never call it.

Splits come from ``corporate_actions_service`` (derived from the entitled adjusted vs
non-split-adjusted price series), never from FMP ``/splits`` — that endpoint is outside
the licence and answers 402.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.services._whale_common import MAX_SPLIT_LOOKUPS as _MAX_SPLIT_LOOKUPS
from app.services.corporate_actions_service import window_for_range

logger = logging.getLogger(__name__)

# Smallest per-share price move (either direction) that reads like a split: 3:2 is
# 1.5, 4:3 is 1.33, a 1:10 reverse is 0.1. See `suspicious_split_tickers`.
SPLIT_PRICE_FACTOR = 1.3


def _finite_float(value: Any, default: float = 0.0) -> float:
    """Coerce to a FINITE float; NaN / Inf / None / garbage -> ``default``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def suspicious_split_tickers(
    current_raw: Sequence[Dict], previous_raw: Sequence[Dict]
) -> List[str]:
    """Tickers whose share count jumped like a split — the only holdings worth a
    corporate-actions lookup (keeps the calls bounded).

    A split moves shares up (or down, reverse-split) by a factor while the per-share
    price moves INVERSELY by ~the same factor, so the position VALUE is roughly
    preserved. A genuine large buy/sell instead changes the value proportionally, leaving
    the price ~flat — so it won't be flagged (and even a flagged ticker is only
    *confirmed* against real split data).

    Rows are keyed by ``symbol`` (falling back to the legacy ``tickercusip``); shares
    are read from ``sharesNumber`` or ``shares``.
    """
    def _map(raw: Sequence[Dict]) -> Dict[str, Tuple[float, float]]:
        m: Dict[str, Tuple[float, float]] = {}
        for h in raw or []:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            m[sym] = (
                _finite_float(h.get("value")),
                _finite_float(h.get("sharesNumber") or h.get("shares")),
            )
        return m

    cur = _map(current_raw)
    prev = _map(previous_raw)
    # ⚠️ Flag on the implied PRICE ratio, not on "shares ≈ price". The holder's
    # share ratio is split × real trade, while the per-share price (value ÷ shares)
    # moves by the split alone — so requiring the two to agree within 35% only ever
    # caught a holder who did NOTHING through the split. One who sold >26% (or
    # bought >54%) across a 10:1 was never a suspect, never looked up, never
    # restated: half a position sold rendered as a $4.0M BOUGHT. A quarter in which
    # the per-share price moved by ≥30% either way is worth the (bounded, cached)
    # lookup; the derived data then confirms or clears it.
    #
    # Ordered strongest-first so `_MAX_SPLIT_LOOKUPS` trims the price-only tail, not
    # the rows where shares and price moved inversely together.
    strong: List[str] = []
    weak: List[str] = []
    for sym in sorted(set(cur) & set(prev)):
        cv, cs = cur[sym]
        pv, ps = prev[sym]
        if cs <= 0 or ps <= 0 or cv <= 0 or pv <= 0:
            continue
        cur_price = cv / cs
        prev_price = pv / ps
        if cur_price <= 0 or prev_price <= 0:
            continue
        price_ratio = prev_price / cur_price
        if not (price_ratio >= SPLIT_PRICE_FACTOR or price_ratio <= 1.0 / SPLIT_PRICE_FACTOR):
            continue  # the per-share price did not move like a split
        share_ratio = cs / ps
        if not (0.7 < share_ratio < 1.4) and abs(share_ratio - price_ratio) <= 0.35 * share_ratio:
            strong.append(sym)      # shares and price moved inversely together
        else:
            weak.append(sym)        # price moved like a split; shares also traded
    return strong + weak


def split_suspects_over_cap(
    current_raw: Sequence[Dict], previous_raw: Sequence[Dict]
) -> List[str]:
    """The split suspects the lookup cap TRIMS for this quarter pair (Batch 1, 2026-10-15) —
    never looked up and never armed, so their raw share diff stands: exactly the tail
    `resolve_13f_split_adjustments` adds to its ``capped`` set for the same rows (pinned
    equal by a test). Pure, zero calls: the marketing adapter re-derives it for a STORED
    Trillion Club build, which keeps no record of the cap (`BuiltFiling.split_unchecked` is
    not a column). Rows as `suspicious_split_tickers` reads them (dicts)."""
    return suspicious_split_tickers(current_raw, previous_raw)[_MAX_SPLIT_LOOKUPS:]


def split_ratio_in_window(
    splits: Optional[List[Dict]],
    start_excl: Optional[str],
    end_incl: Optional[str],
) -> float:
    """Product of FMP stock-split ratios with ``start_excl < date <= end_incl``.

    FMP ``/splits`` rows carry ``date`` + ``numerator``/``denominator`` (10/1 =
    10:1). Quarters with no split map to ``1.0``. Non-finite / non-positive
    ratios are dropped (a NaN would silently disable the restatement).
    """
    if not splits or not end_incl:
        return 1.0
    ratio = 1.0
    for s in splits:
        d = str(s.get("date") or "")[:10]
        num = s.get("numerator")
        den = s.get("denominator")
        if not d or not num or not den:
            continue
        try:
            r = float(num) / float(den)
        except (ValueError, ZeroDivisionError, TypeError):
            continue
        if not math.isfinite(r) or r <= 0:
            continue
        if (start_excl is None or start_excl < d) and d <= end_incl:
            ratio *= r
    return ratio


async def resolve_13f_split_adjustments(
    current_raw: Sequence[Dict],
    prev_raw: Sequence[Dict],
    prev_end: Optional[str],
    curr_end: str,
    *,
    actions: Any,
    log_ctx: str,
    capped: Optional[Set[str]] = None,
) -> Tuple[Dict[str, float], Set[str], Set[str]]:
    """``(split_ratios, unclassified, lookup_failed)`` for one 13F quarter pair.

    ``prev_end`` / ``curr_end`` are the two quarter-end ISO dates (``prev_end`` is
    ``None`` on a first filing). ``actions`` is the corporate-actions primitive — the
    whale path passes ``corporate_actions_source(self)`` so tests can inject a fake.
    ``log_ctx`` carries the caller's identifiers into every log line. ``capped``, when
    given, is a ``set`` the trimmed (never looked up) suspects are ADDED to — the
    return shape stays the three-tuple every caller unpacks; a non-set raises
    ``TypeError`` before any work (a programming error, never a degraded build).

    Never raises for a lookup problem: everything below fails CLOSED (arms the backstop
    and reports the ticker as failed) rather than open. See the module docstring.
    """
    if capped is not None and not isinstance(capped, set):
        raise TypeError(f"resolve_13f_split_adjustments: capped must be a set, got {type(capped).__name__}")
    # Fetch split ratios ONLY for tickers whose share count jumped like a split (value
    # ~preserved) — bounds the lookup to the rare suspicious holdings instead of every
    # position. Without this, a held-through-split position (e.g. a 10:1) fabricates a
    # huge BOUGHT trade in the diff.
    #
    # ⚠️ A spin-off moves the same adjustment factor and changes NO share count, so the
    # derivation classifies rather than just detecting: an unnameable factor comes back as
    # no split at all, and `is_implausible_share_flow` in the diff is the backstop for the
    # case it cannot name (an out-of-range reverse split).
    #
    # Best-effort refinement: a lookup failure never aborts 13F processing. It leaves
    # `split_ratios` without that ticker and arms the backstop for it.
    split_ratios: Dict[str, float] = {}
    unclassified_tickers: Set[str] = set()
    # True when the backstop was armed by a lookup that FAILED (429, outage, a degraded
    # `None` derivation) rather than by an adjustment the classifier saw and could not
    # name. The two arm the same backstop, but only the first is transient — and a
    # snapshot built on it must not be stamped final, or a "data unchanged" skip makes the
    # withheld rows permanent.
    lookup_failed_tickers: Set[str] = set()
    # Bound BEFORE the try: the fail-closed handler reads it, and the very first statement
    # inside can raise — which would turn a recoverable lookup failure into a NameError.
    suspects: List[str] = []
    # The trimmed tail (Batch 1): reported through `capped` AFTER the try, so a later batch
    # failure (which arms only the looked-up suspects) never loses it.
    overflow: List[str] = []
    try:
        suspects = suspicious_split_tickers(current_raw, prev_raw)
        if len(suspects) > _MAX_SPLIT_LOOKUPS:
            # Capped: every suspect costs two price-series fetches (a derived split reads
            # `/full` and `/non-split-adjusted`), and the whale path runs on a USER
            # REQUEST. An entire restated book — a fund that changed custodian, so every
            # position looks like a share multiple — would otherwise fan out unbounded FMP
            # calls inside one request and burn the rate-limit budget the app shares.
            #
            # Suppression fails OPEN for the overflow: those tickers get the raw diff
            # (what shipped before any of this existed), never a fabricated ratio. A
            # caller that must not publish an unchecked share change reads them back
            # through `capped` (the marketing adapter, Batch 1).
            logger.warning(
                "13F splits (%s): %d split suspects — capping lookups at %d; the "
                "remainder keep their raw share diff",
                log_ctx, len(suspects), _MAX_SPLIT_LOOKUPS,
            )
            overflow = suspects[_MAX_SPLIT_LOOKUPS:]
            suspects = suspects[:_MAX_SPLIT_LOOKUPS]
        if suspects:
            from_date, to_date = window_for_range(prev_end, curr_end)
            split_lists = await asyncio.gather(
                *[actions.get_split_rows(t, from_date, to_date) for t in suspects],
                return_exceptions=True,
            )
            # Which suspects carry an adjustment the classifier could NOT name (a
            # spin-off, or a reverse split outside its range). Only those get the
            # magnitude backstop — see `has_unclassified_adjustment`. Shares the events
            # cache with `get_split_rows`, so this costs no extra fetch.
            #
            # ⚠️ `from_date` is the FETCH window and is 10 days wider than the period
            # being diffed (`_WINDOW_LEAD_DAYS`, so a split on the range's first trading
            # day has a prior bar). The split RATIO is filtered back to
            # `prev_end < d <= curr_end` by `split_ratio_in_window`, and this gate has to
            # match or it flags on the lead: an unnameable event in the PREVIOUS
            # quarter's last 10 days — ~11% of every diff — armed the backstop for the
            # current one, deleting real 13F flow the ratio path had correctly ignored.
            flag_results = await asyncio.gather(
                *[
                    actions.has_unclassified_adjustment(
                        t, from_date, to_date,
                        effective_from=prev_end, effective_to=curr_end,
                    )
                    for t in suspects
                ],
                return_exceptions=True,
            )
            for t, flagged in zip(suspects, flag_results):
                # FAIL CLOSED on a per-ticker exception. `gather(return_exceptions=True)`
                # hands back the exception object, and `flagged is True` quietly read that
                # as "no corporate action" — "we could not check" encoded as "we checked
                # and there is nothing". The same derivation feeds `split_ratios`, so a
                # ticker that failed here usually has NO restatement either: the one state
                # where a fabricated multi-million-dollar BOUGHT reaches a written trade.
                if flagged is True or isinstance(flagged, BaseException):
                    if isinstance(flagged, BaseException):
                        logger.warning(
                            "13F splits (%s): unclassified-adjustment probe failed for %s "
                            "(%s: %s) — arming the magnitude backstop (fail-closed)",
                            log_ctx, t, type(flagged).__name__, flagged,
                        )
                        lookup_failed_tickers.add(t)
                    unclassified_tickers.add(t)

            for t, sl in zip(suspects, split_lists):
                if sl is None or isinstance(sl, BaseException):
                    # FAIL CLOSED. `None` is a degraded derivation (see `get_split_rows`);
                    # with no ratio there is no restatement, and the gate above may have
                    # SUCCEEDED on a fresh re-derive and cleared this ticker — the exact
                    # state that fabricates a split as a purchase. Arm the backstop.
                    logger.warning(
                        "13F splits (%s): split lookup failed for %s (%s) — no "
                        "restatement; arming the magnitude backstop (fail-closed)",
                        log_ctx, t,
                        sl if sl is None else f"{type(sl).__name__}: {sl}",
                    )
                    unclassified_tickers.add(t)
                    lookup_failed_tickers.add(t)
                    continue
                r = split_ratio_in_window(sl, prev_end, curr_end)
                # Tolerance, not `!= 1.0`. The derived ratio is an exact rational so 1.0
                # really is 1.0 today, but an exact float compare on a computed quantity
                # is one refactor away from restating an ordinary quarter by 1.0000001.
                if r and abs(r - 1.0) > 1e-9:
                    split_ratios[t] = r
    except Exception as e:
        # FAIL CLOSED for the whole batch, for the same reason as the per-ticker arm
        # above: `split_ratios = {}` means NO restatement, so this is exactly the state
        # that fabricates a split as a purchase. Zeroing the flags too disarmed the only
        # remaining backstop. Every suspect is flagged instead — suspects are the tickers
        # whose share counts already look like a share multiple, so the blast radius is
        # bounded to them, and a withheld row is recoverable where a fabricated BOUGHT
        # that feeds an alert is not.
        logger.warning(
            "13F splits (%s): split adjustment failed (%s: %s) — arming the magnitude "
            "backstop for all %d suspects (fail-closed)",
            log_ctx, type(e).__name__, e, len(suspects or []),
        )
        split_ratios = {}
        unclassified_tickers = set(suspects or [])
        lookup_failed_tickers = set(suspects or [])
    if capped is not None:
        capped.update(overflow)
    return split_ratios, unclassified_tickers, lookup_failed_tickers


#: How many symbols `split_windows` derives at once. Each derivation is two price-series
#: fetches, and the FMP client's pool (20 connections) is shared with user requests.
SPLIT_WINDOW_CONCURRENCY = 6


@dataclass(frozen=True)
class SplitWindow:
    """One symbol's corporate actions over a 13F quarter pair ``(prev_end, curr_end]``
    (`split_windows`, the review of 2026-10-16).

    * ``ratio`` — the product of the stock splits effective in the window (``1.0``: none), or
      ``None`` when the lookup FAILED: ``get_split_rows`` answered ``None`` (a degraded
      derivation — "could not look", never "no split") or raised.
    * ``unclassified`` — an adjustment the classifier could not name (a spin-off, a reverse split
      past its range) effective in the window. ``has_unclassified_adjustment`` itself answers
      ``True`` when it could not derive (the 13F callers' fail-closed method). ``None`` when it was
      not asked (the split lookup failed), raised, or answered something other than a bool.

    Only ``clean`` vouches that the window holds nothing that can fake a share change."""

    ratio: Optional[float]
    unclassified: Optional[bool]

    @property
    def clean(self) -> bool:
        """No split and no unnameable adjustment in the window, both lookups answered."""
        return (self.ratio is not None and abs(self.ratio - 1.0) <= 1e-9
                and self.unclassified is False)


async def split_windows(
    symbols: Sequence[str],
    prev_end: str,
    curr_end: str,
    *,
    actions: Any,
    log_ctx: str,
    concurrency: int = SPLIT_WINDOW_CONCURRENCY,
) -> Dict[str, SplitWindow]:
    """``{SYMBOL: SplitWindow}`` over ``(prev_end, curr_end]`` for each distinct symbol.

    The same INJECTED seam (``actions``, as `resolve_13f_split_adjustments` takes it — never the
    singleton here; ``trillion_club.builder.split_windows`` resolves the production default), the
    same fetch window (`window_for_range`) and the same effective filter, so the derivations the
    suspect lookups made for this quarter pair are cache hits and the two never disagree about a
    window. Symbols are keyed upper-cased and stripped, as the corporate-actions cache keys them;
    a blank or non-string symbol is skipped (the caller decides what an unchecked row means).
    ``prev_end`` / ``curr_end`` are the two quarter-end ISO dates.

    Never raises for a lookup problem: a failed lookup is ``ratio=None`` (and the probe is not
    spent on it — a failed derivation is cached in neither tier, so asking again would re-fetch
    both price series), a failed probe is ``unclassified=None`` — each logged at WARNING with
    ``log_ctx``. Only cancellation propagates; bad arguments are a ``TypeError`` before any call."""
    if not isinstance(prev_end, str) or not isinstance(curr_end, str):
        raise TypeError("split_windows: prev_end and curr_end must be ISO date strings")
    if actions is None:
        raise TypeError("split_windows: pass the corporate-actions seam (actions)")
    from_date, to_date = window_for_range(prev_end, curr_end)
    wanted = list(dict.fromkeys(
        s.strip().upper() for s in symbols if isinstance(s, str) and s.strip()))
    if not wanted:
        return {}
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(sym: str) -> SplitWindow:
        async with sem:
            try:
                rows = await actions.get_split_rows(sym, from_date, to_date)
            except Exception as e:  # noqa: BLE001 — "could not look", never "nothing there"
                logger.warning("13F split window (%s): split lookup for %s raised (%s: %s) — "
                               "not clean (fail closed)", log_ctx, sym, type(e).__name__, e)
                return SplitWindow(None, None)
            if rows is None:
                logger.warning("13F split window (%s): split lookup for %s could not derive — "
                               "not clean (fail closed)", log_ctx, sym)
                return SplitWindow(None, None)
            ratio = split_ratio_in_window(rows, prev_end, curr_end)
            try:
                flagged = await actions.has_unclassified_adjustment(
                    sym, from_date, to_date, effective_from=prev_end, effective_to=curr_end)
            except Exception as e:  # noqa: BLE001 — same rule as the split lookup
                logger.warning("13F split window (%s): adjustment probe for %s raised (%s: %s) — "
                               "not clean (fail closed)", log_ctx, sym, type(e).__name__, e)
                return SplitWindow(ratio, None)
            if not isinstance(flagged, bool):
                logger.warning("13F split window (%s): adjustment probe for %s answered %r, not a "
                               "bool — not clean (fail closed)", log_ctx, sym, type(flagged).__name__)
                return SplitWindow(ratio, None)
            return SplitWindow(ratio, flagged)

    results = await asyncio.gather(*(one(s) for s in wanted), return_exceptions=True)
    out: Dict[str, SplitWindow] = {}
    for sym, r in zip(wanted, results):
        if isinstance(r, asyncio.CancelledError):
            raise r
        if isinstance(r, BaseException):      # `one` catches Exception: a belt for the brace
            logger.warning("13F split window (%s): %s failed (%s: %s) — not clean (fail closed)",
                           log_ctx, sym, type(r).__name__, r)
            out[sym] = SplitWindow(None, None)
            continue
        out[sym] = r
    return out


__all__ = [
    "SPLIT_PRICE_FACTOR",
    "SPLIT_WINDOW_CONCURRENCY",
    "SplitWindow",
    "suspicious_split_tickers",
    "split_suspects_over_cap",
    "split_ratio_in_window",
    "resolve_13f_split_adjustments",
    "split_windows",
]
