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

// MARK: - Auth Policy

/// What an endpoint needs from the caller's identity — the client-side mirror of which
/// FastAPI dependency the route hangs off.
///
/// There are exactly three answers, and every endpoint must pick one (see
/// `APIEndpoint.authPolicy`, whose switch is exhaustive on purpose).
enum AuthPolicy: Sendable, Equatable {
    /// The backend reads no identity. Fine to call with or without a token.
    case `public`

    /// Per-identity, but a signed-out caller is a first-class user: the backend resolves them
    /// to a per-INSTALL guest partition off the `X-Guest-Id` header. Never gate these on a
    /// token — doing so removes a working feature from every guest.
    case guestAllowed

    /// The backend uses strict `get_current_user` / `get_current_user_id`. A tokenless call is
    /// refused, so the client asks for sign-in instead of making it.
    case signInRequired

    /// True when the request is still meaningful without a credential — i.e. everything except
    /// `.signInRequired`.
    ///
    /// Load-bearing for the self-heal path: when a token is rejected and the refresh fails,
    /// `APIClient` clears the credential and retries **tokenless** for these, so a dead session
    /// degrades a guest-capable screen to guest content instead of to an error. It covers
    /// `.public` as well as `.guestAllowed` because a public route can still 401 on a bad
    /// token — several attach a rate limiter that resolves identity.
    var isUsableWithoutCredential: Bool { self != .signInRequired }
}

// MARK: - API Endpoint

/// Type-safe endpoint definitions.
/// Add new endpoints here as the app grows.
enum APIEndpoint: Sendable {

    // MARK: - Analytics
    /// Batched product-analytics events. Fire-and-forget; the backend always
    /// returns 200 (see backend/app/api/v1/endpoints/analytics.py).
    case trackEvents(AnalyticsBatchRequest)

    // MARK: - Auth
    case signIn(email: String, password: String)
    case signUp(email: String, password: String, displayName: String)
    case refreshToken(refreshToken: String)
    case signOut
    /// Start a password reset — sends a one-time code to the address.
    case forgotPassword(email: String)
    /// Finish a password reset with the emailed code.
    case resetPassword(email: String, code: String, newPassword: String)
    /// Change the password of a signed-in user (requires the current one).
    case changePassword(currentPassword: String, newPassword: String)
    /// Re-send the signup confirmation email.
    case resendConfirmation(email: String)
    /// Native social sign-in with a provider identity token (Sign in with Apple).
    case oauthSignIn(provider: String, idToken: String, nonce: String?, displayName: String?)
    /// Exchange a Supabase access token for app tokens (web OAuth flow, e.g. Google).
    case sessionExchange(supabaseAccessToken: String)

    // MARK: - User
    case getCurrentUser
    case getUserCredits
    case updateProfile(displayName: String?, avatarUrl: String?)
    case deleteAccount
    /// Move this install's guest watchlist + portfolios onto the account that just signed in.
    /// Migration 108 partitions guests per install, so without this call the guest-first funnel
    /// silently costs the user their work: they add tickers during onboarding, create an account,
    /// and land on an empty watchlist. The install id travels in the `X-Guest-Id` header that
    /// `APIClient` already attaches to every request — no parameters needed here.
    case claimGuestData

    // MARK: - Billing / Subscription / Settings / Devices
    case getPlanCatalog                                    // public tier catalog (guest-safe)
    case getCreditPackCatalog                              // public consumable catalog (guest-safe)
    case getMySubscription                                 // current user's entitlement
    case getMySettings                                     // synced preference blob
    case updateMySettings(preferences: [String: PreferenceValue])
    case registerDevice(token: String, platform: String, environment: String?)
    /// Detach this device's APNs token on sign-out. Without it the token stays bound to the
    /// account that registered it (`device_tokens.token` is UNIQUE and only a NEW registration
    /// re-binds it), so a signed-out phone keeps receiving the previous account's alerts.
    case unregisterDevice(token: String)
    /// Submit an Apple-signed StoreKit 2 transaction for server-side verification.
    /// The signed blob is the ONLY thing sent — the tier is derived server-side.
    case verifyPurchase(signedTransaction: String)

    // MARK: - Notifications / Price alerts
    /// Inbox page. `before` is a KEYSET cursor (the previous page's last `createdAt`),
    /// not an offset — rows arrive continuously at the head, so an offset page 2 would
    /// repeat or skip whenever a notification landed between requests.
    case listNotifications(limit: Int, before: String?)
    case markNotificationsRead(ids: [String], all: Bool)
    case listPriceAlerts(ticker: String?)
    case createPriceAlert(ticker: String, kind: String, threshold: Double, assetType: String, repeatMode: String)
    case updatePriceAlert(id: String, threshold: Double?, isActive: Bool?, repeatMode: String?)
    case deletePriceAlert(id: String)

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
    case getETFNews(symbol: String, limit: Int)
    case enrichETFNews(symbol: String, articleIds: [String])

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
    /// Make this the user's active group — the one Home, Updates and Tracking follow.
    /// Server-side (migration 126) because the selection has to outlive the device: it
    /// used to be a local `UserDefaults` string, so the backend could not make the other
    /// two screens follow it and switching groups did not sync across devices.
    case activatePortfolio(id: String)
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

    // MARK: - Updates screen
    /// Filter pills for the Updates tab bar: "Market" + the user's watchlist,
    /// each with its session change %. Optional auth (guest-safe).
    case getUpdatesTabs
    /// One tab's content — news timeline AND the AI Insights card in a single
    /// round trip, so switching tabs costs one request, not two.
    case getUpdatesFeed(scope: String, limit: Int, offset: Int = 0)
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
        // Analytics
        case .trackEvents:
            return "/api/v1/events"

        // Auth
        case .signIn:
            return "/api/v1/auth/login"
        case .signUp:
            return "/api/v1/auth/register"
        case .refreshToken:
            return "/api/v1/auth/refresh"
        case .signOut:
            return "/api/v1/auth/logout"
        case .forgotPassword:
            return "/api/v1/auth/forgot-password"
        case .resetPassword:
            return "/api/v1/auth/reset-password"
        case .changePassword:
            return "/api/v1/auth/change-password"
        case .resendConfirmation:
            return "/api/v1/auth/resend-confirmation"
        case .oauthSignIn:
            return "/api/v1/auth/oauth"
        case .sessionExchange:
            return "/api/v1/auth/session-exchange"

        // User
        case .getCurrentUser:
            return "/api/v1/users/me"
        case .getUserCredits:
            return "/api/v1/users/me/credits"
        case .updateProfile, .deleteAccount:
            return "/api/v1/users/me"
        case .claimGuestData:
            return "/api/v1/users/me/claim-guest-data"

        // Billing / Subscription / Settings / Devices
        case .getPlanCatalog:
            return "/api/v1/billing/plans"
        case .getCreditPackCatalog:
            return "/api/v1/billing/credit-packs"
        case .verifyPurchase:
            return "/api/v1/billing/verify"
        case .getMySubscription:
            return "/api/v1/users/me/subscription"
        case .getMySettings, .updateMySettings:
            return "/api/v1/users/me/settings"
        case .registerDevice, .unregisterDevice:
            return "/api/v1/users/me/devices"

        // Notifications / price alerts
        case .listNotifications:
            return "/api/v1/users/me/notifications"
        case .markNotificationsRead:
            return "/api/v1/users/me/notifications/read"
        case .listPriceAlerts, .createPriceAlert:
            return "/api/v1/alerts/price"
        case .updatePriceAlert(let id, _, _, _):
            return "/api/v1/alerts/price/\(id)"
        case .deletePriceAlert(let id):
            return "/api/v1/alerts/price/\(id)"

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
        case .getETFNews(let symbol, _):
            return "/api/v1/etfs/\(symbol)/news"
        case .enrichETFNews(let symbol, _):
            return "/api/v1/etfs/\(symbol)/news/enrich"

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
        case .activatePortfolio(let id):
            return "/api/v1/portfolios/\(id)/activate"
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
        case .trackEvents,
             .signIn, .signUp, .refreshToken, .signOut,
             .forgotPassword, .resetPassword, .changePassword,
             .resendConfirmation, .oauthSignIn, .sessionExchange, .verifyPurchase,
             .addToWatchlist, .generateResearch, .rateReport,
             .createChatSession, .sendChatMessage, .streamChatMessage,
             .chatWithTickerReport, .completeLearnItem, .addBookBookmark,
             .followWhale, .enrichStockNews, .enrichCryptoNews, .enrichIndexNews, .enrichCommodityNews,
             .enrichETFNews,
             .enrichUpdatesNews,
             .createPortfolio, .regenerateResearchReportPDF,
             .prewarmReportCollection,
             .claimGuestData:
            return .POST

        case .updateProfile, .updateChatSession:
            return .PATCH

        case .bulkUpdateHoldings,
             .renamePortfolio, .setPortfolioTickers, .setPortfolioHoldings, .reorderPortfolios,
             .activatePortfolio,
             .updateMySettings:
            return .PUT

        case .registerDevice, .markNotificationsRead, .createPriceAlert:
            return .POST

        case .updatePriceAlert:
            return .PATCH

        case .removeFromWatchlist, .deleteReport, .deleteChatSession,
             .unfollowWhale, .deletePortfolio, .removeBookBookmark, .uncompleteLearnItem,
             .deleteAccount, .unregisterDevice, .deletePriceAlert:
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

        case .listNotifications(let limit, let before):
            var q = ["limit": String(limit)]
            // Omitted on the first page so that request line stays byte-identical.
            if let before, !before.isEmpty { q["before"] = before }
            return q

        case .listPriceAlerts(let ticker):
            // No ticker = the whole list (the settings screen); a ticker = just that
            // symbol's rules, which is what the detail-screen bell needs.
            guard let ticker, !ticker.isEmpty else { return nil }
            return ["ticker": ticker.uppercased()]

        case .getStockNews(_, let limit):
            return ["limit": String(limit)]

        case .getUpdatesFeed(let scope, let limit, let offset):
            var q = ["scope": scope, "limit": String(limit)]
            // Omit offset on page 0 so the request line stays byte-identical to
            // the pre-pagination one — same URL, same CDN/cache key.
            if offset > 0 { q["offset"] = String(offset) }
            return q

        case .getCryptoNews(_, let limit):
            return ["limit": String(limit)]

        case .getIndexNews(_, let limit):
            return ["limit": String(limit)]

        case .getCommodityNews(_, let limit):
            return ["limit": String(limit)]

        case .getETFNews(_, let limit):
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
        case .trackEvents(let batch):
            return batch

        case .signIn(let email, let password):
            return SignInRequest(email: email, password: password)

        case .signUp(let email, let password, let displayName):
            return SignUpRequest(email: email, password: password, displayName: displayName)

        case .refreshToken(let refreshToken):
            return RefreshTokenRequest(refreshToken: refreshToken)

        case .forgotPassword(let email):
            return ForgotPasswordRequest(email: email)

        case .resetPassword(let email, let code, let newPassword):
            return ResetPasswordRequest(email: email, code: code, newPassword: newPassword)

        case .changePassword(let currentPassword, let newPassword):
            return ChangePasswordRequest(
                currentPassword: currentPassword, newPassword: newPassword
            )

        case .resendConfirmation(let email):
            return ResendConfirmationRequest(email: email)

        case .oauthSignIn(let provider, let idToken, let nonce, let displayName):
            return OAuthSignInRequest(
                provider: provider, idToken: idToken, nonce: nonce, displayName: displayName
            )

        case .sessionExchange(let supabaseAccessToken):
            return SessionExchangeRequest(supabaseAccessToken: supabaseAccessToken)

        case .verifyPurchase(let signedTransaction):
            return VerifyPurchaseRequestBody(signedTransaction: signedTransaction)

        case .updateProfile(let displayName, let avatarUrl):
            return UpdateProfileRequest(displayName: displayName, avatarUrl: avatarUrl)

        case .updateMySettings(let preferences):
            return UpdateUserSettingsRequestBody(preferences: preferences)

        case .registerDevice(let token, let platform, let environment):
            return DeviceRegisterRequestBody(token: token, platform: platform, environment: environment)

        case .unregisterDevice(let token):
            // Same body shape; the backend reads only `token` and scopes the delete to the caller.
            return DeviceRegisterRequestBody(token: token, platform: "ios", environment: nil)

        case .markNotificationsRead(let ids, let all):
            return MarkNotificationsReadRequestBody(ids: ids, all: all)

        case .createPriceAlert(let ticker, let kind, let threshold, let assetType, let repeatMode):
            return CreatePriceAlertRequestBody(
                ticker: ticker, kind: kind, threshold: threshold,
                assetType: assetType, repeatMode: repeatMode
            )

        case .updatePriceAlert(_, let threshold, let isActive, let repeatMode):
            return UpdatePriceAlertRequestBody(
                threshold: threshold, isActive: isActive, repeatMode: repeatMode
            )

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

        case .enrichETFNews(_, let articleIds):
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

    // MARK: - Auth Endpoint

    /// The auth flow endpoints themselves. The APIClient's 401 → refresh → retry
    /// interceptor skips these so a failed login/refresh surfaces instead of looping.
    nonisolated var isAuthEndpoint: Bool {
        switch self {
        case .signIn, .signUp, .refreshToken, .signOut,
             .forgotPassword, .resetPassword,
             .resendConfirmation, .oauthSignIn, .sessionExchange:
            return true
        // .changePassword is deliberately NOT here: it is an authenticated request, so it
        // should still get the 401 -> refresh -> retry interceptor.
        default:
            return false
        }
    }

    // MARK: - Auth Policy

    /// What a call to this endpoint needs from the caller's identity.
    ///
    /// This switch is **deliberately exhaustive — do NOT add a `default:` arm.** The predecessor
    /// (`requiresAuth: Bool`) ended in `default: return true`, and that single line was wrong for
    /// **27 of the 42 endpoints** it swept up: watchlist, tracking, every portfolio route, all nine
    /// research routes, `/users/me/credits` and analytics are all guest-capable on the backend
    /// (`get_watchlist_identity` / `get_research_identity` / `get_current_user_or_guest` /
    /// `get_identity_only_user`), and two more were fully public. Nothing enforced the flag, so the
    /// errors were invisible — the moment `APIClient` started honouring it, a `default: true` would
    /// have locked every signed-out user out of features that work today.
    ///
    /// Exhaustiveness makes that impossible: a new endpoint does not compile until someone decides
    /// which of the three answers applies. Pair any change here with the backend dependency on the
    /// matching route — `tests/test_ios_auth_policy_parity.py` asserts the two agree.
    nonisolated var authPolicy: AuthPolicy {
        switch self {

        // ── Public: the backend reads no identity at all ────────────────────────────────

        // Pre-session auth flows. Password recovery in particular must be reachable
        // WITHOUT a token — it exists for people who cannot sign in.
        case .signIn, .signUp, .refreshToken,
             .forgotPassword, .resetPassword,
             .resendConfirmation, .oauthSignIn, .sessionExchange:
            return .public

        // Tier catalog — the paywall has to render for guests.
        case .getPlanCatalog:
            return .public

        // Credit-pack catalog. Public for the same reason: the Buy Credits screen must render
        // before we know who is looking, and it is the same pricing Apple already shows on the
        // storefront. Only BUYING is gated — `verifyPurchase` is `.signInRequired` below,
        // because consumables are not restorable by Apple, so credits must attach to a real
        // account or a reinstall loses money the user actually spent.
        case .getCreditPackCatalog:
            return .public

        // Market data. `getHoldersData` and `enrichStockNews` are here rather than under
        // sign-in-required (where `default: true` used to put them): `/stocks/{t}/holders`
        // and `/stocks/{t}/news/enrich` take no auth dependency at all.
        case .searchStocks, .getStock, .getStockOverview, .getStockOverviewCore, .getStockQuote,
             .getStockFundamentals, .getStockNews, .enrichStockNews, .getStockChart,
             .getAnalystAnalysis, .getSentimentAnalysis, .getTechnicalAnalysis,
             .getTechnicalAnalysisDetail, .getChartEvents, .getEarnings, .getGrowth,
             .getProfitPower, .getRevenueBreakdown, .getHealthCheck, .getSignalOfConfidence,
             .getHoldersData:
            return .public

        case .getIndexDetail, .getIndexNews, .enrichIndexNews,
             .getCryptoDetail, .getCryptoNews, .enrichCryptoNews, .getCryptoFearGreed,
             .getCryptoSentiment, .getCryptoTechnicalAnalysis, .getCryptoTechnicalAnalysisDetail,
             .getETFDetail, .getETFDividends, .getETFHoldingsRisk, .getETFProfile, .getETFNews,
             .enrichETFNews,
             .getCommodityDetail, .getCommodityNews, .enrichCommodityNews:
            return .public

        // Updates: /feed and /news/enrich are plain market data (/tabs is not — see below).
        case .getUpdatesFeed, .enrichUpdatesNews:
            return .public

        // Theme detail is public market data.
        case .getThemeDetail:
            return .public

        case .getPersonas, .getTrendingAnalyses:
            return .public

        // ── Guest-allowed: per-identity, but a guest is a first-class caller ─────────────
        //
        // These resolve through `get_watchlist_identity` / `get_research_identity` /
        // `get_learn_identity` / `get_current_user_or_guest` / `get_identity_only_user`, which
        // partition a signed-out caller per INSTALL off the `X-Guest-Id` header that
        // `APIClient.buildRequest` sends unconditionally. Gating any of these on a token would
        // be a straight feature regression for the guest-first product.

        case .trackEvents:
            return .guestAllowed

        case .getUserCredits:
            return .guestAllowed

        case .getWatchlist, .addToWatchlist, .removeFromWatchlist:
            return .guestAllowed

        case .getTrackingAssets, .bulkUpdateHoldings, .getPortfolioInsights:
            return .guestAllowed

        case .getPortfolios, .createPortfolio, .renamePortfolio, .deletePortfolio,
             .setPortfolioTickers, .setPortfolioHoldings, .reorderPortfolios,
             .activatePortfolio,
             .getPortfolioInsightsForPortfolio:
            // Guest-allowed like every sibling: groups are cheap, are the caller's own
            // data, and are claimable on sign-up (auth.md §1a). `get_watchlist_identity`
            // resolves a signed-out caller to their per-install partition.
            return .guestAllowed

        case .listChatSessions, .createChatSession, .sendChatMessage, .streamChatMessage,
             .getChatHistory, .updateChatSession, .deleteChatSession:
            return .guestAllowed

        case .getLearnProgress, .completeLearnItem, .uncompleteLearnItem,
             .getBookBookmarks, .addBookBookmark, .removeBookBookmark:
            return .guestAllowed

        // Learn CONTENT moved off `.public` when narration became Pro/Max. It is still
        // free to read on every tier — the identity is read ONLY for `tier`, to decide
        // whether the response carries audio URLs. A signed-out caller resolves to a
        // per-install guest and still gets every word of every lesson and article.
        case .getJourney, .getMoneyMoves:
            return .guestAllowed

        case .getUpdatesTabs, .getHomeFeed, .getHomeDashboard:
            return .guestAllowed

        // Whales resolve a signed-out caller through `get_watchlist_identity`, so they are
        // guest-capable — but they are TIER-gated on top of that, which is a different axis:
        // the roster and the profile header always render, while the paid sections come back
        // redacted. Trade groups and the signal drill-down moved off `.public` when that gate
        // landed; they now take the same identity because they serve the same paid data the
        // profile withholds, and leaving them open made that redaction a curtain, not a gate.
        case .getWhaleList, .getWhaleProfile,
             .getWhaleTradeGroups, .getWhaleTradeGroupDetail,
             .getSignalDetail:
            return .guestAllowed

        // ── Sign-in required: the backend uses strict `get_current_user(_id)` ────────────
        //
        // A tokenless call here is answered 401 AUTH_REQUIRED, so `APIClient` short-circuits it
        // into the Sign In Required affordance instead of spending a round trip to be refused.

        // Follows are account-scoped: `whale_follows.user_id` is FK-bound to `public.users`
        // (ON DELETE CASCADE), so a per-install guest id cannot be stored there without the
        // migration-108 treatment. Decision recorded: follow stays account-only.
        case .followWhale, .unfollowWhale, .getWhaleActivity:
            return .signInRequired

        // AI GENERATION IS ACCOUNT-ONLY. These were `.guestAllowed`, and that was the app's one
        // unbounded cost exposure: guest metering keys on `guest_user_id_for(X-Guest-Id)` — a
        // UUID5 of a header the CLIENT picks — so rotating it handed out a fresh
        // GUEST_REPORT_MONTHLY_LIMIT on every request. Each report is ~17 Gemini + ~20 FMP
        // calls, no per-IP limit backs it up, and guest credits don't meter (the sentinel is
        // seeded 100,000). Beyond the bill, saturating REPORT_GET_MAX_INFLIGHT (24) makes
        // PAYING users see 409 SYSTEM_BUSY.
        //
        // A free account is seeded 50 credits and a report costs 20, so signing up buys 2
        // reports/month against the guest's 1 — the taster gets bigger, and the signup ask moves
        // to the moment someone actually wants an analysis. Browsing, watchlist, portfolios,
        // Learn and general chat stay open (App Store 5.1.1(v)).
        //
        // BOTH generation paths must be listed or the gate leaks: `/research/generate` is the
        // deep path, `GET /stocks/{t}/report` the direct one, and they cost the same on a miss.
        case .generateResearch, .getResearchStatus, .getResearchReport, .getResearchReportPDF,
             .regenerateResearchReportPDF, .getResearchTickerReport, .getMyReports,
             .rateReport, .deleteReport:
            return .signInRequired

        // `prewarmReportCollection` fires ~20 FMP calls on detail view to warm the collection
        // cache — pure waste for a caller who can no longer generate.
        case .getTickerReport, .chatWithTickerReport, .prewarmReportCollection:
            return .signInRequired

        case .getCurrentUser, .updateProfile, .deleteAccount, .claimGuestData:
            return .signInRequired

        case .getMySubscription, .getMySettings, .updateMySettings,
             .registerDevice, .unregisterDevice, .verifyPurchase:
            return .signInRequired

        // Push never reaches a guest: `device_tokens` is FK-bound to public.users and
        // auth-only, so a guest inbox is empty by construction and a guest price alert is
        // a rule that can never fire. Both are "durable cross-device identity" by
        // auth.md §1a's tier test — the same tier as settings and devices.
        case .listNotifications, .markNotificationsRead,
             .listPriceAlerts, .createPriceAlert, .updatePriceAlert, .deletePriceAlert:
            return .signInRequired

        // Both take `get_current_user_id`. Short-circuiting a tokenless `signOut` is harmless —
        // its only caller already ignores the result — and saves a guaranteed-401 round trip.
        case .signOut, .changePassword:
            return .signInRequired
        }
    }

    /// Kept as a thin alias so the debug logger reads naturally. Not a gate — `APIClient`
    /// switches on `authPolicy` directly.
    nonisolated var requiresAuth: Bool { authPolicy == .signInRequired }

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

// Password recovery. Property names are camelCase; the encoder's convertToSnakeCase maps
// them to the backend's `new_password` / `current_password`.
nonisolated struct ForgotPasswordRequest: Encodable, Sendable {
    let email: String
}

nonisolated struct ResetPasswordRequest: Encodable, Sendable {
    let email: String
    let code: String
    let newPassword: String
}

nonisolated struct ChangePasswordRequest: Encodable, Sendable {
    let currentPassword: String
    let newPassword: String
}

nonisolated struct ResendConfirmationRequest: Encodable, Sendable {
    let email: String
}

nonisolated struct OAuthSignInRequest: Encodable, Sendable {
    let provider: String
    let idToken: String
    let nonce: String?
    let displayName: String?
}

nonisolated struct SessionExchangeRequest: Encodable, Sendable {
    let supabaseAccessToken: String
}

/// POST /billing/verify body. Only the signed blob — deliberately NOT a product id or
/// tier, so the client cannot influence what it gets entitled to.
nonisolated struct VerifyPurchaseRequestBody: Encodable, Sendable {
    let signedTransaction: String
}

/// Generic `{ "message": "..." }` acknowledgement returned by the password endpoints.
nonisolated struct MessageResponse: Decodable, Sendable {
    let message: String
}

/// `POST /auth/change-password` — acknowledgement WITH replacement credentials.
///
/// Changing a password stamps `users.password_changed_at` server-side, after which every token
/// minted before that instant is rejected — including the one this app is holding, the one it
/// just used to make this very request. Without adopting these, the next call 401s, the refresh
/// interceptor's own staleness check rejects that too, and the user is signed out seconds after
/// being told the change succeeded.
///
/// The tokens are minted AFTER the stamp, so this device survives while every OTHER device is
/// still evicted — which is the entire point of the feature.
/// The credentials are OPTIONAL, and that is load-bearing rather than defensive vagueness.
/// The app and the backend deploy independently: a build carrying this type can run against a
/// Railway instance that predates it and still returns a bare `{"message": ...}`. Declaring the
/// tokens as required would make that decode THROW — after the password had already been
/// changed — so the user would see an error for an operation that succeeded, and be signed out
/// anyway. Absent tokens simply mean "this server can't rotate yet"; the caller keeps the old
/// behaviour instead of failing.
nonisolated struct PasswordChangedResponse: Decodable, Sendable {
    let message: String
    let accessToken: String?
    let refreshToken: String?
    let userId: String?

    // APIClient does not set `.convertFromSnakeCase`, so these are mapped by hand.
    enum CodingKeys: String, CodingKey {
        case message
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case userId = "user_id"
    }
}

nonisolated struct UpdateProfileRequest: Encodable, Sendable {
    let displayName: String?
    let avatarUrl: String?
}

/// PUT /users/me/settings body. Keys inside `preferences` are already snake_case
/// (the @AppStorage keys), so the encoder's convertToSnakeCase leaves them intact.
nonisolated struct UpdateUserSettingsRequestBody: Encodable, Sendable {
    let preferences: [String: PreferenceValue]
}

/// POST /users/me/notifications/read body. `all: true` marks everything and `ids` is
/// ignored — the two are deliberately one call so "mark all read" is not N requests.
nonisolated struct MarkNotificationsReadRequestBody: Encodable, Sendable {
    let ids: [String]
    let all: Bool
}

nonisolated struct CreatePriceAlertRequestBody: Encodable, Sendable {
    let ticker: String
    let kind: String
    let threshold: Double
    let assetType: String
    let repeatMode: String
}

/// PATCH body. Every field is Optional and nil ones are OMITTED by the encoder, so a
/// patch that only changes `isActive` cannot accidentally clear the threshold.
nonisolated struct UpdatePriceAlertRequestBody: Encodable, Sendable {
    let threshold: Double?
    let isActive: Bool?
    let repeatMode: String?
}

nonisolated struct DeviceRegisterRequestBody: Encodable, Sendable {
    let token: String
    let platform: String
    let environment: String?
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
