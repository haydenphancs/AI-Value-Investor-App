//
//  ChatStartersModels.swift
//  ios
//
//  DTOs for GET /api/v1/chat/starters — the daily-rotating suggestion chips shown on the
//  empty chat state and on the five asset detail AI bars.
//
//  Co-located per the house convention: wire types and the UI-facing helpers live in one
//  file per feature. Every field is optional or defaulted, matching the backend contract:
//  that endpoint degrades rather than failing, so a body missing a section is a normal
//  response and must never be a decode error.
//

import Foundation
import OSLog

// MARK: - Wire

struct ChatStarterDTO: Decodable, Equatable {
    let text: String
    /// `hot_ticker` | `hot_sector` | `hot_topic` | `trending` | `fixed` | `evergreen`.
    /// A plain String, not an enum: an unknown value from a newer server must degrade,
    /// not fail the whole decode.
    let kind: String
    /// Present only on `hot_ticker` / `trending`. Carried separately so the client never
    /// has to parse a symbol back out of a sentence.
    let symbol: String?

    enum CodingKeys: String, CodingKey {
        case text, kind, symbol
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decode(String.self, forKey: .text)
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "evergreen"
        symbol = try? c.decodeIfPresent(String.self, forKey: .symbol)
    }

    init(text: String, kind: String = "evergreen", symbol: String? = nil) {
        self.text = text
        self.kind = kind
        self.symbol = symbol
    }
}

struct DetailStarterSetDTO: Decodable, Equatable {
    let ticker: [String]
    let etf: [String]
    let crypto: [String]
    let commodity: [String]
    let index: [String]

    enum CodingKeys: String, CodingKey {
        case ticker, etf, crypto, commodity, index
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ticker = (try? c.decode([String].self, forKey: .ticker)) ?? []
        etf = (try? c.decode([String].self, forKey: .etf)) ?? []
        crypto = (try? c.decode([String].self, forKey: .crypto)) ?? []
        commodity = (try? c.decode([String].self, forKey: .commodity)) ?? []
        index = (try? c.decode([String].self, forKey: .index)) ?? []
    }

    init(ticker: [String] = [], etf: [String] = [], crypto: [String] = [],
         commodity: [String] = [], index: [String] = []) {
        self.ticker = ticker
        self.etf = etf
        self.crypto = crypto
        self.commodity = commodity
        self.index = index
    }

    func templates(for scope: ChatStarterScope) -> [String] {
        switch scope {
        case .ticker: return ticker
        case .etf: return etf
        case .crypto: return crypto
        case .commodity: return commodity
        case .index: return index
        }
    }
}

struct ChatStartersDTO: Decodable, Equatable {
    /// ET calendar date the selection was computed for. The store uses it as the refetch
    /// key so an app left open across midnight picks up the new set.
    let tradingDate: String
    let globalStarters: [ChatStarterDTO]
    let detailStarters: DetailStarterSetDTO

    enum CodingKeys: String, CodingKey {
        case tradingDate = "trading_date"
        case globalStarters = "global_starters"
        case detailStarters = "detail_starters"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradingDate = (try? c.decode(String.self, forKey: .tradingDate)) ?? ""
        globalStarters = (try? c.decode([ChatStarterDTO].self, forKey: .globalStarters)) ?? []
        detailStarters =
            (try? c.decode(DetailStarterSetDTO.self, forKey: .detailStarters))
            ?? DetailStarterSetDTO()
    }

    init(tradingDate: String = "", globalStarters: [ChatStarterDTO] = [],
         detailStarters: DetailStarterSetDTO = DetailStarterSetDTO()) {
        self.tradingDate = tradingDate
        self.globalStarters = globalStarters
        self.detailStarters = detailStarters
    }
}

// MARK: - Scope

/// The five asset detail bars. Mirrors the backend's closed `scope` vocabulary
/// (migration 161's CHECK constraint) minus `global`, which has its own field.
enum ChatStarterScope: String, CaseIterable {
    case ticker, etf, crypto, commodity, index
}

// MARK: - Bundled fallback

/// The offline floor. Decoded from the JSON bundled at
/// `Resources/ChatStarters/chat_starters.json` — the same authored file the backend
/// seeds `public.chat_starters` from, kept byte-identical by
/// `tests/test_chat_starters_catalog.py`.
///
/// This exists so the chip row is never empty: a cold launch with no network, a signed-out
/// user, or a backend outage all still get a full row of questions.
struct BundledChatStarters: Decodable {
    let global: [String]
    let detail: [String: [String]]

    static let shared: BundledChatStarters = load()

    private static func load() -> BundledChatStarters {
        guard
            let url = Bundle.main.url(forResource: "chat_starters", withExtension: "json"),
            let data = try? Data(contentsOf: url),
            let decoded = try? JSONDecoder().decode(BundledChatStarters.self, from: data)
        else {
            // Not fatal, but it does mean the offline floor is gone — say so loudly rather
            // than shipping an empty row that looks like a layout bug.
            Logger(subsystem: "com.phan.caydex", category: "chat-starters")
                .error("bundled catalogue missing or unreadable — the chip row now depends entirely on the network")
            return BundledChatStarters(global: [], detail: [:])
        }
        return decoded
    }

    func templates(for scope: ChatStarterScope) -> [String] {
        detail[scope.rawValue] ?? []
    }
}
