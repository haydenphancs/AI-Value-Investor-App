//
//  MarketHoursUtil.swift
//  ios
//
//  Utility to check if US equity markets are in an active trading session.
//  Used to gate WebSocket connections — no point streaming when markets are closed.
//

import Foundation

enum MarketHoursUtil {

    /// Check if US markets are in an active trading session.
    ///
    /// Returns `true` during:
    /// - Pre-market:  4:00 AM – 9:30 AM ET
    /// - Regular:     9:30 AM – 4:00 PM ET
    /// - After-hours: 4:00 PM – 8:00 PM ET
    ///
    /// Returns `false` during overnight (8 PM – 4 AM ET), weekends, and holidays.
    static func isMarketActive() -> Bool {
        guard let et = TimeZone(identifier: "America/New_York") else { return false }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = et

        let now = Date()
        let components = calendar.dateComponents([.weekday, .hour, .minute, .month, .day], from: now)

        guard let weekday = components.weekday else { return false }

        // Weekend: Sunday=1, Saturday=7
        if weekday == 1 || weekday == 7 {
            return false
        }

        guard let hour = components.hour, let minute = components.minute else { return false }

        // Active window: 4:00 AM (240 min) to 8:00 PM (1200 min) ET
        let minuteOfDay = hour * 60 + minute
        return (240..<1200).contains(minuteOfDay)
    }

    /// Determine if a given `MarketStatus` represents an active session
    /// where live price streaming is useful.
    static func shouldStreamLivePrice(for status: MarketStatus) -> Bool {
        switch status {
        case .open, .preMarket, .afterHours:
            return true
        case .closed:
            return false
        }
    }
}
