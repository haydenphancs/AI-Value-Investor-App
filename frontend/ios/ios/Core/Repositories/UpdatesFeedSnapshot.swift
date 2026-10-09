//
//  UpdatesFeedSnapshot.swift
//  ios
//
//  The Updates tab's on-device snapshot: the last LIVE Market feed, first page, so a cold
//  launch paints "News · Updated <time>" at once and the live load replaces it in place. The
//  store, the owner fence and the disk rules are `AccountSnapshotStore`'s; this file holds only
//  what is Updates': the part, its decode and the keep rule.
//
//  ONE part, `feed`: the exact `GET /updates/feed?scope=__MARKET__&offset=0` response bytes.
//  The /updates/tabs body and the ticker chips are NOT kept (cut by review): a chip's stale
//  `is_locked` could open a ticker feed the plan now locks — `/updates/feed` has no server
//  plan gate — and a seeded chip strip disables `loadTabs`' Market fallback. A ticker-scope
//  feed is never kept for the same reason.
//
//  Lives here, not in `Models/UpdatesModels.swift`: it is a store adapter, not a UI model.
//

import Foundation

typealias UpdatesFeedSnapshotStore = AccountSnapshotStore<UpdatesFeedSnapshot>

extension AccountSnapshotConfig {
    /// `Library/Caches/UpdatesFeedSnapshot/updates-feed-snapshot.v1.plist`.
    nonisolated static let updatesFeed = AccountSnapshotConfig(
        name: "updates-feed-snapshot",
        directoryName: "UpdatesFeedSnapshot",
        fileName: "updates-feed-snapshot.v1.plist",
        schemaVersion: 1,
        // A Market page is tens of KB (50 stories); anything near this cap is not one.
        partCaps: [UpdatesFeedSnapshot.feedPart: 1024 * 1024],
        requiredParts: [UpdatesFeedSnapshot.feedPart]
    )
}

/// The one shared instance. Memory-only in DEBUG screenshot mode (the store's init).
@MainActor private let updatesFeedSnapshotStoreInstance = UpdatesFeedSnapshotStore(
    config: .updatesFeed, directory: AccountSnapshotConfig.updatesFeed.defaultDirectory
)

extension AccountSnapshotStore where Payload == UpdatesFeedSnapshot {
    static var shared: UpdatesFeedSnapshotStore { updatesFeedSnapshotStoreInstance }
}

/// The last live Market feed (first page), decoded through the live DTO.
struct UpdatesFeedSnapshot: AccountSnapshotPayload {
    nonisolated static let feedPart = "feed"

    let feed: UpdatesFeedResponse

    init(feed: UpdatesFeedResponse) {
        self.feed = feed
    }

    static func decode(parts: [String: Data], apiClient: APIClient) async throws -> UpdatesFeedSnapshot {
        guard let body = parts[feedPart] else {
            throw AccountSnapshotDecodeError.missingPart(feedPart)
        }
        let feed = try await apiClient.decodeBody(UpdatesFeedResponse.self, from: body)
        return UpdatesFeedSnapshot(feed: feed)
    }

    /// Kept only for the Market scope's first page with at least one story. Anything else
    /// leaves the previous snapshot alone. Also asked of a file read back (the store deletes a
    /// file this rule would not keep now).
    func persistDecision(previous: UpdatesFeedSnapshot?) -> AccountSnapshotPersistDecision {
        guard feed.scope == UpdatesScope.market else {
            return .keepPrevious("not the Market feed")
        }
        guard (feed.offset ?? 0) == 0 else {
            return .keepPrevious("not the Market feed's first page")
        }
        guard !(feed.articles ?? []).isEmpty else {
            return .keepPrevious("the Market feed came back with no stories")
        }
        return .save
    }
}
