//
//  OnboardingViewModel.swift
//  ios
//
//  Owns the first-run ticker capture.
//
//  DESIGN RULE: onboarding may never trap or stall the user. Every network call here
//  is best-effort and OPTIMISTIC — a tap toggles local selection instantly and the
//  backend write happens behind it. If the write fails, the user still reaches the
//  app with the tickers they picked shown as selected; the watchlist just reconciles
//  on the next successful sync. Blocking a first-run screen on a flaky connection is
//  a far worse outcome than a watchlist that is briefly one ticker short.
//

import Combine
import Foundation

@MainActor
final class OnboardingViewModel: ObservableObject {

    /// Tickers the user has selected, in tap order.
    @Published private(set) var selected: [String] = []
    /// Symbols added via search that aren't in the suggested grid, so they can be
    /// rendered as extra chips.
    @Published private(set) var extraSuggestions: [OnboardingTicker] = []
    @Published var showSearch = false

    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    /// A deliberately broad, recognisable starter set — large caps across sectors plus
    /// crypto — so almost any user finds something they care about without searching.
    /// Not a recommendation list, and never presented as one.
    static let suggestions: [OnboardingTicker] = [
        OnboardingTicker(symbol: "AAPL", name: "Apple"),
        OnboardingTicker(symbol: "NVDA", name: "NVIDIA"),
        OnboardingTicker(symbol: "MSFT", name: "Microsoft"),
        OnboardingTicker(symbol: "TSLA", name: "Tesla"),
        OnboardingTicker(symbol: "AMZN", name: "Amazon"),
        OnboardingTicker(symbol: "GOOGL", name: "Alphabet"),
        OnboardingTicker(symbol: "META", name: "Meta"),
        OnboardingTicker(symbol: "BTC", name: "Bitcoin"),
        OnboardingTicker(symbol: "AMD", name: "AMD"),
        OnboardingTicker(symbol: "COST", name: "Costco"),
        OnboardingTicker(symbol: "JPM", name: "JPMorgan"),
        OnboardingTicker(symbol: "DIS", name: "Disney"),
    ]

    var allChips: [OnboardingTicker] { Self.suggestions + extraSuggestions }

    func isSelected(_ symbol: String) -> Bool {
        selected.contains(symbol.uppercased())
    }

    /// Toggle a ticker. Local state updates immediately; the backend follows.
    func toggle(_ ticker: OnboardingTicker) {
        let symbol = ticker.symbol.uppercased()
        if let idx = selected.firstIndex(of: symbol) {
            selected.remove(at: idx)
            Task { await self.remove(symbol) }
        } else {
            selected.append(symbol)
            Analytics.shared.track(.watchlistAdded, ["ticker": .string(symbol)])
            Task { await self.add(symbol) }
        }
    }

    /// A result chosen from the search sheet. Adds a chip so the choice is visible
    /// alongside the suggestions rather than vanishing into an invisible count.
    func addFromSearch(symbol: String, name: String) {
        let upper = symbol.uppercased()
        guard !upper.isEmpty else { return }
        if !allChips.contains(where: { $0.symbol == upper }) {
            extraSuggestions.append(OnboardingTicker(symbol: upper, name: name))
        }
        guard !isSelected(upper) else { return }
        selected.append(upper)
        Analytics.shared.track(.watchlistAdded, ["ticker": .string(upper)])
        Task { await self.add(upper) }
    }

    func trackCompleted() {
        Analytics.shared.track(.onboardingCompleted, ["count": .int(selected.count)])
    }

    func trackSkipped() {
        Analytics.shared.track(.onboardingSkipped, ["count": .int(selected.count)])
    }

    // MARK: - Best-effort backend sync

    private func add(_ symbol: String) async {
        do {
            try await apiClient.request(endpoint: .addToWatchlist(stockId: symbol))
        } catch {
            // Intentionally non-fatal, and deliberately NOT rolled back in the UI: the
            // user's intent is recorded locally, and un-checking a chip they just
            // tapped because of a network blip is more confusing than a watchlist that
            // syncs late. Logged so a systematically failing write is diagnosable.
            #if DEBUG
            print("⚠️ [Onboarding] add \(symbol) failed: \(error)")
            #endif
        }
    }

    private func remove(_ symbol: String) async {
        do {
            try await apiClient.request(endpoint: .removeFromWatchlist(stockId: symbol))
        } catch {
            #if DEBUG
            print("⚠️ [Onboarding] remove \(symbol) failed: \(error)")
            #endif
        }
    }
}

/// A selectable ticker chip. Local to onboarding — deliberately not a domain model,
/// so this screen carries no dependency on the watchlist/search response shapes.
struct OnboardingTicker: Identifiable, Equatable {
    var id: String { symbol }
    let symbol: String
    let name: String
}
