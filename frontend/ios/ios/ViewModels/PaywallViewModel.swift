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
    @Published var errorMessage: String?

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

    func purchase(tier: String) async {
        Analytics.shared.track(.paywallPurchaseStarted, ["tier": .string(tier)])
        errorMessage = nil
        restoreMessage = nil
        isPendingApproval = false

        guard let product = store.product(for: tier) else {
            // Products missing is a configuration problem, not the user's fault. Say so
            // instead of failing silently on tap.
            errorMessage = store.productLoadError
                ?? "That plan isn't available right now. Please try again shortly."
            return
        }

        do {
            switch try await store.purchase(product) {
            case .success(let appliedTier):
                Analytics.shared.track(.purchaseCompleted, ["tier": .string(appliedTier)])
                purchasedTier = appliedTier
            case .cancelled:
                break   // user dismissed the sheet — not an error
            case .pending:
                isPendingApproval = true
            }
        } catch {
            errorMessage = AppError.from(error).message
        }
    }

    func restore() async {
        errorMessage = nil
        restoreMessage = nil
        let count = await store.restorePurchases()
        restoreMessage = count > 0
            ? "Restored your subscription."
            : "No previous purchases found for this Apple Account."
    }
}
