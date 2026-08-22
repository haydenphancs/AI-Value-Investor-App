//
//  SearchViewModel.swift
//  ios
//
//  ViewModel for the universal search screen.
//
//  ⚠️ `results` used to be called `recentSearches`, and that name WAS the bug. It only ever held
//  the live API results — reassigned on every debounced keystroke and emptied the instant the
//  field went blank — while `RecentSearchesSection` rendered it under a "Recent Searches"
//  heading. So the section could only ever say "No recent searches" at rest, and no ticker or
//  question was recorded anywhere. Live results and history are now two different things:
//  `results` here, and `SearchHistoryStore.shared.entries` for the real history.
//

import Combine
import Foundation
import OSLog

@MainActor
final class SearchViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var searchText: String = ""
    /// Live results for the CURRENT query. Empty whenever the field is empty.
    @Published var results: [SearchResultItem] = []
    @Published var isLoading: Bool = false
    @Published var error: String?

    // MARK: - Navigation
    @Published var selectedSearchSelection: SearchSelection?

    // MARK: - Dependencies
    private let stockRepository: StockRepository
    private let history: SearchHistoryStore

    private static let log = Logger(subsystem: "com.phan.caydex", category: "search")

    // Debounce support for live search
    private var searchTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization
    init(stockRepository: StockRepository? = nil, history: SearchHistoryStore? = nil) {
        self.stockRepository = stockRepository ?? .shared
        self.history = history ?? .shared

        // Starter questions shown as chips in the empty state → tapped, they seed a Cay AI
        // conversation (handled in SearchView), turning the empty search into a lightweight
        // AI discovery hub.

        // Live debounced search as the user types.
        $searchText
            .debounce(for: .milliseconds(300), scheduler: RunLoop.main)
            .removeDuplicates()
            .sink { [weak self] query in
                guard let self else { return }
                let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    // Clears the RESULTS only. History is durable and owned by
                    // SearchHistoryStore — emptying the field must not erase it, which is
                    // precisely what the old shared array did.
                    self.results = []
                    return
                }
                self.searchTask?.cancel()
                self.searchTask = Task { [weak self] in
                    await self?.performSearchAsync()
                }
            }
            .store(in: &cancellables)
    }

    // MARK: - Search Actions

    /// Called when the user submits the field (return key).
    func performSearch() {
        guard !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            await self?.performSearchAsync()
        }
    }

    private func performSearchAsync() async {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }

        isLoading = true
        error = nil

        do {
            let stocks = try await stockRepository.searchStocks(query: query, limit: 10)
            results = stocks.map(Self.resultItem(from:))
        } catch {
            Self.log.error("search failed — \(type(of: error), privacy: .public): \(error.localizedDescription, privacy: .public)")
            self.error = AppError.from(error).message
        }

        isLoading = false
    }

    private static func resultItem(from stock: StockSearchResult) -> SearchResultItem {
        let resultType: SearchResultType
        let subtitle: String
        switch stock.type {
        case "crypto":
            resultType = .crypto
            subtitle = "Crypto"
        case "etf":
            resultType = .etf
            subtitle = "ETF"
        case "fund":
            resultType = .etf   // Funds share the ETF icon.
            subtitle = "Fund"
        default:
            resultType = .stock
            subtitle = stock.exchange ?? stock.sector ?? "Stock"
        }
        return SearchResultItem(
            type: resultType,
            rawType: stock.type ?? "stock",
            ticker: stock.ticker,
            name: stock.companyName,
            subtitle: subtitle,
            imageName: nil,
            // Search results are deliberately NOT followable: following is account-scoped and
            // `.signInRequired` on both sides, and `WhaleService.toggleFollow` is the real
            // implementation. Making search results followable is a product decision, not a fix.
            isFollowable: false,
            isFollowing: false
        )
    }

    // MARK: - Selection

    /// Open a live result, and RECORD it.
    ///
    /// Recording here — on selection — rather than in the debounce sink is what makes the
    /// history a record of what the user chose instead of a transcript of the keyboard.
    func selectSearchResult(_ item: SearchResultItem) {
        guard let ticker = item.ticker, !ticker.isEmpty else { return }
        history.record(ticker: ticker, name: item.name, rawType: item.rawType)
        selectedSearchSelection = SearchSelection(symbol: ticker, type: item.rawType)
    }

    /// Re-open a ticker straight from the history list.
    ///
    /// `rawType` is carried on the entry so this rebuilds the SAME selection the live result
    /// produced; defaulting it to "stock" would reopen a crypto or an ETF as a stock.
    func openHistoryEntry(_ entry: SearchHistoryEntry) {
        guard entry.kind == .ticker else { return }
        selectedSearchSelection = SearchSelection(symbol: entry.text, type: entry.rawType ?? "stock")
    }

    // MARK: - History

    func removeHistoryEntry(_ entry: SearchHistoryEntry) {
        history.remove(entry)
    }

    func clearAllHistory() {
        history.clearAll()
    }

    /// Dismiss error after the user acknowledges it.
    func dismissError() {
        error = nil
    }
}
