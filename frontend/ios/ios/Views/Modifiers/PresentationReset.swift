//
//  PresentationReset.swift
//  ios
//
//  Takes down everything a tab has presented, when something deep inside asks to be seen
//  somewhere else.
//

import SwiftUI

/// Runs `clear` whenever `AppState.presentationResetToken` is bumped.
///
/// WHY THIS EXISTS
/// ---------------
/// "AI Deep Research" parks `AppState.pendingResearchTicker` and calls `dismiss()`. The route
/// works — `ContentView` switches to the Research tab — but `dismiss()` closes exactly ONE
/// presentation level, and the ticker screen is usually deeper than that:
///
///     Home tab (no NavigationStack — every Home destination is modal)
///     └─ .fullScreenCover  ThemeDetailView
///        └─ .fullScreenCover  TickerDetailView   ← dismiss() closes only this
///
/// so the tab came forward BEHIND the theme cover and the button read as broken. Same shape via
/// Signals, and via `SearchView`, which is the highest-traffic entry point of all.
///
/// ⚠️ APPLY THIS AT THE TAB ROOT, AND CLEAR THE ROOT'S OWN STATE.
/// That is the whole design, not a convention. Every one of those chains bottoms out in a tab
/// root's `@State`, so nil-ing that single value unwinds the entire nest in one animation —
/// including a screen presented three deep, and including covers that do not exist yet. Applying
/// it to a PRESENTED screen instead would fix only the chains someone remembered to annotate,
/// which is the failure mode the route itself already shipped once.
///
/// ⚠️ Do NOT drive this off `pendingResearchTicker`. `ContentView` clears that the instant it
/// consumes it, so an observer would race the clear — the "ONE OWNER PER ROUTE KIND" hazard the
/// push-route handler documents. `presentationResetToken` only ever increases, so every observer
/// sees every bump.
///
/// USAGE — on a tab root, listing that root's own presentation state:
///
///     .onPresentationReset {
///         selectedTicker = nil
///         themeDetailTarget = nil
///         showSearch = false
///     }
///
struct PresentationReset: ViewModifier {
    @Environment(AppState.self) private var appState

    let clear: () -> Void

    func body(content: Content) -> some View {
        content.onChange(of: appState.presentationResetToken) { oldValue, newValue in
            // Guard the initial/no-op case. `.onChange` without `initial:` only fires on a real
            // change, but `discardDataForEndedSession()` resets the token to 0, which IS a change
            // — and tearing down presentations on sign-out is the session's job, not ours.
            guard newValue > oldValue else { return }
            clear()
        }
    }
}

extension View {
    /// Clear this view's own presentation state when something asks to be seen elsewhere.
    /// See `PresentationReset` — this belongs on a TAB ROOT.
    func onPresentationReset(_ clear: @escaping () -> Void) -> some View {
        modifier(PresentationReset(clear: clear))
    }
}
