//
//  MetricThresholdZones.swift
//  ios
//
//  Fixed-threshold zone overlay for report drill-down metrics judged against
//  ABSOLUTE cutoffs instead of an industry benchmark — currently the Altman
//  Z-Score (bankruptcy risk). Defines the zone boundaries, colors, labels and a
//  caption so the chart's colored bands stay in sync with the Health Check card's
//  gauge (HealthCheckGaugeBar / health_check_service, the source of truth:
//  Distress < 1.8, Grey 1.8–3.0, Safe > 3.0).
//

import SwiftUI

struct MetricThresholdZones {
    /// Ascending boundary values, e.g. [1.8, 3.0].
    let thresholds: [Double]
    /// Fill color per zone, low→high; length == thresholds.count + 1.
    let zoneColors: [Color]
    /// Zone label per zone, low→high; length == thresholds.count + 1.
    let zoneLabels: [String]
    /// One-line legend caption, e.g. "Distress < 1.8 · Grey 1.8–3.0 · Safe > 3.0".
    let caption: String

    /// Index of the zone a value falls in (0 = below the first threshold). A value
    /// exactly on a boundary reads as the HIGHER zone — matches the gauge's
    /// inclusive "1.8 – 3.0" labeling.
    func zoneIndex(_ v: Double) -> Int {
        var idx = 0
        for t in thresholds where v >= t { idx += 1 }
        return Swift.min(idx, zoneColors.count - 1)
    }
    func zoneLabel(_ v: Double) -> String { zoneLabels[zoneIndex(v)] }
    func zoneColor(_ v: Double) -> Color { zoneColors[zoneIndex(v)] }
}

extension MetricThresholdZones {
    /// Altman Z-Score bankruptcy zones. Cutoffs (1.8 / 3.0) and colors mirror
    /// HealthCheckGaugeBar / health_check_service — the original public-manufacturing
    /// model (1.81 → 1.8, 2.99 → 3.0). Higher is better (further from distress).
    static let altmanZ = MetricThresholdZones(
        thresholds: [1.8, 3.0],
        zoneColors: [AppColors.bearish, AppColors.neutral, AppColors.bullish],
        zoneLabels: ["Distress zone", "Grey zone", "Safe zone"],
        caption: "Distress < 1.8 · Grey 1.8–3.0 · Safe > 3.0"
    )

    /// Zones for a metric's history key, or nil when it uses an industry benchmark
    /// instead. Extensible to other absolute-threshold metrics.
    static func forHistoryKey(_ key: String?) -> MetricThresholdZones? {
        key == "altman_z" ? .altmanZ : nil
    }
}
