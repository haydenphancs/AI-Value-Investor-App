# Caydex — out-of-band launch checklist

Everything only you can do. Code-side work is tracked separately in the plan file
(`~/.claude/plans/clever-baking-eclipse.md`).

Ordered by **when to start**, not by importance. Item 1 is the critical path and gates
submission — start it before anything else, then work the rest while it runs.

---

## 1. Entity formation → Apple organization enrollment 🔴 CRITICAL PATH

**Why:** App Review guideline 5.1.1(ix) requires apps in "highly regulated fields (such as
banking and financial services…)" to be submitted by a legal entity, **not an individual
developer**. Developer-forum records show stock-research apps rejected on exactly this
clause, with Apple requiring the account be "enrolled in the Apple Developer Program as an
organization." Your account is enrolled as an individual (`com.phan.caydex`).

**Why it's first:** this is weeks of calendar time you cannot compress, and it gates
*submission* — not just review.

- [ ] Register an LLC (or equivalent) in your state
- [ ] Get an EIN from the IRS (free, online, usually same-day)
- [ ] Request a **D-U-N-S number** from Dun & Bradstreet (free via Apple's D-U-N-S lookup;
      allow 5–14 business days)
- [ ] Either enrol a new Apple Developer account as an organization, or contact Apple
      Developer Support to convert the existing individual account
- [ ] Confirm the legal entity name matches across LLC registration, D-U-N-S, and Apple

**Note:** you may be able to argue the exemption — Caydex is information/education, holds no
client funds, connects to no brokerage, executes no trades. That argument sometimes
succeeds. It is not reliable enough to bet a launch date on, which is why this is item 1.

---

## 2. Mailboxes

Three addresses are now promised in the legal documents and must actually receive mail.

- [ ] `support@caydexinvest.com` — in Terms §16 and Privacy §13, and used as the ASC
      Support URL contact
- [ ] `copyright@caydexinvest.com` — the DMCA/copyright-complaints address in Terms §9.
      Legally you should monitor this
- [ ] `privacy@caydexinvest.com` *(optional but conventional)* — if you'd rather route
      data-rights requests separately from general support, tell me and I'll update both
      policy surfaces

---

## 3. Host the legal documents

Both files are final and dated **July 29, 2026**.

- [ ] Host `documents/legal/privacy.html` at `https://caydexinvest.com/privacy`
- [ ] Host `documents/legal/terms.html` at `https://caydexinvest.com/terms`
- [ ] Stand up a **support page** at `https://caydexinvest.com/support` — App Store Connect
      requires a Support URL and yours is currently `mailto:`-only. A page with a contact
      form or just the support email plus a short FAQ is enough
- [ ] Verify all three load over HTTPS with no mixed content

The in-app native versions (`PrivacyPolicyView`, `TermsOfUseView`) mirror the hosted text.
If you edit one, edit both — I've kept them in parity.

---

## 4. Purge the copyrighted PDFs from git 🔴 DO THIS TODAY

Two complete copyrighted books are in your **public** repo history. Script is ready and
verified against your repo (0 forks, `git-filter-repo` installed).

- [ ] Flip the repo **private** (Settings → Danger Zone → Change visibility)
- [ ] Run the prepared script (it backs up to a bundle first and refuses to push if any
      PDF blob survives)
- [ ] Flip back to **public**
- [ ] Email GitHub Support to purge cached views — old blobs stay reachable by direct
      commit SHA until garbage collection
- [ ] Move the source PDFs outside the repo, or delete them

---

## 5. Apply the pending migrations

You apply these manually. Review first, as you prefer.

- [ ] **103_persona_style_names.sql** — renames the five personas from real people to style
      names. Each statement should report `UPDATE 1`; a `UPDATE 0` means the key is missing
      and that persona silently kept its old name. **Do not replay 043 or 074 afterwards** —
      both would revert names via `ON CONFLICT DO UPDATE`
- [ ] **104_news_articles_retention.sql** — ⚠️ **DESTRUCTIVE.** Deletes all rows from
      `news_articles` (upstream-derived third-party content; no user data, and nothing in
      the app reads it any more). Adds `expires_at` + a cleanup function so it can't
      re-accumulate. A one-line archive command is in the header if you'd rather keep the rows

No rush window on 103: the iOS app decodes old labels, new labels, and backend keys, so it's
correct either way.

---

## 6. Ask FMP and CoinGecko about commercial redistribution

FMP's terms indicate that **"displaying or redistributing data sourced from FMP requires a
specific Data Display and Licensing Agreement."** Caydex both displays FMP data to end users
in a paid product and persists it in Supabase cache tables (14 tables store raw upstream
`response_json`).

- [ ] Email FMP: does my current plan cover displaying this data to end users in a
      commercial mobile app, and is caching it server-side within terms?
- [ ] Same question to CoinGecko — you're on the free Demo plan, and free tiers commonly bar
      commercial redistribution

Free to ask, definitive answer, and it de-risks the single largest contractual unknown.
Get it in writing.

---

## 7. App Store Connect record

Blocked on item 1 (organization enrolment).

- [ ] Create the app record → note the numeric app ID (nothing in the app needs it today,
      but IAP setup does)
- [ ] **Availability: United States only.** This defers EU AI Act Article 50, GDPR, and the
      Article 27 EU-representative requirement entirely, at zero engineering cost
- [ ] Privacy Policy URL → `https://caydexinvest.com/privacy`
- [ ] Support URL → `https://caydexinvest.com/support`
- [ ] **App Privacy questionnaire** → read straight from
      `documents/legal/app-privacy-answers.md`. Eight data types, tracking = No
- [ ] Category: Finance. Age rating: 4+ (no gambling, no unrestricted web)
- [ ] **App Review notes** → the paragraph in §7 of `app-privacy-answers.md`. This is the
      single highest-value thing you'll paste; it heads off the fintech rejection
- [ ] Do **not** create IAP products yet — that waits on the StoreKit design in Phase 8

**Metadata rule:** no real investor names anywhere in the app name, subtitle, description,
keywords, or screenshots (Guideline 5.2.1). The personas are now style names — keep the
marketing copy consistent with that. Describing methodology in prose is fine
("a quality-and-moat approach in the tradition of Warren Buffett"); naming a feature after a
person is not.

---

## 8. Device support — DECIDED: iPhone-only ✅

`TARGETED_DEVICE_FAMILY = 1` in both configurations, and the dead `~ipad` orientation key
removed. Verified in the built `Info.plist`: `UIDeviceFamily => [1]`, portrait only.

Rationale: all 480+ view files are iPhone-designed, and a stretched iPhone layout on iPad is
a common rejection. The app still runs on iPad in compatibility mode. Reversible as a
one-line build setting if iPad is ever done properly.

**Consequence for App Store Connect:** the listing will show iPhone only, and you only need
**iPhone screenshots** (6.9" and 6.5" display sizes). No iPad screenshots required — that's
a meaningful saving in item 9.

---

## 9. Later phases (not yet)

- **APNs** (Phase 9): `.p8` key, Push capability on the App ID, `APNS_*` env on Railway.
  Bundle `com.phan.caydex`, team `WG697LVCS9`. The entitlements are now correctly wired
  per-configuration, so this will work when you get there
- **IAP products** (Phase 8): Pro $14.99, Max $39.99, plus a sandbox tester account
- **Screenshots** (Phase 10): I can capture these from the simulator when the UI is final

---

## Still needs a real lawyer

Three items I can prepare materials for but shouldn't be the final word on:

1. **Investment-adviser status** — the publisher's-exclusion analysis, particularly given
   the app emits its own Buy/Sell technical rating on named securities and computes a score
   from the user's own holdings
2. **Book study guides** — whether chapter-mapped guides to 10 in-copyright books are
   sufficiently transformative, even written in your own words
3. **Guideline 5.1.1(ix)** — whether Caydex counts as "providing services in a highly
   regulated field," which determines whether item 1 is strictly required or merely prudent
