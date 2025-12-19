import Foundation
import Combine


@MainActor
class APIService {
    private let client: APIClient

    init(client: APIClient? = nil) {
        self.client = client ?? .shared
    }

    // MARK: - Authentication

    func login(supabaseToken: String) async throws -> AuthToken {
        let request = AuthRequest(supabaseToken: supabaseToken)
        let token: AuthToken = try await client.request(.login(supabaseToken: supabaseToken), body: request)
        client.setTokens(access: token.accessToken, refresh: token.refreshToken)
        return token
    }

    func logout() async throws {
        try await client.requestWithoutResponse(.logout)
        client.clearTokens()
    }

    func getCurrentUser() async throws -> User {
        return try await client.request(.currentUser)
    }

    // MARK: - Users

    func getUserProfile() async throws -> User {
        return try await client.request(.userProfile)
    }

    func updateUserProfile(_ update: UserUpdate) async throws -> User {
        return try await client.request(.updateProfile, body: update)
    }

    func getUserUsage() async throws -> UsageStats {
        return try await client.request(.userUsage)
    }

    func getUserStats() async throws -> UserStats {
        return try await client.request(.userStats)
    }

    func deleteAccount() async throws {
        try await client.requestWithoutResponse(.deleteAccount)
        client.clearTokens()
    }

    // MARK: - Stocks

    func searchStocks(query: String) async throws -> [StockSearchResult] {
        return try await client.request(.searchStocks(query: query))
    }

    func getStockDetail(ticker: String) async throws -> Stock {
        return try await client.request(.stockDetail(ticker: ticker))
    }

    func getStockFundamentals(ticker: String) async throws -> [Fundamental] {
        return try await client.request(.stockFundamentals(ticker: ticker))
    }

    func getStockEarnings(ticker: String, upcoming: Bool? = nil) async throws -> [Earnings] {
        return try await client.request(.stockEarnings(ticker: ticker, upcoming: upcoming))
    }

    func getWatchlist() async throws -> [WatchlistItem] {
        return try await client.request(.watchlist)
    }

    func addToWatchlist(_ item: WatchlistCreate) async throws -> WatchlistItem {
        return try await client.request(.addToWatchlist, body: item)
    }

    func removeFromWatchlist(stockId: String) async throws {
        try await client.requestWithoutResponse(.removeFromWatchlist(stockId: stockId))
    }

    // MARK: - News

    func getNewsFeed(page: Int = 1, sentiment: SentimentType? = nil) async throws -> PaginatedResponse<NewsFeedItem> {
        return try await client.request(.newsFeed(page: page, sentiment: sentiment))
    }

    func getBreakingNews() async throws -> [BreakingNews] {
        return try await client.request(.breakingNews)
    }

    func getNewsDetail(newsId: String) async throws -> NewsArticle {
        return try await client.request(.newsDetail(newsId: newsId))
    }

    func getStockNews(ticker: String) async throws -> [NewsArticle] {
        return try await client.request(.stockNews(ticker: ticker))
    }

    func markNewsRead(newsId: String) async throws {
        try await client.requestWithoutResponse(.markNewsRead(newsId: newsId))
    }

    // MARK: - Research

    func generateReport(_ request: ResearchReportCreate) async throws -> ResearchReport {
        return try await client.request(.generateReport, body: request)
    }

    func getResearchReports(limit: Int? = nil) async throws -> [ResearchReport] {
        return try await client.request(.researchReports(limit: limit))
    }

    func getResearchReportDetail(reportId: String) async throws -> ResearchReport {
        return try await client.request(.researchReportDetail(reportId: reportId))
    }

    func rateReport(reportId: String, rating: ResearchReportRate) async throws -> ResearchReport {
        return try await client.request(.rateReport(reportId: reportId), body: rating)
    }

    func deleteReport(reportId: String) async throws {
        try await client.requestWithoutResponse(.deleteReport(reportId: reportId))
    }

    // MARK: - Chat

    func createChatSession(_ request: ChatSessionCreate) async throws -> ChatSession {
        return try await client.request(.createChatSession, body: request)
    }

    func getChatSessions(limit: Int? = nil) async throws -> [ChatSession] {
        return try await client.request(.chatSessions(limit: limit))
    }

    func getChatSessionDetail(sessionId: String) async throws -> ChatSessionWithMessages {
        return try await client.request(.chatSessionDetail(sessionId: sessionId))
    }

    func sendMessage(sessionId: String, content: String) async throws -> ChatMessage {
        let request = ChatMessageCreate(content: content)
        return try await client.request(.sendMessage(sessionId: sessionId), body: request)
    }

    func deleteChatSession(sessionId: String) async throws {
        try await client.requestWithoutResponse(.deleteChatSession(sessionId: sessionId))
    }

    // MARK: - Widget

    func getWidgetLatest() async throws -> WidgetUpdate {
        return try await client.request(.widgetLatest)
    }

    func getWidgetTimeline(hours: Int = 24) async throws -> WidgetTimeline {
        return try await client.request(.widgetTimeline(hours: hours))
    }

    // MARK: - Education

    func getEducationContent(limit: Int? = nil) async throws -> [EducationContent] {
        return try await client.request(.educationContent(limit: limit))
    }

    func getEducationContentDetail(contentId: String) async throws -> EducationContent {
        return try await client.request(.educationContentDetail(contentId: contentId))
    }

    func getEducationBooks() async throws -> [EducationContent] {
        return try await client.request(.educationBooks)
    }

    func getEducationArticles() async throws -> [EducationContent] {
        return try await client.request(.educationArticles)
    }

    func searchEducation(query: String) async throws -> [EducationContent] {
        return try await client.request(.searchEducation(query: query))
    }

    // MARK: - System

    func getDisclaimer() async throws -> String {
        struct DisclaimerResponse: Codable {
            let disclaimer: String
        }
        let response: DisclaimerResponse = try await client.request(.disclaimer)
        return response.disclaimer
    }
}
