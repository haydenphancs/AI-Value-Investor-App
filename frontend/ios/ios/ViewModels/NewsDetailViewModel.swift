//
//  NewsDetailViewModel.swift
//  ios
//
//  ViewModel for the News Detail screen.
//
//  This screen previously FABRICATED its "AI Key Takeaways": a local `switch`
//  on the article's sentiment emitted four canned paragraphs ("Q4 deliveries
//  reached 484,000 units…") that rendered as genuine AI analysis of the story
//  the user was reading. It also hardcoded `readTimeMinutes: 4` and pointed
//  "Read Full Story" at https://example.com/article.
//
//  Everything here is now real: takeaways are the backend's AI enrichment
//  bullets for THIS article, read time is computed from the article body, and
//  the link is the publisher's own URL. When enrichment is unavailable the
//  section is HIDDEN — an absent takeaway beats an invented one.
//

import SwiftUI
import Combine

@MainActor
final class NewsDetailViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var articleDetail: NewsArticleDetail?
    @Published var isLoading: Bool = false
    /// AI enrichment is in flight — the Key Takeaways section shows a shimmer.
    @Published var isEnriching: Bool = false
    @Published var showShareSheet: Bool = false

    // MARK: - Private Properties
    private var article: NewsArticle
    /// Backend scope the article was loaded under, needed for the enrich call.
    private let scope: String
    private let apiClient: APIClient
    private var hasRequestedEnrichment = false

    /// Average adult reading speed, words/minute. Used to derive the read-time
    /// chip from the actual article body instead of a constant.
    private let wordsPerMinute = 200.0

    // MARK: - Initialization
    init(
        article: NewsArticle,
        scope: String = UpdatesScope.market,
        apiClient: APIClient = .shared
    ) {
        self.article = article
        self.scope = scope
        self.apiClient = apiClient
    }

    // MARK: - Public Methods

    func load() async {
        rebuildDetail()
        await enrichIfNeeded()
    }

    func refresh() async {
        hasRequestedEnrichment = false
        await load()
    }

    func openFullStory() {
        guard let url = articleDetail?.articleURL else {
            print("⚠️ NewsDetailVM: No article URL to open")
            return
        }
        UIApplication.shared.open(url)
    }

    func shareArticle() {
        showShareSheet = true
    }

    // MARK: - Private Methods

    private func rebuildDetail() {
        articleDetail = NewsArticleDetail(
            from: article,
            keyTakeaways: Self.takeaways(from: article.summaryBullets),
            readTimeMinutes: estimatedReadTime()
        )
    }

    /// Key Takeaways ARE the AI enrichment bullets. No synthesis happens on the
    /// client — if the backend has not produced bullets, there are no takeaways.
    private static func takeaways(from bullets: [String]) -> [KeyTakeaway] {
        bullets
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .enumerated()
            .map { KeyTakeaway(index: $0.offset + 1, text: $0.element) }
    }

    /// Read time from the article body's word count. Nil when there is no body
    /// to measure — the chip is then hidden rather than showing a made-up number.
    private func estimatedReadTime() -> Int? {
        guard let summary = article.summary, !summary.isEmpty else { return nil }
        let words = summary.split(whereSeparator: { $0.isWhitespace || $0.isNewline }).count
        guard words > 0 else { return nil }
        return max(1, Int((Double(words) / wordsPerMinute).rounded(.up)))
    }

    private func enrichIfNeeded() async {
        guard !article.aiProcessed,
              article.summaryBullets.isEmpty,
              article.isEnrichable,
              !hasRequestedEnrichment
        else { return }

        hasRequestedEnrichment = true
        isEnriching = true
        defer { isEnriching = false }

        do {
            let response: EnrichUpdatesNewsResponse = try await apiClient.request(
                endpoint: .enrichUpdatesNews(scope: scope, articleIds: [article.apiId]),
                responseType: EnrichUpdatesNewsResponse.self
            )
            guard let dto = (response.articles ?? []).first(where: { $0.id == article.apiId }) else {
                print("⚠️ NewsDetailVM: Enrichment returned no row for \(article.apiId)")
                return
            }
            let bullets = dto.summaryBullets ?? []
            guard !bullets.isEmpty else {
                // The backend degraded (Gemini quota / malformed output). Leave
                // the section hidden; it will retry on the next visit.
                print("⚠️ NewsDetailVM: Enrichment produced no bullets for \(article.apiId)")
                return
            }
            article.summaryBullets = bullets
            article.aiProcessed = true
            if let s = NewsSentiment(backend: dto.sentiment) {
                article.sentiment = s
            }
            rebuildDetail()
            print("✅ NewsDetailVM: Enriched article \(article.apiId) — \(bullets.count) takeaways")
        } catch {
            print("⚠️ NewsDetailVM: Enrichment failed: \(AppError.from(error).message)")
        }
    }
}
