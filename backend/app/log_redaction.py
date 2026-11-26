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

Where it is attached (main.py): `SecretRedactingFilter` on the ROOT handlers; uvicorn's own
loggers bypass those (`propagate: False`), so `install_uvicorn_log_redaction` gives the access
log `AccessLogRedactingFilter` and uvicorn's server log `SecretRedactingFilter`; the Sentry
side is `scrub_sentry_event` / `scrub_sentry_transaction` plus the SDK's `EventScrubber`.
"""

import logging
import re
from typing import Any
from urllib.parse import unquote_plus

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
# `token_hash` (2026-11-26, BE-AUTH-01): the Confirm-signup email links to
# `/auth/confirm?token_hash=<hex>&type=signup`, and that hash is a LIVE single-use credential
# (GoTrue's public `/verify` turns it into a session). `token=` alone never matched it: the
# name runs on into `_hash`. `refresh_token=` is a credential for the same reason and was
# missed the same way (`_` is not an anchor, so `token=` inside it never matched either).
_SECRET_QS_RE = re.compile(
    r"(?i)(?:(^|[?&\s'\"(,{\[:])"
    r"((?:api[_-]?key|token(?:_hash)?|(?:access|refresh)[_-]?token|secret|password)=)"
    r"|([?&])(key=))"
    r"[^&\s'\"]+"
)


def _qs_sub(m: "re.Match") -> str:
    return (m.group(1) or m.group(3) or "") + (m.group(2) or m.group(4) or "") + "***"
# `'apikey': 'K'` / `"apikey": "K"` — the repr / JSON form a frame's `params` dict takes.
# (`authorization` is left to the Bearer / JWT patterns so `Bearer ***` stays legible.)
_SECRET_KV_RE = re.compile(
    r"""(?i)((['"])(?:api[_-]?key|token(?:_hash)?|(?:access|refresh)[_-]?token|secret|password)\2"""
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

# Telegram bot tokens (`<bot id>:<35 chars of [A-Za-z0-9_-]>`). The Bot API puts the token in
# the URL PATH — `https://api.telegram.org/bot<token>/sendMessage` — which httpx logs at INFO and
# can echo in an exception, so no query-param pattern above ever sees it. The bot id is public
# (it is the bot's user id) and kept for diagnosis; the secret half is replaced. The digit run
# must not be preceded by another digit (so it cannot start mid-number); `bot<id>:` in a URL
# matches because the id follows a letter. 30+ rather than exactly 35 so a longer token format
# is still covered.
_TELEGRAM_TOKEN_RE = re.compile(r"(?<!\d)(\d{5,16}):[A-Za-z0-9_-]{30,}")

# The marketing publisher's platform credentials (Phase 5, design doc §12.10). None of them is in
# a URL, but a header or a request body can still reach a log line or a frame variable:
#  * X OAuth 1.0a — `Authorization: OAuth oauth_consumer_key="…", oauth_token="…",
#    oauth_signature="…"` (and the same names as form/query parameters). `token=` above never
#    matches `oauth_token=` (it is anchored on a separator, and `_` is not one).
#  * Upload-Post — `Authorization: Apikey <key>` (a space, not `=`).
#  * Bluesky app passwords — `xxxx-xxxx-xxxx-xxxx` of [a-z0-9]. Bounded on both sides by anything
#    but a letter, digit or hyphen, so a UUID (8-4-4-4-12), a date or an idempotency key
#    (`2026-09-29:x:text`) never matches.
_OAUTH_PARAM_RE = re.compile(
    r"(?i)\b(oauth_(?:token|token_secret|signature|consumer_key|consumer_secret|nonce|verifier)"
    r"\s*=\s*\"?)[^\"&,\s]+"
)
_APIKEY_HEADER_RE = re.compile(r"(?i)\b(apikey\s+)[A-Za-z0-9._\-]{8,}")
_APP_PASSWORD_RE = re.compile(r"(?<![A-Za-z0-9-])[a-z0-9]{4}(?:-[a-z0-9]{4}){3}(?![A-Za-z0-9-])")

# Anthropic API keys (`sk-ant-api03-…`, admin `sk-ant-admin01-…`; 2026-10-30, the Claude second
# provider). The SDK sends the key in a header, never a URL, but a key pasted into the wrong
# variable can be echoed by a settings check, and a bare key matches no pattern above.
_ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}")


def redact_secrets(text: Any) -> str:
    """Return ``str(text)`` with secrets and no-diagnostic-value PII replaced by ``***``.

    Covers: secret query params (``apikey=``…), email addresses, bearer tokens, bare JWTs,
    inline Postgres DSN passwords, Telegram bot tokens (in a URL path or bare), OAuth 1.0a
    parameters, ``Apikey <key>`` headers, Bluesky app passwords and Anthropic ``sk-ant-`` keys. Does NOT touch `user_id` UUIDs — see the module
    docstring for why.
    """
    try:
        s = str(text)
        s = _TELEGRAM_TOKEN_RE.sub(r"\1:***", s)
        s = _OAUTH_PARAM_RE.sub(r"\1***", s)
        s = _APIKEY_HEADER_RE.sub(r"\1***", s)
        s = _APP_PASSWORD_RE.sub("***", s)
        s = _ANTHROPIC_KEY_RE.sub("sk-ant-***", s)
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
            # LoggingIntegration sends the UNFORMATTED template plus `params` = `record.args`.
            # A record that reached no root handler (so `SecretRedactingFilter` never folded
            # its args into the message) carries the raw values here.
            if isinstance(le.get("params"), list):
                _redact_strings_in_place(le["params"])
            elif isinstance(le.get("params"), tuple):
                params = list(le["params"])
                _redact_strings_in_place(params)
                le["params"] = params

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
        _scrub_request_target(event)
    except Exception:
        pass
    return event


def scrub_sentry_transaction(event: dict, _hint: Any = None) -> dict:
    """In-place redact secrets from a Sentry TRANSACTION before it is sent.

    `before_send` never runs on transactions, and the httpx integration records every outbound
    call as a span: its `description` is `POST https://api.telegram.org/bot<TOKEN>/sendMessage`
    and its `data` carries `http.query` (for FMP, `symbol=AAPL&apikey=<key>`). With tracing off
    (SENTRY_TRACES_SAMPLE_RATE=0, the default) no transaction is sent; the day someone raises it
    to look into latency, this is what keeps the bot token and the FMP key out of Sentry
    (review 2026-09-29)."""
    try:
        scrub_sentry_event(event, _hint)
        if isinstance(event.get("transaction"), str):
            event["transaction"] = redact_secrets(event["transaction"])
        trace = ((event.get("contexts") or {}).get("trace")) if isinstance(event.get("contexts"), dict) else None
        spans = list(event.get("spans") or [])
        if isinstance(trace, dict):
            spans.append(trace)
        for span in spans:
            if not isinstance(span, dict):
                continue
            if isinstance(span.get("description"), str):
                span["description"] = redact_secrets(span["description"])
            data = span.get("data")
            if isinstance(data, dict):
                for k in ("http.query", "http.fragment"):
                    data.pop(k, None)
                _redact_strings_in_place(data)
            tags = span.get("tags")
            if isinstance(tags, dict):
                _redact_strings_in_place(tags)
        # Also done inside `scrub_sentry_event`; repeated (idempotent) so the request target is
        # scrubbed even if an earlier step there gave up on a malformed event.
        _scrub_request_target(event)
    except Exception:
        pass
    return event


# WEB-SEARCH RESULTS never reach Sentry through a frame's locals (review 2026-10-29). Brave's
# terms allow its results transient use only, and the Updates Insights card is the one place they
# may be kept (owner decision 2026-10-28) — sentry-sdk captures every frame's locals by default.
# A frame in one of these modules holds queries and results, so it keeps its function and line
# but no locals:
_WEB_RESULT_MODULES = frozenset({
    "app.integrations.brave_search",
    "app.services.chat_web_search_service",
    "app.services.jump_web_search_service",
    "app.services.web_result_hygiene",
})
# On the Insights path, only a frame that HOLDS the jump search's pages (one of these locals, not
# empty) loses its locals; the card's other failures keep theirs for triage.
_WEB_PAGE_MODULES = frozenset({
    "app.services.news_insight_service",
    "app.services.updates_insight_sweeper",
})
_WEB_PAGE_VARS = frozenset({"web_context", "web", "pages", "web_block", "web_rows"})
_EMPTY_REPRS = frozenset({"", "None", "()", "[]", "{}", "''", '""', "set()", "frozenset()"})
_WEB_VARS_DROPPED = {"web_search": "[locals dropped: web search results are transient only]"}
# ...and, under ANY local name, a frame holding a value only web results have: a stored card
# source marked `origin: "web"` (`news_insight_service._store`'s `sources`), a chat source pill
# of kind "web" (`chat._persist_turn`'s message) or a `WebPage` repr (review 2026-10-29, round 2).
_WEB_VALUE_MODULES = _WEB_PAGE_MODULES | frozenset({
    "app.api.v1.endpoints.chat",
    "app.services.chat_service",
})
_WEB_VALUE_REPRS = ("WebPage(", "'origin': 'web'", '"origin": "web"', "'kind': 'web'", '"kind": "web"')


# CREDENTIALS never reach Sentry through an auth handler's locals (SEC-1, 2026-11-26). Every
# handler frame in this module holds its request model — `request=SignUpRequest(email=…,
# password='…')` — and sentry-sdk serialises a model it does not know as its REPR STRING under
# the local's name. `request` is not a credential key, and the value regexes above never matched
# the repr's `password='…'` (a quote right after `=`, and `new_password=` has no separator
# before `password`). So any `logger.error(..., exc_info=True)` in a handler — a GoTrue 429 on
# sign-in, an email-quota error on sign-up, a 5xx on change-password — stored the user's
# plaintext password (and the reset code) in Sentry. These frames keep function and line, not
# locals; the exception value and the log line still carry the diagnosis.
# `app.api.email_confirm_page` (TOKSEC-1): its POST handler holds the email link's unredeemed
# `token_hash` in four shapes — the bare local, the raw form `body`, its decoded `text` and the
# `parse_qs` dict of LISTS (`{'token_hash': ['<hex>']}`) — and a single-use confirmation
# credential in Sentry is as bad as a password there.
_CREDENTIAL_FRAME_MODULES = frozenset({
    "app.api.v1.endpoints.auth",
    "app.api.email_confirm_page",
})
_CREDENTIAL_VARS_DROPPED = {
    "auth_handler": "[locals dropped: auth handler frames hold passwords, codes and tokens]",
}

# ...and in EVERY other frame, a credential-named field inside a serialised repr loses its value.
# The handler is not the only frame holding the model: an exception that escapes it carries
# FastAPI's `run_endpoint_function` frame, whose `values` local is `{'request': SignUpRequest(…)}`.
# FRAME-SCOPED on purpose — never applied to log lines, so an operational `code='otp_expired'`
# in a message keeps its value. Field names are whole snake_case segments: `new_password`,
# `id_token`, `token_hash`, `supabase_access_token`, `device_token`, `nonce`, a bare `code` —
# never `error_code` / `status_code` (a `code` segment counts only as the WHOLE name). Both the
# `name='v'` (model repr) and `'name': 'v'` (dict repr) shapes; a value Sentry truncated
# (no closing quote) is redacted to the end of the string.
#
# The regexes only find `<identifier>=<quoted>` / `'<identifier>': <quoted>`; WHICH identifiers
# are secret is decided in Python (`_is_secret_field_name`), so the patterns stay linear (one
# start per identifier, no nested quantifier over the name).
_SECRET_NAME_SEGMENTS = frozenset({
    "password", "passwd", "passcode", "token", "tokens", "secret", "nonce", "otp", "jwt",
})
_QUOTED_VALUE = r"""('(?:[^'\\]|\\.)*(?:'|$)|"(?:[^"\\]|\\.)*(?:"|$))"""
_FRAME_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)=" + _QUOTED_VALUE
)
_FRAME_DICT_ITEM_RE = re.compile(
    r"""(['"])([A-Za-z_][A-Za-z0-9_]*)\1(\s*:\s*)""" + _QUOTED_VALUE
)
_REDACTED_VALUE = "'[redacted]'"


def _is_secret_field_name(name: str) -> bool:
    low = name.lower()
    return low == "code" or any(seg in _SECRET_NAME_SEGMENTS for seg in low.split("_"))


def _redact_secret_fields(text: str, _depth: int = 0) -> str:
    """Blank the value of every credential-named field in a repr string.

    A non-secret field's quoted value is scanned again (bounded depth): `re.sub` never revisits
    a span it consumed, so `note="... password='p' ..."` would otherwise hide the inner field.
    """
    if _depth > 4:
        return text
    try:
        text = _FRAME_FIELD_RE.sub(
            lambda m: f"{m.group(1)}={_REDACTED_VALUE}" if _is_secret_field_name(m.group(1))
            else f"{m.group(1)}={_redact_secret_fields(m.group(2), _depth + 1)}",
            text,
        )
        return _FRAME_DICT_ITEM_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}"
            + (_REDACTED_VALUE if _is_secret_field_name(m.group(2))
               else _redact_secret_fields(m.group(4), _depth + 1)),
            text,
        )
    except Exception:
        return text


def _redact_secret_fields_in_place(node: Any, _depth: int = 0) -> None:
    if _depth > 12:
        return
    items = (
        list(node.items()) if isinstance(node, dict)
        else list(enumerate(node)) if isinstance(node, list)
        else []
    )
    for k, v in items:
        if isinstance(v, str):
            node[k] = _redact_secret_fields(v)
        elif isinstance(v, (dict, list)):
            _redact_secret_fields_in_place(v, _depth + 1)


def _frame_module(frame: dict) -> str:
    module = frame.get("module")
    if isinstance(module, str) and module:
        return module
    path = frame.get("abs_path") or frame.get("filename") or ""
    if not isinstance(path, str) or not path.endswith(".py"):
        return ""
    parts = path[:-3].replace("\\", "/").split("/")
    if "app" not in parts:
        return ""
    # The LAST "app": a container's working directory is often called app too ("/app/app/…").
    start = len(parts) - 1 - parts[::-1].index("app")
    return ".".join(parts[start:])


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, str):
        return value.strip() not in _EMPTY_REPRS
    return True


def _holds_web_value(node: Any, depth: int = 0) -> bool:
    """Whether a serialised local carries a web result (see `_WEB_VALUE_MODULES`). Sentry
    serialises nested values as dicts/lists of repr strings, so both shapes are read."""
    if depth > 12:
        return False
    if isinstance(node, dict):
        for key in ("origin", "kind"):
            value = node.get(key)
            if isinstance(value, str) and value.strip().strip("'\"") == "web":
                return True
        return any(_holds_web_value(v, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_holds_web_value(v, depth + 1) for v in node)
    return isinstance(node, str) and any(r in node for r in _WEB_VALUE_REPRS)


def _holds_web_results(frame: dict) -> bool:
    module = _frame_module(frame)
    if module in _WEB_RESULT_MODULES:
        return True
    local_vars = frame.get("vars") or {}
    if module in _WEB_PAGE_MODULES and any(
        _has_value(local_vars.get(name)) for name in _WEB_PAGE_VARS
    ):
        return True
    return module in _WEB_VALUE_MODULES and _holds_web_value(local_vars)


def _scrub_frames(stacktrace: Any) -> None:
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or []:
        if not isinstance(frame, dict) or not isinstance(frame.get("vars"), dict):
            continue
        if _frame_module(frame) in _CREDENTIAL_FRAME_MODULES:
            frame["vars"] = dict(_CREDENTIAL_VARS_DROPPED)
            continue
        if _holds_web_results(frame):
            frame["vars"] = dict(_WEB_VARS_DROPPED)
            continue
        _redact_strings_in_place(frame["vars"])
        _redact_secret_fields_in_place(frame["vars"])


def _is_credential_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return (low in _CREDENTIAL_KEYS or low in _PII_KEYS
            or any(s in low for s in _CREDENTIAL_SUBSTRINGS))


# The END USER'S ADDRESS, which sentry-sdk keeps out only under the names it knows
# (`DEFAULT_PII_DENYLIST`: x_forwarded_for, x_real_ip, ip_address, remote_addr — whole keys).
# TOKSEC-2: `database.run_gotrue` holds it as `client_ip` and writes it into GoTrue's shared
# headers as `Sb-Forwarded-For`; the SDK's `_request` frame copies that dict into its own
# `headers` local, so every GoTrue failure logged with exc_info carried the caller's IP.
# Mirrored in `SENTRY_PII_DENYLIST_EXTRA` for the SDK's own scrubber (main.py).
_PII_KEYS = frozenset({"sb-forwarded-for", "client_ip"})
SENTRY_PII_DENYLIST_EXTRA = ("sb-forwarded-for", "client_ip")


def sentry_pii_denylist() -> list:
    """The `pii_denylist` for main.py's `EventScrubber`: sentry-sdk's DEFAULT list PLUS ours.

    Passing `pii_denylist=` REPLACES the SDK's default list (scrubber.py: `DEFAULT_PII_DENYLIST
    if pii_denylist is None else pii_denylist`), so the defaults are copied in, never dropped.
    """
    from sentry_sdk.scrubber import DEFAULT_PII_DENYLIST

    return list(DEFAULT_PII_DENYLIST) + [
        k for k in SENTRY_PII_DENYLIST_EXTRA if k not in DEFAULT_PII_DENYLIST
    ]


def _blanked(node: Any, _depth: int = 0) -> Any:
    """`node` with every scalar leaf but None / a bool replaced by `[redacted]` (a copy for a
    tuple, in place for a list / dict). Used under a credential-named KEY whose value is a
    sequence — `parse_qs`'s `{'token_hash': ['<hex>']}` — where no leaf has a name of its own
    to be recognised by. Numbers go too: an OTP held as an int is still the OTP."""
    if _depth > 12:
        return "[redacted]"
    if isinstance(node, dict):
        for k, v in list(node.items()):
            node[k] = _blanked(v, _depth + 1)
        return node
    if isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _blanked(v, _depth + 1)
        return node
    if isinstance(node, tuple):
        return tuple(_blanked(v, _depth + 1) for v in node)
    if node is None or isinstance(node, bool):
        return node
    return "[redacted]"


def _redact_strings_in_place(node: Any, _depth: int = 0) -> None:
    """Apply `redact_secrets` to every str leaf of a dict/list tree, in place.

    KEY-aware as well as value-aware: a serialised frame local `params={'apikey': K}`
    arrives as a dict whose key is `apikey` and whose value is the bare repr `'K'` — no
    `apikey=` prefix for the regex to anchor on — so a credential-named key blanks its value.
    A LIST / tuple under such a key loses every leaf (the `parse_qs` shape, TOKSEC-1); a DICT
    under one is walked key by key as before.
    """
    if _depth > 12:
        return
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if _is_credential_key(k) and isinstance(v, (list, tuple)):
                node[k] = _blanked(v)
            elif _is_credential_key(k) and not isinstance(v, dict):
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
# `jwt`: Bluesky's session fields are `accessJwt` / `refreshJwt` (a frame local holding the
# session dict must not carry them in the clear, even though the JWT pattern catches the value).
_CREDENTIAL_SUBSTRINGS = ("password", "token", "secret", "apikey", "api_key", "jwt")


def _scrub_request_body(event: dict) -> None:
    """Redact credential fields in ``event["request"]["data"]``.

    `send_default_pii=False` does NOT cover request bodies — it gates cookies only — and
    sentry-sdk's Starlette integration attaches the parsed JSON body to every event. A failed
    sign-up or a mistyped password-reset code is logged with ``logger.error(exc_info=True)``,
    LoggingIntegration turns that into an event, and the body rides along: plaintext password,
    or the emailed reset code.

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


# ── Request targets: the access log and Sentry's `request.url` / `query_string` ─────────────
#
# A query parameter whose NAME says credential loses its VALUE. Decided per `name=value` pair
# (never by a regex over the whole line), so the rule can be broader than `_SECRET_QS_RE`'s:
# a bare `code` / `key` is blanked here, where it can only be a request's own parameter, while
# an operational `code='otp_expired'` in a log MESSAGE keeps its value. The name is
# percent-decoded first (`token%5Fhash`), and `-` / `.` split segments like `_` does.
_QUERY_SECRET_NAMES = frozenset({
    "apikey", "api_key", "key", "code", "signature", "sig", "auth", "authorization",
    "credential", "credentials", "session", "sessionid",
})


def _is_secret_query_name(name: str) -> bool:
    low = unquote_plus(name).strip().lower()
    if low in _QUERY_SECRET_NAMES:
        return True
    return _is_secret_field_name(low.replace("-", "_").replace(".", "_"))


def redact_query_string(query: Any) -> str:
    """`query` (``a=1&token_hash=…``, no leading ``?``) with every secret parameter's value
    replaced by ``***``, then `redact_secrets` (emails, JWTs) over the result. Fails CLOSED:
    on any error the whole query becomes ``***``."""
    try:
        text = query if isinstance(query, str) else str(query)
        pairs = []
        for pair in text.split("&"):
            name, eq, value = pair.partition("=")
            pairs.append(f"{name}=***" if eq and value and _is_secret_query_name(name) else pair)
        return redact_secrets("&".join(pairs))
    except Exception:
        return "***"


def redact_request_target(target: Any) -> str:
    """A request target — ``/path?query`` or a full URL — with its secret query values gone.

    The access log's full path is the one place a request's query string is written verbatim:
    the Confirm-signup email links to ``/auth/confirm?token_hash=<hex>&type=signup``, and that
    hash is a live single-use credential (BE-AUTH-01). Fails CLOSED: on any error only the part
    before ``?`` survives."""
    try:
        text = target if isinstance(target, str) else str(target)
        head, sep, query = text.partition("?")
        if not sep:
            return redact_secrets(text)
        return redact_secrets(head) + "?" + redact_query_string(query)
    except Exception:
        try:
            return str(target).split("?", 1)[0] + "?***"
        except Exception:
            return "***"


def _scrub_request_target(event: dict) -> None:
    """`event["request"]["url"]` / `["query_string"]`: sentry-sdk's ASGI integration attaches
    the query string to EVERY event raised inside a request, `send_default_pii` or not, so an
    error during ``GET /auth/confirm?token_hash=…`` carried the hash."""
    try:
        req = event.get("request")
        if not isinstance(req, dict):
            return
        if isinstance(req.get("url"), str):
            req["url"] = redact_request_target(req["url"])
        if isinstance(req.get("query_string"), str):
            req["query_string"] = redact_query_string(req["query_string"])
    except Exception:
        logging.getLogger(__name__).warning("log redaction: the Sentry request target was not scrubbed")


#: uvicorn's access record: ``'%s - "%s %s HTTP/%s" %d'`` with args
#: (client_addr, method, full_path, http_version, status_code) — h11_impl / httptools_impl.
_ACCESS_ARGS_LEN = 5
_ACCESS_PATH_INDEX = 2


class AccessLogRedactingFilter(logging.Filter):
    """Redact secret query values from uvicorn's ACCESS log line (BE-AUTH-01 / DOC-5).

    `uvicorn.access` has its OWN handler with ``propagate: False`` (uvicorn's LOGGING_CONFIG), so
    the root handlers' `SecretRedactingFilter` never saw it, and every open of the email link —
    a person's, or a mail scanner's prefetch — wrote ``GET /auth/confirm?token_hash=<hex>`` to
    the Railway log while the hash could still be redeemed.

    It rewrites ONLY ``record.args[2]`` (the full path with its query) and keeps the 5-tuple:
    uvicorn's `AccessFormatter.formatMessage` unpacks exactly five args, so the generic filter's
    ``record.args = ()`` raised ValueError in the handler — the line was lost and a "--- Logging
    error ---" traceback printed instead. A record of any other shape keeps its args; only an
    args-free message is redacted in place. Never raises: a broken filter must not drop logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, (tuple, list)) and len(args) == _ACCESS_ARGS_LEN:
                target = args[_ACCESS_PATH_INDEX]
                red = redact_request_target(target)
                if red != target:
                    args = tuple(args)
                    record.args = (
                        args[:_ACCESS_PATH_INDEX] + (red,) + args[_ACCESS_PATH_INDEX + 1:]
                    )
            elif not args and isinstance(record.msg, str):
                record.msg = redact_secrets(record.msg)
        except Exception:
            pass
        return True


_UVICORN_ACCESS_LOGGER = "uvicorn.access"
_UVICORN_SERVER_LOGGERS = ("uvicorn", "uvicorn.error")


def _add_filter_once(target: Any, filter_cls: type) -> bool:
    if any(isinstance(f, filter_cls) for f in target.filters):
        return False
    target.addFilter(filter_cls())
    return True


def install_uvicorn_log_redaction() -> None:
    """Attach redaction to uvicorn's own loggers, which bypass the root handlers. Idempotent.

    * `uvicorn.access` — `AccessLogRedactingFilter` on the LOGGER (it runs before every handler
      and before Sentry's breadcrumb hook, and survives uvicorn's ``dictConfig``, which replaces
      handlers but never removes a logger's filters) and on each of its handlers (records a
      child logger propagates to them skip the logger's own filters).
    * `uvicorn` / `uvicorn.error` — `SecretRedactingFilter` on their handlers (uvicorn's
      "default" handler, ``propagate: False``: its "Exception in ASGI application" tracebacks
      never reached the root filter either). Never on a handler that formats access records.

    Call it at import (main.py — the CLI configures logging BEFORE importing the app, the
    Dockerfile CMD and the Procfile alike) and again at lifespan start (a server configured after
    the import replaced the handlers). Never raises; a failure is logged.
    """
    try:
        access = logging.getLogger(_UVICORN_ACCESS_LOGGER)
        _add_filter_once(access, AccessLogRedactingFilter)
        access_handlers = list(access.handlers)
        for handler in access_handlers:
            _add_filter_once(handler, AccessLogRedactingFilter)
        for name in _UVICORN_SERVER_LOGGERS:
            for handler in logging.getLogger(name).handlers:
                if handler in access_handlers or type(handler.formatter).__name__ == "AccessFormatter":
                    _add_filter_once(handler, AccessLogRedactingFilter)
                    continue
                _add_filter_once(handler, SecretRedactingFilter)
    except Exception as e:
        logging.getLogger(__name__).warning(
            "log redaction: could not attach the uvicorn log filters (%s: %s) — the access "
            "log may carry secret query values", type(e).__name__, e,
        )


class SecretRedactingFilter(logging.Filter):
    """Scrub secrets from EVERY log record's final message.

    Attach to the root logger's handlers (in main.py) so any module's log line — e.g.
    the FMP per-symbol warnings that echo the request URL with ``apikey=`` — is redacted
    on the way to stdout (Railway logs), not just the errors Sentry captures.
    NEVER on a handler that formats uvicorn ACCESS records: it sets ``record.args = ()``,
    which `AccessFormatter` cannot unpack — that logger gets `AccessLogRedactingFilter`
    (`install_uvicorn_log_redaction`).
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
