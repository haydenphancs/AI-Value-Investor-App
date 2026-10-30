//
//  EarningsLiveViewModel.swift
//  ios
//
//  ViewModel for one report's live results (`EarningsLiveResultsCard`): polls
//  `GET /api/v1/tracking/earnings/{ticker}/live` while the report is LIVE and stops once it is
//  not. The server says how often (`poll_seconds`, 30 s while live, null otherwise), so the
//  client never polls a report that has finished, or one that has not started.
//
//  Lifecycle mirrors the detail screens' polls (`TickerDetailView`): `start()` on appear and
//  on `didBecomeActive`, `stop()` on disappear and `willResignActive`. `start()` cancels any
//  previous loop first, so re-arming can never leave two pollers running.
//

import Foundation
import SwiftUI

@MainActor
final class EarningsLiveViewModel: ObservableObject {
    @Published private(set) var live: EarningsLiveResponse?
    /// The FIRST load failed and there is nothing to show; a failed poll over a shown answer
    /// keeps the answer (it is still the latest truth we have) and tries again next tick.
    @Published private(set) var loadFailed = false
    @Published private(set) var isLoading = false

    let ticker: String
    private let reportDate: String?
    private let apiClient: APIClient
    private var pollTask: Task<Void, Never>?

    /// Floor on the server's cadence, so a malformed `poll_seconds` cannot hammer the route.
    private static let minimumPollSeconds = 15

    init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {
        self.ticker = ticker.uppercased()
        self.reportDate = reportDate
        self.apiClient = apiClient
    }

    func start() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.load()
                guard !Task.isCancelled else { break }
                // Not live (reported, upcoming, awaiting) → one answer is the whole story.
                guard let seconds = self.live?.pollSeconds, seconds > 0 else { break }
                let wait = UInt64(max(seconds, Self.minimumPollSeconds)) * 1_000_000_000
                try? await Task.sleep(nanoseconds: wait)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    func load() async {
        if live == nil { isLoading = true }
        defer { isLoading = false }
        do {
            let response: EarningsLiveResponse = try await apiClient.request(
                endpoint: .getEarningsLive(ticker: ticker, reportDate: reportDate),
                responseType: EarningsLiveResponse.self
            )
            live = response
            loadFailed = false
        } catch {
            let appError = AppError.from(error)
            if appError.isCancellation { return }
            print("[EarningsLiveVM] ❌ live results for \(ticker) failed: \(appError.title)")
            loadFailed = (live == nil)
        }
    }
}
