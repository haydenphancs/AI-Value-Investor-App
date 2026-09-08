# App Store Connect — IAP metadata to fill in

Everything here is **App Store Connect web-UI work**. None of it can be done from code or from a
build; it is why a product with perfect code can still be un-submittable.

> ⚠️ **THE VALUES BELOW ARE COPIED FROM [`frontend/ios/Caydex.storekit`](../../frontend/ios/Caydex.storekit),
> WHICH IS THE SOURCE OF TRUTH — not from a migration, and not from memory.**
>
> That file is what Xcode uses to simulate purchases, and
> `backend/tests/test_iap_product_and_privacy_parity.py` pins it against the *effective*
> `credit_packs` seed, checks every string fits ASC's fields, and enforces the per-credit
> ladder. `backend/tests/test_asc_metadata_doc_parity.py` now pins THIS PAGE against it too,
> because the first draft of this file was written from **migration 117** and was wrong on all
> four packs — 117 was superseded by **138** and then **141**, so it advertised 90 credits at
> $1.99 where the product actually sells 130 at $2.99. Typing that into ASC charges the user
> one thing and grants another, which `LAUNCH_CHECKLIST.md` §775 calls out by name.
>
> If you change a price or a credit count: edit the migration **and** `Caydex.storekit`, run the
> two tests, then re-read this page.

The one **hard blocker** is §1: a subscription group with `localizations: []` cannot be
submitted, and Apple's error does not name the group.

---

## 1. 🔴 Subscription group display name — THE BLOCKER

**App Store Connect → Caydex → Monetization → Subscriptions → tap the subscription GROUP
(not a subscription inside it) → Localizations → `+` → English (U.S.)**

| Field | Value |
|---|---|
| Subscription Group Display Name | `Caydex Membership` |
| App Name (optional override) | *leave blank — it inherits "Caydex"* |

`Caydex.storekit` already carries this group name, and still shows `"localizations": []` on the
group — the same gap, in the local config.

It goes on the GROUP. Localizing the two subscriptions in §2 does **not** satisfy it. This is
what iOS renders in Settings → Apple ID → Subscriptions, and in the sheet when someone switches
between Pro and Max.

---

## 2. The two subscriptions

Both monthly, both inside that group. A subscription with no price cannot be submitted, so set
pricing first if it is blank.

| Product ID | Reference Name (internal) | Display Name | Description | Price |
|---|---|---|---|---|
| `com.phan.caydex.pro.monthly` | `Caydex Pro Monthly` | `Pro`<br>*3/30* | `1,200 credits a month for reports and chat.`<br>*43/45* | $14.99 |
| `com.phan.caydex.max.monthly` | `Caydex Max Monthly` | `Max`<br>*3/30* | `4,000 credits a month for reports and chat.`<br>*43/45* | $39.99 |

Every number is load-bearing: 1,200 and 4,000 are `plan_credits` (migration 100), and
`REPORT_CREDIT_COST` is 20 (`config.py:219`). If any of those move, this copy is wrong and the
paywall will disagree with the store.

Each subscription also needs a **review screenshot** —
`documents/legal/screenshots/6.9/05-add-credits.png` covers both.

---

## 3. The four credit packs

Type **Consumable**. No group, so §1 does not apply to these.

| Product ID | Reference Name (internal) | Display Name | Description | Price |
|---|---|---|---|---|
| `com.phan.caydex.credits.starter` | `Caydex Credits Starter (130)` | `Starter`<br>*7/30* | `130 credits. Never expire.`<br>*26/45* | $2.99 |
| `com.phan.caydex.credits.plus` | `Caydex Credits Plus (280)` | `Plus`<br>*4/30* | `280 credits. Never expire.`<br>*26/45* | $5.99 |
| `com.phan.caydex.credits.power` | `Caydex Credits Power (650)` | `Power`<br>*5/30* | `650 credits. Never expire.`<br>*26/45* | $12.99 |
| `com.phan.caydex.credits.mega` | `Caydex Credits Mega (1300)` | `Mega`<br>*4/30* | `1,300 credits. Never expire.`<br>*28/45* | $24.99 |

Credits and prices are migration **141** (`117` → `138` → `141`; only the last one is live).

> ⚠️ **"Never expire" must stay in every description.** It is not marketing — it is the
> Guideline 3.1.1 claim the two-pool design exists to honour (purchased credits live in
> `user_credits.purchased_total`, which `ensure_credit_period` never resets), and
> `PaywallView.swift:351` tells the user the same thing. If ASC and the app disagree here, a
> reviewer reads it as a consumable that expires — the one thing 3.1.1 forbids.

**Review notes for the consumables** (paste into each one's Review Notes, which is not
length-capped like the description):

> Credits are consumed inside the app: an AI stock report costs 20 credits, a Cay AI chat costs
> 1. Purchased credits never expire — they are held in a separate balance from the monthly
> subscription allowance, which does reset monthly. To test: sign in with the review account
> provided, open any stock, and tap "AI Report".

Attach `documents/legal/screenshots/6.9/05-add-credits.png` to each.

---

## 4. Everything else on the ASC checklist

Copy for these lives in [`app-store-listing.md`](app-store-listing.md); this is the field list.

- [ ] Name / subtitle / keywords / promotional text / description
- [ ] **Availability: United States only**
- [ ] Privacy Policy `https://caydexinvest.com/privacy` · Support `https://caydexinvest.com/support`
- [ ] App Privacy questionnaire — the **ten** types from
      [`app-privacy-answers.md`](app-privacy-answers.md) §3, Tracking = **No** on all ten
- [ ] Category **Finance** · Age rating **17+**
- [ ] Copyright `2026 Duc Hai Phan` · App Review contact details
- [ ] **App Information → Content Rights → "Yes… and I have the necessary rights"**
- [ ] 🔴 **App Review Information → Attachment → the signed FMP Order Form PDF**, named in
      Review Notes. Guideline 5.2.2 lets a reviewer demand it; pre-empting saves a cycle.
- [ ] **Sign-In Required → the demo account.** Seed its credits first with
      [`backend/scripts/sql/seed_demo_account_credits.sql`](../../backend/scripts/sql/seed_demo_account_credits.sql).
      If that account holds a hand-set paid tier, add to Review Notes: *"The review account has
      been granted Pro-tier access and a credit balance so every feature is reachable without a
      purchase. In-app purchases remain fully testable: the Max subscription and all four credit
      packs are available from the paywall."*
- [ ] App Store Server Notifications, **both** Production and Sandbox →
      `https://caydexinvest.com/api/v1/billing/app-store-notifications`
- [ ] Create a **Sandbox tester** (Users and Access → Sandbox → Testers)
- [ ] On a first submission the IAPs are submitted **with** the app version — select them in the
      version page's In-App Purchases section, or the app ships with no store.

---

## 5. Verifying ASC automatically

[`backend/scripts/asc_audit.py`](../../backend/scripts/asc_audit.py) reads the LIVE App Store
Connect configuration and diffs it against `Caydex.storekit` — every product id, reference name,
display name, description, and the group localization that blocks submission. Read-only; it
issues only GETs.

```bash
export ASC_KEY_ID=XXXXXXXXXX
export ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export ASC_PRIVATE_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8
cd backend && ./venv/bin/python scripts/asc_audit.py
```

Getting the key: **Users and Access → Integrations → App Store Connect API → Team Keys → +**.
Role **App Manager** is the minimum that can read in-app purchases (Developer cannot). Apple
lets you download the `.p8` exactly once — keep it outside the repo.

This closes the last link: the migration is pinned to `credit_packs`, `credit_packs` is pinned
to `Caydex.storekit`, `Caydex.storekit` is pinned to this page — and the script pins all of it
to what Apple actually holds.

---

## 6. Two silent failure modes worth a second look

- A product id in ASC that does not match `credit_packs.product_id` **exactly** means StoreKit
  returns no product and the pack simply does not appear on the Buy Credits screen. No error,
  just a missing row.
- `credit_packs.credits` is what the BACKEND grants; the ASC display name is what the user was
  promised. If they disagree the user is short-changed and the only trace is a support email.
