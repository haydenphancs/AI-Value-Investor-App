//
//  LearnAudioPaywall.swift
//  ios
//
//  Presents the plan sheet when a locked listener tries to play Learn narration.
//
//  One modifier rather than a sheet on each screen: the refusal happens down in the two audio
//  ENGINES (see LearnAudioEntitlement), so the tap that triggers it can come from a Money
//  Moves hero, an article action bar, a book listen row, or a book core header.
//  the mini player, or a Lock Screen remote command. Wiring a presenter per tap site would be
//  six copies of the same three lines, each free to drift.
//

import SwiftUI
import Combine

private struct LearnAudioPaywallModifier: ViewModifier {
    @Environment(\.appState) private var appState
    @ObservedObject private var entitlement = LearnAudioEntitlement.shared
    @State private var showPaywall = false
    /// Where this presenter sits, read when a request arrives — see `PaywallAnchor.isFrontmost`.
    @State private var anchor = PaywallAnchor()

    func body(content: Content) -> some View {
        content
            .background(
                PaywallAnchorView(anchor: anchor)
                    .frame(width: 0, height: 0)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            )
            .onReceive(entitlement.upgradeRequested) { _ in
                // EVERY mounted presenter hears this: Learn's root and each book / article /
                // chapter cover stacked on it. Only the one nothing is presented over answers. The
                // others used to set their sheets too; SwiftUI queued those behind the cover on top
                // and presented them one after another once it closed — up to four plan sheets
                // for one tap (Learn → Library → Book → Chapter).
                guard anchor.isFrontmost else { return }
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
                PaywallView(context: .learnAudio)
                    .environment(\.appState, appState)
            }
            // Each instance owns its sheet, on screens no tab root resets (the covers), so each
            // clears it for a widget or push tap.
            .onPresentationReset {
                showPaywall = false
            }
    }
}

/// The presenter's place on screen, held weakly so the modifier can ask UIKit about it when a
/// request arrives (not before: a cover can go up after the presenter appeared).
@MainActor
private final class PaywallAnchor {
    weak var view: UIView?

    /// Whether a sheet from this presenter is one the user would see: its view is in a window (a
    /// full-screen cover takes the screens under it out of the window) and no controller in its
    /// host chain is presenting anything over it. Unknown — the probe view not made yet — answers
    /// true, so a presenter that cannot be located still shows the plan rather than nothing.
    var isFrontmost: Bool {
        guard let view else { return true }
        guard view.window != nil else { return false }
        guard let host = view.hostViewController else { return true }
        return !sequence(first: host, next: { $0.parent }).contains { $0.presentedViewController != nil }
    }
}

/// A zero-size, non-interactive UIView that hands itself to a `PaywallAnchor`.
private struct PaywallAnchorView: UIViewRepresentable {
    let anchor: PaywallAnchor

    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        anchor.view = view
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) {
        anchor.view = uiView
    }
}

extension View {
    /// Present the plan sheet when Learn narration is refused for this account.
    ///
    /// Attach to a tab root and to each COVER that can start narration — not to a push: the tab
    /// root's presenter sits outside its NavigationStack and already covers every push in it, and
    /// two presenters with nothing over them would both answer. Every instance hears each request;
    /// only the frontmost one presents (see `PaywallAnchor.isFrontmost`).
    func learnAudioPaywall() -> some View {
        modifier(LearnAudioPaywallModifier())
    }
}
