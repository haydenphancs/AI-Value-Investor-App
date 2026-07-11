//
//  MoneyMovesProgressStore.swift
//  ios
//
//  Single source of truth for Money Moves article completion (which case studies the learner
//  has finished). Mirrors BookProgressStore: HYBRID persistence — a local UserDefaults cache
//  (instant + offline) that writes through to the backend
//  (GET/POST /api/v1/learn/money-moves/...). On launch it union-merges the server's set in, so
//  progress survives reinstall and syncs across devices without losing local writes.
//
//  Keyed by article `slug` (the canonical stable id; also the audio file key). A move is marked
//  complete when the learner reaches the end of the article or finishes its narration.
//

import Foundation
import Combine

@MainActor
final class MoneyMovesProgressStore: ObservableObject {
    static let shared = MoneyMovesProgressStore()

    /// Slugs of the articles the learner has completed.
    @Published private(set) var completed: Set<String> = []

    /// Slugs the user un-completed whose backend DELETE hasn't confirmed yet. hydrate()'s union-merge
    /// would otherwise resurrect a just-unmarked slug the moment the GET races ahead of (or the
    /// device is offline for) the DELETE — flipping the article back to "Completed" against the
    /// user's explicit tap. Persisted so an offline unmark survives relaunch; cleared once the
    /// DELETE confirms (or the user re-completes).
    private var pendingUncompleted: Set<String> = []

    private static let defaultsKey = "moneyMoves.completedSlugs"
    private static let pendingKey = "moneyMoves.pendingUncompletedSlugs"
    private static let contentType = "money_move"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        completed = Set(UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? [])
        pendingUncompleted = Set(UserDefaults.standard.stringArray(forKey: Self.pendingKey) ?? [])
    }

    // MARK: - Reads
    func isCompleted(slug: String) -> Bool {
        !slug.isEmpty && completed.contains(slug)
    }

    var completedCount: Int { completed.count }

    // MARK: - Writes

    /// Record a finished article. Idempotent; persists locally and pushes to the backend.
    func markCompleted(slug: String) {
        let s = slug.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty, !completed.contains(s) else { return }
        completed.insert(s)
        pendingUncompleted.remove(s)   // re-completing supersedes any pending un-completion tombstone
        persistLocal()
        Task { await self.pushCompletion(s) }
    }

    /// Un-mark an article (the article-end toggle's "undo"). Removes it locally AND on the backend
    /// (DELETE) so it doesn't reappear on the next sync.
    func unmarkCompleted(slug: String) {
        let s = slug.trimmingCharacters(in: .whitespacesAndNewlines)
        guard completed.contains(s) else { return }
        completed.remove(s)
        pendingUncompleted.insert(s)   // tombstone until the DELETE confirms; blocks hydrate() resurrection
        persistLocal()
        Task { await self.pushUncompletion(s) }
    }

    /// Flip completion — used by the article-end Complete / Completed button.
    func toggleCompleted(slug: String) {
        isCompleted(slug: slug) ? unmarkCompleted(slug: slug) : markCompleted(slug: slug)
    }

    /// Clear all progress (debug / reset). Local only.
    func reset() {
        guard !completed.isEmpty || !pendingUncompleted.isEmpty else { return }
        completed.removeAll()
        pendingUncompleted.removeAll()
        persistLocal()
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

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

    private func pushCompletion(_ slug: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .completeLearnItem(contentType: Self.contentType, key: slug),
                responseType: LearnProgressResponse.self
            )
            merge(resp)
        } catch {
            // Stays in the local cache; re-pushes next time it's marked.
        }
    }

    private func pushUncompletion(_ slug: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .uncompleteLearnItem(contentType: Self.contentType, key: slug),
                responseType: LearnProgressResponse.self
            )
            pendingUncompleted.remove(slug)   // DELETE confirmed → the slug is gone server-side, drop the tombstone
            persistLocal()
            merge(resp)
        } catch {
            // Stays tombstoned (pendingUncompleted) so a racing/next hydrate() can't resurrect it;
            // re-pushes the delete next time it's toggled.
        }
    }

    private func merge(_ resp: LearnProgressResponse) {
        // Subtract not-yet-confirmed un-completions so the union can't resurrect a slug the user
        // just removed while its DELETE is still in flight (or failed offline).
        let remote = Set(resp.keys).subtracting(pendingUncompleted)
        guard !remote.isSubset(of: completed) else { return }
        completed.formUnion(remote)
        persistLocal()
    }

    private func persistLocal() {
        UserDefaults.standard.set(Array(completed), forKey: Self.defaultsKey)
        UserDefaults.standard.set(Array(pendingUncompleted), forKey: Self.pendingKey)
    }
}

// Shared Learn-progress DTOs (LearnProgressResponse / CompleteLearnItemRequest) live in
// BookProgressStore.swift and are reused here.
