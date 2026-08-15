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

    private let repository: AccountRepositoryProtocol
    let store: StoreKitService

    init(
        repository: AccountRepositoryProtocol = AccountRepository.shared,
        store: StoreKitService = .shared
    ) {
        self.repository = repository
        self.store = store
    }

    /// Report cost fallback used in copy before the catalog loads.
    var reportCost: Int { catalog?.reportCost ?? 20 }

    func load() async {
        isLoading = true
        errorMessage = nil
        // Catalog and products in parallel — they're independent, and serialising them
        // doubles the time the paywall shows a spinner.
        async let catalogTask: Void = loadCatalog()
        async let productsTask: Void = store.loadProducts()
        _ = await (catalogTask, productsTask)
        isLoading = false
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

    /// The signed-in user's id, stamped onto the purchase as StoreKit's `appAccountToken` so a
    /// transaction redelivered into a different session can be refused server-side.
    ///
    /// ⚠️ Passed in at TAP time, never snapshotted. It used to be assigned once from `.task`,
    /// so a sheet that stayed mounted across a `.restoring → .authenticated` transition — a
    /// cold launch holding a Keychain token, a transient failure leaving `profile` nil, the
    /// backoff healing seconds later — stamped a stale or nil token onto a real purchase. The
    /// server-side cross-account guard then had nothing to check.
    private var accountID: String?

    /// Which lock opened the sheet. Carried onto the purchase events so conversion is a
    /// single funnel join (`paywall_shown` → `paywall_purchase_started` → `purchase_completed`
    /// on one `reason`) rather than a session-level correlation between three unrelated rows.
    var context: PaywallContext = .general

    func purchase(tier: String, accountID: String?) async {
        self.accountID = accountID
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
                    + "subscription were added. If you were charged, contact support."
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
