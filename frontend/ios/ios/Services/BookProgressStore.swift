//
//  BookProgressStore.swift
//  ios
//
//  Single source of truth for Book Library reading progress (which cores the learner has
//  finished, per book). Mirrors JourneyProgressStore, but hybrid: a local UserDefaults
//  cache (instant + offline) that writes through to the backend
//  (GET/POST /api/v1/learn/books/...). On launch it union-merges the server's set in, so
//  progress survives reinstall and syncs across devices without ever losing local writes.
//
//  Every surface (BookLibraryView mastery %, BookDetailView "Continue Core N" + timeline,
//  BookCoreDetailView completion) observes this store, so finishing a core anywhere updates
//  everywhere live.
//
//  Keys are "<curriculumOrder>-<coreNumber>". curriculumOrder (1..10) is the stable book id;
//  the Book Library content itself lives in the app (BooksContent.swift), not the DB.
//

import Foundation
import Combine

@MainActor
final class BookProgressStore: ObservableObject {
    static let shared = BookProgressStore()

    /// "order-core" keys for every core the learner has completed.
    @Published private(set) var completed: Set<String> = []

    private static let defaultsKey = "bookLibrary.completedCores"
    private static let contentType = "book_core"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        let saved = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
        completed = Set(saved)
    }

    private func key(_ order: Int, _ core: Int) -> String { "\(order)-\(core)" }

    // MARK: - Reads

    func isCompleted(order: Int, core: Int) -> Bool {
        completed.contains(key(order, core))
    }

    func completedCount(order: Int) -> Int {
        completed.reduce(into: 0) { count, k in
            let parts = k.split(separator: "-")
            if parts.count == 2, Int(parts[0]) == order { count += 1 }
        }
    }

    func hasProgress(order: Int) -> Bool {
        completedCount(order: order) > 0
    }

    /// A book is "mastered" once every one of its cores is completed.
    func isMastered(order: Int, totalCores: Int) -> Bool {
        totalCores > 0 && completedCount(order: order) >= totalCores
    }

    /// First core (1...totalCores) the learner hasn't finished; the last core if all are done.
    func resumeCore(order: Int, totalCores: Int) -> Int {
        guard totalCores > 0 else { return 1 }
        for n in 1...totalCores where !isCompleted(order: order, core: n) { return n }
        return totalCores
    }

    // MARK: - Writes

    /// Record a finished core. Idempotent; persists locally and pushes to the backend.
    func markCompleted(order: Int, core: Int) {
        let k = key(order, core)
        guard !completed.contains(k) else { return }
        completed.insert(k)
        persistLocal()
        Task { await self.pushCompletion(k) }
    }

    /// Clear all progress (debug / "reset" affordances). Local only.
    func reset() {
        guard !completed.isEmpty else { return }
        completed.removeAll()
        persistLocal()
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's completed set and union it in. Call when the Library opens.
    func hydrate() async {
        do {
            let resp = try await apiClient.request(
                endpoint: .getLearnProgress(contentType: Self.contentType),
                responseType: LearnProgressResponse.self
            )
            merge(resp)
        } catch {
            // Offline or signed out: keep whatever is local.
        }
    }

    private func pushCompletion(_ key: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .completeLearnItem(contentType: Self.contentType, key: key),
                responseType: LearnProgressResponse.self
            )
            merge(resp)
        } catch {
            // Stays in the local cache; re-pushes next time this core is marked.
        }
    }

    private func merge(_ resp: LearnProgressResponse) {
        let remote = Set(resp.keys)
        guard !remote.isSubset(of: completed) else { return }
        completed.formUnion(remote)
        persistLocal()
    }

    private func persistLocal() {
        UserDefaults.standard.set(Array(completed), forKey: Self.defaultsKey)
    }
}

// MARK: - Shared Learn-progress DTOs
// Used by all three Learn progress stores (Books / Journey / Money Moves) and APIEndpoint.
// The backend's unified completion log returns a flat list of item_keys per content_type.

struct LearnProgressResponse: Decodable {
    let keys: [String]
}

nonisolated struct CompleteLearnItemRequest: Encodable, Sendable {
    let key: String
}
