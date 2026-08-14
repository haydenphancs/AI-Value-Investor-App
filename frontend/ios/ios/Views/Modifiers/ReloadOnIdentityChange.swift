//
//  ReloadOnIdentityChange.swift
//  ios
//
//  One place for "the signed-in identity changed, so what is on screen belongs to
//  someone else." Every tab root needs this and each one was missing it differently.
//

import SwiftUI

/// Runs `action` when the signed-in identity changes.
///
/// WHY THIS EXISTS. Every tab root caches its data in a `@StateObject` ViewModel, and the
/// tabs are **opacity-mounted** — all five live in one `ZStack` for the whole process, so a
/// ViewModel is built once at launch and never rebuilt. Nothing in `AppState` reaches into
/// them: `discardDataForEndedSession()` resets the Learn stores, `WhaleService`,
/// `SettingsSyncManager` and the push registration, but no tab ViewModel. That left the same
/// defect in four places, each expressed differently:
///
/// - **Research** — `requiresSignInForReports` latched at launch (during `.restoring`) and
///   told a signed-in user to sign in for the rest of the run.
/// - **Updates** — `loadIfNeeded()` early-returns on `hasLoadedOnce`, which latches once and
///   is never reset, so the feed is fetched exactly once per process.
/// - **Home** — `loadIfStale()` stamps `lastLoadedAt` on ANY successful load, including one
///   made as a guest, so that load stays "fresh" for 5 minutes after signing in.
/// - **Tracking** — reads `isActiveTab` nowhere and has no activation reload at all.
///
/// The sign-OUT direction is the serious one: without this, the next person to use the device
/// sees the previous account's watchlist, holdings and reports until some unrelated trigger
/// happens to refetch. That is the class of leak `.claude/rules/auth.md` §7 exists to prevent,
/// and the per-tab caches were simply never part of it.
///
/// ## Which transitions fire
///
/// | from → to | fires | why |
/// |---|---|---|
/// | anything → `.authenticated` | ✅ | identity confirmed — load THIS account's data |
/// | `.authenticated` → `.unauthenticated` | ✅ | session ended — the data on screen is not theirs |
/// | anything → `.restoring` | ❌ | hold. The token is deliberately disarmed here, so a reload would refetch as the guest and *replace* good account data with guest data on a transient network blip |
/// | `.restoring` → `.unauthenticated` | ✅ | restore concluded there is no session; anything cached under a previous one must go |
///
/// Deliberately keyed on `auth.status` rather than `user.profile?.id`: the profile is
/// populated asynchronously after the status flips, so keying on it would fire late and, on a
/// failed profile fetch, not at all.
struct ReloadOnIdentityChange: ViewModifier {
    @Environment(AppState.self) private var appState

    /// Clear identity-scoped state, then reload. Clearing matters as much as reloading —
    /// on sign-out the reload may fail or be slow, and stale account data must not sit on
    /// screen in the meantime.
    let action: () async -> Void

    func body(content: Content) -> some View {
        content.onChange(of: appState.auth.status) { oldStatus, newStatus in
            guard Self.isIdentityChange(from: oldStatus, to: newStatus) else { return }
            Task { await action() }
        }
    }

    /// Split out so the decision is testable and stated once. See the table above.
    static func isIdentityChange(from old: AuthStatus, to new: AuthStatus) -> Bool {
        guard old != new else { return false }
        switch new {
        case .authenticated:
            return true
        case .unauthenticated:
            // `.unknown → .unauthenticated` is the ordinary cold launch of a guest: there is
            // no previous identity and nothing cached yet, so firing would just double the
            // launch fetch.
            return old != .unknown
        case .restoring, .loading, .unknown:
            return false
        }
    }
}

extension View {
    /// Reload this surface when the signed-in identity changes.
    ///
    /// Pair it with the tab-activation trigger (`.task(id: isActiveTab)`), which covers a load
    /// that merely raced session restore. This one covers the case that has no other cure:
    /// signing in or out while the user is already looking at the tab.
    func reloadOnIdentityChange(_ action: @escaping () async -> Void) -> some View {
        modifier(ReloadOnIdentityChange(action: action))
    }
}
