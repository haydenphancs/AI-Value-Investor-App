//
//  EarningsLiveViewModel.swift
//  ios
//
//  ViewModel for one report's live results (`EarningsLiveResultsCard`): polls
//  `GET /api/v1/tracking/earnings/{ticker}/live` for as long as the server asks. `poll_seconds`
//  is 30 s while the report is LIVE, 120 s while FMP can still confirm a reported report's
//  preliminary figures, and null otherwise, so the client never polls a report that has
//  finished, or one that has not started.
//
//  With no answer yet there is no `poll_seconds` to read, so a failed first load asks again by
//  itself every 30 s, on either route: a dropped connection at 16:00 ET must not freeze the
//  sheet while the Tracking card behind it updates. That retry is bounded (`maxFailedLoads`),
//  and a request the server refuses as invalid ends the poll at once, shown answer or not: a
//  `report_date` can age out under an open sheet, and the 400 it then gets is permanent.
//  Either way nothing is asking any more, so the card stops saying LIVE (`pollEnded`).
//  `start()` clears all of it, so Try Again and a return to the foreground always ask again.
//
//  Lifecycle mirrors the detail screens' polls (`TickerDetailView`): `start()` on appear and
//  on `didBecomeActive`, `stop()` on disappear and `willResignActive`. `start()` cancels any
//  previous loop first, so re-arming can never leave two pollers running.
//

import Combine
import Foundation
import SwiftUI

@MainActor
final class EarningsLiveViewModel: ObservableObject {
    @Published private(set) var live: EarningsLiveResponse?
    /// A load failed with nothing to show, or the server refused the request: the card says so
    /// and offers Try Again. A failed poll over a shown answer keeps the answer (it is still
    /// the latest truth we have) and tries again next tick.
    @Published private(set) var loadFailed = false
    /// This loop is over with the report's state unknown: the retry ran out, or the server
    /// refused the request. Always with `loadFailed`, so the notice's Try Again is on screen.
    /// Not when the server stops the poll itself: that answer says how the report stands.
    @Published private(set) var pollEnded = false

    let ticker: String
    private let reportDate: String?
    private let apiClient: APIClient
    private let isPreview: Bool
    private var pollTask: Task<Void, Never>?
    /// Failed loads in a row with no answer to show.
    private var failedLoads = 0
    /// The server refused this request as invalid, so asking again gets the same answer.
    private var refused = false

    /// Floor on the server's cadence, so a malformed `poll_seconds` cannot hammer the route.
    private static let minimumPollSeconds = 15
    /// How often a load with no answer asks again: the LIVE cadence.
    private static let noAnswerRetrySeconds = 30
    /// Ten minutes of `noAnswerRetrySeconds`. Past that, the notice's Try Again is the way back.
    private static let maxFailedLoads = 20

    init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {
        self.ticker = ticker.uppercased()
        self.reportDate = reportDate
        self.apiClient = apiClient
        self.isPreview = false
    }

    #if DEBUG
    /// Previews only: `answer` is drawn as it stands and `start()` asks nothing. DEBUG-only, so
    /// no production path can paint sample figures.
    init(previewing answer: EarningsLiveResponse) {
        self.ticker = answer.ticker
        self.reportDate = answer.reportDate
        self.apiClient = .shared
        self.isPreview = true
        self.live = answer
    }
    #endif

    func start() {
        guard !isPreview else { return }
        pollTask?.cancel()
        loadFailed = false
        pollEnded = false
        failedLoads = 0
        refused = false
        pollTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.load()
                guard !Task.isCancelled else { break }
                guard let seconds = self.nextPollSeconds else {
                    self.pollEnded = self.refused || self.live == nil
                    break
                }
                let wait = UInt64(max(seconds, Self.minimumPollSeconds)) * 1_000_000_000
                try? await Task.sleep(nanoseconds: wait)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// Seconds to the next ask, or nil when this loop is over.
    ///
    /// A real answer rules: `poll_seconds` null means the server stopped asking (finished,
    /// upcoming, awaiting), and one answer is the whole story. The retry takes over only with
    /// no answer at all. Never a status borrowed from the Tracking card: that would keep
    /// polling a report that finished under a sheet opened while it was LIVE.
    private var nextPollSeconds: Int? {
        if refused { return nil }
        if let live {
            guard let seconds = live.pollSeconds, seconds > 0 else { return nil }
            return seconds
        }
        guard loadFailed, failedLoads < Self.maxFailedLoads else { return nil }
        return Self.noAnswerRetrySeconds
    }

    private func load() async {
        do {
            let response: EarningsLiveResponse = try await apiClient.request(
                endpoint: .getEarningsLive(ticker: ticker, reportDate: reportDate),
                responseType: EarningsLiveResponse.self
            )
            live = response
            loadFailed = false
            failedLoads = 0
        } catch {
            // `APIClient` wraps a cancellation into `APIError.networkError`, so `stop()` and a
            // superseding `start()` land here. Neither is a failure.
            guard !Task.isCancelled else { return }
            let appError = AppError.from(error)
            print("[EarningsLiveVM] ❌ live results for \(ticker) failed: \(appError.title)")
            if case .validationFailed = appError { refused = true }
            if live == nil { failedLoads += 1 }
            loadFailed = live == nil || refused
        }
    }
}
