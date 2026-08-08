"""A failed StoreKit purchase must be visible to the user and legible in the log.

Two independent defects made a local `ASDErrorDomain 5115` / `AMSErrorDomain 10
"Payment Sheet Failed"` undiagnosable for a whole session:

  1. `PaywallView` rendered `viewModel.errorMessage` inside an `else if` chain gated on
     `catalog == nil`. A plan button cannot exist until the catalog has loaded, so by the
     time the user can tap "Choose Pro", anything written to `errorMessage` is unreachable.
     A thrown purchase error produced NO UI at all — the button just returned to its idle
     label. Fixed by splitting `purchaseError` out and surfacing it as an alert.

  2. `StoreKitService.purchase` called `product.purchase()` bare. Apple reports payment-sheet
     PRESENTATION failures as opaque `NSError`s from its own daemons (`ASDErrorDomain` =
     appstored, `AMSErrorDomain` = AppleMediaServices) whose `localizedDescription` is only
     "The operation couldn't be completed" — so the domain and code are the entire diagnostic,
     and neither was ever logged. The product-*load* path at `:129` already logged; the
     purchase path did not.

These are Python source-scan guards because the project has no XCTest target (see
`test_ios_paid_path_guards.py`). Each assertion below is written so that reintroducing the
exact defect it names makes it fail — the failure mode of a guard like this is passing
vacuously, which is what happened to `test_ios_monitoring_gate.py`'s DEBUG-return assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_VIEW = _ROOT / "Views/Screens/PaywallView.swift"
_VM = _ROOT / "ViewModels/PaywallViewModel.swift"
_SERVICE = _ROOT / "Core/Services/StoreKitService.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _func_body(src: str, signature: str) -> str:
    """Slice from `signature` to the start of the next declaration at the same level.

    Slicing to a *later landmark* is what made the monitoring-gate guard vacuous: its window
    swallowed an unrelated `return` from the following block and passed either way. Here the
    window is bounded by the next `func `/`}` at column 4, so it cannot reach past the method.
    """
    start = src.index(signature)
    nxt = re.search(r"\n    (?:@discardableResult\n    )?(?:private )?func ", src[start + len(signature):])
    return src[start:start + len(signature) + (nxt.start() if nxt else len(src))]


# ── Defect 1: the purchase error must not ride the catalog-gated channel ──────────────


def test_purchase_failures_use_a_channel_that_is_not_gated_on_the_catalog():
    vm = _read(_VM)
    assert "@Published var purchaseError: String?" in vm, (
        "purchase failures must have their own property — `errorMessage` is only rendered "
        "while `catalog == nil`, which a post-tap failure never is"
    )

    body = _func_body(vm, "func purchase(tier: String) async {")
    assert "purchaseError" in body, "purchase(tier:) must report through purchaseError"
    assert "errorMessage" not in body, (
        "purchase(tier:) wrote to errorMessage — that is the catalog channel, and the view "
        "cannot render it once a plan button exists to tap. This is the original defect."
    )


def test_both_purchase_failure_paths_report_not_just_the_thrown_one():
    """The missing-product guard fails the same way the thrown error does — silently."""
    body = _func_body(_read(_VM), "func purchase(tier: String) async {")
    assert body.count("purchaseError =") >= 3, (
        "expected purchaseError to be cleared on entry, set on the missing-product guard, "
        "and set in the catch — one of those paths is still silent"
    )
    guard = body[body.index("guard let product"):]
    assert "purchaseError" in guard[:guard.index("}")], (
        "the missing-product guard must report through purchaseError too"
    )


def test_the_view_surfaces_purchase_errors_outside_the_catalog_branch():
    view = _read(_VIEW)
    assert re.search(r"\.alert\(\s*\"Purchase Failed\"", view), (
        "purchase failures must be surfaced by an alert — the inline errorView is unreachable "
        "after the catalog loads, and a 'Try Again' that reloads the catalog is the wrong "
        "affordance for a failed purchase anyway"
    )

    # The alert must be a modifier on the navigation container, NOT nested inside the
    # `if let catalog = ...` branch — nesting it there would re-create the original bug in a
    # new place. Everything from `if let catalog` to the end of the ScrollView is off limits.
    catalog_branch_start = view.index("if let catalog = viewModel.catalog {")
    catalog_branch_end = view.index("legalLinks", catalog_branch_start)
    assert "purchaseError" not in view[catalog_branch_start:catalog_branch_end], (
        "purchaseError is rendered inside the catalog-gated branch — same defect, new location"
    )


def test_the_catalog_error_path_still_exists():
    """Anti-vacuity. Deleting the inline errorView would also satisfy the assertions above,
    and would silently remove the retry for a genuinely failed catalog load."""
    view = _read(_VIEW)
    assert "} else if let error = viewModel.errorMessage {" in view
    assert "errorView(error)" in view
    vm = _read(_VM)
    assert "errorMessage = AppError.from(error).message" in _func_body(
        vm, "private func loadCatalog() async {"
    ), "the catalog loader must still report through errorMessage"


# ── Defect 2: the NSError domain and code must be logged ──────────────────────────────


def test_the_purchase_call_logs_the_nserror_domain_and_code():
    # The signature gained `accountID:` when consumable credit packs landed (it becomes
    # StoreKit's `appAccountToken`), so this anchors on the stable prefix rather than the full
    # line. Still exact enough to fail if the method is renamed or removed — which is what the
    # guard is for — and `_func_body` bounds the window to this method alone.
    body = _func_body(_read(_SERVICE), "func purchase(_ product: Product")

    assert "do {" in body and "result = try await product.purchase(" in body, (
        "product.purchase() must be wrapped so a presentation failure can be logged; a bare "
        "`try await` sends it straight to AppError.unknown with no diagnostic"
    )

    catch = body[body.index("} catch {"):]
    assert "ns.domain" in catch and "ns.code" in catch, (
        "the catch must log NSError domain AND code — localizedDescription alone reads as "
        "'The operation couldn't be completed', which is why 5115 was invisible"
    )
    assert re.search(r"^\s*throw error\s*$", catch, re.M), (
        "the catch must rethrow — swallowing it would turn a failed purchase into a silent "
        "no-op, which is strictly worse than the bug being fixed"
    )


def test_the_purchase_error_is_not_shown_to_the_user_raw():
    """`.claude/rules/ios-swiftui.md`: never surface a raw system/backend string. The domain
    and code go to the log; the user gets the AppError message."""
    vm = _func_body(_read(_VM), "func purchase(tier: String) async {")
    assert "AppError.from(error).message" in vm
    assert "ns.domain" not in vm and "localizedDescription" not in vm
