"""
Telegram Bot API — a thin client for the marketing REVIEW BOT (design doc §12).

ONE caller: `app/services/marketing/review_service.py`, in the WEB process. The owner approves or
rejects each day's `pending_review` marketing posts from their phone: the review sweep sends one
message per post with ✅/❌ buttons, and the button taps come back to the root webhook
`POST /marketing/telegram/webhook` (app/main.py). The media worker never holds this token — it
holds no social secret at all (rules/marketing.md §2).

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level `httpx.AsyncClient` closed in the app lifespan (`close_telegram_client`); no
caching, no business decisions, no Supabase.

⚠️ THE BOT TOKEN IS IN THE URL PATH (`https://api.telegram.org/bot<token>/<method>`), and that URL
leaks three ways unless handled:
  1. httpx logs every request URL at INFO (`HTTP Request: POST https://…/bot<token>/sendMessage`).
     `_HttpxTelegramTokenFilter` rewrites those records on the `httpx` LOGGER itself, so the
     token never reaches any handler (not only the root handlers that carry
     `SecretRedactingFilter` — pytest's caplog and Sentry's logging hook sit elsewhere).
  2. httpx exception messages can carry the request URL. This module never chains a transport
     exception (`from None`) and never puts a URL in its own messages: every message is built
     from the METHOD name, the HTTP status and Telegram's `description` only, with the token
     scrubbed out of anything upstream-sourced as a backstop.
  3. `app/log_redaction.py` redacts `<digits>:<35 token chars>` everywhere (logs, Sentry frames,
     breadcrumbs) as the last line of defence.

Bot API facts this module relies on — VERIFIED against https://core.telegram.org/bots/api on
2026-09-29 (the page text, not memory):
  * Every answer is JSON `{"ok": bool, "result"?, "description"?, "error_code"?,
    "parameters"?: ResponseParameters}`; `parameters.retry_after` = "In case of exceeding flood
    control, the number of seconds left to wait before the request can be repeated".
  * setWebhook(url, allowed_updates, drop_pending_updates, secret_token): secret_token is
    "1-256 characters. Only characters A-Z, a-z, 0-9, _ and - are allowed", sent back in the
    `X-Telegram-Bot-Api-Secret-Token` header of EVERY webhook request. "In case of an
    unsuccessful request (a request with response HTTP status code different from 2XY), we will
    repeat the request and give up after a reasonable amount of attempts" — so the webhook
    answers 2xx for anything it has authenticated, including a poison update.
  * sendMessage: text "1-4096 characters after entities parsing"; reply_markup takes an
    InlineKeyboardMarkup. `disable_web_page_preview` is GONE from the parameter table — the
    current field is `link_preview_options` (LinkPreviewOptions.is_disabled). We never send
    `parse_mode`: the text is plain, so model-written copy can never be interpreted as markup.
  * sendVideo: `video` may be "an HTTP URL as a String for Telegram to get a video from the
    Internet"; caption "0-1024 characters after entities parsing"; supports_streaming. Sending
    Files: by URL "5 MB max size for photos and 20 MB max for other types of content".
  * editMessageText(chat_id, message_id, text 1-4096, reply_markup: InlineKeyboardMarkup).
  * answerCallbackQuery(callback_query_id, text "0-200 characters"); clients show a progress
    bar until it is called, so every callback is answered, even a refused one.
  * InlineKeyboardButton.callback_data: "1-64 bytes".
  * CallbackQuery: id, from (User), message (MaybeInaccessibleMessage: an InaccessibleMessage has
    `date` "Always 0" and no text), data.
  * FAQ (https://core.telegram.org/bots/faq): "In a single chat, avoid sending more than one
    message per second" — the sweep spaces its sends.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Sequence

import httpx

from app.config import settings
from app.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

#: Limits from the Bot API page (see the module docstring for the verified wording).
MAX_MESSAGE_CHARS = 4096
MAX_CAPTION_CHARS = 1024
MAX_CALLBACK_DATA_BYTES = 64
MAX_CALLBACK_ANSWER_CHARS = 200
#: Telegram fetches a URL-sent video itself, up to this size ("20 MB max for other types").
MAX_URL_SEND_BYTES = 20 * 1024 * 1024

_SECRET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")
#: Cap on Telegram's own `description` in our exception messages (it is short in practice).
_DESCRIPTION_CAP = 300

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_client: Optional[httpx.AsyncClient] = None


# ── Exception hierarchy ────────────────────────────────────────────────


class TelegramException(Exception):
    """Base for Telegram Bot API failures. Carries the METHOD, HTTP status and Telegram's
    description — never the request URL (it contains the bot token)."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        status: Optional[int] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.status = status
        self.description = description


class TelegramRateLimitException(TelegramException):
    """HTTP 429 (flood control). `retry_after` = seconds from `parameters.retry_after`."""

    def __init__(self, message: str, *, method: str, retry_after: Optional[int] = None, **kw: Any) -> None:
        super().__init__(message, method=method, **kw)
        self.retry_after = retry_after


class TelegramUnavailableException(TelegramException):
    """Transient: a 5xx, a timeout, a transport failure or an unreadable answer. Retry later."""


class TelegramRequestError(TelegramException):
    """Telegram refused the request (a 4xx, or `ok: false`): bad chat id, message too long,
    "message is not modified", "query is too old", a blocked bot. Retrying the same request
    will not help."""


class TelegramNotConfiguredException(TelegramException):
    """MARKETING_TELEGRAM_BOT_TOKEN is not set — no request was made."""


# ── client lifecycle ───────────────────────────────────────────────────


class _HttpxTelegramTokenFilter(logging.Filter):
    """Redact the bot token from httpx's own request log line before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if "api.telegram.org" in msg:
                red = redact_secrets(msg)
                if red != msg:
                    record.msg = red
                    record.args = ()
        except Exception:  # a log filter must never break logging
            pass
        return True


def _install_httpx_filter() -> None:
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _HttpxTelegramTokenFilter) for f in target.filters):
        target.addFilter(_HttpxTelegramTokenFilter())


_install_httpx_filter()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _client


async def close_telegram_client() -> None:
    """Tear-down hook for the app.main lifespan."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── helpers ────────────────────────────────────────────────────────────


def is_valid_secret_token(value: Any) -> bool:
    """Does `value` satisfy setWebhook's secret_token rule (1-256 of [A-Za-z0-9_-])?"""
    return isinstance(value, str) and _SECRET_TOKEN_RE.fullmatch(value) is not None


def _scrub(text: Any, token: str) -> str:
    """Upstream-sourced text with the token (and any other secret shape) removed."""
    s = str(text)
    if token:
        s = s.replace(token, "<bot-token>")
    return redact_secrets(s)


def _retry_after(body: Dict[str, Any], headers: httpx.Headers) -> Optional[int]:
    params = body.get("parameters")
    candidates = [params.get("retry_after") if isinstance(params, dict) else None,
                  headers.get("Retry-After")]
    for raw in candidates:
        if isinstance(raw, bool):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return min(value, 86_400)
    return None


#: sendVideo by URL makes Telegram FETCH the MP4 (up to 20 MB) before it answers, so its read
#: timeout is longer than a plain message's: a 15 s timeout on a video Telegram then delivered
#: anyway made every cycle re-send it (review 2026-09-29).
_VIDEO_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


async def _call(method: str, payload: Dict[str, Any], *, timeout: Optional[httpx.Timeout] = None) -> Dict[str, Any]:
    """POST one Bot API method. Returns Telegram's envelope `{"ok": True, "result": …}`."""
    token = settings.MARKETING_TELEGRAM_BOT_TOKEN or ""
    if not token:
        raise TelegramNotConfiguredException(
            f"telegram {method}: MARKETING_TELEGRAM_BOT_TOKEN is not set", method=method,
        )
    try:
        resp = await _get_client().post(f"{API_BASE}/bot{token}/{method}", json=payload,
                                        timeout=timeout or _TIMEOUT)
    except httpx.TimeoutException as e:
        # `from None`: the httpx exception can carry the request (and so the token) — its
        # type name and a scrubbed message are all the diagnosis needs.
        raise TelegramUnavailableException(
            f"telegram {method}: timed out ({type(e).__name__}: {_scrub(e, token)[:200]})",
            method=method,
        ) from None
    except httpx.HTTPError as e:
        raise TelegramUnavailableException(
            f"telegram {method}: transport error ({type(e).__name__}: {_scrub(e, token)[:200]})",
            method=method,
        ) from None

    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None
    envelope: Dict[str, Any] = body if isinstance(body, dict) else {}
    description = _scrub(envelope.get("description") or "", token)[:_DESCRIPTION_CAP] or None

    if status == 429:
        retry_after = _retry_after(envelope, resp.headers)
        raise TelegramRateLimitException(
            f"telegram {method}: HTTP 429 {description or 'Too Many Requests'} "
            f"(retry_after={retry_after})",
            method=method, status=status, description=description, retry_after=retry_after,
        )
    if status >= 500 or status < 200 or 300 <= status < 400:
        raise TelegramUnavailableException(
            f"telegram {method}: HTTP {status} {description or ''}".rstrip(),
            method=method, status=status, description=description,
        )
    if status >= 400:
        raise TelegramRequestError(
            f"telegram {method}: HTTP {status} {description or ''}".rstrip(),
            method=method, status=status, description=description,
        )
    if body is None or not isinstance(body, dict):
        raise TelegramUnavailableException(
            f"telegram {method}: HTTP {status} with an unreadable body", method=method, status=status,
        )
    if envelope.get("ok") is not True:
        raise TelegramRequestError(
            f"telegram {method}: ok=false {description or ''}".rstrip(),
            method=method, status=status, description=description,
        )
    return envelope


# ── Bot API methods ────────────────────────────────────────────────────


async def send_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: Optional[Dict[str, Any]] = None,
    disable_web_page_preview: bool = True,
) -> Dict[str, Any]:
    """sendMessage, PLAIN text (never `parse_mode`). `disable_web_page_preview` maps to the
    current `link_preview_options.is_disabled` (the old field is no longer documented)."""
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if disable_web_page_preview:
        payload["link_preview_options"] = {"is_disabled": True}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _call("sendMessage", payload)


async def send_video(
    chat_id: int,
    video_url: str,
    *,
    caption: Optional[str] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """sendVideo by URL (Telegram fetches it: ≤ 20 MB). Caption is plain text, ≤ 1024."""
    payload: Dict[str, Any] = {"chat_id": chat_id, "video": video_url, "supports_streaming": True}
    if caption:
        payload["caption"] = caption
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _call("sendVideo", payload, timeout=_VIDEO_TIMEOUT)


async def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """editMessageText, plain text. Pass `{"inline_keyboard": []}` to remove the buttons."""
    payload: Dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id, "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _call("editMessageText", payload)


async def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> Dict[str, Any]:
    """answerCallbackQuery — stops the client's progress bar; `text` is a ≤ 200-char toast."""
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:MAX_CALLBACK_ANSWER_CHARS]
    return await _call("answerCallbackQuery", payload)


async def set_webhook(
    url: str,
    secret_token: str,
    *,
    allowed_updates: Sequence[str] = ("callback_query",),
    drop_pending_updates: bool = False,
) -> Dict[str, Any]:
    """setWebhook. Idempotent on Telegram's side (re-setting the same URL is a no-op)."""
    if not is_valid_secret_token(secret_token):
        # Checked here so a malformed secret fails with a clear message and is never sent.
        raise TelegramRequestError(
            "telegram setWebhook: secret_token must be 1-256 characters of [A-Za-z0-9_-]",
            method="setWebhook",
        )
    payload: Dict[str, Any] = {
        "url": url,
        "secret_token": secret_token,
        "allowed_updates": list(allowed_updates),
        "drop_pending_updates": bool(drop_pending_updates),
    }
    return await _call("setWebhook", payload)
