//
//  BuyCreditsViewModel.swift
//  ios
//
//  Drives the Buy Credits screen: one-off CONSUMABLE credit packs.
//
//  Deliberately a sibling of `PaywallViewModel` rather than a mode of it. The two answer
//  different questions — "top me up now" vs "change my plan" — and share nothing but the
//  StoreKit service, which already handles both product families.
//
//  Two things here differ from the paywall and are worth stating:
//
//  1. Credits come from the BACKEND catalog, prices from STOREKIT. `Product` has no credit
//     count, so hardcoding it would let a server config change make this screen lie about
//     what the user gets; and `priceLabel` is USD-only, so Apple's localized `displayPrice`
//     must win wherever it is available.
//
//  2. A REPLAY is a distinct outcome, not a success. StoreKit redelivers unfinished
//     transactions, so "you already had these" is a real thing to say — claiming "250 credits
//     added" for a delivery that granted nothing is a number the user can check.
//

import Foundation
import Combine
import StoreKit

@MainActor
final class BuyCreditsViewModel: ObservableObject {

    @Published var catalog: CreditPackCatalog?
    @Published var isLoading = false
    /// Catalog-load failure only. Purchase failures go to `purchaseError` so they can be an
    /// alert over the packs rather than replacing them — a user whose purchase failed still
    /// wants to see the list and try again.
    @Published var errorMessage: String?

    @Published var purchaseError: String?
    /// Credits added by a completed purchase. Drives the success alert.
    @Published var grantedCredits: Int?
    /// A delivery the server had already applied. Never reported as "added".
    @Published var alreadyApplied = false
    /// Ask to Buy / SCA — Apple will deliver later through `Transaction.updates`.
    @Published var isPendingApproval = false
    @Published var restoreMessage: String?
    /// Presents `PaywallView` on top of this screen.
    @Published var showPlans = false

    private let repository: AccountRepositoryProtocol
    let store: StoreKitService

    /// The signed-in user's id, stamped onto the purchase as StoreKit's `appAccountToken`.
    /// Set by the view from `AppState`. See `StoreKitService.purchase(_:accountID:)`.
    var accountID: String?

    init(
        repository: AccountRepositoryProtocol = AccountRepository.shared,
        store: StoreKitService = .shared
    ) {
        self.repository = repository
        self.store = store
    }

    /// Costs, for the "what a credit buys" explainer. Falls back to the shipped values so the
    /// copy is never blank while the catalog loads.
    var reportCost: Int { catalog?.reportCost ?? AnalysisCost.standard.credits }
    var chatCost: Int { catalog?.chatCost ?? 1 }

    /// Packs Apple will actually sell.
    ///
    /// Filtered against the loaded `Product`s rather than shown raw. The catalog is a DB table
    /// and StoreKit is App Store Connect: a row present in one and not the other renders a card
    /// with a USD-only price and a Buy button that can only ever say "That pack isn't available
    /// right now". Showing a price we cannot charge is the part that matters.
    var packs: [CreditPackDTO] {
        (catalog?.packs ?? []).filter { store.creditPackProduct(id: $0.productId) != nil }
    }

    /// True when the backend offers packs but Apple returned none of them — a configuration
    /// drift the user must not be left staring at an empty screen over.
    var hasUnpurchasablePacks: Bool {
        !(catalog?.packs ?? []).isEmpty && packs.isEmpty
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        // Catalog FIRST, so the StoreKit request can include any pack the backend added that
        // this build does not hardcode. Serialising these two costs one round-trip and buys
        // a storefront that cannot drift from the server's.
        await loadCatalog()
        await store.loadProducts(
            extraProductIDs: (catalog?.packs ?? []).map(\.productId)
        )
        if hasUnpurchasablePacks && errorMessage == nil {
            errorMessage = store.productLoadError
                ?? "Credit packs aren't available on this device right now. Please try again."
        }
        isLoading = false

        // Recover any purchase that was paid for but never recorded — killed mid-purchase,
        // network dropped, or refused because the user was signed out at the time. Consumables
        // are invisible to `restorePurchases()` (`Transaction.currentEntitlements` excludes
        // them), so this is the only path that repairs them. Idempotent server-side, and
        // deliberately AFTER the load so it cannot delay the screen appearing.
        let drained = await store.drainUnfinishedTransactions()
        if drained.creditsGranted > 0 {
            restoreMessage = "We found a purchase that hadn't been applied yet and added "
                + "\(drained.creditsGranted) credits to your balance."
        } else if drained.allFailed {
            // Apple handed us transactions and NONE could be applied. Silence here is what
            // leaves a user who has paid staring at an unchanged balance with no explanation.
            restoreMessage = "We found a purchase that hasn't been applied yet, but couldn't "
                + "complete it: \(AppError.from(drained.lastError ?? AppError.unknown(message: "The purchase couldn't be applied. Please try again.")).message)"
        }
        // drained.applied > 0 with creditsGranted == 0 is a REPLAY the server had already
        // recorded — the balance is already correct, so saying "we added credits" would be a
        // claim the user can check and find false. Stay silent.
    }

    private func loadCatalog() async {
        do {
            catalog = try await repository.fetchCreditPackCatalog()
        } catch {
            errorMessage = AppError.from(error).message
        }
    }

    /// Apple's localized price, e.g. "$4.99" or "€5,49". Nil until products load — the view
    /// then falls back to the catalog's USD `priceLabel` rather than showing nothing.
    func displayPrice(for pack: CreditPackDTO) -> String? {
        store.creditPackProduct(id: pack.productId)?.displayPrice
    }

    func isPurchasing(_ pack: CreditPackDTO) -> Bool {
        store.purchasingProductID == pack.productId
    }

    func purchase(_ pack: CreditPackDTO) async {
        Analytics.shared.track(
            .creditPackPurchaseStarted, ["product_id": .string(pack.productId)]
        )
        purchaseError = nil
        restoreMessage = nil
        grantedCredits = nil
        alreadyApplied = false
        isPendingApproval = false

        guard let product = store.creditPackProduct(id: pack.productId) else {
            // Missing product = a configuration problem (not in App Store Connect, or no
            // StoreKit config in DEBUG), not the user's fault. Say so rather than no-op on tap.
            purchaseError = store.productLoadError
                ?? "That pack isn't available right now. Please try again shortly."
            return
        }

        do {
            switch try await store.purchase(product, accountID: accountID) {
            case .success(let applied):
                if applied.wasReplay || applied.creditsGranted == 0 {
                    // The server had already recorded this transaction. Saying "250 credits
                    // added" here would be a claim the user can check against their balance
                    // and find false.
                    alreadyApplied = true
                } else {
                    grantedCredits = applied.creditsGranted
                    Analytics.shared.track(
                        .creditPackPurchased,
                        ["product_id": .string(pack.productId),
                         "count": .int(applied.creditsGranted)]
                    )
                }
            case .cancelled:
                break   // user dismissed the sheet — not an error
            case .pending:
                isPendingApproval = true
            }
        } catch {
            purchaseError = AppError.from(error).message
        }
    }

    /// "Restore" for consumables is the unfinished-transaction sweep, NOT
    /// `Transaction.currentEntitlements` — Apple excludes consumables from entitlements, so
    /// there is nothing there to restore. Also re-runs `restorePurchases()` so a user who taps
    /// this because their SUBSCRIPTION is missing is not sent to another screen.
    func restore() async {
        purchaseError = nil
        restoreMessage = nil
        let packs = await store.drainUnfinishedTransactions()
        let subs = await store.restorePurchases()
        let seen = packs.seen + subs.seen
        let applied = packs.applied + subs.applied

        if applied > 0 {
            let credits = packs.creditsGranted + subs.creditsGranted
            restoreMessage = credits > 0
                ? "Restored your purchases and added \(credits) credits."
                : "Restored your purchases."
        } else if seen > 0 {
            // THE case the old `Int` return could not express. Apple returned purchases and
            // every submission failed, and the message said "Nothing to restore" — telling a
            // user with an active subscription or an unapplied pack that they never bought
            // anything, and giving them no reason to contact support.
            let err = AppError.from(packs.lastError ?? subs.lastError ?? AppError.unknown(message: "The purchase couldn't be applied. Please try again."))
            restoreMessage = "We found \(seen) purchase\(seen == 1 ? "" : "s") but couldn't "
                + "apply \(seen == 1 ? "it" : "them"): \(err.message)"
        } else {
            restoreMessage = "Nothing to restore — this Apple Account has no unapplied purchases."
        }
    }
}
