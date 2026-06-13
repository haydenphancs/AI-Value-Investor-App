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
    private var didPrefetch = false

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

    /// Fetch article content + narration URLs from the backend once per session.
    func prefetch() async {
        guard !didPrefetch else { return }
        didPrefetch = true
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getMoneyMoves,
                responseType: MoneyMovesAPIResponse.self
            )
            for dto in response.articles {
                remoteByTitle[dto.title] = dto.toArticle()
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
            for dto in file.articles {
                bundledByTitle[dto.title] = dto.toArticle()
            }
        } catch {
            print("[MoneyMovesContentStore] bundled decode failed: \(error)")
        }
    }
}
