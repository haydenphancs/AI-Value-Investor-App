//
//  TrackingSnapshot.swift
//  ios
//
//  The Tracking tab's on-device snapshot: the last LIVE Holdings answer, so a cold launch
//  paints the active group's rows labelled "Updated <time>" and the live load replaces them in
//  place. The store, the owner fence and the disk rules are `AccountSnapshotStore`'s; this
//  file holds only what is Tracking's: the parts, their decode and the keep rule.
//
//  Parts — ALL captured from the SAME live load (Holdings = feed ∩ the active group's tickers,
//  so a mixed pair would filter one load's rows by another load's group):
//    • `assets`     (required) the exact `GET /tracking/assets` response bytes;
//    • `portfolios` (required) the exact `GET /portfolios` response bytes;
//    • `active`     (required) the active portfolio id, UTF-8 (`activePartBody(_:)`);
//    • `insights`   (optional) the exact `GET /portfolios/{active}/insights` bytes. Its JSON
//      `null` (fewer than the minimum holdings) is a KNOWN answer, kept apart from a failure.
//
//  ⚠️ DISPLAY-ONLY. The decoded portfolios must never be written into `PortfolioStore`:
//  `purgeTickers` / `setTickers` are whole-list PUTs, and a snapshot's membership written back
//  would delete what the user added since (data loss).
//

import Foundation
import OSLog

nonisolated private let trackingSnapshotLog = Logger(subsystem: "com.phan.caydex", category: "tracking-snapshot")

typealias TrackingSnapshotStore = AccountSnapshotStore<TrackingSnapshot>

extension AccountSnapshotConfig {
    /// `Library/Caches/TrackingSnapshot/tracking-snapshot.v1.plist`.
    nonisolated static let tracking = AccountSnapshotConfig(
        name: "tracking-snapshot",
        directoryName: "TrackingSnapshot",
        fileName: "tracking-snapshot.v1.plist",
        schemaVersion: 1,
        // `assets` holds up to WATCHLIST_MAX_ITEMS (500) rows with sparklines; the guard test
        // checks this cap against that limit.
        partCaps: [
            TrackingSnapshot.assetsPart: 4 * 1024 * 1024,
            TrackingSnapshot.portfoliosPart: 1024 * 1024,
            TrackingSnapshot.activePart: 256,
            TrackingSnapshot.insightsPart: 64 * 1024,
        ],
        requiredParts: [TrackingSnapshot.assetsPart, TrackingSnapshot.portfoliosPart, TrackingSnapshot.activePart]
    )
}

/// The one shared instance. Memory-only in DEBUG screenshot mode (the store's init).
@MainActor private let trackingSnapshotStoreInstance = TrackingSnapshotStore(
    config: .tracking, directory: AccountSnapshotConfig.tracking.defaultDirectory
)

extension AccountSnapshotStore where Payload == TrackingSnapshot {
    static var shared: TrackingSnapshotStore { trackingSnapshotStoreInstance }
}

/// The last live Holdings answer, mapped exactly as a live load maps it.
struct TrackingSnapshot: AccountSnapshotPayload {
    nonisolated static let assetsPart = "assets"
    nonisolated static let portfoliosPart = "portfolios"
    nonisolated static let activePart = "active"
    nonisolated static let insightsPart = "insights"

    /// The Portfolio Insights answer captured with the rows.
    enum Insights {
        /// No insights part was kept, or its bytes no longer decode: say nothing.
        case unknown
        /// The server answered for the active group: a score, or nil (too few holdings).
        case known(DiversificationScore?)
    }

    /// Every row of the feed (`toTrackedAsset()`), like `TrackingViewModel.trackedAssets`.
    let assets: [TrackedAsset]
    /// Every group, sorted by `sortOrder` like `PortfolioStore.portfolios`. Display-only.
    let portfolios: [Portfolio]
    /// The group that was active when the bodies were captured.
    let activePortfolioId: String?
    let insights: Insights

    init(assets: [TrackedAsset], portfolios: [Portfolio], activePortfolioId: String?, insights: Insights) {
        self.assets = assets
        self.portfolios = portfolios
        self.activePortfolioId = activePortfolioId
        self.insights = insights
    }

    var activePortfolio: Portfolio? {
        portfolios.first { $0.id == activePortfolioId }
    }

    /// What Holdings draws: the feed rows whose ticker is in the active group.
    var holdingsRows: [TrackedAsset] {
        Self.holdingsRows(assets: assets, portfolio: activePortfolio)
    }

    var insightsKnown: Bool {
        if case .known = insights { return true }
        return false
    }

    /// The kept score, or nil — for both "too few holdings" and `unknown` (check `insightsKnown`).
    var insightsScore: DiversificationScore? {
        if case .known(let score) = insights { return score }
        return nil
    }

    /// The same membership rule as `TrackingViewModel.filteredAssets`: uppercased tickers.
    static func holdingsRows(assets: [TrackedAsset], portfolio: Portfolio?) -> [TrackedAsset] {
        guard let portfolio else { return [] }
        let members = Set(portfolio.tickers.map { $0.uppercased() })
        return assets.filter { members.contains($0.ticker.uppercased()) }
    }

    /// The `active` part's bytes for a live `PortfolioStore.activePortfolioId`. nil → empty
    /// bytes, which no save accepts (and a nil id draws no rows, so the decision drops the file).
    nonisolated static func activePartBody(_ portfolioId: String?) -> Data {
        Data((portfolioId ?? "").utf8)
    }

    static func decode(parts: [String: Data], apiClient: APIClient) async throws -> TrackingSnapshot {
        guard let assetsBody = parts[assetsPart] else {
            throw AccountSnapshotDecodeError.missingPart(assetsPart)
        }
        guard let portfoliosBody = parts[portfoliosPart] else {
            throw AccountSnapshotDecodeError.missingPart(portfoliosPart)
        }
        guard let activeBody = parts[activePart] else {
            throw AccountSnapshotDecodeError.missingPart(activePart)
        }
        guard let activeText = String(data: activeBody, encoding: .utf8) else {
            throw AccountSnapshotDecodeError.unreadablePart(activePart)
        }
        let trimmed = activeText.trimmingCharacters(in: .whitespacesAndNewlines)
        let activeId: String? = trimmed.isEmpty ? nil : trimmed

        let feed = try await apiClient.decodeBody(TrackingFeedResponse.self, from: assetsBody)
        let list = try await apiClient.decodeBody(PortfolioListResponseDTO.self, from: portfoliosBody)
        let insights = await decodeInsights(parts[insightsPart], apiClient: apiClient)

        let rows = feed.assets.map { $0.toTrackedAsset() }
        let groups = list.portfolios
            .map { $0.toPortfolio() }
            .sorted { $0.sortOrder < $1.sortOrder }
        return TrackingSnapshot(assets: rows, portfolios: groups, activePortfolioId: activeId, insights: insights)
    }

    /// do/catch, never `try?`: `try?` on a `PortfolioInsightsDTO?` decode flattens a failure
    /// into the same nil as the server's KNOWN "too few holdings" answer.
    private static func decodeInsights(_ body: Data?, apiClient: APIClient) async -> Insights {
        guard let body else { return .unknown }
        do {
            let dto = try await apiClient.decodeBody(PortfolioInsightsDTO?.self, from: body)
            return .known(dto?.toDiversificationScore())
        } catch {
            // The rows are still good; only the score is dropped.
            let detail = "\(type(of: error)): \(error)"
            trackingSnapshotLog.warning("tracking-snapshot: the kept insights no longer decode, shown as unknown — \(detail, privacy: .public)")
            return .unknown
        }
    }

    /// A live answer with no row in the active group says the account has nothing to show:
    /// the saved file goes too. Rows with no known price (a failed price enrich) are not worth
    /// keeping over what is saved. Otherwise keep it.
    func persistDecision(previous: TrackingSnapshot?) -> AccountSnapshotPersistDecision {
        let rows = holdingsRows
        guard !rows.isEmpty else {
            return .deleteSaved("no holdings in the active group")
        }
        guard rows.contains(where: { $0.priceKnown }) else {
            return .keepPrevious("no holding has a known price")
        }
        return .save
    }
}
