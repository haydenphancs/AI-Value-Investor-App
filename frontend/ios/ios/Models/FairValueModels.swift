//
//  FairValueModels.swift
//  ios
//
//  The Caydex Fair Value Estimate (backend model `dcf-v1`,
//  documents/research/dcf-methodology-v1.md): DTO + display model, co-located.
//
//  ⚠️ Wording rules (spec §5) — the reason this file exists as ONE place:
//  • it is a MODEL ESTIMATE, always shown with its range, never a price target;
//  • the gap is stated as "Price N% below/above the estimate" — never Undervalued /
//    Overvalued / Buy / Sell (backend/tests/test_ios_fair_value.py pins it);
//  • the value is the same for every user; nothing here may take user, tier or persona.
//
//  Every field is Optional on the wire: a build that predates a field, or a refusal with
//  no numbers, still decodes. An unknown `status` from a future backend is NO row.
//

import Foundation

// MARK: - DTO

struct CaydexFairValueDTO: Codable, Equatable {
    let symbol: String
    let status: String
    let modelVersion: String?
    let refusalCode: String?
    let refusalReason: String?
    let fairValue: Double?
    let rangeLow: Double?
    let rangeHigh: Double?
    let alternativeValue: Double?
    let currency: String?
    let method: String?
    let discountRatePct: Double?
    let terminalGrowthPct: Double?
    let riskFreePct: Double?
    let equityRiskPremiumPct: Double?
    let beta: Double?
    let analystYears: Int?
    let analystsMin: Int?
    let terminalSharePct: Double?
    let cashConversion: Double?
    let fcfMarginPct: Double?
    let sbcStatus: String?
    let sharesDiluted: Double?
    let lastReportedFiscalYearEnd: String?
    let asOf: String?
    let notes: [String]?

    enum CodingKeys: String, CodingKey {
        case symbol, status, currency, method, beta, notes
        case modelVersion = "model_version"
        case refusalCode = "refusal_code"
        case refusalReason = "refusal_reason"
        case fairValue = "fair_value"
        case rangeLow = "range_low"
        case rangeHigh = "range_high"
        case alternativeValue = "alternative_value"
        case discountRatePct = "discount_rate_pct"
        case terminalGrowthPct = "terminal_growth_pct"
        case riskFreePct = "risk_free_pct"
        case equityRiskPremiumPct = "equity_risk_premium_pct"
        case analystYears = "analyst_years"
        case analystsMin = "analysts_min"
        case terminalSharePct = "terminal_share_pct"
        case cashConversion = "cash_conversion"
        case fcfMarginPct = "fcf_margin_pct"
        case sbcStatus = "sbc_status"
        case sharesDiluted = "shares_diluted"
        case lastReportedFiscalYearEnd = "last_reported_fiscal_year_end"
        case asOf = "as_of"
    }
}

// MARK: - Display model

struct CaydexFairValue: Equatable {
    enum State: Equatable {
        case estimate(value: Double, low: Double, high: Double)
        case refused(reason: String)
    }

    struct Assumption: Equatable, Hashable {
        let label: String
        let value: String
    }

    static let title = "Caydex Fair Value Estimate"
    static let subtitle = "DCF model estimate · not a price target · not a recommendation"
    static let refusedGeneric = "Our model doesn't produce an estimate for this company."
    /// The label over the range, which is the headline: the estimate is its middle mark.
    static let rangeLabel = "Estimate range"
    static let refusedHeadline = "Not modelled"
    /// A report saved before the estimate existed, or one served while it is switched off.
    static let notInReport = "No Caydex Fair Value Estimate is available for this report."

    let state: State
    let alternativeValue: Double?
    let assumptions: [Assumption]
    let asOf: String?
    let notes: [String]

    init(state: State, alternativeValue: Double? = nil, assumptions: [Assumption] = [],
         asOf: String? = nil, notes: [String] = []) {
        self.state = state
        self.alternativeValue = alternativeValue
        self.assumptions = assumptions
        self.asOf = asOf
        self.notes = notes
    }

    init?(dto: CaydexFairValueDTO) {
        switch dto.status {
        case "ok":
            // All three numbers must be positive and finite, and ordered — otherwise it is
            // not an estimate we can show honestly.
            guard let v = Self.positive(dto.fairValue),
                  let lo = Self.positive(dto.rangeLow),
                  let hi = Self.positive(dto.rangeHigh),
                  lo <= v, v <= hi else { return nil }
            self.state = .estimate(value: v, low: lo, high: hi)
        case "refused":
            let reason = (dto.refusalReason ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            self.state = .refused(reason: reason.isEmpty ? Self.refusedGeneric : reason)
        default:
            return nil
        }
        self.alternativeValue = Self.positive(dto.alternativeValue)
        // A refusal has no estimate, so it has no assumptions to show — only its date.
        if case .estimate = state {
            self.assumptions = Self.assumptions(from: dto)
        } else {
            self.assumptions = dto.asOf.map { [Assumption(label: "Checked on", value: $0)] } ?? []
        }
        self.asOf = dto.asOf
        self.notes = (dto.notes ?? []).filter { !$0.isEmpty }
    }

    var isEstimate: Bool {
        if case .estimate = state { return true }
        return false
    }

    var value: Double? {
        if case let .estimate(v, _, _) = state { return v }
        return nil
    }

    var formattedValue: String? { value.map(Self.money) }

    var formattedRange: String? {
        guard case let .estimate(_, lo, hi) = state else { return nil }
        return "Range \(Self.money(lo)) – \(Self.money(hi))"
    }

    /// Low, estimate and high together, or nil. The chart reads ONLY this, so it can never
    /// draw an estimate without its range.
    var bounds: (low: Double, value: Double, high: Double)? {
        guard case let .estimate(v, lo, hi) = state else { return nil }
        return (lo, v, hi)
    }

    /// The headline: "$187.17 – $269.29".
    var formattedRangeBounds: String? {
        guard let b = bounds else { return nil }
        return "\(Self.money(b.low)) – \(Self.money(b.high))"
    }

    /// The range's middle mark: "Estimate $229.53".
    var formattedEstimate: String? {
        value.map { "Estimate \(Self.money($0))" }
    }

    /// VoiceOver does not reliably read an en dash as "to".
    var rangeAccessibilityLabel: String? {
        guard let b = bounds else { return nil }
        return "Estimate range from \(Self.money(b.low)) to \(Self.money(b.high))"
    }

    /// Where a price sits against the range, for the chart's VoiceOver label. Neutral: a
    /// position, never a verdict.
    func pricePosition(of price: Double?) -> String? {
        guard let b = bounds, let price, price.isFinite, price > 0 else { return nil }
        if price > b.high { return "above the estimate range" }
        if price < b.low { return "below the estimate range" }
        return "within the estimate range"
    }

    /// Percent gap of PRICE versus the estimate (negative = price below the estimate).
    /// `nil` unless both are positive finite numbers — never a fabricated 0%.
    func priceGapPercent(versus price: Double?) -> Double? {
        guard let value, let price, price.isFinite, price > 0 else { return nil }
        return (price / value - 1) * 100
    }

    /// "Price 18% below the estimate" / "Price 6% above the estimate" /
    /// "Price in line with the estimate". Never a verdict word.
    func formattedGap(versus price: Double?) -> String? {
        guard let gap = priceGapPercent(versus: price) else { return nil }
        let magnitude = Int(abs(gap).rounded())
        if magnitude == 0 { return "Price in line with the estimate" }
        return "Price \(magnitude)% \(gap < 0 ? "below" : "above") the estimate"
    }

    // MARK: - Helpers

    private static func positive(_ v: Double?) -> Double? {
        guard let v, v.isFinite, v > 0 else { return nil }
        return v
    }

    private static let moneyFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = 2
        f.minimumFractionDigits = 2
        return f
    }()

    static func money(_ v: Double) -> String {
        moneyFormatter.string(from: NSNumber(value: v)) ?? String(format: "$%.2f", v)
    }

    private static let wholeMoneyFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = 0
        f.minimumFractionDigits = 0
        return f
    }()

    /// "$5,812" — for a chart badge, where cents do not fit.
    static func wholeMoney(_ v: Double) -> String {
        wholeMoneyFormatter.string(from: NSNumber(value: v)) ?? String(format: "$%.0f", v)
    }

    private static let isoDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let monthYearFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "MMM yyyy"
        return f
    }()

    /// "Price · Sep 2024 – Sep 2026" from the first and last "yyyy-MM-dd" dates of the series
    /// a chart draws, so the label says the window the line actually covers. Shared by the
    /// report and the Analysis tab. nil when either date does not parse.
    static func pricePeriodLabel(from first: String?, to last: String?) -> String? {
        guard let first, let last,
              let start = isoDayFormatter.date(from: String(first.prefix(10))),
              let end = isoDayFormatter.date(from: String(last.prefix(10))) else { return nil }
        let a = monthYearFormatter.string(from: start)
        let b = monthYearFormatter.string(from: end)
        return a == b ? "Price · \(a)" : "Price · \(a) – \(b)"
    }

    private static func pct(_ v: Double?, digits: Int = 2) -> String? {
        guard let v, v.isFinite else { return nil }
        return String(format: "%.\(digits)f%%", v)
    }

    private static func assumptions(from d: CaydexFairValueDTO) -> [Assumption] {
        var out: [Assumption] = []
        func add(_ label: String, _ value: String?) {
            if let value { out.append(Assumption(label: label, value: value)) }
        }
        add("Method", d.method.map { $0.prefix(1).uppercased() + $0.dropFirst() })
        add("Discount rate (cost of equity)", pct(d.discountRatePct))
        add("Risk-free rate", pct(d.riskFreePct))
        add("Equity risk premium", pct(d.equityRiskPremiumPct))
        add("Beta", d.beta.flatMap { $0.isFinite ? String(format: "%.2f", $0) : nil })
        add("Terminal growth", pct(d.terminalGrowthPct))
        if let years = d.analystYears {
            let who = d.analystsMin.map { ", at least \($0) analysts each" } ?? ""
            add("Analyst forecast years", "\(years) of 10\(who)")
        }
        add("Share of value after year 10", pct(d.terminalSharePct, digits: 0))
        // Labelled by the formula actually used (spec §1.1-1.2): with stock-based pay deducted,
        // conversion is (FCF − stock pay) ÷ (earnings + stock pay).
        let deducted = d.sbcStatus == "deducted"
        add(deducted ? "Cash conversion ((FCF − stock pay) ÷ (earnings + stock pay))"
                     : "Cash conversion (FCF ÷ earnings)",
            d.cashConversion.flatMap { $0.isFinite ? String(format: "%.2f", $0) : nil })
        add(deducted ? "Free-cash-flow margin after stock pay" : "Free-cash-flow margin",
            pct(d.fcfMarginPct, digits: 1))
        switch d.sbcStatus {
        case "deducted": add("Stock-based pay", "Counted as a cost")
        case "not_reported": add("Stock-based pay", "Not reported by our data source")
        default: break
        }
        add("Revenue-based cross-check", positive(d.alternativeValue).map(money))
        add("Last reported fiscal year", d.lastReportedFiscalYearEnd)
        add("Estimated on", d.asOf)
        return out
    }
}

// MARK: - Preview samples

extension CaydexFairValue {
    static let sampleEstimate = CaydexFairValue(
        state: .estimate(value: 229.53, low: 187.17, high: 269.29),
        alternativeValue: 210.39,
        assumptions: [
            Assumption(label: "Discount rate (cost of equity)", value: "8.75%"),
            Assumption(label: "Terminal growth", value: "3.82%"),
            Assumption(label: "Stock-based pay", value: "Counted as a cost"),
        ],
        asOf: "2026-09-25"
    )

    static let sampleRefused = CaydexFairValue(
        state: .refused(reason: "Banks, insurers, asset managers and shell companies don't fit a cash-flow model."),
        assumptions: [Assumption(label: "Checked on", value: "2026-09-25")],
        asOf: "2026-09-25"
    )
}
