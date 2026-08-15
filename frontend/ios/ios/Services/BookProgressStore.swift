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

    /// Fires with a book's `curriculumOrder` at the instant a completion fills in that book's LAST
    /// missing core — the "you finished the whole book" moment.
    ///
    /// It lives HERE, on the single write funnel, rather than as a was-it-complete-before /
    /// is-it-complete-now check at a call site, for two reasons:
    ///
    ///  • **It cannot lose a race.** `markListenedThrough` is driven on EVERY audio tick by BOTH
    ///    `BookDetailView` and `BookCoreDetailView` when they are on screen together (see its own
    ///    doc below), in an order SwiftUI does not define. Whichever lands first does the write; a
    ///    call-site check in the other then reads "already complete" on both sides of its own call,
    ///    sees no transition, and the celebration silently never appears — intermittently, so it
    ///    would pass a hand test about half the time.
    ///  • **It cannot fire dishonestly.** `merge()` — the hydrate / push-response path — writes to
    ///    `completed` WITHOUT going through `markCompleted`, so progress arriving from another
    ///    device can never raise this. Only something the learner just did can.
    let didFinishBook = PassthroughSubject<Int, Never>()

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
    ///
    /// `bookCores` is the full roster of core numbers in this book, and it is what makes
    /// `didFinishBook` honest. It is checked with `allSatisfy`, NOT against `completedCount` —
    /// which makes it strictly stronger than `isMastered` above, whose `>=` on a key count would
    /// call a book finished if a stale key for a core the book no longer has padded the total.
    /// Pass `[]` only where the caller genuinely has no roster; that just suppresses the event.
    func markCompleted(order: Int, core: Int, bookCores: [Int]) {
        let k = key(order, core)
        guard !completed.contains(k) else { return }
        completed.insert(k)
        persistLocal()
        // One-shot by construction: the guard above early-returns on a key we already hold, so once
        // the roster is satisfied no later write for this book can reach this line again. That also
        // covers `markListenedThrough`'s loop — if one tick crosses two boundaries, only the write
        // that fills the last hole satisfies `allSatisfy`.
        if !bookCores.isEmpty, bookCores.allSatisfy({ isCompleted(order: order, core: $0) }) {
            didFinishBook.send(order)
        }
        Task { await self.pushCompletion(k) }
    }

    /// The last playhead sample this store acted on: which book, where it landed, and the
    /// `AudioManager.seekEpoch` in force at the time. A step is "listened through" only when it
    /// continues THAT sample — same book, starts where the last one ended, no seek in between.
    private var lastSample: (order: Int, to: Double, seekEpoch: UInt64)?

    /// During continuous audio playback, auto-complete each core once the playhead crosses out of
    /// it (into the next core, or near the end for the last). Idempotent; returns the cores newly
    /// completed by this step (for a one-shot success haptic).
    ///
    /// Continuity is decided by `AudioManager.seekEpoch`, NOT by the size of the step. The old
    /// `to - from < 2.0` guard used step size as a proxy for "didn't seek", which threw away real
    /// playback: the periodic time observer coalesces ticks when the app is backgrounded or
    /// recovering from a stall, so any boundary inside one of those long-but-genuine steps was
    /// never completed — a fully-listened book ended up "mastered" with holes in its timeline.
    ///
    /// Called every tick by BOTH `BookDetailView` and `BookCoreDetailView` when they are on screen
    /// together. That is harmless: the first call does the work and the second sees a sample that
    /// no longer continues `lastSample`, so it no-ops (and completion is idempotent regardless).
    ///
    /// `bookCores` is forwarded to `markCompleted` so a book finished BY LISTENING raises
    /// `didFinishBook` too. It is deliberately required rather than defaulted: this method has
    /// exactly two call sites and they are the two racing writers, so a default would let one of
    /// them silently stop reporting the finish.
    @discardableResult
    func markListenedThrough(order: Int, from: Double, to: Double,
                             coreStarts: [Int: Int], totalSeconds: Int,
                             seekEpoch: UInt64, bookCores: [Int]) -> [Int] {
        let previous = lastSample
        lastSample = (order, to, seekEpoch)

        guard to > from else { return [] }

        let isContinuous: Bool
        if let previous, previous.order == order {
            // A scrub / skip / core-jump moved the playhead somewhere the listener never heard.
            if previous.seekEpoch != seekEpoch {
                isContinuous = false
            } else {
                // Continues the last sample we acted on. (The duplicate call from the second
                // observing view lands here with a `from` that no longer matches, so it no-ops —
                // and completion is idempotent anyway.)
                isContinuous = abs(previous.to - from) < 0.01
            }
        } else {
            // Nothing to continue yet: the first tick after this view appeared, or a switch to
            // another book. Fall back to the old small-step heuristic for THAT tick only —
            // without it, a core boundary landing inside the very first observed interval would
            // never complete, which is the same missing-badge symptom this method prevents.
            // A step under 2s cannot skip past a whole core.
            isContinuous = (to - from) < 2.0
        }
        guard isContinuous else { return [] }
        let ordered = coreStarts.sorted { $0.value < $1.value }.map(\.key)
        var newly: [Int] = []
        for (i, core) in ordered.enumerated() {
            let isLast = i + 1 == ordered.count
            // Interior core finishes exactly when the next core begins; the last core may stop a
            // tick short of the exact end, so trigger a hair before totalSeconds.
            let trigger = isLast ? Double(totalSeconds) - 0.6
                                 : Double(coreStarts[ordered[i + 1]] ?? totalSeconds)
            if from < trigger, trigger <= to, !isCompleted(order: order, core: core) {
                markCompleted(order: order, core: core, bookCores: bookCores)
                newly.append(core)
            }
        }
        return newly
    }

    /// Clear all progress (debug / "reset" affordances, and SIGN-OUT — see `AppState.signOut`,
    /// which bumps `LearnIdentityEpoch` first so an in-flight hydrate can't refill this with the
    /// previous account's cores). Local only.
    func reset() {
        guard !completed.isEmpty else { return }
        completed.removeAll()
        persistLocal()
    }

    // MARK: - Backend sync (best-effort; the local cache is the source of truth)

    /// Pull the server's completed set, union it in, and push back anything the server is missing.
    /// Call when the Library opens.
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
            // and for `reloadForIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            self.hydrateTask = nil
        }
        hydrateTask = task
        await task.value
    }

    private func performHydrate() async {
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .getLearnProgress(contentType: Self.contentType),
                responseType: LearnProgressResponse.self
            )
            // The account changed while this was in flight (sign-out / switch). These keys belong
            // to the PREVIOUS user — merging them would show their finished cores to the new one,
            // and pushUnsynced would then write them into the new account. Drop it.
            guard epoch == LearnIdentityEpoch.current else {
                print("[BookProgressStore] discarded a hydrate from a previous identity")
                return
            }
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
        // The epoch guard belongs on the WRITE side too, not just on `hydrate()`'s read. This
        // loop is up to 25 network round trips; a sign-out or account switch partway through
        // used to POST the PREVIOUS account's completed cores into the NEW account, durable
        // and visible on all of its devices — exactly the leak LearnIdentityEpoch exists to
        // stop, arrived at from the other direction. Under `.restoring` it is worse in a
        // quieter way: the token is disarmed mid-loop, so the rest of the batch lands in the
        // install's guest partition instead.
        let epoch = LearnIdentityEpoch.current
        for key in reconcileOrder(unsynced).prefix(Self.maxReconcilePushes) {
            guard epoch == LearnIdentityEpoch.current else {
                print("[BookProgressStore] abandoned a reconcile push from a previous identity")
                return
            }
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
        let epoch = LearnIdentityEpoch.current
        do {
            let resp = try await apiClient.request(
                endpoint: .completeLearnItem(contentType: Self.contentType, key: key),
                responseType: LearnProgressResponse.self
            )
            // Guarded here as well as in the loop, because `markCompleted` calls this directly.
            // The response describes the PREVIOUS account's row set if the identity moved while
            // it was in flight, and merging it would refill this store for the wrong user.
            guard epoch == LearnIdentityEpoch.current else { return }
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
