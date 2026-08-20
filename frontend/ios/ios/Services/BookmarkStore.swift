//
//  BookmarkStore.swift
//  ios
//
//  Single source of truth for book bookmarks. Mirrors BookProgressStore: a local UserDefaults
//  cache (instant + offline) that writes through to the backend (GET/POST/DELETE
//  /api/v1/learn/bookmarks). On launch it union-merges the server's set in, so bookmarks survive
//  reinstall and sync across devices without ever losing a local write.
//
//  Bookmarks are keyed by book TITLE — the only stable id shared by LibraryBook, EducationBook,
//  and SearchBookItem (their `id` is a regenerated UUID; only LibraryBook has curriculumOrder).
//  So bookmarking a book on any surface (Library card, Learn "AI-Enabled Books" card, Search,
//  detail header) reflects everywhere that observes this store.
//
//  EXACTLY ONE book is bookmarked at a time: bookmarking a book displaces whatever was bookmarked
//  before. The bookmark is the "the book I'm reading" marker the Book Library hero card resumes
//  from, and that card can only point at one book — a second bookmark made it ambiguous. The
//  storage stays a most-recent-first LIST rather than a single string so the existing sync
//  machinery (tombstones, reconcile pins, server ordering) still applies unchanged, and so extras
//  arriving from an older build or another device can be collapsed rather than silently ignored.
//

import Foundation
import Combine

@MainActor
final class BookmarkStore: ObservableObject {
    static let shared = BookmarkStore()

    /// Bookmarked book titles, most-recent-first (head = latest bookmarked).
    @Published private(set) var bookmarkedTitles: [String] = []

    /// Titles the user un-bookmarked whose backend DELETE hasn't confirmed yet. hydrate()'s
    /// union-merge would otherwise resurrect a just-removed bookmark the moment the GET races ahead
    /// of (or the device is offline for) the DELETE — the book silently reappears bookmarked against
    /// the user's tap. Persisted so an offline removal survives relaunch; cleared once the DELETE
    /// confirms (or the user re-bookmarks). Unlike BookProgressStore, bookmarks are removable, so a
    /// plain union is unsafe here.
    private var pendingRemovals: Set<String> = []

    /// Titles whose server timestamp is a RECONCILE artifact rather than a user tap: `pushUnsynced`
    /// re-POSTs a bookmark whose original add never landed, and the backend stamps a fresh
    /// `completed_at`, so the server's most-recent-first list floats that title to the head — and
    /// the Book Library hero card would then resume the WRONG book. For these titles the position
    /// the user's own taps produced stays authoritative in `merge`. Cleared the moment the user
    /// toggles the title again, because then the server's timestamp really is the user's intent.
    private var reconciledTitles: Set<String> = []

    /// Monotonic counter of LOCAL writes (a toggle, or a tombstone retiring once its DELETE
    /// confirms). Every request snapshots it before awaiting; a response carrying a stale snapshot
    /// describes the server as it was BEFORE that write and must not be merged. Without this, a
    /// hydrate GET issued before an un-bookmark can land after the DELETE confirmed and cleared the
    /// tombstone — resurrecting the title with no tombstone left to filter it — and `pushUnsynced`
    /// would then re-POST it, making the resurrection durable server-side.
    private var localVersion: UInt64 = 0

    /// Titles with a POST/DELETE in flight, counted (an add and a remove for the same title can
    /// overlap). Read only by tombstone pruning: a GET that predates an in-flight write proves
    /// nothing about that title.
    private var inFlightPushes: [String: Int] = [:]

    private static let defaultsKey = "bookLibrary.bookmarkedTitles"
    private static let pendingRemovalsKey = "bookLibrary.pendingRemovedBookmarks"
    private static let reconciledKey = "bookLibrary.reconciledBookmarks"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        bookmarkedTitles = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
        pendingRemovals = Set(UserDefaults.standard.stringArray(forKey: Self.pendingRemovalsKey) ?? [])
        reconciledTitles = Set(UserDefaults.standard.stringArray(forKey: Self.reconciledKey) ?? [])
        // An install made before the one-at-a-time rule still has several saved. Collapse on the
        // spot (purely local — the tombstones this leaves behind are what `hydrate` turns into the
        // server-side DELETEs), so the UI never renders two filled bookmarks even for one frame.
        collapseToSingleBookmark()
    }

    // MARK: - Reads

    func isBookmarked(_ title: String) -> Bool {
        bookmarkedTitles.contains(title)
    }

    /// The bookmarked book — there is at most one (the Book Library hero card resumes this), or nil.
    var mostRecent: String? { bookmarkedTitles.first }

    // MARK: - Writes

    /// Add or remove a bookmark. Updates locally first (instant), then pushes to the backend.
    ///
    /// **Exactly one book can be bookmarked at a time** — bookmarking a book un-bookmarks whatever
    /// else was bookmarked, on every surface at once (Library card, Learn card, Search, the detail
    /// hero and its sticky header), because they all route through here. The bookmark is the "one
    /// book I'm reading" marker the Book Library hero card resumes from, so a second one made that
    /// card ambiguous — it could only ever point at one of them.
    func toggle(_ title: String) {
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        if bookmarkedTitles.contains(t) {
            bookmarkedTitles.removeAll { $0 == t }
            pendingRemovals.insert(t)           // tombstone until the DELETE confirms
            reconciledTitles.remove(t)          // no longer in the list, nothing left to pin
            bumpLocalVersion()                  // invalidates any response already in flight
            persistLocal()
            // Counted in flight from ENQUEUE, not from when the Task starts: in that gap a hydrate
            // would otherwise see the title absent server-side and retire the tombstone early.
            beginPush(t)
            Task { await self.pushRemove(t); self.endPush(t) }
        } else {
            // Everything currently bookmarked is displaced by this tap. They get the SAME tombstone
            // treatment as an explicit un-bookmark, so a hydrate racing these DELETEs can't
            // resurrect them and `pushUnsynced` retries any DELETE that fails offline.
            let displaced = bookmarkedTitles.filter { $0 != t }
            bookmarkedTitles = [t]
            pendingRemovals.formUnion(displaced)
            pendingRemovals.remove(t)           // re-bookmarking supersedes any pending removal
            reconciledTitles.subtract(displaced)
            reconciledTitles.remove(t)          // an explicit tap gives the server a real timestamp
            bumpLocalVersion()
            persistLocal()
            beginPush(t)
            displaced.forEach { beginPush($0) }
            Task {
                // Add first: it is the user's intent, and it must land even if a DELETE fails. The
                // response can't resurrect the displaced titles — `merge` filters tombstones.
                await self.pushAdd(t)
                self.endPush(t)
                for old in displaced {
                    await self.pushRemove(old)
                    self.endPush(old)
                }
            }
        }
    }

    /// Collapse a multi-bookmark list down to the single most-recent one, tombstoning the rest.
    ///
    /// Needed because "one at a time" is a NEW rule: installs that predate it (and any device still
    /// running the old build, or a stale server row) carry several bookmarks, and without this they
    /// would keep rendering several filled bookmark icons forever. Tombstoning rather than plain
    /// dropping is what makes the cleanup durable — `pushUnsynced` sees each tombstone still present
    /// server-side on the next hydrate and retries its DELETE, so the extras don't come back.
    ///
    /// - Returns: true when it changed something (so callers can skip a redundant persist).
    @discardableResult
    private func collapseToSingleBookmark() -> Bool {
        guard bookmarkedTitles.count > 1 else { return false }
        let keep = bookmarkedTitles[0]          // most-recent-first, so the head is the live one
        let displaced = bookmarkedTitles.dropFirst()
        bookmarkedTitles = [keep]
        pendingRemovals.formUnion(displaced)
        reconciledTitles.formIntersection(bookmarkedTitles)
        bumpLocalVersion()
        persistLocal()
        print("[BookmarkStore] collapsed \(displaced.count) extra bookmark(s) — one book at a time")
        return true
    }

    /// Drop every bookmark this device holds locally.
    ///
    /// Called on SIGN-OUT (see `AppState.signOut`). These bookmarks are device-global — the
    /// defaults keys carry no user id — so leaving them in place let the next account to sign in
    /// on this device union them into its own view AND, via `pushUnsynced`, POST them into that
    /// account server-side. Clearing costs a signed-in user nothing: `hydrate()` refills from
    /// their own rows on the next Learn open.
    ///
    /// Tombstones and reconcile pins are cleared too — they describe the PREVIOUS account's
    /// server state, so applying them to the next one would suppress or reorder its real
    /// bookmarks.
    func reset() {
        guard !bookmarkedTitles.isEmpty || !pendingRemovals.isEmpty || !reconciledTitles.isEmpty
        else { return }
        bookmarkedTitles.removeAll()
        pendingRemovals.removeAll()
        reconciledTitles.removeAll()
        bumpLocalVersion()   // any hydrate/push already in flight must not re-fill what was cleared
        persistLocal()
    }

    /// Called for every local write, so an in-flight request can tell its snapshot went stale.
    private func bumpLocalVersion() { localVersion &+= 1 }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's bookmarks and merge them in. Call when the Library / Learn screen opens.
    /// The hydrate currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// Two independent triggers hydrate this store on a signed-in launch —
    /// `AppState.hydrateLearnStores()` from the auth fan-out and the Wiser screen's own
    /// `.task` — and both are legitimate: the Wiser tab must work for a guest, who never
    /// reaches the fan-out. The `localVersion` / `LearnIdentityEpoch` checks below guard the
    /// MERGE, not the REQUEST, so without this both went out and this store's endpoint was
    /// fetched twice per launch.
    private var hydrateTask: Task<Void, Never>?

    func hydrate() async {
        if let running = hydrateTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performHydrate()
            // Cleared HERE, as the task's own last act, rather than after `await
            // task.value` below. Between a task finishing and its awaiting caller
            // being resumed, a THIRD caller can run and observe a completed-but-still
            // -registered task: it would "join" something already done and return
            // instantly without ever loading. That is a silently skipped refresh —
            // and for `handleIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            self.hydrateTask = nil
        }
        hydrateTask = task
        await task.value
    }

    private func performHydrate() async {
        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .getBookBookmarks,
                responseType: BookmarkListResponse.self
            )
            // The ACCOUNT changed while this was in flight (sign-out / switch). `localVersion`
            // cannot see that — each account starts from its own local state — so check the
            // identity epoch too, or the previous user's saved books get merged in and then
            // pushed into the new account.
            guard epoch == LearnIdentityEpoch.current else {
                print("[BookmarkStore] discarded a hydrate from a previous identity")
                return
            }
            // A local write (un-bookmark, or a DELETE confirming and retiring its tombstone) landed
            // while this GET was in flight, so the response describes a PRE-write server state.
            // Merging it would put back what the user just removed — with the tombstone already
            // gone there is nothing left to filter it — and `pushUnsynced` would then re-POST it,
            // making the resurrection durable. Drop it; the next hydrate is authoritative.
            guard token == localVersion else {
                print("[BookmarkStore] discarded a stale hydrate — a local write raced the GET")
                return
            }
            let remote = Set(resp.bookmarks)
            merge(resp.bookmarks)
            pruneConfirmedTombstones(remote: remote)
            // `merge` above already collapsed any extras the server handed back (rows written
            // before the one-at-a-time rule, or by a device still on the old build) into tombstones.
            // They survive pruneConfirmedTombstones — it only retires tombstones the server does NOT
            // have — so pushUnsynced picks them up as `staleTombstones` and DELETEs them right here,
            // rather than leaving the cleanup to some later hydrate.
            await pushUnsynced(remote: remote)
        } catch {
            // Offline or signed out: keep whatever is local — but a decode/contract or 5xx failure
            // silently hides synced bookmarks, so surface it (stays quiet on routine offline).
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookmarkStore] hydrate failed [\(appError.title)]: \(appError.message) — raw: \(error)")
            }
        }
    }

    /// Re-push adds the server doesn't have, and retry removals whose DELETE never confirmed.
    ///
    /// Without this, a bookmark whose POST failed was LOST FOREVER: `toggle` on an
    /// already-bookmarked title takes the REMOVE branch, so the failed add could never be
    /// re-pushed, and `hydrate` only ever merged remote→local. Bookmark offline, reinstall, gone.
    private func pushUnsynced(remote: Set<String>) async {
        // Never re-add a tombstoned title — that would undo the user's explicit un-bookmark.
        let unsyncedAdds = bookmarkedTitles.filter {
            !remote.contains($0) && !pendingRemovals.contains($0)
        }
        // A tombstone the server STILL has means the DELETE never landed; retry it.
        let staleTombstones = pendingRemovals.intersection(remote)

        // Identity guard on the WRITE side — see BookProgressStore.pushUnsynced. Both loops
        // below are awaited network batches, and an account switch partway through wrote the
        // previous user's bookmarks into the new account.
        let epoch = LearnIdentityEpoch.current

        if !unsyncedAdds.isEmpty {
            print("[BookmarkStore] re-pushing \(unsyncedAdds.count) unsynced bookmark(s)")
            // Oldest first so the server's most-recent-first ordering ends up matching local order.
            // Bounded, and the bound can't strand anything: each pushed title leaves `unsyncedAdds`
            // once the server has it, so successive hydrates drain the backlog deterministically.
            for key in unsyncedAdds.reversed().prefix(Self.maxReconcilePushes) {
                guard epoch == LearnIdentityEpoch.current else {
                    print("[BookmarkStore] abandoned a reconcile push from a previous identity")
                    persistLocal()
                    return
                }
                // Pin only on CONFIRMED success. It used to be inserted unconditionally, before
                // the await — so an offline reconcile marked the title "reconciled" permanently
                // even though the server never received it. `merge` then suppressed its real
                // ordering forever, and because the pin is keyed by title with no expiry, the
                // Book Library hero card kept showing the wrong book until the app was deleted.
                if await pushAdd(key) {
                    reconciledTitles.insert(key)   // its server timestamp is now an artifact — see merge
                }
            }
            persistLocal()
        }
        if !staleTombstones.isEmpty {
            print("[BookmarkStore] retrying \(staleTombstones.count) unconfirmed removal(s)")
            for key in staleTombstones.sorted().prefix(Self.maxReconcilePushes) {
                guard epoch == LearnIdentityEpoch.current else {
                    print("[BookmarkStore] abandoned a tombstone retry from a previous identity")
                    return
                }
                await pushRemove(key)
            }
        }
    }

    private static let maxReconcilePushes = 25

    /// Retire tombstones the server demonstrably no longer has. `pushUnsynced` only ever retries a
    /// tombstone the server STILL holds, so a title whose DELETE failed offline before its add had
    /// ever synced kept its tombstone forever — permanently filtering that title out of every
    /// merge, including a legitimate re-bookmark made later on another device. The server reporting
    /// the title absent is proof the removal took effect, so the tombstone has done its job.
    /// Titles with a write still in flight are left alone: this GET may predate that write.
    private func pruneConfirmedTombstones(remote: Set<String>) {
        let confirmed = pendingRemovals.filter { !remote.contains($0) && inFlightPushes[$0] == nil }
        guard !confirmed.isEmpty else { return }
        pendingRemovals.subtract(confirmed)
        persistLocal()
    }

    private func beginPush(_ key: String) { inFlightPushes[key, default: 0] += 1 }

    private func endPush(_ key: String) {
        guard let count = inFlightPushes[key] else { return }
        if count <= 1 { inFlightPushes.removeValue(forKey: key) } else { inFlightPushes[key] = count - 1 }
    }

    /// - Returns: true when the server confirmed the add. Callers use this to decide whether
    ///   the title's server timestamp is now a reconcile ARTIFACT (see `reconciledTitles`);
    ///   pinning one whose push failed marks it as reconciled forever without ever having
    ///   reached the server.
    @discardableResult
    private func pushAdd(_ key: String) async -> Bool {
        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        beginPush(key)
        defer { endPush(key) }
        do {
            let resp = try await apiClient.request(
                endpoint: .addBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
            // TWO guards, answering different questions. `localVersion` catches a local toggle
            // racing this POST; it cannot see the ACCOUNT changing underneath, because each
            // identity starts from its own local state. Returning false also keeps the caller
            // from pinning this title into `reconciledTitles` for the wrong user.
            guard epoch == LearnIdentityEpoch.current, token == localVersion else { return false }
            merge(resp.bookmarks)
            return true
        } catch {
            // Non-fatal: stays in the local cache and `pushUnsynced` retries on the next hydrate.
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookmarkStore] add failed for \(key) [\(appError.title)]: \(appError.message)")
            }
            return false
        }
    }

    private func pushRemove(_ key: String) async {
        let token = localVersion
        let epoch = LearnIdentityEpoch.current
        beginPush(key)
        defer { endPush(key) }
        do {
            let resp = try await apiClient.request(
                endpoint: .removeBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
            // Identity moved while this was in flight → the response is the previous account's
            // list. Bail before touching either the store or the tombstone.
            guard epoch == LearnIdentityEpoch.current else { return }
            // Merge BEFORE retiring the tombstone: while `key` is still tombstoned the merge cannot
            // resurrect it, and retiring the tombstone is itself a local write that would
            // invalidate this very token.
            if token == localVersion { merge(resp.bookmarks) }
            if pendingRemovals.remove(key) != nil {   // DELETE confirmed → the title is gone server-side
                bumpLocalVersion()                    // any GET issued earlier predates this; it must not merge
                persistLocal()
            }
        } catch {
            // Stays tombstoned (pendingRemovals) so a racing/next hydrate() can't resurrect it;
            // `pushUnsynced` retries the DELETE on the next hydrate.
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookmarkStore] remove failed for \(key) [\(appError.title)]: \(appError.message)")
            }
        }
    }

    /// Reconcile with the server's ordered list while never dropping a local-only bookmark that
    /// hasn't pushed yet AND never resurrecting one the user just removed. Server order
    /// (most-recent-first) wins; local-only titles are pending adds kept at the front; titles in
    /// `pendingRemovals` are dropped from the server set until their DELETE confirms.
    private func merge(_ remote: [String]) {
        let effectiveRemote = remote.filter { !pendingRemovals.contains($0) }
        let remoteSet = Set(effectiveRemote)

        // Reconciled titles keep the index the user's own taps gave them: the server re-stamped
        // them when `pushUnsynced` re-POSTed, so their place in the server's most-recent-first list
        // is meaningless and would otherwise hijack the hero card. Everything else still takes
        // server order — that is what keeps a bookmark made on another device sorted correctly.
        let pinned = bookmarkedTitles.enumerated().filter { reconciledTitles.contains($0.element) }
        let pinnedTitles = Set(pinned.map(\.element))

        let pendingLocal = bookmarkedTitles.filter { !remoteSet.contains($0) && !pinnedTitles.contains($0) }
        var merged = pendingLocal + effectiveRemote.filter { !pinnedTitles.contains($0) }
        for entry in pinned {   // ascending offsets, so each lands back on its original index
            merged.insert(entry.element, at: min(entry.offset, merged.count))
        }

        // Collapse on BOTH paths, including "nothing changed" — the list can already have been
        // multi-valued before this response arrived. This is the single funnel every server→local
        // reconciliation passes through (hydrate, pushAdd, pushRemove), so enforcing one-at-a-time
        // here is what stops a second bookmark leaking in from another device between hydrates:
        // toggling A while the server still holds C would otherwise merge to [A, C] and sit there
        // until the next hydrate.
        guard merged != bookmarkedTitles else {
            collapseToSingleBookmark()
            return
        }
        bookmarkedTitles = merged
        reconciledTitles.formIntersection(bookmarkedTitles)   // keep the pin set bounded by the list
        persistLocal()
        collapseToSingleBookmark()
    }

    private func persistLocal() {
        UserDefaults.standard.set(bookmarkedTitles, forKey: Self.defaultsKey)
        UserDefaults.standard.set(Array(pendingRemovals), forKey: Self.pendingRemovalsKey)
        UserDefaults.standard.set(Array(reconciledTitles), forKey: Self.reconciledKey)
    }
}

// MARK: - DTO

/// Backend bookmark list response: `{ "bookmarks": [title, ...] }`, most-recent-first.
struct BookmarkListResponse: Decodable {
    let bookmarks: [String]
}
