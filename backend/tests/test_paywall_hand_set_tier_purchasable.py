"""A tier set on the account by hand must leave every paid plan purchasable.

WHY THIS FILE EXISTS. The 2026-09-24 pre-resubmission audit (finding payments-1, verdict
will-reject) found that App Review could not buy the Max subscription:

  * The review demo account is on Max (`users.tier = premium`, no `subscriptions` row), because
    Learn narration — the feature the 2.5.4 rejection was about — is Pro/Max only.
  * `PaywallView.planCTA` replaced the buy button with "Current Plan" whenever
    `plan.tier == user.tier`, so Max had no button, while the review notes promised "the Max
    subscription … available for sandbox purchase" (Guideline 2.1, "unable to locate the IAP").
  * The Profile Upgrade card rendered for Free accounts only, so a paid account had no route
    to the plans at all except Buy Credits → "more credits with a plan".

The fix: the paywall reads `GET /users/me/subscription` and shows "Current Plan" for a paid
plan only when a STORE (apple | stripe) stands behind it. This file pins both halves:

  1. the backend contract the iOS rule depends on — no subscriptions row → `store` is null;
  2. the Swift, source-scanned (comment-stripped, brace-bound, per testing.md §3).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.api.v1.endpoints import users as users_ep

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "PaywallViewModel.swift"
_PAYWALL = _IOS / "Views" / "Screens" / "PaywallView.swift"
_PROFILE = _IOS / "Views" / "Screens" / "ProfileView.swift"


# ── 1. backend contract ─────────────────────────────────────────────────────────────────


class _FakeSubs:
    def __init__(self, row):
        self._row = row

    def get_user_subscription(self, user_id):
        return self._row


@pytest.mark.asyncio
async def test_a_hand_set_tier_reports_no_store():
    """The demo account's shape: tier on the users row, no subscriptions row."""
    with patch.object(users_ep, "SubscriptionService", lambda: _FakeSubs(None)):
        out = await users_ep.get_my_subscription(user={"id": "u1", "tier": "premium"})
    assert out.tier == "premium"
    assert out.store is None, "iOS treats store=None as 'not paid for' → Max stays purchasable"


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["apple", "stripe", "promo"])
async def test_a_subscription_row_reports_its_store(store):
    row = {"tier": "premium", "status": "active", "current_period_end": None, "store": store}
    with patch.object(users_ep, "SubscriptionService", lambda: _FakeSubs(row)):
        out = await users_ep.get_my_subscription(user={"id": "u1", "tier": "premium"})
    assert out.store == store


# ── 2. the Swift ────────────────────────────────────────────────────────────────────────


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(
        "" if ln.strip().startswith("//") else re.sub(r"\s//.*$", "", ln)
        for ln in src.splitlines()
    )


def _block(src: str, header: str) -> str:
    start = src.index(header)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i : j + 1]
    raise AssertionError(f"unbalanced after {header!r}")


def test_plan_cta_does_not_compare_tiers_alone():
    cta = _block(_strip(_PAYWALL.read_text(encoding="utf-8")), "private func planCTA")
    assert "PaywallViewModel.showsCurrentPlan(" in cta
    assert "currentTierIsStoreBacked: viewModel.currentTierIsStoreBacked" in cta
    assert not re.search(r"if\s+plan\.userTier\s*==\s*currentTier\s*\{", cta), (
        "planCTA is back to `plan.userTier == currentTier` — a hand-set tier (App Review's "
        "demo account) hides the buy button for the plan the reviewer must purchase"
    )


def _backend_store_literals() -> set:
    src = (Path(__file__).resolve().parents[1] / "app" / "services" / "iap_service.py").read_text()
    return set(re.findall(r'"store":\s*"([^"]+)"', src))


def test_every_store_value_the_backend_writes_counts_as_store_backed():
    """THE parity guard. The DTO comment said 'apple | stripe | promo'; the backend writes
    'app_store'. Trusting the comment would have shown every real Apple subscriber a 'Choose'
    button on the plan they already pay for — the double-charge this rule exists to prevent."""
    written = _backend_store_literals()
    assert written, "no `\"store\": \"…\"` literal found in iap_service.py — this scan drifted"
    body = _block(_strip(_VM.read_text(encoding="utf-8")), "static func isStoreBacked")
    case = re.search(r"case\s+([^:]+):\s*return true", body)
    assert case, body
    accepted = set(re.findall(r'"([^"]+)"', case.group(1)))
    missing = written - accepted
    assert not missing, f"backend writes store={sorted(missing)} but iOS isStoreBacked rejects it"
    assert re.search(r"default:\s*return false", body), body
    assert "promo" not in accepted, "a complimentary row is not a paid subscription"


def test_paying_for_requires_an_entitling_same_tier_unexpired_row():
    """`store` alone is not enough: the row outlives the subscription (an expired/revoked Apple
    row keeps its store), so a demo account re-set to Max would read as 'already paying'."""
    vm = _strip(_VM.read_text(encoding="utf-8"))
    body = _block(vm, "static func isPayingFor")
    assert "guard isStoreBacked(store: subscription.store) else { return false }" in body
    assert "guard entitlingStatuses.contains(status) else { return false }" in body
    assert "guard subscription.userTier == accountTier else { return false }" in body
    assert "endDate <= now" in body and "return false" in body
    statuses = re.search(r"entitlingStatuses: Set<String> = \[([^\]]+)\]", vm)
    assert statuses, "entitlingStatuses not found"
    ios = set(re.findall(r'"([^"]+)"', statuses.group(1)))
    backend = (Path(__file__).resolve().parents[1] / "app" / "services" / "iap_service.py").read_text()
    be = set(re.findall(r'"([^"]+)"', re.search(r"_ENTITLING_STATUSES = \{([^}]+)\}", backend).group(1)))
    assert be <= ios, f"iOS entitling statuses {sorted(ios)} miss backend's {sorted(be - ios)}"
    assert not ({"expired", "revoked"} & ios)
    load = _block(vm, "private func loadSubscription")
    assert "Self.isPayingFor(" in load, "the paywall must use the full rule, not isStoreBacked alone"


def test_current_plan_rule_keeps_free_and_requires_a_store_for_paid():
    body = _block(_strip(_VM.read_text(encoding="utf-8")), "static func showsCurrentPlan")
    assert "guard planTier == currentTier else { return false }" in body
    assert "return isFreePlan || currentTierIsStoreBacked" in body


def test_subscription_read_is_gated_on_sign_in_and_fails_closed():
    vm = _strip(_VM.read_text(encoding="utf-8"))
    body = _block(vm, "private func loadSubscription")
    assert "guard isSignedIn else" in body, "a signed-out read raises the sign-in prompt"
    catch = body[body.index("} catch {"):]
    assert "currentTierIsStoreBacked = true" in catch, "a failed read must not expose a buy button"
    assert re.search(r"@Published private\(set\) var currentTierIsStoreBacked: Bool = true", vm)


def test_paid_accounts_have_a_route_to_the_plans():
    profile = _strip(_PROFILE.read_text(encoding="utf-8"))
    i = profile.index("if viewModel.userTier == .free {")
    region = profile[i : i + 1500]
    assert "} else {" in region and region.count("showPaywall = true") >= 2, (
        "ProfileView offers the plans to Free accounts only — a paid account (App Review's demo "
        "account) has no route to the subscriptions"
    )


# MUTATION_LOG (hand-run 2026-09-24, each reverted):
#  1. planCTA back to `if plan.userTier == currentTier {` -> test_plan_cta_... FAILED ✅
#  2. isStoreBacked: removed "app_store" (the literal the backend writes)
#       -> test_every_store_value_the_backend_writes_... FAILED ✅
#  2b. isPayingFor: dropped the `subscription.userTier == accountTier` guard
#       -> test_paying_for_requires_... FAILED ✅
#  3. loadSubscription catch: `= false` -> test_subscription_read_... FAILED ✅
#  4. ProfileView: removed the else branch -> test_paid_accounts_... FAILED ✅
