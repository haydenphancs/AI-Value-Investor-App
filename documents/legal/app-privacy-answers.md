# App Store Connect — App Privacy answer sheet

Read this straight into the App Privacy questionnaire in App Store Connect
(App → App Privacy → Get Started). Every answer below is derived from a codebase audit,
not a guess; the "evidence" column says where it comes from so you can re-verify.

Keep in sync with `frontend/ios/ios/PrivacyInfo.xcprivacy` — the manifest and this
questionnaire must agree, and Apple compares them.

**Status: complete for the current build.** One item changes when IAP ships — see
§6 "Revisit when".

---

## 1. First question: "Do you or your third-party partners collect data from this app?"

**Yes.**

## 2. Second question: "Do you use data for tracking?"

**No.**

Evidence: no `IDFA` / `ASIdentifierManager` / `AppTrackingTransparency` anywhere; no
advertising or analytics SDK — `Package.resolved` has exactly one pin (sentry-cocoa). No
data is shared with data brokers, and nothing is linked with third-party data for
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
| Usage Data → **Product Interaction** | Yes | App Functionality | Watchlist contents, lesson/book completion, bookmarks, followed entities |
| Diagnostics → **Crash Data** | **No** | App Functionality | Sentry. `sendDefaultPii = false` and `SentrySDK.setUser` is never called, so crash reports carry no identity |

Note on the non-obvious one:

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
Other Diagnostic Data · Purchases

Notes on the non-obvious ones:

- **Payment Info** — Apple handles purchases; the app never sees card details.
- **Performance Data** — Sentry runs with `tracesSampleRate = 0.0`, so none is transmitted.
  If you ever raise that value, add Performance Data here *and* to the manifest.
- **Search History** — the in-app search is ticker/entity lookup and is not stored per user.
- **Browsing History** — no `WKWebView` and no URL history collection.
- **Purchases** — nothing to declare yet; see §6.

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

It removes the account row, every FK-linked table, the four tables that have no FK
(`user_learn_progress`, `user_book_progress`, `chat_usage_budget`, `credit_transactions`),
and the user's generated report PDFs in Storage. If a reviewer asks what survives: only
error-monitoring records at Sentry, for that provider's retention period, containing
diagnostic data and a pseudonymous account identifier — no name or email.

---

## 6. Revisit when

- **IAP ships (plan Phase 8)** → add **Purchases → Purchase History**
  (Linked: Yes, Tracking: No, Purpose: App Functionality) here **and** to
  `PrivacyInfo.xcprivacy`. The manifest has a comment marking this.
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
> client funds, connects to no brokerage, and executes no trades. All portfolio figures are
> self-entered by the user for an educational diversification score. Analysis is AI-generated
> and labelled as such throughout, with "not financial advice" disclaimers on every analysis
> surface and a first-run acknowledgement. See Profile → About & Legal → Disclaimers.
>
> No login is required — the app is fully usable as a guest, so no demo account is needed.
