"""The sign-up password rule must be the SAME rule in three places.

Supabase enforces it (Auth → Providers → Email: lowercase, uppercase, digits and symbols;
minimum 8). The backend and the iOS form each mirror it. Three copies of one rule is exactly the
shape that drifts, and each way of drifting has a distinct, user-visible failure:

  * iOS looser than the backend  -> the Create Account button enables, the request is refused.
    THIS IS WHAT SHIPPED: the form gated on `count >= 8` while the provider demanded four
    character classes, so `abcdefgh` was submitted, accepted by our own API, and rejected by
    GoTrue at the last moment — for a rule the user was never shown.
  * backend looser than Supabase -> we accept a password the provider refuses, which is the
    same bug one layer down (now at least legible, as AUTH_PASSWORD_REJECTED).
  * either one STRICTER than Supabase -> a false rejection the user cannot diagnose, because
    our message names a rule their password appears to satisfy.

The third is why "symbol" is any NON-ALPHANUMERIC character on both sides: a superset of
GoTrue's own symbol list can never reject something GoTrue would have accepted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.auth import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_RULE_TEXT,
    _validate_password_strength,
)

_SIGN_IN_VIEW = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Views/Screens/SignInView.swift"
)


def _code_only(src: str) -> str:
    """Whole-line `//` comments blanked.

    Load-bearing: `PasswordRule`'s doc comment quotes the backend function name, the Supabase
    setting and the word "symbol" repeatedly. An un-stripped scan would satisfy every assertion
    below from the prose after the code was deleted.
    """
    return "\n".join("" if l.strip().startswith("//") else l for l in src.splitlines())


def _swift() -> str:
    if not _SIGN_IN_VIEW.exists():
        pytest.fail(f"missing {_SIGN_IN_VIEW}")
    return _code_only(_SIGN_IN_VIEW.read_text(encoding="utf-8"))


def _braced(src: str, header: str) -> str:
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


# ── The backend rule itself ───────────────────────────────────────────────────


@pytest.mark.parametrize("pw", ["Abcdefg1!", "P@ssw0rdd", "aB3$aB3$", "Ünïcodé1!"])
def test_a_compliant_password_is_accepted(pw):
    assert _validate_password_strength(pw) == pw


@pytest.mark.parametrize("pw,missing", [
    ("abcdefgh", "an uppercase letter"),
    ("ABCDEFG1!", "a lowercase letter"),
    ("Abcdefgh!", "a number"),
    ("Abcdefg1", "a symbol"),
])
def test_each_missing_class_is_named(pw, missing):
    with pytest.raises(ValueError) as e:
        _validate_password_strength(pw)
    assert missing in str(e.value)


def test_all_missing_classes_are_reported_at_once():
    """One round trip, not four. Each retry also costs a registration rate-limit slot."""
    with pytest.raises(ValueError) as e:
        _validate_password_strength("abcdefgh")
    msg = str(e.value)
    for expected in ("an uppercase letter", "a number", "a symbol"):
        assert expected in msg, f"{expected!r} not named in {msg!r}"


def test_length_is_still_enforced_and_reported_first():
    with pytest.raises(ValueError, match="at least"):
        _validate_password_strength("Ab1!")


def test_we_are_never_stricter_than_the_provider():
    """Anything GoTrue accepts, we must accept.

    GoTrue checks membership in ASCII sets: a-z, A-Z, 0-9, and a punctuation list. Every
    password satisfying that has an ASCII uppercase (so `.isupper()`), an ASCII lowercase, a
    digit, and a non-alphanumeric — so it satisfies us too. Sampled across the punctuation
    list rather than argued only in a comment.
    """
    for symbol in "!@#$%^&*()_+-=[]{};':\"|<>?,./`~\\":
        pw = f"Abcdefg1{symbol}"
        assert _validate_password_strength(pw) == pw, f"falsely rejected symbol {symbol!r}"


def test_sign_in_is_NOT_subject_to_the_rule():
    """Existing accounts predate it. Enforcing it at sign-in would lock out every user whose
    password does not satisfy it — a hardening change becoming an outage."""
    from app.schemas.auth import SignInRequest

    req = SignInRequest(email="a@b.com", password="abcdefgh")
    assert req.password == "abcdefgh"


# ── iOS mirrors the same rule ─────────────────────────────────────────────────


def test_the_ios_form_gates_on_the_full_rule_not_just_length():
    """The actual regression. `canSubmit` enabled the button on `count >= 8` alone."""
    block = _braced(_swift(), "private var canSubmit: Bool")
    assert "PasswordRule.isSatisfied" in block, (
        "the sign-up button no longer gates on the full password rule — it will enable for a "
        "password the identity provider refuses"
    )
    # Anti-vacuity: prove this is the real gate.
    assert "emailOK" in block and "nameOK" in block, "scan drifted — not the submit gate"


def test_the_ios_rule_checks_every_class_the_backend_does():
    block = _braced(_swift(), "enum PasswordRule")
    for fn in ("hasMinLength", "hasUppercase", "hasLowercase", "hasDigit", "hasSymbol"):
        assert fn in block, f"PasswordRule.{fn} is gone — iOS and the backend now disagree"
    assert "isSatisfied" in block


def test_the_two_minimum_lengths_are_the_same_number():
    block = _braced(_swift(), "enum PasswordRule")
    match = re.search(r"static let minLength\s*=\s*(\d+)", block)
    assert match, "PasswordRule.minLength is gone"
    assert int(match.group(1)) == PASSWORD_MIN_LENGTH, (
        f"iOS requires {match.group(1)} characters, the backend requires {PASSWORD_MIN_LENGTH}"
    )


def test_ios_defines_symbol_the_same_permissive_way():
    """`!isLetter && !isNumber` on Swift, `not c.isalnum()` on Python. A NARROWER definition on
    either side would falsely reject a password the provider accepts."""
    block = _braced(_swift(), "static func hasSymbol")
    assert "!$0.isLetter" in block and "!$0.isNumber" in block, (
        f"hasSymbol changed shape: {block.strip()!r} — re-check it against the backend"
    )


def test_the_requirements_are_shown_on_sign_up_only():
    """On sign-IN the rules are irrelevant and actively misleading: a pre-existing account's
    password need not satisfy them, and showing the list would imply it is wrong."""
    src = _swift()
    assert "PasswordRequirementsView(password: password)" in src, (
        "the requirements list is not rendered — the user is back to discovering the rule by "
        "failing"
    )
    idx = src.index("PasswordRequirementsView(password: password)")
    assert "mode == .signUp" in src[max(0, idx - 200):idx], (
        "the requirements list is not gated to sign-up mode"
    )


def test_the_rule_text_names_every_class():
    """`PASSWORD_RULE_TEXT` is the one human sentence; it must not describe a subset."""
    for word in ("8", "uppercase", "lowercase", "number", "symbol"):
        assert word in PASSWORD_RULE_TEXT.lower(), f"{word!r} missing from PASSWORD_RULE_TEXT"
