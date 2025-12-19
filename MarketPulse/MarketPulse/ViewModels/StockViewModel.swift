import SwiftUI

@MainActor
class StockSearchViewModel: ObservableObject {
    @Published var searchQuery = ""
    @Published var searchResults: [StockSearchResult] = []
    @Published var isSearching = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    private var searchTask: Task<Void, Never>?

    func search() {
        searchTask?.cancel()

        guard !searchQuery.isEmpty else {
            searchResults = []
            return
        }

        searchTask = Task {
            isSearching = true
            errorMessage = nil

            try? await Task.sleep(nanoseconds: 300_000_000) // Debounce 300ms

            guard !Task.isCancelled else { return }

            do {
                searchResults = try await apiService.searchStocks(query: searchQuery)
            } catch {
                errorMessage = error.localizedDescription
            }

            isSearching = false
        }
    }
}

@MainActor
class StockDetailViewModel: ObservableObject {
    @Published var stock: Stock?
    @Published var fundamentals: [Fundamental] = []
    @Published var earnings: [Earnings] = []
    @Published var news: [NewsArticle] = []
    @Published var isInWatchlist = false
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    let ticker: String

    init(ticker: String) {
        self.ticker = ticker
    }

    func loadStock() async {
        isLoading = true
        errorMessage = nil

        async let stockData = loadStockDetail()
        async let fundamentalsData = loadFundamentals()
        async let earningsData = loadEarnings()
        async let newsData = loadNews()

        await stockData
        await fundamentalsData
        await earningsData
        await newsData

        isLoading = false
    }

    private func loadStockDetail() async {
        do {
            stock = try await apiService.getStockDetail(ticker: ticker)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadFundamentals() async {
        do {
            fundamentals = try await apiService.getStockFundamentals(ticker: ticker)
        } catch {
            // Non-critical, ignore error
        }
    }

    private func loadEarnings() async {
        do {
            earnings = try await apiService.getStockEarnings(ticker: ticker, upcoming: true)
        } catch {
            // Non-critical, ignore error
        }
    }

    private func loadNews() async {
        do {
            news = try await apiService.getStockNews(ticker: ticker)
        } catch {
            // Non-critical, ignore error
        }
    }

    func toggleWatchlist() async {
        guard let stock = stock else { return }

        do {
            if isInWatchlist {
                try await apiService.removeFromWatchlist(stockId: stock.id)
                isInWatchlist = false
            } else {
                let item = WatchlistCreate(stockId: stock.id, alertOnNews: true, customNotes: nil)
                _ = try await apiService.addToWatchlist(item)
                isInWatchlist = true
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
