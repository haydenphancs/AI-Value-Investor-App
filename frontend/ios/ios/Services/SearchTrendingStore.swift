//
//  SearchTrendingStore.swift
//  ios
//
//  The search-screen chips ("Trending searches" / "Most added" / "Popular") and the
//  device half of recording a search pick.
//
//  ONE shared store, not a per-screen fetch, for two reasons:
//    • the payload is impersonal and identical for every search surface (Home search from
//      seven presenters, the ticker-search sheet, the Tracking add sheet, the company
//      picker, the Updates "Add Ticker" sheet) — so one request an hour, not one per sheet;
//    • the pick de-dup set has to be ONE per device, or a user could count the same ticker
//      again from another surface.
//  Home search injects it through its ViewModel; the other four surfaces have no ViewModel
//  and already call shared services directly.
//
//  ⚠️ `@Observable`, like `ChatStartersStore` (the store this mirrors): the chips are read
//  inside `View` bodies, and a plain class would never redraw when the fetch lands — the
//  bundled fallback would sit there for the whole visit.
//
//  PRIVACY. A pick is a tap on a search RESULT row — never a chip, never a recent-search
//  row (either would feed the list its own output). It is sent at most once per ticker per
//  7 ET calendar days from this device; the server de-duplicates again and keeps only an
//  anonymous daily counter (migration 179). The remembered set is this user's own picks on
//  a device-global key, so it is cleared when the session ends (auth.md §7).
//

import Foundation
import OSLog

@MainActor
@Observable
final class SearchTrendingStore {

    static let shared = SearchTrendingStore()

    enum Scope {
        /// Every asset type.
        case all
        /// Equities only — surfaces that act on a company.
        case stocksOnly
    }

    /// The most recent server payload. `nil` until the first successful fetch.
    private(set) var remote: SearchTrendingDTO?
    private(set) var fetchedAt: Date?

    @ObservationIgnored private let apiClient: APIClient
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let log = Logger(subsystem: "com.phan.caydex", category: "search-trending")
    @ObservationIgnored private var prefetchTask: Task<Void, Never>?
    @ObservationIgnored private var lastAttemptAt: Date?
    @ObservationIgnored private var pendingPicks = Set<String>()
    /// Bumped when a session ends, so a fetch or a pick still in flight cannot write the
    /// ended session's result into the next account's state.
    @ObservationIgnored private var epoch = 0

    /// The server recomputes hourly; asking more often only returns the same answer.
    static let freshness: TimeInterval = 3600
    /// Floor between attempts, so a failing or older backend is not asked on every sheet.
    private static let minimumRetryInterval: TimeInterval = 300

    static let countedPicksKey = "search.trending.counted.v1"
    static let pickWindowDays = 7
    static let maxCountedPicks = 300
    static let pickTypes: Set<String> = ["stock", "etf", "fund", "crypto"]

    init(apiClient: APIClient = .shared, defaults: UserDefaults = .standard) {
        self.apiClient = apiClient
        self.defaults = defaults
    }

    // MARK: - Reading

    /// The sections one surface shows. Never empty: the bundled "Popular" list covers a
    /// cold start, an offline launch and a backend that predates the route.
    func sections(for scope: Scope) -> [SearchTrendingSection] {
        if let remote {
            let stocksOnly = scope == .stocksOnly
            let dtos = stocksOnly ? remote.stockSections : remote.sections
            let days = (1...31).contains(remote.windowDays) ? remote.windowDays : 7
            var kinds = Set<SearchTrendingKind>()
            let built = dtos
                .compactMap { SearchTrendingSection(dto: $0, windowDays: days, stocksOnly: stocksOnly) }
                .filter { kinds.insert($0.kind).inserted }
            if !built.isEmpty { return built }
        }
        return [SearchTrendingSection.bundledPopular(stocksOnly: scope == .stocksOnly)]
    }

    // MARK: - Fetching

    /// Safe from every surface's `.task`: a fresh store returns at once, concurrent callers
    /// join one request, and attempts are spaced at least five minutes apart.
    func prefetch() async {
        if remote != nil, let fetchedAt, Date().timeIntervalSince(fetchedAt) < Self.freshness {
            return
        }
        if let last = lastAttemptAt, Date().timeIntervalSince(last) < Self.minimumRetryInterval {
            return
        }
        // Join an in-flight fetch. The latch is cleared only AFTER the await, as in
        // ChatStartersStore — clearing it first let a second caller read an empty store.
        if let existing = prefetchTask {
            await existing.value
            return
        }
        let task = Task { await self.load() }
        prefetchTask = task
        await task.value
        prefetchTask = nil
    }

    private func load() async {
        lastAttemptAt = Date()
        let started = epoch
        do {
            let payload = try await apiClient.request(
                endpoint: .getSearchTrending,
                responseType: SearchTrendingDTO.self
            )
            guard epoch == started else { return }
            remote = payload
            fetchedAt = Date()
        } catch {
            let appError = AppError.from(error)
            // Non-fatal by design: the bundled list covers it, and nothing the user did failed.
            log.warning("trending fetch failed [\(appError.title, privacy: .public)]: \(appError.message, privacy: .public) — keeping what we have")
        }
    }

    // MARK: - Picks

    /// Record a tap on a search RESULT row. Fire-and-forget: the user's action (opening or
    /// adding the ticker) has already succeeded; this is a side effect and never surfaces.
    ///
    /// ⚠️ Never call this from a chip, a recent-search row or the watchlist star.
    func recordPick(symbol: String, type: String?) {
        let sym = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let kind = (type ?? "stock").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !sym.isEmpty, Self.pickTypes.contains(kind) else { return }

        // Keyed on the security CLASS, like the server: stock/etf/fund for one symbol are one
        // security (the SQL sums them), a coin is its own.
        let key = "\(sym)_\(kind == "crypto" ? "crypto" : "security")"
        let today = Self.etDayNumber()
        if let last = countedPicks()[key], today - last < Self.pickWindowDays { return }
        guard pendingPicks.insert(key).inserted else { return }

        let started = epoch
        Task { [weak self] in
            guard let self else { return }
            defer { self.pendingPicks.remove(key) }
            do {
                try await self.apiClient.request(endpoint: .recordSearchPick(symbol: sym, type: kind))
                // Remembered only after the server took it, and only for the same session.
                guard self.epoch == started else { return }
                var counted = self.countedPicks()
                counted[key] = today
                self.saveCountedPicks(counted, today: today)
            } catch {
                let appError = AppError.from(error)
                self.log.info("search pick not sent [\(appError.title, privacy: .public)]")
            }
        }
    }

    private func countedPicks() -> [String: Int] {
        (defaults.dictionary(forKey: Self.countedPicksKey) as? [String: Int]) ?? [:]
    }

    private func saveCountedPicks(_ counted: [String: Int], today: Int) {
        let recent = counted.filter { today - $0.value < Self.pickWindowDays }
        let bounded = recent.count <= Self.maxCountedPicks
            ? recent
            : Dictionary(
                recent.sorted { $0.value > $1.value }
                    .prefix(Self.maxCountedPicks).map { ($0.key, $0.value) },
                uniquingKeysWith: { first, _ in first }
            )
        defaults.set(bounded, forKey: Self.countedPicksKey)
    }

    // MARK: - Session

    /// Forget everything tied to the ended session: the remembered picks are that user's
    /// own, on a device-global key; the payload is impersonal but the next account should
    /// fetch its own.
    func clearForEndedSession() {
        epoch += 1
        prefetchTask?.cancel()
        prefetchTask = nil
        remote = nil
        fetchedAt = nil
        lastAttemptAt = nil
        pendingPicks.removeAll()
        defaults.removeObject(forKey: Self.countedPicksKey)
    }

    // MARK: - ET calendar

    /// Today's ET calendar day as a day ordinal. ⚠️ Calendar DAYS, never 168 hours: across
    /// the autumn DST change 168 h lands on the sixth day and would send the pick twice.
    static func etDayNumber(_ date: Date = Date()) -> Int {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York") ?? .gmt
        return calendar.ordinality(of: .day, in: .era, for: date) ?? 0
    }
}
