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

    private static let defaultsKey = "bookLibrary.bookmarkedTitles"
    private static let pendingRemovalsKey = "bookLibrary.pendingRemovedBookmarks"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        bookmarkedTitles = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
        pendingRemovals = Set(UserDefaults.standard.stringArray(forKey: Self.pendingRemovalsKey) ?? [])
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
            persistLocal()
            Task { await self.pushRemove(t) }
        } else {
            bookmarkedTitles.insert(t, at: 0)   // newest first
            pendingRemovals.remove(t)           // re-bookmarking supersedes any pending removal
            persistLocal()
            Task { await self.pushAdd(t) }
        }
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's bookmarks and merge them in. Call when the Library / Learn screen opens.
    func hydrate() async {
        do {
            let resp = try await apiClient.request(
                endpoint: .getBookBookmarks,
                responseType: BookmarkListResponse.self
            )
            merge(resp.bookmarks)
        } catch {
            // Offline or signed out: keep whatever is local — but a decode/contract or 5xx failure
            // silently hides synced bookmarks, so surface it (stays quiet on routine offline).
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookmarkStore] hydrate failed [\(appError.title)]: \(appError.message) — raw: \(error)")
            }
        }
    }

    private func pushAdd(_ key: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .addBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
            merge(resp.bookmarks)
        } catch {
            // Stays in the local cache; re-pushes next time this book is toggled.
        }
    }

    private func pushRemove(_ key: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .removeBookBookmark(key: key),
                responseType: BookmarkListResponse.self
            )
            pendingRemovals.remove(key)   // DELETE confirmed → the title is gone server-side, drop the tombstone
            persistLocal()
            merge(resp.bookmarks)
        } catch {
            // Stays tombstoned (pendingRemovals) so a racing/next hydrate() can't resurrect it;
            // re-pushes the delete next time this book is toggled.
        }
    }

    /// Reconcile with the server's ordered list while never dropping a local-only bookmark that
    /// hasn't pushed yet AND never resurrecting one the user just removed. Server order
    /// (most-recent-first) wins; local-only titles are pending adds kept at the front; titles in
    /// `pendingRemovals` are dropped from the server set until their DELETE confirms.
    private func merge(_ remote: [String]) {
        let effectiveRemote = remote.filter { !pendingRemovals.contains($0) }
        let remoteSet = Set(effectiveRemote)
        let pendingLocal = bookmarkedTitles.filter { !remoteSet.contains($0) }
        let merged = pendingLocal + effectiveRemote
        guard merged != bookmarkedTitles else { return }
        bookmarkedTitles = merged
        persistLocal()
    }

    private func persistLocal() {
        UserDefaults.standard.set(bookmarkedTitles, forKey: Self.defaultsKey)
        UserDefaults.standard.set(Array(pendingRemovals), forKey: Self.pendingRemovalsKey)
    }
}

// MARK: - DTO

/// Backend bookmark list response: `{ "bookmarks": [title, ...] }`, most-recent-first.
struct BookmarkListResponse: Decodable {
    let bookmarks: [String]
}
