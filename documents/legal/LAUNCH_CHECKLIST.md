# Caydex — out-of-band launch checklist

Everything only you can do. Code-side work is tracked separately in the plan file
(`~/.claude/plans/clever-baking-eclipse.md`).

Ordered by **when to start**, not by importance.

> **Verified against the live system 2026-08-05.** §5 previously listed six migrations as
> pending that were all already applied — checked this time against
> `backend/database/schema_snapshot.sql` (a dump of the live Supabase schema) rather than
> against this file's memory. §5b(b) was upgraded from a convention to a blocker, and §6b
> gained two notes. If you are reading this much later, re-verify rather than trust: the
> failure mode of this document is confidently-stated staleness, and unticked boxes that are
> secretly done cost as much time as done boxes that are secretly undone.
>
> Findings behind the changes, including 11 confirmed defects still open, are in
> `~/.claude/plans/everything-is-pushed-and-twinkly-jellyfish.md`.

**Nothing here is on a multi-week critical path.** An earlier version of this file claimed
LLC formation was a launch blocker; §1 records why that was wrong. You can ship on your
existing Individual Apple account, so the whole list is workable in days rather than weeks.

---

## 👉 START HERE — next actions, as of 2026-08-06

This file is long and most of it is already done. If you only do one thing, do #1.

| # | Do this | Why now | Where |
|---|---|---|---|
| **1** | **Point `caydexinvest.com` at Railway, and add the SMTP provider's SPF/DKIM records in the same sitting** | 🔴 Email signup is a DEAD END today — Confirm email is ON but no mail is delivered. One DNS session also unblocks the legal pages, both App Store Connect URLs, and the passkey AASA fetch. | §5c + §3 |
| **2** | Publish the Google OAuth consent screen (*Testing* → *In production*) | Google sign-in currently works **only for allow-listed accounts**, and refresh tokens expire after 7 days. Looks exactly like an app bug, and only after release. | §5e |
| **3** | Purge the two copyrighted PDFs from git history | Public repo. Independent of everything else — can be done any time. | §4 |
| **4** | Create the App Store Connect record + IAP products, set the Server Notifications URL | Needed before you can charge. The notifications URL is what stops a cancelled subscriber keeping their tier. | §6b, §7 |
| **5** | Ask FMP and CoinGecko about commercial redistribution | Free to ask, slow to answer — start the clock early. | §6 |

**Already done, stop re-reading these:** migrations 104–113 (§5), Supabase Apple + Google
providers and redirect URL (§5b d/e/f), the Apple capabilities — Sign in with Apple,
Associated Domains, Push Notifications (§5b, §9), the native Google SDK and its iOS OAuth
client (§5e), iPhone-only device support (§8).

**Meanwhile:** use **Google sign-in** to test the app. It bypasses email confirmation entirely
(Google addresses arrive pre-verified), so #1 does not block anything except email/password
signup.

Code-side defects found in the 2026-08-05 audit — all five P0s fixed, 11 lower-priority ones
still open — are tracked separately in
`~/.claude/plans/everything-is-pushed-and-twinkly-jellyfish.md`.

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

- [x] `support@caydexinvest.com` — in Terms §16 and Privacy §13, and used as the ASC
      Support URL contact. *(Cloudflare Email Routing → haydenphancs@gmail.com, 2026-08-06.
      Namecheap's forwarder stopped working when the nameservers moved; Cloudflare Routing
      replaced it and has better deliverability.)*
- [x] `copyright@caydexinvest.com` — the DMCA/copyright-complaints address in Terms §9.
      Legally you should monitor this. *(routed 2026-08-06)*
- [x] `privacy@caydexinvest.com` *(optional but conventional)* — if you'd rather route
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

## 5. Apply the pending migrations — ✅ 104–111 ALREADY APPLIED (corrected 2026-08-05)

**This section used to list six migrations as pending, with alarming consequences attached.
They were all already applied.** Corrected after checking every one against
`backend/database/schema_snapshot.sql` — a dump of the **live** Supabase schema — rather than
against this file's memory of what had been run.

| Migration | Evidence in the live schema | State |
|---|---|---|
| 104 news_articles retention | `expires_at` column present | ✅ applied |
| 105 `users.password_changed_at` | column present | ✅ applied |
| 106 `guest_report_budget` | table present | ✅ applied |
| 107 `analytics_events` | table present | ✅ applied |
| 108 watchlist + portfolios partition | `watchlist_items_user_id_fkey` / `portfolios_user_id_fkey` **absent** | ✅ applied |
| 109 `push_send_log` | table present | ✅ applied |
| 110 research_reports partition | `research_reports_user_id_fkey` **absent** | ✅ applied |
| 111 chat_sessions partition | `chat_sessions_user_id_fkey` **absent** | ✅ applied |

Only PK/UNIQUE constraints remain on those four tables; the `user_id` foreign keys are gone,
which is exactly what 108/110/111 do and cannot happen by accident.

**110 and 111 were never in this file at all** — it predates them. Both state in their headers
that deploying their code without the migration makes *every guest INSERT fail the FK check*,
and the backend is already deployed. Worth knowing they are fine rather than assuming.

### Still to apply

- [ ] **103_persona_style_names.sql** — the only one from the original list still unverified,
      because it is a data `UPDATE` on `agent_personas` rows and therefore invisible in a
      schema-only dump. Check it directly:
      ```sql
      select persona_key, display_name from agent_personas order by persona_key;
      ```
      If any row still shows a real investor's name, apply it — real names in the app are an
      App Store 5.2.1 risk. Each statement should report `UPDATE 1`; `UPDATE 0` means the key
      is missing and that persona silently kept its old name. **Do not replay 043 or 074
      afterwards** — both revert the names via `ON CONFLICT DO UPDATE`. No rush window: the
      iOS app decodes old labels, new labels, and backend keys.

- [ ] **112_grant_tier_upgrade.sql** — 🔴 **required before you charge anyone.** Fixes a
      mid-month upgrade delivering **zero credits**: `ensure_credit_period` only grants at the
      monthly boundary, so buying Pro on the 10th flipped `users.tier` and left the balance
      untouched — money taken, next tap returns 402, for up to four weeks. Adds a
      `grant_tier_upgrade` RPC that grants on the tier *transition*, idempotent by
      construction so `Transaction.updates` replays cannot farm credits, and never clawing
      back on a downgrade.
      Safe either order: without it the RPC 404s, the failure is logged, and credits land at
      the next monthly reset exactly as they do today.
      **One product decision is baked in** — `used` is preserved, so a Free user who spent 30
      credits and upgrades has 1170 available rather than 1200 ("Pro grants 1200 per period,
      you already used 30 of it"). Zeroing `used` is a one-word change documented in the
      migration header.

- [ ] **113_users_is_admin.sql** — 🔴 closes a privilege escalation. `admin.py` authorized on a
      hardcoded email allowlist, and an email claim is not a credential: Supabase auto-sets
      `email_confirmed_at` while "Confirm email" is off (§5b(b), still unticked), so
      registering `admin@caydexinvest.com` — an address nobody holds, since §2 is unticked
      too — yielded a real session and every admin route. Authorization now reads
      `users.is_admin`, which registration cannot set.
      Fails closed: until applied, admin routes answer 403. **After applying, verify the right
      row was flagged:**
      ```sql
      select id, email, is_admin, created_at from public.users where is_admin;
      ```
      If it flags a row you do not recognise, that address was already registered by someone
      else — clear it immediately and treat it as evidence the escalation was exercised.

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

### (b) Turn ON email confirmation 🔴 DO THIS FIRST — it is a security control, not a nicety

**Authentication → Sign In / Providers → Email → "Confirm email"**

- [ ] Enable it

This is what makes the chosen policy (option A) real. With it OFF, `/register` still returns a
usable session and the backend says so honestly via `confirmation_required: false` — but
anyone can then hold an account on an address they don't own. With it ON, signup returns no
tokens and the user must confirm first.

⚠️ **Upgraded from "conventional" to "blocker" on 2026-08-05.** The consequence is not just
account squatting. Until this is on, *any address is claimable by anyone*, and until migration
113 is applied, `admin.py` grants full admin to two specific addresses by email —
`admin@caydexinvest.com`, which nobody holds (§2 is also unticked). Registering it opened every
admin route: the expensive full-universe benchmark recomputes plus the internal status
endpoints. Backend code is not at fault — it correctly gates on `email_confirmed_at`
(`auth.py:291`); Supabase simply *populates* that field when this setting is off, and
`auth.py:300` says so in as many words.

Two independent fixes, and you want both: this setting, **and** migration 113, which moves
authorization onto `users.is_admin` so it stops depending on a checkbox in another product's
dashboard.

While you're on that screen, check the **Confirm signup** template is sensible — that's the
email new users get, and the app's "Resend confirmation email" button re-sends exactly it.

### (c) Configure real SMTP — 🔴 **BLOCKING SIGNUP RIGHT NOW** (as of 2026-08-06)

**Project Settings → Authentication → SMTP Settings**

> **This is no longer theoretical.** Confirm email was switched ON in (b) while the project is
> still on Supabase's built-in mailer. Observed 2026-08-06: registering
> `duchai6028@gmail.com` reached the app's "Confirm your email" screen — correct backend
> behaviour, `auth.py:291` — and **no email ever arrived**. Email/password signup is therefore
> a dead end today. Nothing in the app can detect or work around it.
>
> **Google sign-in is unaffected** and bypasses the gate entirely (Google's address arrives
> pre-verified), so it is the way to keep testing everything else meanwhile.
>
> Two ways out, pick one *now* rather than at submission:
>   1. Do this section — the real fix.
>   2. Turn **Confirm email** back OFF until SMTP is in, and treat "SMTP → then confirmation
>      ON" as a hard launch gate. Cost: anyone can hold an account on an address they do not
>      own. That is much less severe since migration 113 (holding an address no longer grants
>      admin — §5b(b)), but it must not ship that way.

- [ ] Point SMTP at a real provider
- [ ] Re-test: register a fresh address, confirm the email actually arrives
- [ ] Raise **Authentication → Rate limits** for email sends once real SMTP is in

Supabase's built-in mailer is a *shared* development convenience, rate-limited to a handful of
messages per hour on the free tier, and restricted in who it will deliver to at all. Two
consequences if you leave it: a burst of signups or resets silently stops sending, and mail
from a shared Supabase sender is much more likely to land in spam — which, for a
confirmation-gated signup, means users simply cannot get in.

Resend, Postmark, and Amazon SES all have usable free tiers.

**⚠️ Do this together with §3 (point `caydexinvest.com` at Railway) — they share a
prerequisite.** SMTP setup wants SPF/DKIM DNS records on `caydexinvest.com`, and that domain
is still a Namecheap parking page. So one DNS session unblocks four separate things:

| Needs the domain live | Checklist item |
|---|---|
| SPF/DKIM for SMTP → confirmation emails deliver | §5c (this section) |
| `https://caydexinvest.com/privacy` + `/terms` + `/support` | §3 |
| App Store Connect Privacy Policy URL + Support URL | §7 |
| Apple fetching `/.well-known/apple-app-site-association` (no redirects tolerated) → passkeys | §5b, passkey groundwork |

Doing them separately means touching Namecheap DNS three or four times and waiting for
propagation each time. Doing them together is one sitting.

- [ ] Point `caydexinvest.com` at Railway (also §3)
- [ ] Add the SMTP provider's SPF/DKIM records at the same time

### (d) Enable Sign in with Apple

**Authentication → Sign In / Providers → Apple**

- [x] Enable the provider
- [x] Add `com.phan.caydex` to the authorized client IDs

For a native iOS-only Sign in with Apple, the bundle ID is the audience — you do **not** need
a Services ID or a private key, which is the setup most guides describe (that's for web).

Also, on the Apple side:

- [x] Apple Developer portal → Identifiers → `com.phan.caydex` → enable the **Sign in with
      Apple** capability. *(confirmed done 2026-08-06)* The entitlement is already in the project; this is the matching
      server-side switch. Without it, the button errors at runtime.

### (e) Enable Google

**Authentication → Sign In / Providers → Google**

First, in **Google Cloud Console**:

- [x] Create a project (or reuse one) → APIs & Services → Credentials
- [x] Create an **OAuth client ID** of type **Web application** (yes, Web — the flow goes
      through Supabase, not the device)
- [x] Add this to its Authorized redirect URIs:
      `https://gutlnhsjxrkxvrbqbbqq.supabase.co/auth/v1/callback`
- [x] Complete the OAuth consent screen (app name, support email, logo)

Then back in Supabase:

- [x] Paste the Client ID and Client Secret into the Google provider, and enable it

- [ ] 🔴 **Publish the OAuth consent screen** (Google Cloud → OAuth consent screen →
      **Publish app**, moving it from *Testing* to *In production*). Free, and for the
      `email` / `profile` / `openid` scopes this app uses it needs no verification review.
      **Skip it and Google sign-in works for you and fails for everyone else**: Testing mode
      allows only individually allow-listed accounts (max 100) and expires refresh tokens
      after 7 days. It looks exactly like an app bug and only shows up after release.

#### 🔴 The web flow DEAD-ENDS on a QR code — the iOS client is not optional

Observed on a real device 2026-08-06: signing in with Google reaches *"Verifying it's you —
complete sign-in using your passkey"* and then presents **a QR code to scan with another
device**, on the phone that is displaying it. There is no way past it.

Cause is the **client type**, not a setting. Routing through Supabase's `/auth/v1/authorize`
makes the request on the **Web** client, and Google applies browser-grade risk checks to a Web
client opened inside a mobile web view. It reaches its credential step, finds no usable
credential — Google's passkeys live in Google Password Manager, which iOS cannot read unless
Chrome is the AutoFill provider, so iCloud Keychain has no `google.com` passkey to offer — and
the only WebAuthn transport left is **hybrid**, i.e. the QR. iOS cannot persistently link a
hybrid device either, so it is a fresh QR every time. `prompt=select_account` and a
non-ephemeral session were both tried; neither changes whether Google demands a credential.

The app code is already in place and falls back to the old web flow until all three steps
below are done, so nothing breaks in the meantime.

- [x] **Google Cloud → Credentials → Create OAuth client ID → iOS.** Bundle ID
      `com.phan.caydex`. *(done 2026-08-06 — client id `162115276254-…`)*
- [x] **Supabase → Google provider**: **both** client ids in the *Client IDs* field,
      comma-separated (web first, then iOS), and **Skip nonce check** ON. Required because the
      GoogleSignIn SDK does not expose the raw nonce Supabase would need to verify the
      pairing. Apple is unaffected and keeps its nonce binding.
- [x] **GoogleSignIn-iOS package added** via the Xcode UI — resolved to **9.2.0**, and the
      native branch compiles against it.
- [x] **`Info.plist`** — real `GIDClientID`, plus the reversed form registered as a **second**
      `CFBundleURLTypes` entry.
      ⚠️ **Add, don't replace.** The `caydex` scheme must survive: it is
      `APIConfig.oauthCallbackScheme`, where the web fallback redirects. It was briefly
      overwritten during setup, which silently removed the fallback. Verified afterwards with
      `simctl openurl` that iOS routes **both** schemes to the app, against a deliberately
      unregistered control scheme that correctly failed (`kLSApplicationNotFoundErr`) — a
      missing registration presents as the sheet completing and then *nothing happening*, not
      as an error.
- [x] **Verified by actually signing in 2026-08-06 — the QR dead-end is gone.** Native
      Google sheet, no passkey verification step, straight into the app.
      Root cause for the record: the first attempt used the **Web** client id, and Google
      rejects a custom-scheme redirect for a Web client (`Error 400: invalid_request —
      "Custom scheme URIs are not allowed for 'WEB' client type"`). Both client types share
      the project-number prefix, so they are indistinguishable by eye; check the **Type**
      column in the Credentials table, not the id itself.

Until `GIDClientID` is a real value AND the package is present, `SocialSignInService` takes the
old web path — deliberately, so a half-finished setup cannot ship a broken button.

**Cost: none.** The Cloud project, the OAuth client, the consent screen, and Supabase social
providers are all free; no billing account is needed. Google's paid verification (the
five-figure third-party security assessment) applies to *restricted* scopes such as Gmail or
Drive — not to basic sign-in. Supabase bills on monthly active users, not on how many
providers you enable, and a user signing in with Google instead of a password is the same
single MAU.

**Verify without opening the app** — probes Supabase directly and tells you which step is
missing (a disabled provider and a non-allow-listed redirect look identical from the app):

```bash
./backend/scripts/verify_social_signin.sh
```

### (f) Allow the app's callback URL

**Authentication → URL Configuration → Redirect URLs**

- [x] Add `caydex://auth-callback`

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

Without this, cancellations, refunds, and failed renewals never reach you — the client only
ever reports *purchases*.

Still do it, but it is **no longer the single point of failure it was.** Until 2026-08-05
`reconcile_user_tier` ran only on a client verify or a notification, so one missed EXPIRED or
REFUND left the account paid *forever* — and because `ensure_credit_period` resolves the
monthly allocation from `users.tier`, a cancelled Max subscriber kept receiving 4000 fresh
credits on the 1st of every month, at real Gemini and FMP cost, with nothing in the product
able to notice. An hourly `subscription_expiry_sweep` now expires rows whose paid period
ended and re-reconciles those users, so entitlement self-corrects even with zero notifications
delivered. Grace windows are deliberately generous — 24h for `active`, 60 days for
`grace_period` / `billing_retry` (Apple's maximum retry window) — because demoting a paying
customer early is worse than carrying a lapsed one another day.

### Railway environment

- [ ] `IAP_ENVIRONMENT=Sandbox` while testing, then `Production` at launch
- [ ] `IAP_APP_APPLE_ID=<numeric app id>`
- [ ] `IAP_ROOT_CERT_DIR=certs/apple` (default; set it if you put them elsewhere)

> **App Review buys in SANDBOX against the build you submit for PRODUCTION.** Apple's
> `SignedDataVerifier` is built for exactly one environment and hard-rejects a payload whose
> `environment` field differs, so `IAP_ENVIRONMENT=Production` used to answer HTTP 400 to
> every purchase a reviewer made — a rejection for "in-app purchase doesn't work" that never
> reproduces for you, because your own sandbox testing runs against a Sandbox-configured
> server. Fixed 2026-08-05: the backend now builds verifiers for both real environments and
> retries the sibling **only** on `INVALID_ENVIRONMENT`, which Apple's library raises *after*
> the signature, trust chain and bundle id have already passed. A forged payload still fails
> identically everywhere, and the signature-skipping local environments (`Xcode`,
> `LocalTesting`) are never cross-accepted. Nothing for you to configure — noted so the
> behaviour isn't mistaken for a leak later.

> **The default changed (2026-08-01), and it matters.** `IAP_ENVIRONMENT` used to default to
> `LocalTesting`. In `LocalTesting`/`Xcode`, Apple's library **skips signature verification
> entirely** — so a deploy that simply forgot this variable would have accepted any forged,
> unsigned JWT as a real purchase and handed out Max tier + 4000 credits for free. It now
> defaults to **`Production`**, so an unconfigured deploy fails closed (503, "no root
> certificates") instead. Consequence for you: **local work must now set
> `IAP_ENVIRONMENT=Xcode` explicitly** — it is no longer the default. Guarded by
> `backend/tests/test_iap_environment_fails_closed.py`.

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
- [ ] Set `IAP_ENVIRONMENT=Xcode` on your local backend (`LocalTesting` also works) — **now
      required, not optional**, since the default is `Production` (see the box above). With
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
- [ ] IAP products: **do create them now** — see §6b. (This line used to say "not yet, wait for
      Phase 8"; Phase 8 is done and the StoreKit code ships, so §6b is the live instruction and
      this is no longer a reason to defer.)

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
  - [x] **Push Notifications capability** on `com.phan.caydex` in the developer portal.
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
