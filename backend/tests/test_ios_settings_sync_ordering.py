"""`SettingsSyncManager` — the two orderings that decide whether a settings change survives.

There is no iOS test target, so these are source scans over
`frontend/ios/ios/Core/Services/SettingsSyncManager.swift`. Both invariants below are ORDERING
facts inside one function, which is exactly what a scan can prove and a runtime test could only
prove by simulating a network.

Each defect here shipped, and both are silent: the user's toggle reverts, no error is raised,
nothing is logged, and the change is lost on every one of their devices.

  1. HYDRATE MUST SNAPSHOT BEFORE IT APPLIES. `pendingKeys` stores key NAMES; the VALUES live in
     UserDefaults. `apply(prefs)` writes the SERVER's values into that same store, so reading
     `currentBlob()` afterwards returns what the apply just wrote — and the "local wins" replay
     re-asserted the server's own value over itself. The confirm-filter in `push()` then saw
     blob == local, dropped the key from `pendingKeys`, and nothing ever retried.

  2. PUSH MUST MARK DIRTY BEFORE IT SENDS. Only the two DEFERRED paths marked keys, so on the
     ordinary authenticated+hydrated path — every settings change the app actually makes —
     `pendingKeys` was empty. When the PUT failed, the retry ladder it armed woke up, found
     nothing pending, and did nothing. The ladder was inert for the exact case it exists for.

⚠️ Guard discipline (`project_source_scan_guard_vacuity`): comments are blanked, windows are
brace-bounded, and no window is bounded by the token it asserts. Mutation-tested — see
`test_the_guards_are_not_vacuous`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SRC = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "ios" / "ios" / "Core" / "Services" / "SettingsSyncManager.swift"
)


def _code_only(src: str) -> str:
    """`src` with whole-line comments blanked (line numbering preserved).

    Load-bearing here: this file's comments QUOTE the broken ordering to explain it, so a scan
    of the raw source matches the rationale rather than the code. Whole-line only — stripping a
    trailing `//` would mangle URL literals.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line
        for line in src.splitlines()
    )


def _balanced(src: str, opener: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """The balanced `open_ch … close_ch` region introduced by `opener`.

    NOT `src[start:src.index(token, start)]`: bounding a window with the token you are asserting
    is circular — delete the token and the window grows until it finds another one elsewhere.
    """
    start = src.index(opener) + len(opener) - 1
    assert src[start] == open_ch, f"{opener!r} must end at its opening {open_ch!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {open_ch!r} after {opener!r}")


def _braced_block(src: str, opener: str) -> str:
    return _balanced(src, opener)


@pytest.fixture
def code() -> str:
    if not _SRC.exists():
        pytest.skip(f"{_SRC} not present")
    return _code_only(_SRC.read_text(encoding="utf-8"))


def _hydrate(code: str) -> str:
    return _braced_block(code, "func hydrate() {")


def _push(code: str) -> str:
    return _braced_block(code, "func push() {")


# ── 1. hydrate: snapshot before apply ────────────────────────────────────────

def test_hydrate_reads_the_local_values_before_apply_overwrites_them(code):
    body = _hydrate(code)

    # Non-vacuity FIRST: both anchors must exist, or an ordering assertion between two
    # missing things is trivially true.
    assert "currentBlob()" in body, "hydrate no longer snapshots the local blob at all"
    assert "apply(prefs)" in body, "hydrate no longer applies the server blob"

    assert body.index("currentBlob()") < body.index("apply(prefs)"), (
        "hydrate() reads currentBlob() AFTER apply(prefs) has written the server's values "
        "into UserDefaults, so the 'local wins' replay re-asserts the server value over "
        "itself and the user's deferred change is lost. Snapshot before applying."
    )


def test_hydrate_still_replays_the_snapshot(code):
    """The snapshot is only worth taking if it is applied. Guards against a fix that moves the
    read earlier and then forgets to use it."""
    body = _hydrate(code)
    assert "applyLocalOverrides(" in body, "hydrate no longer re-asserts the pending keys"
    assert body.index("apply(prefs)") < body.index("applyLocalOverrides("), (
        "the local overrides must be re-applied AFTER the server blob, or the server wins"
    )


# ── 2. push: mark dirty before sending ───────────────────────────────────────

def _push_send_path(code: str) -> str:
    """The part of `push()` that actually SENDS — everything after the two early-return guards.

    ⚠️ Scoping this is what makes the test non-vacuous, and the first version of it was NOT
    scoped and passed with the fix deleted. `push()` opens with two deferred branches
    (`!isAuthenticated` and `!hasHydrated`) that both call `deferLocalChange()` and RETURN. A
    whole-body scan for "is anything marked dirty?" is therefore satisfied by those branches —
    which are precisely the paths that were already correct. The bug was on the path they
    return before reaching. Verified by mutation: with the fix removed, the unscoped assertion
    still passed.
    """
    body = _push(code)
    hydrated_guard = _balanced(body, "guard hasHydrated else {")
    return body[body.index(hydrated_guard) + len(hydrated_guard):]


def test_push_records_the_outbound_keys_before_the_request_goes_out(code):
    send_path = _push_send_path(code)

    assert "pushTask = Task" in send_path, "push() no longer launches its request task"
    # `deferLocalChange()` is deliberately NOT accepted here — see `_push_send_path`.
    marker = "pendingKeys.formUnion("
    assert marker in send_path, (
        "push() never marks the keys it is SENDING as dirty on the authenticated+hydrated "
        "path. Nothing else marks them there, so a failed PUT leaves pendingKeys empty, and "
        "the retry ladder push() itself arms then wakes to find nothing pending and does "
        "nothing. The user's change is lost silently, and the next hydrate overwrites it."
    )
    assert send_path.index(marker) < send_path.index("pushTask = Task"), (
        "the dirty-marking must happen BEFORE `pushTask = Task`. Marking inside or after the "
        "task loses the change to a kill mid-request, which is the case durable pendingKeys "
        "exists for."
    )


def test_a_confirmed_push_still_clears_only_what_the_server_took(code):
    """The counterpart. Marking before sending is only safe because the success branch retains
    a key iff its CURRENT local value still differs from what the server confirmed — a blanket
    `pendingKeys = []` would drop a key dirtied while the request was in flight."""
    body = _push(code)
    assert "pendingKeys = []" not in body, (
        "push() clears pendingKeys wholesale — that discards a key dirtied AFTER the blob was "
        "captured, which is a silent lost edit"
    )
    assert "self.pendingKeys = self.pendingKeys.filter" in body, (
        "the success branch must filter pendingKeys against what the server confirmed"
    )


# ── 3. one definition of dirty ───────────────────────────────────────────────

def test_dirty_has_exactly_one_definition(code):
    """`deferLocalChange()` and `push()` must agree on what 'changed' means, or a key can be
    marked by one rule and cleared by another."""
    assert "func changedKeysVsServer()" in code, (
        "the shared 'diff against the last confirmed server blob' helper is gone; two "
        "independent copies of that rule is how the clear/retain halves drift apart"
    )
    defer_body = _braced_block(code, "private func deferLocalChange() {")
    assert "changedKeysVsServer()" in defer_body, (
        "deferLocalChange no longer uses the shared definition"
    )


# ── 4. the timezone is send-only ─────────────────────────────────────────────

def test_notify_timezone_is_never_written_back_from_the_server(code):
    """`notify_timezone` is DEVICE-derived but the blob is ACCOUNT-scoped, so applying the
    server's value makes the last device to sync dictate the zone for all of them — and the
    backend evaluates quiet hours AND the daily-cap roll in it."""
    assert "applyExcludedKeys" in code, "the send-only exclusion set is gone"
    excluded = _balanced(
        code, "static let applyExcludedKeys: Set<String> = [", open_ch="[", close_ch="]"
    )
    assert '"notify_timezone"' in excluded, (
        f"notify_timezone is no longer in applyExcludedKeys (found {excluded!r})"
    )
    apply_body = _braced_block(code, "private func apply(_ prefs: [String: PreferenceValue]) -> Set<String> {")
    assert "applyExcludedKeys.contains(key)" in apply_body, (
        "apply() no longer skips the send-only keys, so a second device's timezone "
        "overwrites this one's"
    )


def test_the_device_timezone_is_refreshed_outside_the_quiet_hours_screen(code):
    """It used to be written ONLY by `NotificationSettingsViewModel.writeQuietTimes()`, so a
    user who never opened that card never sent one and the backend fell back to ET — for their
    cap roll as well as their quiet window."""
    assert "func refreshDeviceTimezone()" in code
    body = _braced_block(code, "func refreshDeviceTimezone() {")
    assert "TimeZone.current.identifier" in body
    assert "NSSystemTimeZoneDidChange" in code, (
        "nothing reacts to the OS timezone changing, so a user who flies keeps the old zone"
    )


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_guards_are_not_vacuous():
    """Mutation test. Re-order the source the way it was BEFORE the fix and confirm each
    ordering assertion goes red. A guard that cannot fail is not a guard.
    """
    if not _SRC.exists():
        pytest.skip(f"{_SRC} not present")
    code = _code_only(_SRC.read_text(encoding="utf-8"))

    # (1) hydrate: move the snapshot after the apply, as it was.
    body = _hydrate(code)
    broken = body.replace("currentBlob()", "XXX_SNAPSHOT_MOVED", 1)
    assert "currentBlob()" not in broken.split("apply(prefs)")[0], (
        "mutation did not actually remove the early snapshot — the guard would pass anyway"
    )

    # (2) push: the dirty-marker must sit on the SEND path, not just somewhere in push().
    #     The unscoped version of this assertion passed with the fix deleted — the early
    #     `deferLocalChange()` branches satisfied it — so the scoping is the guard.
    send_path = _push_send_path(code)
    assert "pendingKeys.formUnion(" in send_path, "no dirty-marker on the send path to mutate"
    assert send_path.index("pendingKeys.formUnion(") < send_path.index("pushTask = Task"), (
        "the mutation baseline is wrong: the marker does not precede the request task"
    )
    # And prove the scoping actually excludes the early branches, or it is decoration.
    assert "deferLocalChange()" not in send_path, (
        "the send-path window still contains an early-return branch, so the assertion above "
        "could be satisfied by code that was never broken"
    )
