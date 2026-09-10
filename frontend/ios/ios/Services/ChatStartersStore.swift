//
//  ChatStartersStore.swift
//  ios
//
//  One fetch per ET day of the rotating starter questions, shared by the global chat
//  cover and all five asset detail AI bars.
//
//  ⚠️ `@Observable`, NOT a plain class — and the difference is load-bearing.
//  `MoneyMovesContentStore` (the store this mirrors) is a plain `final class`, which is
//  fine there because its callers re-read it on an explicit refresh. Here the detail
//  bars read their questions from computed properties that SwiftUI has to be told to
//  re-evaluate: with a plain class the fetch lands and nothing redraws, so the user sees
//  the bundled fallback for the whole screen visit and the daily rotation looks broken.
//  For that to work the read must happen inside a `View` body — reading an `@Observable`
//  property anywhere else establishes no dependency.
//

import Foundation
import OSLog

@MainActor
@Observable
final class ChatStartersStore {

    static let shared = ChatStartersStore()

    /// The most recent server payload. `nil` until the first successful fetch.
    private(set) var remote: ChatStartersDTO?

    /// The ET day `remote` was computed for. The refetch key — see `prefetch()`.
    private(set) var remoteTradingDate: String = ""

    private let apiClient: APIClient
    private let log = Logger(subsystem: "com.phan.caydex", category: "chat-starters")

    private var prefetchTask: Task<Void, Never>?
    private var lastAttemptAt: Date?

    /// Floor between attempts. Without it a rollover with a failing backend — or a device
    /// whose clock disagrees with ours about the date — would refetch on every screen
    /// appearance for as long as the disagreement lasted.
    private static let minimumRetryInterval: TimeInterval = 300

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    // MARK: - Reading

    /// Chips for the empty state of the global chat.
    ///
    /// Falls back to the bundled catalogue, rotated on-device by the same ET day, so an
    /// offline or signed-out launch still shows a set that changes daily rather than the
    /// five frozen strings this feature replaced.
    var globalStarters: [String] {
        if let remote, !remote.globalStarters.isEmpty {
            return dedupe(remote.globalStarters.map(\.text))
        }
        return dedupe(
            Self.rotate(BundledChatStarters.shared.global, count: 8, day: Self.currentETDate())
        )
    }

    /// Question templates for one asset detail bar, with the symbol filled in.
    func detailStarters(for scope: ChatStarterScope, symbol: String) -> [String] {
        let templates: [String] = {
            let fromServer = remote?.detailStarters.templates(for: scope) ?? []
            if !fromServer.isEmpty { return fromServer }
            return Self.rotate(
                BundledChatStarters.shared.templates(for: scope),
                count: 4,
                day: Self.currentETDate(),
                salt: scope.rawValue
            )
        }()

        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        return dedupe(
            templates
                .map { $0.replacingOccurrences(of: "{symbol}", with: trimmed) }
                // A template carrying a placeholder we do not know how to fill is DROPPED,
                // never shown. Rendering a raw "{holder}" to a user is worse than one
                // fewer chip, and a shorter row is invisible where a stray brace is not.
                .filter { !$0.contains("{") && !$0.contains("}") }
        )
    }

    // MARK: - Fetching

    /// Fetch today's starters if we do not already have them.
    ///
    /// Safe to call from every screen's `onAppear`: concurrent callers join the same task,
    /// a warm store returns immediately, and the whole thing is gated on the ET date. That
    /// is what makes six call sites cost one request per day rather than six per launch.
    func prefetch() async {
        let today = Self.currentETDate()
        if remote != nil && remoteTradingDate == today { return }

        if let last = lastAttemptAt,
           Date().timeIntervalSince(last) < Self.minimumRetryInterval,
           remote != nil {
            return
        }

        // Join an in-flight fetch rather than starting a second one. The latch is set only
        // AFTER the await below, deliberately: setting it before is the bug that shipped in
        // MoneyMovesContentStore, where a second caller arriving during the request saw the
        // flag, returned early, and read an empty store.
        if let existing = prefetchTask {
            await existing.value
            return
        }

        let task = Task { await self.load(expecting: today) }
        prefetchTask = task
        await task.value
        prefetchTask = nil
    }

    private func load(expecting day: String) async {
        lastAttemptAt = Date()
        do {
            let payload = try await apiClient.request(
                endpoint: .getChatStarters,
                responseType: ChatStartersDTO.self
            )
            guard !payload.globalStarters.isEmpty else {
                // A successful-but-empty body must not overwrite a good set; the bundled
                // fallback is better than nothing, and the next appearance retries.
                log.warning("starters response carried no chips — keeping what we have")
                return
            }
            remote = payload
            // Trust the SERVER's date, not ours. If the two disagree — a device in another
            // timezone, a wrong clock — adopting the server's is what stops `prefetch`
            // deciding it is still stale and refetching on every appearance.
            remoteTradingDate = payload.tradingDate.isEmpty ? day : payload.tradingDate
        } catch {
            let appError = AppError.from(error)
            // Non-fatal by design: the bundled catalogue covers this. Logged rather than
            // surfaced because nothing the user did failed — there is no action to offer.
            log.warning("starters fetch failed [\(appError.title, privacy: .public)]: \(appError.message, privacy: .public) — using the bundled catalogue")
        }
    }

    /// Drop everything tied to the ended session.
    ///
    /// The payload itself is impersonal, so this is not a privacy fix — it is a freshness
    /// one: the next account in should not inherit a day-key that stops it fetching.
    func clearForEndedSession() {
        prefetchTask?.cancel()
        prefetchTask = nil
        remote = nil
        remoteTradingDate = ""
        lastAttemptAt = nil
    }

    // MARK: - Helpers

    private func dedupe(_ items: [String]) -> [String] {
        // The chip row is a `ForEach` keyed by the string itself, so two identical entries
        // collapse into one element and silently shorten the row.
        var seen = Set<String>()
        return items.filter { seen.insert($0.lowercased()).inserted }
    }

    /// Today's date in ET.
    ///
    /// ⚠️ Never the device locale. "Today" here means the market's day — the server
    /// composes on the same convention (`trading_date_et()`), and a user in Tokyo reading
    /// their own calendar would roll over a day early and refetch against a server that
    /// still says yesterday.
    static func currentETDate() -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York") ?? .gmt
        let parts = calendar.dateComponents([.year, .month, .day], from: Date())
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// The on-device half of the rotation, used only when the server is unreachable.
    ///
    /// Deliberately simple — a stride over the sorted pool rather than a reimplementation
    /// of the backend's shuffle. Two different algorithms would be two things to keep in
    /// sync for a path the user only reaches offline; what matters here is that the set
    /// still CHANGES daily and never repeats within a day.
    static func rotate(_ pool: [String], count: Int, day: String, salt: String = "") -> [String] {
        let items = Array(Set(pool)).sorted()
        guard !items.isEmpty, count > 0 else { return [] }
        guard items.count > count else { return items }

        // Derive the offset from the DATE STRING itself. NOT `hashValue`: Swift seeds
        // string hashing per process launch, so that would reshuffle the row on every
        // cold start and the questions would stop looking daily.
        let stable = day.utf8.reduce(0) { ($0 &* 31 &+ Int($1)) % 100_003 }
        let saltShift = salt.utf8.reduce(0) { ($0 &* 31 &+ Int($1)) % 97 }
        let start = (stable &+ saltShift &* count) % items.count
        return (0..<count).map { items[(start + $0) % items.count] }
    }
}
