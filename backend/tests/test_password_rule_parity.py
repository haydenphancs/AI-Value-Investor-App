"""The new-password rule must be the SAME rule in three places — exactly, not roughly.

Supabase enforces it (Auth → Providers → Email: lowercase, uppercase, digits and symbols;
minimum 8; and GoTrue caps a password at 72 UTF-8 BYTES). The backend
(`_validate_password_strength`) and the iOS form (`PasswordRule`) each mirror it. Three copies
of one rule is exactly the shape that drifts, and each way of drifting has a user-visible
failure:

  * iOS looser than the backend  -> the button enables, the request is refused.
    THIS IS WHAT SHIPPED FIRST: the form gated on `count >= 8` while the provider demanded four
    character classes, so `abcdefgh` was submitted, accepted by our own API, and rejected by
    GoTrue at the last moment — for a rule the user was never shown.
  * either one looser than GoTrue -> the request goes out and GoTrue refuses it. On reset- and
    set-password that is not cosmetic: the server runs `verify_otp` FIRST, which spends the
    single-use emailed code, so a refused password burns the code (PW-1). Three recovery emails
    an hour, and recovery locks.
  * either one STRICTER than GoTrue -> a false rejection the user cannot diagnose.

THE DESIGN WAS REVERSED ON PURPOSE (2026-11-26). Both sides used to count ANY non-alphanumeric
character as a symbol and any `isupper()` / `isNumber` as a class — a superset of GoTrue's ASCII
sets, chosen so we could never be stricter than the provider. It made us LOOSER instead: "Blue
Sky 2025" (a space as its only symbol) or "élan2025€É" passed both our layers and failed
GoTrue, after the code was spent. Verified live: GoTrue refuses 'Aa1 bbbbbbbb' as weak. So the
classes are now GoTrue's own sets — A-Z, a-z, 0-9 and its 32-character symbol list — and every
other character is allowed but counts toward no class. The byte ceiling (72) and the
no-leading/trailing-space rule are gated on both sides too.

It shipped a SECOND time on the other new-password forms (2026-11-26 audit, IOS-AUTH-05 /
TG-6): Reset Password and Change Password still gated on `count >= 8`. The rule and its
checklist now live in one shared file (`Views/Molecules/PasswordRequirementsView.swift`) and
every new-password gate is pinned below, brace-bound and comment-stripped.

How parity is proven, not argued: the iOS rule's VALUES (symbols, ranges, min length, byte cap)
are read from the Swift literals, its predicate SHAPES are pinned by scans, and a Python model
built from those pieces must agree with the real backend validator on every password of a
seeded corpus. Every guard is mutation-tested in memory by `_MUTATIONS` (Swift patched through
`Path.read_text`, never on disk; the backend through the module's own binding).
"""

from __future__ import annotations

import pathlib
import random
import re
import string
from pathlib import Path

import pytest

from app.schemas import auth as auth_schema
from app.schemas.auth import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_RULE_TEXT,
    _validate_password_strength,
)

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_SIGN_IN_VIEW = _IOS / "Views/Screens/SignInView.swift"
# The ONE home of `PasswordRule` and `PasswordRequirementsView` (moved out of SignInView, where
# the checklist was `private` and so could not be reused by the reset / change screens).
_RULE_FILE = _IOS / "Views/Molecules/PasswordRequirementsView.swift"
_FORGOT_VIEW = _IOS / "Views/Screens/ForgotPasswordView.swift"
_CHANGE_VIEW = _IOS / "Views/Screens/ChangePasswordView.swift"

#: The contract (round C1): GoTrue's symbol list, 32 characters, the backslash included. It is
#: exactly ASCII punctuation.
CONTRACT_SYMBOLS = "!@#$%^&*()_+-=[]{};':\"|<>?,./`~\\"
CONTRACT_MAX_BYTES = 72

#: Characters a user may type that look like they "count" but must count toward NO class.
_NON_CLASS = (
    " ", "\u00a0", "\u3000", "€", "£", "§", "¿", "¡", "·", "—", "–", "…", "‘", "’", "“", "”",
    "É", "Ü", "Σ", "Ж", "é", "ß", "σ", "ж", "ı", "٣", "５", "²", "½", "Ⅳ", "🙂", "👍🏽", "ﬁ",
    "！", "＃", "＄",
)


def _validator():
    """The backend rule, read through the module at call time so a mutation can replace it."""
    return auth_schema._validate_password_strength


def _accepts(pw: str) -> bool:
    try:
        _validator()(pw)
    except ValueError:
        return False
    return True


def _refusal(pw: str) -> str:
    try:
        _validator()(pw)
    except ValueError as e:
        return str(e)
    raise AssertionError(f"{pw!r} was accepted")


def _code_only(src: str) -> str:
    """`//` and `/* */` comments removed, string literals kept, line count preserved.

    Load-bearing: `PasswordRule`'s doc comment quotes the backend function name, the Supabase
    setting and the word "symbol" repeatedly, and the gates below carry comments naming
    `PasswordRule` next to the code. An un-stripped scan would satisfy every assertion from the
    prose after the code was deleted — including a TRAILING comment
    (`newPassword.count >= 8 // PasswordRule.isSatisfied`), which a whole-line strip misses.

    String-aware so a `//` inside a literal ("https://…") is not taken for a comment.
    Approximate by design (an interpolation holding a quoted `//` can confuse it); it only has
    to be exact on the small declarations the tests below brace-bound.
    """
    out: list[str] = []
    i, n = 0, len(src)
    in_str = in_multi = False
    while i < n:
        c = src[i]
        if in_multi:
            if src.startswith('"""', i):
                in_multi = False
                out.append('"""')
                i += 3
                continue
            out.append(c)
            i += 1
            continue
        if in_str:
            if c == "\\" and i + 1 < n:
                out.append(src[i:i + 2])
                i += 2
                continue
            if c in ('"', "\n"):
                in_str = False
            out.append(c)
            i += 1
            continue
        if src.startswith('"""', i):
            in_multi = True
            out.append('"""')
            i += 3
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            if j == -1:
                break
            i = j  # keep the newline itself
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                break
            out.append("\n" * src.count("\n", i, j))
            i = j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _swift(path: Path = _SIGN_IN_VIEW) -> str:
    if not path.exists():
        pytest.fail(f"missing {path} — re-point this guard, do not delete it")
    return _code_only(path.read_text(encoding="utf-8"))


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


def _swift_unescape(body: str) -> str:
    """A Swift string literal's body as the string it denotes. Refuses an interpolation."""
    assert "\\(" not in body, f"interpolation in a literal this test reads: {body!r}"
    singles = {"\\": "\\", '"': '"', "'": "'", "n": "\n", "t": "\t", "r": "\r", "0": "\0"}

    def one(m: re.Match) -> str:
        esc = m.group(1)
        if esc.startswith("u{"):
            return chr(int(esc[2:-1], 16))
        assert esc in singles, f"unknown Swift escape \\{esc}"
        return singles[esc]

    return re.sub(r"\\(u\{[0-9A-Fa-f]{1,8}\}|.)", one, body)


# ── The iOS rule, read out of the Swift ──────────────────────────────────────────


_PREDICATES = ("hasMinLength", "fitsMaxBytes", "hasNoEdgeWhitespace",
               "hasUppercase", "hasLowercase", "hasDigit", "hasSymbol")


def _rule_block() -> str:
    return _braced(_swift(_RULE_FILE), "enum PasswordRule")


def _func(block: str, name: str) -> str:
    """One `static func <name>(_ p: String) -> Bool { … }` body."""
    return _braced(block, f"static func {name}(_ p: String) -> Bool")


def _ios_values() -> dict:
    block = _rule_block()
    min_len = re.search(r"static let minLength\s*=\s*(\d+)\b", block)
    max_bytes = re.search(r"static let maxBytes\s*=\s*(\d+)\b", block)
    symbols = re.search(r'static let symbols\s*=\s*"((?:[^"\\\n]|\\.)*)"', block)
    assert min_len and max_bytes and symbols, "PasswordRule's minLength / maxBytes / symbols moved — re-derive"
    ranges = {}
    for name in ("asciiUppercase", "asciiLowercase", "asciiDigits"):
        m = re.search(rf'static let {name}: ClosedRange<Unicode\.Scalar> = "(.)"\.\.\."(.)"', block)
        assert m, f"PasswordRule.{name} is no longer a ClosedRange<Unicode.Scalar> literal — re-derive"
        ranges[name] = (m.group(1), m.group(2))
    return {
        "min_len": int(min_len.group(1)),
        "max_bytes": int(max_bytes.group(1)),
        "symbols": _swift_unescape(symbols.group(1)),
        **ranges,
    }


def _ios_model():
    """`PasswordRule.isSatisfied`, as a Python function, from the Swift's values and shapes.

    Each predicate's SHAPE is pinned by `test_each_ios_predicate_has_the_pinned_shape`; the
    VALUES come from the literals; WHICH predicates `isSatisfied` requires is read from its
    body. So a dropped predicate or a widened set changes this model, and the corpus test sees
    it disagree with the backend.
    """
    v = _ios_values()
    sat = _braced(_rule_block(), "static func isSatisfied(_ p: String) -> Bool")
    required = [name for name in _PREDICATES if f"{name}(p)" in sat]

    def in_range(c: str, rng: tuple[str, str]) -> bool:
        return rng[0] <= c <= rng[1]

    checks = {
        "hasMinLength": lambda p: len(p) >= v["min_len"],          # Unicode scalars
        "fitsMaxBytes": lambda p: len(p.encode("utf-8")) <= v["max_bytes"],
        "hasNoEdgeWhitespace": lambda p: not p or (not p[0].isspace() and not p[-1].isspace()),
        "hasUppercase": lambda p: any(in_range(c, v["asciiUppercase"]) for c in p),
        "hasLowercase": lambda p: any(in_range(c, v["asciiLowercase"]) for c in p),
        "hasDigit": lambda p: any(in_range(c, v["asciiDigits"]) for c in p),
        "hasSymbol": lambda p: any(c in v["symbols"] for c in p),
    }
    return lambda p: all(checks[name](p) for name in required)


# ── The backend rule itself ──────────────────────────────────────────────────────


@pytest.mark.parametrize("pw", [
    "Abcdefg1!", "P@ssw0rdd", "aB3$aB3$", "Ünïcodé1!A",
    # Allowed, counting toward nothing: a space inside, an accent, an emoji.
    "Blue Sky 2025!", "élan2025€É!aB", "Abcdefg1!🙂",
    "Abcdefg1\\",  # the backslash is a symbol
])
def test_a_compliant_password_is_accepted(pw):
    assert _validate_password_strength(pw) == pw


@pytest.mark.parametrize("pw,missing", [
    ("abcdefgh", "an uppercase letter"),
    ("ABCDEFG1!", "a lowercase letter"),
    ("Abcdefgh!", "a number"),
    ("Abcdefg1", "a symbol"),
    # The superset rule's false passes, each now refused, naming the class it lacks.
    ("Blue Sky 2025", "a symbol"),          # a space is not a symbol
    ("élan2025€É", "a symbol"),             # neither is €
    ("élan2025!é", "an uppercase letter"),  # É would have been; é is lowercase but not ASCII
    ("ÉLAN2025!", "a lowercase letter"),
    ("Abcdefgh!٣", "a number"),             # an Arabic-Indic three is not 0-9
    ("Abcdefgh!５", "a number"),             # nor is a full-width five
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


@pytest.mark.parametrize("pw,ok", [
    ("Aa1!" + "a" * 68, True),     # 72 bytes: the ceiling itself
    ("Aa1!" + "a" * 69, False),    # 73 bytes
    ("Aa1!" + "é" * 34, True),     # 38 characters, 72 bytes
    ("Aa1!" + "é" * 35, False),    # 39 characters, 74 bytes — short, and still too long
    ("Aa1!" + "🙂" * 17, True),    # 21 characters, 72 bytes
    ("Aa1!" + "🙂" * 18, False),   # 22 characters, 76 bytes
    ("Aa1!" + "x" * 10_000, False),
])
def test_the_ceiling_is_72_utf8_bytes_not_characters(pw, ok):
    if ok:
        assert _validate_password_strength(pw) == pw
    else:
        assert "72 bytes" in _refusal(pw), "the too-long refusal does not say the limit is in bytes"


@pytest.mark.parametrize("pw", [" Abcdefg1!", "Abcdefg1! ", "\tAbcdefg1!", "Abcdefg1!\u00a0"])
def test_a_password_padded_with_whitespace_is_refused(pw):
    assert not _accepts(pw)


def test_we_are_never_stricter_than_the_provider():
    """Anything GoTrue accepts, we must accept: every symbol on its list, alone, is enough."""
    for symbol in CONTRACT_SYMBOLS:
        pw = f"Abcdefg1{symbol}"
        assert _validate_password_strength(pw) == pw, f"falsely rejected symbol {symbol!r}"


def test_the_backend_counts_exactly_the_contract_symbols():
    """Probed through the validator itself, not read off a constant: a character is a symbol to
    the backend iff "Abc<c>defg1" (upper, lower, digit, nothing else) is accepted."""
    probe = [chr(c) for c in range(0x21, 0x7F)] + list(_NON_CLASS)
    counted = {c for c in probe if _accepts(f"Abc{c}defg1")}
    assert counted == set(CONTRACT_SYMBOLS), (
        f"the backend's symbols drifted from GoTrue's list: extra {sorted(counted - set(CONTRACT_SYMBOLS))}, "
        f"missing {sorted(set(CONTRACT_SYMBOLS) - counted)}")
    assert len(CONTRACT_SYMBOLS) == 32 == len(set(CONTRACT_SYMBOLS))
    assert set(CONTRACT_SYMBOLS) == set(string.punctuation), "GoTrue's list is ASCII punctuation"


def test_the_backend_letters_and_digits_are_ascii_only():
    for c in _NON_CLASS:
        assert not _accepts(f"abc{c}defg1!"), f"{c!r} counted as an uppercase letter"
        assert not _accepts(f"ABC{c}DEFG1!"), f"{c!r} counted as a lowercase letter"
        assert not _accepts(f"Abc{c}defgh!"), f"{c!r} counted as a number"
    for c in string.ascii_uppercase:
        assert _accepts(f"abc{c}defg1!")
    for c in string.digits:
        assert _accepts(f"Abc{c}defgh!")


def test_sign_in_is_NOT_subject_to_the_rule():
    """Existing accounts predate it. Enforcing it at sign-in would lock out every user whose
    password does not satisfy it — a hardening change becoming an outage."""
    from app.schemas.auth import SignInRequest

    for pw in ("abcdefgh", "Blue Sky 2025", "Aa1!" + "é" * 40):
        assert SignInRequest(email="a@b.com", password=pw).password == pw


# ── The scanner itself (a vacuous scanner makes every guard below vacuous) ────


def test_the_comment_stripper_removes_trailing_and_block_comments_but_keeps_strings():
    src = (
        'let a = x.count >= 8 // PasswordRule.isSatisfied(x)\n'
        '/* PasswordRule.isSatisfied(y)\n   still a comment */ let b = 1\n'
        'let url = "https://caydexinvest.com" // trailing\n'
        'let s = "a \\" // not a comment"\n'
    )
    code = _code_only(src)
    assert "PasswordRule" not in code, code
    assert 'x.count >= 8' in code and "let b = 1" in code
    assert '"https://caydexinvest.com"' in code and "trailing" not in code
    assert '"a \\" // not a comment"' in code, "a // inside a string literal was stripped"
    assert code.count("\n") == src.count("\n"), "line structure changed"


def test_the_swift_unescaper_reads_the_symbol_literal_correctly():
    assert _swift_unescape('!@#\\"|\\\\') == '!@#"|\\'
    assert _swift_unescape("a\\u{1F642}b") == "a🙂b"
    with pytest.raises(AssertionError, match="interpolation"):
        _swift_unescape("x\\(y)")


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
    block = _rule_block()
    sat = _braced(block, "static func isSatisfied(_ p: String) -> Bool")
    for fn in _PREDICATES:
        assert f"static func {fn}(_ p: String) -> Bool" in block, (
            f"PasswordRule.{fn} is gone — iOS and the backend now disagree")
        assert f"{fn}(p)" in sat, (
            f"PasswordRule.isSatisfied no longer requires {fn} — the button enables for a "
            "password the server refuses")
    assert "||" not in sat, "isSatisfied ORs a predicate — every one is required"


def test_the_two_minimum_lengths_are_the_same_number():
    assert _ios_values()["min_len"] == PASSWORD_MIN_LENGTH, (
        f"iOS requires {_ios_values()['min_len']} characters, the backend requires {PASSWORD_MIN_LENGTH}"
    )


def test_ios_and_backend_count_exactly_the_same_32_symbols():
    """The superset rule (`!$0.isLetter && !$0.isNumber` / `not c.isalnum()`) is gone on both
    sides: a space, "€" or an em dash is no symbol to GoTrue, so it is none to us."""
    ios = _ios_values()["symbols"]
    assert ios == CONTRACT_SYMBOLS, (
        f"iOS PasswordRule.symbols drifted from GoTrue's list: {ios!r} != {CONTRACT_SYMBOLS!r}")
    assert auth_schema.PASSWORD_SYMBOLS == CONTRACT_SYMBOLS, "the backend's PASSWORD_SYMBOLS drifted"


def test_the_byte_ceiling_is_the_same_on_both_sides():
    ios = _ios_values()["max_bytes"]
    assert ios == CONTRACT_MAX_BYTES == auth_schema.PASSWORD_MAX_BYTES, (
        f"iOS caps at {ios} bytes, the backend at {auth_schema.PASSWORD_MAX_BYTES}, GoTrue at 72")
    # And the backend's constant is what its validator applies (not a decoy).
    largest = max(n for n in range(8, 120) if _accepts("Aa1!" + "a" * (n - 4)))
    assert largest == CONTRACT_MAX_BYTES, f"the backend validator's real ceiling is {largest} bytes"


def test_each_ios_predicate_has_the_pinned_shape():
    """The exact-set reading, predicate by predicate. `_ios_model` relies on these shapes."""
    block = _rule_block()
    expected = {
        "hasMinLength": "p.unicodeScalars.count >= minLength",
        "fitsMaxBytes": "p.utf8.count <= maxBytes",
        "hasUppercase": "p.unicodeScalars.contains { asciiUppercase.contains($0) }",
        "hasLowercase": "p.unicodeScalars.contains { asciiLowercase.contains($0) }",
        "hasDigit": "p.unicodeScalars.contains { asciiDigits.contains($0) }",
        "hasSymbol": "p.unicodeScalars.contains { symbolSet.contains($0) }",
    }
    for name, shape in expected.items():
        body = " ".join(_func(block, name).split())
        assert shape in body, f"PasswordRule.{name} changed shape: {body!r} — re-check it against the backend"
    edge = _func(block, "hasNoEdgeWhitespace")
    assert "p.unicodeScalars.first" in edge and "p.unicodeScalars.last" in edge \
        and edge.count("properties.isWhitespace") == 2, (
        "hasNoEdgeWhitespace no longer checks both ends — a padded password reaches a 422")
    assert "private static let symbolSet: Set<Unicode.Scalar> = Set(symbols.unicodeScalars)" in block, (
        "PasswordRule.symbolSet is no longer built from `symbols` alone")
    v = _ios_values()
    assert (v["asciiUppercase"], v["asciiLowercase"], v["asciiDigits"]) == (("A", "Z"), ("a", "z"), ("0", "9")), (
        f"a class range drifted from ASCII: {v}")
    for loose in ("isUppercase", "isLowercase", "isLetter", "isNumber", "isPunctuation", "isSymbol",
                  "isASCII", "p.count"):
        assert loose not in block, f"PasswordRule reads `{loose}` — the classes are GoTrue's ASCII sets only"


def test_ios_and_backend_agree_on_a_corpus():
    """The model of iOS's `isSatisfied` (values from the Swift, shapes pinned above) against the
    real backend validator, password by password: the strongest evidence the two are ONE rule."""
    ios_accepts = _ios_model()
    alphabet = (string.ascii_letters + string.digits + CONTRACT_SYMBOLS + "  "
                + "".join(_NON_CLASS))
    rng = random.Random(20261126)
    corpus = [
        "", "a", "Abcdefg1!", "Blue Sky 2025", "Blue Sky 2025!", "élan2025€É", "Aa1 bbbbbbbb",
        " Abcdefg1!", "Abcdefg1! ", "Abcdefg1!\u00a0", "Aa1!" + "a" * 68, "Aa1!" + "a" * 69,
        "Aa1!" + "é" * 34, "Aa1!" + "é" * 35, "Aa1!🙂🙂🙂🙂", "Aa1!ééé", "Aa1!éééé",
        "ABCDEFG1!", "abcdefg1!", "Abcdefgh!", "Abcdefg1", "Abcdefg1\\", "Abcdefg1\"",
    ]
    for _ in range(4000):
        n = rng.choice((rng.randint(0, 12), rng.randint(6, 40), rng.randint(60, 80)))
        corpus.append("".join(rng.choice(alphabet) for _ in range(n)))
    disagreements = [p for p in corpus if ios_accepts(p) != _accepts(p)]
    assert not disagreements, (
        f"{len(disagreements)} password(s) where iOS and the backend disagree, e.g. "
        f"{disagreements[:3]!r} (iOS {'accepts' if ios_accepts(disagreements[0]) else 'refuses'} the first)")
    accepted = sum(map(_accepts, corpus))
    assert 100 < accepted < len(corpus) - 100, f"corpus is one-sided ({accepted}/{len(corpus)} accepted)"


def test_the_checklist_shows_the_contract_copy_and_the_warning_rows():
    view = _braced(_swift(_RULE_FILE), "struct PasswordRequirementsView")
    items = _braced(view, "private var items: [(label: String, met: Bool)]")
    for row, predicate in (
        ('"\\(PasswordRule.minLength)+ characters"', "hasMinLength"),
        ('"An uppercase letter (A–Z)"', "hasUppercase"),
        ('"A lowercase letter (a–z)"', "hasLowercase"),
        ('"A number (0–9)"', "hasDigit"),
        ('"A symbol such as ! @ # $ — spaces don\'t count"', "hasSymbol"),
    ):
        assert re.search(re.escape(f"({row}, PasswordRule.{predicate}(password))"), items), (
            f"the checklist row {row} is gone or no longer tied to PasswordRule.{predicate}")
    warnings = _braced(view, "private var warnings: [String]")
    assert "if !PasswordRule.fitsMaxBytes(password) { out.append(Self.tooLongWarning) }" in warnings, (
        "the over-72-bytes warning row is not shown when the password is too long — the button "
        "sits disabled with every row green")
    assert "if !PasswordRule.hasNoEdgeWhitespace(password) { out.append(Self.edgeSpaceWarning) }" in warnings
    assert re.search(r'static let tooLongWarning =\s*"Too long: \\\(PasswordRule\.maxBytes\) bytes at most '
                     r'\(emoji and accented letters use more than one\)"', view), "the too-long copy drifted"
    body = _braced(view, "var body: some View")
    assert "ForEach(items" in body and "ForEach(warnings" in body, "a row group is no longer rendered"


def test_the_rule_and_its_checklist_are_shared_not_private():
    """The reset and change screens use both. `private struct PasswordRequirementsView` inside
    SignInView is what kept them on "At least 8 characters"."""
    src = _swift(_RULE_FILE)
    for decl in ("enum PasswordRule", "struct PasswordRequirementsView"):
        assert re.search(rf"(?<![\w.]){re.escape(decl)}\b", src), f"{decl} is not in {_RULE_FILE}"
        assert not re.search(rf"\b(?:private|fileprivate)\s+{re.escape(decl)}\b", src), (
            f"`{decl}` is private again — the other new-password screens cannot use it"
        )
    view = _braced(src, "struct PasswordRequirementsView")
    assert re.search(r"\blet password\s*:\s*String\b", view) and "init(password: String)" in view, (
        "PasswordRequirementsView no longer takes `password:` — every call site breaks"
    )
    assert "static func isSatisfied(_ p: String) -> Bool" in _rule_block(), (
        "PasswordRule.isSatisfied(_:) changed signature — every gate calls it")


def test_there_is_exactly_one_copy_of_the_rule_in_the_app():
    """A second definition (say, a `private enum PasswordRule` re-added to one screen, which
    shadows the shared one inside that file) is a copy that drifts silently."""
    found: dict[str, list[str]] = {"enum PasswordRule": [], "struct PasswordRequirementsView": []}
    for path in _IOS.rglob("*.swift"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        if "PasswordRule" not in raw and "PasswordRequirementsView" not in raw:
            continue  # cheap pre-filter: the tree holds hundreds of files, two of them huge
        code = _code_only(raw)
        for decl in found:
            if re.search(rf"\b{re.escape(decl)}\b", code):
                found[decl].append(str(path.relative_to(_IOS)))
    rel = str(_RULE_FILE.relative_to(_IOS))
    for decl, paths in found.items():
        assert paths == [rel], f"`{decl}` must be declared once, in {rel}; found in {paths}"


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


# ── Every OTHER form that sets a new password gates on the same rule ──────────
#
# (path, gate header, tokens proving the block is the real submit gate)
_NEW_PASSWORD_GATES = [
    pytest.param(_FORGOT_VIEW, "private var canReset: Bool", ("normalizedCode",),
                 id="reset-password"),
    pytest.param(_CHANGE_VIEW, "private var canSubmitChange: Bool",
                 ("currentPassword", "confirmPassword"), id="change-password"),
    pytest.param(_CHANGE_VIEW, "private var canSubmitSet: Bool",
                 ("normalizedCode", "confirmPassword"), id="set-password"),
]


@pytest.mark.parametrize("path,header,anchors", _NEW_PASSWORD_GATES)
def test_every_new_password_gate_uses_the_full_rule(path, header, anchors):
    block = _braced(_swift(path), header)
    for anchor in anchors:
        assert anchor in block, f"scan drifted — {header} in {path.name} is not the submit gate"
    assert "PasswordRule.isSatisfied(newPassword)" in block, (
        f"{path.name} `{header}` no longer gates on the full password rule — its button enables "
        f"for a password the server refuses with a rule the screen never showed"
    )
    assert not re.search(r"newPassword\.count\s*>=", block), (
        f"{path.name} `{header}` is back to a length-only check on the new password"
    )


# (path, the form's declaration) — the checklist must be ON the form the gate belongs to.
_NEW_PASSWORD_FORMS = [
    pytest.param(_FORGOT_VIEW, "private var enterCodeForm", id="reset-password"),
    pytest.param(_CHANGE_VIEW, "private var form:", id="change-password"),
    pytest.param(_CHANGE_VIEW, "private var enterCodeStep", id="set-password"),
]


@pytest.mark.parametrize("path,header", _NEW_PASSWORD_FORMS)
def test_every_new_password_form_shows_the_checklist(path, header):
    block = _braced(_swift(path), header)
    assert "PasswordRequirementsView(password: newPassword)" in block, (
        f"{path.name} `{header}` hides the rule: the user meets it only as an error"
    )


@pytest.mark.parametrize("path", [_FORGOT_VIEW, _CHANGE_VIEW], ids=lambda p: p.stem)
def test_no_new_password_screen_states_a_subset_of_the_rule(path):
    """"At least 8 characters" was the whole visible rule on both screens."""
    code = _swift(path)
    assert "At least 8 characters" not in code, (
        f"{path.name} tells the user the rule is only a length"
    )


@pytest.mark.parametrize("path", [_FORGOT_VIEW, _CHANGE_VIEW], ids=lambda p: p.stem)
def test_code_copy_names_no_digit_count(path):
    """The OTP length is one project-wide Supabase setting (8 today; the schema takes 6-12).
    "a 6-digit code" told users to expect fewer digits than arrived (SOC-10, AUTH-BE-12)."""
    code = _swift(path)
    hit = re.search(r"\b\d+\s*-?\s*digit", code, re.IGNORECASE)
    assert not hit, f"{path.name} names a code length in its copy: {hit.group(0)!r}"


def test_the_rule_text_names_every_class():
    """`PASSWORD_RULE_TEXT` is the one human sentence; it must not describe a subset."""
    for word in ("8", "uppercase", "lowercase", "number", "symbol"):
        assert word in PASSWORD_RULE_TEXT.lower(), f"{word!r} missing from PASSWORD_RULE_TEXT"


# ── Mutations — every guard above goes red on the regression it names ────────

_T_SYMBOLS = test_ios_and_backend_count_exactly_the_same_32_symbols
_T_SHAPES = test_each_ios_predicate_has_the_pinned_shape
_T_BYTES = test_the_byte_ceiling_is_the_same_on_both_sides
_T_CORPUS = test_ios_and_backend_agree_on_a_corpus
_T_CLASSES = test_the_ios_rule_checks_every_class_the_backend_does
_T_CHECKLIST = test_the_checklist_shows_the_contract_copy_and_the_warning_rows
_T_SHARED = test_the_rule_and_its_checklist_are_shared_not_private
_T_BACKEND_SYMBOLS = test_the_backend_counts_exactly_the_contract_symbols
_T_BACKEND_ASCII = test_the_backend_letters_and_digits_are_ascii_only

_SYMBOLS_LINE = 'static let symbols = "!@#$%^&*()_+-=[]{};\':\\"|<>?,./`~\\\\"'

# (name, ((old, new), ...), guard, message the guard must fail with) — Swift, in memory.
_SWIFT_MUTATIONS = [
    ("symbol-set-gains-a-space", ((_SYMBOLS_LINE, _SYMBOLS_LINE[:-1] + ' "'),),
     _T_SYMBOLS, "iOS PasswordRule.symbols drifted"),
    ("symbol-set-loses-the-backslash", ((_SYMBOLS_LINE, _SYMBOLS_LINE.replace('`~\\\\"', '`~"')),),
     _T_SYMBOLS, "iOS PasswordRule.symbols drifted"),
    ("symbol-back-to-superset",
     (("p.unicodeScalars.contains { symbolSet.contains($0) }",
       "p.contains { !$0.isLetter && !$0.isNumber }"),),
     _T_SHAPES, "PasswordRule.hasSymbol changed shape"),
    ("uppercase-any-script",
     (("p.unicodeScalars.contains { asciiUppercase.contains($0) }", "p.contains { $0.isUppercase }"),),
     _T_SHAPES, "PasswordRule.hasUppercase changed shape"),
    ("digit-range-widened",
     (('asciiDigits: ClosedRange<Unicode.Scalar> = "0"..."9"', 'asciiDigits: ClosedRange<Unicode.Scalar> = "0"..."z"'),),
     _T_SHAPES, "a class range drifted from ASCII"),
    ("min-length-in-graphemes",
     (("p.unicodeScalars.count >= minLength", "p.count >= minLength"),),
     _T_SHAPES, "PasswordRule.hasMinLength changed shape"),
    ("byte-cap-128", (("static let maxBytes = 72", "static let maxBytes = 128"),),
     _T_BYTES, "iOS caps at 128 bytes"),
    ("byte-cap-not-required",
     (("hasMinLength(p) && fitsMaxBytes(p) && hasNoEdgeWhitespace(p)", "hasMinLength(p) && hasNoEdgeWhitespace(p)"),),
     _T_CLASSES, "isSatisfied no longer requires fitsMaxBytes"),
    ("byte-cap-not-required-corpus",
     (("hasMinLength(p) && fitsMaxBytes(p) && hasNoEdgeWhitespace(p)", "hasMinLength(p) && hasNoEdgeWhitespace(p)"),),
     _T_CORPUS, "iOS and the backend disagree"),
    ("edge-space-not-required-corpus",
     (("hasMinLength(p) && fitsMaxBytes(p) && hasNoEdgeWhitespace(p)", "hasMinLength(p) && fitsMaxBytes(p)"),),
     _T_CORPUS, "iOS and the backend disagree"),
    ("symbol-set-widened-corpus", ((_SYMBOLS_LINE, _SYMBOLS_LINE[:-1] + '€ "'),),
     _T_CORPUS, "iOS and the backend disagree"),
    ("or-in-is-satisfied",
     (("&& hasUppercase(p) && hasLowercase(p)", "&& hasUppercase(p) || hasLowercase(p)"),),
     _T_CLASSES, "isSatisfied ORs a predicate"),
    ("checklist-old-symbol-copy",
     (('"A symbol such as ! @ # $ — spaces don\'t count"', '"A symbol"'),),
     _T_CHECKLIST, "the checklist row"),
    ("too-long-row-never-shown",
     (("if !PasswordRule.fitsMaxBytes(password) { out.append(Self.tooLongWarning) }",
       "if false { out.append(Self.tooLongWarning) }"),),
     _T_CHECKLIST, "the over-72-bytes warning row is not shown"),
    ("is-satisfied-renamed",
     (("static func isSatisfied(_ p: String) -> Bool", "static func isSatisfied(password p: String) -> Bool"),),
     _T_SHARED, "PasswordRule.isSatisfied(_:) changed signature"),
]


@pytest.mark.parametrize("name,edits,guard,message", _SWIFT_MUTATIONS, ids=[m[0] for m in _SWIFT_MUTATIONS])
def test_each_swift_mutation_is_killed(monkeypatch, name, edits, guard, message):
    """Patched in memory only — other sessions read these Swift files concurrently."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(_RULE_FILE, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert old and mutated.count(old) == 1, (
            f"{name}: mutation anchor {old[:70]!r} occurs {mutated.count(old)}x — re-derive it")
        mutated = mutated.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == _RULE_FILE:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def _old_superset_rule(value: str) -> str:
    """The backend rule before 2026-11-26: any non-alphanumeric a symbol, any isupper() a capital,
    128 characters the ceiling. Kept only to prove the guards reject it."""
    if len(value) < 8 or len(value) > 128 or value.strip() != value:
        raise ValueError("length / padding")
    if not (any(c.isupper() for c in value) and any(c.islower() for c in value)
            and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value)):
        raise ValueError("Password must include a symbol.")
    return value


def _no_byte_cap(value: str) -> str:
    """Today's rule minus the 72-byte ceiling."""
    present = set(value)
    if (len(value) >= 8 and value.strip() == value
            and present & set(string.ascii_uppercase) and present & set(string.ascii_lowercase)
            and present & set(string.digits) and present & set(CONTRACT_SYMBOLS)):
        return value
    raise ValueError("refused")


_BACKEND_MUTATIONS = [
    ("backend-superset-symbols", _old_superset_rule, _T_BACKEND_SYMBOLS, "the backend's symbols drifted"),
    ("backend-superset-letters", _old_superset_rule, _T_BACKEND_ASCII, "counted as an uppercase letter"),
    ("backend-superset-corpus", _old_superset_rule, _T_CORPUS, "iOS and the backend disagree"),
    ("backend-no-byte-cap", _no_byte_cap, _T_BYTES, "the backend validator's real ceiling"),
]


@pytest.mark.parametrize("name,rule,guard,message", _BACKEND_MUTATIONS, ids=[m[0] for m in _BACKEND_MUTATIONS])
def test_each_backend_mutation_is_killed(monkeypatch, name, rule, guard, message):
    monkeypatch.setattr(auth_schema, "_validate_password_strength", rule)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def test_the_mutation_tables_cover_every_parity_guard():
    covered = {m[2] for m in _SWIFT_MUTATIONS} | {m[2] for m in _BACKEND_MUTATIONS}
    for guard in (_T_SYMBOLS, _T_SHAPES, _T_BYTES, _T_CORPUS, _T_CLASSES, _T_CHECKLIST, _T_SHARED,
                  _T_BACKEND_SYMBOLS, _T_BACKEND_ASCII):
        assert guard in covered, f"{guard.__name__} has no mutation"
