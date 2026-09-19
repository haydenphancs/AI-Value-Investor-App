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

# Match a secret query param and replace its VALUE with `***`.
#
# Anchored on a `?`/`&` OR on the start of the text / a separator: the old `[?&]`-only
# anchor meant a BARE `apikey=K` — which is exactly how sentry-sdk's httpx integration
# records a request's query string (`breadcrumb.data["http.query"]`, no leading `?`) and how
# a `params={'apikey': K}` frame var reprs — passed through untouched, so every Sentry event
# raised after an FMP call carried the production key in its breadcrumbs. The word boundary
# still avoids clobbering `sort_key=`: the name must not be preceded by a letter/underscore.
# The BARE name `key` stays QUERY-anchored only (`?key=` / `&key=` — a Google-style API
# key parameter). Widening it to the prose anchors alongside the others turned every
# operational `key=<value>` — the push dispatcher's dedup keys, the marketing publisher's
# idempotency keys — into `key=***` in exactly the log lines written to find a stranded
# row (W2 regress-B-2).
_SECRET_QS_RE = re.compile(
    r"(?i)(?:(^|[?&\s'\"(,{\[:])((?:api[_-]?key|token|access[_-]?token|secret|password)=)"
    r"|([?&])(key=))"
    r"[^&\s'\"]+"
)


def _qs_sub(m: "re.Match") -> str:
    return (m.group(1) or m.group(3) or "") + (m.group(2) or m.group(4) or "") + "***"
# `'apikey': 'K'` / `"apikey": "K"` — the repr / JSON form a frame's `params` dict takes.
# (`authorization` is left to the Bearer / JWT patterns so `Bearer ***` stays legible.)
_SECRET_KV_RE = re.compile(
    r"""(?i)((['"])(?:api[_-]?key|token|access[_-]?token|secret|password)\2"""
    r"""\s*[:=]\s*(['"]))[^'"]+(['"])"""
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
        s = _SECRET_QS_RE.sub(_qs_sub, s)
        s = _SECRET_KV_RE.sub(r"\1***\4", s)
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
                if not isinstance(b, dict):
                    continue
                if isinstance(b.get("message"), str):
                    b["message"] = redact_secrets(b["message"])
                data = b.get("data")
                if isinstance(data, dict):
                    # The httpx integration records `http.query` on every outbound call —
                    # for FMP that is `symbol=AAPL&apikey=<key>`, no leading `?`. It has
                    # no triage value (`url` keeps host + path); drop it outright, then
                    # redact whatever strings remain.
                    for k in ("http.query", "http.fragment"):
                        data.pop(k, None)
                    _redact_strings_in_place(data)

        # Frame locals: on an FMP HTTPStatusError the failing frame holds
        # `e=HTTPStatusError("... apikey=<key>")` and `params={'apikey': '<key>'}`. By the
        # time before_send runs the event is serialised, so these are strings or
        # dict/list trees of repr strings — walk them all.
        exc = event.get("exception")
        if isinstance(exc, dict):
            for val in exc.get("values") or []:
                st = (val or {}).get("stacktrace") if isinstance(val, dict) else None
                _scrub_frames(st)
        _scrub_frames(event.get("stacktrace"))
        for th in ((event.get("threads") or {}).get("values") or []):
            if isinstance(th, dict):
                _scrub_frames(th.get("stacktrace"))
        extra = event.get("extra")
        if isinstance(extra, dict):
            _redact_strings_in_place(extra)

        _scrub_request_body(event)
        _scrub_request_headers(event)
    except Exception:
        pass
    return event


def _scrub_frames(stacktrace: Any) -> None:
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or []:
        if isinstance(frame, dict) and isinstance(frame.get("vars"), dict):
            _redact_strings_in_place(frame["vars"])


def _is_credential_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return low in _CREDENTIAL_KEYS or any(s in low for s in _CREDENTIAL_SUBSTRINGS)


def _redact_strings_in_place(node: Any, _depth: int = 0) -> None:
    """Apply `redact_secrets` to every str leaf of a dict/list tree, in place.

    KEY-aware as well as value-aware: a serialised frame local `params={'apikey': K}`
    arrives as a dict whose key is `apikey` and whose value is the bare repr `'K'` — no
    `apikey=` prefix for the regex to anchor on — so a credential-named key blanks its value.
    """
    if _depth > 12:
        return
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if _is_credential_key(k) and not isinstance(v, (dict, list)):
                node[k] = "[redacted]"
            elif isinstance(v, str):
                node[k] = redact_secrets(v)
            elif isinstance(v, (dict, list)):
                _redact_strings_in_place(v, _depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = redact_secrets(v)
            elif isinstance(v, (dict, list)):
                _redact_strings_in_place(v, _depth + 1)


# Request headers that carry a credential. sentry-sdk's own SENSITIVE_HEADERS list covers
# Authorization / Cookie / X-Api-Key / X-Forwarded-For / X-Real-IP and nothing else, so a
# custom header — the marketing worker's shared secret, the admin token, the widget token if
# it ever travels as a header — would be stored in the clear on every request-scoped
# `logger.error`. Exact names first, then a substring pass for anything token-shaped.
_CREDENTIAL_HEADERS = {
    "x-marketing-worker-token", "x-admin-token", "authorization", "apikey", "x-api-key",
    "x-guest-id", "cookie", "set-cookie",
}
_CREDENTIAL_HEADER_FRAGMENTS = ("token", "secret", "apikey", "api-key", "api_key", "password")


def _scrub_request_headers(event: dict) -> None:
    req = event.get("request")
    if not isinstance(req, dict):
        return
    headers = req.get("headers")
    if not isinstance(headers, dict):
        return
    for key in list(headers):
        k = str(key).lower()
        if k in _CREDENTIAL_HEADERS or any(f in k for f in _CREDENTIAL_HEADER_FRAGMENTS):
            headers[key] = "[redacted]"


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
            # The TRACEBACK too. `Formatter.format` renders `exc_info` into `exc_text` only
            # if nothing pre-set it, so a filter that touched the message alone left every
            # `logger.error(..., exc_info=True)` site — the stock endpoints, the chat widget
            # fetcher, the global 500 handler — printing the raw httpx line, FMP key and all,
            # into the Railway log. `formatException` walks `__cause__` / `__context__`, so a
            # `raise Typed(...) from e` chain is covered as well.
            if record.exc_info and not record.exc_text:
                record.exc_text = redact_secrets(
                    logging.Formatter().formatException(record.exc_info)
                )
        except Exception:
            pass
        return True
