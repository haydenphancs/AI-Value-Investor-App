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
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client, GeminiQuotaError
from app.services.ticker_report_cache import current_close_cycle_start
from app.services.updates_materiality import PROMPT_VERSION, finite
from app.utils.market_hours import is_market_active

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

# The card is presented as a "48h" summary in the iOS badge
# (``AIInsightCardResponse.badge`` in schemas/updates.py). The sweeper bounds the
# corpus to THIS window before BOTH the materiality fingerprint and generation,
# so the badge is literally true rather than a decorative label — the summary
# only ever covers articles published in the last 48 hours, and it refreshes as
# older stories age out of the window. The Updates endpoint uses the SAME window
# to decide whether to surface a card at all (no news in the window ⇒ no card).
# Change this and the badge string together.
CORPUS_WINDOW_HOURS = 48
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

_SYSTEM_INSTRUCTION = (
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
                fetched = await inflight
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
    ) -> Optional[Dict[str, Any]]:
        """Generate a card with Gemini and persist it. Returns ``None`` on any
        failure, **without writing anything**.

        ``price_move`` (optional) is the grounded "why did it move" block for a
        big move — a SEPARATE, cited field from the news bullets. It is persisted
        with the card but is purely additive: a None/malformed value never blocks
        or fails the news card.

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

        prompt = self._build_prompt(scope, articles, inputset_id, price_band, quote)

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
            emsg = str(e).lower()
            is_quota = isinstance(e, GeminiQuotaError) or any(
                s in emsg for s in ("429", "quota", "resource_exhausted", "rate limit")
            )
            if is_quota:
                # Already governed by the Gemini quota circuit breaker — a known
                # transient capacity condition, not an incident.
                logger.warning("Insight generation quota-limited for %s: %s", scope, e)
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
        stored = await asyncio.to_thread(
            self._store,
            scope, card, inputset_id, trigger_reason, len(articles), market_active,
            price_move,
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
            # Additive JSONB (migration 091). Only a well-shaped block is written;
            # a card without a big move stores NULL. Never blocks the news card.
            "price_move": _sanitize_price_move(price_move),
        }
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
    ) -> str:
        is_market = scope.startswith("__")
        subject = "the overall US stock market" if is_market else scope

        lines = []
        for i, a in enumerate(articles):
            title = re.sub(r"\s+", " ", str(a.get("headline") or "")).strip()
            text = re.sub(r"\s+", " ", str(a.get("summary") or "")).strip()
            text = _clip(text, MAX_ARTICLE_TEXT_CHARS)
            when = str(a.get("published_at") or "")[:16]
            lines.append(f"[{i}] ({when}) {title}" + (f"\n     {text}" if text else ""))

        price_line = ""
        if quote:
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
- The FINAL bullet must explain why an everyday investor should care, in plain English. Vary how you open it — sometimes a short transition like "In short," or "The takeaway," (always followed by a COMMA, never a colon), sometimes just state the insight directly. NEVER use "So What?" as a prefix.
- No introductory phrases like "This article discusses" or "The key points are".
- "sentiment": exactly one of "bullish" | "bearish" | "neutral", describing the NET directional implication for {subject}.
    - "bullish": the balance of articles points to a direct upward catalyst (earnings beats, upgrades, major wins, easing conditions).
    - "bearish": the balance points to a direct downward catalyst (misses, downgrades, investigations, recalls, tightening conditions).
    - "neutral": everything else — mixed signals, macro commentary, educational or backward-looking pieces, or unclear direction. When in doubt, choose neutral.
- Never state a fact, number, company or event that is not in the articles below.
{price_line}

Input set: {inputset_id}

Articles:
{chr(10).join(lines)}"""


# ── Helpers ───────────────────────────────────────────────────────────

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
    rows: Sequence[Dict[str, Any]], cutoff: datetime
) -> List[Dict[str, Any]]:
    """Keep only article rows published at/after ``cutoff``.

    Bounds both the sweeper's insight corpus AND the Updates endpoint's
    show/hide decision to a real time window (``CORPUS_WINDOW_HOURS``), so the
    "48h" badge is honest and a scope with no recent news surfaces no card at
    all. A row with a missing or unparseable ``published_at`` is DROPPED — an
    undated article cannot be asserted to fall inside the window, and keeping it
    would reintroduce the over-claim the window exists to remove. Non-dict rows
    are skipped rather than raising.
    """
    kept: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("published_at"))
        if ts is not None and ts >= cutoff:
            kept.append(r)
    return kept


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[NewsInsightService] = None


def get_news_insight_service() -> NewsInsightService:
    global _service
    if _service is None:
        _service = NewsInsightService()
    return _service
