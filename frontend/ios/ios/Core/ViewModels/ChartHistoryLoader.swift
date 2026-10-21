//
//  ChartHistoryLoader.swift
//  ios
//
//  One per detail ViewModel. Holds a CONTIGUOUS, date-ordered store of the bars the chart
//  can scroll through for one (symbol, interval) — every range payload the VM absorbs plus
//  every older page fetched as the user pans left — and hands the chart the part that lies
//  strictly before the current payload.
//
//  Why a store and not "older pages + the latest payload": range payloads for one interval
//  start at different dates (3M daily starts later than 6M daily; crypto 1W is a rolling
//  window). Concatenating older pages with whichever payload came last leaves a HOLE when the
//  new payload starts later than the pages end, and every MA/RSI/MACD across the hole is
//  silently wrong. Absorbing every payload keeps the series contiguous by construction.
//

import Combine
import Foundation
import os

@MainActor
final class ChartHistoryLoader: ObservableObject {
    struct Key: Equatable {
        let symbol: String
        let interval: ChartInterval
        let extendedHours: Bool
        /// 1D is one session laid out by time of day and never pans: no older merge there.
        let pannable: Bool
    }

    @Published private(set) var state: ChartHistoryState = .empty

    private let asset: ChartHistoryAsset
    private let repository: StockRepositoryProtocol
    private let log = Logger(subsystem: "com.caydex.ios", category: "ChartHistory")

    private var key: Key?
    /// Ascending, contiguous: older pages, then the latest payload.
    private var store: [StockPricePoint] = []
    /// Index in `store` of the latest payload's first bar.
    private var payloadStart = 0
    /// Stop asking for older pages (first bar reached, a provider bound, a refusal, ALL).
    private var exhausted = false
    /// The store begins at the symbol's FIRST bar — the backend's `reached_start`. Kept apart
    /// from `exhausted`: paging that stops at a look-back bound or a plan cap leaves warm-up
    /// bars at the left edge, and the chart may show those only at the true start.
    private var reachedStart = false
    /// The store hit `maxStoredBars`: stop loading, but keep the indicator floor (the bars
    /// past the cap still exist, so the leftmost window is not the true start).
    private var capped = false
    /// The last payload had bars. An empty write (a failed range switch) draws nothing, so
    /// no older bars are published for it — but the store is KEPT for the next payload.
    private var payloadPresent = false
    private var settledThrough: String?
    private var inFlight: Task<Void, Never>?
    private var generation = 0
    private var lastFailureAt: Date?

    private static let maxStoredBars = 30_000
    private static let retryAfterFailure: TimeInterval = 10
    /// A payload whose first close disagrees with the stored bar for the same date by more
    /// than this was adjusted differently (a split settled between the two fetches).
    private static let seamTolerance = 0.005

    init(asset: ChartHistoryAsset, repository: StockRepositoryProtocol = StockRepository.shared) {
        self.asset = asset
        self.repository = repository
    }

    // MARK: - Payloads

    /// Call at EVERY write of the screen's chart bars (first paint, range switch, live tick,
    /// core→full) with the range/interval those bars were fetched for.
    func absorb(_ payload: [StockPricePoint], symbol: String, range: ChartTimeRange, interval: ChartInterval, extendedHours: Bool) {
        let newKey = Key(
            symbol: symbol.uppercased(),
            interval: interval,
            extendedHours: interval.isIntraday && extendedHours,
            pannable: range != .oneDay
        )
        if newKey != key { reset(to: newKey) }
        // ALL is not marked exhausted here: the chart never pages on ALL, and a key shared
        // with 5Y/1Y must still learn from the BACKEND whether its first bar is the
        // symbol's (CoinGecko's ALL starts at a plan cap). An empty ALL write marked the key
        // done forever.

        guard newKey.pannable else {
            store = payload
            payloadStart = 0
            payloadPresent = !payload.isEmpty
            publish(barsChanged: true)
            return
        }
        guard let first = payload.first?.date else {
            // Draw nothing older under an empty payload, but keep the store: wiping it here
            // left `exhausted` / `reachedStart` / `settledThrough` describing bars that no
            // longer existed, so the next payload could never page in again.
            payloadPresent = false
            publish(barsChanged: true)
            return
        }
        payloadPresent = true

        let cut = ChartSeries.lowerBound(store.map(\.date), first)
        var older = Array(store[0..<cut])
        if !older.isEmpty {
            if cut < store.count, store[cut].date == first {
                // Seam check on the shared bar: a differently-adjusted payload means the
                // stored history no longer lines up (a split) — drop it and page it again.
                let stored = store[cut].close
                let fresh = payload[0].close
                if stored > 0, abs(fresh - stored) / stored > Self.seamTolerance {
                    log.warning("chart history seam mismatch for \(newKey.symbol, privacy: .public) at \(first, privacy: .public) (\(stored) vs \(fresh)) — dropping older bars")
                    older = []
                    restartOlder()
                    settledThrough = nil
                }
            } else if cut == store.count {
                // The store ends BEFORE this payload begins: there is a gap. Never bridge it.
                older = []
                restartOlder()
            }
        }
        store = older + payload
        payloadStart = older.count
        publish(barsChanged: true)
    }

    /// The older bars were dropped: paging starts again from the payload's own first bar.
    private func restartOlder() {
        exhausted = false
        reachedStart = false
        capped = false
    }

    // MARK: - Older pages

    /// The chart is panning toward (or sits at) its left floor. Leading-edge and
    /// single-flight: repeats while a page is in flight are absorbed; after a failure a
    /// new attempt waits `retryAfterFailure`.
    /// `force` = the user tapped Retry: skip the post-failure back-off.
    func requestOlder(force: Bool = false) {
        guard let key, key.pannable, payloadPresent, !exhausted, !capped, inFlight == nil,
              let oldest = store.first?.date else { return }
        if !force, let lastFailureAt, Date().timeIntervalSince(lastFailureAt) < Self.retryAfterFailure { return }

        let gen = generation
        publish(status: .loading)
        inFlight = Task { [weak self, repository, asset] in
            do {
                let page = try await repository.getChartHistory(
                    asset: asset,
                    symbol: key.symbol,
                    interval: key.interval.rawValue,
                    before: oldest,
                    limit: nil,
                    extendedHours: key.extendedHours
                )
                guard let self, self.generation == gen, self.key == key else { return }
                self.apply(page, before: oldest)
            } catch {
                guard let self, self.generation == gen else { return }
                self.inFlight = nil
                guard !Task.isCancelled else { return }
                self.fail(error, key: key, before: oldest)
            }
        }
    }

    private func apply(_ page: ChartHistoryResponse, before: String) {
        inFlight = nil
        lastFailureAt = nil
        guard page.interval == key?.interval.rawValue else {
            // The backend substitutes the interval it can serve (a crypto pair is hourly
            // intraday whatever finer size was asked). Bars of another size must never be
            // merged into this series — every indicator across them would be wrong.
            log.warning("chart history for \(page.symbol, privacy: .public) served \(page.interval, privacy: .public), asked \(self.key?.interval.rawValue ?? "-", privacy: .public) — not merged")
            exhausted = true
            publish()
            return
        }
        guard store.first?.date == before else {
            // The store moved while the page was in flight (a payload replaced it): the page
            // no longer abuts it. Drop it; the next pan asks again from the new edge.
            publish()
            return
        }
        if let had = settledThrough, let now = page.settledThrough, had != now {
            // The backend's history was rebuilt (new close cycle, possibly a split) — the
            // stored older bars may be adjusted differently from this page. Restart them.
            log.info("chart history settled_through changed (\(had, privacy: .public) → \(now, privacy: .public)); restarting older bars")
            let droppedOlder = payloadStart > 0
            store = Array(store[payloadStart...])
            payloadStart = 0
            reachedStart = false
            settledThrough = now
            if droppedOlder {
                // This page was cut at the edge just dropped (`before` was the oldest STORED
                // bar), so every bar in it is older than the hole that now separates it
                // from the payload. Merging it would compute every indicator across that
                // hole. Discard it: the next request asks again from the payload's start.
                publish(barsChanged: true)
                return
            }
        }
        settledThrough = page.settledThrough ?? settledThrough

        // Strictly older than the cursor, ascending, de-duplicated — never trust the edge.
        var bars = page.bars.filter { $0.date < before }
        if !bars.isEmpty, zip(bars, bars.dropFirst()).contains(where: { $0.date >= $1.date }) {
            bars.sort { $0.date < $1.date }
            var unique: [StockPricePoint] = []
            unique.reserveCapacity(bars.count)
            for b in bars where unique.last?.date != b.date { unique.append(b) }
            bars = unique
        }
        guard !bars.isEmpty else {
            if page.hasMore {
                // No cursor to advance with: asking again would repeat the same request.
                log.warning("chart history page for \(page.symbol, privacy: .public) returned no bars before \(before, privacy: .public) but has_more=true — treating as exhausted")
            } else {
                // Nothing older: the store already begins where the source does — the
                // symbol's first bar only if the backend says so.
                reachedStart = page.reachedStart ?? false
            }
            exhausted = true
            publish()
            return
        }
        store = bars + store
        payloadStart += bars.count
        if !page.hasMore {
            exhausted = true
            reachedStart = page.reachedStart ?? false
        }
        if store.count > Self.maxStoredBars {
            let excess = store.count - Self.maxStoredBars
            store.removeFirst(excess)
            payloadStart = max(0, payloadStart - excess)
            capped = true
            reachedStart = false
        }
        publish(barsChanged: true)
    }

    private func fail(_ error: Error, key: Key, before: String) {
        let appError = AppError.from(error)
        if let apiError = error as? APIError, case .notFound = apiError {
            // An older backend without the route: stop asking for this session.
            exhausted = true
        } else if case .validationFailed = appError {
            exhausted = true
        } else {
            lastFailureAt = Date()
        }
        log.error("chart history failed for \(key.symbol, privacy: .public) \(key.interval.rawValue, privacy: .public) before \(before, privacy: .public): \(String(describing: error), privacy: .public)")
        publish(status: exhausted ? .exhausted : .failed)
    }

    // MARK: - State

    private func reset(to newKey: Key) {
        inFlight?.cancel()
        inFlight = nil
        generation &+= 1
        key = newKey
        store = []
        payloadStart = 0
        exhausted = false
        reachedStart = false
        capped = false
        payloadPresent = false
        settledThrough = nil
        lastFailureAt = nil
    }

    private func currentStatus() -> ChartHistoryState.Status {
        if exhausted { return .exhausted }
        if inFlight != nil { return .loading }
        if lastFailureAt != nil { return .failed }
        return .idle
    }

    /// `barsChanged` = the store or the payload changed: re-copy the older bars and bump the
    /// revision (the chart rebuilds its series on it). A status-only publish (loading,
    /// failed, a dropped page) keeps both, so it never rebuilds the series.
    private func publish(status: ChartHistoryState.Status? = nil, barsChanged: Bool = false) {
        let older: [StockPricePoint]
        if barsChanged {
            older = payloadPresent && payloadStart > 0 ? Array(store[0..<payloadStart]) : []
        } else {
            older = state.olderBars
        }
        state = ChartHistoryState(
            olderBars: older,
            status: status ?? currentStatus(),
            reachedStart: payloadPresent && reachedStart,
            revision: barsChanged ? state.revision &+ 1 : state.revision
        )
    }
}
