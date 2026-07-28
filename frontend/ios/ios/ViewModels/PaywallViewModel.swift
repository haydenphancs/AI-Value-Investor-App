//
//  PaywallViewModel.swift
//  ios
//
//  Loads the live tier catalog (GET /billing/plans) for the paywall. Read-only:
//  the actual purchase is deferred (no StoreKit yet), so the view only needs the
//  catalog + the user's current tier (read from AppState).
//

import Foundation
import Combine

@MainActor
final class PaywallViewModel: ObservableObject {

    @Published var catalog: PlanCatalog?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    private let repository: AccountRepositoryProtocol

    init(repository: AccountRepositoryProtocol = AccountRepository.shared) {
        self.repository = repository
    }

    /// Report cost fallback used in copy before the catalog loads.
    var reportCost: Int { catalog?.reportCost ?? 20 }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            catalog = try await repository.fetchPlanCatalog()
        } catch {
            errorMessage = AppError.from(error).message
        }
        isLoading = false
    }
}
