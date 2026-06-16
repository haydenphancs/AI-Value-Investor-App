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
    // Ordered card lists (by sortOrder) so the catalog can be served from content
    // instead of hardcoded in the view. Remote is authoritative; bundled is the
    // offline fallback for whatever shipped in the binary.
    private var bundledCards: [MoneyMove] = []
    private var remoteCards: [MoneyMove] = []
    private var didPrefetch = false

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
        let have = Set(result.map { $0.title })
        result += bundledCards.filter { !have.contains($0.title) }
        return result
    }

    /// Fetch article content + narration URLs from the backend once per session.
    func prefetch() async {
        guard !didPrefetch else { return }
        didPrefetch = true
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getMoneyMoves,
                responseType: MoneyMovesAPIResponse.self
            )
            let ordered = response.articles.sorted { ($0.sortOrder ?? .max) < ($1.sortOrder ?? .max) }
            remoteByTitle = [:]
            remoteCards = []
            remoteFeatured = nil
            for dto in ordered {
                let art = dto.toArticle()
                remoteByTitle[dto.title] = art
                remoteCards.append(dto.toCard())
                if dto.isFeatured == true, remoteFeatured == nil { remoteFeatured = art }
            }
        } catch {
            // Stay on bundled content; never block the screen on a network hiccup.
            didPrefetch = false
            print("[MoneyMovesContentStore] backend fetch failed, using bundled content: \(error)")
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
                bundledCards.append(dto.toCard())
                if dto.isFeatured == true, bundledFeatured == nil { bundledFeatured = art }
            }
        } catch {
            print("[MoneyMovesContentStore] bundled decode failed: \(error)")
        }
    }
}
