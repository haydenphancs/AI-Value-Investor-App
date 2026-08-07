"""Admin authorization must not be grantable by registering an email address.

`_authorize_admin` used to accept any caller whose token carried one of two hardcoded
addresses:

    _ADMIN_EMAILS = {"haiphan@caydexinvest.com", "admin@caydexinvest.com"}
    if user and user.get("email") in _ADMIN_EMAILS: return

An email claim is not a credential. `POST /auth/register` refuses to mint tokens unless
`email_confirmed_at` is set (`auth.py:291`) — correct — but Supabase POPULATES that field
automatically when the project's "Confirm email" setting is off, which is its state today
(LAUNCH_CHECKLIST §5b(b), unticked). `auth.py:300` acknowledges it: "Confirmation disabled
project-side: a real session exists, so behave as before."

So registering `admin@caydexinvest.com` — an address nobody holds, since the mailboxes in §2
of the checklist are not set up either — yielded a real session and every admin route:
expensive full-universe FMP recomputes, plus internal counts from the status endpoints.

Authorization now reads `users.is_admin` (migration 113), which registration cannot set.
Turning on "Confirm email" is still required and is tracked separately — but a control whose
only guard is a checkbox in another product's dashboard is not a control.

No network, no Supabase.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.api.v1.endpoints import admin


_ADMIN_ID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"


def _user(**over):
    row = {"id": _ADMIN_ID, "email": "someone@example.com", "is_admin": False}
    row.update(over)
    return row


def _denied(user, token=None):
    with pytest.raises(HTTPException) as ei:
        admin._authorize_admin(user, token)
    return ei.value


# ── The escalation this closes ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "email", ["haiphan@caydexinvest.com", "admin@caydexinvest.com"]
)
def test_the_old_allowlisted_emails_no_longer_grant_admin(email):
    """THE regression guard. Holding the address must mean nothing without the DB flag."""
    exc = _denied(_user(email=email, is_admin=False))
    assert exc.status_code == 403


def test_email_alone_never_grants_even_with_the_flag_column_missing():
    """A row written before migration 113 has no `is_admin` key at all. That must read as
    'not an admin', not as 'unknown, allow'."""
    row = {"id": _ADMIN_ID, "email": "admin@caydexinvest.com"}
    assert "is_admin" not in row
    assert _denied(row).status_code == 403


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1, "", "yes"])
def test_only_a_real_boolean_true_grants_admin(value):
    """`is_admin is True`, not truthiness. A JSON round-trip can deliver the string "false",
    which is truthy, and a NULL column must not be mistaken for permission."""
    assert _denied(_user(is_admin=value)).status_code == 403


def test_the_flag_grants_admin():
    admin._authorize_admin(_user(is_admin=True), None)  # must not raise


# ── 401 vs 403 (auth.md rule 2) ───────────────────────────────────────────────

def test_no_credential_at_all_is_401_not_403():
    """iOS only attempts recovery on 401. Answering a MISSING credential with 403 is what
    made 'tap Follow while signed out' revert silently — see .claude/rules/auth.md rule 2."""
    exc = _denied({"id": "guest", "is_guest": True})
    assert exc.status_code == 401
    assert exc.detail["error_code"] == "AUTH_REQUIRED"


def test_no_user_and_no_token_is_401():
    assert _denied(None).status_code == 401


def test_the_REAL_guest_sentinel_is_401_not_403():
    """The test above passes a hand-made `{"is_guest": True}` — a shape the dependency these
    routes actually use NEVER produces. That is why this shipped anyway.

    `get_current_user_or_guest` (dependencies.py, the base dependency every route in admin.py
    depends on) returns a bare sentinel with NO `is_guest` key — only the four `*_identity`
    wrappers stamp it. So `user.get("is_guest")` read None, `presented_nothing` was False, and
    a caller with no credential whatsoever got 403 — the exact shape auth.md rule 2 bans, and
    the thing `_authorize_admin`'s own docstring claimed to have fixed. The 401 branch was
    unreachable in production.

    Build the sentinel the same way the dependency does, so this test cannot drift from it.
    """
    from app.dependencies import GUEST_USER_ID

    sentinel = {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}
    assert "is_guest" not in sentinel, (
        "if the base dependency starts stamping is_guest, update this fixture from it"
    )

    exc = _denied(sentinel)
    assert exc.status_code == 401
    assert exc.detail["error_code"] == "AUTH_REQUIRED"


def test_a_guest_sentinel_with_a_wrong_token_is_403_not_401():
    """Presenting a credential — even a bad one — is not the same as presenting nothing.
    Only the no-credential case may be 401."""
    exc = _denied({"id": "00000000-0000-4000-8000-00000000dead"}, "some-wrong-token")
    assert exc.status_code == 403


# ── Header robustness (unauthenticated 500) ───────────────────────────────────

@pytest.mark.parametrize(
    "header",
    [
        "tökén",                 # latin-1 decoded header with a byte > 0x7F
        "adminÿ",
        "  ",          # line separators
        "日本語",
        "\x80\x81",
    ],
)
def test_a_non_ascii_admin_token_header_is_rejected_not_a_500(monkeypatch, header):
    """`secrets.compare_digest` raises TypeError on a non-ASCII `str`, and Starlette decodes
    header values as latin-1 — so ANY byte >0x7F in `X-Admin-Token` used to crash every route
    in this file with an unauthenticated 500. Comparing bytes keeps it constant-time and makes
    junk simply not match."""
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "s3cret-maintenance-token")
    exc = _denied(_user(), header)
    assert exc.status_code == 403          # rejected, not crashed
    assert exc.detail["error_code"] == "AUTH_FORBIDDEN"


def test_a_non_ascii_token_does_not_crash_when_the_server_token_is_also_non_ascii(monkeypatch):
    """Both sides encode, so a non-ASCII configured secret is comparable too — and an equal
    pair still matches."""
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "tökén-sécret")
    admin._authorize_admin(None, "tökén-sécret")          # must not raise
    assert _denied(_user(), "tökén-wrong").status_code == 403


def test_encoding_cannot_collide_two_different_tokens(monkeypatch):
    """`errors="ignore"` drops unencodable code points. Guard that it cannot make a WRONG
    token equal the real one — surrogates are the only thing utf-8 refuses, and dropping
    them must not leave a matching prefix."""
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "abc")
    for probe in ["ab\ud800c", "\ud800abc\ud800", "abcd", "ab"]:
        if probe.encode("utf-8", "ignore") == b"abc":
            admin._authorize_admin(None, probe)   # a genuine byte-equal match is fine
        else:
            assert _denied(_user(), probe).status_code == 403


def test_authenticated_non_admin_is_403():
    """A valid credential that simply isn't allowed is 403 — clearing the token would be
    wrong, the session is fine."""
    exc = _denied(_user())
    assert exc.status_code == 403
    assert exc.detail["error_code"] == "AUTH_FORBIDDEN"


# ── Token path ────────────────────────────────────────────────────────────────

def test_matching_admin_token_grants_access(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "s3cret-maintenance-token")
    admin._authorize_admin(None, "s3cret-maintenance-token")  # must not raise


def test_wrong_admin_token_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "s3cret-maintenance-token")
    assert _denied(_user(), "not-the-token").status_code == 403


def test_unset_admin_token_cannot_be_matched(monkeypatch):
    """With ADMIN_TOKEN unset, the header path must be closed — not satisfied by sending
    nothing, and not by sending the empty string."""
    monkeypatch.setattr(settings, "ADMIN_TOKEN", None)
    assert _denied(_user(), None).status_code == 403
    assert _denied(_user(), "").status_code == 403
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    assert _denied(_user(), "").status_code == 403


# ── Source guard ──────────────────────────────────────────────────────────────

def test_no_email_allowlist_is_reintroduced():
    """Fails the build if an email comparison comes back as an authorization decision —
    including as a 'fallback so it keeps working'.

    Uses the AST rather than text matching, because the file documents the old mechanism on
    purpose and a grep would either trip on the docstring or have to be loosened until it
    stopped catching anything.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "api" / "v1" / "endpoints" / "admin.py"
    )
    tree = ast.parse(path.read_text())

    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_authorize_admin"
    )
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body  # drop the docstring node

    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert "@" not in sub.value, (
                    f"_authorize_admin contains the literal {sub.value!r} — an email claim "
                    "is not a credential; authorize on users.is_admin instead"
                )
                # The exact shape that shipped: `user.get("email") in _ADMIN_EMAILS`.
                # No "@" and no attribute access, so the two checks around this one both
                # miss it.
                assert sub.value != "email", (
                    "_authorize_admin looks up the 'email' key again — that is the "
                    "escalation this test exists to prevent"
                )
            if isinstance(sub, ast.Attribute):
                assert sub.attr != "email", "_authorize_admin reads .email again"

    # And the module-level allowlist must not come back under any name.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                assert getattr(t, "id", "") != "_ADMIN_EMAILS", "the email allowlist is back"
        if isinstance(node, ast.AnnAssign):
            assert getattr(node.target, "id", "") != "_ADMIN_EMAILS", (
                "the email allowlist is back"
            )
