//
//  PaywallViewModel.swift
//  ios
//
//  Loads the live tier catalog (GET /billing/plans) AND drives the StoreKit purchase.
//
//  The catalog and the StoreKit products are two different things and both are needed: the
//  catalog is our pricing/credit config (so copy stays truthful without an app update),
//  while the StoreKit `Product` is what Apple will actually charge. The price SHOWN comes
//  from StoreKit — Apple's localized price is the one the user is charged, and showing our
//  own number could differ by storefront, currency, or a price change we haven't deployed.
//

import Foundation
import Combine
import OSLog
import StoreKit

@MainActor
final class PaywallViewModel: ObservableObject {

    @Published var catalog: PlanCatalog?
    @Published var isLoading: Bool = false
    /// Failure to load the CATALOG. Rendered inline, in place of the plan list, with a retry —
    /// which only makes sense while there is no list to show.
    @Published var errorMessage: String?
    /// Failure of a PURCHASE the user just tapped. Deliberately a separate property from
    /// `errorMessage`: the view renders that one only when `catalog == nil`, so by the time a
    /// plan button exists to tap, anything written to it is unreachable. A purchase failure was
    /// therefore completely silent — the button simply returned to its idle label. This one is
    /// surfaced as an alert, because the user is mid-action and a retry link would be wrong.
    @Published var purchaseError: String?

    /// Set after a successful purchase so the view can confirm what was applied.
    @Published var purchasedTier: String?
    /// Ask to Buy / SCA: the purchase isn't done and isn't failed.
    @Published var isPendingApproval: Bool = false
    /// Result of a restore, so "nothing to restore" reads differently from "restored".
    @Published var restoreMessage: String?

    /// Whether the account's current tier is PAID FOR through a store subscription
    /// (`GET /users/me/subscription`: a store-backed row that still entitles, for this tier —
    /// see `isPayingFor`), as opposed to a tier set on the account by hand (App Review's demo
    /// account, TestFlight testers) with no live subscription behind it.
    ///
    /// ⚠️ It exists because App Review could not buy Max. The demo account is on Max so the
    /// reviewer can hear Pro/Max narration (Guideline 2.5.4), and `planCTA` used to replace the
    /// buy button with "Current Plan" whenever `plan.tier == user.tier` — so the one product the
    /// review notes said was purchasable had no button (Guideline 2.1, "unable to locate the
    /// in-app purchase"). A hand-set tier now leaves every paid plan purchasable; a real
    /// subscriber still sees "Current Plan" and cannot be invited to pay twice.
    ///
    /// Starts `true` and stays `true` on any failure: the conservative reading is "they
    /// already pay", which at worst hides a button, never double-charges.
    @Published private(set) var currentTierIsStoreBacked: Bool = true

    private let repository: AccountRepositoryProtocol
    let store: StoreKitService

    /// Optional + nil-coalesce — see the note on `BuyCreditsViewModel.init`. Both singletons
    /// are MainActor-isolated and a default argument is checked as nonisolated at the call
    /// site, so the live defaults are resolved inside this `@MainActor` init.
    init(
        repository: AccountRepositoryProtocol? = nil,
        store: StoreKitService? = nil
    ) {
        self.repository = repository ?? AccountRepository.shared
        self.store = store ?? .shared
    }

    /// Report cost fallback used in copy before the catalog loads.
    var reportCost: Int { catalog?.reportCost ?? 20 }

    /// - Parameter isSignedIn: gates the subscription read. `getMySubscription` is
    ///   `.signInRequired`, and APIClient answers a signed-out call by raising the sign-in
    ///   prompt — which must not fire just because a paywall was opened.
    /// - Parameter accountTier: the tier the account is on now (`appState.user.tier`), which
    ///   the subscription row must match to count as "already paying for this plan".
    func load(isSignedIn: Bool, accountTier: UserTier) async {
        isLoading = true
        errorMessage = nil
        // Catalog, products and the subscription in parallel — they're independent, and
        // serialising them multiplies the time the paywall shows a spinner.
        async let catalogTask: Void = loadCatalog()
        async let productsTask: Void = store.loadProducts()
        async let subscriptionTask: Void = loadSubscription(isSignedIn: isSignedIn, accountTier: accountTier)
        _ = await (catalogTask, productsTask, subscriptionTask)
        isLoading = false
    }

    private func loadSubscription(isSignedIn: Bool, accountTier: UserTier) async {
        guard isSignedIn else {
            currentTierIsStoreBacked = true
            return
        }
        do {
            let subscription = try await repository.fetchSubscription()
            currentTierIsStoreBacked = Self.isPayingFor(
                subscription: subscription,
                accountTier: accountTier,
                now: Date()
            )
        } catch {
            // Degrade to "Current Plan" (see the property). Logged, because a paywall that
            // silently hides a purchasable plan is the exact defect this state exists to fix.
            currentTierIsStoreBacked = true
            Self.log.warning("paywall subscription read failed; keeping Current Plan: \(String(describing: type(of: error)), privacy: .public): \(AppError.from(error).message, privacy: .public)")
        }
    }

    private static let log = Logger(subsystem: "com.phan.caydex", category: "paywall")

    /// A tier is paid for only when a STORE stands behind it. nil (no subscription row — the
    /// tier was set on the account directly) and anything unrecognised are not.
    ///
    /// ⚠️ `app_store` is the value the backend actually WRITES (`iap_service` →
    /// `"store": "app_store"`). The DTO comment said "apple | stripe | promo", and the first
    /// version of this check trusted it — which would have shown every real Apple subscriber a
    /// "Choose" button on the plan they already pay for. `apple` / `stripe` stay accepted for
    /// safety. `tests/test_paywall_hand_set_tier_purchasable.py` reads the backend literal.
    nonisolated static func isStoreBacked(store: String?) -> Bool {
        switch store?.lowercased() {
        case "app_store", "apple", "stripe": return true
        default: return false
        }
    }

    /// Statuses under which the backend still entitles the row (`iap_service._ENTITLING_STATUSES`,
    /// plus the DTO's documented short form `grace`).
    nonisolated static let entitlingStatuses: Set<String> = ["active", "grace_period", "grace", "billing_retry"]

    /// Whether this account currently PAYS a store for the tier it is on.
    ///
    /// ⚠️ `store` alone is not enough, and that was the first version of this fix. The
    /// subscription row outlives the subscription: an EXPIRED or REVOKED Apple row keeps
    /// `store = "apple"`. Re-setting App Review's demo account to Max by hand after a sandbox
    /// subscription lapsed would then read as "already paying for Max" and hide the Max button
    /// again. So the row must also still entitle, be for THIS tier, and not have ended —
    /// except in grace/billing-retry, where the period has ended by definition (same carve-out
    /// as `iap_service.winning_tier`).
    nonisolated static func isPayingFor(
        subscription: SubscriptionDTO,
        accountTier: UserTier,
        now: Date
    ) -> Bool {
        guard isStoreBacked(store: subscription.store) else { return false }
        let status = subscription.status.lowercased()
        guard entitlingStatuses.contains(status) else { return false }
        guard subscription.userTier == accountTier else { return false }
        if status == "active", let end = subscription.currentPeriodEnd,
           let endDate = parseISO8601(end), endDate <= now {
            return false
        }
        return true
    }

    private nonisolated static func parseISO8601(_ raw: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = withFraction.date(from: raw) { return d }
        return ISO8601DateFormatter().date(from: raw)
    }

    /// True when `planTier` should render "Current Plan" instead of a buy button.
    ///
    /// The free plan is always "Current Plan" for a free account (it is never bought); a paid
    /// plan is "Current Plan" only when the user already pays for it through a store.
    nonisolated static func showsCurrentPlan(
        planTier: UserTier,
        currentTier: UserTier,
        isFreePlan: Bool,
        currentTierIsStoreBacked: Bool
    ) -> Bool {
        guard planTier == currentTier else { return false }
        return isFreePlan || currentTierIsStoreBacked
    }

    private func loadCatalog() async {
        do {
            catalog = try await repository.fetchPlanCatalog()
        } catch {
            errorMessage = AppError.from(error).message
        }
    }

    /// Apple's localized price for a tier, e.g. "$14.99". Nil when the product hasn't
    /// loaded — the view then falls back to the catalog price rather than showing nothing.
    func displayPrice(forTier tier: String) -> String? {
        store.product(for: tier)?.displayPrice
    }

    func canPurchase(tier: String) -> Bool {
        store.product(for: tier) != nil
    }

    /// Which lock opened the sheet. Carried onto the purchase events so conversion is a
    /// single funnel join (`paywall_shown` → `paywall_purchase_started` → `purchase_completed`
    /// on one `reason`) rather than a session-level correlation between three unrelated rows.
    var context: PaywallContext = .general

    /// - Parameter accountID: the signed-in user's id, stamped onto the purchase as StoreKit's
    ///   `appAccountToken` so a transaction redelivered into a different session can be refused
    ///   server-side.
    ///
    ///   ⚠️ Passed in at TAP time and used only from this parameter — deliberately NOT stored.
    ///   It used to be assigned once from `.task`, so a sheet that stayed mounted across a
    ///   `.restoring → .authenticated` transition — a cold launch holding a Keychain token, a
    ///   transient failure leaving `profile` nil, the backoff healing seconds later — stamped a
    ///   stale or nil token onto a real purchase, and the server-side cross-account guard had
    ///   nothing to check. A `private var accountID` survived that fix as a write-only property;
    ///   it is gone, because the next reader of it would have reintroduced exactly that staleness.
    func purchase(tier: String, accountID: String?) async {
        Analytics.shared.track(.paywallPurchaseStarted, [
            "tier": .string(tier),
            "reason": .string(context.rawValue),
        ])
        purchaseError = nil
        restoreMessage = nil
        isPendingApproval = false

        guard let product = store.product(for: tier) else {
            // Products missing is a configuration problem, not the user's fault. Say so
            // instead of failing silently on tap.
            purchaseError = store.productLoadError
                ?? "That plan isn't available right now. Please try again shortly."
            return
        }

        do {
            switch try await store.purchase(product, accountID: accountID) {
            case .success(let applied):
                Analytics.shared.track(.purchaseCompleted, [
                    "tier": .string(applied.tier),
                    "reason": .string(context.rawValue),
                ])
                purchasedTier = applied.tier
            case .cancelled:
                break   // user dismissed the sheet — not an error
            case .pending:
                isPendingApproval = true
            case .unverified:
                // Apple could not verify its own transaction, so it was never sent
                // to the server. Nothing is pending and nothing will arrive.
                purchaseError = "Apple couldn't verify that purchase, so no "
                    + "subscription was added. If you were charged, contact support."
            }
        } catch {
            purchaseError = AppError.from(error).message
        }
    }

    func restore() async {
        purchaseError = nil
        restoreMessage = nil
        let result = await store.restorePurchases()
        if result.applied > 0 {
            restoreMessage = "Restored your subscription."
        } else if result.seen > 0 {
            // Apple returned entitlements and every submission failed. Reporting "no previous
            // purchases" there tells an ACTIVE subscriber they never bought anything, and
            // leaves them no reason to contact support.
            let err = AppError.from(result.lastError ?? AppError.unknown(message: "The purchase couldn't be applied. Please try again."))
            restoreMessage = "We found your purchase but couldn't restore it: \(err.message)"
        } else {
            restoreMessage = "No previous purchases found for this Apple Account."
        }
    }
}
