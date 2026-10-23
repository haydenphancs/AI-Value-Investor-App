"""Telegram review of the Wiser weekly pieces (plan §1.9): the owner picks the topic, reads and listens to
each draft, and nothing reaches Money Moves without their ✅ (owner decision 2026-10-22: "Approve each in
Telegram").

It rides the marketing REVIEW BOT — the same bot, the same private chat, the same webhook
(`marketing.review_service.handle_update` hands every `y:` callback here AFTER its owner allow-list check,
before its own parser) and the same ONE pacer and flood back-off (`marketing.telegram_pacing`).

THE SWEEP (`review_cycle`, every Learn tick; unclaimed, fenced by compare-and-swap)
  a. Topic cards — a Deep Dive waiting for its topic (`topic_pending`, or `planned` with candidates and no
     `chosen_index`) whose topic carries no `card_message_ids`: ONE loud message listing up to 3
     candidates (label, category, tickers, score, peg facts with their SEC URLs) with buttons 1 / 2 / 3.
     The card's message id is stamped into `topic.card_message_ids` (fenced on the row's `updated_at`).
  b. Review bundles — a piece `ready_for_review`: a silent header (kind/category, DRY RUN, publish and
     hold times in ET, topic + peg URLs, the shadow judge's flags, words, narration length, the worst
     narration block, tokens), the narration as Telegram audio by a SIGNED URL of the private object (a
     link when Telegram cannot attach it), the article as plain text in ≤ 4096-character chunks sent
     silently, then ONE loud decision message: ✅ Approve · ❌ Reject · ♻️ Regenerate · ⏭ Next topic ·
     🔁 Retry narration. Then `ready_for_review → in_review` with `review_message_ids = [decision]`,
     fenced on the draft / round / narration job the owner was shown.
  Delivery is AT LEAST ONCE: a crash or a failure between the sends and the stamp sends the whole card or
  bundle again on a later tick, and the earlier message's buttons then decide nothing (their message id
  is not the stamped one). Only a decision rings (Quiet Telegram, rules/marketing.md §2). A 429 — or the
  shared pacer refusing because another sender's 429 closed the chat — stops the sweep and records the
  back-off for every sender. A piece whose bundle fails for another reason is retried after
  `BUNDLE_RETRY_SECONDS` (not every tick: each attempt re-sends silent messages).

THE TAPS (`handle_learn_callback`), data `y:<verb>:<piece uuid>` (≤ 41 bytes; only `y`/`z` were free —
marketing pins x, b, e and q invalid):
  a approve · r reject (→ the reason keyboard r1-r6, plus ♻️ / ⏭) · r1..r6 tone / accuracy / compliance /
  weak / topic / other · g regenerate · n next topic · m retry narration · t1-t3 topic pick · u 🗑 —
  withdraw an APPROVED piece's approval before it goes live, or unpublish a PUBLISHED one (asks to confirm
  either way) · uc confirm · uk keep it.
  EVERY decision is fenced twice: on the piece's status, and on the TAPPED message being one the ledger
  recorded for it (`review_message_ids`, or `topic.card_message_ids` for a pick) — a stale button from an
  older bundle or round decides nothing. Approve is one conditional UPDATE that also requires the hold to
  stay open for `APPROVE_HOLD_MARGIN_SECONDS` more (`ledger.transition(within_hold_at=now + margin)`): the
  publisher runs once a scheduler tick, so an approval in the hold's last minutes would be accepted and
  then expire unpublished. Inside the margin the answer is "too close" (with the rebuild script's
  `--publish-at` as the way on); past the hold, "too late — expired". Regenerate and Next topic are
  bounded by the writer caps (LEARN_MAX_CONTENT_REJECTIONS / LEARN_MAX_WRITER_FAILURES, cumulative per
  piece) and the engine's catch-up cut-off (`schedule.catch_up_cutoff`); Retry narration by the cut-off
  with no build time. Every tap is answered (answerCallbackQuery) exactly once, and the tapped message is
  edited to show the verdict.
  WITHDRAW (🗑 on an approved piece): one `approved → owner_rejected` compare-and-swap fenced on the
  `review_round` and `draft_sha256` the owner approved — the publisher's `approved → publishing` swap is
  fenced on the same columns, so whichever lands first wins and the owner is told which. Open narration
  jobs are cancelled and the owner alerted only after the swap won.
  THE REJECT REASON belongs to ONE round: Reject (and a withdrawal) clears the previous one, the reason
  keyboard that follows records the new one, and a rebuild carries it only from `owner_rejected` (the
  reason the owner gave for THIS draft); ♻️ / ⏭ straight from review clears it (it was an older round's).
  Timing limit: a reason tapped after ♻️ / ⏭ reaches the rewrite only if it lands while the piece is
  still `planned` — before the build step's next tick (≤ 5 min) takes it; once it is building the tap is
  refused ("it is being built") and that round is written without it.

⚠️ LEDGER RULE GAP (reported with wave B): `ledger.transition_problem` lets `planned` be entered only from
a REBUILDABLE status, so `in_review → planned` (Regenerate / Next topic, contract 4) and `topic_pending →
planned` (a topic pick, contract 1) are refused by the ledger as written. This module asks the ledger at
run time: when the direct move is allowed it is used; otherwise Regenerate / Next topic go
`in_review → owner_rejected → planned` (both legal today — the owner did turn the draft down), and a topic
pick on a `topic_pending` piece is recorded IN PLACE with an ERROR naming the needed ledger change (a
`planned` piece — the other shape the scheduler reads — is picked fully today).

Plain text only: no `parse_mode` anywhere, so model-written titles and paragraphs can never be read as
markup. The signed audio URL travels only in the request body and the owner's own chat — never in a log.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import (
    MAX_CALLBACK_DATA_BYTES,
    MAX_CAPTION_CHARS,
    MAX_MESSAGE_CHARS,
    TelegramException,
    TelegramRateLimitException,
    TelegramRequestError,
)
from app.services import learn_audio_urls
from app.services.learn_weekly import alerts, ledger, publisher, schedule
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly.errors import LearnWeeklyError
from app.services.marketing import review_service, telegram_pacing
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── callback grammar ──────────────────────────────────────────────────────────────────────────

CALLBACK_PREFIX = "y:"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
#: code → verb. Two-character codes at most: "y:uc:<uuid>" is 41 bytes, inside Telegram's 64.
VERBS: Dict[str, str] = {
    "a": "approve", "r": "reject", "g": "regenerate", "n": "next_topic", "m": "retry_narration",
    "r1": "reason", "r2": "reason", "r3": "reason", "r4": "reason", "r5": "reason", "r6": "reason",
    "t1": "pick", "t2": "pick", "t3": "pick",
    "u": "unpublish", "uc": "confirm_unpublish", "uk": "keep_published",
}
_CALLBACK_RE = re.compile(rf"y:(a|r[1-6]?|g|n|m|t[1-3]|u[ck]?):({_UUID})", re.ASCII)
#: The reject reasons (code stored in `reject_reason`, which the repair round reads) and their labels.
REJECT_REASONS: Dict[str, Tuple[str, str]] = {
    "r1": ("tone", "Tone"), "r2": ("accuracy", "Accuracy"), "r3": ("compliance", "Compliance"),
    "r4": ("weak", "Weak / boring"), "r5": ("topic", "Wrong topic"), "r6": ("other", "Other"),
}

#: Statuses a topic can be picked in: `topic_pending` (contract 1) and `planned` with an unchosen topic
#: (what the scheduler's build step reads as "waiting for its topic").
PICKABLE_STATUSES = (c.TOPIC_PENDING, c.PLANNED)
#: Statuses Regenerate / Next topic rebuild from: the owner is looking at a draft, or turned it down.
REBUILD_FROM = (c.IN_REVIEW, c.OWNER_REJECTED)
#: A bundle that failed for a non-flood reason is not re-sent before this (each try re-sends messages).
BUNDLE_RETRY_SECONDS = 900
#: Rows read per status per sweep.
SCAN_LIMIT = 20
#: The most message ids kept per piece (the decision, the publish reply, a confirm fallback, …).
MAX_REMEMBERED_MESSAGES = 20
#: Approve must leave the hold open at least this long. The publisher runs once a scheduler tick
#: (`scheduler.TICK_SECONDS` = 300 s, pinned ≤ half of this by a test), and `_publish_approved`
#: refuses a piece whose hold has ended — so an approval in the hold's last minutes was accepted
#: ("goes live within minutes") and then expired unpublished. Two ticks of margin.
APPROVE_HOLD_MARGIN_SECONDS = 600
_APPROVE_MARGIN = timedelta(seconds=APPROVE_HOLD_MARGIN_SECONDS)
#: The "too close" toast (answerCallbackQuery caps text at 200 characters).
_TOO_CLOSE_TOAST = "Too close to the hold end — use scripts/learn_weekly_rebuild.py --publish-at"
#: Judge rules that put a ⚠️ line at the TOP of the loud decision message (the first line a phone
#: notification shows). These are the two classes the deterministic validators miss off-lexicon (a buy
#: directive or a valuation verdict on a live company), and in shadow mode the judge only annotates —
#: the owner's tap is the gate, so the warning sits where the tap is. Pinned ⊆ `judge.RULE_CODES`.
WARN_JUDGE_RULES: Tuple[str, ...] = ("learn_directive", "learn_valuation_verdict")
_WARN_JUDGE_WORDS = {"learn_directive": "a buy/sell directive", "learn_valuation_verdict": "a valuation verdict"}
#: How a statistic's trend shows in the review body: the arrow the app draws (iOS `StatisticTrend`:
#: up / down / neutral; anything else draws NO arrow). An arrow is a claim the model chose, so the
#: owner must see it before approving.
_TREND_ARROWS = {"up": "↑ up", "down": "↓ down", "neutral": "→ neutral"}
#: The spoken words/s a block is judged against when only its pace is known: the middle of the
#: Chatterbox band (`learn_narrator/engines/chatterbox.py` WPS_BAND 1.8-3.6).
_WPS_CENTRE = 2.7
#: One peg fact's text in a card or header.
_PEG_TEXT_CAP = 220
_KEYBOARD_REMOVED: Dict[str, Any] = {"inline_keyboard": []}
_STATUS_WORDS = {
    c.PLANNED: "waiting to be built", c.TOPIC_PENDING: "waiting for its topic", c.BUILDING: "being built",
    c.DRAFTED: "drafted", c.MEDIA_PENDING: "being narrated", c.READY_FOR_REVIEW: "about to be sent for review",
    c.IN_REVIEW: "in review", c.APPROVED: "approved", c.PUBLISHING: "publishing", c.PUBLISHED: "published",
    c.REHEARSED: "rehearsed (dry run)", c.SKIPPED: "skipped", c.FAILED: "failed",
    c.OWNER_REJECTED: "rejected", c.EXPIRED: "expired", c.UNPUBLISHED: "unpublished",
}

# Process-local: piece id → monotonic instant before which its bundle is not re-sent after a failure.
_bundle_retry_at: Dict[str, float] = {}
_not_configured_logged = False


def callback_data(code: str, piece_id: str) -> str:
    """`y:<code>:<uuid>`. Refuses (ValueError) anything that would not parse back to the same pair — a
    button that could never be decided is a bug, not something to ship."""
    data = f"{CALLBACK_PREFIX}{code}:{piece_id}"
    if parse_callback_data(data) != (code, piece_id):
        raise ValueError(f"learn callback data for {code!r} / {piece_id!r} would not round-trip")
    return data


def parse_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    """`y:<code>:<uuid>` → (code, uuid); anything else → None. Strict: a canonical lowercase uuid, a FULL
    match (no trailing newline), at most Telegram's 64 bytes."""
    if not isinstance(data, str) or len(data.encode("utf-8", "replace")) > MAX_CALLBACK_DATA_BYTES:
        return None
    match = _CALLBACK_RE.fullmatch(data)
    return (match.group(1), match.group(2)) if match else None


def _button(text: str, code: str, pid: str) -> Dict[str, str]:
    return {"text": text, "callback_data": callback_data(code, pid)}


def decision_keyboard(pid: str) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [_button("✅ Approve", "a", pid), _button("❌ Reject", "r", pid)],
        [_button("♻️ Regenerate", "g", pid), _button("⏭ Next topic", "n", pid)],
        [_button("🔁 Retry narration", "m", pid)],
    ]}


def reasons_keyboard(pid: str, *, with_rebuild: bool) -> Dict[str, Any]:
    rows = [
        [_button(REJECT_REASONS[k][1], k, pid) for k in ("r1", "r2", "r3")],
        [_button(REJECT_REASONS[k][1], k, pid) for k in ("r4", "r5", "r6")],
    ]
    if with_rebuild:
        rows.append([_button("♻️ Regenerate", "g", pid), _button("⏭ Next topic", "n", pid)])
    return {"inline_keyboard": rows}


def unpublish_keyboard(pid: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[_button("🗑 Unpublish", "u", pid)]]}


def confirm_unpublish_keyboard(pid: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[_button("🗑 Yes, unpublish", "uc", pid), _button("Keep it", "uk", pid)]]}


def approved_keyboard(pid: str) -> Dict[str, Any]:
    """What an approved decision message keeps. The same `u` verb decides by the piece's status at tap
    time — withdraw the approval before it goes live, unpublish after — so the label names both."""
    return {"inline_keyboard": [[_button("🗑 Withdraw approval / unpublish", "u", pid)]]}


def confirm_withdraw_keyboard(pid: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[_button("🗑 Yes, withdraw", "uc", pid), _button("Keep it approved", "uk", pid)]]}


def topic_keyboard(pid: str, n: int) -> Dict[str, Any]:
    return {"inline_keyboard": [[_button(str(i + 1), f"t{i + 1}", pid) for i in range(max(0, min(n, 3)))]]}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────


def _aware(now: Any) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be a timezone-aware datetime, got {now!r}")
    return now.astimezone(timezone.utc)


def _ts(value: Any) -> Optional[datetime]:
    return ledger._parse_ts(value)


def _et(dt: Optional[datetime]) -> str:
    return dt.astimezone(ET).strftime("%a %b %d %H:%M ET") if dt else "?"


def _clock(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%H:%M ET")


def _int(value: Any) -> Optional[int]:
    return value if type(value) is int else None


def _num(value: Any) -> Optional[float]:
    """A finite float, or None. A JSON integer too large for a float (`10**400` survives `json.loads`)
    raises OverflowError in `float()` / `math.isfinite()` — caught here, because a raise in a header
    helper fails the whole bundle and the piece would expire unseen."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _status_words(status: Any) -> str:
    return _STATUS_WORDS.get(status, alerts.scrub(status or "unknown", 32))


def _fit(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    """`text` cut to `limit` UTF-16 units (Telegram's measure) with an ellipsis, never mid-character."""
    if review_service.utf16_len(text) <= limit:
        return text
    out, units = [], 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > limit - 1:
            break
        out.append(ch)
        units += width
    return "".join(out) + "…"


def message_id_of(envelope: Any) -> Optional[int]:
    """The `message_id` of a Bot API send/edit answer, or None."""
    result = envelope.get("result") if isinstance(envelope, dict) else None
    return _int(result.get("message_id")) if isinstance(result, dict) else None


def _review_ids(piece: Mapping[str, Any]) -> List[int]:
    ids = piece.get("review_message_ids")
    return [i for i in ids if type(i) is int] if isinstance(ids, list) else []


def _int_setting(name: str, default: int) -> int:
    value = getattr(settings, name, None)
    if type(value) is int and value >= 1:
        return value
    logger.error("learn review: %s=%r is not a positive int — using %d", name, value, default)
    return default


def _max_words(kind: Any) -> int:
    return c.DEEP_DIVE_WORDS[1] if kind == c.KIND_DEEP_DIVE else c.SLOT_WORDS[1]


# ── the topic ─────────────────────────────────────────────────────────────────────────────────


def topic_of(piece: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The piece's topic decision (contract 1) when it is usable — version 1 with 1-3 candidate objects —
    else None."""
    topic = piece.get("topic")
    if not isinstance(topic, dict) or topic.get("version") != 1:
        return None
    cands = topic.get("candidates")
    if not isinstance(cands, list) or not 1 <= len(cands) <= 3 or not all(isinstance(x, dict) for x in cands):
        return None
    return topic


def _awaiting_pick(topic: Mapping[str, Any]) -> bool:
    return topic.get("chosen_index") is None


def _label(cand: Mapping[str, Any]) -> str:
    for key in ("label", "title", "key"):
        if isinstance(cand.get(key), str) and cand[key].strip():
            return alerts.scrub(cand[key], 80)
    return "untitled topic"


def _cand_tickers(cand: Mapping[str, Any]) -> str:
    tickers = cand.get("tickers")
    if isinstance(tickers, (list, tuple)):
        names = [alerts.scrub(t, 12) for t in tickers if isinstance(t, str) and t.strip()][:6]
        if names:
            return ", ".join(names)
    return "no tickers"


def _peg_lines(cand: Mapping[str, Any], *, indent: str = "") -> List[str]:
    lines = []
    for fact in (cand.get("peg_facts") or [])[:3] if isinstance(cand.get("peg_facts"), list) else []:
        if not isinstance(fact, dict):
            continue
        text = alerts.scrub(fact.get("text") or "", _PEG_TEXT_CAP)
        url = fact.get("url")
        url_text = alerts.scrub(url, 300) if isinstance(url, str) and url.startswith("https://") else "(no URL)"
        if text:
            lines.append(f"{indent}• {text} — {url_text}")
    return lines


def _chosen(topic: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not topic:
        return None
    idx = topic.get("chosen_index")
    cands = topic.get("candidates") or []
    return cands[idx] if type(idx) is int and 0 <= idx < len(cands) else None


def _subjects(cand: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """`ledger.subjects_json` of the candidate's subjects, or None when they are unreadable (logged)."""
    raw = cand.get("subjects")
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("learn review: candidate subjects are a %s, not a list — not copied", type(raw).__name__)
        return None
    out = []
    for s in raw[:3]:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip():
            logger.warning("learn review: unreadable candidate subject %r — skipped", s)
            continue
        out.append(c.Subject(name=s["name"].strip(),
                             cik=s.get("cik") if isinstance(s.get("cik"), str) else None,
                             ticker=s.get("ticker") if isinstance(s.get("ticker"), str) else None))
    return ledger.subjects_json(out)


def topic_card_text(piece: Mapping[str, Any], topic: Mapping[str, Any]) -> str:
    """The Thursday topic card (pure)."""
    key = alerts.scrub(piece.get("period_key") or "?", 40)
    try:
        times = schedule.times_for_period_key(str(piece.get("period_key")))
        auto = getattr(times, "auto_pick_at", None)
    except (TypeError, ValueError):
        auto = None
    kind = alerts.kind_label(piece.get("kind"))
    lines = [f"📌 {kind[:1].upper() + kind[1:]} topic · {key} · publishes "
             f"{_et(_ts(piece.get('scheduled_publish_at')))}",
             f"Tap 1, 2 or 3. No tap by {_et(auto)} → #1 is used." if auto else "Tap a number to pick.",
             ""]
    for i, cand in enumerate(topic["candidates"][:3]):
        score = _num(cand.get("score"))
        score_text = f" · score {score:.2f}" if score is not None else ""
        cat = cand.get("category") if cand.get("category") in c.CATEGORIES else "category ?"
        quiet = " · quiet-week fallback" if cand.get("reason") == "quiet_week_fallback" else ""
        lines.append(f"{i + 1}. {_label(cand)} — {cat}")
        lines.append(f"   {_cand_tickers(cand)}{score_text}{quiet}")
        pegs = _peg_lines(cand, indent="   ")
        lines.extend(pegs or ["   • no peg fact"])
    ctx = topic.get("demand_context")
    if isinstance(ctx, dict) and ctx:
        parts = []
        for k, v in list(ctx.items())[:4]:
            if isinstance(v, list):
                vals = ", ".join(alerts.scrub(x, 16) for x in v[:6] if isinstance(x, (str, int, float)))
                parts.append(f"{alerts.scrub(k, 30)}: {vals or 'none'}")
        if parts:
            lines += ["", "Context only (not ranked on): " + " · ".join(parts)]
    return _fit("\n".join(lines))


# ── the review bundle (pure text) ─────────────────────────────────────────────────────────────


def article_words(draft: Mapping[str, Any]) -> int:
    """Body words: paragraph / subheading / callout text and bullet items (titles excluded)."""
    return publisher._words(draft)


def _scored(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> List[Tuple[float, Mapping[str, Any]]]:
    """(value, block) for every block carrying the first of `keys` it has as a finite number."""
    out = []
    for b in rows:
        value = next((v for v in (_num(b.get(k)) for k in keys) if v is not None), None)
        if value is not None:
            out.append((value, b))
    return out


def worst_block(checks: Any) -> str:
    """The narration block most worth a listen, from the job's `checks`.

    The shape `narration.complete` STORES is `{"worker": <the worker's checks>, "server":
    narration.timing_stats(...) + bytes/s}`; the worker's checks carry per-block rows under `blocks`
    (`learn_narrator.main.build_timings`: `i`, `align_coverage`, `words_per_sec`, and on Chatterbox
    `confidence`), and `bounded_checks` may DROP that list to fit the size cap — then the server's
    sentence-pace extremes (`min_spw_block` / `max_spw_block`) are the fallback. A top-level
    `blocks` list (the worker's own payload before the server wraps it; older fixtures) is read too.
    Pinned to the real writer by the parity test. Read defensively — an odd shape says so instead of
    failing the bundle.

    Which block: one with an unaligned sentence (coverage < 1: its read-along highlight drifts), else
    the lowest aligner confidence (Chatterbox), else the pace farthest from the band's middle."""
    if not isinstance(checks, dict):
        return "no checks recorded"
    direct = checks.get("worst_block")
    if isinstance(direct, (str, int, float)) and not isinstance(direct, bool):
        return alerts.scrub(direct, 80)
    worker = checks.get("worker")
    source = worker if isinstance(worker, dict) and "blocks" in worker else checks
    blocks = source.get("blocks")
    rows = [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []

    def at(b: Mapping[str, Any]) -> str:
        return f"#{alerts.scrub(b.get('i', '?'), 6)}"

    coverage = _scored(rows, ("align_coverage", "coverage"))
    if coverage:
        value, b = min(coverage, key=lambda x: x[0])
        if value < 1.0:
            return f"{at(b)} coverage {value:.2f}"
    confidence = _scored(rows, ("confidence",))
    if confidence:
        value, b = min(confidence, key=lambda x: x[0])
        return f"{at(b)} confidence {value:.2f}"
    pace = _scored(rows, ("words_per_sec", "words_per_second", "wps"))
    if pace:
        value, b = max(pace, key=lambda x: abs(x[0] - _WPS_CENTRE))
        return f"{at(b)} {value:.2f} words/s"
    if coverage:                                  # every block fully aligned and nothing else scored
        value, b = min(coverage, key=lambda x: x[0])
        return f"{at(b)} coverage {value:.2f}"
    server = checks.get("server")
    if isinstance(server, dict):
        lo, hi = _num(server.get("min_spw")), _num(server.get("max_spw"))
        lo_b, hi_b = server.get("min_spw_block"), server.get("max_spw_block")
        parts = []
        if lo is not None and lo_b is not None:
            parts.append(f"#{alerts.scrub(lo_b, 6)} fastest {lo:.2f} s/word")
        if hi is not None and hi_b is not None:
            parts.append(f"#{alerts.scrub(hi_b, 6)} slowest {hi:.2f} s/word")
        if parts:
            return " · ".join(parts)
    return "no per-block scores" if rows else "no per-block checks"


def judge_lines(validation: Any) -> List[str]:
    """The shadow judge's verdict for the header (`validation.judge`, read defensively)."""
    judge = validation.get("judge") if isinstance(validation, dict) else None
    if judge is None:
        return ["Shadow judge: not run"]
    mode = judge.get("mode") if isinstance(judge, dict) else None
    flags: Any = judge
    if isinstance(judge, dict):
        flags = next((judge[k] for k in ("flags", "verdicts", "violations") if isinstance(judge.get(k), list)), None)
        if flags is None:
            status = judge.get("status") or judge.get("error")
            return [f"Shadow judge: {alerts.scrub(status or 'no flags recorded', 80)}"]
    if not isinstance(flags, list):
        return ["Shadow judge: unreadable verdict"]
    head = f"Shadow judge ({alerts.scrub(mode, 16)})" if mode else "Shadow judge"
    if not flags:
        return [f"{head}: no flags"]
    out = [f"{head}: {len(flags)} flag(s)"]
    for f in flags[:5]:
        if isinstance(f, dict):
            rule = f.get("rule") or f.get("code") or "flag"
            where = f.get("field") or f.get("section") or f.get("label")     # `label`: JudgeRecord's Verdict
            quote = f.get("quote") or f.get("detail")
            out.append(f"• {alerts.scrub(rule, 40)}" + (f" ({alerts.scrub(where, 40)})" if where else "")
                       + (f": {alerts.scrub(quote, 120)}" if quote else ""))
        else:
            out.append(f"• {alerts.scrub(f, 120)}")
    if len(flags) > 5:
        out.append(f"• … {len(flags) - 5} more")
    return out


def judge_flagged_rules(validation: Any) -> List[str]:
    """The rule codes the judge flagged (`validation.judge`: `JudgeRecord.as_dict()` — `verdicts` of
    {field, rule, quote, …}; older shapes `flags` / `violations`, a dict's `code`, or a bare
    "rule: quote" string), in order, deduplicated. [] when nothing readable was flagged."""
    judge = validation.get("judge") if isinstance(validation, dict) else None
    flags: Any = judge
    if isinstance(judge, dict):
        flags = next((judge[k] for k in ("flags", "verdicts", "violations") if isinstance(judge.get(k), list)), None)
    if not isinstance(flags, list):
        return []
    out: List[str] = []
    for f in flags:
        rule = (f.get("rule") or f.get("code")) if isinstance(f, dict) else (
            f.split(":", 1)[0] if isinstance(f, str) else None)
        if isinstance(rule, str) and rule.strip() and rule.strip() not in out:
            out.append(rule.strip())
    return out


def judge_warning(validation: Any) -> Optional[str]:
    """The ⚠️ line that opens the decision message when the judge flagged a `WARN_JUDGE_RULES` rule
    (in shadow mode it blocks nothing, so the owner must not miss it); None otherwise."""
    hits = [r for r in WARN_JUDGE_RULES if r in judge_flagged_rules(validation)]
    if not hits:
        return None
    judge = validation.get("judge") if isinstance(validation, dict) else None
    mode = judge.get("mode") if isinstance(judge, dict) else None
    who = f"The {alerts.scrub(mode, 16)} judge" if isinstance(mode, str) and mode.strip() else "The judge"
    return (f"⚠️ {who} flagged {' and '.join(_WARN_JUDGE_WORDS[r] for r in hits)} ({', '.join(hits)}) — read "
            f"the flagged lines in the header before you approve.")


def _duration(seconds: Any) -> str:
    value = _num(seconds)
    if value is not None and value > 0:
        s = int(round(value))
        return f"{s // 60}:{s % 60:02d}"
    return "?"


def review_header_text(piece: Mapping[str, Any], job: Optional[Mapping[str, Any]],
                       draft: Mapping[str, Any]) -> str:
    """The silent first message of a review bundle (pure)."""
    topic = topic_of(piece)
    cand = _chosen(topic) or {}
    category = piece.get("category") if piece.get("category") in c.CATEGORIES else "category ?"
    dry = " · DRY RUN (approve rehearses only — nothing goes live)" if piece.get("dry_run") is True else ""
    title = piece.get("title") or draft.get("title") or "untitled"
    lines = [
        f"📘 Review · {alerts.kind_label(piece.get('kind'))} {alerts.scrub(piece.get('period_key'), 40)} · "
        f"{category} · round {_int(piece.get('review_round')) or 0}{dry}",
        f"“{alerts.scrub(title, 200)}”",
        f"Publishes {_et(_ts(piece.get('scheduled_publish_at')))} · the hold ends "
        f"{_et(_ts(piece.get('hold_until')))} (unapproved → expired)",
    ]
    if cand:
        how = "auto-picked (no tap)" if topic.get("auto_picked") is True else "picked by you"
        lines.append(f"Topic: {_label(cand)} ({_cand_tickers(cand)}) — {how}")
        pegs = _peg_lines(cand)
        lines += (["Peg facts:"] + pegs) if pegs else ["Peg facts: none recorded"]
    else:
        lines.append("Topic: not recorded")
    lines += judge_lines(piece.get("validation"))
    job = job or {}
    lines.append(f"{article_words(draft):,} words · narration {_duration(job.get('duration_seconds'))} · "
                 f"worst block: {worst_block(job.get('checks'))}")
    tokens = _int(piece.get("tokens_used"))
    lines.append(f"Writer tokens: {tokens:,}" if tokens is not None else "Writer tokens: ?")
    if piece.get("reject_reason"):
        lines.append(f"Last reject reason (fed to this round): {alerts.scrub(piece.get('reject_reason'), 60)}")
    lines.append("Next: the narration, then the article. The decision comes last.")
    return _fit("\n".join(lines))


def _trend_text(stat: Mapping[str, Any]) -> str:
    """The arrow the app will draw beside a statistic, e.g. ` (↑ up)`, plus its `trendValue` when it
    has one. The model chose it, so the owner reviews it; an unknown value is named (the app draws none)."""
    trend, extra = stat.get("trend"), stat.get("trendValue")
    extra_text = f", {alerts.scrub(extra, 40)}" if isinstance(extra, str) and extra.strip() else ""
    if trend is None:
        return f" ({extra_text[2:]})" if extra_text else ""
    shown = _TREND_ARROWS.get(trend) if isinstance(trend, str) else None
    if shown is None:
        shown = f"trend {alerts.scrub(trend, 16)!r} — the app draws no arrow"
    return f" ({shown}{extra_text})"


def article_body_text(draft: Mapping[str, Any]) -> str:
    """The article as plain text, in reading order (pure). Split into ≤ 4096 chunks by the sender."""
    out: List[str] = []
    for key in ("title", "subtitle"):
        if isinstance(draft.get(key), str) and draft[key].strip():
            out.append(draft[key].strip())
    highlights = draft.get("keyHighlights")
    if isinstance(highlights, list) and highlights:
        out.append("")
        out.append("KEY POINTS")
        for h in highlights:
            if isinstance(h, dict):
                out.append(f"• {h.get('title') or ''} — {h.get('description') or ''}".strip(" —"))
    stats = draft.get("statistics")
    if isinstance(stats, list) and stats:
        out.append("")
        out.append("BY THE NUMBERS")
        for s in stats:
            if isinstance(s, dict):
                out.append(f"• {s.get('value') or '?'} — {s.get('label') or ''}{_trend_text(s)}")
    for section in draft.get("sections") or ():
        if not isinstance(section, dict):
            continue
        out.append("")
        out.append(f"== {section.get('title') or 'Section'} ==")
        for block in section.get("content") or ():
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            text = block.get("text") if isinstance(block.get("text"), str) else ""
            if kind == "bulletList":
                for item in block.get("items") or ():
                    if isinstance(item, str):
                        out.append(f"• {item}")
            elif kind == "subheading":
                out.append(f"▸ {text}")
            elif kind == "callout":
                out.append(f"[{block.get('style') or 'callout'}] {text}")
            elif text:
                out.append(text)
            out.append("")
    return "\n".join(out).strip()


def decision_text(piece: Mapping[str, Any], title: str) -> str:
    hold = _ts(piece.get("hold_until"))
    warning = judge_warning(piece.get("validation"))
    lines = [warning] if warning else []
    lines += [f"Decide: “{alerts.scrub(title, 200)}” ({alerts.kind_label(piece.get('kind'))} "
              f"{alerts.scrub(piece.get('period_key'), 40)}, round {_int(piece.get('review_round')) or 0})",
              f"✅ Approve → goes live {_et(_ts(piece.get('scheduled_publish_at')))} (at once if that has passed)",
              "❌ Reject · ♻️ Regenerate (same topic, new draft) · ⏭ Next topic · 🔁 Retry narration",
              f"Approve by {_et(hold - _APPROVE_MARGIN if hold else None)} (the hold ends {_et(hold)}) — "
              f"otherwise it expires, unpublished."]
    if piece.get("dry_run") is True:
        lines.append("DRY RUN — Approve only rehearses; nothing goes live.")
    return "\n".join(lines)


# ── sending (the sweep) ───────────────────────────────────────────────────────────────────────


async def _send(chat_id: int, text: str, *, keyboard: Optional[Dict[str, Any]] = None, silent: bool,
                reply_to: Optional[int] = None) -> Optional[int]:
    await telegram_pacing.PACER.wait()
    env = await telegram.send_message(chat_id, text, reply_markup=keyboard, disable_notification=silent,
                                      reply_to_message_id=reply_to)
    return message_id_of(env)


async def _signed_audio_url(path: str) -> Optional[str]:
    """A signed URL of the PRIVATE narration object, minted through `learn_audio_urls` (scoped to the
    Money Moves bucket only; its signatures live 24 h). None when signing failed (logged there)."""
    pair = (learn_audio_urls.MONEY_MOVES_BUCKET, path)
    try:
        signed = await learn_audio_urls.sign_many([pair], allowed={learn_audio_urls.MONEY_MOVES_BUCKET})
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("learn review: signing the narration failed (%s: %s)", type(e).__name__, e)
        return None
    return signed.get(pair)


async def _send_audio(chat_id: int, piece: Mapping[str, Any], job: Mapping[str, Any], title: str) -> None:
    path = job.get("audio_path")
    if not publisher.is_weekly_audio_path(path):
        logger.error("learn review: piece %s job %s has no weekly audio path (%r)", piece.get("id"),
                     job.get("id"), path)
        await _send(chat_id, "⚠️ This round has no narration file — listen before approving is impossible; "
                             "use 🔁 Retry narration.", silent=True)
        return
    url = await _signed_audio_url(path)
    if not url:
        await _send(chat_id, "⚠️ The narration could not be signed for playback (see the logs). "
                             "🔁 Retry narration re-renders it.", silent=True)
        return
    try:
        await telegram_pacing.PACER.wait()
        await telegram.send_audio(chat_id, url, caption=_fit(f"Narration · {alerts.scrub(title, 200)}",
                                                              MAX_CAPTION_CHARS),
                                  title=alerts.scrub(title, 64), performer="Caydex",
                                  duration=job.get("duration_seconds"), size_bytes=job.get("audio_bytes"),
                                  disable_notification=True)
    except TelegramRequestError as e:
        # Telegram cannot attach it (too large by URL, an unfetchable URL): the owner still gets to listen.
        # The URL is a 24 h read link to one private object, sent to the owner's own chat — never logged.
        logger.warning("learn review: piece %s narration not attachable (%s: %s) — sending a link instead",
                       piece.get("id"), type(e).__name__, e)
        await _send(chat_id, f"🎧 Narration (Telegram could not attach it) — link valid 24 h:\n{url}", silent=True)


async def _review_job(piece: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    return await publisher.ready_narration_job(piece)


def _backed_off(pid: str) -> bool:
    until = _bundle_retry_at.get(pid)
    return until is not None and time.monotonic() < until


def _note_failure(pid: str) -> None:
    _bundle_retry_at[pid] = time.monotonic() + BUNDLE_RETRY_SECONDS


async def _fail_piece(piece: Mapping[str, Any], *, reason: str, stage: str) -> None:
    """A piece the review cannot ever show (its draft or narration is missing): fail it loudly instead of
    leaving it to expire unseen. Fenced on the status read, so a concurrent move wins."""
    moved = await ledger.transition(piece["id"], from_statuses=[piece.get("status")], to_status=c.FAILED,
                                    fields={"last_error": reason, "last_stage": stage},
                                    fence={"updated_at": piece.get("updated_at")})
    if moved is not None:
        try:
            await ledger.cancel_open_jobs(moved["id"], reason=f"failed: {reason}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("learn review: cancelling jobs of failed piece %s failed (%s: %s)", moved["id"],
                         type(e).__name__, e)
        await alerts.notify_terminal(moved, status=c.FAILED, reason=reason, stage=stage)


async def _send_topic_card(chat_id: int, piece: Mapping[str, Any], topic: Mapping[str, Any],
                           counters: Dict[str, int]) -> None:
    pid = str(piece["id"])
    mid = await _send(chat_id, topic_card_text(piece, topic), keyboard=topic_keyboard(pid, len(topic["candidates"])),
                      silent=False)
    if mid is None:
        raise LearnWeeklyError("the topic card was sent but Telegram returned no message id", piece_id=pid)
    counters["topic_cards"] += 1
    stamped = await ledger.transition(pid, from_statuses=[piece["status"]], to_status=piece["status"],
                                      fields={"topic": {**topic, "card_message_ids": [mid]}},
                                      fence={"updated_at": piece.get("updated_at")})
    if stamped is None:
        logger.warning("learn review: topic card for piece %s was sent but not stamped (the row changed) — it "
                       "is re-sent next tick if still unpicked; the older card's buttons are stale", pid)
    else:
        counters["stamped"] += 1


async def _send_bundle(chat_id: int, piece: Mapping[str, Any], counters: Dict[str, int]) -> None:
    pid = str(piece["id"])
    draft = piece.get("draft")
    if not isinstance(draft, dict) or not isinstance(draft.get("sections"), list) or not draft["sections"]:
        logger.error("learn review: piece %s is ready_for_review with no usable draft — failing it", pid)
        await _fail_piece(piece, reason="draft_missing", stage="review")
        return
    job = await _review_job(piece)
    if job is None:
        logger.error("learn review: piece %s is ready_for_review with no ready narration job — failing it", pid)
        await _fail_piece(piece, reason="narration_missing", stage="review")
        return
    title = piece.get("title") or draft.get("title") or "untitled"
    await _send(chat_id, review_header_text(piece, job, draft), silent=True)
    await _send_audio(chat_id, piece, job, title)
    for chunk in review_service.split_message(article_body_text(draft), MAX_MESSAGE_CHARS):
        await _send(chat_id, chunk, silent=True)
    decision = await _send(chat_id, decision_text(piece, title), keyboard=decision_keyboard(pid), silent=False)
    if decision is None:
        raise LearnWeeklyError("the decision message was sent but Telegram returned no message id", piece_id=pid)
    counters["bundles"] += 1
    stamped = await ledger.transition(
        pid, from_statuses=[c.READY_FOR_REVIEW], to_status=c.IN_REVIEW,
        fields={"review_message_ids": [decision]},
        fence={"draft_sha256": piece.get("draft_sha256"), "review_round": piece.get("review_round"),
               "narration_job_id": piece.get("narration_job_id")})
    if stamped is None:
        logger.warning("learn review: bundle for piece %s was sent but not stamped (the draft, round or job "
                       "changed) — re-sent next tick if still ready; this message's buttons are stale", pid)
    else:
        counters["stamped"] += 1
        logger.info("learn review: piece %s (%s, round %s) is in review — decision message %d", pid,
                    piece.get("period_key"), piece.get("review_round"), decision)


async def _sweep(statuses: Sequence[str], kind: Optional[str], label: str,
                 counters: Dict[str, int]) -> List[Dict[str, Any]]:
    try:
        return await ledger.list_pieces(statuses=list(statuses), kind=kind, limit=SCAN_LIMIT)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("learn review: could not list %s (%s: %s)", label, type(e).__name__, e, exc_info=True)
        counters["failed"] += 1
        return []


async def review_cycle(now: datetime) -> Dict[str, int]:
    """One review sweep: topic cards, then review bundles. Never raises (except CancelledError)."""
    now = _aware(now)
    counters = {"topic_cards": 0, "bundles": 0, "stamped": 0, "failed": 0, "rate_limited": 0}
    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None:
        counters["not_configured"] = 1
        global _not_configured_logged
        if not _not_configured_logged:          # once per process, not every 5-minute tick
            _not_configured_logged = True
            logger.error("learn review: the review bot is not configured (MARKETING_TELEGRAM_*) — nothing is "
                         "sent; pieces wait for review and EXPIRE at their hold")
        return counters
    if telegram_pacing.is_rate_limited():
        counters["rate_limited"] = 1
        logger.info("learn review: Telegram flood control for %.0f s more — sweep skipped",
                    telegram_pacing.seconds_left())
        return counters
    try:
        for piece in await _sweep(PICKABLE_STATUSES, c.KIND_DEEP_DIVE, "pieces waiting for a topic", counters):
            topic = topic_of(piece)
            if topic is None:
                # Only a topic that EXISTS but cannot be read fails the piece; a topic_pending row whose topic
                # is not written yet may be mid-write by the topic step (the deadman covers a stuck one).
                if piece.get("status") == c.TOPIC_PENDING and piece.get("topic") is not None:
                    logger.error("learn review: piece %s is topic_pending with an unreadable topic — failing it",
                                 piece.get("id"))
                    await _fail_piece(piece, reason="topic_unreadable", stage="topic")
                elif piece.get("status") == c.TOPIC_PENDING:
                    logger.warning("learn review: piece %s is topic_pending with no topic yet", piece.get("id"))
                continue
            if not _awaiting_pick(topic) or topic.get("card_message_ids"):
                continue
            hold = _ts(piece.get("hold_until"))
            if hold is not None and now >= hold:
                continue
            pid = str(piece["id"])
            if _backed_off(pid):
                continue
            try:
                await _send_topic_card(chat_id, piece, topic, counters)
            except (TelegramRateLimitException, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001 — one piece never stops the sweep
                logger.error("learn review: topic card for piece %s failed (%s: %s) — retried in %d s", pid,
                             type(e).__name__, e, BUNDLE_RETRY_SECONDS, exc_info=True)
                counters["failed"] += 1
                _note_failure(pid)
        for piece in await _sweep([c.READY_FOR_REVIEW], None, "pieces ready for review", counters):
            hold = _ts(piece.get("hold_until"))
            if hold is not None and now >= hold:
                continue                 # expire_due ends it; a bundle now could never be approved
            pid = str(piece["id"])
            if _backed_off(pid):
                continue
            try:
                await _send_bundle(chat_id, piece, counters)
                _bundle_retry_at.pop(pid, None)
            except (TelegramRateLimitException, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001
                logger.error("learn review: bundle for piece %s failed (%s: %s) — retried in %d s", pid,
                             type(e).__name__, e, BUNDLE_RETRY_SECONDS, exc_info=True)
                counters["failed"] += 1
                _note_failure(pid)
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        counters["rate_limited"] = 1
        logger.warning("learn review: Telegram flood control (retry_after=%s s, %s) — sweep stopped; the rest "
                       "go out on a later tick", e.retry_after, type(e).__name__)
    return counters


# ── auto-pick ─────────────────────────────────────────────────────────────────────────────────


def _auto_pick_at(piece: Mapping[str, Any]) -> Optional[datetime]:
    """Fri 07:00 ET of a Deep Dive's week; a slot piece (no topic card) is picked as soon as it waits."""
    try:
        times = schedule.times_for_period_key(str(piece.get("period_key")))
    except (TypeError, ValueError) as e:
        logger.error("learn review: piece %s has an unreadable period_key %r (%s) — not auto-picked",
                     piece.get("id"), piece.get("period_key"), e)
        return None
    return getattr(times, "auto_pick_at", None) or getattr(times, "build_at", None)


async def _apply_pick(piece: Mapping[str, Any], topic: Mapping[str, Any], idx: int, *, owner: bool,
                      by: Optional[str], now: datetime) -> Optional[Dict[str, Any]]:
    """Record candidate `idx` as the chosen topic and release the piece to the build (`planned`).
    Fenced on the row's `updated_at`: a concurrent pick (the owner vs the auto-pick) has one winner."""
    cand = topic["candidates"][idx]
    new_topic = {**topic, "chosen_index": idx, "owner_pick": idx if owner else topic.get("owner_pick"),
                 "auto_picked": not owner}
    fields: Dict[str, Any] = {"topic": new_topic}
    if cand.get("category") in c.CATEGORIES:
        fields["category"] = cand["category"]
    subjects = _subjects(cand)
    if subjects is not None:
        fields["subjects"] = subjects
    # `reviewed_by` / `reviewed_at` record the DRAFT decision (Approve / Reject), never a topic pick: the
    # picker is in `topic.owner_pick` / `auto_picked` and in the log line below.
    logger.info("learn review: piece %s topic #%d picked by %s at %s", piece.get("id"), idx + 1,
                by if owner else "auto-pick", now.isoformat())
    status = piece.get("status")
    to = c.PLANNED
    if status != c.PLANNED and ledger.transition_problem([status], c.PLANNED) is not None:
        to = status
        logger.error("learn review: the ledger refuses %s → planned, so piece %s keeps status %s with topic #%d "
                     "recorded — the build step reads only `planned`; allow it in ledger._ONLY_FROM[PLANNED] "
                     "(contract 1)", status, piece.get("id"), status, idx + 1)
    return await ledger.transition(piece["id"], from_statuses=[status], to_status=to, fields=fields,
                                   fence={"updated_at": piece.get("updated_at")})


async def _note_auto_pick(piece: Mapping[str, Any], topic: Mapping[str, Any]) -> None:
    """A silent line under the topic card saying #1 was auto-picked. Best effort, never raises."""
    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None or telegram_pacing.is_rate_limited():
        return
    ids = [i for i in topic.get("card_message_ids") or [] if type(i) is int]
    try:
        await _send(chat_id, f"⏱ No pick by 07:00 ET — #1 auto-picked for {alerts.scrub(piece.get('period_key'), 40)}: "
                             f"{_label(topic['candidates'][0])}", silent=True, reply_to=ids[-1] if ids else None)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
    except Exception as e:  # noqa: BLE001
        logger.warning("learn review: auto-pick note for piece %s not sent (%s: %s)", piece.get("id"),
                       type(e).__name__, e)


async def auto_pick(now: datetime) -> Dict[str, int]:
    """Pieces still waiting for a topic past their auto-pick time (Fri 07:00 ET for a Deep Dive) with no
    owner pick → candidate #1 (`chosen_index` 0, `auto_picked` true) → `planned`."""
    now = _aware(now)
    out = {"picked": 0, "waiting": 0, "lost": 0, "failed": 0}
    try:
        pieces = await ledger.list_pieces(statuses=list(PICKABLE_STATUSES), limit=SCAN_LIMIT)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("learn auto-pick: could not list pieces (%s: %s)", type(e).__name__, e, exc_info=True)
        out["failed"] += 1
        return out
    for piece in pieces:
        topic = topic_of(piece)
        if topic is None or not _awaiting_pick(topic):
            continue
        due = _auto_pick_at(piece)
        if due is None:
            out["failed"] += 1
            continue
        if now < due:
            out["waiting"] += 1
            continue
        hold = _ts(piece.get("hold_until"))
        if hold is not None and now >= hold:
            continue
        try:
            row = await _apply_pick(piece, topic, 0, owner=False, by=None, now=now)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("learn auto-pick: piece %s failed (%s: %s)", piece.get("id"), type(e).__name__, e,
                         exc_info=True)
            out["failed"] += 1
            continue
        if row is None:
            out["lost"] += 1
            continue
        out["picked"] += 1
        logger.info("learn auto-pick: piece %s (%s) — no owner pick by %s, #1 %r used", piece.get("id"),
                    piece.get("period_key"), _et(due), _label(topic["candidates"][0]))
        await _note_auto_pick(row, topic)
    return out


# ── the taps ──────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Tap:
    cq_id: str
    chat_id: int
    from_id: int
    message: Dict[str, Any]
    code: str
    pid: str
    now: datetime
    piece: Dict[str, Any] = field(default_factory=dict)
    answered: bool = False

    @property
    def by(self) -> str:
        return f"telegram:{self.from_id}"

    @property
    def message_id(self) -> Optional[int]:
        return _int(self.message.get("message_id"))


async def _answer(tap: _Tap, text: str) -> None:
    tap.answered = True
    try:
        await telegram.answer_callback_query(tap.cq_id, text)
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("learn review: answerCallbackQuery failed piece=%s (%s: %s)", tap.pid, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        logger.error("learn review: answerCallbackQuery raised piece=%s (%s: %s)", tap.pid, type(e).__name__, e,
                     exc_info=True)


async def _show(tap: _Tap, line: str, keyboard: Optional[Dict[str, Any]] = None, *,
                fallback_text: Optional[str] = None) -> Optional[int]:
    """Append `line` to the tapped message and set its keyboard (removed when None). If the message cannot
    be edited and a keyboard must be shown, it goes out on a NEW message — whose id is returned so the
    caller records it (its buttons must pass the review fence). Never raises (except CancelledError)."""
    text = review_service.append_verdict(tap.message.get("text"), line)
    markup = keyboard if keyboard is not None else _KEYBOARD_REMOVED
    try:
        if tap.message_id is None:
            raise TelegramRequestError("no message_id to edit", method="editMessageText")
        await telegram.edit_message_text(tap.chat_id, tap.message_id, text, reply_markup=markup)
        return None
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("learn review: edit hit flood control piece=%s (%s)", tap.pid, e)
        return None
    except TelegramException as e:
        if "message is not modified" in str(getattr(e, "description", "") or "").lower():
            return None
        logger.info("learn review: edit failed piece=%s (%s: %s)", tap.pid, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        logger.error("learn review: edit raised piece=%s (%s: %s)", tap.pid, type(e).__name__, e, exc_info=True)
    if keyboard is None or not keyboard.get("inline_keyboard"):
        return None
    try:
        env = await telegram.send_message(tap.chat_id, fallback_text or line, reply_markup=keyboard)
        return message_id_of(env)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
    except Exception as e:  # noqa: BLE001
        logger.warning("learn review: could not re-offer the buttons piece=%s (%s: %s)", tap.pid, type(e).__name__, e)
    return None


async def remember_message_id(piece: Mapping[str, Any], message_id: Optional[int]) -> None:
    """Add a message that carries this piece's buttons to `review_message_ids` (fenced on the list read,
    capped). Best effort: never raises — a lost id only makes that one message's buttons stale."""
    if message_id is None or not piece.get("id"):
        return
    try:
        current = await ledger.get_piece_by_id(piece["id"])
        if current is None:
            return
        ids = _review_ids(current)
        if message_id in ids:
            return
        status = current.get("status")
        stored = current.get("review_message_ids") if isinstance(current.get("review_message_ids"), list) else []
        row = await ledger.transition(current["id"], from_statuses=[status], to_status=status,
                                      fields={"review_message_ids": (ids + [message_id])[-MAX_REMEMBERED_MESSAGES:]},
                                      fence={"review_message_ids": stored})
        if row is None:
            logger.warning("learn review: message %s not recorded on piece %s (the list changed)", message_id,
                           piece["id"])
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("learn review: recording message %s on piece %s failed (%s: %s)", message_id,
                       piece.get("id"), type(e).__name__, e)


def _on_review_message(tap: _Tap) -> bool:
    return tap.message_id is not None and tap.message_id in _review_ids(tap.piece)


async def _stale(tap: _Tap, why: str) -> bool:
    logger.info("learn review: stale tap %s on piece %s (message %s): %s", tap.code, tap.pid, tap.message_id, why)
    await _answer(tap, "This button is out of date — use the latest message")
    try:
        if tap.message_id is not None:
            await telegram.edit_message_reply_markup(tap.chat_id, tap.message_id, _KEYBOARD_REMOVED)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — tidying only
        logger.info("learn review: could not clear stale buttons piece=%s (%s: %s)", tap.pid, type(e).__name__, e)
    return False


async def _not_now(tap: _Tap, what: str) -> bool:
    await _answer(tap, f"Not {what} — it is {_status_words(tap.piece.get('status'))}")
    return False


async def _explain_lost(tap: _Tap, what: str) -> bool:
    """A decision whose compare-and-swap lost: say what the piece is NOW (re-read)."""
    fresh = await ledger.get_piece_by_id(tap.pid)
    status = (fresh or {}).get("status")
    hold = _ts((fresh or {}).get("hold_until"))
    if what == "approved" and status == c.IN_REVIEW and hold is not None and tap.now >= hold:
        text = "Too late — the review hold ended; it expires unpublished"
    elif what == "approved" and status == c.IN_REVIEW and hold is not None and tap.now >= hold - _APPROVE_MARGIN:
        text = _TOO_CLOSE_TOAST          # the CAS's own margin refused it (e.g. the hold moved under the tap)
    elif fresh is None:
        text = "Piece not found"
    else:
        text = f"Not {what} — it is {_status_words(status)} now"
    await _answer(tap, text)
    return False


# approve / reject ─────────────────────────────────────────────────────────────────────────────


async def _approve(tap: _Tap) -> bool:
    piece = tap.piece
    if piece.get("status") != c.IN_REVIEW:
        if piece.get("status") == c.APPROVED:
            await _answer(tap, "Already approved")
            return False
        return await _not_now(tap, "approved")
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's decision message")
    hold = _ts(piece.get("hold_until"))
    if hold is not None and tap.now >= hold:
        await _answer(tap, "Too late — the review hold ended; it expires unpublished")
        await _show(tap, f"⏰ Too late {_clock(tap.now)} — the hold ended {_et(hold)}; it expires unpublished")
        return False
    if hold is not None and tap.now >= hold - _APPROVE_MARGIN:
        # Accepting it would say "goes live within minutes" and then let it expire: the publisher may
        # not run again before the hold ends. Say so, and name the way on.
        logger.info("learn review: approve of piece %s refused at %s — %s before the hold ends %s (margin %d s)",
                    tap.pid, tap.now.isoformat(), hold - tap.now, hold.isoformat(), APPROVE_HOLD_MARGIN_SECONDS)
        await _answer(tap, _TOO_CLOSE_TOAST)
        await _show(tap, f"⏰ Too close {_clock(tap.now)} — the hold ends {_et(hold)}, before the publisher's next "
                         f"run is sure to land, so it expires unpublished. To still run it: after it expires, "
                         f"scripts/learn_weekly_rebuild.py --period {alerts.scrub(piece.get('period_key'), 40)} "
                         f"--publish-at <new time> --yes")
        return False
    row = await ledger.transition(tap.pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED,
                                  fields={"reviewed_by": tap.by, "reviewed_at": tap.now, "last_stage": "review"},
                                  fence={"review_round": piece.get("review_round"),
                                         "draft_sha256": piece.get("draft_sha256")},
                                  within_hold_at=tap.now + _APPROVE_MARGIN)
    if row is None:
        return await _explain_lost(tap, "approved")
    dry = row.get("dry_run") is True
    sched = _ts(row.get("scheduled_publish_at"))
    when = "within minutes" if sched is not None and tap.now >= sched else _et(sched)
    logger.info("learn review: piece %s (%s, round %s) APPROVED by %s", tap.pid, row.get("period_key"),
                row.get("review_round"), tap.by)
    await _answer(tap, "Approved ✅ (DRY RUN — rehearsal only)" if dry else "Approved ✅")
    line = (f"✅ Approved {_clock(tap.now)} — DRY RUN: rehearsed at {when}, nothing goes live" if dry
            else f"✅ Approved {_clock(tap.now)} — goes live {when} · 🗑 withdraws the approval until then")
    new_id = await _show(tap, line, None if dry else approved_keyboard(tap.pid), fallback_text=line)
    await remember_message_id(row, new_id)
    return True


async def _reject(tap: _Tap) -> bool:
    piece = tap.piece
    if piece.get("status") != c.IN_REVIEW:
        if piece.get("status") == c.OWNER_REJECTED:
            await _answer(tap, "Already rejected")
            return False
        return await _not_now(tap, "rejected")
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's decision message")
    # `reject_reason: None` — a reason belongs to ONE round: whatever an earlier round left must not feed
    # this draft's rebuild; the reason keyboard that follows records this rejection's own.
    row = await ledger.transition(tap.pid, from_statuses=[c.IN_REVIEW], to_status=c.OWNER_REJECTED,
                                  fields={"reviewed_by": tap.by, "reviewed_at": tap.now, "last_stage": "review",
                                          "reject_reason": None},
                                  fence={"review_round": piece.get("review_round"),
                                         "draft_sha256": piece.get("draft_sha256")})
    if row is None:
        return await _explain_lost(tap, "rejected")
    await _cancel_jobs(tap.pid, "owner_rejected")
    await _answer(tap, "Rejected ❌")
    await alerts.notify_terminal(row, status=c.OWNER_REJECTED, reason="owner_rejected", stage="review")
    line = (f"❌ Rejected {_clock(tap.now)} — why? (optional; it feeds a rebuild) · ♻️ Regenerate / ⏭ Next "
            f"topic rebuild it")
    new_id = await _show(tap, line, reasons_keyboard(tap.pid, with_rebuild=True), fallback_text=line)
    await remember_message_id(row, new_id)
    return True


async def _reason(tap: _Tap) -> bool:
    code, label = REJECT_REASONS[tap.code]
    piece = tap.piece
    status = piece.get("status")
    if status not in (c.OWNER_REJECTED, c.PLANNED):
        await _answer(tap, f"No reason recorded — it is {_status_words(status)}")
        return False
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's review message")
    if piece.get("reject_reason") == code:
        await _answer(tap, f"Reason already saved: {label}")
        return False
    row = await ledger.transition(tap.pid, from_statuses=[status], to_status=status,
                                  fields={"reject_reason": code},
                                  fence={"review_round": piece.get("review_round")})
    if row is None:
        await _answer(tap, "Could not record that — tap again")
        return False
    logger.info("learn review: piece %s reject reason %s (by %s)", tap.pid, code, tap.by)
    await _answer(tap, f"Reason saved: {label}")
    keyboard = reasons_keyboard(tap.pid, with_rebuild=True) if status == c.OWNER_REJECTED else None
    await _show(tap, f"Reason: {label}", keyboard)
    return True


# rebuild (regenerate / next topic) ────────────────────────────────────────────────────────────


def rebuild_problem(piece: Mapping[str, Any], now: datetime, *, next_topic: bool) -> Optional[str]:
    """Why a Regenerate / Next topic is refused now, as the owner's toast; None = allowed. Bounded by the
    writer caps (cumulative per piece: every rebuild spends the same writer) and the engine's cut-off (a
    rebuild that cannot be narrated and reviewed before the hold ends would only burn money)."""
    hold = _ts(piece.get("hold_until"))
    if hold is None:
        return "Cannot rebuild — the piece's hold time is unreadable"
    # The engine the enqueue will really use (a junk setting falls back there), like build's cut-off.
    from app.services.learn_weekly import narration

    cutoff = schedule.catch_up_cutoff(hold, narration.current_engine(), _max_words(piece.get("kind")))
    if now >= cutoff:
        return f"Too late to rebuild — the cut-off was {_et(cutoff)} (the hold ends {_et(hold)})"
    rejections = _int(piece.get("content_rejections")) or 0
    failures = _int(piece.get("writer_failures")) or 0
    if rejections >= _int_setting("LEARN_MAX_CONTENT_REJECTIONS", 2):
        return f"Writer cap reached ({rejections} content rejections) — no rebuild"
    if failures >= _int_setting("LEARN_MAX_WRITER_FAILURES", 3):
        return f"Writer cap reached ({failures} writer failures) — no rebuild"
    if next_topic:
        topic = topic_of(piece)
        idx = (topic or {}).get("chosen_index")
        if topic is None or type(idx) is not int:
            return "No topic list to move on from"
        if idx + 1 >= len(topic["candidates"]):
            return "No next topic — this was the last candidate"
    return None


async def _cancel_jobs(pid: str, reason: str) -> None:
    try:
        await ledger.cancel_open_jobs(pid, reason=reason)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a stray job renders for nothing; the piece decision stands
        logger.error("learn review: cancelling open jobs of piece %s failed (%s: %s)", pid, type(e).__name__, e,
                     exc_info=True)


async def reopen(piece: Mapping[str, Any], *, fields: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Send a reviewed piece back to `planned` for a new round (`review_round + 1`, fenced on the round
    read). Direct when the ledger allows `<status> → planned`; otherwise `in_review / ready_for_review →
    owner_rejected → planned` (both legal today; see the module docstring's LEDGER RULE GAP). A crash
    between the two leaves `owner_rejected`, which the same buttons rebuild from.

    `reject_reason` is CLEARED unless `fields` carries one: the writer reads it on every round it builds,
    so a reason the owner gave for round 1 must not keep steering rounds 2, 3, … that nobody gave a
    reason for. The caller passes the reason that belongs to this rebuild (`_rebuild`: the one recorded
    after this draft was rejected)."""
    status = piece.get("status")
    rnd = _int(piece.get("review_round")) or 0
    fence = {"review_round": piece.get("review_round")}
    out = {"reject_reason": None, **fields, "review_round": rnd + 1}
    if ledger.transition_problem([status], c.PLANNED) is None:
        return await ledger.transition(piece["id"], from_statuses=[status], to_status=c.PLANNED, fields=out,
                                       fence=fence)
    if status not in (c.IN_REVIEW, c.READY_FOR_REVIEW):
        logger.error("learn review: piece %s cannot be rebuilt from %s", piece.get("id"), status)
        return None
    # The intermediate `owner_rejected` already carries the rebuild's reason, so a crash between the two
    # writes leaves the row a later ♻️ from `owner_rejected` would carry forward correctly.
    step = await ledger.transition(piece["id"], from_statuses=[status], to_status=c.OWNER_REJECTED,
                                   fields={"last_stage": "review_reopen", "reject_reason": out["reject_reason"]},
                                   fence=fence)
    if step is None:
        return None
    return await ledger.transition(piece["id"], from_statuses=[c.OWNER_REJECTED], to_status=c.PLANNED,
                                   fields=out, fence=fence)


async def _rebuild(tap: _Tap, *, next_topic: bool) -> bool:
    piece = tap.piece
    what = "moved to the next topic" if next_topic else "regenerated"
    if piece.get("status") not in REBUILD_FROM:
        return await _not_now(tap, what)
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's review message")
    problem = rebuild_problem(piece, tap.now, next_topic=next_topic)
    if problem:
        await _answer(tap, problem)
        return False
    fields: Dict[str, Any] = {"reviewed_by": tap.by, "reviewed_at": tap.now, "last_stage": "review",
                              "last_error": None}
    if piece.get("status") == c.OWNER_REJECTED:
        # Reject (and a withdrawal) cleared the older round's reason, so what is here now is the reason the
        # owner gave for THIS draft — the one the rewrite must hear. From `in_review` there is none:
        # `reopen` clears whatever an earlier round left.
        fields["reject_reason"] = piece.get("reject_reason")
    label = None
    if next_topic:
        topic = topic_of(piece)
        idx = topic["chosen_index"] + 1
        cand = topic["candidates"][idx]
        label = _label(cand)
        fields["topic"] = {**topic, "chosen_index": idx}
        if cand.get("category") in c.CATEGORIES:
            fields["category"] = cand["category"]
        subjects = _subjects(cand)
        if subjects is not None:
            fields["subjects"] = subjects
    row = await reopen(piece, fields=fields)
    if row is None:
        # Nothing cancelled: a concurrent 🔁 Retry narration may have just pointed the piece at a NEW job,
        # and cancelling before the swap would leave it media_pending on a job that never runs.
        return await _explain_lost(tap, what)
    # Only now — the piece is `planned`, so any job still open is an orphan by definition (the expiry and
    # Reject paths use the same order: swap first, cancel after).
    await _cancel_jobs(tap.pid, "next_topic" if next_topic else "regenerate")
    logger.info("learn review: piece %s %s by %s → planned, round %s", tap.pid, what, tap.by, row.get("review_round"))
    toast = f"Next topic: {label}" if next_topic else "Regenerating"
    await _answer(tap, toast)
    line = (f"⏭ Next topic {_clock(tap.now)}: {label} — a new draft (round {row.get('review_round')}) comes back "
            f"for review" if next_topic else
            f"♻️ Regenerating {_clock(tap.now)} — round {row.get('review_round')} comes back for review")
    carried = row.get("reject_reason")
    if carried:
        line += f". Your reason ({alerts.scrub(carried, 40)}) feeds the rewrite"
    line += (". Why? (optional — it feeds the rewrite only if tapped before the build starts, within ~5 min)"
             if not carried else ". To change it, tap another within ~5 min, before the build starts")
    new_id = await _show(tap, line, reasons_keyboard(tap.pid, with_rebuild=False), fallback_text=line)
    await remember_message_id(row, new_id)
    return True


# retry narration ──────────────────────────────────────────────────────────────────────────────


def _narration() -> Any:
    try:
        return importlib.import_module("app.services.learn_weekly.narration")
    except ModuleNotFoundError as e:
        if e.name != "app.services.learn_weekly.narration":
            raise
        return None


async def _retry_narration(tap: _Tap) -> bool:
    """🔁 Retry narration: queue round max + 1 FIRST, then move `in_review → media_pending` with
    `narration_job_id` = the new job in ONE compare-and-swap. The order matters: the narration
    completion handler accepts only the job `narration_job_id` names, so a status moved without its
    pointer would leave the piece waiting for a render nobody will accept. A failed enqueue changes
    nothing (the buttons stay); a lost swap leaves at most a queued job the completion handler ignores."""
    piece = tap.piece
    if piece.get("status") != c.IN_REVIEW:
        return await _not_now(tap, "re-narrated")
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's decision message")
    module = _narration()
    enqueue = getattr(module, "enqueue_for_piece", None) if module is not None else None
    pick_engine = getattr(module, "current_engine", None) if module is not None else None
    engine = pick_engine() if callable(pick_engine) else settings.LEARN_NARRATOR_ENGINE
    hold = _ts(piece.get("hold_until"))
    if hold is None or tap.now >= schedule.catch_up_cutoff(hold, engine, _max_words(piece.get("kind")),
                                                           build_seconds=0):
        await _answer(tap, "Too late to re-narrate before the hold ends")
        return False
    draft = piece.get("draft")
    if not isinstance(draft, dict) or not callable(enqueue):
        logger.error("learn review: retry narration impossible for piece %s (draft %s, narration module %s)",
                     tap.pid, type(draft).__name__, "present" if module is not None else "missing")
        await _answer(tap, "Narration retry is unavailable — see the logs")
        return False
    job: Any = None
    try:
        rounds = [j.get("round") for j in await ledger.list_jobs(tap.pid) if type(j.get("round")) is int]
        next_round = max(rounds, default=0) + 1
        job = await enqueue(piece, draft, engine=engine, round=next_round)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("learn review: queueing narration for piece %s failed (%s: %s)", tap.pid, type(e).__name__, e,
                     exc_info=True)
        job = None
    if not isinstance(job, dict) or not job.get("id"):
        await _answer(tap, "Could not queue the narration — tap again")
        return False
    row = await ledger.transition(tap.pid, from_statuses=[c.IN_REVIEW], to_status=c.MEDIA_PENDING,
                                  fields={"narration_job_id": job["id"], "last_stage": "review"},
                                  fence={"review_round": piece.get("review_round"),
                                         "draft_sha256": piece.get("draft_sha256")})
    if row is None:
        logger.warning("learn review: narration job %s (round %s) for piece %s was queued but the piece moved on "
                       "— its completion will be ignored", job["id"], job.get("round"), tap.pid)
        return await _explain_lost(tap, "re-narrated")
    logger.info("learn review: piece %s narration re-queued as job %s round %s (%s) by %s", tap.pid, job["id"],
                job.get("round"), engine, tap.by)
    await _answer(tap, "Narration re-queued 🔁")
    await _show(tap, f"🔁 Narration re-queued {_clock(tap.now)} (round {job.get('round')}, {engine}) — a new "
                     f"review bundle follows")
    return True


# topic pick ───────────────────────────────────────────────────────────────────────────────────


async def _pick(tap: _Tap) -> bool:
    piece = tap.piece
    topic = topic_of(piece)
    if piece.get("status") not in PICKABLE_STATUSES or topic is None:
        return await _not_now(tap, "picked")
    cards = [i for i in topic.get("card_message_ids") or [] if type(i) is int]
    if tap.message_id is None or tap.message_id not in cards:
        return await _stale(tap, "not this piece's topic card")
    idx = int(tap.code[1]) - 1
    if idx >= len(topic["candidates"]):
        await _answer(tap, "No such candidate")
        return False
    if not _awaiting_pick(topic):
        chosen = topic.get("chosen_index")
        how = " (auto-picked)" if topic.get("auto_picked") is True else ""
        await _answer(tap, f"Already picked #{chosen + 1 if type(chosen) is int else '?'}{how}")
        return False
    row = await _apply_pick(piece, topic, idx, owner=True, by=tap.by, now=tap.now)
    if row is None:
        return await _explain_lost(tap, "picked")
    label = _label(topic["candidates"][idx])
    logger.info("learn review: piece %s topic #%d (%s) picked by %s", tap.pid, idx + 1, label, tap.by)
    await _answer(tap, f"Picked #{idx + 1}: {label}")
    await _show(tap, f"✅ Picked #{idx + 1} {_clock(tap.now)}: {label}")
    return True


# unpublish ────────────────────────────────────────────────────────────────────────────────────


def _withdrawable(piece: Mapping[str, Any]) -> bool:
    """An approved piece that can still go live: 🗑 withdraws its approval (a dry run never goes live, and
    its Approve offered no 🗑)."""
    return piece.get("status") == c.APPROVED and piece.get("dry_run") is not True


async def _withdraw_ask(tap: _Tap) -> bool:
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's message")
    sched = _ts(tap.piece.get("scheduled_publish_at"))
    when = "within minutes" if sched is not None and tap.now >= sched else _et(sched)
    line = (f"Withdraw the approval? It will not go live ({when}); the piece ends rejected, and ♻️ Regenerate / "
            f"⏭ Next topic can still rebuild it while there is time. If the publisher gets there first, this "
            f"button unpublishes it instead.")
    new_id = await _show(tap, line, confirm_withdraw_keyboard(tap.pid), fallback_text=line)
    await remember_message_id(tap.piece, new_id)
    await _answer(tap, "Confirm the withdrawal")
    return True


async def _withdraw_confirm(tap: _Tap) -> bool:
    """`approved → owner_rejected` in ONE compare-and-swap fenced on the round and draft the owner approved.
    The publisher's `approved → publishing` is fenced on the same columns, so exactly one of the two lands;
    the owner is told which."""
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's message")
    piece = tap.piece
    row = await ledger.transition(tap.pid, from_statuses=[c.APPROVED], to_status=c.OWNER_REJECTED,
                                  fields={"reviewed_by": tap.by, "reviewed_at": tap.now, "last_stage": "review",
                                          "reject_reason": None},
                                  fence={"review_round": piece.get("review_round"),
                                         "draft_sha256": piece.get("draft_sha256")})
    if row is None:
        fresh = await ledger.get_piece_by_id(tap.pid)
        status = (fresh or {}).get("status")
        if status in (c.PUBLISHING, c.PUBLISHED):
            # The publisher won the race: say so plainly and hand over the unpublish button.
            logger.warning("learn review: withdraw of piece %s by %s lost to the publisher — it is %s", tap.pid,
                           tap.by, status)
            await _answer(tap, "Too late — it went live first; use 🗑 Unpublish")
            line = (f"⚠️ Not withdrawn {_clock(tap.now)} — the publisher got there first; it is "
                    f"{_status_words(status)}. 🗑 Unpublish takes it down"
                    + (" (tap again in a minute, once publishing has finished)." if status == c.PUBLISHING else "."))
            new_id = await _show(tap, line, unpublish_keyboard(tap.pid), fallback_text=line)
            await remember_message_id(fresh or piece, new_id)
            return False
        return await _explain_lost(tap, "withdrawn")
    logger.warning("learn review: piece %s (%s, round %s) APPROVAL WITHDRAWN by %s — it will not go live",
                   tap.pid, row.get("period_key"), row.get("review_round"), tap.by)
    await _cancel_jobs(tap.pid, "approval_withdrawn")
    await _answer(tap, "Approval withdrawn — it will not go live")
    await alerts.notify_terminal(row, status=c.OWNER_REJECTED, reason="approval_withdrawn", stage="review")
    line = (f"↩️ Approval withdrawn {_clock(tap.now)} — it will not go live. Why? (optional; it feeds a rebuild) · "
            f"♻️ Regenerate / ⏭ Next topic rebuild it")
    new_id = await _show(tap, line, reasons_keyboard(tap.pid, with_rebuild=True), fallback_text=line)
    await remember_message_id(row, new_id)
    return True


async def _unpublish_ask(tap: _Tap) -> bool:
    if _withdrawable(tap.piece):
        return await _withdraw_ask(tap)
    if tap.piece.get("status") != c.PUBLISHED:
        if tap.piece.get("dry_run") is True:
            await _answer(tap, "DRY RUN pieces never go live — nothing to unpublish")
            return False
        await _answer(tap, f"Nothing to unpublish — it is {_status_words(tap.piece.get('status'))}")
        return False
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's message")
    line = ("Unpublish it? It leaves Money Moves at once; apps that already loaded it keep it until their next "
            "session.")
    new_id = await _show(tap, line, confirm_unpublish_keyboard(tap.pid), fallback_text=line)
    await remember_message_id(tap.piece, new_id)
    await _answer(tap, "Confirm the unpublish")
    return True


async def _unpublish_confirm(tap: _Tap) -> bool:
    if _withdrawable(tap.piece):
        return await _withdraw_confirm(tap)
    if tap.piece.get("status") != c.PUBLISHED:
        if tap.piece.get("status") == c.UNPUBLISHED:
            await _answer(tap, "Already unpublished")
            return False
        return await _not_now(tap, "unpublished")
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's message")
    done = await publisher.unpublish(tap.pid, now=tap.now)
    if not done:
        return await _explain_lost(tap, "unpublished")
    logger.warning("learn review: piece %s UNPUBLISHED by %s", tap.pid, tap.by)
    await _answer(tap, "Unpublished 🗑")
    await _show(tap, f"🗑 Unpublished {_clock(tap.now)} — gone from Money Moves; apps that already loaded it keep it "
                     f"until their next session")
    return True


async def _unpublish_keep(tap: _Tap) -> bool:
    if _withdrawable(tap.piece):
        if not _on_review_message(tap):
            return await _stale(tap, "not this piece's message")
        sched = _ts(tap.piece.get("scheduled_publish_at"))
        when = "within minutes" if sched is not None and tap.now >= sched else _et(sched)
        await _show(tap, f"Kept — still approved; goes live {when}.", approved_keyboard(tap.pid))
        await _answer(tap, "Kept")
        return True
    if tap.piece.get("status") != c.PUBLISHED:
        await _answer(tap, f"It is {_status_words(tap.piece.get('status'))}")
        return False
    if not _on_review_message(tap):
        return await _stale(tap, "not this piece's message")
    await _show(tap, "Kept — still live.", unpublish_keyboard(tap.pid))
    await _answer(tap, "Kept")
    return True


_HANDLERS = {
    "approve": _approve, "reject": _reject, "reason": _reason,
    "regenerate": lambda t: _rebuild(t, next_topic=False), "next_topic": lambda t: _rebuild(t, next_topic=True),
    "retry_narration": _retry_narration, "pick": _pick,
    "unpublish": _unpublish_ask, "confirm_unpublish": _unpublish_confirm, "keep_published": _unpublish_keep,
}


async def handle_learn_callback(*, cq_id: str, chat_id: int, from_id: int, message: Any, data: Any,
                                now: Optional[datetime] = None) -> bool:
    """Apply one `y:` button tap. Called ONLY by `marketing.review_service.handle_update`, AFTER its owner
    allow-list (the tapping user and the chat are the configured owner). Returns whether the tap decided or
    recorded something. Never raises (except CancelledError); every tap is answered exactly once."""
    parsed = parse_callback_data(data)
    msg = message if isinstance(message, dict) else {}
    when = now if isinstance(now, datetime) and now.tzinfo is not None else datetime.now(timezone.utc)
    tap = _Tap(cq_id=cq_id, chat_id=chat_id, from_id=from_id, message=msg, code=(parsed or ("", ""))[0],
               pid=(parsed or ("", ""))[1], now=when.astimezone(timezone.utc))
    if parsed is None:
        logger.warning("learn review: unknown callback data %r from %s — nothing decided", str(data)[:80], from_id)
        await _answer(tap, "Unknown action")
        return False
    decided = False
    try:
        piece = await ledger.get_piece_by_id(tap.pid)
        if piece is None:
            await _answer(tap, "Piece not found")
            await _show(tap, "Piece not found")
        else:
            tap.piece = piece
            decided = bool(await _HANDLERS[VERBS[tap.code]](tap))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the webhook answers 200 whatever happens
        logger.error("learn review: tap %s on piece %s FAILED (%s: %s) — the buttons stay; tap again", tap.code,
                     tap.pid, type(e).__name__, e, exc_info=True)
        if not tap.answered:
            await _answer(tap, "Could not record that — tap again")
        return False
    if not tap.answered:     # a handler path that forgot to answer: the client's spinner must still stop
        logger.error("learn review: tap %s on piece %s was not answered by its handler", tap.code, tap.pid)
        await _answer(tap, "Done")
    return decided
