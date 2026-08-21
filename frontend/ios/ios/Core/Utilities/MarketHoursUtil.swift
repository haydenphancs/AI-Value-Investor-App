//
//  MarketHoursUtil.swift
//  ios
//
//  Utility to check if US equity markets are in an active trading session.
//  Used to gate WebSocket connections — no point streaming when markets are closed.
//

import Foundation

enum MarketHoursUtil {

    /// NYSE/NASDAQ full closures, as `yyyy-MM-dd` in ET. Mirrors the backend's
    /// `US_MARKET_HOLIDAYS` in `app/utils/market_hours.py` — keep both in sync.
    /// Without these the 30s price-refresh timer polled all day on Thanksgiving
    /// and Christmas against a tape that never moved.
    private static let holidays: Set<String> = [
        // 2025
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        // 2026
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        // 2027
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    ]

    /// Half-days: the market shuts at 13:00 ET, with no after-hours session.
    private static let earlyCloses: Set<String> = [
        "2025-07-03", "2025-11-28", "2025-12-24",
        "2026-11-27", "2026-12-24",
        "2027-11-26",
    ]

    private static let etCalendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        return calendar
    }()

    /// Check if US markets are in an active trading session.
    ///
    /// Returns `true` during:
    /// - Pre-market:  4:00 AM – 9:30 AM ET
    /// - Regular:     9:30 AM – 4:00 PM ET
    /// - After-hours: 4:00 PM – 8:00 PM ET
    ///
    /// Returns `false` during overnight (8 PM – 4 AM ET), weekends, and holidays.
    static func isMarketActive(at now: Date = Date()) -> Bool {
        let calendar = etCalendar
        let components = calendar.dateComponents([.weekday, .hour, .minute, .year, .month, .day], from: now)

        guard let weekday = components.weekday else { return false }

        // Weekend: Sunday=1, Saturday=7
        if weekday == 1 || weekday == 7 {
            return false
        }

        guard let year = components.year,
              let month = components.month,
              let day = components.day,
              let hour = components.hour,
              let minute = components.minute else { return false }

        let key = String(format: "%04d-%02d-%02d", year, month, day)
        if holidays.contains(key) { return false }

        // Active window: 4:00 AM (240 min) to 8:00 PM (1200 min) ET
        let minuteOfDay = hour * 60 + minute
        // Half-day: nothing trades at or after the 13:00 bell.
        if earlyCloses.contains(key) && minuteOfDay >= 13 * 60 { return false }
        return (240..<1200).contains(minuteOfDay)
    }

    /// Whether a price refresh is worth making right now for a set of assets.
    ///
    /// US equities only move during `isMarketActive()`, but crypto trades 24/7 and
    /// the FMP commodity codes are continuously-quoted futures — gating those on
    /// the equity session froze their rows every evening and all weekend, which is
    /// exactly when a crypto holder looks. Mirrors the backend's
    /// `asset_class.trades_extended_hours`.
    static func shouldRefreshPrices(assetTypes: [String], symbols: [String] = []) -> Bool {
        if isMarketActive() { return true }
        let roundTheClock: Set<String> = ["crypto", "commodity"]
        if assetTypes.contains(where: { roundTheClock.contains($0.lowercased()) }) { return true }
        // `asset_type` is unreliable (the column defaults to "Stock" and the
        // watchlist-add path never writes it), so fall back to the symbol shape —
        // the same heuristic the backend uses.
        return symbols.contains(where: symbolTradesAroundTheClock)
    }

    /// FMP's USD-suffixed commodity codes, checked before the generic crypto
    /// `USD` suffix test so gold and crude aren't classified as coins.
    /// Unambiguous FMP futures codes only. The friendly English names the
    /// backend keeps for chat voicing ("GOLD", "OIL", …) are deliberately absent:
    /// GOLD is Barrick Mining (NYSE), a regular-session equity, and treating it
    /// as a commodity here would un-gate the 30s poll 24/7 for an ordinary
    /// equity portfolio. Mirrors backend `asset_class._COMMODITY_SYMBOLS`.
    /// `nonisolated` so `symbolTradesAroundTheClock` can be — an immutable Sendable
    /// literal set, safe to read from any isolation. Same lever as `APIConfig`'s constants.
    /// Mirrors `asset_class._COMMODITY_SYMBOLS` on the backend — pinned by
    /// `tests/test_asset_class.py`. PAUSD (palladium) and ZWUSD (wheat) were missing
    /// from BOTH copies, so both fell through to the generic USD-suffix crypto rule.
    nonisolated private static let commoditySymbols: Set<String> = [
        "GCUSD", "SIUSD", "CLUSD", "NGUSD", "PLUSD", "PAUSD", "HGUSD",
        "ZSUSD", "ZCUSD", "ZWUSD", "ZUSD", "LBUSD", "OJUSD", "KCUSD",
        "SBUSD", "CTUSD", "CCUSD",
    ]

    /// `nonisolated` for the same reason as `commoditySymbols` above.
    nonisolated private static let bareCryptoSymbols: Set<String> = [
        "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "MATIC", "LINK",
        "XRP", "DOGE", "SHIB", "UNI", "AAVE", "LTC", "BCH", "ATOM",
    ]

    /// `nonisolated`: passed as a function value to `symbols.contains(where:)`, which calls
    /// it in a nonisolated context. Annotated rather than wrapped at the call site (the lever
    /// used in `HomeRepository`) because this really is pure symbol-shape math with no state —
    /// it was only MainActor by `SWIFT_DEFAULT_ACTOR_ISOLATION` default, never by need.
    nonisolated static func symbolTradesAroundTheClock(_ symbol: String) -> Bool {
        let sid = symbol.trimmingCharacters(in: .whitespaces).uppercased()
        guard !sid.isEmpty, !sid.hasPrefix("^") else { return false }
        if commoditySymbols.contains(sid) { return true }
        // The USD/USDT suffix rule needs a base symbol in front of it — "USD" on
        // its own is a real listed ETF, not a coin. Mirrors the backend's
        // asset_class.detect_asset_class.
        if sid.count > 3 && (sid.hasSuffix("USD") || sid.hasSuffix("USDT")) { return true }
        return bareCryptoSymbols.contains(sid)
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
