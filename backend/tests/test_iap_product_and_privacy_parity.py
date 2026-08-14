"""IAP product ids and the IAP privacy filing must agree across every surface.

TWO BUG CLASSES, both of which had already happened by 2026-08-14.

1. PRODUCT-ID DRIFT. A StoreKit product id lives in four places — the local
   `Caydex.storekit` config, the Swift product list, the `credit_packs` seed in migration
   117, and App Store Connect. A real purchase VERIFIES against Apple and then fails to map
   to a plan, so the user is charged and gets nothing. Nothing in CI could see this: the
   launch checklist warned about it in prose and had no test. ASC itself is outside this
   test's reach — but the three in-repo surfaces can be pinned, and a mismatch here is a
   superset of the ASC mismatch in practice.

2. THE PRIVACY FILING GOING STALE BEHIND A SHIPPED FEATURE. `PrivacyInfo.xcprivacy` is
   compiled into the binary, so a wrong answer costs a NEW BUILD to fix after submission.
   It carried "Purchase History — no StoreKit purchase flow exists yet. ADD IT when IAP
   ships", and `app-privacy-answers.md` listed Purchases under "verified absent", for a
   week after StoreKit shipped. Both were prose reminders with nothing reading them. This
   is that reader: if the purchase flow exists, both surfaces must declare it, and if it is
   ever removed, both must stop — the check fails in BOTH directions, like the Photos one
   in `test_ios_feedback_flow.py` that this is modelled on.
"""

import plistlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_STOREKIT = _REPO / "frontend/ios/Caydex.storekit"
_MANIFEST = _IOS / "PrivacyInfo.xcprivacy"
_ANSWERS = _REPO / "documents/legal/app-privacy-answers.md"
_MIGRATION_117 = _REPO / "backend/database/migrations/117_purchased_credits_and_packs.sql"

_PRODUCT_RE = re.compile(r"com\.phan\.caydex\.[a-z0-9.]+")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path.relative_to(_REPO)}")
    return path.read_text(encoding="utf-8")


def _storekit_products() -> dict[str, str]:
    """product id -> type ("Consumable" / "RecurringSubscription") from the local config."""
    import json

    data = json.loads(_read(_STOREKIT))
    found: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            pid = node.get("productID")
            if isinstance(pid, str) and pid.startswith("com.phan.caydex."):
                found[pid] = str(node.get("type") or "")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


# ── 1. Product-id parity ────────────────────────────────────────────────────────


def test_the_storekit_config_is_readable_and_not_empty():
    """Guard against the guard — a moved or emptied config would pass everything below."""
    products = _storekit_products()
    assert len(products) >= 6, f"expected >=6 products in Caydex.storekit, found {products}"
    assert any(t == "Consumable" for t in products.values()), "no consumable packs found"
    assert any("Subscription" in t for t in products.values()), "no subscriptions found"


def test_every_consumable_pack_is_seeded_in_migration_117():
    """A pack Apple sells but `credit_packs` does not know = verified purchase, no credits."""
    packs = {p for p, t in _storekit_products().items() if t == "Consumable"}
    seed = _read(_MIGRATION_117)
    missing = sorted(p for p in packs if p not in seed)
    assert not missing, (
        f"consumable products absent from the credit_packs seed in 117: {missing}. "
        "The purchase verifies against Apple and then cannot be mapped to a credit grant — "
        "the user is charged and receives nothing."
    )


def _swift_product_ids() -> set[str]:
    """Ids declared in `StoreKitService.ProductID`.

    Scoped to that enum on purpose. A repo-wide grep for `com.phan.caydex.*` also matches
    Keychain service names (`…​.guest`, `…​.network`) and the credit-pack PREFIX quoted in a
    doc comment — none of which are products.
    """
    src = _read(_IOS / "Core/Services/StoreKitService.swift")
    start = src.find("enum ProductID {")
    assert start != -1, "StoreKitService.ProductID not found — this scan has drifted"
    depth, i = 0, src.index("{", start)
    for end in range(i, len(src)):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
            if depth == 0:
                break
    body = src[i : end + 1]
    # Only `static let x = "…"` declarations; skips `//` doc comments quoting the prefix.
    return {
        m.group(1)
        for line in body.splitlines()
        if not line.strip().startswith("//")
        for m in [re.search(r'=\s*"(com\.phan\.caydex\.[a-z0-9.]+)"', line)]
        if m
    }


def test_ios_and_the_storekit_config_declare_exactly_the_same_products():
    swift = _swift_product_ids()
    config = set(_storekit_products())
    assert swift, "no product ids parsed out of ProductID — scan drifted"
    assert swift == config, (
        "StoreKitService.ProductID and Caydex.storekit disagree.\n"
        f"  in Swift only:   {sorted(swift - config) or '—'}\n"
        f"  in config only:  {sorted(config - swift) or '—'}\n"
        "A product the app asks for but Apple does not sell is silently dropped from "
        "Product.products(for:), so the paywall renders an incomplete or empty list."
    )


def test_backend_subscription_ids_match_the_ios_ones():
    """`iap_service` maps product id -> tier. A mismatch verifies the purchase and then
    raises 'unmapped product', i.e. payment taken, tier not granted."""
    from app.config import settings

    swift = _swift_product_ids()
    for name in ("IAP_PRODUCT_PRO_MONTHLY", "IAP_PRODUCT_MAX_MONTHLY"):
        value = getattr(settings, name)
        assert value in swift, (
            f"settings.{name} = {value!r} is not a product the iOS app offers ({sorted(swift)}). "
            "The backend would refuse a purchase the app can actually make."
        )


def test_every_consumable_starts_with_the_backends_credit_pack_prefix():
    """The backend routes a verified transaction to the CREDIT path by prefix. A pack
    outside the prefix is diagnosed as an unmapped subscription and refused."""
    from app.config import settings

    prefix = settings.IAP_CREDIT_PACK_PREFIX
    packs = {p for p, t in _storekit_products().items() if t == "Consumable"}
    stray = sorted(p for p in packs if not p.startswith(prefix))
    assert not stray, f"consumables outside IAP_CREDIT_PACK_PREFIX {prefix!r}: {stray}"

    # ...and the inverse: no SUBSCRIPTION may accidentally carry the credit prefix, or it
    # would be granted as credits instead of a tier.
    subs = {p for p, t in _storekit_products().items() if "Subscription" in t}
    misrouted = sorted(p for p in subs if p.startswith(prefix))
    assert not misrouted, f"subscriptions matching the credit-pack prefix: {misrouted}"


def test_every_product_has_a_type():
    for pid, ptype in _storekit_products().items():
        assert ptype, f"{pid} has no type in Caydex.storekit"


# ── 2. IAP ⇄ privacy-filing parity (fails in BOTH directions) ───────────────────

_PURCHASE_HISTORY = "NSPrivacyCollectedDataTypePurchaseHistory"


def _purchase_flow_exists() -> list[str]:
    """Swift files that actually invoke a StoreKit purchase."""
    hits = []
    for path in sorted(_IOS.rglob("*.swift")):
        text = path.read_text(encoding="utf-8")
        if "import StoreKit" in text and re.search(r"\.purchase\(|Product\.products", text):
            hits.append(str(path.relative_to(_REPO)))
    return hits


def test_the_manifest_parses_and_declares_data_types():
    """Guard against the guard: an unparseable manifest must fail loudly, not silently."""
    with _MANIFEST.open("rb") as fh:
        plist = plistlib.load(fh)
    types = plist.get("NSPrivacyCollectedDataTypes")
    assert isinstance(types, list) and types, "manifest declares no collected data types"


def test_a_shipped_purchase_flow_is_declared_in_the_manifest_and_the_answer_sheet():
    uses_storekit = _purchase_flow_exists()
    with _MANIFEST.open("rb") as fh:
        plist = plistlib.load(fh)
    declared = {
        entry.get("NSPrivacyCollectedDataType")
        for entry in plist.get("NSPrivacyCollectedDataTypes", [])
    }
    manifest_declares = _PURCHASE_HISTORY in declared
    answers = _read(_ANSWERS)
    # The answer sheet must SELECT it, not merely mention it in a "revisit when" note.
    answers_declares = "Purchase History" in answers and re.search(
        r"^\|\s*Purchases\s*→\s*\*\*Purchase History\*\*", answers, re.M
    )

    if uses_storekit:
        assert manifest_declares, (
            f"a StoreKit purchase flow ships ({uses_storekit[:3]}) but PrivacyInfo.xcprivacy "
            f"does not declare {_PURCHASE_HISTORY}. The manifest is compiled into the binary — "
            "fixing this after submission costs a new build."
        )
        assert answers_declares, (
            "a StoreKit purchase flow ships but app-privacy-answers.md does not select "
            "Purchases → Purchase History in its §3 table. Apple compares the manifest "
            "against the App Privacy answers you paste into App Store Connect."
        )
    else:
        assert not manifest_declares, (
            f"PrivacyInfo.xcprivacy declares {_PURCHASE_HISTORY} but no StoreKit purchase "
            "flow was found — an over-declaration is still a wrong answer."
        )


def test_purchase_history_is_linked_because_credit_purchases_stores_a_user_id():
    """`credit_purchases.user_id` is NOT NULL, so the history is tied to an account.
    Answering "not linked" here would be a false statement to Apple."""
    seed = _read(_MIGRATION_117)
    assert re.search(r"user_id\s+UUID\s+NOT NULL", seed), (
        "credit_purchases.user_id is no longer NOT NULL — re-check whether Purchase History "
        "is still 'Linked to the user' before changing the manifest."
    )
    with _MANIFEST.open("rb") as fh:
        plist = plistlib.load(fh)
    for entry in plist.get("NSPrivacyCollectedDataTypes", []):
        if entry.get("NSPrivacyCollectedDataType") == _PURCHASE_HISTORY:
            assert entry.get("NSPrivacyCollectedDataTypeLinked") is True
            assert entry.get("NSPrivacyCollectedDataTypeTracking") is False
            break
    else:
        pytest.fail(f"{_PURCHASE_HISTORY} not declared — see the test above")


def test_payment_info_is_never_declared():
    """Apple handles payment; we only ever see a signed transaction. Declaring Payment Info
    would be wrong in the other direction, and invites questions we cannot answer yes to."""
    with _MANIFEST.open("rb") as fh:
        plist = plistlib.load(fh)
    declared = {
        e.get("NSPrivacyCollectedDataType") for e in plist.get("NSPrivacyCollectedDataTypes", [])
    }
    assert "NSPrivacyCollectedDataTypePaymentInfo" not in declared
