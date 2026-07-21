//
//  JourneyProgressStore.swift
//  ios
//
//  Single source of truth for Investor Journey lesson completion.
//
//  Both the full-screen journey (InvestorJourneyViewModel) and the Learn-tab overview card
//  (LearnViewModel) read completion from here so they always agree. HYBRID persistence: a local
//  UserDefaults cache (instant + offline) that writes through to the backend
//  (GET/POST /api/v1/learn/journey/...). On launch it union-merges the server's set in, so
//  progress survives reinstall and syncs across devices without ever losing a local write.
//
//  It is model-agnostic: it only knows lesson *titles* (the stable lesson key — the journey
//  catalog is static in-app and its Lesson.id is a fresh UUID each launch). Each surface maps
//  that set onto its own view-model shape.
//

import Foundation
import Combine

@MainActor
final class JourneyProgressStore: ObservableObject {
    static let shared = JourneyProgressStore()

    /// Titles of lessons the learner has finished.
    @Published private(set) var completedTitles: Set<String> = []

    /// Per-title count of failed pushes. Reconcile batches are ordered by it so a title the server
    /// keeps rejecting sinks to the back: the journey catalog is far larger than
    /// `maxReconcilePushes`, so a poison prefix would otherwise take the same slots in every batch
    /// and strand everything behind it. Session-only — a relaunch retries everything equally.
    private var pushFailures: [String: Int] = [:]

    private static let defaultsKey = "investorJourney.completedLessonTitles"
    private static let contentType = "journey_lesson"
    private let apiClient: APIClient

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        let saved = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
        completedTitles = Set(saved)
    }

    func isCompleted(_ title: String) -> Bool {
        completedTitles.contains(title)
    }

    /// Record a finished lesson. Idempotent; persists locally and pushes to the backend.
    func markCompleted(_ title: String) {
        guard !completedTitles.contains(title) else { return }
        completedTitles.insert(title)
        persistLocal()
        Task { await self.pushCompletion(title) }
    }

    /// Clear all progress (debug / "reset journey" affordances). Local only.
    func reset() {
        guard !completedTitles.isEmpty else { return }
        completedTitles.removeAll()
        persistLocal()
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's completed set and union it in. Call when the Learn surface opens.
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
                print("[JourneyProgressStore] hydrate failed [\(appError.title)]: \(appError.message) — raw: \(error)")
            }
        }
    }

    /// Re-push completions the server doesn't have yet.
    ///
    /// Without this, a completion whose POST failed was LOST FOREVER: `markCompleted` returns early
    /// once the title is in the local set, so it could never be re-pushed, and `hydrate` only ever
    /// merged remote→local. Finish a lesson offline, reinstall, and it was gone. Safe because the
    /// sync model is union-only — nothing is ever deleted by a merge.
    private func pushUnsynced(remote: Set<String>) async {
        let unsynced = completedTitles.subtracting(remote)
        guard !unsynced.isEmpty else { return }
        print("[JourneyProgressStore] re-pushing \(unsynced.count) unsynced completion(s)")
        // Bounded and deterministically ordered so a large backlog can't stall the Learn surface
        // opening; the rest go on the next hydrate. Nothing strands: a pushed title leaves
        // `unsynced` once the server has it, and a title that keeps failing sorts to the back
        // rather than re-claiming a slot, so successive hydrates drain the whole backlog.
        for title in reconcileOrder(unsynced).prefix(Self.maxReconcilePushes) {
            await pushCompletion(title)
        }
    }

    /// Fewest failures first, then lexicographic — stable across hydrates, and it rotates titles
    /// that keep failing out of the head of the batch.
    private func reconcileOrder(_ titles: Set<String>) -> [String] {
        titles.sorted {
            let (l, r) = (pushFailures[$0] ?? 0, pushFailures[$1] ?? 0)
            return l == r ? $0 < $1 : l < r
        }
    }

    private static let maxReconcilePushes = 25

    private func pushCompletion(_ title: String) async {
        do {
            let resp = try await apiClient.request(
                endpoint: .completeLearnItem(contentType: Self.contentType, key: title),
                responseType: LearnProgressResponse.self
            )
            pushFailures.removeValue(forKey: title)
            merge(resp)
        } catch {
            // Non-fatal: stays in the local cache and `pushUnsynced` retries on the next hydrate.
            // Logged rather than swallowed — a persistent failure means progress is only local.
            pushFailures[title, default: 0] += 1
            let appError = AppError.from(error)
            if !appError.isExpectedOffline {
                print("[JourneyProgressStore] push failed for \(title) [\(appError.title)]: \(appError.message)")
            }
        }
    }

    private func merge(_ resp: LearnProgressResponse) {
        let remote = Set(resp.keys)
        guard !remote.isSubset(of: completedTitles) else { return }
        completedTitles.formUnion(remote)
        persistLocal()
    }

    private func persistLocal() {
        UserDefaults.standard.set(Array(completedTitles), forKey: Self.defaultsKey)
    }
}

// Shared Learn-progress DTOs (LearnProgressResponse / CompleteLearnItemRequest) live in
// BookProgressStore.swift and are reused here.
