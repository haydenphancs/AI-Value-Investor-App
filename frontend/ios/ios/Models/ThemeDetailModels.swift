//
//  ThemeDetailModels.swift
//  ios
//
//  Models for the Emerging Frontiers theme detail screen — decoded from
//  `GET /api/v1/home/themes/{slug}` and mapped to display-ready models so the
//  view stays dumb. Co-located DTO + UI model per the codebase convention.
//
//  The DTOs carry RAW numbers (explicit snake_case CodingKeys — the APIClient
//  decoder does NOT convertFromSnakeCase); `toDisplay()` formats price / percent
//  / market cap and picks the green/red sign here.
//

import SwiftUI

// MARK: - Wire DTOs

/// One constituent company as served by the backend (raw numbers).
struct ThemeConstituentDTO: Decodable {
    let ticker: String
    let companyName: String?
    let price: Double?
    let changePercent: Double?
    let marketCap: Double?

    enum CodingKeys: String, CodingKey {
        case ticker, price
        case companyName = "company_name"
        case changePercent = "change_percent"
        case marketCap = "market_cap"
    }

    func toDisplay() -> ThemeConstituent {
        ThemeConstituent(
            ticker: ticker,
            name: CompanyNameFormatter.clean((companyName?.isEmpty == false) ? companyName! : ticker),
            priceText: ThemeDetailFormat.price(price),
            changeText: changePercent.map { String(format: "%+.2f%%", $0) } ?? "",
            isPositive: (changePercent ?? 0) >= 0,
            marketCapText: ThemeDetailFormat.marketCap(marketCap)
        )
    }
}

/// The full theme drill-down payload.
struct ThemeDetailDTO: Decodable {
    let slug: String
    let title: String
    let subtitle: String?
    let imageUrl: String?
    let accentHex: String
    let constituents: [ThemeConstituentDTO]

    enum CodingKeys: String, CodingKey {
        case slug, title, subtitle, constituents
        case imageUrl = "image_url"
        case accentHex = "accent_hex"
    }

    func toDisplay() -> ThemeDetail {
        ThemeDetail(
            slug: slug,
            title: title,
            subtitle: subtitle ?? "",
            imageUrl: imageUrl,
            accent: Color(hex: accentHex),
            companies: constituents.map { $0.toDisplay() }
        )
    }
}

// MARK: - Display models

/// One row in the theme's "Companies" list (display-ready strings).
struct ThemeConstituent: Identifiable {
    let id = UUID()
    let ticker: String
    let name: String
    let priceText: String       // "$233.45" or "" when unavailable
    let changeText: String      // "+2.10%" or "" (nil change → hidden)
    let isPositive: Bool
    let marketCapText: String   // "3.5T Cap" or ""
}

/// Everything the theme detail screen renders.
struct ThemeDetail {
    let slug: String
    let title: String
    let subtitle: String
    let imageUrl: String?
    let accent: Color
    let companies: [ThemeConstituent]

    var isEmpty: Bool { companies.isEmpty }
}

// MARK: - Formatting

enum ThemeDetailFormat {
    /// Grouped price with two decimals, e.g. `6952.4 → "$6,952.40"`. Empty when
    /// missing / non-positive / non-finite.
    static func price(_ p: Double?) -> String {
        guard let p, p.isFinite, p > 0 else { return "" }
        let n = _priceFormatter.string(from: NSNumber(value: p)) ?? String(format: "%.2f", p)
        return "$" + n
    }

    /// Market cap abbreviated M / B / T with one decimal + " Cap", matching the
    /// scanners: `3.1e12 → "3.1T Cap"`, `4.52e10 → "45.2B Cap"`, `2.6e8 → "260.0M Cap"`.
    static func marketCap(_ cap: Double?) -> String {
        guard let cap, cap.isFinite, cap > 0 else { return "" }
        if cap >= 1_000_000_000_000 { return String(format: "%.1fT Cap", cap / 1_000_000_000_000) }
        if cap >= 1_000_000_000 { return String(format: "%.1fB Cap", cap / 1_000_000_000) }
        return String(format: "%.1fM Cap", cap / 1_000_000)
    }

    private static let _priceFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f
    }()
}
