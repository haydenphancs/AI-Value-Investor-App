"""Secret + PII redaction on the way to stdout and Sentry.

Sentry is a third-party processor, so anything reaching it must be disclosed. The
redaction layer previously covered ONLY secret-named query params (`?apikey=…`), which
left email addresses, bearer tokens, bare JWTs, and inline DSN passwords flowing into
Sentry events and Railway logs.

Deliberate non-goal: `user_id` UUIDs are NOT redacted. They are pseudonymous, they are
the primary handle for diagnosing an incident from logs alone, and CLAUDE.md requires
errors to carry `user_id` / `report_id`. The correct treatment is disclosure in the
privacy policy, not deletion — see the module docstring in app/log_redaction.py. The test
below pins that so a future "redact all UUIDs" change is a conscious decision.
"""

from __future__ import annotations

import logging

from app.log_redaction import (
    SecretRedactingFilter,
    redact_secrets,
    scrub_sentry_event,
)

_UUID = "11111111-2222-4333-8444-555555555555"
_JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)


# ── Secrets ───────────────────────────────────────────────────────────────────

def test_redacts_secret_query_params():
    out = redact_secrets("GET https://fmp.com/x?symbol=AAPL&apikey=SUPERSECRET failed")
    assert "SUPERSECRET" not in out
    assert "apikey=***" in out
    assert "symbol=AAPL" in out, "non-secret params must survive for debugging"


def test_redacts_every_secret_param_alias():
    for name in ("apikey", "api_key", "api-key", "token", "access_token",
                 "access-token", "secret", "password", "key"):
        out = redact_secrets(f"https://x.com/a?{name}=LEAKED")
        assert "LEAKED" not in out, name


def test_does_not_clobber_unrelated_params_ending_in_key():
    out = redact_secrets("https://x.com/a?sort_key=name&monkey=yes")
    assert "sort_key=name" in out
    assert "monkey=yes" in out


def test_redacts_dsn_password():
    out = redact_secrets("postgresql://postgres:MyP4ssw0rd@db.supabase.co:5432/postgres")
    assert "MyP4ssw0rd" not in out
    assert "postgres:***@db.supabase.co" in out
    assert "db.supabase.co" in out, "host must survive — it identifies the failure"


# ── PII ───────────────────────────────────────────────────────────────────────

def test_redacts_email_addresses():
    out = redact_secrets("signup failed for Duc.Hai+test@gmail.com (dup)")
    assert "gmail.com" not in out
    assert "Duc.Hai" not in out
    assert "***@***" in out
    assert "(dup)" in out, "surrounding log structure must survive"


def test_redacts_bearer_tokens():
    out = redact_secrets(f"headers={{'Authorization': 'Bearer {_JWT}'}}")
    assert _JWT not in out
    assert "Bearer ***" in out


def test_redacts_bare_jwts():
    assert _JWT not in redact_secrets(f"refresh token {_JWT} expired")


def test_short_bearer_like_strings_are_left_alone():
    """The 20-char floor stops it eating words like 'bearer of bad news'."""
    assert "bearer of bad" in redact_secrets("the bearer of bad news")


# ── The deliberate exception ──────────────────────────────────────────────────

def test_user_id_uuids_are_preserved_on_purpose():
    msg = f"Account deletion failed for user={_UUID}: KeyError"
    out = redact_secrets(msg)
    assert _UUID in out, (
        "user_id must survive redaction — it is the primary diagnostic handle and "
        "CLAUDE.md requires it in error logs. If this is being changed deliberately, "
        "update app/log_redaction.py's docstring and the privacy policy together."
    )
    assert out == msg


def test_report_and_ticker_context_is_preserved():
    msg = f"report_id=abc-123 ticker=AAPL persona=warren_buffett user={_UUID} failed"
    assert redact_secrets(msg) == msg


# ── Robustness ────────────────────────────────────────────────────────────────

def test_handles_non_string_input():
    assert redact_secrets(None) == "None"
    assert redact_secrets(12345) == "12345"
    assert redact_secrets({"a": 1}) == "{'a': 1}"


def test_handles_empty_and_multiline():
    assert redact_secrets("") == ""
    multi = "line1 a@b.co\nline2 ?apikey=X"
    out = redact_secrets(multi)
    assert "a@b.co" not in out and "apikey=X" not in out
    assert "\n" in out, "line structure must survive"


def test_multiple_secrets_in_one_string_all_go():
    out = redact_secrets(
        f"user=me@x.com token={_JWT} url=https://a.com/b?apikey=K1"
    )
    for leak in ("me@x.com", _JWT, "apikey=K1"):
        assert leak not in out, leak


# ── Sentry event scrubbing ────────────────────────────────────────────────────

def test_scrub_sentry_event_covers_every_free_text_field():
    event = {
        "message": "boom for a@b.co",
        "logentry": {"message": "?apikey=K", "formatted": f"Bearer {_JWT}"},
        "exception": {"values": [{"value": "failed for c@d.co"}]},
        "breadcrumbs": {"values": [{"message": f"?token={_JWT}"}]},
    }
    out = scrub_sentry_event(event)
    assert "a@b.co" not in out["message"]
    assert "apikey=K" not in out["logentry"]["message"]
    assert _JWT not in out["logentry"]["formatted"]
    assert "c@d.co" not in out["exception"]["values"][0]["value"]
    assert _JWT not in out["breadcrumbs"]["values"][0]["message"]


_FMP_KEY = "d41d8cd98f00b204e9800998ecf8427e"


def test_a_bare_query_string_is_redacted_without_a_leading_question_mark():
    """sentry-sdk's httpx integration records `http.query` WITHOUT the `?`, and a frame's
    `params={'apikey': K}` reprs without one either. The old `[?&]`-only anchor let both
    through, so every event raised after an FMP call carried the production key."""
    assert _FMP_KEY not in redact_secrets(f"symbol=AAPL&apikey={_FMP_KEY}")
    assert redact_secrets(f"apikey={_FMP_KEY}") == "apikey=***"
    assert _FMP_KEY not in redact_secrets(f"{{'apikey': '{_FMP_KEY}', 'symbol': 'AAPL'}}")
    assert _FMP_KEY not in redact_secrets(f'{{"apikey": "{_FMP_KEY}"}}')
    # The boundary still protects unrelated names.
    assert redact_secrets("sort_key=abc") == "sort_key=abc"


def test_sentry_http_breadcrumbs_and_frame_vars_never_carry_the_fmp_key():
    """THE leak: any request that made an FMP call and then logged at ERROR produced an event
    whose breadcrumb list held `http.query: symbol=AAPL&apikey=<key>` for every FMP call in
    that request, and on an HTTPStatusError the frame vars held `e=...apikey=<key>` and
    `params={'apikey': '<key>'}`. Anyone with Sentry access (90-day retention) could read
    the key. Breadcrumb `data`, `extra` and every frame's `vars` are walked."""
    event = {
        "breadcrumbs": {"values": [
            {"type": "http", "category": "httpx", "data": {
                "url": "https://financialmodelingprep.com/stable/profile",
                "method": "GET", "status_code": 403,
                "http.query": f"symbol=AAPL&apikey={_FMP_KEY}",
                "http.fragment": "",
                "nested": {"again": [f"token={_FMP_KEY}"]},
            }},
            {"message": "plain", "data": "not-a-dict"},
        ]},
        "exception": {"values": [{
            "type": "HTTPStatusError",
            "value": f"Client error '403 Forbidden' for url 'https://x?apikey={_FMP_KEY}'",
            "stacktrace": {"frames": [
                {"function": "_make_request", "vars": {
                    "e": f"HTTPStatusError(\"Client error for url 'https://x?apikey={_FMP_KEY}'\")",
                    "params": {"apikey": f"'{_FMP_KEY}'", "symbol": "'AAPL'"},
                    "url": "'https://x/stable/profile'",
                    "headers": ["'Authorization: Bearer " + _JWT + "'"],
                }},
                {"function": "caller", "vars": None},
            ]},
        }]},
        "threads": {"values": [{"stacktrace": {"frames": [
            {"vars": {"q": f"apikey={_FMP_KEY}"}}]}}]},
        "extra": {"request_url": f"https://x?apikey={_FMP_KEY}"},
    }
    out = scrub_sentry_event(event)
    blob = str(out)
    assert _FMP_KEY not in blob, blob
    assert _JWT not in blob
    crumb = out["breadcrumbs"]["values"][0]["data"]
    assert "http.query" not in crumb and "http.fragment" not in crumb
    assert crumb["url"].endswith("/stable/profile"), "the diagnostic host+path must survive"
    assert crumb["status_code"] == 403
    frame = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame["params"]["symbol"] == "'AAPL'", "non-secret locals must survive"
    assert frame["params"]["apikey"] == "[redacted]", "a credential-named key is blanked"


def test_the_sentry_init_declares_the_sdk_scrubber_as_a_belt():
    """`EventScrubber(recursive=True)` blanks denylisted KEYS client-side, so a nested
    `params={'apikey': …}` is caught even if the value-based walk above regresses."""
    import inspect
    import app.main as main_mod

    init = inspect.getsource(main_mod)
    init = init[init.index("sentry_sdk.init("):]
    init = init[:init.index("\n    )\n")]
    assert "event_scrubber=EventScrubber(recursive=True)" in init


def test_sentry_never_receives_a_plaintext_password():
    """THE regression this guard exists for.

    `send_default_pii=False` gates cookies only. sentry-sdk's Starlette integration attaches
    the parsed JSON body to EVERY event, so a failed sign-up — logged with
    `logger.error(..., exc_info=True)`, which LoggingIntegration turns into an event — used to
    ship the user's plaintext password to a third-party store. Only reproducible in
    production, since Sentry initialises only when ENVIRONMENT == "production"."""
    event = {
        "request": {
            "url": "https://api/api/v1/auth/register",
            "data": {
                "email": "someone@example.com",
                "password": "hunter2-the-real-thing",
                "display_name": "Someone",
            },
        }
    }
    out = scrub_sentry_event(event)
    body = out["request"]["data"]
    assert body["password"] == "[redacted]"
    assert "hunter2-the-real-thing" not in str(out)
    # The non-credential shape survives, so a genuine 400 is still diagnosable.
    assert body["display_name"] == "Someone"


def test_sentry_never_receives_a_password_reset_code_or_token():
    """The 6-digit reset code is a bearer credential for the duration of its life, and
    `new_password` / `*_token` are the same class of secret under different spellings."""
    event = {
        "request": {
            "data": {
                "code": "482913",
                "new_password": "brand-new-secret",
                "refresh_token": _JWT,
                "supabase_access_token": _JWT,
                "signed_transaction": "eyJhbGciOi.fake.payload",
            }
        }
    }
    body = scrub_sentry_event(event)["request"]["data"]
    for key in (
        "code", "new_password", "refresh_token",
        "supabase_access_token", "signed_transaction",
    ):
        assert body[key] == "[redacted]", f"{key} leaked to Sentry"
    assert _JWT not in str(body)


def test_sentry_request_body_scrub_tolerates_non_dict_bodies():
    """With `max_request_body_size='never'` the body is an AnnotatedValue, not a dict, and a
    form/raw body can be a string. Neither may raise inside before_send — an exception there
    drops the event and blinds the monitoring."""
    for data in (None, "raw string", ["a", "b"], 42, object()):
        event = {"request": {"data": data}}
        assert scrub_sentry_event(event) is not None
    # A string body still gets the free-text redactor.
    out = scrub_sentry_event({"request": {"data": f"Bearer {_JWT}"}})
    assert _JWT not in out["request"]["data"]


def test_scrub_sentry_event_tolerates_malformed_shapes():
    """A malformed event must never raise inside before_send — that would drop the
    event entirely and blind the monitoring."""
    for bad in (
        {},
        {"message": None},
        {"logentry": "not a dict"},
        {"exception": {"values": None}},
        {"exception": {"values": [None, {"value": None}, "str"]}},
        {"breadcrumbs": {"values": [{}, None]}},
    ):
        assert scrub_sentry_event(dict(bad)) is not None


# ── stdout filter ─────────────────────────────────────────────────────────────

def test_logging_filter_redacts_the_formatted_message():
    f = SecretRedactingFilter()
    rec = logging.LogRecord(
        name="t", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="failed for %s at %s", args=("user@example.com", "?apikey=K"), exc_info=None,
    )
    assert f.filter(rec) is True
    out = rec.getMessage()
    assert "user@example.com" not in out
    assert "apikey=K" not in out


def test_logging_filter_leaves_clean_records_untouched():
    f = SecretRedactingFilter()
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="cache hit for ticker=%s", args=("AAPL",), exc_info=None,
    )
    f.filter(rec)
    assert rec.getMessage() == "cache hit for ticker=AAPL"
    assert rec.args == ("AAPL",), "args must not be flattened when nothing changed"


# ── request headers ──────────────────────────────────────────────────────────


def test_scrub_sentry_event_redacts_credential_request_headers():
    """sentry-sdk only masks Authorization/Cookie/X-Api-Key/X-Forwarded-For/X-Real-IP. The
    marketing worker's shared secret and the admin token travel in custom headers, so a
    request-scoped `logger.error` used to store them in the clear."""
    from app.log_redaction import scrub_sentry_event

    event = {
        "request": {
            "url": "https://api.example/api/v1/internal/marketing/runs/claim",
            "headers": {
                "X-Marketing-Worker-Token": "s3cret-worker-token",
                "x-admin-token": "adm1n",
                "Authorization": "Bearer abc",
                "apikey": "sb_secret_xyz",
                "X-Widget-Token": "w1dget",
                "Content-Type": "application/json",
                "User-Agent": "caydex-marketing-worker",
            },
        }
    }
    out = scrub_sentry_event(event)
    h = out["request"]["headers"]
    for k in ("X-Marketing-Worker-Token", "x-admin-token", "Authorization", "apikey", "X-Widget-Token"):
        assert h[k] == "[redacted]", k
    assert h["Content-Type"] == "application/json" and h["User-Agent"] == "caydex-marketing-worker"


def test_scrub_sentry_event_tolerates_events_without_request_headers():
    from app.log_redaction import scrub_sentry_event

    assert scrub_sentry_event({"message": "x"})["message"] == "x"
    assert scrub_sentry_event({"request": {"headers": None}})["request"]["headers"] is None
    assert scrub_sentry_event({"request": "not a dict"})["request"] == "not a dict"


def test_the_log_filter_redacts_the_traceback_not_just_the_message():
    """`exc_info=True` sites — the stock endpoints, the chat widget fetcher, the global 500
    handler — rendered the raw httpx line into the Railway log because the filter only
    touched `record.msg`; `Formatter.format` builds `exc_text` AFTER filters run unless one
    pre-sets it."""
    import io
    import logging as _logging

    import httpx

    from app.log_redaction import SecretRedactingFilter

    key = "d41d8cd98f00b204e9800998ecf8427e"
    url = f"https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={key}"
    resp = httpx.Response(403, request=httpx.Request("GET", url))
    stream = io.StringIO()
    handler = _logging.StreamHandler(stream)
    handler.setFormatter(_logging.Formatter("%(message)s"))
    handler.addFilter(SecretRedactingFilter())
    log = _logging.getLogger("test.redaction.traceback")
    log.propagate = False
    log.addHandler(handler)
    try:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            log.error("profile fetch failed", exc_info=True)
        # A chained typed exception keeps the raw cause in the traceback too.
        try:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError("typed wrapper") from e
        except RuntimeError:
            log.error("wrapped", exc_info=True)
    finally:
        log.removeHandler(handler)
    out = stream.getvalue()
    assert "Traceback" in out, "vacuous: no traceback was rendered"
    assert key not in out, out
    assert "apikey=***" in out


# ── W2 regress-B-2: operational `key=<value>` is not a secret ────────────────────


def test_a_bare_operational_key_survives_but_a_query_key_param_does_not():
    """Widening the anchor for `apikey=` (Sentry breadcrumbs carry it bare) dragged the
    bare word `key` along, and every push dedup key / marketing idempotency key in the
    logs became `key=***` — in exactly the lines written to find a stranded row."""
    kept = redact_secrets("push: could not return row user=u1 key=whale:abc:2026 to deferred")
    assert "key=whale:abc:2026" in kept
    assert "idempotency_key=abc-123" in redact_secrets("marketing post idempotency_key=abc-123 not ledgered")
    # ...while a Google-style `?key=` / `&key=` query parameter is still a secret.
    assert redact_secrets("GET https://g.com/x?key=GOOG123&q=1") == "GET https://g.com/x?key=***&q=1"
    assert "key=***" in redact_secrets("https://g.com/x?q=1&key=GOOG123")
    # ...and the bare secret names are still caught in prose (the Sentry breadcrumb shape).
    assert redact_secrets("apikey=SECRET in breadcrumb") == "apikey=*** in breadcrumb"
    assert redact_secrets("token=abc123") == "token=***"
