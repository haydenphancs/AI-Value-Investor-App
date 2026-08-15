//
//  AppError.swift
//  ios
//
//  Unified Error Handling
//
//  Maps backend error codes and network errors to user-friendly messages.
//  Provides suggested actions for recovery.
//

import Foundation

// MARK: - App Error

/// Unified error type for the application.
/// Maps various error sources to user-actionable messages.
enum AppError: Error, Identifiable, Equatable, Sendable {
    // Network errors
    case noConnection
    case timeout
    case serverError(statusCode: Int)

    // Auth errors
    case unauthorized
    case tokenExpired
    case forbidden

    /// Not signed in, on something that needs an account. `feature` is the human phrase for what
    /// they were trying to do ("follow investors"), so the prompt reads as an invitation rather
    /// than an error. NOT a session failure — see `isAuthError`.
    case signInRequired(feature: String?)

    /// The session is definitively over (password changed elsewhere, or the account is gone).
    /// Carries the backend's copy so the user learns WHY rather than a generic "expired".
    case sessionEnded(message: String)

    /// Identity could not be verified right now (503 AUTH_UNAVAILABLE). Retryable, and
    /// explicitly not a reason to sign anybody out.
    case authUnavailable(message: String)

    /// The account exists and the password was right — the email address was never confirmed
    /// (403 EMAIL_NOT_CONFIRMED).
    ///
    /// Its own case because none of the existing ones fit. `.forbidden` discards the message
    /// and says "You don't have permission", which misdescribes it; `.validationFailed` titles
    /// it "Invalid Input" and points at a form field that isn't wrong; and the generic
    /// `.apiError` it used to fall into offers a retry button, when re-submitting the same
    /// credentials will fail identically until the user opens the email.
    ///
    /// NOT an auth error (`isAuthError` stays false): there is no session to refresh or clear,
    /// and treating it as one would sign out a user who never got signed in.
    case emailNotConfirmed(message: String)

    // Business errors
    case insufficientCredits(required: Int, available: Int)
    /// The caller's PLAN doesn't allow this action (WHALE_FOLLOW_LOCKED, HTTP 403).
    /// Deliberately not folded into `.insufficientCredits`: that case says "top up" and
    /// routes to Buy Credits, and no amount of credits unlocks a follow slot or a gated
    /// section. Same `.upgrade` action, different destination and different sentence.
    case planUpgradeRequired(message: String)
    case notFound(resource: String)
    case validationFailed(message: String)
    case rateLimited(retryAfter: Int)

    /// The App Store transaction belongs to a DIFFERENT Caydex account (409
    /// `PURCHASE_ALREADY_LINKED`). TERMINAL: ownership of a transaction never moves, so no
    /// amount of retrying can clear it.
    ///
    /// Its own case rather than `.forbidden` because "You don't have permission to perform this
    /// action" is materially wrong here — the user is not lacking a permission, their purchase
    /// is attached to another account and they need to sign in with it. And not `.apiError`,
    /// whose `suggestedAction` is `.retry`: this arrived as a retryable 503 SYSTEM_BUSY, which
    /// told the user to "reopen the app shortly" AND kept `StoreKitService` from calling
    /// `Transaction.finish()`, so Apple redelivered the same transaction on every launch
    /// forever, against a condition that can never resolve.
    case purchaseAlreadyLinked(message: String)

    /// The transaction's `appAccountToken` names a DIFFERENT Caydex account, and the server
    /// recorded nothing — nobody has been credited for it.
    ///
    /// Deliberately separate from `.purchaseAlreadyLinked`, because the two demand opposite
    /// handling and getting it wrong destroys a purchase. `.purchaseAlreadyLinked` means the
    /// transaction IS recorded, against someone else — terminal, so `StoreKitService` finishes
    /// it and Apple stops redelivering. This case means the grant was refused BEFORE anything
    /// was written, so finishing it would delete a purchase the user paid for with no
    /// redelivery left to repair it. The transaction must stay unfinished until the buying
    /// account signs in, at which point the same redelivery grants it.
    case purchaseAccountMismatch(message: String)

    /// Apple refunded or cancelled this purchase, so no credits were granted.
    ///
    /// The THIRD distinct answer to "this purchase isn't grantable", and it must stay distinct:
    /// `.purchaseAlreadyLinked` means somebody else was credited, `.purchaseAccountMismatch`
    /// means nobody was and the transaction must be KEPT, and this one means it can never be
    /// grantable by anyone. Terminal AND finishable — the backend used to answer a revoked
    /// purchase with `INVALID_INPUT`, which maps to `.validationFailed`, which
    /// `StoreKitService.handle` does not finish, so Apple redelivered it on every launch
    /// forever with a user-visible error.
    case purchaseRevoked(message: String)

    /// Apple's OWN signature check on the transaction failed, so it was never sent to us.
    ///
    /// The FOURTH answer to "this purchase isn't grantable", and the only one that never
    /// reaches the backend — the other three are decisions the server made. It exists because
    /// `.unknown` was what a caller actually saw: `StoreKitService.handle` reported an
    /// unverified transaction by returning `nil` without throwing, so every sweep produced
    /// `SweepResult{seen > 0, applied: 0, lastError: nil}` and fell back to
    /// "The purchase couldn't be applied. Please try again." Retrying cannot help — the blob
    /// is unverifiable and will fail identically forever — and because `record(_:op:)` was
    /// never reached, no analytics event was emitted either.
    ///
    /// Terminal but NOT finishable: an unverified transaction must never be `finish()`ed
    /// (Apple's guidance, and the file header's rule 2), so it stays in
    /// `Transaction.unfinished` and the sweeps re-report it. That is intentional — the
    /// alternative is discarding something we cannot prove is not a real purchase.
    case purchaseUnverified(message: String)

    /// No installed app can handle a URL we tried to open — `mailto:` with no mail client,
    /// an App Store link on a device without the App Store, and so on.
    ///
    /// Its own case rather than `.unknown`: this is a KNOWN failure mode with a specific,
    /// actionable message, and root invariant #3 is that a known failure must never land on
    /// generic copy. `.unknown` would say "An unexpected error occurred. Please try again."
    /// and offer `.retry` — but retrying is guaranteed to fail again, because nothing on the
    /// device can service the URL. `.contactSupport` is equally wrong when the link that
    /// failed *is* the contact-support link.
    ///
    /// This existed because every `UIApplication.shared.open` in the app discarded its
    /// result. `Profile → Help & Support` therefore did nothing at all on the Simulator,
    /// which ships no Mail app — no UI, no log, for the entire life of the file.
    case noAppToOpenURL(what: String)

    // API errors (from backend)
    case apiError(code: String, message: String)

    // Generic
    case unknown(message: String)

    var id: String {
        switch self {
        case .noConnection: return "no_connection"
        case .timeout: return "timeout"
        case .serverError(let code): return "server_\(code)"
        case .unauthorized: return "unauthorized"
        case .tokenExpired: return "token_expired"
        case .forbidden: return "forbidden"
        case .signInRequired(let f): return "sign_in_required_\(f ?? "generic")"
        case .sessionEnded: return "session_ended"
        case .authUnavailable: return "auth_unavailable"
        case .emailNotConfirmed: return "email_not_confirmed"
        case .insufficientCredits: return "insufficient_credits"
        case .planUpgradeRequired: return "plan_upgrade_required"
        case .notFound(let r): return "not_found_\(r)"
        case .validationFailed: return "validation"
        case .rateLimited: return "rate_limited"
        case .purchaseAlreadyLinked: return "purchase_already_linked"
        case .purchaseAccountMismatch: return "purchase_account_mismatch"
        case .purchaseRevoked: return "purchase_revoked"
        case .purchaseUnverified: return "purchase_unverified"
        case .noAppToOpenURL: return "no_app_to_open_url"
        case .apiError(let code, _): return "api_\(code)"
        case .unknown: return "unknown"
        }
    }

    // MARK: - User-Friendly Messages

    var title: String {
        switch self {
        case .noConnection:
            return "No Connection"
        case .timeout:
            return "Request Timeout"
        case .serverError:
            return "Server Error"
        case .unauthorized, .tokenExpired:
            return "Session Expired"
        case .forbidden:
            return "Access Denied"
        case .signInRequired:
            return "Sign In Required"
        case .sessionEnded:
            return "Signed Out"
        case .authUnavailable:
            return "Couldn't Verify Account"
        case .emailNotConfirmed:
            return "Confirm Your Email"
        case .insufficientCredits:
            return "Insufficient Credits"
        case .planUpgradeRequired:
            return "Upgrade Required"
        case .notFound:
            return "Not Found"
        case .validationFailed:
            return "Invalid Input"
        case .rateLimited:
            return "Too Many Requests"
        case .purchaseAlreadyLinked:
            return "Purchase Linked Elsewhere"
        case .purchaseAccountMismatch:
            return "Different Account"
        case .purchaseRevoked:
            return "Purchase Refunded"
        case .purchaseUnverified:
            return "Couldn’t Verify Purchase"
        case .noAppToOpenURL:
            return "Can’t Open That Here"
        case .apiError:
            return "Error"
        case .unknown:
            return "Something Went Wrong"
        }
    }

    var message: String {
        switch self {
        case .noConnection:
            return "Please check your internet connection and try again."
        case .timeout:
            return "The request took too long. Please try again."
        case .serverError:
            return "We're experiencing technical difficulties. Please try again later."
        case .unauthorized, .tokenExpired:
            return "Your session has expired. Please sign in again."
        case .forbidden:
            return "You don't have permission to perform this action."
        case .signInRequired(let feature):
            // Feature-specific copy when the call site supplied it; the generic line otherwise.
            if let feature, !feature.isEmpty {
                return "Sign in to \(feature)."
            }
            return "Sign in to use this feature."
        case .sessionEnded(let message):
            return message.isEmpty ? "Your session has ended. Please sign in again." : message
        case .authUnavailable(let message):
            return message.isEmpty
                ? "We couldn't verify your account just now. Please try again in a moment."
                : message
        case .emailNotConfirmed(let message):
            return message.isEmpty
                ? "Please confirm your email address, then sign in again. Check your inbox for the link."
                : message
        case .insufficientCredits(let required, let available):
            // required <= 0 → the amount/balance wasn't plumbed (the backend
            // INSUFFICIENT_CREDITS path carries no required/available on businessError):
            // show a generic upgrade message instead of a nonsensical "requires 0 credits".
            if required <= 0 {
                return "You don't have enough credits. Upgrade your tier or wait for the monthly reset."
            }
            return "This action requires \(required) credits, but you only have \(available). Upgrade your plan to get more."
        case .planUpgradeRequired(let message):
            // The backend's own copy verbatim — it knows WHICH limit was hit and stays
            // number-free so the plan table is the only place the numbers live.
            return message.isEmpty
                ? "Your plan doesn't include this. Upgrade to unlock it."
                : message
        case .notFound(let resource):
            return "The requested \(resource) could not be found."
        case .validationFailed(let msg):
            return msg
        case .purchaseAlreadyLinked(let message):
            return message
        case .purchaseAccountMismatch(let message):
            return message
        case .purchaseRevoked(let message):
            return message
        case .purchaseUnverified(let message):
            return message
        case .noAppToOpenURL(let what):
            return "This device has no app set up to open \(what)."
        case .rateLimited(let seconds):
            return "Please wait \(seconds) seconds before trying again."
        case .apiError(_, let msg):
            return msg
        case .unknown(let msg):
            return msg.isEmpty ? "An unexpected error occurred. Please try again." : msg
        }
    }

    // MARK: - Suggested Actions

    var suggestedAction: ErrorAction {
        switch self {
        case .noConnection:
            return .waitForConnection
        case .timeout, .serverError:
            return .retry
        case .unauthorized, .tokenExpired:
            return .signIn
        case .forbidden:
            return .goBack
        case .signInRequired, .sessionEnded:
            return .signIn
        case .authUnavailable:
            return .retry
        // `.retry` — the user acts OUTSIDE the app (opens the email), then comes back and
        // tries again. Not `.fixInput`: nothing on the form is wrong.
        case .emailNotConfirmed:
            return .retry
        case .insufficientCredits, .planUpgradeRequired:
            return .upgrade
        case .notFound:
            return .goBack
        case .validationFailed:
            return .fixInput
        case .rateLimited:
            return .waitAndRetry
        // NOT .retry — the condition can never clear, and a retry button is what kept the
        // StoreKit transaction alive across every launch.
        case .purchaseAlreadyLinked:
            return .contactSupport
        // `.signIn`, NOT `.contactSupport`: the purchase is intact and un-granted, and signing
        // in as the account that bought it is precisely the action that claims it.
        case .purchaseAccountMismatch:
            return .signIn
        // Terminal: a refund can never become grantable, so `.retry` would be a lie and is
        // exactly what left the transaction redelivering.
        case .purchaseRevoked:
            return .contactSupport
        // Also NOT .retry, for the same reason and a different cause: the signature check is
        // deterministic, so the next attempt fails identically. If money did change hands,
        // support is the only route to it.
        case .purchaseUnverified:
            return .contactSupport
        // NOT .retry: nothing on the device can service the URL, so a retry is guaranteed to
        // fail. NOT .contactSupport either — the link that failed may BE contact support.
        case .noAppToOpenURL:
            return .goBack
        case .apiError, .unknown:
            return .retry
        }
    }

    var isRetryable: Bool {
        switch self {
        case .timeout, .serverError, .rateLimited, .authUnavailable:
            return true
        default:
            return false
        }
    }

    /// True for the routine "we're just offline or signed out" failures that best-effort background
    /// sync (progress/bookmark `hydrate()`) is expected to swallow quietly. Everything else — a decode
    /// / contract drift (surfaces as `.unknown`), a 5xx, an unexpected code — should be LOGGED rather
    /// than silently hiding just-synced content, per the no-silent-swallow rule.
    var isExpectedOffline: Bool {
        switch self {
        // Auth failures used to be in this list, and that was the bug: all ten Learn-store
        // catch blocks are gated on `if !appError.isExpectedOffline`, so a 401/403 on any Learn
        // read or write produced NO output in ANY build configuration. An auth failure is not a
        // routine offline blip — it is exactly the thing worth knowing about — so only genuine
        // connectivity failures qualify now.
        case .noConnection, .timeout:
            return true
        default:
            return false
        }
    }

    /// A genuine authentication failure (the token/session is invalid), as opposed
    /// to a transient network/server error. Used at launch to decide whether to
    /// clear the stored token — a transient offline error must NOT sign the user out.
    var isAuthError: Bool {
        switch self {
        case .unauthorized, .tokenExpired, .sessionEnded:
            return true

        // `.forbidden` was here, and it was dangerous. This property has exactly two callers,
        // both in `restoreAuthState`, and one of them clears BOTH Keychain tokens and discards
        // the device's Learn data. While a missing credential produced 403, that meant an
        // ordinary permission wall at launch could destroy a perfectly valid session. 403 now
        // means only "authenticated but not allowed" (AUTH_FORBIDDEN, EMAIL_NOT_CONFIRMED),
        // which re-authenticating cannot fix, so it must not touch the session.
        //
        // `.emailNotConfirmed` (the typed case EMAIL_NOT_CONFIRMED now maps to) falls to the
        // `default` below for that same reason, and one more: there is no session yet to
        // destroy — the user never got signed in.
        //
        // `.signInRequired` is excluded for the mirror-image reason: no credential was sent, so
        // there is nothing wrong with any credential we hold. It is the SELF-HEAL signal — if a
        // Keychain token exists while we are getting this, the client is running tokenless and
        // should restore, not sign out.
        //
        // `.authUnavailable` is excluded because it is transient by definition.
        default:
            return false
        }
    }

    // MARK: - Factory

    /// Create AppError from any Error
    static func from(_ error: Error) -> AppError {
        // Already an AppError
        if let appError = error as? AppError {
            return appError
        }

        // URLSession errors
        if let urlError = error as? URLError {
            switch urlError.code {
            case .notConnectedToInternet, .networkConnectionLost:
                return .noConnection
            case .timedOut:
                return .timeout
            case .cannotConnectToHost, .cannotFindHost:
                return .serverError(statusCode: 0)
            default:
                return .unknown(message: urlError.localizedDescription)
            }
        }

        // API response errors
        if let apiError = error as? APIError {
            return mapAPIError(apiError)
        }

        // StoreKit-layer failures, which never reach the backend at all. Same shape as the
        // `APIError` mapping above: the service throws the low-level typed error, the
        // user-facing case is decided here.
        if let storeKitError = error as? StoreKitError {
            switch storeKitError {
            case .unverified:
                return .purchaseUnverified(
                    message: "Apple couldn’t verify this purchase, so nothing was added to "
                        + "your account. If you were charged, please contact support."
                )
            }
        }

        return .unknown(message: error.localizedDescription)
    }

    private static func mapAPIError(_ error: APIError) -> AppError {
        switch error {
        case .unauthorized:
            return .unauthorized
        case .forbidden:
            return .forbidden

        // Client-side pre-flight: a `.signInRequired` endpoint called with no token. The request
        // was never made, so there is no backend copy to surface — the call site supplies the
        // feature phrase via `AppState.requestSignIn(for:)`.
        case .authRequired:
            return .signInRequired(feature: nil)

        // The six structured auth codes. Each maps to a case whose `isAuthError` /
        // `suggestedAction` semantics match what the client must actually DO — that mapping is
        // the whole point of having distinct codes rather than one AUTH_FAILED.
        case .authError(let code, let message):
            switch code {
            case "AUTH_REQUIRED":
                return .signInRequired(feature: nil)
            case "AUTH_TOKEN_INVALID":
                // The refresh already ran and failed by the time this surfaces (the interceptor
                // owns the retry), so the session really is over.
                return .tokenExpired
            case "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND":
                return .sessionEnded(message: message)
            case "AUTH_UNAVAILABLE":
                return .authUnavailable(message: message)
            case "AUTH_CREDENTIALS_INVALID":
                // What they TYPED was wrong — the session, if any, is untouched. `.validationFailed`
                // carries the server's own wording, is excluded from `isAuthError` (so nothing
                // clears the Keychain) and suggests fixing the field rather than signing in.
                //
                // Before this code existed the backend sent a bare-string 401, which decodes to
                // `.unauthorized` → "Your session has expired. Please sign in again." So a user
                // mistyping their current password was told their session had ended, and because
                // `.unauthorized` triggers a refresh while `.changePassword` is not an auth
                // endpoint, the client refreshed and REPLAYED the request — spending two of the
                // five per-user attempts on one typo.
                return .validationFailed(message: message)
            case "AUTH_PROVIDER_FAILED":
                // Apple/Google identity verification failed. Retryable and non-destructive; the
                // copy must never mention a password, because that flow has none — the old
                // `.unauthorized` fallback sent these users to reset a password they may not have.
                return .authUnavailable(message: message)
            default:
                // An AUTH_* code this build doesn't know. Fail NON-destructively.
                //
                // This used to return `.sessionEnded`, which is a member of `isAuthError`, which
                // is what clears the Keychain in `performRestore`. So shipping a seventh auth
                // code — AUTH_RATE_LIMITED, AUTH_MFA_REQUIRED, anything — would have signed out
                // every user still on the previous build, permanently, the moment the backend
                // first emitted it. Exactly three codes are allowed to destroy a credential
                // (`.claude/rules/auth.md` §3); this arm silently widened that to "and anything
                // new". `.authUnavailable` keeps the credential and keeps retrying, which
                // degrades gracefully and self-corrects when the user updates.
                return .authUnavailable(message: message)
            }
        case .notFound:
            return .notFound(resource: "item")
        case .serverError(let code):
            return .serverError(statusCode: code)
        case .rateLimited(let retryAfter):
            return .rateLimited(retryAfter: retryAfter)
        case .businessError(let code, let message):
            // Map backend error codes. The backend emits the flat code INSUFFICIENT_CREDITS
            // (HTTP 402). required/available aren't carried on businessError, so this uses the
            // generic-upgrade branch of .insufficientCredits (required==0 → no "requires 0
            // credits" copy) while still giving the "Insufficient Credits" title + "Upgrade"
            // action instead of a generic .apiError.
            if code == "INSUFFICIENT_CREDITS" {
                return .insufficientCredits(required: 0, available: 0)
            }
            // A PLAN gate (403), not a balance one. Kept distinct from the credits branch
            // above because the two send the user to different places: credits → Buy
            // Credits, this → the plan sheet. Buying credits never unlocks a follow slot.
            if code == "WHALE_FOLLOW_LOCKED" {
                return .planUpgradeRequired(message: message)
            }
            // Capacity / back-pressure codes (HTTP 409). SYSTEM_BUSY = the whole
            // service is at capacity; TOO_MANY_CONCURRENT_REPORTS = this user's
            // own cap. Both carry a specific backend user_message and want a
            // retry — surface that message verbatim via .apiError (whose
            // suggestedAction is .retry) rather than a generic fallback.
            if code == "SYSTEM_BUSY" || code == "TOO_MANY_CONCURRENT_REPORTS" {
                return .apiError(code: code, message: message)
            }
            // The App Store transaction belongs to a DIFFERENT Caydex account (HTTP 409).
            // TERMINAL — ownership never moves, so retrying can never succeed. Deliberately
            // NOT `.apiError`, whose `suggestedAction` is `.retry`: this used to arrive as a
            // 503 SYSTEM_BUSY telling the user to "reopen the app shortly", which also meant
            // `StoreKitService` never called `Transaction.finish()` and Apple redelivered the
            // same transaction on every launch, forever.
            if code == "PURCHASE_ALREADY_LINKED" {
                return .purchaseAlreadyLinked(message: message)
            }
            // Separate case, separate handling: nothing was recorded server-side, so
            // `StoreKitService` must NOT finish this transaction. Finishing it deletes a
            // purchase the user paid for. See `AppError.purchaseAccountMismatch`.
            if code == "PURCHASE_ACCOUNT_MISMATCH" {
                return .purchaseAccountMismatch(message: message)
            }
            // Apple refunded it. Terminal AND finishable — the backend used to answer this
            // with INVALID_INPUT, which maps to `.validationFailed`, which `handle` does not
            // finish, so the transaction was redelivered on every launch forever.
            if code == "PURCHASE_REVOKED" {
                return .purchaseRevoked(message: message)
            }
            // A theme card tapped after it was deleted → a typed .notFound rather
            // than a generic .apiError (per the "don't fall through" rule).
            if code == "THEME_NOT_FOUND" {
                return .notFound(resource: "theme")
            }
            // Chat message over the length ceiling (HTTP 400) → a typed validation
            // error (title "Invalid Input", .fixInput action) surfacing the backend's
            // specific message, per the "don't fall through to a generic" rule.
            if code == "CHAT_MESSAGE_TOO_LONG" {
                return .validationFailed(message: message)
            }
            // Per-user daily chat budget exhausted (HTTP 409). Surface the backend
            // user_message verbatim (matches the SYSTEM_BUSY capacity handling above).
            if code == "CHAT_DAILY_LIMIT_REACHED" {
                return .apiError(code: code, message: message)
            }
            // The holdings datastore was transiently unreadable (HTTP 503). This
            // is deliberately NOT an empty feed: the Assets tab purges portfolio
            // tickers that are missing from the feed, so the client must be able
            // to tell "couldn't read" from "you have nothing" or it deletes the
            // user's portfolios. Surface the backend's retry copy verbatim.
            if code == "WATCHLIST_UNAVAILABLE" {
                return .apiError(code: code, message: message)
            }
            // 503 NOTIFICATIONS_UNAVAILABLE: the inbox (or the price-alert list) could not
            // be READ. Same hazard as WATCHLIST_UNAVAILABLE and for the same reason it is
            // not an empty 200 — "you have no notifications" and "we couldn't reach them"
            // are indistinguishable in the UI, and rendering the second as the first is a
            // failure nobody reports. Surface the backend's retry copy verbatim.
            if code == "NOTIFICATIONS_UNAVAILABLE" {
                return .apiError(code: code, message: message)
            }
            // 503 SETTINGS_UNAVAILABLE: the synced preference blob could not be READ. The
            // third member of the same family, and it was the one missing a branch. Same
            // contract: it must never look like "you have no settings", because
            // `SettingsSyncManager` gates its FULL-REPLACE push on "the server state is
            // known" — an empty 200 there opens that gate on a session whose blob was never
            // seen and the next push overwrites ~23 keys with whatever this device holds.
            if code == "SETTINGS_UNAVAILABLE" {
                return .apiError(code: code, message: message)
            }
            // 500 ACCOUNT_DELETE_INCOMPLETE: the deletion got part way and STOPPED, and the
            // auth row was deliberately kept so a retry can reach the rest — which means the
            // account still exists. That is the one thing the user has to be told, and it is
            // exactly what a generic `.serverError` does not say.
            //
            // This arm is reachable at all only because the backend now sends the structured
            // body: `validateResponse`'s 5xx branch decodes `APIErrorResponse` but has NO
            // `{"detail": ...}` fallback (only the 4xx branch does), so the previous
            // bare-string `HTTPException` was thrown away and rendered as a transient
            // "something went wrong" for an operation that is neither transient nor complete.
            if code == "ACCOUNT_DELETE_INCOMPLETE" {
                return .apiError(code: code, message: message)
            }
            // 409 PRICE_ALERT_LIMIT_REACHED: the request was well-formed and the ACCOUNT is
            // the conflict. `.validationFailed` gets `.fixInput`, which is right — the user
            // must delete an alert — where a retry button could never succeed.
            if code == "PRICE_ALERT_LIMIT_REACHED" {
                return .validationFailed(message: message)
            }
            // 404 PRICE_ALERT_NOT_FOUND: deleted on another device, or never theirs.
            // `.notFound` renders "goBack" rather than a retry that cannot help.
            if code == "PRICE_ALERT_NOT_FOUND" {
                return .notFound(resource: "price alert")
            }
            // 403 AUTH_FORBIDDEN: authenticated, just not permitted. A typed `.forbidden` rather
            // than a generic `.apiError` so it gets "Access Denied" + `.goBack` instead of a
            // retry button that can never succeed — and, critically, so it is NOT an auth error
            // (re-authenticating cannot grant permission, and must not cost the session).
            if code == "AUTH_FORBIDDEN" {
                return .forbidden
            }
            // 503 AUTH_UNAVAILABLE can also arrive on the businessError path (the default arm of
            // validateResponse handles non-401 statuses).
            if code == "AUTH_UNAVAILABLE" {
                return .authUnavailable(message: message)
            }
            // 403 EMAIL_NOT_CONFIRMED. `validateResponse`'s 403 comment has always CLAIMED this
            // routes to `.forbidden`, but no branch existed, so it fell through to `.apiError`:
            // title "Error", action `.retry`. `.forbidden` would be wrong too — it discards the
            // backend's message and says "You don't have permission", which is not the problem.
            // It has its own case because the required action ("go confirm your email") matches
            // none of the existing ones.
            if code == "EMAIL_NOT_CONFIRMED" {
                return .emailNotConfirmed(message: message)
            }

            // ── Codes that used to fall through to the generic branch below ──────────────
            //
            // `.apiError` is not silent — it surfaces the backend `user_message` verbatim and
            // its analyticsKey is `api_<CODE>`. What it gets wrong is the TITLE ("Error") and
            // the ACTION: it is unconditionally `.retry`, which is wrong for roughly half of
            // these. Each branch below exists to fix the action.

            // 404s. Retrying cannot conjure the resource; `.notFound` renders `.goBack`.
            if code == "TICKER_NOT_FOUND" {
                return .notFound(resource: "ticker")
            }
            if code == "REPORT_NOT_FOUND" {
                return .notFound(resource: "report")
            }
            // 422s. The caller sent something the backend won't accept — `.fixInput`, not a
            // retry of the identical request.
            if code == "INVALID_INPUT" || code == "INVALID_PERSONA" {
                return .validationFailed(message: message)
            }
            // Upstream quota. `.waitAndRetry` instead of an immediate retry that will just
            // burn another rejected call. `retryAfter` cannot come from the payload — APIClient
            // drops `details` when it builds `.businessError` — so 60s is the standing default,
            // matching the 429 branch of `validateResponse`.
            if code == "FMP_RATE_LIMITED" || code == "GEMINI_QUOTA_EXCEEDED" {
                return .rateLimited(retryAfter: 60)
            }
            // Upstream down. Honestly a server-side failure, and `.serverError` already says
            // "we're experiencing technical difficulties" with a retry — which IS right here.
            if code == "FMP_UNAVAILABLE" || code == "GEMINI_UNAVAILABLE" {
                return .serverError(statusCode: 503)
            }
            // Deliberately `.apiError`, listed rather than left to fall through: for these
            // three the backend's own `user_message` is the best copy available AND `.retry`
            // is genuinely the right action (the report pipeline is worth another attempt,
            // and REPORT_NOT_READY resolves by asking again). Naming them makes that a
            // decision a reader can check, not an accident of ordering.
            if code == "DATA_INCOMPLETE"
                || code == "REPORT_GENERATION_FAILED"
                || code == "REPORT_NOT_READY" {
                return .apiError(code: code, message: message)
            }
            return .apiError(code: code, message: message)
        case .decodingError:
            return .unknown(message: "Failed to process server response")
        case .networkError(let underlying):
            return .from(underlying)
        case .unknown(let message):
            return .unknown(message: message)
        }
    }
}

// MARK: - Error Action

enum ErrorAction: Sendable {
    case retry
    case waitAndRetry
    case waitForConnection
    case signIn
    case goBack
    case fixInput
    case upgrade
    case contactSupport

    var buttonTitle: String {
        switch self {
        case .retry:
            return "Try Again"
        case .waitAndRetry:
            return "Wait"
        case .waitForConnection:
            return "OK"
        case .signIn:
            return "Sign In"
        case .goBack:
            return "Go Back"
        case .fixInput:
            return "OK"
        case .upgrade:
            return "Upgrade"
        case .contactSupport:
            return "Contact Support"
        }
    }
}

// MARK: - API Error (Network Layer)

/// Low-level API errors before mapping to AppError
/// @unchecked Sendable because Error types may not conform but are used safely
enum APIError: Error, @unchecked Sendable {
    case unauthorized
    case forbidden
    case notFound
    case serverError(statusCode: Int)
    case rateLimited(retryAfter: Int)
    case businessError(code: String, message: String)
    case decodingError(Error)
    case networkError(Error)
    case unknown(message: String)

    /// Raised by `APIClient` BEFORE the request goes out, when a `.signInRequired` endpoint is
    /// called with no token. Distinct from `.unauthorized` on purpose: nothing is wrong with the
    /// session (there isn't one), so this must never trigger a refresh or touch the Keychain.
    case authRequired

    /// A 401 that carried the structured `{error_code, ...}` body — one of the `AUTH_*` codes.
    /// Separate from `.businessError` so auth failures keep their identity instead of being
    /// indistinguishable from a 404 or a credits error.
    case authError(code: String, message: String)
}

extension APIError {
    /// Whether this failure is worth spending a token refresh on.
    ///
    /// ⚠️ This predicate exists because of a trap. The refresh-and-retry interceptor is written
    /// three times over — `request<T>`, `request` (void) and `downloadData` — and each used to
    /// pattern-match `if case .unauthorized = error`. The moment a 401 started decoding into
    /// `.authError` instead, those matches would stop firing and **token refresh would silently
    /// die app-wide**: every user signed out after 24 hours with no failing test to show it.
    /// Every interceptor now asks this instead, so the set of refreshable failures is defined
    /// once.
    ///
    /// `AUTH_REQUIRED` is deliberately excluded — no credential was sent, so there is nothing to
    /// refresh. `AUTH_ACCOUNT_NOT_FOUND` too: the account is gone and the refresh will fail for
    /// the same reason. Both are handled by the sign-in affordance instead.
    var triggersTokenRefresh: Bool {
        switch self {
        case .unauthorized:
            return true
        case .authError(let code, _):
            return code == "AUTH_TOKEN_INVALID" || code == "AUTH_SESSION_EXPIRED"
        default:
            return false
        }
    }

    /// True when the failure means "you are not signed in", as opposed to "your session broke".
    /// Drives the Sign In Required affordance and must never cost the user a stored credential.
    var isSignInRequired: Bool {
        switch self {
        case .authRequired:
            return true
        case .authError(let code, _):
            return code == "AUTH_REQUIRED"
        default:
            return false
        }
    }
}

// MARK: - Analytics

extension AppError {
    /// A stable, low-cardinality code for analytics dimensions.
    ///
    /// Deliberately NOT `message`: user-facing copy is long, changes with wording
    /// tweaks (silently splitting a metric in two), and — for `.apiError` — can carry
    /// backend-supplied text. Analytics props are for dimensions, never free text.
    var analyticsCode: String {
        switch self {
        case .noConnection:        return "no_connection"
        case .timeout:             return "timeout"
        case .serverError:         return "server_error"
        case .unauthorized:        return "unauthorized"
        case .tokenExpired:        return "token_expired"
        case .forbidden:           return "forbidden"
        case .signInRequired:      return "sign_in_required"
        case .sessionEnded:        return "session_ended"
        case .authUnavailable:     return "auth_unavailable"
        case .emailNotConfirmed:   return "email_not_confirmed"
        case .insufficientCredits: return "insufficient_credits"
        case .planUpgradeRequired: return "plan_upgrade_required"
        case .notFound:            return "not_found"
        case .validationFailed:    return "validation_failed"
        case .rateLimited:         return "rate_limited"
        case .purchaseAlreadyLinked: return "purchase_already_linked"
        case .purchaseAccountMismatch: return "purchase_account_mismatch"
        case .purchaseRevoked:        return "purchase_revoked"
        case .purchaseUnverified:     return "purchase_unverified"
        case .noAppToOpenURL:      return "no_app_to_open_url"
        // The backend's ErrorCode enum is already a stable machine-readable
        // vocabulary, so it is safe as a dimension. The message is not.
        case .apiError(let code, _): return code
        case .unknown:             return "unknown"
        }
    }
}
