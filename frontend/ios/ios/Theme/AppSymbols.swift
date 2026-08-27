//
//  AppSymbols.swift
//  ios
//
//  Design tokens for SF Symbol NAMES — the string half of the theme.
//

import SwiftUI

/// Symbol names that must not be written as bare literals at the call site.
///
/// **Why this file exists.** `Image(systemName:)` takes a `String`, so — alone among Apple's
/// versioned APIs — the compiler performs **no availability check**. A symbol introduced after
/// `IPHONEOS_DEPLOYMENT_TARGET` compiles clean, renders perfectly on a developer's simulator,
/// and draws **nothing** on a user's device. There is no warning, no crash, and no log line.
///
/// That shipped. `sparkles.2` is iOS **26.0**; the deployment target is **18.0**; and the name
/// was written out 39 times across 38 files. Every TestFlight tester below iOS 26 saw the app's
/// entire AI iconography — the header button, the chat avatar, Generate Analysis, Deep Research,
/// every report section header, onboarding, the consent screen — as blank space. It was reported
/// as "Should it be an AI icon?", because all the tester could see was an empty tile.
///
/// Two rules follow, and `tests/test_ios_symbol_availability.py` enforces both:
///
/// 1. A symbol newer than the deployment target may only be named inside an
///    `if #available(iOS N, *)` whose `N` covers it — as `ai` does below.
/// 2. A symbol name arriving from the SERVER is clamped through ``validated(_:fallback:)``
///    before it reaches a view. Content is editable without an app release, so it can name a
///    symbol this OS has never heard of.
struct AppSymbols {

    /// The Cay AI mark.
    ///
    /// `sparkles.2` (iOS 26) where it exists, the classic `sparkles` (iOS 13) below — so the
    /// glyph is never missing, on any OS the app supports.
    ///
    /// `static let`, not a computed `var`: Swift evaluates a `static let` initializer exactly
    /// once for the process lifetime, whereas a computed property would re-run the version
    /// check on every SwiftUI body pass, on a token read by ~38 files.
    static let ai: String = {
        if #available(iOS 26.0, *) { return "sparkles.2" }
        return "sparkles"
    }()

    /// Clamp a symbol name that came from OUTSIDE the binary — server content, a cached
    /// payload, bundled JSON authored against a newer SF Symbols release.
    ///
    /// The same discipline `Color(themedHex:role:fallback:)` imposes on server-supplied colour:
    /// presentation values from the network are corrected at the model boundary, never trusted
    /// into a view. `UIImage(systemName:)` returns `nil` for a name this OS does not carry,
    /// which is the only reliable runtime test — the availability database is not queryable at
    /// runtime.
    ///
    /// - Returns: `name` when this OS can actually draw it, otherwise `fallback`.
    static func validated(_ name: String?, fallback: String) -> String {
        resolved(name) ?? fallback
    }

    /// The nil-returning form, for callers that want to distinguish "unusable" from "absent"
    /// rather than substitute. `PlanFeature.validatedSymbol` forwards here.
    static func resolved(_ name: String?) -> String? {
        guard let trimmed = name?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty,
              UIImage(systemName: trimmed) != nil
        else { return nil }
        return trimmed
    }
}
