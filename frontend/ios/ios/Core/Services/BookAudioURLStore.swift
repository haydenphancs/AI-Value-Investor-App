//
//  BookAudioURLStore.swift
//  ios
//
//  Where a book's narration URL comes from, now that it is no longer compiled in.
//
//  `BookAudioContent.swift` used to bake the PUBLIC Supabase Storage URL of all ten books
//  into the binary. That made the Pro/Max narration gate cosmetic: one network request, or
//  one `strings` pass over the app, handed anyone all 276 MB. The URLs are now short-lived
//  SIGNED urls minted per request by GET /api/v1/learn/books/audio, so the bucket can go
//  private (migration 128) and the gate becomes real.
//
//  ⚠️ THE REGRESSION THIS TYPE EXISTS TO AVOID.
//  `AudioManager.isMissingNarration` treats a nil/empty `audioUrl` on a `.books` episode as
//  a hard refuse — correct for a locked user, but it means a PAYING user who taps Play
//  before the fetch has landed is told there is no narration. So:
//    • `prefetch()` runs when a Book screen appears, and
//    • every play site `await`s `url(for:)`, which fetches on demand if the memo is cold.
//  The prefetch makes the wait invisible; the await makes it correct.
//
//  Durations and per-core offsets are NOT here — those stay compiled in `BookAudioInfo`,
//  because they render for locked users too.
//

import Foundation
import Combine

@MainActor
final class BookAudioURLStore: ObservableObject {
    static let shared = BookAudioURLStore()

    /// curriculumOrder -> signed URL. Empty until the first successful fetch, and empty by
    /// design for a locked account (the backend answers 200 with no books).
    @Published private(set) var urls: [Int: String] = [:]

    /// What the backend said about this account's plan on the last fetch. Purely
    /// informational — the gate itself is `LearnAudioEntitlement`.
    @Published private(set) var audioLocked: Bool = false

    private var inFlight: Task<Void, Never>?
    /// Signatures live 24h server-side and are reused for 6h, so re-fetching more often than
    /// this is pure waste. A failed fetch does NOT set this, so it retries immediately.
    private var lastLoadedAt: Date?
    private static let refreshInterval: TimeInterval = 60 * 60   // 1h

    private init() {}

    /// Fire-and-forget warm-up. Safe to call from `.task` on every Book screen.
    func prefetch() {
        guard LearnAudioEntitlement.shared.isUnlocked else { return }
        guard needsRefresh else { return }
        Task { await load() }
    }

    /// The playable URL for a book, fetching if the memo is cold. `nil` means "don't play":
    /// either this account isn't entitled, or the URL could not be obtained.
    func url(for curriculumOrder: Int) async -> String? {
        guard LearnAudioEntitlement.shared.isUnlocked else { return nil }
        if let cached = urls[curriculumOrder], !needsRefresh { return cached }
        await load()
        return urls[curriculumOrder]
    }

    /// Drop one book's URL after a playback failure so the next attempt re-fetches.
    /// See `AudioManager.handleItemStatusChange`.
    func invalidate(curriculumOrder: Int) {
        urls.removeValue(forKey: curriculumOrder)
        lastLoadedAt = nil
    }

    /// Clear everything on sign-out / downgrade. A signed URL minted for the previous
    /// account must not survive into the next one on the same device.
    func reset() {
        inFlight?.cancel()
        inFlight = nil
        urls = [:]
        audioLocked = false
        lastLoadedAt = nil
    }

    private var needsRefresh: Bool {
        guard let lastLoadedAt else { return true }
        return Date().timeIntervalSince(lastLoadedAt) > Self.refreshInterval
    }

    /// Single-flight: a burst of taps across the library shares one request.
    private func load() async {
        if let inFlight {
            await inFlight.value
            return
        }
        let task = Task { await self.fetch() }
        inFlight = task
        await task.value
        inFlight = nil
    }

    private func fetch() async {
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getBooksAudio,
                responseType: BooksAudioResponse.self
            )
            var next: [Int: String] = [:]
            for book in response.books where !book.audioURL.isEmpty {
                next[book.curriculumOrder] = book.audioURL
            }
            urls = next
            audioLocked = response.audioLocked
            // Latch the timestamp ONLY on a response that actually carried URLs. A locked or
            // empty response must stay re-fetchable, or upgrading mid-session would leave the
            // library silent for an hour with no way to recover but a relaunch.
            lastLoadedAt = next.isEmpty ? nil : Date()
        } catch {
            // Non-fatal and deliberately quiet in the UI — the play site turns a nil URL into
            // the normal "no narration" path. Logged because a failure here looks to the user
            // exactly like a book that was never narrated.
            let appError = AppError.from(error)
            print("[BookAudioURLStore] fetch failed [\(appError.title)]: \(appError.message) — raw: \(error)")
            lastLoadedAt = nil
        }
    }
}

// MARK: - DTO

/// Mirrors `backend/app/schemas/learn_books_audio.py`.
struct BooksAudioResponse: Decodable {
    let books: [BookAudioURLDTO]
    let audioLocked: Bool
    let tierRequired: String?

    enum CodingKeys: String, CodingKey {
        case books
        case audioLocked = "audio_locked"
        case tierRequired = "tier_required"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Every field defaulted: this response is pure enhancement, and a decode failure here
        // would silence narration for a paying user over a field they don't need.
        books = (try? c.decode([BookAudioURLDTO].self, forKey: .books)) ?? []
        audioLocked = (try? c.decode(Bool.self, forKey: .audioLocked)) ?? false
        tierRequired = try? c.decodeIfPresent(String.self, forKey: .tierRequired)
    }
}

struct BookAudioURLDTO: Decodable {
    let curriculumOrder: Int
    let audioURL: String

    enum CodingKeys: String, CodingKey {
        case curriculumOrder = "curriculum_order"
        case audioURL = "audio_url"
    }
}
