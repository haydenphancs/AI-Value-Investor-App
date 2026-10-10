//
//  ThirteenFQuarter.swift
//  ios
//
//  The date label on a 13F row: "Q2 2026 13F".
//
//  A 13F row's date (`whale_trades.date` / `whale_trade_groups.date`) is the QUARTER END the
//  filing reports holdings for — FMP's institutional-ownership `date`, or the hydrators'
//  `{year}-{q*3:02d}-30` fallback — never the day the fund filed, let alone a trade day. Shown
//  as "5 days ago", a fund that filed five days after the quarter closed read as having traded
//  last week. So the row names the quarter, in the same words as the Home signals drill-down
//  (`SignalDetailFormat.whaleDate`).
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`, and the time zone is INJECTABLE. There is no
//  XCTest target, so `backend/tests/test_ios_whale_13f_quarter_label.py` pipes this file into
//  `xcrun swift -` and runs it across time zones. A SwiftUI import makes it unrunnable there.
//

import Foundation

/// `nonisolated`: the target defaults to MainActor isolation, and this is pure.
nonisolated enum ThirteenFQuarter {

    /// "Q2 2026 13F" for a 13F row's date, as `DateParser` parsed it.
    ///
    /// The quarter comes from the MONTH alone, so the fallback's 03-30 / 12-30 land in Q1 / Q4.
    /// Read in the GREGORIAN calendar whatever the device is set to — a 13F quarter is a
    /// calendar quarter, and a Buddhist-calendar phone would otherwise print "Q2 2569" — and in
    /// the device zone, which is the zone `DateParser` read the "yyyy-MM-dd" in, so the month
    /// read back is the month that was sent. A UTC timestamp at a quarter end (`DateParser`'s
    /// ISO fallback) stays inside its quarter in every zone from -12 h to +14 h.
    static func label(for date: Date, timeZone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let year = calendar.component(.year, from: date)
        let month = calendar.component(.month, from: date)
        let quarter = (month - 1) / 3 + 1
        return "Q\(quarter) \(year) 13F"
    }
}
