import SwiftUI

@MainActor
class DashboardViewModel: ObservableObject {
    @Published var widgetUpdate: WidgetUpdate?
    @Published var breakingNews: [BreakingNews] = []
    @Published var watchlistPreview: [WatchlistItem] = []
    @Published var reportsPreview: [ResearchReport] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadDashboard() async {
        isLoading = true
        errorMessage = nil

        async let widget = loadWidget()
        async let news = loadBreakingNews()
        async let watchlist = loadWatchlistPreview()
        async let reports = loadReportsPreview()

        await widget
        await news
        await watchlist
        await reports

        isLoading = false
    }

    func refresh() async {
        await loadDashboard()
    }

    private func loadWidget() async {
        do {
            widgetUpdate = try await apiService.getWidgetLatest()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadBreakingNews() async {
        do {
            breakingNews = try await apiService.getBreakingNews()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadWatchlistPreview() async {
        do {
            let items = try await apiService.getWatchlist()
            watchlistPreview = Array(items.prefix(Config.maxWatchlistPreview))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadReportsPreview() async {
        do {
            let reports = try await apiService.getResearchReports(limit: Config.maxReportsPreview)
            reportsPreview = reports
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
