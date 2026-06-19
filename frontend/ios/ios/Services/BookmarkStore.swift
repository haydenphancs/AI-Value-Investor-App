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

    private static let defaultsKey = "bookLibrary.bookmarkedTitles"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        bookmarkedTitles = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
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
            persistLocal()
            Task { await self.pushRemove(t) }
        } else {
            bookmarkedTitles.insert(t, at: 0)   // newest first
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
            // Offline or signed out: keep whatever is local.
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
            merge(resp.bookmarks)
        } catch {
            // Stays removed locally; re-pushes next time this book is toggled.
        }
    }

    /// Reconcile with the server's ordered list while never dropping a local-only bookmark that
    /// hasn't pushed yet. Server order (most-recent-first) wins; any titles only present locally
    /// are pending pushes, so they're kept at the front.
    private func merge(_ remote: [String]) {
        let remoteSet = Set(remote)
        let pendingLocal = bookmarkedTitles.filter { !remoteSet.contains($0) }
        let merged = pendingLocal + remote
        guard merged != bookmarkedTitles else { return }
        bookmarkedTitles = merged
        persistLocal()
    }

    private func persistLocal() {
        UserDefaults.standard.set(bookmarkedTitles, forKey: Self.defaultsKey)
    }
}

// MARK: - DTO

/// Backend bookmark list response: `{ "bookmarks": [title, ...] }`, most-recent-first.
struct BookmarkListResponse: Decodable {
    let bookmarks: [String]
}
