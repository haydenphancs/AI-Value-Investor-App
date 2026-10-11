"""News-sentiment timeline for the Updates tab (migration 180).

WHAT IT IS
  How many headlines Cay AI scored bullish / bearish / neutral for one scope (a ticker, a coin
  pair or ``__MARKET__``) on each ET day, over 7 / 30 / 90 days.

WHERE THE LABELS COME FROM — nothing new is generated
  Every label already exists: the per-article enrichment
  (``news_cache_service._enrich_articles_uncached``) writes ``ticker_news_cache.sentiment``
  and the Updates sweeper runs it every 15 minutes. That table keeps no history — rows are
  deleted ~6 h after their last refresh — so :func:`record_labels` copies each NEW label into
  ``news_sentiment_log`` at the one place labels are written. It costs one batched insert
  per enrichment call and no model call.

  Deliberately NOT at ingest: five different writers fetch news (sweeper refresh, first
  fetch, the Analysis tab's 14-day persist, the chat tool, the index path) and each sees a
  different slice, so an "unscored" count would depend on who happened to open which screen.

WHAT IS STORED — labels, never text
  scope, md5(external_id) as a uuid, the ET day, the label, its confidence. No headline, URL,
  summary or publisher: migration 104 removed our last long-term copy of news text, and this
  table must not become a second one.

WHAT COUNTS — the chart gate (2026-10-10)
  A blind re-label of 200 live headlines by three independent raters found the labeller
  over-directional (69 bullish where the raters saw 50, 69 neutral where they saw 98). The
  question now names the scope as its subject and lets "neutral" be a normal answer
  (``news_cache_service.SENTIMENT_RUBRIC``), and :func:`tone_skip_reason` keeps three kinds
  of headline OFF the chart (they stay in the feed): an article the model says is only
  "passing" for the scope (about another company or asset — on the Market line, a story the
  Market feed hides), a quarterly-holdings filing, and a law-firm solicitation. Wire copies
  of one story in one batch count once. A gated article is still LOGGED, with its
  ``skip_reason`` (migration 191), so the verdict is first-label-wins like the label;
  news_sentiment_daily() counts only reason-less rows. Each row records the rules that wrote
  it (``labeller``, :data:`TONE_LABELLER_VERSION`).

HONESTY RULES the endpoint and the chart rely on
  * First label wins (ON CONFLICT DO NOTHING). A re-fetch after expiry can reset a row and
    the model can label it differently; an article is counted once, with its first label.
  * An article labelled more than :data:`MAX_LABEL_AGE_HOURS` after it was published is
    skipped. Inside that window a label CAN still land on an earlier day — the equity
    sweep sleeps 20:00–04:00 ET and over the weekend, so Friday-evening news is scored on
    Monday morning — which is why every day inside it is sent as ``is_partial`` ("so far"),
    not just today. Older days are final.
  * Days with no labelled article are ABSENT, never zero: "nothing scored" is not "no news".
  * The labels cover what the sweeper and readers enriched — Market plus the top-200
    watchlist tickers, the newest 25 in-window articles per pass — not every article
    published. The card counts "headlines", never "all news", and the chat is told they are
    the headlines Cay AI scored (:func:`summarize_tone`).
  * ``tracking_since`` is the OLDEST DAY STILL ON FILE (``MIN(et_day)``), not a stored first
    day: the sweep keeps :data:`RETENTION_DAYS` and nothing records a scope's start beyond it.
    It is the day scoring began only until the feed's history reaches that edge; from then on
    it moves forward daily, so the card says "120+ days" and the chat "at least 120 days"
    instead of a date (:func:`at_retention_edge`, :func:`since_phrase`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.inflight import fail_shared_future
from app.utils.market_hours import ET, to_utc_instant

logger = logging.getLogger(__name__)

TABLE = "news_sentiment_log"
DAILY_RPC = "news_sentiment_daily"

#: The windows the endpoint serves. Must equal the iOS toggle's options.
TREND_DAYS: Tuple[int, ...] = (7, 30, 90)
#: Rows older than this are swept. Covers the longest window with a month to spare.
RETENTION_DAYS = 120
#: An article labelled later than this after publication is not logged (see module doc).
#: Equal to news_cache_service.REFRESH_LOOKBACK_HOURS: the sweeper never re-fetches older.
MAX_LABEL_AGE_HOURS = 96
#: A published_at this far in the future is a bad timestamp; the label day falls back to
#: the labelling day instead of charting news on a day that has not happened.
_FUTURE_SKEW = timedelta(hours=2)

SENTIMENTS = ("bullish", "bearish", "neutral")
#: Must equal the CHECK on news_sentiment_log.source (migration 181; test-pinned).
SOURCES = ("live", "seed", "backfill")
_SENTIMENT_SET = frozenset(SENTIMENTS)

_CACHE_TTL_SECONDS = 300
_BUILDING_TTL_SECONDS = 30
_CACHE_MAX_ENTRIES = 2048

BACKFILL_TABLE = "news_sentiment_backfill"
HISTORY_BUILDING = "building"
HISTORY_READY = "ready"
#: The newest days are always covered by the live labels and the nightly top-up, so a
#: history counts as "ready" once the backfill reaches this many days before today.
_READY_RECENT_SLACK_DAYS = 3


class SentimentTrendUnavailable(Exception):
    """The label log could not be read (Supabase blip, or migration 180 not applied)."""


# ── Pure helpers ───────────────────────────────────────────────────────────────


def article_key(external_id: Any) -> Optional[str]:
    """``md5(external_id)::uuid`` as Postgres computes it, or None when it is not an identity.

    Hashes the exact stored value (not stripped): the migration's seed runs ``md5()`` over
    the column as-is, and a live row must land on the same key or the seed and the first
    live write would count one article twice. ``unknown_N`` ids are POSITIONAL placeholders
    (``_build_and_cache_rows`` falls back to them when an article has neither url nor
    title), so two different articles can share one — never an identity.
    """
    if not isinstance(external_id, str) or not external_id:
        return None
    if external_id.startswith("unknown_"):
        return None
    digest = hashlib.md5(external_id.encode("utf-8"), usedforsecurity=False).hexdigest()
    return str(uuid.UUID(digest))


def et_day(value: Any) -> Optional[date]:
    """The ET calendar day of a timestamp (str or datetime), or None when unparseable.

    Naive strings are FMP wall clock in New York (``to_utc_instant``); a naive datetime is
    read as UTC, like ``earnings_window_service.et_date``.
    """
    if isinstance(value, datetime):
        instant = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        instant = to_utc_instant(value)
    if instant is None:
        return None
    return instant.astimezone(ET).date()


def normalize_sentiment(value: Any) -> Optional[str]:
    """bullish / bearish / neutral, or None for anything else (never a default)."""
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if s == "positive":
        return "bullish"
    if s == "negative":
        return "bearish"
    return s if s in _SENTIMENT_SET else None


def _clamp_confidence(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    # OverflowError: int(float("inf")); a NaN raises ValueError. Either must cost the
    # confidence only — never the batch (build_log_rows runs over every row of it).
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(100, n))


#: Which labelling rules wrote a log row (`news_sentiment_log.labeller`, migration 191).
#: 2 = the scope-specific question with a `relevance` answer and the chart gate
#: (:func:`tone_skip_reason`), 2026-10-10. Rows from earlier rules carry NULL; migration 192
#: deletes them for the re-labellable scopes and re-queues their backfill. Bump it whenever
#: the question or the gate changes what a label means.
TONE_LABELLER_VERSION = 2
#: The ET day the version-2 rules went live. Scopes the re-label (migration 192) cannot rebuild
#: — the Market line, an unsupported index — keep older days labelled by the old rules, so the
#: chat is told not to compare across this date (`summarize_tone`).
TONE_RULES_SINCE = date(2026, 10, 10)

#: The model's answer to "what is this article about, relative to the scope?" (the enrichment
#: prompt's `relevance` item). Only "passing" keeps a label off the chart.
RELEVANCE_VALUES = ("primary", "secondary", "passing")


def normalize_relevance(value: Any) -> Optional[str]:
    """primary / secondary / passing, or None for anything else (a missing answer)."""
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    return s if s in RELEVANCE_VALUES else None


def is_trend_scope(scope: Any) -> bool:
    """Whether ``scope`` is one the News Tone endpoint can read — the ONE rule, shared with
    ``GET /updates/sentiment-trend``'s validator. A label logged under any other key (the
    commodity detail screen's ``COMMODITY_GC``) can never be charted."""
    if not isinstance(scope, str) or not scope or len(scope) > 32:
        return False
    if scope == "__MARKET__":
        return True
    # FMP symbols: letters, digits, and . - ^ = (indices, crypto pairs, futures).
    return all(c.isalnum() or c in ".-^=" for c in scope)


def tone_skip_reason(row: Dict[str, Any]) -> Optional[str]:
    """Why a labelled article must stay off the News Tone chart, or None when it counts. Pure.

    Measured 2026-10-10 against three blind raters: most of the labeller's over-direction came
    from headlines that are not news about the scope — a story about another company or asset
    (the model's own `relevance` answer "passing"), the syndicated quarterly-holdings template
    and law-firm solicitations (`news_tone_filters`). They stay in the feed; only the chart
    skips them. A missing `relevance` (an older answer, or a provider that dropped the field)
    counts, as before — a schema hiccup must never silently empty the chart."""
    from app.services.news_tone_filters import is_law_firm_solicitation, is_routine_holdings_filing

    if normalize_relevance(row.get("relevance")) == "passing":
        return "off_topic"
    title = row.get("headline")
    if not isinstance(title, str) or not title:
        title = row.get("title")
    if is_routine_holdings_filing(title):
        return "holdings_filing"
    if is_law_firm_solicitation(title):
        return "solicitation"
    return None


def build_log_rows(
    scope: str,
    rows: Iterable[Dict[str, Any]],
    *,
    now: datetime,
    source: str = "live",
    max_age_hours: Optional[int] = MAX_LABEL_AGE_HOURS,
    undated: str = "labelled_day",
    oldest_day: Optional[date] = None,
    model: Optional[str] = None,
    skipped: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Log rows for the labelled cache rows of ONE scope. Pure.

    Skips a row with no identity or no valid label. A row :func:`tone_skip_reason` keeps off the
    chart, and a second copy of a title already in this batch (wire copies of one story under
    different URLs, on the same ET day), is logged WITH its ``skip_reason`` — kept off the chart
    by news_sentiment_daily(), and first-label-wins like any label. The defaults are the LIVE
    writer's rules: a label made more than
    :data:`MAX_LABEL_AGE_HOURS` after publication is skipped, and a row without a usable
    ``published_at`` (or one dated more than ``_FUTURE_SKEW`` ahead) is charted on the
    labelling day. A date up to ``_FUTURE_SKEW`` ahead is clamped to the labelling day, so a
    label written at 23:30 ET never lands on tomorrow's bar (which the chart hides until
    tomorrow).

    The backfill passes ``source="backfill"``, ``max_age_hours=None`` (history is old by
    definition), ``undated="skip"`` (an undated historical article must not land on today's
    bar) and ``oldest_day`` (nothing before the backfill horizon). ``model`` records which
    model produced the label. ``skipped`` (optional) counts the gate's reasons (and undated
    rows), for the caller's one log line.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    if undated not in ("labelled_day", "skip"):
        raise ValueError(f"undated must be 'labelled_day' or 'skip', got {undated!r}")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    scope = (scope or "").strip()
    if not is_trend_scope(scope):
        return []
    from app.services.news_tone_filters import title_key

    oldest = now - timedelta(hours=max_age_hours) if max_age_hours is not None else None
    labelled_day = now.astimezone(ET).date()
    model = (model or "").strip()[:80] or None
    out: List[Dict[str, Any]] = []
    seen: set = set()
    seen_titles: set = set()

    def _skip(reason: str) -> None:
        if skipped is not None:
            skipped[reason] = skipped.get(reason, 0) + 1

    for row in rows:
        if not isinstance(row, dict):
            continue
        key = article_key(row.get("external_id"))
        sentiment = normalize_sentiment(row.get("sentiment"))
        if key is None or sentiment is None or key in seen:
            continue
        published = row.get("published_at")
        instant = (
            (published if published.tzinfo else published.replace(tzinfo=timezone.utc))
            if isinstance(published, datetime)
            else to_utc_instant(published)
        )
        if oldest is not None and instant is not None and instant < oldest:
            continue
        if instant is None or instant > now + _FUTURE_SKEW:
            if undated == "skip":
                _skip("undated")
                continue
            day = labelled_day
        else:
            day = min(instant.astimezone(ET).date(), labelled_day)
        if oldest_day is not None and day < oldest_day:
            continue
        # The gate: a row it keeps off the chart is STILL logged, with its reason, so the
        # verdict is first-label-wins like the label (the backfill never re-asks about it, and
        # a later answer cannot put it back). news_sentiment_daily() counts only reason-less rows.
        reason = tone_skip_reason(row)
        # Keyed with the day: a recurring generic title ("Stock market today") on two days is
        # two stories; wire copies of one story share the day.
        tkey = title_key(row.get("headline") if isinstance(row.get("headline"), str) else row.get("title"))
        tkey = (tkey, day) if tkey is not None else None
        if reason is None and tkey is not None and tkey in seen_titles:
            reason = "duplicate_title"
        if reason is not None:
            _skip(reason)
        elif tkey is not None:
            seen_titles.add(tkey)
        seen.add(key)
        entry = {
            "scope": scope,
            "article_key": key,
            "et_day": day.isoformat(),
            "sentiment": sentiment,
            "confidence": _clamp_confidence(row.get("sentiment_confidence")),
            "source": source,
            "labelled_at": now.isoformat(),
            "labeller": TONE_LABELLER_VERSION,
            "skip_reason": reason,
        }
        if model:
            entry["model"] = model
        out.append(entry)
    return out


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):   # OverflowError: int(float("inf"))
        return 0
    return n if n > 0 else 0


def net_score(bullish: int, bearish: int, total: int) -> int:
    """(bullish − bearish) / total, as a whole percent in [−100, 100]; 0 when total is 0.

    Halves round AWAY from zero (12.5 → 13, −12.5 → −13) in integer arithmetic — the rule
    Swift's ``Double.rounded()`` uses, which is how the iOS chart recomputes this number.
    Python's ``round`` rounds halves to EVEN, so the chat would have said "net +12" under a
    chart reading "net +13".
    """
    if total <= 0:
        return 0
    diff = (bullish - bearish) * 100
    magnitude = (abs(diff) * 2 + total) // (2 * total)
    return max(-100, min(100, magnitude if diff >= 0 else -magnitude))


def open_since(now: datetime) -> date:
    """The oldest ET day that can still gain a label: the day of ``now − MAX_LABEL_AGE_HOURS``."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - timedelta(hours=MAX_LABEL_AGE_HOURS)).astimezone(ET).date()


def shape_series(
    raw: Iterable[Dict[str, Any]], *, today: date, since: date, partial_from: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """RPC rows → the wire series, oldest first. Pure.

    Drops a malformed row, a day outside ``[since, today]`` and a day whose counts are all
    zero (absent means "nothing scored", and the chart must not draw a flat zero there).
    ``is_partial`` marks every day from ``partial_from`` (default: today) — the days a late
    label can still land on, see the module docstring.
    """
    partial_from = partial_from or today
    by_day: Dict[date, Dict[str, int]] = {}
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        # `_series_day` refuses a datetime: it IS a `date` subclass, and comparing it with the
        # window's dates below raised a TypeError outside every guard (a 500, not a 503).
        day = _series_day(row.get("day"))
        if day is None:
            continue
        if day < since or day > today:
            continue
        counts = {s: _count(row.get(s)) for s in SENTIMENTS}
        if sum(counts.values()) == 0:
            continue
        by_day[day] = counts
    series = []
    for day in sorted(by_day):
        c = by_day[day]
        total = c["bullish"] + c["bearish"] + c["neutral"]
        series.append({
            "date": day.isoformat(),
            "bullish": c["bullish"],
            "bearish": c["bearish"],
            "neutral": c["neutral"],
            "total": total,
            "net_score": net_score(c["bullish"], c["bearish"], total),
            "is_partial": day >= partial_from,
        })
    return series


def _window_net(series: List[Dict[str, Any]], start: date, end: date) -> Optional[int]:
    bull = bear = total = 0
    for d in series:
        day = date.fromisoformat(d["date"])
        if start <= day <= end:
            bull += d["bullish"]
            bear += d["bearish"]
            total += d["total"]
    return net_score(bull, bear, total) if total else None


def _fmt_day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def at_retention_edge(tracking_since: date, today: date) -> bool:
    """True when a scope's oldest label on file sits at (or past) the log's retention edge.

    ``tracking_since`` is ``MIN(et_day)`` over what the log still holds, and
    :meth:`NewsSentimentTrendService.sweep_expired` deletes every day before
    ``today − RETENTION_DAYS``. So once a scope's oldest kept day is that old, its first days
    are gone (or go at the next sweep) and the date no longer marks when Cay AI started scoring
    the feed — it moves forward every day. The app mirrors this rule
    (``SentimentTrend.historyReachesRetentionEdge``, test-pinned).
    """
    return tracking_since <= today - timedelta(days=RETENTION_DAYS)


def since_phrase(tracking_since: Optional[date], today: date) -> str:
    """How far back a scope's scored headlines go, as a clause for chat grounding ("" when the
    answer carried no date). Pure.

    What the data supports, and no more. The log keeps RETENTION_DAYS and no record of a
    scope's first scored day survives the sweep, so the date is worded as the oldest headline
    ON FILE — the start of scoring for a young feed, but after a gap in a sparse feed's coverage
    an older, swept day can hide behind it. At the retention edge the date is dropped for "at
    least RETENTION_DAYS days", which stays true and stops the daily drift.
    """
    if tracking_since is None:
        return ""
    if at_retention_edge(tracking_since, today):
        # True on EVERY edge day, including exactly RETENTION_DAYS back, when that day is still
        # on file (the sweep deletes only older days) — so never claim the first day is gone.
        return (f"; scored for at least {RETENTION_DAYS} days (the log keeps only the last "
                f"{RETENTION_DAYS} days and records no start date)")
    return (f"; oldest scored headline on file: {_fmt_day(tracking_since)} "
            f"(the log keeps {RETENTION_DAYS} days)")


_BUILDING_NOTE = (
    "This feed's 90-day history is still being built, so older days in this window "
    "are incomplete: do not read a trend from them yet."
)
#: The same caveat when no window detail carries it (the focus window has no scored day).
_BUILDING_NOTE_WINDOWS = (
    "This feed's 90-day history is still being built, so its older days are incomplete: "
    "do not read a trend from them yet."
)


def summarize_trend(
    series: List[Dict[str, Any]], *, days: int, today: date, tracking_since: Optional[date] = None,
    history_status: Optional[str] = None, partial_from: Optional[date] = None,
) -> Optional[str]:
    """A compact, figure-exact text of the series for chat grounding, or None when empty.

    Pure and deterministic, so the chat sees exactly the numbers the chart draws — for the
    window the chart is showing (`days`). While the 90-day history is still being built
    (`history_status` "building"), older days are NOT final and the text says so: the chart's
    legend row says "filling in 90 days…", and the model must not call a trend from a history
    that is still arriving.

    ``partial_from`` is the oldest ET day a late label can still land on
    (:func:`open_since`); the "still filling in" sentence starts there. Without it the
    sentence starts at the first day the series marks partial — which, when the open days
    have no row yet, is later than the truth and called open days "final".
    """
    if not series:
        return None
    bull = sum(d["bullish"] for d in series)
    bear = sum(d["bearish"] for d in series)
    neut = sum(d["neutral"] for d in series)
    total = bull + bear + neut
    parts = [
        f"News tone over the last {days} days (headlines Cay AI scored, by ET day"
        + since_phrase(tracking_since, today)
        + f"): {total} scored — {bull} bullish, {bear} bearish, {neut} neutral; "
        f"net tone {_signed(net_score(bull, bear, total))}% on a -100..+100 scale."
    ]
    recent = _window_net(series, today - timedelta(days=6), today)
    prior = _window_net(series, today - timedelta(days=13), today - timedelta(days=7))
    if days > 7 and recent is not None and prior is not None:
        parts.append(f"Last 7 days net {_signed(recent)}% vs the 7 days before {_signed(prior)}%.")
    most_bearish = max(series, key=lambda d: (d["bearish"] - d["bullish"], d["bearish"]))
    most_bullish = max(series, key=lambda d: (d["bullish"] - d["bearish"], d["bullish"]))
    if most_bearish["bearish"] > most_bearish["bullish"]:
        d = date.fromisoformat(most_bearish["date"])
        parts.append(
            f"Most bearish day: {_fmt_day(d)} ({most_bearish['bearish']} bearish vs "
            f"{most_bearish['bullish']} bullish)."
        )
    if most_bullish["bullish"] > most_bullish["bearish"]:
        d = date.fromisoformat(most_bullish["date"])
        parts.append(
            f"Most bullish day: {_fmt_day(d)} ({most_bullish['bullish']} bullish vs "
            f"{most_bullish['bearish']} bearish)."
        )
    building = history_status == HISTORY_BUILDING
    window_start = today - timedelta(days=days - 1)
    if partial_from is not None:
        first: Optional[date] = max(partial_from, window_start) if partial_from <= today else None
    else:
        partial = [d for d in series if d["is_partial"]]
        first = date.fromisoformat(partial[0]["date"]) if partial else None
    if first is not None:
        parts.append(
            f"Counts from {_fmt_day(first)} on are still filling in (late articles are scored "
            "overnight and after weekends)" + ("." if building else "; earlier days are final.")
        )
    if building:
        parts.append(_BUILDING_NOTE)
    return " ".join(parts)


#: The net score at which a window without a majority side still "leans" (owner, 2026-10-10:
#: "Majority-based + %"). Mirrors iOS `NewsSentimentTrendChart.toneWord` (pinned equal by
#: tests/test_chat_updates_scope_context.py), so the chat names the card's verdict in the
#: card's own words.
TONE_WORD_THRESHOLD = 20


def tone_word(bullish: int, bearish: int, neutral: int) -> str:
    """The word the News Tone card's summary line shows for a window's counts.

    "Mostly" means a real majority: the old rule said "Mostly positive" at net +20 even when
    most headlines were neutral (2 positive, 6 neutral read "Mostly positive"). In order:
    a side with more than half of all scored headlines is "Mostly positive/negative"; else a
    net score of ±20 or beyond "Leans"; else more than half neutral is "Mostly neutral"; else
    "Mixed". Integer math, so the boundary (exactly half) is never a majority."""
    bullish, bearish, neutral = _count(bullish), _count(bearish), _count(neutral)
    total = bullish + bearish + neutral
    if total <= 0:
        return "Mixed"
    if 2 * bullish > total:
        return "Mostly positive"
    if 2 * bearish > total:
        return "Mostly negative"
    net = net_score(bullish, bearish, total)
    if net >= TONE_WORD_THRESHOLD:
        return "Leaning positive"
    if net <= -TONE_WORD_THRESHOLD:
        return "Leaning negative"
    if 2 * neutral > total:
        return "Mostly neutral"
    return "Mixed"


def _signed(value: int) -> str:
    """The card's own sign rule (iOS `signed`): "+21", "0", "-7" — never "+0"."""
    return f"+{value}" if value > 0 else str(value)


def _series_day(value: Any) -> Optional[date]:
    """A wire day ("YYYY-MM-DD", or a plain `date`) → the date, or None. A `datetime` is an
    instant, not a calendar day, and is refused (it would also break every date comparison)."""
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def coerce_series(series: Any) -> List[Dict[str, Any]]:
    """A wire series made safe to summarise, oldest first. Pure; never raises.

    ``get_trend`` already serves :func:`shape_series` output, but the chat reads whatever the
    service (or its cache) hands back, and one malformed row must cost that row — never the
    whole tone block (:func:`summarize_trend` indexes keys, so a bad row raised and dropped the
    trend as a "failed read"). Mirrors the iOS decoder (``SentimentTrend(dto:)``): a row that
    is not a dict or cannot be dated is skipped, the first row for a day wins, counts are
    non-negative ints (``_count``), an all-zero day is dropped, ``total`` and ``net_score``
    are recomputed from the counts, and ``is_partial`` holds only when it is literally true.
    """
    if not isinstance(series, (list, tuple)):
        return []
    by_day: Dict[date, Dict[str, Any]] = {}
    for row in series:
        if not isinstance(row, dict):
            continue
        day = _series_day(row.get("date"))
        if day is None or day in by_day:
            continue
        counts = {s: _count(row.get(s)) for s in SENTIMENTS}
        total = sum(counts.values())
        if total == 0:
            continue
        by_day[day] = {
            "date": day.isoformat(),
            "bullish": counts["bullish"],
            "bearish": counts["bearish"],
            "neutral": counts["neutral"],
            "total": total,
            "net_score": net_score(counts["bullish"], counts["bearish"], total),
            "is_partial": row.get("is_partial") is True,
        }
    return [by_day[d] for d in sorted(by_day)]


def window_slice(series: List[Dict[str, Any]], *, days: int, today: date) -> List[Dict[str, Any]]:
    """The days of a (coerced) series inside the ``days``-day window ending ``today``.

    The endpoint's own window rule (``_fetch``: ``since = today − (days − 1)``), and the rule
    the app cuts its 7D / 30D views from the 90-day answer with (``SentimentTrend.trimmed``):
    a window of N days is today and the N − 1 ET days before it.
    """
    start = today - timedelta(days=days - 1)
    return [d for d in series if start <= date.fromisoformat(d["date"]) <= today]


def summarize_tone_windows(
    series: Any, *, today: date, tracking_since: Optional[date] = None,
) -> Optional[str]:
    """The News Tone card as one compact clause per window its toggle offers (7D / 30D / 90D),
    for chat grounding. Pure. None when the widest window has no scored headline.

    Every window is cut from ONE series (the 90-day answer) by :func:`window_slice`, so each
    clause equals what the card shows on that window. Written in the card's words — Positive /
    Negative / Neutral, the headline count, the net score with the card's sign rule and the
    summary line's tone word — with one sentence mapping them to Cay AI's bullish / bearish /
    neutral labels, which the headline list and the day-level text use. A window with no
    scored headline says so: the card shows "No scored headlines in this window yet." there.
    """
    rows = coerce_series(series)
    windows: List[Tuple[int, int, int, int]] = []
    for days in sorted(TREND_DAYS):
        cut = window_slice(rows, days=days, today=today)
        windows.append((
            days,
            sum(d["bullish"] for d in cut),
            sum(d["bearish"] for d in cut),
            sum(d["neutral"] for d in cut),
        ))
    if sum(windows[-1][1:]) == 0:
        return None
    clauses = []
    for days, bull, bear, neut in windows:
        total = bull + bear + neut
        if total == 0:
            clauses.append(f"{days}D: no scored headlines")
            continue
        net = net_score(bull, bear, total)
        clauses.append(
            f"{days}D: {total} headline{'' if total == 1 else 's'} ({bull} positive, "
            f"{bear} negative, {neut} neutral), net {_signed(net)}% ({tone_word(bull, bear, neut)})"
        )
    # "as the News Tone card counts it", never "on screen": the app hides the card until a
    # feed has a few days of history, and the model must not point at a card that is not there.
    return (
        "News tone for this feed, as the News Tone card counts it (headlines Cay AI scored, "
        "by ET day" + since_phrase(tracking_since, today)
        + "). The card's Positive / Negative / Neutral are Cay AI's bullish / bearish / neutral "
        "labels; net = (positive - negative) / all scored, as a whole percent from -100% to "
        "+100%. The card leaves out headlines that are not mainly about this feed's subject, "
        "routine fund-holdings filings, law-firm solicitations and repeat wire copies of one "
        "story, so its counts can be lower than the labels in the headline list. "
        + "; ".join(clauses) + "."
    )


def summarize_tone(
    data: Any, *, focus_days: int, today: date, partial_from: Optional[date] = None,
) -> Optional[str]:
    """The Updates chat's whole news-tone grounding, from ONE 90-day ``get_trend`` answer.

    Two parts: :func:`summarize_tone_windows` (every window the card offers, and how far back
    the feed's scored headlines on file go — :func:`since_phrase`), then
    :func:`summarize_trend`'s day-level detail for ``focus_days`` — the window the chart was
    showing when the chat opened (``window=N``), or 30. The detail's own date clause is left
    out (the first part says it), and when the
    focus window is empty the building caveat, which the detail would have carried, is added
    here instead. A focus outside the card's windows gets no detail (never a guessed one).
    None when nothing was scored in 90 days, or ``data`` is not a trend answer.
    ``partial_from`` (:func:`open_since` of the read) starts the "still filling in" sentence.
    """
    if not isinstance(data, dict):
        return None
    series = coerce_series(data.get("series"))
    windows = summarize_tone_windows(
        series, today=today, tracking_since=_series_day(data.get("tracking_since")),
    )
    if windows is None:
        return None
    status = data.get("history_status")
    status = status if status in (HISTORY_BUILDING, HISTORY_READY) else None
    detail = (
        summarize_trend(
            window_slice(series, days=focus_days, today=today),
            days=focus_days, today=today, history_status=status, partial_from=partial_from,
        )
        if focus_days in TREND_DAYS else None
    )
    if detail is None and status == HISTORY_BUILDING:
        detail = _BUILDING_NOTE_WINDOWS
    if any(date.fromisoformat(d["date"]) < TONE_RULES_SINCE for d in series):
        # Re-labelled scopes hold no older-rule days after migration 192; this is for the
        # rest (the Market line above all), until the 90-day windows pass the date.
        caveat = (f"Cay AI's labelling rules changed on {_fmt_day(TONE_RULES_SINCE)}, "
                  f"{TONE_RULES_SINCE.year} (more headlines read as neutral; stories not about "
                  "this feed no longer count): compare days on the same side of that date only.")
        detail = f"{detail} {caveat}" if detail else caveat
    return windows + ("\n" + detail if detail else "")


def history_status_for(supabase: Any, scope: str, today: date) -> Optional[str]:
    """See ``NewsSentimentTrendService._history_status``. Blocking; never raises — a failed
    read is logged at WARNING and answers None."""
    try:
        return read_history_status(supabase, scope, today)
    except Exception as e:  # noqa: BLE001
        logger.warning("news_sentiment_backfill status read failed for %s (%s: %s)",
                       scope, type(e).__name__, e)
        return None


def read_history_status(supabase: Any, scope: str, today: date) -> Optional[str]:
    """``building`` / ``ready`` / None. Blocking; RAISES when the read fails, so a caller can
    tell "no backfill row" from "could not read it" (the trend cache does)."""
    from app.config import settings

    if not getattr(settings, "SENTIMENT_BACKFILL_ENABLED", False):
        return None
    if not scope or scope == "__MARKET__" or supabase is None:
        return None
    result = (
        supabase.table(BACKFILL_TABLE)
        .select("status,covered_from,covered_to,attempts")
        .eq("scope", scope)
        .limit(1)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    row = rows[0] if rows and isinstance(rows[0], dict) else None
    status = row.get("status") if row else None
    if status not in ("queued", "running", "done", "failed"):
        return None          # no row yet, 'unsupported', or not a backfill row at all
    if status == "done":
        return HISTORY_READY
    # The worker's own horizon (SENTIMENT_BACKFILL_DAYS clamped to 90): a reader with a wider
    # one would call a finished 90-day history "building" for as long as the setting exceeds 90.
    from app.services.news_sentiment_backfill_service import horizon_for

    horizon = horizon_for(today)
    covered_from = _as_date(row.get("covered_from"))
    covered_to = _as_date(row.get("covered_to"))
    if (covered_from is not None and covered_to is not None
            and covered_from <= horizon and covered_to >= today - timedelta(days=_READY_RECENT_SLACK_DAYS)):
        return HISTORY_READY
    if status in ("queued", "running"):
        return HISTORY_BUILDING
    # 'failed' below the attempt cap is retried within the hour, so an incomplete history is
    # still being built (None here let the card and chat present a partial 90 days as final).
    # AT the cap it is retried once a day and may stay incomplete for weeks (an article the
    # model will never label holds coverage there): that is not "building" — the card would
    # promise "filling in" and the chat "do not read a trend" for as long — so it reads None.
    from app.services.news_sentiment_backfill_service import MAX_ATTEMPTS

    try:
        attempts = int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    return HISTORY_BUILDING if attempts < MAX_ATTEMPTS else None


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


# ── Writes ─────────────────────────────────────────────────────────────────────


#: Set once PostgREST reports an optional column missing — `model` (migration 181) or
#: `labeller` (migration 191) — because the deploy ran ahead of its migration: later writes
#: leave it out instead of failing. Process-local; a restart re-checks.
_model_column_missing = False
_labeller_column_missing = False
_skip_reason_column_missing = False
#: When `labeller` was found missing: the heal re-checks after this long, so a deploy that ran
#: ahead of migration 191 starts writing the column once 191 lands — no restart needed (rows
#: written without it read as the old rules and would survive migration 192's re-label).
_labeller_missing_at = 0.0
_LABELLER_RECHECK_SECONDS = 600.0
#: column -> (flag name, the migration that adds it)
_OPTIONAL_COLUMNS = {
    "model": ("_model_column_missing", 181),
    "labeller": ("_labeller_column_missing", 191),
    "skip_reason": ("_skip_reason_column_missing", 191),
}


def _missing_optional_column(exc: BaseException, rows: List[Dict[str, Any]]) -> Optional[str]:
    """The optional column PostgREST says is missing, when THIS call's rows carry it."""
    text = str(exc).lower()
    # Only PostgREST's "unknown column" answer (PGRST204 "Could not find the 'x' column", or
    # Postgres' "column x … does not exist"): a constraint error that merely NAMES a column
    # (a null or CHECK violation) must raise, never be "healed" by dropping data.
    if not ("pgrst204" in text or "could not find" in text or "does not exist" in text):
        return None
    for col in _OPTIONAL_COLUMNS:
        named = f"'{col}'" in text or f'"{col}"' in text or f"column {col} " in f"{text} "
        if named and any(col in r for r in rows):
            return col
    return None


def _strip_missing(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows as the live schema can take them. Without `skip_reason` (191 not applied) a
    gated row is DROPPED, never written unflagged: unflagged, it would count on the chart."""
    global _labeller_column_missing, _skip_reason_column_missing
    if ((_labeller_column_missing or _skip_reason_column_missing)
            and time.monotonic() - _labeller_missing_at > _LABELLER_RECHECK_SECONDS):
        # Re-check: migration 191 may have landed since.
        _labeller_column_missing = _skip_reason_column_missing = False
    drop = {c for c, (flag, _) in _OPTIONAL_COLUMNS.items() if globals()[flag]}
    if not drop:
        return rows
    if "skip_reason" in drop:
        rows = [r for r in rows if not r.get("skip_reason")]
    return [{k: v for k, v in r.items() if k not in drop} for r in rows]


def upsert_log_rows(supabase: Any, payload: List[Dict[str, Any]]) -> int:
    """Blocking upsert of prepared log rows (first label wins). Returns rows NEWLY written.

    Call through ``asyncio.to_thread``. Raises on failure, except the one it heals: a deploy
    that ran ahead of its migration (181's `model`, 191's `labeller`) is retried without the
    missing column — no live label is lost to the ordering. A row written without `labeller`
    reads as the OLD rules, so migration 192 re-labels it: the heal can cost a re-label,
    never a label.
    """
    if not payload:
        return 0
    rows = _strip_missing(payload)
    for _ in range(len(_OPTIONAL_COLUMNS) + 1):
        try:
            result = (
                supabase.table(TABLE)
                .upsert(rows, on_conflict="scope,article_key", ignore_duplicates=True)
                .execute()
            )
            return len(getattr(result, "data", None) or [])
        except Exception as e:
            # Judged on THIS call's rows, not the flag: a concurrent batch built with the column
            # whose error arrives after another batch set the flag must still retry, not drop.
            col = _missing_optional_column(e, rows)
            if col is None:
                raise
            flag, migration = _OPTIONAL_COLUMNS[col]
            if not globals()[flag]:
                logger.warning(
                    "news_sentiment_log: `%s` column missing (migration %d not applied) — "
                    "writing labels without it until restart", col, migration,
                )
            globals()[flag] = True
            if col in ("labeller", "skip_reason"):
                globals()["_labeller_missing_at"] = time.monotonic()
            rows = _strip_missing(payload)
            if not rows:
                return 0
    raise RuntimeError("news_sentiment_log upsert: optional-column heal did not converge")


async def record_labels(
    supabase: Any,
    scope: str,
    rows: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    model: Optional[str] = None,
) -> int:
    """Log the labels of freshly enriched cache rows. Returns rows NEWLY logged. Never raises.

    Rows may carry ``headline`` and ``relevance`` for the chart gate (:func:`tone_skip_reason`).
    An undated row is skipped, like the backfill does: with no date it cannot be placed on a
    day, and charting it on the labelling day put an old story on today's bar.

    Best-effort by design: enrichment already succeeded and the user already has their
    bullets. A transient Supabase failure is retried (the write is ON CONFLICT DO NOTHING, so
    a replay is harmless); a failure that survives the retries loses the WHOLE batch's labels
    for good (the rows are already marked processed), so it is logged at ERROR with the scope
    and batch size — visible in Sentry, never silent.
    """
    if supabase is None:
        return 0
    skipped: Dict[str, int] = {}
    try:
        payload = build_log_rows(
            scope, rows, now=now or datetime.now(timezone.utc), model=model,
            undated="skip", skipped=skipped,
        )
    except Exception as e:  # noqa: BLE001 — a malformed row must not break enrichment
        logger.warning("news_sentiment_log: could not build rows for %s (%s: %s)",
                       scope, type(e).__name__, e)
        return 0
    if skipped:
        logger.info("news_sentiment_log: %s — kept %d label(s) off the chart (%s)",
                    scope, sum(skipped.values()), dict(sorted(skipped.items())))
    if not payload:
        return 0

    from app.utils.supabase_errors import retry_idempotent_sync

    try:
        return await asyncio.to_thread(
            retry_idempotent_sync,
            lambda: upsert_log_rows(supabase, payload),
            what=f"news_sentiment_log upsert {scope}",
            logger=logger,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "news_sentiment_log: dropped %d label(s) for %s (%s: %s)",
            len(payload), scope, type(e).__name__, e,
        )
        return 0


# ── Reads ──────────────────────────────────────────────────────────────────────


def _today_et(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


class NewsSentimentTrendService:
    """Reads the log through ``news_sentiment_daily`` with a 5-minute memory tier.

    No Supabase cache tier on purpose: the log IS the durable store and the RPC is one
    indexed GROUP BY over at most a few thousand rows.
    """

    def __init__(self, supabase: Any = None) -> None:
        self._supabase = supabase
        self._cache: Dict[Tuple[str, int, date], Tuple[float, Dict[str, Any]]] = {}
        self._inflight: Dict[Tuple[str, int, date], asyncio.Future] = {}
        #: Keys whose last backfill-status read FAILED: served on the short TTL, so one blip
        #: is not pinned for five minutes as "no backfill" (which also stops the app's re-checks).
        self._status_unknown: set = set()

    @property
    def supabase(self) -> Any:
        if self._supabase is None:
            from app.database import get_supabase

            self._supabase = get_supabase()
        return self._supabase

    def reset(self) -> None:
        self._cache.clear()
        self._inflight.clear()
        self._status_unknown.clear()

    async def get_trend(self, scope: str, days: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """``{scope, days, series, tracking_since, history_status}``.

        ``tracking_since`` is the scope's oldest day still in the log — the start of scoring
        only until the history reaches the retention edge (:func:`at_retention_edge`).

        Raises :class:`SentimentTrendUnavailable`."""
        if days not in TREND_DAYS:
            raise ValueError(f"days must be one of {TREND_DAYS}, got {days!r}")
        today = _today_et(now)
        key = (scope, days, today)

        cached = self._cache.get(key)
        if cached:
            # A scope whose history is still being built changes minute to minute, so the
            # app's re-checks must see new weeks arrive: 30 s instead of 5 min.
            short = cached[1].get("history_status") == HISTORY_BUILDING or key in self._status_unknown
            ttl = _BUILDING_TTL_SECONDS if short else _CACHE_TTL_SECONDS
            if time.monotonic() - cached[0] < ttl:
                return cached[1]

        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await self._fetch(scope, days, today, now)
            if len(self._cache) >= _CACHE_MAX_ENTRIES:
                self._evict(today, keep=key)
            self._cache[key] = (time.monotonic(), result)
            # Guarded: `reset()` (tests, or a future caller) may have resolved or dropped it.
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as e:
            # A joiner must get the TYPED failure the endpoint maps to its 503 — never a bare
            # RuntimeError, which would fall through to a generic 500.
            fail_shared_future(
                fut,
                e if isinstance(e, SentimentTrendUnavailable)
                else SentimentTrendUnavailable(f"shared read failed: {type(e).__name__}: {e}"),
            )
            raise
        finally:
            self._inflight.pop(key, None)

    def _evict(self, today: date, *, keep: Tuple[str, int, date]) -> None:
        """Make room at the cap without dropping the hot keys: first every entry from an
        earlier ET day or past the long TTL, then (only if still full) the oldest quarter.
        Clearing the whole cache made every scope miss at once."""
        cutoff = time.monotonic() - _CACHE_TTL_SECONDS
        for k in [k for k, (ts, _) in self._cache.items() if k[2] != today or ts < cutoff]:
            self._cache.pop(k, None)
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[: max(1, len(oldest) // 4)]:
                self._cache.pop(k, None)
        self._status_unknown &= set(self._cache) | {keep}

    async def _fetch(
        self, scope: str, days: int, today: date, now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        since = today - timedelta(days=days - 1)
        partial_from = min(today, open_since(now or datetime.now(timezone.utc)))
        client = self.supabase

        def _read() -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
            # Status FIRST: a run that completes between the two reads then errs toward
            # "building" (one more 30 s re-check), never toward a "ready" answer — cached for
            # minutes on both sides — whose newest labels are missing from the series.
            try:
                status, status_ok = self._history_status(scope, today), True
            except Exception as e:  # noqa: BLE001 — the status never decides whether the chart loads
                logger.warning("news_sentiment_backfill status read failed for %s (%s: %s)",
                               scope, type(e).__name__, e)
                status, status_ok = None, False
            daily = client.rpc(
                DAILY_RPC, {"p_scope": scope, "p_since": since.isoformat()}
            ).execute()
            first = (
                client.table(TABLE)
                .select("et_day")
                .eq("scope", scope)
                .order("et_day")
                .limit(1)
                .execute()
            )
            first_rows = getattr(first, "data", None) or []
            first_day = first_rows[0].get("et_day") if first_rows and isinstance(first_rows[0], dict) else None
            return (getattr(daily, "data", None) or []), first_day, status, status_ok

        try:
            raw, first_day, history_status, status_ok = await asyncio.to_thread(_read)
        except Exception as e:
            raise SentimentTrendUnavailable(f"{type(e).__name__}: {e}") from e

        key = (scope, days, today)
        if status_ok:
            self._status_unknown.discard(key)
        else:
            self._status_unknown.add(key)
            # Keep the last known "building" so the app's re-checks continue through a blip.
            previous = self._cache.get(key)
            if previous and previous[1].get("history_status") == HISTORY_BUILDING:
                history_status = HISTORY_BUILDING

        tracking_since: Optional[date] = None
        if first_day:
            try:
                tracking_since = date.fromisoformat(str(first_day)[:10])
            except ValueError:
                tracking_since = None
        return {
            "scope": scope,
            "days": days,
            "series": shape_series(raw, today=today, since=since, partial_from=partial_from),
            "tracking_since": tracking_since.isoformat() if tracking_since else None,
            "history_status": history_status,
        }

    def _history_status(self, scope: str, today: date) -> Optional[str]:
        """``building`` / ``ready`` / None for the scope's 90-day backfill.

        Blocking — called inside ``_fetch``'s thread. RAISES on a failed read (migration 181
        not applied, a Supabase blip): ``_fetch`` answers None for it, logs a WARNING, keeps
        a previous "building", and serves that answer on the short TTL. The status only
        decides whether the app shows "Building 90-day history…", never whether the chart
        loads. None while the backfill is switched off, so a leftover queued row cannot
        promise a history that nothing is building.
        """
        return read_history_status(self.supabase, scope, today)

    def sweep_expired(self, today: Optional[date] = None) -> int:
        """Delete labels older than :data:`RETENTION_DAYS` (ET). Best-effort; returns rows."""
        cutoff = (today or _today_et()) - timedelta(days=RETENTION_DAYS)
        try:
            result = self.supabase.table(TABLE).delete().lt("et_day", cutoff.isoformat()).execute()
            count = len(getattr(result, "data", None) or [])
            if count:
                logger.info("news_sentiment_log sweep: deleted %d row(s) before %s", count, cutoff)
            return count
        except Exception as e:  # noqa: BLE001
            logger.warning("news_sentiment_log sweep failed (%s: %s)", type(e).__name__, e)
            return 0


_service: Optional[NewsSentimentTrendService] = None


def get_news_sentiment_trend_service() -> NewsSentimentTrendService:
    global _service
    if _service is None:
        _service = NewsSentimentTrendService()
    return _service


__all__ = [
    "DAILY_RPC",
    "MAX_LABEL_AGE_HOURS",
    "NewsSentimentTrendService",
    "RETENTION_DAYS",
    "SENTIMENTS",
    "SentimentTrendUnavailable",
    "TABLE",
    "TREND_DAYS",
    "article_key",
    "build_log_rows",
    "et_day",
    "is_trend_scope",
    "normalize_relevance",
    "tone_skip_reason",
    "TONE_LABELLER_VERSION",
    "get_news_sentiment_trend_service",
    "history_status_for",
    "read_history_status",
    "upsert_log_rows",
    "SOURCES",
    "TONE_WORD_THRESHOLD",
    "at_retention_edge",
    "coerce_series",
    "net_score",
    "normalize_sentiment",
    "open_since",
    "record_labels",
    "shape_series",
    "since_phrase",
    "summarize_tone",
    "summarize_tone_windows",
    "summarize_trend",
    "tone_word",
    "window_slice",
]
