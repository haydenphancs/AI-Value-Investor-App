"""Source-scan guards: the Holdings widget grant, extension half (2026-11-27).

The Holdings tile changed only while the app ran: the extension fetched Market only, could not
write the Holdings slot, and `/widget/portfolio-mover` needs a session the extension must never
hold. The fix gives each device with a Movers widget a HOLDINGS GRANT — an opaque, 30-day,
server-revocable key minted only inside a refresh-token rotation (owner: "Own key, 30-day life
(Recommended)") — and lets the extension refresh the Holdings slot the app created with it.

What a regression here would cost, guard by guard:

  keychain_item        the key in a synchronizable, backed-up or app-ID Keychain item travels to
                       another phone, or lands beside the session tokens;
  no_access_group_ent  a `keychain-access-groups` entitlement makes its first entry the DEFAULT
                       Keychain group, so `KeychainService`'s session tokens (written with no
                       group) move where the extension can read them (auth.md §8);
  api_split            the extension minting, storing, marking or revoking a grant, or the app
                       writing the extension's refusal note — one writer per key, or the
                       cross-process races of the v2 store come back;
  holdings_branch      the extension fetching Holdings without a grant for the SLOT's account,
                       using a payload after the session ended or the grant was replaced, or
                       hiding the tile on a refusal of a grant the app already renewed — and the
                       Market branch drifting while this was added;
  lapsed_branch        an expired grant spends a request, or hides a renewed grant's tile;
  slot_owner_render    a Holdings slot rendered for an account other than the signed-in one;
  extension_write      the extension creating or re-owning the slot, replacing a newer snapshot,
                       storing past the last-moment fence, or keeping bytes written across a
                       sign-out (the compensating delete);
  extension_drop       a refusal dropping a slot the app has since given a new grant, or a
                       drop that reloads (a loop inside `timeline()`);
  fetcher              the key sent over plain HTTP or (shipped) to a non-production host, on a
                       cached or shared session, as `Authorization`, or a 401 DENY-list that
                       wipes every tile on one misshapen deploy (only three codes mean refused);
  no_key_in_logs       the key interpolated into a log line or a grant's description.

Comments are stripped and every scan is brace-bound to its declaration (`.claude/rules/
testing.md` §3). Rule 3 is executable: `_MUTATIONS` applies each plausible regression to an
in-memory COPY and asserts its guard turns red for the right reason — the files on disk are
never touched. Only `swiftc -parse` runs in a subagent; the behaviour is proven by the main
session's canonical build and the owner's device checks.

The APP half (mint flag, lossy `AuthResponse` decode, foreground-only forced refresh, adoption,
revocation, the `.fresh` purge, `clearForEndedSession`) is pinned beside the app code it guards.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Callable

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios"
_APP_DIR = _IOS / "ios"
_WIDGETS_DIR = _IOS / "CaydexWidgets"

_PATHS = {
    "grant": _IOS / "Shared" / "WidgetHoldingsGrant.swift",
    "fetcher": _IOS / "Shared" / "WidgetHoldingsFetcher.swift",
    "store": _IOS / "Shared" / "WidgetSnapshotStore.swift",
    "apiconfig": _IOS / "Shared" / "WidgetAPIConfig.swift",
    "widget": _WIDGETS_DIR / "MoversWidget.swift",
    "push": _WIDGETS_DIR / "MoversPushHandler.swift",
    "ent_app_debug": _APP_DIR / "ios.entitlements",
    "ent_app_release": _APP_DIR / "ios-Release.entitlements",
    "ent_widget_debug": _WIDGETS_DIR / "CaydexWidgets.entitlements",
    "ent_widget_release": _WIDGETS_DIR / "CaydexWidgets-Release.entitlements",
    "auth_service": _APP_DIR / "Core" / "Services" / "AuthService.swift",
    "guest_identity": _APP_DIR / "Core" / "Services" / "GuestIdentity.swift",
}
_ENTITLEMENT_KEYS = ("ent_app_debug", "ent_app_release", "ent_widget_debug", "ent_widget_release")

# The three 401 codes `get_holdings_grant_caller` answers for a dead key. Everything else is
# "unavailable" on the device (auth.md §3: AUTH_REQUIRED never clears anything).
_REFUSAL_CODES = {"AUTH_TOKEN_INVALID", "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND"}
# The agreed cross-team contract (the backend's `HOLDINGS_GRANT_HEADER`).
_HEADER = "X-Caydex-Holdings-Token"


# ── Scanning helpers ─────────────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, keeping line structure.

    `\\s//`, not a bare `//`, for a trailing comment: a bare one would eat `https://` in a
    literal. The comments beside this change quote nearly every token the scans look for.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(
        "" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line)
        for line in src.splitlines()
    )


def _balanced(code: str, open_index: int, opener: str = "{", closer: str = "}") -> str:
    if open_index < 0 or open_index >= len(code) or code[open_index] != opener:
        raise LookupError(f"expected {opener!r} at {open_index}")
    depth = 0
    for i in range(open_index, len(code)):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return code[open_index : i + 1]
    raise LookupError(f"unbalanced {opener}{closer} at {open_index}")


def _block(src: str, header: str) -> str:
    """The brace-balanced body after `header`, in comment-stripped source."""
    code = _strip_comments(src)
    start = code.find(header)
    if start == -1:
        raise LookupError(f"{header!r} not found — this scan has drifted")
    return _balanced(code, code.index("{", start))


def _block_re(src: str, pattern: str) -> str:
    """The brace-balanced body opened by the `{` that ends the first match of `pattern`."""
    code = _strip_comments(src)
    m = re.search(pattern, code, re.S)
    if not m:
        raise LookupError(f"/{pattern}/ not found — this scan has drifted")
    return _balanced(code, m.end() - 1)


def _swift_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.swift") if p.is_file())


def _sources() -> dict[str, str]:
    src = {key: path.read_text(encoding="utf-8") for key, path in _PATHS.items()}
    named = {p.resolve() for p in _PATHS.values()}
    # Every other extension file, so a NEW file under CaydexWidgets/ cannot dodge the split.
    src["extension_rest"] = "\n".join(
        _strip_comments(p.read_text(encoding="utf-8"))
        for p in _swift_files(_WIDGETS_DIR) if p.resolve() not in named
    )
    # Every app-target file, comment-stripped, for the other half of the split.
    src["app_all"] = "\n".join(
        _strip_comments(p.read_text(encoding="utf-8")) for p in _swift_files(_APP_DIR)
    )
    return src


def _safe(fn: Callable[[dict[str, str]], list[str]], src: dict[str, str]) -> list[str]:
    try:
        return fn(src)
    except (LookupError, ValueError) as exc:
        return [f"scan could not find its anchor: {exc}"]


def _order(body: str, *needles: str) -> bool:
    """Every needle present, each after the one before."""
    at = -1
    for needle in needles:
        nxt = body.find(needle, at + 1)
        if nxt == -1:
            return False
        at = nxt
    return True


# ── 1. The Keychain item ─────────────────────────────────────────────────────────────────


def _problems_keychain_item(src: dict[str, str]) -> list[str]:
    problems = []
    grant = src["grant"]
    code = _strip_comments(grant)
    base = _block(grant, "private static func baseQuery(account: String?) -> [String: Any]")
    if "kSecAttrAccessGroup as String: accessGroup" not in base:
        problems.append("the grant's Keychain queries no longer name the App Group as access group")
    if "kSecAttrSynchronizable as String: false" not in base:
        problems.append("the grant item may synchronize through iCloud Keychain")
    if not re.search(r'static let keychainService = "com\.phan\.caydex\.widget-holdings"', code):
        problems.append(
            "the grant's Keychain service changed — it must stay distinct from KeychainService's "
            "bundle-id service so neither store can touch the other's items"
        )
    if not re.search(
        r"static var accessGroup: String \{\s*WidgetSharedConfig\.appGroupIdentifier\s*\}", code
    ):
        problems.append("the access group is no longer the App Group constant")
    # ONE place builds a query: every Sec* call works on a dictionary from `baseQuery`, so the
    # access group and the no-sync flag cannot be forgotten by a second, hand-built query.
    for token in ("kSecClass as String", "kSecAttrAccessGroup", "kSecAttrSynchronizable"):
        if code.count(token) != 1:
            problems.append(f"{token} appears {code.count(token)} times — every query must come from baseQuery")
    store = _block(grant, "static func store(_ g: Grant) throws")
    accessible = re.findall(r"kSecAttrAccessible\w*", store)
    wanted = {"kSecAttrAccessible", "kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly"}
    if set(accessible) != wanted or store.count("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly") != 2:
        problems.append(
            "store() does not set AfterFirstUnlockThisDeviceOnly on BOTH the add and the update — "
            f"found {sorted(set(accessible))}; a backed-up item restores the key to another phone"
        )
    loose = re.findall(r"kSecAttrAccessible(?:Always|WhenUnlocked|AfterFirstUnlock)(?!ThisDeviceOnly)\w*", code)
    if loose:
        problems.append(f"a non-ThisDeviceOnly accessibility is named: {sorted(set(loose))}")
    if not _order(store, "isWellFormedToken(g.token)", "fingerprint(of: g.token)", "SecItemAdd("):
        problems.append("store() writes before checking the key's shape and its fingerprint")
    read = _block(grant, "private static func readItem(fingerprint fp: String) -> ReadResult")
    if "fingerprint(of: stored.token) == fp" not in read:
        problems.append("a stored item is trusted without checking its key matches its account's fingerprint")
    return problems


# ── 2. No `keychain-access-groups`, anywhere ─────────────────────────────────────────────


def _problems_no_access_group_entitlement(src: dict[str, str]) -> list[str]:
    problems = []
    for key in _ENTITLEMENT_KEYS:
        data = plistlib.loads(src[key].encode("utf-8"))
        if "keychain-access-groups" in data:
            problems.append(
                f"{_PATHS[key].name} declares keychain-access-groups — its first entry becomes a "
                "DEFAULT Keychain group and the session tokens can move into it"
            )
    keychain = _block(src["auth_service"], "final class KeychainService")
    if "kSecAttrAccessGroup" in keychain:
        problems.append("KeychainService (the session tokens) gained an access group")
    if "kSecAttrAccessGroup" in _strip_comments(src["guest_identity"]):
        problems.append("GuestIdentity gained an access group — every install's guest data is orphaned")
    return problems


# ── 3. Who may call what ─────────────────────────────────────────────────────────────────

# The APP mints, stores, marks, sweeps, deletes and revokes; the extension never does.
_APP_ONLY = (
    "WidgetHoldingsGrant.store(", "WidgetHoldingsGrant.setMarker(", "WidgetHoldingsGrant.removeMarker(",
    "WidgetHoldingsGrant.pendingKeys(", "WidgetHoldingsGrant.delete(", "requestOnNextRefresh",
    "WidgetHoldingsFetcher.revoke(",
)
# The EXTENSION writes its refusal note and its own fenced slot writes; the app never does.
_EXTENSION_ONLY = (
    "WidgetHoldingsGrant.noteRefused(", "writeHoldingsFromExtension(", "dropPortfolioFromExtension(",
    "WidgetHoldingsFetcher.fetchHoldings(",
)


def _problems_api_split(src: dict[str, str]) -> list[str]:
    problems = []
    extension = "\n".join(
        _strip_comments(src[k]) for k in ("widget", "push")
    ) + "\n" + src["extension_rest"]
    for call in _APP_ONLY:
        if call in extension:
            problems.append(f"the extension calls {call} — only the app may")
    for call in _EXTENSION_ONLY:
        if call in src["app_all"]:
            problems.append(f"the app calls {call} — only the extension may")
    # Inside Shared, only the store's fence reads the grant (the marker), never mutates it.
    store = _strip_comments(src["store"])
    for call in _APP_ONLY + ("WidgetHoldingsGrant.noteRefused(",):
        if call in store:
            problems.append(f"WidgetSnapshotStore calls {call}")
    return problems


# ── 4. The Holdings branch of the timeline ───────────────────────────────────────────────

_TIMELINE = "func timeline(for configuration: MoversConfigurationIntent, in context: Context)"

# The Market branch, byte for byte (comments stripped, whitespace normalised): the Holdings
# work was added BESIDE it, and its own guards (`test_ios_widget_self_refresh.py`,
# `test_ios_widget_extension_guards.py`) assume it never moved.
_MARKET_BRANCH = """
if mode == .market, let fresh = await WidgetMarketFetcher.fetchMarket() {
if WidgetAPIConfig.widgetToken == tokenAtFetch {
snap = fresh
WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)
} else {
Self.log.warning("widget: market refresh discarded — the widget token changed while it was in flight")
}
}
"""

_FENCE = re.compile(
    r"if\s+WidgetAPIConfig\.widgetToken\s*==\s*tokenAtFetch\s*,\s*"
    r"WidgetHoldingsGrant\.markerFingerprint\s*==\s*(\w+)\.fp\s*\{"
)


def _normalise(block: str) -> str:
    return "\n".join(line.strip() for line in block.splitlines() if line.strip())


def _case_body(switch_body: str, case: str) -> str:
    """The text of one `case …:` arm, up to the next `case`/`default` at the same depth."""
    m = re.search(rf"\bcase\s+{re.escape(case)}\b[^:\n]*:", switch_body)
    if not m:
        raise LookupError(f"case {case} not found")
    rest = switch_body[m.end():]
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return rest[:i]
            depth -= 1
        elif depth == 0 and re.match(r"\s*\b(?:case|default)\b", rest[i:]) and rest[i - 1 : i] == "\n":
            return rest[:i]
    return rest


def _problems_holdings_branch(src: dict[str, str]) -> list[str]:
    problems = []
    timeline = _block(src["widget"], _TIMELINE)

    if _normalise(_MARKET_BRANCH) not in _normalise(timeline):
        problems.append("the Market branch is no longer byte-for-byte what it was")

    if timeline.count("WidgetHoldingsFetcher.fetchHoldings(") != 1:
        problems.append(
            f"expected exactly ONE fetchHoldings( in timeline(), found "
            f"{timeline.count('WidgetHoldingsFetcher.fetchHoldings(')}"
        )
    portfolio = re.search(r"\bif\s+mode\s*==\s*\.portfolio\s*\{", timeline)
    if not portfolio:
        problems.append("the Holdings work is not inside `if mode == .portfolio {`")
        return problems
    branch = _balanced(timeline, portfolio.end() - 1)
    if "WidgetHoldingsFetcher.fetchHoldings(" not in branch:
        problems.append("fetchHoldings is called outside the `mode == .portfolio` branch")
    if not re.search(
        r"let\s+held\s*=\s*await\s+Task\.detached\([^)]*\)\s*\{\s*WidgetHoldingsGrant\.lookup\(\)\s*\}\.value",
        branch,
    ):
        problems.append("the grant is not read off the main thread through WidgetHoldingsGrant.lookup()")

    live = re.search(
        r"if\s+case\s+\.live\(let\s+grantAtFetch\)\s*=\s*held\s*,"
        r"\s*!WidgetHoldingsGrant\.isRefused\(grantAtFetch\.fp\)\s*,"
        r"\s*let\s+ownerAtFetch\s*=\s*WidgetSnapshotStore\.portfolioOwner\(\)\s*,"
        r"\s*ownerAtFetch\.lowercased\(\)\s*==\s*grantAtFetch\.userId\.lowercased\(\)\s*\{",
        branch,
    )
    if not live:
        problems.append(
            "the fetch is no longer gated on a LIVE grant, not yet refused, whose account owns "
            "the stored slot — all captured before the await"
        )
        return problems
    fetch_body = _balanced(branch, live.end() - 1)
    switch = re.search(
        r"switch\s+await\s+WidgetHoldingsFetcher\.fetchHoldings\(grantAtFetch\)\s*\{", fetch_body
    )
    if not switch:
        problems.append("the gated block does not switch on fetchHoldings(grantAtFetch)")
        return problems
    arms = _balanced(fetch_body, switch.end() - 1)

    payload = _case_body(arms, ".payload")
    fence = _FENCE.search(payload)
    if not fence or fence.group(1) != "grantAtFetch":
        problems.append("a fresh payload is used without re-checking the widget token AND the grant marker")
    else:
        fenced = _balanced(payload, fence.end() - 1)
        if not _order(fenced, "WidgetSnapshotStore.writeHoldingsFromExtension(fresh, grant: grantAtFetch)",
                      "snap = snapshot(for: .portfolio)"):
            problems.append("the fenced payload path does not store, then render what the store kept")
        outside = payload.replace(fenced, "")
        if "writeHoldingsFromExtension(" in outside or re.search(r"\bsnap\s*=", outside):
            problems.append("the payload is stored or rendered outside the fence")

    refused = _case_body(arms, ".refused")
    fence = _FENCE.search(refused)
    if not fence or fence.group(1) != "grantAtFetch":
        problems.append("a refusal hides the tile without checking the grant was not replaced meanwhile")
    else:
        fenced = _balanced(refused, fence.end() - 1)
        if not _order(fenced, "WidgetHoldingsGrant.noteRefused(grantAtFetch.fp)",
                      "WidgetSnapshotStore.dropPortfolioFromExtension(",
                      "snap = dropped ? nil : snapshot(for: .portfolio)"):
            problems.append("the fenced refusal path no longer notes, drops and renders nothing")
        outside = refused.replace(fenced, "")
        if "noteRefused(" in outside or "dropPortfolioFromExtension(" in outside:
            problems.append("a refusal acts outside the fence")

    unavailable = _case_body(arms, ".unavailable")
    if re.search(r"\bsnap\s*=", unavailable) or "drop" in unavailable or "noteRefused" in unavailable:
        problems.append("an unavailable answer changes the tile — it must keep the stored snapshot")

    reload_at = timeline.find("WidgetRefreshSchedule.nextRefresh(")
    if reload_at == -1 or reload_at < timeline.find("WidgetHoldingsFetcher.fetchHoldings(") \
            or reload_at < timeline.find("WidgetMarketFetcher.fetchMarket()"):
        problems.append("the reload is decided before both fetches — it cannot read the final snapshot")
    if not re.search(
        r"WidgetRefreshSchedule\.nextRefresh\(\s*after:\s*now,\s*marketSession:\s*snap\?\.marketSession,"
        r"\s*builtAt:\s*snap\?\.asOf\s*\)",
        timeline,
    ):
        problems.append("the reload no longer passes the final snapshot's phase and build time")
    return problems


# ── 5. A lapsed grant: hidden without a request, and only while it is still the marker ───


def _problems_lapsed_branch(src: dict[str, str]) -> list[str]:
    problems = []
    timeline = _block(src["widget"], _TIMELINE)
    m = re.search(
        r"else\s+if\s+case\s+\.expired\(let\s+lapsed\)\s*=\s*held\s*,"
        r"\s*!WidgetHoldingsGrant\.isRefused\(lapsed\.fp\)\s*,"
        r"\s*WidgetAPIConfig\.widgetToken\s*==\s*tokenAtFetch\s*,"
        r"\s*WidgetHoldingsGrant\.markerFingerprint\s*==\s*lapsed\.fp\s*\{",
        timeline,
    )
    if not m:
        return [
            "an expired grant is no longer handled under the token + marker fence (owner matrix: "
            "a lapsed grant → 'Open Caydex', never a stale tile kept for a key the server refuses)"
        ]
    body = _balanced(timeline, m.end() - 1)
    if "fetchHoldings(" in body:
        problems.append("an expired grant spends a request")
    if not _order(body, "WidgetHoldingsGrant.noteRefused(lapsed.fp)", "dropPortfolioFromExtension(",
                  "snap = dropped ? nil : snapshot(for: .portfolio)"):
        problems.append("the lapsed path no longer notes, drops and renders nothing")
    return problems


# ── 6. The Holdings slot renders only for the signed-in account ──────────────────────────


def _problems_slot_owner_render(src: dict[str, str]) -> list[str]:
    read = _block(src["widget"], "private func snapshot(for mode: MoversMode)")
    arm = read[read.index("case .portfolio:") : read.index("case .market:")]
    m = re.search(
        r"guard\s+let\s+token\s*=\s*WidgetAPIConfig\.widgetToken\s*,"
        r"\s*let\s+subject\s*=\s*WidgetAPIConfig\.jwtSubject\(of:\s*token\)\s*,"
        r"\s*let\s+owner\s*=\s*WidgetSnapshotStore\.portfolioOwner\(\)\s*,"
        r"\s*owner\.lowercased\(\)\s*==\s*subject\.lowercased\(\)\s*else\s*\{\s*return\s+nil\s*\}",
        arm,
    )
    if not m or arm.find("return envelope?.portfolio") < m.end():
        return [
            "the Holdings slot is rendered without checking its owner is the widget token's "
            "account — a slot left behind by a cross-process race would show another account"
        ]
    return []


# ── 7. The extension's write ─────────────────────────────────────────────────────────────


def _problems_extension_write(src: dict[str, str]) -> list[str]:
    problems = []
    store = src["store"]
    write = _block(store, "static func writeHoldingsFromExtension(")
    if "reloadTimelines" in write or "reloading" in write:
        problems.append("writeHoldingsFromExtension reloads — a loop inside timeline()")
    if "writePortfolio(" in write or "write(mode:" in write:
        problems.append("the extension's write goes through the app's owner-changing writer")
    setter = write.find("defaults.set(")
    if setter == -1:
        return problems + ["writeHoldingsFromExtension no longer stores anything"]
    before = write[:setter]
    if not re.search(r"guard\s+let\s+existing\s*=\s*storedPortfolio\(defaults\)\s+else\s*\{[^}]*return\s+false", before):
        problems.append("the extension may CREATE a Holdings slot — only the app may")
    if not re.search(
        r"guard\s+let\s+slotOwner\s*=\s*existing\.owner\s*,\s*"
        r"slotOwner\.lowercased\(\)\s*==\s*grant\.userId\.lowercased\(\)\s+else\s*\{[^}]*return\s+false",
        before,
    ):
        problems.append("the extension may write a slot owned by another account (or by nobody)")
    if not re.search(r'guard\s+snapshot\.mode\s*==\s*"portfolio"\s+else\s*\{[^}]*return\s+false', before):
        problems.append("a market-scoped payload can land in the Holdings slot from the extension")
    if not re.search(r"guard\s+snapshot\.asOf\s*>=\s*existing\.snapshot\.asOf\s+else\s*\{[^}]*return\s+false", before):
        problems.append("an OLDER payload can replace a newer stored snapshot")
    if not re.search(
        r"if\s+!snapshot\.hasContent\(for:\s*\.portfolio\)\s*,\s*existing\.snapshot\.hasContent\(for:\s*\.portfolio\)",
        before,
    ):
        problems.append("a degraded refresh can replace a good snapshot")
    fence = re.search(
        r"guard\s+let\s+tokenAtStore\s*=\s*WidgetAPIConfig\.widgetToken\s*,"
        r"\s*WidgetAPIConfig\.jwtSubject\(of:\s*tokenAtStore\)\?\.lowercased\(\)\s*==\s*grant\.userId\.lowercased\(\)\s*,"
        r"\s*WidgetHoldingsGrant\.markerFingerprint\s*==\s*grant\.fp\s+else\s*\{[^}]*return\s+false",
        before,
    )
    if not fence:
        problems.append("no last-moment fence (widget token for the grant's account + marker) before the store")
    if "encoder.encode(" not in before or (fence and before.find("encoder.encode(") > fence.start()):
        problems.append("the fence is not the LAST thing before the store (the encode comes after it)")
    targets = set(re.findall(r"defaults\.set\([^\n]*forKey:\s*WidgetSharedConfig\.(\w+)\)", write))
    if targets != {"snapshotKeyV2Portfolio"}:
        problems.append(f"writeHoldingsFromExtension writes {sorted(targets)}, expected only the Holdings slot")
    after = write[setter:]
    comp = re.search(
        r"if\s+WidgetAPIConfig\.widgetToken\s*!=\s*tokenAtStore\s*"
        r"\|\|\s*WidgetHoldingsGrant\.markerFingerprint\s*!=\s*grant\.fp\s*\{",
        after,
    )
    if not comp:
        problems.append("no compensating delete when the session or the grant moved during the store")
    else:
        undo = _balanced(after, comp.end() - 1)
        if not re.search(
            r"if\s+defaults\.data\(forKey:\s*WidgetSharedConfig\.snapshotKeyV2Portfolio\)\s*==\s*data\s*\{\s*"
            r"defaults\.removeObject\(forKey:\s*WidgetSharedConfig\.snapshotKeyV2Portfolio\)",
            undo,
        ):
            problems.append(
                "the compensating delete does not remove exactly the bytes it wrote (and only those — "
                "an app write that landed in between must survive)"
            )
        if not re.search(r"\breturn\s+false\b", undo):
            problems.append("the compensating delete reports the write as stored")
    ext = _block(store, "public static func writeFromExtension(")
    if not re.search(r"guard\s+mode\s*==\s*\.market\s+else\b", ext):
        problems.append("writeFromExtension no longer refuses the Holdings slot")
    has_any = _block(store, "nonisolated public static var hasAnyState: Bool")
    if "WidgetHoldingsGrant.markerKey" not in has_any:
        problems.append("hasAnyState ignores the grant marker — a restored marker escapes the orphan clear")
    return problems


def _problems_extension_drop(src: dict[str, str]) -> list[str]:
    problems = []
    drop = _block(src["store"], "static func dropPortfolioFromExtension(owner: String, fingerprint: String)")
    for banned in ("reloadTimelines", "clearPortfolio(", "clearAll("):
        if banned in drop:
            problems.append(f"dropPortfolioFromExtension calls {banned} — a loop inside timeline()")
    remove = drop.find("removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)")
    marker = re.search(
        r"guard\s+WidgetHoldingsGrant\.markerFingerprint\s*==\s*fingerprint\s+else\s*\{[^}]*return\s+false", drop
    )
    owner = re.search(
        r"guard\s+let\s+slotOwner\s*=\s*storedPortfolio\(defaults\)\?\.owner\s*,"
        r"\s*slotOwner\.lowercased\(\)\s*==\s*owner\.lowercased\(\)\s+else\s*\{[^}]*return\s+false",
        drop,
    )
    if remove == -1:
        problems.append("dropPortfolioFromExtension no longer removes the Holdings slot")
    if not marker or (remove != -1 and marker.start() > remove):
        problems.append("a refusal drops the slot of a grant the app has since replaced")
    if not owner or (remove != -1 and owner.start() > remove):
        problems.append("a refusal drops another account's slot")
    if re.search(r"removeObject\(forKey:\s*WidgetSharedConfig\.(?!snapshotKeyV2Portfolio)", drop):
        problems.append("dropPortfolioFromExtension removes more than the Holdings slot")
    return problems


# ── 8. The fetcher ───────────────────────────────────────────────────────────────────────


def _problems_fetcher(src: dict[str, str]) -> list[str]:
    problems = []
    fetcher = src["fetcher"]
    code = _strip_comments(fetcher)
    if "URLSessionConfiguration.ephemeral" not in code or "config.urlCache = nil" not in code:
        problems.append("the holdings session is not ephemeral with no URL cache — a holdings body lands on disk")
    if "URLSession.shared" in code:
        problems.append("the holdings fetcher uses URLSession.shared (its on-disk cache outlives the session)")
    for key in ("fetcher", "grant", "widget", "push"):
        if re.search(r'"Authorization"', _strip_comments(src[key])):
            problems.append(f"{_PATHS[key].name} sets Authorization — the grant is never a bearer")
    req = _block(fetcher, "private static func holdingsRequest(")
    check = req.find("guard mayCarryHoldingsKey(url) else")
    first_key = req.find("holdingsTokenHeader")
    if check == -1 or first_key == -1 or first_key < check:
        problems.append("the key is attached before the destination is checked")
    if "forHTTPHeaderField: \"X-App-Version\"" not in req:
        problems.append("the holdings request no longer sends X-App-Version")
    if "request.timeoutInterval = WidgetAPIConfig.requestTimeout" not in req:
        problems.append("the holdings request has no bounded timeout")
    if req.count("forHTTPHeaderField:") != 3:
        problems.append("the holdings request sets a header this scan does not know")
    may = _block(fetcher, "static func mayCarryHoldingsKey(_ url: URL) -> Bool")
    https = may.find('url.scheme?.lowercased() == "https"')
    host = re.search(
        r"#if\s+!DEBUG\s+guard\s+let\s+host\s*=\s*url\.host\?\.lowercased\(\)\s*,"
        r"\s*host\s*==\s*WidgetAPIConfig\.productionBaseURL\.host\?\.lowercased\(\)\s+else\s*\{\s*return\s+false\s*\}\s*#endif",
        may,
    )
    if https == -1:
        problems.append("the key may travel over plain HTTP")
    if not host:
        problems.append("a shipped build may send the key to a host other than production")

    codes = re.search(r"static let refusalCodes: Set<String> = \[([^\]]*)\]", code)
    found = set(re.findall(r'"([A-Z_]+)"', codes.group(1))) if codes else set()
    if found != _REFUSAL_CODES:
        problems.append(f"the refusal allow-list is {sorted(found)}, not exactly {sorted(_REFUSAL_CODES)}")
    classify = _block(fetcher, "private static func classifyUnauthorized(")
    gate = re.search(r"if\s+let\s+code\s*,\s*refusalCodes\.contains\(code\)\s*\{", classify)
    if not gate or ".refused(code: code)" not in _balanced(classify, gate.end() - 1):
        problems.append("a 401 is classified as refused without the allow-list")
    if not re.search(r"return\s+\.unavailable\s*\}?\s*$", classify.rstrip("}").rstrip()):
        problems.append("a 401 outside the allow-list is not .unavailable")
    if len(re.findall(r"\.refused\(code:", code)) != 1:
        problems.append("a refusal is constructed outside classifyUnauthorized")
    fetch = _block(fetcher, "static func fetchHoldings(_ grant: WidgetHoldingsGrant.Grant) async -> Outcome")
    if not re.search(r"if\s+http\.statusCode\s*==\s*401\s*\{\s*return\s+classifyUnauthorized\(", fetch):
        problems.append("fetchHoldings no longer routes a 401 through the allow-list")
    if not re.search(r'guard\s+snapshot\.mode\s*==\s*"portfolio"\s+else', fetch):
        problems.append("a non-portfolio 200 is accepted as Holdings")
    if not re.search(r"guard\s+snapshot\.hasContent\(for:\s*\.portfolio\)\s+else", fetch):
        problems.append("a degraded 200 is accepted as Holdings")
    revoke = _block(fetcher, "static func revoke(_ grant: WidgetHoldingsGrant.Grant, reason: String) async -> Bool")
    if not re.search(r"guard\s+revokeReasons\.contains\(reason\)\s+else", revoke):
        problems.append("revoke sends a reason the server does not accept")
    if not re.search(r'static let revokeReasons: Set<String> = \["app_revoked", "widget_removed"\]', code):
        problems.append("the revoke reasons drifted from the server's body enum")
    # The answers, as the backend gives them (`_device_fetch` / `_device_revoke` below are these
    # arms in Python, checked against the real routes): revoke is DONE on a 2xx — `{revoked:
    # false}` is "already dead", not a failure — or on a 401 the allow-list calls refused; a 503
    # AUTH_UNAVAILABLE (store down), a 429 or anything else keeps the key for a retry. A fetch
    # keeps the snapshot on every non-2xx but a refused 401 (503 = the switch off, 429 = the
    # per-grant limit).
    ok = re.search(r"if\s+\(200\.\.<300\)\.contains\(http\.statusCode\)\s*\{", revoke)
    if not ok or not re.search(r"\breturn\s+true\b", _balanced(revoke, ok.end() - 1)):
        problems.append("revoke no longer treats a 2xx (revoked or already revoked) as done")
    dead = re.search(
        r"if\s+http\.statusCode\s*==\s*401\s*,\s*case\s+\.refused\s*=\s*"
        r"classifyUnauthorized\(data,\s*route:\s*\"holdings-grant/revoke\"\)\s*\{",
        revoke,
    )
    if not dead or not re.search(r"\breturn\s+true\b", _balanced(revoke, dead.end() - 1)):
        problems.append("revoke treats a 401 as 'already dead' without the refusal allow-list")
    if len(re.findall(r"\breturn\s+true\b", revoke)) != 2:
        problems.append("revoke reports done on an answer other than a 2xx or a refused 401")
    status_gate = re.search(
        r"guard\s+\(200\.\.<300\)\.contains\(http\.statusCode\)\s+else\s*\{", fetch
    )
    if not status_gate or not re.search(
        r"\breturn\s+\.unavailable\b", _balanced(fetch, status_gate.end() - 1)
    ):
        problems.append("a non-2xx holdings answer (503 switch off, 429) no longer keeps the snapshot")
    if len(re.findall(r"\.payload\(", fetch)) != 1:
        problems.append("fetchHoldings builds a payload outside its one checked path")
    return problems


# ── 9. The key never reaches a log line or a description ─────────────────────────────────


_INTERPOLATION = re.compile(r"\\\(([^()]*(?:\([^()]*\)[^()]*)*)\)")


def _problems_no_key_in_logs(src: dict[str, str]) -> list[str]:
    problems = []
    for key in ("grant", "fetcher", "store", "widget", "push"):
        for m in _INTERPOLATION.finditer(_strip_comments(src[key])):
            expr = m.group(1)
            if re.search(r"(?i)token", expr) and "tokenHeader" not in expr:
                problems.append(f"{_PATHS[key].name} interpolates a key into a string: \\({expr})")
    grant = _block(src["grant"], "nonisolated struct Grant:")
    desc = _block(grant, "var description: String")
    mirror = _block(grant, "var customMirror: Mirror")
    if "token" in desc:
        problems.append("Grant.description includes the key")
    if "token" in mirror:
        problems.append("Grant's mirror includes the key — dump() would print it")
    if not re.search(r"\bCustomStringConvertible\b", grant + _strip_comments(src["grant"])):
        problems.append("Grant no longer redacts its description")
    return problems


# ── 10. The key shape the device accepts is exactly the shape the server mints ───────────

_BASE64URL = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_LOWER_HEX = set(b"0123456789abcdef")
_INT = r"(0x[0-9A-Fa-f]+|\d+)"


def _swift_byte_set(src: str, func: str) -> set[int]:
    """The bytes a `switch byte { case …: return true  default: return false }` predicate accepts.

    Read from the comment-stripped, brace-bound function: every `case` arm returning `true`,
    parsed item by item (`0x41...0x5A`, `0x30..<0x3A`, `0x2D`). An item this parser does not know
    raises, so a reshaped predicate fails loudly instead of being read as "accepts nothing"."""
    body = _block_re(src, rf"static func {func}\(_ byte: UInt8\) -> Bool\s*\{{")
    if not re.search(r"\bswitch\s+byte\s*\{", body):
        raise LookupError(f"{func} no longer switches on its byte")
    if not re.search(r"\bdefault\s*:\s*return\s+false\b", body):
        raise LookupError(f"{func} has no `default: return false` arm")
    arms = re.findall(r"\bcase\s+([^:]+):\s*return\s+(true|false)\b", body)
    if not arms or len(re.findall(r"\bcase\b", body)) != len(arms):
        raise LookupError(f"{func} has a case arm this scan cannot read")
    accepted: set[int] = set()
    for items, result in arms:
        if result != "true":
            raise LookupError(f"{func} has a `case … return false` arm this scan cannot read")
        for item in (part.strip() for part in items.split(",")):
            r = re.fullmatch(rf"{_INT}\s*(\.\.\.|\.\.<)\s*{_INT}", item)
            one = re.fullmatch(_INT, item)
            if r:
                low, high = int(r.group(1), 0), int(r.group(3), 0)
                accepted |= set(range(low, high + 1 if r.group(2) == "..." else high))
            elif one:
                accepted.add(int(one.group(1), 0))
            else:
                raise ValueError(f"{func}: case item {item!r} is not a byte literal or range")
    return accepted


def _swift_key_constants(src: str) -> tuple[str, int]:
    code = _strip_comments(src)
    prefix = re.search(r'static let tokenPrefix = "([^"]*)"', code)
    tail = re.search(r"static let tokenTailLength = (\d+)\b", code)
    if not prefix or not tail:
        raise LookupError("the key-shape constants are gone")
    return prefix.group(1), int(tail.group(1))


def _swift_accepts(token: str, prefix: str, tail_length: int, alphabet: set[int]) -> bool:
    """`isWellFormedToken(_:)` in Python, built from what the scan READ out of the Swift source
    (the prefix, the tail length, the byte set) — never from Python's own idea of base64url."""
    if not token.startswith(prefix):
        return False
    tail = token.encode("utf-8")[len(prefix.encode("utf-8")):]
    return len(tail) == tail_length and all(b in alphabet for b in tail)


def _problems_key_shape(src: dict[str, str]) -> list[str]:
    from app.services import widget_holdings_grant_service as grants

    problems = []
    grant = src["grant"]
    alphabet = _swift_byte_set(grant, "isBase64URLByte")
    if alphabet != _BASE64URL:
        missing = "".join(sorted(chr(b) for b in _BASE64URL - alphabet))
        extra = sorted(hex(b) for b in alphabet - _BASE64URL)
        problems.append(
            f"isBase64URLByte accepts the wrong bytes (missing {missing!r}, extra {extra}) — "
            "`secrets.token_urlsafe` keys hold '-' and '_' in ~75% of mints, and each one the "
            "device refuses is a Holdings grant never adopted, silently"
        )
    prefix, tail = _swift_key_constants(grant)
    if prefix != grants.KEY_PREFIX or tail != grants.KEY_BODY_LENGTH:
        problems.append(
            f"the Swift key shape is {prefix!r} + {tail}, the server mints "
            f"{grants.KEY_PREFIX!r} + {grants.KEY_BODY_LENGTH}"
        )
    well = _block_re(grant, r"static func isWellFormedToken\(_ token: String\) -> Bool\s*\{")
    if not re.search(r"guard\s+token\.hasPrefix\(tokenPrefix\)\s+else\s*\{\s*return\s+false\s*\}", well):
        problems.append("isWellFormedToken no longer requires the cwh1_ prefix")
    if not re.search(r"let\s+tail\s*=\s*token\.utf8\.dropFirst\(tokenPrefix\.utf8\.count\)", well):
        problems.append("isWellFormedToken no longer reads the tail as the UTF-8 bytes after the prefix")
    if not re.search(r"guard\s+tail\.count\s*==\s*tokenTailLength\s+else\s*\{\s*return\s+false\s*\}", well):
        problems.append("isWellFormedToken no longer requires exactly tokenTailLength tail bytes")
    if not re.search(r"return\s+tail\.allSatisfy\(isBase64URLByte\)\s*\}\s*$", well):
        problems.append("isWellFormedToken no longer checks every tail byte against isBase64URLByte")
    if len(re.findall(r"\breturn\b", well)) != 3:
        problems.append("isWellFormedToken has a return this scan does not know")
    # The fingerprint: `String(byte, radix: 16)` is LOWER-case hex, and `isFingerprint` must accept
    # exactly that — or `setMarker` refuses every grant the app stores and the extension never
    # sees one.
    if _swift_byte_set(grant, "isLowerHexByte") != _LOWER_HEX:
        problems.append("isLowerHexByte no longer accepts exactly 0-9a-f — every fingerprint is refused")
    fp = _block_re(grant, r"static func fingerprint\(of token: String\) -> String\s*\{")
    if not re.search(r"String\(byte,\s*radix:\s*16\)", fp) or "uppercase" in fp:
        problems.append("fingerprint(of:) no longer renders lower-case hex")
    return problems


_GUARDS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "keychain_item": _problems_keychain_item,
    "no_access_group_ent": _problems_no_access_group_entitlement,
    "api_split": _problems_api_split,
    "holdings_branch": _problems_holdings_branch,
    "lapsed_branch": _problems_lapsed_branch,
    "slot_owner_render": _problems_slot_owner_render,
    "extension_write": _problems_extension_write,
    "extension_drop": _problems_extension_drop,
    "fetcher": _problems_fetcher,
    "no_key_in_logs": _problems_no_key_in_logs,
    "key_shape": _problems_key_shape,
}


@pytest.mark.parametrize("name", sorted(_GUARDS))
def test_the_current_source_passes(name):
    problems = _safe(_GUARDS[name], _sources())
    assert problems == [], f"{name}: {problems}"


# Each: (guard, file, the exact text to replace, its regression). The text must exist.
_MUTATIONS: list[tuple[str, str, str, str]] = [
    # keychain_item
    ("keychain_item", "grant", "            kSecAttrAccessGroup as String: accessGroup,\n", ""),
    ("keychain_item", "grant", "kSecAttrSynchronizable as String: false", "kSecAttrSynchronizable as String: true"),
    ("keychain_item", "grant",
     "        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly\n",
     "        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock\n"),
    ("keychain_item", "grant",
     '    static let keychainService = "com.phan.caydex.widget-holdings"',
     '    static let keychainService = "com.phan.caydex"'),
    ("keychain_item", "grant",
     "    static var accessGroup: String { WidgetSharedConfig.appGroupIdentifier }",
     '    static var accessGroup: String { "WG697LVCS9.com.phan.caydex" }'),
    ("keychain_item", "grant",
     "        guard fingerprint(of: stored.token) == fp else { return .corrupt(\"key does not match its fingerprint\") }\n",
     ""),
    # A second, hand-built query that forgets the access group.
    ("keychain_item", "grant",
     "    private static func deleteItem(account: String) {\n",
     "    private static func deleteItem(account: String) {\n"
     "        _ = SecItemDelete([kSecClass as String: kSecClassGenericPassword] as CFDictionary)\n"),
    # no_access_group_ent
    ("no_access_group_ent", "ent_widget_release",
     "\t<key>aps-environment</key>",
     "\t<key>keychain-access-groups</key>\n\t<array>\n\t\t<string>$(AppIdentifierPrefix)com.phan.caydex</string>\n"
     "\t</array>\n\t<key>aps-environment</key>"),
    ("no_access_group_ent", "ent_app_debug",
     "\t<key>aps-environment</key>",
     "\t<key>keychain-access-groups</key>\n\t<array>\n\t\t<string>group.com.phan.caydex</string>\n"
     "\t</array>\n\t<key>aps-environment</key>"),
    # api_split
    ("api_split", "widget",
     "                        WidgetHoldingsGrant.noteRefused(grantAtFetch.fp)\n",
     "                        WidgetHoldingsGrant.noteRefused(grantAtFetch.fp)\n"
     "                        WidgetHoldingsGrant.setMarker(grantAtFetch.fp)\n"),
    ("api_split", "push",
     "    init() {}\n",
     "    init() { Task { _ = await WidgetHoldingsFetcher.revoke(nil, reason: \"app_revoked\") } }\n"),
    ("api_split", "app_all", "", "\nWidgetHoldingsGrant.noteRefused(fp)\n"),
    # holdings_branch
    ("holdings_branch", "widget",
     "            if WidgetAPIConfig.widgetToken == tokenAtFetch {\n                snap = fresh\n",
     "            if true {\n                snap = fresh\n"),
    ("holdings_branch", "widget",
     "               !WidgetHoldingsGrant.isRefused(grantAtFetch.fp),\n", ""),
    ("holdings_branch", "widget",
     "               ownerAtFetch.lowercased() == grantAtFetch.userId.lowercased() {",
     "               true {"),
    ("holdings_branch", "widget",
     "                case .payload(let fresh):\n"
     "                    // ⚠️ THE SAME WINDOW AS THE MARKET FETCH, AND ONE MORE: the session can end\n"
     "                    // (token cleared) or the app can replace the grant (marker moved) during the\n"
     "                    // await. The store fences both again at the last moment.\n"
     "                    if WidgetAPIConfig.widgetToken == tokenAtFetch,\n"
     "                       WidgetHoldingsGrant.markerFingerprint == grantAtFetch.fp {",
     "                case .payload(let fresh):\n"
     "                    if WidgetAPIConfig.widgetToken == tokenAtFetch {"),
    ("holdings_branch", "widget",
     "                case .refused(let code):\n",
     "                case .refused(let code):\n"
     "                    WidgetSnapshotStore.dropPortfolioFromExtension(owner: grantAtFetch.userId, fingerprint: grantAtFetch.fp)\n"),
    ("holdings_branch", "widget",
     "                    // Offline, throttled, the switch off, a rolled-back backend: keep the snapshot.\n"
     "                    break\n",
     "                    snap = nil\n"),
    ("holdings_branch", "widget",
     "        if mode == .portfolio {\n",
     "        if mode == .portfolio || mode == .market {\n"),
    ("holdings_branch", "widget",
     "        let reload = WidgetRefreshSchedule.nextRefresh(\n"
     "            after: now, marketSession: snap?.marketSession, builtAt: snap?.asOf\n"
     "        )\n",
     "        let reload = WidgetRefreshSchedule.nextRefresh(after: now)\n"),
    ("holdings_branch", "widget",
     "                WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)\n",
     "                WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)\n"
     "                _ = await WidgetHoldingsFetcher.fetchHoldings(grantAtFetch)\n"),
    # lapsed_branch
    ("lapsed_branch", "widget",
     "                      WidgetHoldingsGrant.markerFingerprint == lapsed.fp {",
     "                      true {"),
    ("lapsed_branch", "widget",
     "                WidgetHoldingsGrant.noteRefused(lapsed.fp)\n",
     "                WidgetHoldingsGrant.noteRefused(lapsed.fp)\n"
     "                _ = await WidgetHoldingsFetcher.fetchHoldings(lapsed)\n"),
    # slot_owner_render
    ("slot_owner_render", "widget",
     "                  owner.lowercased() == subject.lowercased()\n", "                  true\n"),
    # extension_write
    ("extension_write", "store",
     "        guard let existing = storedPortfolio(defaults) else {\n",
     "        guard let existing = storedPortfolio(defaults) ?? Optional(WidgetPortfolioSlot(owner: grant.userId, snapshot: snapshot)) else {\n"),
    ("extension_write", "store",
     "              slotOwner.lowercased() == grant.userId.lowercased() else {\n"
     "            log.warning(\"widget: holdings refresh not stored — the slot belongs to another account\")",
     "              true else {\n"
     "            log.warning(\"widget: holdings refresh not stored — the slot belongs to another account\")"),
    ("extension_write", "store",
     "        guard snapshot.asOf >= existing.snapshot.asOf else {\n", "        guard true else {\n"),
    ("extension_write", "store",
     "              WidgetHoldingsGrant.markerFingerprint == grant.fp\n        else {\n",
     "              true\n        else {\n"),
    ("extension_write", "store",
     "            if defaults.data(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio) == data {\n"
     "                defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)\n"
     "            }\n",
     ""),
    ("extension_write", "store",
     "        if WidgetAPIConfig.widgetToken != tokenAtStore\n"
     "            || WidgetHoldingsGrant.markerFingerprint != grant.fp {",
     "        if WidgetAPIConfig.widgetToken != tokenAtStore {"),
    ("extension_write", "store",
     "            WidgetHoldingsGrant.markerKey,\n", ""),
    ("extension_write", "store",
     "        guard mode == .market else {\n"
     "            log.error(\"widget: the extension may write only the market slot — refused \\(mode.rawValue, privacy: .public)\")",
     "        guard mode == .market || mode == .portfolio else {\n"
     "            log.error(\"widget: the extension may write only the market slot — refused \\(mode.rawValue, privacy: .public)\")"),
    # extension_drop
    ("extension_drop", "store",
     "        guard WidgetHoldingsGrant.markerFingerprint == fingerprint else {\n",
     "        guard true else {\n"),
    ("extension_drop", "store",
     "        defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)\n"
     "        log.info(\"widget: Holdings slot removed — its grant was refused\")\n",
     "        clearPortfolio()\n"
     "        log.info(\"widget: Holdings slot removed — its grant was refused\")\n"),
    # fetcher
    ("fetcher", "fetcher", "URLSessionConfiguration.ephemeral", "URLSessionConfiguration.default"),
    ("fetcher", "fetcher",
     "            let (data, response) = try await session.data(for: request)\n            guard let http = response as? HTTPURLResponse else {\n                log.warning(\"widget holdings fetch: non-HTTP response\")",
     "            let (data, response) = try await URLSession.shared.data(for: request)\n            guard let http = response as? HTTPURLResponse else {\n                log.warning(\"widget holdings fetch: non-HTTP response\")"),
    ("fetcher", "fetcher",
     '        request.setValue(grant.token, forHTTPHeaderField: WidgetAPIConfig.holdingsTokenHeader)\n',
     '        request.setValue("Bearer \\(grant.fp)", forHTTPHeaderField: "Authorization")\n'),
    ("fetcher", "fetcher",
     '        guard url.scheme?.lowercased() == "https" else { return false }\n', ""),
    ("fetcher", "fetcher",
     "        #if !DEBUG\n", "        #if DEBUG\n"),
    ("fetcher", "fetcher",
     '        "AUTH_TOKEN_INVALID", "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND",\n',
     '        "AUTH_TOKEN_INVALID", "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND", "AUTH_REQUIRED",\n'),
    ("fetcher", "fetcher",
     "        if let code, refusalCodes.contains(code) {",
     "        if let code, !code.isEmpty {"),
    ("fetcher", "fetcher",
     '            guard snapshot.mode == "portfolio" else {\n', '            guard true else {\n'),
    ("fetcher", "fetcher",
     "            if http.statusCode == 401 {\n                return classifyUnauthorized(data, route: \"holdings-mover\")\n            }\n",
     "            if http.statusCode == 401 {\n                return .refused(code: \"AUTH_REQUIRED\")\n            }\n"),
    # Any 401 read as "already dead" — an AUTH_REQUIRED (header stripped) would delete live keys.
    ("fetcher", "fetcher",
     'if http.statusCode == 401, case .refused = classifyUnauthorized(data, route: "holdings-grant/revoke") {',
     "if http.statusCode == 401 {"),
    # A 503 (store down) read as revoked: the key is deleted while the server still honours it.
    ("fetcher", "fetcher",
     '            log.warning("widget holdings revoke: HTTP \\(http.statusCode, privacy: .public) — kept for a retry")\n',
     "            if http.statusCode == 503 { return true }\n"
     '            log.warning("widget holdings revoke: HTTP \\(http.statusCode, privacy: .public) — kept for a retry")\n'),
    # A 2xx no longer done: every renewal's old key would be retried forever.
    ("fetcher", "fetcher",
     '                log.info("widget holdings revoke: grant \\(grant.fp, privacy: .public) revoked (\\(reason, privacy: .public))")\n'
     "                return true\n",
     '                log.info("widget holdings revoke: grant \\(grant.fp, privacy: .public) revoked (\\(reason, privacy: .public))")\n'
     "                return false\n"),
    # A 503 (the switch off) decoded as a payload instead of keeping the snapshot.
    ("fetcher", "fetcher",
     "            guard (200..<300).contains(http.statusCode) else {\n",
     "            guard http.statusCode != 429 else {\n"),
    # The key attached BEFORE the destination check.
    ("fetcher", "fetcher",
     "        guard mayCarryHoldingsKey(url) else {\n",
     "        var early = URLRequest(url: url)\n"
     "        early.setValue(grant.token, forHTTPHeaderField: WidgetAPIConfig.holdingsTokenHeader)\n"
     "        guard mayCarryHoldingsKey(url) else {\n"),
    # no_key_in_logs
    ("no_key_in_logs", "fetcher",
     '            log.error("widget holdings: grant \\(grant.fp, privacy: .public) holds a malformed key — not sent")',
     '            log.error("widget holdings: grant \\(grant.token, privacy: .public) holds a malformed key — not sent")'),
    ("no_key_in_logs", "grant",
     '            "Grant(fp: \\(fp), grantId: \\(grantId), userId: \\(userId), expiresAt: \\(expiresAt))"',
     '            "Grant(fp: \\(fp), key: \\(token), grantId: \\(grantId))"'),
    ("no_key_in_logs", "grant",
     '                "fp": fp, "grantId": grantId, "userId": userId,\n',
     '                "fp": fp, "token": token, "grantId": grantId, "userId": userId,\n'),
    # key_shape (2026-11-27 review: the old contract test never read the Swift charset)
    ("key_shape", "grant", "0x30...0x39, 0x2D, 0x5F: return true", "0x30...0x39, 0x5F: return true"),
    ("key_shape", "grant", "0x30...0x39, 0x2D, 0x5F: return true", "0x30...0x39, 0x2D: return true"),
    ("key_shape", "grant", "case 0x41...0x5A, 0x61...0x7A,", "case 0x41...0x59, 0x61...0x7A,"),
    # The standard base64 alphabet's '+' and '/' — a superset is drift too.
    ("key_shape", "grant", "0x2D, 0x5F: return true", "0x2B, 0x2D, 0x2F, 0x5F: return true"),
    ("key_shape", "grant", "        guard token.hasPrefix(tokenPrefix) else { return false }\n", ""),
    ("key_shape", "grant",
     "        guard tail.count == tokenTailLength else { return false }",
     "        guard tail.count >= tokenTailLength else { return false }"),
    ("key_shape", "grant", "        return tail.allSatisfy(isBase64URLByte)", "        return !tail.isEmpty"),
    ("key_shape", "grant", "    static let tokenTailLength = 43", "    static let tokenTailLength = 44"),
    ("key_shape", "grant", '    static let tokenPrefix = "cwh1_"', '    static let tokenPrefix = "cwh_"'),
    ("key_shape", "grant", "        case 0x30...0x39, 0x61...0x66: return true",
     "        case 0x30...0x39, 0x41...0x46: return true"),
    ("key_shape", "grant", "String(byte, radix: 16)", "String(byte, radix: 16, uppercase: true)"),
]


@pytest.mark.parametrize(
    "guard, file, old, new", _MUTATIONS,
    ids=[f"{g}-{i}" for i, (g, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_guard_bites(guard, file, old, new):
    """MUTATION_LOG, executable: the regression applied to a COPY must turn its guard red."""
    sources = _sources()
    assert old in sources[file], (
        f"mutation anchor for {guard!r} is gone from {file} — update the table, do not delete it"
    )
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1) if old else sources[file] + new
    problems = _safe(_GUARDS[guard], mutated)
    assert problems, f"{guard} stayed green on a regression: {new!r}"
    assert not all(p.startswith("scan could not find") for p in problems), problems


def test_a_comment_cannot_satisfy_a_guard():
    """Control: the real fence deleted but written back in a COMMENT must still fail."""
    sources = _sources()
    old = "              WidgetHoldingsGrant.markerFingerprint == grant.fp\n        else {\n"
    assert old in sources["store"]
    mutated = dict(sources)
    mutated["store"] = sources["store"].replace(
        old, "              // WidgetHoldingsGrant.markerFingerprint == grant.fp\n              true\n        else {\n", 1
    )
    assert _safe(_problems_extension_write, mutated)


# ── Cross-checks against the backend and the agreed contract ─────────────────────────────


def test_the_refusal_codes_are_real_backend_error_codes():
    from app.api.error_response import ErrorCode

    names = {member.value if isinstance(member.value, str) else member.name for member in ErrorCode}
    names |= {member.name for member in ErrorCode}
    missing = _REFUSAL_CODES - names
    assert not missing, f"the device's refusal allow-list names codes the backend never emits: {sorted(missing)}"
    assert "AUTH_REQUIRED" not in _REFUSAL_CODES, "AUTH_REQUIRED must never hide a tile (auth.md §3)"


def test_the_header_is_the_agreed_one():
    code = _strip_comments(_PATHS["apiconfig"].read_text(encoding="utf-8"))
    m = re.search(r'public static let holdingsTokenHeader = "([^"]+)"', code)
    assert m, "WidgetAPIConfig.holdingsTokenHeader is gone"
    assert m.group(1) == _HEADER
    assert m.group(1) != re.search(r'public static let tokenHeader = "([^"]+)"', code).group(1), (
        "the grant shares the market token's header — a route could accept one for the other"
    )


def test_the_header_matches_the_backend_dependency():
    """`HOLDINGS_GRANT_HEADER` in `app/dependencies.py` is the other end of the same string."""
    import app.dependencies as deps

    assert getattr(deps, "HOLDINGS_GRANT_HEADER", None) == _HEADER, (
        "the backend's HOLDINGS_GRANT_HEADER is missing or differs from the extension's header — "
        "every holdings request would answer 401 AUTH_REQUIRED (kept as unavailable: the tile freezes)"
    )


def _device_reads(body: bytes) -> str | None:
    """`WidgetHoldingsFetcher.errorCode(in:)`, in Python: the TOP-LEVEL `error_code` string of a
    JSON object of at most 16 KiB, 1-64 characters, else nothing."""
    import json

    if len(body) > 16_384:
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    code = obj.get("error_code") if isinstance(obj, dict) else None
    return code if isinstance(code, str) and 0 < len(code) <= 64 else None


def test_the_backend_refusals_are_read_the_way_the_device_reads_them():
    """The real route, answered BEFORE any grant lookup (so no store is touched): no key is
    AUTH_REQUIRED — which the device must NOT read as a refusal — and a malformed key is
    AUTH_TOKEN_INVALID, which it must. Both arrive as a top-level `error_code` (auth.md §3's
    `auth_error` body), the only place `errorCode(in:)` looks."""
    from fastapi.testclient import TestClient

    from app.database import get_supabase
    from app.main import app

    class _NoStore:
        def __getattr__(self, name):
            raise AssertionError(f"the holdings route touched the store ({name}) before refusing")

    previous = app.dependency_overrides.get(get_supabase)
    app.dependency_overrides[get_supabase] = lambda: _NoStore()
    try:
        client = TestClient(app)
        for path in ("/api/v1/widget/holdings-mover",):
            missing = client.get(path)
            assert missing.status_code == 401, missing.text
            assert _device_reads(missing.content) == "AUTH_REQUIRED"
            assert _device_reads(missing.content) not in _REFUSAL_CODES
            bad = client.get(path, headers={_HEADER: "cwh1_not-a-real-key"})
            assert bad.status_code == 401, bad.text
            assert _device_reads(bad.content) == "AUTH_TOKEN_INVALID"
            assert _device_reads(bad.content) in _REFUSAL_CODES
            # The market widget token's header is not this route's credential.
            market = client.get(path, headers={"X-Caydex-Widget-Token": "cwh1_" + "A" * 43})
            assert market.status_code == 401 and _device_reads(market.content) == "AUTH_REQUIRED"
        revoke = client.post("/api/v1/widget/holdings-grant/revoke", json={"reason": "app_revoked"})
        assert revoke.status_code == 401 and _device_reads(revoke.content) == "AUTH_REQUIRED"
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_supabase, None)
        else:
            app.dependency_overrides[get_supabase] = previous


def _device_fetch(response) -> str:
    """`fetchHoldings`'s status arms, in Python (pinned to the Swift by the `fetcher` guard)."""
    if response.status_code == 401:
        return "refused" if _device_reads(response.content) in _REFUSAL_CODES else "unavailable"
    return "payload" if 200 <= response.status_code < 300 else "unavailable"


def _device_revoke(response) -> bool:
    """`revoke`'s answer, in Python: True = the key is certainly dead, delete the Keychain item."""
    if 200 <= response.status_code < 300:
        return True
    return response.status_code == 401 and _device_reads(response.content) in _REFUSAL_CODES


_LIVE = {"grant_id": "11111111-1111-4111-8111-111111111111", "user_id": "22222222-2222-4222-8222-222222222222"}


@pytest.mark.parametrize(
    "check_status, revoke_status, switch_on, want_fetch, want_revoke",
    [
        # The three refusals hide the tile, and a revoke of such a key is already done.
        ("unknown", None, True, "refused", True),
        ("revoked", None, True, "refused", True),
        ("expired", None, True, "refused", True),
        ("evicted", None, True, "refused", True),
        ("account_missing", None, True, "refused", True),
        # The grant store down (or an answer the dependency does not know): keep everything.
        (RuntimeError, None, True, "unavailable", False),
        ("superseded_shape_drift", None, True, "unavailable", False),
        # A live key while the switch is OFF: the data route 503s (keep the snapshot), the revoke
        # still works — 200 {revoked: true} or {revoked: false} are BOTH "dead now".
        ("live", "revoked", False, "unavailable", True),
        ("live", "already_revoked_or_unknown", False, "unavailable", True),
        # The revoke's store write failed: 503 AUTH_UNAVAILABLE, keep the key and retry.
        ("live", RuntimeError, False, "unavailable", False),
    ],
)
def test_every_backend_answer_is_read_as_the_device_should(
    monkeypatch, check_status, revoke_status, switch_on, want_fetch, want_revoke,
):
    """The two routes' REAL dependencies and handlers, with only the grant store stubbed, read
    through the device's arms: what hides a tile, what keeps it, and when a key may be deleted."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.core import security
    from app.database import get_supabase
    from app.main import app
    from app.services import widget_holdings_grant_service as grants

    def _check(_supabase, _hashed):
        if check_status is RuntimeError:
            raise grants.GrantStoreUnavailable("stubbed store outage")
        if check_status == "live":
            return grants.GrantCheck(status="live", **_LIVE)
        return grants.GrantCheck(status=check_status)

    revoked: list[tuple] = []

    def _revoke(_supabase, grant_id, user_id, reason):
        revoked.append((grant_id, user_id, reason))
        if revoke_status is RuntimeError:
            raise grants.GrantStoreUnavailable("stubbed store outage")
        return revoke_status

    monkeypatch.setattr(grants, "check", _check)
    monkeypatch.setattr(grants, "revoke", _revoke)
    monkeypatch.setattr(security.rate_limiter, "is_allowed", lambda *a, **k: True)
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", switch_on)

    class _NoStore:
        def __getattr__(self, name):
            raise AssertionError(f"the route touched the store directly ({name})")

    previous = app.dependency_overrides.get(get_supabase)
    app.dependency_overrides[get_supabase] = lambda: _NoStore()
    try:
        client = TestClient(app)
        key = {_HEADER: grants.new_token()}
        fetched = client.get("/api/v1/widget/holdings-mover", headers=key)
        assert _device_fetch(fetched) == want_fetch, (fetched.status_code, fetched.text)
        if want_fetch == "unavailable":
            assert fetched.status_code == 503 and _device_reads(fetched.content) == "AUTH_UNAVAILABLE"
        gone = client.post("/api/v1/widget/holdings-grant/revoke", headers=key,
                           json={"reason": "widget_removed"})
        assert _device_revoke(gone) is want_revoke, (gone.status_code, gone.text)
        if check_status == "live":
            assert revoked == [(_LIVE["grant_id"], _LIVE["user_id"], "widget_removed")]
            if revoke_status is not RuntimeError:
                assert gone.json() == {"revoked": revoke_status == "revoked"}
        # The body may be omitted: the reason defaults to app_revoked, the answer is read alike.
        bare = client.post("/api/v1/widget/holdings-grant/revoke", headers=key)
        assert _device_revoke(bare) is want_revoke, (bare.status_code, bare.text)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_supabase, None)
        else:
            app.dependency_overrides[get_supabase] = previous


def test_the_revoke_reasons_are_the_servers_body_enum():
    """`revokeReasons` is the Swift side of `WidgetHoldingsGrantRevokeRequest.reason`'s Literal: a
    reason the server does not accept is a 422 the device reads as "keep the key", forever."""
    import typing

    from app.schemas.widget import WidgetHoldingsGrantRevokeRequest

    server = set(typing.get_args(WidgetHoldingsGrantRevokeRequest.model_fields["reason"].annotation))
    code = _strip_comments(_PATHS["fetcher"].read_text(encoding="utf-8"))
    m = re.search(r"static let revokeReasons: Set<String> = \[([^\]]*)\]", code)
    assert m, "WidgetHoldingsFetcher.revokeReasons is gone"
    assert set(re.findall(r'"([a-z_]+)"', m.group(1))) == server, server


def test_the_device_reader_is_strict():
    """The Python mirror of `errorCode(in:)` — and the Swift it mirrors, pinned below."""
    assert _device_reads(b'{"error_code": "AUTH_SESSION_EXPIRED"}') == "AUTH_SESSION_EXPIRED"
    assert _device_reads(b'{"detail": {"error_code": "AUTH_SESSION_EXPIRED"}}') is None
    assert _device_reads(b'{"detail": "Not authenticated"}') is None
    assert _device_reads(b"not json") is None
    assert _device_reads(b'["AUTH_TOKEN_INVALID"]') is None
    assert _device_reads(b'{"error_code": ""}') is None
    assert _device_reads(b'{"error_code": 401}') is None
    assert _device_reads(b'{"error_code": "' + b"A" * 65 + b'"}') is None
    assert _device_reads(b'{"error_code": "AUTH_TOKEN_INVALID", "pad": "' + b"x" * 16_400 + b'"}') is None
    swift = _block(_PATHS["fetcher"].read_text(encoding="utf-8"), "nonisolated static func errorCode(in data: Data) -> String?")
    for token in ("data.count <= 16_384", 'body["error_code"] as? String', "!code.isEmpty, code.count <= 64"):
        assert token in swift, f"errorCode(in:) no longer has {token!r} — the mirror above is stale"


def test_the_routes_are_the_agreed_ones():
    code = _strip_comments(_PATHS["apiconfig"].read_text(encoding="utf-8"))
    assert 'baseURL.appendingPathComponent("api/v1/widget/holdings-mover")' in code
    assert 'baseURL.appendingPathComponent("api/v1/widget/holdings-grant/revoke")' in code
    assert "portfolio-mover" not in code, "the extension must never reach the session-only route"


def test_the_key_shape_matches_what_the_server_mints():
    """`"cwh1_" + secrets.token_urlsafe(32)`: the Swift shape check must accept exactly that.

    Rewritten 2026-11-27 (review): the first version checked Python's own `token_urlsafe` output
    against a Python regex — a tautology that stayed green with `-` or `_` dropped from the Swift
    predicate, i.e. with ~75% of real keys refused on the device. Now the device's acceptance is
    rebuilt from the Swift SOURCE (`_swift_accepts`) and compared with the server's own
    `is_well_formed` on real mints and on every single-byte edge."""
    from app.services import widget_holdings_grant_service as grants

    src = _PATHS["grant"].read_text(encoding="utf-8")
    alphabet = _swift_byte_set(src, "isBase64URLByte")
    prefix, tail = _swift_key_constants(src)

    def device(token: str) -> bool:
        return _swift_accepts(token, prefix, tail, alphabet)

    for _ in range(256):
        minted = grants.new_token()
        assert device(minted), f"the device refuses a server-minted key shape: {minted[:5]}…"
        assert grants.is_well_formed(minted)
    # Both '-' and '_' really occur in what the server mints — not just in principle.
    assert device(grants.KEY_PREFIX + "-" * tail) and device(grants.KEY_PREFIX + "_" * tail)

    body = "A" * (tail - 1)
    edges = [grants.KEY_PREFIX + body + chr(b) for b in range(256)]
    edges += [
        grants.KEY_PREFIX + "A" * (tail - 1),          # one short
        grants.KEY_PREFIX + "A" * (tail + 1),          # one long
        grants.KEY_PREFIX + "A" * tail + "\n",          # a trailing newline
        grants.KEY_PREFIX + "A" * (tail - 2) + "é",    # a non-ASCII character worth two bytes
        "cwh2_" + "A" * tail, "CWH1_" + "A" * tail, "A" * (tail + 5), "",
    ]
    disagree = [e for e in edges if device(e) != grants.is_well_formed(e)]
    assert not disagree, f"device and server disagree on {[repr(e[-3:]) for e in disagree]}"


def test_every_entitlements_file_is_scanned():
    """A new `.entitlements` file must not escape the keychain-access-groups guard."""
    on_disk = {p.resolve() for p in _IOS.rglob("*.entitlements") if ".build" not in p.parts}
    scanned = {_PATHS[k].resolve() for k in _ENTITLEMENT_KEYS}
    assert on_disk <= scanned, f"unscanned entitlements: {sorted(str(p) for p in on_disk - scanned)}"


def test_the_scanner_helpers_are_not_vacuous():
    assert _strip_comments('let u = "https://x" // trailing') == 'let u = "https://x"'
    assert _strip_comments("/* a { */ b()") == " b()"
    assert _block("struct A {\n  // }\n  x()\n}\ny()", "struct A") == "{\n\n  x()\n}"
    arms = "{\ncase .a(let x):\n  f(x)\ncase .b:\n  g()\n}"
    assert "f(x)" in _case_body(arms, ".a") and "g()" not in _case_body(arms, ".a")
    assert _order("a b c", "a", "c") and not _order("c a", "a", "c")
    for path in _PATHS.values():
        assert path.exists(), f"{path} moved — every scan above would silently fail to anchor"
