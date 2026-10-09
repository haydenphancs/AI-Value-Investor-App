"""The Updates and Tracking on-device snapshots — the generic store and its AppState wiring.

Owner ask (2026-10-08): Updates (Live News) and Tracking (Holdings) should appear almost
instantly. A returning user's cold launch now paints that account's last LIVE answer from
`Library/Caches`, labelled "Updated <time>", and the live load replaces it. The store is ONE
generic type, `AccountSnapshotStore<Payload>` (`Core/Repositories/AccountSnapshotStore.swift`),
with two payloads (`UpdatesFeedSnapshot.swift`, `TrackingSnapshot.swift`). It copies
`HomeDashboardSnapshotStore`'s contract; Home is NOT moved onto it (its own guard file,
`test_ios_home_instant_paint_guards.py`, pins Home's class text).

The owner fence is the dangerous half (auth.md §7): the files hold one account's news and its
holdings with shares and values, so every path that changes WHO is signed in must re-bind or
clear both stores, and a load that left under one identity must never be saved under the next.

What this file pins (store + wiring; the ViewModel rows land with the ViewModels):
  * the store: owner + age + future-skew display fence, the save fences in order (epoch, owner,
    capture age, older-capture, the payload's decision, the part set), the epoch bumps, the one
    serial disk tail, the launch bind that reads nothing, the lazy single-flight disk-only
    `prepare` and its rechecks, memory-only screenshot mode inside `#if DEBUG`, file
    protection, Caches (never Documents, never the App Group) and the one wording source;
  * the payloads: Updates keeps the Market first page only; Tracking drops the file on a live
    "no holdings" answer, keeps the previous one over unpriced rows, and decodes insights with
    do/catch (never `try?`); distinct files from each other and from Home; schemaVersion ↔ the
    `.vN.` in the file name;
  * AppState: the launch bind after the widget seed and BEFORE Home's prime (Home's prime still
    immediately followed by the restore), spelled through `storedCredentialSubject()` — never
    the inline `flatMap` form, whose first copy Home's `prime-no-owner` mutation rewrites — the
    re-binds in `applyProfile` and the account switch, the session-end clears; Settings → Clear
    Cache purging and awaiting all three stores before the recount.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails, several naming the assertion they must fail WITH. The 96 h window
and the part caps are checked against the real backend settings.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "store": _IOS / "Core/Repositories/AccountSnapshotStore.swift",
    "updates_payload": _IOS / "Core/Repositories/UpdatesFeedSnapshot.swift",
    "tracking_payload": _IOS / "Core/Repositories/TrackingSnapshot.swift",
    "home_store": _IOS / "Core/Repositories/HomeDashboardSnapshotStore.swift",
    "app": _IOS / "Core/State/AppState.swift",
    "settings": _IOS / "Views/Screens/AppSettingsView.swift",
}

_STORE_CLASS = "final class AccountSnapshotStore<Payload: AccountSnapshotPayload>"
_POLICY = "nonisolated enum AccountSnapshotPolicy"
_DISK = "nonisolated enum AccountSnapshotDisk"
_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"
_ON_AUTH = "private func onAuthenticated(userId: String? = nil, identity: Int) async"
_FUNNEL = "private func discardDataForEndedSession()"
_SUBJECT_HELPER = "private func storedCredentialSubject() -> String?"
_SAVE = "func save(parts: [String: Data], payload: Payload, savedAt: Date, epoch captured: Int)"
_PREPARE = "func prepare(apiClient: APIClient = .shared) async"
_READ = "private func readAndPublish("
_LAUNCH_BIND = "func bindLaunchOwner(ownerUserId: String?)"
_INIT = "init(config: AccountSnapshotConfig, directory: URL?)"

_BIND_UPDATES = "UpdatesFeedSnapshotStore.shared.bindLaunchOwner(ownerUserId: accountSnapshotOwner)"
_BIND_TRACKING = "TrackingSnapshotStore.shared.bindLaunchOwner(ownerUserId: accountSnapshotOwner)"
_OWNER_LINE = "let accountSnapshotOwner = storedCredentialSubject()"
_WIDGET_SEED = "WidgetRefreshService.shared.refresh(identity: identityGeneration)"
_RESTORE = 'await restoreSession(trigger: "launch")'
_HOME_PRIME = "await HomeDashboardSnapshotStore.shared.prime("

# Home's own regexes (test_ios_home_instant_paint_guards.py), so the adjacency checked here is
# exactly the one Home's `prime-after-restore` / `prime-no-owner` mutations rely on.
_PRIME_CALL = r"(await HomeDashboardSnapshotStore\.shared\.prime\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))"
_FLATMAP_SPELLING = r"authService\.getStoredToken\(\)\.flatMap \{ WidgetJWT\.subject\(of: \$0\) \}"

_PAYLOAD_KEYS = ("updates_payload", "tracking_payload")
_NETWORK_TOKENS = (".request(", "requestReturningBody(", "downloadData(", "stream(", "URLSession")


# ── Scanning helpers (copied from the Home guard file; testing.md: no shared conftest) ──

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `bindLaunchOwner`, `storedCredentialSubject`, `purgeCache` and every fence, so an
    un-stripped scan would pass on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _sources() -> dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = _strip(path.read_text(encoding="utf-8"))
    return out


def _block_at(src: str, idx: int) -> str:
    """The brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _block(src: str, header: str) -> str:
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    start = src.find(header)
    return _block_at(src, start + len(header))


def _idx(src: str, token: str) -> int:
    at = src.find(token)
    assert at != -1, f"{token!r} not found"
    return at


def _norm(src: str) -> str:
    """Whitespace-collapsed, so an exact-expression check survives re-indentation only."""
    return " ".join(src.split())


def _in_order(src: str, *tokens: str) -> None:
    positions = [_idx(src, t) for t in tokens]
    assert positions == sorted(positions), f"out of order: {list(zip(tokens, positions))}"


def _arm(switch_body: str, case: str) -> str:
    """One `case` arm of a flat switch: from `case X` to the next `case`/end."""
    at = _idx(switch_body, case)
    nxt = switch_body.find("\n        case ", at + len(case))
    return switch_body[at : nxt if nxt != -1 else len(switch_body)]


def _store(s: dict[str, str]) -> str:
    return _block(s["store"], _STORE_CLASS)


def _configure(s: dict[str, str]) -> str:
    return _block(s["app"], _CONFIGURE)


def _account_switch(s: dict[str, str]) -> str:
    oa = _block(s["app"], _ON_AUTH)
    m = re.search(r"if\s+let\s+userId\s*,\s*let\s+previous\s*=\s*lastAuthenticatedUserId\s*,"
                  r"\s*previous\s*!=\s*userId\s*\{", oa)
    assert m, "the account-switch branch moved — this scan has drifted"
    return _block_at(oa, m.start())


def _config_block(payload_src: str, static_name: str) -> str:
    """The `AccountSnapshotConfig(...)` argument list of `static let <name> = …`."""
    m = re.search(rf"static let {static_name} = AccountSnapshotConfig\(", payload_src)
    assert m, f"`AccountSnapshotConfig.{static_name}` moved — this scan has drifted"
    depth = 0
    for i in range(m.end() - 1, len(payload_src)):
        if payload_src[i] == "(":
            depth += 1
        elif payload_src[i] == ")":
            depth -= 1
            if depth == 0:
                return payload_src[m.end() : i]
    raise AssertionError(f"unbalanced parentheses in AccountSnapshotConfig.{static_name}")


_CONFIG_NAMES = {"updates_payload": "updatesFeed", "tracking_payload": "tracking"}


def _string_arg(config: str, label: str) -> str:
    m = re.search(rf'\b{label}: "([^"]+)"', config)
    assert m, f"`{label}:` is no longer a string literal — this scan has drifted"
    return m.group(1)


def _int_product(expr: str) -> int:
    value = 1
    for factor in expr.split("*"):
        value *= int(factor.strip().replace("_", ""))
    return value


def _part_names(payload_src: str) -> dict[str, str]:
    """`TrackingSnapshot.assetsPart` → "assets", from the payload's `static let …Part = "…"`."""
    names = {}
    owner = re.search(r"struct (\w+): AccountSnapshotPayload", payload_src)
    assert owner, "the payload struct moved — this scan has drifted"
    for m in re.finditer(r'static let (\w+Part) = "([^"]+)"', payload_src):
        names[f"{owner.group(1)}.{m.group(1)}"] = m.group(2)
    return names


def _config(s: dict[str, str], key: str) -> dict:
    """The parsed config of one payload: directory, file, schema, part caps, required parts."""
    src = s[key]
    config = _config_block(src, _CONFIG_NAMES[key])
    names = _part_names(src)
    caps_m = re.search(r"partCaps:\s*\[(.*?)\]\s*,\s*requiredParts:", config, re.S)
    assert caps_m, "`partCaps:` moved — this scan has drifted"
    caps = {}
    for entry in caps_m.group(1).split(","):
        if not entry.strip():
            continue
        key_expr, _, size_expr = entry.partition(":")
        key_expr = key_expr.strip()
        assert key_expr in names, f"part cap keyed by {key_expr!r}, not a declared part name"
        caps[names[key_expr]] = _int_product(size_expr)
    req_m = re.search(r"requiredParts:\s*\[(.*?)\]", config, re.S)
    assert req_m, "`requiredParts:` moved — this scan has drifted"
    required = set()
    for entry in req_m.group(1).split(","):
        if entry.strip():
            assert entry.strip() in names, f"required part {entry.strip()!r} is not a declared part name"
            required.add(names[entry.strip()])
    schema_m = re.search(r"\bschemaVersion: (\d+)", config)
    assert schema_m, "`schemaVersion:` is no longer an integer literal — this scan has drifted"
    return {
        "directory": _string_arg(config, "directoryName"),
        "file": _string_arg(config, "fileName"),
        "schema": int(schema_m.group(1)),
        "caps": caps,
        "required": required,
    }


# ── AppState and Settings: the wiring ────────────────────────────────────────────────

def _check_launch_bind(s):
    cfg = _configure(s)
    _in_order(cfg, "await primeStoredCredential()", _WIDGET_SEED, _OWNER_LINE, _BIND_UPDATES,
              _BIND_TRACKING, _HOME_PRIME, _RESTORE)
    for bind in (_BIND_UPDATES, _BIND_TRACKING):
        assert s["app"].count(bind) == 1, f"`{bind}` must run once, at launch only"
    assert not re.search(r"await\s+(UpdatesFeedSnapshotStore|TrackingSnapshotStore)\.shared\.bindLaunchOwner", cfg), (
        "the launch bind is awaited — it must cost Home's first frame nothing"
    )
    assert not re.search(r"(UpdatesFeedSnapshotStore|TrackingSnapshotStore)\.shared\.prepare\(", s["app"]), (
        "AppState reads an account snapshot — the file is read lazily at tab mount, never at launch"
    )
    assert re.search(_PRIME_CALL + r"\s*" + re.escape(_RESTORE), cfg), (
        "something now sits between Home's prime and the launch restore — Home's prime must be "
        "immediately followed by `restoreSession` (Home's `prime-after-restore` mutation)"
    )


def _check_subject_helper(s):
    helper = _block(s["app"], _SUBJECT_HELPER)
    assert "guard let token = authService.getStoredToken() else { return nil }" in _norm(helper), (
        "storedCredentialSubject no longer reads the stored credential"
    )
    assert "return WidgetJWT.subject(of: token)" in helper, (
        "storedCredentialSubject no longer returns the token's `sub` — the snapshots have no owner"
    )
    first = re.search(_FLATMAP_SPELLING, s["app"])
    prime = re.search(_PRIME_CALL, s["app"])
    assert first and prime, "Home's prime or its owner expression moved — this scan has drifted"
    assert prime.start() <= first.start() < prime.end(), (
        "the inline flatMap owner spelling appears before Home's prime — Home's `prime-no-owner` "
        "mutation rewrites the FIRST copy, so its guard would go vacuous"
    )


def _check_apply_profile_binds(s):
    block = _block(s["app"], "func applyProfile(")
    for line in ("UpdatesFeedSnapshotStore.shared.bindOwner(profile.id)",
                 "TrackingSnapshotStore.shared.bindOwner(profile.id)"):
        assert line in block, (
            f"applyProfile no longer runs `{line}` — a sign-in as another account could show "
            "the previous account's snapshot"
        )


def _check_account_switch_rebinds(s):
    switch = _account_switch(s)
    discard = _idx(switch, "discardDataForEndedSession()")
    binds = [_idx(switch, f"{store}.shared.bindOwner(userId)")
             for store in ("UpdatesFeedSnapshotStore", "TrackingSnapshotStore")]
    assert all(discard < b for b in binds), (
        "an account-snapshot re-bind runs before the discard, which unbinds it again — every "
        "save is then refused for the rest of the process"
    )
    assert "await" not in switch[discard : max(binds)], (
        "an await sits between the discard and the re-binds — a load started meanwhile "
        "captures the unbound epoch"
    )


def _check_session_end_clears(s):
    funnel = _block(s["app"], _FUNNEL)
    for line in ("UpdatesFeedSnapshotStore.shared.clearForEndedSession()",
                 "TrackingSnapshotStore.shared.clearForEndedSession()"):
        assert line in funnel, (
            f"the session-end funnel no longer runs `{line}` — the ended account's news or "
            "holdings paint on the next cold launch (auth.md §7)"
        )


def _check_clear_cache_purges(s):
    clear = _block(s["settings"], "private func clearCache()")
    lets = {
        "homeSnapshotPurge": "HomeDashboardSnapshotStore.shared.purgeCache()",
        "updatesSnapshotPurge": "UpdatesFeedSnapshotStore.shared.purgeCache()",
        "trackingSnapshotPurge": "TrackingSnapshotStore.shared.purgeCache()",
    }
    for name, call in lets.items():
        assert f"let {name} = {call}" in clear, f"Clear Cache no longer runs `{call}`"
    task = _block_at(clear, _idx(clear, "Task {"))
    recount = _idx(task, "calculateCacheSize()")
    for name in lets:
        assert _idx(task, f"await {name}?.value") < recount, (
            "Clear Cache recounts before every purge has landed — the size shown is stale"
        )
    assert _idx(clear, "Task {") > max(_idx(clear, call) for call in lets.values()), (
        "a purge is queued after the recount task"
    )


# ── The store ────────────────────────────────────────────────────────────────────────

def _check_display_fenced(s):
    disp = _block(_store(s), "func snapshotForDisplay(")
    for token in ("boundOwner", "snapshot.ownerUserId == owner,", "AccountSnapshotPolicy.isDisplayable("):
        assert token in disp, f"snapshotForDisplay lost `{token}`"
    window = _block(_block(s["store"], _POLICY), "static func isDisplayable(")
    assert "age <= maxDisplayAge" in window, "the display window no longer bounds the age"
    assert "age >= -maxFutureSkew" in window, (
        "the display window no longer refuses a future-dated save (a clock set back)"
    )


_SAVE_FENCES = (
    "guard captured == epoch else",
    "guard let owner = boundOwner else",
    "guard AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date()) else",
    "if let previous, previous.savedAt > savedAt {",
    "switch payload.persistDecision(previous: previous?.payload) {",
    "AccountSnapshotDisk.partsProblem(parts, config: config)",
)


def _check_save_fenced(s):
    save = _block(_store(s), _SAVE)
    _in_order(save, *_SAVE_FENCES, "enqueue {")
    assert "let previous = snapshotForDisplay()" in save, (
        "the previous snapshot handed to persistDecision is not owner- and age-fenced"
    )
    published = _idx(save, "current = Snapshot(")
    assert published > max(_idx(save, fence) for fence in _SAVE_FENCES), (
        "save publishes the snapshot to memory before its fences — a load that left as account A "
        "puts A's answer into `current` under owner B (auth.md §7)"
    )
    assert published < _idx(save, "enqueue {"), "the file is written before the in-memory snapshot"
    for refusal in ("guard captured == epoch else", "guard let owner = boundOwner else",
                    "if let previous, previous.savedAt > savedAt {"):
        body = _block_at(save, _idx(save, refusal))
        assert re.search(r"\breturn\b", body), f"the `{refusal}` refusal does not refuse"
    decision = _block_at(save, _idx(save, "switch payload.persistDecision("))
    keep = _arm(decision, "case .keepPrevious")
    assert re.search(r"\breturn\b", keep) and "current =" not in keep and "enqueue" not in keep, (
        "a keepPrevious decision still saves"
    )
    drop = _arm(decision, "case .deleteSaved")
    for token in ("current = nil", "enqueueDelete()", "return"):
        assert token in drop, f"a deleteSaved decision no longer runs `{token}` — the old file stays"
    assert "liveAnswerGeneration &+= 1" in drop, (
        "a deleteSaved decision no longer bumps the live-answer generation — a prepare read that "
        "started before it can put the dropped snapshot back"
    )
    assert ("liveAnswerGeneration &+= 1 current = Snapshot(ownerUserId: owner, savedAt: savedAt, "
            "payload: payload)") in _norm(save), (
        "a live save no longer bumps the live-answer generation right before it publishes — "
        "a prepare read that started earlier can overwrite it (out of order)"
    )
    prep = _block(_store(s), _PREPARE)
    _in_order(prep, "let readEpoch = epoch", "let readGeneration = liveAnswerGeneration", "readAndPublish(")
    assert "break" in _arm(decision, "case .save"), "the save arm moved — this scan has drifted"
    parts = _block_at(save, _idx(save, "AccountSnapshotDisk.partsProblem(parts, config: config)"))
    assert re.search(r"\breturn\b", parts), "a bad part set is still written"


def _check_epoch_bumps(s):
    store = _store(s)
    clear = _block(store, "func clearForEndedSession()")
    _in_order(clear, "epoch &+= 1", "enqueueDelete()")
    for token in ("boundOwner = nil", "current = nil", "fileUnread = false"):
        assert token in clear, f"clearForEndedSession lost `{token}`"
    bind = _block(store, "func bindOwner(_ userId: String)")
    _in_order(bind, "guard owner != boundOwner", "epoch &+= 1", "enqueueDelete()")
    after_guard = bind[_idx(bind, "guard owner != boundOwner"):]
    assert "current = nil" in after_guard, "a new owner keeps the previous owner's snapshot in memory"
    assert "fileUnread = false" in after_guard, "a new owner could still read the previous owner's file"
    assert re.search(r"^\s*boundOwner = owner\s*$", after_guard, re.M), (
        "bindOwner no longer binds the new owner — the next live save is filed under the "
        "PREVIOUS account's id"
    )
    purge = _block(store, "func purgeCache()")
    _in_order(purge, "epoch &+= 1", "enqueueDelete()", "return diskTail")
    assert "current = nil" in purge, "a purge keeps the snapshot in memory"
    assert "boundOwner" not in purge, "a purge must keep the binding, or the next live load cannot save"


def _check_disk_serial(s):
    store = _store(s)
    enqueue = _block(store, "private func enqueue(")
    _in_order(enqueue, "let previous = diskTail", "diskTail = Task.detached", "await previous?.value", "operation()")
    read = _block(store, "private func readOnDiskTail(")
    _in_order(read, "let previous = diskTail", "await previous?.value", "AccountSnapshotDisk.read(at:")
    assert "diskTail = " in read, "the prepare read does not take its place on the tail"
    for op in ("AccountSnapshotDisk.write(", "AccountSnapshotDisk.delete("):
        lines = [ln for ln in store.splitlines() if op in ln]
        assert lines, f"`{op}` is never called — this scan has drifted"
        for line in lines:
            assert "enqueue {" in line, f"`{op}` runs outside the serial tail: {line.strip()}"
    assert store.count("Task.detached") == 3, "a disk operation runs outside enqueue/readOnDiskTail"
    assert "Data(contentsOf:" not in store, "the store reads the file outside the tail (on main)"


def _check_launch_bind_reads_nothing(s):
    store = _store(s)
    assert store.count(_LAUNCH_BIND + " {") == 1, (
        "bindLaunchOwner's signature changed — it must stay synchronous (no `async`)"
    )
    bind = _block(store, _LAUNCH_BIND)
    for token in ("await", "prepare(", "readOnDiskTail", "Payload.decode", "Task"):
        assert token not in bind, f"the launch bind does `{token}` — it must bind only, never read"
    changed = _block_at(bind, _idx(bind, "if owner != boundOwner"))
    for token in ("epoch &+= 1", "current = nil", "fileUnread = owner != nil"):
        assert token in changed, f"the launch bind's owner change lost `{token}`"
    assert re.search(r"^\s*boundOwner = owner\s*$", bind, re.M), (
        "the launch bind does not bind the stored credential's owner — applyProfile's bindOwner "
        "(nil → this account) then deletes the file before anything reads it"
    )
    no_owner = _block_at(bind, _idx(bind, "guard owner != nil else"))
    assert "enqueueDelete()" in no_owner, "with no stored credential the file is kept"
    assert "fileUnread = false" in no_owner, "with no stored credential a later prepare reads the file"


def _check_prepare_rejects(s):
    store = _store(s)
    read = _block(store, _READ)
    assert "await readOnDiskTail(" in read, "prepare does not read through the serial tail"
    flat = _norm(read)
    recheck = ("guard epoch == readEpoch, boundOwner == owner, current == nil, "
               "liveAnswerGeneration == readGeneration else")
    assert flat.count(recheck) >= 2, (
        "prepare does not re-check the binding AND the absence of a newer live save after BOTH "
        "awaits — a rejection could delete the file a save just wrote"
    )
    assert flat.index(recheck) < _idx(flat, "switch outcome"), "the post-read re-check moved below the switch"
    switch = _block_at(read, _idx(read, "switch outcome"))
    assert "enqueueDelete()" in _arm(switch, "case .corrupt"), "a corrupt file is kept"
    failed = _arm(switch, "case .readFailed")
    assert "enqueueDelete()" not in failed, (
        "a READ failure deletes the file — a launch before first unlock would lose it"
    )
    assert "fileUnread = true" in failed, "a READ failure is never retried"
    assert "enqueueDelete()" not in _arm(switch, "case .missing")
    rejected = read[_idx(read, "AccountSnapshotDisk.rejection("):]
    _in_order(rejected, "AccountSnapshotDisk.rejection(", "enqueueDelete()", "return")
    decode = read[_idx(read, "try await Payload.decode(parts: envelope.parts, apiClient: apiClient)"):]
    catch = decode[_idx(decode, "} catch {"):]
    assert ("if epoch == readEpoch, boundOwner == owner, current == nil, "
            "liveAnswerGeneration == readGeneration {") in _norm(catch) and "enqueueDelete()" in catch, (
        "an undecodable file is kept, or deleted even after a newer live save"
    )
    decoded = decode[: _idx(decode, "} catch {")]
    _in_order(_norm(decoded), recheck, "payload.persistDecision(previous: nil)", "guard decision == .save else",
              "current = Snapshot(ownerUserId: owner, savedAt: envelope.savedAt, payload: payload)")
    unkept = _block_at(decoded, _idx(decoded, "guard decision == .save else"))
    assert "enqueueDelete()" in unkept and "return" in unkept, (
        "a file this build would not save now is still shown"
    )
    disk = _block(s["store"], _DISK)
    rejection = _block(disk, "static func rejection(")
    for token in ("schemaVersion", "normalizedOwner(envelope.ownerUserId) != owner", "isDisplayable(",
                  "partsProblem(envelope.parts, config: config)"):
        assert token in rejection, f"the prepare read no longer rejects on `{token}`"
    parts = _block(disk, "static func partsProblem(")
    for token in ("config.requiredParts.subtracting(names)", "names.subtracting(config.allowedParts)",
                  "config.partCaps[name]", "size == 0 || size > cap"):
        assert token in parts, f"the part check lost `{token}`"
    for check in ("if !missing.isEmpty {", "if !unknown.isEmpty {", "if size == 0 || size > cap {"):
        assert "return" in _block_at(parts, _idx(parts, check)), f"`{check}` no longer refuses"


def _check_prepare_single_flight(s):
    prepare = _block(_store(s), _PREPARE)
    join = _block_at(prepare, _idx(prepare, "if let running = readTask"))
    assert "await running.value" in join and "return" in join, "a second prepare does not join the running read"
    _in_order(prepare, "if let running = readTask", "guard fileUnread, let owner = boundOwner, let fileURL else",
              "fileUnread = false", "let readEpoch = epoch", "let task = Task", "readTask = task",
              "await task.value")
    task = _block_at(prepare, _idx(prepare, "let task = Task"))
    _in_order(task, "await self.readAndPublish(", "self.readTask = nil")


def _check_prepare_disk_only(s):
    store = _store(s)
    for header in (_PREPARE, _READ, "private func readOnDiskTail("):
        body = _block(store, header)
        for token in _NETWORK_TOKENS:
            assert token not in body, f"`{header}` does `{token}` — prepare is disk-only (no launch request)"
    for key in _PAYLOAD_KEYS:
        for token in _NETWORK_TOKENS:
            assert token not in s[key], (
                f"{_FILES[key].name} does `{token}` — a payload decodes bytes, it never fetches"
            )
        decode = _block(s[key], "static func decode(parts: [String: Data], apiClient: APIClient) async throws")
        assert "apiClient.decodeBody(" in decode, f"{_FILES[key].name} no longer decodes through the live DTOs"


def _check_screenshot_memory_only(s):
    store = _store(s)
    init = _block(store, _INIT)
    m = re.search(r"#if DEBUG\n(.*?)\n\s*#else\n(.*?)\n\s*#endif", init, re.S)
    assert m, "the store's init no longer branches on `#if DEBUG` — this scan has drifted"
    assert _norm(m.group(1)) == "fileURL = StoreScreenshotMode.isOn ? nil : url", (
        "screenshot mode no longer makes every store memory-only — a capture run reads the real "
        "account's file or writes the canned fixtures to disk"
    )
    assert _norm(m.group(2)) == "fileURL = url", "the release arm of the store's init changed"
    assert store.count("fileURL = ") == 2, "something else re-points the store's file"
    assert store.count("init(") == 1, "a second initializer can bypass the screenshot-mode arm"
    # `.shared` is ONE instance per payload, built through that init with the payload's config.
    for key, ctor in (("updates_payload", "UpdatesFeedSnapshotStore("), ("tracking_payload", "TrackingSnapshotStore(")):
        assert s[key].count(ctor) == 1, f"{_FILES[key].name} builds more than one store"


def _check_file_protection(s):
    disk = _block(s["store"], _DISK)
    write = _block(disk, "static func write(")
    assert ".atomic" in write and ".completeFileProtectionUntilFirstUserAuthentication" in write, (
        "the snapshot is written without `.atomic` + data protection"
    )
    config = _block(s["store"], "nonisolated struct AccountSnapshotConfig: Sendable")
    directory = _block(config, "var defaultDirectory: URL?")
    assert ".cachesDirectory" in directory, (
        "the snapshots left Library/Caches — a backed-up folder follows a restore onto a phone "
        "without the Keychain session it belongs to"
    )
    for key in ("store", *_PAYLOAD_KEYS):
        for token in ("containerURL(forSecurityApplicationGroupIdentifier", "UserDefaults(suiteName:"):
            assert token not in s[key], (
                f"{_FILES[key].name} reaches the App Group — the widget's container is not a place "
                "for one account's news or holdings"
            )


def _check_label_single_source(s):
    label = _block(_block(s["store"], _POLICY), "static func updatedLabel(")
    assert _norm(label) == "{ HomeDashboardViewModel.snapshotStatusText(savedAt: savedAt, now: now, calendar: calendar) }", (
        "the 'Updated <time>' wording has a second source — it must forward to Home's"
    )


# ── The payloads ─────────────────────────────────────────────────────────────────────

def _check_distinct_files(s):
    home = s["home_store"]
    home_dir = re.search(r'appendingPathComponent\("([^"]+)", isDirectory: true\)', home)
    home_file = re.search(r'static let fileName = "([^"]+)"', home)
    assert home_dir and home_file, "Home's file location moved — this scan has drifted"
    dirs = {"home": home_dir.group(1)}
    files = {"home": home_file.group(1)}
    for key in _PAYLOAD_KEYS:
        cfg = _config(s, key)
        dirs[key] = cfg["directory"]
        files[key] = cfg["file"]
    assert len(set(dirs.values())) == len(dirs), f"two snapshots share a directory: {dirs}"
    assert len(set(files.values())) == len(files), f"two snapshots share a file name: {files}"


def _check_schema_matches_file(s):
    for key in _PAYLOAD_KEYS:
        cfg = _config(s, key)
        m = re.fullmatch(r"[a-z0-9-]+\.v(\d+)\.plist", cfg["file"])
        assert m, f"{cfg['file']!r} is not `<name>.vN.plist`"
        assert int(m.group(1)) == cfg["schema"], (
            f"{_FILES[key].name}: schemaVersion {cfg['schema']} but the file is {cfg['file']!r} — "
            "bump both together"
        )


def _check_updates_rule(s):
    src = s["updates_payload"]
    cfg = _config(s, "updates_payload")
    assert set(cfg["caps"]) == {"feed"} and cfg["required"] == {"feed"}, (
        "the Updates snapshot keeps more than the Market feed — the /tabs body and ticker chips "
        "were cut (a stale `is_locked` opens a feed the plan now locks)"
    )
    decision = _block(src, "func persistDecision(previous: UpdatesFeedSnapshot?) -> AccountSnapshotPersistDecision")
    for condition in ("guard feed.scope == UpdatesScope.market else",
                      "guard (feed.offset ?? 0) == 0 else",
                      "guard !(feed.articles ?? []).isEmpty else"):
        body = _block_at(decision, _idx(decision, condition))
        assert "return .keepPrevious(" in body, f"`{condition}` no longer keeps the previous snapshot"
    assert ".deleteSaved(" not in decision, "an Updates answer deletes the saved feed"
    assert decision.rstrip("}").rstrip().endswith("return .save"), "the Updates rule's last word is not `.save`"


def _check_tracking_rule(s):
    src = s["tracking_payload"]
    cfg = _config(s, "tracking_payload")
    assert cfg["required"] == {"assets", "portfolios", "active"}, (
        "the Tracking snapshot no longer requires the assets, portfolios and active-group parts"
    )
    assert set(cfg["caps"]) == {"assets", "portfolios", "active", "insights"}, "the Tracking part set changed"
    decision = _block(src, "func persistDecision(previous: TrackingSnapshot?) -> AccountSnapshotPersistDecision")
    _in_order(decision, "let rows = holdingsRows", "guard !rows.isEmpty else",
              "guard rows.contains(where: { $0.priceKnown }) else", "return .save")
    empty = _block_at(decision, _idx(decision, "guard !rows.isEmpty else"))
    assert "return .deleteSaved(" in empty, (
        "a live answer with no holdings keeps the old file — the next launch paints holdings the "
        "account no longer has"
    )
    unpriced_guard = "guard rows.contains(where: { $0.priceKnown }) else"
    unpriced = _block_at(decision, _idx(decision, unpriced_guard) + len(unpriced_guard))
    assert "return .keepPrevious(" in unpriced, "rows with no known price overwrite a good snapshot"
    rows = _block(src, "static func holdingsRows(assets: [TrackedAsset], portfolio: Portfolio?) -> [TrackedAsset]")
    assert "$0.uppercased()" in rows and "members.contains($0.ticker.uppercased())" in rows, (
        "the snapshot's membership rule is not the live one (uppercased tickers)"
    )
    assert "try?" not in src, (
        "TrackingSnapshot uses `try?` — on the insights decode it flattens a failure into the "
        "server's KNOWN 'too few holdings' null"
    )
    insights = _block(src, "private static func decodeInsights(")
    assert "do {" in insights and "} catch {" in insights and "return .unknown" in insights[_idx(insights, "} catch {"):], (
        "a failed insights decode is no longer kept apart from a known answer"
    )
    assert "PortfolioStore" not in src and "portfolioStore" not in src, (
        "the Tracking snapshot reaches PortfolioStore — its membership is DISPLAY-ONLY; written "
        "back, the whole-list PUTs would delete what the user added since"
    )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "launch_bind": _check_launch_bind,
    "subject_helper": _check_subject_helper,
    "apply_profile_binds": _check_apply_profile_binds,
    "switch_rebinds": _check_account_switch_rebinds,
    "session_end_clears": _check_session_end_clears,
    "clear_cache_purges": _check_clear_cache_purges,
    "display_fenced": _check_display_fenced,
    "save_fenced": _check_save_fenced,
    "epoch_bumps": _check_epoch_bumps,
    "disk_serial": _check_disk_serial,
    "launch_bind_reads_nothing": _check_launch_bind_reads_nothing,
    "prepare_rejects": _check_prepare_rejects,
    "prepare_single_flight": _check_prepare_single_flight,
    "prepare_disk_only": _check_prepare_disk_only,
    "screenshot_memory_only": _check_screenshot_memory_only,
    "file_protection": _check_file_protection,
    "label_single_source": _check_label_single_source,
    "distinct_files": _check_distinct_files,
    "schema_matches_file": _check_schema_matches_file,
    "updates_rule": _check_updates_rule,
    "tracking_rule": _check_tracking_rule,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── Mutations: each guard must fail on the bug it names ──────────────────────────────

def _rm(token: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(token) >= 1, f"mutation anchor {token!r} not in source"
        return src.replace(token, "", 1)
    return mutate


def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _chain(*mutators: Callable[[str], str]) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        for m in mutators:
            src = m(src)
        return src
    return mutate


_BIND_BLOCK = (r"([ \t]*let accountSnapshotOwner = storedCredentialSubject\(\)\n"
               r"[ \t]*UpdatesFeedSnapshotStore\.shared\.bindLaunchOwner\(ownerUserId: accountSnapshotOwner\)\n"
               r"[ \t]*TrackingSnapshotStore\.shared\.bindLaunchOwner\(ownerUserId: accountSnapshotOwner\)\n)")
_RESTORE_RE = r"(await restoreSession\(trigger: \"launch\"\)\n)"

MUTATIONS: list[tuple] = [
    # ── AppState and Settings ──
    ("bind-after-restore", "launch_bind", "app",
     _sub(_BIND_BLOCK + r"(.*?)" + _RESTORE_RE, r"\2\3\1"), "out of order"),
    ("bind-before-widget-seed", "launch_bind", "app",
     _sub(r"([ \t]*WidgetRefreshService\.shared\.refresh\(identity: identityGeneration\)\n)(.*?)" + _BIND_BLOCK,
          r"\3\1\2"), "out of order"),
    ("bind-between-prime-and-restore", "launch_bind", "app",
     _sub(_BIND_BLOCK + r"(.*?)" + _PRIME_CALL + r"(\s*)" + _RESTORE_RE, r"\2\3\n\1\4\5"), "out of order"),
    ("bind-awaited", "launch_bind", "app",
     _sub(r"(\n[ \t]*)(TrackingSnapshotStore\.shared\.bindLaunchOwner)", r"\1await \2"), "is awaited"),
    ("bind-no-owner", "launch_bind", "app",
     _sub(r"let accountSnapshotOwner = storedCredentialSubject\(\)", "let accountSnapshotOwner: String? = nil"),
     "not found"),
    ("launch-reads", "launch_bind", "app",
     _sub(r"(TrackingSnapshotStore\.shared\.bindLaunchOwner\(ownerUserId: accountSnapshotOwner\)\n)",
          r"\1            await TrackingSnapshotStore.shared.prepare(apiClient: apiClient)\n"),
     "never at launch"),
    ("between-prime-and-restore", "launch_bind", "app",
     _sub(_PRIME_CALL + r"(\s*)" + _RESTORE_RE, r"\1\2Task.yield()\n            \3"),
     "immediately followed"),
    ("subject-dropped", "subject_helper", "app",
     _sub(r"return WidgetJWT\.subject\(of: token\)", "return nil"), "returns the token's `sub`"),
    ("flatmap-spelling", "subject_helper", "app",
     _sub(r"let accountSnapshotOwner = storedCredentialSubject\(\)",
          "let accountSnapshotOwner = authService.getStoredToken().flatMap { WidgetJWT.subject(of: $0) }"),
     "appears before Home's prime"),
    ("no-apply-bind-updates", "apply_profile_binds", "app", _rm("UpdatesFeedSnapshotStore.shared.bindOwner(profile.id)")),
    ("no-apply-bind-tracking", "apply_profile_binds", "app", _rm("TrackingSnapshotStore.shared.bindOwner(profile.id)")),
    ("switch-rebind-before-discard", "switch_rebinds", "app",
     _sub(r"(previous != userId \{\n)([ \t]*discardDataForEndedSession\(\)\n)(.*?)"
          r"([ \t]*UpdatesFeedSnapshotStore\.shared\.bindOwner\(userId\)\n)", r"\1\4\2\3"),
     "runs before the discard"),
    ("no-switch-rebind-tracking", "switch_rebinds", "app", _rm("TrackingSnapshotStore.shared.bindOwner(userId)"),
     "not found"),
    ("switch-await-before-rebind", "switch_rebinds", "app",
     _sub(r"(\n[ \t]*)(TrackingSnapshotStore\.shared\.bindOwner\(userId\))", r"\1await Task.yield()\1\2"),
     "an await sits between"),
    ("no-session-clear-updates", "session_end_clears", "app", _rm("UpdatesFeedSnapshotStore.shared.clearForEndedSession()")),
    ("no-session-clear-tracking", "session_end_clears", "app", _rm("TrackingSnapshotStore.shared.clearForEndedSession()")),
    ("no-purge-tracking", "clear_cache_purges", "settings", _rm("TrackingSnapshotStore.shared.purgeCache()"),
     "Clear Cache no longer runs"),
    ("recount-before-purge", "clear_cache_purges", "settings", _rm("await trackingSnapshotPurge?.value"), "not found"),
    ("recount-first", "clear_cache_purges", "settings",
     _sub(r"(Task \{\n)([ \t]*await homeSnapshotPurge\?\.value\n.*?)([ \t]*calculateCacheSize\(\)\n)",
          r"\1\3\2"), "recounts before"),
    # ── The store ──
    ("display-any-owner", "display_fenced", "store", _rm("snapshot.ownerUserId == owner,")),
    ("display-no-age", "display_fenced", "store", _sub(r"age <= maxDisplayAge && ", ""), "bounds the age"),
    ("display-no-skew", "display_fenced", "store", _sub(r" && age >= -maxFutureSkew", ""), "future-dated"),
    ("save-no-epoch", "save_fenced", "store", _sub(r"guard captured == epoch else", "if captured != epoch")),
    ("save-any-capture-age", "save_fenced", "store",
     _sub(r"guard AccountSnapshotPolicy\.isDisplayable\(savedAt: savedAt, now: Date\(\)\) else", "if false"),
     "not found"),
    ("save-older-overwrites", "save_fenced", "store",
     _sub(r"if let previous, previous\.savedAt > savedAt \{", "if false {"), "not found"),
    ("save-ignores-decision", "save_fenced", "store",
     _sub(r"switch payload\.persistDecision\(previous: previous\?\.payload\) \{",
          "switch AccountSnapshotPersistDecision.save {"), "not found"),
    ("save-previous-unfenced", "save_fenced", "store",
     _sub(r"let previous = snapshotForDisplay\(\)", "let previous = current"), "not owner- and age-fenced"),
    ("save-unchecked-parts", "save_fenced", "store",
     _sub(r"if let problem = AccountSnapshotDisk\.partsProblem\(parts, config: config\) \{", "if false {"),
     "not found"),
    ("save-publishes-early", "save_fenced", "store",
     _sub(r"(func save\(parts: \[String: Data\], payload: Payload, savedAt: Date, epoch captured: Int\) \{\n)(.*?)"
          r"([ \t]*current = Snapshot\(ownerUserId: owner, savedAt: savedAt, payload: payload\)\n)", r"\1\3\2"),
     "before its fences"),
    ("keep-previous-saves", "save_fenced", "store",
     _sub(r"(case \.keepPrevious\(let reason\):.*?)\n[ \t]*return\n", r"\1\n"), "still saves"),
    ("delete-saved-keeps-file", "save_fenced", "store",
     _sub(r"(case \.deleteSaved\(let reason\):\s*liveAnswerGeneration &\+= 1\s*current = nil\s*"
          r"fileUnread = false\s*)enqueueDelete\(\)", r"\1"),
     "the old file stays"),
    ("delete-saved-no-generation", "save_fenced", "store",
     _sub(r"(case \.deleteSaved\(let reason\):\s*)liveAnswerGeneration &\+= 1\s*", r"\1"),
     "live-answer generation"),
    ("save-no-generation", "save_fenced", "store",
     _sub(r"liveAnswerGeneration &\+= 1(\s*current = Snapshot\(ownerUserId: owner, savedAt: savedAt)", r"\1"),
     "out of order"),
    ("prepare-no-generation-capture", "save_fenced", "store",
     _sub(r"let readGeneration = liveAnswerGeneration\n", "let readGeneration = 0\n"), "not found"),
    ("clear-no-bump", "epoch_bumps", "store", _sub(r"(func clearForEndedSession\(\) \{.*?)epoch &\+= 1", r"\1")),
    ("bind-no-bump", "epoch_bumps", "store",
     _sub(r"(guard owner != boundOwner else \{ return \}\s*)epoch &\+= 1", r"\1")),
    ("purge-no-bump", "epoch_bumps", "store", _sub(r"(func purgeCache\(\) -> Task<Void, Never>\? \{\s*)epoch &\+= 1", r"\1")),
    ("bind-keeps-old-owner", "epoch_bumps", "store",
     _sub(r"(guard owner != boundOwner else \{ return \}.*?)\n[ \t]*boundOwner = owner\n", r"\1\n"),
     "no longer binds the new owner"),
    ("bind-keeps-snapshot", "epoch_bumps", "store",
     _sub(r"(guard owner != boundOwner else \{ return \}\s*epoch &\+= 1\s*)current = nil\n", r"\1"),
     "keeps the previous owner's snapshot"),
    ("purge-unbinds", "epoch_bumps", "store",
     _sub(r"(func purgeCache\(\) -> Task<Void, Never>\? \{\n)", r"\1        boundOwner = nil\n"),
     "must keep the binding"),
    ("enqueue-unchained", "disk_serial", "store", _sub(r"await previous\?\.value\s*operation\(\)", "operation()")),
    ("write-off-tail", "disk_serial", "store",
     _sub(r"enqueue \{ AccountSnapshotDisk\.write\(envelope, to: fileURL, config: diskConfig\) \}",
          "AccountSnapshotDisk.write(envelope, to: fileURL, config: diskConfig)"), "outside the serial tail"),
    ("delete-off-tail", "disk_serial", "store",
     _sub(r"enqueue \{ AccountSnapshotDisk\.delete\(at: fileURL, config: diskConfig\) \}",
          "AccountSnapshotDisk.delete(at: fileURL, config: diskConfig)"), "outside the serial tail"),
    ("launch-bind-reads", "launch_bind_reads_nothing", "store",
     _sub(r"(func bindLaunchOwner\(ownerUserId: String\?\) \{\n)", r"\1        Task { await self.prepare() }\n"),
     "bind only, never read"),
    ("launch-bind-async", "launch_bind_reads_nothing", "store",
     _sub(r"func bindLaunchOwner\(ownerUserId: String\?\) \{", "func bindLaunchOwner(ownerUserId: String?) async {"),
     "must stay synchronous"),
    ("launch-bind-nil-keeps-file", "launch_bind_reads_nothing", "store",
     _sub(r"(guard owner != nil else \{\s*fileUnread = false\s*)enqueueDelete\(\)", r"\1"), "the file is kept"),
    ("launch-bind-no-binding", "launch_bind_reads_nothing", "store",
     _sub(r"(fileUnread = owner != nil\n[ \t]*\}\n)[ \t]*boundOwner = owner\n", r"\1"),
     "does not bind the stored credential's owner"),
    ("launch-bind-never-reads", "launch_bind_reads_nothing", "store",
     _sub(r"fileUnread = owner != nil", "fileUnread = false"), "lost `fileUnread = owner != nil`"),
    ("read-failure-deletes", "prepare_rejects", "store",
     _sub(r"(case \.readFailed\(let reason\):)", r"\1\n            enqueueDelete()"), "a READ failure deletes"),
    ("read-failure-no-retry", "prepare_rejects", "store",
     _sub(r"(case \.readFailed\(let reason\):\s*)fileUnread = true\n", r"\1"), "never retried"),
    ("corrupt-kept", "prepare_rejects", "store",
     _sub(r"(case \.corrupt\(let reason\):.*?)enqueueDelete\(\)", r"\1"), "a corrupt file is kept"),
    ("read-recheck-no-current", "prepare_rejects", "store",
     _sub(r"guard epoch == readEpoch, boundOwner == owner, current == nil,",
          "guard epoch == readEpoch, boundOwner == owner,"), "after BOTH"),
    ("read-recheck-no-generation", "prepare_rejects", "store",
     _sub(r"current == nil,\s*liveAnswerGeneration == readGeneration else \{",
          "current == nil else {"), "after BOTH"),
    ("prepare-no-recheck", "prepare_rejects", "store",
     _sub(r"(try await Payload\.decode\(parts: envelope\.parts, apiClient: apiClient\)\s*)"
          r"guard epoch == readEpoch, boundOwner == owner, current == nil,\s*"
          r"liveAnswerGeneration == readGeneration else \{", r"\1if false {"),
     "after BOTH"),
    ("rejection-any-owner", "prepare_rejects", "store",
     _sub(r"if AccountSnapshotPolicy\.normalizedOwner\(envelope\.ownerUserId\) != owner \{", "if false {"),
     "no longer rejects"),
    ("rejection-ignores-parts", "prepare_rejects", "store",
     _sub(r"if let problem = partsProblem\(envelope\.parts, config: config\) \{", "if false {"),
     "no longer rejects"),
    ("parts-ignore-required", "prepare_rejects", "store",
     _sub(r"if !missing\.isEmpty \{\s*return [^\n]*\n\s*\}", ""), "not found"),
    ("prepare-decodes-but-drops", "prepare_rejects", "store",
     _sub(r"current = Snapshot\(ownerUserId: owner, savedAt: envelope\.savedAt, payload: payload\)", "_ = payload"),
     "not found"),
    ("prepare-shows-unkept", "prepare_rejects", "store",
     _sub(r"guard decision == \.save else \{", "if false {"), "not found"),
    ("decode-failure-deletes-newer", "prepare_rejects", "store",
     _sub(r"if epoch == readEpoch, boundOwner == owner, current == nil,\s*"
          r"liveAnswerGeneration == readGeneration \{", "if true {"),
     "deleted even after a newer live save"),
    ("prepare-not-single-flight", "prepare_single_flight", "store",
     _sub(r"if let running = readTask \{\s*await running\.value\s*return\s*\}\n", ""), "not found"),
    ("prepare-rereads", "prepare_single_flight", "store",
     _sub(r"(guard fileUnread, let owner = boundOwner, let fileURL else \{ return \}\n)[ \t]*fileUnread = false\n",
          r"\1"), "not found"),
    ("prepare-never-clears", "prepare_single_flight", "store", _rm("self.readTask = nil"), "not found"),
    ("payload-decode-fetches", "prepare_disk_only", "updates_payload",
     _sub(r"try await apiClient\.decodeBody\(UpdatesFeedResponse\.self, from: body\)",
          "try await apiClient.request(endpoint: .getUpdatesFeed(scope: UpdatesScope.market, limit: 50), "
          "responseType: UpdatesFeedResponse.self)"), "it never fetches"),
    ("tracking-decode-fetches", "prepare_disk_only", "tracking_payload",
     _sub(r"try await apiClient\.decodeBody\(TrackingFeedResponse\.self, from: assetsBody\)",
          "try await apiClient.requestReturningBody(endpoint: .getTrackingAssets, "
          "responseType: TrackingFeedResponse.self).value"), "it never fetches"),
    ("prepare-fetches", "prepare_disk_only", "store",
     _sub(r"(let outcome = await readOnDiskTail\(fileURL\)\n)",
          r"\1        _ = try? await apiClient.downloadData(endpoint: .getTrackingAssets)\n"),
     "prepare is disk-only"),
    ("screenshot-writes", "screenshot_memory_only", "store",
     _sub(r"fileURL = StoreScreenshotMode\.isOn \? nil : url", "fileURL = url"), "memory-only"),
    ("screenshot-second-init", "screenshot_memory_only", "store",
     _sub(r"(\n[ \t]*static func inMemory\()",
          r"\n    init(unsafeDirectory: URL) { config = .tracking; fileURL = unsafeDirectory }\n\1"),
     "re-points"),
    ("screenshot-second-store", "screenshot_memory_only", "tracking_payload",
     _sub(r"(\nextension AccountSnapshotStore)",
          r"\n@MainActor private let rogue = TrackingSnapshotStore(config: .tracking, directory: nil)\1"),
     "builds more than one store"),
    ("no-file-protection", "file_protection", "store",
     _sub(r", \.completeFileProtectionUntilFirstUserAuthentication", ""), "data protection"),
    ("backed-up-dir", "file_protection", "store", _sub(r"\.cachesDirectory", ".documentDirectory"), "left Library/Caches"),
    ("app-group", "file_protection", "tracking_payload",
     _sub(r"(\nextension AccountSnapshotStore)",
          r'\nlet rogueDir = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: "group.x")\1'),
     "reaches the App Group"),
    ("label-own-wording", "label_single_source", "store",
     _sub(r"HomeDashboardViewModel\.snapshotStatusText\(savedAt: savedAt, now: now, calendar: calendar\)",
          '"Updated \\\\(savedAt)"'), "second source"),
    # ── The payloads ──
    ("files-collide", "distinct_files", "tracking_payload",
     _sub(r'directoryName: "TrackingSnapshot",\s*fileName: "tracking-snapshot\.v1\.plist"',
          'directoryName: "UpdatesFeedSnapshot", fileName: "updates-feed-snapshot.v1.plist"'), "share a directory"),
    ("dir-is-homes", "distinct_files", "updates_payload",
     _sub(r'directoryName: "UpdatesFeedSnapshot"', 'directoryName: "HomeDashboard"'), "share a directory"),
    ("schema-bumped-alone", "schema_matches_file", "updates_payload",
     _sub(r"schemaVersion: 1,", "schemaVersion: 2,"), "bump both together"),
    # A re-added /tabs part: declared next to `feed` AND given a cap — the shape a real re-add takes.
    ("updates-keeps-tabs", "updates_rule", "updates_payload",
     _chain(_sub(r'(nonisolated static let feedPart = "feed")', r'\1\n    nonisolated static let tabsPart = "tabs"'),
            _sub(r"partCaps: \[UpdatesFeedSnapshot\.feedPart: 1024 \* 1024\]",
                 "partCaps: [UpdatesFeedSnapshot.feedPart: 1024 * 1024, UpdatesFeedSnapshot.tabsPart: 256 * 1024]")),
     "keeps more than the Market feed"),
    ("updates-keeps-ticker-feed", "updates_rule", "updates_payload",
     _sub(r"guard feed\.scope == UpdatesScope\.market else \{", "if false {"), "not found"),
    ("updates-keeps-later-page", "updates_rule", "updates_payload",
     _sub(r"guard \(feed\.offset \?\? 0\) == 0 else \{", "if false {"), "not found"),
    ("updates-keeps-empty", "updates_rule", "updates_payload",
     _sub(r"guard !\(feed\.articles \?\? \[\]\)\.isEmpty else \{", "if false {"), "not found"),
    ("updates-empty-deletes", "updates_rule", "updates_payload",
     _sub(r'return \.keepPrevious\("the Market feed came back with no stories"\)',
          'return .deleteSaved("the Market feed came back with no stories")'), "no longer keeps the previous"),
    ("tracking-empty-keeps-file", "tracking_rule", "tracking_payload",
     _sub(r"return \.deleteSaved\(", "return .keepPrevious("), "keeps the old file"),
    ("tracking-unpriced-saved", "tracking_rule", "tracking_payload",
     _sub(r"guard rows\.contains\(where: \{ \$0\.priceKnown \}\) else \{", "if false {"), "not found"),
    ("tracking-active-optional", "tracking_rule", "tracking_payload",
     _sub(r"requiredParts: \[TrackingSnapshot\.assetsPart, TrackingSnapshot\.portfoliosPart, TrackingSnapshot\.activePart\]",
          "requiredParts: [TrackingSnapshot.assetsPart, TrackingSnapshot.portfoliosPart]"), "no longer requires"),
    ("tracking-insights-try", "tracking_rule", "tracking_payload",
     _sub(r"let dto = try await apiClient\.decodeBody\(PortfolioInsightsDTO\?\.self, from: body\)",
          "let dto = (try? await apiClient.decodeBody(PortfolioInsightsDTO?.self, from: body)) ?? nil"),
     "uses `try?`"),
    ("tracking-membership-case", "tracking_rule", "tracking_payload",
     _sub(r"members\.contains\(\$0\.ticker\.uppercased\(\)\)", "members.contains($0.ticker)"), "not the live one"),
    ("tracking-writes-store", "tracking_rule", "tracking_payload",
     _sub(r"(\n    var activePortfolio: Portfolio\? \{)",
          r"\n    func writeBack(_ store: PortfolioStore) {}\1"), "DISPLAY-ONLY"),
]

_MUTATION_ROWS = [m if len(m) == 5 else (*m, None) for m in MUTATIONS]


@pytest.mark.parametrize("label,guard,key,mutate,message", _MUTATION_ROWS, ids=[m[0] for m in _MUTATION_ROWS])
def test_every_guard_kills_its_mutation(label, guard, key, mutate, message):
    sources = _sources()
    mutated = dict(sources)
    mutated[key] = mutate(sources[key])
    assert mutated[key] != sources[key], f"mutation {label!r} changed nothing"
    # With a message, the guard must fail on THE assertion that names this bug, not trip an
    # unrelated earlier one.
    with pytest.raises(AssertionError, match=re.escape(message) if message else None):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {row[1] for row in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_mutation_labels_are_unique():
    labels = [row[0] for row in MUTATIONS]
    assert len(labels) == len(set(labels)), "two mutation rows share a label"


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// UpdatesFeedSnapshotStore.shared.clearForEndedSession()\n"
        "/// TrackingSnapshotStore.shared.bindLaunchOwner(ownerUserId: accountSnapshotOwner)\n"
        "/* let accountSnapshotOwner = storedCredentialSubject()\n"
        "   fileURL = StoreScreenshotMode.isOn ? nil : url */\n"
        'let url = "https://example.com"  // await trackingSnapshotPurge?.value\n'
    )
    code = _strip(prose)
    for token in ("clearForEndedSession", "bindLaunchOwner", "storedCredentialSubject",
                  "StoreScreenshotMode", "trackingSnapshotPurge"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"


# ── Backend ↔ iOS: the numbers the snapshots' safety rests on ───────────────────────

def _swift_seconds(src: str, name: str) -> int:
    m = re.search(rf"static let {name}: TimeInterval = ([\d *]+)\n", src)
    assert m, f"`{name}` is no longer a plain product literal — this scan has drifted"
    return _int_product(m.group(1))


def test_the_account_snapshots_cannot_outlive_a_provably_live_session():
    """A saved snapshot proves only that an access token was minted within ACCESS minutes
    before the save; refresh rotates both tokens. So a snapshot older than REFRESH − ACCESS may
    belong to a session the server already considers dead — it must never paint. And the
    owner's decision is ONE window for Home, Updates and Tracking."""
    from app.config import settings

    s = _sources()
    max_age = _swift_seconds(_block(s["store"], _POLICY), "maxDisplayAge")
    assert max_age == 96 * 60 * 60, "owner decision 2026-10-08 is 96 h — change it there first"
    assert max_age == _swift_seconds(s["home_store"], "maxDisplayAge"), (
        "the account snapshots' window drifted from Home's — the owner decided one window"
    )
    bound = (settings.REFRESH_TOKEN_EXPIRE_MINUTES - settings.ACCESS_TOKEN_EXPIRE_MINUTES) * 60
    assert max_age < bound, (
        f"maxDisplayAge {max_age}s ≥ refresh − access lifetime {bound}s: a dead session's "
        "news or holdings could paint on a cold launch"
    )
    skew = _swift_seconds(_block(s["store"], _POLICY), "maxFutureSkew")
    assert 0 < skew <= 10 * 60, "the future-skew allowance is no longer a few minutes"


def test_the_part_caps_leave_room_for_a_real_answer():
    """A cap below a real answer means that account never gets a snapshot (every save is
    refused with a WARNING); a cap far above it means a launch could read a huge file."""
    from app.config import settings

    s = _sources()
    updates = _config(s, "updates_payload")["caps"]
    tracking = _config(s, "tracking_payload")["caps"]
    assert updates["feed"] >= 512 * 1024, "a 50-story Market page with summaries must fit"
    # Up to WATCHLIST_MAX_ITEMS rows, each ~1-2 KB with its sparkline.
    assert tracking["assets"] >= int(settings.WATCHLIST_MAX_ITEMS) * 2 * 1024, (
        "a full watchlist's tracking feed would not fit — that account never gets a snapshot"
    )
    assert tracking["portfolios"] >= 256 * 1024
    assert tracking["active"] >= 64, "a portfolio id (a UUID) must fit the active part"
    assert tracking["insights"] >= 16 * 1024
    for caps in (updates, tracking):
        total = sum(caps.values()) + 16 * 1024
        assert total <= 8 * 1024 * 1024, f"a snapshot file may reach {total} bytes — too much to read at mount"
    envelope = re.search(r"static let envelopeOverheadBytes = (\d+) \* 1024", _sources()["store"])
    assert envelope and int(envelope.group(1)) == 16, "the envelope allowance moved — this scan has drifted"


def test_the_required_parts_are_capped():
    """`requiredParts` ⊆ `partCaps` keys: a required part with no cap fails every save."""
    s = _sources()
    for key in _PAYLOAD_KEYS:
        cfg = _config(s, key)
        assert cfg["required"] <= set(cfg["caps"]), f"{_FILES[key].name}: a required part has no cap"
        assert cfg["required"], f"{_FILES[key].name}: no required part"
