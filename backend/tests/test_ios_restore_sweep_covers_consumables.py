""""Restore Purchases" must sweep consumables too — and sync, and tell the truth.

`Transaction.currentEntitlements` — the source `restorePurchases()` reads — EXCLUDES
consumables by design. Apple does not restore them at all; the server ledger is the restore
mechanism and `Transaction.unfinished` is how the client reaches it.

So a credit pack the user paid for whose `POST /billing/verify` never landed (offline at the
moment of purchase, a 503, an app kill) was invisible to the button, and Account → General
Settings → Restore Purchases answered "No previous purchases found for this Apple Account" to
someone whose money Apple had already taken.

`BuyCreditsViewModel.restore()` already did both sweeps; the Account screen's copy did not.

2026-11-15 audit, three more defects on the same rows:

1. No `AppStore.sync()`. Without it `currentEntitlements` holds only what THIS device already
   knows, so a subscriber restoring on a new or switched device could be told "No previous
   purchases found". The sync now runs in `StoreKitService.syncAndRestore()` — and ONLY from
   the three user-tapped restore buttons, because it can show the Apple Account password
   prompt (never from launch, sign-in or the `Transaction.updates` listener).
2. The copy was classified from counts alone: a credit pack the server had already recorded
   (a replay, 0 credits) or a lapsed renewal read "Restored your subscription." to a
   non-subscriber, and a failure beside a success was dropped.
3. "Manage Subscription" had no in-flight guard and never re-read the entitlement after
   Apple's sheet closed, so a plan change whose background verify failed kept showing the old
   plan until the next cold launch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_SERVICE = _IOS / "Core" / "Services" / "StoreKitService.swift"
_PAYWALL_VM = _IOS / "ViewModels" / "PaywallViewModel.swift"
_CREDITS_VM = _IOS / "ViewModels" / "BuyCreditsViewModel.swift"


def _code_only(src: str) -> str:
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _braced_block(src: str, opener: str) -> str:
    start = src.index(opener) + len(opener) - 1
    assert src[start] == "{", f"{opener!r} must end at its opening brace"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


@pytest.fixture
def subscription_section() -> str:
    return _braced_block(_read(_SETTINGS), "private var subscriptionSection: some View {")


@pytest.fixture
def service() -> str:
    return _read(_SERVICE)


@pytest.fixture
def sync_and_restore(service) -> str:
    return _braced_block(service, "func syncAndRestore() async -> RestoreResult {")


@pytest.fixture
def restore_result(service) -> str:
    return _braced_block(service, "struct RestoreResult: Sendable {")


@pytest.fixture
def subscription_message(restore_result) -> str:
    return _braced_block(restore_result, "var subscriptionMessage: String {")


@pytest.fixture
def sweep_count(service) -> str:
    return _braced_block(service, "mutating func count(_ purchase: PurchaseApplied) {")


def test_the_window_is_really_the_subscription_section(subscription_section):
    """Anti-vacuity: the button assertions below are scoped to this block."""
    assert "syncAndRestore()" in subscription_section, (
        "the brace-bounded window does not contain the restore call, so it is not the "
        "section and the assertions below prove nothing"
    )
    assert "subscriptionMessage" in subscription_section, (
        "the Settings restore no longer takes its copy from RestoreResult.subscriptionMessage"
    )


def test_restore_syncs_then_drains_then_reads_entitlements(sync_and_restore):
    order = ["AppStore.sync()", "drainUnfinishedTransactions()", "restorePurchases()"]
    for token in order:
        assert token in sync_and_restore, (
            f"syncAndRestore() no longer calls {token}. Without the sync a new device misses "
            f"its subscription; without the unfinished sweep a paid-but-unapplied credit pack "
            f"is invisible; without the entitlement sweep nothing is restored at all"
        )
    positions = [sync_and_restore.index(t) for t in order]
    assert positions == sorted(positions), (
        "syncAndRestore() must sync FIRST (so the sweeps read Apple's fresh state), then the "
        "unfinished sweep, then the entitlement sweep"
    )


def test_a_cancelled_sync_still_runs_the_sweeps(sync_and_restore):
    """Dismissing the Apple Account prompt is not a failure: the sweeps still read what this
    device holds. A `return` or `throw` in that arm would make Cancel skip the restore."""
    marker = "catch StoreKit.StoreKitError.userCancelled"
    assert marker in sync_and_restore, (
        "the user-cancelled sync is not handled on its own, so a dismissed password prompt "
        "is reported as an App Store failure (or worse, skips the sweeps)"
    )
    arm = sync_and_restore[sync_and_restore.index(marker):
                           sync_and_restore.index("let drained")]
    assert not re.search(r"\b(return|throw)\b", arm), (
        "an AppStore.sync failure path returns or throws before the sweeps run"
    )
    assert "Self.log.error" in arm, "a real sync failure is not logged with its error case"


def _swift_files() -> list[Path]:
    if not _IOS.exists():
        pytest.skip(f"{_IOS} not present")
    return sorted(_IOS.rglob("*.swift"))


def test_app_store_sync_runs_only_from_user_tapped_restore_buttons(sync_and_restore):
    """`AppStore.sync()` can present the Apple Account password prompt. From the launch or
    sign-in drain, or the `Transaction.updates` listener, that is a password sheet nobody
    asked for — so it lives in ONE function, and only the three Restore buttons call it."""
    sync_sites = []
    callers = []
    for path in _swift_files():
        code = _code_only(path.read_text(encoding="utf-8"))
        if "AppStore.sync(" in code:
            sync_sites.append(path.relative_to(_IOS).as_posix())
        if re.search(r"\.syncAndRestore\(\)", code):
            callers.append(path.relative_to(_IOS).as_posix())

    assert sync_sites == ["Core/Services/StoreKitService.swift"], (
        f"AppStore.sync() is called outside StoreKitService.syncAndRestore(): {sync_sites}"
    )
    service = _read(_SERVICE)
    assert service.count("AppStore.sync(") == 1 and "AppStore.sync()" in sync_and_restore, (
        "StoreKitService calls AppStore.sync() somewhere other than syncAndRestore()"
    )
    assert sorted(callers) == sorted([
        "Views/Screens/AppSettingsView.swift",
        "ViewModels/PaywallViewModel.swift",
        "ViewModels/BuyCreditsViewModel.swift",
    ]), f"syncAndRestore() has a caller that is not a Restore button: {callers}"

    # …and inside those files, only the restore entry points.
    assert ".syncAndRestore()" in _braced_block(
        _read(_SETTINGS), "private var subscriptionSection: some View {")
    for vm in (_PAYWALL_VM, _CREDITS_VM):
        code = _read(vm)
        body = _braced_block(code, "func restore() async {")
        assert ".syncAndRestore()" in body
        assert code.count(".syncAndRestore()") == 1, (
            f"{vm.name} calls syncAndRestore() outside restore()"
        )


def test_both_sweeps_tally_what_the_backend_applied(service):
    for func in ("func restorePurchases() async -> SweepResult {",
                 "func drainUnfinishedTransactions() async -> SweepResult {"):
        body = _braced_block(service, func)
        assert "result.count(" in body, (
            f"{func.split('(')[0]} counts an applied transaction without classifying it, so "
            f"the copy falls back to counts and calls a pack replay a restored subscription"
        )


def test_nothing_to_restore_requires_both_sweeps_to_be_empty(subscription_message, restore_result):
    """"No previous purchases found" must be unreachable while EITHER sweep saw something."""
    assert "No previous purchases found" in subscription_message, (
        "the empty-case copy is gone; if it was renamed, update this guard"
    )
    assert re.search(r"var seen: Int \{ drained\.seen \+ restored\.seen \}", restore_result), (
        "RestoreResult.seen is not derived from BOTH sweeps, so a consumable-only history "
        "still renders as 'nothing to restore'"
    )
    assert re.search(r"var applied: Int \{ drained\.applied \+ restored\.applied \}",
                     restore_result)
    empty = subscription_message.index("No previous purchases found")
    for guard in ("if applied > 0 {", "if seen > 0 {"):
        assert guard in subscription_message[:empty], (
            f"'No previous purchases found' is reachable without first ruling out {guard!r}"
        )


def test_granted_credits_are_reported_to_the_user(subscription_message):
    """A silent credit grant reads as a no-op — the user tapped restore and nothing said what
    happened."""
    assert "creditsGranted" in subscription_message, (
        "restored credits are never surfaced in the result message"
    )


def _branch_condition(block: str, needle: str) -> str:
    """The `if` / `else if` line governing the first line that contains `needle`."""
    lines = block.splitlines()
    idx = next(i for i, line in enumerate(lines) if needle in line)
    for line in reversed(lines[:idx]):
        if re.search(r"\bif\b", line):
            return line
    raise AssertionError(f"no branch governs {needle!r}")


def test_restored_subscription_copy_requires_an_applied_paid_tier(subscription_message,
                                                                   sweep_count):
    """Counts said "Restored your subscription." to a non-subscriber whenever anything was
    accepted — a pack replay, or a lapsed renewal whose winning tier is free."""
    lines = [line for line in subscription_message.splitlines()
             if "Restored your subscription" in line]
    assert lines, "the restored-subscription copy is gone; update this guard if renamed"
    block_lines = subscription_message.splitlines()
    for i, line in enumerate(block_lines):
        if "Restored your subscription" not in line:
            continue
        governing = next(prev for prev in reversed(block_lines[:i]) if re.search(r"\bif\b", prev))
        assert "subscriptionsRestored > 0" in governing, (
            f"'Restored your subscription' is shown under {governing.strip()!r}, which does "
            f"not require a subscription the backend actually applied on a paid tier"
        )
    # The tally itself: only a NON-pack applied on a non-free winning tier.
    pack_arm = sweep_count[sweep_count.index("if purchase.isCreditPack"):
                           sweep_count.index("} else {")]
    assert "subscriptionsRestored" not in pack_arm, "a credit pack is counted as a subscription"
    sub_arm = sweep_count[sweep_count.index("} else {"):]
    assert "subscriptionsRestored += 1" in sub_arm and '"free"' in sub_arm, (
        "a subscription is counted as restored without checking its winning tier is not free"
    )


def test_a_pack_replay_says_the_credits_were_already_added(subscription_message, sweep_count):
    needle = "Credits from that purchase were already added to your account."
    assert needle in subscription_message
    assert "packReplays > 0" in _branch_condition(subscription_message, needle)
    pack_arm = sweep_count[sweep_count.index("if purchase.isCreditPack"):
                           sweep_count.index("} else {")]
    assert re.search(r"purchase\.wasReplay && purchase\.creditsGranted == 0", pack_arm), (
        "packReplays is not limited to a pack the server had already recorded (0 credits)"
    )


def test_a_failure_beside_a_success_is_still_reported(restore_result, subscription_message):
    suffix = _braced_block(restore_result, "var partialFailureSuffix: String {")
    assert "guard failed > 0" in suffix and "AppError.from(" in suffix, (
        "the partial-failure suffix is not driven by failed transactions through AppError.from"
    )
    applied_arm = _braced_block(subscription_message, "if applied > 0 {")
    assert "+ partialFailureSuffix" in applied_arm, (
        "a successful restore drops the purchases that failed beside it"
    )
    credits_restore = _braced_block(_read(_CREDITS_VM), "func restore() async {")
    assert "result.partialFailureSuffix" in credits_restore, (
        "Buy Credits' restore still drops a failure whenever anything else succeeded"
    )


def test_a_sync_error_is_shown_only_when_both_sweeps_found_nothing(subscription_message):
    assert "syncError" in subscription_message
    assert subscription_message.index("syncError") > subscription_message.index("if seen > 0 {"), (
        "an App Store sync error can override a sweep that found something — the sweep is the "
        "real answer"
    )
    credits_restore = _braced_block(_read(_CREDITS_VM), "func restore() async {")
    assert credits_restore.index("result.syncError") > credits_restore.index("} else if seen > 0 {")


def test_manage_subscription_is_guarded_and_refreshes_after_the_sheet(subscription_section):
    body = _braced_block(_read(_SETTINGS), "private func openManageSubscription() {")
    assert "guard !isOpeningSubscriptions else { return }" in body, (
        "no in-flight guard: a second tap queues a second subscriptions sheet"
    )
    assert "defer { isOpeningSubscriptions = false }" in body, (
        "the in-flight flag is not reset on every exit, so the row can stay disabled forever"
    )
    after_sheet = body[body.index("AppStore.showManageSubscriptions(in:"):]
    drain = after_sheet.find("drainUnfinishedTransactions()")
    refresh = after_sheet.find("refreshEntitlement()")
    assert 0 <= drain < refresh, (
        "after Apple's sheet closes the app must submit any unfinished transaction and THEN "
        "re-read the tier — else a plan change whose background verify failed shows the old plan"
    )
    assert "AppStore.sync" not in body, "Manage Subscription must never trigger a password prompt"
    assert "isBusy: isOpeningSubscriptions" in subscription_section
    assert ".disabled(isOpeningSubscriptions)" in subscription_section
