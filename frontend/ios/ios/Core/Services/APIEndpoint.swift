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
    case getStockOverview(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false)
    case getStockOverviewCore(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false)
    case getStockQuote(ticker: String)
    case getStockFundamentals(ticker: String)
    case getStockNews(ticker: String, limit: Int)
    case enrichStockNews(ticker: String, articleIds: [String])
    case getStockChart(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false)
    case getAnalystAnalysis(ticker: String)
    case getSentimentAnalysis(ticker: String)
    case getTechnicalAnalysis(ticker: String)
    case getTechnicalAnalysisDetail(ticker: String)
    case getChartEvents(ticker: String)
    case getEarnings(ticker: String)
    case getGrowth(ticker: String)
    case getProfitPower(ticker: String)
    case getRevenueBreakdown(ticker: String)
    case getHealthCheck(ticker: String)
    case getSignalOfConfidence(ticker: String)
    case getHoldersData(ticker: String)
    case getTickerReport(ticker: String, persona: String)
    /// Fire-and-forget: warm the report's persona-neutral ticker_data_cache when
    /// the user opens the detail view, so a later Generate Analysis skips the
    /// ~20-call FMP fan-out. Best-effort — the result is ignored.
    case prewarmReportCollection(ticker: String)

    // MARK: - Indices
    case getIndexDetail(symbol: String, range: String, interval: String? = nil)
    case getIndexNews(symbol: String, limit: Int)
    case enrichIndexNews(symbol: String, articleIds: [String])

    // MARK: - Crypto
    case getCryptoDetail(symbol: String, range: String, interval: String? = nil)
    case getCryptoNews(symbol: String, limit: Int)
    case enrichCryptoNews(symbol: String, articleIds: [String])
    case getCryptoFearGreed
    case getCryptoSentiment(symbol: String)
    case getCryptoTechnicalAnalysis(symbol: String)
    case getCryptoTechnicalAnalysisDetail(symbol: String)

    // MARK: - ETFs
    case getETFDetail(symbol: String, range: String, interval: String? = nil)
    case getETFDividends(symbol: String)
    case getETFHoldingsRisk(symbol: String)
    case getETFProfile(symbol: String)

    // MARK: - Commodities
    case getCommodityDetail(symbol: String, range: String, interval: String? = nil)
    case getCommodityNews(symbol: String, limit: Int)
    case enrichCommodityNews(symbol: String, articleIds: [String])

    // MARK: - Watchlist
    case getWatchlist
    case addToWatchlist(stockId: String)
    case removeFromWatchlist(stockId: String)

    // MARK: - Tracking
    case getTrackingAssets
    case bulkUpdateHoldings(items: [HoldingUpdateItem])
    case getPortfolioInsights

    // MARK: - Portfolios
    case getPortfolios
    case createPortfolio(name: String)
    case renamePortfolio(id: String, name: String)
    case deletePortfolio(id: String)
    case setPortfolioTickers(id: String, tickers: [String])
    case setPortfolioHoldings(id: String, items: [HoldingUpdateItem])
    case reorderPortfolios(ids: [String])
    /// Server-computed diversification health score for one portfolio.
    case getPortfolioInsightsForPortfolio(id: String)

    // MARK: - Research
    case generateResearch(stockId: String, persona: String)
    case getResearchStatus(reportId: String)
    case getResearchReport(reportId: String)
    case getResearchReportPDF(reportId: String)
    case regenerateResearchReportPDF(reportId: String)
    /// Returns the full TickerReportResponse cached in `ticker_report_data`
    /// for a completed research report. Faster than /stocks/{ticker}/report
    /// and preserves the persona used at generation time.
    case getResearchTickerReport(reportId: String)
    case getMyReports(limit: Int)
    case rateReport(reportId: String, rating: Int, feedback: String?)
    case deleteReport(reportId: String)

    // MARK: - News
    case getNewsFeed(page: Int, perPage: Int)
    case getNewsArticle(articleId: String)

    // MARK: - Updates screen
    /// Filter pills for the Updates tab bar: "Market" + the user's watchlist,
    /// each with its session change %. Optional auth (guest-safe).
    case getUpdatesTabs
    /// One tab's content — news timeline AND the AI Insights card in a single
    /// round trip, so switching tabs costs one request, not two.
    case getUpdatesFeed(scope: String, limit: Int)
    /// On-demand AI enrichment (bullets + sentiment) for specific articles.
    case enrichUpdatesNews(scope: String, articleIds: [String])

    // MARK: - Chat
    case listChatSessions(limit: Int, offset: Int)
    case createChatSession(stockId: String?, contextType: String? = nil, referenceId: String? = nil)
    case sendChatMessage(sessionId: String, message: String, context: String? = nil, contextType: String? = nil, referenceId: String? = nil)
    case streamChatMessage(sessionId: String, message: String, context: String? = nil, contextType: String? = nil, referenceId: String? = nil)
    case getChatHistory(sessionId: String)
    case updateChatSession(sessionId: String, title: String?, isSaved: Bool?)
    case deleteChatSession(sessionId: String)

    // MARK: - Ticker Report Chat
    case chatWithTickerReport(ticker: String, message: String, persona: String)

    // MARK: - Whales
    case getWhaleList(category: String?)
    case getWhaleActivity
    case getWhaleProfile(whaleId: String)
    case getWhaleTradeGroups(whaleId: String)
    case getWhaleTradeGroupDetail(whaleId: String, groupId: String)
    case followWhale(whaleId: String)
    case unfollowWhale(whaleId: String)

    // MARK: - Home
    case getHomeFeed
    case getHomeDashboard
    case getSignalDetail(kind: String, ticker: String)
    case getThemeDetail(slug: String)

    // MARK: - Learn / Investor Journey
    case getJourney

    // MARK: - Learn / Money Moves
    case getMoneyMoves

    // MARK: - Learn progress (unified completion log; contentType = book_core|journey_lesson|money_move)
    case getLearnProgress(contentType: String)
    case completeLearnItem(contentType: String, key: String)
    case uncompleteLearnItem(contentType: String, key: String)

    // MARK: - Learn / Book bookmarks (account-synced; key = book title)
    case getBookBookmarks
    case addBookBookmark(key: String)
    case removeBookBookmark(key: String)

    // MARK: - Personas
    case getPersonas

    // MARK: - Trending
    case getTrendingAnalyses

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
        case .getStockOverviewCore(let ticker, _, _, _):
            return "/api/v1/stocks/\(ticker)/overview/core"
        case .getStockOverview(let ticker, _, _, _):
            return "/api/v1/stocks/\(ticker)/overview"
        case .prewarmReportCollection(let ticker):
            return "/api/v1/stocks/\(ticker)/prewarm-report"
        case .getStockQuote(let ticker):
            return "/api/v1/stocks/\(ticker)/quote"
        case .getStockFundamentals(let ticker):
            return "/api/v1/stocks/\(ticker)/fundamentals"
        case .getStockNews(let ticker, _):
            return "/api/v1/stocks/\(ticker)/news"
        case .enrichStockNews(let ticker, _):
            return "/api/v1/stocks/\(ticker)/news/enrich"
        case .getStockChart(let ticker, _, _, _):
            return "/api/v1/stocks/\(ticker)/chart"
        case .getAnalystAnalysis(let ticker):
            return "/api/v1/stocks/\(ticker)/analyst-analysis"
        case .getSentimentAnalysis(let ticker):
            return "/api/v1/stocks/\(ticker)/sentiment"
        case .getTechnicalAnalysis(let ticker):
            return "/api/v1/stocks/\(ticker)/technical-analysis"
        case .getTechnicalAnalysisDetail(let ticker):
            return "/api/v1/stocks/\(ticker)/technical-analysis/detail"
        case .getChartEvents(let ticker):
            return "/api/v1/stocks/\(ticker)/chart-events"
        case .getEarnings(let ticker):
            return "/api/v1/stocks/\(ticker)/earnings"
        case .getGrowth(let ticker):
            return "/api/v1/stocks/\(ticker)/growth"
        case .getProfitPower(let ticker):
            return "/api/v1/stocks/\(ticker)/profit-power"
        case .getRevenueBreakdown(let ticker):
            return "/api/v1/stocks/\(ticker)/revenue-breakdown"
        case .getHealthCheck(let ticker):
            return "/api/v1/stocks/\(ticker)/health-check"
        case .getSignalOfConfidence(let ticker):
            return "/api/v1/stocks/\(ticker)/signal-of-confidence"
        case .getHoldersData(let ticker):
            return "/api/v1/stocks/\(ticker)/holders"
        case .getTickerReport(let ticker, _):
            return "/api/v1/stocks/\(ticker)/report"

        // Crypto
        case .getCryptoDetail(let symbol, _, _):
            return "/api/v1/crypto/\(symbol)"
        case .getCryptoNews(let symbol, _):
            return "/api/v1/crypto/\(symbol)/news"
        case .enrichCryptoNews(let symbol, _):
            return "/api/v1/crypto/\(symbol)/news/enrich"
        case .getCryptoFearGreed:
            return "/api/v1/crypto/fear-greed"
        case .getCryptoSentiment(let symbol):
            return "/api/v1/crypto/\(symbol)/sentiment"
        case .getCryptoTechnicalAnalysis(let symbol):
            return "/api/v1/crypto/\(symbol)/technical-analysis"
        case .getCryptoTechnicalAnalysisDetail(let symbol):
            return "/api/v1/crypto/\(symbol)/technical-analysis/detail"

        // Indices
        case .getIndexDetail(let symbol, _, _):
            return "/api/v1/indices/\(symbol)"
        case .getIndexNews(let symbol, _):
            return "/api/v1/indices/\(symbol)/news"
        case .enrichIndexNews(let symbol, _):
            return "/api/v1/indices/\(symbol)/news/enrich"

        // ETFs
        case .getETFDetail(let symbol, _, _):
            return "/api/v1/etfs/\(symbol)"
        case .getETFDividends(let symbol):
            return "/api/v1/etfs/\(symbol)/dividends"
        case .getETFHoldingsRisk(let symbol):
            return "/api/v1/etfs/\(symbol)/holdings-risk"
        case .getETFProfile(let symbol):
            return "/api/v1/etfs/\(symbol)/profile"

        // Commodities
        case .getCommodityDetail(let symbol, _, _):
            return "/api/v1/commodities/\(symbol)"
        case .getCommodityNews(let symbol, _):
            return "/api/v1/commodities/\(symbol)/news"
        case .enrichCommodityNews(let symbol, _):
            return "/api/v1/commodities/\(symbol)/news/enrich"

        // Watchlist
        case .getWatchlist:
            return "/api/v1/watchlist"
        case .addToWatchlist, .removeFromWatchlist:
            return "/api/v1/watchlist"

        // Tracking
        case .getTrackingAssets:
            return "/api/v1/tracking/assets"
        case .bulkUpdateHoldings:
            return "/api/v1/tracking/assets/holdings"
        case .getPortfolioInsights:
            return "/api/v1/tracking/portfolio-insights"

        // Portfolios
        case .getPortfolios, .createPortfolio:
            return "/api/v1/portfolios"
        case .renamePortfolio(let id, _), .deletePortfolio(let id):
            return "/api/v1/portfolios/\(id)"
        case .setPortfolioTickers(let id, _):
            return "/api/v1/portfolios/\(id)/tickers"
        case .setPortfolioHoldings(let id, _):
            return "/api/v1/portfolios/\(id)/holdings"
        case .reorderPortfolios:
            return "/api/v1/portfolios/reorder"
        case .getPortfolioInsightsForPortfolio(let id):
            return "/api/v1/portfolios/\(id)/insights"

        // Research
        case .generateResearch:
            return "/api/v1/research/generate"
        case .getResearchStatus(let reportId):
            return "/api/v1/research/reports/\(reportId)/status"
        case .getResearchReport(let reportId):
            return "/api/v1/research/reports/\(reportId)"
        case .getResearchReportPDF(let reportId):
            return "/api/v1/research/reports/\(reportId)/pdf"
        case .regenerateResearchReportPDF(let reportId):
            return "/api/v1/research/reports/\(reportId)/pdf/regenerate"
        case .getResearchTickerReport(let reportId):
            return "/api/v1/research/reports/\(reportId)/ticker-report"
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

        // Updates screen
        case .getUpdatesTabs:
            return "/api/v1/updates/tabs"
        case .getUpdatesFeed:
            return "/api/v1/updates/feed"
        case .enrichUpdatesNews:
            return "/api/v1/updates/news/enrich"

        // Chat
        case .listChatSessions:
            return "/api/v1/chat/sessions"
        case .createChatSession:
            return "/api/v1/chat/sessions"
        case .sendChatMessage(let sessionId, _, _, _, _):
            return "/api/v1/chat/sessions/\(sessionId)/messages"
        case .streamChatMessage(let sessionId, _, _, _, _):
            return "/api/v1/chat/sessions/\(sessionId)/messages/stream"
        case .getChatHistory(let sessionId):
            return "/api/v1/chat/sessions/\(sessionId)"
        case .updateChatSession(let sessionId, _, _):
            return "/api/v1/chat/sessions/\(sessionId)"
        case .deleteChatSession(let sessionId):
            return "/api/v1/chat/sessions/\(sessionId)"

        // Ticker Report Chat
        case .chatWithTickerReport(let ticker, _, _):
            return "/api/v1/stocks/\(ticker)/report/chat"

        // Whales
        case .getWhaleList:
            return "/api/v1/whales"
        case .getWhaleActivity:
            return "/api/v1/whales/activity"
        case .getWhaleProfile(let whaleId):
            return "/api/v1/whales/\(whaleId)/profile"
        case .getWhaleTradeGroups(let whaleId):
            return "/api/v1/whales/\(whaleId)/trade-groups"
        case .getWhaleTradeGroupDetail(let whaleId, let groupId):
            return "/api/v1/whales/\(whaleId)/trade-groups/\(groupId)"
        case .followWhale(let whaleId):
            return "/api/v1/whales/\(whaleId)/follow"
        case .unfollowWhale(let whaleId):
            return "/api/v1/whales/\(whaleId)/follow"

        // Home
        case .getHomeFeed:
            return "/api/v1/home/feed"
        case .getHomeDashboard:
            return "/api/v1/home/dashboard"
        case .getSignalDetail(let kind, let ticker):
            return "/api/v1/home/signals/\(kind)/\(ticker)"
        case .getThemeDetail(let slug):
            return "/api/v1/home/themes/\(slug)"

        // Learn / Investor Journey
        case .getJourney:
            return "/api/v1/learn/journey"

        // Learn / Money Moves
        case .getMoneyMoves:
            return "/api/v1/learn/money-moves"

        // Learn / Book Library progress
        case .getLearnProgress(let contentType):
            return "/api/v1/learn/progress/\(contentType)"
        case .completeLearnItem(let contentType, _):
            return "/api/v1/learn/progress/\(contentType)"
        case .uncompleteLearnItem(let contentType, _):
            return "/api/v1/learn/progress/\(contentType)"

        // Learn / Book bookmarks (key travels in the body, not the path)
        case .getBookBookmarks, .addBookBookmark, .removeBookBookmark:
            return "/api/v1/learn/bookmarks"

        // Personas
        case .getPersonas:
            return "/api/v1/research/personas"

        // Trending
        case .getTrendingAnalyses:
            return "/api/v1/research/trending"
        }
    }

    // MARK: - Method

    nonisolated var method: HTTPMethod {
        switch self {
        case .signIn, .signUp, .refreshToken, .signOut,
             .addToWatchlist, .generateResearch, .rateReport,
             .createChatSession, .sendChatMessage, .streamChatMessage,
             .chatWithTickerReport, .completeLearnItem, .addBookBookmark,
             .followWhale, .enrichStockNews, .enrichCryptoNews, .enrichIndexNews, .enrichCommodityNews,
             .enrichUpdatesNews,
             .createPortfolio, .regenerateResearchReportPDF,
             .prewarmReportCollection:
            return .POST

        case .updateProfile, .updateChatSession:
            return .PATCH

        case .bulkUpdateHoldings,
             .renamePortfolio, .setPortfolioTickers, .setPortfolioHoldings, .reorderPortfolios:
            return .PUT

        case .removeFromWatchlist, .deleteReport, .deleteChatSession,
             .unfollowWhale, .deletePortfolio, .removeBookBookmark, .uncompleteLearnItem:
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

        case .getUpdatesFeed(let scope, let limit):
            return ["scope": scope, "limit": String(limit)]

        case .getCryptoNews(_, let limit):
            return ["limit": String(limit)]

        case .getIndexNews(_, let limit):
            return ["limit": String(limit)]

        case .getCommodityNews(_, let limit):
            return ["limit": String(limit)]

        case .getStockOverview(_, let range, let interval, let extendedHours),
             .getStockOverviewCore(_, let range, let interval, let extendedHours):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            if extendedHours { params["extended_hours"] = "true" }
            return params

        case .getStockChart(_, let range, let interval, let extendedHours):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            if extendedHours { params["extended_hours"] = "true" }
            return params

        case .getTickerReport(_, let persona):
            return ["persona": persona]

        case .getCryptoDetail(_, let range, let interval):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            return params

        case .getIndexDetail(_, let range, let interval):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            return params

        case .getETFDetail(_, let range, let interval):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            return params

        case .getCommodityDetail(_, let range, let interval):
            var params = ["range": range]
            if let interval = interval { params["interval"] = interval }
            return params

        case .getMyReports(let limit):
            return ["limit": String(limit)]

        case .getNewsFeed(let page, let perPage):
            return ["page": String(page), "per_page": String(perPage)]

        case .listChatSessions(let limit, let offset):
            return ["limit": String(limit), "offset": String(offset)]

        case .getWhaleList(let category):
            if let category = category {
                return ["category": category]
            }
            return nil

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

        case .completeLearnItem(_, let key), .uncompleteLearnItem(_, let key):
            return CompleteLearnItemRequest(key: key)

        case .addBookBookmark(let key), .removeBookBookmark(let key):
            return BookBookmarkRequest(bookKey: key)

        case .removeFromWatchlist(let stockId):
            return RemoveFromWatchlistRequest(stockId: stockId)

        case .generateResearch(let stockId, let persona):
            return GenerateResearchRequest(stockId: stockId, investorPersona: persona)

        case .rateReport(_, let rating, let feedback):
            return RateReportRequest(rating: rating, feedback: feedback)

        case .createChatSession(let stockId, let contextType, let referenceId):
            return CreateChatSessionRequest(stockId: stockId, contextType: contextType, referenceId: referenceId)

        case .sendChatMessage(_, let message, let context, let contextType, let referenceId):
            return SendChatMessageRequest(message: message, context: context, contextType: contextType, referenceId: referenceId)

        case .streamChatMessage(_, let message, let context, let contextType, let referenceId):
            return SendChatMessageRequest(message: message, context: context, contextType: contextType, referenceId: referenceId)

        case .updateChatSession(_, let title, let isSaved):
            return UpdateChatSessionRequestBody(title: title, isSaved: isSaved)

        case .chatWithTickerReport(let ticker, let message, let persona):
            return TickerReportChatRequestBody(ticker: ticker, message: message, persona: persona)

        case .bulkUpdateHoldings(let items):
            // FastAPI accepts a JSON array as the body when the route handler
            // declares its parameter as `List[BulkHoldingUpdateItem]`.
            return items

        case .enrichStockNews(_, let articleIds):
            return EnrichStockNewsRequest(articleIds: articleIds)

        case .enrichCryptoNews(_, let articleIds):
            return EnrichStockNewsRequest(articleIds: articleIds)

        case .enrichIndexNews(_, let articleIds):
            return EnrichStockNewsRequest(articleIds: articleIds)

        case .enrichCommodityNews(_, let articleIds):
            return EnrichStockNewsRequest(articleIds: articleIds)

        case .enrichUpdatesNews(let scope, let articleIds):
            return EnrichUpdatesNewsRequest(scope: scope, articleIds: articleIds)

        case .createPortfolio(let name):
            return CreatePortfolioRequestBody(name: name)

        case .renamePortfolio(_, let name):
            return RenamePortfolioRequestBody(name: name)

        case .setPortfolioTickers(_, let tickers):
            return SetPortfolioTickersRequestBody(tickers: tickers)

        case .setPortfolioHoldings(_, let items):
            return SetPortfolioHoldingsRequestBody(items: items)

        case .reorderPortfolios(let ids):
            return ReorderPortfoliosRequestBody(portfolioIds: ids)

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
        // Stock/crypto/commodity endpoints are public on the backend
        case .searchStocks, .getStock, .getStockOverview, .getStockOverviewCore, .getStockQuote, .getStockFundamentals, .getStockNews, .getStockChart,
             .getAnalystAnalysis, .getSentimentAnalysis, .getTechnicalAnalysis, .getTechnicalAnalysisDetail,
             .getChartEvents, .getEarnings, .getGrowth, .getProfitPower, .getRevenueBreakdown, .getHealthCheck, .getSignalOfConfidence, .getTickerReport, .prewarmReportCollection, .chatWithTickerReport, .getCryptoDetail, .getCryptoNews, .enrichCryptoNews, .getCryptoFearGreed, .getCryptoSentiment, .getCryptoTechnicalAnalysis, .getCryptoTechnicalAnalysisDetail, .getIndexDetail, .getIndexNews, .enrichIndexNews, .getETFDetail, .getETFDividends, .getETFHoldingsRisk, .getETFProfile, .getCommodityDetail, .getCommodityNews, .enrichCommodityNews:
            return false
        // News endpoints are public
        case .getNewsFeed, .getNewsArticle:
            return false
        // Updates: /tabs uses OPTIONAL auth (guest watchlist when signed out);
        // /feed and /news/enrich are fully public market data.
        case .getUpdatesTabs, .getUpdatesFeed, .enrichUpdatesNews:
            return false
        // Whale list/profile/trade-groups use optional auth (token sent if available)
        case .getWhaleList, .getWhaleProfile, .getWhaleTradeGroups, .getWhaleTradeGroupDetail:
            return false
        // Home feed + dashboard use optional auth on the backend (public market data)
        case .getHomeFeed, .getHomeDashboard, .getSignalDetail, .getThemeDetail:
            return false
        // Journey lesson content is public
        case .getJourney:
            return false
        // Money Moves article content is public
        case .getMoneyMoves:
            return false
        // Learn progress uses optional auth (token sent if signed in; guests still work via cache)
        case .getLearnProgress, .completeLearnItem, .uncompleteLearnItem:
            return false
        // Book bookmarks use optional auth (token sent if signed in; guests still work via cache)
        case .getBookBookmarks, .addBookBookmark, .removeBookBookmark:
            return false
        // Personas are public
        case .getPersonas:
            return false
        // Trending is public
        case .getTrendingAnalyses:
            return false
        // Chat endpoints use optional auth (guest access)
        case .listChatSessions, .createChatSession, .sendChatMessage, .streamChatMessage, .getChatHistory,
             .updateChatSession, .deleteChatSession:
            return false
        // Everything else requires auth
        default:
            return true
        }
    }

    // MARK: - Timeout

    nonisolated var timeout: TimeInterval {
        switch self {
        case .generateResearch, .getTickerReport, .regenerateResearchReportPDF:
            return 120 // 2 minutes for AI generation / inline PDF render
        case .getResearchReportPDF:
            return 60 // binary PDF download
        case .sendChatMessage, .chatWithTickerReport:
            return 60 // 1 minute for chat
        case .streamChatMessage:
            return 120 // SSE stream stays open while tokens arrive
        case .getTechnicalAnalysis, .getTechnicalAnalysisDetail,
             .getCryptoTechnicalAnalysis, .getCryptoTechnicalAnalysisDetail:
            return 45 // 45 seconds for technical indicator computation
        case .getStockOverview:
            return 60 // 1 minute for aggregated overview (many FMP calls)
        case .getStockOverviewCore:
            return 20 // fast subset (quote + chart + profile); surface a hang quickly
        case .getCryptoDetail, .getIndexDetail, .getETFDetail, .getCommodityDetail:
            return 60 // 1 minute for aggregated detail
        case .getCryptoSentiment:
            return 60 // 1 minute — first call may warm ApeWisdom cache
        case .getWhaleProfile:
            return 60 // 1 minute for whale profile (may fetch from FMP)
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
    var contextType: String? = nil   // ChatContextType raw value (screen the user asks from)
    var referenceId: String? = nil   // ticker / "TICKER|persona" / slug / book order
}

nonisolated struct SendChatMessageRequest: Encodable, Sendable {
    let message: String
    let context: String?
    var contextType: String? = nil   // per-message override of the session's context type
    var referenceId: String? = nil   // per-message override of the session's reference id
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

/// One row of the bulk-update payload sent to ``PUT /tracking/assets/holdings``.
/// Setting both ``shares`` and ``marketValue`` to nil clears the holding values
/// for that ticker — the row stays on the user's watchlist but stops counting
/// toward the diversification score.
nonisolated struct HoldingUpdateItem: Encodable, Sendable {
    let ticker: String
    let shares: Double?
    let marketValue: Double?

    enum CodingKeys: String, CodingKey {
        case ticker
        case shares
        case marketValue = "market_value"
    }
}

nonisolated struct EnrichStockNewsRequest: Encodable, Sendable {
    let articleIds: [String]

    enum CodingKeys: String, CodingKey {
        case articleIds = "article_ids"
    }
}

/// Updates-screen enrichment. Unlike the per-asset enrich endpoints the scope
/// travels in the BODY, not the path — the Market feed's scope (`__MARKET__`)
/// is not a URL-safe ticker.
nonisolated struct EnrichUpdatesNewsRequest: Encodable, Sendable {
    let scope: String
    let articleIds: [String]

    enum CodingKeys: String, CodingKey {
        case scope
        case articleIds = "article_ids"
    }
}

nonisolated struct BookBookmarkRequest: Encodable, Sendable {
    let bookKey: String

    enum CodingKeys: String, CodingKey {
        case bookKey = "book_key"
    }
}
