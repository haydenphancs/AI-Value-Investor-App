//
//  SearchHistoryStore.swift
//  ios
//
//  The user's own search history: tickers they opened and questions they asked Cay AI.
//
//  WHY THIS EXISTS. "Recent Searches" on SearchView was never a history. It rendered
//  `SearchViewModel.recentSearches`, which was the LIVE results array — reassigned from the API
//  on every keystroke and emptied by the debounce sink the moment the field went blank. So at
//  rest the section could only ever read "No recent searches", and nothing in the app recorded a
//  ticker the user opened or a question they asked. This store is the record that was missing.
//
//  DEVICE-LOCAL ON PURPOSE. Search history is cheap, personal and disposable — it does not
//  justify a Supabase table, an endpoint, an RLS policy and the guest-partition checklist in
//  `.claude/rules/auth.md` §1a. The cost of that choice is that it does NOT follow the user to
//  another device, which is the normal trade for search history.
//
//  ⚠️ Because the key carries no user id, this is exactly the store class that leaks across
//  accounts. It MUST be reset from `AppState.discardDataForEndedSession()` — the same funnel
//  that clears the four Learn stores and `WhaleService.followedWhaleIds`, both of which shipped
//  this bug first. Pinned by `tests/test_ios_search_history_guards.py`.
//

import Combine
import Foundation
import OSLog

@MainActor
final class SearchHistoryStore: ObservableObject {
    static let shared = SearchHistoryStore()

    /// Most-recent-first. The head is the last thing the user searched or asked.
    @Published private(set) var entries: [SearchHistoryEntry] = []

    /// Past this, the oldest entries are dropped on write. A search history is a shortcut, not
    /// an archive — and an unbounded `UserDefaults` blob decoded on every launch is a slow leak
    /// nobody would ever notice.
    static let maxEntries = 20

    private static let defaultsKey = "search.history.entries"
    /// `os.Logger`, not `print()`. CLAUDE.md bans `print()` in production code; the older
    /// stores in this folder predate that and should not be copied here.
    private static let log = Logger(subsystem: "com.phan.caydex", category: "search-history")
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        entries = Self.load(from: defaults)
    }

    // MARK: - Recording

    /// Record a ticker the user actually OPENED — not every keystroke.
    ///
    /// Recording on selection rather than on typing is what makes this a history instead of a
    /// transcript of the keyboard: "AAPL" typed one letter at a time would otherwise land four
    /// rows deep before the user had chosen anything.
    func record(ticker: String, name: String?, rawType: String?) {
        let symbol = ticker.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !symbol.isEmpty else { return }
        insert(
            SearchHistoryEntry(
                kind: .ticker,
                text: symbol,
                subtitle: name?.isEmpty == false ? name : nil,
                rawType: rawType
            )
        )
    }

    /// Newest-first insert with de-duplication.
    ///
    /// Re-searching AAPL MOVES it to the head rather than stacking a second row — otherwise the
    /// list fills with one repeated symbol and the cap evicts everything else. Matching is
    /// case-insensitive and scoped to the same `kind`, so the ticker "TSLA" and a question that
    /// happens to read "tsla" stay separate rows.
    private func insert(_ entry: SearchHistoryEntry) {
        var next = entries.filter {
            !($0.kind == entry.kind && $0.text.caseInsensitiveCompare(entry.text) == .orderedSame)
        }
        next.insert(entry, at: 0)
        if next.count > Self.maxEntries {
            next = Array(next.prefix(Self.maxEntries))
        }
        entries = next
        persist()
    }

    // MARK: - Removal

    func remove(_ entry: SearchHistoryEntry) {
        entries.removeAll { $0.id == entry.id }
        persist()
    }

    /// User-initiated "Clear All".
    func clearAll() {
        guard !entries.isEmpty else { return }
        entries = []
        persist()
    }

    /// Session ended (sign-out, or a credential that turned out to be dead).
    ///
    /// Deliberately a separate method from `clearAll()` even though the bodies agree today: one
    /// is a user action and the other is a security boundary, and a future "keep history across
    /// sign-out" preference must not be able to weaken the second by editing the first.
    func reset() {
        entries = []
        defaults.removeObject(forKey: Self.defaultsKey)
    }

    // MARK: - Persistence

    private func persist() {
        do {
            defaults.set(try JSONEncoder().encode(entries), forKey: Self.defaultsKey)
        } catch {
            // Non-fatal: the in-memory list is still correct for this run, so the user sees the
            // right thing and only loses it on relaunch. Logged rather than swallowed —
            // silent degradation is the hardest bug to find (CLAUDE.md, error handling).
            Self.log.warning("could not persist history — \(type(of: error), privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }

    private static func load(from defaults: UserDefaults) -> [SearchHistoryEntry] {
        guard let data = defaults.data(forKey: defaultsKey) else { return [] }
        do {
            // Tickers only. Search can no longer ask Cay AI, so a stored `.question` row has
            // nothing to reopen — tapping one would be a dead row that looks alive. Filtered on
            // READ rather than migrated on write, because the decode has to tolerate those rows
            // anyway (see `SearchHistoryEntry.Kind.question`) and a rewrite pass would be one
            // more thing that can fail between versions.
            return Array(try JSONDecoder().decode([SearchHistoryEntry].self, from: data)
                .filter { $0.kind == .ticker }
                .prefix(maxEntries))
        } catch {
            // A blob written by an older shape. Drop it rather than fail the screen: history is
            // disposable, and a decode error here must never be able to block search.
            log.warning("discarding unreadable history — \(type(of: error), privacy: .public): \(error.localizedDescription, privacy: .public)")
            defaults.removeObject(forKey: defaultsKey)
            return []
        }
    }
}
