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

- [ ] **105_password_changed_at.sql** — adds one nullable column to `public.users`. Needed
      for password reset to actually evict a thief's session: the app mints its own JWTs, so
      without this a reset leaves stolen tokens valid for up to 7 days. Safe to apply any
      time; NULL means "no restriction", so existing sessions are unaffected

- [ ] **106_guest_report_budget.sql** — new table + `claim_guest_report` /
      `release_guest_report` RPCs. This is what stops signed-out users generating
      **unlimited** free AI reports (~17 Gemini + ~20 FMP calls each). Reviewed by the
      migration reviewer: idempotent, RLS + service-role-only, REVOKE-before-GRANT.
      **Safe to apply any time, and safe NOT to apply yet** — until it exists the RPC
      call fails and the endpoint deliberately fails OPEN, with the per-install rate
      limit (3/min) and the 409 admission cap still enforcing a ceiling. Applying it
      turns on the monthly allowance (`GUEST_REPORT_MONTHLY_LIMIT`, default **1**)

- [ ] **107_analytics_events.sql** — the product-analytics table. Same fail-open shape
      as 106: until it exists the ingest endpoint returns 200 and logs a warning, so
      nothing breaks — but **you record no analytics at all**, which is the whole point
      of it. Apply this one before launch or you'll be flying blind on day 1

- [ ] **108_watchlist_guest_partition.sql** — 🔴 **MUST be applied in the SAME release as
      the code**, not before or after. It drops two `ON DELETE CASCADE` foreign keys
      (`watchlist_items`, `portfolios`) so signed-out users stop sharing ONE watchlist
      between them. Account deletion relied on those cascades, so `users.py` now purges
      both tables explicitly — apply the migration without that code and deleting an
      account silently leaves the user's watchlist and portfolios behind.
      Two consequences to expect rather than debug: every existing guest install's
      watchlist renders **empty** after deploy (4 pre-launch test rows become
      unreachable — that shared list was wrong data anyway), and it is **not
      practically reversible** once guest rows exist.

- [ ] **109_push_send_log.sql** — the delivery-dedup claim table. Apply this **before**
      the APNs key starts producing real sends, not after: without it there is nothing
      stopping the same alert going out twice to the same person (a sweeper re-trip, a
      retry, or two overlapping Railway instances during a deploy). A duplicate push
      can't be taken back, so this one is worth applying early even though push is
      otherwise inert. Additive and non-breaking.

No rush window on 103: the iOS app decodes old labels, new labels, and backend keys, so it's
correct either way. 105 is additive and non-breaking, but password recovery is only fully
effective once it's applied.

**Tune the guest allowance after launch.** `GUEST_REPORT_MONTHLY_LIMIT = 1` in
`backend/app/config.py` is a deliberate starting point: it delivers the "wow" report
without an account while making sign-up a strict upgrade (2/month + chat + saved
reports + watchlist). It was previously unlimited, which made signing in a *downgrade*.
Raise it if signups look too gated; set it to 0 to require sign-in for any report.

---

## 5b. Supabase dashboard setup 🔴 REQUIRED — nothing here is code

Six settings, all in the Supabase dashboard for project `gutlnhsjxrkxvrbqbbqq`. Auth is now
built against these, so the app's sign-in flows do not work until they're done. Roughly
20 minutes total, plus a Google Cloud detour.

### (a) Put the reset CODE in the password-reset email

**Authentication → Emails → Reset Password**

Password recovery asks the user for a **6-digit code**. Supabase — not your backend — sends
that email, from a template you control. The default template contains only
`{{ .ConfirmationURL }}`, which renders a *link* and no number. So today a user would get an
email with a link, while the app asks for a code that isn't in it. Dead end, and nothing in
the app can detect or work around it.

- [ ] Edit the template body to include `{{ .Token }}`:

```
Hi,

Use this code to reset your Caydex password:

{{ .Token }}

Enter it in the app along with your new password. The code expires shortly.
If you didn't request this, you can ignore this email.
```

### (b) Turn ON email confirmation

**Authentication → Sign In / Providers → Email → "Confirm email"**

- [ ] Enable it

This is what makes the chosen policy (option A) real. With it OFF, `/register` still returns a
usable session and the backend says so honestly via `confirmation_required: false` — but
anyone can then hold an account on an address they don't own. With it ON, signup returns no
tokens and the user must confirm first.

While you're on that screen, check the **Confirm signup** template is sensible — that's the
email new users get, and the app's "Resend confirmation email" button re-sends exactly it.

### (c) Configure real SMTP

**Project Settings → Authentication → SMTP Settings**

- [ ] Point it at a real provider

Supabase's built-in mailer is a *shared* development convenience, rate-limited to a handful
of messages per hour on the free tier. Two consequences if you leave it: a burst of signups
or resets silently stops sending, and mail from a shared Supabase sender is much more likely
to land in spam — which, for a confirmation-gated signup, means users simply can't get in.

Resend, Postmark, and Amazon SES all have usable free tiers. You'll add DNS records for
SPF/DKIM on `caydexinvest.com` as part of setup; that's what keeps you out of spam.

- [ ] Also raise **Authentication → Rate limits** for email sends once real SMTP is in

### (d) Enable Sign in with Apple

**Authentication → Sign In / Providers → Apple**

- [ ] Enable the provider
- [ ] Add `com.phan.caydex` to the authorized client IDs

For a native iOS-only Sign in with Apple, the bundle ID is the audience — you do **not** need
a Services ID or a private key, which is the setup most guides describe (that's for web).

Also, on the Apple side:

- [ ] Apple Developer portal → Identifiers → `com.phan.caydex` → enable the **Sign in with
      Apple** capability. The entitlement is already in the project; this is the matching
      server-side switch. Without it, the button errors at runtime.

### (e) Enable Google

**Authentication → Sign In / Providers → Google**

First, in **Google Cloud Console**:

- [ ] Create a project (or reuse one) → APIs & Services → Credentials
- [ ] Create an **OAuth client ID** of type **Web application** (yes, Web — the flow goes
      through Supabase, not the device)
- [ ] Add this to its Authorized redirect URIs:
      `https://gutlnhsjxrkxvrbqbbqq.supabase.co/auth/v1/callback`
- [ ] Complete the OAuth consent screen (app name, support email, logo)

Then back in Supabase:

- [ ] Paste the Client ID and Client Secret into the Google provider, and enable it

### (f) Allow the app's callback URL

**Authentication → URL Configuration → Redirect URLs**

- [ ] Add `caydex://auth-callback`

This is the custom scheme the app listens on after Google consent. It's already registered in
`Info.plist`; Supabase refuses to redirect to URLs that aren't on this allow-list, so without
it Google sign-in ends on an error page.

### How to check it all works

Once (a)–(f) are done, from a simulator or device:

1. **Sign up** with a real address → you should get no session and a "Confirm your email"
   screen. Check the inbox, click the link, then sign in.
2. **Sign in before confirming** → a specific "Please confirm your email address first"
   message, not "incorrect password".
3. **Forgot password** → the email must contain a 6-digit number. If it contains only a link,
   step (a) isn't saved.
4. **Continue with Apple** → native sheet, then straight into the app (no confirmation step —
   Apple's address is already verified).
5. **Continue with Google** → web sheet, then straight into the app.

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

## 6b. In-app purchase setup (Phase 8) 🔴 REQUIRED before you can charge

The code is done and tested against a local StoreKit configuration. These six items make it
work against real Apple infrastructure.

### App Store Connect

- [ ] Create two **auto-renewable subscriptions** in a subscription group. The product IDs
      must match exactly, or a real purchase verifies and then fails to map to a plan:
      - `com.phan.caydex.pro.monthly` — $14.99/month
      - `com.phan.caydex.max.monthly` — $39.99/month
- [ ] Fill in the localised display name, description, and a review screenshot for each
      (Apple rejects subscriptions with incomplete metadata)
- [ ] Note the app's numeric **Apple ID** (App Information) → set `IAP_APP_APPLE_ID`
- [ ] Create a **Sandbox tester** account (Users and Access → Sandbox)

### Apple root certificates

- [ ] Download Apple's public root CAs from https://www.apple.com/certificateauthority/
      (you want **AppleRootCA-G3.cer**) and place them in `backend/certs/apple/`
- [ ] Deploy them with the app on Railway

Verification **fails closed** without these: with `IAP_ENVIRONMENT=Sandbox` or `Production`
and no certificates, the endpoint returns 503 rather than accepting anything. That is
deliberate — no trust anchor must never silently mean "trust everything" on a payment path.

### Server notifications

- [ ] App Store Connect → App Information → **App Store Server Notifications**, set the
      Production and Sandbox URLs to:
      `https://<your-railway-host>/api/v1/billing/app-store-notifications`

Without this, cancellations, refunds, and failed renewals never reach you and a lapsed
subscriber keeps their paid tier forever — the client only ever reports *purchases*.

### Railway environment

- [ ] `IAP_ENVIRONMENT=Sandbox` while testing, then `Production` at launch
- [ ] `IAP_APP_APPLE_ID=<numeric app id>`
- [ ] `IAP_ROOT_CERT_DIR=certs/apple` (default; set it if you put them elsewhere)

### Testing it locally, before App Store Connect exists

A StoreKit configuration file (`frontend/ios/Caydex.storekit`) defines both subscriptions
locally, and the Debug scheme now points at it. So the full purchase flow — sheet, receipt,
backend verification, credits — works on the simulator with **no App Store Connect products
and no sandbox account**.

One catch: **it only applies when you launch from Xcode (⌘R).** `simctl launch` doesn't read
the scheme, and `xcodebuild` has no run action, so I could not exercise the purchase flow
from the command line. Everything below is compile-verified and unit-tested; the purchase
sheet itself needs your ⌘R.

- [ ] Open the project in Xcode, ⌘R, open the paywall, tap **Choose Pro**
- [ ] Confirm the sheet appears, the purchase completes, and credits update to 1200
- [ ] Set `IAP_ENVIRONMENT=Xcode` on your local backend (`LocalTesting` also works) — with
      `Production`/`Sandbox` the backend correctly refuses to verify an Xcode-signed receipt
- [ ] In Xcode's **Debug → StoreKit** menu you can also force failures, Ask to Buy, refunds,
      and renewals — worth walking through the refund case, since that's what the webhook
      handles

### The test matrix worth actually running

Once sandbox is live, on a real device with a sandbox tester signed in:

1. Buy Pro → tier becomes `pro`, credits become 1200
2. Upgrade to Max → tier becomes `premium`, credits 4000
3. Cancel → access persists until `current_period_end`, then drops to free
4. Request a refund → the REFUND notification should drop the tier immediately
5. Restore Purchases on a second device → tier restored, credits **not** double-granted
6. Kill and relaunch the app repeatedly → `Transaction.updates` replays, and credits must
   NOT increase (this is the one users would farm if it were wrong)

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

- **APNs** — ✅ key created (`7YPQRK276L`) and the five `APNS_*` variables are set on
  Railway. The code path is built: the insight sweeper alerts watchers of a ticker that
  moved materially, deduped per user per ticker per trading day.
  Still outstanding:
  - [ ] **Push Notifications capability** on `com.phan.caydex` in the developer portal.
        Without it the app can't register and no device token is ever issued
  - [ ] A **real iPhone** for the first end-to-end delivery — push does not work in the
        Simulator, so everything to date is unit-tested against a stub
  - [ ] Flip `APNS_ENV` from `sandbox` to `production` at launch. A device token is only
        valid in the environment that issued it, and Debug/Release builds already
        declare different `aps-environment` values
  - [ ] Keep a copy of the `.p8` in your password manager — Apple allows exactly one
        download, and the file currently exists only at `BIGDATA/myApp/`
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
