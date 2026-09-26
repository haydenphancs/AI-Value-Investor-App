# App Store Connect — App Privacy answer sheet

Read this straight into the App Privacy questionnaire in App Store Connect
(App → App Privacy → Get Started). Every answer below is derived from a codebase audit,
not a guess; the "evidence" column says where it comes from so you can re-verify.

Keep in sync with `frontend/ios/ios/PrivacyInfo.xcprivacy` — the manifest and this
questionnaire must agree, and Apple compares them.

**Status: complete for the current build**, including IAP — StoreKit shipped and
**Purchases → Purchase History** is now the tenth data type (added 2026-08-14; §6's trigger
had fired but was never applied, so this sheet and the manifest both understated collection
by one type for a week).

---

## 1. First question: "Do you or your third-party partners collect data from this app?"

**Yes.**

## 2. Second question: "Do you use data for tracking?"

**No.**

Evidence: no `IDFA` / `ASIdentifierManager` / `AppTrackingTransparency` anywhere, and no
advertising or attribution SDK. `Package.resolved` has **nine** pins — sentry-cocoa plus the
GoogleSignIn dependency tree (googlesignin-ios, appauth-ios, gtmappauth, gtm-session-fetcher,
googleutilities, app-check, promises, interop-ios-for-google-sdks). None of the nine is an
ad or attribution SDK and all ship their own manifests, so the answer is unchanged.
*(This line used to read "exactly one pin (sentry-cocoa)" — stale since the Google SDK
landed. The conclusion was right; the stated reason was not. Re-count before trusting it.)*
No data is shared with data brokers, and nothing is linked with third-party data for
advertising. So there is **no** ATT prompt and `NSPrivacyTracking` is `false`.

---

## 3. Data types to select

For each type Apple asks three things: **Linked to the user?**, **Used for tracking?**,
and **Purposes**. Answer *Used for tracking = No* for every row.

| Select this data type | Linked | Purposes | Evidence |
|---|---|---|---|
| Contact Info → **Email Address** | Yes | App Functionality | Sign-up/sign-in; `public.users.email` |
| Contact Info → **Name** | Yes | App Functionality | Optional profile display name; `public.users.display_name` |
| Identifiers → **User ID** | Yes | App Functionality | Account UUID |
| Identifiers → **Device ID** | Yes | App Functionality | Random per-install UUID in the Keychain, sent as `X-Guest-Id` (`GuestIdentity.swift`). Rate limiting + pre-sign-in learning progress |
| Financial Info → **Other Financial Info** | Yes | App Functionality | Self-entered share counts / position values for the diversification score (`portfolio_items.shares`, `market_value`) |
| User Content → **Other User Content** | Yes | App Functionality | Chat messages, report ratings and written feedback |
| User Content → **Photos or Videos** | Yes | App Functionality | Optional screenshot the user attaches to a Help Us Improve bug report, emailed to support@. Out-of-process `PhotosPicker`, one image at a time, visible in the composer before it sends |
| Usage Data → **Product Interaction** | Yes | App Functionality | Watchlist contents, lesson/book completion, bookmarks, followed entities, and the optional learning preferences (experience level, explanation style, answer length, topics of interest) in `user_investor_profile` |
| Purchases → **Purchase History** | Yes | App Functionality | StoreKit 2 subscriptions + the four consumable credit packs. Every verified transaction is written to `credit_purchases` with a **NOT NULL `user_id`** alongside `transaction_id` / `product_id` / `price_cents`; subscriptions also set `users.tier`. Linked, therefore — see the note below |
| Diagnostics → **Crash Data** | **No** | App Functionality | Sentry. `sendDefaultPii = false` and `SentrySDK.setUser` is never called, so crash reports carry no identity |
| Search History → **Search History** | **No** | App Functionality | A tap on a search RESULT row feeds the "Trending searches" chips. The server keeps only an anonymous daily count per ticker (`search_pick_daily`, migration 179) — no user, device, IP or timestamp column exists. De-duplicated before the write, on the device and in server memory (a keyed digest never persisted). Typed queries are sent only to the search call itself (they can appear in server/Sentry logs like any request line) |

Notes on the non-obvious ones:

- **Learning preferences do NOT add a data type.** They are self-described *content*
  preferences — reading level, explanation style, answer length, subjects of interest — which
  is the same class as the watchlist and lesson progress already covered by Product
  Interaction. They are deliberately **not** Financial Info: the profile collects no finances,
  risk tolerance, time horizon, tax situation or investment objectives, and a test
  (`test_no_suitability_field_ever_creeps_in`) fails the build if anyone adds one. So the
  existing selection stands; only this evidence line changes.


- **Photos or Videos** — selected as of the Help Us Improve screen. It is optional and
  user-initiated: nothing is read unless the user picks an image, and they see it in the
  mail composer before sending. `PhotosPicker` is out-of-process, so there is no
  permission prompt and no `NSPhotoLibraryUsageDescription` — but the image still
  reaches us by email, which is why it is declared.

### Do NOT select these — verified absent

Payment Info · Credit Info · Precise Location · Coarse Location · Physical Address ·
Phone Number · Other Contact Info · Health · Fitness · Sensitive Info · Contacts ·
Audio Data · Gameplay Content · Customer Support · Emails or Text
Messages · Browsing History · Advertising Data · Performance Data ·
Other Diagnostic Data

Notes on the non-obvious ones:

- **Payment Info** — still absent, and it is NOT the same as Purchase History. Apple handles
  payment; the app receives only Apple's signed transaction. No card number, no billing
  address, no bank detail ever reaches us. Declare Purchase History, never Payment Info.
- **Performance Data** — Sentry runs with `tracesSampleRate = 0.0`, so none is transmitted.
  If you ever raise that value, add Performance Data here *and* to the manifest.
- **Search History** — MOVED to "select" on 2026-09-26 with the search-screen chips (§3
  table), as **not linked**. It is still not stored per user: only anonymous per-ticker daily
  counts. Declared anyway, because a tap on a result now leaves the device and is kept (as a
  count) beyond the request — Apple's test for "collected". `PrivacyInfo.xcprivacy` carries
  the same entry; `tests/test_ios_search_trending_guards.py` fails the build if the two drift.
- **Browsing History** — no `WKWebView` and no URL history collection.

---

## 4. Privacy Policy URL

`https://caydexinvest.com/privacy` (host `documents/legal/privacy.html` there).

The policy already covers what Apple 5.1.1(i) requires: what is collected and how, every
third party that receives it **by name**, the retention and deletion policy, and how to
withdraw consent. Cross-check it against the table above before submitting — they must
tell the same story.

---

## 5. Account deletion (Apple 5.1.1(v))

In-app deletion exists: **Profile → Settings → Delete Account**, calling
`DELETE /api/v1/users/me`.

It removes the account row, every FK-linked table, the **ten** tables that have no FK to
cascade from, and the user's generated report PDFs in Storage.

Nine keyed on `user_id` (`_UNLINKED_USER_TABLES`, `api/v1/endpoints/users.py`):
`user_learn_progress` · `chat_usage_budget` · `credit_transactions` · `watchlist_items` ·
`portfolios` · `push_send_log` · `research_reports` · `chat_sessions` ·
`user_investor_profile`. One keyed on `identity_key` (`_UNLINKED_IDENTITY_TABLES`):
`analytics_events`. `chat_messages` needs no entry — it cascades from `chat_sessions(id)`.

⚠️ This list grows every time a table is made guest-writable, because migrations 108/110/111/131
each **dropped** a `user_id` FK so signed-out callers can be partitioned per install — and that
FK *was* the deletion path. A new guest-writable table without an entry here means a deleted
account's rows survive, which the privacy policy says they do not.

*(Corrected 2026-08-14: this said "the four tables" and named `user_book_progress`, which
migration 116 dropped. It had missed six tables added since.)*

If a reviewer asks what survives: only error-monitoring records at Sentry, for that provider's
retention period, containing diagnostic data and a pseudonymous account identifier — no name
or email.

---

## 6. Revisit when

- ~~**IAP ships (plan Phase 8)** → add **Purchases → Purchase History**~~ — **DONE
  2026-08-14.** Declared in §3 and in `PrivacyInfo.xcprivacy`. Left here as a record of how
  it went wrong: the trigger fired when StoreKit shipped, nothing re-read this section, and
  both surfaces stayed stale for a week. A "revisit when" line is only as good as the thing
  that re-reads it — prefer a test.
- **A new consumable or subscription product** → no new data type; `credit_purchases` already
  covers it. But the product id must exist in App Store Connect *and* in `credit_packs`
  (migration 117), or the purchase verifies and then fails to map.
- **Push delivery ships (plan Phase 9)** → no new data type; the device token is already
  covered by Device ID. Notification *content* is generated server-side.
- **Any new SDK** → check whether it ships its own privacy manifest and whether it adds a
  data type. Update `Package.resolved`, the manifest, `AcknowledgementsView`, and this file.
- **`tracesSampleRate` raised above 0** → add Performance Data (see §3).

---

## 7. Related review-notes text (not part of the questionnaire)

Paste into **App Review Information → Notes** — it heads off the most likely rejection
for an app in this category. This is the text `backend/scripts/asc_review_resubmit.py`
writes; applied to App Store Connect on 2026-09-24 and read back unchanged (the 1.0 (9) rejection under 2.5.4 was
answered by the "Background audio" paragraph; `remote-notification` was removed from the
build). Edit the script, not this copy, and re-run it; ASC caps the field at 4,000 chars.

> Caydex is an information and education tool for researching publicly traded companies. It
> is not a broker-dealer, investment adviser, or financial institution; it holds no client
> funds, connects to no brokerage, executes no trades, and does not accept or move money.
> All portfolio figures are self-entered by the user for an educational diversification
> score.
>
> AI-generated content. Company analysis, the written research reports, and the in-app chat
> are generated by a large language model and labelled as AI-generated throughout. The app
> also displays several of its own computed indicators on named securities — a technical
> Buy/Sell meter, an estimated fair value, and a 0-100 company score. These are
> deterministic outputs of published formulas over public financial data, presented as
> information for the user's own research, not as a recommendation or personalised advice.
> Every one of these surfaces carries a "not financial advice" disclaimer, and the app
> requires a first-run acknowledgement before any analysis is shown. See Profile → About &
> Legal → Disclaimers.
>
> Educational library. The Wiser tab contains original study guides written by us that
> summarise the ideas of ten well-known investing books, plus original lessons and articles.
> No book text is reproduced; all narration audio is of our own writing.
>
> Demo account — please use this to review. Caydex requires an account. Our market-data
> licence (the signed Order Form is attached) grants End-User Display Rights only, which
> permit the data to be displayed solely through the licensee's authenticated platform, so
> we cannot serve it to a signed-out caller. The app also contains significant account-based
> features — credits, paid AI reports, subscriptions, watchlists and portfolios — and
> provides Sign in with Apple and in-app account deletion (tap the profile picture at the
> top right of Home > General Settings > Delete Account, under Danger Zone). Credentials are
> in the App Review sign-in fields. The account is on the Max plan and pre-loaded with
> credits, so every feature, including narration, works without a purchase. Every in-app
> purchase can still be bought in sandbox from this account: Profile > Plans for the Pro and
> Max subscriptions, and Profile > Add Credits for the four credit packs.
>
> In-app purchases. Two auto-renewable subscriptions (Pro, Max) and four consumable credit
> packs. Credits are consumed only inside the app for AI generation; they are not a
> currency, cannot be transferred or cashed out. Purchased credit packs do not expire
> (Guideline 3.1.1); the monthly subscription allowance resets separately each month.
>
> Background audio (UIBackgroundModes: audio). Caydex narrates its education library, and
> the narration keeps playing after the user leaves the app or locks the screen, with play,
> pause, skip 15 s and scrubbing on the Lock Screen and in Control Center. Narration is a
> Pro/Max feature; the demo account is on Max, so it plays with no purchase. To hear it:
> sign in, tap the Wiser tab (last tab), tap the first Money Moves article, then tap Listen
> Now. Books work the same way (Wiser > AI-Enabled Books > any book > Listen Now). Then go
> to the Home Screen or lock the device: the narration continues. A screen recording of this
> on a physical iPhone is attached to App Review Information. The app declares no other
> background mode.
>
> Age rating 18+. Our Terms require users to be 18 or the age of majority. The app contains
> no gambling, no unrestricted web access and no user-generated content visible to other
> users.
