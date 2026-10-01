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
    /// Beyond this, a same-session intraday snapshot gets its time printed.
    ///
    /// Longer than the Market tile's 20-minute self-refresh, so a tile that IS keeping up
    /// stays quiet; a Holdings tile (which only the app can refresh) written at 10:05 and
    /// still on screen at 15:55 says "As of 10:05 AM ET" instead of passing for current.
    ///
    /// Internal, not private: the timeline schedules a render just past it (`ageBoundary`),
    /// and that must be THIS constant, never a second copy of the 45.
    static let intradayAgeLimit: TimeInterval = 45 * 60
    /// A regular-session build at or after this ET minute is close enough to the bell to
    /// keep "Tue close". Earlier than that, the numbers were NEVER the close.
    private static let nearCloseMinute = 15 * 60 + 55
    /// The opening bell, ET minute of day — the moment a pre-market reading stops being the
    /// latest one. Mirrors `WidgetRefreshSchedule.regularOpen`; a local copy so this file
    /// still compiles on its own in `scripts/widget-session-label-check.sh`.
    private static let regularOpenMinute = 9 * 60 + 30
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
        //
        //    But only if the numbers WERE the close. A Holdings snapshot the app wrote at
        //    10:05 on Tuesday and nothing refreshed was labelled "Tue close" on Wednesday —
        //    an intraday −1.2% presented as a close that was really −2.7%. Such a build says
        //    when it was taken instead.
        //
        // 5. Old enough that a weekday name no longer identifies the day, and old enough
        //    that asking for a refresh is the useful thing to say.
        //
        //    Both decided by `priorSessionClaim`, which `agedLabel` and `compactAgedLabel`
        //    share, so no wording of the label can make a different claim from another.
        return fullWording(
            priorSessionClaim(asOf: asOf, day: day, daysAgo: daysAgo, marketSession: marketSession)
        )
    }

    /// The label ONLY when it changes how the numbers should be read — nil otherwise.
    ///
    /// "Live 2:14 PM ET" beside a live quote is noise: it tells the reader something they
    /// already assume. "Fri close" on a Sunday is not noise — it is the difference between
    /// a fact and a lie, because the tile is otherwise showing a −5.02% that looks like
    /// today's.
    ///
    /// So this returns nil for the current session and a label for everything else. That
    /// keeps the honesty guarantee while costing zero pixels on the overwhelmingly common
    /// day, which is what makes it affordable on a 155pt tile.
    ///
    /// Two exceptions within the day, both "As of 10:05 AM ET" — the current session, but not
    /// the current numbers:
    ///   • a REGULAR-session snapshot older than `intradayAgeLimit` — the case a Holdings tile
    ///     hits whenever the app was opened once mid-morning;
    ///   • a PRE-MARKET snapshot once the opening bell has rung after it. Before the bell a
    ///     07:31 reading is still the latest there is (and stays silent); after it, the 08:30
    ///     numbers on a tile nothing refreshed are not the session's.
    ///
    /// Absent `sessionDate` (an old backend) is NOT treated as current: we cannot tell,
    /// and the safe answer to "is this today's data" is to say what we know rather than
    /// imply freshness we have not established.
    ///
    /// `sessionLabel` does not change the decision (the server's sentence is only ever the
    /// CURRENT wording, which this never returns); it is accepted so the call reads like
    /// `displayLabel`'s.
    public static func agedLabel(
        asOf: Date,
        sessionDate: String?,
        marketSession: String,
        sessionLabel: String?,
        now: Date = Date()
    ) -> String? {
        agedClaim(asOf: asOf, sessionDate: sessionDate, marketSession: marketSession, now: now)
            .map { fullWording($0) }
    }

    /// The SAME decision as `agedLabel`, worded for a slot with no room: "Tue 10:05",
    /// "Tue close", "Sep 23", "As of 10:05". nil exactly when `agedLabel` is nil.
    ///
    /// For the Lock Screen's inline line, which used to fall back from "AAPL +2.10% · Tue
    /// 10:05 AM ET" straight to a bare "AAPL +2.10%" — a Tuesday intraday move presented as
    /// today's. With this the line sheds the ticker, then the number, never the age.
    public static func compactAgedLabel(
        asOf: Date,
        sessionDate: String?,
        marketSession: String,
        now: Date = Date()
    ) -> String? {
        agedClaim(asOf: asOf, sessionDate: sessionDate, marketSession: marketSession, now: now)
            .map { compactWording($0) }
    }

    /// WHICH claim an aged label makes, before it is worded. One decision shared by every
    /// wording (`agedLabel`, `compactAgedLabel`, `displayLabel`'s previous-session steps), so
    /// a shorter label can never say something different from the longer one.
    enum AgedClaim {
        /// Today's session, not the current numbers: "As of 10:05 AM ET".
        case asOf(Date)
        /// A previous session's INTRADAY reading: "Tue 10:05 AM ET".
        case dayAndTime(day: Date, asOf: Date)
        /// A previous session's close: "Tue close".
        case dayClose(Date)
        /// Too old for a weekday name: "Sep 23 — open Caydex".
        case monthDay(Date)
        /// An old backend (no `session_date`): the original footer wording, verbatim.
        case legacy(String)
    }

    static func agedClaim(
        asOf: Date, sessionDate: String?, marketSession: String, now: Date
    ) -> AgedClaim? {
        guard let sessionDate, let day = parseDay(sessionDate) else {
            // `displayLabel` step 1: nothing to reason from, so the original wording or nothing.
            let legacy = legacyLabel(marketSession)
            return legacy.isEmpty ? nil : .legacy(legacy)
        }
        let cal = easternCalendar
        let daysAgo = cal.dateComponents(
            [.day], from: cal.startOfDay(for: day), to: cal.startOfDay(for: now)
        ).day ?? 0
        if daysAgo <= 0 {
            // Today's session. Silent while it is current — but an intraday reading the app
            // wrote hours ago is not current, and Holdings cannot refresh itself. Same honest
            // tense as `displayLabel` step 3; nothing else changes wording within the day.
            if marketSession == "regular", now.timeIntervalSince(asOf) > intradayAgeLimit {
                return .asOf(asOf)
            }
            if marketSession == "premarket", let open = regularOpen(on: asOf, cal: cal),
               asOf < open, now >= open {
                return .asOf(asOf)
            }
            return nil
        }
        return priorSessionClaim(asOf: asOf, day: day, daysAgo: daysAgo, marketSession: marketSession)
    }

    /// `displayLabel` steps 4 and 5: a previous session's day — with the build's time when
    /// the numbers were intraday — or, past the weekday horizon, its date.
    private static func priorSessionClaim(
        asOf: Date, day: Date, daysAgo: Int, marketSession: String
    ) -> AgedClaim {
        if daysAgo <= weekdayNameHorizon {
            if builtMidSession(asOf: asOf, day: day, marketSession: marketSession) {
                return .dayAndTime(day: day, asOf: asOf)
            }
            return .dayClose(day)
        }
        return .monthDay(day)
    }

    private static func fullWording(_ claim: AgedClaim) -> String {
        switch claim {
        case .asOf(let t):                return "As of \(clock(t))"
        case .dayAndTime(let day, let t): return "\(weekday(day)) \(clock(t))"
        case .dayClose(let day):          return "\(weekday(day)) close"
        case .monthDay(let day):          return "\(monthDay(day)) — open Caydex"
        case .legacy(let text):           return text
        }
    }

    /// No " ET", no AM/PM, no "— open Caydex": the claim (which day, close or intraday) is
    /// kept whole, only its decoration goes.
    private static func compactWording(_ claim: AgedClaim) -> String {
        switch claim {
        case .asOf(let t):                return "As of \(shortClock(t))"
        case .dayAndTime(let day, let t): return "\(weekday(day)) \(shortClock(t))"
        case .dayClose(let day):          return "\(weekday(day)) close"
        case .monthDay(let day):          return monthDay(day)
        case .legacy(let text):           return text
        }
    }

    /// The instant, after `now`, at which `agedLabel` starts speaking for a snapshot of
    /// TODAY's session — or nil when there is none to wait for.
    ///
    /// The label is re-evaluated only at timeline entries, and during regular hours the
    /// timeline holds ONE (`renderDates` stops at the 20-minute reload). So a Holdings tile
    /// written at 10:05 kept its unlabelled 10:05 render until the first reload at least 45
    /// minutes on — 11:05 when every reload ran on time, later whenever WidgetKit deferred one.
    /// The provider adds an entry here, which is exactly when the wording changes.
    ///
    /// - regular: one second past `intradayAgeLimit` (the rule is a strict `>`).
    /// - premarket: the opening bell, when the reading stops being the latest.
    /// - otherwise nil: no rule changes wording within the day.
    public static func ageBoundary(
        asOf: Date, sessionDate: String?, marketSession: String, now: Date
    ) -> Date? {
        guard let sessionDate, let day = parseDay(sessionDate) else { return nil }
        let cal = easternCalendar
        guard cal.isDate(day, inSameDayAs: now) else { return nil }
        let boundary: Date
        switch marketSession {
        case "regular":
            boundary = asOf.addingTimeInterval(intradayAgeLimit + 1)
        case "premarket":
            guard let open = regularOpen(on: asOf, cal: cal), asOf < open else { return nil }
            boundary = open
        default:
            return nil
        }
        return boundary > now ? boundary : nil
    }

    /// Whether `asOf`'s ET calendar day is before `now`'s.
    ///
    /// The "aged" test for a ROUND-THE-CLOCK headline (crypto), whose 24 h move belongs to
    /// the day it was BUILT, not to the equity session the payload is stamped with: a Saturday
    /// build — or a Monday pre-market one — is stamped Friday, so `isPriorSession` called a
    /// live 24 h move "aged" on the very day it was built. By build day it ages exactly when
    /// the reader's ET day moves past it.
    public static func isPriorETDay(asOf: Date, now: Date = Date()) -> Bool {
        let cal = easternCalendar
        let daysAgo = cal.dateComponents(
            [.day], from: cal.startOfDay(for: asOf), to: cal.startOfDay(for: now)
        ).day ?? 0
        return daysAgo > 0
    }

    /// Whether `sessionDate` names an ET day BEFORE `now`'s — the numbers are from a previous
    /// session. Absent or unreadable ⇒ false: with no anchor there is nothing to compare.
    ///
    /// What decides whether a cause sentence written with "today" may still be shown, which is
    /// a different question from whether the footer has something to say (`agedLabel` also
    /// speaks up for a same-day intraday reading, where "today" is still true).
    public static func isPriorSession(sessionDate: String?, now: Date = Date()) -> Bool {
        guard let sessionDate, let day = parseDay(sessionDate) else { return false }
        let cal = easternCalendar
        let daysAgo = cal.dateComponents(
            [.day], from: cal.startOfDay(for: day), to: cal.startOfDay(for: now)
        ).day ?? 0
        return daysAgo > 0
    }

    /// True when a snapshot was built during that session's day before the close was near —
    /// so its numbers are intraday (or pre-market), not the close.
    ///
    /// Three conditions, each load-bearing:
    ///   • `regular` or `premarket` — `session_phase` leaves both before 16:00 (13:00 on a
    ///     half-day), so either alone proves the build preceded the close; no client holiday
    ///     table is needed. A pre-market build dated TODAY (crypto-only Holdings, an
    ///     unstamped batch) was called "Wed close" the next morning for an 08:30 reading.
    ///   • `asOf`'s ET date == the session date — a 09:31 build whose quotes were all still
    ///     stamped with YESTERDAY'S session is regular-phase, but its numbers ARE yesterday's
    ///     close, and "Mon 9:31 AM ET" would pair the wrong day with the wrong time. The same
    ///     guard keeps "Tue close" for an equity pre-market build, which the backend dates
    ///     with the PRIOR session.
    ///   • before 15:55 ET — a 15:58 reading is the close to every practical purpose.
    static func builtMidSession(asOf: Date, day: Date, marketSession: String) -> Bool {
        guard marketSession == "regular" || marketSession == "premarket" else { return false }
        let cal = easternCalendar
        guard cal.isDate(asOf, inSameDayAs: day) else { return false }
        let parts = cal.dateComponents([.hour, .minute], from: asOf)
        let minuteOfDay = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        return minuteOfDay < nearCloseMinute
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

    /// "10:05" — the compact label's time. No AM/PM: beside a weekday or "As of" on a market
    /// tile, the trading hours make it unambiguous.
    private static func shortClock(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "h:mm"
        return f.string(from: d)
    }

    /// 09:30 ET on `d`'s ET day.
    private static func regularOpen(on d: Date, cal: Calendar) -> Date? {
        var c = cal.dateComponents([.year, .month, .day], from: d)
        c.hour = regularOpenMinute / 60
        c.minute = regularOpenMinute % 60
        c.second = 0
        return cal.date(from: c)
    }
}
