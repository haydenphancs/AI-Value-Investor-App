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

logger = logging.getLogger(__name__)


# ── Versioning ────────────────────────────────────────────────────────

# Bump when the prompt or the output contract changes. It is part of the
# fingerprint, so a bump invalidates every cached card exactly once.
PROMPT_VERSION = 1


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
PER_SCOPE_ATTEMPT_CAP = 10


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


def band_score(band: str, change_percent: Any) -> float:
    """Priority score, used to order admission when the global budget binds.

    Linear in the size of the move so an escalation (−6% → −12%) always outranks
    the move it replaces.
    """
    pct = abs(finite(change_percent) or 0.0)
    if band == BAND_EXTREME:
        return 10.0 + pct
    if band == BAND_NOTABLE:
        return 5.0 + pct
    return pct


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
) -> Decision:
    state = state or {}

    # ── 0. Empty corpus: never hand Gemini nothing ──
    # With no articles the model has no grounding and will confabulate a
    # plausible-sounding market summary. Silence beats a fabricated card.
    if not corpus:
        return Decision(action=ACTION_SKIP, reason="no_corpus")

    quote = quote or {}
    change_pct = quote.get("changePercentage")
    band = price_band(
        change_pct,
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
    today = now.astimezone(timezone.utc).date().isoformat()
    same_day = regen_day == today
    successes = int(state.get("regen_count_today") or 0) if same_day else 0
    attempts = int(state.get("attempts_today") or 0) if same_day else 0

    if successes >= PER_SCOPE_DAILY_CAP:
        return Decision(
            action=ACTION_SKIP, reason="daily_cap",
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
        score=band_score(band, change_pct),
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
    "Decision",
    "finite",
    "price_band",
    "band_score",
    "compute_inputset_id",
    "corpus_article_ids",
    "decide",
]
