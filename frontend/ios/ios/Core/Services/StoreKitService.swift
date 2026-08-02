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
        static let all: [String] = [proMonthly, maxMonthly]
    }

    enum PurchaseOutcome: Sendable, Equatable {
        case success(tier: String)
        /// The user dismissed the sheet. Not an error; never surfaced as one.
        case cancelled
        /// Ask to Buy / Screen Time — Apple will deliver later via `Transaction.updates`.
        case pending
    }

    @Published private(set) var products: [Product] = []
    @Published private(set) var isLoadingProducts = false
    @Published private(set) var isPurchasing = false
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

    func loadProducts() async {
        isLoadingProducts = true
        productLoadError = nil
        do {
            let loaded = try await Product.products(for: ProductID.all)
            // Cheapest first so the paywall order is stable regardless of what StoreKit
            // returns; `Product.products(for:)` does not guarantee request order.
            products = loaded.sorted { $0.price < $1.price }
            if products.isEmpty {
                // Distinguish "Apple returned nothing" from "we haven't asked yet". Usually
                // means the products aren't configured, or no StoreKit config in DEBUG.
                productLoadError = "No subscription options are available right now."
            }
        } catch {
            productLoadError = "Couldn't load subscription options. Please try again."
            #if DEBUG
            print("🔴 [StoreKit] product load failed: \(error)")
            #endif
        }
        isLoadingProducts = false
    }

    func product(for tier: String) -> Product? {
        let id: String
        switch tier.lowercased() {
        case "pro": id = ProductID.proMonthly
        case "premium", "max": id = ProductID.maxMonthly
        default: return nil
        }
        return products.first { $0.id == id }
    }

    // MARK: - Purchase

    /// Run the purchase sheet and, on success, hand the signed transaction to the backend.
    ///
    /// Throws only for genuine failures. Cancellation returns `.cancelled` because a user
    /// tapping "Cancel" is not an error and must not raise an alert.
    func purchase(_ product: Product) async throws -> PurchaseOutcome {
        isPurchasing = true
        defer { isPurchasing = false }

        let result = try await product.purchase()

        switch result {
        case .success(let verification):
            let tier = try await handle(verificationResult: verification, origin: "purchase")
            // A nil tier here means the backend accepted it but reported nothing usable;
            // treat as pending rather than claiming success we can't substantiate.
            guard let tier else { return .pending }
            return .success(tier: tier)

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
    func restorePurchases() async -> Int {
        var restored = 0
        // `currentEntitlements` is the source for restore: it holds only ACTIVE
        // entitlements, unlike `Transaction.all` which includes expired history.
        for await entitlement in Transaction.currentEntitlements {
            if (try? await handle(verificationResult: entitlement, origin: "restore")) != nil {
                restored += 1
            }
        }
        return restored
    }

    // MARK: - Verification hand-off

    /// Send a signed transaction to the backend and finish it only once recorded.
    ///
    /// Returns the tier the backend applied, or nil when the transaction was unverified or
    /// not something we act on.
    @discardableResult
    private func handle(
        verificationResult: VerificationResult<Transaction>, origin: String
    ) async throws -> String? {
        switch verificationResult {
        case .unverified(_, let error):
            // Apple itself could not verify this. Do NOT forward it and do NOT finish it —
            // forwarding wastes a round-trip on something guaranteed to fail server-side.
            #if DEBUG
            print("🔴 [StoreKit] unverified transaction (\(origin)): \(error)")
            #endif
            return nil

        case .verified(let transaction):
            // `jsonRepresentation` is the JWS Apple signed. That is what the backend
            // verifies — deliberately not the decoded fields, which the client could edit.
            let signed = String(decoding: transaction.jsonRepresentation, as: UTF8.self)

            let tier: String
            do {
                tier = try await AccountRepository.shared
                    .verifyPurchase(signedTransaction: signed)
            } catch {
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
            return tier
        }
    }
}
