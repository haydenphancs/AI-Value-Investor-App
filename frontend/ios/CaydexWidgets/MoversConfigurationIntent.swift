//
//  MoversConfigurationIntent.swift
//  CaydexWidgets
//
//  The Market ⇄ Portfolio switch, exposed through the widget's own long-press
//  "Edit Widget" sheet.
//
//  `AppIntentConfiguration` rather than the legacy `IntentConfiguration` + SiriKit
//  `.intentdefinition` file: the deployment target is iOS 18, and the old path needs a
//  separate Intents extension and a code-generated intent class. This is one Swift file.
//

import AppIntents
import OSLog
import WidgetKit

enum MoversMode: String, AppEnum {
    case market
    case portfolio

    static var typeDisplayRepresentation: TypeDisplayRepresentation { "Source" }

    static var caseDisplayRepresentations: [MoversMode: DisplayRepresentation] {
        [
            // The two modes answer DIFFERENT questions, which is why the wording no
            // longer matches. Market is the state of the tape — indices, breadth and
            // the day's one-line read — so someone glancing at it knows what is going
            // on without picking through names. Holdings is a mover list, because
            // there the individual name IS the point.
            .market: DisplayRepresentation(
                title: "Market",
                subtitle: "What the whole market is doing right now"
            ),
            .portfolio: DisplayRepresentation(
                title: "My Holdings",
                subtitle: "Your active portfolio's biggest movers, and why"
            ),
        ]
    }
}

struct MoversConfigurationIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource { "Market & Holdings" }
    /// Says that the in-tile ⇆ switch outranks this setting. It does, for every tile set up
    /// the same way (tiles added later included) until it is tapped back, and nothing on the
    /// tile can say so — so the sheet where a choice "does nothing" is where it is said.
    static var description: IntentDescription {
        IntentDescription(
            "Choose whether the widget follows the market or your own holdings. A switch made with the ⇆ button on the widget stays in place until you tap it back."
        )
    }

    // Defaults to market because it is the mode the extension can keep fresh by itself: it
    // authenticates `/widget/market-mover` with the widget token the app publishes, while
    // holdings mode can only ever render what the app last wrote.
    //
    // ⚠️ This comment used to say market "needs no identity… so a signed-out user who adds the
    // widget still sees real content". That is no longer true and must not be relied on: with
    // no session there is no widget token, the fetch is skipped, the snapshots are cleared on
    // sign-out, and the provider renders "Sign in to Caydex…" in BOTH modes whenever the token
    // is absent. That is the intended render, not a regression; FMP data may not be displayed
    // to an unauthenticated caller.
    @Parameter(title: "Show", default: .market)
    var mode: MoversMode
}


// MARK: - The in-tile toggle

/// Flips the tile between Market and Holdings without opening the app.
///
/// WidgetKit hands a timeline provider the widget's CONFIGURATION but no stable per-instance
/// identity, and an `AppIntent` cannot write back into a configuration — so the choice has
/// to live in the App Group. It used to live there as ONE global value, which made one tap
/// flip every Caydex tile on the Home Screen (and the Lock Screen), beat Edit Widget forever,
/// and carry over to the next account.
///
/// Now it is keyed by the tapped tile's CONFIGURED mode (`base`): a tap on a tile configured
/// as Market records "Market tiles show Holdings", and only tiles configured as Market follow
/// it. Tapping back to the configured mode removes the entry, so Edit Widget is in charge
/// again.
///
/// Until then the tap OUTRANKS Edit Widget for that base — honestly stated, not fixed: every
/// tile configured as Market (one added later included) shows Holdings, and re-choosing
/// "Market" in Edit Widget changes nothing. Both limits are the platform's: WidgetKit gives
/// no per-instance identity, so two tiles configured the same way cannot be told apart, and
/// an expiry would make the tile flip back by itself — the "widget changes on its own"
/// complaint this replaced. `MoversConfigurationIntent.description` tells the user, and the
/// gallery preview ignores the override (`MoversProvider.snapshot(for:in:)`).
struct ToggleMoversModeIntent: AppIntent {
    static var title: LocalizedStringResource { "Switch between Market and Holdings" }
    /// The tile redraws in place; opening the app would defeat the point of the button.
    static var openAppWhenRun: Bool { false }

    /// The mode to show after the tap.
    @Parameter(title: "Show")
    var mode: MoversMode

    /// The tapped tile's configured mode — WHICH tiles this tap is about.
    @Parameter(title: "Tile", default: .market)
    var base: MoversMode

    init() {}
    init(mode: MoversMode, base: MoversMode) {
        self.mode = mode
        self.base = base
    }

    func perform() async throws -> some IntentResult {
        WidgetModeOverride.set(mode, for: base)
        return .result()
    }
}

/// Where the in-tile toggle's choices live: one entry per CONFIGURED mode,
/// `["market": "portfolio"]` meaning "tiles configured as Market show Holdings".
///
/// Separate from `MoversConfigurationIntent.mode` because the two answer different
/// questions: the configuration is what the user chose when they placed the tile, the
/// override is what they tapped since. The provider applies it to Home Screen tiles only —
/// the Lock Screen families have no button to tap back with — and `clearAll()` removes it
/// when a session ends.
///
/// `nonisolated`: `ToggleMoversModeIntent.perform()` is not main-actor isolated, and this is
/// a thin wrapper over thread-safe `UserDefaults` with no main-actor state.
nonisolated enum WidgetModeOverride {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// The override for tiles configured as `base`, or nil. A legacy build's single global
    /// string under the same key is not a map, and reads as "no override".
    static func current(for base: MoversMode) -> MoversMode? {
        guard let map = WidgetSharedDefaults.store?.dictionary(forKey: WidgetSharedConfig.modeOverrideKey)
                as? [String: String],
              let raw = map[base.rawValue]
        else { return nil }
        return MoversMode(rawValue: raw)
    }

    static func set(_ mode: MoversMode, for base: MoversMode) {
        guard let store = WidgetSharedDefaults.store else {
            log.error("widget: cannot record the toggle — App Group unavailable")
            return
        }
        var map = (store.dictionary(forKey: WidgetSharedConfig.modeOverrideKey) as? [String: String]) ?? [:]
        // Compared by raw value: this runs off the main actor, and the strings are the
        // stored form anyway.
        if mode.rawValue == base.rawValue {
            // Back to what the tile is configured as: that is no override at all.
            map.removeValue(forKey: base.rawValue)
        } else {
            map[base.rawValue] = mode.rawValue
        }
        if map.isEmpty {
            store.removeObject(forKey: WidgetSharedConfig.modeOverrideKey)
        } else {
            store.set(map, forKey: WidgetSharedConfig.modeOverrideKey)
        }
    }
}
