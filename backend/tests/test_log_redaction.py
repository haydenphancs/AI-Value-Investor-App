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
