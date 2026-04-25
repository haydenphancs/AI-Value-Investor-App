//
//  PortfolioStore.swift
//  ios
//
//  Server-backed observable store for the named portfolios that group tickers
//  on the Tracking screen. The list of portfolios and their ticker membership
//  live on the backend; only the active-portfolio selection is persisted in
//  UserDefaults so the user sees the same view across app restarts.
//
//  Mutation strategy:
//    - Portfolio CRUD (create / rename / delete / reorder) talks to the server
//      first, then updates @Published state on success.
//    - Ticker membership mutations (add / remove) update @Published state
//      optimistically for instant feedback, then sync to the server. The
//      server payload is the full ordered ticker list (not a diff), so the
//      sync call is idempotent — a transient failure just means the next
//      successful call repairs the state.
//

import Foundation
import Combine
import SwiftUI

@MainActor
final class PortfolioStore: ObservableObject {
    // MARK: - Singleton

    static let shared = PortfolioStore()

    // MARK: - Published state

    @Published private(set) var portfolios: [Portfolio] = []
    @Published private(set) var activePortfolioId: String?
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var hasLoadedOnce: Bool = false

    // MARK: - Private

    private let apiClient: APIClient
    private static let activeIdKey = "TrackingView.activePortfolioId"

    var activePortfolio: Portfolio? {
        portfolios.first { $0.id == activePortfolioId }
    }

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        self.activePortfolioId = UserDefaults.standard.string(forKey: Self.activeIdKey)
    }

    // MARK: - Loading

    func loadPortfolios() async {
        isLoading = true
        defer {
            isLoading = false
            hasLoadedOnce = true
        }

        do {
            let response = try await apiClient.request(
                endpoint: .getPortfolios,
                responseType: PortfolioListResponseDTO.self
            )
            let loaded = response.portfolios
                .map { $0.toPortfolio() }
                .sorted { $0.sortOrder < $1.sortOrder }
            self.portfolios = loaded
            ensureActiveSelection()
            print("[PortfolioStore] ✅ Loaded \(loaded.count) portfolios from API")
        } catch {
            print("[PortfolioStore] ❌ Load failed: \(error)")
        }
    }

    // MARK: - Active selection

    func setActivePortfolio(_ id: String) {
        guard portfolios.contains(where: { $0.id == id }) else { return }
        activePortfolioId = id
        UserDefaults.standard.set(id, forKey: Self.activeIdKey)
    }

    /// If the persisted active id is gone (deleted on another device, first
    /// launch, etc.) fall back to the first portfolio so the UI always has a
    /// valid selection.
    private func ensureActiveSelection() {
        if let current = activePortfolioId,
           portfolios.contains(where: { $0.id == current }) {
            return
        }
        if let first = portfolios.first {
            activePortfolioId = first.id
            UserDefaults.standard.set(first.id, forKey: Self.activeIdKey)
        } else {
            activePortfolioId = nil
        }
    }

    // MARK: - Portfolio CRUD

    @discardableResult
    func createPortfolio(named name: String) async throws -> Portfolio {
        let dto = try await apiClient.request(
            endpoint: .createPortfolio(name: name),
            responseType: PortfolioDTO.self
        )
        let new = dto.toPortfolio()
        portfolios.append(new)
        portfolios.sort { $0.sortOrder < $1.sortOrder }
        setActivePortfolio(new.id)
        return new
    }

    @discardableResult
    func renamePortfolio(id: String, to newName: String) async throws -> Portfolio {
        let dto = try await apiClient.request(
            endpoint: .renamePortfolio(id: id, name: newName),
            responseType: PortfolioDTO.self
        )
        let updated = dto.toPortfolio()
        if let index = portfolios.firstIndex(where: { $0.id == id }) {
            portfolios[index] = updated
        }
        return updated
    }

    func deletePortfolio(id: String) async throws {
        try await apiClient.request(endpoint: .deletePortfolio(id: id))
        portfolios.removeAll { $0.id == id }
        if activePortfolioId == id {
            ensureActiveSelection()
        }
    }

    func reorderPortfolios(_ newOrder: [Portfolio]) async throws {
        let ids = newOrder.map(\.id)
        var reordered: [Portfolio] = []
        for (index, portfolio) in newOrder.enumerated() {
            var copy = portfolio
            copy.sortOrder = index
            reordered.append(copy)
        }
        portfolios = reordered
        try await apiClient.request(endpoint: .reorderPortfolios(ids: ids))
    }

    // MARK: - Ticker membership (optimistic)

    /// Append a ticker to the active portfolio (or `portfolioId` if given).
    /// No-op if the ticker is already there.
    func addTicker(_ ticker: String, to portfolioId: String? = nil) async throws {
        let symbol = ticker.uppercased()
        guard let targetId = portfolioId ?? activePortfolioId,
              let index = portfolios.firstIndex(where: { $0.id == targetId }) else {
            return
        }
        if portfolios[index].tickers.contains(symbol) { return }

        let originalTickers = portfolios[index].tickers
        portfolios[index].tickers.append(symbol)

        do {
            try await syncTickers(for: targetId)
        } catch {
            portfolios[index].tickers = originalTickers
            throw error
        }
    }

    /// Drop a ticker from the active portfolio (or `portfolioId` if given).
    func removeTicker(_ ticker: String, from portfolioId: String? = nil) async throws {
        let symbol = ticker.uppercased()
        guard let targetId = portfolioId ?? activePortfolioId,
              let index = portfolios.firstIndex(where: { $0.id == targetId }) else {
            return
        }
        let originalTickers = portfolios[index].tickers
        portfolios[index].tickers.removeAll { $0 == symbol }
        guard portfolios[index].tickers != originalTickers else { return }

        do {
            try await syncTickers(for: targetId)
        } catch {
            portfolios[index].tickers = originalTickers
            throw error
        }
    }

    /// Replace the ticker list for a portfolio (used by drag-to-reorder in the
    /// Edit Portfolio sheet).
    func setTickers(_ tickers: [String], in portfolioId: String) async throws {
        guard let index = portfolios.firstIndex(where: { $0.id == portfolioId }) else { return }
        let normalized = tickers.map { $0.uppercased() }
        let originalTickers = portfolios[index].tickers
        portfolios[index].tickers = normalized

        do {
            try await syncTickers(for: portfolioId)
        } catch {
            portfolios[index].tickers = originalTickers
            throw error
        }
    }

    /// Remove a ticker from every portfolio (used by the long-press
    /// "Remove from all portfolios" action). Best-effort: any single sync
    /// failure is logged but the others still run.
    func removeTickerFromAllPortfolios(_ ticker: String) async {
        let symbol = ticker.uppercased()
        for portfolio in portfolios where portfolio.tickers.contains(symbol) {
            do {
                try await removeTicker(symbol, from: portfolio.id)
            } catch {
                print("[PortfolioStore] ❌ Failed to remove \(symbol) from \(portfolio.name): \(error)")
            }
        }
    }

    /// Drop tickers from every portfolio that no longer exist on the master
    /// watchlist (e.g. removed from another device). Idempotent — only fires
    /// the per-portfolio sync if something actually changed.
    func purgeTickers(notIn allowed: Set<String>) async {
        for portfolio in portfolios {
            let upperAllowed = Set(allowed.map { $0.uppercased() })
            let filtered = portfolio.tickers.filter { upperAllowed.contains($0) }
            if filtered != portfolio.tickers {
                do {
                    try await setTickers(filtered, in: portfolio.id)
                } catch {
                    print("[PortfolioStore] ❌ Failed to purge orphans in \(portfolio.name): \(error)")
                }
            }
        }
    }

    // MARK: - Server sync helper

    private func syncTickers(for portfolioId: String) async throws {
        guard let portfolio = portfolios.first(where: { $0.id == portfolioId }) else { return }
        let dto = try await apiClient.request(
            endpoint: .setPortfolioTickers(id: portfolioId, tickers: portfolio.tickers),
            responseType: PortfolioDTO.self
        )
        let serverTruth = dto.toPortfolio()
        if let index = portfolios.firstIndex(where: { $0.id == portfolioId }) {
            // Server is the source of truth — accept its filtered/normalized
            // list (it drops tickers not on the master watchlist).
            portfolios[index].tickers = serverTruth.tickers
        }
    }
}

// MARK: - Environment

private struct PortfolioStoreKey: EnvironmentKey {
    @MainActor static var defaultValue: PortfolioStore { PortfolioStore.shared }
}

extension EnvironmentValues {
    var portfolioStore: PortfolioStore {
        get { self[PortfolioStoreKey.self] }
        set { self[PortfolioStoreKey.self] = newValue }
    }
}
