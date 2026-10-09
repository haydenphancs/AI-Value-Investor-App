"""The active-group hint must never hold anything but a real group id.

`PortfolioStore` saves the active group's id in UserDefaults (`TrackingView.activePortfolioId`)
and, at the next launch, `TrackingViewModel` starts the insights request for that id before
`GET /portfolios` answers. Store-screenshot mode (DEBUG) answers `GET /portfolios` with a sample
group whose id is `store-shot-sample`. Writing that id into the real hint sent it to production
on the next normal launch: `GET /portfolios/store-shot-sample/insights` → 500 (Sentry, 2026-10-08).

Pinned here (comments stripped, scans brace-bound to the declaration, per testing.md):
  1. every write of the key goes through `storeActiveIdHint`;
  2. that helper returns early in store-screenshot mode, inside `#if DEBUG`;
  3. the launch read goes through `readActiveIdHint`, which drops a non-UUID value.
"""

from __future__ import annotations

import re
from pathlib import Path

_STORE = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "ios" / "ios" / "Core" / "Services" / "PortfolioStore.swift"
)


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code() -> str:
    assert _STORE.exists(), f"{_STORE} is gone — this scan has drifted"
    return _strip_comments(_STORE.read_text())


def _block_after(src: str, anchor: str) -> str:
    start = src.find(anchor)
    assert start != -1, f"{anchor!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    raise AssertionError(f"unbalanced braces after {anchor!r}")


_WRITE_RE = re.compile(
    r"UserDefaults\.standard\.(?:set\([^)]*|removeObject\()\s*forKey:\s*(?:Self\.)?activeIdKey"
)


def test_every_write_of_the_hint_goes_through_the_helper():
    src = _code()
    helper = _block_after(src, "private static func storeActiveIdHint(")
    reader = _block_after(src, "private static func readActiveIdHint(")
    outside = src.replace(helper, "").replace(reader, "")
    stray = _WRITE_RE.findall(outside)
    assert not stray, f"active-group hint written outside storeActiveIdHint: {stray}"
    # The call sites exist, so the scan above is not vacuous.
    assert len(re.findall(r"Self\.storeActiveIdHint\(", src)) >= 4


def test_the_helper_skips_the_write_in_store_screenshot_mode():
    helper = _block_after(_code(), "private static func storeActiveIdHint(")
    guard = re.search(
        r"#if DEBUG\s*\n\s*if StoreScreenshotMode\.isOn, id != nil \{ return \}\s*\n\s*#endif", helper
    )
    assert guard, (
        "storeActiveIdHint must return early under StoreScreenshotMode.isOn for a WRITE "
        "(id != nil) only, inside #if DEBUG"
    )
    first_write = helper.find("UserDefaults.standard")
    assert first_write != -1 and guard.start() < first_write, "the guard must precede every write"


def test_the_session_end_removal_is_not_skipped_in_screenshot_mode():
    """`reset()` passes nil at session end; a device-global key must not outlive its session
    (auth.md §7), screenshot mode or not. A bare `if StoreScreenshotMode.isOn { return }`
    would skip the removal too (review 2026-10-08)."""
    src = _code()
    helper = _block_after(src, "private static func storeActiveIdHint(")
    assert not re.search(r"if StoreScreenshotMode\.isOn \{ return \}", helper)
    assert "removeObject(forKey: activeIdKey)" in helper
    reset = _block_after(src, "func reset()")
    assert "Self.storeActiveIdHint(nil)" in reset


def test_the_launch_read_drops_a_non_uuid_hint():
    src = _code()
    init = _block_after(src, "private init(apiClient: APIClient")
    assert "Self.readActiveIdHint()" in init
    assert "UserDefaults" not in init, "init must read the hint only through readActiveIdHint"
    reader = _block_after(src, "private static func readActiveIdHint(")
    assert re.search(r"guard UUID\(uuidString: stored\) != nil else \{", reader)
    assert "removeObject(forKey: activeIdKey)" in reader
