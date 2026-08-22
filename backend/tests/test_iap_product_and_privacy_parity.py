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
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_STOREKIT = _REPO / "frontend/ios/Caydex.storekit"
_MANIFEST = _IOS / "PrivacyInfo.xcprivacy"
_ANSWERS = _REPO / "documents/legal/app-privacy-answers.md"
_MIGRATIONS = _REPO / "backend/database/migrations"
# 117 is where the credit_purchases DDL lives, permanently. Deliberately NOT used for the
# SEED any more — see `_effective_seed`.
_MIGRATION_CREDIT_PURCHASES_DDL = _MIGRATIONS / "117_purchased_credits_and_packs.sql"

_PRODUCT_RE = re.compile(r"com\.phan\.caydex\.[a-z0-9.]+")

# App Store Connect field limits. The local config's strings are what a person retypes into
# ASC, so a string that cannot fit there can never be made identical to it — which is what
# the price/credits parity assertions below are ultimately protecting.
_ASC_DISPLAY_NAME_MAX = 30
_ASC_DESCRIPTION_MAX = 45


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


def _storekit_catalog() -> dict[str, dict]:
    """product id -> {type, displayPrice, referenceName, displayName, description}.

    Richer sibling of `_storekit_products`; the extra fields are exactly the ones a person
    retypes into App Store Connect, which is why they are worth pinning.
    """
    import json

    data = json.loads(_read(_STOREKIT))
    found: dict[str, dict] = {}

    def walk(node):
        if isinstance(node, dict):
            pid = node.get("productID")
            if isinstance(pid, str) and pid.startswith("com.phan.caydex."):
                loc = (node.get("localizations") or [{}])[0]
                found[pid] = {
                    "type": str(node.get("type") or ""),
                    "displayPrice": str(node.get("displayPrice") or ""),
                    "referenceName": str(node.get("referenceName") or ""),
                    "displayName": str(loc.get("displayName") or ""),
                    "description": str(loc.get("description") or ""),
                }
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _strip_sql_comments(sql: str) -> str:
    """Blank `--` line comments and `/* */` blocks.

    Load-bearing for `_effective_seed`: migration 138's header DISCUSSES the seed it
    supersedes and quotes the old ladder. A scan over raw text would let any migration that
    merely mentions the insert win the max(), and would then parse rows out of prose.
    """
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _effective_seed(table: str) -> tuple[Path, dict[str, dict]]:
    """(file, {pk -> row}) from the HIGHEST-numbered migration that INSERTs into `table`.

    Resolved, never hardcoded to a filename. A test pinned to `117_*.sql` silently pins a
    SUPERSEDED ladder the moment 138 reprices everything: 117 still contains a perfectly
    valid `credit_packs` seed, and `product_id in seed_text` — all the old assertion ever
    checked — keeps passing against it forever.

    Columns are indexed BY NAME from the INSERT header rather than by position: a future
    migration that lists `price_cents` before `credits` would otherwise silently swap two
    numbers that are both small integers, and every assertion downstream would still pass.
    """
    candidates: list[tuple[int, Path, str]] = []
    for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        if re.search(rf"INSERT\s+INTO\s+(?:public\.)?{table}\b", code, re.I):
            candidates.append((int(path.name[:3]), path, code))
    assert candidates, f"no migration INSERTs into {table} — the resolver has drifted"

    _, path, code = max(candidates, key=lambda c: c[0])
    m = re.search(
        rf"INSERT\s+INTO\s+(?:public\.)?{table}\s*\(([^)]*)\)\s*VALUES(.*?)(?:ON\s+CONFLICT|;)",
        code,
        re.I | re.S,
    )
    assert m, f"could not parse the {table} seed in {path.name}"
    columns = [c.strip() for c in m.group(1).split(",")]

    rows: dict[str, dict] = {}
    for tup in re.findall(r"\(([^()]*)\)", m.group(2)):
        raw = [v.strip() for v in tup.split(",")]
        if len(raw) != len(columns):
            continue
        row = {}
        for col, val in zip(columns, raw):
            if val.startswith("'") and val.endswith("'"):
                row[col] = val[1:-1]
            else:
                row[col] = int(val)
        rows[str(row[columns[0]])] = row
    return path, rows


def _pack_seed() -> dict[str, dict]:
    path, rows = _effective_seed("credit_packs")
    # Anti-vacuity: a resolver that returned {} would make every assertion below trivially
    # true. Four packs, five columns each, all ids in the credit namespace.
    assert len(rows) == 4, f"{path.name} seeded {len(rows)} packs, expected 4"
    for pid, row in rows.items():
        assert pid.startswith("com.phan.caydex.credits."), pid
        for col in ("product_id", "credits", "price_cents", "display_name", "sort_order"):
            assert col in row, f"{pid}: {path.name} seed did not yield {col}"
    return rows


def test_the_seed_resolver_finds_the_current_ladder_not_a_superseded_one():
    """Guard against the guard. If this ever resolves back to 117 while a later migration
    reprices `credit_packs`, every parity assertion below is checking history."""
    path, rows = _effective_seed("credit_packs")
    numbers = sorted(int(p.name[:3]) for p in _MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")
                     if re.search(r"INSERT\s+INTO\s+(?:public\.)?credit_packs\b",
                                  _strip_sql_comments(p.read_text(encoding="utf-8")), re.I))
    assert int(path.name[:3]) == max(numbers), (
        f"resolved {path.name} but {max(numbers)} also seeds credit_packs"
    )
    assert len(rows) == 4


def test_every_consumable_pack_is_seeded_in_the_effective_seed():
    """A pack Apple sells but `credit_packs` does not know = verified purchase, no credits.

    Equality, not containment. The two directions fail differently and both cost money:
    config-only is charged-and-granted-nothing; seed-only is a pack we price, advertise and
    cannot sell.
    """
    packs = {p for p, t in _storekit_products().items() if t == "Consumable"}
    seeded = set(_pack_seed())
    assert packs == seeded, (
        f"only in Caydex.storekit: {sorted(packs - seeded)}; only in the seed: "
        f"{sorted(seeded - packs)}. A product Apple sells that credit_packs cannot map "
        "charges the user and grants nothing."
    )


def test_the_storekit_price_equals_the_seeded_price():
    """`LAUNCH_CHECKLIST.md:775`: "ASC and the table must agree or the user is charged one
    price and shown another." Nothing tested it. ASC is out of reach, but the local config
    is the text a person copies INTO ASC, so pinning it to the seed is the closest control
    that exists."""
    seed = _pack_seed()
    catalog = _storekit_catalog()
    for pid, row in seed.items():
        # Decimal, never float: 11.99 * 100 == 1198.9999... in binary floating point.
        cents = int(Decimal(catalog[pid]["displayPrice"]) * 100)
        assert cents == row["price_cents"], (
            f"{pid}: Caydex.storekit says {catalog[pid]['displayPrice']} "
            f"({cents}c), the seed says {row['price_cents']}c"
        )


def test_the_storekit_copy_states_the_seeded_credit_count():
    """The config carries no credits field, so the number lives in `referenceName` and
    `description` — and those are the strings retyped into ASC. A pack whose copy promises
    130 credits while the seed grants 90 is a complaint, not a rounding error."""
    seed = _pack_seed()
    catalog = _storekit_catalog()
    checked = 0
    for pid, row in seed.items():
        entry = catalog[pid]
        desc_n = re.search(r"([\d,]+)", entry["description"])
        ref_n = re.search(r"\((\d+)\)", entry["referenceName"])
        assert desc_n, f"{pid}: no credit count in description {entry['description']!r}"
        assert ref_n, f"{pid}: no credit count in referenceName {entry['referenceName']!r}"
        assert int(desc_n.group(1).replace(",", "")) == row["credits"], (
            f"{pid}: description promises {desc_n.group(1)}, the seed grants {row['credits']}"
        )
        assert int(ref_n.group(1)) == row["credits"], (
            f"{pid}: referenceName says {ref_n.group(1)}, the seed grants {row['credits']}"
        )
        checked += 1
    # Anti-vacuity: a regex that stopped matching must fail, not silently check nothing.
    assert checked == 4


def test_the_storekit_copy_fits_app_store_connect_s_fields():
    """These strings have to be enterable in ASC verbatim, or "identical to the config" is
    not a reachable state. All four descriptions were 57-60 chars against a 45-char limit
    until the 138 reprice."""
    for pid, entry in _storekit_catalog().items():
        assert len(entry["displayName"]) <= _ASC_DISPLAY_NAME_MAX, (
            f"{pid}: displayName is {len(entry['displayName'])} chars, ASC allows "
            f"{_ASC_DISPLAY_NAME_MAX}"
        )
        assert len(entry["description"]) <= _ASC_DESCRIPTION_MAX, (
            f"{pid}: description is {len(entry['description'])} chars, ASC allows "
            f"{_ASC_DESCRIPTION_MAX} — it cannot be entered verbatim"
        )


def test_no_pack_undercuts_the_cheapest_subscription_per_credit():
    """The rule migration 117 stated in a comment and nothing enforced: a top-up must never
    be better value per credit than a plan, or the subscription is dominated by a one-off.

    The ceiling is DERIVED from the plan seed, not hardcoded — the binding constraint is the
    most expensive paid plan per credit (Pro at $0.01249; Max at $0.00999 is looser), so a
    future subscription reprice re-arms this automatically.
    """
    _, plans = _effective_seed("plan_credits")
    paid = {
        t: r for t, r in plans.items()
        if int(r.get("price_cents") or 0) > 0 and int(r.get("monthly_credits") or 0) > 0
    }
    # Anti-vacuity: without this, dropping a plan from the seed LOWERS the ceiling and makes
    # the invariant trivially satisfiable — a derived bound going quietly vacuous.
    assert {"pro", "premium"} <= set(paid), f"plan seed lost a paid tier: {sorted(paid)}"

    ceiling = max(Fraction(r["price_cents"], r["monthly_credits"]) for r in paid.values())
    assert ceiling > 0
    for pid, row in _pack_seed().items():
        rate = Fraction(row["price_cents"], row["credits"])
        assert rate > ceiling, (
            f"{pid} is ${float(rate):.6f}/credit, at or below the subscription rate "
            f"${float(ceiling):.6f} — a top-up would undercut a plan"
        )


def test_the_pack_ladder_is_strictly_monotonic():
    """A dearer pack must be BETTER per credit, never worse.

    This is what rejected keeping Mega at Pro's 1,200 credits when it was repriced to
    $24.99: that is $0.020825/credit, 4% worse than Power, so the biggest pack would have
    become the worst value and penalised the user who spends most. Mechanical, not an
    argument in a comment.
    """
    rows = sorted(_pack_seed().values(), key=lambda r: r["price_cents"])
    rates = [Fraction(r["price_cents"], r["credits"]) for r in rows]
    assert len(set(rates)) == len(rates), f"two packs share a $/credit rate: {rates}"
    assert rates == sorted(rates, reverse=True), (
        "the ladder inverts — a more expensive pack is worse per credit: "
        + ", ".join(f"{r['display_name']} ${float(x):.6f}" for r, x in zip(rows, rates))
    )
    # sort_order must agree with price, or the storefront lists them out of ladder order.
    assert [r["sort_order"] for r in rows] == list(range(1, len(rows) + 1))


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
    seed = _read(_MIGRATION_CREDIT_PURCHASES_DDL)
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


# ---------------------------------------------------------------------------
# The App Review notes must not contradict the Terms on credit expiry.
#
# app-privacy-answers.md §7 is pasted VERBATIM into App Review Information, and it said
# credits "expire per the Terms" while terms.html §5, all four ASC pack descriptions
# ("Never expire."), three iOS surfaces and the entire two-pool design (migrations 117/118)
# say purchased credits do NOT expire. That is a written contradiction on a payments
# guideline, handed to a reviewer, in the one document no other test read.
#
# Guideline 3.1.1 is the reason the two-pool design exists at all: consumables bought with
# real money must not be revoked by the monthly reset, which is why the three tier RPCs are
# forbidden from touching `purchased_total`.
# ---------------------------------------------------------------------------
_TERMS_HTML = _REPO / "documents/legal/terms.html"


def _answers_review_notes() -> str:
    """§7 only — the blockquote that actually gets pasted into ASC."""
    text = _ANSWERS.read_text(encoding="utf-8")
    start = text.index("## 7.")
    return text[start:]


def test_the_review_notes_do_not_claim_purchased_credits_expire():
    notes = _answers_review_notes()
    assert "expire per the Terms" not in notes, (
        "app-privacy-answers.md §7 tells the reviewer credits 'expire per the Terms', but "
        "terms.html §5 says credit packs 'do not expire' and every ASC pack description "
        "reads 'Never expire.' This text is pasted verbatim into App Review Information."
    )
    assert re.search(r"do not expire|never expire", notes, re.I), (
        "§7 must state affirmatively that purchased credits do not expire — Guideline 3.1.1 "
        "is the whole reason for the separate purchased pool (migrations 117/118)."
    )


def test_the_terms_still_say_credit_packs_do_not_expire():
    """The other half of the pair. If the Terms are ever softened, the review notes above
    become the lie instead — so pin both, or the guard only points one way."""
    terms = re.sub(r"<[^>]+>", " ", _TERMS_HTML.read_text(encoding="utf-8"))
    terms = re.sub(r"\s+", " ", terms)
    assert re.search(r"credit pack[^.]*do not expire", terms, re.I), (
        "terms.html no longer states that one-time credit packs do not expire; the App "
        "Review notes and the four ASC pack descriptions both promise exactly that."
    )
