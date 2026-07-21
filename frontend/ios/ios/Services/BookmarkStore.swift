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
//  The list is kept most-recent-first: `mostRecent` drives the Book Library hero-card shortcut.
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
    }

    // MARK: - Reads

    func isBookmarked(_ title: String) -> Bool {
        bookmarkedTitles.contains(title)
    }

    /// The most-recently bookmarked book (the Book Library hero shortcut opens this), or nil.
    var mostRecent: String? { bookmarkedTitles.first }

    // MARK: - Writes

    /// Add or remove a bookmark. Updates locally first (instant), then pushes to the backend.
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
            bookmarkedTitles.insert(t, at: 0)   // newest first
            pendingRemovals.remove(t)           // re-bookmarking supersedes any pending removal
            reconciledTitles.remove(t)          // an explicit tap gives the server a real timestamp
            bumpLocalVersion()
            persistLocal()
            beginPush(t)
            Task { await self.pushAdd(t); self.endPush(t) }
        }
    }

    /// Called for every local write, so an in-flight request can tell its snapshot went stale.
    private func bumpLocalVersion() { localVersion &+= 1 }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's bookmarks and merge them in. Call when the Library / Learn screen opens.
    func hydrate() async {
        let token = localVersion
        do {
            let resp = try await apiClient.request(
                endpoint: .getBookBookmarks,
                responseType: BookmarkListResponse.self
            )
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

        if !unsyncedAdds.isEmpty {
            print("[BookmarkStore] re-pushing \(unsyncedAdds.count) unsynced bookmark(s)")
            // Oldest first so the server's most-recent-first ordering ends up matching local order.
            // Bounded, and the bound can't strand anything: each pushed title leaves `unsyncedAdds`
            // once the server has it, so successive hydrates drain the backlog deterministically.
            for key in unsyncedAdds.reversed().prefix(Self.maxReconcilePushes) {
                reconciledTitles.insert(key)   // its server timestamp is now an artifact — see merge
                await pushAdd(key)
            }
            persistLocal()
        }
        if !staleTombstones.isEmpty {
            print("[BookmarkStore] retrying \(staleTombstones.count) unconfirmed removal(s)")
            for key in staleTombstones.sorted().prefix(Self.maxReconcilePushes) {
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

    private func pushAdd(_ key: String) async {
        let token = localVersion
        beginPush(key)
        defer { endPush(key) }
        do {
            let resp = try await apiClient.request(
                endpoint: .addBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
            // Same stale-snapshot guard as hydrate: a toggle that landed while this POST was in
            // flight makes the echoed list a pre-write view of the server.
            guard token == localVersion else { return }
            merge(resp.bookmarks)
        } catch {
            // Non-fatal: stays in the local cache and `pushUnsynced` retries on the next hydrate.
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookmarkStore] add failed for \(key) [\(appError.title)]: \(appError.message)")
            }
        }
    }

    private func pushRemove(_ key: String) async {
        let token = localVersion
        beginPush(key)
        defer { endPush(key) }
        do {
            let resp = try await apiClient.request(
                endpoint: .removeBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
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

        guard merged != bookmarkedTitles else { return }
        bookmarkedTitles = merged
        reconciledTitles.formIntersection(bookmarkedTitles)   // keep the pin set bounded by the list
        persistLocal()
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
