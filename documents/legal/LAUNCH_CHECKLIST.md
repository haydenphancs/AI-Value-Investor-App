# Caydex — out-of-band launch checklist

Everything only you can do. Code-side work is tracked separately in the plan file
(`~/.claude/plans/clever-baking-eclipse.md`).

Ordered by **when to start**, not by importance.

> ## 🔎 Whole-file audit 2026-08-14 — read this before anything else
>
> Every section was re-checked against the repo and live HTTP (95 findings, 93 survived
> adversarial verification). **The file was missing four launch blockers entirely** and three
> of the five START HERE items were wrong about scope or state. Corrections are inline and
> dated; the summary:
>
> | | |
> |---|---|
> | **NEW blockers, were in no section** | Paid Applications Agreement/banking/W-9 (§6b) · four **consumable** credit packs — §6b listed only the 2 subscriptions (§6b) · `PRICE_ALERT_*` undeclared, price alerts dead in prod (**FIXED in code**) · `CFBundleDisplayName` missing, Home Screen read "ios" (**FIXED in code**) |
> | **Unticked but DONE** | §5c domain→Railway · "11 defects still open" (all fixed) · Time Sensitive entitlement |
> | **Asserted but WRONG** | §3 doc date · §3 "all four text/html" · §5 "043 reverts persona names" · §5 guest allowance · §7 "Nine data types" · §9 push rationale + `.p8` path · §7 "ASC record not created" |
> | **Stale command** | the `curl -sI` block below returns **405** on all four paths (HEAD is not routed; GET returns 200) |
>
> Still true and worth trusting: §5's migration tables, §1's residual-review-risk analysis,
> §3's hosted↔in-app legal parity, §5b (d)(e)(f), §8.
>
> **§5 re-verified against the LIVE DATABASE 2026-08-14 evening** (migrations 103–**136**, all
> applied, nothing pending — including 135 and 136, which landed that day).
>
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

## 🔴🔴 §0 — A LIVE PRODUCTION CREDENTIAL IS PUBLIC RIGHT NOW (added 2026-08-14 evening)

**This was in no section of this file, and it is the only item where an attacker's clock is
running.** Everything else on this list is a deadline you control.

The **current, unrotated** Supabase `service_role` JWT is in mainline git history at
`MarketPulse/MarketPulse/App/Config.swift`, mislabeled as a constant called `supabaseAnonKey`,
valid to 2035. Verified by hashing it against `backend/.env` — **byte-identical, never rotated** —
and an anonymous fetch at commit `dd81c7b0` returns **HTTP 200**. Re-verified still exposed and
still unrotated at 23:40 ET on 2026-08-14.

`service_role` bypasses **every** RLS policy: read/write/delete on `users`, `user_credits`,
`chat_sessions`, everything.

Why the usual sweeps missed it: a filename grep for `.p8` / `.pem` / `.key` comes back clean
because it lives in a `.swift` file, and the constant name reads like the anon key, which is
*meant* to be public. Only a content scan hashing history blobs against the live `.env` finds it.
Confirmed clean by that same scan: `SUPABASE_JWT_SECRET`, `SUPABASE_DB_PASSWORD`, `DATABASE_URL`,
`ADMIN_TOKEN`, and the FMP and Gemini keys appear in **zero** history blobs.

- [ ] 🔴 Supabase → Project Settings → API → **rotate the service role key**
- [ ] Update `backend/.env` **and** the Railway variable, redeploy, smoke-test one authed endpoint

⚠️ **Since the repo now stays public by decision (§4), rotation is the ONLY mitigation.** There is
no belt-and-braces here.

Also in history, lower severity: an OpenSSH **ed25519 private key** committed under the filename
`eval "$(ssh-agent -s)"` (commit `3f784c89`). Its fingerprint does **not** match the key on the
GitHub account, but confirm it is not a deploy key or on any server before dismissing it.

---

## 👉 START HERE — next actions, re-ordered 2026-08-14 (evening)

Ordered by **third-party lead time first**, because those are the only items you cannot
compress by working harder.

| # | Do this | Why now | Where |
|---|---|---|---|
| **0** | 🔴 **Rotate the leaked `service_role` key** | A live production credential that bypasses all RLS is anonymously downloadable today. The repo stays public, so rotation is the only fix. | §0 |
| **1** | **ASC → Business: Paid Applications Agreement, banking, W-9** | Gates *every* §6b item: until active, ASC returns **no products at all** and the paywall reads "not available on this device" — identical to a wrong product id, the most commonly misdiagnosed IAP failure. Bank verification takes days. | §6b |
| **2** | Ask **FMP and CoinGecko** about commercial redistribution | Free to send, slow to answer — and the answer feeds the ASC **Content Rights** declaration (§7), so it is on the submission path, not beside it. | §6 |
| **3** | **Create the App Review demo account** | The review notes you are told to paste promise credentials that do not exist. Live DB: 4 users, none of them a reviewer. Anonymous `GET /stocks/AAPL/report` → **401**, so a reviewer signed out cannot reach the headline feature. Cheapest self-inflicted rejection on the list. | §7 |
| **4** | **SMTP** (Resend/Postmark/SES) → Supabase | Email/password signup is a dead end: Confirm email is ON and the built-in mailer does not deliver. ⚠️ **MERGE** the provider's `include:` into the existing SPF; replacing it breaks §2 inbound mail. Note this is **no longer a submission blocker** — create the demo account in Supabase Studio with "Auto Confirm" and SMTP drops off the critical path. | §5c |
| **5** | Create **six** IAP products + subscription-group localization | Four **consumable** credit packs, not just the two subscriptions. Blocked on #1. | §6b |
| **6** | **Decide the real-investor-name question**, then shoot screenshots | Five user-visible surfaces put a living investor's name in a capturable frame. This gates screenshots, not the reverse. | §7 |
| **7** | Flag your own account `is_admin` | Matches **zero** rows, so every admin route 403s for every account including yours. One SQL `UPDATE` — and do it **before** clearing `ADMIN_TOKEN` or you lock yourself out. | §5 |
| **8** | Publish the Google OAuth consent screen | Works only for allow-listed accounts; refresh tokens die at 7 days. ⚠️ **Not a lead-time item** — non-sensitive scopes need no verification review, so it is ~60 seconds. It was ranked #4 in a lead-time-ordered table by mistake. | §5e |

**What changed on 2026-08-14 (evening).** A full re-verification against the **live** database,
live HTTP, the anonymous GitHub API and the Xcode archives on disk. The old #3 ("flip the repo
private") is **withdrawn — the repo stays public by decision**; see §4 for the two consequences.
Two genuine submission blockers were absent from this file entirely (the demo account, and the
ASC Content Rights / Copyright / reviewer-contact fields), and several ticked items turned out to
be either already done or never a problem — see the new **Corrections** section.

### ✅ Corrections — verified 2026-08-14 evening, stop spending time on these

Each was checked against the live system, not against this file's memory.

| Claim in this file | Reality |
|---|---|
| "Check the app icon for an **alpha channel** first" (§9), treated as a pre-screenshot gate | **Fixed 2026-06-06, twenty minutes after it broke.** The two archives are a complete forensic record: 12:23 upload fails 90717 → icon replaced 12:41 → 12:43 upload succeeds. Confirmed three ways — `sips` reports `hasAlpha:no`, the file is a baseline JPEG (which physically cannot carry alpha), and the built `Assets.car` rendition reports `Opaque: True`. **Zero work remains.** |
| "**47** remote `origin/claude/*` branches" (§4 + runbook) | **23.** `git branch -r` reads 47 because 24 tracking refs are stale. The runbook's own check (`git branch -r \| grep … # expect 0`) will still read 47 after a *perfect* cleanup. Use `git ls-remote --heads origin 'refs/heads/claude/*'`, and `git fetch --prune` first. |
| Export compliance — not mentioned anywhere | **Already handled.** `INFOPLIST_KEY_ITSAppUsesNonExemptEncryption = NO` in **both** build configs and baked into the built plist. ASC will not ask. |
| "The ASC record **probably** already exists" (§7) | **It does.** The 2026-06-06 archive records `uploadedBuildNumber="1", state="success"` for adamId **6759525689**. Do not create a second record — and note this makes the build-number bump a *hard* rejection rather than housekeeping. |
| Migrations "103–134" | **103 through 136 are all applied; nothing is pending.** 135 (signup credit seed) and 136 (journey-images bucket) landed 2026-08-14 and were verified against the live database. |
| App Store listing copy — §7 states a *constraint* on it (the no-real-names rule) but never assigns writing it | **Now written**: [`app-store-listing.md`](app-store-listing.md). Every field measured against Apple's limits — the description came in at 4,018 on the first pass and would have been silently truncated on paste; it is now 3,906 / 4,000. |

**Verified clean, do not re-audit:** in-app account deletion (5.1.1(v)) is implemented and
hardened; paywall 3.1.2 disclosure is complete in both `PaywallView` and `BuyCreditsView`; every
linked SPM package ships a privacy manifest; required-reason APIs are correctly declared;
`PrivacyInfo.xcprivacy` re-parsed and agrees with the answer sheet at exactly ten data types; §8
device support is fully accurate.

**Already done, stop re-reading these:** migrations 103–136 — ALL of them (§5, re-verified live 2026-08-14), Supabase Apple + Google
providers and redirect URL (§5b d/e/f), the Apple capabilities — Sign in with Apple,
Associated Domains, Push Notifications (§5b, §9), the native Google SDK and its iOS OAuth
client (§5e), iPhone-only device support (§8), and **as of 2026-08-06** the whole domain
sitting: `caydexinvest.com` live on Railway via Cloudflare, `/privacy` `/terms` `/support`
serving 200 text/html, the AASA returning `application/json` with **zero** redirects, and
`support@` / `copyright@` / `privacy@` receiving via Cloudflare Email Routing (§2, §3).

**Verified live 2026-08-14**, not assumed:
```bash
for p in privacy terms support .well-known/apple-app-site-association; do printf "%-46s " "$p"; curl -s -o /dev/null -w "%{http_code} %{content_type} redirects=%{num_redirects}\n" "https://caydexinvest.com/$p"; done
```

⚠️ **The old form of this command used `curl -sI` (HEAD) and now returns `405` on all four
paths** — the routes are registered `@app.get` only (`main.py`), and FastAPI's `APIRoute` does
not auto-add HEAD. The pages are healthy; GET returns `200 text/html` ×3 plus
`200 application/json` for the AASA, zero redirects. Anyone re-running the old command before
submission would read four 405s as an outage and un-tick §3 on a phantom. Apple fetches the
AASA with GET, so nothing is actually broken.

**Meanwhile:** use **Google sign-in** to test the app. It bypasses email confirmation entirely
(Google addresses arrive pre-verified), so #1 does not block anything except email/password
signup.

~~Code-side defects found in the 2026-08-05 audit — 11 lower-priority ones still open.~~
✅ **All of them are now fixed** (spot-verified in source 2026-08-14: the auth-transition view
rebuild, the empty-report cache poisoning, degraded-FMP persistence in two services, the
ApeWisdom cache deadlock, the chat stock card's +0.00%, the literal "HTTP 400", the
whitespace-padded bearer, APNs detach on sign-out, PDF deletion past 100 objects, the cache
template's cancellation hang, and the sentiment TTL clobber — plus the P2 unbounded caches).
Nothing is outstanding in `~/.claude/plans/everything-is-pushed-and-twinkly-jellyfish.md`.

**The 2026-08-14 audit replaced them with a new list**, fixed the same day: price alerts dead in
production, `CFBundleDisplayName`, the tenth privacy data type, the false App Review note, the
missing `match` notification category, an undisclaimered Buy/Sell verdict served by the API, 36
fabricated view counts, and seven TTS prompts naming real investors.

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

Both files are dated **August 13, 2026** (`terms.html:33`, `privacy.html:48`, commit `d0ca155`).
*(This line said "final and dated July 29, 2026" until 2026-08-14 — the date was two revisions
stale, and "final" is not a useful word for a document that has moved twice since.)*

- [x] Host `documents/legal/privacy.html` at `https://caydexinvest.com/privacy`
- [x] Host `documents/legal/terms.html` at `https://caydexinvest.com/terms`
- [x] Stand up a **support page** at `https://caydexinvest.com/support` — App Store Connect
      requires a Support URL and yours is currently `mailto:`-only. A page with a contact
      form or just the support email plus a short FAQ is enough
- [x] Verify all three load over HTTPS with no mixed content
      *(re-verified live 2026-08-14 with GET: the three pages return 200 `text/html`, and the
      AASA returns 200 **`application/json`** — not `text/html`, as this line claimed until
      2026-08-14. Content type is the one thing Apple is strict about for the AASA, so getting
      it wrong here would have masked a real failure. Zero redirects on all four.)*

The in-app native versions (`PrivacyPolicyView`, `TermsOfUseView`) mirror the hosted text.
If you edit one, edit both — I've kept them in parity.

---

## 4. Purge git history 🔴 — SCOPE CORRECTED 2026-08-07

**Full runbook: [GIT_HISTORY_PURGE_RUNBOOK.md](GIT_HISTORY_PURGE_RUNBOOK.md).** Read that, not
this summary — it has the pre-flight safety check, the exact `git-filter-repo` invocation, and
the trap about `documents/Books`.

This section used to scope the job as "the two copyrighted PDFs". That is off by three orders
of magnitude. `.git` is **1.0 GB**, and the PDFs are a few MB of it. Measured 2026-08-07:

| Path | Bytes in history |
|---|---|
| `backend/data/book_audio` | **702 MB** |
| `separate_project/stocks_detector` | 367 MB |
| `backend/data/journey_audio` | 83 MB |
| `backend/data/money_moves_audio` | 45 MB |
| `*_gemini_bak` | 54 MB |
| The PDFs | a few MB |

⚠️ **The narration audio is NOT a copyright problem** — it narrates the app's own ~500-word
`documents/Books/core N.txt` summaries (~150 wpm against a 58-minute clip), not the books. It
is a *size* problem. The two PDFs remain a genuine copyright item and ride the same rewrite.

✅ **Pre-flight already verified:** all 10 book clips, 207 journey clips and 13 money-moves
clips are in Supabase Storage, so the repo copies are redundant and recoverable. Re-check
before running (command in the runbook).

- [x] ~~**Flip the repo private**~~ — 🚫 **DECIDED 2026-08-14: the repo STAYS PUBLIC.**
      Ticked as *decided*, not as *done*. Two consequences follow, and neither is optional:
      1. **Rotating the leaked `service_role` key (§0) is now the ONLY mitigation** for that
         credential, not a belt-and-braces one.
      2. **This purge stops being optional cleanup.** Both copyrighted PDFs stay anonymously
         fetchable by commit SHA (`raw.githubusercontent` at `58f91f4e…` → `HTTP 200`,
         re-verified 2026-08-14) for as long as the repo is public. The purge is now the
         actual fix for the copyright exposure, so it belongs on the pre-launch list rather
         than "after submission". Same for the ed25519 private key in §0.
- [ ] ⚠️ **Add three paths the runbook's `--path` list is missing**, all found 2026-08-14:
      `--path MarketPulse` (the leaked `service_role` key),
      `--path 'eval "$(ssh-agent -s)"'` and `--path 'eval "$(ssh-agent -s)".pub'`.
      Purging without these leaves both secrets fetchable by SHA. **Rotation still comes
      first** — GitHub serves unreachable objects for a while after a rewrite.
- [ ] **Commit or stash your working tree** — `git filter-repo --force` ends in an
      unconditional `git reset --hard` and will destroy uncommitted work (runbook §1b)
- [ ] Delete the ~~**47**~~ **23** remote `origin/claude/*` branches on GitHub — `--all` does *not*
      rewrite them, and each keeps the full old history alive (runbook *After*)
- [ ] Run the runbook (back up first — it rewrites every commit)
- [ ] Force-push, then delete and re-clone every other copy of the repo
- [ ] Email GitHub Support to purge cached views — old blobs stay reachable by direct
      commit SHA until garbage collection
- [ ] Confirm `du -sh .git` lands in the **60–120 MB** range (not "well under 100 MB" — that
      target is unreachable while the 14.4 MB of book cover art stays tracked, which it should),
      and both gates still pass

⚠️ **The runbook had six defects, two of them data-destroying — corrected 2026-08-14.** Do not
work from a cached memory of it; re-read it.

---

## 5. Migrations — ✅ 103–136 ALL APPLIED, NONE PENDING (re-verified 2026-08-14 evening)

> **Re-verified 2026-08-14 (evening) by querying the LIVE database directly** — not the
> snapshot, not this file's memory. Every migration from **103 to 136** is applied and
> **nothing is pending.** The table below covers 103–115; 116–134 are itemised after it.
>
> **135 `signup_credit_seed_from_plan_credits`** — applied, both halves verified
> (`create_user_credits` AND `handle_new_auth_user` now read `plan_credits`). It fixed a real
> defect: BOTH signup seeds hardcoded a dead pricing generation — 3/25/100 and a flat 50 —
> against a live `plan_credits` of 50/1200/4000. The nested trigger runs first, so the outer
> `50` hit its `ON CONFLICT DO NOTHING` and was unreachable code; what actually landed for a
> new free account was **3 credits, against a 20-credit report.** Migration 100 saw both and
> deferred them. Confirmed live: a new free account now gets **50**.
>
> **136 `journey_images_bucket`** — applied (bucket present).
>
> ⚠️ **121 and 122 do not exist** — no files, and `git log --diff-filter=D` shows none were
> ever deleted. They are skipped numbers, not a hole in the apply chain. Same for 033. Do not
> go looking for them.
>
> Method note, because it nearly caught me: I first probed 126 and 127 by GUESSING their column
> names from the filenames and got `false` for both. Both are in fact applied — 127 adds
> `return_status` / `return_window_years`, nothing called "provenance". Verify against the
> migration's actual DDL, never against what its name suggests.

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

### 116–134 — applied, verified live 2026-08-14

Checked by querying for each migration's real objects, not by name-matching.

| Migration | Evidence in the live database | State |
|---|---|---|
| 116 drop `user_book_progress` | `to_regclass` returns NULL — table gone | ✅ applied |
| 117 purchased-credit pool | `user_credits.purchased_total` present | ✅ applied |
| 118 two-pool spend/refund | `spend_credits` / `refund_credits` functions present | ✅ applied |
| 119 `notification_events` | table present | ✅ applied |
| 120 `notification_job_state` | table present | ✅ applied |
| 123 credit_purchases txn index | index on `transaction_id` present | ✅ applied |
| 124 refund pairs each debit once | `credit_transactions.reverses*` column present | ✅ applied |
| 125 `price_alerts` | table present | ✅ applied |
| 126 portfolios active group | `set_active_portfolio()` + `idx_portfolios_one_active_per_user` present | ✅ applied |
| 127 whale return provenance | `whales.return_source` / `return_status` / `return_window_years` present | ✅ applied |
| 128 learn media buckets private | `journey-media`, `money-moves-media`, `book-media` all `public=false` | ✅ applied |
| 129 portfolio unique constraints | constraint present | ✅ applied |
| 130 chat rolling summary | `chat_sessions.memory_summary` present | ✅ applied |
| 131 `user_investor_profile` | table present | ✅ applied |
| 132 `user_memory_facts` | table present | ✅ applied |
| 133 book covers bucket | `book-covers` bucket present | ✅ applied |
| 134 `answered_fields` | column + CHECK present; probed live and it accepts `'{}'` and valid values, rejects out-of-vocabulary and NULL elements | ✅ applied |

`backend/database/schema_snapshot.sql` was regenerated on 2026-08-14 **after** 134 landed, so it
now answers for every one of these.

### Migrations: all applied — but one ACTION remains

> **Re-verified 2026-08-07 against a fresh `schema_snapshot.sql`.** The previous dump was from
> 2026-08-04 and predated 112–115, so it could not answer for any of them; this section listed
> three migrations as pending that were all already applied. **103–115 are now all applied and
> nothing is pending.** The remaining work below is a data change and an env var, not DDL.

- [x] **103_persona_style_names.sql** — applied. *(verified live 2026-08-07: all five active
      personas carry style names, no real investor names.)* Real names would be an App Store
      5.2.1 risk. **Do not replay 074** — it reverts the names via `ON CONFLICT DO UPDATE`
      (`074:17-32`), so this can regress without anyone touching 103.
      ⚠️ *Corrected 2026-08-14: this said "do not replay 043 **or** 074". Only 074 rewrites
      `name`. `043:16-23` is a plain `UPDATE` that never touches it, and it carries the
      icon/colour alignment the iOS fallback depends on — so avoiding 043 on this advice would
      have been the actual mistake.*
      To re-check (this is a data `UPDATE`, invisible to a schema-only dump):
      ```sql
      select key, name from public.agent_personas order by key;
      ```
      ⚠️ The query printed here previously was `select persona_key, display_name …`, which
      **errors** — `agent_personas` has no such columns (`schema_snapshot.sql`, and
      `103_persona_style_names.sql:42-45` itself writes `SET name = … WHERE key = …`). Anyone
      who ran it saw a failure and could reasonably have read that as "not applied".

- [x] **112_grant_tier_upgrade.sql** — applied *(verified live 2026-08-07)*. Fixed a mid-month
      upgrade delivering **zero credits**: `ensure_credit_period` only grants at the monthly
      boundary, so buying Pro on the 10th flipped `users.tier` and left the balance untouched —
      money taken, next tap returns 402, for up to four weeks. The `grant_tier_upgrade` RPC
      grants on the tier *transition*, idempotent by construction so `Transaction.updates`
      replays cannot farm credits, and never claws back on a downgrade.
      **One product decision is baked in** — `used` is preserved, so a Free user who spent 30
      credits and upgrades has 1170 available rather than 1200. Zeroing `used` is a one-word
      change documented in the migration header.

- [x] **113_users_is_admin.sql** — applied *(verified live 2026-08-07)*. Closed a privilege
      escalation: `admin.py` authorized on a hardcoded email allowlist, and an email claim is
      not a credential — Supabase auto-sets `email_confirmed_at` while "Confirm email" is off
      (§5b(b), still unticked), so registering `admin@caydexinvest.com` — an address nobody
      holds, since §2 is unticked too — yielded a real session and every admin route.
      Authorization now reads `users.is_admin`, which registration cannot set.

- [ ] 🔴 **Flag your own account as admin — `is_admin` currently matches ZERO rows.**
      113 is applied and fails closed (`admin.py:64-66` returns only when
      `user.get("is_admin") is True`), so **every admin route answers 403 for every signed-in
      account today, including yours.** The escalation is closed; so is your own door.
      ```sql
      select id, email, is_admin, created_at from public.users where is_admin;
      -- verified 2026-08-07: (0 rows)

      update public.users set is_admin = true where email = '<your real address>';
      select id, email, is_admin from public.users where is_admin;   -- expect exactly 1 row
      ```
      If the first query ever returns a row you do not recognise, that address was registered
      by someone else — clear it and treat it as evidence the escalation was exercised.

- [ ] **Only after the row above is flagged:** confirm `ADMIN_TOKEN` is unset on Railway.
      ⚠️ **Sequence matters, and getting it backwards locks you out.** `config.py:38` defaults
      `ADMIN_TOKEN` to `None` and `admin.py:58-60` honours it only when set, comparing with
      constant-time `secrets.compare_digest` on bytes — it is a deliberate, off-by-default
      escape hatch for scripted maintenance, **not** a weak secret. But while `is_admin`
      matches zero rows it is the *only* credential that can reach an admin route. Unset it
      first and your sole recovery path is a direct SQL write. Flag the row, verify the select
      returns it, sign in and hit one admin route, **then** clear the variable.

- [x] **114_revoke_tier_credits_and_event_ordering.sql** — applied *(verified live 2026-08-07)*.
      A refund dropped the tier but left the credits spendable: revocation routed through
      `grant_tier_upgrade`, which never lowers `total`, so a refunded Max subscriber kept 4000
      credits (~200 reports) for the rest of the month having paid nothing. Also adds
      `subscriptions.last_event_at` for monotonic event ordering.

- [x] **115_drop_vestigial_credits_update_policy.sql** — applied *(verified live 2026-08-07)*.
      Dropped the `credits_update_own` UPDATE policy on `user_credits`. It was unreachable
      (`anon`/`authenticated` hold no privileges on that table) but read like an intentional
      "users may update their own credits" and was one routine `GRANT UPDATE … TO authenticated`
      away from becoming exactly that. Evidence: the fresh dump keeps `credits_select_own` and
      `credits_service_all` on that table and no longer contains `credits_update_own`.

~~**Tune the guest allowance after launch.**~~ ⚠️ **DEAD — deleted 2026-08-14.** This paragraph
described `GUEST_REPORT_MONTHLY_LIMIT = 1` as "delivering the wow report without an account".
It does nothing. AI generation went **account-only**: both doors (`POST /research/generate` and
`GET /stocks/{ticker}/report`) take `get_current_user`, `guest_report_budget_service` is
transitively dead, and `tests/test_auth_dependency_matrix.py` pins the absence. **Guests get
zero reports, not one.**

This matters beyond a stale knob: it was the origin of the false line in the App Review notes
("no login is required — the app is fully usable as a guest, so no demo account is needed"),
which would have sent a reviewer into a signed-out build unable to reach the app's headline
feature. That note is rewritten; **provide demo credentials in App Review Information**.

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

- [x] Enable it — *believed DONE 2026-08-06; **confirm in the dashboard**, I cannot read it.*

> ⚠️ **This file contradicted itself here for a week (resolved 2026-08-14).** The box was
> unticked and §5's migration-113 bullet called the setting "off … still unticked", while
> START HERE and §5c both stated it was switched **ON** on 2026-08-06 — §5c citing an actual
> registration of a real address that reached the "Confirm your email" screen. The two dated,
> observation-backed statements win, so it is ticked above.
>
> The live blocker is **not** this setting, it is §5c (SMTP): confirmation email is ON and
> nothing delivers it. Go there.
>
> Also stale below: the paragraphs arguing "until migration 113 is applied…" and "§2 is also
> unticked". **113 is applied** (verified live 2026-08-07) and **§2 is fully ticked** — the
> admin-by-email-claim escalation this section warns about is closed twice over. Left in place
> because the reasoning is still worth reading, but do not act on it as a live risk.

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

- [x] Point `caydexinvest.com` at Railway (also §3) — **DONE.** ⚠️ *This box was unticked and
      the paragraph below it said the domain "is still a Namecheap parking page" until
      2026-08-14. Both were stale. Verified live that day: `https://caydexinvest.com/privacy`
      returns `HTTP/2 200` with `server: railway-hikari` and `x-railway-edge`, and `dig NS`
      returns Cloudflare, not Namecheap. §3 had been back-ticked; §5c never was — the exact
      "unticked boxes that are secretly done" failure the preamble warns about.*
- [ ] Add the SMTP provider's **sending DKIM**, and **MERGE** its `include:` into the existing
      SPF record

⚠️ **Do NOT replace the SPF record.** `dig TXT caydexinvest.com` already returns
`v=spf1 include:_spf.mx.cloudflare.net ~all`, paired with Cloudflare Email Routing MX records —
that is what makes `support@` / `copyright@` / `privacy@` receive mail (§2). Overwriting it with
the provider's suggested record silently breaks every address this file promises in the legal
documents. Add the provider's `include:` **into** the existing string, and add its DKIM
selector as a new record (`resend._domainkey` / `send.` currently return nothing).

The old "one DNS session unblocks four things" table is gone: two of those four (§3 and the
AASA) are done, and the §7 row is not a DNS task at all — it is blocked on the App Store Connect
record. DNS work remaining is DKIM plus the SPF merge, and nothing else.

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

The code is done and tested against a local StoreKit configuration. These items make it work
against real Apple infrastructure.

> **Rewritten 2026-08-14.** This section said "these six items" and listed **two** products.
> **Six ship.** The four consumable credit packs (migration 117, `Caydex.storekit`,
> `StoreKitService.ProductID`, `BuyCreditsView`) appeared nowhere in this file — a
> `grep -i "consumable\|credit pack"` over all 787 lines returned zero hits. It also had no
> entry for the Paid Applications Agreement, which gates everything below it.
> `backend/tests/test_iap_product_and_privacy_parity.py` now pins the four in-repo surfaces
> against each other; App Store Connect is the one surface no test can reach, so it is on you.

### App Store Connect — Business (do this FIRST, it has the longest lead time)

- [ ] 🔴 **Accept the Paid Applications Agreement**, add **banking** details and complete the
      **W-9 / tax forms** (Business → Agreements, Tax, and Banking).
      Until this is active ASC returns **no IAP products at all** — `Product.products(for:)`
      comes back empty, so Add Credits renders every pack with its credit count and
      **"Price unavailable"** where the price belongs, no Buy button, and a banner carrying
      the reason. That is the designed degraded state, **not** a bug — and it is the same
      symptom a wrong product id produces, which is why this is the most commonly
      misdiagnosed IAP failure. Bank verification is not instant; start it early.

### App Store Connect — products (all SIX, blocked on the agreement above)

- [ ] Create two **auto-renewable subscriptions** in a subscription group. The product IDs
      must match exactly, or a real purchase verifies and then fails to map to a plan:
      - `com.phan.caydex.pro.monthly` — $14.99/month
      - `com.phan.caydex.max.monthly` — $39.99/month
- [ ] Create four **consumable** in-app purchases. Type matters: *Consumable*, not
      Non-Consumable (which would let one purchase be restored forever) and not
      Non-Renewing Subscription. Each must be **Cleared for Sale**, and on the first
      submission all four must be **attached to the app version**.

      Prices and credit grants come from `credit_packs` (migration **138**, superseding
      117's ladder) — ASC and the table must agree or the user is charged one price and
      shown another. Every cell below is **character-identical** to
      `frontend/ios/Caydex.storekit`; `tests/test_iap_product_and_privacy_parity.py` pins
      those two in-repo copies to each other, and this table is the only control on ASC.

      | Product ID | Reference Name (internal) | Price | Display Name (≤30) | Description (≤45) |
      |---|---|---|---|---|
      | `com.phan.caydex.credits.starter` | `Caydex Credits Starter (130)` | **$2.99** | `Starter` | `130 credits. Never expire.` |
      | `com.phan.caydex.credits.plus` | `Caydex Credits Plus (280)` | **$5.99** | `Plus` | `280 credits. Never expire.` |
      | `com.phan.caydex.credits.power` | `Caydex Credits Power (600)` | **$11.99** | `Power` | `600 credits. Never expire.` |
      | `com.phan.caydex.credits.mega` | `Caydex Credits Mega (1300)` | **$24.99** | `Mega` | `1,300 credits. Never expire.` |

      ⚠️ The `com.phan.caydex.credits.` **prefix is load-bearing**: the backend routes a
      verified transaction to the credit path by prefix (`IAP_CREDIT_PACK_PREFIX`). A pack id
      outside it is diagnosed as an unmapped *subscription* and refused.
      ⚠️ **Set United States as the base price country** and let ASC generate the rest. Never
      hand-set a foreign price: Apple's localized `displayPrice` always wins in the UI, so a
      hand-set price is a number nobody reviews and everybody pays.
      ⚠️ **Once these exist, a reprice must change ASC and `credit_packs` in the SAME
      session.** A repriced row against an old ASC price means the app quotes $5.99 while
      Apple charges $4.99. (Today this is safe only because nothing is purchasable yet.)
- [ ] **Review screenshot** for the IAPs — required before submission. One screenshot of the
      Add Credits screen showing all four cards satisfies all four products.
- [ ] **Review notes** for the IAPs, one line: *"Consumable credit packs. Credits are spent
      in-app on AI-generated research reports and chat. Purchased credits never expire
      (Guideline 3.1.1); the monthly subscription allowance resets separately. Requires
      sign-in — test account below."* Reuse the reviewer account from §7.
- [ ] **Tax category** — leave the default. These are digital services, not e-books, news, or
      software subscriptions.
- [ ] Give the **subscription group** a localized display name — it currently has
      `localizations: []`, and subscriptions cannot be submitted for review without one
- [ ] Fill in the localised display name, description, and a review screenshot for each
      (Apple rejects subscriptions with incomplete metadata)
- [ ] Note the app's numeric **Apple ID** (App Information) → set `IAP_APP_APPLE_ID`
- [ ] Create a **Sandbox tester** account (Users and Access → Sandbox)

### Apple root certificates

- [ ] Download Apple's public root CAs from https://www.apple.com/certificateauthority/
      (you want **AppleRootCA-G3**) and place them in `backend/certs/apple/`
- [ ] Deploy them with the app on Railway

Verification **fails closed** without these: with `IAP_ENVIRONMENT=Sandbox` or `Production`
and no certificates, the endpoint returns 503 rather than accepting anything. That is
deliberate — no trust anchor must never silently mean "trust everything" on a payment path.
`backend/certs/` does not exist today (checked 2026-08-14), so this is genuinely open.

**Three traps this section did not mention until 2026-08-14, each of which looks like success:**

- ⚠️ **`.gitignore:145` is `*.cer`.** Railway builds from git, so saving Apple's file as
  `AppleRootCA-G3.cer` and running a normal `git add .` silently omits it — you tick this box
  and production still 503s. Save it as **`.der` or `.crt`** (both accepted by the loader,
  neither ignored), or `git add -f`.
- ⚠️ **Do not convert it to PEM.** The loader is `load_certificate(FILETYPE_ASN1, …)`, which is
  DER-only. A PEM root **builds a verifier fine** — the startup log says "verifier ready" — and
  then throws `INVALID_CERTIFICATE` on the first real purchase, returning **400 "If you were
  charged, contact support"** to every legitimate buyer. A deploy that accuses your paying
  customers of forgery is worse than one that fails closed.
- ⚠️ **`IAP_APP_APPLE_ID` is a SECOND, independent 503.** It defaults to `None` and the library
  raises *"appAppleId is required when the environment is Production"*. Certificates load
  first, so this only surfaces **after** you fix the certs — and the box above blames the certs
  alone, sending you back to re-check something you already did.

**Readiness probe — run this instead of discovering the answer via a paying customer.**
It mutates nothing (the payload fails signature verification by design):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://<your-railway-host>/api/v1/billing/app-store-notifications \
  -H 'Content-Type: application/json' -d '{"signedPayload":"x"}'
```

**400** = the verifier was built; certificates and app id are both fine. **503** = certificates
missing *or* `IAP_APP_APPLE_ID` unset. Anything else is worth reading the logs for.

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

- [ ] ⚠️ **The app record probably ALREADY EXISTS — check before creating one.** The Xcode
      archive at `~/Library/Developer/Xcode/Archives/2026-06-06/` records
      `uploadEvent { state = success, adamId = 6759525689, uploadDestination = App Store }`,
      and an App Store upload requires an existing record. Two consequences if so:
      - That upload's sibling failed with **error 90717 — "Invalid large app icon… can't be
        transparent or contain an alpha channel."** Verify the icon before re-uploading, or
        you will burn another round trip on it.
      - `CURRENT_PROJECT_VERSION = 1` / `MARKETING_VERSION = 1.0` is the build already accepted
        on 2026-06-06, so you **must bump the build number** or the upload is rejected outright.

      Note the numeric app ID either way — IAP setup needs it (`IAP_APP_APPLE_ID`).
- [ ] **Paste the listing copy** — written 2026-08-14, [`app-store-listing.md`](app-store-listing.md):
      app name, subtitle, keywords, promotional text and the full description, each measured
      against Apple's character limits. Nothing existed before that date; this file stated the
      no-real-names *constraint* on the copy but never assigned writing it.
      ⚠️ The description came in at **4,018 / 4,000** on the first pass — Apple truncates
      silently — so it is now 3,906 with ~94 characters of headroom. If you add a sentence, cut
      one. "What's New" does **not** apply to a 1.0.
- [ ] **Availability: United States only.** This defers EU AI Act Article 50, GDPR, and the
      Article 27 EU-representative requirement entirely, at zero engineering cost
- [ ] Privacy Policy URL → `https://caydexinvest.com/privacy`
- [ ] Support URL → `https://caydexinvest.com/support`
- [ ] **App Privacy questionnaire** → read straight from
      `documents/legal/app-privacy-answers.md`. **TEN** data types, tracking = No.
      ⚠️ **Was "Nine" until 2026-08-14.** The tenth is **Purchases → Purchase History** (Linked
      = Yes, Tracking = No, purpose = App Functionality): StoreKit shipped, and every verified
      transaction is stored in `credit_purchases` with a NOT NULL `user_id`. Both machine-
      readable surfaces (`PrivacyInfo.xcprivacy` and the answer sheet) still said "no StoreKit
      purchase flow exists yet" a week after it shipped; both are now corrected and pinned by
      `tests/test_iap_product_and_privacy_parity.py`. Declare Purchase History, **never**
      Payment Info — Apple handles payment and the app never sees card details.
      ⚠️ The ninth is **User Content → Photos or Videos** (Linked = Yes, purpose = App
      Functionality), added 2026-08-07 with the Help Us Improve screen: the user can attach a
      screenshot to a bug report, which is emailed to `support@`. It is optional and
      user-initiated, and `PhotosPicker` runs out of process so there is no permission prompt —
      but the image reaches us, so it is declared. The code and the two machine-readable
      surfaces are pinned together by
      `tests/test_ios_feedback_flow.py::test_photos_usage_and_the_privacy_filing_agree`, which
      fails the build if they disagree **in either direction**; this ASC answer is the one part
      no test can reach, so it is on you.
- [ ] Category: Finance. **Age rating: 17+.** ⚠️ This line said **4+** until 2026-08-07 and that
      was an inconsistency App Review reads as carelessness: `documents/legal/terms.html:41`
      (mirrored verbatim in `Views/Screens/TermsOfUseView.swift:27`) requires users to be **18
      or the age of majority**, and a 4+ listing on an app whose own Terms bar minors
      contradicts itself. 17+ is the highest rating Apple offers — there is no 18+ tier — so it
      is the closest available match. Answer the questionnaire honestly: no gambling, no
      unrestricted web, no user-generated content. Neither of the two questions that normally
      drive the number up ("Unrestricted Web Access", "Frequent/Intense Mature Themes") applies,
      so you will need to set 17+ via the age-gate question rather than have it derived.
      Leave both Terms surfaces alone — they already agree with each other.
- [ ] **App Review notes** → the paragraph in §7 of `app-privacy-answers.md`. This is the
      single highest-value thing you'll paste; it heads off the fintech rejection
- [ ] IAP products: **do create them now** — see §6b. (This line used to say "not yet, wait for
      Phase 8"; Phase 8 is done and the StoreKit code ships, so §6b is the live instruction and
      this is no longer a reason to defer.)

### 🔴 Added 2026-08-14 evening — four required items that were in NO section

- [ ] 🔴 **A demo account, actually created and seeded.** The App Review notes you are told to
      paste promise *"Credentials are in the App Review sign-in fields; the account is pre-loaded
      with credits."* Live DB: **4 users, none of them a review account.** And anonymous
      `GET /stocks/AAPL/report` returns **401 `AUTH_REQUIRED`** — so a reviewer signed out
      literally cannot exercise the headline feature. Pasting that paragraph with the sign-in
      fields empty converts a fixed problem back into a rejection.
      ⚠️ **Google SSO will not work here** — ASC's Sign-In Required fields take a username and a
      password the reviewer types into the app; there is nothing to type for a Google account.
      **Best path:** Supabase Studio → Authentication → Add user with **Auto Confirm User**
      ticked. That bypasses the dead SMTP entirely and takes §5c off the submission path.
- [ ] 🔴 **Content Rights** declaration (ASC → App Information). **Substantive here, not
      clerical**: the Learn library ships original study guides for ten in-copyright books, the
      book cover art typesets real author names in a public bucket, and the app redistributes
      FMP / CoinGecko / FRED data. Answering "I have the rights" depends on §6 (the
      redistribution questions, still unasked) and the lawyer item on the study guides.
      **Do not answer it before those come back.**
- [ ] **Copyright** field — suggested `2026 Duc Hai Phan` (must match the Individual account's
      seller name), and **App Review Information contact details** (first name, last name, phone,
      email). Pure data entry, but hard-required to reach Submit.
- [ ] ⚠️ **The first Release export must be driven from the Xcode UI, not a script.**
      `security find-identity -v -p codesigning` returns **2 valid identities, both "Apple
      Development"** — there is no Apple Distribution certificate on this machine, and the Release
      config still specifies `CODE_SIGN_IDENTITY = "Apple Development"`. Organizer → Distribute
      App mints the distribution cert while you are signed in; a headless
      `xcodebuild -exportArchive … app-store-connect` fails outright.
      Related: **the Release configuration has never been compiled.** DerivedData holds only
      `Debug-iphonesimulator`, and the only archives are the two from 2026-06-06 — so ~10 weeks of
      work has never been through archive / validate / sign. Budget time for first-archive
      surprises, and note that a missing App-ID capability shows up here as a *signing failure*,
      which is itself the cheapest way to verify the entitlements are really enabled.

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
  Railway.

  ⚠️ **Rewritten 2026-08-14 — this described push as ONE notification.** Nine kinds across six
  categories now ship (`notification_kinds.py`, seven default-on), driven by four background
  loops. The insight sweeper is one of them. Additions this section never had:
  - **`PUSH_DRY_RUN` must be ABSENT on Railway.** It is the global kill switch: when set, the
    dispatcher writes the ledger row and never calls APNs — so the in-app inbox and badge keep
    working perfectly and you cannot tell from inside the app that nothing was ever sent.
  - A mangled `APNS_AUTH_KEY` PEM sets `PushService.enabled = False` and degrades at
    **`logger.debug`**, deliberately. No endpoint or startup log exposes whether signing is
    actually configured — so "the variables are set" is an assumption, not an observation.
  - **Time Sensitive Notifications** entitlement shipped 2026-08-08 (in both `.entitlements`
    files; the App ID capability is confirmed enabled). This section never recorded it. The
    silent regression risk is losing the key from one of the two files — which already happened
    once to `applesignin`.
  - `AppDelegate.Category.all` was missing `match`, so profile-match pushes arrived with no
    action buttons. **Fixed 2026-08-14** and pinned by
    `tests/test_ios_notification_category_parity.py`.

  Still outstanding:
  - [x] **Push Notifications capability** on `com.phan.caydex` in the developer portal.
        Without it the app can't register and no device token is ever issued
  - [ ] A **real iPhone** for the first end-to-end APNs delivery.
        ⚠️ *"Push does not work in the Simulator" is only half true and cost testing time.*
        The entire **client** half — categories, action buttons, interruption level, badge,
        cold-launch routing — is testable with **no phone** via `xcrun simctl push` and the
        nine fixtures in `frontend/ios/scripts/push-fixtures/`. What needs a device is the
        APNs round trip itself (token issuance and delivery).
        ⚠️ **Sign in first.** `PushNotificationManager` stashes and returns early unless
        authenticated, while onboarding spends the one-shot iOS permission prompt while the
        user is still a guest. Sign in, confirm a `device_tokens` row exists with
        `environment='production'`, *then* trigger.
  - [ ] Flip `APNS_ENV` from `sandbox` to `production` at launch.
        ⚠️ *Correct instruction, wrong reason — fixed 2026-08-14.* `push_service` routes
        **per token** using each row's stored environment; `APNS_ENV` is only the fallback for
        NULL rows, and the client has always supplied one. Still flip it. And note a
        mis-routed token gets a **400**, while the pruning logic deliberately removes tokens
        only on **410** — so a wrongly-routed token stays dead in the table forever.
  - [ ] Keep a copy of the `.p8` in your password manager — Apple allows exactly one download.
        Actual path: `/Users/haiphan/BIGDATA/myApp/data and keys/AuthKey_7YPQRK276L.p8`
        *(this said `BIGDATA/myApp/` until 2026-08-14 — one directory short).*
        ✅ Good news: `git rev-list --objects --all | grep '\.p8'` is **empty**, so the key was
        never committed and does not ride the §4 purge.
- ~~**IAP products** (Phase 8)~~ — ⚠️ **not a "later phase".** Phase 8 is done and the StoreKit
  code ships; §6b is the live instruction and lists **six** products, not two. This line
  contradicted §6b and §7 and is retained only so the contradiction is not re-introduced.
- **Screenshots** (Phase 10): I can capture these from the simulator when the UI is final.
  iPhone only (§8) — 6.9" and 6.5". ⚠️ Check the app icon for an **alpha channel** first;
  the 2026-06-06 upload failed on exactly that (error 90717).

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
4. **Real investor names in the Learn library** *(added 2026-08-14 — this list predates both
   surfaces)*. Migration 103 removed real names from the personas for Guideline 5.2.1, but the
   same shape survives elsewhere: the generated **book cover art typesets real names**
   ("WARREN BUFFETT", "BENJAMIN GRAHAM") and the covers live in a **public** bucket with their
   URLs baked into the app, and three **Journey lesson titles** are named after living
   investors ("The Buffett Way", "The Lynch Way", "The Cathie Wood Way"), with view code
   branching on the literal strings. These are titles of real books and descriptions of real
   methodologies, which is a much stronger position than the personas had — but "LESSON 1: THE
   BUFFETT WAY" is exactly the card that ends up in an App Store screenshot, which §7's
   metadata rule forbids. Worth a lawyer's read, and worth deciding before screenshots.
