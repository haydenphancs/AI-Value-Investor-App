//
//  ProfitabilityChartModels.swift
//  ios
//
//  Models for the report's Profitability drill-down (the per-metric 2-line chart).
//  One unified `ProfitabilityMetricSeries` per metric, assembled from TWO frozen
//  report sources:
//    • the 4 MARGINS (gross/operating/net/fcf) come from `profit_power` — the SAME
//      data as the live TickerDetailView Profit Power chart (see `toMarginSeries`).
//    • ROE / ROA come from the Profitability card's baked `DeepDiveMetric` history
//      (they have no Profit Power counterpart — see `toProfitabilitySeries`).
//  Each series carries a company value + a sector-average value per period; the
//  chart draws a yellow company line + a gray dashed sector line.
//

import Foundation

// MARK: - Metric type

enum ProfitabilityMetricType: String, CaseIterable, Identifiable {
    case grossMargin = "Gross Margin"
    case operatingMargin = "Operating Margin"
    case netMargin = "Net Margin"
    case fcfMargin = "FCF Margin"
    case roe = "ROE"
    case roa = "ROA"

    var id: String { rawValue }
}

// MARK: - Series models

/// One period's company + sector value for a metric. Both Optional:
///   company == nil → undefined period (line breaks, label "—")
///   sector  == nil → no sector benchmark that period (dashed line breaks)
struct ProfitabilityChartPoint: Identifiable {
    let id = UUID()
    let period: String   // "2024" (annual) or "Q1 '24" (quarterly)
    let company: Double?  // %
    let sector: Double?   // % (sector median)
}

struct ProfitabilityMetricSeries: Identifiable {
    let metric: ProfitabilityMetricType
    let annual: [ProfitabilityChartPoint]
    let quarterly: [ProfitabilityChartPoint]
    /// "industry" / "sector" / nil — the period-agnostic fallback level (an older report's
    /// one word for every line). The sheet reads the per-period level below first. nil for a
    /// margin of a per-line (v7) payload, whose per-tab levels are complete: a nil one there
    /// is an undrawn line, which must not borrow this word.
    var peerLevel: String? = nil
    /// "industry" / "sector" / nil — the peer group THIS metric's dashed line is drawn from
    /// on the Annual / Quarterly tab. Each line is one population, chosen per metric AND per
    /// period type by the backend, so the two tabs (and two metrics) can differ. `var`s with
    /// a nil default, so every existing memberwise construction still compiles.
    var annualPeerLevel: String? = nil
    var quarterlyPeerLevel: String? = nil

    var id: String { metric.rawValue }

    func points(for period: GrowthPeriodType) -> [ProfitabilityChartPoint] {
        period == .annual ? annual : quarterly
    }

    /// ≥2 real company points in EITHER granularity → chartable (chip shown).
    var hasData: Bool {
        annual.filter { $0.company != nil }.count >= 2
            || quarterly.filter { $0.company != nil }.count >= 2
    }

    /// ≥2 real company points at ANNUAL granularity → open on Annual.
    var hasAnnual: Bool {
        annual.filter { $0.company != nil }.count >= 2
    }
}

// MARK: - Source mappings

extension ProfitPowerResponseDTO {
    /// The 4 MARGIN series, built directly from the frozen Profit Power DTO so the
    /// report's Profitability drill-down shows the SAME margins (and per-margin
    /// sector medians) as the live detail Profit Power chart.
    ///
    /// Each margin names its OWN line's peer group per tab
    /// (`ProfitPowerSectionData.lineLevel`). A per-line map (profit_power v7, 2026-10-08:
    /// "annual.<metric>" / "quarterly.<metric>" keys) answers ONLY with that line's key: the
    /// backend (and the report's narrowing) writes one exactly for a line that draws a peer
    /// point, so a missing key is a line that is not on screen and gets no level — and no
    /// period-agnostic `peerLevel` either, which the sheet would read in its place (and hand
    /// to ROE/ROA) and which names the NET line. Only an older payload falls back: the tab's
    /// level (a per-tab map), then the payload-wide `peerGroupLevel` (a pre-2026-10-07
    /// report). `key` is the backend metric name (`_MARGIN_BENCHMARK_METRICS` in
    /// profit_power_service.py).
    func toMarginSeries() -> [ProfitabilityMetricSeries] {
        let levels: [String: String] = peerGroupLevels ?? [:]
        let fallbackLevel: String? = ProfitPowerSectionData.hasPerLineLevels(levels) ? nil : peerGroupLevel
        func make(
            _ metric: ProfitabilityMetricType,
            key: String,
            company: @escaping (ProfitPowerDataPointDTO) -> Double?,
            sector: @escaping (ProfitPowerDataPointDTO) -> Double?
        ) -> ProfitabilityMetricSeries {
            func pts(_ dtos: [ProfitPowerDataPointDTO]) -> [ProfitabilityChartPoint] {
                dtos.map {
                    ProfitabilityChartPoint(
                        period: $0.period, company: company($0), sector: sector($0)
                    )
                }
            }
            let annualLevel: String? = ProfitPowerSectionData.lineLevel(
                in: levels, period: .annual, metric: key, legacyLevel: peerGroupLevel
            )
            let quarterlyLevel: String? = ProfitPowerSectionData.lineLevel(
                in: levels, period: .quarterly, metric: key, legacyLevel: peerGroupLevel
            )
            return ProfitabilityMetricSeries(
                metric: metric, annual: pts(annual), quarterly: pts(quarterly),
                peerLevel: fallbackLevel,
                annualPeerLevel: annualLevel,
                quarterlyPeerLevel: quarterlyLevel
            )
        }
        return [
            make(.grossMargin, key: "gross_margin",
                 company: { $0.grossMargin }, sector: { $0.sectorAverageGrossMargin }),
            make(.operatingMargin, key: "operating_margin",
                 company: { $0.operatingMargin }, sector: { $0.sectorAverageOperatingMargin }),
            make(.netMargin, key: "net_margin",
                 company: { $0.netMargin }, sector: { $0.sectorAverageNetMargin }),
            make(.fcfMargin, key: "fcf_margin",
                 company: { $0.fcfMargin }, sector: { $0.sectorAverageFcfMargin }),
        ]
    }
}

extension DeepDiveMetric {
    /// Build a Profitability series from this metric's baked history (used for
    /// ROE/ROA, which aren't in profit_power). Sector points are joined to the
    /// company periods by label (the backend already aligns them). Each tab names its own
    /// line's group (`sectorAnnualLevel` / `sectorQuarterlyLevel`, chosen separately by the
    /// backend); `peerLevel` is only the fallback for a report that predates them.
    func toProfitabilitySeries(
        _ metric: ProfitabilityMetricType, peerLevel: String? = nil
    ) -> ProfitabilityMetricSeries {
        func pts(
            _ company: [MetricHistoryPoint]?, _ sector: [MetricHistoryPoint]?
        ) -> [ProfitabilityChartPoint] {
            let comp = company ?? []
            var sectorByPeriod: [String: Double?] = [:]
            for s in (sector ?? []) { sectorByPeriod[s.period] = s.value }
            return comp.map {
                ProfitabilityChartPoint(
                    period: $0.period,
                    company: $0.value,
                    sector: sectorByPeriod[$0.period] ?? nil
                )
            }
        }
        return ProfitabilityMetricSeries(
            metric: metric,
            annual: pts(annualHistory, sectorAnnualHistory),
            quarterly: pts(quarterlyHistory, sectorQuarterlyHistory),
            peerLevel: peerLevel,
            annualPeerLevel: sectorAnnualLevel,
            quarterlyPeerLevel: sectorQuarterlyLevel
        )
    }
}
