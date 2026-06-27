//
//  DirectionalBandSegments.swift
//  ios
//
//  Pure geometry shared by the report's two 2-line drill-down charts
//  (ProfitabilityChartView + MetricHistoryLineChart). Given an index-aligned
//  company line and industry/sector line, it produces the GREEN/RED band that
//  fills the space between them — GREEN where the company sits on the metric's
//  "good" side of the benchmark, RED on the "bad" side — split into single-color
//  sub-segments at each crossover so the color flips exactly where the lines meet.
//
//  Whether "good" means above or below the benchmark is metric-dependent
//  (`higherIsBetter`): margins/ROE/ROA/coverage = higher-is-better; valuation
//  multiples (P/E, P/B, …) and Debt/Equity = lower-is-better. See
//  `DeepDiveMetric.higherIsBetter(forHistoryKey:)`.
//
//  No SwiftUI / Charts dependency → trivially unit-testable.
//

import Foundation

/// One vertex of the filled band: at `x`, the ribbon spans between `lower` and
/// `upper` (the two line values; order doesn't matter — AreaMark fills between).
struct DirectionalBandVertex {
    let x: Double
    let lower: Double   // industry/sector line value
    let upper: Double   // company line value
}

/// A single-color sub-segment of the company-vs-benchmark band.
struct DirectionalBandSegment: Identifiable {
    let id: Int
    let isGood: Bool    // company on the metric's favorable side → green; else red
    let vertices: [DirectionalBandVertex]
}

/// Build the green/red band between an index-aligned company line and benchmark line.
///
/// `company[i]` / `sector[i]` must be the SAME y-values used to DRAW the lines
/// (already clamped where the chart clamps) so the band edges coincide exactly.
/// The band only spans index ranges where BOTH lines have a value; at every adjacent
/// pair whose signed gap (company − sector) strictly flips sign, the trapezoid is
/// split at the interpolated crossover x (the band pinches to zero-width there) so
/// each emitted segment carries one color.
///
/// Emitted per adjacent span (not merged): contiguous same-color spans share their
/// boundary vertex, so they render as one seamless region.
func directionalBandSegments(
    company: [Double?],
    sector: [Double?],
    higherIsBetter: Bool
) -> [DirectionalBandSegment] {
    let n = Swift.min(company.count, sector.count)
    guard n >= 2 else { return [] }

    // company ABOVE benchmark (d > 0) is good when higher-is-better; BELOW (d < 0)
    // is good when lower-is-better.
    func good(_ d: Double) -> Bool { higherIsBetter ? d > 0 : d < 0 }

    var out: [DirectionalBandSegment] = []
    var nextID = 0

    func emit(_ verts: [DirectionalBandVertex], isGood: Bool) {
        guard verts.count >= 2 else { return }
        // Skip a span whose ribbon is everywhere zero-width (lines coincident) — it
        // would be invisible anyway.
        let hasArea = verts.contains { abs($0.upper - $0.lower) > 0 }
        guard hasArea else { return }
        out.append(DirectionalBandSegment(id: nextID, isGood: isGood, vertices: verts))
        nextID += 1
    }

    for i in 0..<(n - 1) {
        guard let cL = company[i], let sL = sector[i],
              let cR = company[i + 1], let sR = sector[i + 1] else { continue }
        let dL = cL - sL
        let dR = cR - sR
        let vL = DirectionalBandVertex(x: Double(i), lower: sL, upper: cL)
        let vR = DirectionalBandVertex(x: Double(i + 1), lower: sR, upper: cR)

        let opposite = (dL > 0 && dR < 0) || (dL < 0 && dR > 0)
        if opposite {
            // Linear crossover: d(t) = dL + t·(dR − dL) = 0 → t = dL / (dL − dR).
            let t = dL / (dL - dR)                  // ∈ (0,1); denom ≠ 0 (opposite signs)
            let xC = Double(i) + t
            let yC = cL + t * (cR - cL)             // == sL + t·(sR − sL) at the crossing
            let vC = DirectionalBandVertex(x: xC, lower: yC, upper: yC)
            emit([vL, vC], isGood: good(dL))
            emit([vC, vR], isGood: good(dR))
        } else {
            // Same side, or one endpoint touches (d == 0): color from the non-zero gap.
            emit([vL, vR], isGood: dL != 0 ? good(dL) : good(dR))
        }
    }
    return out
}
