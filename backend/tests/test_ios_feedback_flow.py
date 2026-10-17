"""The "Help Us Improve" report flow must stay usable, honest, and inside its privacy filing.

`Profile → Help Us Improve` was a bare `mailto:` with a fixed subject and no body. Two failures:
it did **nothing at all** on any device without a mail client (the Simulator always) because the
open's result was discarded, and where it did work it dropped the user into an empty compose
window — so the reports that arrived carried no build number, no OS version, no steps, and were
usually untriageable.

It is now a native screen that composes in-app via `MFMailComposeViewController`.

Four things here are load-bearing enough to pin:

1. **`canSendMail()` is consulted BEFORE the Send button is offered.** That is the entire
   advantage the composer has over `mailto:`, whose only signal is a failed open. Lose it and
   the screen is back to offering a button that cannot work.
2. **The report body is built once.** The user types in exactly one place; the composer carries
   that text. If the composer also opened with a "What happened: / What I expected:" template,
   the user would see empty prompts beneath text they had already written and conclude they
   had to type it twice.
3. **The screenshot picker and the privacy filing agree.** The screen ships a `PhotosPicker`,
   so "Photos or Videos" is a declared collected data type in `PrivacyInfo.xcprivacy`,
   `documents/legal/app-privacy-answers.md`, the privacy policy and the App Store Connect
   answers. The guard runs both ways: a picker without the declaration fails, and so does a
   declaration left behind after the picker is removed.
4. **The reward promises a review, never credits.** Signed-in users can earn free credits for a
   report the owner confirms by hand (`scripts/grant_feedback_reward.py --apply` is the only
   thing that moves them). So the copy must say "we review every report" and "if we can confirm
   the issue", must not name an amount or a time, and must never mention ratings — the path
   from the "Rate Caydex" prompt opens the screen with the reward hidden. The report carries a
   report code and, for an account, its Account ID, because the sender address alone (often a
   Hide My Email relay) cannot say which account to credit.

Source-scan, per `project_source_scan_guard_vacuity`: comments stripped so this file's own prose
cannot satisfy an assertion, windows brace-bounded rather than anchored on the token under test,
and every assertion mutation-tested before being believed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_FEEDBACK = _IOS / "Views/Screens/FeedbackView.swift"
_PROFILE = _IOS / "Views/Screens/ProfileView.swift"
_APP_INFO = _IOS / "Core/Utilities/AppInfo.swift"
_GRANT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/grant_feedback_reward.py"


def _code_only(src: str) -> str:
    """Whole-line `//` comments blanked, line numbers preserved.

    Mandatory here: this screen's header comment *quotes* the APIs it forbids ("Do NOT add a
    photo picker", "PhotosPicker") precisely so the next reader understands why. A raw scan
    would read the warning as the violation.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _source(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


def _balanced(src: str, opener: str) -> str:
    """The delimited region introduced by `opener`, by matching its final delimiter.

    Handles `{ … }` blocks and `( … )` argument lists — `opener` ends with whichever it opens.
    Deliberately NOT `src[start:src.index(token, start)]`: bounding a window with the very token
    you are asserting is present is circular, because deleting the token just grows the window
    until it finds another one somewhere else. That exact mistake made an earlier guard in this
    repo pass while the bug it named was live.
    """
    pairs = {"{": "}", "(": ")", "[": "]"}
    start = src.index(opener) + len(opener) - 1
    open_ch = src[start]
    assert open_ch in pairs, f"{opener!r} must end at an opening delimiter, got {open_ch!r}"
    close_ch = pairs[open_ch]
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {open_ch!r} after {opener!r}")


# ── The row reaches the screen ────────────────────────────────────────────────


def test_the_row_pushes_the_screen_rather_than_firing_a_mailto():
    src = _source(_PROFILE)
    assert "FeedbackView()" in src, "Help Us Improve must push FeedbackView"
    assert "openFeedback" not in src, (
        "the old bare-mailto handler is still here — it did nothing on any device without a "
        "mail client, which is the bug being fixed"
    )
    # The row must be a NavigationLink, like every other row in that card.
    idx = src.index("FeedbackView()")
    assert "NavigationLink" in src[max(0, idx - 400):idx], (
        "FeedbackView is referenced but not from a NavigationLink"
    )


# ── canSendMail is checked up front ───────────────────────────────────────────


def test_send_is_offered_only_when_the_device_can_send_mail():
    src = _source(_FEEDBACK)
    assert "MailComposeView.canSendMail" in src, (
        "the screen must ask canSendMail() BEFORE offering Send — that is the whole advantage "
        "over mailto:, whose only signal is a failed open"
    )
    branch = _balanced(src, "if MailComposeView.canSendMail {")
    assert "sendButton" in branch, "the Send button must live in the canSendMail branch"

    # And the else-branch must actually help rather than dead-end.
    after = src[src.index("if MailComposeView.canSendMail {"):]
    else_branch = _balanced(after[after.index("} else {") + len("} else "):], "{")
    assert "MailUnavailableCard(" in else_branch, (
        "when mail is unavailable the screen must offer the shared copy-the-report card"
    )


def test_the_fallback_carries_the_whole_report_not_just_the_address():
    """A user who typed three paragraphs on a device with no mail client must not lose them."""
    src = _source(_FEEDBACK)
    args = _balanced(src, "MailUnavailableCard(")
    assert "payload: reportBody" in args, (
        "the fallback must carry `reportBody` — handing over only the address throws away "
        f"everything the user wrote. Got: {args[:160]}"
    )
    assert ".share(" in args, (
        "the report fallback must SHARE, not just copy. A bare 'Copied' is a dead end: it says "
        "something happened and nothing about what to do next, which is exactly how it read on "
        "a device with no Mail app. The share sheet gives a real destination."
    )


# ── The description is written once ───────────────────────────────────────────


def test_the_body_is_the_users_text_and_is_built_in_one_place():
    src = _source(_FEEDBACK)
    body = _balanced(src, "private var reportBody: String {")
    assert "message" in body, "the body must be built from the in-app text field"
    assert "diagnosticsBlock" in body, (
        "the body must carry the diagnostics block, or reports arrive without a build number "
        "and cannot be triaged"
    )
    assert src.count("private var reportBody") == 1

    # Both the composer and the copy fallback must use that ONE value.
    assert "body: reportBody" in src, "the composer must send reportBody"


def test_the_composer_does_not_reprompt_for_what_the_user_already_typed():
    """The duplication the design exists to avoid: an in-app box AND a template in the composer
    makes the user think they have to write it twice."""
    src = _source(_FEEDBACK)
    for template in ("What happened:", "What I expected:", "Steps to reproduce:"):
        assert template not in src, (
            f"the composer body ships a {template!r} prompt as well as the in-app field — the "
            f"user sees empty prompts under text they already wrote"
        )


# ── The privacy filing stays true ─────────────────────────────────────────────


def test_photos_usage_and_the_privacy_filing_agree():
    """The code and the App Privacy filing must never disagree — in EITHER direction.

    This started life as a flat ban on Photos APIs, from when the screenshot was attached with
    Mail's own control and the app touched no images. That was the wrong shape: the moment the
    product legitimately wants a picker, a ban is something to delete rather than something that
    protects anything, and deleting a guard is exactly when the filing gets forgotten.

    So it is a consistency check. Add a picker without declaring Photos and this fails. Declare
    Photos without shipping a picker and it fails too — an over-declaration is a false statement
    to Apple and to users, just a less dangerous one.

    The four surfaces that must agree are `PrivacyInfo.xcprivacy`, `app-privacy-answers.md`, the
    privacy policy, and the App Store Connect answers. Only the first two are machine-checkable
    from here; the policy is covered by `test_legal_pages.py` and the ASC answer is yours to set.
    """
    photo_apis = ("PhotosPicker", "PHPickerViewController", "UIImagePickerController",
                  "import Photos", "import PhotosUI")
    uses_photos = []
    for path in sorted(_IOS.rglob("*.swift")):
        code = _code_only(path.read_text(encoding="utf-8"))
        for api in photo_apis:
            if api in code:
                uses_photos.append(f"{path.relative_to(_IOS)}: {api}")

    manifest = (_IOS / "PrivacyInfo.xcprivacy").read_text(encoding="utf-8")
    declared_in_manifest = "NSPrivacyCollectedDataTypePhotosorVideos" in manifest

    answers_path = _IOS.parents[2] / "documents/legal/app-privacy-answers.md"
    answers = answers_path.read_text(encoding="utf-8") if answers_path.exists() else ""
    # Match the TABLE ROW, not any mention. "Photos or Videos" appears in this file in prose
    # either way — including in the note explaining the decision — so a substring check is
    # satisfied by the commentary and passes with the actual answer deleted. (Caught by
    # mutation: removing the row and re-listing Photos under "do NOT select" left this green.)
    declared_in_answers = bool(
        re.search(r"^\|\s*User Content\s*→\s*\*\*Photos or Videos\*\*\s*\|", answers, re.M)
    )

    if uses_photos:
        assert declared_in_manifest, (
            f"a Photos API is used ({uses_photos[:3]}) but PrivacyInfo.xcprivacy does not "
            f"declare NSPrivacyCollectedDataTypePhotosorVideos"
        )
        assert declared_in_answers, (
            "a Photos API is used but app-privacy-answers.md still lists 'Photos or Videos' "
            "under 'Do NOT select these' — the App Store Connect answer would be wrong"
        )
    else:
        assert not declared_in_manifest, (
            "PrivacyInfo.xcprivacy declares Photos but no Photos API is used — an "
            "over-declaration is still a false statement about what the app collects"
        )
        assert not declared_in_answers, (
            "app-privacy-answers.md selects 'Photos or Videos' but no Photos API is used"
        )


def test_the_photos_detector_is_not_vacuous():
    """Prove the predicate would catch a picker, against synthetic source — so it keeps working
    even when the codebase is in whichever state makes the check above trivially satisfied."""
    photo_apis = ("PhotosPicker", "PHPickerViewController", "UIImagePickerController",
                  "import Photos", "import PhotosUI")
    sample = "import SwiftUI\nimport PhotosUI\n\nPhotosPicker(selection: $item) { }"
    assert any(api in sample for api in photo_apis), "the detector no longer sees a picker"
    assert not any(api in "import SwiftUI\nTextField(\"x\", text: $y)" for api in photo_apis), (
        "the detector fires on source with no Photos API at all"
    )


# ── Address and diagnostics live in one place ─────────────────────────────────


def test_app_version_and_build_are_defined_once():
    """They were computed in three places before this work; the report would have made a fourth."""
    hits = []
    for path in sorted(_IOS.rglob("*.swift")):
        code = _code_only(path.read_text(encoding="utf-8"))
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if key in code:
                hits.append(f"{path.relative_to(_IOS)}: {key}")
    assert all(h.startswith("Core/Utilities/AppInfo.swift") for h in hits), (
        f"the bundle version keys are read outside AppInfo: {hits}"
    )


def test_the_report_goes_to_the_routed_address():
    """`feedback@` has NO Cloudflare route and silently bounced every message sent to it."""
    src = _source(_FEEDBACK)
    assert "AppInfo.supportEmail" in src, "the recipient must come from AppInfo"
    assert "feedback@" not in src, "feedback@ has no mail route — it bounces silently"
    info = _source(_APP_INFO)
    assert re.search(r'supportEmail\s*=\s*"support@caydexinvest\.com"', info)


def test_the_detector_is_not_vacuous():
    """If `_code_only` or the globs rotted, every scan above would pass on empty input."""
    assert len(list(_IOS.rglob("*.swift"))) > 100, "the Swift glob found almost nothing"
    src = _source(_FEEDBACK)
    assert "MailComposeView" in src and "NumberedBadge" in src, (
        "comment-stripping ate the file body"
    )


# ── The reward: promised as a review, granted only by hand ────────────────────
#
# Credits are NEVER automatic: the owner confirms a report, then runs
# `scripts/grant_feedback_reward.py --apply`. These pin the app's half of that. The copy says
# exactly that and no more, the reward never shows on the path from a rating prompt (Guideline
# 3.2.2), and the report carries what the owner needs to credit the right account exactly once.

_REWARD_KEYS = ("rowSubtitle", "callout", "guestNote", "reviewStepTitle", "reviewStepDetail",
                "thankYou", "composerCaption")

# Each must be said somewhere across the reward copy (after ’ → ' normalisation).
_REWARD_REQUIRED = ("we review every report", "if we can confirm the issue",
                    "as soon as we've reviewed it", "duplicates", "can't reproduce")

# Never said by any reward string. An amount or a time frame is a promise a manual review may
# not keep, and any mention of ratings would make the reward read as payment for one, which
# Guideline 3.2.2 forbids. "review" on its own stays legal: "we review every report" is the promise.
_REWARD_BANNED = (
    (r"\d", "a number (no amount, no time frame)"),
    (r"right away", "a time promise"),
    (r"instantly", "a time promise"),
    (r"immediately", "a time promise"),
    (r"automatic", "automation (credits are granted by hand)"),
    (r"guarantee", "a guarantee"),
    (r"\brat(e|es|ed|ing|ings)\b", "ratings"),
    (r"\bstars?\b", "star ratings"),
    (r"app store", "the App Store"),
    (r"\breviews\b", "App Store reviews"),
    (r"\b(a|your) review\b", "a review the user writes"),
)

_ONE_LINE_LITERAL = r'"((?:[^"\\\n]|\\.)*)"'


def _string_literals(src: str) -> list[str]:
    """The text of every Swift string literal in `src`, with ’ normalised to '.

    Triple-quoted blocks are taken first and blanked, so their delimiters cannot pair up with
    the one-line literals around them. Escapes stay inside the literal they belong to. Known
    blind spot: a literal nested inside an interpolation splits in two — no copy here is built
    that way. Run it on `_source()` output so commented-out strings are not read as live ones.
    """
    found: list[str] = []

    def _take(match: re.Match) -> str:
        found.append(match.group(1))
        return " "

    rest = re.sub(r'"""(.*?)"""', _take, src, flags=re.S)
    found.extend(m.group(1) for m in re.finditer(_ONE_LINE_LITERAL, rest))
    return [text.replace("\u2019", "'") for text in found]


def _copy_violations(texts: list[str]) -> list[str]:
    return [
        f"{why}: {pattern!r} in {text!r}"
        for text in texts
        for pattern, why in _REWARD_BANNED
        if re.search(pattern, text.lower())
    ]


def _missing_phrases(texts: list[str]) -> list[str]:
    joined = " ".join(texts).lower()
    return [phrase for phrase in _REWARD_REQUIRED if phrase not in joined]


def test_the_reward_copy_promises_a_review_not_credits():
    src = _source(_FEEDBACK)
    enum = _balanced(src, "enum FeedbackRewardCopy {")
    for key in _REWARD_KEYS:
        assert re.search(rf"\bstatic let {key} = \"", enum), (
            f"FeedbackRewardCopy.{key} is gone or no longer a plain literal"
        )
    literals = _string_literals(enum)
    assert len(literals) >= len(_REWARD_KEYS), f"the reward copy was not read: {literals}"

    missing = _missing_phrases(literals)
    assert not missing, (
        f"the reward copy no longer says {missing} — it must promise a REVIEW, say a confirmed "
        f"issue is what earns credits, and say that not every report qualifies"
    )
    bad = _copy_violations(literals)
    assert not bad, (
        "the reward copy promises what a manual review cannot, or ties the reward to ratings "
        f"(Guideline 3.2.2): {bad}"
    )


def test_the_row_subtitle_is_the_agreed_wording():
    enum = _balanced(_source(_FEEDBACK), "enum FeedbackRewardCopy {")
    m = re.search(r"static let rowSubtitle = " + _ONE_LINE_LITERAL, enum)
    assert m, "FeedbackRewardCopy.rowSubtitle is gone"
    assert m.group(1) == "Report an issue · earn free credits", (
        f"the Profile row's subtitle changed to {m.group(1)!r} — the owner chose this wording"
    )


def test_the_copy_guards_are_not_vacuous():
    """The extractor, the bans and the requirements must fire on copy that breaks them —
    otherwise the copy test above passes on anything, including an empty enum."""
    sample = (
        'let a = "Earn 20 credits instantly"\n'
        'let b = """\nThanks for the 5-star rating on the App Store\n"""\n'
        '        // "a commented-out credit"\n'
        'let c = "we can\u2019t say \\"why\\" \\(x)"\n'
    )
    literals = _string_literals(_code_only(sample))
    assert "Earn 20 credits instantly" in literals, "one-line literals are not collected"
    assert any("5-star rating" in s for s in literals), "triple-quoted blocks are not collected"
    assert not any("commented-out" in s for s in literals), "comments leak into the literals"
    assert 'we can\'t say \\"why\\" \\(x)' in literals, "escapes or ’ normalisation broke"

    flagged = " ".join(_copy_violations(literals))
    for why in ("a number", "a time promise", "ratings", "star ratings", "the App Store"):
        assert why in flagged, f"the {why!r} ban did not fire"
    assert _copy_violations(["Leave a review"]) and _copy_violations(["Your reviews help"])
    assert not _copy_violations(["We review every report and reply once we've reviewed it."]), (
        "'review' on its own is the promise itself and must stay legal"
    )
    assert _missing_phrases(["Thanks!"]) == list(_REWARD_REQUIRED)


def test_no_other_literal_on_the_screen_mentions_credits():
    """Every reward string lives in `FeedbackRewardCopy`, where the copy test can see it. A
    "credits" string typed straight into a `Text(...)` would dodge that test entirely."""
    src = _source(_FEEDBACK)
    enum = _balanced(src, "enum FeedbackRewardCopy {")
    outside = _string_literals(src.replace(enum, "", 1))
    assert any("Help Us Improve" in s for s in outside), "positive anchor — no literals were read"
    offenders = [s for s in outside if "credit" in s.lower()]
    assert not offenders, (
        f"reward copy outside FeedbackRewardCopy, beyond the copy guard's reach: {offenders}"
    )


def test_the_reward_needs_both_a_reward_path_and_an_account():
    src = _source(_FEEDBACK)
    assert "self.showsReward = showsReward" in _balanced(src, "init(showsReward: Bool = true) {"), (
        "the call sites need an internal init that takes showsReward (the memberwise one is "
        "private) and it must default to true for Profile's row"
    )
    gate = _balanced(src, "private var canEarnReward: Bool {")
    assert "showsReward" in gate, (
        "canEarnReward ignores showsReward, so the reward would show on the path from the "
        "rating prompt (Guideline 3.2.2)"
    )
    assert "accountId" in gate, "a guest has no account to credit — the reward needs one"
    assert re.search(
        r"private var accountId: String\? \{\s*appState\.user\.profile\?\.id\s*\}", src
    ), "accountId must be the signed-in profile's id — the same source as currentAccountId"


def test_every_reward_string_on_the_screen_is_gated():
    """Not just defined behind a flag — USED behind one. Each reference to the reward copy
    sits in a branch that only `canEarnReward` (or, for the guest note, `showsReward`) opens."""
    src = _source(_FEEDBACK)
    outside = src.replace(_balanced(src, "enum FeedbackRewardCopy {"), "", 1)
    used = set(re.findall(r"FeedbackRewardCopy\.(\w+)", outside))
    assert used == set(_REWARD_KEYS) - {"rowSubtitle"}, (
        f"the screen's reward strings changed ({sorted(used)}) — gate any new one, then update this"
    )
    for key in sorted(used):
        assert outside.count(f"FeedbackRewardCopy.{key}") == 1, (
            f"FeedbackRewardCopy.{key} is used more than once — only one use is checked as gated"
        )

    intro = _balanced(src, "private var intro: some View {")
    assert "FeedbackRewardCopy.callout" in _balanced(intro, "if canEarnReward {")
    assert "FeedbackRewardCopy.guestNote" in _balanced(intro, "if showsReward {")
    caption = _balanced(src, "private var composerCaption: String {")
    assert "FeedbackRewardCopy.composerCaption" in _balanced(caption, "if canEarnReward {")
    assert re.search(r"Text\(\s*canEarnReward\s*\?\s*FeedbackRewardCopy\.thankYou\b", src), (
        "the thank-you alert must use the reward copy only when canEarnReward"
    )
    rewarded = _balanced(src, "private static let rewardStepList: [Step] = baseSteps + [")
    assert "FeedbackRewardCopy.reviewStepDetail" in rewarded
    assert "FeedbackRewardCopy.reviewStepTitle" in rewarded
    chooser = re.sub(r"\s+", " ", _balanced(src, "private var stepList: [Step] {"))
    assert "canEarnReward ? Self.rewardStepList : Self.neutralStepList" in chooser, (
        f"the reward step must be chosen by canEarnReward. Got: {chooser}"
    )


def test_the_reward_copy_is_used_only_by_the_feedback_screen_and_its_row():
    """A new reference (a rating prompt, a paywall) would put the reward somewhere none of
    the gating above reaches."""
    users = sorted(
        str(path.relative_to(_IOS)) for path in _IOS.rglob("*.swift")
        if "FeedbackRewardCopy" in _code_only(path.read_text(encoding="utf-8"))
    )
    assert users == ["Views/Screens/FeedbackView.swift", "Views/Screens/ProfileView.swift"], users


def test_the_profile_row_offers_the_reward_only_to_an_account():
    src = _source(_PROFILE)
    after = src[src.index("FeedbackView()"):]
    label = re.sub(r"\s+", " ", _balanced(after, "} label: {"))
    assert '"Help Us Improve"' in label, "positive anchor — this is not the feedback row's label"
    assert re.search(
        r"subtitle: canEarnFeedbackReward \? FeedbackRewardCopy\.rowSubtitle : nil", label
    ), f"the row must show FeedbackRewardCopy.rowSubtitle to account holders only. Got: {label}"
    gate = _balanced(src, "private var canEarnFeedbackReward: Bool {")
    assert "viewModel.isAuthenticated" in gate, "an account holder must see the reward"
    assert "viewModel.isRestoring" in gate, (
        "a reconnecting user still has an account (auth.md §5) and must not be shown the guest row"
    )


# ── The report says which report, and which account ───────────────────────────


def test_the_report_names_the_account_only_when_there_is_one():
    info = _source(_APP_INFO)
    decl = info[info.index("static func diagnosticsBlock("):]
    signature = re.sub(r"\s+", " ", _balanced(decl, "diagnosticsBlock("))
    assert "accountId: String? = nil" in signature, signature
    assert "reportCode: String? = nil" in signature, signature
    body = _balanced(decl, "-> String {")

    guarded = _balanced(body, "if let accountId {")
    assert "Account ID:" in guarded, "the Account ID line is gone — confirmed reports can't be credited"
    assert "Account ID:" not in info.replace(guarded, "", 1), (
        "`Account ID:` is written outside `if let accountId {` — a guest would get a blank line"
    )
    assert "Report: " in _balanced(body, "if let reportCode {"), (
        "the report code line is gone — the owner needs it as the grant script's --ref"
    )
    for line in ("Please keep the details below", "App: Caydex", "Device: ", "Account: "):
        assert line in body, f"the diagnostics block lost {line!r}"


def test_the_report_body_carries_the_account_and_the_code():
    src = _source(_FEEDBACK)
    body = re.sub(r"\s+", " ", _balanced(src, "private var reportBody: String {"))
    assert "accountId: accountId" in body, "the report must name the account to credit"
    assert "reportCode: reportCode" in body, "the report must carry its report code"


def test_each_report_gets_its_own_code():
    src = _source(_FEEDBACK)
    assert re.search(r"@State private var reportCode = AppInfo\.newReportCode\(\)", src), (
        "the code must be @State: a plain property would be re-rolled on every re-render"
    )
    composer = _balanced(src, "MailComposeView(")
    assert re.search(r'subject:\s*"[^"\n]*\\\(reportCode\)[^"\n]*"', composer), (
        "the report code must be in the subject, where the owner sees it in the inbox"
    )
    assert "reportCode = AppInfo.newReportCode()" in _balanced(src, "if sent {"), (
        "a sent report must retire its code, or the next report would share it and be refused "
        "as a replay when the owner credits it"
    )


def _swift_report_code_alphabet(info: str) -> str:
    m = re.search(r"private static let reportCodeAlphabet = " + _ONE_LINE_LITERAL, info)
    assert m, "AppInfo.reportCodeAlphabet is gone or no longer one literal"
    return m.group(1)


def test_the_report_code_avoids_confusable_characters():
    info = _source(_APP_INFO)
    alphabet = _swift_report_code_alphabet(info)
    assert len(alphabet) >= 20, f"the alphabet is suspiciously small: {alphabet!r}"
    assert not set("0O1IL") & set(alphabet), (
        f"the report code alphabet has 0/O/1/I/L in it — codes get mistyped: {alphabet!r}"
    )
    assert len(set(alphabet)) == len(alphabet), "a repeated character skews the code"
    assert re.fullmatch(r"[A-Z2-9]+", alphabet), "uppercase letters and digits only"
    maker = _balanced(info, "static func newReportCode() -> String {")
    assert '"R-"' in maker and "reportCodeAlphabet" in maker and "0..<6" in maker, maker


def test_the_report_code_matches_the_grant_script():
    """The owner pastes the code from the email into `--ref`; the script normalises and checks
    it with its own copy of the format. The two copies must agree."""
    if not _GRANT_SCRIPT.exists():
        pytest.skip(f"{_GRANT_SCRIPT} not present")
    tree = ast.parse(_GRANT_SCRIPT.read_text(encoding="utf-8"))
    constants = {
        target.id: node.value
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    for name in ("REPORT_CODE_ALPHABET", "REPORT_CODE_PREFIX", "REPORT_CODE_LENGTH"):
        assert name in constants, f"{name} is missing from {_GRANT_SCRIPT.name}"
    script = {name: ast.literal_eval(constants[name]) for name in
              ("REPORT_CODE_ALPHABET", "REPORT_CODE_PREFIX", "REPORT_CODE_LENGTH")}
    info = _source(_APP_INFO)
    maker = _balanced(info, "static func newReportCode() -> String {")
    assert script["REPORT_CODE_ALPHABET"] == _swift_report_code_alphabet(info), (
        "the app and the grant script disagree on the report code alphabet"
    )
    assert f'"{script["REPORT_CODE_PREFIX"]}"' in maker, (
        f"the app's report code prefix is not the script's {script['REPORT_CODE_PREFIX']!r}"
    )
    assert f"0..<{script['REPORT_CODE_LENGTH']}" in maker, (
        f"the app's report code length is not the script's {script['REPORT_CODE_LENGTH']}"
    )
