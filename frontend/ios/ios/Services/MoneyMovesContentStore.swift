//
//  MoneyMovesContentStore.swift
//  ios
//
//  Source of Money Moves article content (the full case-study / deep-dive articles).
//
//  Primary source: the backend `GET /api/v1/learn/money-moves` endpoint, which serves
//  each article's content (with its narration audioUrl) from Supabase.
//  Offline fallback: the bundled money_moves.json (same shape, text only).
//
//  Both sources decode to the same MoneyMoveArticleDTO and map into MoneyMoveArticle,
//  so the views never care where the content came from. Mirrors JourneyContentStore.
//

import Foundation

@MainActor
final class MoneyMovesContentStore {
    static let shared = MoneyMovesContentStore()

    private var bundledByTitle: [String: MoneyMoveArticle] = [:]
    private var remoteByTitle: [String: MoneyMoveArticle] = [:]
    // Slug-keyed too: slug is the canonical id (title can collide across articles). Card taps
    // resolve by slug first so two articles sharing a title still open the right one.
    private var bundledBySlug: [String: MoneyMoveArticle] = [:]
    private var remoteBySlug: [String: MoneyMoveArticle] = [:]
    // Ordered card lists (by sortOrder) so the catalog can be served from content
    // instead of hardcoded in the view. Remote is authoritative; bundled is the
    // offline fallback for whatever shipped in the binary.
    private var bundledCards: [MoneyMove] = []
    private var remoteCards: [MoneyMove] = []
    /// Latched only after remote content has actually LANDED (see `prefetch()`).
    private var didPrefetch = false
    /// The single in-flight fetch, so concurrent callers join it instead of racing past it.
    private var prefetchTask: Task<Void, Never>?

    // The featured "deep dive" hero article (isFeatured == true). Remote is authoritative.
    private var remoteFeatured: MoneyMoveArticle?
    private var bundledFeatured: MoneyMoveArticle?

    private init() {
        loadBundled()
    }

    /// Full article for a card title: prefer fresh backend content, fall back to bundled.
    /// Returns nil when neither source has authored content for the title (caller then
    /// generates placeholder content).
    func article(forTitle title: String) -> MoneyMoveArticle? {
        remoteByTitle[title] ?? bundledByTitle[title]
    }

    /// Full article by slug (the canonical id). Preferred over title lookup so a title collision
    /// can't open the wrong article. nil for an empty slug (placeholder cards) or no match.
    func article(forSlug slug: String) -> MoneyMoveArticle? {
        guard !slug.isEmpty else { return nil }
        return remoteBySlug[slug] ?? bundledBySlug[slug]
    }

    func hasContent(forTitle title: String) -> Bool {
        remoteByTitle[title] != nil || bundledByTitle[title] != nil
    }

    /// The featured "deep dive" hero article (isFeatured == true), preferring fresh backend
    /// content. nil only if no article anywhere is flagged featured. Flipping the flag
    /// server-side swaps the hero with NO app update.
    func featuredArticle() -> MoneyMoveArticle? {
        remoteFeatured ?? bundledFeatured
    }

    /// Authored catalog cards, ordered by sortOrder. Remote (backend) takes precedence;
    /// any bundled-only article (e.g. shipped but not yet seeded) is appended so nothing
    /// disappears offline. Adding a new article server-side makes a new card appear here
    /// with no app update.
    func cards() -> [MoneyMove] {
        var result = remoteCards
        // Dedup on SLUG, not title. Slug is the article's identity everywhere
        // else in this file (and is the audio-clip key); title is editorial and
        // changes. Keying on title meant that renaming an article server-side
        // stopped matching its bundled copy, so BOTH rendered — the same story
        // twice in one category row under two different names.
        let have = Set(result.map { $0.slug })
        result += bundledCards.filter { !have.contains($0.slug) }
        return result
    }

    /// Fetch article content + narration URLs from the backend once per session.
    ///
    /// Concurrent callers JOIN the in-flight fetch rather than returning early. Setting a
    /// `didPrefetch` flag before the await let a second caller (LearnView pre-fetches on appear;
    /// the user taps "See All" a moment later) sail straight through against still-empty remote
    /// maps and render bundled/placeholder cards for the rest of the session — deterministic, not a
    /// rare race, since both callers hop the same actor.
    func prefetch() async {
        guard !didPrefetch else { return }
        if let inFlight = prefetchTask {
            await inFlight.value
            return
        }
        let task = Task { await self.loadRemote() }
        prefetchTask = task
        await task.value
        prefetchTask = nil
    }

    private func loadRemote() async {
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getMoneyMoves,
                responseType: MoneyMovesAPIResponse.self
            )
            let ordered = response.articles.sorted { ($0.sortOrder ?? .max) < ($1.sortOrder ?? .max) }
            remoteByTitle = [:]
            remoteBySlug = [:]
            remoteCards = []
            remoteFeatured = nil
            for dto in ordered {
                let art = dto.toArticle()
                remoteByTitle[dto.title] = art
                if !dto.slug.isEmpty { remoteBySlug[dto.slug] = art }
                remoteCards.append(dto.toCard())
                if dto.isFeatured == true, remoteFeatured == nil { remoteFeatured = art }
            }
            // Latch ONLY on content that actually landed. A successful-but-empty response (a cold
            // backend cache that degraded to `articles: []`, every article dropped on decode) used
            // to latch the flag anyway and freeze the whole session on bundled content with no
            // retry — a durable outage from one unlucky request.
            didPrefetch = !remoteCards.isEmpty
            if remoteCards.isEmpty {
                print("[MoneyMovesContentStore] remote returned 0 usable articles — staying on bundled content, will retry on the next prefetch.")
            }
        } catch {
            // Stay on bundled content; never block the screen on a network hiccup. But surface the
            // failure loudly + legibly: a DECODE failure here is backend↔iOS contract drift that
            // silently hides just-published content, so it must be diagnosable — not a bare swallow.
            let appError = AppError.from(error)
            print("[MoneyMovesContentStore] remote fetch failed [\(appError.title)]: \(appError.message) — raw: \(error). Falling back to bundled content.")
        }
    }

    // MARK: - Bundled loading

    private func loadBundled() {
        guard let url = Bundle.main.url(forResource: "money_moves", withExtension: "json"),
              let data = try? Data(contentsOf: url) else {
            print("[MoneyMovesContentStore] money_moves.json not found in bundle")
            return
        }
        do {
            let file = try JSONDecoder().decode(MoneyMovesContentFile.self, from: data)
            let ordered = file.articles.sorted { ($0.sortOrder ?? .max) < ($1.sortOrder ?? .max) }
            for dto in ordered {
                let art = dto.toArticle()
                bundledByTitle[dto.title] = art
                if !dto.slug.isEmpty { bundledBySlug[dto.slug] = art }
                bundledCards.append(dto.toCard())
                if dto.isFeatured == true, bundledFeatured == nil { bundledFeatured = art }
            }
        } catch {
            print("[MoneyMovesContentStore] bundled decode failed: \(error)")
        }
    }
}
