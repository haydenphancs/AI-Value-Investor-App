"""The widget's App Group identifier must be byte-identical everywhere it appears.

A mismatch is SILENT in both directions. `UserDefaults(suiteName:)` returns nil for an
unknown suite rather than failing, so the app writes into nothing and the widget reads
nothing — and the widget's failure mode is not an error state, it is the placeholder,
sitting on the Home Screen looking like the feature was never built. Nothing is logged
on the iOS side beyond the one `Logger.error` in `WidgetSnapshotStore`, which nobody
reads from a shipped build.

The identifier lives in four places and they drift independently:

  1. `ios/ios.entitlements`          (Debug)
  2. `ios/ios-Release.entitlements`  (Release — REPLACES the Debug file, does not extend it)
  3. `WidgetSharedConfig.appGroupIdentifier` in Swift
  4. the widget extension's own entitlements, once that target exists

(2) is the one that has already bitten this project: `ios-Release.entitlements` carries a
warning at the top precisely because Sign in with Apple was once lost in that split, so a
capability present in Debug was missing from every shipped build.

Also pinned here: the wire contract between `schemas/widget.py` and the Swift DTO. A
`CodingKeys` entry that stops matching a Pydantic field name does not warn — it throws at
decode time, in an extension, where nobody sees it.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
# NOT under `ios/Core/Services/` despite being shared infrastructure, which is where
# `.claude/rules/ios-swiftui.md` would otherwise put it. The Xcode project uses
# file-system-synchronized groups (objectVersion 77): a folder is synchronized to a
# TARGET, so anything inside `ios/` belongs to the app target alone. A file both the app
# and the widget extension compile has to live in its own synchronized group, and
# `frontend/ios/Shared/` is it.
_STORE = _REPO / "frontend/ios/Shared/WidgetSnapshotStore.swift"

_DEBUG_ENTITLEMENTS = _IOS / "ios.entitlements"
_RELEASE_ENTITLEMENTS = _IOS / "ios-Release.entitlements"
# Created with the widget target; asserted only once it exists so this file is useful
# before that lands and becomes stricter automatically afterwards.
_WIDGET_ENTITLEMENTS = _REPO / "frontend/ios/CaydexWidgets/CaydexWidgets.entitlements"

_APP_GROUPS_KEY = "com.apple.security.application-groups"


def _swift_source() -> str:
    assert _STORE.exists(), f"missing {_STORE}"
    return _STORE.read_text()


def _strip_swift_comments(src: str) -> str:
    """Drop `//` lines and `/* */` blocks.

    Load-bearing, not hygiene: `WidgetSnapshotStore.swift` names the App Group inside a
    long explanatory comment about why the widget must not read the Keychain. A scan
    that reads documentation as declarations would find the right string for the wrong
    reason and keep passing after someone changed the actual constant.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        re.sub(r"//.*$", "", line) for line in src.splitlines()
        if not line.strip().startswith("//")
    )


def _swift_app_group() -> str:
    m = re.search(
        r'\bappGroupIdentifier\s*=\s*"([^"]+)"', _strip_swift_comments(_swift_source())
    )
    assert m, (
        "WidgetSharedConfig.appGroupIdentifier not found in WidgetSnapshotStore.swift. "
        "If it was renamed, update this test — do not delete the assertion."
    )
    return m.group(1)


def _plist_app_groups(path: Path) -> list[str]:
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    return list(data.get(_APP_GROUPS_KEY) or [])


# ── App Group parity ──────────────────────────────────────────────────


def test_swift_declares_an_app_group():
    group = _swift_app_group()
    assert group.startswith("group."), (
        f"App Group ids must start with 'group.' — iOS rejects the entitlement "
        f"otherwise. Got {group!r}"
    )


@pytest.mark.parametrize(
    "path", [_DEBUG_ENTITLEMENTS, _RELEASE_ENTITLEMENTS], ids=["debug", "release"]
)
def test_both_app_entitlements_declare_the_group(path: Path):
    groups = _plist_app_groups(path)
    assert groups, (
        f"{path.name} has no {_APP_GROUPS_KEY}. The Release file REPLACES the Debug one "
        f"rather than extending it, so a group present only in Debug is absent from "
        f"every shipped build — that is how applesignin was lost once already."
    )
    assert _swift_app_group() in groups, (
        f"{path.name} declares {groups} but Swift uses {_swift_app_group()!r}. "
        f"UserDefaults(suiteName:) returns nil on a mismatch, silently."
    )


def test_debug_and_release_declare_exactly_the_same_groups():
    assert _plist_app_groups(_DEBUG_ENTITLEMENTS) == _plist_app_groups(
        _RELEASE_ENTITLEMENTS
    ), "Debug and Release App Groups diverged — Release is what ships."


def test_widget_entitlements_match_once_the_target_exists():
    if not _WIDGET_ENTITLEMENTS.exists():
        pytest.skip("widget extension target not created yet")
    assert _swift_app_group() in _plist_app_groups(_WIDGET_ENTITLEMENTS), (
        "the widget extension's entitlements do not include the shared App Group — the "
        "extension will read an empty suite and render its placeholder forever."
    )


def test_guest_identity_still_has_no_keychain_access_group():
    """The trap that would cost every existing guest their data.

    `GuestIdentity` stores the per-install id with no `kSecAttrAccessGroup`. Adding one
    so an extension could read it makes the EXISTING read miss, `current` mints a fresh
    UUID, and the next write stores it in the new group — silently abandoning that
    install's watchlist, portfolios, chats and Learn progress, with no recovery path
    (the orphaned rows are service-role-only).

    The widget must never need this: it reads a snapshot the app wrote.
    """
    src = _IOS / "Core/Services/GuestIdentity.swift"
    if not src.exists():
        pytest.skip("GuestIdentity.swift not found")
    assert "kSecAttrAccessGroup" not in _strip_swift_comments(src.read_text()), (
        "GuestIdentity gained a Keychain access group. If this was to let the widget "
        "read the guest id, revert it: it rotates every existing install's identity. "
        "The widget reads the App Group snapshot instead."
    )


# ── wire contract ─────────────────────────────────────────────────────


def _pydantic_fields(model_name: str) -> set[str]:
    import importlib

    mod = importlib.import_module("app.schemas.widget")
    return set(getattr(mod, model_name).model_fields.keys())


def _swift_coding_keys(struct: str) -> set[str]:
    """The wire names a Swift struct's CodingKeys map to."""
    src = _strip_swift_comments(_swift_source())
    m = re.search(rf"struct\s+{struct}\s*:.*?\n\}}\n", src, re.S)
    assert m, f"struct {struct} not found in WidgetSnapshotStore.swift"
    body = m.group(0)
    ck = re.search(r"enum CodingKeys[^{]*\{(.*?)\}", body, re.S)
    assert ck, f"{struct} has no CodingKeys — snake_case would not decode"
    keys: set[str] = set()
    for line in ck.group(1).splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for part in line[len("case "):].split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                keys.add(part.split("=", 1)[1].strip().strip('"'))
            else:
                keys.add(part.strip())
    return keys


@pytest.mark.parametrize(
    "swift_struct, pydantic_model",
    [
        ("WidgetMoverSnapshot", "WidgetMoverPayload"),
        ("WidgetMover", "WidgetMoverResponse"),
        ("WidgetBasket", "WidgetBasketResponse"),
        ("WidgetMoveContext", "WidgetMoveContextResponse"),
        ("WidgetMarketContext", "WidgetMarketContextResponse"),
        ("WidgetIndex", "WidgetIndexResponse"),
    ],
)
def test_swift_decodes_every_field_the_backend_sends(swift_struct, pydantic_model):
    """Swift may ignore fields it does not need, but must never MISNAME one.

    A `CodingKeys` entry that does not match a Pydantic field name is not a warning —
    it is a `keyNotFound` at decode time inside an extension, which renders as the
    placeholder with no user-visible error.
    """
    swift_keys = _swift_coding_keys(swift_struct)
    backend_keys = _pydantic_fields(pydantic_model)
    unknown = swift_keys - backend_keys
    assert not unknown, (
        f"{swift_struct} expects {sorted(unknown)}, which {pydantic_model} does not send. "
        f"Backend fields: {sorted(backend_keys)}"
    )


def test_the_cause_kinds_agree_in_both_directions():
    """iOS switches on this to decide whether it may show a "why" badge at all.

    A backend kind Swift has never heard of decodes to `.none` rather than throwing,
    so a divergence does not crash — it silently downgrades a real cause to "nothing
    identifiable", which is exactly the kind of quiet wrongness this file exists for.
    """
    from app.services.daily_move_attribution import CauseKind

    backend = {k.value for k in CauseKind}
    src = _strip_swift_comments(_swift_source())
    m = re.search(r"enum WidgetCauseKind[^{]*\{(.*?)\n\}", src, re.S)
    assert m, "WidgetCauseKind not found in Swift"
    body = m.group(1)
    swift = set()
    for case, raw in re.findall(r'case\s+(\w+)(?:\s*=\s*"([^"]+)")?', body):
        swift.add(raw or case)
    assert swift == backend, (
        f"cause kinds diverged — Swift {sorted(swift)} vs backend {sorted(backend)}"
    )


def test_every_optional_payload_key_is_decoded_leniently():
    """The new-app-against-old-backend guarantee, and nothing else pins it.

    The widget ships inside an app update; the backend deploys independently, so a user
    can run a new binary against a backend that has not shipped the new fields yet. A
    plain `decode(...)` on an absent key throws `keyNotFound`, `read()` catches it and
    returns nil, and the user gets the PLACEHOLDER on their Home Screen — with no error
    surface, no retry affordance, and no crash report anyone would think to send.

    `runners_up` set the precedent with a custom `init(from:)`; this asserts every
    optional field on the payload follows it. Only fields the backend can genuinely omit
    are checked: `mode`, `as_of` and `market_session` are required on both sides.
    """
    src = _strip_swift_comments(_swift_source())
    m = re.search(r"struct\s+WidgetMoverSnapshot\s*:.*?\n\}\n", src, re.S)
    assert m, "WidgetMoverSnapshot not found"
    init = re.search(r"public init\(from decoder: Decoder\) throws \{(.*?)\n    \}", m.group(0), re.S)
    assert init, "WidgetMoverSnapshot has no custom init(from:) — optional keys would be strict"
    body = init.group(1)

    import importlib
    mod = importlib.import_module("app.schemas.widget")
    fields = mod.WidgetMoverPayload.model_fields

    required = {"mode", "as_of", "market_session"}
    optional = {
        name for name, f in fields.items()
        if name not in required and not f.is_required()
    }
    assert optional, "expected some optional payload fields — did the schema change?"

    for wire_name in sorted(optional):
        # Find the CodingKeys case that maps to this wire name, then assert the init
        # reads it leniently.
        case = re.search(
            rf"case\s+(\w+)\s*=\s*\"{re.escape(wire_name)}\"", m.group(0)
        )
        swift_case = case.group(1) if case else wire_name
        assert re.search(rf"decodeIfPresent\([^)]*forKey:\s*\.{swift_case}\b", body), (
            f"`{wire_name}` (.{swift_case}) is not read with decodeIfPresent — a backend "
            f"that omits it would fail the WHOLE decode and blank the widget"
        )
