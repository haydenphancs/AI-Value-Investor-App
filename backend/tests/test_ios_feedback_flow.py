"""The "Help Us Improve" report flow must stay usable, honest, and inside its privacy filing.

`Profile → Help Us Improve` was a bare `mailto:` with a fixed subject and no body. Two failures:
it did **nothing at all** on any device without a mail client (the Simulator always) because the
open's result was discarded, and where it did work it dropped the user into an empty compose
window — so the reports that arrived carried no build number, no OS version, no steps, and were
usually untriageable.

It is now a native screen that composes in-app via `MFMailComposeViewController`.

Three things here are load-bearing enough to pin:

1. **`canSendMail()` is consulted BEFORE the Send button is offered.** That is the entire
   advantage the composer has over `mailto:`, whose only signal is a failed open. Lose it and
   the screen is back to offering a button that cannot work.
2. **The report body is built once.** The user types in exactly one place; the composer carries
   that text. If the composer also opened with a "What happened: / What I expected:" template,
   the user would see empty prompts beneath text they had already written and conclude they
   had to type it twice.
3. **No Photos API, anywhere.** The screenshot is attached with Mail's own Insert Photo control
   inside the compose sheet. The moment this app reads the photo library itself, "Photos or
   Videos" must be added to `documents/legal/app-privacy-answers.md`, `PrivacyInfo.xcprivacy`,
   the privacy policy and the App Store Connect answers — four surfaces that currently declare
   it absent, where a wrong answer is a review rejection. This guard is what stops a future
   session adding a picker and silently invalidating the filing.

Source-scan, per `project_source_scan_guard_vacuity`: comments stripped so this file's own prose
cannot satisfy an assertion, windows brace-bounded rather than anchored on the token under test,
and every assertion mutation-tested before being believed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_FEEDBACK = _IOS / "Views/Screens/FeedbackView.swift"
_PROFILE = _IOS / "Views/Screens/ProfileView.swift"
_APP_INFO = _IOS / "Core/Utilities/AppInfo.swift"


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
