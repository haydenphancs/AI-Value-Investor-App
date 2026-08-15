//
//  WidgetSessionLabel.swift
//  Caydex
//
//  What time the numbers on the tile are from — derived at RENDER time, every time.
//
//  ⚠️ THIS IS NOT A STALENESS FLAG, AND MUST NEVER BECOME ONE.
//  `is_stale` was deliberately deleted from this payload once already. It meant "the
//  market is closed", which is a different thing: a Saturday tile showing Friday's close
//  is CORRECT, not stale. What was actually missing is the anchor — WHICH SESSION these
//  numbers describe — so the tile can name it instead of implying "now".
//
//  WHY DERIVED, NOT STORED
//  The widget extension cannot fetch. The app refreshes on cold launch and foreground
//  only, so between two app opens the SAME bytes re-render indefinitely. Any freshness
//  wording baked in at write time is therefore a claim that decays without anything
//  updating it — which is exactly what went wrong: `market_session` is captured at write
//  time, so a snapshot written Friday at 18:00 rendered "After hours" on Monday, and one
//  written during regular hours rendered an EMPTY label forever.
//
//  Deriving from `session_date` fixes it structurally. The date does not decay; the
//  sentence built from it is re-evaluated on every timeline entry, so the tile ages its
//  own label with no network, no background task, and no flag anyone has to keep true.
//
//  Pure and clock-injected so it can be tested. Its failure mode — the widget lying about
//  time — is not observable by looking at a Home Screen on a Tuesday afternoon; you would
//  have to wait until Sunday.
//

import Foundation

public enum WidgetSessionLabel {
    /// Beyond this, "Live" is no longer a defensible word for an intraday quote.
    private static let liveGrace: TimeInterval = 15 * 60
    /// Past this many days a weekday name is ambiguous ("Fri" — which Friday?).
    private static let weekdayNameHorizon = 5

    private static var easternCalendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        return cal
    }

    /// The label to render, true AS OF `now`.
    ///
    /// - Parameters:
    ///   - sessionDate: `YYYY-MM-DD`, ET. Absent on a pre-`session_date` backend.
    ///   - sessionLabel: the server's sentence, true at `asOf`.
    public static func displayLabel(
        asOf: Date,
        sessionDate: String?,
        marketSession: String,
        sessionLabel: String?,
        now: Date = Date()
    ) -> String {
        // 1. Old backend: no anchor to reason from. Fall back to the original behaviour
        //    rather than inventing a claim from a field that is not there.
        guard let sessionDate, let day = parseDay(sessionDate) else {
            return legacyLabel(marketSession)
        }

        let cal = easternCalendar
        let today = cal.startOfDay(for: now)
        let dayStart = cal.startOfDay(for: day)
        let daysAgo = cal.dateComponents([.day], from: dayStart, to: today).day ?? 0

        if daysAgo <= 0 {
            // Numbers from TODAY's session.
            let age = now.timeIntervalSince(asOf)

            // 2. Genuinely live, and recent enough that the word still holds.
            if marketSession == "regular", age < liveGrace, let sessionLabel,
               !sessionLabel.isEmpty {
                return sessionLabel
            }

            // 3. Today, but the server's wording has decayed. "Live 2:14 PM ET" read at
            //    16:40 is false in a way "As of 2:14 PM ET" is not — same instant, honest
            //    tense. Pre-market and after-hours labels do not decay the same way, so
            //    they are passed through.
            switch marketSession {
            case "premarket", "afterhours":
                if let sessionLabel, !sessionLabel.isEmpty { return sessionLabel }
                return marketSession == "premarket" ? "Pre-market" : "After hours"
            case "closed":
                return "At the close"
            default:
                return "As of \(clock(asOf))"
            }
        }

        // 4. A previous session. Name the DAY — this is the whole point: "Fri close" read
        //    on a Sunday is true, and is what the tile used to be unable to say.
        if daysAgo <= weekdayNameHorizon {
            return "\(weekday(day)) close"
        }

        // 5. Old enough that a weekday name no longer identifies the day, and old enough
        //    that asking for a refresh is the useful thing to say.
        return "\(monthDay(day)) — open Caydex"
    }

    /// What the tile said before `session_date` existed. Kept verbatim so a new app
    /// against an old backend behaves exactly as it does today.
    private static func legacyLabel(_ marketSession: String) -> String {
        switch marketSession {
        case "closed":     return "At the close"
        case "premarket":  return "Pre-market"
        case "afterhours": return "After hours"
        default:           return ""
        }
    }

    // MARK: - Formatting

    /// `YYYY-MM-DD` only. Deliberately not ISO8601DateFormatter: this is a plain calendar
    /// date with no time component, and the `.iso8601` strategy rejects it.
    static func parseDay(_ s: String) -> Date? {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: s)
    }

    private static func weekday(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "EEE"
        return f.string(from: d)
    }

    private static func monthDay(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "MMM d"
        return f.string(from: d)
    }

    private static func clock(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "h:mm a"
        return f.string(from: d) + " ET"
    }
}
