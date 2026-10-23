"""Topic of the week: rank themes and single tickers from the week's ranking signals (plan §1.5).

Pure and deterministic: stdlib + `contracts` only — no FMP, Supabase or Gemini import, no clock,
no randomness. `signals.py` (the ONE module that may import FMP) builds the `TickerSignal`s; the
scheduler reads the no-repeat windows from the ledger and passes them in. The output is the top 3
`TopicCandidate`s the Thursday Telegram card offers the owner.

Why each rule is shaped the way it is:

* **Robust z, not a plain z.** A ticker's weekly news count is compared with ITS OWN trailing
  weeks: `(x − median) / (1.4826 · max(MAD, 1))`. Median and MAD ignore one freak week in the
  baseline; the `max(MAD, 1)` floor stops a quiet ticker (MAD 0: the same 2 stories every week)
  from turning one extra story into an infinite z. The result is clamped to [−5, 10] so a single
  outlier week (an IPO, a viral headline) cannot outvote every other signal in a theme's mean.
* **Missing is never zero.** `None` on a count means the fetch failed (rate limit, auth, timeout).
  Reading it as 0 would rank a ticker whose feed broke as "quiet" and could hand the week to a
  theme whose members happened to fetch. A malformed count (bool, NaN, negative, non-number) is
  treated the same way. If more than 20% of the scoreable universe is missing the whole ranking is
  untrustworthy, so `rank_topics` raises `ScoringAborted` and the caller falls back.
* **Exclusions are fail-closed.** `size_ok` must be exactly `True` (unknown size is excluded), a
  foreign 20-F/40-F filer is never a main subject in the MVP, and any upstream `excluded_reason`
  (blocklist, ETF, …) excludes even when it is an empty string — a set field is a decision.
* **Themes beat tickers.** A theme needs breadth (≥ 2 members above 1.0) and scores the mean of its
  top 3; a lone ticker needs a score ≥ 3. A theme covers its top member, so a ticker candidate that
  is the top member of a SELECTED theme is dropped — whichever ranks higher (see `rank_topics`).
* **Order-independent.** Members and candidates are sorted by (score desc, key asc) before any
  sum or cut, so shuffling the inputs never changes the answer. The one order-dependent rule is
  deliberate: a duplicated `TickerSignal` / theme slug keeps the FIRST and logs a WARNING.

FMP numbers live only inside this computation; candidates carry scores and tickers, never FMP text.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.learn_weekly.contracts import ThemeInput, TickerSignal, TopicCandidate

logger = logging.getLogger(__name__)

# ── Constants (the plan's §1.5 numbers) ───────────────────────────────────────────────────────

MAD_SCALE = 1.4826            # makes the MAD a consistent estimator of σ for normal data
MAD_FLOOR = 1.0               # max(MAD, 1): a flat baseline must not divide by ~0
MIN_BASELINE_WEEKS = 4        # fewer usable trailing weeks → no z (a new listing, a broken read)
Z_MIN = -5.0
Z_MAX = 10.0

W_NEWS = 0.6
W_8K = 0.4
EARNINGS_BONUS = 0.5
EARNINGS_ITEM = "2.02"        # 8-K Item 2.02 "Results of Operations and Financial Condition"

THEME_TOP_K = 3               # a theme scores the mean of its top 3 members
THEME_MEMBER_FLOOR = 1.0      # a member counts toward breadth only ABOVE this score (strict)
THEME_MIN_STRONG = 2          # … and a theme needs at least this many such members
TICKER_MIN_SCORE = 3.0        # a lone ticker qualifies only at or above this score

MAX_MISSING_PERCENT = 20      # > 20% of the non-excluded universe missing → ScoringAborted

KIND_THEME = "theme"
KIND_TICKER = "ticker"
REASON_RANKED = "ranked"
REASON_QUIET_WEEK = "quiet_week_fallback"

# Exclusion codes (logs and the topic card; never FMP text).
EXCLUDED_BAD_TICKER = "bad_ticker"
EXCLUDED_SIZE = "size_not_ok"
EXCLUDED_FOREIGN = "foreign_filer"
EXCLUDED_UPSTREAM = "upstream"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")


class ScoringAborted(Exception):
    """More than `MAX_MISSING_PERCENT`% of the non-excluded universe has no news count this week.

    The ranking would be decided by which feeds happened to answer, so none is returned; the caller
    falls back (`quiet_week_fallback`, or skips the week) and alerts. Carries the counts so the alert
    is legible from the log alone.
    """

    def __init__(self, missing: int, universe: int, sample: Sequence[str]):
        self.missing = int(missing)
        self.universe = int(universe)
        self.sample: Tuple[str, ...] = tuple(sample)
        super().__init__(
            f"topic scoring aborted: news count missing for {self.missing} of {self.universe} "
            f"non-excluded tickers (> {MAX_MISSING_PERCENT}%); first missing: "
            f"{', '.join(self.sample) or '-'}"
        )


# ── Value helpers ─────────────────────────────────────────────────────────────────────────────


def _as_count(value: Any) -> Optional[float]:
    """A usable weekly count as a float, or None (missing / malformed).

    `bool` is an `int` subclass — `True` must not read as a count of 1. Negative and non-finite
    values are malformed upstream rows. An int too large for a float is malformed too (float()
    would raise OverflowError deep inside the arithmetic otherwise).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(f) or f < 0:
        return None
    return f


def _norm_ticker(value: Any) -> Optional[str]:
    """Strip + upper-case so `nvda` in a theme basket meets `NVDA` from the signals; None if unusable."""
    if not isinstance(value, str):
        return None
    t = value.strip().upper()
    return t or None


def _norm_slug(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _window(values: Any, norm, name: str) -> Set[str]:
    """A no-repeat window as a normalised set. A lone string or None is a caller bug — raise.

    A bare `str` would iterate as characters and silently match nothing; `None` usually means the
    ledger read failed, and treating that as "nothing recent" would let the week repeat a topic.
    """
    if values is None or isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings, got {type(values).__name__}")
    out: Set[str] = set()
    for v in values:
        n = norm(v)
        if n is None:
            logger.warning("learn_weekly topic_scorer: ignoring malformed %s entry %r", name, v)
            continue
        out.add(n.lower() if norm is _norm_slug else n)
    return out


def _items(sig: TickerSignal) -> Tuple[str, ...]:
    """The 8-K item codes, stripped. A bare string is malformed: `"2.02" in "12.02"` would match."""
    raw = sig.items_8k
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (tuple, list, set, frozenset)):
        if raw:
            logger.warning("learn_weekly topic_scorer: %s items_8k is a %s, not a sequence — ignored",
                           sig.ticker, type(raw).__name__)
        return ()
    return tuple(i.strip() for i in raw if isinstance(i, str))


# ── Public scoring API ────────────────────────────────────────────────────────────────────────


def robust_z(x: Any, baseline: Any) -> Optional[float]:
    """`(x − median) / (1.4826 · max(MAD, 1))`, clamped to [−5, 10]; None when it cannot be computed.

    None when `x` is missing or malformed (None, bool, NaN/inf, negative, non-number), or when the
    baseline has fewer than 4 usable values (finite, non-negative, non-bool numbers — malformed
    weeks are dropped, never read as zero).
    """
    fx = _as_count(x)
    if fx is None:
        return None
    if baseline is None or isinstance(baseline, (str, bytes)):
        return None
    try:
        usable = [c for c in (_as_count(v) for v in baseline) if c is not None]
    except TypeError:                     # not iterable
        return None
    if len(usable) < MIN_BASELINE_WEEKS:
        return None
    med = statistics.median(usable)
    mad = statistics.median(abs(v - med) for v in usable)
    z = (fx - med) / (MAD_SCALE * max(mad, MAD_FLOOR))
    return min(Z_MAX, max(Z_MIN, z))


def exclusion_reason(sig: TickerSignal) -> Optional[str]:
    """Why this ticker may not be ranked at all, or None. Fail-closed (see the module docstring)."""
    if _norm_ticker(getattr(sig, "ticker", None)) is None:
        return EXCLUDED_BAD_TICKER
    if sig.excluded_reason is not None:
        return EXCLUDED_UPSTREAM
    if sig.size_ok is not True:
        return EXCLUDED_SIZE
    # Only an explicit False/None passes: a truthy non-bool ("yes", 1) is a malformed row.
    if sig.foreign_filer is not None and sig.foreign_filer is not False:
        return EXCLUDED_FOREIGN
    return None


def ticker_score(sig: TickerSignal) -> Optional[Tuple[float, Dict[str, float]]]:
    """`0.6·z_news + 0.4·z_8k (+0.5 when earnings week AND an Item 2.02 8-K)`, with its components.

    None when the ticker is excluded or its news z is unavailable (missing count or a baseline too
    short to judge). A missing 8-K z counts as 0 — SEC is the secondary signal — and is flagged in
    `components["z_8k_missing"]` so the topic card shows it. Components: `z_news`, `z_8k`,
    `z_8k_missing`, `earnings_bonus`, `saturated` (all floats; flags are 1.0 / 0.0). A saturated news
    count is a lower bound, so its z is too (the true score can only be higher).
    """
    if exclusion_reason(sig) is not None:
        return None
    z_news = robust_z(sig.news_week, sig.news_baseline)
    if z_news is None:
        return None
    z_8k = robust_z(sig.filings_8k_week, sig.filings_8k_baseline)
    z_8k_missing = z_8k is None
    if z_8k is None:
        z_8k = 0.0
    bonus = EARNINGS_BONUS if (sig.earnings_this_week is True and EARNINGS_ITEM in _items(sig)) else 0.0
    score = W_NEWS * z_news + W_8K * z_8k + bonus
    components = {
        "z_news": z_news,
        "z_8k": z_8k,
        "z_8k_missing": 1.0 if z_8k_missing else 0.0,
        "earnings_bonus": bonus,
        "saturated": 1.0 if sig.news_saturated is True else 0.0,
    }
    return score, components


def dedupe_signals(signals: Iterable[TickerSignal]) -> List[TickerSignal]:
    """One signal per normalised ticker, the FIRST kept (WARNING per duplicate). Bad tickers pass
    through so `exclusion_reason` reports them."""
    seen: Set[str] = set()
    out: List[TickerSignal] = []
    for sig in signals:
        t = _norm_ticker(getattr(sig, "ticker", None))
        if t is not None:
            if t in seen:
                logger.warning("learn_weekly topic_scorer: duplicate TickerSignal for %s — keeping the first", t)
                continue
            seen.add(t)
        out.append(sig)
    return out


def score_tickers(signals: Iterable[TickerSignal]) -> Dict[str, Tuple[float, Dict[str, float]]]:
    """Every scoreable ticker (normalised) → (score, components), after `dedupe_signals`.

    Exposed so the topic card can show each member's z-scores next to the ranked candidates.
    """
    scores: Dict[str, Tuple[float, Dict[str, float]]] = {}
    for sig in dedupe_signals(signals):
        scored = ticker_score(sig)
        if scored is not None:
            scores[_norm_ticker(sig.ticker)] = scored       # type: ignore[index]  # not excluded ⇒ valid
    return scores


# ── Themes ────────────────────────────────────────────────────────────────────────────────────


def _theme_members(theme: ThemeInput) -> Tuple[str, ...]:
    raw = theme.members
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (tuple, list, set, frozenset)):
        logger.warning("learn_weekly topic_scorer: theme %r members is a %s, not a sequence — no members",
                       theme.slug, type(raw).__name__)
        return ()
    out: List[str] = []
    for m in raw:
        t = _norm_ticker(m)
        if t is None:
            logger.warning("learn_weekly topic_scorer: theme %r has a malformed member %r — ignored", theme.slug, m)
        elif t not in out:                 # listed twice in ONE theme counts once
            out.append(t)
    return tuple(out)


def _ranked_members(members: Sequence[str],
                    scores: Mapping[str, Tuple[float, Dict[str, float]]]) -> List[Tuple[str, float]]:
    """Scored members by (score desc, ticker asc) — the only order sums and cuts ever see."""
    scored = [(t, scores[t][0]) for t in members if t in scores]
    scored.sort(key=lambda p: (-p[1], p[0]))
    return scored


def _dedupe_themes(themes: Iterable[ThemeInput]) -> List[Tuple[str, ThemeInput]]:
    """(normalised slug, theme) per slug, the FIRST kept; malformed slugs dropped. Both WARN."""
    seen: Set[str] = set()
    out: List[Tuple[str, ThemeInput]] = []
    for th in themes:
        slug = _norm_slug(getattr(th, "slug", None))
        if slug is None:
            logger.warning("learn_weekly topic_scorer: theme with a malformed slug %r — ignored",
                           getattr(th, "slug", None))
            continue
        if slug.lower() in seen:
            logger.warning("learn_weekly topic_scorer: duplicate theme slug %s — keeping the first", slug)
            continue
        seen.add(slug.lower())
        out.append((slug, th))
    return out


def _title(slug: str, theme: ThemeInput) -> str:
    title = theme.title.strip() if isinstance(theme.title, str) else ""
    if not title:
        logger.warning("learn_weekly topic_scorer: theme %s has no title — using the slug", slug)
        return slug
    return title


def _theme_candidate(slug: str, theme: ThemeInput,
                     scores: Mapping[str, Tuple[float, Dict[str, float]]]) -> Optional[TopicCandidate]:
    ranked = _ranked_members(_theme_members(theme), scores)
    strong = sum(1 for _, s in ranked if s > THEME_MEMBER_FLOOR)
    if strong < THEME_MIN_STRONG:
        return None
    top = ranked[:THEME_TOP_K]
    total = 0.0
    for _, s in top:                       # summed in ranked order: float-deterministic
        total += s
    mean = total / len(top)
    components: Dict[str, float] = {
        "mean_top3": mean,
        "members_scored": float(len(ranked)),
        "members_above_floor": float(strong),
    }
    for t, s in top:
        components[f"score:{t}"] = s
    return TopicCandidate(kind=KIND_THEME, key=slug, title=_title(slug, theme), score=mean,
                          tickers=tuple(t for t, _ in top), components=components, reason=REASON_RANKED)


def _last_covered_order(slug: str, value: Any) -> Tuple[int, str, str]:
    """Never covered first, then the oldest date, then the slug. An unreadable date sorts LAST:
    we cannot tell how recent it was, and putting it first could repeat a recent theme."""
    if value is None:
        return (0, "", slug)
    if isinstance(value, datetime):
        return (1, value.date().isoformat(), slug)
    if isinstance(value, date):
        return (1, value.isoformat(), slug)
    if isinstance(value, str) and _ISO_DATE_RE.match(value.strip()):
        try:
            return (1, date.fromisoformat(value.strip()[:10]).isoformat(), slug)
        except ValueError:
            pass
    logger.warning("learn_weekly topic_scorer: theme %s has an unreadable last_covered %r — ordered last",
                   slug, value)
    return (2, "", slug)


def quiet_week_fallback(themes: Iterable[ThemeInput], *, recent_themes: Iterable[str],
                        scores: Optional[Mapping[str, Tuple[float, Dict[str, float]]]] = None,
                        ) -> List[TopicCandidate]:
    """The least-recently-covered ELIGIBLE theme outside `recent_themes`, as one score-0 candidate.

    Used when nothing qualifies (and by the caller after `ScoringAborted`, without `scores`). Its
    `tickers` are the theme's top scored members when `scores` is given, else empty: nothing carried
    a score, so none is claimed. No eligible theme left → [] and the caller skips the week.
    """
    recent = _window(recent_themes, _norm_slug, "recent_themes")
    pool = [(slug, th) for slug, th in _dedupe_themes(themes)
            if th.eligible is True and slug.lower() not in recent]
    if not pool:
        logger.warning("learn_weekly topic_scorer: quiet week and no eligible theme outside the "
                       "%d recent ones — nothing to offer", len(recent))
        return []
    slug, theme = min(pool, key=lambda p: _last_covered_order(p[0], p[1].last_covered))
    ranked = _ranked_members(_theme_members(theme), scores or {})[:THEME_TOP_K]
    logger.info("learn_weekly topic_scorer: quiet-week fallback → theme %s (last covered %r)",
                slug, theme.last_covered)
    return [TopicCandidate(kind=KIND_THEME, key=slug, title=_title(slug, theme), score=0.0,
                           tickers=tuple(t for t, _ in ranked),
                           components={"members_scored": float(len(ranked))},
                           reason=REASON_QUIET_WEEK)]


# ── Ranking ───────────────────────────────────────────────────────────────────────────────────


def rank_topics(signals: Iterable[TickerSignal], themes: Iterable[ThemeInput], *,
                recent_themes: Iterable[str], recent_companies: Iterable[str],
                top_n: int = 3) -> List[TopicCandidate]:
    """The week's top `top_n` topics, best first; the quiet-week fallback when none qualifies.

    * `recent_themes` — Deep Dive theme slugs of the last 4 weeks (never offered again).
    * `recent_companies` — tickers that were a main subject in the last 12 weeks (never offered as a
      single-ticker topic; they may still lift a theme they belong to).
    * Raises `ScoringAborted` when > 20% of the non-excluded universe has no news count (exactly
      20% does not abort), and `TypeError` / `ValueError` on a caller bug (see `_window`, `top_n`).

    A theme qualifies when eligible, outside `recent_themes`, and ≥ 2 members score above 1.0; it
    scores the mean of its top 3 members. A ticker in two themes counts in both. A ticker qualifies
    alone at a score ≥ 3 outside `recent_companies`. Candidates are walked by (score desc, key asc,
    kind); a ticker that is the TOP member of a selected theme is dropped — whichever ranks higher,
    because a theme's mean can never exceed its top member's score, so "only when the theme ranks
    higher" would fire on exact ties alone and the card would offer the same story twice.
    """
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError(f"top_n must be a positive int, got {top_n!r}")
    recent_theme_set = _window(recent_themes, _norm_slug, "recent_themes")
    recent_company_set = _window(recent_companies, _norm_ticker, "recent_companies")
    themes = list(themes)

    unique = dedupe_signals(signals)
    excluded: Dict[str, int] = {}
    universe = 0
    missing: List[str] = []
    for sig in unique:
        reason = exclusion_reason(sig)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        universe += 1
        if _as_count(sig.news_week) is None:
            missing.append(_norm_ticker(sig.ticker))           # type: ignore[arg-type]
    if missing and len(missing) * 100 > universe * MAX_MISSING_PERCENT:
        sample = sorted(missing)[:10]
        # WARNING, not ERROR: a handled degradation — the caller falls back and alerts the owner.
        logger.warning("learn_weekly topic_scorer: aborting — news missing for %d of %d non-excluded "
                     "tickers (excluded %s); first missing: %s",
                     len(missing), universe, dict(sorted(excluded.items())), ", ".join(sample))
        raise ScoringAborted(len(missing), universe, sample)

    scores = score_tickers(unique)
    unscoreable = universe - len(missing) - len(scores)
    logger.info("learn_weekly topic_scorer: universe %d (excluded %s), news missing %d, "
                "short baseline %d, scored %d", universe, dict(sorted(excluded.items())),
                len(missing), unscoreable, len(scores))
    if universe and unscoreable * 100 > universe * MAX_MISSING_PERCENT:
        # Not an abort (a new listing has no 8-week history), but this many means an upstream bug.
        logger.warning("learn_weekly topic_scorer: %d of %d tickers have too short a news baseline "
                       "to score — check signals.py", unscoreable, universe)

    candidates: List[TopicCandidate] = []
    for slug, theme in _dedupe_themes(themes):
        if theme.eligible is not True or slug.lower() in recent_theme_set:
            continue
        cand = _theme_candidate(slug, theme, scores)
        if cand is not None:
            candidates.append(cand)
    for ticker in sorted(scores):
        score, components = scores[ticker]
        if score >= TICKER_MIN_SCORE and ticker not in recent_company_set:
            candidates.append(TopicCandidate(kind=KIND_TICKER, key=ticker, title=ticker, score=score,
                                             tickers=(ticker,), components=dict(components),
                                             reason=REASON_RANKED))
    candidates.sort(key=lambda c: (-c.score, c.key, c.kind))

    selected: List[TopicCandidate] = []
    claimed: Set[str] = set()              # top members of selected themes
    for cand in candidates:
        if len(selected) >= top_n:
            break
        if cand.kind == KIND_TICKER:
            if cand.key in claimed:
                continue
        else:
            top_member = cand.tickers[0]
            claimed.add(top_member)
            # A ticker already taken above this theme is the same story: drop it, the slot refills.
            selected = [c for c in selected if not (c.kind == KIND_TICKER and c.key == top_member)]
        selected.append(cand)

    if not selected:
        logger.info("learn_weekly topic_scorer: no theme or ticker qualified — quiet week")
        return quiet_week_fallback(themes, recent_themes=recent_theme_set, scores=scores)
    logger.info("learn_weekly topic_scorer: top %d → %s", len(selected),
                ", ".join(f"{c.kind}:{c.key}={c.score:.2f}" for c in selected))
    return selected
