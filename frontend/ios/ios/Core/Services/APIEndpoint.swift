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
    case getStockChart(ticker: String, range: String)
    case getTickerReport(ticker: String, persona: String)

    // MARK: - Indices
    case getIndexDetail(symbol: String, range: String)

    // MARK: - Crypto
    case getCryptoDetail(symbol: String, range: String)

    // MARK: - ETFs
    case getETFDetail(symbol: String, range: String)

    // MARK: - Watchlist
    case getWatchlist
    case addToWatchlist(stockId: String)
    case removeFromWatchlist(stockId: String)

    // MARK: - Tracking
    case getTrackingAssets
    case getHoldings
    case addHolding(ticker: String, companyName: String?, marketValue: Double, assetType: String?)
    case updateHolding(ticker: String, marketValue: Double)
    case deleteHolding(ticker: String)

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
    case listChatSessions(limit: Int, offset: Int)
    case createChatSession(stockId: String?)
    case sendChatMessage(sessionId: String, message: String)
    case getChatHistory(sessionId: String)
    case updateChatSession(sessionId: String, title: String?, isSaved: Bool?)
    case deleteChatSession(sessionId: String)

    // MARK: - Ticker Report Chat
    case chatWithTickerReport(ticker: String, message: String, persona: String)

    // MARK: - Home
    case getHomeFeed

    // MARK: - Personas
    case getPersonas

    // MARK: - Path

    nonisolated var path: String {
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
        case .getStockChart(let ticker, _):
            return "/api/v1/stocks/\(ticker)/chart"
        case .getTickerReport(let ticker, _):
            return "/api/v1/stocks/\(ticker)/report"

        // Crypto
        case .getCryptoDetail(let symbol, _):
            return "/api/v1/crypto/\(symbol)"

        // Indices
        case .getIndexDetail(let symbol, _):
            return "/api/v1/indices/\(symbol)"

        // ETFs
        case .getETFDetail(let symbol, _):
            return "/api/v1/etfs/\(symbol)"

        // Watchlist
        case .getWatchlist:
            return "/api/v1/watchlist"
        case .addToWatchlist, .removeFromWatchlist:
            return "/api/v1/watchlist"

        // Tracking
        case .getTrackingAssets:
            return "/api/v1/tracking/assets"
        case .getHoldings:
            return "/api/v1/tracking/holdings"
        case .addHolding:
            return "/api/v1/tracking/holdings"
        case .updateHolding(let ticker, _):
            return "/api/v1/tracking/holdings/\(ticker)"
        case .deleteHolding(let ticker):
            return "/api/v1/tracking/holdings/\(ticker)"

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
        case .listChatSessions:
            return "/api/v1/chat/sessions"
        case .createChatSession:
            return "/api/v1/chat/sessions"
        case .sendChatMessage(let sessionId, _):
            return "/api/v1/chat/sessions/\(sessionId)/messages"
        case .getChatHistory(let sessionId):
            return "/api/v1/chat/sessions/\(sessionId)"
        case .updateChatSession(let sessionId, _, _):
            return "/api/v1/chat/sessions/\(sessionId)"
        case .deleteChatSession(let sessionId):
            return "/api/v1/chat/sessions/\(sessionId)"

        // Ticker Report Chat
        case .chatWithTickerReport(let ticker, _, _):
            return "/api/v1/stocks/\(ticker)/report/chat"

        // Home
        case .getHomeFeed:
            return "/api/v1/home/feed"

        // Personas
        case .getPersonas:
            return "/api/v1/research/personas"
        }
    }

    // MARK: - Method

    nonisolated var method: HTTPMethod {
        switch self {
        case .signIn, .signUp, .refreshToken, .signOut,
             .addToWatchlist, .generateResearch, .rateReport,
             .createChatSession, .sendChatMessage,
             .chatWithTickerReport, .addHolding:
            return .POST

        case .updateProfile, .updateChatSession:
            return .PATCH

        case .updateHolding:
            return .PUT

        case .removeFromWatchlist, .deleteReport, .deleteChatSession, .deleteHolding:
            return .DELETE

        default:
            return .GET
        }
    }

    // MARK: - Query Parameters

    nonisolated var queryParameters: [String: String]? {
        switch self {
        case .searchStocks(let query, let limit):
            return ["q": query, "limit": String(limit)]

        case .getStockNews(_, let limit):
            return ["limit": String(limit)]

        case .getStockChart(_, let range):
            return ["range": range]

        case .getTickerReport(_, let persona):
            return ["persona": persona]

        case .getCryptoDetail(_, let range):
            return ["range": range]

        case .getIndexDetail(_, let range):
            return ["range": range]

        case .getETFDetail(_, let range):
            return ["range": range]

        case .getMyReports(let limit):
            return ["limit": String(limit)]

        case .getNewsFeed(let page, let perPage):
            return ["page": String(page), "per_page": String(perPage)]

        case .listChatSessions(let limit, let offset):
            return ["limit": String(limit), "offset": String(offset)]

        default:
            return nil
        }
    }

    // MARK: - Body

    nonisolated var body: (any Encodable & Sendable)? {
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

        case .updateChatSession(_, let title, let isSaved):
            return UpdateChatSessionRequestBody(title: title, isSaved: isSaved)

        case .chatWithTickerReport(let ticker, let message, let persona):
            return TickerReportChatRequestBody(ticker: ticker, message: message, persona: persona)

        case .addHolding(let ticker, let companyName, let marketValue, let assetType):
            return AddHoldingRequestBody(ticker: ticker, companyName: companyName, marketValue: marketValue, assetType: assetType)

        case .updateHolding(_, let marketValue):
            return UpdateHoldingRequestBody(marketValue: marketValue)

        default:
            return nil
        }
    }

    // MARK: - Auth Required

    nonisolated var requiresAuth: Bool {
        switch self {
        // Auth endpoints
        case .signIn, .signUp, .refreshToken:
            return false
        // Stock/crypto endpoints are public on the backend
        case .searchStocks, .getStock, .getStockQuote, .getStockFundamentals, .getStockNews, .getStockChart,
             .getTickerReport, .chatWithTickerReport, .getCryptoDetail, .getIndexDetail, .getETFDetail:
            return false
        // News endpoints are public
        case .getNewsFeed, .getNewsArticle:
            return false
        // Home feed uses optional auth on the backend
        case .getHomeFeed:
            return false
        // Personas are public
        case .getPersonas:
            return false
        // Everything else requires auth
        default:
            return true
        }
    }

    // MARK: - Timeout

    nonisolated var timeout: TimeInterval {
        switch self {
        case .generateResearch, .getTickerReport:
            return 120 // 2 minutes for AI generation
        case .sendChatMessage, .chatWithTickerReport:
            return 60 // 1 minute for chat
        case .getCryptoDetail, .getIndexDetail, .getETFDetail:
            return 60 // 1 minute for AI snapshot generation
        default:
            return 30 // 30 seconds default
        }
    }
}

// MARK: - Request Bodies

nonisolated struct SignInRequest: Encodable, Sendable {
    let email: String
    let password: String
}

nonisolated struct SignUpRequest: Encodable, Sendable {
    let email: String
    let password: String
    let displayName: String
}

nonisolated struct RefreshTokenRequest: Encodable, Sendable {
    let refreshToken: String
}

nonisolated struct UpdateProfileRequest: Encodable, Sendable {
    let displayName: String?
    let avatarUrl: String?
}

nonisolated struct AddToWatchlistRequest: Encodable, Sendable {
    let stockId: String
}

nonisolated struct RemoveFromWatchlistRequest: Encodable, Sendable {
    let stockId: String
}

nonisolated struct GenerateResearchRequest: Encodable, Sendable {
    let stockId: String
    let investorPersona: String
}

nonisolated struct RateReportRequest: Encodable, Sendable {
    let rating: Int
    let feedback: String?
}

nonisolated struct CreateChatSessionRequest: Encodable, Sendable {
    let stockId: String?
}

nonisolated struct SendChatMessageRequest: Encodable, Sendable {
    let message: String
}

nonisolated struct UpdateChatSessionRequestBody: Encodable, Sendable {
    let title: String?
    let isSaved: Bool?
}

nonisolated struct TickerReportChatRequestBody: Encodable, Sendable {
    let ticker: String
    let message: String
    let persona: String
}

nonisolated struct AddHoldingRequestBody: Encodable, Sendable {
    let ticker: String
    let companyName: String?
    let marketValue: Double
    let assetType: String?
}

nonisolated struct UpdateHoldingRequestBody: Encodable, Sendable {
    let marketValue: Double
}
