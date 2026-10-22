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
/// …AND ON EVERY VIEW A TAB ROOT REACHES WITHOUT CROSSING ONE OF THOSE COVERS. A root's reset
/// unwinds what it presented, but not what its on-screen children present: the header's logo
/// quote, a disclaimer atom's sheet, a popover on a Home card, and every cover on a PUSHED
/// screen that the root deliberately keeps (Learn's Money Moves, Library and Journey). Each of
/// those owns its `@State`, so each clears it here — otherwise a widget or push tap lands
/// underneath it. `test_ios_widget_deep_links.py` walks the view tree from `RootView` and fails
/// on any presentation such a view leaves out.
///
/// USAGE — listing that view's own presentation state, nothing but unconditional clears:
///
///     .onPresentationReset {
///         selectedTicker = nil
///         themeDetailTarget = nil
///         showSearch = false
///     }
///
struct PresentationReset: ViewModifier {
    /// Optional on purpose: the modifier now sits on atoms and on view modifiers that hundreds
    /// of `#Preview`s render with no `AppState`, and a required read would crash every one of
    /// them. In the app the environment always holds it — `iosApp` injects it on `RootView`,
    /// and it reaches presented content too (Home's ticker cover reads it without re-injection).
    @Environment(AppState.self) private var appState: AppState?

    let clear: () -> Void

    func body(content: Content) -> some View {
        content.onChange(of: appState?.presentationResetToken) { oldValue, newValue in
            // Guard the initial/no-op case. `.onChange` without `initial:` only fires on a real
            // change, but `discardDataForEndedSession()` resets the token to 0, which IS a change
            // — and tearing down presentations on sign-out is the session's job, not ours.
            guard let oldValue, let newValue, newValue > oldValue else { return }
            clear()
        }
    }
}

extension View {
    /// Clear this view's own presentation state when something asks to be seen elsewhere.
    /// See `PresentationReset` — this belongs on a TAB ROOT and on every view it reaches.
    func onPresentationReset(_ clear: @escaping () -> Void) -> some View {
        modifier(PresentationReset(clear: clear))
    }
}
