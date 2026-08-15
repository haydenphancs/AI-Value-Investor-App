//
//  StoreKitService.swift
//  ios
//
//  StoreKit 2 purchase flow for the Pro / Max subscriptions.
//
//  The client's job is deliberately narrow: present products, run the purchase sheet, and
//  hand Apple's SIGNED transaction to our backend. It never decides what the user is
//  entitled to — `POST /billing/verify` verifies the signature against Apple's certificate
//  chain and derives the tier from the verified payload. So a compromised client can at
//  worst submit a receipt that fails verification.
//
//  Three things here are easy to get wrong and are the reason this is a service rather than
//  view code:
//
//  1. `Transaction.updates` must be observed from app launch, not from when the paywall
//     opens. Apple delivers renewals, Ask-to-Buy approvals, and purchases completed while
//     the app was closed through that stream. Miss it and a renewal silently never applies.
//
//  2. A transaction must be `finish()`ed ONLY after the backend has recorded it. Finishing
//     early tells Apple "handled", and if our call failed the entitlement is lost with no
//     redelivery. Leaving it unfinished is the safe failure: Apple redelivers.
//
//  3. `Product.SubscriptionInfo.RenewalState` is NOT the entitlement source of truth here —
//     the backend is. Local state is only used to decide whether to bother calling.
//

import Combine
import Foundation
import StoreKit

/// Failures originating in the StoreKit layer itself, before the backend is involved.
///
/// Its own type rather than an `AppError`, mirroring how `APIClient` throws `APIError` and lets
/// `AppError.from(_:)` decide the user-facing case. It also keeps Apple's underlying
/// `VerificationResult.VerificationError` attached, which is the entire diagnostic — the
/// user-facing string deliberately does not carry it.
enum StoreKitError: Error {
    /// Apple's own signature check on the transaction failed.
    ///
    /// Thrown rather than returned as `nil`, because a `nil` return is invisible to the three
    /// sweeps that call `handle` in a loop: they record failures from `catch`, so a
    /// non-throwing failure produced `seen > 0, applied: 0, lastError: nil` — an "everything
    /// failed" result carrying no error and emitting no analytics.
    case unverified(underlying: Error)
}

extension Notification.Name {
    /// Posted after the backend has RECORDED a verified transaction, so the app can adopt the
    /// new tier and credit balance without waiting for a relaunch. Fired for interactive
    /// purchases and for `Transaction.updates` replays (restores, Ask-to-Buy, renewals) alike.
    static let caydexEntitlementChanged = Notification.Name("caydexEntitlementChanged")
}

@MainActor
final class StoreKitService: ObservableObject {
    static let shared = StoreKitService()

    /// Product identifiers. MUST match App Store Connect and the backend's
    /// `IAP_PRODUCT_*` settings exactly — a mismatch verifies fine and then fails to map
    /// to a plan, which is the confusing failure of "payment taken, nothing granted".
    enum ProductID {
        static let proMonthly = "com.phan.caydex.pro.monthly"
        static let maxMonthly = "com.phan.caydex.max.monthly"

        /// CONSUMABLE credit packs. The prefix matters: the backend routes a verified
        /// transaction to the credit path by product-id prefix (`IAP_CREDIT_PACK_PREFIX`),
        /// so a pack whose id does not start with `com.phan.caydex.credits.` is diagnosed as
        /// an unmapped SUBSCRIPTION and refused.
        static let creditsStarter = "com.phan.caydex.credits.starter"
        static let creditsPlus = "com.phan.caydex.credits.plus"
        static let creditsPower = "com.phan.caydex.credits.power"
        static let creditsMega = "com.phan.caydex.credits.mega"

        static let subscriptions: [String] = [proMonthly, maxMonthly]
        static let creditPacks: [String] = [creditsStarter, creditsPlus, creditsPower, creditsMega]
        /// One `Product.products(for:)` round-trip loads both families.
        static let all: [String] = subscriptions + creditPacks
    }

    enum PurchaseOutcome: Sendable, Equatable {
        case success(PurchaseApplied)
        /// The user dismissed the sheet. Not an error; never surfaced as one.
        case cancelled
        /// Ask to Buy / Screen Time — Apple will deliver later via `Transaction.updates`.
        case pending
        /// Apple's OWN signature check on the transaction failed, so it was never forwarded.
        ///
        /// Deliberately not `.pending`: nothing is coming later. This used to be laundered
        /// into `.pending` under a comment claiming "the backend accepted it but reported
        /// nothing usable" — the backend was never called — and the user was shown
        /// "Waiting for approval… it will be applied automatically" for a purchase that will
        /// never complete. Narrow (a tampered device, or a broken local certificate state)
        /// but the classification was simply false.
        case unverified
    }

    @Published private(set) var products: [Product] = []
    @Published private(set) var isLoadingProducts = false
    /// True while ANY purchase is in flight. Drives the one-at-a-time guard: every plan button
    /// disables, because two concurrent StoreKit sheets is not a state worth supporting.
    @Published private(set) var isPurchasing = false

    /// WHICH product is being purchased, or nil. Distinct from `isPurchasing` on purpose.
    ///
    /// The paywall renders one CTA per plan and drove the label from `isPurchasing` alone, so
    /// tapping "Choose Pro" made BOTH Pro and Max read "Processing…" — the app claiming to be
    /// buying two subscriptions at once, on the screen where a user is deciding what to pay
    /// for. Disabling every button is right; captioning every button is not.
    @Published private(set) var purchasingProductID: String?
    /// Non-nil when product loading failed, so the paywall can say so rather than showing
    /// an empty list that looks like "no plans available".
    @Published private(set) var productLoadError: String?

    private var updatesTask: Task<Void, Never>?

    private init() {}

    // MARK: - Lifecycle

    /// Start observing `Transaction.updates`. Call once, at app launch.
    ///
    /// Idempotent: calling again is a no-op rather than starting a second listener, which
    /// would double-submit every transaction.
    func startObservingTransactions() {
        guard updatesTask == nil else { return }
        updatesTask = Task.detached { [weak self] in
            for await update in Transaction.updates {
                // Swallow per-transaction failures deliberately: throwing out of this loop
                // would END the listener, so one backend hiccup would stop every FUTURE
                // renewal from ever being applied. The transaction is left unfinished, so
                // Apple redelivers it on the next launch.
                do {
                    try await self?.handle(verificationResult: update, origin: "updates")
                } catch {
                    // Release-VISIBLE, not just a DEBUG print. `.claude/rules/auth.md` §6 bans
                    // `#if DEBUG`-only reporting on a path that moves money, and this listener
                    // is where renewals, Ask-to-Buy approvals and redeliveries land — the
                    // failures nobody is watching for. `Analytics` is an actor with a
                    // `nonisolated` `track`, so this is safe from the detached task.
                    Analytics.shared.track(.backgroundSyncFailed, [
                        "op": .string("iap_updates"),
                        "code": .string(AppError.from(error).analyticsCode),
                    ])
                    #if DEBUG
                    print("🔴 [StoreKit] updates listener: \(error) — left unfinished for redelivery")
                    #endif
                }
            }
        }
    }

    func stopObservingTransactions() {
        updatesTask?.cancel()
        updatesTask = nil
    }

    // MARK: - Products

    /// Load the App Store products.
    ///
    /// `extraProductIDs` lets a caller add ids that came from the BACKEND catalog. The pack
    /// list is a DB table, so a pack added server-side is rendered by `BuyCreditsView`
    /// immediately — but if we only ever ask Apple for the four hardcoded ids, that new pack
    /// has no `Product`: its price falls back to the USD-only catalog string and its Buy button
    /// dead-ends on "That pack isn't available right now". Asking for the union keeps the two
    /// catalogs from drifting.
    func loadProducts(extraProductIDs: [String] = []) async {
        isLoadingProducts = true
        productLoadError = nil
        do {
            let requested = Array(Set(ProductID.all + extraProductIDs))
            let loaded = try await Product.products(for: requested)
            // Cheapest first so the paywall order is stable regardless of what StoreKit
            // returns; `Product.products(for:)` does not guarantee request order.
            products = loaded.sorted { $0.price < $1.price }
            if products.isEmpty {
                // Distinguish "Apple returned nothing" from "we haven't asked yet". Usually
                // means the products aren't configured, or no StoreKit config in DEBUG.
                productLoadError = "No purchase options are available right now."
            }
        } catch {
            productLoadError = "Couldn't load purchase options. Please try again."
            #if DEBUG
            print("🔴 [StoreKit] product load failed: \(error)")
            #endif
        }
        isLoadingProducts = false
    }

    /// Tier → App Store product id. Pure mapping, independent of whether products have loaded.
    ///
    /// Split out from `product(for:)` so the paywall can compare against `purchasingProductID`
    /// without depending on `products` being populated — the mapping is what identifies a plan,
    /// the loaded `Product` is just the thing you buy with.
    func productID(for tier: String) -> String? {
        switch tier.lowercased() {
        case "pro": return ProductID.proMonthly
        case "premium", "max": return ProductID.maxMonthly
        default: return nil
        }
    }

    func product(for tier: String) -> Product? {
        guard let id = productID(for: tier) else { return nil }
        return products.first { $0.id == id }
    }

    /// The loaded consumable products, keyed by id, for the Buy Credits screen.
    ///
    /// A filtered view rather than a second `@Published` array: one source of truth for what
    /// Apple returned means the two screens cannot disagree about which products exist.
    func creditPackProduct(id: String) -> Product? {
        products.first { $0.id == id }
    }

    // MARK: - Purchase

    /// Run the purchase sheet and, on success, hand the signed transaction to the backend.
    ///
    /// Throws only for genuine failures. Cancellation returns `.cancelled` because a user
    /// tapping "Cancel" is not an error and must not raise an alert.
    ///
    /// `accountID` is the signed-in user's id, stamped onto the purchase as StoreKit's
    /// `appAccountToken` and returned by Apple INSIDE the signed payload. It is the only thing
    /// that can catch a first delivery landing in the wrong session: user A buys, the verify
    /// call fails so the transaction stays unfinished, A signs out, B signs in on the same
    /// device, and `Transaction.updates` redelivers into B's session. There is no prior
    /// purchase row for the server's dedup check to catch that, so without the token B is
    /// credited for what A paid. The caller supplies it because this service deliberately
    /// holds no `AppState` reference.
    func purchase(_ product: Product, accountID: String? = nil) async throws -> PurchaseOutcome {
        isPurchasing = true
        purchasingProductID = product.id
        defer {
            isPurchasing = false
            purchasingProductID = nil
        }

        var options: Set<Product.PurchaseOption> = []
        // A non-UUID id (or none) simply omits the option — Apple requires a UUID here, and a
        // purchase that cannot carry the token must still be allowed to complete. The server
        // treats an absent token as "unproven but permitted" and logs it.
        if let accountID, let token = UUID(uuidString: accountID) {
            options.insert(.appAccountToken(token))
        }

        // `product.purchase()` presents Apple's payment sheet, and a failure to PRESENT it
        // throws here rather than returning a `.userCancelled` / `.pending` case. Those
        // failures arrive as opaque `NSError`s from Apple's own daemons — `ASDErrorDomain`
        // (appstored) and `AMSErrorDomain` (AppleMediaServices) — whose `localizedDescription`
        // is only "The operation couldn't be completed", so the domain + code are the entire
        // diagnostic. Logging them here is what makes a presentation failure legible; without
        // it the error travelled silently to `AppError.unknown` and, because the paywall only
        // rendered `errorMessage` while the catalog was absent, showed the user nothing at all.
        let result: Product.PurchaseResult
        do {
            result = try await product.purchase(options: options)
        } catch {
            #if DEBUG
            let ns = error as NSError
            print("🔴 [StoreKit] purchase sheet failed for \(product.id): "
                  + "\(ns.domain) code=\(ns.code) — \(ns.localizedDescription)")
            if !ns.userInfo.isEmpty { print("🔴 [StoreKit] userInfo: \(ns.userInfo)") }
            #endif
            throw error
        }

        switch result {
        case .success(let verification):
            // Checked HERE rather than by catching `StoreKitError.unverified` from `handle`,
            // because this is the one caller that must not treat it as an error to report:
            // `.unverified` is a `PurchaseOutcome` the sheet renders with its own copy.
            // Folding it into `.pending` told the user to wait for something never coming.
            guard case .verified = verification else { return .unverified }
            let applied = try await handle(verificationResult: verification, origin: "purchase")
            return .success(applied)

        case .userCancelled:
            return .cancelled

        case .pending:
            // Ask to Buy, or SCA. Apple delivers the transaction later through
            // `Transaction.updates`, which is why that listener has to be running.
            return .pending

        @unknown default:
            // A future StoreKit case. Pending is the honest answer: we don't know that it
            // succeeded, and Apple will redeliver if it did.
            return .pending
        }
    }

    /// Re-submit whatever Apple currently considers entitled.
    ///
    /// Required by App Review 3.1.1 ("you should make sure you have a restore mechanism").
    /// Returns the number of entitlements submitted, so the UI can distinguish "restored
    /// your subscription" from "nothing to restore" — the latter is the common case for
    /// someone who never subscribed, and showing a generic success there is confusing.
    @discardableResult
    func restorePurchases() async -> SweepResult {
        var result = SweepResult()
        // `currentEntitlements` is the source for restore: it holds only ACTIVE
        // entitlements, unlike `Transaction.all` which includes expired history.
        for await entitlement in Transaction.currentEntitlements {
            result.seen += 1
            // `catch`, not `try?`. Swallowing here is what made "Restore Purchases" answer
            // "No previous purchases found for this Apple Account" to a user whose
            // entitlements Apple DID return but whose every submission failed — telling
            // someone with an active subscription that they never bought anything.
            do {
                _ = try await handle(verificationResult: entitlement, origin: "restore")
                result.applied += 1
            } catch {
                result.record(error, op: "iap_restore")
            }
        }
        return result
    }

    // MARK: - Verification hand-off

    /// Send a signed transaction to the backend and finish it only once recorded.
    ///
    /// Returns what the backend applied. Every failure THROWS — there is no "failed quietly"
    /// return value. It used to answer an unverified transaction with `nil`, which the three
    /// sweeps below could not see: they classify from `catch`, so the failure reached the user
    /// as `allFailed` with a nil `lastError` ("The purchase couldn't be applied. Please try
    /// again.") and emitted no analytics at all, on a payment path.
    ///
    /// Handles BOTH product families. That is the point: subscriptions and consumable credit
    /// packs differ only in what the server does with them, while the parts that are easy to
    /// get wrong — never finishing an unverified transaction, finishing only AFTER the server
    /// has recorded it, finishing a terminal 409 so Apple stops redelivering, and announcing
    /// the change — are identical and must not be duplicated into a second code path.
    @discardableResult
    private func handle(
        verificationResult: VerificationResult<Transaction>, origin: String
    ) async throws -> PurchaseApplied {
        switch verificationResult {
        case .unverified(_, let error):
            // Apple itself could not verify this. Do NOT forward it and do NOT finish it —
            // forwarding wastes a round-trip on something guaranteed to fail server-side, and
            // finishing would discard something we cannot prove is not a real purchase.
            //
            // THROW rather than return nil. The transaction stays unfinished either way; what
            // changes is that the sweeps can now see it, so it is reported and counted instead
            // of silently producing "nothing applied, no error".
            #if DEBUG
            print("🔴 [StoreKit] unverified transaction (\(origin)): \(error)")
            #endif
            throw StoreKitError.unverified(underlying: error)

        case .verified(let transaction):
            // The JWS Apple signed — `VerificationResult.jwsRepresentation`, NOT
            // `Transaction.jsonRepresentation`. The backend re-verifies Apple's signature over
            // this blob, so it must be the signed envelope and deliberately not the decoded
            // fields, which a client can edit.
            //
            // ⚠️ This previously sent `String(decoding: transaction.jsonRepresentation,
            // as: UTF8.self)` under a comment asserting that WAS the JWS. It is not: that
            // property is the decoded payload as JSON. Two consequences, and the second is
            // the one that matters:
            //   1. Every verification failed. PyJWT split the JSON on "." and base64-decoded
            //      the first fragment, yielding binary — `DecodeError: Invalid header string:
            //      'utf-8' codec can't decode byte 0x9a`. A 400 on every purchase, in every
            //      environment, so IAP could never have worked in production either.
            //   2. It inverted the security property the comment claims. The decoded JSON is
            //      exactly the client-editable form; had the server accepted it, a caller
            //      could have minted any transaction they liked.
            let signed = verificationResult.jwsRepresentation

            let applied: PurchaseApplied
            do {
                applied = try await AccountRepository.shared
                    .verifyPurchase(signedTransaction: signed)
            } catch {
                // TERMINAL failures must be FINISHED, not retried.
                //
                // Leaving a transaction unfinished is right for a transient failure — Apple
                // redelivers it, so a backend outage delays the entitlement instead of losing
                // a purchase. But it is wrong for a condition that can never clear: ownership
                // of an App Store transaction never moves between accounts, so a purchase
                // bound to a different Caydex account fails identically on every attempt.
                // Left unfinished, `Transaction.updates` re-delivers it on EVERY launch,
                // forever, and the user is told each time to "reopen the app shortly".
                //
                // (The backend previously returned 503 SYSTEM_BUSY here, which made this
                // indistinguishable from a real outage. It now returns 409
                // PURCHASE_ALREADY_LINKED, which is what makes this branch possible.)
                //
                // ⚠️ ONLY `.purchaseAlreadyLinked` finishes here, and the exclusion is
                // load-bearing. `.purchaseAccountMismatch` (409 PURCHASE_ACCOUNT_MISMATCH) is
                // ALSO terminal for this account, but the server refused BEFORE recording
                // anything — no `credit_purchases` row, nobody credited. Finishing that one
                // would delete a purchase the user paid for, with no redelivery left to repair
                // it. It must fall through to the "leave unfinished" branch below, so the same
                // transaction is redelivered and granted once the buying account signs in.
                // Two terminal-and-finishable conditions, and ONLY these two.
                //   .purchaseAlreadyLinked — we already credited another account.
                //   .purchaseRevoked       — Apple refunded it; it can never be grantable.
                // `.purchaseAccountMismatch` is deliberately excluded: nothing was recorded
                // and nobody was credited, so finishing it would destroy a paid purchase with
                // no redelivery left to repair it.
                let mapped = AppError.from(error)
                var isTerminalAndFinishable = false
                if case .purchaseAlreadyLinked = mapped { isTerminalAndFinishable = true }
                if case .purchaseRevoked = mapped { isTerminalAndFinishable = true }
                if isTerminalAndFinishable {
                    await transaction.finish()
                    #if DEBUG
                    print("🔴 [StoreKit] transaction belongs to another account (\(origin)); finishing so Apple stops redelivering it")
                    #endif
                    throw error
                }

                // Left UNFINISHED on purpose. Apple redelivers unfinished transactions on
                // the next launch, so a backend outage delays the entitlement instead of
                // losing a purchase the user paid for.
                #if DEBUG
                print("🔴 [StoreKit] backend verify failed (\(origin)); leaving unfinished: \(error)")
                #endif
                throw error
            }

            // Recorded server-side — now safe to tell Apple we've handled it.
            await transaction.finish()

            // Adopt the new entitlement IN THIS SESSION.
            //
            // The backend has the tier and the credits; the app did not. Nothing re-read them
            // after a purchase: none of the five paywall presentations passes an `onDismiss`,
            // `PaywallView` has no `.onDisappear`, and `ProfileView.onAppear` does not re-fire
            // when a sheet above it closes. So a user paid, the sheet dismissed, and the app
            // still showed Free with the old credit balance — and `canGenerateResearch` still
            // gated them — until the next cold launch. The most likely reaction to that is a
            // refund request, or a second purchase.
            //
            // Placed here rather than in `PaywallViewModel` deliberately: this is the single
            // funnel for BOTH an interactive purchase and the `Transaction.updates` replay
            // (restores, Ask-to-Buy approvals, renewals), so a fix in the paywall would miss
            // every non-interactive grant.
            //
            // Announced rather than called: `StoreKitService` holds no `AppState` reference, and
            // injecting one would mean changing `configure(...)` in iosApp.swift. `AppState`
            // subscribes to this in its own initialiser.
            NotificationCenter.default.post(name: .caydexEntitlementChanged, object: nil)
            return applied
        }
    }

    // MARK: - Unfinished-transaction recovery

    /// Re-submit every transaction Apple still considers unfinished.
    ///
    /// This is the restore mechanism for CONSUMABLES. `restorePurchases()` above cannot do it:
    /// `Transaction.currentEntitlements` excludes consumables by design, so a credit pack that
    /// was paid for but never recorded — the app was killed mid-purchase, the network dropped,
    /// or `verifyPurchase` was refused because the user was signed out — is invisible to it.
    /// `Transaction.unfinished` is where those live.
    ///
    /// Safe to call repeatedly: the server's grant is idempotent per transaction id, so a
    /// re-submission of something already recorded returns "replay" and grants nothing.
    ///
    /// Returns how many were successfully applied.
    @discardableResult
    func drainUnfinishedTransactions() async -> SweepResult {
        // One at a time rather than a task group: this runs at sign-in and on every Buy
        // Credits load, and a user with several stranded transactions would otherwise fire
        // that many concurrent `POST /billing/verify` calls at once.
        var result = SweepResult()
        for await unfinished in Transaction.unfinished {
            result.seen += 1
            do {
                let applied = try await handle(verificationResult: unfinished,
                                               origin: "unfinished")
                result.applied += 1
                result.creditsGranted += applied.creditsGranted
            } catch {
                result.record(error, op: "iap_drain_unfinished")
            }
        }
        return result
    }

    /// Outcome of a sweep over Apple's transactions.
    ///
    /// Replaces a bare `Int`, which could not distinguish the three states a user needs told
    /// apart — "you had nothing to restore", "we restored N", and "we found N and every one of
    /// them FAILED". The old code collapsed all three with `try?`, so a user whose purchases
    /// could not be applied was told "Nothing to restore" while their money sat unclaimed.
    struct SweepResult: Sendable {
        /// Transactions Apple handed us.
        var seen = 0
        /// Of those, how many the backend accepted.
        var applied = 0
        /// Credits actually added (0 for subscriptions, and 0 for a replay).
        var creditsGranted = 0
        /// The last failure, for the user-facing message.
        var lastError: Error?

        /// True when Apple gave us transactions and NONE could be applied — the state that
        /// must never render as "nothing to restore".
        var allFailed: Bool { seen > 0 && applied == 0 }

        mutating func record(_ error: Error, op: String) {
            lastError = error
            // Release-visible. `.claude/rules/auth.md` §6 bans `#if DEBUG`-only reporting on a
            // user-initiated path: a release build that says nothing is exactly how a whole
            // class of silent failures survived unnoticed.
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string(op),
                "code": .string(AppError.from(error).analyticsCode),
            ])
            #if DEBUG
            print("🔴 [StoreKit] \(op) failed: \(AppError.from(error).message)")
            #endif
        }
    }
}
