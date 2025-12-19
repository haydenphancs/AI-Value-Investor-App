import Foundation
import Combine

enum APIEndpoint {
    // MARK: - Authentication
    case login(supabaseToken: String)
    case refresh(refreshToken: String)
    case logout
    case verifyToken
    case currentUser

    // MARK: - Users
    case userProfile
    case updateProfile
    case userUsage
    case userStats
    case deleteAccount

    // MARK: - Stocks
    case searchStocks(query: String)
    case stockDetail(ticker: String)
    case stockFundamentals(ticker: String)
    case stockEarnings(ticker: String, upcoming: Bool?)
    case watchlist
    case addToWatchlist
    case removeFromWatchlist(stockId: String)

    // MARK: - News
    case newsFeed(page: Int, sentiment: SentimentType?)
    case breakingNews
    case newsDetail(newsId: String)
    case stockNews(ticker: String)
    case markNewsRead(newsId: String)

    // MARK: - Research
    case generateReport
    case researchReports(limit: Int?)
    case researchReportDetail(reportId: String)
    case rateReport(reportId: String)
    case deleteReport(reportId: String)

    // MARK: - Chat
    case createChatSession
    case chatSessions(limit: Int?)
    case chatSessionDetail(sessionId: String)
    case sendMessage(sessionId: String)
    case deleteChatSession(sessionId: String)

    // MARK: - Widget
    case widgetLatest
    case widgetTimeline(hours: Int)
    case widgetHistory
    case widgetDetail(updateId: String)
    case generateWidget

    // MARK: - Education
    case educationContent(limit: Int?)
    case educationContentDetail(contentId: String)
    case educationBooks
    case educationArticles
    case educationTopics
    case favoriteContent(contentId: String)
    case searchEducation(query: String)

    // MARK: - System
    case health
    case disclaimer

    var path: String {
        switch self {
        // Auth
        case .login: return "/auth/token"
        case .refresh: return "/auth/refresh"
        case .logout: return "/auth/logout"
        case .verifyToken: return "/auth/verify"
        case .currentUser: return "/auth/me"

        // Users
        case .userProfile: return "/users/me"
        case .updateProfile: return "/users/me"
        case .userUsage: return "/users/me/usage"
        case .userStats: return "/users/me/stats"
        case .deleteAccount: return "/users/me"

        // Stocks
        case .searchStocks: return "/stocks/search"
        case .stockDetail(let ticker): return "/stocks/\(ticker)"
        case .stockFundamentals(let ticker): return "/stocks/\(ticker)/fundamentals"
        case .stockEarnings(let ticker, _): return "/stocks/\(ticker)/earnings"
        case .watchlist: return "/stocks/watchlist/me"
        case .addToWatchlist: return "/stocks/watchlist"
        case .removeFromWatchlist(let stockId): return "/stocks/watchlist/\(stockId)"

        // News
        case .newsFeed: return "/news/feed"
        case .breakingNews: return "/news/breaking"
        case .newsDetail(let newsId): return "/news/\(newsId)"
        case .stockNews(let ticker): return "/news/stock/\(ticker)"
        case .markNewsRead(let newsId): return "/news/\(newsId)/mark-read"

        // Research
        case .generateReport: return "/research/generate"
        case .researchReports: return "/research/reports"
        case .researchReportDetail(let reportId): return "/research/reports/\(reportId)"
        case .rateReport(let reportId): return "/research/reports/\(reportId)/rate"
        case .deleteReport(let reportId): return "/research/reports/\(reportId)"

        // Chat
        case .createChatSession: return "/chat/sessions"
        case .chatSessions: return "/chat/sessions"
        case .chatSessionDetail(let sessionId): return "/chat/sessions/\(sessionId)"
        case .sendMessage(let sessionId): return "/chat/sessions/\(sessionId)/messages"
        case .deleteChatSession(let sessionId): return "/chat/sessions/\(sessionId)"

        // Widget
        case .widgetLatest: return "/widget/latest"
        case .widgetTimeline: return "/widget/timeline"
        case .widgetHistory: return "/widget/history"
        case .widgetDetail(let updateId): return "/widget/\(updateId)"
        case .generateWidget: return "/widget/generate"

        // Education
        case .educationContent: return "/education/content"
        case .educationContentDetail(let contentId): return "/education/content/\(contentId)"
        case .educationBooks: return "/education/books"
        case .educationArticles: return "/education/articles"
        case .educationTopics: return "/education/topics"
        case .favoriteContent(let contentId): return "/education/content/\(contentId)/favorite"
        case .searchEducation: return "/education/search"

        // System
        case .health: return "/health"
        case .disclaimer: return "/disclaimer"
        }
    }

    var method: HTTPMethod {
        switch self {
        case .login, .refresh, .logout, .verifyToken,
             .addToWatchlist, .markNewsRead, .generateReport,
             .rateReport, .createChatSession, .sendMessage,
             .generateWidget, .favoriteContent:
            return .post

        case .updateProfile:
            return .patch

        case .deleteAccount, .removeFromWatchlist, .deleteReport, .deleteChatSession:
            return .delete

        default:
            return .get
        }
    }

    var queryItems: [URLQueryItem]? {
        switch self {
        case .searchStocks(let query):
            return [URLQueryItem(name: "query", value: query)]

        case .stockEarnings(_, let upcoming):
            if let upcoming = upcoming {
                return [URLQueryItem(name: "upcoming", value: String(upcoming))]
            }
            return nil

        case .newsFeed(let page, let sentiment):
            var items = [URLQueryItem(name: "page", value: String(page))]
            if let sentiment = sentiment {
                items.append(URLQueryItem(name: "sentiment", value: sentiment.rawValue))
            }
            return items

        case .researchReports(let limit), .chatSessions(let limit), .educationContent(let limit):
            if let limit = limit {
                return [URLQueryItem(name: "limit", value: String(limit))]
            }
            return nil

        case .widgetTimeline(let hours):
            return [URLQueryItem(name: "hours", value: String(hours))]

        case .searchEducation(let query):
            return [URLQueryItem(name: "query", value: query)]

        default:
            return nil
        }
    }
}

enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
    case patch = "PATCH"
    case delete = "DELETE"
}
