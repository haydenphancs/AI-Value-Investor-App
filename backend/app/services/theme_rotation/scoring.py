"""Relevance scoring and eligibility floors for the monthly theme rotation. Pure functions.

Score (0-100) — relevance decides membership, the market only breaks ties:

    exposure   55   how much of the business IS the theme (revenue segments; else industry
                    half-credit; else ≥2 description keywords, capped at 60% — or 80% when
                    the relevance check ALSO rates it a core, majority-revenue pure play)
    ETF vote   20   how many of the theme's seed ETFs hold it, and how heavily
    market     15   3- and 6-month return vs the theme's median, clipped at ±2σ — the owner's
                    "current market" signal, deliberately small (momentum-ranked thematic
                    baskets are the documented cause of their post-launch underperformance)
    size/liq.  10   percentile of log market cap and of 6-month traded value in the pool
                    (was 15: it let megacaps outrank small pure plays on size alone)

UNKNOWN is never zero for a member: a member with no usable exposure data scores a neutral
0.5, so a data gap cannot rotate a stock out. A NEWCOMER with unknown exposure scores 0 —
it has to prove relevance to get in.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from statistics import median, pstdev
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.theme_rotation.models import Candidate, Reason, ScoreBreakdown, ThemeDefinition

US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "NYSEAMERICAN"})

MIN_PRICE = 3.0
MIN_SESSION_COVERAGE = 0.90
SEASONING_SESSIONS = 63          # ~3 months of trading before a new listing may join
FAST_TRACK_SESSIONS = 10         # a "significant IPO" (above the theme's median cap)
DESCRIPTION_MIN_HITS = 2
DESCRIPTION_CREDIT_PER_HIT = 0.2
DESCRIPTION_CREDIT_CAP = 0.6     # a description alone can never claim a pure play
INDUSTRY_CREDIT = 0.5
UNKNOWN_MEMBER_EXPOSURE = 0.5
ETF_WEIGHT_FULL_CREDIT = 5.0     # a ≥5% position in a seed ETF earns the full weight part
Z_CLIP = 2.0

W_EXPOSURE, W_ETF, W_MARKET, W_SIZE = 55.0, 20.0, 15.0, 10.0
REVIEWED_PURE_PLAY_EXPOSURE = 0.8
_PURE_PLAY_BANDS = frozenset({"over_50", "pre_revenue"})


# ── Keywords ──────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=4096)
def keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Whole-word match with a simple plural/verb ending, case-insensitive.

    "space" matches "space" and "spaces" but not "aerospace"; "gene" not "general";
    "security" not "securities" (the ending set excludes "ies").
    """
    words = [re.escape(w) for w in keyword.lower().split()]
    body = r"[\s\-]+".join(words)
    return re.compile(rf"(?<![a-z0-9]){body}(?:s|es|ed|ing)?(?![a-z0-9])", re.IGNORECASE)


def _term_key(keyword: str) -> str:
    """Keywords that differ only by a trailing "s" are ONE term ("robotic"/"robotics")."""
    return keyword[:-1] if len(keyword) > 3 and keyword.endswith("s") else keyword


def count_keyword_hits(text: Optional[str], keywords: Iterable[str]) -> int:
    """How many DISTINCT pieces of theme evidence `text` contains.

    One word or phrase counts once, however many keywords match it. The pattern allows a
    plural ending, so "robotics" matches both the "robotic" and "robotics" keywords, and
    "optical interconnect" matches that phrase plus "optical" and "interconnect" — counting
    keywords made ONE word pass the ≥2-hit gate on its own. So: keywords differing only by
    a trailing "s" are one term; terms are tried longest first; and a term whose every
    match lies inside text already credited to another term adds nothing. A term repeated
    in the text still counts once.
    """
    if not text or not isinstance(text, str):
        return 0
    terms: Dict[str, List[str]] = {}
    for kw in keywords:
        if not isinstance(kw, str) or not kw.strip():
            continue          # a blank keyword would match the empty string everywhere
        norm = " ".join(kw.lower().split())
        terms.setdefault(_term_key(norm), []).append(norm)
    claimed: List[Tuple[int, int]] = []
    hits = 0
    for key in sorted(terms, key=lambda k: (-max(len(w) for w in terms[k]), k)):
        spans = [m.span() for w in dict.fromkeys(terms[key])
                 for m in keyword_pattern(w).finditer(text)]
        if any(not any(a < end and start < b for start, end in claimed) for a, b in spans):
            hits += 1
            claimed.extend(spans)
    return hits


def segment_theme_share(segments: Optional[Dict[str, object]],
                        keywords: Sequence[str]) -> Optional[float]:
    """Share of revenue (0..1) in segments whose NAMES match a theme keyword.

    None when there is no usable segment data (missing, empty, or no positive revenue) —
    unknown, never zero. Negative or non-numeric segment values are ignored.
    """
    if not isinstance(segments, dict) or not segments:
        return None
    total = 0.0
    matched = 0.0
    for name, raw in segments.items():
        value = _finite(raw)
        if value is None or value <= 0:
            continue
        total += value
        if isinstance(name, str) and count_keyword_hits(name, keywords) > 0:
            matched += value
    if total <= 0:
        return None
    return min(1.0, matched / total)


# ── Exposure ──────────────────────────────────────────────────────────────────────────

def exposure_of(c: Candidate, defn: ThemeDefinition) -> Tuple[float, str]:
    """(exposure 0..1, source). The strongest available evidence wins."""
    options: List[Tuple[float, str]] = []
    share = _finite(c.segment_share)     # NaN is unknown, not a known zero
    if share is not None and share > 0:
        options.append((min(1.0, share), "segments"))
    if (c.industry or "") in defn.industries:
        options.append((INDUSTRY_CREDIT, "industry"))
    if c.keyword_hits >= DESCRIPTION_MIN_HITS:
        credit = min(DESCRIPTION_CREDIT_CAP, DESCRIPTION_CREDIT_PER_HIT * c.keyword_hits)
        if c.is_member:
            # On-theme wording is evidence FOR a member: it must never score below the
            # neutral a member with no evidence at all gets (2 hits = 0.4 < 0.5 did).
            credit = max(credit, UNKNOWN_MEMBER_EXPOSURE)
        options.append((credit, "description"))
        # A pre-revenue or segment-less pure play (a reactor developer, a gene-editing
        # biotech) can never SHOW theme revenue, so its exposure would be stuck at 60%
        # beneath any diversified giant. The relevance check may lift it to 80% — but
        # only on top of ≥2 keywords in its own description (objective corroboration),
        # only for "core" + a majority-revenue band, and never higher.
        if c.fit == "core" and c.fit_band in _PURE_PLAY_BANDS:
            options.append((REVIEWED_PURE_PLAY_EXPOSURE, "description"))
    if options:
        return max(options, key=lambda o: (o[0], o[1]))
    if share is not None:
        # Segment data exists and none of it is the theme — a known zero.
        return 0.0, "segments"
    return (UNKNOWN_MEMBER_EXPOSURE if c.is_member else 0.0), "unknown"


# ── Pool context (medians, spreads, percentiles) ──────────────────────────────────────

@dataclass(frozen=True)
class PoolContext:
    median_3m: Optional[float]
    sd_3m: Optional[float]
    median_6m: Optional[float]
    sd_6m: Optional[float]
    cap_percentile: Dict[str, float]
    adtv_percentile: Dict[str, float]
    etfs_loaded: int
    median_member_cap: Optional[float]


def build_pool_context(candidates: Sequence[Candidate], etfs_loaded: int) -> PoolContext:
    r3 = [v for v in (_finite(c.ret_3m) for c in candidates) if v is not None]
    r6 = [v for v in (_finite(c.ret_6m) for c in candidates) if v is not None]
    member_caps = [v for v in (_positive(c.market_cap) for c in candidates if c.is_member)
                   if v is not None]
    return PoolContext(
        median_3m=median(r3) if r3 else None,
        sd_3m=pstdev(r3) if len(r3) >= 2 else None,
        median_6m=median(r6) if r6 else None,
        sd_6m=pstdev(r6) if len(r6) >= 2 else None,
        cap_percentile=_log_percentiles({c.ticker: c.market_cap for c in candidates}),
        adtv_percentile=_log_percentiles({c.ticker: c.adtv_6m for c in candidates}),
        etfs_loaded=max(0, int(etfs_loaded)),
        median_member_cap=median(member_caps) if member_caps else None,
    )


def _log_percentiles(values: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Percentile (0..1) of log(value) among the positive values; ties share a rank."""
    logs = {t: math.log(v) for t, v in ((t, _positive(v)) for t, v in values.items()) if v}
    if not logs:
        return {}
    if len(logs) == 1:
        return {t: 1.0 for t in logs}
    ordered = sorted(logs.values())
    n = len(ordered)
    out: Dict[str, float] = {}
    for t, v in logs.items():
        below = sum(1 for x in ordered if x < v)
        equal = sum(1 for x in ordered if x == v)
        out[t] = (below + (equal - 1) / 2) / (n - 1)
    return out


# ── Score ─────────────────────────────────────────────────────────────────────────────

def score_candidate(c: Candidate, defn: ThemeDefinition, ctx: PoolContext) -> ScoreBreakdown:
    exposure, source = exposure_of(c, defn)
    exposure_pts = W_EXPOSURE * exposure

    if ctx.etfs_loaded > 0:
        held = min(1.0, max(0, c.etf_holders) / ctx.etfs_loaded)
        weight = min(1.0, max(0.0, _finite(c.etf_max_weight) or 0.0) / ETF_WEIGHT_FULL_CREDIT)
        etf_pts = W_ETF * (0.7 * held + 0.3 * weight)
    else:
        etf_pts = W_ETF * 0.5 if c.is_member else 0.0

    zs = [z for z in (_z(c.ret_3m, ctx.median_3m, ctx.sd_3m),
                      _z(c.ret_6m, ctx.median_6m, ctx.sd_6m)) if z is not None]
    z = max(-Z_CLIP, min(Z_CLIP, sum(zs) / len(zs))) if zs else 0.0
    market_pts = W_MARKET * (z + Z_CLIP) / (2 * Z_CLIP)

    neutral = 0.5 if c.is_member else 0.0
    cap_pct = ctx.cap_percentile.get(c.ticker, neutral)
    adtv_pct = ctx.adtv_percentile.get(c.ticker, neutral)
    size_pts = (W_SIZE / 2) * cap_pct + (W_SIZE / 2) * adtv_pct

    total = exposure_pts + etf_pts + market_pts + size_pts
    return ScoreBreakdown(
        total=round(total, 2), exposure_pts=round(exposure_pts, 2), etf_pts=round(etf_pts, 2),
        market_pts=round(market_pts, 2), size_pts=round(size_pts, 2),
        exposure=round(exposure, 4), exposure_source=source,
    )


def _z(value: Optional[float], mid: Optional[float], sd: Optional[float]) -> Optional[float]:
    v = _finite(value)
    if v is None or mid is None:
        return None
    if sd is None or not math.isfinite(sd) or sd <= 0:
        return 0.0
    return (v - mid) / sd


# ── Eligibility floors ────────────────────────────────────────────────────────────────

def is_us_listed(exchange: Optional[str]) -> bool:
    return isinstance(exchange, str) and exchange.strip().upper() in US_EXCHANGES


def floor_failure(c: Candidate, defn: ThemeDefinition, *,
                  median_member_cap: Optional[float] = None) -> Optional[Reason]:
    """The reason a candidate may not be (or stay) in the theme, or None when it passes.

    Members face LOWER bars than newcomers (the index-provider buffer): a member is only
    forced out on evidence — missing data never removes one. Newcomers must prove every
    floor.
    """
    if c.actively_trading is False:
        return Reason.DELISTED
    cap, price = _positive(c.market_cap), _positive(c.price)

    if c.is_member:
        # No PRICE floor for a member: a low share price says nothing about size or
        # liquidity (the first live preview would have evicted Denison Mines, a ~$2
        # uranium major, on price alone). Newcomers still need $3.
        if cap is not None and cap < defn.incumbent_min_market_cap:
            return Reason.BELOW_FLOORS
        # An OTC ADR member (FANUY, YASKY, ABBNY on 2026-09-23) keeps its seat: US volume
        # understates its real liquidity at home, so the liquidity floor would evict it
        # on a technicality. The exchange rule itself applies to newcomers only.
        if is_us_listed(c.exchange) and not c.history_blocked:
            adtv = _finite(c.adtv_6m)
            if adtv is not None and adtv < defn.incumbent_min_adtv:
                return Reason.BELOW_FLOORS
            coverage = _finite(c.session_coverage)
            if coverage is not None and coverage < MIN_SESSION_COVERAGE:
                return Reason.BELOW_FLOORS
        return None

    if c.history_blocked:
        return Reason.NO_DATA
    if not is_us_listed(c.exchange):
        return Reason.NOT_US_LISTED
    if cap is None or cap < defn.min_market_cap:
        return Reason.FAILS_FLOORS
    if price is None or price < MIN_PRICE:
        return Reason.FAILS_FLOORS
    adtv = _finite(c.adtv_6m)
    if adtv is None or adtv < defn.min_adtv:
        return Reason.FAILS_FLOORS
    if c.sessions_listed is not None and c.sessions_listed < SEASONING_SESSIONS:
        significant = (c.sessions_listed >= FAST_TRACK_SESSIONS and median_member_cap is not None
                       and cap > median_member_cap)
        if not significant:
            return Reason.IPO_SEASONING
    coverage = _finite(c.session_coverage)
    if coverage is None or coverage < MIN_SESSION_COVERAGE:
        return Reason.FAILS_FLOORS
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────────────

def _finite(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _positive(value: object) -> Optional[float]:
    v = _finite(value)
    return v if v is not None and v > 0 else None
