""""Rate the App" and the share download link follow where THIS copy of the app came from.

TestFlight 1.0 (3) and 1.0 (6), General Settings › About — *"rate the app doesn't work?
Because we have not launch yet on apple store?"* The row was a silent no-op: the App Store id
was a deliberate blank until launch, so `reviewURL` was nil and the row fell back to
`requestReview()`, which iOS never displays for a TestFlight install.

The brief proposed filling the id in on launch day. That cannot work for 1.0: the id is
compiled in, and the binary App Review approves is built BEFORE launch, so the flip could only
ship in a 1.0.1 — every 1.0 App Store user would have kept the silent row and a website link in
every share. So the id is set now (6759525689) and the decision is made at RUNTIME:

* App Store install → the write-review deep link, and the App Store page in shares.
* TestFlight / App Review / Xcode → an explanatory alert with "Send Feedback", and the website
  in shares (the listing 404s until approval).
* Unknown → the review link for the Rate row (the tapper is the user; telling a real customer
  "pre-release build" would be false), but the WEBSITE for shares (the recipient is a third
  party; a dead link is the worst outcome).

Two halves, because there is no XCTest target:

A. `InstallSourcePolicy` is EXECUTED — piped into `xcrun swift -` with a harness, the mechanism
   `test_ios_weekly_investor_quotes.py` uses. The half that skips without `xcrun`.
B. Source guards over `InstallSourceStore` and the call sites. Comments are stripped and every
   check is brace-bounded to its declaration (.claude/rules/testing.md §3) — the comments beside
   this fix name `requestReview()`, `CAYDEX_INSTALL_SOURCE` and `AppTransaction.shared`
   verbatim, so an un-stripped scan would pass on prose after the code was reverted.

⚠️ The install source comes from the receipt's FILE NAME, never `AppTransaction`: measured
2026-09-24 on the Simulator, `AppTransaction.shared` with no cached app transaction put an
interactive "Sign in to Apple Account" sheet on screen at launch. Pinned tree-wide below.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_POLICY = _IOS / "Core/Utilities/InstallSourcePolicy.swift"
_STORE = _IOS / "Core/Services/InstallSourceStore.swift"
_APP_INFO = _IOS / "Core/Utilities/AppInfo.swift"
_SETTINGS = _IOS / "Views/Screens/AppSettingsView.swift"
_LAUNCH_CHECKLIST = _REPO / "documents/legal/LAUNCH_CHECKLIST.md"   # GITIGNORED — local only
_STORE_LISTING = _REPO / "documents/legal/app-store-listing.md"     # tracked

_APP_STORE_ID = "6759525689"


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks, `//` lines and trailing `//` tails. See the module docstring.
    Block comments first: a `/*`-wrapped modifier still compiles and binds nothing, so a
    line-only pass would leave a commented-out alert looking live."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    return _strip_comments(path.read_text())


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a call wrapped across lines still matches."""
    return re.sub(r"\s+", " ", text)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of the FIRST declaration matching `header`, comments stripped."""
    src = _strip_comments(src)
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
                return src[open_brace : i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


# ── A. The policy, executed ───────────────────────────────────────────

_HARNESS = r"""
var failures = 0
var cases = 0
func check<T: Equatable>(_ name: String, _ got: T, _ want: T) {
    cases += 1
    if got != want {
        failures += 1
        print("FAIL|\(name)|got=\(String(describing: got))|want=\(String(describing: want))")
    }
}
let P = InstallSourcePolicy.self
let store = URL(string: "https://apps.apple.com/app/id6759525689")!
let review = URL(string: "https://apps.apple.com/app/id6759525689?action=write-review")!
let web = URL(string: "https://caydexinvest.com")!
let none: InstallSource? = nil

// --- classify: a DEBUG build / the Simulator is development, WHATEVER the receipt says ------
// (the Simulator's receipt path is named "receipt" — measured — and must not pass for the App Store)
check("dev_prod_receipt", P.classify(receiptFileName: "receipt", isDevelopmentBuild: true), .development)
check("dev_sandbox_receipt", P.classify(receiptFileName: "sandboxReceipt", isDevelopmentBuild: true), .development)
check("dev_no_receipt", P.classify(receiptFileName: nil, isDevelopmentBuild: true), .development)
check("dev_garbage_receipt", P.classify(receiptFileName: "garbage", isDevelopmentBuild: true), .development)

// --- classify: a release build → the receipt's file name decides ---------------------------
check("sandbox_receipt", P.classify(receiptFileName: "sandboxReceipt", isDevelopmentBuild: false), .preRelease)
check("prod_receipt", P.classify(receiptFileName: "receipt", isDevelopmentBuild: false), .appStore)
check("no_receipt", P.classify(receiptFileName: nil, isDevelopmentBuild: false), none)
// --- classify: EXACT match only — an unexpected name is unknown, never a confident guess ---
check("empty_receipt", P.classify(receiptFileName: "", isDevelopmentBuild: false), none)
check("uppercase_receipt", P.classify(receiptFileName: "SANDBOXRECEIPT", isDevelopmentBuild: false), none)
check("suffixed_receipt", P.classify(receiptFileName: "sandboxReceipt.bak", isDevelopmentBuild: false), none)
check("padded_receipt", P.classify(receiptFileName: " receipt", isDevelopmentBuild: false), none)
check("prefix_receipt", P.classify(receiptFileName: "receipt2", isDevelopmentBuild: false), none)
check("garbage_receipt", P.classify(receiptFileName: "garbage", isDevelopmentBuild: false), none)

// --- downloadURL: the store page ONLY for a known App Store install -------------------------
check("dl_appstore", P.downloadURL(for: .appStore, appStoreURL: store, websiteURL: web), store)
check("dl_appstore_no_id", P.downloadURL(for: .appStore, appStoreURL: nil, websiteURL: web), web)
check("dl_prerelease", P.downloadURL(for: .preRelease, appStoreURL: store, websiteURL: web), web)
check("dl_development", P.downloadURL(for: .development, appStoreURL: store, websiteURL: web), web)
check("dl_unknown", P.downloadURL(for: nil, appStoreURL: store, websiteURL: web), web)
check("dl_prerelease_no_id", P.downloadURL(for: .preRelease, appStoreURL: nil, websiteURL: web), web)
check("dl_development_no_id", P.downloadURL(for: .development, appStoreURL: nil, websiteURL: web), web)
check("dl_unknown_no_id", P.downloadURL(for: nil, appStoreURL: nil, websiteURL: web), web)

// --- rateAction ------------------------------------------------------------------------------
check("rate_appstore", P.rateAction(for: .appStore, reviewURL: review), .openReview(review))
check("rate_unknown", P.rateAction(for: nil, reviewURL: review), .openReview(review))
check("rate_prerelease", P.rateAction(for: .preRelease, reviewURL: review), .explainPreRelease)
check("rate_development", P.rateAction(for: .development, reviewURL: review), .explainPreRelease)
check("rate_appstore_no_id", P.rateAction(for: .appStore, reviewURL: nil), .systemPrompt)
check("rate_unknown_no_id", P.rateAction(for: nil, reviewURL: nil), .systemPrompt)
check("rate_prerelease_no_id", P.rateAction(for: .preRelease, reviewURL: nil), .explainPreRelease)
check("rate_development_no_id", P.rateAction(for: .development, reviewURL: nil), .explainPreRelease)

// --- parseOverride: outer nil = not understood; .some(nil) = "unknown" --------------------
let notUnderstood: InstallSource?? = nil
let unknownAnswer: InstallSource?? = .some(nil)
check("ov_appstore", P.parseOverride("appStore"), .some(.appStore))
check("ov_prerelease", P.parseOverride("preRelease"), .some(.preRelease))
check("ov_development", P.parseOverride("development"), .some(.development))
check("ov_unknown", P.parseOverride("unknown"), unknownAnswer)
check("ov_empty", P.parseOverride(""), notUnderstood)
check("ov_wrong_case", P.parseOverride("AppStore"), notUnderstood)
check("ov_lower", P.parseOverride("prerelease"), notUnderstood)
check("ov_padded", P.parseOverride(" appStore"), notUnderstood)

print("DONE|\(failures)|\(cases)")
"""

# A LITERAL, not `_HARNESS.count(...)`: counting the harness would shrink with it, so deleting
# the core regression case would stay green. Change it only when adding cases.
_EXPECTED_CASES = 37
# The cases this fix exists for — a TestFlight install must never get the store link or prompt.
_CORE_CASES = ("sandbox_receipt", "dev_prod_receipt", "dl_prerelease", "dl_unknown",
               "rate_prerelease", "rate_development", "prod_receipt")


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    src = _POLICY.read_text() + "\n" + _HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=src, text=True,
                              capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — InstallSourcePolicy probably stopped "
            "compiling standalone (a StoreKit / SwiftUI / UIKit import will do it).\n"
            f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-4000:]}")
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_every_policy_case(swift_output: str):
    failures = [line for line in swift_output.splitlines() if line.startswith("FAIL|")]
    assert not failures, "InstallSourcePolicy mismatches:\n  " + "\n  ".join(failures)


def test_the_harness_ran_every_case(swift_output: str):
    m = re.search(r"DONE\|(\d+)\|(\d+)", swift_output)
    assert m, swift_output[-2000:]
    assert int(m.group(1)) == 0
    assert int(m.group(2)) == _EXPECTED_CASES, (
        f"the harness ran {m.group(2)} cases, expected {_EXPECTED_CASES}")
    assert _HARNESS.count("\ncheck(") == _EXPECTED_CASES
    for name in _CORE_CASES:
        assert f'check("{name}",' in _HARNESS, f"core case {name} was removed from the harness"


def test_policy_is_foundation_only():
    raw = _POLICY.read_text()
    code = _strip_comments(raw)
    assert "import Foundation" in code
    for banned in ("import StoreKit", "import SwiftUI", "import UIKit", "import OSLog",
                   "import Combine"):
        assert banned not in code, (
            f"InstallSourcePolicy has `{banned}` — it must stay Foundation-only or "
            "`xcrun swift -` cannot run it and every executed case above stops being tested")
    assert "import StoreKit" in raw, "the header explaining the rule is gone (anti-vacuity)"


# ── B. The id ─────────────────────────────────────────────────────────


def test_the_app_store_id_is_the_real_record():
    """Replaces `test_the_app_store_id_is_still_unset`, which pinned the id BLANK until launch
    day. The blank was the bug: a compiled-in id can only change in a new binary, so the 1.0
    build could never have got it. The pre-launch 404 is now handled by the runtime gate below.
    """
    m = re.search(r'static let appStoreAppID = "([^"]*)"', _code(_APP_INFO))
    assert m, "AppInfo.appStoreAppID declaration not found"
    assert m.group(1) == _APP_STORE_ID, (
        f"appStoreAppID is {m.group(1)!r}; Caydex's App Store record is {_APP_STORE_ID}. A blank "
        "silences the Rate row for every 1.0 user; a wrong number deep-links them to somebody "
        "else's app.")
    assert m.group(1).isdigit()


def test_the_id_matches_the_recorded_app_store_record():
    """Parity, not two hard-coded copies. The TRACKED store listing always runs; the launch
    checklist is gitignored (a local working file), so it is checked only where it exists —
    the pattern `test_ios_weekly_investor_quotes.py` uses — rather than erroring on a fresh
    checkout or worktree."""
    listed = set(re.findall(r"Apple ID (\d+)", _STORE_LISTING.read_text()))
    assert listed, "app-store-listing.md no longer records the Apple ID — this check has drifted"
    assert listed == {_APP_STORE_ID}, f"app-store-listing.md records Apple ID {listed}"
    if _LAUNCH_CHECKLIST.exists():
        ids = set(re.findall(r"adamId \*\*(\d+)\*\*", _LAUNCH_CHECKLIST.read_text()))
        assert ids, "LAUNCH_CHECKLIST no longer records the adamId — this check has drifted"
        assert ids == {_APP_STORE_ID}, f"LAUNCH_CHECKLIST records adamId {ids}"


def test_the_store_urls_are_built_from_the_id():
    """The harness uses hand-written URLs; pin that AppInfo builds the same shapes."""
    code = _flat(_code(_APP_INFO))
    assert 'return URL(string: "https://apps.apple.com/app/id\\(id)")' in code
    assert 'return URL(string: base.absoluteString + "?action=write-review")' in code
    assert "guard !id.isEmpty, id.allSatisfy(\\.isNumber) else { return nil }" in code


# ── C. The gate ───────────────────────────────────────────────────────


def test_the_download_link_is_gated_on_the_install_source():
    block = _flat(_decl_block(_APP_INFO.read_text(), "static var downloadURL: URL"))
    assert ("InstallSourcePolicy.downloadURL(for: InstallSourceStore.current, "
            "appStoreURL: appStoreURL, websiteURL: websiteURL)") in block, (
        "AppInfo.downloadURL must ask InstallSourcePolicy with the RUNTIME install source — "
        "otherwise every TestFlight / review share links a store page that 404s until approval")
    assert "??" not in block, "a bare `appStoreURL ?? websiteURL` ignores the install source"


def _rate_arms() -> dict[str, str]:
    block = _decl_block(_SETTINGS.read_text(), "private func rateTheApp()")
    parts = re.split(r"\bcase \.(\w+)", block)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_rate_the_app_asks_the_policy():
    block = _flat(_decl_block(_SETTINGS.read_text(), "private func rateTheApp()"))
    assert ("InstallSourcePolicy.rateAction(for: InstallSourceStore.current, "
            "reviewURL: AppInfo.reviewURL)") in block
    assert "default:" not in block, (
        "the switch must be exhaustive — a new RateAction case has to be decided, not defaulted")
    assert set(_rate_arms()) == {"openReview", "explainPreRelease", "systemPrompt"}


def test_pre_release_explains_instead_of_prompting():
    """The regression this file exists for: `requestReview()` on a TestFlight build is a
    silent no-op, so it must never be what a pre-release tap does."""
    arms = _rate_arms()
    assert "showPreReleaseRating = true" in arms["explainPreRelease"]
    assert "requestReview" not in arms["explainPreRelease"]
    assert "openInSystem" not in arms["explainPreRelease"]


def test_app_store_opens_the_review_link_and_reports_failure():
    arm = _rate_arms()["openReview"]
    assert "openInSystem(url" in arm
    assert "appStoreUnavailable = true" in arm, (
        "a device that cannot open the App Store (the Simulator) must say so, not do nothing")
    assert "requestReview" not in arm


def test_request_review_is_only_the_last_resort():
    block = _decl_block(_SETTINGS.read_text(), "private func rateTheApp()")
    assert block.count("requestReview()") == 1
    assert "requestReview()" in _rate_arms()["systemPrompt"]


def test_the_pre_release_alert_offers_a_way_forward():
    settings = _SETTINGS.read_text()
    header = '.alert("Rate Caydex", isPresented: $showPreReleaseRating)'
    actions = _flat(_decl_block(settings, header))
    assert 'Button("Send Feedback") { showFeedback = true }' in actions
    # The message is the SECOND trailing closure (`} message: {`), outside the first block.
    after = _strip_comments(settings)
    after = after[after.index(header):]
    message = _flat(_decl_block(after, "} message:"))
    assert "App Store version" in message, "positive anchor — the copy explains where to rate"
    # App Review installs are sandbox installs, so a REVIEWER reads this alert: a build that
    # calls itself pre-release / beta / unavailable invites a Guideline 2.2 / 2.1 rejection.
    # String LITERALS only — what the user sees, not identifiers like `$showPreReleaseRating`.
    shown = " ".join(re.findall(r'"([^"]*)"', header + " " + actions + " " + message)).lower()
    assert "rate caydex" in shown and "send feedback" in shown, "literal extraction is broken"
    for banned in ("pre-release", "prerelease", "beta", "testflight", "test build", "trial",
                   "not available", "unavailable", "demo"):
        assert banned not in shown, f"the alert a reviewer can see says {banned!r}"
    # Worded to stay true after launch too — a TestFlight build still cannot be rated then.
    for dated in ("once caydex is", "until launch", "not launched", "coming soon"):
        assert dated not in shown, f"the alert copy goes stale at launch: {dated!r}"
    dest = _decl_block(settings, ".navigationDestination(isPresented: $showFeedback)")
    assert "FeedbackView()" in dest


# ── D. InstallSourceStore ─────────────────────────────────────────────


def test_app_transaction_is_never_read():
    """`AppTransaction.shared` with no cached app transaction starts an INTERACTIVE receipt
    renewal — a "Sign in to Apple Account" sheet at launch (measured 2026-09-24, Simulator,
    storekitd log `Sending authentication request for receipt renewal`). `refresh()` always
    prompts. Tree-wide: no screen may pay a sign-in sheet to pick a link."""
    offenders = [str(p.relative_to(_IOS)) for p in sorted(_IOS.rglob("*.swift"))
                 if re.search(r"AppTransaction\s*\.\s*(shared|refresh)", _code(p))]
    assert not offenders, offenders
    store = _code(_STORE)
    assert "import StoreKit" not in store and "AppTransaction" not in store


def test_current_is_the_override_then_the_resolved_receipt_answer():
    block = _decl_block(_STORE.read_text(), "static var current: InstallSource?")
    assert block.rstrip("} \n").endswith("return resolved"), (
        "after the DEBUG override, `current` must return the once-computed receipt answer")
    resolved = _flat(_decl_block(_STORE.read_text(), "private static let resolved: InstallSource? ="))
    assert ("InstallSourcePolicy.classify(receiptFileName: receipt, "
            "isDevelopmentBuild: isDevelopmentBuild)") in resolved
    assert "let receipt = receiptFileName()" in resolved
    assert "log.info(" in resolved, "log the decision once, so a device log shows the branch"


def test_a_debug_or_simulator_build_is_development():
    """The Simulator's receipt path reads `receipt` — without this it classified as an App
    Store install (measured 2026-09-24)."""
    block = _decl_block(_STORE.read_text(), "private static var isDevelopmentBuild: Bool")
    for token in ("#if DEBUG || targetEnvironment(simulator)", "return true", "#else",
                  "return false", "#endif"):
        assert token in block, f"isDevelopmentBuild lost `{token}`"
    assert block.index("#if DEBUG || targetEnvironment(simulator)") < block.index("return true")
    assert block.index("#else") < block.index("return false") < block.index("#endif")
    assert block.index("return true") < block.index("#else")


def test_the_receipt_name_comes_from_the_bundle():
    block = _decl_block(_STORE.read_text(), "private static func receiptFileName()")
    assert "(Bundle.main as ReceiptURLReading).appStoreReceiptURL?.lastPathComponent" in block
    assert "extension Bundle: ReceiptURLReading {}" in _code(_STORE)


def test_the_debug_override_cannot_ship():
    code = _code(_STORE)
    hits = [m.start() for m in re.finditer(r"CAYDEX_INSTALL_SOURCE", code)]
    assert hits, "the override is gone — the Simulator can no longer show each branch"
    for pos in hits:
        opened = code.rfind("#if DEBUG", 0, pos)
        between = code[opened:pos] if opened != -1 else ""
        assert opened != -1 and "#endif" not in between and "#else" not in between, (
            "CAYDEX_INSTALL_SOURCE is read outside `#if DEBUG` — an environment variable would "
            "decide what a production build links to")


# ── E. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    decoy = (
        "private func rateTheApp() {\n"
        "    // requestReview() InstallSourcePolicy.rateAction(\n"
        "    switch x {\n"
        "    case .systemPrompt: foo()   // requestReview()\n"
        "    }\n"
        "}\n"
        "func neighbour() { requestReview() }\n"
    )
    block = _decl_block(decoy, "private func rateTheApp()")
    assert "requestReview" not in block, "comments are not being stripped"
    assert "InstallSourcePolicy" not in block, "comments are not being stripped"
    assert "neighbour" not in block, "the scan leaked into the next declaration"

    block_decoy = (
        "private func rateTheApp() {\n"
        "    switch x {\n"
        "    case .explainPreRelease: /* showPreReleaseRating = true */ break\n"
        "    }\n"
        "}\n"
    )
    assert "showPreReleaseRating" not in _decl_block(block_decoy, "private func rateTheApp()"), (
        "block comments are not being stripped")
    wrapped = '/*\n.alert("Rate Caydex", isPresented: $showPreReleaseRating) { }\n*/\n'
    with pytest.raises(AssertionError):
        _decl_block(wrapped, '.alert("Rate Caydex", isPresented: $showPreReleaseRating)')

    debug_decoy = "#if DEBUG\nlet a = 1\n#endif\nlet b = env[\"CAYDEX_INSTALL_SOURCE\"]\n"
    pos = debug_decoy.index("CAYDEX_INSTALL_SOURCE")
    opened = debug_decoy.rfind("#if DEBUG", 0, pos)
    assert "#endif" in debug_decoy[opened:pos], "the #if DEBUG scan would miss a leaked read"
