"""Every notification category the backend sends must be registered by iOS.

WHY. iOS matches `aps.category` against the categories registered with
`UNUserNotificationCenter` AT THE MOMENT THE NOTIFICATION ARRIVES. An unregistered value is
not an error: the banner simply renders with no action buttons. No exception, no log, no
crash — the notification just quietly does less than it was built to do.

That already happened. `notification_kinds.py` grew `CATEGORY_MATCH` ("match") with the
profile-match kind, and `AppDelegate.Category.all` was never updated, so every profile-match
push shipped without its View / Mark as Read buttons. `AppDelegate.swift`'s own doc comment
warns about exactly this and there was nothing enforcing it.

Source-scan guard, so it must not pass vacuously — see the anti-vacuity tests at the bottom.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APP_DELEGATE = _REPO / "frontend/ios/ios/Core/AppDelegate.swift"


def _ios_registered_categories() -> set[str]:
    """String literals inside `AppDelegate.Category`'s `all` array.

    Parsed from the enum body, comments stripped, so a value that is only MENTIONED in a
    comment (or defined but left out of `all`) does not count as registered — being in `all`
    is what actually reaches `setNotificationCategories`.
    """
    src = _APP_DELEGATE.read_text(encoding="utf-8")
    start = src.find("private enum Category {")
    assert start != -1, "AppDelegate.Category not found — this scan has drifted"
    depth, open_brace = 0, src.index("{", start)
    for end in range(open_brace, len(src)):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
            if depth == 0:
                break
    body = "\n".join(
        line for line in src[open_brace : end + 1].splitlines()
        if not line.strip().startswith("//")
    )

    constants = dict(re.findall(r'static let (\w+)\s*=\s*"([^"]+)"', body))
    all_match = re.search(r"static let all\s*=\s*\[([^\]]*)\]", body, re.S)
    assert all_match, "AppDelegate.Category.all not found — this scan has drifted"

    registered: set[str] = set()
    for name in (n.strip() for n in all_match.group(1).split(",")):
        if not name:
            continue
        assert name in constants, f"Category.all references unknown constant {name!r}"
        registered.add(constants[name])
    return registered


def _backend_categories() -> set[str]:
    from app.services.notification_kinds import NOTIFICATION_KINDS

    return {k.category for k in NOTIFICATION_KINDS.values()}


def _backend_thread_ids() -> set[str]:
    from app.services.notification_kinds import NOTIFICATION_KINDS

    return {k.thread_id for k in NOTIFICATION_KINDS.values()}


def test_every_backend_category_is_registered_by_ios():
    missing = sorted(_backend_categories() - _ios_registered_categories())
    assert not missing, (
        f"backend sends aps.category values iOS never registers: {missing}. "
        "iOS renders those notifications with NO action buttons, silently — no error and "
        "nothing in any log. Add them to AppDelegate.Category (constant AND `all`)."
    )


def test_every_backend_thread_id_is_registered_by_ios():
    """`AppDelegate`'s doc comment pins the contract to `thread_id`; both sets must agree."""
    missing = sorted(_backend_thread_ids() - _ios_registered_categories())
    assert not missing, f"backend thread_ids iOS never registers: {missing}"


def test_ios_registers_nothing_the_backend_never_sends():
    """Not fatal, but a stale identifier means a category was renamed on one side only —
    which is how the `match` gap would have looked one commit earlier."""
    stray = sorted(_ios_registered_categories() - _backend_categories() - _backend_thread_ids())
    assert not stray, (
        f"iOS registers categories the backend never sends: {stray}. Either the backend kind "
        "was removed and iOS was not updated, or one side was renamed."
    )


def test_the_profile_match_category_specifically_stays_registered():
    """The exact regression, pinned by value so a revert is a red test."""
    assert "match" in _ios_registered_categories(), (
        "AppDelegate.Category.all no longer contains 'match' — profile-match pushes will "
        "again arrive with no action buttons."
    )


# ── Anti-vacuity: prove the scan is really reading both sides ───────────────────


def test_the_scan_found_real_values_on_both_sides():
    ios = _ios_registered_categories()
    backend = _backend_categories()
    assert len(ios) >= 6, f"only parsed {len(ios)} iOS categories: {sorted(ios)}"
    assert len(backend) >= 5, f"only found {len(backend)} backend categories: {sorted(backend)}"
    # Every value should look like a wire identifier, not a Swift symbol name.
    for value in ios:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", value), f"{value!r} looks like a symbol, not a wire value"


def test_comment_only_mentions_do_not_count_as_registered():
    """The parser strips `//` lines. If it ever stopped, the `match` fix's own explanatory
    comment would make this test pass without `match` being in `all` — vacuous."""
    src = _APP_DELEGATE.read_text(encoding="utf-8")
    assert "//" in src, "AppDelegate has no comments — parser assumption changed"
    fake = "zzz_not_a_real_category"
    assert fake not in _ios_registered_categories()


def test_the_registry_is_non_empty():
    from app.services.notification_kinds import NOTIFICATION_KINDS

    kinds = list(NOTIFICATION_KINDS.values())
    assert len(kinds) >= 8, f"registry returned only {len(kinds)} kinds — scan drifted"
    for kind in kinds:
        assert kind.category, f"{kind.key} has an empty category"
        assert kind.thread_id, f"{kind.key} has an empty thread_id"
