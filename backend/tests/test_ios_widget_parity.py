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
from typing import Callable

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
        # Both were missing until 2026-09-30: `WidgetCause` had no CodingKeys at all (the
        # scanner asserted on them), and `WidgetMarketBrief` was simply never listed.
        ("WidgetCause", "WidgetCauseResponse"),
        ("WidgetMarketBrief", "WidgetMarketBriefResponse"),
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


# The 2026-09-30 contract: what the Holdings tile (name, counts, gainers / losers) and the
# Market tile (the Home pulse assets) are drawn from. Pinned in BOTH directions — the subset
# test above lets Swift silently ignore a field, which for these would leave the tile
# rendering "My Holdings" with no counts while the backend sends everything. 2026-10-14 adds
# the cause's `note` (the context line under an established cause's headline).
_CONTRACT_KEYS = {
    ("WidgetMoverSnapshot", "WidgetMoverPayload"): {
        "group_name", "holdings_count", "up_count", "down_count", "flat_count",
        "top_gainers", "top_losers", "market_assets",
    },
    ("WidgetIndex", "WidgetIndexResponse"): {"short_label", "rolling_24h"},
    # 2026-11-12: `cause_card` — the row links to the Updates card that holds its sources.
    ("WidgetMover", "WidgetMoverResponse"): {"rolling_24h", "cause_card"},
    ("WidgetCause", "WidgetCauseResponse"): {"detail_aged", "note"},
}


@pytest.mark.parametrize("pair", sorted(_CONTRACT_KEYS), ids=lambda p: p[0])
def test_the_new_contract_keys_are_on_both_sides(pair):
    swift_struct, pydantic_model = pair
    wanted = _CONTRACT_KEYS[pair]
    missing_backend = wanted - _pydantic_fields(pydantic_model)
    assert not missing_backend, f"{pydantic_model} does not send {sorted(missing_backend)}"
    missing_swift = wanted - _swift_coding_keys(swift_struct)
    assert not missing_swift, (
        f"{swift_struct} does not decode {sorted(missing_swift)} — the backend sends them and "
        f"the tile renders without them, silently"
    )


def test_the_snapshot_arrays_decode_lossily():
    """One malformed element must cost that element, not the tile.

    A strict `[WidgetMover]` failed the array on one bad row, the array failed the snapshot,
    and `read()` returned nil — the whole Home Screen tile fell back to its placeholder.
    """
    src = _strip_swift_comments(_swift_source())
    m = re.search(r"struct\s+WidgetMoverSnapshot\s*:.*?\n\}\n", src, re.S)
    assert m, "WidgetMoverSnapshot not found"
    for case, element in (
        ("runnersUp", "WidgetMover"), ("topGainers", "WidgetMover"),
        ("topLosers", "WidgetMover"), ("marketAssets", "WidgetIndex"),
    ):
        assert re.search(
            rf"decodeIfPresent\(LossyArray<{element}>\.self,\s*forKey:\s*\.{case}\)",
            m.group(0),
        ), f".{case} is not decoded through LossyArray<{element}>"
    ctx = re.search(r"struct\s+WidgetMarketContext\s*:.*?\n\}\n", src, re.S)
    assert ctx and re.search(
        r"decodeIfPresent\(LossyArray<WidgetIndex>\.self,\s*forKey:\s*\.indices\)", ctx.group(0)
    ), "WidgetMarketContext.indices is decoded strictly again"
    assert not re.search(r"decodeIfPresent\(\[", src), (
        "a strict array decode (`decodeIfPresent([T].self, …)`) is back in the wire model"
    )


# ── WidgetKit push entitlements (2026-11-27) ──────────────────────────
#
# The widget extension now carries `aps-environment` (WidgetKit push, iOS 26+), which must differ
# per configuration exactly as the app's does: a Debug build's widget push token belongs to the
# APNs sandbox, a distribution build's to production, and a `development` value in a TestFlight
# build makes every widget push fail SILENTLY. So the widget gets the app's Debug/Release split:
# a second entitlements file that REPLACES (never extends) the Debug one, selected per
# configuration in project.pbxproj. Each of these is the kind of drift that compiles, signs and
# ships, and is noticed only when nothing ever wakes.

_WIDGET_RELEASE_ENTITLEMENTS = _REPO / "frontend/ios/CaydexWidgets/CaydexWidgets-Release.entitlements"
_PBXPROJ = _REPO / "frontend/ios/ios.xcodeproj/project.pbxproj"
_APP_BUNDLE_ID = "com.phan.caydex"
_WIDGET_BUNDLE_ID = "com.phan.caydex.CaydexWidgets"

_ALL_ENTITLEMENTS = {
    "app-debug": _DEBUG_ENTITLEMENTS,
    "app-release": _RELEASE_ENTITLEMENTS,
    "widget-debug": _WIDGET_ENTITLEMENTS,
    "widget-release": _WIDGET_RELEASE_ENTITLEMENTS,
}
_APS = {
    "app-debug": "development", "widget-debug": "development",
    "app-release": "production", "widget-release": "production",
}


def _plist(path: Path) -> dict:
    assert path.exists(), f"missing {path} — the widget's Release entitlements must exist (no skip)"
    with path.open("rb") as fh:
        return plistlib.load(fh)


def _pbx_block(pbx: str, open_index: int, what: str) -> str:
    """The brace-balanced `{ … }` that opens at `open_index`."""
    depth = 0
    for i in range(open_index, len(pbx)):
        if pbx[i] == "{":
            depth += 1
        elif pbx[i] == "}":
            depth -= 1
            if depth == 0:
                return pbx[open_index : i + 1]
    raise AssertionError(f"unbalanced {what} block")  # pragma: no cover - a malformed project file


def _build_configurations(pbx: str) -> list[dict[str, str]]:
    """Each XCBuildConfiguration block, brace-bound, as {id, name, bundle_id, entitlements,
    deployment}. `deployment` is the block's OWN setting ("" when it inherits): resolve it through
    `_effective_deployment_targets`, never read it raw."""
    out = []
    for m in re.finditer(r"(\w+) /\* (\w+) \*/ = \{\s*isa = XCBuildConfiguration;", pbx):
        block = _pbx_block(pbx, pbx.index("{", m.start()), "XCBuildConfiguration")
        def _setting(key: str) -> str | None:
            s = re.search(rf"\b{key} = \"?([^\";]+)\"?;", block)
            return s.group(1) if s else None
        out.append({
            "id": m.group(1),
            "name": _setting("name") or m.group(2),
            "bundle_id": _setting("PRODUCT_BUNDLE_IDENTIFIER") or "",
            "entitlements": _setting("CODE_SIGN_ENTITLEMENTS") or "",
            "deployment": _setting("IPHONEOS_DEPLOYMENT_TARGET") or "",
        })
    return out


def _configuration_list_members(pbx: str, list_id: str) -> list[str]:
    """The XCBuildConfiguration ids an XCConfigurationList names, in order."""
    m = re.search(rf"\b{re.escape(list_id)} /\*[^*]*\*/ = \{{\s*isa = XCConfigurationList;", pbx)
    assert m, f"XCConfigurationList {list_id} not found — the project file's shape changed"
    block = _pbx_block(pbx, pbx.index("{", m.start()), "XCConfigurationList")
    members = re.search(r"buildConfigurations = \((.*?)\);", block, re.S)
    assert members, f"XCConfigurationList {list_id} has no buildConfigurations"
    return re.findall(r"(\w+) /\* \w+ \*/,", members.group(1))


def _effective_deployment_targets(pbx: str) -> dict[tuple[str, str], str]:
    """{(bundle id, configuration name): the deployment target Xcode USES} for every native target.

    A target configuration that does not set `IPHONEOS_DEPLOYMENT_TARGET` inherits the
    PROJECT-level configuration of the same name (the PBXProject's own configuration list), so
    reading the target block alone sees "" for both of today's targets and can never notice the
    project line being raised. "" here means set NOWHERE — Xcode then falls back to the SDK's
    own version, i.e. the newest iOS. (No `baseConfigurationReference` .xcconfig is in use;
    `test_no_xcconfig_can_set_the_deployment_target_unseen` keeps that true.)"""
    by_id = {c["id"]: c for c in _build_configurations(pbx)}
    project = re.search(r"\w+ /\* Project object \*/ = \{\s*isa = PBXProject;", pbx)
    assert project, "the PBXProject object is gone — the project file's shape changed"
    project_block = _pbx_block(pbx, pbx.index("{", project.start()), "PBXProject")
    project_list = re.search(r"\bbuildConfigurationList = (\w+) ", project_block)
    assert project_list, "the PBXProject names no buildConfigurationList"
    inherited = {
        by_id[cid]["name"]: by_id[cid]["deployment"]
        for cid in _configuration_list_members(pbx, project_list.group(1))
    }
    out: dict[tuple[str, str], str] = {}
    for target in re.finditer(r"\w+ /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;", pbx):
        block = _pbx_block(pbx, pbx.index("{", target.start()), "PBXNativeTarget")
        target_list = re.search(r"\bbuildConfigurationList = (\w+) ", block)
        assert target_list, "a PBXNativeTarget names no buildConfigurationList"
        for cid in _configuration_list_members(pbx, target_list.group(1)):
            config = by_id[cid]
            out[(config["bundle_id"], config["name"])] = (
                config["deployment"] or inherited.get(config["name"], "")
            )
    return out


def _deployment_problems(pbx: str) -> list[str]:
    """The widget and the app must each deploy to (at most) the committed iOS 18 floor, in BOTH
    configurations, and the widget to exactly the app's target — set somewhere, never inherited
    from the SDK's newest version."""
    floor = 18.0  # `test_ios_deployment_floor._MAX_ALLOWED_TARGET`
    effective = _effective_deployment_targets(pbx)
    problems = []
    for name in ("Debug", "Release"):
        app = effective.get((_APP_BUNDLE_ID, name))
        widget = effective.get((_WIDGET_BUNDLE_ID, name))
        for who, value in (("app", app), ("widget", widget)):
            if value is None:
                problems.append(f"no {name} configuration for the {who} target")
            elif not re.fullmatch(r"\d+(?:\.\d+)*", value):
                problems.append(
                    f"the {who}'s {name} deployment target is {value!r} — set nowhere, Xcode "
                    "uses the SDK's newest iOS"
                )
            elif float(".".join(value.split(".")[:2])) > floor:
                problems.append(
                    f"the {who}'s {name} deployment target is {value} — above the iOS {floor} "
                    "floor; WidgetKit push (iOS 26) stays behind @available, never a raised target"
                )
        if app is not None and widget is not None and app != widget:
            problems.append(f"the widget's {name} deployment target {widget!r} differs from the app's {app!r}")
    return problems


def _entitlement_path_problems(pbx: str) -> list[str]:
    problems = []
    configs = {(c["bundle_id"], c["name"]): c for c in _build_configurations(pbx) if c["bundle_id"]}
    wanted = {
        (_APP_BUNDLE_ID, "Debug"): "ios/ios.entitlements",
        (_APP_BUNDLE_ID, "Release"): "ios/ios-Release.entitlements",
        (_WIDGET_BUNDLE_ID, "Debug"): "CaydexWidgets/CaydexWidgets.entitlements",
        (_WIDGET_BUNDLE_ID, "Release"): "CaydexWidgets/CaydexWidgets-Release.entitlements",
    }
    for key, path in wanted.items():
        config = configs.get(key)
        if config is None:
            problems.append(f"no {key[1]} build configuration for {key[0]}")
        elif config["entitlements"] != path:
            problems.append(f"{key[0]} {key[1]} signs with {config['entitlements']!r}, expected {path!r}")
    return problems


@pytest.mark.parametrize("which", sorted(_ALL_ENTITLEMENTS))
def test_every_entitlements_file_declares_the_one_app_group(which):
    groups = list(_plist(_ALL_ENTITLEMENTS[which]).get(_APP_GROUPS_KEY) or [])
    assert groups == [_swift_app_group()], (
        f"{_ALL_ENTITLEMENTS[which].name} declares {groups}; every file must declare exactly "
        f"[{_swift_app_group()!r}] — the Release file REPLACES the Debug one"
    )


@pytest.mark.parametrize("which", sorted(_ALL_ENTITLEMENTS))
def test_aps_environment_matches_the_configuration(which):
    got = _plist(_ALL_ENTITLEMENTS[which]).get("aps-environment")
    assert got == _APS[which], (
        f"{_ALL_ENTITLEMENTS[which].name}: aps-environment is {got!r}, expected {_APS[which]!r}. "
        "A sandbox value in a distribution build (or the reverse) makes every push fail silently."
    )


@pytest.mark.parametrize("which", sorted(_ALL_ENTITLEMENTS))
def test_no_entitlements_file_declares_keychain_access_groups(which):
    """Its first entry would become the DEFAULT Keychain group: KeychainService (the session
    tokens) and GuestIdentity write with no group, so they would move into it — and listing the
    app's own group on the extension would hand it the refresh token (auth.md §8)."""
    assert "keychain-access-groups" not in _plist(_ALL_ENTITLEMENTS[which])


def test_each_configuration_signs_with_its_own_entitlements_file():
    problems = _entitlement_path_problems(_PBXPROJ.read_text(encoding="utf-8"))
    assert problems == [], problems


def test_the_entitlement_path_scan_bites():
    """MUTATION_LOG, executable: the widget's two paths swapped (the realistic slip — one
    configuration edited, the other copied) must turn the scan red; so must the Release one
    pointed back at the Debug file."""
    pbx = _PBXPROJ.read_text(encoding="utf-8")
    debug = "CODE_SIGN_ENTITLEMENTS = CaydexWidgets/CaydexWidgets.entitlements;"
    release = 'CODE_SIGN_ENTITLEMENTS = "CaydexWidgets/CaydexWidgets-Release.entitlements";'
    assert pbx.count(debug) == 1 and pbx.count(release) == 1, "the widget's entitlement lines moved"
    swapped = pbx.replace(debug, "@@D@@").replace(release, debug).replace("@@D@@", release)
    assert _entitlement_path_problems(swapped)
    assert _entitlement_path_problems(pbx.replace(release, debug))


def test_the_widget_target_still_deploys_to_ios_18():
    """WidgetKit push is iOS 26 API, behind `@available`/`#available` — the extension must not
    have been raised to 26 to make it compile, which would drop the widget on iOS 18-25.

    Resolved through inheritance (2026-11-27 review): both widget configurations set NO target of
    their own, so the first version of this test read "" and accepted it — raising the PROJECT
    line to 26.0, the realistic slip, stayed green."""
    pbx = _PBXPROJ.read_text(encoding="utf-8")
    effective = _effective_deployment_targets(pbx)
    widget = {k: v for k, v in effective.items() if k[0] == _WIDGET_BUNDLE_ID}
    assert set(widget) == {(_WIDGET_BUNDLE_ID, "Debug"), (_WIDGET_BUNDLE_ID, "Release")}, widget
    assert _deployment_problems(pbx) == []


# The widget's Release configuration, as the project file spells its one unique line.
_WIDGET_RELEASE_LINE = 'CODE_SIGN_ENTITLEMENTS = "CaydexWidgets/CaydexWidgets-Release.entitlements";'
_PROJECT_TARGET_LINE = "IPHONEOS_DEPLOYMENT_TARGET = 18.0;"

_DEPLOYMENT_MUTATIONS: list[tuple[str, Callable[[str], str]]] = [
    # The reviewer's case: the project-level line raised (both configurations inherit it).
    ("project raised to 26.0", lambda pbx: pbx.replace(_PROJECT_TARGET_LINE, "IPHONEOS_DEPLOYMENT_TARGET = 26.0;")),
    # Only the project's Release raised — the shipped build, the one nobody runs locally.
    ("project Release raised", lambda pbx: pbx[::-1].replace(
        _PROJECT_TARGET_LINE[::-1], "IPHONEOS_DEPLOYMENT_TARGET = 26.0;"[::-1], 1)[::-1]),
    # An explicit 26.0 written on the widget's Release configuration alone.
    ("widget Release set to 26.0", lambda pbx: pbx.replace(
        _WIDGET_RELEASE_LINE, _WIDGET_RELEASE_LINE + "\n\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 26.0;")),
    # The project line deleted: set nowhere, so Xcode falls back to the SDK's newest iOS.
    ("project line deleted", lambda pbx: pbx.replace("\t\t\t\t" + _PROJECT_TARGET_LINE + "\n", "")),
    # The widget lowered below the app: it would install where the app cannot.
    ("widget Release lowered", lambda pbx: pbx.replace(
        _WIDGET_RELEASE_LINE, _WIDGET_RELEASE_LINE + "\n\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 17.0;")),
]


@pytest.mark.parametrize("label, mutate", _DEPLOYMENT_MUTATIONS, ids=[m[0] for m in _DEPLOYMENT_MUTATIONS])
def test_the_deployment_scan_bites(label, mutate):
    """MUTATION_LOG, executable: each regression applied to an in-memory COPY turns it red."""
    pbx = _PBXPROJ.read_text(encoding="utf-8")
    assert pbx.count(_PROJECT_TARGET_LINE) == 2, "the project-level deployment lines moved"
    assert pbx.count(_WIDGET_RELEASE_LINE) == 1, "the widget's Release configuration moved"
    mutated = mutate(pbx)
    assert mutated != pbx, f"{label}: the mutation did not apply"
    assert _deployment_problems(mutated), f"{label}: the deployment scan stayed green"


def test_no_xcconfig_can_set_the_deployment_target_unseen():
    """`_effective_deployment_targets` resolves target → project only. An .xcconfig attached as a
    `baseConfigurationReference` sits between the two and is invisible to it."""
    assert "baseConfigurationReference" not in _PBXPROJ.read_text(encoding="utf-8"), (
        "a configuration now has a base .xcconfig — teach _effective_deployment_targets to read "
        "it before trusting the deployment guard"
    )


def test_the_apns_topic_is_the_app_not_the_widget():
    """WidgetKit push uses the APP's bundle id + `.push-type.widgets` as its topic. A topic built
    from the widget's bundle id answers DeviceTokenNotForTopic for every device."""
    from app.config import Settings

    pbx = _PBXPROJ.read_text(encoding="utf-8")
    bundle_ids = {c["bundle_id"] for c in _build_configurations(pbx) if c["bundle_id"]}
    assert {_APP_BUNDLE_ID, _WIDGET_BUNDLE_ID} <= bundle_ids
    apns = Settings.model_fields["APNS_BUNDLE_ID"].default
    assert apns == _APP_BUNDLE_ID and apns != _WIDGET_BUNDLE_ID
