//
//  LearnAudioPaywall.swift
//  ios
//
//  Presents the plan sheet when a locked listener tries to play Learn narration.
//
//  One modifier rather than a sheet on each screen: the refusal happens down in the two audio
//  ENGINES (see LearnAudioEntitlement), so the tap that triggers it can come from a Money
//  Moves hero, an article action bar, a book listen row, a book core header, a Journey card,
//  the mini player, or a Lock Screen remote command. Wiring a presenter per tap site would be
//  six copies of the same three lines, each free to drift.
//

import SwiftUI
import Combine

private struct LearnAudioPaywallModifier: ViewModifier {
    @Environment(\.appState) private var appState
    @ObservedObject private var entitlement = LearnAudioEntitlement.shared
    @State private var showPaywall = false

    func body(content: Content) -> some View {
        content
            .onReceive(entitlement.upgradeRequested) { _ in
                showPaywall = true
            }
            // A PLAN gate, so the plan sheet — not the BuyCredits route a 402 takes. No amount
            // of credits unlocks narration.
            //
            // ⚠️ `.environment(\.appState, appState)` is REQUIRED: PaywallView reads the custom
            // `\.appState` key, a sheet inherits neither environment automatically, and without
            // it the sheet resolves that key's default `AppState()` and highlights the wrong
            // "current plan".
            .sheet(isPresented: $showPaywall) {
                PaywallView()
                    .environment(\.appState, appState)
            }
    }
}

extension View {
    /// Present the plan sheet when Learn narration is refused for this account.
    ///
    /// Attach to any screen that can start narration. Safe to attach more than once in a
    /// hierarchy — each instance owns its own `showPaywall`, and only the innermost presenting
    /// view will actually show a sheet.
    func learnAudioPaywall() -> some View {
        modifier(LearnAudioPaywallModifier())
    }
}
