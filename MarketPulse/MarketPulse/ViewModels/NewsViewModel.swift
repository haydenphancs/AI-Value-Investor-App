import SwiftUI
import Combine


@MainActor
class NewsViewModel: ObservableObject {
    @Published var newsItems: [NewsFeedItem] = []
    @Published var selectedSentiment: SentimentType?
    @Published var currentPage = 1
    @Published var hasMore = true
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadNews(refresh: Bool = false) async {
        if refresh {
            currentPage = 1
            hasMore = true
            newsItems = []
        }

        guard !isLoading && hasMore else { return }

        if currentPage == 1 {
            isLoading = true
        } else {
            isLoadingMore = true
        }

        errorMessage = nil

        do {
            let response = try await apiService.getNewsFeed(page: currentPage, sentiment: selectedSentiment)

            if refresh {
                newsItems = response.items
            } else {
                newsItems.append(contentsOf: response.items)
            }

            hasMore = response.hasNext
            if hasMore {
                currentPage += 1
            }
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
        isLoadingMore = false
    }

    func filterBySentiment(_ sentiment: SentimentType?) async {
        selectedSentiment = sentiment
        await loadNews(refresh: true)
    }
}

@MainActor
class NewsDetailViewModel: ObservableObject {
    @Published var article: NewsArticle?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    let newsId: String

    init(newsId: String) {
        self.newsId = newsId
    }

    func loadArticle() async {
        isLoading = true
        errorMessage = nil

        do {
            article = try await apiService.getNewsDetail(newsId: newsId)

            // Mark as read
            try? await apiService.markNewsRead(newsId: newsId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }
}
