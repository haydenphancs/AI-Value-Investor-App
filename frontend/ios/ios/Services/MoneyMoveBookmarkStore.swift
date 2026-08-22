//
//  MoneyMoveBookmarkStore.swift
//  ios
//
//  The ONE saved Money Move topic. Account-synced through
//  GET/PUT/DELETE /api/v1/learn/money-move-bookmark, with a local UserDefaults cache in front so
//  the row renders instantly and keeps working offline.
//
//  Keyed by article `slug` — the canonical id, the same key MoneyMovesProgressStore uses, so a
//  saved topic and a completed one can never disagree about which article they mean. A placeholder
//  card carries an empty slug and is deliberately not saveable (see MoneyMovesDetailView).
//
//  WHY THIS IS NOT A SECOND BookmarkStore
//  --------------------------------------
//  BookmarkStore is 447 lines because it reconciles an ordered LIST: per-title tombstones,
//  reconcile pins for re-pushed entries, in-flight counting, and a collapse pass retrofitting
//  "one at a time" onto list storage. None of that is needed here, because this resource is
//  single-valued on the wire and the server REPLACES: PUT wipes any other row for this user, so
//  displacing the previous topic needs no DELETE and no ordering ever has to be reconciled.
//
//  What survives from that store is the part that is genuinely load-bearing:
//
//   * `needsPush` / `pendingRemoval` — the tombstone system collapsed to what one value needs.
//     While either is set, `hydrate` RE-PUSHES local instead of adopting the server's answer.
//     Without it, an un-bookmark made offline is resurrected by the next GET (the server still
//     has the row) and the user's explicit tap is silently reversed.
//   * `localVersion` — a response that was issued before a local write describes a pre-write
//     server, so merging it puts back what the user just changed.
//   * `LearnIdentityEpoch` — `localVersion` cannot see the ACCOUNT changing underneath a request,
//     because each identity starts from its own local state. Without this, the previous account's
//     bookmark is adopted and then pushed into the new one.
//
//  ⚠️ The defaults key carries no user id, so this store MUST be reset from
//  `AppState.discardDataForEndedSession()` — auth.md §7, the same rule the four Learn stores,
//  `WhaleService.followedWhaleIds` and `SearchHistoryStore` are all bound by.
//

import Combine
import Foundation
import OSLog

@MainActor
final class MoneyMoveBookmarkStore: ObservableObject {
    static let shared = MoneyMoveBookmarkStore()

    /// The saved topic's slug — at most one, ever. `nil` means nothing is saved.
    @Published private(set) var bookmarkedSlug: String?

    /// `bookmarkedSlug` holds a local value the server has not confirmed.
    private var needsPush = false

    /// A slug the user cleared whose DELETE has not confirmed. Kept separately from
    /// `bookmarkedSlug` because a removal has to remember WHICH slug to delete after the
    /// visible value is already gone.
    private var pendingRemoval: String?

    /// Monotonic counter of local writes. Every request snapshots it before awaiting; a response
    /// carrying a stale snapshot predates that write and must not be adopted.
    private var localVersion: UInt64 = 0

    /// The hydrate currently in flight, if any. Concurrent callers JOIN it.
    private var hydrateTask: Task<Void, Never>?

    private static let slugKey = "moneyMoves.bookmarkedSlug"
    private static let pendingRemovalKey = "moneyMoves.pendingRemovedBookmark"
    private static let needsPushKey = "moneyMoves.bookmarkNeedsPush"
    /// `os.Logger`, not `print()` — CLAUDE.md bans `print()` in production code. The older stores
    /// in this folder predate that rule and should not be copied here.
    private static let log = Logger(subsystem: "com.phan.caydex", category: "money-move-bookmark")

    private let apiClient: APIClient
    private let defaults: UserDefaults

    init(apiClient: APIClient = .shared, defaults: UserDefaults = .standard) {
        self.apiClient = apiClient
        self.defaults = defaults
        bookmarkedSlug = defaults.string(forKey: Self.slugKey)
        pendingRemoval = defaults.string(forKey: Self.pendingRemovalKey)
        needsPush = defaults.bool(forKey: Self.needsPushKey)
    }

    // MARK: - Reads

    func isBookmarked(slug: String) -> Bool {
        !slug.isEmpty && bookmarkedSlug == slug
    }

    /// True while a local change is waiting on the network — `hydrate` must not overwrite it.
    private var hasUnsyncedWrite: Bool { needsPush || pendingRemoval != nil }

    // MARK: - Writes

    /// Save this topic, or clear it if it is already the saved one.
    ///
    /// **Exactly one topic is saved at a time.** Saving a new one displaces the old with no
    /// DELETE at all: the backend's PUT is replace-semantics, so the displaced row is gone by the
    /// time the response lands. That is the whole reason this store needs no per-item tombstones.
    func toggle(slug: String) {
        let s = slug.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return }

        if bookmarkedSlug == s {
            bookmarkedSlug = nil
            pendingRemoval = s      // remember what to DELETE; blocks hydrate from resurrecting it
            needsPush = false
        } else {
            bookmarkedSlug = s
            needsPush = true
            // A queued removal is superseded: the PUT below replaces every row for this user,
            // so the old slug is deleted server-side by the save itself.
            pendingRemoval = nil
        }
        bumpLocalVersion()          // invalidates any response already in flight
        persistLocal()
        Task { await self.pushUnsynced() }
    }

    /// Drop the saved topic this device holds locally. Called on SIGN-OUT
    /// (`AppState.discardDataForEndedSession`).
    ///
    /// The defaults key carries no user id, so leaving it in place would let the next account to
    /// sign in on this device see the previous user's saved topic AND — via `pushUnsynced` — write
    /// it into their own rows. Costs a signed-in user nothing: `hydrate()` refills from their own
    /// row the next time the Money Moves screen opens.
    func reset() {
        guard bookmarkedSlug != nil || pendingRemoval != nil || needsPush else { return }
        bookmarkedSlug = nil
        pendingRemoval = nil
        needsPush = false
        bumpLocalVersion()   // any hydrate/push already in flight must not re-fill what was cleared
        persistLocal()
    }

    private func bumpLocalVersion() { localVersion &+= 1 }

    // MARK: - Backend sync (best-effort; the local cache is what the UI reads)

    /// Pull the server's saved topic. Call when the Money Moves screen opens.
    ///
    /// Single-flight: concurrent callers join the running task rather than issuing a second GET.
    func hydrate() async {
        if let running = hydrateTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performHydrate()
            // Cleared HERE, as the task's own last act, rather than after `await task.value`
            // below. Between a task finishing and its awaiting caller being resumed, a THIRD
            // caller can observe a completed-but-still-registered task: it would "join" something
            // already done and return instantly without ever loading. That is a silently skipped
            // refresh. (Same reasoning as BookmarkStore.hydrate.)
            self.hydrateTask = nil
        }
        hydrateTask = task
        await task.value
    }

    private func performHydrate() async {
        // A local change is waiting on the network. Adopting the server's answer now would
        // reverse it — most damagingly for a removal, where the server still holds the row the
        // user cleared. Push local instead; the next hydrate reads back the settled value.
        if hasUnsyncedWrite {
            await pushUnsynced()
            return
        }

        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .getMoneyMoveBookmark,
                responseType: MoneyMoveBookmarkResponse.self
            )
            // The ACCOUNT changed while this was in flight (sign-out / switch). `localVersion`
            // cannot see that — each account starts from its own local state.
            guard epoch == LearnIdentityEpoch.current else {
                Self.log.info("discarded a hydrate from a previous identity")
                return
            }
            // A local write landed while this GET was in flight, so the response describes a
            // PRE-write server. Drop it; the next hydrate is authoritative.
            guard token == localVersion else {
                Self.log.info("discarded a stale hydrate — a local write raced the GET")
                return
            }
            adopt(resp.bookmark)
        } catch {
            // Offline or signed out: keep whatever is local. A decode/contract or 5xx failure
            // silently hides a synced bookmark, so surface it (quiet on routine offline).
            report(error, operation: "hydrate")
        }
    }

    /// Push whatever local change has not been confirmed. No-op when everything is in sync.
    private func pushUnsynced() async {
        if needsPush {
            guard let slug = bookmarkedSlug else {
                // Defensive: `toggle` cannot produce "needs push, nothing to push" — but a
                // partially-written defaults blob (killed mid-`persistLocal`) can restore one,
                // and left set it would block EVERY future hydrate from adopting the server's
                // value. Permanent silent staleness with no way back, so heal it here.
                needsPush = false
                persistLocal()
                return
            }
            await pushSet(slug)
        } else if let slug = pendingRemoval {
            await pushRemove(slug)
        }
    }

    private func pushSet(_ slug: String) async {
        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .setMoneyMoveBookmark(slug: slug),
                responseType: MoneyMoveBookmarkResponse.self
            )
            // TWO guards, answering different questions — see the type comment. Bail before
            // clearing `needsPush`, or a save that raced a sign-out is marked synced for the
            // wrong account and never retried for the right one.
            guard epoch == LearnIdentityEpoch.current, token == localVersion else { return }
            needsPush = false
            adopt(resp.bookmark)
            persistLocal()
        } catch {
            // Stays flagged; the next hydrate retries. The backend surfaces its own failures
            // rather than answering 200, so a 2xx really does mean the row landed.
            report(error, operation: "save \(slug)")
        }
    }

    private func pushRemove(_ slug: String) async {
        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .removeMoneyMoveBookmark(slug: slug),
                responseType: MoneyMoveBookmarkResponse.self
            )
            guard epoch == LearnIdentityEpoch.current else { return }
            // Retire the tombstone BEFORE adopting: while it is set, `adopt` refuses to take a
            // server value it would resurrect, and retiring it is itself a local write that
            // would invalidate this very token.
            if pendingRemoval == slug {
                pendingRemoval = nil
                bumpLocalVersion()   // any GET issued earlier predates this; it must not adopt
            }
            if token == localVersion { adopt(resp.bookmark) }
            persistLocal()
        } catch {
            // Stays tombstoned so a racing hydrate can't resurrect it; retried on next hydrate.
            report(error, operation: "remove \(slug)")
        }
    }

    /// Take the server's value as our own, unless it is the very slug the user just cleared.
    private func adopt(_ remote: String?) {
        if let remote, remote == pendingRemoval { return }
        guard bookmarkedSlug != remote else { return }
        bookmarkedSlug = remote
        persistLocal()
    }

    private func report(_ error: Error, operation: String) {
        let appError = AppError.from(error)
        guard !appError.isExpectedOffline else { return }
        Self.log.error(
            "\(operation, privacy: .public) failed [\(appError.title, privacy: .public)]: \(appError.message, privacy: .public)"
        )
    }

    private func persistLocal() {
        defaults.set(bookmarkedSlug, forKey: Self.slugKey)
        defaults.set(pendingRemoval, forKey: Self.pendingRemovalKey)
        defaults.set(needsPush, forKey: Self.needsPushKey)
    }
}

// MARK: - DTO

/// Backend response: `{ "bookmark": "<slug>" | null }`. Single-valued, not a list — the Money
/// Moves screen shows exactly one saved topic.
struct MoneyMoveBookmarkResponse: Decodable {
    let bookmark: String?
}
