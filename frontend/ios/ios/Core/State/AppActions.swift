//
//  AppActions.swift
//  ios
//
//  A narrow bridge from the app's singleton services to AppState.
//
//  Why this exists: several long-lived services (`WhaleService`, `PortfolioStore`, the Learn
//  stores) are singletons that predate dependency injection here, and they are exactly the
//  places where a failed user action used to disappear into a `print`. They need two things —
//  "am I signed in?" and "surface this failure" — and nothing else.
//
//  This is the same shape the codebase already uses for `SettingsSyncManager.configure(appState:)`
//  and `PushNotificationManager.configure(appState:)`: a weak reference, set once at the app
//  root. It is deliberately NOT a general-purpose global `AppState` accessor — Views and
//  ViewModels must keep using `@Environment(AppState.self)` / init injection, per
//  .claude/rules/ios-swiftui.md. Widening this surface would quietly reintroduce the singleton
//  state coupling those rules exist to prevent.
//

import Foundation

@MainActor
final class AppActions {

    static let shared = AppActions()

    /// Weak: `AppState` owns the app's lifetime, never this.
    private weak var appState: AppState?

    private init() {}

    func configure(appState: AppState) {
        self.appState = appState
    }

    // MARK: - Queries

    /// Whether a usable credential is armed right now.
    ///
    /// Note this is `.authenticated` specifically, NOT "has a stored token": during `.restoring`
    /// the client token is deliberately disarmed, so a write issued then would land on the guest
    /// partition rather than the user's account. Treating that window as signed-out is the
    /// honest answer.
    var isSignedIn: Bool { appState?.auth.isAuthenticated ?? false }

    /// A credential IS stored but is not armed yet — the `.restoring` window.
    ///
    /// `isSignedIn == false` answers two very different questions with one word: "this is a
    /// deliberate guest" and "this is your account, mid-reconnect". For ACTIONS that collapse
    /// is correct and deliberate (see above). For COPY it is not: telling someone who is signed
    /// in to sign in is the same defect `.claude/rules/auth.md` §5 documents, and the restore
    /// backoff runs `2s → 8s → 30s → 120s → 300s` indefinitely, so the window is not brief.
    ///
    /// Use this to choose what to SAY, never to decide whether a write may proceed.
    var isRestoringSession: Bool { appState?.hasUnusedStoredCredential ?? false }

    // MARK: - Actions

    /// Raise the shared "this needs an account" prompt.
    func requestSignIn(for feature: String?) {
        appState?.requestSignIn(for: feature)
    }

    /// Report a failed USER-INITIATED mutation.
    ///
    /// The single funnel replacing ~20 hand-rolled `print`-and-revert blocks. Routes an
    /// auth failure to the sign-in prompt and everything else to a toast, and always logs —
    /// so a failure is never invisible, in any build configuration.
    ///
    /// - Parameters:
    ///   - action: an infinitive phrase completing "Couldn't …", e.g. "follow this investor".
    ///   - signInFeature: phrase for the sign-in prompt when the failure turns out to be an auth
    ///     one; falls back to `action` when omitted.
    func reportMutationFailure(_ error: Error, action: String, signInFeature: String? = nil) {
        appState?.reportMutationFailure(error, action: action, signInFeature: signInFeature)
    }
}
