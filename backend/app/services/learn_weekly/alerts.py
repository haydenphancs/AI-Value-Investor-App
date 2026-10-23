"""Loud alerts of the Wiser weekly engine (plan §1.9): one Telegram line in the owner's review chat.

Why a module of its own: the build step, the narrator API, the review bot and the publisher each end a
piece in a terminal status somewhere — `failed`, `skipped`, `expired`, `owner_rejected` — and a piece that
dies quietly is indistinguishable from "the owner simply got no Deep Dive this week". So every such
transition calls `notify_terminal`, which (1) logs at ERROR with every identifier (greppable from the logs
alone, CLAUDE.md "fail loudly and legibly") and (2) sends ONE loud plain-text line to the review chat:
kind, period, status, the last completed stage and a reason code.

Rules this module keeps, each the reason a caller can call it from anywhere, including its own failure
path:
  * it NEVER raises (except `CancelledError`): an alert that cannot be sent is an ERROR log line with the
    full alert text, never an exception that replaces the failure it was reporting;
  * it shares the review chat's ONE pacer and flood back-off with the marketing bot
    (`marketing/telegram_pacing`): it checks the back-off before sending, waits its turn
    (`PACER.wait()`), and records a 429 (`note_rate_limit`) so every other sender stops too;
  * plain text only (no `parse_mode`) — reasons and titles are folded onto one line, stripped of control
    characters and capped, so a model-written title or an exception text cannot inject markup or lines;
  * it never puts a URL, a token or a signed link in a line: callers pass reason CODES.

`deadman_check` is the Friday 15:00 ET guard (plan §1.4/§1.9): no Deep Dive in review by then → a loud
"No Deep Dive in review yet" line. It FAILS CLOSED: a ledger row it cannot read is reported as such, and
a line it could not deliver returns "unsent" so the day-claimed caller leaves the day open and retries.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Mapping, Optional

from app.integrations import telegram
from app.integrations.telegram import TelegramRateLimitException
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger
from app.services.learn_weekly.schedule import DeepDiveTimes
from app.services.marketing import review_service, telegram_pacing

logger = logging.getLogger(__name__)

#: One alert line, whole (Telegram allows 4096; an alert is a sentence, not a report).
ALERT_TEXT_CAP = 600
#: One interpolated value inside a line (a reason code, a title, a last_error).
FIELD_CAP = 160
#: Statuses that mean the week's Deep Dive reached the owner (or went past them) by the deadman hour.
DEADMAN_OK_STATUSES = frozenset({c.IN_REVIEW, c.APPROVED, c.PUBLISHING, c.PUBLISHED, c.REHEARSED})

_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085  ]")
_KIND_LABEL = {c.KIND_DEEP_DIVE: "Deep Dive", c.KIND_SLOT: "category piece"}


def scrub(value: Any, cap: int = FIELD_CAP) -> str:
    """`value` as ONE plain line: control characters (newlines included) become spaces, runs of
    whitespace collapse, and the result is capped at `cap` characters with an ellipsis. Never raises."""
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 — a hostile __str__ must not break an alert
        text = f"<unprintable {type(value).__name__}>"
    text = " ".join(_CONTROL.sub(" ", text).split())
    if cap <= 1:
        return text[:max(cap, 0)]
    return text if len(text) <= cap else text[: cap - 1] + "…"


def kind_label(kind: Any) -> str:
    return _KIND_LABEL.get(kind, scrub(kind or "piece", 20))


async def notify(text: str, *, loud: bool = True) -> bool:
    """Send one plain-text line to the owner's review chat. Never raises (except CancelledError).

    Returns whether Telegram accepted it (callers that must not mark a day done on an undelivered alert,
    like `deadman_check`, read it; the plan's contract callers may ignore it). An undeliverable line — the
    bot is not configured, the chat is in flood control, Telegram refused it — is logged at ERROR WITH its
    text, so the alert still exists in the logs."""
    line = scrub(text, ALERT_TEXT_CAP)
    if not line:
        logger.error("learn weekly alert: empty alert text — nothing sent")
        return False
    try:
        chat_id = review_service.review_chat_id()
        if not review_service.is_configured() or chat_id is None:
            logger.error("learn weekly alert NOT SENT (the review bot is not configured — "
                         "MARKETING_TELEGRAM_*): %s", line)
            return False
        if telegram_pacing.is_rate_limited():
            logger.error("learn weekly alert NOT SENT (Telegram flood control for %.0f s more): %s",
                         telegram_pacing.seconds_left(), line)
            return False
        await telegram_pacing.PACER.wait()
        await telegram.send_message(chat_id, line, disable_notification=not loud)
        return True
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        # Telegram's 429 — or the shared pacer refusing because ANOTHER sender's 429 closed the chat
        # while this one waited. Either way the back-off is recorded for every sender (never shortened).
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.error("learn weekly alert NOT SENT (Telegram flood control, retry_after=%s s, %s): %s",
                     e.retry_after, type(e).__name__, line)
        return False
    except Exception as e:  # noqa: BLE001 — an alert never raises
        logger.error("learn weekly alert NOT SENT (%s: %s): %s", type(e).__name__, e, line, exc_info=True)
        return False


async def notify_terminal(piece: Mapping[str, Any], *, status: str, reason: str,
                          stage: Optional[str] = None) -> None:
    """Report a piece that just ENDED in `status` (failed / skipped / expired / owner_rejected): one ERROR
    log line with every identifier, then one loud Telegram line. Never raises (except CancelledError).

    `reason` is a short CODE ("hold_ended", "catch_up_cutoff_passed", "narration_missing"); `stage` is the
    last stage that COMPLETED (defaults to the piece's `last_stage`). Called with a status outside
    `contracts.ALERT_STATUSES` it still alerts — the caller decided — but logs a WARNING: it is a bug."""
    try:
        p: Mapping[str, Any] = piece if isinstance(piece, Mapping) else {}
        if not isinstance(piece, Mapping):
            logger.warning("learn weekly notify_terminal: piece is a %s, not a row — alerting without its ids",
                           type(piece).__name__)
        if status not in c.ALERT_STATUSES:
            logger.warning("learn weekly notify_terminal: status %r is not an alert status %s — alerting anyway",
                           status, sorted(c.ALERT_STATUSES))
        last = stage if stage else (p.get("last_stage") or "none")
        title = p.get("title")
        logger.error("learn weekly: piece %s (%s %s, round %s) → %s after stage %s: %s%s",
                     p.get("id"), p.get("kind"), p.get("period_key"), p.get("review_round"), status,
                     scrub(last, 100), scrub(reason),
                     f" (last_error: {scrub(p.get('last_error'))})" if p.get("last_error") else "")
        text = (f"🔴 Learn {kind_label(p.get('kind'))} {scrub(p.get('period_key') or '?', 40)}: "
                f"{scrub(status, 32)} — after stage {scrub(last, 60)} — reason: {scrub(reason)}")
        if isinstance(title, str) and title.strip():
            text += f" · “{scrub(title, 120)}”"
        if p.get("dry_run") is True:
            text += " · DRY RUN"
        await notify(text, loud=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — never replace the failure being reported
        logger.error("learn weekly notify_terminal itself failed (%s: %s) — piece %r status %s reason %s",
                     type(e).__name__, e, (piece or {}).get("id") if isinstance(piece, Mapping) else piece,
                     status, reason, exc_info=True)


async def deadman_check(now: Any, dd: DeepDiveTimes) -> str:
    """Friday 15:00 ET: is this week's Deep Dive in front of the owner yet?

    "ok"         — the piece is in_review / approved / publishing / published (or rehearsed): nothing sent.
    "alerted"    — it is missing or still upstream of review: a loud "No Deep Dive in review yet:
                   status … / last_error …" line was DELIVERED.
    "unreadable" — the ledger read failed: fail closed — never "ok" on a guess; an alert says so.
    "unsent"     — something needed an alert but Telegram did not take it (logged at ERROR with the
                   text). The day-claimed caller treats anything but ok / alerted as not done and
                   checks again on its next wake.
    """
    try:
        piece = await ledger.get_piece(dd.period_key)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — fail closed: an unreadable row is never "ok"
        logger.error("learn weekly deadman: could not read the Deep Dive %s (%s: %s) — failing closed",
                     dd.period_key, type(e).__name__, e, exc_info=True)
        await notify(f"⚠️ Learn Deep Dive {scrub(dd.period_key, 40)}: the deadman check could not read the "
                     f"ledger ({type(e).__name__}) — whether a draft is in review is UNKNOWN; check the logs",
                     loud=True)
        return "unreadable"
    if piece is None:
        text = (f"⏰ No Deep Dive in review yet ({scrub(dd.period_key, 40)}): no piece exists — status none "
                f"/ last_error none")
        logger.error("learn weekly deadman: no piece for %s at %s", dd.period_key, now)
    else:
        status = piece.get("status")
        if status in DEADMAN_OK_STATUSES:
            logger.info("learn weekly deadman: %s is %s — ok", dd.period_key, status)
            return "ok"
        text = (f"⏰ No Deep Dive in review yet ({scrub(dd.period_key, 40)}): status {scrub(status, 32)} / "
                f"last_error {scrub(piece.get('last_error') or 'none')} / last stage "
                f"{scrub(piece.get('last_stage') or 'none', 60)}")
        logger.error("learn weekly deadman: %s is %s at %s (piece %s, last_error=%r)", dd.period_key, status,
                     now, piece.get("id"), piece.get("last_error"))
    sent = await notify(text, loud=True)
    return "alerted" if sent else "unsent"
