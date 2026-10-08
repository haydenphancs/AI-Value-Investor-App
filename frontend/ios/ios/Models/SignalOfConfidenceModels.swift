//
//  SignalOfConfidenceModels.swift
//  ios
//
//  Data models for the Signal of Confidence Section in the Financial tab
//  Displays dividends, buybacks, and shares outstanding over time
//

import Foundation
import SwiftUI

// MARK: - Signal of Confidence View Type

enum SignalOfConfidenceViewType: String, CaseIterable, Identifiable {
    case yield = "Yield (%)"
    case capital = "Capital ($)"

    var id: String { rawValue }
}

// MARK: - Signal of Confidence Metric Type

enum SignalOfConfidenceMetricType: String, CaseIterable, Identifiable {
    case dividends = "Dividends"
    case buybacks = "Buybacks"
    case sharesOutstanding = "Shares Outstanding"

    var id: String { rawValue }

    var color: Color {
        switch self {
        case .dividends: return AppColors.confidenceDividends
        case .buybacks: return AppColors.confidenceBuybacks
        case .sharesOutstanding: return AppColors.confidenceSharesOutstanding
        }
    }

    var isLine: Bool {
        self == .sharesOutstanding
    }

    var description: String {
        switch self {
        case .dividends:
            return "Cash payments made to shareholders, typically quarterly. A consistent or growing dividend signals financial stability and management confidence."
        case .buybacks:
            return "When a company repurchases its own shares, reducing shares outstanding. This returns cash to shareholders and can boost earnings per share."
        case .sharesOutstanding:
            return "Total number of shares held by all shareholders. Decreasing share count from buybacks is generally positive; increasing count from dilution is concerning."
        }
    }
}

// MARK: - Signal of Confidence Data Point

struct SignalOfConfidenceDataPoint: Identifiable {
    let id = UUID()
    let period: String                    // e.g., "Q2 '24", "Q3 '24"
    /// Trailing-12-month yield at this quarter's end, % (1.3 = 1.3%): the four quarters'
    /// cash over the market cap at THIS period end. The quarter x4 turned cash-settlement
    /// timing into fake swings (KO 0.12% → 5.85% on a 2.95% yield), so it survives only
    /// where four consecutive quarters of cash flow are not on file — unmarked on the wire,
    /// which is why the card's caption names both bases.
    let dividendYield: Double
    let buybackYield: Double              // Trailing-12-month, same basis
    let dividendAmount: Double            // Dollar amount in millions
    let buybackAmount: Double             // Dollar amount in millions
    /// In millions (e.g. 150 for 150M). NIL when the filing did not report it.
    ///
    /// Not `0.0`: no listed company has zero weighted-average shares, so a zero was
    /// indistinguishable from a measurement — it plunged the shares line to the axis and
    /// made the summary read a flat -100% share-count change (a spectacular fake
    /// buyback). Same reasoning, and the same fix, as ProfitPower's `Double?` margins.
    /// Also nil for an interior one-quarter vendor artifact the server refuses (FMP copies the
    /// ANNUAL average into fiscal-Q4 rows: CRWV Q4 '25 read 435M between 498M and 527M). The
    /// newest point is never nil for that reason — the server ships the count that quarter's
    /// own EPS implies — because build 1.0 (10) prints a nil newest count as "0.00M".
    let sharesOutstanding: Double?
    /// The market cap (USD millions) this quarter's yields were divided by, or nil (an older
    /// backend, or no usable cap). Only the Capital ($) view's scale reads it — never shown —
    /// so an immaterial amount cannot fill the chart (TestFlight 1.0 (11), CRWV: a $2.6M bar
    /// on a ~$50B company towered over a "$0–$3M" axis). Defaulted and declared before
    /// `cashFlowReported`, so every memberwise init and preview compiles unchanged.
    var marketCap: Double? = nil
    /// False when no cash-flow figures are on hand for this quarter: the vendor has no
    /// cash-flow filing on record for it (an interior or leading-edge hole in the history)
    /// or — for the whole series — the cash-flow fetch failed (`degraded` carries
    /// "cash_flow"), so nothing user-facing may name the cause. The four cash fields above
    /// are then 0.0 PLACEHOLDERS — non-Optional on the wire, because shipped builds decode them as
    /// `Double` — not a measured "paid nothing": every text rendering of them prints "—".
    /// The bars still draw (a zero-height bar is what a measured zero already looks like),
    /// so every column stays aligned with its index-positioned label rows.
    ///
    /// The LAST stored property, defaulted, so every memberwise init and preview compiles
    /// unchanged; the DTO maps a missing key (older backend, cached payload) to true.
    var cashFlowReported: Bool = true

    /// Total shareholder yield (dividend + buyback)
    var totalYield: Double {
        dividendYield + buybackYield
    }

    /// Total capital returned in millions
    var totalCapitalReturned: Double {
        dividendAmount + buybackAmount
    }
}

// MARK: - Signal of Confidence Summary

struct SignalOfConfidenceSummary {
    let totalYield: Double                // Total shareholder yield percentage
    let dividendYield: Double             // Dividend portion
    let buybackYield: Double              // Buyback portion
    let shareCountChange: Double          // Percentage change (negative = buybacks reducing count)
    // Always present, unlike `DividendInfo.buybackStatus`, which is nil for every
    // company that pays no dividend. Defaulted so existing call sites and previews
    // keep compiling.
    var buybackStatus: BuybackStatus = .low
    /// False when fewer than two quarters reported a share count. `shareCountChange` is
    /// then a 0.0 placeholder (non-Optional on the wire), NOT a measured "unchanged" — the
    /// card and Cay AI used to present it as one. Defaulted so existing inits compile.
    var shareCountChangeKnown: Bool = true

    var shareCountDescription: String {
        guard shareCountChangeKnown else {
            return "Share count change: not reported."
        }
        if shareCountChange < 0 {
            return "Share count decrease by \(String(format: "%.1f", abs(shareCountChange)))%."
        } else if shareCountChange > 0 {
            return "Share count increase by \(String(format: "%.1f", shareCountChange))%."
        } else {
            return "Share count unchanged."
        }
    }

    var formattedSummary: String {
        "Total Yield: \(String(format: "%.1f", totalYield))% (\(String(format: "%.1f", dividendYield))% Dividends + \(String(format: "%.1f", buybackYield))% Buyback)."
    }
}

// MARK: - Dividend Yield Status

enum DividendYieldStatus: String {
    case low = "Low"
    case fair = "Fair"
    case high = "High"
    case veryHigh = "Very High"

    var color: Color {
        switch self {
        case .low: return AppColors.bearish
        case .fair: return AppColors.neutral
        case .high: return AppColors.bullish
        case .veryHigh: return AppColors.primaryBlue
        }
    }

    static func from(yield: Double, industryAverage: Double) -> DividendYieldStatus {
        let ratio = yield / industryAverage
        if ratio < 0.7 { return .low }
        if ratio < 1.0 { return .fair }
        if ratio < 1.5 { return .high }
        return .veryHigh
    }
}

// MARK: - Buyback Status

enum BuybackStatus: String {
    case diluting = "Diluting"
    case dilutingMild = "Diluting (Mild)"
    case low = "Low"
    case moderate = "Moderate"
    case high = "High"
    case veryHigh = "Very High"

    var color: Color {
        switch self {
        case .diluting: return AppColors.bearish
        case .dilutingMild: return AppColors.bearish
        case .low: return AppColors.bearish
        case .moderate: return AppColors.neutral
        case .high: return AppColors.bullish
        case .veryHigh: return AppColors.primaryBlue
        }
    }
}

// MARK: - Dividend Info

/// Dividends paid per share in one completed fiscal year.
///
/// ⚠️ `perShare == 0` is a MEASUREMENT, not a gap: the company paid nothing that year.
/// Intel's series ends `0.3736, 0.0000` because it suspended its dividend, and that final
/// zero is the most informative point in it. Years before a company ever paid are trimmed
/// server-side, so a leading zero never reaches here and the two cannot be confused.
struct AnnualDividend: Identifiable {
    let year: String
    let perShare: Double

    var id: String { year }

    /// Four decimals: real payouts run from NVDA's $0.0160 to XOM's $4.0026, and rounding
    /// to cents would render a token dividend as $0.02 or, worse, $0.00.
    var formatted: String { String(format: "$%.4f", perShare) }
}

struct DividendInfo {
    let exDividendDate: Date?
    let paymentDate: Date?
    /// Optional because it is genuinely unknown for a company with too little history —
    /// the backend leaves it at 0.0 there, and "0.00%" on a trailing-average row reads as
    /// a measured fact rather than an absence. Mapped to nil at the repository boundary
    /// when the backend sends 0.
    let fiveYearAvgYield: Double?
    let status: DividendYieldStatus
    let buybackStatus: BuybackStatus

    // ── Annual dividend amounts ──────────────────────────────────────────────────
    // From the entitled `ratios` (period=annual). Defaulted so every existing
    // construction site — previews, mocks, the buyback-only path — still compiles.
    var annualDividends: [AnnualDividend] = []
    /// Latest completed fiscal year's dividend per share.
    var perShare: Double? = nil
    var perShareYear: String? = nil
    /// Total growth across the series. **Optional because it is often undefined**, and
    /// zero would be a lie: a company that started paying inside the window (GOOGL, META,
    /// both 2024) has no growth RATE. A company that cut to nothing does, and it is -100%.
    var growthPct: Double? = nil
    /// How many years the growth figure spans, so a label cannot claim "5Y" over one year.
    var growthYears: Int? = nil
    /// The window `fiveYearAvgYield` actually spans, from the backend's `avg_yield_window`
    /// ("8Q" = eight quarterly trailing-12-month yields; "5Y" only on the annual fallback).
    /// The field is named five-year for DTO compatibility, and the card used to print
    /// "5Y Avg Yield" over at most two years of data. nil → a window-neutral label.
    var avgYieldWindowLabel: String? = nil

    /// Row label for `fiveYearAvgYield`: "Avg Dividend Yield (2Y)" for "8Q", "(6Q)" for a
    /// count that is not whole years, and a window-neutral "Trailing Avg Dividend Yield"
    /// when the backend did not say (an older payload) — never a claimed "5Y".
    var averageYieldLabel: String {
        guard let raw = avgYieldWindowLabel?.trimmingCharacters(in: .whitespaces),
              raw.count >= 2,
              let n = Int(raw.dropLast()), n > 0 else {
            return "Trailing Avg Dividend Yield"
        }
        if raw.hasSuffix("Q") {
            return n % 4 == 0
                ? "Avg Dividend Yield (\(n / 4)Y)"
                : "Avg Dividend Yield (\(n)Q)"
        }
        if raw.hasSuffix("Y") {
            return "Avg Dividend Yield (\(n)Y)"
        }
        return "Trailing Avg Dividend Yield"
    }

    var formattedPerShare: String {
        guard let v = perShare else { return "—" }
        return String(format: "$%.4f", v)
    }

    /// e.g. "+27.1% over 5y", or nil when there is no defined rate to show.
    var formattedGrowth: String? {
        guard let pct = growthPct, let years = growthYears, years > 0 else { return nil }
        return String(format: "%+.1f%% over %dy", pct, years)
    }

    var formattedExDividendDate: String {
        guard let date = exDividendDate else { return "N/A" }
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: date)
    }

    var formattedPaymentDate: String {
        guard let date = paymentDate else { return "N/A" }
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: date)
    }

    var formattedYield: String {
        guard let v = fiveYearAvgYield, v > 0 else { return "—" }
        return String(format: "%.2f%%", v)
    }
}

extension DividendInfo {
    static let sample: DividendInfo = {
        var exComponents = DateComponents()
        exComponents.year = 2025
        exComponents.month = 11
        exComponents.day = 10
        let exDate = Calendar.current.date(from: exComponents) ?? Date()

        var payComponents = DateComponents()
        payComponents.year = 2025
        payComponents.month = 11
        payComponents.day = 16
        let payDate = Calendar.current.date(from: payComponents) ?? Date()

        // Real measured KO figures, so the preview shows the shape the card ships with.
        return DividendInfo(
            exDividendDate: exDate,
            paymentDate: payDate,
            fiveYearAvgYield: 0.68,
            status: .low,
            buybackStatus: .moderate,
            annualDividends: [
                AnnualDividend(year: "2020", perShare: 1.6407),
                AnnualDividend(year: "2021", perShare: 1.6806),
                AnnualDividend(year: "2022", perShare: 1.7597),
                AnnualDividend(year: "2023", perShare: 1.8395),
                AnnualDividend(year: "2024", perShare: 1.9399),
                AnnualDividend(year: "2025", perShare: 2.0402),
            ],
            perShare: 2.0402,
            perShareYear: "2025",
            growthPct: 24.3,
            growthYears: 5,
            avgYieldWindowLabel: "8Q"
        )
    }()
}

// MARK: - Money format (shared)

/// ONE dollar format for every Signal of Confidence surface — the Financials chart and the
/// report's `CapitalAllocationMiniChart` used to disagree on the same quarter ("$2B" vs
/// "$1.5B"): the full chart rounded billions to whole numbers, so $1,499M and $1,500M read
/// "$1B" and "$2B" and its 0.9/0.6/0.3 axis ticks collided.
///
/// Precision follows magnitude so no label is wider than today's "$999M" inside the
/// eight `.fixedSize()` columns: ≥ $1T `%.1fT`, ≥ $10B `%.0fB`, ≥ $1B `%.1fB`, else `%.0fM`.
/// A value that would round up into the next tier's digits ("$1000M", "$1000B") is
/// promoted to that tier instead.
///
/// The bottom of the scale (TestFlight 1.0 (11), CRWV): a measured zero is "$0" — the axis
/// baseline's own text and the report header's ("$0M" printed in every cell of a company
/// that returned nothing) — and an amount under $10M keeps one decimal ("$0.3M", "$2.6M",
/// "<$0.1M"), so a real payment never reads as zero and two axis ticks never print the same
/// figure (0.6x and 0.9x of a $1.7M axis both read "$1M" in whole millions).
enum SignalOfConfidenceFormat {
    /// `millions` is USD millions, as every SoC amount arrives.
    static func money(millions: Double) -> String {
        guard millions.isFinite else { return "—" }
        let sign = millions < 0 ? "-" : ""
        let m = abs(millions)
        if m == 0 {
            return "$0"
        }
        if m < 0.05 {
            return sign + "<$0.1M"
        }
        if m < 9.95 {
            return sign + String(format: "$%.1fM", m)
        }
        if m >= 1_000_000 || (m / 1_000).rounded() >= 1_000 {
            return sign + String(format: "$%.1fT", m / 1_000_000)
        }
        if m >= 10_000 {
            return sign + String(format: "$%.0fB", m / 1_000)
        }
        if m >= 1_000 || m.rounded() >= 1_000 {
            return sign + String(format: "$%.1fB", m / 1_000)
        }
        return sign + String(format: "$%.0fM", m)
    }
}

// MARK: - Bar scale (shared)

/// ONE materiality floor for both Signal of Confidence charts — the Financials tab's
/// `SignalOfConfidenceChartView` and the report's `CapitalAllocationMiniChart` draw the same
/// quarters, and the floor went into only the first (review 2026-10-07: CRWV's lone $1.47M
/// buyback filled 87% of the report's Capital view and 1% of the Financials tab's).
///
/// TestFlight 1.0 (11), CRWV: one $2.6M quarter on a ~$50B company set a "$0–$3M" axis and
/// drew a full-height bar beside eight "$0M" cells — a lone giant bar implying significance.
/// An axis therefore never tops out below the amount that would be material.
enum SignalOfConfidenceScale {
    /// The smallest top the Yield view's axis takes, in percent (trailing twelve months).
    /// Below it a total shareholder yield draws as the short bar it is, against a 0–1% axis.
    static let materialYieldFloor: Double = 1.0

    /// The axis top below which an amount is not material, for `viewType`. Yield:
    /// `materialYieldFloor`. Capital: a QUARTER's worth of that yield (a bar is one quarter's
    /// cash, the yield floor a year) on the median `marketCap` of `points` (USD millions, the
    /// yields' own denominator), so both views draw the same amount at the same height.
    /// 0 — no floor, the data alone scales it — when no point carries a usable cap (an older
    /// backend, a cached payload, a report stored before the cap shipped).
    static func materialityFloor(
        for points: [SignalOfConfidenceDataPoint],
        viewType: SignalOfConfidenceViewType
    ) -> Double {
        switch viewType {
        case .yield:
            return materialYieldFloor
        case .capital:
            let caps = points.compactMap { $0.marketCap }.filter { $0.isFinite && $0 > 0 }.sorted()
            guard !caps.isEmpty else { return 0 }
            let middle = caps.count / 2
            let median = caps.count % 2 == 1 ? caps[middle] : (caps[middle - 1] + caps[middle]) / 2
            // percent → fraction (/ 100), a year → one quarter (/ 4)
            let capitalFloor: Double = median * (materialYieldFloor / 100) / 4
            return capitalFloor.isFinite ? capitalFloor : 0
        }
    }

    /// True when every quarter has cash-flow figures and none returned a dollar — decided
    /// from the CASH, never from the selected view's bars. Yields are rounded to 0.01% and
    /// read 0.00 for a quarter with no usable market cap, so a Yield-view test said "no
    /// dividends or buybacks" for a tiny repurchaser (and, with every cap source down, for
    /// a top one) while the Capital view drew its bars (review 2026-10-07). A quarter with
    /// no figures (`cashFlowReported == false`) is an unknown, not a zero, so it withholds it.
    static func returnedNothing(_ points: [SignalOfConfidenceDataPoint]) -> Bool {
        !points.isEmpty && points.allSatisfy { point in
            point.cashFlowReported && !(point.dividendAmount + point.buybackAmount > 0)
        }
    }
}

// MARK: - Signal of Confidence Section Data

struct SignalOfConfidenceSectionData {
    let dataPoints: [SignalOfConfidenceDataPoint]
    let summary: SignalOfConfidenceSummary
    let dividendInfo: DividendInfo?

    init(
        dataPoints: [SignalOfConfidenceDataPoint],
        summary: SignalOfConfidenceSummary,
        dividendInfo: DividendInfo? = nil
    ) {
        self.dataPoints = dataPoints
        self.summary = summary
        self.dividendInfo = dividendInfo
    }

    /// Get max yield for chart scaling (stacked: dividend + buyback per quarter)
    var maxYield: Double {
        let maxTotal = dataPoints.map { $0.totalYield }.max() ?? 0
        return maxTotal * 1.15
    }

    /// Get max capital for chart scaling in millions (stacked: dividend + buyback per quarter)
    var maxCapital: Double {
        let maxTotal = dataPoints.map { $0.totalCapitalReturned }.max() ?? 0
        return maxTotal * 1.15
    }

    /// Get shares outstanding range for normalization
    var sharesRange: (min: Double, max: Double) {
        let shares = dataPoints.compactMap { $0.sharesOutstanding }
        let minShares = (shares.min() ?? 0) * 0.95
        let maxShares = (shares.max() ?? 1) * 1.05
        return (minShares, maxShares)
    }

    /// True when at least one quarter has no cash-flow figures (`cashFlowReported == false`),
    /// so its dividend and buyback cells read "—". Drives the card's one-line key for that dash.
    var hasUnreportedCashFlow: Bool {
        dataPoints.contains { !$0.cashFlowReported }
    }

    /// The quarter `summary.shareCountChange` is measured FROM: the oldest point that reports
    /// a share count, by the server's own rule (oldest → newest REPORTED count). nil when none
    /// does. Lets "+36.3%" say what it spans ("since Q4 '24") instead of leaving the reader
    /// to guess — for a company that returns no capital, that change IS the signal.
    var shareCountWindowStart: String? {
        dataPoints.first { ($0.sharesOutstanding ?? 0) > 0 }?.period
    }
}

// MARK: - Sample Data

extension SignalOfConfidenceSectionData {
    static let sampleData = SignalOfConfidenceSectionData(
        dataPoints: [
            SignalOfConfidenceDataPoint(
                period: "Q2 '24",
                dividendYield: 1.3,
                buybackYield: 1.1,
                dividendAmount: 3800,
                buybackAmount: 3200,
                sharesOutstanding: 155
            ),
            SignalOfConfidenceDataPoint(
                period: "Q3 '24",
                dividendYield: 1.6,
                buybackYield: 1.3,
                dividendAmount: 4200,
                buybackAmount: 3500,
                sharesOutstanding: 152
            ),
            SignalOfConfidenceDataPoint(
                period: "Q4 '24",
                dividendYield: 1.55,
                buybackYield: 0.95,
                dividendAmount: 4100,
                buybackAmount: 2500,
                sharesOutstanding: 158
            ),
            SignalOfConfidenceDataPoint(
                period: "Q1 '25",
                dividendYield: 1.35,
                buybackYield: 1.15,
                dividendAmount: 3900,
                buybackAmount: 3300,
                sharesOutstanding: 162
            ),
            SignalOfConfidenceDataPoint(
                period: "Q2 '25",
                dividendYield: 2.65,
                buybackYield: 1.6,
                dividendAmount: 7500,
                buybackAmount: 4500,
                sharesOutstanding: 168
            )
        ],
        summary: SignalOfConfidenceSummary(
            totalYield: 4.2,
            dividendYield: 1.5,
            buybackYield: 2.7,
            shareCountChange: 0.0
        ),
        dividendInfo: .sample
    )

    /// Preview-only: the sample series with an INTERIOR cash-flow gap — Q4 '24 has no
    /// cash-flow filing on record, so it carries the server's 0.0 placeholders with
    /// `cashFlowReported == false`. Sample shapes, not market data.
    static var sampleInteriorCashFlowGap: SignalOfConfidenceSectionData {
        let points = sampleData.dataPoints.map { point -> SignalOfConfidenceDataPoint in
            guard point.period == "Q4 '24" else { return point }
            return SignalOfConfidenceDataPoint(
                period: point.period,
                dividendYield: 0,
                buybackYield: 0,
                dividendAmount: 0,
                buybackAmount: 0,
                sharesOutstanding: point.sharesOutstanding,
                cashFlowReported: false
            )
        }
        return SignalOfConfidenceSectionData(
            dataPoints: points,
            summary: sampleData.summary,
            dividendInfo: sampleData.dividendInfo
        )
    }

    /// Preview-only: a company that returned no capital in any quarter while its share count
    /// rose — every cash cell a measured "$0", two quarters' counts not reported, and the
    /// dilution as the signal. Sample shapes, not market data.
    static var sampleNoCapitalReturnDiluting: SignalOfConfidenceSectionData {
        let periods = ["Q3 '24", "Q4 '24", "Q1 '25", "Q2 '25", "Q3 '25", "Q4 '25", "Q1 '26", "Q2 '26"]
        let shares: [Double?] = [nil, 400, 400, 480, 500, nil, 530, 550]
        var points: [SignalOfConfidenceDataPoint] = []
        for index in periods.indices {
            points.append(SignalOfConfidenceDataPoint(
                period: periods[index],
                dividendYield: 0,
                buybackYield: 0,
                dividendAmount: 0,
                buybackAmount: 0,
                sharesOutstanding: shares[index],
                marketCap: 45_000
            ))
        }
        let summary = SignalOfConfidenceSummary(
            totalYield: 0,
            dividendYield: 0,
            buybackYield: 0,
            shareCountChange: 37.5,
            buybackStatus: .diluting
        )
        return SignalOfConfidenceSectionData(dataPoints: points, summary: summary, dividendInfo: nil)
    }
}

// MARK: - Signal of Confidence Info Item

struct SignalOfConfidenceInfoItem: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let icon: String
    let example: String?

    init(title: String, description: String, icon: String, example: String? = nil) {
        self.title = title
        self.description = description
        self.icon = icon
        self.example = example
    }
}

extension SignalOfConfidenceInfoItem {
    static let valueInvestingTips: [SignalOfConfidenceInfoItem] = [
        SignalOfConfidenceInfoItem(
            title: "Total Shareholder Yield",
            description: "The sum of dividend yield and buyback yield. This shows the total percentage of market cap being returned to shareholders annually. A higher yield indicates management confidence and commitment to rewarding shareholders.",
            icon: "percent",
            example: "A 4% total yield (1.5% dividends + 2.5% buybacks) means shareholders receive 4% of their investment back annually."
        ),
        SignalOfConfidenceInfoItem(
            title: "Dividend Consistency",
            description: "Companies that maintain or grow dividends through economic cycles demonstrate financial strength. Look for companies with long dividend track records (Dividend Aristocrats have 25+ years).",
            icon: "calendar.badge.checkmark",
            example: "Apple increased dividends for 12 consecutive years, signaling management's confidence in future cash flows."
        ),
        SignalOfConfidenceInfoItem(
            title: "Buyback Effectiveness",
            description: "Buybacks are most valuable when shares are undervalued. Companies buying back stock at high valuations destroy value. Check if buybacks actually reduce share count or just offset dilution from stock compensation.",
            icon: "arrow.down.circle.fill",
            example: "If share count drops 3% annually from buybacks, EPS grows 3% even with flat earnings."
        ),
        SignalOfConfidenceInfoItem(
            title: "Share Count Trend",
            description: "A declining share count over time indicates effective capital allocation. Rising share counts despite buybacks suggest excessive stock-based compensation diluting existing shareholders.",
            icon: "chart.line.downtrend.xyaxis",
            example: "If a company spends $10B on buybacks but share count increases, the money went to employees, not shareholders."
        ),
        SignalOfConfidenceInfoItem(
            title: "Dividend vs Buyback Trade-off",
            description: "Dividends are taxed immediately; buybacks defer taxes until you sell. However, dividends are harder to cut (signals distress), while buybacks can stop anytime without negative perception.",
            icon: "arrow.left.arrow.right",
            example: "Tech companies often prefer buybacks for tax efficiency; utilities favor dividends for income-seeking investors."
        )
    ]
}
