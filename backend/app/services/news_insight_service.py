"""
News Insight Service — N articles → one AI "Insights" card.

Powers the card at the top of the iOS Updates screen, for both the general
market scope (``__MARKET__``) and each watchlist ticker.

ARCHITECTURE
------------
Read path (``get_cards``) is a pure cache read — **there is no code path from an
HTTP handler to Gemini**. Cards are produced only by the background sweeper
(``updates_insight_sweeper.py``), which calls :meth:`generate_and_store` after
the materiality gate in ``updates_materiality.py`` trips. That is what keeps the
Updates tab at sub-100 ms regardless of LLM latency.

Cache is the canonical two-tier shape (CLAUDE.md invariant #4):
  Tier 1 — in-memory dict, 300 s.
  Tier 2 — ``ai_insight_cache`` (migration 088), soft/hard expiry.
  ``_inflight`` dedup so N concurrent readers cause one Supabase round-trip.

NEVER WRITE A DEGRADED CARD
---------------------------
Every failure path returns without writing. This repo has a documented incident
(see ``news_cache_service._batch_enrich_articles``) where a "neutral + empty
bullets" fallback was persisted with ``ai_processed=True``, poisoning a shared
6-hour cache for every user with no retry path. Here the rule is enforced twice:
in Python (validate-then-write) and in Postgres (CHECK constraints on
``bullets`` length, ``sentiment`` domain and ``headline`` length), so a degraded
card cannot be stored even by a future refactor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import settings
from app.services.agents.persona_config import neutral_system_instruction
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client, is_transient_gemini_error
from app.services.market_news_quality import is_material_headline
from app.services.ticker_report_cache import current_close_cycle_start
from app.services.updates_materiality import PROMPT_VERSION, finite
from app.utils.market_hours import is_market_active, last_completed_close

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────

# Flash-Lite, not Flash: this is extractive compression of prose we already have
# into a fixed JSON schema — not a reasoning task. Flash costs ~3.6x more for
# quality that is not visible in a 4-bullet card. Overridable via settings so a
# model deprecation is a config change, not a deploy.
INSIGHT_MODEL: str = getattr(
    settings, "INSIGHT_AI_MODEL", None
) or "gemini-2.5-flash-lite"

# How many articles feed one roll-up. Beyond ~25 the marginal article adds
# tokens without adding signal, and the older items dilute "what happened today".
MAX_CORPUS_ARTICLES = 25

# The corpus window is DYNAMIC: prefer the last PRIMARY_WINDOW_HOURS (24h) and
# fall back to CORPUS_WINDOW_HOURS (48h) only when the scope has no news in the
# last 24h. The chosen window drives the iOS badge ("24h"/"48h"), so a scope with
# fresh news is honestly labelled "24h" rather than over-claiming a 48h lookback.
# The sweeper bounds each scope's corpus to the SAME window before BOTH the
# materiality fingerprint and generation (so the badge is literally true), and the
# Updates endpoint uses it to decide whether to surface a card at all (no news in
# 48h ⇒ no card). ``select_recent_corpus`` is the single source of that decision —
# change these constants there, not by hand.
PRIMARY_WINDOW_HOURS = 24
CORPUS_WINDOW_HOURS = 48
# Third tier, used ONLY when 24h and 48h are both empty AND the market was shut
# for long enough to explain it (see ``_closed_market_window_hours``). Without it
# a quiet ticker whose last story was Friday has an empty 48h window every Monday
# morning, and the endpoint's `if feed_recent:` gate renders NO card at all --
# even though a perfectly good one is sitting unexpired in the cache, because the
# 96h hard TTL below was raised for exactly this reason and the gate overrides it.
# 96h is the same number and the same rationale as _HARD_TTL_*: it spans a
# Thursday-close-to-Monday-open holiday weekend.
MAX_WINDOW_HOURS = 96
# Small tolerance for clock skew / same-minute stamping so a legitimately
# just-published article isn't dropped, while genuinely future-dated rows are.
_FUTURE_SKEW_HOURS = 2
# Per-article text budget, characters. Headlines carry most of the signal.
MAX_ARTICLE_TEXT_CHARS = 400

MIN_BULLETS = 2
MAX_BULLETS = 5
MAX_HEADLINE_CHARS = 160

_MEM_TTL_SECONDS = 300               # Tier-1
_SOFT_TTL_ACTIVE_SECONDS = 15 * 60   # flagged is_stale after this
_SOFT_TTL_CLOSED_SECONDS = 4 * 3600
# Hard expiry must span the longest gap between two sweeps, and the sweeper only
# runs while `is_market_active()`. The longest real gap is a long weekend:
# Friday 20:00 ET → Tuesday 04:00 ET ≈ 80 hours. A 12h hard TTL meant the card
# written on Friday evening expired Saturday morning and EVERY scope — including
# the default Market tab — served the non-AI fallback for the rest of the
# weekend. 96h covers a Thursday-close-to-Monday-open holiday weekend.
_HARD_TTL_ACTIVE_SECONDS = 96 * 3600
_HARD_TTL_CLOSED_SECONDS = 96 * 3600

_SENTIMENTS = ("Bullish", "Bearish", "Neutral")

_TABLE = "ai_insight_cache"


# ── Sentiment normalization ───────────────────────────────────────────

def normalize_card_sentiment(raw: Any) -> Optional[str]:
    """Map any sentiment spelling to the card domain, or ``None`` to abstain.

    Two incompatible conventions already exist in this database:
    ``ticker_news_cache.sentiment`` stores lowercase ``bullish|bearish|neutral``
    (plus legacy ``'Positive'|'Negative'`` admitted by its CHECK), while the iOS
    card decodes ``Bullish|Bearish|Neutral``. Returning ``None`` for an unknown
    or missing value matters: a NULL row is an *abstention*, and silently
    counting it as Neutral would let a handful of unenriched articles outvote
    the real signal.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("bullish", "positive"):
        return "Bullish"
    if s in ("bearish", "negative"):
        return "Bearish"
    if s == "neutral":
        return "Neutral"
    return None


# ── Gemini structured-output schema ───────────────────────────────────

_INSIGHT_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "bullets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "sentiment": {
            "type": "STRING",
            "enum": ["bullish", "bearish", "neutral"],
        },
    },
    "required": ["headline", "bullets", "sentiment"],
}

# Wrapped in IDENTITY_RULE + ADVICE_BOUNDARY: this brief is shown as Cay AI output on the
# Updates tab, so it needs the same guards the report and chat surfaces get.
_SYSTEM_INSTRUCTION = neutral_system_instruction(
    "You are an expert financial translator. You read a batch of financial news "
    "and distill it into ONE short brief for everyday investors. Keep the tone "
    "friendly, accessible and reliable. Use concrete numbers from the articles "
    "when they are present. Never invent facts, numbers, tickers or events that "
    "are not in the supplied articles. Do not use introductory phrases. "
    "For sentiment you MUST return exactly one of: bullish, bearish, neutral."
)


class NewsInsightService:
    """Builds, caches and serves the Updates-screen AI Insights card."""

    def __init__(self) -> None:
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        # Tier 1: scope -> (monotonic_ts, card dict)
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._inflight: Dict[str, asyncio.Future] = {}

    # ── Public: read path (never touches Gemini) ──────────────────────

    async def get_cards(self, scopes: Sequence[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Return ``{scope: card|None}`` for the requested scopes.

        Pure cache read: Tier 1, then one batched Supabase select for the
        misses. A scope with no stored card (or an expired one) yields ``None``
        and the caller decides whether to render a deterministic fallback.
        """
        wanted = [s for s in dict.fromkeys(scopes) if s]
        if not wanted:
            return {}

        out: Dict[str, Optional[Dict[str, Any]]] = {}
        missing: List[str] = []
        mono = time.monotonic()
        for scope in wanted:
            hit = self._cache.get(scope)
            if hit and (mono - hit[0]) < _MEM_TTL_SECONDS:
                out[scope] = hit[1]
            else:
                missing.append(scope)

        if not missing:
            return out

        key = "|".join(sorted(missing))
        inflight = self._inflight.get(key)
        if inflight is not None:
            try:
                fetched = await asyncio.shield(inflight)
            except Exception:
                fetched = {}
        else:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            try:
                fetched = await asyncio.to_thread(self._select_cards, missing)
                if not fut.done():
                    fut.set_result(fetched)
            except Exception as e:
                logger.warning(
                    "Insight cache read failed for %s: %s: %s",
                    missing, type(e).__name__, e,
                )
                fetched = {}
                if not fut.done():
                    fut.set_result(fetched)
            finally:
                # SETTLE ON CANCELLATION. CancelledError is a BaseException, so neither branch
                # above runs when this coroutine is cancelled, and joiners parked on
                # `await inflight` (line ~193) hang forever — on the Updates tab hot path.
                #
                # `{}` rather than an exception: it is already this method's documented
                # degraded value (the `except` branch above sets exactly that), so joiners fall
                # through to `build_fallback_card` instead of failing.
                if not fut.done():
                    fut.set_result({})
                self._inflight.pop(key, None)

        now_mono = time.monotonic()
        for scope in missing:
            card = fetched.get(scope)
            out[scope] = card
            if card is not None:
                self._cache[scope] = (now_mono, card)
        return out

    def _select_cards(self, scopes: List[str]) -> Dict[str, Dict[str, Any]]:
        """Blocking Supabase read — always called via ``asyncio.to_thread``.

        The Supabase Python SDK is synchronous; calling it directly from an
        ``async def`` blocks the event loop for the whole round-trip.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .select("*")
            .in_("scope", scopes)
            .gt("hard_expires_at", now_iso)
            .execute()
        )
        # Read the session state ONCE per query rather than per row: every row
        # in a batch must agree on it, and a sweep can span a session boundary.
        market_active = is_market_active()
        cards: Dict[str, Dict[str, Any]] = {}
        for row in (result.data or []):
            card = self._row_to_card(row, market_active=market_active)
            if card is not None:
                cards[card["scope"]] = card
        return cards

    def _row_to_card(
        self, row: Dict[str, Any], market_active: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """Map a DB row to the API card shape, dropping anything malformed.

        A row that fails validation is treated as a cache MISS rather than
        surfaced — a half-written card in a finance app is worse than no card.

        ``market_active`` is injectable so the staleness branch is testable
        without depending on the wall clock of whoever runs the suite.
        """
        try:
            bullets = row.get("bullets")
            if isinstance(bullets, str):
                bullets = json.loads(bullets)
            if not isinstance(bullets, list):
                raise ValueError(f"bullets is {type(bullets).__name__}, not list")
            bullets = [str(b) for b in bullets if isinstance(b, str) and b.strip()]
            if not (MIN_BULLETS <= len(bullets) <= MAX_BULLETS):
                raise ValueError(f"bullets length {len(bullets)} out of range")

            sentiment = normalize_card_sentiment(row.get("sentiment")) or "Neutral"
            headline = (row.get("headline") or "").strip()
            if not headline:
                raise ValueError("empty headline")

            # `is_stale` means "the inputs may have moved on and the sweeper
            # has not caught up yet" — it is a statement about the SWEEPER,
            # which only runs while `is_market_active()` (04:00–20:00 ET, see
            # updates_insight_sweeper.run_insight_sweeper_loop).
            #
            # Outside that window nothing is sweeping and nothing will until the
            # next session opens, so a soft-expired card is not behind anything:
            # it IS the latest view of the world. Reporting stale there is what
            # made every scope render "Catching up…" — replacing the card's
            # timestamp with a claim that a refresh was pending — for the whole
            # 8h overnight window and every weekend. The last active sweep
            # stamps a 15-minute soft expiry and then the loop goes to sleep, so
            # the flag tripped ~15 min after the 20:00 ET close, every night.
            if market_active is None:
                market_active = is_market_active()
            soft = _parse_ts(row.get("soft_expires_at"))
            now = datetime.now(timezone.utc)
            return {
                "scope": row.get("scope"),
                "headline": headline,
                "bullets": bullets,
                "sentiment": sentiment,
                "article_count": int(row.get("article_count") or 0),
                "generated_at": _iso(row.get("generated_at")),
                "is_stale": bool(
                    soft is not None and soft <= now and market_active
                ),
                "price_move": _sanitize_price_move(row.get("price_move")),
                "sources": _sanitize_sources(row.get("sources")),
                "refreshing": False,
                "ai_generated": True,
                "trigger_reason": row.get("trigger_reason"),
            }
        except Exception as e:
            logger.warning(
                "Discarding malformed ai_insight_cache row for scope=%s: %s: %s",
                row.get("scope"), type(e).__name__, e,
            )
            return None

    # ── Public: deterministic (non-LLM) fallback ──────────────────────

    def build_fallback_card(
        self,
        scope: str,
        corpus: Sequence[Dict[str, Any]],
        market_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """A truthful, LLM-free card for a scope that has never been generated.

        Bullets are the actual top headlines — no synthesis, no interpretation,
        nothing invented. Sentiment is a majority vote over the *enriched*
        articles only; NULL sentiments abstain rather than counting as Neutral,
        and the whole vote abstains to Neutral if nothing has an opinion.

        Returns ``None`` for an empty corpus: an honest absent card beats a
        fabricated one.
        """
        usable = [r for r in corpus if isinstance(r, dict) and (r.get("headline") or "").strip()]
        if not usable:
            return None

        # De-dup BEFORE the pad, not after. Corpus dedup keys on `url or title`
        # (url first), so three syndications of one wire story survive as three
        # rows with identical headlines — which collapsed to a SINGLE bullet
        # after the pad had already decided no padding was needed, yielding a
        # card below MIN_BULLETS with nothing to raise on it.
        bullets = list(dict.fromkeys(
            _clip((r.get("headline") or "").strip(), 180)
            for r in usable[:6]
        ))[:3]
        # The card contract requires >= 2 bullets. With a single article, add an
        # honest provenance line rather than padding with invented commentary.
        if len(bullets) < MIN_BULLETS:
            bullets.append(
                f"Showing the latest {len(usable)} "
                f"{'story' if len(usable) == 1 else 'stories'}; "
                "the AI summary is still being prepared."
            )

        votes = [
            s for s in (normalize_card_sentiment(r.get("sentiment")) for r in usable)
            if s is not None
        ]
        bull = votes.count("Bullish")
        bear = votes.count("Bearish")
        sentiment = "Bullish" if bull > bear else "Bearish" if bear > bull else "Neutral"

        label = "Market" if scope.startswith("__") else scope
        return {
            "scope": scope,
            "headline": f"Latest {label} headlines",
            "bullets": bullets[:MAX_BULLETS],
            "sentiment": sentiment,
            # NOT the AI-card badge (the plain window label, e.g. "48h"). Letting
            # the Pydantic default fill this in put an AI-styled label on text no
            # model wrote — the exact fabrication this screen was rebuilt to remove.
            "badge": "Latest headlines",
            "article_count": len(usable),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_stale": False,
            # Tells iOS to poll shortly, because the sweeper will replace this
            # with a real AI card within one cycle — which is only TRUE while
            # the sweeper is running. It is gated on `is_market_active()`
            # (updates_insight_sweeper.run_insight_sweeper_loop), so overnight
            # and at weekends this promise cannot be kept: no cycle is coming
            # until the next session opens. Asserting it anyway made iOS render
            # a bare "Catching up…" for up to ~60 hours and fire two futile
            # re-polls on every feed load. Same reasoning as `is_stale` above:
            # both flags are statements about the SWEEPER, not about the card.
            "refreshing": (
                is_market_active() if market_active is None else bool(market_active)
            ),
            "ai_generated": False,
            "trigger_reason": None,
            # The stories these headlines come from — so the sources screen works
            # on the deterministic fallback card too (its bullets ARE these).
            "sources": _corpus_sources(usable),
        }

    # ── Public: generation (sweeper only) ─────────────────────────────

    async def generate_and_store(
        self,
        scope: str,
        corpus: Sequence[Dict[str, Any]],
        inputset_id: str,
        price_band: Optional[str],
        trigger_reason: str,
        quote: Optional[Dict[str, Any]] = None,
        market_active: bool = True,
        price_move: Optional[Dict[str, Any]] = None,
        preserve_price_move: bool = False,
        catalyst_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate a card with Gemini and persist it. Returns ``None`` on any
        failure, **without writing anything**.

        ``price_move`` (optional) is the grounded "why did it move" block for a
        big move — a SEPARATE, cited field from the news bullets. It is persisted
        with the card but is purely additive: a None/malformed value never blocks
        or fails the news card.

        ``catalyst_sources`` (optional) are the raw grounding sources
        (``[{title, uri, publisher}]``) the "why it moved" web search consulted.
        They are MERGED into the card's ``sources`` list alongside the FMP-news
        corpus sources (see ``_merge_sources``), so a reader can open the outside
        stories behind the price-move explanation. None/malformed → corpus-only.

        ``preserve_price_move`` — when True and ``price_move`` is None, the stored
        ``price_move`` column is left untouched instead of being overwritten to
        NULL, so a still-valid "why it moved" block is not wiped by a regen where
        the catalyst was merely unavailable this cycle (see the sweeper).

        The corpus passed here MUST be the same corpus the materiality gate
        evaluated — otherwise we can regenerate because of a story the summary
        never sees, which is worse than not regenerating at all.
        """
        articles = [
            r for r in corpus
            if isinstance(r, dict) and (r.get("headline") or "").strip()
        ][:MAX_CORPUS_ARTICLES]
        if not articles:
            logger.warning("Insight generation skipped for %s: empty corpus", scope)
            return None

        prompt = self._build_prompt(
            scope, articles, inputset_id, price_band, quote, price_move
        )

        started = time.monotonic()
        try:
            response = await self.gemini.generate_json(
                prompt=prompt,
                system_instruction=_SYSTEM_INSTRUCTION,
                model_name=INSIGHT_MODEL,
                response_schema=_INSIGHT_SCHEMA,
            )
            parsed = json.loads(response.get("text", ""))
        except json.JSONDecodeError as e:
            # Expected degradation, not a code bug: the model returned truncated
            # or non-JSON output. WARNING keeps it out of Sentry; the next sweep
            # retries because nothing was written.
            logger.warning(
                "Insight generation returned malformed JSON for %s: %s", scope, e
            )
            return None
        except Exception as e:
            if is_transient_gemini_error(e):
                # A known transient Gemini capacity condition (quota OR server
                # overload / "high demand") — already retried + circuit-governed,
                # and the card just isn't regenerated this cycle. Not an incident.
                logger.warning("Insight generation degraded (transient) for %s: %s", scope, e)
            else:
                logger.error(
                    "Insight generation failed for %s: %s: %s",
                    scope, type(e).__name__, e, exc_info=True,
                )
            return None

        card = self._validate(scope, parsed)
        if card is None:
            return None

        gen_seconds = round(time.monotonic() - started, 2)
        # The source stories this summary was built from — the LITERAL corpus
        # (title + url), captured at generation so a possibly-older card keeps its
        # own point-in-time sources rather than the current window. The "why it
        # moved" catalyst's web sources (if any) are merged in, reserved slots so
        # they always surface next to the FMP headlines.
        sources = _merge_sources(
            _corpus_sources(articles),
            _catalyst_web_sources(catalyst_sources),
        )
        stored = await asyncio.to_thread(
            self._store,
            scope, card, inputset_id, trigger_reason, len(articles), market_active,
            price_move, preserve_price_move, sources,
        )
        if not stored:
            return None

        logger.info(
            "Insight generated for scope=%s reason=%r articles=%d sentiment=%s in %.2fs",
            scope, trigger_reason, len(articles), card["sentiment"], gen_seconds,
        )
        # Invalidate Tier 1 so the next read picks up the new row.
        self._cache.pop(scope, None)
        return card

    def _validate(self, scope: str, parsed: Any) -> Optional[Dict[str, Any]]:
        """Validate the model output. Returns ``None`` (⇒ no write) if degraded."""
        if not isinstance(parsed, dict):
            logger.warning(
                "Insight output for %s was %s, expected object",
                scope, type(parsed).__name__,
            )
            return None

        headline = str(parsed.get("headline") or "").strip()
        headline = re.sub(r"\s+", " ", headline)
        if not headline:
            logger.warning("Insight output for %s had an empty headline", scope)
            return None
        headline = _clip(headline, MAX_HEADLINE_CHARS)

        raw_bullets = parsed.get("bullets")
        if not isinstance(raw_bullets, list):
            logger.warning(
                "Insight output for %s had bullets=%s, expected array",
                scope, type(raw_bullets).__name__,
            )
            return None
        bullets = []
        for b in raw_bullets:
            if not isinstance(b, str):
                continue
            t = re.sub(r"\s+", " ", b).strip()
            if t:
                bullets.append(_clip(t, 400))
        # De-dup: a repeated bullet renders twice under SwiftUI's ForEach(id:\.self)
        # and reads as a rendering bug.
        bullets = list(dict.fromkeys(bullets))[:MAX_BULLETS]
        if len(bullets) < MIN_BULLETS:
            logger.warning(
                "Insight output for %s had only %d usable bullets (need >= %d) "
                "— discarding, will retry next sweep",
                scope, len(bullets), MIN_BULLETS,
            )
            return None

        sentiment = normalize_card_sentiment(parsed.get("sentiment"))
        if sentiment is None:
            logger.warning(
                "Insight output for %s had unrecognised sentiment %r — discarding",
                scope, parsed.get("sentiment"),
            )
            return None

        return {"headline": headline, "bullets": bullets, "sentiment": sentiment}

    def _store(
        self,
        scope: str,
        card: Dict[str, Any],
        inputset_id: str,
        trigger_reason: str,
        article_count: int,
        market_active: bool,
        price_move: Optional[Dict[str, Any]] = None,
        preserve_price_move: bool = False,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Blocking upsert — always called via ``asyncio.to_thread``."""
        now = datetime.now(timezone.utc)
        soft = _SOFT_TTL_ACTIVE_SECONDS if market_active else _SOFT_TTL_CLOSED_SECONDS
        hard = _HARD_TTL_ACTIVE_SECONDS if market_active else _HARD_TTL_CLOSED_SECONDS
        row = {
            "scope": scope,
            "headline": card["headline"],
            "bullets": card["bullets"],
            "sentiment": card["sentiment"],
            "article_count": article_count,
            "inputset_id": inputset_id,
            "prompt_version": PROMPT_VERSION,
            "ai_model": INSIGHT_MODEL,
            "trigger_reason": _clip(trigger_reason, 200),
            "generated_at": now.isoformat(),
            "close_cycle": current_close_cycle_start(now).isoformat(),
            "soft_expires_at": (now + timedelta(seconds=soft)).isoformat(),
            "hard_expires_at": (now + timedelta(seconds=hard)).isoformat(),
            # Additive JSONB (migration 092). The corpus stories this card was built
            # from — [{title, url}]; NULL when unknown. Never blocks the news card.
            "sources": _sanitize_sources(sources),
        }
        sanitized_move = _sanitize_price_move(price_move)
        # Additive JSONB (migration 091). Only a well-shaped block is written; a
        # card without a big move stores NULL. When `preserve_price_move` is set
        # and there is no new block, OMIT the column from the upsert so an
        # existing, still-valid block is kept on conflict instead of wiped to NULL
        # (a PostgREST upsert only SETs the columns present in the payload).
        if not (preserve_price_move and sanitized_move is None):
            row["price_move"] = sanitized_move
        try:
            self.supabase.table(_TABLE).upsert(row, on_conflict="scope").execute()
            return True
        except Exception as e:
            logger.error(
                "Insight cache write failed for %s: %s: %s",
                scope, type(e).__name__, e, exc_info=True,
            )
            return False

    async def touch(self, scope: str, market_active: bool) -> None:
        """Re-stamp an existing card's freshness without calling Gemini.

        Used when the close-cycle ceiling fires but the input set is unchanged —
        the card is provably still correct, it just should not be labelled stale.
        """
        now = datetime.now(timezone.utc)
        soft = _SOFT_TTL_ACTIVE_SECONDS if market_active else _SOFT_TTL_CLOSED_SECONDS
        hard = _HARD_TTL_ACTIVE_SECONDS if market_active else _HARD_TTL_CLOSED_SECONDS

        def _do() -> None:
            self.supabase.table(_TABLE).update({
                "close_cycle": current_close_cycle_start(now).isoformat(),
                "soft_expires_at": (now + timedelta(seconds=soft)).isoformat(),
                "hard_expires_at": (now + timedelta(seconds=hard)).isoformat(),
            }).eq("scope", scope).execute()

        try:
            await asyncio.to_thread(_do)
            self._cache.pop(scope, None)
        except Exception as e:
            logger.warning(
                "Insight touch failed for %s: %s: %s", scope, type(e).__name__, e
            )

    async def mark_verified_current(
        self, scopes: List[str], market_active: bool
    ) -> None:
        """Extend soft expiry for cards the sweeper just re-verified as unchanged.

        ``is_stale`` means "the sweeper hasn't checked this recently", NOT "the
        text is old". A card whose input fingerprint is unchanged is provably
        still correct — that is the entire premise of the fingerprint. Without
        this, every quiet scope would flip to "Catching up…" 15 minutes after
        generation and stay there indefinitely, because the fingerprint skip
        path never re-stamped anything.

        One batched update, not one per scope.
        """
        if not scopes:
            return
        now = datetime.now(timezone.utc)
        soft = _SOFT_TTL_ACTIVE_SECONDS if market_active else _SOFT_TTL_CLOSED_SECONDS
        hard = _HARD_TTL_ACTIVE_SECONDS if market_active else _HARD_TTL_CLOSED_SECONDS

        def _do() -> None:
            self.supabase.table(_TABLE).update({
                "soft_expires_at": (now + timedelta(seconds=soft)).isoformat(),
                "hard_expires_at": (now + timedelta(seconds=hard)).isoformat(),
            }).in_("scope", scopes).execute()

        try:
            await asyncio.to_thread(_do)
            for s in scopes:
                self._cache.pop(s, None)
        except Exception as e:
            logger.warning(
                "Could not re-stamp %d verified-current insight cards: %s: %s",
                len(scopes), type(e).__name__, e,
            )

    # ── Prompt ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        scope: str,
        articles: Sequence[Dict[str, Any]],
        inputset_id: str,
        price_band: Optional[str],
        quote: Optional[Dict[str, Any]],
        price_move: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build the roll-up prompt.

        ``price_move`` is the "why it moved" catalyst, when one was produced for
        this scope THIS cycle. It exists here for one reason: the catalyst and
        these bullets used to be two independent model calls over the same day's
        evidence, with no shared context and no cross-de-dup, so on any earnings
        day both independently wrote the same story and the reader saw it twice.
        Passing it in is what makes the bullets additive instead of a second
        telling. See ``_catalyst_block``.
        """
        is_market = scope.startswith("__")
        subject = "the overall US stock market" if is_market else scope

        lines = []
        for i, a in enumerate(articles):
            title = re.sub(r"\s+", " ", str(a.get("headline") or "")).strip()
            text = re.sub(r"\s+", " ", str(a.get("summary") or "")).strip()
            text = _clip(text, MAX_ARTICLE_TEXT_CHARS)
            when = str(a.get("published_at") or "")[:16]
            lines.append(f"[{i}] ({when}) {title}" + (f"\n     {text}" if text else ""))

        # A catalyst SUPERSEDES the generic price line -- never both.
        #
        # `price_line` states the exact session move and then says "mention this
        # ONLY if the articles explain it". On precisely the days a catalyst
        # exists, the articles DO explain it, so that sentence invites the model
        # to write the very explanation the catalyst already carries. Emitting
        # both re-creates the duplication this block exists to remove.
        price_line = _catalyst_block(price_move)
        if not price_line and quote:
            pct = finite(quote.get("changePercentage"))
            if pct is not None:
                price_line = (
                    f"\nPrice context: {subject} is {'up' if pct >= 0 else 'down'} "
                    f"{abs(pct):.2f}% in the current session"
                    + (f" ({price_band} move)." if price_band else ".")
                    + " Mention this ONLY if the articles explain it; never invent a cause."
                )

        return f"""Write ONE short brief summarising what these {len(articles)} news articles mean for {subject} right now.

Rules:
- "headline": one sentence, under 90 characters, stating the single most important theme. No ticker-symbol soup, no clickbait, no invented numbers.
- "bullets": {MIN_BULLETS} to {MAX_BULLETS} bullets. Each under 30 words. Cover the distinct threads across the articles rather than restating one story. Use concrete figures ONLY when they appear in the articles below.
- The FINAL bullet must explain why an everyday investor should care, in plain English. NO LEAD-IN: start it with the point itself. Do NOT open it with a transition of any kind: not "In short,", "The takeaway,", "The takeaway for everyday investors,", "Ultimately,", "So,", "Bottom line,", "Overall,", "In summary,", "The upshot,", "What this means,", and never "So What?". The app marks this bullet with its own icon, so naming it in words is redundant on screen and is stripped before display — a lead-in only costs you words from the 30-word budget.
- No introductory phrases like "This article discusses" or "The key points are".
- "sentiment": exactly one of "bullish" | "bearish" | "neutral" — the NET directional lean for {subject}, judged by weighing the articles together, not by counting headlines.
    - "bullish": the balance tilts to upward catalysts (earnings beats, upgrades, wins, easing conditions, raised guidance, constructive positioning).
    - "bearish": the balance tilts to downward catalysts (misses, downgrades, investigations, recalls, tightening conditions, cut guidance).
    - "neutral": the upward and downward forces are genuinely balanced, or the articles are purely backward-looking / educational with no directional read.
  Commit to the net lean: a set that leans positive is "bullish" even if it carries caveats, and likewise "bearish" for a set that leans negative. Reserve "neutral" for a true balance — do NOT use it as a safe default.
- Never state a fact, number, company or event that is not in the articles below.
- ATTRIBUTION. The brief is about {subject} and nothing else. Several articles below may
  cover peers or the whole sector — use them only as context, and never write a headline
  that states a peer's event, or a sector-wide move, as though it happened to {subject}.
  If the articles do not support a claim about {subject} specifically, say what they do
  support in plainer terms rather than reaching for a bigger one.
- RECENCY. Each article is stamped with its publication time. Describe what is happening
  NOW; do not recap an older quarter, filing or event as if it were current news just
  because a recent article mentions it in passing.
- NAME THE METRIC. "beats estimates" is ambiguous when revenue and earnings disagree —
  and they often do. If the articles report a beat or a miss, say WHICH measure it was
  (revenue, earnings, guidance). An unqualified "beats estimates" next to an earnings
  chart showing the opposite measure reads to the user as a straight contradiction.
{price_line}

Input set: {inputset_id}

Articles:
{chr(10).join(lines)}"""


# ── Helpers ───────────────────────────────────────────────────────────

def catalyst_display_line(price_move: Optional[Dict[str, Any]]) -> str:
    """The "why it moved" text exactly as the iOS card renders it, or "".

    Kept here, next to the prompt that must describe it, because the prompt tells
    the model this line is ALREADY shown to the reader. If the two drift the
    instruction becomes a lie and the de-dup quietly stops working -- the model
    would be told not to repeat a sentence the user never sees. iOS builds the
    same string in ``InsightPriceMove.displayLine``; a source-scan guard pins the
    two together.
    """
    if not isinstance(price_move, dict):
        return ""
    reason = str(price_move.get("reason") or "").strip()
    if not reason:
        return ""
    tag = str(price_move.get("catalyst_tag") or "").strip()
    return f"{tag} — {reason}" if tag else reason


def _catalyst_block(price_move: Optional[Dict[str, Any]]) -> str:
    """Prompt fragment telling the model the move is already explained for it.

    Returns "" when there is no usable catalyst, which is the common case -- only
    non-market scopes on an Unusual/Extreme move ever get one -- so a calm
    ticker's prompt is byte-identical to what it was before this existed.
    """
    shown = catalyst_display_line(price_move)
    if not shown:
        return ""
    return (
        "\nALREADY EXPLAINED -- DO NOT REPEAT IT. The reason for the current move is\n"
        "shown to the reader directly above your bullets, on its own line, as:\n"
        f'    "{shown}"\n'
        "That line comes from a separate web-cited step. It is not yours to restate,\n"
        "re-explain, summarise or paraphrase, and no bullet may open with that event.\n"
        "Write only what it does NOT already say. If the articles hold nothing beyond\n"
        f"it, write FEWER bullets -- {MIN_BULLETS} is fine -- rather than padding with a\n"
        "reworded version of it. The headline may name the event; the bullets may not\n"
        "re-explain it."
    )


def _clip(text: str, limit: int) -> str:
    """Trim to AT MOST ``limit`` characters, cutting on a word boundary if possible.

    The ellipsis is counted against the budget. Appending it after slicing to
    ``limit`` yields ``limit + 1`` characters, which is exactly the off-by-one
    that turns a DB length CHECK into a failed write and a missing card.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def _sanitize_price_move(pm: Any) -> Optional[Dict[str, Any]]:
    """Coerce a ``price_move`` block (from the sweeper, or a DB JSONB row) to a
    clean, JSON-safe dict, or ``None``. NEVER raises — a malformed block must
    never block or fail the news card, and ``change_percent`` is finite-guarded
    so it cannot break ``allow_nan=False`` serialization.

    Requires a non-empty ``tier`` and ``reason`` (an empty block is not worth
    rendering). ``catalyst_tag`` is None for a "no clear catalyst" outcome.
    """
    if not isinstance(pm, dict):
        return None
    tier = pm.get("tier")
    reason = pm.get("reason")
    if not isinstance(tier, str) or not tier.strip():
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    tag = pm.get("catalyst_tag")
    tag = tag.strip() if isinstance(tag, str) and tag.strip() else None
    # Accept the sweeper's `change_pct` AND the stored/wire `change_percent`, so
    # re-sanitizing an already-stored block on read-back is idempotent.
    cp = finite(pm.get("change_percent", pm.get("change_pct")))
    return {
        "tier": tier.strip(),
        "change_percent": round(cp, 2) if cp is not None else None,
        "catalyst_tag": _clip(tag, 60) if tag else None,
        "reason": _clip(reason.strip(), 300),
    }


# Max source rows kept per card — a screenful of provenance, not the whole corpus.
_MAX_SOURCES = 8


def _corpus_sources(
    articles: Sequence[Dict[str, Any]], cap: int = _MAX_SOURCES
) -> List[Dict[str, Any]]:
    """The source stories a card was built from — ``[{title, url, publisher}]`` —
    from the corpus dicts (headline + article_url + source_name). Drops rows with
    no title, dedups by url (falling back to title when there is no url), and caps
    at ``cap``. Pure; the result is fed straight to ``_sanitize_sources`` on write.

    ``publisher`` is the outlet NAME as the news feed reported it ("CNBC
    Television"), never the hosting domain. It is omitted entirely when unknown,
    so the client keeps its URL-host fallback for legacy cards.

    ORDERING — material headlines first, then recency. The cap is the whole
    reason: the corpus holds ~25 rows and only ``cap`` (8) are cited, so a pure
    recency slice spent those slots on whatever happened to be newest. Measured on
    a live Jackson Hole corpus, that cited a mortgage lawsuit and two Venezuela
    oil wires while "Markets Brace for Possible Rate Hike After Warsh's Hawkish
    Turn" fell outside the list; ranking put 8 of 8 on the day's actual story.

    Deliberately a STABLE sort, so equal-materiality rows keep their newest-first
    order, and deliberately a re-ORDER rather than a filter — nothing is dropped,
    so a quiet day with fewer than ``cap`` material stories still fills the list.
    Safe against the cache: ``compute_inputset_id`` digests SORTED article ids and
    never sees this list, so reordering cannot invalidate a card or change a
    generated bullet. This is display provenance only; the model still reads the
    full corpus in its own order.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = str(a.get("headline") or "").strip()
        if not title:
            continue
        url = str(a.get("article_url") or a.get("url") or "").strip()
        key = url or title.lower()
        if key in seen:
            continue
        seen.add(key)
        row: Dict[str, Any] = {"title": title, "url": url}
        # isinstance, NOT str(...): a malformed cache row would otherwise be
        # stringified into the subtitle, rendering "{'a': 1}" under a headline.
        # `_sanitize_sources` rejects non-str, but it runs AFTER this and would
        # only ever see the coerced string.
        name = a.get("source_name")
        publisher = name.strip() if isinstance(name, str) else ""
        if publisher:
            row["publisher"] = publisher
        out.append(row)
    # Rank AFTER dedup and BEFORE the cap — ranking a list already truncated by
    # recency would sort the wrong 8 rows and change nothing that matters.
    out.sort(key=lambda r: 0 if is_material_headline(r.get("title")) else 1)
    return out[:cap]


# Max WEB (catalyst) sources folded into a card's source list. Grounded search
# can return many; keep only the most relevant few. Grounding chunks arrive
# roughly in relevance order, so a head-slice is "highest quality first".
_MAX_CATALYST_SOURCES = 3

# A bare host like "reuters.com" / "www.sub.domain.co.uk" — NOT a headline.
_BARE_DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+$")


def _catalyst_web_sources(
    raw: Any, cap: int = _MAX_CATALYST_SOURCES
) -> List[Dict[str, Any]]:
    """Map the "why it moved" catalyst's grounding sources
    (``[{title, uri, publisher}]``) into the card source shape ``[{title, url}]``.

    Grounded search returns Vertex AI redirect ``uri``s (they open and redirect to
    the publisher) and a ``title`` that is frequently a bare host like
    ``reuters.com``. Keep ``title`` when it reads like a headline; otherwise fall
    back to a capitalized publisher name so the row stays human-nameable. Dedups
    by publisher (avoid several links from one site) and by url, and caps at
    ``cap`` (grounding order ≈ relevance, so a head-slice keeps the best few).
    NEVER raises — a malformed value yields ``[]`` and never blocks the card."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen_pub: set = set()
    seen_url: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("uri") or item.get("url") or "").strip()
        if not url or url in seen_url:
            continue
        title = str(item.get("title") or "").strip()
        publisher = str(item.get("publisher") or "").strip()
        if title and not _BARE_DOMAIN_RE.match(title.lower()):
            label = title                       # a real headline
        elif publisher:
            label = publisher.capitalize()      # "reuters" -> "Reuters"
        elif title:
            # bare-domain title, no publisher — strip TLD for a name
            label = title.lower().replace("www.", "").split(".")[0].capitalize()
        else:
            continue                            # nothing nameable
        pub_key = (publisher or label).lower()
        if pub_key in seen_pub:
            continue
        seen_pub.add(pub_key)
        seen_url.add(url)
        row: Dict[str, Any] = {"title": label, "url": url}
        # Same field the corpus rows carry, so a merged list renders one way —
        # but ONLY when it says something the title does not. Grounding usually
        # returns a bare host as the `title`, and both branches above then fall
        # back to the publisher name for the label, so an unconditional write
        # renders "Reuters" as the row title AND as its subtitle.
        pub_name = publisher.capitalize() if publisher else ""
        if pub_name and pub_name.casefold() != label.casefold():
            row["publisher"] = pub_name
        out.append(row)
        if len(out) >= cap:
            break
    return out


def _merge_sources(
    corpus: List[Dict[str, Any]],
    web: List[Dict[str, Any]],
    cap: int = _MAX_SOURCES,
) -> List[Dict[str, Any]]:
    """Merge the FMP-news corpus sources with the catalyst web sources into one
    ``[{title, url}]`` list. Web sources get RESERVED slots so they always appear
    when present: corpus (real headlines / literal inputs) first, then up to
    ``_MAX_CATALYST_SOURCES`` web rows, total capped at ``cap``. Deduped by url
    (falling back to lowercased title). When ``web`` is empty this returns exactly
    ``corpus[:cap]`` — i.e. behavior is unchanged for cards with no catalyst."""
    web = (web or [])[:_MAX_CATALYST_SOURCES]
    keep = max(cap - len(web), 0)
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    for src in list(corpus or [])[:keep] + web:
        if not isinstance(src, dict):
            continue
        title = str(src.get("title") or "").strip()
        if not title:
            continue
        url = str(src.get("url") or "").strip()
        key = url or title.lower()
        if key in seen:
            continue
        seen.add(key)
        row: Dict[str, Any] = {"title": title, "url": url}
        pub = src.get("publisher")
        publisher = pub.strip() if isinstance(pub, str) else ""
        if publisher:
            row["publisher"] = publisher
        merged.append(row)
        if len(merged) >= cap:
            break
    return merged


# Publisher names are short ("Bloomberg Markets and Finance" is 29). Clipped
# anyway because this value originates upstream and is rendered on one line.
_MAX_PUBLISHER_CHARS = 80


def _sanitize_sources(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Coerce a ``sources`` value (from the builder, or a DB JSONB row) to a clean
    ``[{title, url, publisher?}]`` list, or ``None``. NEVER raises — a malformed
    value must not block or fail the news card. Drops rows without a non-empty
    title; empty url is allowed (a source with no link is still nameable, just not
    tappable). Idempotent on read-back, and bounded so a giant stored blob can't
    bloat the response.

    ``publisher`` is OMITTED when absent or blank rather than emitted as ``""``.
    This function runs on write and again on read-back, so it is the choke point
    for the whole field: cards stored before it existed have no such key and must
    keep flowing through unchanged, and the client then falls back to the URL host.
    """
    if not isinstance(raw, list):
        return None
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        url = item.get("url")
        url = url.strip() if isinstance(url, str) else ""
        row: Dict[str, Any] = {"title": _clip(title, 200), "url": _clip(url, 500)}
        publisher = item.get("publisher")
        publisher = publisher.strip() if isinstance(publisher, str) else ""
        if publisher:
            row["publisher"] = _clip(publisher, _MAX_PUBLISHER_CHARS)
        out.append(row)
        if len(out) >= _MAX_SOURCES:
            break
    return out or None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str:
    """Normalise a timestamp to the ISO-8601 form the iOS decoder expects
    (``.iso8601`` rejects fractional seconds)."""
    dt = _parse_ts(value) or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def articles_within_window(
    rows: Sequence[Dict[str, Any]], cutoff: datetime, upper: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Keep only article rows published in ``[cutoff, upper]``.

    Bounds both the sweeper's insight corpus AND the Updates endpoint's
    show/hide decision to a real time window (``CORPUS_WINDOW_HOURS``), so the
    "48h" badge is honest and a scope with no recent news surfaces no card at
    all. A row with a missing or unparseable ``published_at`` is DROPPED — an
    undated article cannot be asserted to fall inside the window, and keeping it
    would reintroduce the over-claim the window exists to remove. Non-dict rows
    are skipped rather than raising.

    ``upper`` (usually ``now`` + a small skew) drops FUTURE-dated rows: a
    parseable-but-future ``published_at`` (embargoed PR, FMP TZ glitch) would
    otherwise satisfy ``ts >= cutoff`` and fake a "24h" card for a scope whose
    only real news is >48h old — the exact over-claim this window prevents.
    """
    kept: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("published_at"))
        if ts is None or ts < cutoff:
            continue
        if upper is not None and ts > upper:
            continue
        kept.append(r)
    return kept


# Multi-ticker round-ups: how many tagged symbols before an article stops being
# "about" any one of them. FMP tags a sector wrap with every name it mentions.
_ROUNDUP_TICKER_COUNT = 3

# Legal-form tokens that carry no identifying signal. A headline writes "Oracle",
# never "Oracle Corporation", so these have to come off before the name can be
# matched against one.
_CORPORATE_SUFFIXES = frozenset({
    "inc", "inc.", "incorporated", "corp", "corp.", "corporation", "co", "co.",
    "company", "ltd", "ltd.", "limited", "plc", "llc", "lp", "l.p.", "nv", "n.v.",
    "sa", "s.a.", "ag", "ab", "as", "asa", "oyj", "spa", "se", "kgaa",
    "holding", "holdings", "group", "technologies", "the",
})

# A one-token company name below this length is too generic to assert authorship
# from ("Co", "AT&T" is fine, but a 3-letter fragment is noise).
_MIN_NAME_TOKEN_CHARS = 4


def company_name_variants(company_name: Optional[str]) -> List[str]:
    """Lowercase forms of ``company_name`` that may plausibly appear in a headline.

    Returns longest-first, so the most specific match is attempted first, and an
    empty list when there is nothing usable.

    WHY THIS IS NOT JUST ``name.lower()``: headlines drop the legal form. The old
    code tried the full name and then its first TWO words, and its own docstring
    example did not work — ``"Archer Aviation Inc."`` yields the lead ``"archer
    aviation"``, which does not appear in a headline reading ``"Archer Jumps 9%"``.
    So the name path only ever fired when a headline happened to print two or more
    words of the registered name verbatim. Combined with no caller supplying a name
    at all, it had never matched anything in production.
    """
    raw = (company_name or "").strip()
    if not raw:
        return []
    # Trim a trailing parenthetical / share-class tail: "Alphabet Inc. (Class A)".
    raw = re.split(r"[(\[]", raw, maxsplit=1)[0]
    tokens = [t for t in re.split(r"[\s,]+", raw.lower()) if t]
    # Dots removed, not stripped: "p.l.c." must reach "plc", and `.strip(".")` only
    # takes them off the ends ("p.l.c").
    while tokens and tokens[-1].replace(".", "") in _CORPORATE_SUFFIXES:
        tokens.pop()
    # A leading article too: "The Kroger Co." must reach "kroger", or the head token
    # is "the" (below the length floor) and the name path yields nothing usable.
    while tokens and tokens[0].strip(".") == "the":
        tokens.pop(0)
    if not tokens:
        return []

    variants: List[str] = []
    # The full stripped name is specific enough to trust at any length — matching is
    # whole-token, so "BP"/"GE"/"3M" hit the standalone word and nothing else. A length
    # floor here would drop exactly the names that are ALWAYS written this way.
    full = " ".join(tokens)
    if len(full) >= 2:
        variants.append(full)
    # The distinguishing head of a multi-word name: "Plug Power Inc." → "plug power" →
    # "plug"; "Archer Aviation Inc." → "archer". A DERIVED single token is the loosest
    # signal here, so it keeps the length floor: it is an OR among stronger signals and
    # is only ever tested inside THIS ticker's own feed.
    head = tokens[0]
    if len(head) >= _MIN_NAME_TOKEN_CHARS and head != full:
        variants.append(head)
    return variants


def article_is_about(
    row: Dict[str, Any], scope: str, company_name: Optional[str] = None
) -> bool:
    """Is this article ABOUT `scope`, as opposed to merely listing it?

    WHY THIS EXISTS — a real card, observed in production:

        scope         PLUG
        article_count 1
        source        "FuelCell Energy Sinks 8%, Bloom Energy Falls 3%,
                       Plug Power Drops 3%: What's Behind the Hydrogen Stock Selloff?"
        headline      "Hydrogen Stocks Face Selloff"
        trigger       band Notable->Typical (+4.16%)

    A sector wrap led by two other companies became the SOLE input for a card about
    PLUG, and the model — correctly summarising what it was given — announced a selloff
    on a day PLUG rose 4.16%. Nothing was wrong with the summary; the corpus was wrong.

    ⚠️ NEITHER OBVIOUS TEST CATCHES THAT ARTICLE:

      * tag membership — FMP genuinely tags it `PLUG`, so `scope in related_tickers`
        is true;
      * name-in-title — "Plug Power" IS in the title. It is simply THIRD, after
        FuelCell and Bloom.

    So the discriminating signal is POSITION, not presence. In a wrap, the companies are
    listed in order of what the piece is about, and ours has to lead. The leading clause
    (up to the first comma, colon or dash) is what a headline puts its subject in.

    Deliberately reads the title only, never the body: a body mention is exactly the
    passing reference this exists to reject. Not applied to `MARKET_SCOPE`, which is
    about the market by definition.

    ⚠️ `company_name` IS LOAD-BEARING — supply it. Without a name the only title signal
    is the literal SYMBOL, and headlines print "Oracle", never "ORCL". It shipped as a
    dead parameter (accepted, documented, unit-tested, passed by nobody), which starved
    every non-eponymous ticker's corpus down to whatever FMP's tag ordering happened to
    admit and froze those cards behind `fingerprint_unchanged`. The sweeper resolves it
    from `watchlist_items`; `tests/test_updates_insight_subject_wiring.py` asserts the
    call site, not just the lookup.
    """
    if not isinstance(row, dict) or not scope:
        return False

    title = str(row.get("headline") or "").strip()
    if not title:
        # An untitled row cannot be shown to be about anything. Reject rather than
        # guess — an unattributable article is the input this function exists to drop.
        return False

    symbol = scope.strip().upper()
    tags = []
    related = row.get("related_tickers")
    if isinstance(related, list):
        tags = [str(t).strip().upper() for t in related if str(t or "").strip()]

    # FMP lists the article's PRIMARY symbol first, so an article is "about" its lead
    # tag. This is the only signal that works when the title names companies but the
    # tags are symbols — "FuelCell Energy" in the title is unmatchable from "FCEL"
    # unless a ticker→name map happens to be supplied.
    #
    # Applied at EVERY tag count, not only to round-ups. Restricting it to ≥3 tags left
    # the two-tag case with no signal at all: "Oracle vs. Amazon: Which Is the Better AI
    # Cloud Stock?" tagged ["ORCL","AMZN"] failed the symbol test ("orcl" is not in a
    # headline that says "Oracle"), failed the name test (no caller supplied one), and
    # failed `tags == [symbol]` — so a story that leads with the company's own name was
    # dropped from its own corpus. Worse, it was PATH-DEPENDENT: enrichment merges
    # Gemini's extracted symbols into `related_tickers`, so an article that qualified at
    # ingest as ["ORCL"] silently stopped qualifying the moment it became
    # ["ORCL","MSFT"] — the corpus shrank as the pipeline did more work.
    #
    # Still a soft signal (an OR, never a veto): the ordering is FMP convention rather
    # than a contract, so the worst case is admitting one extra peer article among many
    # — far better than starving a ticker's corpus, which is what froze the ORCL card.
    # The pathology this filter exists to stop (a peer wrap as the SOLE input) is
    # unaffected: it is rejected precisely because our symbol is NOT the lead tag.
    #
    # ⚠️ KNOW HOW WEAK THIS MAKES THE WHOLE FILTER before tuning it. Measured against
    # 608 live articles across 14 per-ticker feeds (2026-08-26): the scope was the lead
    # tag in 608 of 608, because `news/stock?symbols=X` returns `symbol: "X"` and
    # enrichment only APPENDS Gemini's extra symbols. So on today's data this predicate
    # is close to a tautology and almost nothing is dropped. The production incident
    # below predates the create-only pre-pass in `news_cache_service`, when a
    # sweeper-discovered row had EMPTY FMP tags and enrichment filled them in Gemini's
    # (headline) order — which is how PLUG ended up third in its own feed. Tighten this
    # only against freshly measured tag data, not against the docstring's intuition.
    if tags and tags[0] == symbol:
        return True

    # A wrap names several companies; a story names one. Above the threshold we demand
    # the lead clause, below it the whole title is fair game.
    is_roundup = len(tags) >= _ROUNDUP_TICKER_COUNT
    haystack = title.lower()
    if is_roundup:
        haystack = re.split(r"[,:—–-]", haystack, maxsplit=1)[0]

    # Whole-token match, NOT a substring. A naive `in` makes every short ticker a
    # wildcard: "F" (Ford) matches the "f" inside any word, and "BE" (Bloom Energy)
    # matches "Beyond Meat" / "Best Buy" — admitting peer coverage on the strength of a
    # letter. Lookarounds rather than `\b` because a symbol may start or end with a
    # non-word character (`^GSPC`, `BRK.B`), where `\b` asserts against the wrong side
    # and silently never matches.
    if symbol and re.search(
        rf"(?<![A-Za-z0-9]){re.escape(symbol.lower())}(?![A-Za-z0-9])", haystack
    ):
        return True

    for variant in company_name_variants(company_name):
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])", haystack
        ):
            return True

    # No title match, and we are not the lead tag. The piece is about somebody else.
    #
    # The old final clause here was `return tags == [symbol]` — "accept an oblique
    # headline ('Hydrogen maker lands 5MW order') when nobody else is tagged". That case
    # is now decided earlier and identically by the lead-tag rule (a lone tag IS the lead
    # tag), so keeping it would be dead code that reads like a live guard.
    return False


def filter_to_subject(
    rows: Sequence[Dict[str, Any]],
    scope: str,
    company_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Keep only the articles actually about `scope`.

    FAILS OPEN TO NOTHING, deliberately. When no article survives, the caller gets an
    empty corpus and `generate_and_store` produces no card — which is the honest
    outcome, because every remaining article is about somebody else. The alternative
    (fall back to the unfiltered set "so there is at least a card") is precisely the
    behaviour that shipped the Hydrogen-selloff headline.
    """
    kept = [r for r in rows if article_is_about(r, scope, company_name)]
    dropped = len(rows) - len(kept)
    if dropped:
        logger.info(
            "news corpus: dropped %d/%d article(s) not about %s (peer or sector "
            "coverage); %d kept",
            dropped, len(rows), scope, len(kept),
        )
    return kept


def select_recent_corpus(
    rows: Sequence[Dict[str, Any]],
    now: datetime,
    scope: Optional[str] = None,
    company_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Pick the corpus window for a scope: prefer the last 24h, fall back to 48h.

    Returns ``(windowed_rows, window_hours)``. A scope WITH news in the last 24h
    is summarised over just that window and badged "24h"; a scope whose freshest
    news is 24–48h old uses the 48h window and is badged "48h". The two callers —
    the sweeper (corpus for the fingerprint + generation) and the Updates endpoint
    (show/hide + fallback + badge) — MUST both go through here so the badge always
    matches the news the card actually summarises. An empty return (no news in
    48h) means "no card".
    """
    # SUBJECT FILTER BEFORE THE WINDOW, not after.
    #
    # Order matters: filtering first means the 24h/48h choice is made over articles that
    # are actually about this scope. The other way round, a scope whose only 24h article
    # is a peer round-up would pick the 24h window, then filter it to empty and emit no
    # card — while a perfectly good 36h-old article about the company sat in the 48h
    # window, unread. That is a silent loss of a real card.
    #
    # `scope=None` (MARKET_SCOPE, and any caller that has not opted in) skips the filter
    # entirely, so market coverage is unaffected.
    if scope:
        rows = filter_to_subject(rows, scope, company_name)

    upper = now + timedelta(hours=_FUTURE_SKEW_HOURS)
    primary = articles_within_window(
        rows, now - timedelta(hours=PRIMARY_WINDOW_HOURS), upper
    )
    if primary:
        return primary, PRIMARY_WINDOW_HOURS
    fallback = articles_within_window(
        rows, now - timedelta(hours=CORPUS_WINDOW_HOURS), upper
    )
    if fallback:
        return fallback, CORPUS_WINDOW_HOURS

    # Both standard windows are empty. Stretch ONLY across a market that was
    # actually shut -- a quiet ticker in a normal trading week keeps the 48h
    # answer and therefore keeps getting no card, which is the honest outcome.
    extended_hours = _closed_market_window_hours(now)
    if extended_hours <= CORPUS_WINDOW_HOURS:
        return [], CORPUS_WINDOW_HOURS
    extended = articles_within_window(
        rows, now - timedelta(hours=extended_hours), upper
    )
    return extended, extended_hours


def _closed_market_window_hours(now: datetime) -> int:
    """How far back to look once 24h AND 48h have both come back empty.

    Rounds the gap since the last completed session close UP to a whole day and
    clamps it to [CORPUS_WINDOW_HOURS, MAX_WINDOW_HOURS], so the badge is always
    one of "48h" / "72h" / "96h" rather than an odd number nobody can read.

    Anchoring on the last CLOSE is the whole trick. The intuitive rule -- "extend
    across weekend days" -- fires on an ordinary Tuesday, because 48h back from a
    Tuesday afternoon lands on a Sunday. Asking instead "has the tape finished a
    session since the cutoff?" stretches on a Monday morning (last close: Friday)
    and on the Tuesday after a Monday holiday (96h), while leaving a merely quiet
    ticker mid-week at 48h. Returning CORPUS_WINDOW_HOURS means "do not stretch".

    A naive ``now`` is read as UTC, matching every other caller in this module.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    gap_hours = (now - last_completed_close(now)).total_seconds() / 3600.0
    if not math.isfinite(gap_hours) or gap_hours <= 0:
        # Clock skew, or a `now` that predates the last close. Never widen on
        # a number we cannot explain.
        return CORPUS_WINDOW_HOURS
    whole_days = int(math.ceil(gap_hours / 24.0)) * 24
    return max(CORPUS_WINDOW_HOURS, min(whole_days, MAX_WINDOW_HOURS))


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[NewsInsightService] = None


def get_news_insight_service() -> NewsInsightService:
    global _service
    if _service is None:
        _service = NewsInsightService()
    return _service
