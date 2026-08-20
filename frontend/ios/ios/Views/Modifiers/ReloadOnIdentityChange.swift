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
    @Environment(\.isActiveTab) private var isActiveTab

    /// Clear identity-scoped state, then reload IF this tab is on screen. Clearing matters
    /// as much as reloading — on sign-out the reload may fail or be slow, and stale account
    /// data must not sit on screen in the meantime — so the handler must clear
    /// unconditionally and gate only the fetch. See "Clear eagerly, fetch lazily" below.
    let action: (_ isActiveTab: Bool) async -> Void

    /// The identity generation this view has already reacted to.
    ///
    /// Seeded on appear, which is the whole cold-launch fix. `auth.status` moves
    /// `.unknown → .restoring → .authenticated` on every launch of a signed-in user, and the
    /// final hop looks exactly like a sign-in to a status observer. It is not: the stored
    /// credential is armed BEFORE any tab mounts, so the loads already on the wire were
    /// answered for that same account, and `.authenticated` only announces who it was.
    /// `AppState.identityGeneration` is what distinguishes discovering an identity from
    /// changing one, and it does not move on that hop — so a launch reloads nothing, while a
    /// real sign-in, sign-out or account switch still reloads everything.
    ///
    /// Measured: this hop alone was re-fetching `/home/dashboard`, the four Research calls,
    /// the five Tracking calls and the two Updates calls on every single launch.
    @State private var handledGeneration: Int?

    func body(content: Content) -> some View {
        content
            .onAppear {
                if handledGeneration == nil { handledGeneration = appState.identityGeneration }
            }
            .onChange(of: appState.auth.status) { oldStatus, newStatus in
                guard Self.isIdentityChange(from: oldStatus, to: newStatus) else { return }
                let generation = appState.identityGeneration
                guard handledGeneration != generation else { return }
                handledGeneration = generation
                let active = isActiveTab
                Task { await action(active) }
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
    ///
    /// ## Clear eagerly, fetch lazily
    ///
    /// The handler receives `isActiveTab` and MUST clear its identity-scoped state **before**
    /// consulting it, then fetch only when the tab is on screen.
    ///
    /// All five tabs are opacity-mounted in one `ZStack`, so this modifier is live on four tab
    /// roots at once and a single sign-in used to fan out eleven requests — five for Tracking,
    /// four for Research, two for Updates — for tabs the user was not looking at. Deferring the
    /// FETCH costs nothing: each tab root already re-loads on activation via
    /// `.task(id: isActiveTab)`, and a cleared ViewModel has no freshness stamp to suppress it.
    ///
    /// Deferring the CLEAR would be a data leak (`.claude/rules/auth.md` §7) — the next person
    /// to use the device would find the previous account's watchlist and holdings still in a
    /// hidden tab's ViewModel, ready to render the moment they tap it. That ordering is pinned
    /// by `tests/test_ios_tabs_reload_on_identity_change.py`.
    func reloadOnIdentityChange(_ action: @escaping (_ isActiveTab: Bool) async -> Void) -> some View {
        modifier(ReloadOnIdentityChange(action: action))
    }
}
