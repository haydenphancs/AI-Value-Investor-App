"""One plain-text Telegram message to the owner, for a job with no Telegram sweep of its own.

The health jobs tell the owner through the marketing review bot: the run-health and digest
reports (`marketing/digest_service`), Learn's alerts (`learn_weekly/alerts`) and the stake
finder (`trillion_club/stake_review`). A job outside those three uses this function, so it
reaches the same chat under the same rules:

  * it shares the chat's ONE pacer and flood back-off (`marketing/telegram_pacing`): it checks
    the back-off, waits its turn, and records a 429 so every other sender stops too;
  * plain text only, no `parse_mode`;
  * it NEVER raises (except `CancelledError`), so a caller can use it on its failure path. A
    message that cannot be delivered is an ERROR log line WITH its text, so it still exists.

One attempt. A caller that must not mark work done on an undelivered alert reads the result.
Web process only, like every sender to this chat; a script run from a laptop has no bot
configured and gets the ERROR line.
"""

from __future__ import annotations

import asyncio
import logging

from app.integrations import telegram
from app.integrations.telegram import TelegramRateLimitException

logger = logging.getLogger(__name__)

#: Telegram allows 4096. Under it, so a cut message still ends in the marker below.
MAX_ALERT_CHARS = 4000
_CUT_MARK = "\n… cut: the rest is in the Railway log."


def _fit(text: str) -> str:
    if len(text) <= MAX_ALERT_CHARS:
        return text
    return text[: MAX_ALERT_CHARS - len(_CUT_MARK)].rstrip() + _CUT_MARK


async def send_owner_alert(text: str, *, what: str, loud: bool = True) -> bool:
    """Send `text` to the owner's review chat. True when Telegram accepted it; `what` names the
    alert in the log lines ("peer floor alert")."""
    body = _fit(text.strip()) if isinstance(text, str) else ""
    if not body:
        logger.error("%s: empty alert text, nothing sent", what)
        return False
    try:
        # Lazy: the review bot's module pulls in the marketing package, and this one is imported
        # by jobs that never alert on a normal run.
        from app.services.marketing import review_service, telegram_pacing

        chat_id = review_service.review_chat_id()
        if not review_service.is_configured() or chat_id is None:
            logger.error(
                "%s NOT SENT (the review bot is not configured, MARKETING_TELEGRAM_*): %s",
                what, body,
            )
            return False
        if telegram_pacing.is_rate_limited():
            logger.error(
                "%s NOT SENT (Telegram flood control for %.0f s more): %s",
                what, telegram_pacing.seconds_left(), body,
            )
            return False
        await telegram_pacing.PACER.wait()
        await telegram.send_message(chat_id, body, disable_notification=not loud)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        # Telegram's 429, or the shared pacer refusing because another sender's 429 closed the
        # chat while this one waited. The back-off is recorded for every sender.
        from app.services.marketing import telegram_pacing

        telegram_pacing.note_rate_limit(e.retry_after)
        logger.error(
            "%s NOT SENT (Telegram flood control, retry_after=%s s, %s): %s",
            what, e.retry_after, type(e).__name__, body,
        )
        return False
    except Exception as e:  # noqa: BLE001 — an alert never raises
        logger.error("%s NOT SENT (%s: %s): %s", what, type(e).__name__, e, body, exc_info=True)
        return False
    logger.info("%s sent (%d characters)", what, len(body))
    return True
