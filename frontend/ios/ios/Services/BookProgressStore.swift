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

    /// Per-key count of failed pushes. Reconcile batches are ordered by it so a key the server
    /// keeps rejecting sinks to the back: with a backlog larger than `maxReconcilePushes` a
    /// poison prefix would otherwise take the same slots in every batch and strand everything
    /// behind it. Session-only — a relaunch retries everything on equal footing.
    private var pushFailures: [String: Int] = [:]

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

    /// During continuous audio playback, auto-complete each core once the playhead crosses out of
    /// it (into the next core, or near the end for the last). Ignores seeks / large jumps so
    /// skipping ahead never marks cores the learner didn't actually listen through. Idempotent;
    /// returns the cores newly completed by this step (for a one-shot success haptic).
    @discardableResult
    func markListenedThrough(order: Int, from: Double, to: Double,
                             coreStarts: [Int: Int], totalSeconds: Int) -> [Int] {
        guard to > from, to - from < 2.0 else { return [] }   // continuous playback only
        let ordered = coreStarts.sorted { $0.value < $1.value }.map(\.key)
        var newly: [Int] = []
        for (i, core) in ordered.enumerated() {
            let isLast = i + 1 == ordered.count
            // Interior core finishes exactly when the next core begins; the last core may stop a
            // tick short of the exact end, so trigger a hair before totalSeconds.
            let trigger = isLast ? Double(totalSeconds) - 0.6
                                 : Double(coreStarts[ordered[i + 1]] ?? totalSeconds)
            if from < trigger, trigger <= to, !isCompleted(order: order, core: core) {
                markCompleted(order: order, core: core)
                newly.append(core)
            }
        }
        return newly
    }

    /// Clear all progress (debug / "reset" affordances). Local only.
    func reset() {
        guard !completed.isEmpty else { return }
        completed.removeAll()
        persistLocal()
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's completed set, union it in, and push back anything the server is missing.
    /// Call when the Library opens.
    func hydrate() async {
        do {
            let resp = try await apiClient.request(
                endpoint: .getLearnProgress(contentType: Self.contentType),
                responseType: LearnProgressResponse.self
            )
            merge(resp)
            await pushUnsynced(remote: Set(resp.keys))
        } catch {
            // Offline or signed out: keep whatever is local — but a decode/contract or 5xx failure
            // silently hides synced progress, so surface it (stays quiet on routine offline).
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookProgressStore] hydrate failed [\(appError.title)]: \(appError.message) — raw: \(error)")
            }
        }
    }

    /// Re-push completions the server doesn't have yet.
    ///
    /// Without this, a completion whose POST failed was LOST FOREVER: `markCompleted` returns
    /// early once the key is in the local set, so it could never be re-pushed, and `hydrate` only
    /// ever merged remote→local. Finish a core offline, reinstall, and it was gone. Reconciling
    /// here is safe because the sync model is union-only — nothing is ever deleted by a merge.
    private func pushUnsynced(remote: Set<String>) async {
        let unsynced = completed.subtracting(remote)
        guard !unsynced.isEmpty else { return }
        print("[BookProgressStore] re-pushing \(unsynced.count) unsynced completion(s)")
        // Bounded and deterministically ordered so a large backlog can't stall the Library open;
        // the rest go on the next hydrate. Nothing strands: a pushed key leaves `unsynced` once the
        // server has it, and a key that keeps failing sorts to the back rather than re-claiming a
        // slot, so successive hydrates drain the whole backlog `maxReconcilePushes` at a time.
        for key in reconcileOrder(unsynced).prefix(Self.maxReconcilePushes) {
            await pushCompletion(key)
        }
    }

    /// Fewest failures first, then lexicographic — stable across hydrates, and it rotates keys that
    /// keep failing out of the head of the batch.
    private func reconcileOrder(_ keys: Set<String>) -> [String] {
        keys.sorted {
            let (l, r) = (pushFailures[$0] ?? 0, pushFailures[$1] ?? 0)
            return l == r ? $0 < $1 : l < r
        }
    }

    private static let maxReconcilePushes = 25

    private func pushCompletion(_ key: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .completeLearnItem(contentType: Self.contentType, key: key),
                responseType: LearnProgressResponse.self
            )
            pushFailures.removeValue(forKey: key)
            merge(resp)
        } catch {
            // Non-fatal: the completion stays in the local cache and `pushUnsynced` retries it on
            // the next hydrate. Logged rather than swallowed — a persistent failure here means
            // progress is only ever local (invariant: never degrade silently).
            pushFailures[key, default: 0] += 1
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[BookProgressStore] push failed for \(key) [\(appError.title)]: \(appError.message)")
            }
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
