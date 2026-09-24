//
//  AlertDestinationCover.swift
//  ios
//
//  Opens an alert's chosen destination the way the rest of the app opens a ticker.
//

import SwiftUI

/// Presents an `AlertDestination` in a `.fullScreenCover` that writes its **own**
/// `NavigationStack` — the shape `HomeDashboardView` uses for a Daily Scanners tap.
///
/// WHY THIS EXISTS
/// ---------------
/// Both Activity detail screens are sheets, and both used to PUSH the destination inside the
/// sheet's stack:
///
///     Tracking NavigationStack
///     └─ .sheet → a second NavigationStack
///        └─ NotificationDetailView
///           └─ .navigationDestination → TickerDetailView   ← inside a sheet
///
/// `TickerDetailView` cannot survive that. It declares SEVEN `.sheet` modifiers — search, price
/// alerts, share, upgrades/downgrades, technical analysis — and inside the alert sheet every one
/// of them is a sheet presented from a sheet. A tester reported the search icon doing nothing;
/// search was simply the one they tapped, and `WhaleProfileView`, the other destination, has five
/// sheets of its own. The screen was also visibly slow to arrive, being the heaviest in the app
/// rendered inside a modal presentation.
///
/// Every other caller gets this right — `HomeDashboardView`, `ThemeDetailView`,
/// `SignalTickerDetailView` and `SearchView` all give the ticker screen a presentation context
/// with a stack of its own. Alerts was the only place that nested
/// it as a destination of another screen.
///
/// ⚠️ THE `NavigationStack` INSIDE THE COVER IS THE WHOLE FIX. A cover starts a new presentation
/// context, and `.navigationDestination` is inert without a stack ancestor *in that same context*.
/// Without it `TickerDetailView`'s search-result push (`$selectedSearchResult`) silently does
/// nothing — the search sheet closes and the screen just sits there. Deleting the stack would look
/// harmless and reintroduce half the original bug.
///
/// ⚠️ `NotificationRouteContent` has no stack of its own, on purpose — any stack-owning wrapper
/// around it would double-stack inside the one written here. (There was one,
/// `NotificationRouteDestination`; it was removed with the push path that skipped the detail.)
///
/// THREE owners use it, all with the same sheet → cover hand-off: `AlertsTabContent`
/// (notification rows), `TrackingView` (digest cards) and `ContentView` (a TAPPED PUSH's detail).
///
/// USAGE — on the view that owns the sheet, with the sheet's `onDismiss` doing the hand-off:
///
///     .sheet(item: $selected, onDismiss: { opened = pending; pending = nil }) { … }
///     .alertDestinationCover($opened)
///
struct AlertDestinationCover: ViewModifier {
    @Environment(\.appState) private var appState

    @Binding var destination: AlertDestination?

    func body(content: Content) -> some View {
        content.fullScreenCover(item: $destination) { destination in
            NavigationStack {
                destinationView(destination)
            }
            // Both spellings, matching how `ProfileView` is presented from `TrackingView`:
            // screens under here read the state either way (`TickerDetailView` takes
            // `AppState.self`, `TrackingView` takes the key path) and a cover does not
            // inherit either for free.
            .environment(appState)
            .environment(\.appState, appState)
        }
    }

    /// The ONE five-way dispatch. Deliberately here rather than in the two detail screens that
    /// used to hold a copy each — a second copy of this switch is exactly the "taps from the
    /// inbox go to the right place but taps from the banner don't" drift that
    /// `NotificationRouteContent`'s file header warns about.
    @ViewBuilder
    private func destinationView(_ destination: AlertDestination) -> some View {
        switch destination.target {
        case .whale(let whaleId):
            WhaleProfileView(whaleId: whaleId)
        default:
            if let route = destination.route {
                NotificationRouteContent(route: route)
            }
        }
    }
}

extension View {
    /// Open an alert's destination in its own presentation context, like Home's Daily Scanners.
    ///
    /// Apply to the view that owns the alert-detail sheet, and set the binding from that sheet's
    /// `onDismiss` — a cover cannot be presented while its sheet is still on screen.
    func alertDestinationCover(_ destination: Binding<AlertDestination?>) -> some View {
        modifier(AlertDestinationCover(destination: destination))
    }
}
