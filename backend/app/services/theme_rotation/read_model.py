"""What the app shows about the monthly rotation — read side, cached.

Only the latest PUBLISHED LIVE run is ever shown (a dry run or a preview never reaches a
user). Per theme it yields the review date, the change count, the "What changed" list
(added / returned / removed with their fixed-template reasons) and each current stock's
role ("pure_play" / "diversified") and whether it is new this month.

Cache: 10 minutes in process memory. A failed read is cached for 60 seconds only, as
"nothing to show" — never as "no changes" for 10 minutes: a blip must not tell users a
theme was reviewed and nothing changed when we simply could not read it.
Before migration 174 is applied every read fails → the app shows the card as it always did.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from app.utils.inflight import fail_shared_future

logger = logging.getLogger(__name__)

_TTL_SECONDS = 600
_DEGRADED_TTL_SECONDS = 60
PURE_PLAY_EXPOSURE = 0.5
_KNOWN_SOURCES = frozenset({"segments", "industry", "description"})
_SHOWN_ACTIONS = ("added", "returned", "removed")
_PURE_PLAY_BANDS = frozenset({"over_50", "pre_revenue"})


@dataclass(frozen=True)
class ThemeChange:
    ticker: str
    action: str
    reason: str


@dataclass(frozen=True)
class ThemeReview:
    run_month: str
    change_count: int
    changes: List[ThemeChange] = field(default_factory=list)
    roles: Dict[str, str] = field(default_factory=dict)
    new: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class LatestReview:
    run_month: Optional[str]
    themes: Dict[str, ThemeReview] = field(default_factory=dict)
    # True when the read FAILED (not "nothing published yet"): callers must not cache
    # anything built on it for long, or a blip reads as "no changes" for minutes.
    degraded: bool = False


_cache: Tuple[float, float, Optional[LatestReview]] = (0.0, 0.0, None)   # (at, ttl, value)
_inflight: Optional["asyncio.Future[LatestReview]"] = None
# Bumped by invalidate(). A read that started BEFORE a publish and finishes after it would
# otherwise write last month's review back into the cache for 10 minutes — and the
# post-publish rebuild would JOIN that stale read instead of starting a fresh one.
_generation = 0


def invalidate() -> None:
    global _cache, _inflight, _generation
    _generation += 1
    _cache = (0.0, 0.0, None)
    _inflight = None


async def latest_review() -> LatestReview:
    """The latest published live review, or an empty one when none / unreadable."""
    global _cache, _inflight
    at, ttl, value = _cache
    if value is not None and time.monotonic() - at < ttl:
        return value
    if _inflight is not None:
        return await asyncio.shield(_inflight)
    fut: "asyncio.Future[LatestReview]" = asyncio.get_running_loop().create_future()
    _inflight = fut
    generation = _generation
    try:
        try:
            review = await asyncio.to_thread(_read_latest)
            ttl = _TTL_SECONDS
        except Exception as e:
            logger.warning("theme rotation read model: latest review unreadable (%s: %s) — "
                           "cards show no review info for %ds", type(e).__name__, e,
                           _DEGRADED_TTL_SECONDS)
            review = LatestReview(run_month=None, degraded=True)
            ttl = _DEGRADED_TTL_SECONDS
        if generation == _generation:
            _cache = (time.monotonic(), ttl, review)
        if not fut.done():
            fut.set_result(review)
        return review
    except BaseException as exc:
        fail_shared_future(fut, exc)
        raise
    finally:
        # Only clear OUR future: after invalidate() a newer read may own the slot.
        if _inflight is fut:
            _inflight = None


def _read_latest() -> LatestReview:
    from app.database import get_supabase

    db = get_supabase()
    runs = (db.table("theme_rotation_runs").select("id, run_month")
            .eq("mode", "live").eq("status", "published")
            .order("run_month", desc=True).limit(1).execute())
    rows = getattr(runs, "data", None) or []
    if not rows:
        return LatestReview(run_month=None)
    run_id, run_month = rows[0]["id"], str(rows[0].get("run_month") or "")[:10]
    decisions = (db.table("theme_rotation_decisions")
                 .select("slug, ticker, action, reason_text, score_parts, was_member")
                 .eq("run_id", run_id)
                 .in_("action", ["kept", "added", "returned", "removed", "deferred"])
                 .execute())
    return build_review(run_month, getattr(decisions, "data", None) or [])


def build_review(run_month: str, rows: List[dict]) -> LatestReview:
    """Pure: decision rows of ONE published run → per-theme review. Tested directly."""
    per: Dict[str, Dict[str, object]] = {}
    for r in rows:
        slug, ticker, action = r.get("slug"), r.get("ticker"), r.get("action")
        if not isinstance(slug, str) or not isinstance(ticker, str) or not isinstance(action, str):
            continue
        entry = per.setdefault(slug, {"changes": [], "roles": {}, "new": set(),
                                      "added": 0, "removed": 0})
        if action in _SHOWN_ACTIONS:
            entry["changes"].append(ThemeChange(ticker=ticker, action=action,  # type: ignore[union-attr]
                                                reason=str(r.get("reason_text") or "")))
            if action == "removed":
                entry["removed"] += 1  # type: ignore[operator]
            else:
                entry["added"] += 1  # type: ignore[operator]
                entry["new"].add(ticker)  # type: ignore[union-attr]
        # A member whose removal the change cap DEFERRED is still in the published list,
        # so it gets its tag like any kept member; a deferred OUTSIDER never joined.
        on_list = (action in ("kept", "added", "returned")
                   or (action == "deferred" and r.get("was_member") is True))
        if on_list:
            role = role_of(r.get("score_parts"))
            if role:
                entry["roles"][ticker] = role  # type: ignore[index]
    themes = {}
    for slug, e in per.items():
        order = {"added": 0, "returned": 1, "removed": 2}
        changes = sorted(e["changes"], key=lambda c: (order.get(c.action, 9), c.ticker))  # type: ignore[arg-type]
        themes[slug] = ThemeReview(
            run_month=run_month,
            change_count=max(int(e["added"]), int(e["removed"])),  # type: ignore[arg-type]
            changes=changes, roles=dict(e["roles"]), new=set(e["new"]),  # type: ignore[arg-type]
        )
    return LatestReview(run_month=run_month, themes=themes)


def role_of(score_parts: object) -> Optional[str]:
    """"pure_play" / "diversified" / None — shown as a tag on each company in the app.

    "Pure play" needs REAL evidence that the theme is most of the business: a majority of
    revenue in theme segments, or the relevance check rating the company "core" with a
    majority-revenue band. Merely sitting in the theme's industry does NOT qualify — the
    first version equated industry credit (0.5) with a pure play and tagged Rio Tinto (iron
    ore) one. Weaker known evidence is "diversified"; no evidence at all is no tag.
    """
    if not isinstance(score_parts, dict):
        return None
    source = score_parts.get("exposure_source")
    exposure = score_parts.get("exposure")
    fit, band = score_parts.get("fit"), score_parts.get("fit_band")
    if not isinstance(exposure, (int, float)) or isinstance(exposure, bool):
        exposure = None
    if source == "segments" and exposure is not None and exposure >= PURE_PLAY_EXPOSURE:
        return "pure_play"
    if fit == "core" and band in _PURE_PLAY_BANDS:
        return "pure_play"
    if fit in ("core", "adjacent") or (source in _KNOWN_SOURCES and exposure is not None):
        return "diversified"
    return None
