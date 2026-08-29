//
//  ErrorPresentationHost.swift
//  ios
//
//  Re-hosts the app's global failure surfaces inside a `.fullScreenCover`, because the root's
//  copies cannot reach one.
//
//  WHY THIS EXISTS
//  ---------------
//  `ErrorToastView` (`AppState.currentError`) and `ToastView` (`AppState.toastMessage`) are
//  attached to the ROOT view in `iosApp.swift`. A `.fullScreenCover` is a separate presented
//  `UIViewController` drawn ABOVE that hierarchy, so both surfaces render BEHIND a cover.
//  Anything raised from inside one is invisible — not delayed, not clipped: never seen at all.
//
//  This is the same defect `GlobalAudioOverlay` was written for ("A full-screen cover draws
//  ABOVE RootContainerView, hiding the root audio overlay — so every covered screen renders the
//  player itself via this modifier instead"), and this modifier is its mirror for errors.
//
//  WHAT WAS ACTUALLY BROKEN
//  ------------------------
//  Account is a `.fullScreenCover`, presented from seven sites. Three live paths were silent:
//
//    * `AppSettingsView.deleteAccount()` assigned `appState.currentError` — so a failed account
//      deletion, a destructive irreversible action, told the user NOTHING.
//    * `AppState.claimGuestDataIfNeeded()` raises `showToast("We couldn't move your saved
//      tickers…")`. Sign-in is a `.sheet` presented from inside this cover, so the one warning
//      that matters at exactly that moment was drawn behind it.
//    * `AppActions.reportMutationFailure` routes to the same two surfaces, so it was not
//      available as a fix either.
//
//  The file already knew: `AppSettingsView` carries a comment saying a toast raised from that
//  screen "is drawn behind the cover and never seen. Verified on the Simulator." The workaround
//  there was a local `.alert` per call site, which does not generalise — this does.
//
//  ⚠️ THE SHEETS MUST BE LOCAL TOO. The root's `showPaywallFromError` / `showSignInFromError`
//  sheets are presented from the root, so an "Upgrade" or "Sign In" tap on a toast rendered here
//  would open a sheet the user cannot see. This modifier owns its own, presented from inside the
//  cover, which is why it is a `ViewModifier` with state rather than two bare `.overlay`s.
//
//  USAGE — apply to the ROOT view of any `.fullScreenCover` whose content can raise an error:
//
//      NavigationStack { … }
//          .errorPresentationHost()
//

import SwiftUI

struct ErrorPresentationHost: ViewModifier {
    @Environment(\.appState) private var appState

    /// Local mirrors of the root's two error-action destinations. Presented from INSIDE the
    /// cover so they are actually visible — see the warning in the file header.
    @State private var showBuyCreditsFromError = false
    @State private var showSignInFromError = false

    func body(content: Content) -> some View {
        content
            .overlay {
                if let error = appState.currentError {
                    ErrorToastView(
                        error: error,
                        onDismiss: { appState.clearError() },
                        onAction: { handleErrorAction($0) }
                    )
                }
            }
            .overlay {
                if let toast = appState.toastMessage {
                    ToastView(message: toast)
                }
            }
            // ⚠️ `appState.signInPrompt` is deliberately NOT re-presented here.
            //
            // It is bound to a `.sheet(item:)` on the root, and a second binding to the same
            // item would have TWO presentation modifiers racing for one value: UIKit cannot
            // present from a controller that is already presenting the cover, so the pair
            // resolves to "only presenting a single sheet is supported" and one of them is
            // dropped — which can be the visible one. The two overlays above have no such
            // problem: they are plain view-hierarchy, drawn inside this cover, competing with
            // nothing.
            //
            // A `requestSignIn(for:)` raised from inside a cover is a separate problem with a
            // different fix, and is out of scope here rather than papered over with a racing
            // duplicate.
            //
            // ⚠️ IT IS NO LONGER "UNCONFIRMED". Verified on the simulator: the guest banner
            // on Profile → Notifications called `AppActions.requestSignIn(for:)` and NOTHING
            // HAPPENED — the root cannot present while Account's cover is up, so the prompt
            // is simply swallowed. The fix that works is the one this file already uses for
            // its own two destinations: a cover-LOCAL `.sheet` presenting `SignInView`
            // directly, which is what `NotificationsSettingsView` now does. Any other screen
            // inside a cover needing sign-in must do the same, not call `requestSignIn`.

            // A 402 INSUFFICIENT_CREDITS carries `action: "upgrade"`. Buy Credits rather than the
            // subscription paywall, matching the root's routing and its reasoning: the user was
            // mid-action and a one-tap top-up unblocks them where a monthly commitment is where
            // they bounce.
            .sheet(isPresented: $showBuyCreditsFromError) {
                BuyCreditsView()
                    .environment(\.appState, appState)
            }
            .sheet(isPresented: $showSignInFromError) {
                SignInView()
                    .environment(appState)
            }
    }

    /// Mirrors `iosApp.handleErrorAction`. Kept as a copy rather than shared because the root's
    /// version drives the root's `@State` flags; the two differ only in WHICH presentation
    /// hosts the destination, which is the entire point of this type.
    private func handleErrorAction(_ action: ErrorAction) {
        switch action {
        case .upgrade:
            appState.clearError()
            showBuyCreditsFromError = true
        case .signIn:
            appState.clearError()
            showSignInFromError = true
        case .retry, .waitAndRetry, .waitForConnection, .goBack, .fixInput, .contactSupport:
            // No global destination: retry/back are owned by whichever screen raised the error,
            // and the rest are acknowledgements. Dismiss is the honest behaviour.
            appState.clearError()
        }
    }
}

extension View {
    /// Render `AppState`'s global error/toast/sign-in surfaces from INSIDE a `.fullScreenCover`.
    ///
    /// Apply to the cover's root view. Without it, every failure raised inside the cover is
    /// drawn behind it and the user sees nothing — see the file header.
    func errorPresentationHost() -> some View {
        modifier(ErrorPresentationHost())
    }
}
