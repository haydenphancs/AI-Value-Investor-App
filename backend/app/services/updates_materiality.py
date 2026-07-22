"""
Materiality predicate for the Updates-screen AI Insights card.

PURE MODULE — no network, no Supabase, no Gemini, no wall-clock reads except
through the injected ``now``. Everything here is a deterministic function of its
arguments, which is what makes it exhaustively testable (see
``backend/tests/test_updates_materiality.py``).

WHY THIS EXISTS
---------------
The Insights card is a Gemini roll-up of the articles already cached in
``ticker_news_cache``. A fixed TTL is the wrong refresh primitive:

  * On a quiet ticker the article set is unchanged, so the LLM is paid to emit a
    byte-identical card. Roughly 60% of scope-days look like this.
  * On the day that actually matters — a −12% collapse at 09:35 — a 3h TTL is
    still up to 3 hours late.

So we regenerate on EVIDENCE instead of on a clock:

  1. **Fingerprint** — sha256 over the sorted article ids plus the price band,
     the prompt version and the model id. Identical inputs cannot produce a
     different card, so an unchanged fingerprint is a *proof* that regenerating
     is wasted spend, not a heuristic.
  2. **Price band crossing** — thresholds taken from the SEC Limit Up-Limit Down
     Plan tiers (±5% for Tier-1 / large-cap names, ±10% otherwise), plus a
     softer ±2% "notable" band. Crossing a band re-keys the fingerprint, so a
     big move refreshes the card even before the news wires catch up.
  3. **Close-cycle ceiling** — one guaranteed evaluation per *trading* day via
     ``current_close_cycle_start()``, so nothing can go stale forever. This is a
     ceiling, not a trigger: when the fingerprint is unchanged it re-stamps
     freshness for $0 rather than calling Gemini.

Anti-stampede: on a market-wide dislocation (|S&P| ≥ MWCB Level-1 = 7%) every
per-ticker scope is suspended and only the market card regenerates — otherwise
one macro event would fan out into hundreds of near-identical LLM calls.

Every branch returns a REASON STRING that the caller persists on the state row,
so "why didn't AAPL refresh at 14:32?" is answerable with one SELECT rather than
log archaeology (a CLAUDE.md debuggability requirement).
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

# Constants only — never the clock-reading `session_phase()` helper itself. The
# caller injects the phase, exactly as it injects `now`, so this module stays
# pure and exhaustively testable.
from app.utils.market_hours import ET, SESSION_PREMARKET, SESSION_REGULAR
# Volatility-relative tier vocabulary + the pure z→tier map, shared with the
# report's "Recent Price Movement" section (single source of truth). This is a
# PURE leaf module (stdlib only) so importing it keeps this gate pure/testable.
from app.services.price_volatility import (
    TIER_EXTREME,
    TIER_NOTABLE,
    TIER_TYPICAL,
    TIER_UNUSUAL,
    _tier_for_z,
)

logger = logging.getLogger(__name__)


# ── Versioning ────────────────────────────────────────────────────────

# Bump when the prompt or the output contract changes. It is part of the
# fingerprint, so a bump invalidates every cached card exactly once.
# v2: the price signal changed from a FIXED band (flat/notable/extreme) to a
# VOLATILITY-RELATIVE tier (Typical/Notable/Unusual/Extreme vs the ticker's own
# σ). The vocabulary is part of the fingerprint, so the bump makes the switch a
# single controlled regen wave (bounded by the per-cycle + global caps) instead
# of an uncontrolled band↔tier flip.
PROMPT_VERSION = 2


# ── Thresholds ────────────────────────────────────────────────────────
# Fractions, not percentages: 0.05 == 5%.

# SEC Limit Up-Limit Down Plan: Tier-1 NMS stocks get ±5% bands, everything
# else ±10%. We approximate "Tier 1" with a market-cap floor because FMP does
# not expose the tier directly.
_LULD_TIER1_PCT = 0.05
_LULD_TIER2_PCT = 0.10
_LULD_TIER1_MIN_MARKET_CAP = 10_000_000_000.0

# A softer band below LULD. Two percent on a single session is a move a retail
# investor notices and asks about, which is exactly when the card should be current.
_NOTABLE_PCT = 0.02

# Index / general-market scopes move on a much tighter scale — a 5% S&P day is a
# historic event, not a "notable" one. Calibrated against the SEC market-wide
# circuit-breaker ladder (7% / 13% / 20%).
_INDEX_NOTABLE_PCT = 0.01
_INDEX_EXTREME_PCT = 0.03

# Market-Wide Circuit Breaker Level 1. Above this the whole tape is moving, so a
# per-ticker card would just restate the macro story N times.
_MWCB_L1_PCT = 0.07

# Debounce. Session cooldown is short enough to track a developing story,
# long enough that a stock oscillating around a band edge cannot bill us twice
# a minute.
COOLDOWN_SESSION_SECONDS = 900     # 15 min while the market is active
COOLDOWN_CLOSED_SECONDS = 3600     # 60 min overnight / weekend

# Per-scope caps. `regen_count_today` counts SUCCESSES only; `attempts_today`
# absorbs failures so a run of transient Gemini 429s cannot pin a scope for the
# rest of the day.
PER_SCOPE_DAILY_CAP = 6

# The market card is read by every user on every visit — it backs the default
# tab — and the general news wire never goes quiet, so it is the one scope where
# 6/day is visibly too few. It is a single scope, so the extra spend is ~10
# Flash-Lite calls a day in total, not per ticker.
PER_SCOPE_DAILY_CAP_MARKET = 16

PER_SCOPE_ATTEMPT_CAP = 10

# Share of a scope's daily allowance that may be spent BEFORE the opening bell.
#
# The sweeper wakes at 04:00 ET, and the news wire is busiest overnight-into-
# morning, so without this the whole budget was gone before 09:30: observed
# live, `__MARKET__` generated for the 6th and last time at 05:49 ET and then
# sat frozen through the entire regular session with
# `last_skip_reason = "daily_cap"`. Reserving two thirds for 09:30-16:00 puts
# the spend where people are actually reading.
#
# Deliberately NOT applied to after-hours (16:00-20:00 ET): that window is when
# earnings land, which is the single most material news event a ticker has all
# quarter.
#
# This ceiling only measures the right thing because `regen_count_today` is
# keyed on the ET TRADING DATE (see `_decide_inner`), which rolls at ET midnight
# — outside the 04:00-20:00 sweep window. Keyed on the UTC date it rolled at
# 19:00 ET under EST, an hour INSIDE the window, so the previous evening's
# after-hours spend was billed to the next morning and pre-consumed this
# reserve. If you ever change the day key, change it in
# `claim_updates_insight_scope` (migration 089) too — they must agree.
PREMARKET_CAP_DIVISOR = 3


def daily_cap_for(is_market_scope: bool) -> int:
    """The per-scope success ceiling for one day."""
    return PER_SCOPE_DAILY_CAP_MARKET if is_market_scope else PER_SCOPE_DAILY_CAP


def premarket_cap_for(is_market_scope: bool) -> int:
    """The success ceiling that applies before 09:30 ET. Always >= 1 so a cold
    scope can still get its first card of the day in pre-market."""
    return max(1, daily_cap_for(is_market_scope) // PREMARKET_CAP_DIVISOR)


# ── Decision ──────────────────────────────────────────────────────────

ACTION_GENERATE = "generate"
ACTION_TOUCH = "touch"      # inputs unchanged but the daily ceiling fired: re-stamp, $0
ACTION_SKIP = "skip"


@dataclass(frozen=True)
class Decision:
    """The gate's verdict for one scope in one sweep."""

    action: str
    reason: str
    inputset_id: Optional[str] = None
    price_band: Optional[str] = None
    score: float = 0.0

    @property
    def should_generate(self) -> bool:
        return self.action == ACTION_GENERATE

    @property
    def should_touch(self) -> bool:
        return self.action == ACTION_TOUCH


# ── Numeric guards ────────────────────────────────────────────────────

def finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None``.

    FMP returns ``NaN``/``Infinity`` JSON tokens on thinly-traded or
    just-listed symbols, and Python's ``json`` happily parses them into float
    NaN/inf. Feeding those into a comparison yields ``False`` for every branch
    (silently disabling the gate) and into a Pydantic response yields a 500
    under ``allow_nan=False``. Both failure modes are on record in this repo.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── Price band ────────────────────────────────────────────────────────

BAND_FLAT = "flat"
BAND_NOTABLE = "notable"
BAND_EXTREME = "extreme"
BAND_UNKNOWN = "unknown"


def price_band(
    change_percent: Any,
    market_cap: Any = None,
    is_index: bool = False,
) -> str:
    """Bucket a session move into flat / notable / extreme.

    ``change_percent`` is FMP's ``changePercentage`` — a PERCENT (e.g. ``-2.14``),
    not a fraction. Returns ``BAND_UNKNOWN`` when the quote is unusable, which
    the caller treats as "no price signal" rather than as ``flat`` — an unknown
    move must not be mistaken for a calm one.

    Bucketing (rather than using the raw number) is deliberate: it keeps the
    fingerprint stable against tick-by-tick wiggle so we only pay for a
    regeneration when the move changes *category*.
    """
    pct = finite(change_percent)
    if pct is None:
        return BAND_UNKNOWN
    move = abs(pct) / 100.0

    if is_index:
        if move >= _INDEX_EXTREME_PCT:
            return BAND_EXTREME
        if move >= _INDEX_NOTABLE_PCT:
            return BAND_NOTABLE
        return BAND_FLAT

    cap = finite(market_cap) or 0.0
    extreme = _LULD_TIER1_PCT if cap >= _LULD_TIER1_MIN_MARKET_CAP else _LULD_TIER2_PCT
    if move >= extreme:
        return BAND_EXTREME
    if move >= _NOTABLE_PCT:
        return BAND_NOTABLE
    return BAND_FLAT


def volatility_tier(change_percent: Any, sigma_daily: Any, is_index: bool = False) -> str:
    """Bucket a SESSION move by how abnormal it is for THIS ticker — the report's
    method (price_volatility) applied to a 1-day move (√N = 1).

    ``z = |change%| / (σ_daily·100)`` → Typical / Notable (z≥1) / Unusual (z≥2) /
    Extreme (z≥3). Returns ``BAND_UNKNOWN`` when the move is unusable (so the gate
    treats it as "no signal", not a calm move); returns ``TIER_TYPICAL`` when σ is
    unusable (caller decides whether to fall back to the fixed band).

    ``is_index`` is accepted for signature symmetry but does not change the z math
    — σ already encodes how quiet the index is, so no separate index scale is
    needed on this path.
    """
    pct = finite(change_percent)
    if pct is None:
        return BAND_UNKNOWN
    sig = finite(sigma_daily)
    if sig is None or sig <= 0:
        return TIER_TYPICAL
    z = abs(pct) / (sig * 100.0)
    return _tier_for_z(z)


def classify_move(
    change_percent: Any,
    sigma_daily: Any,
    market_cap: Any = None,
    is_index: bool = False,
) -> str:
    """The gate's price-signal classifier: volatility-relative tier when σ is
    known, else the fixed price band (new/low-history tickers, or before the
    daily σ precompute has run).

    Returns the tier vocabulary (Typical/Notable/Unusual/Extreme) on the σ path
    and the band vocabulary (flat/notable/extreme) on the fallback path; both are
    opaque labels to the fingerprint and are handled by ``move_score``. An
    unusable ``change_percent`` returns ``BAND_UNKNOWN`` on either path.
    """
    if finite(change_percent) is None:
        return BAND_UNKNOWN
    sig = finite(sigma_daily)
    if sig is not None and sig > 0:
        return volatility_tier(change_percent, sig, is_index)
    return price_band(change_percent, market_cap, is_index)


def move_score(band_or_tier: str, change_percent: Any) -> float:
    """Priority score, used to order admission when the per-cycle/global budget
    binds. Abnormality-ranked: an Extreme/Unusual tier outranks a Notable one,
    and within a tier the raw magnitude breaks ties (an escalation −6% → −12%
    always outranks the move it replaces). Handles BOTH the tier vocabulary
    (σ path) and the fixed-band vocabulary (fallback).
    """
    pct = abs(finite(change_percent) or 0.0)
    if band_or_tier in (TIER_EXTREME, BAND_EXTREME):
        return 20.0 + pct
    if band_or_tier == TIER_UNUSUAL:
        return 15.0 + pct
    if band_or_tier in (TIER_NOTABLE, BAND_NOTABLE):
        return 10.0 + pct
    return pct  # Typical / flat / unknown


# ── Fingerprint ───────────────────────────────────────────────────────

def compute_inputset_id(
    article_ids: Iterable[Any],
    band: str,
    model: str,
    prompt_version: int = PROMPT_VERSION,
) -> str:
    """Stable digest of everything that can change the generated card.

    Sorted so that FMP reordering the same articles is not mistaken for new
    news. ``band`` is included because the prompt embeds the price context, and
    ``model`` / ``prompt_version`` because either changes the output for
    identical articles.
    """
    keys = sorted(str(a) for a in article_ids if a is not None and str(a) != "")
    payload = "\n".join(keys) + f"|band={band}|pv={prompt_version}|m={model}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def corpus_article_ids(corpus: Sequence[Dict[str, Any]]) -> list:
    """Extract the stable identity of each article in a corpus.

    Prefers ``external_id`` (the article URL — stable across cache cycles) over
    the DB ``id`` (a fresh UUID every time the 6h cache turns over, which would
    make the fingerprint change on every cycle and defeat the whole design).
    """
    ids = []
    for row in corpus:
        if not isinstance(row, dict):
            continue
        key = row.get("external_id") or row.get("article_url") or row.get("id")
        if key:
            ids.append(str(key))
    return ids


# ── The gate ──────────────────────────────────────────────────────────

def _cooldown_seconds(market_active: bool) -> int:
    return COOLDOWN_SESSION_SECONDS if market_active else COOLDOWN_CLOSED_SECONDS


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a Supabase timestamptz into an aware UTC datetime, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decide(
    *,
    scope: str,
    corpus: Sequence[Dict[str, Any]],
    quote: Optional[Dict[str, Any]],
    state: Optional[Dict[str, Any]],
    market_change_percent: Any,
    close_cycle_start: datetime,
    now: datetime,
    model: str,
    market_active: bool,
    is_market_scope: bool,
    sigma_daily: Optional[float] = None,
    session_phase: str = SESSION_REGULAR,
) -> Decision:
    """Decide whether ``scope``'s Insights card should be regenerated.

    Returns a :class:`Decision` whose ``reason`` is always populated. Never
    raises: any unexpected input degrades to ``skip("gate_error")`` so a single
    malformed FMP row cannot break the whole sweep or 500 the Updates screen.
    """
    try:
        return _decide_inner(
            scope=scope, corpus=corpus, quote=quote, state=state,
            market_change_percent=market_change_percent,
            close_cycle_start=close_cycle_start, now=now, model=model,
            market_active=market_active, is_market_scope=is_market_scope,
            sigma_daily=sigma_daily, session_phase=session_phase,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(
            "Materiality gate raised for scope=%s: %s: %s",
            scope, type(e).__name__, e,
        )
        return Decision(action=ACTION_SKIP, reason="gate_error")


def _decide_inner(
    *,
    scope: str,
    corpus: Sequence[Dict[str, Any]],
    quote: Optional[Dict[str, Any]],
    state: Optional[Dict[str, Any]],
    market_change_percent: Any,
    close_cycle_start: datetime,
    now: datetime,
    model: str,
    market_active: bool,
    is_market_scope: bool,
    sigma_daily: Optional[float] = None,
    session_phase: str = SESSION_REGULAR,
) -> Decision:
    state = state or {}

    # ── 0. Empty corpus: never hand Gemini nothing ──
    # With no articles the model has no grounding and will confabulate a
    # plausible-sounding market summary. Silence beats a fabricated card.
    if not corpus:
        return Decision(action=ACTION_SKIP, reason="no_corpus")

    quote = quote or {}
    change_pct = quote.get("changePercentage")
    # Volatility-relative tier vs the ticker's own σ (a 3% day is Extreme for a
    # utility, Typical for a meme stock); falls back to the fixed band when σ is
    # unavailable (new/low-history ticker, or before the daily precompute ran).
    band = classify_move(
        change_pct,
        sigma_daily,
        market_cap=quote.get("marketCap"),
        is_index=is_market_scope,
    )
    if band == BAND_UNKNOWN:
        # An ABSENT price signal must be neutral to the gate, not a change event.
        # The band feeds the fingerprint, so letting `flat -> unknown` through
        # re-keys the digest and reports a byte-identical corpus as
        # "new_articles" — then a second time on the way back to `flat`. A
        # universe-wide quote hiccup would cost ~2 x universe generations.
        band = state.get("last_price_band") or BAND_UNKNOWN

    inputset_id = compute_inputset_id(corpus_article_ids(corpus), band, model)

    # ── 1. Fingerprint short-circuit — the money-saver ──
    # Checked BEFORE every other branch: if the inputs are identical the output
    # is provably identical, so there is nothing to buy at any price.
    if inputset_id == state.get("last_inputset_id"):
        prev_cycle = _parse_ts(state.get("close_cycle"))
        if prev_cycle is None or prev_cycle < close_cycle_start:
            # A new trading day settled. Re-stamp freshness so the card cannot
            # be flagged stale forever — costs $0, no LLM call.
            return Decision(
                action=ACTION_TOUCH, reason="cycle_touch",
                inputset_id=inputset_id, price_band=band,
            )
        return Decision(
            action=ACTION_SKIP, reason="fingerprint_unchanged",
            inputset_id=inputset_id, price_band=band,
        )

    # ── 2. Market-wide dislocation: one macro card, not N restatements ──
    mkt = finite(market_change_percent)
    if (
        not is_market_scope
        and mkt is not None
        and abs(mkt) / 100.0 >= _MWCB_L1_PCT
    ):
        return Decision(
            action=ACTION_SKIP, reason="mwcb_market_only",
            inputset_id=inputset_id, price_band=band,
        )

    # ── 3. Per-scope daily cap (successes only) ──
    regen_day = str(state.get("regen_day") or "")
    # The ET trading date, NOT the UTC date. Under EDT the UTC day rolls at
    # 20:00 ET, exactly when the sweeper stops, so the two agreed by luck. Under
    # EST it rolls at 19:00 ET — one hour INSIDE the sweep window — so the
    # previous evening's after-hours generations landed on the same key as the
    # next morning's pre-market and pre-spent its reserve, starving the window
    # this budget exists to protect. An ET-keyed day rolls at ET midnight, far
    # outside 04:00-20:00, and is what "trading day" means anyway.
    # `claim_updates_insight_scope` must use the same key — migration 089.
    today = now.astimezone(ET).date().isoformat()
    same_day = regen_day == today
    successes = int(state.get("regen_count_today") or 0) if same_day else 0
    attempts = int(state.get("attempts_today") or 0) if same_day else 0

    if successes >= daily_cap_for(is_market_scope):
        return Decision(
            action=ACTION_SKIP, reason="daily_cap",
            inputset_id=inputset_id, price_band=band,
        )
    # Reserve the bulk of the allowance for the regular session. Skipping here
    # is not a loss: the scope re-trips the moment the bell rings, and the
    # articles that drove it are still in the corpus.
    if (
        session_phase == SESSION_PREMARKET
        and successes >= premarket_cap_for(is_market_scope)
    ):
        return Decision(
            action=ACTION_SKIP, reason="premarket_reserved",
            inputset_id=inputset_id, price_band=band,
        )
    if attempts >= PER_SCOPE_ATTEMPT_CAP:
        return Decision(
            action=ACTION_SKIP, reason="attempt_cap",
            inputset_id=inputset_id, price_band=band,
        )

    # ── 4. Cooldown ──
    last_gen = _parse_ts(state.get("last_generated_at"))
    if last_gen is not None:
        elapsed = (now - last_gen).total_seconds()
        # A negative elapsed means clock skew or a future timestamp; treat it as
        # "just generated" and wait, rather than regenerating in a tight loop.
        if elapsed < _cooldown_seconds(market_active):
            return Decision(
                action=ACTION_SKIP, reason="cooldown",
                inputset_id=inputset_id, price_band=band,
            )

    # ── 5. Regenerate — describe WHY ──
    prev_band = state.get("last_price_band")
    reasons = []
    if state.get("last_inputset_id") is None:
        reasons.append("cold_start")
    if prev_band and prev_band != band and band != BAND_UNKNOWN:
        pct = finite(change_pct)
        reasons.append(
            f"band {prev_band}->{band}"
            + (f" ({pct:+.2f}%)" if pct is not None else "")
        )
    if not reasons:
        reasons.append(f"new_articles ({len(corpus)})")

    return Decision(
        action=ACTION_GENERATE,
        reason=" + ".join(reasons),
        inputset_id=inputset_id,
        price_band=band,
        score=move_score(band, change_pct),
    )


__all__ = [
    "PROMPT_VERSION",
    "PER_SCOPE_DAILY_CAP",
    "PER_SCOPE_ATTEMPT_CAP",
    "COOLDOWN_SESSION_SECONDS",
    "COOLDOWN_CLOSED_SECONDS",
    "ACTION_GENERATE",
    "ACTION_TOUCH",
    "ACTION_SKIP",
    "BAND_FLAT",
    "BAND_NOTABLE",
    "BAND_EXTREME",
    "BAND_UNKNOWN",
    "TIER_TYPICAL",
    "TIER_NOTABLE",
    "TIER_UNUSUAL",
    "TIER_EXTREME",
    "Decision",
    "finite",
    "price_band",
    "volatility_tier",
    "classify_move",
    "move_score",
    "compute_inputset_id",
    "corpus_article_ids",
    "decide",
]
