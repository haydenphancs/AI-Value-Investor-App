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
    /// "industry" / "sector" / nil — which peer group the dashed line represents,
    /// from the backend. Drives the "Industry Avg" vs "Sector Avg" label.
    var peerLevel: String? = nil

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
    func toMarginSeries() -> [ProfitabilityMetricSeries] {
        func make(
            _ metric: ProfitabilityMetricType,
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
            return ProfitabilityMetricSeries(
                metric: metric, annual: pts(annual), quarterly: pts(quarterly),
                peerLevel: peerGroupLevel
            )
        }
        return [
            make(.grossMargin,
                 company: { $0.grossMargin }, sector: { $0.sectorAverageGrossMargin }),
            make(.operatingMargin,
                 company: { $0.operatingMargin }, sector: { $0.sectorAverageOperatingMargin }),
            make(.netMargin,
                 company: { $0.netMargin }, sector: { $0.sectorAverageNetMargin }),
            make(.fcfMargin,
                 company: { $0.fcfMargin }, sector: { $0.sectorAverageFcfMargin }),
        ]
    }
}

extension DeepDiveMetric {
    /// Build a Profitability series from this metric's baked history (used for
    /// ROE/ROA, which aren't in profit_power). Sector points are joined to the
    /// company periods by label (the backend already aligns them).
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
            peerLevel: peerLevel
        )
    }
}
