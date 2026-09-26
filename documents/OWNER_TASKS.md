# Caydex — what runs by itself, and what you must do by hand

Last full sweep: **2026-09-25**. Keep this file current. Whenever a change adds a background
loop, a feature flag, a manual step or a deadline, update this file in the same change.

- **Part 1** lists what runs automatically. You only need to keep the switches on.
- **Part 2** lists what only you can do. Tick the boxes as you go.

Commands run from `backend/` unless stated otherwise. **⚠️ PROD** marks a command that writes
production (Supabase, Storage or App Store Connect). Run its dry run first and read the plan.

---

## Part 1 — Automatic (no action, as long as the switches are right)

### The two master switches

1. **`ENVIRONMENT` on Railway must NOT be `development`.**
   - `development` is the code default. With it, the web server silently skips every loop below: refunds, subscription expiry, pushes and all the pre-warmers.
   - The only trace is one INFO log line at boot.
2. **The web service runs ONE uvicorn worker.** Most loops are safe only because of that.
   - A parity test (`backend/tests/test_deploy_command_parity.py`) fails if `--workers` is added.

### 1A. Web-server loops (`backend/app/main.py`, 24 of them)

| # | Job | When | Turned on by (default) | What it does |
|---|---|---|---|---|
| 1 | Close snapshot | hourly, all day | always | Stores the last two official closes for every symbol. Every day-change % is computed against them. |
| 2 | Social snapshot | once per UTC day (hourly retry) | always | ApeWisdom mention counts → `social_mentions_history`. Feeds the 7-day social count. |
| 3 | News pre-warm | every 2 h | always | News for the top 20 watchlist tickers. Also deletes old rows from 6 log/budget tables. |
| 4 | Report pre-warm | hourly | `REPORT_PREWARM_ENABLED` (on) | Keeps the top 20 tickers' report data warm. |
| 5 | Scanner pre-warm | every 15 min, 9:30–16:00 ET | `SCANNER_PREWARM_ENABLED` (on) | Daily Scanners, Signals and Themes caches. |
| 6 | Index pre-warm | every 30 min | `INDEX_PREWARM_ENABLED` (on) | S&P 500 / Nasdaq / Dow detail pages. |
| 7 | Quarterly industry chain | first Sunday of Jan/Apr/Jul/Oct, 02:00 UTC; the next phase starts every 30 min | always (per-phase claim) | Industry dossier → competitor intel → patents/FDA → moat benchmarks → sector & industry medians. A failed phase is retried up to 3× the same day. |
| 8 | TTM benchmarks | Sunday 06:00 UTC | always (claim) | Refreshes trailing-12-month industry medians. Retried the same way. |
| 9 | Volatility | daily 08:00 UTC, and once at boot | always | Volatility for the top 200 watchlist tickers plus SPY. |
| 10 | Whale hydration | politicians every 6 h; full sweep daily ≥ 02:00 UTC | always (claim, 3 h) | 13F holdings, congressional trades and filings → the `whale_*` tables. |
| 11 | Whale profile pre-warm | once, after the first politician sweep | `WHALE_PREWARM_ENABLED` (on) | Rebuilds `whale_profile_cache`. |
| 12 | Research refunds | every 5 min | always | Refunds reports stuck over 15 min and sends a "research failed" push. |
| 13 | Subscription expiry | hourly | always | Expires lapsed subscriptions and drops the tier, even if Apple's notification was lost. |
| 14 | Updates insights | every 5 min, 04:00–20:00 ET; crypto-only every 30 min otherwise | always | "Why it moved" cards and `ticker_move` pushes for the top 200. |
| 15 | Chat starters | every 15 min while the market is active | `CHAT_STARTER_WARM_ENABLED` (on) | Pre-answers the suggested chat questions, at most 60 a day. |
| 16 | Theme rotation | 1st US trading day of the month, 18:30 ET; catch-up for 7 days | **`THEME_ROTATION_ENABLED` (OFF)**. Also `THEME_ROTATION_DRY_RUN` (off = publishes for real). | Re-scores every Emerging Frontiers list and changes at most 30%. |
| 17 | Theme insights | trading days 18:15 ET | **`THEME_INSIGHTS_ENABLED` (OFF)** | Daily performance plus a "why it's moving" note per theme. |
| 18 | Trillion Club daily | every day 07:00 ET | **`TRILLION_CLUB_JOBS_ENABLED` (OFF)** | Membership (market caps), 13F holdings, filing dates. |
| 19 | Trillion Club weekly | Monday 08:00 ET (no make-up later in the week) | same flag | 13F re-hash, new-filer probe, discovery screen. |
| 20 | Marketing publisher | every 10 min | **`MARKETING_ENABLED` (OFF)**; `MARKETING_DRY_RUN` (on) | Posts approved content. **No platform is wired, so it never posts.** |
| 21 | Link-hit flush | every 60 s | always | Saves `/go/{campaign}` click counts (needs migration 173). |
| 22 | Push dispatch | every 60 s | the notification trio: on unless `ENVIRONMENT=development`; APNs keys required | Sends queued pushes, including ones deferred for quiet hours. |
| 23 | Scheduled pushes | hourly wake | same | Earnings after 16:00 ET · insider + whale/congress after 18:00 ET · profile match after 19:00 ET. Once per ET day each, retried hourly after a failure. |
| 24 | Price alerts | every 60 s | same | All rules 04:00–20:00 ET; crypto-only rules otherwise. |

Started by a user action, not a clock: the report worker, the pre-warm when a ticker is opened,
admin recomputes, and marketing script generation.

### 1B. Railway

- **Web service** (`backend/railway.toml`):
  - Builds from the Dockerfile; health check `/health/pdf`.
  - Restarts on failure at most 5 times. After that, every loop stays down until you redeploy.
- **Marketing worker** (`backend/marketing/railway.toml`), **not created yet**:
  - Cron `15 * * * *` (UTC); starts a day's run from 16:00 ET.
  - Stays dry-run unless `MARKETING_DRY_RUN` is changed.
  - Phase 3 (2026-09-26, uncommitted): it now narrates the script (Kokoro voice, baked into the image) and stops there, closing each run as `phase3_voice_only`. Give the service **4 GB of memory**; the voice step peaks near 2 GB.

### 1C. Database (Supabase)

- No pg_cron and no edge functions. The only automatic work is row triggers:
  - new login → `public.users` row + `user_credits` row
  - chat message count
  - whale follower counts
  - `updated_at` stamps

### 1D. iPhone app

- No background tasks. The only background mode is audio.
- **Widget:** reloads every 20 min in session, 60 min in extended hours, and at the next 04:00 ET otherwise. It fetches for itself in market mode.
- **While the app is open:**
  - Home refreshes every 60 s.
  - A stock's quote every 15 s during the market day.
  - ETF, index, commodity and crypto screens every 30 s.
  - Research status every 3 s while a report is generating.
- **On every foreground:** re-registers the push token and syncs the time zone.

### 1E. This Mac (Claude Code hooks, `.claude/hooks/`)

- **Session start** — prints context. It does not start the watchdog.
- **Build guard** — blocks an iOS build without `-jobs 2`, a second concurrent build, and any build when swap is nearly full.
- **Swift memory watchdog** — runs only while a Swift build is running (yours or Claude's, ⌘B included), and stops about 30 s after it. A macOS LaunchAgent checks for builds every 5 s; check or remove it with `bash .claude/hooks/install-swift-build-watchdog-agent.sh status` (or `uninstall`). The watchdog kills a compiler at 10 GB, or at 7 GB when RAM is under pressure (a normal Archive build peaks at about 5.3 GB). It logs to `~/Library/Logs/caydex-swift-watchdog.log`.
- **After each edit** — Python syntax check; SQL migration lint; iOS theme parity tests; design-doc parity test.
- **FMP URL guard** — blocks a `/api/v3` URL.

---

## Part 2 — By hand (only you can do these)

### 2.1 Do now / verify once

- [x] **Railway variables** (verified 2026-09-25 against deploy 95ea9b25):
  - `ENVIRONMENT=production`; all 24 loops started.
  - Deploys use root `/backend` and config `/backend/railway.toml`.
  - `SENTRY_DSN` is set and the SDK starts.
  - APNs keys are set, `APNS_ENV=production`, `PUSH_DRY_RUN` is unset.
- [ ] **Sentry quota:** the DSN is fine, but Sentry received no backend events after 2026-09-04. Check the Sentry project's quota and rate-limit page.
- [ ] **Clean up Railway variables** (found 2026-09-25; one staged change, so one redeploy):
  - **Delete unused secrets.** Nothing on Railway reads them: `DATABASE_URL`, `SUPABASE_DB_PASSWORD`, `REDIS_URL`, `SERP_API_KEY`, `NEWS_API_KEY`, `FINANCIAL_NEWS_API_KEY`, `DISCORD_WEBHOOK_URL`. Keep them in your local `backend/.env`, because `dump_schema.sh`, `check_function_grants.py` and `error_digest.py` use them.
  - **Delete 12 dead knobs** that do nothing: `FREE/PRO/PREMIUM_TIER_DEEP_RESEARCH_LIMIT`, `DEEP_RESEARCH_TIMEOUT_SECONDS`, `CACHE_TTL_SECONDS`, `AI_MODEL_VERSION`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `LOG_FORMAT`, `NEWS_SCRAPING_SCHEDULE`, `WIDGET_RENDER_TIMEOUT_SECONDS`, `WIDGET_UPDATE_SCHEDULE`.
  - **Decide on three overrides.** `LEGAL_DISCLAIMER`, `APP_NAME` and `APP_VERSION` are set on Railway and override the code. Railway's disclaimer is SHORTER than the one in `config.py`; delete it if that isn't intended.
- [ ] **Migration 177 check** (the demo-tier code is live in 95ea9b25). In Studio: `SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='comp_tier';` should return 1 row, and `SELECT email, comp_tier FROM public.users WHERE comp_tier IS NOT NULL;` should show the App Review demo account as `premium`.
- [ ] **App Review:** 1.0 (10) was resubmitted 2026-09-24. If it's rejected again: `./venv/bin/python scripts/asc_review_resubmit.py --video <mov>` (dry run), then add `--apply` (**⚠️ ASC**).
- [x] **Demo-tier fix is deployed.** `def effective_tier` is in deployed commit 95ea9b25; the database side is checked by the migration 177 item above.
- [x] **Migration 176 is applied** (verified live 2026-09-26: `marketing_scripts.run_date`, `content_rejections` and `reject_reason` exist). The marketing worker needed it.
- [ ] **Migration 172** (data-only 'N/A' sector cleanup): run its two VERIFY counts. Both must be 0; if not, apply it.
- [ ] **Migration 166 constraint:** run the count query in `166_…sql`. If it returns 0, run `ALTER TABLE public.credit_transactions VALIDATE CONSTRAINT credit_transactions_split_sums;`.
- [x] **Trillion Club go-live.** Seed done 2026-09-24. Both flags are on. The daily job ran OK on 2026-09-25 (19 members evaluated). One filing, Alphabet 2025-Q3, is marked degraded because of an ambiguous CUSIP (`91864C107`) and is retried daily; check around 2026-10-03 that it cleared.
- [x] **Emerging Frontiers flags are on**, with `THEME_ROTATION_DRY_RUN=true`. Theme insights first run tonight, 18:15 ET.
- [ ] **After Thu 2026-10-01 18:30 ET:** review October's dry-run decisions in `theme_rotation_runs`. If the lists should publish, set `THEME_ROTATION_DRY_RUN=false` (or delete it) **by Oct 7**. After Oct 8 18:30 ET there is no October rotation.
- [ ] **Learn misattribution reseed** (production still serves the old text):
  1. `./venv/bin/python scripts/seed_journey.py` (**⚠️ PROD**)
  2. `./venv/bin/python scripts/seed_money_moves.py` (**⚠️ PROD**)
  3. Then, after ≤ 1 h or a restart: `./venv/bin/python scripts/check_money_moves_published.py`
- [ ] **Whale registry sync** (Berkshire's title change is only in the JSON):
  1. `./venv/bin/python -m scripts.sync_whale_registry --dry-run`
  2. Then without the flag (**⚠️ PROD**).
- [ ] **Re-dump the schema** (the snapshot is from 09-19; 6 migrations are newer):
  1. `./scripts/dump_schema.sh`
  2. `./venv/bin/python scripts/generate_schema_doc.py`, then `--check`
  3. Then ask Claude to prune `_PENDING_MIGRATION_TABLES` / `_PENDING_CLIENT_REVOKES` in the two grants/atlas tests.
- [x] **Committed and pushed** as 95ea9b25 (2026-09-25).
- [x] **Deployed** 2026-09-25 15:09 UTC as commit 95ea9b25. It booted clean with no errors, and the full suite on that commit passed (25,946). Fixes included:
  - a refunded subscription could be restored by replaying the old Apple receipt;
  - a lost Apple refund webhook;
  - price-alert re-enable;
  - 13F pushes and cards;
  - the widget-token mint;
  - guest PDF cleanup;
  - degraded snapshot caching;
  - event-loop blocking;
  - the deep-report final answer;
  - the FMP key in tool errors.
- [ ] **Ship an iOS build** with the 2026-09-25 report-screen fix. Opening an old "report ready" notification for a deleted report no longer charges 20 credits by itself; it shows "Report No Longer Available" with a priced Regenerate button.
- [ ] **Check whether the FMP key ever leaked into stored reports.** In Supabase Studio, search `research_reports` for text containing `apikey=` (for example `full_report::text ILIKE '%apikey=%'`). If any row matches, rotate the FMP API key and clean those rows. Until 2026-09-25 a failed FMP call inside the deep-research tools could pass the raw request URL, key included, to the model.
- [ ] **Orphaned guest-era report PDFs.** Reports claimed from guest mode before 2026-09-25 left their PDF under `research-pdfs/reports/<install-id>/` with no handle, so account deletion can't find them. If you want them gone, ask Claude for a one-off cleanup script. It would move each file to its account's folder, or delete it when no account owns it.
- [ ] **Decide on a small migration** to close the last credit-pack refund gap: a refund that arrives before the grant, for a transaction with no usable account token, can't be recorded (`credit_purchases.user_id` is NOT NULL). Options: a nullable `user_id`, or a separate refund-tombstone table.
- [ ] **Caydex Fair Value Estimate go-live** (built 2026-09-25; details in `documents/research/dcf-fair-value.md` §9 and `dcf-methodology-v1.md`). Two switches: `DCF_SHADOW` records the estimate for every Analysis-tab view and shows it to NOBODY; `DCF_ENABLED` publishes it to EVERY client at once (App Store builds included — there is no per-build gate).
  **Status, checked 2026-09-26:** steps 1, 2, 3 and 5 are DONE. Production runs commit `68be3cc6` with `DCF_SHADOW=true`, `DCF_ENABLED=true` and `FRED_API_KEY` set, and both tables exist. The 2–3 week shadow watch was skipped: both switches went on the same morning. **Step 4 is NOT done.** App Store Connect's newest build is 1.0 (10), uploaded 2026-09-24, before the DCF code, so testers see neither the Caydex row nor FMP's DCF row on the Analysis tab. The build number is bumped to 11 in all four places; archive and upload it. The weekly `dcf_fair_value_history` summary is still worth asking for.
  **Also 2026-09-26 (uncommitted, not deployed):** the report's Wall Street section became "Valuation & Institutions" (range first, range chart, no analyst UI), the Analysis-tab Valuation card got the same chart, and a launch-day bug was fixed. The bug (Sentry, `/research/generate`: "DcfFairValueResponse is not JSON serializable") meant the report-data cache has saved nothing since `DCF_ENABLED` went on, so every report re-fetches all of its data. Commit and **deploy the backend**, then archive build 11 (it contains the new screens). The original steps:
  1. Apply migration 178 (`backend/database/migrations/178_dcf_fair_value.sql`) in Studio. It adds `dcf_fair_value_cache` and the append-only `dcf_fair_value_history`. Check that `FRED_API_KEY` is set on Railway: the model cannot run without the 10-year Treasury history.
  2. Deploy the backend. The switches are still off, but three wording changes ship regardless: the PDF hero and the stored `valuation_analysis` state a neutral price gap instead of Undervalued/Overvalued, persona prompts no longer tell the AI to compute its own intrinsic value, and the sign-in screen states assent to the Terms.
  3. Set `DCF_SHADOW=true` on Railway. Live watch, 2–3 weeks, invisible to users: ask Claude weekly to summarise `dcf_fair_value_history` (value jumps and their causes, refusal mix, anything odd).
  4. Ship an iOS build with the new row through TestFlight and the App Store (it shows the row only when the backend sends it).
  5. Set `DCF_ENABLED=true`. For ALL users at once: FMP's DCF disappears from the Analysis tab (App Store builds without the new row show no DCF row); NEW AI reports carry the estimate and derive their valuation from it; the PDF hero, report narratives and chat quote it.
  - To turn it off: set `DCF_ENABLED=false`. New reports stop carrying it; every stored-report read path (report screens, chat grounding) and newly rendered PDFs drop the estimate block; report caches treat reports built with it as misses; cached snapshots rebuild on their own. NOT withdrawn: PDF files already rendered (served from Storage as they are), and valuation figures inside a user's saved report that were derived from the estimate (saved reports are frozen snapshots).
- [ ] **Search-screen chips go-live** ("Trending searches" / "Most added" / "Popular" on every search screen; built 2026-09-26):
  1. Apply migration 179 (`backend/database/migrations/179_search_trending.sql`) in Studio, then run its VERIFY queries (anon/authenticated must NOT execute the three functions; RLS on).
  2. Deploy the backend. Order is forgiving: before 179 the chips show the curated "Popular" list and picks are dropped (one ERROR log line), and search itself is untouched.
  3. **Before submitting the iOS build that includes the chips:** App Store Connect → App Privacy → add **Search History**, *not linked*, *App Functionality*, *not tracking* (it must match `PrivacyInfo.xcprivacy` and `documents/legal/app-privacy-answers.md`). The updated privacy policy (dated September 26, 2026) goes live at `caydexinvest.com/privacy` with the backend deploy in step 2 — the backend serves `app/templates/legal/privacy.html`, kept byte-equal to `documents/legal/privacy.html` and to the in-app `PrivacyPolicyView` by `tests/test_legal_pages.py`.
  4. Next schema re-dump: remove `public.search_pick_daily` from `_PENDING_MIGRATION_TABLES` in `tests/test_schema_doc_generator.py`.
  - The curated fallback and a `blocked` list live in `backend/data/search_trending_popular.json` — edit + deploy to change them, no app update.
- [ ] **Old launch items with no "done" record** (`documents/legal/LAUNCH_CHECKLIST.md`):
  - Supabase SMTP → Resend, plus `{{ .Token }}` in the reset-password template
  - publish the Google OAuth consent screen
  - re-shoot `02-ai-research-personas.png`
  - revoke the old sandbox APNs key `7YPQRK276L`

### 2.2 Recurring

| Cadence | Task | How |
|---|---|---|
| **Every ≤ 45 days** — next by **2026-11-08** | Refresh the hand-kept market caps for **Saudi Aramco, Samsung and SK hynix** (`manual_cap_usd` / `manual_cap_as_of`, both 2026-09-24 today). On day 46 (11-09) Aramco and Samsung **disappear** from the club. | Edit `backend/data/trillion_club_seed.json` → dry run `-m scripts.seed_trillion_club` → `--apply --update` (**⚠️ PROD**) |
| **Monthly** — next by **2026-10-19** | Re-dump the schema and regenerate the atlas. Do it sooner after 3–5 new migrations. | `./scripts/dump_schema.sh` → `scripts/generate_schema_doc.py` |
| **Monthly**, before the 1st trading day | Preview the next theme rotation | `-m scripts.preview_theme_rotation --month YYYY-MM` |
| **Monthly** | Watch the quotas. The FMP licence covers **1,000 unique users/month**; CoinGecko Basic is **100k calls/month**. | FMP / CoinGecko dashboards |
| **Quarterly**, after each 13F deadline (next **2026-11-16**) | Trillion Club stake re-check. **Nebius:** the warrant has been exercisable since 2026-09-11, so check it now. **Nscale / Syntiant:** an IPO flips the kind to listed. **Apple's Globalstar units:** unpublish when the Amazon merger closes. **AVSMC:** convert the commitment once TSMC reports it. **IMS:** the Dec-2024 figure. **Idemitsu:** the IR page is overwritten yearly. Never delete a JSON row: set `"published": false`. | Edit the seed JSON → `--apply --update` (**⚠️ PROD**) |
| **Quarterly** | Choose per company whether to ingest its 13F (`use_13f`; a bank's 13F is client assets) | Seed JSON or Studio |
| **Quarterly** | Regenerate the industry and benchmark universe files, then **hand-upload** them to the private `universe-data` bucket. `industry_universe.json` (May) is overdue; `benchmark_universe.json` (June) is due. | `-m scripts.discover_industries`; `-m scripts.build_benchmark_universe`; upload in Studio |
| **Quarterly** | Skim `documents/System Design/SYSTEM_DESIGN_GUIDELINES.md` for drift | — |
| **Optional, after each 13F season** | Warm the institutions chart | `-m scripts.hydrate_hedge_fund_flow --dry-run`, then the real run (**⚠️ PROD**) |
| **Every 120 days** — by **2027-01-22** | Re-verify the Trillion Club stakes and bump `verified_on`. All 130 are dated 2026-09-24 and flag stale on 01-23. | Seed JSON → `--apply --update` (**⚠️ PROD**) |
| **Yearly**, early January | Update the fair value's rate pair when Damodaran publishes his 1 Jan implied equity risk premium (pages.stern.nyu.edu/~adamodar → Implied ERP). Change `ERP_PCT` and `RF_AT_ERP_PCT` together, bump `MODEL_VERSION`, and update `dcf-methodology-v1.md`. Values will step once. | Ask Claude |
| **Yearly** | Apple Paid Apps Agreement / membership (current term ends **2027-02-17**) | developer.apple.com |
| **Yearly** (first 2027 submission) | ASC copyright year (`COPYRIGHT` in `scripts/asc_review_resubmit.py`) | ASC |
| **Yearly** | Add the next NYSE holidays. **iOS** `MarketHoursUtil.swift` stops at **2027**: add 2028 in an app release before 2028-01-17. **Backend** `app/utils/market_hours.py` runs to 2028, and a test fails on **2028-01-02** until 2029 is added. | Code change |
| **Yearly** | Confirm `caydexinvest.com` auto-renews (Namecheap; DNS on Cloudflare). It is also the passkey domain. | Registrar |

### 2.3 Every release (in this order)

1. [ ] Bump `CURRENT_PROJECT_VERSION` in **all 4** targets. The next build is **11**; a test pins all four equal.
2. [ ] `./venv/bin/python scripts/fmp_entitlement_probe.py --gate`: must exit 0.
3. [ ] `./venv/bin/python scripts/asc_audit.py`: read-only diff of App Store IAP config vs `Caydex.storekit`.
4. [ ] Archive in Xcode (**you only**; Claude never archives). One build at a time on this Mac.
5. [ ] `./frontend/ios/scripts/upload-dsyms.sh <path/to/that.xcarchive>`. Always pass the path; the default re-uploads the previous build.
6. [ ] Deploy the backend on Railway, since some fixes are backend-only.
7. [ ] After TestFlight feedback: `./venv/bin/python scripts/pull_testflight_feedback.py --since YYYY-MM-DD`.
8. [ ] Store screenshots: launch with `SIMCTL_CHILD_CAYDEX_QUOTE_WEEK=off`. No real investor names may appear.

### 2.4 When something happens

- **New migration:**
  - You apply it in Studio. Claude never does.
  - Add its table to `scripts/schema_curation.py` in the same change.
  - After changing functions, run `scripts/check_function_grants.py` (read-only).
  - Before trusting a "PENDING" note, check production.
- **Learn article or lesson changed:**
  1. `CLONE_MODE=block ./venv_clone/bin/python scripts/clone_learn_audio.py {moneymoves|journey} <name> --canonical`
  2. `verify_learn_voice.py`
  3. `./venv_clone/bin/python scripts/align_{money_moves,journey}_audio.py <name>`
  4. `seed_*` (**⚠️ PROD**)
- **Book text or covers changed:** needs an **app update**, because the text lives in generated Swift. Regenerate with `gen_books_swift.py` / `gen_book_read_along.py`. They now emit one statement per chapter; never one giant literal, which is what crashed the Mac.
- **New TestFlight tester:** edit `scripts/testflight_testers.local.json` → `seed_testflight_testers.py --dry-run` → run it (**⚠️ PROD**).
- **Give an account a comp tier:** `scripts/set_comp_tier.py --email … --tier premium`, then add `--apply` (**⚠️ PROD**).
- **Chat starter questions edited:** `scripts/seed_chat_starters.py --dry-run` → run it (**⚠️ PROD**).
- **Marketing go-live, when you decide:**
  1. ~~Apply 176~~ (done, verified 2026-09-26).
  2. Commit and **deploy the web service FIRST**. Since 2026-09-26 every worker call after the daily claim must carry the `X-Marketing-Claim` header, and the voice step reads assets back through a new route; an old web service would refuse the new worker. Leave `MARKETING_JUDGE_MODE` unset on the web service (the default is `enforce`; `shadow` only records the judge's verdicts).
  3. Create the Railway worker service: root `/backend`, config `/backend/marketing/railway.toml`, **4 GB memory**.
  4. Set the same `MARKETING_WORKER_TOKEN` on both services. On the worker also set `MARKETING_API_BASE_URL=https://caydexinvest.com` and `MARKETING_RUN_HOUR_ET` (default 16). Optional: `MARKETING_TTS_VOICE` (default `af_heart`), `MARKETING_TTS_SPEED`, `MARKETING_TTS_THREADS`, `MARKETING_MAX_VIDEO_SECONDS` (default 75).
  5. Keep `MARKETING_DRY_RUN=true` and `MARKETING_AUTO_PUBLISH=false`. The semantic judge does not yet meet its own calibration bar (it misses present-tense restatements of a past deal price), so a human reads every post.
  6. Check the first run: its row in `marketing_runs` should end `skipped` with `metadata.skip_reason = phase3_voice_only` (no video or posts exist yet, by design), and its metadata should show `preflight.voice.ready = true` and a `voice_asset_id`.
  7. Read the drafts: `scripts/marketing_preview.py --all-items` (or `--next 9`).
  8. After launch, set `MARKETING_APP_STORE_URL`.
- **Flags that need evidence before turning on:**
  - `CHAT_MODEL_ROUTING_ENABLED`: run `scripts/eval_model_routing.py` first.
  - `CHAT_RAG_ENABLED`: needs an ingestion pipeline.
  - `CHAT_PERSONALIZATION_ENABLED` / `CHAT_MEMORY_FACTS_ENABLED`: need a Terms §2 carve-out.
  - `PUSH_DRY_RUN`: must stay unset on Railway.
- **Decisions waiting on you:**
  - DCF fair value (`documents/research/dcf-fair-value.md` §8)
  - the Trillion Club "Bets" title and Eli Lilly's membership (counsel)
  - lawyer items in `LAUNCH_CHECKLIST.md`: adviser status, book study guides, LLC, real names on covers
- **Legal name change:** SSA → bank (about 2 weeks later) → ASC in 4 places: legal entity via Apple Support, W-9, bank account, DSA contact. Update the ASC bank promptly or a payout can bounce.

### 2.5 Deadlines, by date

| Date | What happens if nothing is done |
|---|---|
| **2026-10-01** 18:30 ET | First theme rotation, if you enabled it. Live unless `THEME_ROTATION_DRY_RUN=true`. |
| **2026-10-19** | Monthly schema dump due |
| **2026-11-08** | Last day Aramco and Samsung show in the Trillion Club; refresh the caps by then |
| **2026-11-16** | Q3 13F deadline, then the quarterly stake re-check |
| **2027-01-22** | Last day before all 130 stakes flag stale |
| **2027-02-17** | Apple Paid Apps Agreement / membership term ends |
| **2027 first submission** | ASC copyright still says 2026 |
| **before 2028-01-17** | iOS app needs the 2028 market holidays |
| **2028-01-02** | Backend holiday test fails until 2029 is added |

### 2.6 Scripts that write production (never run them "just to see")

- **Supabase:**
  - `seed_trillion_club` (`--apply`)
  - `sync_whale_registry`
  - `seed_journey`, `seed_money_moves`, `seed_book_audio`, `seed_book_covers`
  - `seed_chat_starters`, `seed_testflight_testers`
  - `set_comp_tier --apply`
  - `hydrate_hedge_fund_flow`
  - `recompute_industry_benchmarks`, `recompute_industry_moat_benchmarks`
  - `sql/seed_demo_account_credits.sql`
- **App Store Connect:** `asc_review_resubmit --apply`, `asc_apply_metadata`.

**Read-only against production (safe):**
- `asc_audit`, `pull_testflight_feedback`, `check_money_moves_published`, `check_function_grants`
- `fmp_entitlement_probe`, `preview_trillion_club`, `preview_theme_rotation` (without `--record`)
- `marketing_preview`, `error_digest`
- `dump_schema.sh` (writes only the local snapshot file)

**Spend money only:** the `eval_*` scripts and `load_test_reports` (spends real credits).

---

### Known, accepted gaps in the automatic jobs

These were found in the 2026-09-25 sweep and deliberately not changed. The last five came from the whole-app sweep's fixes:
- The whale hydrator's database writes briefly block the web server.
- An empty earnings calendar from FMP is read as a holiday.
- A theme rotation that failed twice retries just after midnight ET.
- During a database outage the Updates sweeper refreshes news 3× as often.
- Price alerts write every rule every minute: fine now, slow near 5,000 rules.
- A disabled pre-warmer logs one WARNING per boot.
- A one-shot price alert that was capped or muted is not replayed later; it fires on its next crossing.
- A daily `percent_move` alert counts +1 trigger every minute while the move holds (display only).
- A subscription bought on a NEW Apple lineage, whose app verify is delayed past a newer notification on the old subscription, reads as stale. The webhook still grants it.
- Growth and Profit Power cache a build whose sector-benchmark read failed (no "vs sector" line) for 24 h.
- The Tracking "Whales Bought/Sold" card now shows 13F rows. Before, it only ever showed congress rows.
