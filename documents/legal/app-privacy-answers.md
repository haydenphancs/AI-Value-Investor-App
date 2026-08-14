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
| Financial Info → **Other Financial Info** | Yes | App Functionality | Self-entered share counts / position values for the diversification score (`portfolio_holdings.shares`, `market_value`) |
| User Content → **Other User Content** | Yes | App Functionality | Chat messages, report ratings and written feedback |
| User Content → **Photos or Videos** | Yes | App Functionality | Optional screenshot the user attaches to a Help Us Improve bug report, emailed to support@. Out-of-process `PhotosPicker`, one image at a time, visible in the composer before it sends |
| Usage Data → **Product Interaction** | Yes | App Functionality | Watchlist contents, lesson/book completion, bookmarks, followed entities, and the optional learning preferences (experience level, explanation style, answer length, topics of interest) in `user_investor_profile` |
| Purchases → **Purchase History** | Yes | App Functionality | StoreKit 2 subscriptions + the four consumable credit packs. Every verified transaction is written to `credit_purchases` with a **NOT NULL `user_id`** alongside `transaction_id` / `product_id` / `price_cents`; subscriptions also set `users.tier`. Linked, therefore — see the note below |
| Diagnostics → **Crash Data** | **No** | App Functionality | Sentry. `sendDefaultPii = false` and `SentrySDK.setUser` is never called, so crash reports carry no identity |

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
Messages · Search History · Browsing History · Advertising Data · Performance Data ·
Other Diagnostic Data

Notes on the non-obvious ones:

- **Payment Info** — still absent, and it is NOT the same as Purchase History. Apple handles
  payment; the app receives only Apple's signed transaction. No card number, no billing
  address, no bank detail ever reaches us. Declare Purchase History, never Payment Info.
- **Performance Data** — Sentry runs with `tracesSampleRate = 0.0`, so none is transmitted.
  If you ever raise that value, add Performance Data here *and* to the manifest.
- **Search History** — the in-app search is ticker/entity lookup and is not stored per user.
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
for an app in this category:

> Caydex is an information and education tool for researching publicly traded companies.
> It is not a broker-dealer, investment adviser, or financial institution; it holds no
> client funds, connects to no brokerage, executes no trades, and does not accept or move
> money. All portfolio figures are self-entered by the user for an educational
> diversification score.
>
> **AI-generated content.** Company analysis, the written research reports and the in-app
> chat are generated by a large language model and labelled as AI-generated throughout.
> The app also displays several of its own computed indicators on named securities — a
> technical Buy/Sell meter, an estimated fair value, and a 0–100 company score. These are
> deterministic outputs of published formulas over public financial data, presented as
> information for the user's own research, not as a recommendation or personalised advice.
> Every one of these surfaces carries a "not financial advice" disclaimer, and the app
> requires a first-run acknowledgement before any analysis is shown.
> See Profile → About & Legal → Disclaimers.
>
> **Educational library.** The Learn section contains original study guides written by us
> that summarise the ideas of ten well-known investing books, plus original lessons and
> articles. No book text is reproduced; all narration audio is of our own writing.
>
> **Demo account — please use this to review.** Most of the app (market data, company
> detail screens, news, search, the Learn library, watchlist and portfolios) is usable
> signed out. However, **AI report generation and report chat require an account** because
> each run has a real per-use cost, so a reviewer signed out cannot exercise those features.
> Credentials are in the App Review sign-in fields; the account is pre-loaded with credits.
>
> **In-app purchases.** Two auto-renewable subscriptions (Pro, Max) and four consumable
> credit packs. Credits are consumed only inside the app for AI generation; they are not a
> currency, cannot be transferred or cashed out, and expire per the Terms.
>
> **Background modes.** `remote-notification` — opt-in push for price alerts, earnings and
> watchlist moves, all user-configurable in Settings and off until permission is granted.
> `audio` — narrated audio for the Learn library continues when the screen locks, with
> Lock Screen and Control Center transport controls. Neither runs background location or
> background fetch of user data.
>
> **Age rating 17+.** Our Terms require users to be 18 or the age of majority; 17+ is the
> closest available rating. The app contains no gambling, no unrestricted web access and no
> user-generated content visible to other users.
