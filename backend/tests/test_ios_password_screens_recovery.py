"""Source-scan guards for the password-recovery screens (ForgotPasswordView, ChangePasswordView).

Pins the 2026-11-26 sign-in hardening (review findings PW-2..PW-6, contract C2):
- a code the server already SPENT (AUTH_CODE_USED, AUTH_PASSWORD_REJECTED after verify) is cleared
  from the field and the soft send-wait is lifted, so the user asks for a new one instead of
  resubmitting a dead code;
- both send paths honour a per-address wait, and a 429 starts an upper-bound wait;
- the resend copy never claims a send happened (the backend answers the same 200 either way);
- the hidden AutoFill username field is out of the accessibility tree;
- the show/hide eye is a 44x44 target and keeps focus.

Comment-stripped and brace-bounded (testing.md §3), and every guard is mutation-tested below: each
mutation breaks the real source IN MEMORY and must make a check fail.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
FORGOT = _ROOT / "frontend/ios/ios/Views/Screens/ForgotPasswordView.swift"
CHANGE = _ROOT / "frontend/ios/ios/Views/Screens/ChangePasswordView.swift"


def _sources():
    if not FORGOT.exists() or not CHANGE.exists():
        pytest.skip("iOS sources not present")
    return FORGOT.read_text(encoding="utf-8"), CHANGE.read_text(encoding="utf-8")


def strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        # drop // comments outside string literals (approximate: these files have no "//" in strings)
        in_str = False
        cut = len(line)
        i = 0
        while i < len(line):
            c = line[i]
            if c == '"' and (i == 0 or line[i - 1] != "\\"):
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def braced(src: str, header: str) -> str:
    at = src.index(header)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced")


def case_arm(block: str, label: str) -> str:
    """The text of `case "<label>":` up to the next `case`/`default:`."""
    m = re.search(r'case\s+"' + re.escape(label) + r'"\s*:(.*?)(?=\bcase\s+"|\bdefault\s*:)', block, re.S)
    assert m, f"no arm for {label}"
    return m.group(1)


def checks(forgot: str, change: str) -> list[str]:
    fails = []

    def need(cond, msg):
        if not cond:
            fails.append(msg)

    f, c = strip(forgot), strip(change)

    # C2 / PW-4: post-verify codes clear the code field and lift the soft cooldown.
    for src, fn, name in ((f, "private func submitReset", "forgot"), (c, "private func submitSet", "change")):
        body = braced(src, fn)
        for code in ("AUTH_CODE_USED", "AUTH_PASSWORD_REJECTED"):
            try:
                arm = case_arm(body, code)
            except AssertionError as e:
                need(False, f"{name}: {e}")
                continue
            need(re.search(r'\bcode\s*=\s*""', arm), f"{name}: {code} does not clear the code")
            need("releaseSendCooldownAfterSpentCode(" in arm, f"{name}: {code} keeps the send wait")
        try:
            arm = case_arm(body, "AUTH_CODE_USED")
            need("newPassword = \"\"" not in arm, f"{name}: AUTH_CODE_USED throws away a fine password")
        except AssertionError:
            pass

    # PW-3: both send paths honour the per-address wait; a 429 starts an upper-bound wait.
    for src, fn, name in ((f, "private func sendCode", "forgot"), (c, "private func requestCode", "change")):
        body = braced(src, fn)
        guard = body[: body.index("else")]
        need("secondsUntilSend(" in guard, f"{name}: send guard ignores the cooldown")
        catch = body[body.index("} catch {"):]
        need(re.search(r"case\s+\.rateLimited\(let\s+\w+\)\?\s*=\s*error\s+as\?\s+APIError", catch),
             f"{name}: 429 not read")
        need(re.search(r"startSendCooldown\((?:[^()]|\([^()]*\))*isUpperBound:\s*true", catch, re.S),
             f"{name}: 429 starts no upper-bound wait")
        need(re.search(r'\bcode\s*=\s*""', catch) is None, f"{name}: a failed send clears the code")
    for src, name in ((f, "forgot"), (c, "change")):
        btn = braced(src, "private var sendCodeButton")
        need(re.search(r"enabled:[^\n]*wait\s*==\s*0", btn), f"{name}: step-1 Send ignores the wait")
        need("TimelineView" in btn, f"{name}: step-1 wait never refreshes")
        key = braced(src, "private static func cooldownKey")
        need(".lowercased()" in key and "trimmingCharacters" in key, f"{name}: key differs from backend's")
        clamp = braced(src, "private static func rateLimitWait")
        need("min(" in clamp and "max(" in clamp, f"{name}: Retry-After not clamped")
        rel = braced(src, "private func releaseSendCooldownAfterSpentCode")
        need("isUpperBound == false" in rel, f"{name}: release would lift a 429's wait")
        start = braced(src, "private func startSendCooldown")
        need(re.search(r"running\.until\s*>=\s*until", start), f"{name}: a cooldown can be shortened")

    # PW-5: hedged resend copy.
    for src, name in ((f, "forgot"), (c, "change")):
        for bad in ("no longer work", "We've sent a new code", "on its way", "Replaces the code you have now"):
            need(bad not in src, f"{name}: unhedged resend copy {bad!r}")
        need("If a new code was sent to" in src, f"{name}: hedged notice missing")

    # PW-2: the username field is out of the a11y tree; the row reads as static text.
    for src, name in ((f, "forgot"), (c, "change")):
        block = braced(src, "private var accountUsernameField")
        tf = block[block.index("TextField("):]
        tf = tf[: tf.index("}")]
        need(".textContentType(.username)" in tf, f"{name}: scan drifted (no username field)")
        need(".accessibilityHidden(true)" in tf, f"{name}: username field visible to VoiceOver")
        tail = block[block.index("TextField("):]
        need(".accessibilityElement(children: .ignore)" in tail, f"{name}: row not one element")
        need('.accessibilityLabel("Account email")' in tail and ".accessibilityValue(" in tail,
             f"{name}: address not readable to VoiceOver")

    # PW-6: 44x44 eye with a content shape; focus re-asserted on the next run loop.
    inp = braced(f, "private var newPasswordInput")
    btn = inp[inp.index("Button {"):]
    need(".frame(minWidth: 44, minHeight: 44)" in btn and ".contentShape(Rectangle())" in btn,
         "forgot: eye under 44x44")
    need("labeled(\"New password\")" not in braced(f, "private var enterCodeForm"),
         "forgot: eye back inside labeled() padding")
    tog = braced(f, "private func toggleNewPasswordVisibility")
    need(re.search(r"focusedField\s*=\s*nil", tog), "forgot: focus not cleared before the swap")
    need(re.search(r"DispatchQueue\.main\.async\s*\{\s*focusedField\s*=\s*\.newPassword", tog),
         "forgot: focus not re-asserted next run loop")
    return fails


MUTATIONS = [
    ("forgot", 'case "AUTH_CODE_USED":', 'case "AUTH_CODE_USEDX":'),
    ("change", 'case "AUTH_CODE_USED":', 'case "AUTH_CODE_USEDX":'),
    ("forgot", 'code = ""\n                    releaseSendCooldownAfterSpentCode(for: address)\n                case "AUTH_PASSWORD_REJECTED"',
     'releaseSendCooldownAfterSpentCode(for: address)\n                case "AUTH_PASSWORD_REJECTED"'),
    ("forgot", "guard !isSubmitting, !isResending, emailLooksValid,\n              secondsUntilSend(to: trimmedEmail, at: Date()) == 0 else",
     "guard !isSubmitting, !isResending, emailLooksValid else"),
    ("change", "isUpperBound: true\n                    )", "isUpperBound: false\n                    )"),
    ("forgot", "enabled: emailLooksValid && wait == 0", "enabled: emailLooksValid"),
    ("change", "enabled: !accountEmail.isEmpty && wait == 0", "enabled: !accountEmail.isEmpty"),
    ("forgot", "if sendCooldowns[key]?.isUpperBound == false { sendCooldowns[key] = nil }", "sendCooldowns[key] = nil"),
    ("change", "running.until >= until", "false"),
    ("forgot", "min(max(retryAfter, sendCooldownSeconds), rateLimitWaitCapSeconds)", "retryAfter"),
    ("change", "If a new code was sent to", "We've sent a new code to"),
    ("forgot", "Asks for a new code. Earlier codes stop working once a new one is sent.", "Replaces the code you have now"),
    ("forgot", "                .allowsHitTesting(false)\n                .accessibilityHidden(true)", "                .allowsHitTesting(false)"),
    ("change", "            .accessibilityValue(accountEmail)\n", ""),
    ("forgot", ".frame(minWidth: 44, minHeight: 44)", ""),
    ("forgot", "        DispatchQueue.main.async {\n            focusedField = .newPassword\n        }", "        focusedField = .newPassword"),
    ("forgot", "if wasFocused { focusedField = nil }", ""),
    ("forgot", ".lowercased()", ""),
    # comment-only edits must NOT rescue a broken guard: break the code, leave prose naming the token
    ("change", "            .accessibilityElement(children: .ignore)\n", "            // .accessibilityElement(children: .ignore)\n"),
]


def test_the_recovery_screens_pass_every_guard():
    forgot, change = _sources()
    assert checks(forgot, change) == []


@pytest.mark.parametrize("which,old,new", MUTATIONS, ids=[f"{w}:{o.strip()[:40]}" for w, o, _ in MUTATIONS])
def test_each_guard_catches_its_mutation(which, old, new):
    forgot, change = _sources()
    base = checks(forgot, change)
    target = forgot if which == "forgot" else change
    assert target.count(old) >= 1, f"mutation anchor drifted ({which}): {old[:60]!r}"
    mutated = target.replace(old, new, 1)
    res = checks(mutated, change) if which == "forgot" else checks(forgot, mutated)
    assert [r for r in res if r not in base], f"mutation not caught: {old[:60]!r}"
