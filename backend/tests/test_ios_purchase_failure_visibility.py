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


def _strip_comments(src: str) -> str:
    """Drop `//` comment lines and trailing comments.

    Mandatory for any assertion of the form "token X must NOT appear": the codebase explains
    its subtler invariants in prose that quotes the very identifiers being forbidden, so an
    unstripped scan fails on documentation rather than on code.
    """
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


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


# ── Defect 3: only ONE of the two 409s may finish the transaction ─────────────────────


def test_only_purchase_already_linked_finishes_the_transaction():
    """`handle()` must finish a terminal 409 ONLY when the server actually recorded it.

    The backend raises two structurally different 409s. `PURCHASE_ALREADY_LINKED` means the
    transaction IS recorded against another account — somebody was credited, the condition can
    never clear, so finishing it is right and stops Apple redelivering forever.
    `PURCHASE_ACCOUNT_MISMATCH` is refused BEFORE any grant: no `credit_purchases` row, nobody
    credited. Finishing THAT one deletes a purchase the user paid for, with no redelivery left
    to repair it — it must fall through to the "leave unfinished" branch so the same transaction
    is granted once the buying account signs in.

    Naming `purchaseAccountMismatch` anywhere in this function is therefore the regression: the
    only correct handling is to NOT special-case it.
    """
    # Comments STRIPPED before scanning. This function is heavily commented precisely because
    # the distinction is subtle, and those comments name `.purchaseAccountMismatch` to explain
    # why it must NOT be special-cased — a raw scan reads that prose as the very branch it is
    # forbidding. (See `project_source_scan_guard_vacuity`: strip comments, bound the window.)
    body = _strip_comments(_func_body(_read(_SERVICE), "private func handle("))

    assert "purchaseAlreadyLinked" in body and "await transaction.finish()" in body, (
        "the terminal-409 branch is gone — an already-linked transaction will redeliver on "
        "every launch forever"
    )
    assert "purchaseAccountMismatch" not in body, (
        "handle() special-cases .purchaseAccountMismatch. That 409 means the server recorded "
        "NOTHING, so any branch naming it here is either finishing a purchase nobody was "
        "credited for, or is dead code that invites someone to. Let it fall through."
    )


def test_the_two_purchase_409_codes_map_to_distinct_apperror_cases():
    """A shared case would make the finish-decision above unrepresentable."""
    err = _read(_ROOT / "Core/Utilities/AppError.swift")
    assert 'if code == "PURCHASE_ALREADY_LINKED"' in err
    assert 'if code == "PURCHASE_ACCOUNT_MISMATCH"' in err
    assert "case purchaseAccountMismatch(message: String)" in err, (
        "PURCHASE_ACCOUNT_MISMATCH must have its OWN AppError case — falling through to "
        ".apiError would lose the distinction handle() depends on"
    )


# ── Defect 4: exactly ONE card may say "Processing…" ──────────────────────────────────


def test_tier_to_product_id_is_injective_so_one_purchase_marks_one_card():
    """At most one plan card can ever read "Processing…".

    The paywall decides each card's label by comparing the store's single
    `purchasingProductID` against THAT plan's product id. Two cards can therefore both match
    only if two tiers map to the SAME product id — so this pins the mapping as injective over
    the tiers the backend actually ships (`plan_credits`: free / pro / premium).

    Reported as "tapping Choose Pro makes both Pro and Max say Processing", which is the
    behaviour the pre-`purchasingProductID` code had (driving the label from the global
    `isPurchasing`). This is the assertion that keeps it from coming back.
    """
    body = _strip_comments(_func_body(_read(_SERVICE), "func productID(for tier: String)"))

    mapped = dict(re.findall(r'case\s+"(\w+)"[^:]*:\s*return\s+ProductID\.(\w+)', body))
    # `case "premium", "max":` — the multi-label form the regex above sees only partly.
    for m in re.finditer(r'case\s+((?:"\w+"\s*,\s*)+"\w+")\s*:\s*return\s+ProductID\.(\w+)', body):
        for tier in re.findall(r'"(\w+)"', m.group(1)):
            mapped[tier] = m.group(2)

    assert {"pro", "premium"} <= set(mapped), (
        f"the shipped tiers are no longer mapped — got {mapped}. `plan_credits` seeds "
        "free/pro/premium, and an unmapped paid tier means its Choose button does nothing."
    )
    paid = {t: p for t, p in mapped.items() if t in {"pro", "premium"}}
    assert len(set(paid.values())) == len(paid), (
        f"two tiers map to the SAME product id ({paid}) — one purchase would light up BOTH "
        "plan cards as 'Processing…', which is exactly the reported bug"
    )


def test_the_paywall_cta_label_is_per_product_not_global():
    """`isPurchasing` is global to the service. Driving the LABEL from it is what made every
    card read "Processing…" on any tap; only the disabled/dimmed state may use it."""
    body = _strip_comments(_func_body(_read(_VIEW), "private func planCTA("))

    assert "isThisPlan" in body, "the per-product comparison is gone"
    assert "purchasingProductID" in body, (
        "the CTA must compare the store's purchasingProductID against THIS plan's product id"
    )
    # The label ternary must key off the per-product flag, never the global one.
    label = re.search(r"Text\(\s*(is\w+)\s*\n?\s*\?", body) or re.search(r"Text\((is\w+)\s*\?", body)
    assert label and label.group(1) == "isThisPlan", (
        "the \"Processing…\" label must be driven by isThisPlan, not by the global "
        f"isPurchasing (found: {label.group(1) if label else 'no ternary'})"
    )


def test_the_credit_pack_cta_label_is_per_product_too():
    """Same rule on the Buy Credits screen — four packs, one purchase."""
    view = _strip_comments(_read(_ROOT / "Views/Screens/BuyCreditsView.swift"))
    assert "isThisPack" in view
    assert re.search(r"Text\(isThisPack \?", view), (
        "the pack CTA label must be driven by isThisPack, not by the global isPurchasing"
    )


# ── The blob sent to /billing/verify must be Apple's SIGNED envelope ──────────────────


def test_the_client_sends_the_jws_not_the_decoded_transaction():
    """`handle` must forward `VerificationResult.jwsRepresentation`, never
    `Transaction.jsonRepresentation`.

    This shipped, and it broke IAP completely in EVERY environment:

      let signed = String(decoding: transaction.jsonRepresentation, as: UTF8.self)

    under a comment asserting that property "is the JWS Apple signed". It is not — it is the
    DECODED payload as JSON. The backend hands the blob to PyJWT, which split the JSON on "."
    and base64-decoded the first fragment into binary:

      DecodeError: Invalid header string: 'utf-8' codec can't decode byte 0x9a in position 1

    …so every purchase 400'd. No test saw it because nothing read this line, and the two
    surfaces that could have disagreed (product ids, privacy manifest) both agreed.

    The second consequence is the one that makes this a security assertion rather than a
    correctness one: the decoded JSON is precisely the client-editable form the signature
    exists to defend against. Had the server ever accepted it, a caller could have minted any
    transaction they liked. So this test pins BOTH directions — the right property present and
    the wrong one absent.
    """
    body = _strip_comments(_func_body(_read(_ROOT / "Core/Services/StoreKitService.swift"),
                                      "private func handle("))
    assert body, "StoreKitService.handle not found — this guard has drifted"

    assert "jwsRepresentation" in body, (
        "the verify hand-off must send VerificationResult.jwsRepresentation — the signed "
        "envelope the backend re-verifies Apple's signature over"
    )
    assert "jsonRepresentation" not in body, (
        "handle() references Transaction.jsonRepresentation — that is the DECODED, "
        "client-editable payload, not the JWS. Sending it 400s every purchase and would "
        "defeat signature verification if the server ever accepted it."
    )
    # Anti-vacuity: comment-stripping must not have emptied the window, and the property must
    # be read off the VERIFICATION RESULT rather than off some other value that happens to
    # expose a similarly-named member.
    assert "verifyPurchase" in body and "case .verified" in body
    assert re.search(r"verificationResult\.jwsRepresentation", body), (
        "jwsRepresentation must be read from the VerificationResult parameter"
    )
