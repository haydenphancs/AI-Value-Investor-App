""""Restore Purchases" must sweep consumables too.

`Transaction.currentEntitlements` — the source `restorePurchases()` reads — EXCLUDES
consumables by design. Apple does not restore them at all; the server ledger is the restore
mechanism and `Transaction.unfinished` is how the client reaches it.

So a credit pack the user paid for whose `POST /billing/verify` never landed (offline at the
moment of purchase, a 503, an app kill) was invisible to the button, and Account → General
Settings → Restore Purchases answered "No previous purchases found for this Apple Account" to
someone whose money Apple had already taken.

`BuyCreditsViewModel.restore()` already did both sweeps; the Account screen's copy did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"


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


@pytest.fixture
def subscription_section() -> str:
    if not _SETTINGS.exists():
        pytest.skip(f"{_SETTINGS} not present")
    src = _code_only(_SETTINGS.read_text(encoding="utf-8"))
    return _braced_block(src, "private var subscriptionSection: some View {")


def test_the_window_is_really_the_subscription_section(subscription_section):
    """Anti-vacuity: every assertion below is scoped to this block."""
    assert "restorePurchases()" in subscription_section, (
        "the brace-bounded window does not contain the restore call, so it is not the "
        "section and the assertions below prove nothing"
    )


def test_restore_also_drains_unfinished_transactions(subscription_section):
    assert "drainUnfinishedTransactions()" in subscription_section, (
        "Restore Purchases only sweeps currentEntitlements, which excludes consumables — a "
        "paid-but-unapplied credit pack is invisible and the user is told they never bought "
        "anything"
    )


def test_nothing_to_restore_requires_both_sweeps_to_be_empty(subscription_section):
    """"No previous purchases found" must be unreachable while EITHER sweep saw something."""
    assert "No previous purchases found" in subscription_section, (
        "the empty-case copy is gone; if it was renamed, update this guard"
    )
    # The message is chosen from a combined count, not from the entitlement sweep alone.
    assert "drained.seen" in subscription_section and "restored.seen" in subscription_section, (
        "the outcome is not derived from BOTH sweeps, so a consumable-only history still "
        "renders as 'nothing to restore'"
    )


def test_granted_credits_are_reported_to_the_user(subscription_section):
    """A silent credit grant reads as a no-op — the user tapped restore and nothing said what
    happened."""
    assert "creditsGranted" in subscription_section, (
        "restored credits are never surfaced in the result message"
    )
