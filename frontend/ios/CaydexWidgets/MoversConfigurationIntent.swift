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
            // "Tracked by Caydex", not "the market": market mode ranks inside the
            // universe Caydex actually follows — the only tickers with the volatility
            // baseline and the cached explanation this widget needs. Naming it honestly
            // here costs nothing and stops the widget implying it scanned every listed
            // stock in the country.
            .market: DisplayRepresentation(
                title: "Market",
                subtitle: "Biggest mover among the stocks Caydex tracks"
            ),
            .portfolio: DisplayRepresentation(
                title: "My Holdings",
                subtitle: "Biggest mover in your active group"
            ),
        ]
    }
}

struct MoversConfigurationIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource { "Biggest Mover" }
    static var description: IntentDescription {
        IntentDescription("Choose whether the widget follows the market or your own holdings.")
    }

    // Defaults to market because it needs no identity: `/widget/market-mover` is a
    // public route, so a signed-out user who adds the widget still sees real content
    // rather than a sign-in prompt on their Home Screen.
    @Parameter(title: "Show", default: .market)
    var mode: MoversMode
}
