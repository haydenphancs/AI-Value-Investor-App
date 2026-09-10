"""Pre-computed answers for the day's Ask Cay AI suggestion chips.

WHY. The chips are the questions people are most likely to tap, and — unlike anything a user
types — they are known BEFORE they are asked. So a background pass answers each of the day's
global chips once and stores it; tapping one then replays a stored answer instead of paying a
full Gemini turn and, for a big mover, a grounded web search. The chips also happen to be the
questions that benefit most from the expensive path, which is exactly what makes pre-warming
worth doing rather than merely clever.

⚠️ KEYED ON THE QUESTION, NEVER ON THE SLOT. `chat_starters_service` rebuilds its set every
`_RESPONSE_TTL_SECONDS` (900s) and its hot-ticker / hot-sector slots track the live tape, so
the day's questions DRIFT intraday. Keying on a slot index would eventually serve one
question's answer under a different question's text. Keying on the text makes drift
self-correcting: a newly promoted chip misses and is warmed on the next pass; a chip that
rotates out leaves an unread row the daily sweep removes.

🔒 THE ROWS ARE GLOBALLY SHARED. One row serves every caller. That is the same constraint
`chat_starters_service` documents for the questions, and it binds harder here because an
ANSWER is far more likely to carry something personal. So generation runs with **no user
id**: no personalisation, no memory facts, no reader lens, no watchlist. `redact_signals()`
is per-request, so a Pro-gated signal that reached this table would be served to Free users
with no filter left to catch it.

NO HTTP PATH TO GEMINI. `news_insight_service`'s header states the invariant for the Updates
cards — *"there is no code path from an HTTP handler to Gemini"* — and this mirrors it. The
endpoint only ever READS this table; every write comes from the lifespan loop in `main.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)

_TABLE = "chat_starter_answers"

# Answers longer than this are refused rather than stored. A starter chip is a one-line
# question; a multi-kilobyte answer means the model ignored `_BRIEF_STYLE`, and persisting it
# would make every tap of that chip render a wall of text for the rest of the day.
_MAX_ANSWER_CHARS = 6000

# Below this, the "answer" is a refusal or an error sentence. Storing it would pin that
# failure for the whole day — the exact trap that made three deep-dive retests read a
# byte-identical 220-char reply and look like the fix had not landed.
_MIN_ANSWER_CHARS = 80

# Warmed serially with a small gap rather than fanned out: this is background work with a
# whole day to finish, and the Updates sweeper is already competing for the same Gemini
# quota and the same FMP universe cache.
_WARM_CONCURRENCY = 2


def _today_et() -> str:
    """The ET trading day, shared with the rotation's own boundary.

    Imported from `push_dispatch_service` rather than re-implemented so the chips and their
    answers can never roll over at different moments — which would spend an evening serving
    yesterday's answers under today's questions.
    """
    from app.services.push_dispatch_service import trading_date_et

    return trading_date_et()


def question_hash(question: str) -> str:
    """Stable key for a question.

    NFKC + casefold + whitespace collapse, so a chip whose text differs only in a curly
    apostrophe, a non-breaking space or capitalisation still hits its own warmed row. The
    same normalisation must be applied on the write and the read — hence one function, used
    by both.
    """
    text = unicodedata.normalize("NFKC", question or "").casefold()
    text = " ".join(text.split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Read path (the endpoint's half) ──────────────────────────────────────────

async def lookup(question: str) -> Optional[Dict[str, Any]]:
    """Today's stored answer for this question, or None.

    NEVER raises. A missing table (migration not yet applied), a Supabase blip or a malformed
    row all mean "answer it live", which is what the app did before this existed.
    """
    if not getattr(settings, "CHAT_STARTER_WARM_ENABLED", True):
        return None
    q = (question or "").strip()
    if not q:
        return None

    def _read() -> Optional[Dict[str, Any]]:
        result = (
            get_supabase()
            .table(_TABLE)
            .select("question, answer, widget, suggestions, tokens_used")
            .eq("question_hash", question_hash(q))
            .eq("answer_date", _today_et())
            .limit(1)
            .execute()
        )
        rows = list(result.data or [])
        return rows[0] if rows else None

    try:
        row = await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "chat starter warm lookup failed (%s: %s) — answering live",
            type(e).__name__, e,
        )
        return None
    if not row:
        return None
    answer = (row.get("answer") or "").strip()
    if len(answer) < _MIN_ANSWER_CHARS:
        # Defensive: a short row should never have been written, but serving one would
        # replay a refusal all day.
        logger.warning("chat starter warm row too short to serve (%d chars)", len(answer))
        return None
    return {
        "answer": answer,
        "widget": row.get("widget") or None,
        "suggestions": list(row.get("suggestions") or []),
    }


# ── Write path (the lifespan loop's half) ────────────────────────────────────

async def warm_todays_starters() -> int:
    """Ensure every global chip for today has a stored answer. Returns how many were written.

    Self-limiting by construction: a chip already warmed today makes no Gemini call, so a
    steady state costs nothing and only genuinely new questions spend anything.
    """
    if not getattr(settings, "CHAT_STARTER_WARM_ENABLED", True):
        return 0

    from app.services.chat_starters_service import get_chat_starters_service

    try:
        starters = await get_chat_starters_service().get_starters()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "starter warm: could not read today's chips (%s: %s)", type(e).__name__, e
        )
        return 0

    # `global_starters` ONLY — the chips on the Ask Cay AI empty state. The detail-screen
    # sets are per-symbol templates, so warming them means (watchlist size × chips per
    # screen) answers a day rather than eight, which is a different feature with a different
    # cost. Deliberately out of scope.
    questions = [
        s.text for s in (getattr(starters, "global_starters", None) or [])
        if getattr(s, "text", "").strip()
    ]
    if not questions:
        return 0

    day = _today_et()
    try:
        already = await asyncio.to_thread(_warmed_hashes, day)
    except Exception as e:  # noqa: BLE001
        # A read failure must not cause a REWRITE of everything — that would spend a full
        # day's budget in one pass. Skip this round; the next one retries.
        logger.warning(
            "starter warm: could not read today's warmed set (%s: %s) — skipping this pass",
            type(e).__name__, e,
        )
        return 0

    pending = [q for q in questions if question_hash(q) not in already]
    if not pending:
        return 0

    cap = int(getattr(settings, "CHAT_STARTER_WARM_DAILY_CAP", 60))
    room = cap - len(already)
    if room <= 0:
        # Not silent: a cap that binds every day means the chips are churning faster than
        # this can follow, and the pre-warm has quietly stopped helping.
        logger.warning(
            "starter warm: daily cap %d reached (%d warmed today) — %d question(s) will "
            "answer live", cap, len(already), len(pending),
        )
        return 0
    if len(pending) > room:
        logger.warning(
            "starter warm: %d question(s) pending but only %d under the daily cap — "
            "warming the first %d", len(pending), room, room,
        )
        pending = pending[:room]

    sem = asyncio.Semaphore(_WARM_CONCURRENCY)

    async def _one(q: str) -> bool:
        async with sem:
            return await _warm_one(q, day)

    results = await asyncio.gather(*(_one(q) for q in pending), return_exceptions=True)
    written = 0
    for q, r in zip(pending, results):
        if isinstance(r, BaseException):
            logger.warning(
                "starter warm: %r raised (%s: %s)", q[:60], type(r).__name__, r
            )
        elif r:
            written += 1
    if written:
        logger.info("starter warm: stored %d/%d answer(s) for %s",
                    written, len(pending), day)
    return written


def _warmed_hashes(day: str) -> set:
    result = (
        get_supabase()
        .table(_TABLE)
        .select("question_hash")
        .eq("answer_date", day)
        .execute()
    )
    return {r["question_hash"] for r in (result.data or []) if r.get("question_hash")}


async def _warm_one(question: str, day: str) -> bool:
    """Generate and store one answer. Returns True when a row was written."""
    from app.services.chat_service import ChatService

    try:
        result = await ChatService().generate_response(
            # A synthetic, non-existent session id. `_get_recent_messages` returns [] for it,
            # which is what we want: a warmed answer must not depend on any conversation.
            session_id=str(uuid.uuid4()),
            user_message=question,
            session_type="NORMAL",
            # 🔒 Every personalisation lever, explicitly OFF rather than left to a default.
            # These rows are shared by every caller, and `CHAT_PERSONALIZATION_ENABLED` being
            # False today is a config value, not a guarantee.
            stock_id=None,
            context=None,
            context_type=None,
            reference_id=None,
            reader_lens=None,
            user_id=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "starter warm: generation failed for %r (%s: %s)",
            question[:60], type(e).__name__, e,
        )
        return False

    answer = (result.get("content") or "").strip()
    if not (_MIN_ANSWER_CHARS <= len(answer) <= _MAX_ANSWER_CHARS):
        logger.warning(
            "starter warm: refusing to store a %d-char answer for %r — a refusal or a "
            "runaway would be replayed all day", len(answer), question[:60],
        )
        return False

    row = {
        "question_hash": question_hash(question),
        "answer_date": day,
        "question": question,
        "answer": answer,
        "widget": result.get("widget"),
        "suggestions": [],
        "tokens_used": result.get("tokens_used"),
        "model": settings.GEMINI_MODEL,
    }

    def _write() -> None:
        get_supabase().table(_TABLE).upsert(
            row, on_conflict="question_hash,answer_date"
        ).execute()

    try:
        await asyncio.to_thread(_write)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "starter warm: store failed for %r (%s: %s) — the answer is lost, the question "
            "will answer live", question[:60], type(e).__name__, e,
        )
        return False
    return True


def sweep_expired() -> int:
    """Delete every answer older than today (ET). Returns the row count, best-effort.

    Yesterday's answer is not stale, it is WRONG: "Why is X down 22% today?" carrying
    yesterday's catalyst is the same class of error `daily_move_attribution` was written to
    prevent. Runs in `main.py` beside the `chat_usage_budget` sweep.
    """
    try:
        result = (
            get_supabase()
            .table(_TABLE)
            .delete()
            .lt("answer_date", _today_et())
            .execute()
        )
        count = len(result.data or [])
        if count:
            logger.info("chat_starter_answers sweep: deleted %d expired row(s)", count)
        return count
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "chat_starter_answers sweep failed (%s: %s)", type(e).__name__, e
        )
        return 0
