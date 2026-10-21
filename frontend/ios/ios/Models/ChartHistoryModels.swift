//
//  ChartHistoryModels.swift
//  ios
//
//  Older chart history, paged in as the user pans left:
//  `GET /{stocks|etfs|indices|commodities|crypto}/{symbol}/chart/history`.
//

import Foundation

/// The detail router a history page is served from — its raw value IS the path prefix.
/// `nonisolated` so `APIEndpoint` (Sendable, nonisolated `path`) can switch over it.
nonisolated enum ChartHistoryAsset: String, Sendable {
    case stocks
    case etfs
    case indices
    case commodities
    case crypto
}

/// One page of bars strictly OLDER than the request's `before` cursor, oldest first.
///
/// `hasMore == false` when paging has nothing older to give — a failed or empty upstream is
/// an error from the backend, never "start of history".
struct ChartHistoryResponse: Codable {
    enum CodingKeys: String, CodingKey {
        case symbol, interval, bars
        case hasMore = "has_more"
        case settledThrough = "settled_through"
        case reachedStart = "reached_start"
    }

    let symbol: String
    let interval: String
    let bars: [StockPricePoint]
    let hasMore: Bool
    /// The close the backend's daily history was settled through. Two pages with different
    /// values may straddle a split (FMP history is split-adjusted when fetched).
    let settledThrough: String?
    /// The page holds the FIRST bar the source has for the symbol. `hasMore == false` alone
    /// also covers paging that stopped short of it (the intraday look-back bound, a
    /// provider's plan cap), where the leftmost bars are indicator warm-up. Missing → false.
    let reachedStart: Bool?
}

/// What `TickerChartView` is given about older history: plain values, so the chart (a
/// molecule) never holds the loader that fetches.
struct ChartHistoryState {
    enum Status: Equatable {
        case idle
        case loading
        case failed
        /// Nothing older will be asked for: the source's first bar was reached, paging
        /// stopped at a provider bound, or this range is ALL. Whether the leftmost bar is
        /// the true start is `reachedStart`, not this.
        case exhausted
    }

    /// Contiguous bars strictly before the screen's current range payload.
    var olderBars: [StockPricePoint]
    var status: Status
    /// The bars in hand begin at the symbol's first bar (the backend said so). Only then may
    /// the chart show the leftmost ~210 bars, whose indicators have no full lookback.
    var reachedStart: Bool
    /// Bumped whenever the BARS change (each payload the VM absorbs, each page merged) — the
    /// chart's series memo keys on it. A status-only change (loading, failed) does not bump
    /// it, so it never rebuilds the series.
    var revision: Int

    var isExhausted: Bool { status == .exhausted }

    static let empty = ChartHistoryState(olderBars: [], status: .idle, reachedStart: false, revision: 0)
}
