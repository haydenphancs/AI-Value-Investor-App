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
    `params={'apikey': …}` is caught even if the value-based walk above regresses. Its
    `pii_denylist` must be `sentry_pii_denylist()` (the SDK default PLUS our address keys —
    TOKSEC-2); brace-bound to the one `sentry_sdk.init(...)` call by AST."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "init" and isinstance(n.func.value, ast.Name) and n.func.value.id == "sentry_sdk"]
    assert len(calls) == 1
    scrubber = {k.arg: k.value for k in calls[0].keywords}.get("event_scrubber")
    assert isinstance(scrubber, ast.Call) and ast.unparse(scrubber.func) == "EventScrubber"
    kw = {k.arg: ast.unparse(k.value) for k in scrubber.keywords}
    assert kw.get("recursive") == "True"
    assert kw.get("pii_denylist") == "sentry_pii_denylist()"
    assert not scrubber.args and "send_default_pii" not in kw and "denylist" not in kw


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


# ── transactions (review 2026-09-29) ───────────────────────────────────────────


def test_a_transaction_carries_no_bot_token_and_no_fmp_key():
    """`before_send` never runs on transactions; their httpx spans hold full URLs (the Telegram
    bot token is in the PATH) and `http.query` (FMP's apikey)."""
    from app.log_redaction import scrub_sentry_transaction

    token = "123456789:AAH" + "x" * 32
    event = {
        "type": "transaction",
        "transaction": f"POST https://api.telegram.org/bot{token}/sendMessage",
        "spans": [
            {"op": "http.client", "description": f"POST https://api.telegram.org/bot{token}/answerCallbackQuery",
             "data": {"url": f"https://api.telegram.org/bot{token}/answerCallbackQuery", "http.method": "POST"}},
            {"op": "http.client", "description": "GET https://financialmodelingprep.com/stable/quote",
             "data": {"url": "https://financialmodelingprep.com/stable/quote",
                      "http.query": "symbol=AAPL&apikey=SECRETKEY123456"}},
        ],
        "contexts": {"trace": {"data": {"note": f"bot{token}"}}},
        "request": {"url": f"https://x.example/?apikey=SECRETKEY123456"},
    }
    out = scrub_sentry_transaction(event)
    blob = repr(out)
    assert token.split(":", 1)[1] not in blob and "SECRETKEY123456" not in blob
    assert "http.query" not in out["spans"][1]["data"]
    assert out["spans"][0]["description"].startswith("POST https://api.telegram.org/bot123456789:")


def test_the_sentry_init_scrubs_transactions():
    """Brace-bound to the `sentry_sdk.init(...)` call itself, not a mention elsewhere in main.py."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "init" and isinstance(n.func.value, ast.Name) and n.func.value.id == "sentry_sdk"]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw.get("before_send_transaction") == "scrub_sentry_transaction"


# ── the marketing publisher's platform credentials (Phase 5) ───────────────────


def test_x_oauth1_header_parameters_are_redacted_but_the_header_stays_legible():
    header = ('OAuth oauth_consumer_key="ck_9f8e7d6c5b4a", oauth_nonce="b1946ac92492d2347c", '
              'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D", oauth_signature_method="HMAC-SHA1", '
              'oauth_timestamp="1318622958", oauth_token="1234567890-AbCdEfGhIjKl", oauth_version="1.0"')
    out = redact_secrets(f"x_api create_post failed Authorization={header}")
    for secret in ("ck_9f8e7d6c5b4a", "b1946ac92492d2347c", "hCtSmYh", "1234567890-AbCdEfGhIjKl"):
        assert secret not in out
    # Non-secret parameters survive, so a signing bug is still diagnosable.
    assert 'oauth_signature_method="HMAC-SHA1"' in out and 'oauth_timestamp="1318622958"' in out
    # The form/query spelling too.
    assert redact_secrets("oauth_token=abc123&oauth_verifier=zzz") == "oauth_token=***&oauth_verifier=***"


def test_an_upload_post_apikey_header_is_redacted():
    out = redact_secrets("headers={'Authorization': 'Apikey up_live_8f7e6d5c4b3a2910'}")
    assert "up_live_8f7e6d5c4b3a2910" not in out and "Apikey ***" in out


def test_a_bluesky_app_password_is_redacted_but_uuids_dates_and_keys_are_not():
    assert redact_secrets("createSession password abcd-ef12-gh34-ij56 refused") == \
        "createSession password *** refused"
    keep = (f"post_id={_UUID} run 2026-09-29 key=2026-09-29:bluesky:text "
            "rkey=3lbxy2ab4cd2e at 2026-10-01T03:17:28+00:00 card 1234-5678")
    assert redact_secrets(keep) == keep


def test_bluesky_session_fields_are_credential_keys_in_frames():
    event = {"exception": {"values": [{"stacktrace": {"frames": [{"vars": {
        "session": {"accessJwt": "opaque-access", "refreshJwt": "opaque-refresh", "did": "did:plc:abc"},
    }}]}}]}}
    out = scrub_sentry_event(event)
    session = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["session"]
    assert session["accessJwt"] == "[redacted]" and session["refreshJwt"] == "[redacted]"
    assert session["did"] == "did:plc:abc"


# ── web-search results never reach Sentry through frame locals (review 2026-10-29) ──

_PAGE_TITLE = "Oracle stock soars as Investor Day targets top estimates"


def _frame_event(**frame):
    return {"exception": {"values": [{"value": "boom", "stacktrace": {"frames": [frame]}}]}}


def _vars(out):
    return out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]


def test_a_frame_in_a_web_search_module_keeps_no_locals():
    """Brave's terms allow transient use only; the Updates Insights card is the one place a
    result may be kept. sentry-sdk captures every frame's locals by default."""
    for module in ("app.services.jump_web_search_service", "app.services.web_result_hygiene",
                   "app.services.chat_web_search_service", "app.integrations.brave_search"):
        out = scrub_sentry_event(_frame_event(
            module=module, function="digest", vars={"title": _PAGE_TITLE, "query": "why is oracle"}))
        assert _PAGE_TITLE not in str(out) and "why is oracle" not in str(out), module
        assert list(_vars(out)) == ["web_search"]


def test_a_card_frame_holding_the_jump_pages_keeps_no_locals_and_others_keep_theirs():
    holding = scrub_sentry_event(_frame_event(
        module="app.services.news_insight_service", function="_build_prompt",
        vars={"web": [{"title": _PAGE_TITLE}], "scope": "'ORCL'", "prompt": f"'… {_PAGE_TITLE} …'"}))
    assert _PAGE_TITLE not in str(holding)
    sweep = scrub_sentry_event(_frame_event(
        module="app.services.updates_insight_sweeper", function="run_sweep",
        vars={"web_context": {"ORCL": ["WebPage(publisher='CNBC')"]}, "scope": "'ORCL'"}))
    assert list(_vars(sweep)) == ["web_search"]
    # No pages held: the frame keeps its locals for triage (secrets still redacted).
    plain = scrub_sentry_event(_frame_event(
        module="app.services.news_insight_service", function="_build_prompt",
        vars={"web": "[]", "web_context": "None", "scope": "'ORCL'", "apikey": "k-123"}))
    kept = _vars(plain)
    assert kept["scope"] == "'ORCL'" and kept["apikey"] == "[redacted]"


def test_the_module_is_read_from_the_path_when_sentry_gives_none():
    out = scrub_sentry_event(_frame_event(
        abs_path="/app/backend/app/services/jump_web_search_service.py", function="digest",
        vars={"title": _PAGE_TITLE}))
    assert _PAGE_TITLE not in str(out)
    other = scrub_sentry_event(_frame_event(
        abs_path="/app/backend/app/services/chat_service.py", function="x", vars={"pages": "3"}))
    assert _vars(other) == {"pages": "3"}


def test_a_container_path_named_app_still_finds_the_module():
    out = scrub_sentry_event(_frame_event(
        abs_path="/app/app/services/web_result_hygiene.py", function="parse_page_age",
        vars={"value": _PAGE_TITLE}))
    assert _PAGE_TITLE not in str(out)


def test_a_frame_holding_a_web_source_under_any_name_keeps_no_locals():
    """Round 2 (2026-10-29): the name-based check missed `_store`'s `sources` (a card's sources
    lead with the jump pages, `origin: "web"`) and chat's `_persist_turn` message carrying the
    Brave pills (`kind: "web"`). A value only web results have drops the frame's locals."""
    store = scrub_sentry_event(_frame_event(
        module="app.services.news_insight_service", function="_store",
        vars={"scope": "'ORCL'", "sources": [
            {"title": f"'{_PAGE_TITLE}'", "url": "'https://www.cnbc.com/x'", "origin": "'web'"}]}))
    assert _PAGE_TITLE not in str(store) and list(_vars(store)) == ["web_search"]
    persist = scrub_sentry_event(_frame_event(
        module="app.api.v1.endpoints.chat", function="_persist_turn",
        vars={"ai_msg": "{'rich_content': {'sources': [{'kind': 'web', 'title': '"
                        + _PAGE_TITLE + "'}]}}"}))
    assert _PAGE_TITLE not in str(persist)
    # A frame with no web value keeps its locals for triage.
    plain = scrub_sentry_event(_frame_event(
        module="app.api.v1.endpoints.chat", function="_persist_turn",
        vars={"ai_msg": "{'rich_content': {'sources': [{'kind': 'news', 'title': 'x'}]}}"}))
    assert "news" in str(_vars(plain))


# ── SEC-1: auth request models never reach Sentry through frame locals (2026-11-26) ──────
#
# The leak, reproduced offline before the fix: sentry-sdk serialises a pydantic model as its REPR
# under the local's name — `request: "SignUpRequest(email='***@***', password='Hunter2!x', …)"` —
# and neither the key-based scrubbers (`request` is not a credential key) nor the value regexes
# (a quote right after `password=`, and no separator before `password` in `new_password=`) touched
# it. Any `logger.error(..., exc_info=True)` in an auth handler (a GoTrue 429 on sign-in, an email
# quota error on sign-up, a 5xx on change-password) stored the plaintext password in Sentry.
#
# These tests run the REAL pipeline sentry-sdk 2.x runs before `before_send`: frame capture with
# locals (`event_from_exception`), `EventScrubber(recursive=True)` (main.py's), `serialize`, then
# our `scrub_sentry_event`. Every model in `app.schemas.auth` is built with a sentinel in every
# field (`model_construct`: no validators, so a model added tomorrow is covered unchanged).

_SECRET_FIELD_NAMES = {
    # Every credential field the auth schemas carry today, by hand — the generic rule must
    # classify each one as secret, or this list catches it.
    "password", "new_password", "current_password", "code", "refresh_token", "access_token",
    "id_token", "nonce", "supabase_access_token", "device_token",
}
_MUST_SURVIVE_FIELD_NAMES = {"display_name", "provider", "user_id", "message", "tier", "avatar_url"}


def _is_secret_field(name: str) -> bool:
    from app.log_redaction import _is_secret_field_name

    return name in _SECRET_FIELD_NAMES or _is_secret_field_name(name)


def _auth_model_instances():
    """[(class name, instance, {field: sentinel})] for every pydantic model in schemas/auth."""
    from pydantic import BaseModel
    import app.schemas.auth as schemas

    out = []
    for name, cls in sorted(vars(schemas).items()):
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        if cls.__module__ != schemas.__name__:
            continue
        values = {
            field: (f"S3CRET-{name}-{field}" if _is_secret_field(field) else f"plain-{name}-{field}")
            for field in cls.model_fields
        }
        out.append((name, cls.model_construct(**values), values))
    assert {"SignInRequest", "SignUpRequest", "ResetPasswordRequest", "ChangePasswordRequest",
            "SetPasswordRequest", "RefreshTokenRequest", "OAuthSignInRequest",
            "SessionExchangeRequest", "TokenResponse"} <= {n for n, _, _ in out}
    return out


def _through_sentry(module_name: str, frame_locals: dict, *, sdk_scrubber=None,
                    ours: bool = True) -> dict:
    """Raise inside a function whose frame belongs to `module_name` and holds `frame_locals`
    (as named parameters, i.e. real locals), and return the event as `before_send` would see
    it after our scrubber. `sdk_scrubber` replaces the SDK scrubber main.py configures (default:
    the bare `EventScrubber(recursive=True)`); `ours=False` stops before `scrub_sentry_event`, to
    test the SDK layer alone."""
    import sys
    from sentry_sdk.consts import DEFAULT_OPTIONS
    from sentry_sdk.scrubber import EventScrubber
    from sentry_sdk.serializer import serialize
    from sentry_sdk.utils import event_from_exception

    namespace = {"__name__": module_name}
    params = ", ".join(frame_locals)
    exec(  # noqa: S102 — builds a frame whose f_globals["__name__"] is the module under test
        compile(
            f"def handler({params}):\n    raise RuntimeError('AuthApiError: 429 rate limited')\n",
            f"/srv/app/{module_name.replace('.', '/')}.py", "exec",
        ),
        namespace,
    )
    try:
        namespace["handler"](**frame_locals)
    except RuntimeError:
        exc_info = sys.exc_info()
    options = dict(DEFAULT_OPTIONS)
    options.update(include_local_variables=True, include_source_context=False)
    event, _hint = event_from_exception(exc_info, client_options=options)
    (sdk_scrubber or EventScrubber(recursive=True)).scrub_event(event)
    event = serialize(
        event, max_request_body_size="never", max_value_length=options["max_value_length"],
    )
    return scrub_sentry_event(event) if ours else event


def _frame_of(event: dict, module_name: str) -> dict:
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    matching = [f for f in frames if f.get("module") == module_name]
    assert matching, f"no frame for {module_name} in {[f.get('module') for f in frames]}"
    return matching[-1]


def test_an_auth_handler_frame_keeps_no_locals_for_any_auth_model():
    """The handler frame itself: every model, its secrets AND its other fields gone (the frame
    keeps function and line; the exception value and the log line carry the diagnosis)."""
    for name, model, values in _auth_model_instances():
        event = _through_sentry(
            "app.api.v1.endpoints.auth",
            {"request": model, "req": "<starlette Request>", "user_id": "u-123"},
        )
        frame = _frame_of(event, "app.api.v1.endpoints.auth")
        assert list(frame["vars"]) == ["auth_handler"], (name, frame["vars"])
        assert frame["function"] == "handler" and frame["lineno"]
        blob = str(event)
        for field, sentinel in values.items():
            if _is_secret_field(field):
                assert sentinel not in blob, f"{name}.{field} reached Sentry"


def test_an_auth_model_in_any_other_frame_loses_its_credential_values():
    """The handler is not the only frame holding the model: an exception escaping it carries
    FastAPI's `run_endpoint_function` frame, whose `values` local is `{'request': <model>}`. In
    a frame outside the auth module the locals stay (triage), but every credential-named field
    inside a repr is blanked — independently of `Field(repr=False)` on the schema."""
    for name, model, values in _auth_model_instances():
        event = _through_sentry(
            "fastapi.routing",
            {"values": {"request": model, "req": "<starlette Request>"}, "response": model},
        )
        frame = _frame_of(event, "fastapi.routing")
        blob = str(frame["vars"])
        for field, sentinel in values.items():
            if _is_secret_field(field):
                assert sentinel not in blob, f"{name}.{field} reached Sentry via fastapi.routing"
            elif field in _MUST_SURVIVE_FIELD_NAMES and f"{field}=" in blob:
                assert sentinel in blob, f"{name}.{field} was over-redacted"
        whole = str(event)
        assert not any(s in whole for f, s in values.items() if _is_secret_field(f)), name


def test_the_hand_listed_credential_fields_are_all_classified_secret():
    """If the generic rule ever stops covering a field the auth schemas really carry, the
    pipeline tests above would still pass on THEIR classification — this pins the rule itself."""
    from app.log_redaction import _is_secret_field_name

    for field in _SECRET_FIELD_NAMES:
        assert _is_secret_field_name(field), field
    for field in _MUST_SURVIVE_FIELD_NAMES | {"error_code", "status_code", "token_type_hint_x"}:
        if field == "token_type_hint_x":
            assert _is_secret_field_name(field)  # any `token` segment is a credential name
            continue
        assert not _is_secret_field_name(field), field


def test_a_frame_in_the_auth_module_is_found_by_its_path_too():
    """sentry-sdk can omit `module`; the path still identifies the auth handler."""
    out = scrub_sentry_event(_frame_event(
        abs_path="/app/backend/app/api/v1/endpoints/auth.py", function="register",
        vars={"request": "SignUpRequest(email='x', password='Hunter2!xyz', display_name='V')"}))
    assert "Hunter2!xyz" not in str(out)
    assert list(_vars(out)) == ["auth_handler"]


def test_frame_field_redaction_keeps_diagnostic_codes():
    """`error_code=` / `status_code=` are what diagnoses an event — never redacted, and a quoted
    value Sentry truncated (no closing quote) is still blanked to its end."""
    out = scrub_sentry_event(_frame_event(
        module="app.services.some_service", function="f",
        vars={
            "exc": "HTTPException(status_code=401, detail={'error_code': 'AUTH_REQUIRED'})",
            "err": "AuthError(error_code='AUTH_CODE_INVALID', code='482913')",
            "long": "ResetPasswordRequest(email='x', new_password='abcdefgh...",
            "nested": "{'note': \"see password='p@ss-in-a-note'\"}",
        }))
    v = _vars(out)
    assert "status_code=401" in v["exc"] and "'error_code': 'AUTH_REQUIRED'" in v["exc"]
    assert "error_code='AUTH_CODE_INVALID'" in v["err"] and "482913" not in v["err"]
    assert "abcdefgh" not in v["long"]
    assert "p@ss-in-a-note" not in v["nested"]


def test_log_lines_are_not_widened_to_redact_codes():
    """FRAME-scoped on purpose: an operational log line keeps its `code=` / `error_code=` values
    (widening the global value regex to `\\w*code=` would blank every one of them)."""
    line = "verify_otp refused: code='otp_expired' error_code=AUTH_CODE_INVALID status_code=400"
    assert redact_secrets(line) == line


# ── The email link's token_hash (BE-AUTH-01, TOKSEC-1) and the user's address (TOKSEC-2) ──────
#
# The Confirm-signup email links to `/auth/confirm?token_hash=<56 hex>&type=signup`. Until it is
# redeemed (or expires) that hash is a LIVE single-use credential: GoTrue's public `/verify`
# turns it into a session. `token=` never matched `token_hash=` (the name runs on), so the
# query-string rule, Sentry's `request.query_string` and the POST handler's locals all kept it.

_HASH = "a1" * 28  # 56 hex, the shape GoTrue's sha224 token hash has


def test_a_token_hash_query_value_is_redacted_everywhere_the_global_rule_reaches():
    line = f'127.0.0.1:5000 - "GET /auth/confirm?token_hash={_HASH}&type=signup HTTP/1.1" 200'
    out = redact_secrets(line)
    assert _HASH not in out and "token_hash=***&type=signup" in out, out
    # The bare query string (no `?`), the form body's repr, and the dict / JSON reprs.
    assert _HASH not in redact_secrets(f"token_hash={_HASH}&type=signup")
    assert _HASH not in redact_secrets(f"bytearray(b'token_hash={_HASH}&type=signup')")
    assert _HASH not in redact_secrets(f"{{'token_hash': '{_HASH}', 'type': 'signup'}}")
    assert _HASH not in redact_secrets(f'{{"token_hash": "{_HASH}"}}')
    # A Supabase implicit-flow fragment's refresh token (not a JWT, so no other rule saw it).
    frag = "https://caydexinvest.com/#access_token=x&refresh_token=r3fr3shT0k3nV4lu3&type=signup"
    assert "r3fr3shT0k3nV4lu3" not in redact_secrets(frag)
    assert "type=signup" in redact_secrets(frag)


def test_the_widened_rule_still_spares_lookalike_names():
    """`token(?:_hash)?` must not swallow `token_type=`, nor `sort_token_hash_x=`-style names
    without a separator before them, nor an operational `code=`."""
    for keep in ("token_type=bearer", "x_token_hash=abc", "code=otp_expired",
                 "error_code=AUTH_CODE_INVALID", "hash=deadbeef"):
        assert redact_secrets(f"note {keep} end") == f"note {keep} end", keep


def test_redact_query_string_blanks_every_secret_name_and_keeps_the_rest():
    from app.log_redaction import redact_query_string

    q = (f"token_hash={_HASH}&type=signup&code=12345678&key=K3Y&api-key=AK&apikey=AK2"
         f"&token%5Fhash={_HASH}&otp=999&nonce=n1&symbol=AAPL&q=&flag&token_hash={_HASH}"
         "&id.token=itok&email=someone@example.com")
    out = redact_query_string(q)
    for leaked in (_HASH, "12345678", "K3Y", "AK", "AK2", "999", "n1", "itok", "someone@example.com"):
        assert f"={leaked}&" not in out + "&" and leaked not in out.split("&"), (leaked, out)
    assert _HASH not in out and "12345678" not in out and "K3Y" not in out and "itok" not in out
    assert "type=signup" in out and "symbol=AAPL" in out
    assert "q=" in out.split("&") and "flag" in out.split("&"), "empty / bare params are kept as-is"
    assert out.count("token_hash=***") == 2 and "token%5Fhash=***" in out
    # `&amp;` left undecoded by a mail client still splits the hash off on its own.
    assert _HASH not in redact_query_string(f"token_hash={_HASH}&amp;type=signup")
    # Non-str input and a pathological one never raise.
    assert redact_query_string(None) == "None"
    assert redact_query_string("&&&==&=") == "&&&==&="


def test_redact_query_string_and_request_target_fail_closed(monkeypatch):
    import app.log_redaction as lr

    def boom(_name):
        raise RuntimeError("classifier broke")

    monkeypatch.setattr(lr, "_is_secret_query_name", boom)
    assert lr.redact_query_string(f"token_hash={_HASH}") == "***"
    out = lr.redact_request_target(f"/auth/confirm?token_hash={_HASH}&type=signup")
    assert _HASH not in out and out.startswith("/auth/confirm?")


def test_redact_request_target_keeps_the_path_and_handles_full_urls():
    from app.log_redaction import redact_request_target

    assert redact_request_target("/api/v1/stocks/AAPL/overview") == "/api/v1/stocks/AAPL/overview"
    out = redact_request_target(f"https://caydexinvest.com/auth/confirm?token_hash={_HASH}&type=signup")
    assert out == "https://caydexinvest.com/auth/confirm?token_hash=***&type=signup"
    assert redact_request_target("/x?") == "/x?"
    assert redact_request_target(b"/raw?token_hash=" + _HASH.encode()).count(_HASH) == 0


def test_scrub_sentry_event_redacts_the_request_url_and_query_string():
    """sentry-sdk's ASGI integration attaches the query string to EVERY event raised inside a
    request, `send_default_pii` or not. It was scrubbed only for transactions."""
    from app.log_redaction import scrub_sentry_transaction

    def ev():
        return {"request": {"url": f"https://caydexinvest.com/auth/confirm?token_hash={_HASH}",
                            "query_string": f"token_hash={_HASH}&type=signup&code=12345678",
                            "method": "GET"},
                "exception": {"values": [{"type": "RuntimeError", "value": "boom"}]}}

    for scrub in (scrub_sentry_event, scrub_sentry_transaction):
        out = scrub(ev())
        assert _HASH not in str(out) and "12345678" not in str(out), scrub.__name__
        assert out["request"]["query_string"] == "token_hash=***&type=signup&code=***"
        assert out["request"]["url"].endswith("/auth/confirm?token_hash=***")
        assert out["request"]["method"] == "GET"


def test_logentry_params_are_redacted():
    """LoggingIntegration ships the unformatted template plus `params` = `record.args`."""
    out = scrub_sentry_event({"logentry": {
        "message": "fetch failed: %s", "params": [f"GET /x?apikey={_FMP_KEY}", "AAPL"]}})
    assert _FMP_KEY not in str(out) and out["logentry"]["params"][1] == "AAPL"
    out = scrub_sentry_event({"logentry": {"message": "%s", "params": (f"token_hash={_HASH}",)}})
    assert _HASH not in str(out)


def test_a_list_under_a_credential_key_loses_every_leaf():
    """`parse_qs` gives `{'token_hash': ['<hex>']}`: the old walk blanked a credential key's value
    only when it was NOT a list, so the hash rode through (TOKSEC-1)."""
    out = scrub_sentry_event(_frame_event(
        module="starlette.requests", function="form",
        vars={"form": {"token_hash": [_HASH], "type": ["signup"]},
              "code": ["12345678", 12345678, None, True],
              "secret": ("tuple-secret", ["nested-secret", {"inner": "deep-secret"}]),
              "tokens": [{"kind": "access", "value": "dict-in-list-secret"}],
              "values": ["plain-a", "plain-b"]}))
    v = _vars(out)
    blob = str(v)
    for leaked in (_HASH, "12345678", "tuple-secret", "nested-secret", "deep-secret", "dict-in-list-secret"):
        assert leaked not in blob, leaked
    assert v["form"]["type"] == ["signup"], "a non-credential list keeps its values"
    assert v["code"][2] is None and v["code"][3] is True, "None / bools carry no secret"
    assert v["values"] == ["plain-a", "plain-b"]


def test_an_email_confirm_page_frame_keeps_no_locals():
    """The POST handler holds the unredeemed hash four ways: the bare local, the raw `body`, its
    decoded `text`, and the `parse_qs` dict of lists. Run through the REAL SDK pipeline."""
    from urllib.parse import parse_qs

    text = f"token_hash={_HASH}&type=signup"
    event = _through_sentry("app.api.email_confirm_page", {
        "body": bytearray(text.encode()), "text": text, "form": parse_qs(text),
        "token_hash": _HASH, "client_ip": "203.0.113.9",
    })
    frame = _frame_of(event, "app.api.email_confirm_page")
    assert list(frame["vars"]) == ["auth_handler"], frame["vars"]
    assert frame["function"] == "handler" and frame["lineno"]
    assert _HASH not in str(event) and "203.0.113.9" not in str(event)


def test_the_same_locals_in_any_other_frame_lose_the_hash_value_by_value():
    """Outside the credential modules the locals stay for triage, but every copy of the hash goes:
    the parse_qs list (key rule), the body's repr and the text (the widened query rule)."""
    from urllib.parse import parse_qs

    text = f"token_hash={_HASH}&type=signup"
    event = _through_sentry("starlette.requests", {
        "body": bytearray(text.encode()), "text": text, "form": parse_qs(text), "chunks": [text.encode()],
    })
    frame = _frame_of(event, "starlette.requests")
    assert _HASH not in str(event), frame["vars"]
    assert "type" in str(frame["vars"]["form"]), "the rest of the frame survives"


def test_sentry_pii_denylist_keeps_the_sdk_defaults_and_adds_the_address_keys():
    """`pii_denylist=` REPLACES the SDK's default list; ours must start from it."""
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST, EventScrubber

    from app.log_redaction import sentry_pii_denylist

    lst = sentry_pii_denylist()
    assert set(DEFAULT_PII_DENYLIST) <= set(lst)
    assert {"sb-forwarded-for", "client_ip"} <= set(lst)
    assert len(lst) == len(set(lst)), "no duplicates"
    assert sentry_pii_denylist() is not sentry_pii_denylist(), "a fresh list (the SDK extends it)"
    scrubber = EventScrubber(recursive=True, pii_denylist=sentry_pii_denylist())
    for key in [*DEFAULT_DENYLIST, *DEFAULT_PII_DENYLIST, "sb-forwarded-for", "client_ip"]:
        assert key.lower() in scrubber.denylist, key


def test_the_forwarded_address_never_reaches_sentry_from_a_gotrue_frame():
    """TOKSEC-2: the SDK's `_request` frame copies GoTrue's shared headers dict — with our
    `Sb-Forwarded-For` — into its own `headers` local, and `run_gotrue` keeps `client_ip`. Each
    layer must catch it ALONE: the SDK scrubber main.py builds, and our own `scrub_sentry_event`
    (against the bare default scrubber, as if main.py regressed)."""
    from sentry_sdk.scrubber import EventScrubber

    from app.log_redaction import sentry_pii_denylist

    ip = "203.0.113.9"
    headers = {"Authorization": "Bearer sb_secret_abcdefghijklmnop", "apiKey": "sb_secret_x",
               "Sb-Forwarded-For": ip, "X-Client-Info": "gotrue-py/2.9"}
    frames = [("gotrue._sync.gotrue_base_api", {"method": "POST", "url": "token", "headers": dict(headers)}),
              ("app.database", {"verb": "<bound method sign_in>", "client_ip": ip, "owner": "<GoTrue>"})]
    for module, local_vars in frames:
        sdk_only = _through_sentry(
            module, local_vars,
            sdk_scrubber=EventScrubber(recursive=True, pii_denylist=sentry_pii_denylist()), ours=False)
        assert ip not in str(sdk_only), (module, "SDK layer")
        ours_only = _through_sentry(module, local_vars)  # bare default SDK scrubber + ours
        assert ip not in str(ours_only), (module, "our layer")
        frame = _frame_of(ours_only, module)
        if module.startswith("gotrue"):
            assert "gotrue-py/2.9" in str(frame["vars"]["headers"]), "other headers survive"
    # And the SDK's bare default list really does miss it (the reason both exist — not vacuous).
    bare = _through_sentry(frames[0][0], frames[0][1], ours=False)
    assert ip in str(bare)


# ── decode_token: an expired token is routine, not a warning (AUTH-BE-14 caveat) ──────────────

def test_decode_token_logs_an_expired_token_at_debug_and_a_bad_one_at_warning(caplog):
    """Every access token ends by expiring; `/auth/refresh` logs that outcome itself at INFO, so a
    WARNING here doubled every lapsed session. A forged or malformed token stays a WARNING."""
    from datetime import datetime, timedelta, timezone

    import pytest
    from jose import JWTError, jwt
    from jose.exceptions import ExpiredSignatureError

    from app.config import settings
    from app.core.security import decode_token

    expired = jwt.encode(
        {"sub": "u-1", "type": "access", "exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    forged = jwt.encode({"sub": "u-1", "type": "access"}, "not-the-secret", algorithm="HS256")

    with caplog.at_level(logging.DEBUG, logger="app.core.security"):
        with pytest.raises(ExpiredSignatureError):
            decode_token(expired)
    expired_records = [r for r in caplog.records if r.name == "app.core.security"]
    assert expired_records and all(r.levelno == logging.DEBUG for r in expired_records), (
        [(r.levelname, r.getMessage()) for r in expired_records])
    assert expired not in caplog.text

    caplog.clear()
    for bad in (forged, "not.a.jwt", ""):
        with caplog.at_level(logging.DEBUG, logger="app.core.security"):
            with pytest.raises(JWTError) as info:
                decode_token(bad)
        assert not isinstance(info.value, ExpiredSignatureError)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3 and all("Token decode failed" in r.getMessage() for r in warnings)
    assert forged not in caplog.text


def test_the_address_key_names_match_what_run_gotrue_actually_uses():
    """The scrub is BY NAME, so a rename in `database.run_gotrue` (its local, or the header it
    writes) would silently reopen TOKSEC-2. Pin the pairing to the source."""
    import ast
    import inspect

    import app.database as database
    from app.log_redaction import _PII_KEYS, SENTRY_PII_DENYLIST_EXTRA

    header = database.GOTRUE_FORWARDED_FOR_HEADER.lower()
    assert header in _PII_KEYS and header in SENTRY_PII_DENYLIST_EXTRA
    tree = ast.parse(inspect.getsource(database.run_gotrue))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    reads_contextvar = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                        and "_gotrue_client_ip" in ast.unparse(n.value)]
    assert reads_contextvar, "run_gotrue no longer reads the bound address"
    for node in reads_contextvar:
        for target in node.targets:
            assert isinstance(target, ast.Name) and target.id in _PII_KEYS, (
                f"run_gotrue holds the address as {ast.unparse(target)!r}: add it to _PII_KEYS "
                "and SENTRY_PII_DENYLIST_EXTRA")
    assert "client_ip" in assigned and "client_ip" in SENTRY_PII_DENYLIST_EXTRA
