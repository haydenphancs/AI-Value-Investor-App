//
//  APIEndpoint.swift
//  ios
//
//  Type-Safe API Endpoint Definitions
//
//  Each endpoint defines:
//  - HTTP method
//  - Path
//  - Query parameters
//  - Request body
//  - Auth requirements
//  - Timeout
//

import Foundation

// MARK: - HTTP Method

enum HTTPMethod: String, Sendable {
    case GET
    case POST
    case PUT
    case PATCH
    case DELETE
}

// MARK: - API Endpoint

/// Type-safe endpoint definitions.
/// Add new endpoints here as the app grows.
enum APIEndpoint: Sendable {

    // MARK: - Auth
    case signIn(email: String, password: String)
    case signUp(email: String, password: String, displayName: String)
    case refreshToken(refreshToken: String)
    case signOut

    // MARK: - User
    case getCurrentUser
    case getUserCredits
    case updateProfile(displayName: String?, avatarUrl: String?)

    // MARK: - Stocks
    case searchStocks(query: String, limit: Int)
    case getStock(ticker: String)
    case getStockQuote(ticker: String)
    case getStockFundamentals(ticker: String)
    case getStockNews(ticker: String, limit: Int)

    // MARK: - Watchlist
    case getWatchlist
    case addToWatchlist(stockId: String)
    case removeFromWatchlist(stockId: String)

    // MARK: - Research
    case generateResearch(stockId: String, persona: String)
    case getResearchStatus(reportId: String)
    case getResearchReport(reportId: String)
    case getMyReports(limit: Int)
    case rateReport(reportId: String, rating: Int, feedback: String?)
    case deleteReport(reportId: String)

    // MARK: - News
    case getNewsFeed(page: Int, perPage: Int)
    case getNewsArticle(articleId: String)

    // MARK: - Chat
    case createChatSession(stockId: String?)
    case sendChatMessage(sessionId: String, message: String)
    case getChatHistory(sessionId: String)

    // MARK: - Personas
    case getPersonas

    // MARK: - Path

    var path: String {
        switch self {
        // Auth
        case .signIn:
            return "/api/v1/auth/login"
        case .signUp:
            return "/api/v1/auth/register"
        case .refreshToken:
            return "/api/v1/auth/refresh"
        case .signOut:
            return "/api/v1/auth/logout"

        // User
        case .getCurrentUser:
            return "/api/v1/users/me"
        case .getUserCredits:
            return "/api/v1/users/me/credits"
        case .updateProfile:
            return "/api/v1/users/me"

        // Stocks
        case .searchStocks:
            return "/api/v1/stocks/search"
        case .getStock(let ticker):
            return "/api/v1/stocks/\(ticker)"
        case .getStockQuote(let ticker):
            return "/api/v1/stocks/\(ticker)/quote"
        case .getStockFundamentals(let ticker):
            return "/api/v1/stocks/\(ticker)/fundamentals"
        case .getStockNews(let ticker, _):
            return "/api/v1/stocks/\(ticker)/news"

        // Watchlist
        case .getWatchlist:
            return "/api/v1/watchlist"
        case .addToWatchlist, .removeFromWatchlist:
            return "/api/v1/watchlist"

        // Research
        case .generateResearch:
            return "/api/v1/research/generate"
        case .getResearchStatus(let reportId):
            return "/api/v1/research/reports/\(reportId)/status"
        case .getResearchReport(let reportId):
            return "/api/v1/research/reports/\(reportId)"
        case .getMyReports:
            return "/api/v1/research/reports"
        case .rateReport(let reportId, _, _):
            return "/api/v1/research/reports/\(reportId)/rate"
        case .deleteReport(let reportId):
            return "/api/v1/research/reports/\(reportId)"

        // News
        case .getNewsFeed:
            return "/api/v1/news"
        case .getNewsArticle(let articleId):
            return "/api/v1/news/\(articleId)"

        // Chat
        case .createChatSession:
            return "/api/v1/chat/sessions"
        case .sendChatMessage(let sessionId, _):
            return "/api/v1/chat/sessions/\(sessionId)/messages"
        case .getChatHistory(let sessionId):
            return "/api/v1/chat/sessions/\(sessionId)"

        // Personas
        case .getPersonas:
            return "/api/v1/research/personas"
        }
    }

    // MARK: - Method

    var method: HTTPMethod {
        switch self {
        case .signIn, .signUp, .refreshToken, .signOut,
             .addToWatchlist, .generateResearch, .rateReport,
             .createChatSession, .sendChatMessage:
            return .POST

        case .updateProfile:
            return .PATCH

        case .removeFromWatchlist, .deleteReport:
            return .DELETE

        default:
            return .GET
        }
    }

    // MARK: - Query Parameters

    var queryParameters: [String: String]? {
        switch self {
        case .searchStocks(let query, let limit):
            return ["q": query, "limit": String(limit)]

        case .getStockNews(_, let limit):
            return ["limit": String(limit)]

        case .getMyReports(let limit):
            return ["limit": String(limit)]

        case .getNewsFeed(let page, let perPage):
            return ["page": String(page), "per_page": String(perPage)]

        default:
            return nil
        }
    }

    // MARK: - Body

    var body: Encodable? {
        switch self {
        case .signIn(let email, let password):
            return SignInRequest(email: email, password: password)

        case .signUp(let email, let password, let displayName):
            return SignUpRequest(email: email, password: password, displayName: displayName)

        case .refreshToken(let refreshToken):
            return RefreshTokenRequest(refreshToken: refreshToken)

        case .updateProfile(let displayName, let avatarUrl):
            return UpdateProfileRequest(displayName: displayName, avatarUrl: avatarUrl)

        case .addToWatchlist(let stockId):
            return AddToWatchlistRequest(stockId: stockId)

        case .removeFromWatchlist(let stockId):
            return RemoveFromWatchlistRequest(stockId: stockId)

        case .generateResearch(let stockId, let persona):
            return GenerateResearchRequest(stockId: stockId, investorPersona: persona)

        case .rateReport(_, let rating, let feedback):
            return RateReportRequest(rating: rating, feedback: feedback)

        case .createChatSession(let stockId):
            return CreateChatSessionRequest(stockId: stockId)

        case .sendChatMessage(_, let message):
            return SendChatMessageRequest(message: message)

        default:
            return nil
        }
    }

    // MARK: - Auth Required

    var requiresAuth: Bool {
        switch self {
        case .signIn, .signUp, .refreshToken, .getPersonas:
            return false
        default:
            return true
        }
    }

    // MARK: - Timeout

    var timeout: TimeInterval {
        switch self {
        case .generateResearch:
            return 120 // 2 minutes for AI generation
        case .sendChatMessage:
            return 60 // 1 minute for chat
        default:
            return 30 // 30 seconds default
        }
    }
}

// MARK: - Request Bodies

struct SignInRequest: Encodable, Sendable {
    let email: String
    let password: String
}

struct SignUpRequest: Encodable, Sendable {
    let email: String
    let password: String
    let displayName: String
}

struct RefreshTokenRequest: Encodable, Sendable {
    let refreshToken: String
}

struct UpdateProfileRequest: Encodable, Sendable {
    let displayName: String?
    let avatarUrl: String?
}

struct AddToWatchlistRequest: Encodable, Sendable {
    let stockId: String
}

struct RemoveFromWatchlistRequest: Encodable, Sendable {
    let stockId: String
}

struct GenerateResearchRequest: Encodable, Sendable {
    let stockId: String
    let investorPersona: String
}

struct RateReportRequest: Encodable, Sendable {
    let rating: Int
    let feedback: String?
}

struct CreateChatSessionRequest: Encodable, Sendable {
    let stockId: String?
}

struct SendChatMessageRequest: Encodable, Sendable {
    let message: String
}
