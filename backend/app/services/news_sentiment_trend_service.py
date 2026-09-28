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
    published. The app says "Headlines Cay AI scored", never "all news".
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
_SENTIMENT_SET = frozenset(SENTIMENTS)

_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 2048


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
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


def build_log_rows(scope: str, rows: Iterable[Dict[str, Any]], *, now: datetime) -> List[Dict[str, Any]]:
    """Log rows for the labelled cache rows of ONE scope. Pure.

    Skips a row with no identity, no valid label, or a label made more than
    :data:`MAX_LABEL_AGE_HOURS` after publication. A row without a usable ``published_at``
    (or one dated in the future) is charted on the labelling day.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    scope = (scope or "").strip()
    if not scope or len(scope) > 32:
        return []
    oldest = now - timedelta(hours=MAX_LABEL_AGE_HOURS)
    labelled_day = now.astimezone(ET).date()
    out: List[Dict[str, Any]] = []
    seen: set = set()
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
        if instant is not None and instant < oldest:
            continue
        if instant is None or instant > now + _FUTURE_SKEW:
            day = labelled_day
        else:
            day = instant.astimezone(ET).date()
        seen.add(key)
        out.append({
            "scope": scope,
            "article_key": key,
            "et_day": day.isoformat(),
            "sentiment": sentiment,
            "confidence": _clamp_confidence(row.get("sentiment_confidence")),
            "source": "live",
            "labelled_at": now.isoformat(),
        })
    return out


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
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
        day_value = row.get("day")
        try:
            day = day_value if isinstance(day_value, date) else date.fromisoformat(str(day_value)[:10])
        except (TypeError, ValueError):
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


def summarize_trend(
    series: List[Dict[str, Any]], *, days: int, today: date, tracking_since: Optional[date] = None,
) -> Optional[str]:
    """A compact, figure-exact text of the series for chat grounding, or None when empty.

    Pure and deterministic, so the chat sees exactly the numbers the chart draws.
    """
    if not series:
        return None
    bull = sum(d["bullish"] for d in series)
    bear = sum(d["bearish"] for d in series)
    neut = sum(d["neutral"] for d in series)
    total = bull + bear + neut
    parts = [
        f"News tone over the last {days} days (headlines Cay AI scored, by ET day"
        + (f"; tracked since {_fmt_day(tracking_since)}" if tracking_since else "")
        + f"): {total} scored — {bull} bullish, {bear} bearish, {neut} neutral; "
        f"net tone {net_score(bull, bear, total):+d} on a -100..+100 scale."
    ]
    recent = _window_net(series, today - timedelta(days=6), today)
    prior = _window_net(series, today - timedelta(days=13), today - timedelta(days=7))
    if days > 7 and recent is not None and prior is not None:
        parts.append(f"Last 7 days net {recent:+d} vs the 7 days before {prior:+d}.")
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
    partial = [d for d in series if d["is_partial"]]
    if partial:
        first = date.fromisoformat(partial[0]["date"])
        parts.append(
            f"Counts from {_fmt_day(first)} on are still filling in (late articles are scored "
            "overnight and after weekends); earlier days are final."
        )
    return " ".join(parts)


# ── Writes ─────────────────────────────────────────────────────────────────────


async def record_labels(
    supabase: Any, scope: str, rows: Iterable[Dict[str, Any]], *, now: Optional[datetime] = None,
) -> int:
    """Log the labels of freshly enriched cache rows. Returns rows NEWLY logged. Never raises.

    Best-effort by design: enrichment already succeeded and the user already has their
    bullets; a lost log write costs one article on the chart, never the feed. It is logged
    at WARNING with the scope and batch size so a missing migration is visible, not silent.
    """
    if supabase is None:
        return 0
    try:
        payload = build_log_rows(scope, rows, now=now or datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001 — a malformed row must not break enrichment
        logger.warning("news_sentiment_log: could not build rows for %s (%s: %s)",
                       scope, type(e).__name__, e)
        return 0
    if not payload:
        return 0

    def _do() -> int:
        result = (
            supabase.table(TABLE)
            .upsert(payload, on_conflict="scope,article_key", ignore_duplicates=True)
            .execute()
        )
        return len(getattr(result, "data", None) or [])

    try:
        return await asyncio.to_thread(_do)
    except Exception as e:  # noqa: BLE001
        logger.warning(
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

    @property
    def supabase(self) -> Any:
        if self._supabase is None:
            from app.database import get_supabase

            self._supabase = get_supabase()
        return self._supabase

    def reset(self) -> None:
        self._cache.clear()
        self._inflight.clear()

    async def get_trend(self, scope: str, days: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """``{scope, days, series, tracking_since}``. Raises :class:`SentimentTrendUnavailable`."""
        if days not in TREND_DAYS:
            raise ValueError(f"days must be one of {TREND_DAYS}, got {days!r}")
        today = _today_et(now)
        key = (scope, days, today)

        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await self._fetch(scope, days, today, now)
            if len(self._cache) >= _CACHE_MAX_ENTRIES:
                self._cache.clear()
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

    async def _fetch(
        self, scope: str, days: int, today: date, now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        since = today - timedelta(days=days - 1)
        partial_from = min(today, open_since(now or datetime.now(timezone.utc)))
        client = self.supabase

        def _read() -> Tuple[List[Dict[str, Any]], Optional[str]]:
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
            return (getattr(daily, "data", None) or []), first_day

        try:
            raw, first_day = await asyncio.to_thread(_read)
        except Exception as e:
            raise SentimentTrendUnavailable(f"{type(e).__name__}: {e}") from e

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
        }

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
    "get_news_sentiment_trend_service",
    "net_score",
    "normalize_sentiment",
    "open_since",
    "record_labels",
    "shape_series",
    "summarize_trend",
]
