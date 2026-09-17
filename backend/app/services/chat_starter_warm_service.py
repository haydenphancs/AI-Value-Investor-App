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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.utils.market_hours import (
    SESSION_AFTERHOURS,
    SESSION_REGULAR,
    session_phase,
)

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


# ── The tape-bound chips are not a once-a-day answer ──────────────────────────
#
# The lifespan loop runs whenever `is_market_active()`, which starts at 04:00 ET. At
# 04:05 on a Monday the screener still reports FRIDAY's close, so "What tickers are hot
# today?" answered then is Friday's leaderboard — and, once stored, it was replayed
# (and charged) as "today" until midnight. Three rules, all on the kinds in
# `chat_starters_service.TAPE_KINDS`; evergreen chips keep the once-a-day behaviour:
#
#   1. The FIRST write waits for the day's numbers to be the day's: the regular session
#      or after the close (`_tape_is_todays`). Pre-market the tape is the prior session's.
#   2. Through the regular session the row is RE-WARMED once it is older than
#      `CHAT_STARTER_WARM_TAPE_TTL_SECONDS`, so a 09:35 answer does not stand at 15:50.
#      After the close the tape is static and the last regular-session write stands.
#   3. At READ time a tape-bound row older than twice that TTL during the regular session
#      is refused (the loop is dead or the daily cap bound): answer live, as before this
#      table existed. The stored `widget` is separately re-fetched by the endpoint when
#      older than `CHAT_STARTER_WIDGET_MAX_AGE_SECONDS`, so a 10:15 price is never
#      replayed under a green "Live" dot at 15:50.


def _tape_is_todays(now: Optional[datetime] = None) -> bool:
    """True once the screener's changes describe TODAY's session — regular or post-close."""
    return session_phase(now) in (SESSION_REGULAR, SESSION_AFTERHOURS)


def _tape_ttl_seconds() -> float:
    return float(getattr(settings, "CHAT_STARTER_WARM_TAPE_TTL_SECONDS", 3600) or 3600)


def _row_age_seconds(created_at: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since a row's `created_at`, or None when it cannot be read.

    None rather than 0 or infinity: an unreadable stamp must neither force a re-warm on
    every pass (a budget leak) nor pin a stale row for the day. The callers treat None as
    "age unknown — keep the once-a-day behaviour".
    """
    if not created_at:
        return None
    try:
        stamp = created_at if isinstance(created_at, datetime) else datetime.fromisoformat(
            str(created_at).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - stamp).total_seconds())


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
            .select("question, answer, widget, suggestions, tokens_used, created_at")
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
    age = _row_age_seconds(row.get("created_at"))
    if _stale_for_the_tape(q, age):
        # Rule 3 above. The write side should have re-warmed this an hour ago; that it
        # did not means the loop is dead or the cap bound, and neither is a reason to
        # tell a user at 15:50 what was hot at 09:35.
        logger.warning(
            "chat starter warm row for %r is %.0fs old during the regular session — "
            "answering live", q[:60], age,
        )
        return None
    widget = row.get("widget") or None
    widget_max_age = float(
        getattr(settings, "CHAT_STARTER_WIDGET_MAX_AGE_SECONDS", 900) or 900
    )
    return {
        "answer": answer,
        "widget": widget,
        # The endpoint re-fetches (or drops) a stale card by symbol; a warmed
        # `current_price` must never reach the client under a "Live" dot hours later.
        "widget_stale": bool(widget) and (age is None or age > widget_max_age),
        "suggestions": list(row.get("suggestions") or []),
    }


def _stale_for_the_tape(question: str, age: Optional[float]) -> bool:
    """Rule 3: a tape-bound row older than 2× the re-warm TTL while the tape is moving."""
    if age is None:
        return False
    from app.services.chat_starters_service import is_tape_bound

    if not is_tape_bound(question):
        return False
    if session_phase() != SESSION_REGULAR:
        return False
    return age > 2 * _tape_ttl_seconds()


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
    from app.services.chat_starters_service import TAPE_KINDS

    chips: List[Tuple[str, str]] = [
        (s.text, str(getattr(s, "kind", "") or "evergreen"))
        for s in (getattr(starters, "global_starters", None) or [])
        if getattr(s, "text", "").strip()
    ]
    if not chips:
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

    # Forget yesterday's strikes and re-warm counts; keep today's.
    for k in [k for k in _refusals if not k.startswith(day + ":")]:
        _refusals.pop(k, None)
    for k in [k for k in _rewarms if k != day]:
        _rewarms.pop(k, None)

    phase = session_phase()
    tape_ok = phase in (SESSION_REGULAR, SESSION_AFTERHOURS)
    ttl = _tape_ttl_seconds()
    pending: List[str] = []
    rewarm: set = set()
    for q, kind in chips:
        if _refusals.get(_refusal_key(day, q), 0) >= _MAX_WARM_REFUSALS:
            continue
        h = question_hash(q)
        if kind in TAPE_KINDS:
            if not tape_ok:
                # Rule 1: pre-market the tape is the previous session's. Not a warning —
                # this is every pass between 04:00 and 09:30 ET.
                logger.debug("starter warm: %r waits for today's tape (%s)", q[:60], phase)
                continue
            if h in already:
                age = _row_age_seconds(already[h])
                # Rule 2: through the regular session only; after the close the tape is
                # static and the last regular-session write stands.
                if phase == SESSION_REGULAR and age is not None and age > ttl:
                    rewarm.add(h)
                    pending.append(q)
                continue
            pending.append(q)
        elif h not in already:
            pending.append(q)
    if not pending:
        return 0

    cap = int(getattr(settings, "CHAT_STARTER_WARM_DAILY_CAP", 60))
    # Re-warms replace rows rather than adding them, so they are invisible to a cap that
    # counts rows — and the cap is a GEMINI-SPEND bound. Counted explicitly.
    room = cap - len(already) - _rewarms.get(day, 0)
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
            if question_hash(q) in rewarm:
                _rewarms[day] = _rewarms.get(day, 0) + 1
        if not (r is True):
            key = _refusal_key(day, q)
            _refusals[key] = _refusals.get(key, 0) + 1
            if _refusals[key] == _MAX_WARM_REFUSALS:
                logger.warning(
                    "starter warm: %r refused %d times today — parked until tomorrow, it "
                    "will answer live", q[:60], _MAX_WARM_REFUSALS,
                )
    if written:
        logger.info("starter warm: stored %d/%d answer(s) for %s",
                    written, len(pending), day)
    return written


# Per-(day, question) refusal counter. A chip whose answer is refused (degraded, too short,
# runaway) stores NO row, so `_warmed_hashes` never sees it, `pending` re-selects it and the
# daily cap — which counts STORED rows — cannot bound the retries: up to 64 passes/day would
# each pay a Gemini call for the same failing question. Three strikes parks it for the day.
_refusals: Dict[str, int] = {}
_MAX_WARM_REFUSALS = 3

# Per-day count of tape-bound re-warms (rule 2), charged against the daily cap.
_rewarms: Dict[str, int] = {}


def _refusal_key(day: str, question: str) -> str:
    return f"{day}:{question_hash(question)}"


def _warmed_hashes(day: str) -> Dict[str, Any]:
    """`{question_hash: created_at}` for every row stored today.

    The stamp is what rule 2 ages a tape-bound row by. A row whose stamp cannot be read
    maps to None and keeps the once-a-day behaviour (`_row_age_seconds`).
    """
    result = (
        get_supabase()
        .table(_TABLE)
        .select("question_hash, created_at")
        .eq("answer_date", day)
        .execute()
    )
    return {
        r["question_hash"]: r.get("created_at")
        for r in (result.data or []) if r.get("question_hash")
    }


async def _warm_one(question: str, day: str) -> bool:
    """Generate and store one answer. Returns True when a row was written."""
    from app.services.chat_service import ChatService

    svc = ChatService()
    try:
        result = await svc.generate_response(
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

    if result.get("degraded"):
        # A tool-less fallback answer (the function-calling round failed) has none of its
        # live market data. The live endpoint REFUNDS such a turn; a warmed copy would be
        # replayed — and charged — all day. Answer live instead.
        logger.warning(
            "starter warm: refusing to store a degraded (%s) answer for %r — it will answer live",
            result.get("degraded"), question[:60],
        )
        return False
    answer = (result.get("content") or "").strip()
    if not (_MIN_ANSWER_CHARS <= len(answer) <= _MAX_ANSWER_CHARS):
        logger.warning(
            "starter warm: refusing to store a %d-char answer for %r — a refusal or a "
            "runaway would be replayed all day", len(answer), question[:60],
        )
        return False

    # The follow-up chips too — generated once here, replayed with the answer. The row
    # used to store `[]` and the replay then paid a live suggestions call on every tap:
    # the one Gemini call the warm path could have saved for free was the one it did not.
    suggestions: list = []
    try:
        suggestions = [
            str(x).strip() for x in
            (await svc.generate_followup_suggestions(question, answer) or [])
            if str(x).strip()
        ][:2]
    except Exception as e:  # noqa: BLE001 — chips are best-effort, the answer is the row
        logger.warning(
            "starter warm: suggestions failed for %r (%s: %s) — storing none",
            question[:60], type(e).__name__, e,
        )

    row = {
        "question_hash": question_hash(question),
        "answer_date": day,
        "question": question,
        "answer": answer,
        "widget": result.get("widget"),
        "suggestions": suggestions,
        "tokens_used": result.get("tokens_used"),
        "model": settings.GEMINI_MODEL,
        # Stamped here, not left to the column default: an upsert onto the existing
        # primary key UPDATES the row and a default only fires on INSERT, so a re-warmed
        # row would otherwise keep its first write's stamp and look stale forever.
        "created_at": datetime.now(timezone.utc).isoformat(),
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
