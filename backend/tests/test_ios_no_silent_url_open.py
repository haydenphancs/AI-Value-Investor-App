"""No user-initiated action may open a URL and throw away the result.

`UIApplication.shared.open(url)` — the one-argument form — discards whether the open succeeded.
That is not a tidiness issue, it was a shipped, invisible, user-facing bug:

  * `Profile → Help & Support` and `Profile → Help Us Improve` are `mailto:` links, and **the
    iOS Simulator ships no Mail app**. Both rows did nothing at all — no UI, no log — for the
    entire life of the file. The same happens on a real device with no mail account configured.
  * `Settings → Manage Subscription` opens an App Store deep link, which the Simulator also
    cannot service, with the same silent result.

`.claude/rules/auth.md` §6: a user-initiated action must never fail silently. The fix routes
every one of these through `openInSystem(_:action:onFailure:)` in
`frontend/ios/ios/Views/Modifiers/InAppBrowser.swift`, which passes a `completionHandler:` and
reports a failure through `AppActions.shared.reportMutationFailure`.

## Why this DERIVES the call sites instead of listing them

Listing the three sites the audit found would pin exactly the three already fixed. The
`_inflight` precedent is the cautionary one: an audit named 6 services and the derived guard
found 25. So this walks every Swift file and asserts a property of each call site it finds — a
fourth one added next month fails the build rather than silently joining the pattern.

Per `project_source_scan_guard_vacuity`: comments are stripped (this file's own prose quotes the
API it forbids), windows are brace-bounded, and every assertion here was mutation-tested — the
defect reintroduced, this file confirmed failing, the source restored.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"

# The single sanctioned wrapper. Its own body is the one place allowed to call the raw API.
_ROUTER = "Views/Modifiers/InAppBrowser.swift"
_WRAPPER = "openInSystem"


def _swift_files() -> list[Path]:
    if not _IOS.is_dir():
        pytest.skip(f"{_IOS} not present")
    return sorted(_IOS.rglob("*.swift"))


def _code_only(src: str) -> str:
    """Blank whole-line `//` comments, preserving line numbers.

    Required, not cosmetic: several of these files *discuss* `UIApplication.shared.open` at
    length in exactly the comments that explain why they no longer call it. Scanning raw text
    would flag the explanation as the offence.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _open_call_sites() -> list[tuple[Path, int, str]]:
    """Every `UIApplication.shared.open(` in app code, as (path, 1-indexed line, call text).

    The call text runs to the matching close paren so a `completionHandler:` on a later line is
    still seen — these calls are routinely wrapped across lines.
    """
    sites: list[tuple[Path, int, str]] = []
    for path in _swift_files():
        code = _code_only(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"UIApplication\.shared\.open\(", code):
            start = m.end() - 1  # at the '('
            depth = 0
            end = len(code)
            for i in range(start, len(code)):
                if code[i] == "(":
                    depth += 1
                elif code[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            # Include a trailing closure if one follows the call.
            tail = code[end:end + 40]
            if re.match(r"\s*\{", tail):
                end += len(tail) - len(tail.lstrip())
                depth = 0
                for i in range(end, len(code)):
                    if code[i] == "{":
                        depth += 1
                    elif code[i] == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
            sites.append((path, code[:m.start()].count("\n") + 1, code[m.start():end]))
    return sites


def test_every_url_open_handles_failure():
    offenders = []
    for path, line, call in _open_call_sites():
        handled = "completionHandler:" in call or re.search(r"\)\s*\{", call)
        if not handled:
            offenders.append(f"{path.relative_to(_IOS)}:{line}")
    assert not offenders, (
        "these calls discard whether the open succeeded, so the action fails silently: "
        f"{offenders}. Use openInSystem(_:action:) instead — see InAppBrowser.swift."
    )


def test_only_the_shared_router_calls_the_raw_api():
    """Even a correctly-handled call, copy-pasted, drifts. Keeping exactly one call site means
    the reporting behaviour is fixed in one place for the whole app."""
    outside = {
        str(path.relative_to(_IOS))
        for path, _, _ in _open_call_sites()
        if str(path.relative_to(_IOS)) != _ROUTER
    }
    assert not outside, (
        f"UIApplication.shared.open is called outside {_ROUTER}: {sorted(outside)}. "
        f"Route it through {_WRAPPER}(_:action:) so the failure is reported once, centrally."
    )


def test_the_wrapper_actually_reports_the_failure():
    """Anti-vacuity #1. Both assertions above are satisfied by a wrapper whose completion
    handler is empty — which would be the original bug with extra steps."""
    src = _code_only((_IOS / _ROUTER).read_text(encoding="utf-8"))
    body = src[src.index(f"func {_WRAPPER}("):]
    body = body[:body.index("\nprivate func")] if "\nprivate func" in body else body

    assert "reportMutationFailure" in body, (
        "the wrapper must report through AppActions — a completion handler that does nothing "
        "is indistinguishable from no completion handler"
    )
    assert "noAppToOpenURL" in body, (
        "report a typed AppError, not a generic one: .unknown says 'try again' and offers a "
        "retry, but nothing on the device can ever service the URL (root invariant #3)"
    )
    assert re.search(r"guard\s+!success\s+else\s*\{\s*return\s*\}", body), (
        "the wrapper must report ONLY on failure — reporting unconditionally would toast on "
        "every successful open"
    )


def test_the_detector_finds_the_known_call_site():
    """Anti-vacuity #2. If the regex rots, every assertion above passes on an empty set and
    this guard silently protects nothing."""
    sites = _open_call_sites()
    assert sites, "detector found zero UIApplication.shared.open calls — the regex has rotted"
    assert any(str(p.relative_to(_IOS)) == _ROUTER for p, _, _ in sites), (
        f"detector no longer sees the call inside {_ROUTER}"
    )


def test_the_detector_would_catch_a_bare_call():
    """Anti-vacuity #3, the strongest one: prove the predicate rejects the exact defect.

    Asserted against synthetic source rather than the repo, so it keeps testing the detector
    even once the codebase is clean — which is precisely when a rotted detector stops being
    noticeable.
    """
    bare = "UIApplication.shared.open(url)"
    handled = "UIApplication.shared.open(url, options: [:]) { success in }"
    assert not ("completionHandler:" in bare or re.search(r"\)\s*\{", bare)), (
        "the predicate accepts a bare open() — it would not have caught the original bug"
    )
    assert "completionHandler:" in handled or re.search(r"\)\s*\{", handled)
