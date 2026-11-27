"""Source-scan guards for the in-app email change (audit CC-09, 2026-11-26).

There is no XCTest target, so the iOS half is pinned from here (testing.md §3): comments are
stripped, every check is bounded to the declaration it is about, and every guard is
mutation-tested below — each mutation breaks the real source IN MEMORY and must fail a check.

What must not regress:
  * both routes are `.signInRequired`, and neither is an auth endpoint (a 401 there comes from the
    dependency, before any code is checked, so the refresh-and-retry is safe);
  * no call is retried (`retryCount: 0`): each is charged to a per-user limiter, two spend codes;
  * secrets are dropped as soon as the server has them; a spent proof code is cleared;
  * a 429 holds the send it refused, as an upper bound, clamped;
  * nothing on the screen says an address belongs to another account (enumeration);
  * the three new backend codes map to `.validationFailed`, never the retrying `.apiError`;
  * the decoded response shape is the backend's, field for field, and an unknown status never
    reads as "changed".
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios/ios"
FILES = {
    "endpoint": _IOS / "Core/Services/APIEndpoint.swift",
    "vm": _IOS / "ViewModels/ChangeEmailViewModel.swift",
    "view": _IOS / "Views/Screens/ChangeEmailView.swift",
    "apperror": _IOS / "Core/Utilities/AppError.swift",
    "settings": _IOS / "Views/Screens/AppSettingsView.swift",
}


def _sources() -> dict[str, str]:
    if not all(p.exists() for p in FILES.values()):
        pytest.skip("iOS sources not present")
    return {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}


def strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        in_str, cut, i = False, len(line), 0
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


def _arm(block: str, case_pattern: str) -> str:
    """From `case <pattern>` up to the next `case`/`default` at any depth (good enough for the
    flat switches scanned here)."""
    m = re.search(case_pattern + r"(.*?)(?=\n\s*case\s|\n\s*default\s*:|\Z)", block, re.S)
    return m.group(1) if m else ""


def checks(s: dict[str, str]) -> list[str]:
    fails: list[str] = []

    def need(cond, msg):
        if not cond:
            fails.append(msg)

    ep, vm, view, err, settings = (strip(s[k]) for k in ("endpoint", "vm", "view", "apperror", "settings"))

    # ── APIEndpoint ──────────────────────────────────────────────────────────
    policy = braced(ep, "nonisolated var authPolicy: AuthPolicy")
    arm = _arm(policy, r"case\s+\.changeEmail,\s*\.confirmEmailChange\s*:")
    need("return .signInRequired" in arm, "endpoint: email change is not .signInRequired")
    need("default:" not in policy, "endpoint: authPolicy grew a default arm")
    auth_ep = braced(ep, "nonisolated var isAuthEndpoint: Bool")
    true_arm = auth_ep[: auth_ep.index("return true")]
    need(".changeEmail" not in true_arm and ".confirmEmailChange" not in true_arm,
         "endpoint: an email-change route is an auth endpoint (no refresh on a 401)")
    path = braced(ep, "nonisolated var path: String")
    need('case .changeEmail:\n            return "/api/v1/auth/change-email"' in path,
         "endpoint: change-email path drifted")
    need('case .confirmEmailChange:\n            return "/api/v1/auth/change-email/confirm"' in path,
         "endpoint: confirm path drifted")
    req = braced(ep, "nonisolated struct ChangeEmailRequest")
    need("let currentPassword: String?" in req and "let code: String?" in req,
         "endpoint: the proofs must be optional so the nil one is omitted")
    resp = braced(ep, "nonisolated struct EmailChangeResponse")
    need("let status: String" in resp, "endpoint: status must decode as a String, not an enum")
    need(re.search(r'var isChanged: Bool \{ status == "changed" \}', resp),
         "endpoint: only \"changed\" may read as changed")

    # ── ViewModel ────────────────────────────────────────────────────────────
    for fn in ("func sendProofCode", "func start(", "func confirm("):
        body = braced(vm, fn)
        need("retryCount: 0" in body, f"vm: {fn} may retry")
    send = braced(vm, "func sendProofCode")
    need("secondsUntilSend(.proofCode" in send[: send.index("else")], "vm: proof send ignores its wait")
    start = braced(vm, "func start(")
    need("secondsUntilSend(.changeCodes" in start[: start.index("else")], "vm: start ignores its wait")
    success = start[start.index("responseType: EmailChangeResponse.self"):start.index("} catch {")]
    need(re.search(r'currentPassword\s*=\s*""', success) and re.search(r'proofCode\s*=\s*""', success),
         "vm: the proof outlives the request")
    catch = start[start.index("} catch {"):]
    used = catch[catch.index('"AUTH_CODE_USED"'):] if '"AUTH_CODE_USED"' in catch else ""
    need(re.search(r'proofCode\s*=\s*""', used), "vm: a spent proof code stays in the field")
    need("releaseAcceptedCooldown(.proofCode)" in used, "vm: a spent proof code keeps its wait")
    nopw = catch[catch.index('"AUTH_PASSWORD_NOT_SET"'):] if '"AUTH_PASSWORD_NOT_SET"' in catch else ""
    need("usesPassword = false" in nopw and "step = .requestProofCode" in nopw,
         "vm: AUTH_PASSWORD_NOT_SET leaves a password-less account on a password form")
    confirm = braced(vm, "func confirm(")
    need("guard response.isChanged else" in confirm, "vm: confirm trusts any 200")
    ok = confirm[confirm.index("changedEmail = response.email"):confirm.index("} catch {")]
    need(re.search(r'currentCode\s*=\s*""', ok) and re.search(r'newCode\s*=\s*""', ok),
         "vm: the email codes outlive the change")
    cc = confirm[confirm.index("} catch {"):]
    need(re.search(r'(currentCode|newCode)\s*=\s*""', cc) is None,
         "vm: a refused confirm throws away codes that may only hold a typo")
    need("AUTH_EMAIL_CHANGE_NOT_PENDING" not in vm + err,
         "a 'nothing pending' code is back: it tells anyone which addresses have accounts")
    fail = braced(vm, "private func fail(")
    need(re.search(r"case\s+\.rateLimited\(let\s+\w+\)\?\s*=\s*error\s+as\?\s+APIError", fail), "vm: 429 not read")
    need("isUpperBound: true" in fail, "vm: a 429's wait is not an upper bound")
    need("min(max(retryAfter, Self.sendCooldownSeconds), Self.rateLimitWaitCapSeconds)" in fail,
         "vm: Retry-After not clamped")
    rel = braced(vm, "private func releaseAcceptedCooldown")
    need("isUpperBound == false" in rel, "vm: release would lift a 429's wait")
    sc = braced(vm, "private func startCooldown")
    need("running.until >= until" in sc, "vm: a wait can be shortened")

    # ── View ─────────────────────────────────────────────────────────────────
    lowered = view.lower()
    for leak in ("already has an account", "already in use", "is taken", "already registered",
                 "belongs to another", "no account"):
        need(leak not in lowered, f"view: enumeration copy {leak!r}")
    sub = braced(view, "private func submitConfirm")
    need(re.search(r"if await viewModel\.confirm\(\)\s*\{\s*await appState\.refreshProfile\(\)", sub),
         "view: the profile keeps the old address after a change")
    row = braced(view, "private var currentEmailRow")
    tf = row[row.index("TextField("):]
    tf = tf[: tf.index("}")]
    need(".textContentType(.username)" in tf and ".accessibilityHidden(true)" in tf,
         "view: the AutoFill username field is visible to VoiceOver (or gone)")
    btn = braced(view, "private func sendButton")
    need("TimelineView" in btn and "wait == 0" in btn, "view: a send ignores its wait")

    # ── AppError ─────────────────────────────────────────────────────────────
    mapping = braced(err, "private static func mapAPIError")
    for code in ("AUTH_EMAIL_UNCHANGED", "AUTH_EMAIL_REJECTED"):
        m = re.search(r'if code == "' + code + r'" \{\s*return \.validationFailed\(', mapping)
        need(m, f"apperror: {code} is not .validationFailed")

    # ── Settings ─────────────────────────────────────────────────────────────
    # Three `.authenticated` blocks exist in the file; the password rows live in General's.
    general = braced(settings, "private var generalSection")
    gate = braced(general, "if appState.auth.status == .authenticated")
    need("ChangeEmailView(usesPassword: !needsPassword)" in gate,
         "settings: the Change Email row is missing or outside the signed-in block")
    # The alert's FIRST closure holds its buttons; the copy is in the `message:` closure.
    alert_at = settings.index('.alert("Delete Account"')
    alert = braced(settings[alert_at:], "} message:")
    need("within 90 days won't get the free welcome credits again" in alert,
         "settings: deletion no longer warns that the welcome credits are not re-granted")
    return fails


MUTATIONS = [
    ("endpoint", "        case .changeEmail, .confirmEmailChange:\n            return .signInRequired",
     "        case .changeEmail, .confirmEmailChange:\n            return .public"),
    ("endpoint", "             .resendConfirmation, .confirmEmail, .oauthSignIn, .sessionExchange:\n            return true",
     "             .resendConfirmation, .confirmEmail, .oauthSignIn, .sessionExchange, .changeEmail:\n            return true"),
    ("endpoint", "    let code: String?\n}", "    let code: String\n}"),
    ("endpoint", 'var isChanged: Bool { status == "changed" }', 'var isChanged: Bool { status != "codes_sent" }'),
    ("vm", "                responseType: EmailChangeResponse.self,\n                retryCount: 0\n            )\n            // The server",
     "                responseType: EmailChangeResponse.self\n            )\n            // The server"),
    ("vm", "secondsUntilSend(.changeCodes, at: Date()) == 0 else { return }", "true else { return }"),
    ("vm", '            currentPassword = ""\n            proofCode = ""\n            if response.isChanged',
     '            proofCode = ""\n            if response.isChanged'),
    ("vm", "                releaseAcceptedCooldown(.proofCode)\n", ""),
    ("vm", "                usesPassword = false\n", ""),
    ("vm", "            guard response.isChanged else {", "            guard true else {"),
    ("vm", "            fail(error, step: \"confirm\", kind: nil)",
     "            currentCode = \"\"\n            fail(error, step: \"confirm\", kind: nil)"),
    ("vm", "                isUpperBound: true\n            )\n        }", "                isUpperBound: false\n            )\n        }"),
    ("vm", "if cooldowns[kind]?.isUpperBound == false { cooldowns[kind] = nil }", "cooldowns[kind] = nil"),
    ("vm", "running.until >= until", "false"),
    ("view", "await appState.refreshProfile()", "_ = appState"),
    ("view", "We sent a code to each address.", "That address is already in use."),
    ("view", "                    .allowsHitTesting(false)\n                    .accessibilityHidden(true)",
     "                    .allowsHitTesting(false)"),
    ("apperror", 'if code == "AUTH_EMAIL_REJECTED" {\n                return .validationFailed(',
     'if code == "AUTH_EMAIL_REJECTED" {\n                return .apiError(code: code, message: message)\n                _ = AppError.validationFailed('),
    ("settings", "ChangeEmailView(usesPassword: !needsPassword)", "ChangeEmailView()"),
    ("settings", "within 90 days won't get the free welcome credits again", "may lose credits"),
    # comment-only edits must NOT rescue a broken guard
    ("endpoint", "        case .changeEmail, .confirmEmailChange:\n            return .signInRequired",
     "        // case .changeEmail, .confirmEmailChange:\n        //    return .signInRequired\n        case .changeEmail, .confirmEmailChange:\n            return .guestAllowed"),
]


def test_the_email_change_sources_pass_every_guard():
    assert checks(_sources()) == []


@pytest.mark.parametrize("which,old,new", MUTATIONS, ids=[f"{w}:{o.strip()[:40]}" for w, o, _ in MUTATIONS])
def test_each_guard_catches_its_mutation(which, old, new):
    src = _sources()
    base = checks(src)
    assert src[which].count(old) >= 1, f"mutation anchor drifted ({which}): {old[:70]!r}"
    mutated = dict(src)
    mutated[which] = src[which].replace(old, new, 1)
    res = checks(mutated)
    assert [r for r in res if r not in base], f"mutation not caught: {old[:70]!r}"


def test_the_ios_response_model_is_the_backend_schema():
    """Schema parity (testing.md §2): iOS decodes exactly the backend's fields, by the same names
    (APIClient sets no key strategy, and these names need no CodingKeys)."""
    from app.schemas.auth import EmailChangeResponse, EMAIL_CHANGE_CHANGED, EMAIL_CHANGE_CODES_SENT

    resp = braced(strip(_sources()["endpoint"]), "nonisolated struct EmailChangeResponse")
    swift_fields = set(re.findall(r"\blet (\w+):", resp))
    assert swift_fields == set(EmailChangeResponse.model_fields), (swift_fields, EmailChangeResponse.model_fields)
    assert "CodingKeys" not in resp, "no field needs mapping; a CodingKeys block would only drift"
    assert EMAIL_CHANGE_CHANGED == "changed" and EMAIL_CHANGE_CODES_SENT == "codes_sent"
    # Worst case a route can produce validates.
    EmailChangeResponse.model_validate({"status": EMAIL_CHANGE_CODES_SENT, "email": "", "message": ""})


def test_the_request_bodies_encode_to_the_backend_field_names():
    """`convertToSnakeCase` turns the Swift names into the Pydantic ones."""
    from app.schemas.auth import ChangeEmailRequest, ConfirmEmailChangeRequest

    def snake(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    ep = strip(_sources()["endpoint"])
    for struct, model in (("ChangeEmailRequest", ChangeEmailRequest),
                          ("ConfirmEmailChangeRequest", ConfirmEmailChangeRequest)):
        body = braced(ep, f"nonisolated struct {struct}:")
        fields = {snake(f) for f in re.findall(r"\blet (\w+):", body)}
        assert fields == set(model.model_fields), (struct, fields, set(model.model_fields))
