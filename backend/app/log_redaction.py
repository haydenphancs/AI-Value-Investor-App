"""
Secret + PII redaction for logs and Sentry events.

Two separate concerns:

1. SECRETS. FMP (and some other integrations) put the API key in the request query string
   (`?...&apikey=<key>`), which httpx echoes verbatim in its HTTPStatusError message. That
   message flows into logger.error → Sentry → the Discord digest, leaking the key.
   CLAUDE.md rule: never log secrets.

2. PII. Sentry is a third-party processor, so anything reaching it must be disclosed. The
   pattern set below scrubs values that have NO diagnostic value: email addresses and
   bearer/JWT tokens.

   `user_id` UUIDs are deliberately NOT redacted. They are pseudonymous, they are the
   primary handle for diagnosing a report from logs alone, and CLAUDE.md explicitly
   requires errors to carry `user_id` / `report_id`. Stripping them would trade real
   debuggability for no privacy gain, since Sentry access is already restricted. The
   correct treatment is DISCLOSURE — the privacy policy names Sentry as a recipient of
   diagnostic data including a pseudonymous account identifier — not deletion.
"""

import logging
import re
from typing import Any

# Match a `?`/`&`-prefixed secret query param and replace its VALUE with `***`.
# The leading `[?&]` anchor avoids clobbering unrelated words (e.g. `sort_key=`).
_SECRET_QS_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret|password|key)=)[^&\s'\"]+"
)

# Email addresses. Deliberately conservative so it can't eat surrounding log structure.
_EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b"
)

# `Bearer <jwt>` / bare three-segment JWTs. Access tokens can appear in a logged header
# dict or an httpx request repr.
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{20,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")

# Supabase / Postgres connection strings, which carry the password inline.
_DSN_RE = re.compile(r"(?i)\b(postgres(?:ql)?://[^:@\s]+:)[^@\s]+(@)")


def redact_secrets(text: Any) -> str:
    """Return ``str(text)`` with secrets and no-diagnostic-value PII replaced by ``***``.

    Covers: secret query params (``apikey=``…), email addresses, bearer tokens, bare JWTs,
    and inline Postgres DSN passwords. Does NOT touch `user_id` UUIDs — see the module
    docstring for why.
    """
    try:
        s = str(text)
        s = _SECRET_QS_RE.sub(r"\1***", s)
        s = _DSN_RE.sub(r"\1***\2", s)
        s = _BEARER_RE.sub(r"\1***", s)
        s = _JWT_RE.sub("***", s)
        s = _EMAIL_RE.sub("***@***", s)
        return s
    except Exception:
        return str(text)


def scrub_sentry_event(event: dict, _hint: Any = None) -> dict:
    """In-place redact secrets from a Sentry event before it is stored/sent.

    Covers the message, the log entry, exception values, and breadcrumb messages —
    every place an httpx URL (with ``apikey=``) can surface. Belt-and-suspenders so no
    integration can ever ship a key into Sentry (and onward to the Discord digest).
    """
    try:
        if isinstance(event.get("message"), str):
            event["message"] = redact_secrets(event["message"])

        le = event.get("logentry")
        if isinstance(le, dict):
            if isinstance(le.get("message"), str):
                le["message"] = redact_secrets(le["message"])
            if isinstance(le.get("formatted"), str):
                le["formatted"] = redact_secrets(le["formatted"])

        exc = event.get("exception")
        if isinstance(exc, dict):
            for val in exc.get("values") or []:
                if isinstance(val, dict) and isinstance(val.get("value"), str):
                    val["value"] = redact_secrets(val["value"])

        bc = event.get("breadcrumbs")
        if isinstance(bc, dict):
            for b in bc.get("values") or []:
                if isinstance(b, dict) and isinstance(b.get("message"), str):
                    b["message"] = redact_secrets(b["message"])

        _scrub_request_body(event)
    except Exception:
        pass
    return event


# Body keys that must never reach Sentry in the clear. Matched case-insensitively against
# the whole key, plus a substring pass for the compound spellings (new_password, id_token…).
_CREDENTIAL_KEYS = {
    "password", "new_password", "current_password", "old_password", "confirm_password",
    "token", "access_token", "refresh_token", "id_token", "identity_token",
    "code", "otp", "secret", "api_key", "apikey", "authorization", "nonce",
    "supabase_access_token", "signed_transaction", "signed_payload",
}
_CREDENTIAL_SUBSTRINGS = ("password", "token", "secret", "apikey", "api_key")


def _scrub_request_body(event: dict) -> None:
    """Redact credential fields in ``event["request"]["data"]``.

    `send_default_pii=False` does NOT cover request bodies — it gates cookies only — and
    sentry-sdk's Starlette integration attaches the parsed JSON body to every event. A failed
    sign-up or a mistyped password-reset code is logged with ``logger.error(exc_info=True)``,
    LoggingIntegration turns that into an event, and the body rides along: plaintext password,
    or the 6-digit reset code.

    `max_request_body_size="never"` in `main.py` is the primary control and suppresses the
    body outright. This is the second layer, because that is a single option one edit away
    from being flipped back, and the consequence is credentials sitting in a third-party
    store. Keys are redacted rather than the whole body dropped so a genuine 400 is still
    diagnosable (which field, which shape) without exposing the value.
    """
    req = event.get("request")
    if not isinstance(req, dict):
        return
    data = req.get("data")
    if isinstance(data, str):
        req["data"] = redact_secrets(data)
        return
    if not isinstance(data, dict):
        return  # AnnotatedValue (already suppressed), list, or absent — nothing to do.

    for key in list(data.keys()):
        if not isinstance(key, str):
            continue
        low = key.lower()
        if low in _CREDENTIAL_KEYS or any(s in low for s in _CREDENTIAL_SUBSTRINGS):
            data[key] = "[redacted]"
        elif isinstance(data[key], str):
            data[key] = redact_secrets(data[key])


class SecretRedactingFilter(logging.Filter):
    """Scrub secrets from EVERY log record's final message.

    Attach to the root logger's handlers (in main.py) so any module's log line — e.g.
    the FMP per-symbol warnings that echo the request URL with ``apikey=`` — is redacted
    on the way to stdout (Railway logs), not just the errors Sentry captures.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = redact_secrets(msg)
            if red != msg:
                record.msg = red
                record.args = ()
        except Exception:
            pass
        return True
