//
//  PortfolioStore.swift
//  ios
//
//  Server-backed observable store for the named portfolios that group tickers
//  on the Tracking screen. The list of portfolios, their ticker membership,
//  and per-portfolio holding values (shares / market_value) live on the
//  backend — INCLUDING which group is active (migration 126). That selection used
//  to be a device-local UserDefaults string, which meant the server could not build
//  Home's watchlist section or the Updates ticker chips from it, so those two
//  screens never followed the group the user picked and the choice did not sync
//  across devices. The UserDefaults key survives only as a cold-start hint for the
//  window before the first successful `GET /portfolios`.
//
//  Mutation strategy:
//    - Portfolio CRUD (create / rename / delete / reorder) talks to the server
//      first, then updates @Published state on success.
//    - Ticker membership mutations (add / remove) update @Published state
//      optimistically for instant feedback, then sync to the server. The
//      server payload is the full ordered ticker list (not a diff), so the
//      sync call is idempotent — a transient failure just means the next
//      successful call repairs the state. The server preserves per-item
//      holdings for tickers that survive the swap.
//    - Per-portfolio holdings (shares / market_value) are pushed via
//      `setHoldings(_:in:)`; the server response replaces the local copy.
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
            // The SERVER decides which group is active (migration 126). Home and the
            // Updates chips read it straight from the database, so if this device kept
            // its own opinion the three surfaces would disagree — which is the entire
            // class of bug this change exists to remove. The local key is now only a
            // cold-start hint for the window before the first successful load.
            if let serverActive = loaded.first(where: \.isActive) {
                activePortfolioId = serverActive.id
                UserDefaults.standard.set(serverActive.id, forKey: Self.activeIdKey)
            } else {
                ensureActiveSelection()
            }
            print("[PortfolioStore] ✅ Loaded \(loaded.count) portfolios from API")
        } catch {
            print("[PortfolioStore] ❌ Load failed: \(error)")
        }
    }

    // MARK: - Active selection

    /// Switch the active group, optimistically and then on the server.
    ///
    /// The switch has to reach the backend: Home's watchlist section and the Updates
    /// ticker chips are built server-side from `portfolios.is_active`, so a local-only
    /// change would move the Tracking tab and leave the other two showing the previous
    /// group. It also makes the selection follow the user across devices.
    ///
    /// Optimistic in memory, reverted on failure, and never silent — a user-initiated
    /// mutation that fails must say so (auth.md §6). Note what is NOT done here: the
    /// UserDefaults write is deferred until the server confirms, because persisting an
    /// optimistic value would make a switch the server never received survive a kill.
    func setActivePortfolio(_ id: String) async {
        guard portfolios.contains(where: { $0.id == id }) else { return }
        guard id != activePortfolioId else { return }

        let previous = activePortfolioId
        activePortfolioId = id
        applyActiveFlag(id)

        do {
            _ = try await apiClient.request(
                endpoint: .activatePortfolio(id: id),
                responseType: PortfolioDTO.self
            )
            UserDefaults.standard.set(id, forKey: Self.activeIdKey)
        } catch {
            activePortfolioId = previous
            applyActiveFlag(previous)
            AppActions.shared.reportMutationFailure(
                error, action: "switch to that list"
            )
        }
    }

    /// Keep the in-memory `isActive` flags consistent with `activePortfolioId`.
    /// Exactly one row is active, so this clears every other one.
    private func applyActiveFlag(_ id: String?) {
        for index in portfolios.indices {
            portfolios[index].isActive = (portfolios[index].id == id)
        }
    }

    /// If the persisted active id is gone (deleted on another device, first
    /// launch, etc.) fall back to the first portfolio so the UI always has a
    /// valid selection.
    /// Drop the previous session's portfolios. Called from `AppState.discardDataForEndedSession`.
    ///
    /// `portfolios` is in-memory and `activePortfolioId` is a device-global UserDefaults key, so
    /// neither was cleared when a session ended. `loadPortfolios()` degrades by keeping whatever
    /// it already has, so the next account signing in on this phone — with the backend
    /// unreachable, or just before its first successful load — saw the PREVIOUS user's portfolio
    /// names in the picker, and their ticker membership driving the filtered asset and alert
    /// lists. Same class as the Learn stores and followed whales; same funnel.
    func reset() {
        portfolios = []
        activePortfolioId = nil
        hasLoadedOnce = false
        UserDefaults.standard.removeObject(forKey: Self.activeIdKey)
    }

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
        // The server deliberately does NOT auto-activate a new group — doing it there
        // would move Home and Updates as a side effect of an action that only said "make
        // a new list". Switching is the CLIENT's call, and here the user just created it,
        // so switching is what they meant. `new.isActive` is already true when this was
        // their first group, and the guard makes that a no-op.
        await setActivePortfolio(new.id)
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
        // Snapshot BEFORE the optimistic write. Every other optimistic mutation in this file
        // (`addTicker`, `removeTicker`, `setTickers`) reverts on throw; this one did not, so a
        // failed reorder left the local list in the new order and the server in the old one.
        // The drag looked like it stuck, and the next load silently undid it.
        let previous = portfolios
        portfolios = reordered
        do {
            try await apiClient.request(endpoint: .reorderPortfolios(ids: ids))
        } catch {
            portfolios = previous
            throw error
        }
    }

    // MARK: - Ticker membership (optimistic)

    /// Append a ticker to the active portfolio (or `portfolioId` if given).
    /// No-op if the ticker is already there. The new item starts with no
    /// per-portfolio holding values; the user fills those in via the
    /// Insights config sheet.
    func addTicker(_ ticker: String, to portfolioId: String? = nil) async throws {
        let symbol = ticker.uppercased()
        guard let targetId = portfolioId ?? activePortfolioId,
              let index = portfolios.firstIndex(where: { $0.id == targetId }) else {
            return
        }
        if portfolios[index].items.contains(where: { $0.ticker == symbol }) { return }

        let originalItems = portfolios[index].items
        portfolios[index].items.append(
            PortfolioItem(ticker: symbol, shares: nil, marketValue: nil)
        )

        do {
            try await syncTickers(for: targetId)
        } catch {
            portfolios[index].items = originalItems
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
        let originalItems = portfolios[index].items
        portfolios[index].items.removeAll { $0.ticker == symbol }
        guard portfolios[index].items != originalItems else { return }

        do {
            try await syncTickers(for: targetId)
        } catch {
            portfolios[index].items = originalItems
            throw error
        }
    }

    /// Replace the ticker list for a portfolio (used by drag-to-reorder in the
    /// Edit Portfolio sheet). Preserves per-portfolio holdings for tickers
    /// already in the portfolio; new tickers come in with empty holdings.
    func setTickers(_ tickers: [String], in portfolioId: String) async throws {
        guard let index = portfolios.firstIndex(where: { $0.id == portfolioId }) else { return }
        let normalized = tickers.map { $0.uppercased() }
        let originalItems = portfolios[index].items
        let priorByTicker = Dictionary(uniqueKeysWithValues: originalItems.map { ($0.ticker, $0) })
        portfolios[index].items = normalized.map { ticker in
            priorByTicker[ticker] ?? PortfolioItem(ticker: ticker, shares: nil, marketValue: nil)
        }

        do {
            try await syncTickers(for: portfolioId)
        } catch {
            portfolios[index].items = originalItems
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
        // NEVER purge against an empty allow-set. A total-wipe delta INFERRED from
        // a feed response is never trustworthy: the tracking feed returns an empty
        // asset list both when the watchlist is genuinely empty AND (historically)
        // when the datastore read failed, and an empty set makes every portfolio
        // differ → setTickers([]) → the backend deletes every portfolio_items row
        // with no reinsert, destroying hand-entered shares/market_value for good.
        // Genuine emptying always flows through the explicit removeTicker /
        // removeTickerFromAllPortfolios paths, which are per-ticker and intentional.
        guard !allowed.isEmpty else {
            print("[PortfolioStore] ⚠️ Skipping purge: empty allow-set (degraded or empty feed) — refusing to clear \(portfolios.count) portfolio(s)")
            return
        }
        for portfolio in portfolios {
            let upperAllowed = Set(allowed.map { $0.uppercased() })
            let filteredTickers = portfolio.tickers.filter { upperAllowed.contains($0) }
            if filteredTickers != portfolio.tickers {
                do {
                    try await setTickers(filteredTickers, in: portfolio.id)
                } catch {
                    print("[PortfolioStore] ❌ Failed to purge orphans in \(portfolio.name): \(error)")
                }
            }
        }
    }

    // MARK: - Per-portfolio holdings (shares / market_value)

    /// Push a bulk update of `shares` / `market_value` for tickers in the
    /// given portfolio. The server response is the full refreshed portfolio,
    /// which we drop into local state — keeps display + diversification calc
    /// in sync without a separate refresh.
    @discardableResult
    func setHoldings(_ items: [HoldingUpdateItem], in portfolioId: String) async throws -> Portfolio {
        let dto = try await apiClient.request(
            endpoint: .setPortfolioHoldings(id: portfolioId, items: items),
            responseType: PortfolioDTO.self
        )
        let updated = dto.toPortfolio()
        if let index = portfolios.firstIndex(where: { $0.id == portfolioId }) {
            portfolios[index] = updated
        }
        return updated
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
            // items list (it drops tickers not on the master watchlist and
            // returns the canonical per-portfolio holdings).
            portfolios[index].items = serverTruth.items
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
