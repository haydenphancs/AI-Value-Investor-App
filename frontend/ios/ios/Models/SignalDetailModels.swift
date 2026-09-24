//
//  SignalDetailModels.swift
//  ios
//
//  Per-ticker drill-down for the Home "App-Exclusive Signals" cards (Whale
//  Accumulation, Congressional Buys, CEO Buys): WHO bought/added the ticker, WHEN,
//  HOW MUCH. Every kind-dependent string below is a `switch` with an explicit case per
//  kind — a new kind must never fall through into another kind's wording (before CEO
//  Buys, anything that was not "whale" was worded as congress by an `else`).
//  Decoded from `GET /api/v1/home/signals/{kind}/{ticker}` and mapped to a
//  display model with pre-formatted strings the row renders directly.
//
//  Explicit snake_case CodingKeys — the APIClient decoder does NOT use
//  `.convertFromSnakeCase` (a mismatch = a decode crash).
//

import SwiftUI

// MARK: - Wire DTOs

struct SignalTickerDetailDTO: Decodable {
    let symbol: String
    let kind: String                 // "whale" | "congress" | "ceo"
    let companyName: String
    let price: Double?
    let marketCap: Double?
    let asOfDate: String?
    let holders: [SignalHolderDTO]

    enum CodingKeys: String, CodingKey {
        case symbol, kind, price, holders
        case companyName = "company_name"
        case marketCap = "market_cap"
        case asOfDate = "as_of_date"
    }
}

struct SignalHolderDTO: Decodable {
    let whaleId: String?             // non-nil → in our registry → tappable profile
    let name: String
    let subtitle: String             // whale: "13F fund" · congress: "Senator (KY)" · ceo: officer title
    let transactionDate: String?
    let disclosureDate: String?
    let allocationPercent: Double?
    let allocationChange: Double?
    let isNewPosition: Bool?
    let amountEst: Double?           // whale: 13F implied-price $ ESTIMATE · ceo: EXACT shares × price
    let amountRange: String?         // congress filed range "$1,001 – $15,000"
    let owner: String?
    let shares: Double?              // ceo: shares bought (the average price is amountEst ÷ shares)
    let action: String

    enum CodingKeys: String, CodingKey {
        case name, subtitle, owner, action, shares
        case whaleId = "whale_id"
        case transactionDate = "transaction_date"
        case disclosureDate = "disclosure_date"
        case allocationPercent = "allocation_percent"
        case allocationChange = "allocation_change"
        case isNewPosition = "is_new_position"
        case amountEst = "amount_est"
        case amountRange = "amount_range"
    }

    // Decode-safe: the backend always sends `subtitle`/`action` (Pydantic defaults),
    // but if a future change or proxy omits one, `decodeIfPresent` defaults it rather
    // than throwing keyNotFound and nuking the WHOLE holder-list decode.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        whaleId = try c.decodeIfPresent(String.self, forKey: .whaleId)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? ""
        transactionDate = try c.decodeIfPresent(String.self, forKey: .transactionDate)
        disclosureDate = try c.decodeIfPresent(String.self, forKey: .disclosureDate)
        allocationPercent = try c.decodeIfPresent(Double.self, forKey: .allocationPercent)
        allocationChange = try c.decodeIfPresent(Double.self, forKey: .allocationChange)
        isNewPosition = try c.decodeIfPresent(Bool.self, forKey: .isNewPosition)
        amountEst = try c.decodeIfPresent(Double.self, forKey: .amountEst)
        amountRange = try c.decodeIfPresent(String.self, forKey: .amountRange)
        owner = try c.decodeIfPresent(String.self, forKey: .owner)
        shares = try c.decodeIfPresent(Double.self, forKey: .shares)
        action = try c.decodeIfPresent(String.self, forKey: .action) ?? "BOUGHT"
    }
}

// MARK: - Display model (pre-formatted; the row renders these directly)

struct SignalHolder: Identifiable {
    let id = UUID()
    let whaleId: String?             // non-nil → tappable → WhaleProfileView
    let name: String
    let subtitle: String
    let dateText: String             // "Filed Jun 30" / "Traded Jun 1 · Disclosed Jun 30"
    let primaryText: String          // right headline (allocation move / range)
    let secondaryText: String        // right secondary (~$ est / owner)
    var isTappable: Bool { whaleId != nil }
}

struct SignalTickerDetail {
    let symbol: String
    let kind: String                 // "whale" | "congress" | "ceo"
    let companyName: String
    let priceText: String            // "" when unavailable
    let marketCapText: String        // "45.2B Cap" or ""
    let holders: [SignalHolder]

    var isEmpty: Bool { holders.isEmpty }
    /// Header sub-line under the ticker.
    var subtitleLine: String {
        switch kind {
        case "whale": return "Funds accumulating"
        case "ceo": return "CEO open-market buys"
        default: return "Members buying"
        }
    }
    /// Honest empty-state copy.
    var emptyText: String {
        switch kind {
        case "whale": return "No tracked funds are currently adding \(symbol)."
        case "ceo": return "No CEO open-market purchases of \(symbol) in the last 30 days."
        default: return "No congress members bought \(symbol) in the last 30 days."
        }
    }
}

// MARK: - DTO → display mapping

extension SignalTickerDetailDTO {
    func toDisplay() -> SignalTickerDetail {
        SignalTickerDetail(
            symbol: symbol,
            kind: kind,
            companyName: companyName,
            priceText: SignalDetailFormat.price(price),
            marketCapText: SignalDetailFormat.marketCap(marketCap),
            holders: holders.map { $0.toDisplay(kind: kind) }
        )
    }
}

extension SignalHolderDTO {
    func toDisplay(kind: String) -> SignalHolder {
        switch kind {
        case "whale":
            return SignalHolder(
                whaleId: whaleId,
                name: name,
                subtitle: subtitle,
                dateText: SignalDetailFormat.whaleDate(disclosureDate ?? transactionDate),
                primaryText: SignalDetailFormat.whalePrimary(
                    allocationPercent: allocationPercent,
                    allocationChange: allocationChange,
                    isNew: isNewPosition
                ),
                secondaryText: SignalDetailFormat.whaleSecondary(amountEst)
            )
        case "ceo":
            // CEOs are never registry whales, so the row is never tappable. The dollar
            // figure is EXACT (shares × the reported price), so no "~ … est." hedge.
            return SignalHolder(
                whaleId: nil,
                name: name,
                subtitle: subtitle,
                dateText: SignalDetailFormat.insiderDate(traded: transactionDate, filed: disclosureDate),
                primaryText: amountEst.map(SignalDollarFormat.compact) ?? "",
                secondaryText: SignalDollarFormat.sharesAtPrice(shares: shares, amount: amountEst) ?? ""
            )
        default:
            return SignalHolder(
                whaleId: whaleId,
                name: name,
                subtitle: subtitle,
                dateText: SignalDetailFormat.congressDate(
                    traded: transactionDate, disclosed: disclosureDate
                ),
                primaryText: amountRange ?? "",
                secondaryText: owner ?? ""
            )
        }
    }
}

// MARK: - Formatting helpers

enum SignalDetailFormat {
    static func price(_ p: Double?) -> String {
        guard let p, p.isFinite, p > 0 else { return "" }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return "$" + (f.string(from: NSNumber(value: p)) ?? String(format: "%.2f", p))
    }

    /// "45.2B Cap" / "3.1T Cap" / "500.5M Cap"; "" when missing.
    static func marketCap(_ cap: Double?) -> String {
        guard let cap, cap.isFinite, cap > 0 else { return "" }
        if cap >= 1_000_000_000_000 { return String(format: "%.1fT Cap", cap / 1_000_000_000_000) }
        if cap >= 1_000_000_000 { return String(format: "%.1fB Cap", cap / 1_000_000_000) }
        return String(format: "%.1fM Cap", cap / 1_000_000)
    }

    /// Compact dollars: "~$2.4M est." / "~$920K est."; "" when missing.
    static func whaleSecondary(_ amount: Double?) -> String {
        guard let amount, amount.isFinite, amount > 0 else { return "" }
        let short: String
        if amount >= 1_000_000_000 { short = String(format: "$%.2fB", amount / 1_000_000_000) }
        else if amount >= 1_000_000 { short = String(format: "$%.1fM", amount / 1_000_000) }
        else if amount >= 1_000 { short = String(format: "$%.0fK", amount / 1_000) }
        else { short = String(format: "$%.0f", amount) }
        return "~\(short) est."
    }

    /// "3.1% · New" / "3.1% · +1.2pts" / "New" — allocation weight + move.
    static func whalePrimary(allocationPercent: Double?, allocationChange: Double?, isNew: Bool?) -> String {
        var parts: [String] = []
        if let a = allocationPercent, a.isFinite, a > 0 {
            parts.append(String(format: "%.1f%%", a))
        }
        if isNew == true {
            parts.append("New")
        } else if let c = allocationChange, c.isFinite, abs(c) >= 0.05 {
            parts.append(String(format: "%+.1fpts", c))
        }
        return parts.joined(separator: " · ")
    }

    static func whaleDate(_ iso: String?) -> String {
        guard let d = shortDate(iso) else { return "" }
        return "Filed \(d)"
    }

    /// "Traded Sep 18 · Filed Sep 20" — a Form 4 is FILED, not "disclosed" like a PTR.
    static func insiderDate(traded: String?, filed: String?) -> String {
        var parts: [String] = []
        if let t = shortDate(traded) { parts.append("Traded \(t)") }
        if let f = shortDate(filed) { parts.append("Filed \(f)") }
        return parts.joined(separator: " · ")
    }

    static func congressDate(traded: String?, disclosed: String?) -> String {
        var parts: [String] = []
        if let t = shortDate(traded) { parts.append("Traded \(t)") }
        if let d = shortDate(disclosed) { parts.append("Disclosed \(d)") }
        return parts.joined(separator: " · ")
    }

    // "yyyy-MM-dd" → "MMM d" in a fixed zone (calendar date never shifts with device TZ).
    private static let isoParser: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    private static let shortDisplay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "MMM d"
        return f
    }()
    private static func shortDate(_ iso: String?) -> String? {
        guard let iso, let d = isoParser.date(from: String(iso.prefix(10))) else { return nil }
        return shortDisplay.string(from: d)
    }
}
