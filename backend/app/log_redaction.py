"""
Secret redaction for logs + Sentry events.

FMP (and some other integrations) put the API key in the request query string
(`?...&apikey=<key>`), which httpx echoes verbatim in its HTTPStatusError message.
That message then flows into logger.error → Sentry → the Discord digest, leaking the
key. These helpers scrub secret-looking query params to `***` at those exits.
CLAUDE.md rule: never log secrets.
"""

import logging
import re
from typing import Any

# Match a `?`/`&`-prefixed secret query param and replace its VALUE with `***`.
# The leading `[?&]` anchor avoids clobbering unrelated words (e.g. `sort_key=`).
_SECRET_QS_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret|password|key)=)[^&\s'\"]+"
)


def redact_secrets(text: Any) -> str:
    """Return ``str(text)`` with secret-looking query values (e.g. ``apikey=...``) → ``***``."""
    try:
        return _SECRET_QS_RE.sub(r"\1***", str(text))
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
    except Exception:
        pass
    return event


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
