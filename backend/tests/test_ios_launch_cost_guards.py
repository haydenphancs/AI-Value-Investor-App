"""Guards for the cold-launch fan-out fixes and the two silent failures found with them.

WHY THIS FILE EXISTS. A cold-launch console log showed ~33 HTTP requests, roughly half of them
exact duplicates: `/home/dashboard` four times, `/users/me/credits` twice, two Learn-progress
endpoints twice each, the widget pair twice, the localhost probe twice. It also showed the
`app_open` analytics event being dropped, and it printed a Supabase signed URL and the user's
email in clear.

Every assertion below pins a fix whose ABSENCE is invisible at runtime — the app looks fine
either way, which is exactly how each of these survived.

⚠️ The `/home/dashboard` case is the reason this file is written the way it is. The code
carried a five-line comment explaining that `oldTier` "is inspected rather than ignored" to
stop a redundant launch fetch — above an `onChange` that used the zero-argument closure and
inspected nothing. The fix existed only in prose. Per `.claude/rules/testing.md` §3 and the
`project_source_scan_guard_vacuity` memory, every scan here is brace-bounded, comment-stripped,
and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. The fixes' own comments name every token
    asserted below, so an un-stripped scan would pass on prose after a revert."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _array_literal(src: str, header: str) -> str:
    """The bracket-balanced body of an array declaration, comments stripped.

    NOT `_decl_block`: that one scans forward for the first `{`, which for a `[...]` literal
    silently lands in the NEXT declaration's body — so the assertion would be checking a
    function it was never pointed at. (Caught while writing this file, which is the argument
    for mutation-testing every guard.)
    """
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_bracket = src.index("[", start)
    depth = 0
    for i in range(open_bracket, len(src)):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_bracket : i + 1])
    pytest.fail(f"unbalanced brackets after {header!r}")


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. Analytics: the timer flush must not cancel itself ───────────────────────

_ANALYTICS = _IOS / "Core/Services/Analytics.swift"


def test_the_flush_timer_does_not_call_flush_directly():
    """`flush()` opens with `flushTask?.cancel()` to kill a pending timer. Called from INSIDE
    the timer task — where `flushTask` still refers to the task that is executing — that
    cancelled itself, and `URLSession.data(for:)` then failed with `URLError.cancelled` (-999)
    before the request left the device.

    Every 30-second flush therefore failed. The batch-cap and `flushNow()` paths were fine
    (each runs in a fresh task), so analytics looked alive while losing very nearly everything.
    """
    block = _decl_block(_read(_ANALYTICS), "private func scheduleFlush()")
    assert "flushFromTimer" in block, (
        "scheduleFlush no longer routes through `flushFromTimer`. Calling `flush()` directly "
        "from the timer task makes it cancel itself — see the doc comment on flushFromTimer."
    )
    assert not re.search(r"await\s+self\?\.flush\(\)", block), (
        "scheduleFlush calls `flush()` directly from inside the timer task again. That is the "
        "self-cancel: every timed flush will fail with -999 before reaching the network."
    )


def test_the_timer_entry_point_releases_the_slot_instead_of_cancelling_it():
    block = _decl_block(_read(_ANALYTICS), "private func flushFromTimer()")
    assert "flushTask = nil" in block, (
        "flushFromTimer does not release `flushTask`, so `flush()` will cancel the task that "
        "is calling it."
    )
    assert ".cancel()" not in block, (
        "flushFromTimer cancels something. It runs INSIDE the timer task; any cancel here is "
        "the bug this function exists to prevent."
    )


def test_a_cancelled_flush_requeues_its_batch():
    """`flush()` removes the batch from the buffer BEFORE sending. Dropping on failure is
    deliberate for a server outage (a retry amplifier helps nobody), but a cancellation means
    the request never reached the network at all — nothing was delivered, so nothing should
    be thrown away."""
    block = _decl_block(_read(_ANALYTICS), "private func flush()")
    assert "buffer.insert(contentsOf: batch, at: 0)" in block, (
        "flush() no longer re-queues a batch whose request was cancelled — those events are "
        "silently lost even though the server never saw them."
    )
    assert ".cancelled" in block or "Task.isCancelled" in block, (
        "flush() does not distinguish a cancellation from a delivery failure."
    )


def test_the_drop_is_reported_outside_debug():
    """The self-cancelling timer went unnoticed for the life of the file because the only
    diagnostic was a `#if DEBUG print`. In production, analytics failing 100% of the time was
    indistinguishable from analytics working."""
    block = _decl_block(_read(_ANALYTICS), "private func flush()")
    assert "log.warning" in block, (
        "the flush failure path has no unconditional log. A DEBUG-only print is how a total "
        "analytics outage stays invisible in production."
    )


# ── 2. The tier observer that only ever existed in a comment ───────────────────

_TIER_OBSERVERS = {
    "Home": _IOS / "Views/Screens/HomeDashboardView.swift",
    "WhaleProfile": _IOS / "Views/Screens/WhaleProfileView.swift",
}


@pytest.mark.parametrize("screen", sorted(_TIER_OBSERVERS))
def test_unlock_observers_watch_entitlement_generation_not_raw_tier(screen):
    """`user.tier` is declared `= .free` and `applyProfile` writes the real value during
    session restore, so observing it directly fires a `.free → .pro` change on EVERY cold
    launch of every paying account. On Home that was a whole extra `/home/dashboard`.

    `AppState.entitlementGeneration` bumps only for a tier change AFTER the profile has
    hydrated once, so hydration is silent while a real purchase still unlocks immediately.
    """
    src = _strip_comments(_read(_TIER_OBSERVERS[screen]))
    assert "onChange(of: appState.user.tier)" not in src, (
        f"{screen} observes `user.tier` directly again. That fires on the hydration write "
        "during every launch of a paid account — a purchase-unlock observer reacting to no "
        "purchase. Observe `appState.entitlementGeneration` instead."
    )
    assert "onChange(of: appState.entitlementGeneration)" in src, (
        f"{screen} no longer reacts to an entitlement change at all — a purchase would not "
        "unlock until relaunch."
    )


def test_entitlement_generation_ignores_the_hydration_write():
    """The guard has to live in `applyProfile`, not in a view: `onChange` delivery is deferred
    to the next update pass, so a view-side "was this the first write?" flag already reads
    true by the time it is consulted."""
    block = _decl_block(_read(_IOS / "Core/State/AppState.swift"), "func applyProfile(")
    assert "hasHydratedProfileOnce" in block, (
        "applyProfile no longer distinguishes the hydration write from a real tier change, so "
        "`entitlementGeneration` bumps on every launch and the observers above fire with it."
    )
    assert re.search(r"if\s+hasHydratedProfileOnce\s*,\s*previousTier\s*!=\s*incomingTier", block), (
        "the entitlement bump is not guarded on BOTH 'we have hydrated before' AND 'the tier "
        "actually changed'."
    )


# ── 3. Identity generation: discovering an identity is not changing one ────────


def test_the_identity_modifier_seeds_and_compares_a_generation():
    """`auth.status` moves `.unknown → .restoring → .authenticated` on every launch, and the
    last hop is indistinguishable from a sign-in to a status observer. It is not one: the
    stored credential is armed before any tab mounts, so the loads already on the wire were
    answered for that same account."""
    block = _decl_block(
        _read(_IOS / "Views/Modifiers/ReloadOnIdentityChange.swift"), "struct ReloadOnIdentityChange"
    )
    assert "handledGeneration" in block, (
        "the modifier no longer tracks which identity generation it has reacted to, so a plain "
        "cold launch reloads every tab root again."
    )
    assert "appState.identityGeneration" in block, "the modifier does not read the generation"
    assert re.search(r"guard\s+handledGeneration\s*!=\s*generation\s+else\s*\{\s*return\s*\}", block), (
        "the modifier fires without comparing the generation it already handled."
    )
    assert "isActiveTab" in block, (
        "the modifier no longer passes `isActiveTab` to its action, so the clear-eagerly / "
        "fetch-lazily split cannot be expressed by the handlers."
    )


def test_a_dead_stored_credential_still_forces_a_reload():
    """The one case where 'first resolution of the process' must NOT be treated as a discovery.

    A launch that primes a DEAD credential sends the mount-time loads with that token and has
    them rejected, then concludes 'guest'. If that counted as a discovery nothing would reload
    and the active tab would hold its 401 until the user happened to switch tabs.
    """
    src = _read(_IOS / "Core/State/AppState.swift")
    block = _decl_block(src, "private func invalidateIdentity(")
    assert "identityGeneration &+= 1" in block, (
        "invalidateIdentity no longer bumps unconditionally — a dead stored credential would "
        "strand whatever the launch already loaded."
    )
    stripped = _strip_comments(src)
    for site in ("endSessionForDeadCredential", "signOut"):
        fn = _decl_block(src, f"func {site}()")
        assert "invalidateIdentity" in fn, (
            f"{site} uses the discovery-tolerant `resolveIdentity` instead of "
            "`invalidateIdentity`, so a session ending may not bump the generation."
        )
    assert "noteCredentialDisarmed()" in stripped, (
        "the transient-restore disarm no longer bumps the identity. A 60s auto-refresh tick "
        "during a flaky-network restore would load GUEST data and stamp it as the user's, and "
        "the heal would then decline to replace it."
    )


# ── 4. Tab-activation gates ────────────────────────────────────────────────────


def test_the_learn_tab_gates_its_launch_hydration():
    """Wiser was the only tab root with a plain `.task { }` and no activation gate, so its five
    hydrations ran at cold launch on a tab nobody was looking at — staggered against
    `AppState.hydrateLearnStores()`, whose stores' `hydrateTask` join only collapses
    OVERLAPPING calls. Result: `learn/progress/journey_lesson` and `learn/progress/money_move`
    were each fetched twice per launch."""
    block = _decl_block(_read(_IOS / "Views/Screens/LearnView.swift"), "struct LearnContentView")
    assert re.search(r"\.task\(id:\s*isActiveTab\s*\)", block), (
        "LearnContentView's `.task` is not keyed on `isActiveTab` — it hydrates at launch for "
        "a hidden tab, duplicating the auth fan-out's hydration."
    )
    assert re.search(r"guard\s+isActiveTab\s+else\s*\{\s*return\s*\}", block), (
        "LearnContentView keys its task on `isActiveTab` but does not guard on it, so it still "
        "runs on the deactivation edge."
    )


def test_the_localhost_probe_is_single_flight():
    """`resolve()` is called from the root `.task` AND from `didBecomeActive`, which also fires
    on a cold launch — two identical 1-second `health/live` probes every launch, visible in the
    log as a pair of `-1004`s."""
    src = _read(_IOS / "Core/Services/ServerEnvironmentManager.swift")
    block = _decl_block(src, "func resolve() async")
    assert "resolveCoordinator" in block, (
        "resolve() no longer joins a probe already in flight, so concurrent launch triggers "
        "each pay for their own."
    )


def test_the_widget_force_respects_the_identity_it_already_ran_under():
    """`force: true` exists so a sign-in can override the 60s throttle — the previous fetch
    answered for somebody else. At cold launch that is false: `AppState` arms the credential
    BEFORE the seed refresh, so forcing one from `onAuthenticated` re-fetched identical data."""
    block = _decl_block(
        _read(_IOS / "Core/Services/WidgetRefreshService.swift"), "private func forceIsMeaningful("
    )
    assert "lastRefreshIdentity" in block, (
        "a forced widget refresh no longer compares the identity the last one ran under, so "
        "every launch spends two extra requests re-fetching the same snapshots."
    )
    assert "inFlightIdentity" in block, (
        "forceIsMeaningful consults only the COMPLETED run. That fails open at exactly the "
        "moment that matters: at cold launch `onAuthenticated` forces a refresh while the seed "
        "run is still in flight, so nothing has been stamped yet. Measured on the simulator — "
        "the widget still ran twice per launch until the in-flight run was consulted too."
    )


# ── 5. Money + privacy on the request path ─────────────────────────────────────

_API_CLIENT = _IOS / "Core/Services/APIClient.swift"


@pytest.mark.parametrize("fn", ["attemptFailover<T: Decodable>", "attemptFailoverVoid"])
def test_failover_obeys_the_get_only_money_rule(fn):
    """A failover is a RE-SEND, so it is bound by the same rule as the 5xx retry path.
    `POST /research/generate` precharges 20 credits, inserts a row and spawns a worker before
    it returns; re-issuing one whose response was merely lost bills the user twice for one tap.

    `isSafeToRetryAfterServerError` was written for exactly this and the failover path ignored
    it — which made the careful GET-only rule bypassable.
    """
    block = _decl_block(_read(_API_CLIENT), f"private func {fn}(")
    assert "isSafeToRetryAfterServerError" in block, (
        f"{fn} re-sends any HTTP method. A dropped response on a paid POST is billed twice."
    )


def test_debug_logging_redacts_secrets():
    """The pasted launch log contained a Supabase Storage signed URL WITH its token and the
    account's email; a sign-in would have printed the password, since `SignInRequest` is an
    `httpBody` like any other. Console output gets pasted into bug reports."""
    src = _read(_API_CLIENT)
    for fn in ("private func logRequest(", "private func logResponse("):
        block = _decl_block(src, fn)
        if "Body:" in block:
            assert "Self.redact(" in block, f"{fn} prints a body without redacting it"
    # The key list and the function that applies it are separate declarations; assert both,
    # or a redactor with an empty list passes.
    keys = _array_literal(src, "private static let redactedKeys")
    for key in ("password", "token", "email"):
        assert f'"{key}"' in keys, f"`{key}` is no longer redacted from logged bodies"
    redact = _decl_block(src, "private static func redact(")
    assert "redactedKeys" in redact, "redact() no longer applies the key list"


# ── 6. Anti-vacuity ────────────────────────────────────────────────────────────


def test_the_blocks_are_slices_not_whole_files():
    src = _read(_ANALYTICS)
    block = _decl_block(src, "private func flush()")
    assert 0 < len(block) < len(src), (
        "_decl_block stopped bounding — every assertion scoped to a declaration is now "
        "satisfiable from anywhere in the file"
    )


def test_the_array_extractor_stops_at_its_own_closing_bracket():
    """`_decl_block` would have walked past a `[...]` literal into the next function's body."""
    sample = 'private static let redactedKeys = [\n    "a", "b",\n]\n\nfunc other() {\n    let c = 1\n}'
    block = _array_literal(sample, "private static let redactedKeys")
    assert '"a"' in block and '"b"' in block
    assert "other" not in block and "let c" not in block


def test_comment_only_mentions_do_not_satisfy_the_assertions():
    stripped = _strip_comments(
        "// onChange(of: appState.user.tier)\n// flushFromTimer\nlet x = 1  // isActiveTab"
    )
    assert "user.tier" not in stripped
    assert "flushFromTimer" not in stripped
    assert "isActiveTab" not in stripped
    assert "let x = 1" in stripped
