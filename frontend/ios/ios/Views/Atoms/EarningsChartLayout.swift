//
//  EarningsChartLayout.swift
//  ios
//
//  Atom (layout constants): the one y-axis gutter shared by the three stacked
//  earnings charts — EarningsChartView (dots), EarningsSurpriseBarChart (3Y bars)
//  and EarningsSurpriseRow (1Y percentages).
//
//  All three place column i at `gutter + (i + 0.5) * (width - gutter) / n`, so a
//  bar or a percentage sits under its dot ONLY when every chart uses the same
//  gutter. Each used to carry its own copy: the row was fixed to follow the main
//  chart's revenue width, the bar chart never was, and in Revenue × 3Y every bar
//  sat up to half a column LEFT of its dot (9.6pt for the oldest quarter at 14
//  columns), close enough to the boundary to read as the neighbouring quarter's
//  surprise. One function makes that drift impossible to reintroduce by editing
//  one file. Pinned by backend/tests/test_ios_earnings_deepcheck_guards.py.
//

import SwiftUI

enum EarningsChartLayout {
    /// Width of the y-axis column, including its trailing padding.
    /// Revenue labels ("23.3B") are wider than EPS labels ("2.49").
    static func yAxisWidth(for dataType: EarningsDataType) -> CGFloat {
        dataType == .revenue ? 50 : 40
    }
}
