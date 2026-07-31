# Caydex — out-of-band launch checklist

Everything only you can do. Code-side work is tracked separately in the plan file
(`~/.claude/plans/clever-baking-eclipse.md`).

Ordered by **when to start**, not by importance.

**Nothing here is on a multi-week critical path.** An earlier version of this file claimed
LLC formation was a launch blocker; §1 records why that was wrong. You can ship on your
existing Individual Apple account, so the whole list is workable in days rather than weeks.

---

## 1. Entity formation — NOT REQUIRED to publish ⚠️ CORRECTED

**An earlier version of this checklist said an LLC was a launch blocker on the critical
path. That was wrong.** Recording the correction and the evidence, because it changes the
timeline substantially.

**You can ship on your existing Individual account.**

Why the original claim was wrong:

1. **Apple's enrolment requirements contain no app-type restrictions.**
   `developer.apple.com/help/account/membership/program-enrollment/` lists what Individual
   vs Organization enrolment needs, and there is no mention of regulated industries,
   finance, or restrictions on what an individual account may publish. The distinction is
   seller name + legal-entity status. Nothing gates a finance app to an org account.
2. **5.1.1(ix) lives in the App Review Guidelines, not the enrolment rules.** It is a
   review-time judgement, not an enrolment gate. Conflating the two was the core error.
3. **The clause is narrower than it looks.** It reads: apps that "provide services in
   highly regulated fields … should be submitted by a legal entity **that provides the
   services**." That targets front-ends for regulated providers — a bank, a broker, a
   crypto exchange. The same reading holds for health apps: the trigger is providing
   medical advice/services, not publishing information about medicine. Caydex provides no
   regulated service, holds no funds, connects to no brokerage, executes no trades.
4. **Empirically, finance-category research and tracker apps ship under individual
   accounts.** Live examples with a personal name as seller (the signature of an Individual
   account): *Portfolio Tracker: Finance Bay* (Arkadiusz Szczepkowicz), *Portfolio X –
   Stock Tracker* (Popa Alexandru), *Portfolio – Monitor Stocks* (Raphael Odermatt).
5. **The forum rejection that drove the original claim was a different kind of app.** That
   developer was subject to SEBI/AMFI rules in India — mutual-fund *distribution*, an
   actually regulated financial service.

**Trade-off of staying Individual:** your personal legal name appears as the App Store
seller instead of "Caydex". Cosmetic.

### The one residual review risk

Caydex emits its **own** `Strong Buy`/`Strong Sell` rating on named securities via the
Technical Meter (`schemas/technical_analysis.py`). Pure trackers and news apps do not do
that, and it is the feature most likely to read as advice rather than information. If a
5.1.1(ix) or 3.1.5-adjacent rejection ever arrives, that is the likeliest hook — and
softening or removing that meter is far cheaper than forming a company. It is already
disclaimer-covered.

### Why an LLC may still be worth doing — on your own timeline

Not for Apple. For **liability**: you are about to take recurring payments from strangers
for financial-adjacent content, and as an individual your personal assets are exposed if
someone claims they relied on the app and lost money. That is a real business decision, not
a launch blocker, and it is worth 30 minutes with a CPA or attorney. Not legal or tax
advice.

If you do decide to convert later, it is straightforward and non-destructive: Apple's
migration path keeps the **Apple ID, Team ID (`WG697LVCS9`), certificates, and existing
apps** intact — only the seller name changes. Sequence: register the LLC → EIN (IRS, free,
~10 min) → D-U-N-S (free via Apple's D&B lookup; the long pole) → wait ~2 business days →
submit `developer.apple.com/contact/request/migrate-individual-account`. Roughly 2–5 weeks,
mostly waiting on D-U-N-S. Apple requires the D-U-N-S registered to the legal entity — no
DBAs or trade names.


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

No longer blocked — your existing Individual account can create this record (see §1).

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
3. **Whether an LLC is worth forming** — a liability question, not an Apple one (§1).
   Worth a CPA/attorney conversation on your own timeline, independent of launch
