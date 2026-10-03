//
//  ChatConversationModels.swift
//  ios
//
//  Data models for rich chat conversation content
//

import Foundation
import SwiftUI

// MARK: - Chat Message Role
enum ChatMessageRole: Equatable {
    case user
    case assistant
}

// MARK: - Chat Context Type

/// The screen a chat is grounded on. iOS sends the raw value + a `reference_id`
/// so the backend fetches the already-cached data for that screen (report / ETF /
/// crypto / article / ...) and injects a compact grounding block — instead of the
/// client shipping a big raw context string. Mirrors the backend `ChatContextType`
/// enum (backend/app/schemas/chat.py).
enum ChatContextType: String {
    case tickerReport = "TICKER_REPORT"
    case stock = "STOCK"
    case etf = "ETF"
    case crypto = "CRYPTO"
    case index = "INDEX"
    case commodity = "COMMODITY"
    case moneyMovesArticle = "MONEY_MOVES_ARTICLE"
    case journeyLesson = "JOURNEY_LESSON"
    case book = "BOOK"
    /// The Updates tab. `reference_id` is the feed scope (a ticker, a coin pair or
    /// `UpdatesScope.market`); the backend reads that feed's Insights card, headlines and
    /// news-tone trend itself.
    case updatesScope = "UPDATES_SCOPE"
    case none = "NONE"

    /// Short human label + glyph for the "Grounded on …" chip.
    var groundingLabel: String {
        switch self {
        case .tickerReport: return "Research Report"
        case .stock: return "Stock"
        case .etf: return "ETF"
        case .crypto: return "Crypto"
        case .index: return "Market"
        case .commodity: return "Commodity"
        case .moneyMovesArticle: return "Money Moves"
        case .journeyLesson: return "Lesson"
        case .book: return "Book"
        case .updatesScope: return "Updates"
        case .none: return ""
        }
    }

    /// True when `referenceLabel` names the subject completely, so the chip should read
    /// "Grounded on <reference>" rather than "Grounded on <label> · <reference>".
    ///
    /// A book's TITLE is the subject; prefixing it with a category said the same thing twice
    /// ("Grounded on Study Guide · The Intelligent Investor"). A ticker is the opposite case —
    /// "AAPL" alone does not say what is being read, so those keep their label.
    var groundingReferenceStandsAlone: Bool {
        self == .book
    }

    /// The softened chip, shown when the server says this screen's grounding did NOT reach
    /// the turn (`ChatMessageDTO.contextGrounded == false`) — e.g. a report chat after the
    /// shared report cache rolled over and no saved report id was sent. Claiming "Grounded
    /// on Research Report" there told the user the answer came from a report it never saw.
    var groundingUnavailableLabel: String {
        switch self {
        case .tickerReport: return "Report not available — answering generally"
        case .none: return ""
        default: return "\(groundingLabel) not available — answering generally"
        }
    }

    var groundingIcon: String {
        switch self {
        case .tickerReport: return "doc.text.magnifyingglass"
        case .stock, .index: return "chart.line.uptrend.xyaxis"
        case .etf: return "chart.pie.fill"
        case .crypto: return "bitcoinsign.circle.fill"
        case .commodity: return "cube.fill"
        case .moneyMovesArticle: return "newspaper.fill"
        case .journeyLesson: return "map.fill"
        case .book: return "book.fill"
        case .updatesScope: return "newspaper"
        case .none: return AppSymbols.ai
        }
    }
}

// MARK: - Report Chat Agent Mode

/// The analysis-style MODE a report chat runs in: "Cay AI · Growth Hunter Agent".
///
/// Cay AI is always the speaker; "<Style> Agent" names the method it applies in this chat, never
/// a separate assistant and never a real investor. Derived ONLY from the chat's context type and
/// `reference_id` — the bytes the backend reads on every turn, restored when a chat is reopened
/// from history — so the label the app shows and the method the server applies come from the
/// same place. `AIChatScreen.reportAgentMode` is its single construction site; the grounding
/// chip is its only surface ("Cay AI · Growth Hunter Agent · MSFT report").
///
/// There is deliberately NO greeting card (owner, 2026-10-02: the chip says enough). The server's
/// mode voice never greets or announces itself either, so nothing introduces the mode but the chip.
///
/// Known limitation: this reads the REFERENCE only, while the server's voice prefers the persona
/// of the report it actually grounds on (`report_voice_prompt.resolve_voice_key`). The two agree
/// for every reference this build sends (the report screen sends the on-screen report's own
/// persona). A history reference from an older build can still diverge: a wrong default segment
/// (`|warren_buffett` on a Growth report) labels the wrong style, and an empty or unknown segment
/// shows no mode while the server speaks the grounded report's voice.
struct ReportChatAgentMode {
    let persona: AnalysisPersona
    /// The report's ticker from the reference's first segment, kept only when it is a plausible
    /// symbol (the backend's pattern); "" otherwise, and the chip then names no ticker.
    let ticker: String

    /// nil unless this is a report chat whose reference names a known persona (the closed,
    /// hard-coded `allCases`). Unknown or missing persona → nil: the chip keeps its plain
    /// "Grounded on" label.
    init?(contextType: ChatContextType?, referenceId: String?) {
        guard contextType == .tickerReport,
              let persona = AnalysisPersona.forChatReference(referenceId)
        else { return nil }
        self.persona = persona
        self.ticker = Self.validatedTicker(in: referenceId)
    }

    /// "Growth Hunter" — the hard-coded persona's name without "The ".
    var styleName: String { persona.compactName }

    /// "Growth Hunter Agent". NOT `AnalysisPersona.agentLabel` ("GARP Agent"), which is the
    /// report-progress label built from `shortName`.
    var chatModeLabel: String { "\(styleName) Agent" }

    /// The reference's first segment (split KEEPING empty parts, like the backend), trimmed and
    /// upper-cased, when it matches the backend's symbol pattern; "" otherwise.
    private static func validatedTicker(in referenceId: String?) -> String {
        guard let first = referenceId?
            .split(separator: "|", omittingEmptySubsequences: false).first
        else { return "" }
        let symbol = first.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let pattern = #"^\^?[A-Z0-9][A-Z0-9.\-]{0,14}$"#
        return symbol.range(of: pattern, options: .regularExpression) != nil ? symbol : ""
    }
}

// MARK: - Rich Content Type
enum RichContentType {
    case text(String)
    case sentimentAnalysis(SentimentAnalysis)
    case stockPerformance(StockPerformance)
    case stockChart(StockChartWidgetData)
    case marketOverview(MarketOverviewWidgetData)
    case riskFactors(RiskFactorsData)
    case tip(TipData)
    case bulletPoints([ChatBulletPoint])
}

// MARK: - Thinking / Sources (futuristic chat)

/// One "source" pill for the thinking card. Two kinds share the wire shape:
///
/// * a GROUNDING pill (a screen context or a filing section) — `label`/`detail` only, exactly
///   what the backend's `_build_sources` writes and what every shipped build decodes;
/// * a WEB pill (report chat's live web search, 2026-10-02) — `kind: "web"`, `label: "Web"`,
///   `detail` = the publisher (derived server-side from the article's URL host, never from the
///   page's own metadata), plus `title`, `url` (https) and `published_at` ("YYYY-MM-DD" or null).
///   Older builds read it as a plain "Web · <publisher>" pill, which is why `label` stays "Web".
///
/// Web pills are LIVE: by default the server does not store them, so a reopened chat shows the
/// grounding pills and the card's "Web search" badge, not the links.
struct ChatSource: Codable, Identifiable, Sendable, Hashable {
    /// Grounding pills keep `label|detail` — the id every stored row already has, so reopening an
    /// old chat causes no ForEach churn. A web pill keys on its URL: the server sends one pill per
    /// host, but an older server could send two from the same publisher, and those must not share
    /// an id in a `ForEach`.
    var id: String {
        if isWeb, let link = url?.trimmingCharacters(in: .whitespacesAndNewlines), !link.isEmpty {
            return "web|" + link
        }
        return label + "|" + (detail ?? "")
    }
    let label: String
    let detail: String?
    /// "web" for a web pill; nil for a grounding pill (every legacy row).
    let kind: String?
    /// The article's title (web pills only). Read out by VoiceOver, never drawn on the pill.
    let title: String?
    /// The article's address (web pills only). Untrusted: only `webURL` may turn it into a link.
    let url: String?
    /// The article's calendar date, "YYYY-MM-DD" (web pills only); nil when unknown.
    let publishedAt: String?

    /// The longest address a pill may open. Longer is not an article link.
    static let maxURLLength = 2048
    /// The most pills one answer can show. The server sends at most 6 grounding + 5 web pills;
    /// the cap only stops a malformed row from drawing an unbounded horizontal row.
    static let maxPills = 16

    init(label: String, detail: String?, kind: String? = nil, title: String? = nil,
         url: String? = nil, publishedAt: String? = nil) {
        self.label = label
        self.detail = detail
        self.kind = kind
        self.title = title
        self.url = url
        self.publishedAt = publishedAt
    }

    private enum CodingKeys: String, CodingKey {
        case label, detail, kind, title, url
        case publishedAt = "published_at"
    }

    /// Total (never-throwing) decode. `sources` is an OPTIONAL field on `ChatMessageDTO`, but
    /// `decodeIfPresent` only swallows an absent/null ARRAY — a present array whose element is a
    /// malformed object (missing the non-optional `label`) still rethrows, and array decoding is
    /// all-or-nothing, so ONE bad pill would collapse the entire `[ChatMessageDTO]` history decode
    /// (blank conversation). A missing label degrades to "" (dropped at render), never a crash —
    /// mirrors the `ChatWidgetData.unknown` hardening. Every web field is optional the same way:
    /// a wrong type degrades that one field to nil (a web pill without a usable link renders as a
    /// plain label), never the pill and never the history.
    init(from decoder: Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.label = ""; self.detail = nil
            self.kind = nil; self.title = nil; self.url = nil; self.publishedAt = nil
            return
        }
        self.label = (try? c.decode(String.self, forKey: .label)) ?? ""
        self.detail = try? c.decode(String.self, forKey: .detail)
        self.kind = try? c.decode(String.self, forKey: .kind)
        self.title = try? c.decode(String.self, forKey: .title)
        self.url = try? c.decode(String.self, forKey: .url)
        self.publishedAt = try? c.decode(String.self, forKey: .publishedAt)
    }

    /// True for a web search result pill.
    var isWeb: Bool {
        kind?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "web"
    }

    /// The ONLY way a pill becomes tappable. An https address with a real public host and no
    /// user info, or nil. The server builds pills from https URLs only; this is the second layer,
    /// because the address is a third party's and the in-app browser must never be handed
    /// `javascript:`, a custom scheme (`caydex://` is this app's own sign-in scheme) or
    /// `https://reuters.com@evil.example` (user info that disguises the real host).
    /// `URLComponents`, not `URL.host` / `URL.user` (deprecated since iOS 16).
    var webURL: URL? {
        guard isWeb,
              let raw = url?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty, raw.count <= Self.maxURLLength,
              let parts = URLComponents(string: raw),
              parts.scheme?.lowercased() == "https",
              let host = parts.host, Self.isPublicHostName(host),
              parts.user == nil, parts.password == nil, parts.port == nil,
              let resolved = parts.url
        else { return nil }
        return resolved
    }

    /// The link's host without "www.", lowercased ("reuters.com"); nil without a usable link.
    var webHost: String? {
        guard let link = webURL,
              let host = URLComponents(url: link, resolvingAgainstBaseURL: false)?.host?.lowercased()
        else { return nil }
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    /// Who published the article: the server's publisher name, else the link's host. nil when
    /// neither exists — such a pill attributes nothing and is dropped by `sanitized(_:)`.
    /// Capped, because the pills sit in a horizontal scroll where `lineLimit` never truncates.
    var webPublisherName: String? {
        guard isWeb else { return nil }
        if let named = Self.oneLine(detail, cap: 48) { return named }
        return webHost.map { String($0.prefix(48)) }
    }

    /// "Oct 1, 2026" from `published_at`, or nil when it is absent or not a calendar date.
    var publishedDisplay: String? { ChatSourceDate.display(publishedAt) }

    /// What VoiceOver reads for a web pill: kind, publisher, date, host (when the publisher name
    /// is not simply the host) and the title — so the destination is never only implied.
    var webAccessibilityLabel: String {
        var parts = ["Web source"]
        if let name = webPublisherName { parts.append(name) }
        if let date = publishedDisplay { parts.append(date) }
        if let host = webHost, host != webPublisherName?.lowercased() { parts.append(host) }
        var spoken = parts.joined(separator: ", ")
        if let heading = Self.oneLine(title, cap: 160) { spoken += ". " + heading }
        return spoken
    }

    /// The pills an answer may show, in the server's order. Drops an empty-label pill (only a
    /// malformed row produces one, via the total decode above), a web pill that names no
    /// publisher, and a repeated id (first wins), and stops at `maxPills`. Every entry path —
    /// history, the non-stream reply, `done`, and the live `sources` frame — goes through here.
    static func sanitized(_ raw: [ChatSource]?) -> [ChatSource]? {
        guard let raw else { return nil }
        var seen = Set<String>()
        var kept: [ChatSource] = []
        for source in raw {
            if kept.count >= maxPills { break }
            if source.label.isEmpty { continue }
            if source.isWeb && source.webPublisherName == nil { continue }
            guard seen.insert(source.id).inserted else { continue }
            kept.append(source)
        }
        return kept
    }

    /// A dotted public DNS name: never an IP literal, `localhost`, a `.local` / `.localhost` /
    /// `.internal` name or an IPv6 literal. Mirrors the server's pill check.
    private static func isPublicHostName(_ host: String) -> Bool {
        let name = host.lowercased()
        guard name.contains("."), !name.contains(":"),
              !name.hasPrefix("."), !name.hasSuffix("."),
              !name.hasSuffix(".local"), !name.hasSuffix(".localhost"), !name.hasSuffix(".internal"),
              let topLevel = name.split(separator: ".").last,
              topLevel.contains(where: { $0.isLetter })
        else { return false }
        return true
    }

    /// Whitespace runs (newlines included) folded to one space, trimmed, capped; nil when empty.
    private static func oneLine(_ text: String?, cap: Int) -> String? {
        guard let text else { return nil }
        let folded = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        return folded.isEmpty ? nil : String(folded.prefix(cap))
    }
}

/// `published_at` is a calendar DATE, so it is parsed and shown in UTC with the POSIX locale and
/// the Gregorian calendar: in the device zone a date-only value lands a day early west of UTC,
/// and a Buddhist- or Japanese-calendar phone would print another year.
private enum ChatSourceDate {
    static let parser: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        f.isLenient = false
        return f
    }()

    static let shown: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "MMM d, yyyy"
        return f
    }()

    /// "2026-10-01" → "Oct 1, 2026". Anything else (a relative age, a timestamp, garbage) → nil.
    static func display(_ raw: String?) -> String? {
        guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              value.count == 10,
              let date = parser.date(from: value)
        else { return nil }
        return shown.string(from: date)
    }
}

/// Thinking-process summary shown in the collapsible "Done in Xs · N sources" card.
/// `stages` are the server-authored progress labels. `elapsedMs` is nil WHILE the answer is
/// generating (the card renders an active "Thinking…" state) and set on completion.
struct ChatThinking: Codable, Sendable {
    let stages: [String]
    let sourceCount: Int?
    let elapsedMs: Int?
    /// The model's streamed reasoning preamble — replaces the old canned "stages". nil/empty for
    /// legacy rows (which still carry `stages`). (Backend always sends `stages`, now as `[]`.)
    let reasoning: String?
    /// Where this turn's web search stands — the card's "Searching the web…" header and its
    /// "Web search" badge. nil: no web search on this turn (every legacy row).
    let webSearchState: WebSearchState?

    /// Report chat's live web search, as the card shows it.
    enum WebSearchState: Sendable, Equatable {
        /// The server announced the search (`tool_start`) and its step has not landed yet.
        case searching
        /// The search step finished. On a stored row: the server's `web_searched: true`, a flag
        /// it keeps on a turn whose answer used web results (never the results themselves).
        case done
        /// The search did not run on this turn (the day's limit, or unavailable). Live only.
        case skipped
    }

    enum CodingKeys: String, CodingKey {
        case stages, reasoning
        case sourceCount = "source_count"
        case elapsedMs = "elapsed_ms"
        case webSearched = "web_searched"
    }

    // Defaulted init so callers can construct without every field (a `let` optional is otherwise
    // required by the synthesized memberwise init).
    init(stages: [String] = [], sourceCount: Int? = nil, elapsedMs: Int? = nil, reasoning: String? = nil,
         webSearchState: WebSearchState? = nil) {
        self.stages = stages
        self.sourceCount = sourceCount
        self.elapsedMs = elapsedMs
        self.reasoning = reasoning
        self.webSearchState = webSearchState
    }

    /// Written by hand only because `webSearchState` has no wire key of its own: the wire carries
    /// `web_searched: true` for `.done` and nothing for any other state (the live-only states never
    /// leave the device). The other four fields encode exactly as the synthesized version did.
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(stages, forKey: .stages)
        try c.encodeIfPresent(sourceCount, forKey: .sourceCount)
        try c.encodeIfPresent(elapsedMs, forKey: .elapsedMs)
        try c.encodeIfPresent(reasoning, forKey: .reasoning)
        if webSearchState == .done { try c.encode(true, forKey: .webSearched) }
    }

    /// Total (never-throwing) decode. `thinking` is OPTIONAL on `ChatMessageDTO`, but `decodeIfPresent`
    /// only swallows an absent/null OBJECT — a PRESENT object that fails to decode (missing/null/
    /// wrong-type `stages`, which is non-optional) rethrows and collapses the whole all-or-nothing
    /// `[ChatMessageDTO]` history decode → a blank conversation. Default every field so any shape
    /// degrades gracefully (mirrors the `ChatWidgetData.unknown` hardening). Not producible by today's
    /// backend (it always writes `stages: []` + an int `elapsed_ms`), but a future/hand-edited row
    /// must never be able to blank a user's history.
    init(from decoder: Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.stages = []; self.sourceCount = nil; self.elapsedMs = nil; self.reasoning = nil
            self.webSearchState = nil
            return
        }
        self.stages = (try? c.decode([String].self, forKey: .stages)) ?? []
        self.sourceCount = try? c.decode(Int.self, forKey: .sourceCount)
        self.elapsedMs = try? c.decode(Int.self, forKey: .elapsedMs)
        self.reasoning = try? c.decode(String.self, forKey: .reasoning)
        // Only a real `true` is a web-searched turn: absent, false, null or a wrong type is not.
        self.webSearchState = (try? c.decode(Bool.self, forKey: .webSearched)) == true ? WebSearchState.done : nil
    }

    /// True while the answer is still being produced (drives the animated header).
    var isActive: Bool { elapsedMs == nil }

    /// Elapsed seconds for the "Done in Xs" label (min 1s so it never reads "0s").
    var elapsedSeconds: Int { max(1, Int((Double(elapsedMs ?? 0) / 1000).rounded())) }

    /// Trimmed reasoning text if non-empty (the card renders reasoning when present, else stages).
    var reasoningText: String? {
        guard let r = reasoning?.trimmingCharacters(in: .whitespacesAndNewlines), !r.isEmpty else { return nil }
        return r
    }

    /// Whether the thinking card should render at all: while active (shows "Thinking…"), or once
    /// there is reasoning / stages / grounded sources to show. Sources are gated on `sourceCount`
    /// (the card owns the source pills) so a finished message whose model skipped the reasoning
    /// preamble — or that came via the non-streaming fallback (reasoning "", stages []) — still
    /// surfaces its grounding attribution instead of silently dropping the pills. A web-searched
    /// turn always shows the card too: its "Web search" badge is the answer's only marker that
    /// third-party pages were read, and the server's stored `source_count` does not count the
    /// live web pills.
    var shouldDisplay: Bool {
        isActive || reasoningText != nil || !stages.isEmpty || (sourceCount ?? 0) > 0
            || webSearchState == .done
    }
}

// MARK: - Rich Chat Message
struct RichChatMessage: Identifiable {
    let id: UUID
    let role: ChatMessageRole
    /// `var` so the streaming path can grow the text in place (same id → no ForEach re-insert).
    var content: [RichContentType]
    let timestamp: Date
    /// Thinking-process summary (assistant only). Present while generating (active) and after
    /// completion (collapsed card). nil for user messages + legacy rows.
    var thinking: ChatThinking?
    /// Grounded-context source pills shown inside the thinking card.
    var sources: [ChatSource]?
    /// AI follow-up questions shown under the latest answer.
    var suggestions: [String]?
    /// What this turn cost, when it cost less than usual. nil → render no chip.
    var credit: ChatTurnCostDTO?
    /// The model CUT this answer (it hit its output ceiling mid-sentence) and the server's
    /// continuation round did not complete it. The row renders a "cut short" notice and the
    /// server sends the single "Continue your answer" chip in `suggestions`. Never true for
    /// a user bubble, a legacy row, or a complete answer (TestFlight 2026-09-16, E1).
    var truncated: Bool
    /// The backend row id (`chat_messages.id`) when this message came from the server —
    /// a history load or a `done` frame. nil for the optimistic user bubble and the live
    /// streaming bubble. The stream-failure reconcile keys on it: "is the last assistant
    /// row on the server one we have NOT seen" is exact regardless of how many rows the
    /// server's history page holds, where a count of matching user texts was not.
    var serverId: String?

    /// `id` defaults to a fresh UUID (existing call sites unaffected). A caller
    /// can pass a stable id so a streaming message can be replaced in place each
    /// token without ForEach re-inserting the row.
    init(id: UUID = UUID(), role: ChatMessageRole, content: [RichContentType], timestamp: Date,
         thinking: ChatThinking? = nil, sources: [ChatSource]? = nil, suggestions: [String]? = nil,
         credit: ChatTurnCostDTO? = nil, truncated: Bool = false, serverId: String? = nil) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.thinking = thinking
        self.sources = sources
        self.suggestions = suggestions
        self.credit = credit
        self.truncated = truncated
        self.serverId = serverId
    }

    var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: timestamp)
    }

    /// Concatenated plain text of this message (user bubbles are always a single `.text`). Used by
    /// the stream-failure reconcile to count how many turns carry the same text.
    var plainText: String {
        content.reduce(into: "") { acc, item in
            if case let .text(t) = item { acc += t }
        }
    }
}

// MARK: - Stock Chart Widget (Codable — from backend)

/// Matches the backend ``StockChartWidget`` Pydantic model.
/// Uses explicit CodingKeys with snake_case raw values so the
/// default JSONDecoder (no keyDecodingStrategy) works correctly.
struct StockChartWidgetData: Codable, Identifiable {
    let id: UUID = UUID()

    let widgetType: String
    let ticker: String
    let companyName: String
    let currentPrice: Double
    let change: Double
    let changePercent: Double
    let dayHigh: Double
    let dayLow: Double
    let volume: Int
    let avgVolume: Int
    let marketCap: Double?
    let peRatio: Double?
    let yearHigh: Double?
    let yearLow: Double?
    /// `false` ⇒ `dayHigh`/`dayLow` are placeholders the card must not render.
    ///
    /// Optional because it is decoded with the synthesised initialiser, so an older server
    /// that does not send the key must not fail the whole message. nil is NOT "known": the
    /// `> 0` test in `hasDayRange` is what actually decides, and this flag makes the server's
    /// intent explicit alongside it.
    let dayRangeKnown: Bool?
    /// `false` ⇒ `change`/`changePercent` are 0.0 placeholders — the quote carried no change
    /// (a CoinGecko `null` 24 h move, a single FRED observation). Optional for the same
    /// reason as `dayRangeKnown`; nil reads as known, matching servers that predate it.
    let changeKnown: Bool?
    /// nil = unknown; true = US session open → the card shows a green "Live" dot, else "Closed".
    let isMarketOpen: Bool?
    let historicalData: [HistoricalDataPointDTO]

    enum CodingKeys: String, CodingKey {
        case widgetType = "widget_type"
        case ticker
        case companyName = "company_name"
        case currentPrice = "current_price"
        case change
        case changePercent = "change_percent"
        case dayHigh = "day_high"
        case dayLow = "day_low"
        case volume
        case avgVolume = "avg_volume"
        case marketCap = "market_cap"
        case peRatio = "pe_ratio"
        case yearHigh = "year_high"
        case yearLow = "year_low"
        case dayRangeKnown = "day_range_known"
        case changeKnown = "change_known"
        case isMarketOpen = "is_market_open"
        case historicalData = "historical_data"
    }

    // Computed helpers for the UI

    /// Whether the day change is a real number. `changeKnown == false` is the server saying
    /// the 0.0 is a placeholder; every reader below (sign, colour, arrow, text) is neutral then.
    var hasKnownChange: Bool { changeKnown != false }

    var isPositive: Bool { changePercent >= 0 }

    var formattedPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedChange: String {
        guard hasKnownChange else { return "—" }
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
    }

    var formattedAbsChange: String {
        guard hasKnownChange else { return "—" }
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", change))"
    }

    /// Whether today's high/low are real numbers.
    ///
    /// ⚠️ THE CARD SHIPPED "Day High $0.00 / Day Low $0.00" AS FACT, beside a live price.
    /// `dayHigh`/`dayLow` came from FMP's `/stable/quote`, which is in a package the signed
    /// Order Form does not include and answers 402 — so the backend's `or 0` rendered a
    /// fabricated zero. Same class as the index screen's `Open 0.00` and the 0-P/E "Bargain"
    /// badge; this call site was missed in that sweep.
    ///
    /// The `> 0` test is the load-bearing half and works against any server, including one
    /// too old to send `day_range_known`: no traded instrument has a zero or negative daily
    /// high. The flag is the server saying so explicitly.
    var hasDayRange: Bool {
        dayRangeKnown != false && dayHigh > 0 && dayLow > 0
    }

    var formattedDayHigh: String { hasDayRange ? String(format: "$%.2f", dayHigh) : "—" }
    var formattedDayLow: String { hasDayRange ? String(format: "$%.2f", dayLow) : "—" }

    var formattedVolume: String { Self.abbreviate(Double(volume)) }
    var formattedAvgVolume: String { Self.abbreviate(Double(avgVolume)) }
    var formattedMarketCap: String? {
        guard let mc = marketCap else { return nil }
        return Self.abbreviate(mc)
    }

    /// The points the chart may actually plot.
    ///
    /// The backend used to coerce a present-but-null FMP `close` to the literal `0`, and NaN
    /// survives an `or 0` coercion outright (NaN is truthy). Either one plotted as a real price
    /// drags the y-domain floor to zero — the true price band then occupies ~9% of a 140pt plot
    /// and the line reads as dead flat beside a three-figure header. The backend now drops those
    /// rows; this filter is the second half of the same guard, so an already-persisted
    /// `rich_content` row from before that fix still renders honestly.
    ///
    /// The index is the ORIGINAL enumeration offset, which is what makes it a stable, unique
    /// `ForEach` id even when FMP returns duplicate or empty dates.
    var chartPoints: [ChatChartPoint] {
        historicalData.enumerated().compactMap { index, point in
            guard let close = point.close.finiteOrNil, close > 0 else { return nil }
            return ChatChartPoint(id: index, date: point.date, close: close)
        }
    }

    /// Close prices for the chart line. Derived from `chartPoints` so the y-domain and the marks
    /// can never be computed from different sets of values.
    var chartCloses: [Double] {
        chartPoints.map(\.close)
    }

    /// Direction of the PLOTTED series (first → last close), which is what colours the chart.
    ///
    /// Deliberately NOT `isPositive`: that is the quote's ONE-DAY change, while the line spans
    /// ~30 days. A stock up 1% today inside a 12% monthly decline would otherwise render a
    /// falling curve in green. The header price/arrow/badge keep the one-day change — the date
    /// range printed under the chart is what scopes the line.
    var isSeriesPositive: Bool {
        let closes = chartCloses
        // With no series to read, fall back to the day change only when it is REAL.
        guard let first = closes.first, let last = closes.last, first > 0 else {
            return hasKnownChange ? isPositive : true
        }
        return last >= first
    }

    private static func abbreviate(_ value: Double) -> String {
        switch abs(value) {
        case 1_000_000_000_000...:
            return String(format: "%.2fT", value / 1_000_000_000_000)
        case 1_000_000_000...:
            return String(format: "%.2fB", value / 1_000_000_000)
        case 1_000_000...:
            return String(format: "%.1fM", value / 1_000_000)
        case 1_000...:
            return String(format: "%.1fK", value / 1_000)
        default:
            return String(format: "%.0f", value)
        }
    }
}

/// One plottable chart vertex: a validated close plus its original index in `historicalData`.
///
/// A struct rather than a tuple because `ForEach(_:id:)` needs a `KeyPath`, and Swift has no
/// key paths into tuple elements.
struct ChatChartPoint: Identifiable, Equatable {
    let id: Int
    /// Carried so the chart's date-range caption labels the bars actually DRAWN. Reading
    /// `historicalData.first` instead would name a day that got filtered out.
    let date: String
    let close: Double
}

struct HistoricalDataPointDTO: Codable, Identifiable {
    var id: String { date }

    let date: String
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Int
}

// MARK: - Market Overview Widget Data

struct MarketOverviewSectorEntry: Codable, Identifiable, Sendable {
    var id: String { sector }
    let sector: String
    let changePercent: Double

    var isPositive: Bool { changePercent >= 0 }
    var formattedChange: String {
        String(format: "%@%.1f%%", changePercent >= 0 ? "+" : "", changePercent)
    }

    enum CodingKeys: String, CodingKey {
        case sector
        case changePercent = "change_percent"
    }
}

struct MarketOverviewMacroEntry: Codable, Identifiable, Sendable {
    var id: String { title }
    let title: String
    let signal: String  // "positive", "neutral", "cautious"

    enum CodingKeys: String, CodingKey {
        case title, signal
    }
}

struct MarketOverviewWidgetData: Codable, Identifiable, Sendable {
    var id: String { "market_overview_\(peRatio)" }

    let widgetType: String
    let peRatio: Double
    /// `false` → `peRatio` / `earningsYield` are 0.0 placeholders (unlicensed quote, thin
    /// sector benchmark); render "—". Optional so older backends (no key) decode as known.
    let peKnown: Bool?
    let forwardPe: Double
    let valuationLevel: String
    let earningsYield: Double
    let historicalAvgPe: Double
    let sectors: [MarketOverviewSectorEntry]
    let advancing: Int
    let declining: Int
    let macroIndicators: [MarketOverviewMacroEntry]

    /// The multiple is real only when the backend says so AND it is a positive number —
    /// `0` is the unknown sentinel on this wire, exactly as for `forwardPe`.
    var hasKnownPE: Bool { (peKnown ?? true) && peRatio > 0 }

    enum CodingKeys: String, CodingKey {
        case widgetType = "widget_type"
        case peRatio = "pe_ratio"
        case peKnown = "pe_known"
        case forwardPe = "forward_pe"
        case valuationLevel = "valuation_level"
        case earningsYield = "earnings_yield"
        case historicalAvgPe = "historical_avg_pe"
        case sectors, advancing, declining
        case macroIndicators = "macro_indicators"
    }
}

// MARK: - Polymorphic Widget Decoding

enum ChatWidgetData: Codable, Sendable {
    case stockChart(StockChartWidgetData)
    case marketOverview(MarketOverviewWidgetData)
    /// An unrenderable widget — a future/unknown `widget_type`, or a known type whose payload
    /// failed to decode. Kept as a case (instead of throwing) so ONE bad widget can never fail
    /// its whole message, and one bad message can never fail the entire `[ChatMessageDTO]`
    /// history decode (Swift array decoding is all-or-nothing). It renders as nothing.
    case unknown

    private enum TypeKey: String, CodingKey {
        case widgetType = "widget_type"
    }

    /// Total (never-throwing) decode. An unknown `widget_type`, or a known type whose concrete
    /// payload doesn't decode, degrades to `.unknown` rather than propagating — so a forward-compat
    /// widget type shipped by the backend can't blank a user's conversation on an older app build.
    init(from decoder: Decoder) throws {
        // `widget_type` read leniently (absent/empty → the legacy stock_chart path). Coalesce to
        // "" so the switch matches plain string literals unambiguously. `decode` (not
        // decodeIfPresent) yields a single-level `String?` under `try?` — absent/null/wrong-type
        // all fall through to "".
        var type = ""
        if let container = try? decoder.container(keyedBy: TypeKey.self),
           let decoded = try? container.decode(String.self, forKey: .widgetType) {
            type = decoded
        }

        switch type {
        case "market_overview":
            self = (try? MarketOverviewWidgetData(from: decoder)).map(Self.marketOverview) ?? .unknown
        case "stock_chart", "":
            // Back-compat: legacy rows with no `widget_type` are stock charts.
            self = (try? StockChartWidgetData(from: decoder)).map(Self.stockChart) ?? .unknown
        default:
            // A renderable type this build doesn't know yet — skip it, never crash.
            self = .unknown
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .stockChart(let data):
            try data.encode(to: encoder)
        case .marketOverview(let data):
            try data.encode(to: encoder)
        case .unknown:
            break  // nothing to persist — unknown widgets are dropped, not round-tripped
        }
    }
}

// MARK: - Sentiment Analysis
struct SentimentAnalysis: Identifiable {
    let id = UUID()
    let overallSentiment: SentimentType
    let percentage: Int
    let bulletPoints: [ChatBulletPoint]
    let dataUpdatedText: String

    enum SentimentType: String {
        case bullish = "Bullish"
        case bearish = "Bearish"
        case neutral = "Neutral"

        var color: Color {
            switch self {
            case .bullish: return AppColors.bullish
            case .bearish: return AppColors.bearish
            case .neutral: return AppColors.neutral
            }
        }
    }
}

// MARK: - Chat Bullet Point
struct ChatBulletPoint: Identifiable {
    let id = UUID()
    let text: String
    let indicatorType: IndicatorType

    enum IndicatorType {
        case success  // Green checkmark
        case warning  // Yellow/amber triangle
        case info     // Blue info circle

        var color: Color {
            switch self {
            case .success: return AppColors.bullish
            case .warning: return AppColors.neutral
            case .info: return AppColors.primaryBlue
            }
        }

        var iconName: String {
            switch self {
            case .success: return "checkmark.circle.fill"
            case .warning: return "exclamationmark.triangle.fill"
            case .info: return "info.circle.fill"
            }
        }
    }
}

// MARK: - Stock Performance
struct StockPerformance: Identifiable {
    let id = UUID()
    let currentPrice: Double
    let changePercent: Double
    let period: String
    let dayHigh: Double
    let dayLow: Double
    let volume: String
    let avgVolume: String
    let chartData: [Double]
    let followUpQuestion: String?

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }

    var formattedDayHigh: String {
        String(format: "$%.2f", dayHigh)
    }

    var formattedDayLow: String {
        String(format: "$%.2f", dayLow)
    }
}

// MARK: - Risk Factor
struct RiskFactor: Identifiable {
    let id = UUID()
    let iconName: String
    let iconColor: Color
    let title: String
    let description: String
    let impactLevel: ImpactLevel

    enum ImpactLevel: String {
        case high = "High Impact"
        case medium = "Medium Impact"
        case variable = "Variable Impact"

        var color: Color {
            switch self {
            case .high: return AppColors.bearish
            case .medium: return AppColors.neutral
            case .variable: return AppColors.primaryBlue
            }
        }
    }
}

// MARK: - Risk Factors Data
struct RiskFactorsData: Identifiable {
    let id = UUID()
    let introText: String
    let factors: [RiskFactor]
}

// MARK: - Tip Data
struct TipData: Identifiable {
    let id = UUID()
    let title: String
    let content: String
}

// MARK: - Page Indicator
struct PageIndicatorData {
    let currentPage: Int
    let totalPages: Int
}

// MARK: - Tolerant ISO-8601 parsing

/// Parse a backend ISO-8601 timestamp, tolerating BOTH fractional-second and whole-second forms.
/// `ISO8601DateFormatter` with `.withFractionalSeconds` returns `nil` for a timestamp that has no
/// fractional part (e.g. a Postgres `now()` / Python `isoformat()` landing on an exact second), so
/// we fall back to the no-fractional formatter. Without this, such rows silently parse to `Date()`
/// (now) — corrupting message timestamps and bucketing history into the wrong day. Formatters are
/// cached (creating an `ISO8601DateFormatter` per parse is expensive).
enum BackendISO8601 {
    private static let withFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let withoutFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func date(from string: String) -> Date? {
        withFractional.date(from: string) ?? withoutFractional.date(from: string)
    }
}

// MARK: - Backend Response DTOs (Codable)

/// Matches backend ``ChatSessionResponse``.
struct ChatSessionDTO: Codable, Identifiable, Sendable {
    let id: String
    let title: String?
    let sessionType: String?
    let stockId: String?
    let contextType: String?
    let referenceId: String?
    let previewMessage: String?
    let messageCount: Int
    let isSaved: Bool
    let createdAt: String
    let lastMessageAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case sessionType = "session_type"
        case stockId = "stock_id"
        case contextType = "context_type"
        case referenceId = "reference_id"
        case previewMessage = "preview_message"
        case messageCount = "message_count"
        case isSaved = "is_saved"
        case createdAt = "created_at"
        case lastMessageAt = "last_message_at"
    }

    /// Parsed context type (nil for legacy/general sessions).
    var chatContextType: ChatContextType? {
        contextType.flatMap { ChatContextType(rawValue: $0) }
    }

    /// Map session_type to ChatHistoryItemType for the history panel.
    var historyItemType: ChatHistoryItemType {
        switch sessionType?.uppercased() {
        case "STOCK": return .stock
        case "BOOK": return .book
        case "CONCEPT": return .concept
        case "JOURNEY": return .journey
        case "REPORT": return .report
        default: return .normal
        }
    }

    /// Parse createdAt into Date (tolerant of whole-second timestamps — see BackendISO8601).
    var date: Date {
        BackendISO8601.date(from: lastMessageAt ?? createdAt)
            ?? BackendISO8601.date(from: createdAt)
            ?? Date()
    }

    /// Convert to ChatHistoryItem for the history panel UI.
    func toChatHistoryItem() -> ChatHistoryItem {
        ChatHistoryItem(
            sessionId: id,
            type: historyItemType,
            title: title ?? "Chat",
            preview: previewMessage ?? "",
            timestamp: date,
            isSaved: isSaved
        )
    }
}

/// What one chat turn cost the user. Matches backend ``ChatTurnCost``.
///
/// Chat is a flat 1 credit and stays that way, so this exists to surface the cases where a
/// turn cost LESS — a follow-up covered by the previous turn, or a credit handed back
/// because the answer was degraded or came from a zero-cost cache hit. A normally-charged
/// turn sends no payload at all, so there is nothing to render and no meter on every answer.
///
/// Every field is Optional/defaulted: this rides in `rich_content`, so it is absent on every
/// message written before the feature shipped.
struct ChatTurnCostDTO: Codable, Equatable, Sendable {
    /// "charged" | "free_followup" | "refunded" | "guest"
    let outcome: String
    /// Credits actually retained for this turn.
    let credits: Int
    /// Machine-readable refund reason (`chat_cache_hit`, `chat_degraded_unmerged`, …).
    let reason: String?
    /// Server-authored display string. Rendered verbatim — never composed on the client, so
    /// the wording can change without an App Store release.
    let label: String?
    /// Spendable balance after this turn. LIVE-ONLY: it arrives on the SSE frame and is never
    /// persisted, because replaying "42 credits left" on a three-day-old message would be
    /// showing a number that was true once.
    let balance: Int?

    enum CodingKeys: String, CodingKey { case outcome, credits, reason, label, balance }

    init(outcome: String, credits: Int, reason: String? = nil, label: String? = nil, balance: Int? = nil) {
        self.outcome = outcome
        self.credits = credits
        self.reason = reason
        self.label = label
        self.balance = balance
    }

    /// NEVER-THROWING, like every other rich-content decoder in this file. `credit` rides
    /// inside `rich_content` on the persisted row, and `ChatMessageDTO` decodes the whole
    /// history in one pass — a synthesized decoder here would have let ONE row with a
    /// missing `credits` or a non-integer collapse the entire conversation into a blank
    /// screen. The backend writes this shape today; the guard is for the row it writes next
    /// year. A missing outcome reads as a plain charge, which renders nothing.
    init(from decoder: Decoder) throws {
        // The container fetch itself must not throw either: a `credit` value that is not
        // an object (a bare string, a number, `[]`) would otherwise propagate through the
        // synthesized `ChatMessageDTO` decoder and fail the whole history.
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            outcome = "charged"; credits = 0; reason = nil; label = nil; balance = nil
            return
        }
        outcome = (try? c.decodeIfPresent(String.self, forKey: .outcome)) ?? "charged"
        credits = (try? c.decodeIfPresent(Int.self, forKey: .credits)) ?? 0
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        label = try? c.decodeIfPresent(String.self, forKey: .label)
        balance = try? c.decodeIfPresent(Int.self, forKey: .balance)
    }

    /// Whether this turn actually moved the balance, i.e. whether a refresh is worth a request.
    var movedCredits: Bool { outcome == "charged" || outcome == "refunded" }

    /// Whether there is anything to tell the user. False for a plain charge.
    var isWorthShowing: Bool { label?.isEmpty == false }
}

/// Matches backend ``ChatMessageResponse``.
struct ChatMessageDTO: Codable, Identifiable, Sendable {
    let id: String
    let sessionId: String
    let role: String
    let content: String
    let widget: ChatWidgetData?
    /// Phase-2 multi-widget list (a turn can emit chart + comparison chart, …). Optional so old
    /// backend rows/builds decode unchanged; when absent, fall back to the single `widget`.
    let widgets: [ChatWidgetData]?
    let citations: [ChatCitationDTO]?
    let tokensUsed: Int?
    // Futuristic-chat fields — all Optional so old backend responses (which omit them) decode
    // unchanged (synthesized Codable uses decodeIfPresent for optionals → absent = nil).
    let sources: [ChatSource]?
    let suggestions: [String]?
    let thinking: ChatThinking?
    /// What this turn cost. Present only when it cost less than usual (free / refunded).
    let credit: ChatTurnCostDTO?
    /// `true` ONLY when the model cut this answer and no continuation completed it
    /// (backend `ChatMessageResponse.truncated`, rich_content-backed). Absent on every
    /// legacy row and every complete turn → nil → no notice. Optional so a backend that
    /// predates the field decodes unchanged.
    let truncated: Bool?
    /// The server's verdict on whether this turn's screen grounding actually arrived
    /// (backend `ChatMessageResponse.context_grounded`, rich_content-backed). `false` means
    /// the chat ran without it, so the "Grounded on …" chip must not claim it. nil — an old
    /// server, a legacy row, or a context type the server gives no verdict for — leaves the
    /// chip as it was. Optional, so a backend that predates the field decodes unchanged.
    let contextGrounded: Bool?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case role, content, widget, widgets, citations
        case tokensUsed = "tokens_used"
        case sources, suggestions, thinking, credit, truncated
        case contextGrounded = "context_grounded"
        case createdAt = "created_at"
    }

    /// The newest server grounding verdict among `messages` (assistant rows only), or nil
    /// when none carries one.
    static func latestGroundingVerdict(in messages: [ChatMessageDTO]) -> Bool? {
        messages.last(where: { $0.role == "assistant" && $0.contextGrounded != nil })?.contextGrounded
    }

    /// Convert to the UI-facing RichChatMessage.
    func toRichChatMessage() -> RichChatMessage {
        let msgRole: ChatMessageRole = role == "user" ? .user : .assistant
        var richContent: [RichContentType] = []

        // Widgets first (polymorphic). Prefer the Phase-2 `widgets` list; fall back to the single
        // `widget` for legacy rows / old backends. Each renders in order (chart, comparison, …).
        let allWidgets = widgets ?? widget.map { [$0] } ?? []
        for w in allWidgets {
            switch w {
            case .stockChart(let data):
                richContent.append(.stockChart(data))
            case .marketOverview(let data):
                richContent.append(.marketOverview(data))
            case .unknown:
                continue  // unrenderable/future widget → render text-only, don't crash
            }
        }

        // Always add the text content
        if !content.isEmpty {
            richContent.append(.text(content))
        }

        let timestamp = BackendISO8601.date(from: createdAt) ?? Date()

        // The one source sanitizer (empty labels, unattributed web pills, repeated ids) — the
        // live `sources` frame goes through the same function.
        return RichChatMessage(role: msgRole, content: richContent, timestamp: timestamp,
                               thinking: thinking, sources: ChatSource.sanitized(sources),
                               suggestions: suggestions, credit: credit,
                               truncated: msgRole == .assistant && (truncated ?? false),
                               serverId: id.isEmpty ? nil : id)
    }
}

/// Codable citation from backend.
struct ChatCitationDTO: Codable, Sendable {
    let index: Int?
    let source: String?
    let text: String?

    init(index: Int?, source: String?, text: String?) {
        self.index = index
        self.source = source
        self.text = text
    }

    private enum CodingKeys: String, CodingKey { case index, source, text }

    /// Total (never-throwing) decode, for the same reason as `ChatSource`: `citations` is
    /// an optional ARRAY on `ChatMessageDTO`, and array decoding is all-or-nothing — one
    /// non-object element (a legacy row, a hand-edited JSONB) in one message's citations
    /// used to blank the entire history decode. The synthesized decoder also rejected a
    /// numeric-string `index`; every field is optional, so a bad one is simply nil.
    init(from decoder: Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.index = nil; self.source = nil; self.text = nil; return
        }
        self.index = try? c.decode(Int.self, forKey: .index)
        self.source = try? c.decode(String.self, forKey: .source)
        self.text = try? c.decode(String.self, forKey: .text)
    }
}

/// Matches backend ``ChatSessionListResponse``.
struct ChatSessionListDTO: Codable, Sendable {
    let sessions: [ChatSessionDTO]
    /// The PAGE length (historical meaning), not the account's session count.
    let total: Int
    /// Whether another page exists past this one (backend `has_more`). Optional: a
    /// backend that predates it decodes as nil, and the client then falls back to the
    /// page-length heuristic. Drives the history walk that lists EVERY session
    /// (TestFlight 2026-09-16, E6).
    let hasMore: Bool?

    enum CodingKeys: String, CodingKey {
        case sessions, total
        case hasMore = "has_more"
    }
}

/// Matches backend ``ChatHistoryResponse``.
struct ChatHistoryDTO: Codable, Sendable {
    let session: ChatSessionDTO
    let messages: [ChatMessageDTO]
}

// MARK: - Sample Data
extension RichChatMessage {
    static let sampleConversation: [RichChatMessage] = [
        // User asks about Tesla
        RichChatMessage(
            role: .user,
            content: [.text("What's the current sentiment around Tesla stock?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -7, to: Date())!
        ),

        // AI responds with sentiment analysis
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Based on the latest market data and social sentiment analysis, here's what I found about Tesla (TSLA):"),
                .sentimentAnalysis(SentimentAnalysis(
                    overallSentiment: .bullish,
                    percentage: 68,
                    bulletPoints: [
                        ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                        ChatBulletPoint(text: "Cybertruck production ramping up successfully", indicatorType: .success),
                        ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning),
                        ChatBulletPoint(text: "Analyst price targets range from $180-$350", indicatorType: .info)
                    ],
                    dataUpdatedText: "Data updated 5 minutes ago"
                ))
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: -6, to: Date())!
        ),

        // User asks about performance
        RichChatMessage(
            role: .user,
            content: [.text("How's Tesla's stock performance?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -5, to: Date())!
        ),

        // AI responds with stock chart widget (Rich Media)
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Here's Tesla's stock performance over the past month:"),
                .stockChart(StockChartWidgetData.sample)
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: -4, to: Date())!
        ),

        // User asks about risks
        RichChatMessage(
            role: .user,
            content: [.text("What are the key risks to consider?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -2, to: Date())!
        ),

        // AI responds with risk factors
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Here are the major risk factors for Tesla investors to monitor:"),
                .riskFactors(RiskFactorsData(
                    introText: "",
                    factors: [
                        RiskFactor(
                            iconName: "exclamationmark.triangle.fill",
                            iconColor: AppColors.bearish,
                            title: "Market Competition",
                            description: "Traditional automakers and new EV startups intensifying competition globally",
                            impactLevel: .high
                        ),
                        RiskFactor(
                            iconName: "doc.text.fill",
                            iconColor: AppColors.neutral,
                            title: "Regulatory Changes",
                            description: "Potential changes in EV subsidies and environmental regulations",
                            impactLevel: .medium
                        ),
                        RiskFactor(
                            iconName: "shippingbox.fill",
                            iconColor: AppColors.neutral,
                            title: "Supply Chain Constraints",
                            description: "Battery materials and semiconductor availability concerns",
                            impactLevel: .medium
                        ),
                        RiskFactor(
                            iconName: "dollarsign.circle.fill",
                            iconColor: AppColors.primaryBlue,
                            title: "Valuation Concerns",
                            description: "High P/E ratio compared to traditional automakers",
                            impactLevel: .variable
                        )
                    ]
                )),
                .tip(TipData(
                    title: "RISK MITIGATION TIP",
                    content: "Consider diversifying your portfolio and maintaining a long-term investment horizon to weather short-term volatility."
                ))
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: 0, to: Date())!
        )
    ]
}

// MARK: - Sample Widget Data
extension StockChartWidgetData {
    static let sample = StockChartWidgetData(
        widgetType: "stock_chart",
        ticker: "TSLA",
        companyName: "Tesla, Inc.",
        currentPrice: 242.84,
        change: 19.42,
        changePercent: 8.7,
        dayHigh: 245.12,
        dayLow: 238.45,
        volume: 124_500_000,
        avgVolume: 98_200_000,
        marketCap: 789_000_000_000,
        peRatio: 62.3,
        yearHigh: 299.29,
        yearLow: 138.80,
        dayRangeKnown: true,
        changeKnown: true,
        isMarketOpen: true,
        historicalData: [
            HistoricalDataPointDTO(date: "2026-01-30", open: 220, high: 223, low: 218, close: 220, volume: 80_000_000),
            HistoricalDataPointDTO(date: "2026-01-31", open: 221, high: 227, low: 220, close: 225, volume: 85_000_000),
            HistoricalDataPointDTO(date: "2026-02-03", open: 224, high: 225, low: 216, close: 218, volume: 90_000_000),
            HistoricalDataPointDTO(date: "2026-02-04", open: 219, high: 232, low: 218, close: 230, volume: 95_000_000),
            HistoricalDataPointDTO(date: "2026-02-05", open: 230, high: 236, low: 229, close: 235, volume: 88_000_000),
            HistoricalDataPointDTO(date: "2026-02-06", open: 234, high: 235, low: 226, close: 228, volume: 82_000_000),
            HistoricalDataPointDTO(date: "2026-02-07", open: 229, high: 241, low: 228, close: 240, volume: 110_000_000),
            HistoricalDataPointDTO(date: "2026-02-10", open: 240, high: 241, low: 236, close: 238, volume: 100_000_000),
            HistoricalDataPointDTO(date: "2026-02-11", open: 239, high: 246, low: 238, close: 245, volume: 115_000_000),
            HistoricalDataPointDTO(date: "2026-02-12", open: 244, high: 246, low: 240, close: 242, volume: 105_000_000),
            HistoricalDataPointDTO(date: "2026-02-13", open: 242, high: 244, low: 239, close: 240, volume: 98_000_000),
            HistoricalDataPointDTO(date: "2026-02-14", open: 241, high: 245, low: 240, close: 243, volume: 102_000_000),
            HistoricalDataPointDTO(date: "2026-02-18", open: 243, high: 247, low: 241, close: 245, volume: 108_000_000),
            HistoricalDataPointDTO(date: "2026-02-19", open: 244, high: 246, low: 240, close: 241, volume: 96_000_000),
            HistoricalDataPointDTO(date: "2026-02-20", open: 241, high: 244, low: 239, close: 242, volume: 99_000_000),
            HistoricalDataPointDTO(date: "2026-02-21", open: 242, high: 245, low: 240, close: 242.84, volume: 124_500_000),
        ]
    )
}
