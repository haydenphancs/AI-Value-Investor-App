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
                subtitle: "Biggest mover in your active group"
            ),
        ]
    }
}

struct MoversConfigurationIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource { "Market & Holdings" }
    static var description: IntentDescription {
        IntentDescription("Choose whether the widget follows the market or your own holdings.")
    }

    // Defaults to market because it needs no identity: `/widget/market-mover` is a
    // public route, so a signed-out user who adds the widget still sees real content
    // rather than a sign-in prompt on their Home Screen.
    @Parameter(title: "Show", default: .market)
    var mode: MoversMode
}


// MARK: - The in-tile toggle

/// Flips the tile between Market and Holdings without opening the app.
///
/// ⚠️ THE OVERRIDE IS GLOBAL, AND THAT IS A PLATFORM LIMIT, NOT A CHOICE. WidgetKit
/// hands a timeline provider the widget's CONFIGURATION but no stable per-instance
/// identity, and an `AppIntent` cannot write back into a configuration. So the override
/// lives in the App Group and one tap flips every Caydex tile on the Home Screen.
/// Long-press → Edit Widget still works and remains the way to run two tiles in
/// different modes — as long as the toggle is never tapped, because the override wins
/// once it is set.
struct ToggleMoversModeIntent: AppIntent {
    static var title: LocalizedStringResource { "Switch between Market and Holdings" }
    /// The tile redraws in place; opening the app would defeat the point of the button.
    static var openAppWhenRun: Bool { false }

    @Parameter(title: "Show")
    var mode: MoversMode

    init() {}
    init(mode: MoversMode) { self.mode = mode }

    func perform() async throws -> some IntentResult {
        WidgetModeOverride.set(mode)
        return .result()
    }
}

/// Where the in-tile toggle's choice lives.
///
/// Separate from `MoversConfigurationIntent.mode` because the two answer different
/// questions: the configuration is what the user chose when they placed the tile, the
/// override is what they tapped since. The provider prefers the override, so an
/// untouched tile keeps behaving exactly as it always did.
enum WidgetModeOverride {
    private static let key = "widget.movers.modeOverride"

    static func current() -> MoversMode? {
        guard let raw = WidgetSharedDefaults.store?.string(forKey: key) else { return nil }
        return MoversMode(rawValue: raw)
    }

    static func set(_ mode: MoversMode) {
        WidgetSharedDefaults.store?.set(mode.rawValue, forKey: key)
    }
}
