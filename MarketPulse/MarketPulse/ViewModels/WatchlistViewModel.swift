import SwiftUI

@MainActor
class WatchlistViewModel: ObservableObject {
    @Published var watchlistItems: [WatchlistItem] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadWatchlist() async {
        isLoading = true
        errorMessage = nil

        do {
            watchlistItems = try await apiService.getWatchlist()
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func removeFromWatchlist(_ item: WatchlistItem) async {
        do {
            try await apiService.removeFromWatchlist(stockId: item.stockId)
            watchlistItems.removeAll { $0.id == item.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
