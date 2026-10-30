//
//  EarningsLiveModels.swift
//  ios
//
//  Live earnings results — the Tracking card's LIVE state and the Alerts sheet's
//  estimate-vs-actual section (owner request 2026-10-29, INTC's report day).
//
//  Data: `GET /api/v1/tracking/earnings/{ticker}/live` (`EarningsLiveResponse`) for the sheet,
//  and the live fields on each `AlertDTO` of the Tracking feed for the card. The backend
//  (`earnings_live_service.py`) decides every status and every guard; this file only decodes
//  and formats, so the card, the sheet and the push can never disagree about a report.
//

import Foundation
import SwiftUI

// MARK: - Status

/// Where one report stands. Mirrors `earnings_live_service.STATUS_*`.
///
/// `.live` is drawn ONLY while the server is genuinely checking — report day, 06:00–22:00 ET,
/// results not complete — so the red LIVE label never claims something that is not
/// happening (the `LiveNewsHeader` rule: nothing live on screen, no "Live").
enum EarningsLiveStatus: String {
    case upcoming
    case live
    case reported
    case awaiting
    /// No report for this ticker in the window (a reschedule moved it out). Not named `none`:
    /// a case called `none` collides with `Optional.none` wherever the status is optional.
    case notScheduled = "none"

    /// Unknown or absent values read as `.upcoming`: an older backend that sends no status
    /// is describing exactly the card this build used to draw.
    init(wire: String?) {
        self = EarningsLiveStatus(rawValue: (wire ?? "").lowercased()) ?? .upcoming
    }
}

// MARK: - One metric's outcome

/// Beat / missed / in line / pending for ONE metric of ONE report, and the colour that goes
/// with it. Reuses the Financials tab's `EarningsQuarterResult` so a green "beat" means the
/// same thing on the card, the sheet and the earnings chart.
struct EarningsMetricOutcome: Equatable {
    let estimate: Double?
    let actual: Double?
    let surprisePct: Double?
    /// The figure is from the company's own release, not yet confirmed by FMP.
    let isPreliminary: Bool
    /// A GAAP EPS against an adjusted consensus: shown, never coloured beat/miss.
    let isGAAP: Bool
    /// A figure exists but a sanity guard holds it (a dropped-digit signature).
    let isChecking: Bool

    init(estimate: Double?, actual: Double?, surprisePct: Double?,
         isPreliminary: Bool = false, isGAAP: Bool = false, isChecking: Bool = false) {
        self.estimate = estimate
        self.actual = actual
        self.surprisePct = surprisePct
        self.isPreliminary = isPreliminary
        self.isGAAP = isGAAP
        self.isChecking = isChecking
    }

    var hasLanded: Bool { actual != nil }

    /// `.pending` until the figure lands. `.noEstimate` when there is nothing comparable —
    /// no consensus, or a GAAP figure against an adjusted one.
    var result: EarningsQuarterResult {
        guard let actual else { return .pending }
        guard !isGAAP, let estimate else { return .noEstimate }
        if actual > estimate { return .beat }
        if actual < estimate { return .missed }
        return .matched
    }

    /// Text-safe tokens (`gain` / `loss`), never the chart-only graphic ones.
    var color: Color {
        switch result {
        case .beat: return AppColors.gain
        case .missed: return AppColors.loss
        case .matched: return AppColors.accentCyan
        case .pending, .noEstimate: return AppColors.textSecondary
        }
    }

    /// "Beat" / "Miss" / "In line" — the word, so the meaning never rests on colour alone.
    var word: String? {
        switch result {
        case .beat: return "Beat"
        case .missed: return "Miss"
        case .matched: return "In line"
        case .pending, .noEstimate: return nil
        }
    }

    /// "+7.3%" via the ONE surprise formatter the Financials tab uses.
    var surpriseText: String? {
        guard let pct = surprisePct, pct.isFinite, !isGAAP else { return nil }
        return EarningsQuarterData.surpriseText(pct, isMatch: result == .matched)
    }

    /// `arrowtriangle.up.fill` / `.down.fill` — the non-colour cue (`AppSentiment`).
    var arrowSymbol: String? {
        switch result {
        case .beat: return AppSentiment.symbolName(isPositive: true)
        case .missed: return AppSentiment.symbolName(isPositive: false)
        case .matched, .pending, .noEstimate: return nil
        }
    }
}

// MARK: - Formatting

/// Dollar figures for earnings. NOT `CompactNumberFormat`: that one drops to whole units at
/// 10+ for chart axes, which would print a $17.1B actual against a $16.6B estimate as
/// "17B vs 17B" — the one comparison this screen exists to make.
enum EarningsFigureFormat {

    /// "$0.41", "-$0.25" (the sign before the dollar — "$-0.25" is how the old card read).
    static func eps(_ value: Double) -> String {
        guard value.isFinite else { return "—" }
        let sign = value < 0 ? "-" : ""
        return sign + String(format: "$%.2f", abs(value))
    }

    /// "$17.1B", "$113.4B", "$523.4M", "$950K". One decimal at every scale so two nearby
    /// figures stay distinguishable; small pre-revenue estimates read "$23.5M", not "$0.0B".
    static func revenue(_ value: Double) -> String {
        guard value.isFinite else { return "—" }
        let sign = value < 0 ? "-" : ""
        let v = abs(value)
        let body: String
        if v >= 1_000_000_000_000 {
            body = String(format: "$%.2fT", v / 1_000_000_000_000)
        } else if v >= 1_000_000_000 {
            body = String(format: "$%.1fB", v / 1_000_000_000)
        } else if v >= 1_000_000 {
            body = String(format: "$%.1fM", v / 1_000_000)
        } else if v >= 1_000 {
            body = String(format: "$%.0fK", v / 1_000)
        } else {
            body = String(format: "$%.0f", v)
        }
        return sign + body
    }

    /// "+24.8%" / "-3.1%" for a year-over-year change.
    static func growth(_ pct: Double) -> String {
        guard pct.isFinite else { return "—" }
        let sign = pct > 0 ? "+" : (pct < 0 ? "-" : "")
        return sign + String(format: "%.1f%%", abs(pct))
    }
}

// MARK: - Wire: GET /tracking/earnings/{ticker}/live
//
// Declared like the Tracking feed's own DTOs (`TrackingFeedResponse`, `AlertDTO`), with
// explicit snake_case CodingKeys — the decoder does not convert. Pinned against the backend's
// `EarningsLiveResponse` by `backend/tests/test_ios_earnings_live_guards.py`.

struct EarningsLiveMetricDTO: Codable, Equatable {
    let estimate: Double?
    let actual: Double?
    let surprisePct: Double?
    let source: String?
    let basis: String?
    let checking: Bool?

    enum CodingKeys: String, CodingKey {
        case estimate, actual, source, basis, checking
        case surprisePct = "surprise_pct"
    }
}

struct EarningsLiveQuarterDTO: Codable, Equatable {
    let quarter: String
    let result: String
    let surprisePct: Double?

    enum CodingKeys: String, CodingKey {
        case quarter, result
        case surprisePct = "surprise_pct"
    }
}

struct EarningsLiveYearAgoDTO: Codable, Equatable {
    let quarter: String?
    let revenue: Double?
    let eps: Double?
    let revenueGrowthPct: Double?
    let epsGrowthPct: Double?

    enum CodingKeys: String, CodingKey {
        case quarter, revenue, eps
        case revenueGrowthPct = "revenue_growth_pct"
        case epsGrowthPct = "eps_growth_pct"
    }
}

struct EarningsLiveFilingDTO: Codable, Equatable {
    let url: String?
    let acceptedAt: String?

    enum CodingKeys: String, CodingKey {
        case url
        case acceptedAt = "accepted_at"
    }
}

struct EarningsLiveResponse: Codable, Equatable {
    let ticker: String
    let companyName: String?
    let reportDate: String
    let status: String
    let checkedAt: String?
    let preliminary: Bool?
    let eps: EarningsLiveMetricDTO
    let revenue: EarningsLiveMetricDTO
    let filing: EarningsLiveFilingDTO?
    let guidance: String?
    let yearAgo: EarningsLiveYearAgoDTO?
    let lastQuarters: [EarningsLiveQuarterDTO]?
    let pollSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case ticker, status, preliminary, eps, revenue, filing, guidance
        case companyName = "company_name"
        case reportDate = "report_date"
        case checkedAt = "checked_at"
        case yearAgo = "year_ago"
        case lastQuarters = "last_quarters"
        case pollSeconds = "poll_seconds"
    }
}

extension EarningsLiveResponse {
    var liveStatus: EarningsLiveStatus { EarningsLiveStatus(wire: status) }

    var epsOutcome: EarningsMetricOutcome {
        EarningsMetricOutcome(
            estimate: eps.estimate, actual: eps.actual, surprisePct: eps.surprisePct,
            isPreliminary: eps.source == "filing", isGAAP: eps.basis == "gaap",
            isChecking: eps.checking ?? false
        )
    }

    var revenueOutcome: EarningsMetricOutcome {
        EarningsMetricOutcome(
            estimate: revenue.estimate, actual: revenue.actual,
            surprisePct: revenue.surprisePct, isPreliminary: revenue.source == "filing",
            isChecking: revenue.checking ?? false
        )
    }

    var checkedDate: Date? { EarningsLiveDates.parse(checkedAt) }
    var acceptedDate: Date? { EarningsLiveDates.parse(filing?.acceptedAt) }
    var filingURL: URL? { filing?.url.flatMap(URL.init(string:)) }

    /// The Financials-tab outcome of each of the last quarters, oldest first.
    var quarterResults: [(quarter: String, result: EarningsQuarterResult, surprisePct: Double?)] {
        (lastQuarters ?? []).map { q in
            let result: EarningsQuarterResult
            switch q.result {
            case "beat": result = .beat
            case "missed": result = .missed
            case "matched": result = .matched
            default: result = .noEstimate
            }
            return (quarter: q.quarter, result: result, surprisePct: q.surprisePct)
        }
    }
}

/// ISO-8601 instants from the live endpoints ("2026-10-29T20:05:00Z", with or without
/// fractional seconds).
enum EarningsLiveDates {
    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func parse(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        return plain.date(from: raw) ?? fractional.date(from: raw)
    }

    private static let etClock: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "h:mm a"
        return f
    }()

    /// "4:02 PM ET" — earnings times are always quoted in New York time.
    static func etTime(_ date: Date) -> String {
        etClock.string(from: date) + " ET"
    }
}
