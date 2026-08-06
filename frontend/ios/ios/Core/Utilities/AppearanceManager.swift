//
//  AppearanceManager.swift
//  ios
//
//  Applies + persists the user's appearance choice (System / Dark / Light) by
//  setting `overrideUserInterfaceStyle` on every connected window scene — the
//  robust way to theme across fullScreenCovers/sheets (unlike `.preferredColorScheme`,
//  which does not reliably reach content presented outside the view tree).
//
//  STATUS (light-mode sweep done): the per-view `.preferredColorScheme(.dark)` and
//  `.toolbarColorScheme(.dark)` modifiers that used to pin every screen dark have been
//  removed, and `AppColors` surface/text tokens are now adaptive (see Theme/AppTheme.swift),
//  so Light and System actually re-theme the app. Appearance is driven two ways that always
//  agree because both read `storageKey`:
//    1. a reactive root `.preferredColorScheme` in `iosApp` (correct from frame 0 — no
//       cold-launch flash — and updates instantly on change), and
//    2. this window-level override (reliable across sheets / fullScreenCovers, and what
//       remote settings-sync re-applies on hydrate).
//  Default is `.dark` to preserve the shipped look until the user opts into Light/System.
//

import SwiftUI

@MainActor
enum AppearanceManager {

    /// UserDefaults key — also the synced-preferences key (see SettingsSyncManager).
    static let storageKey = "appearance_mode"

    /// The ONE parse. `iosApp`'s root `.preferredColorScheme` and this type must
    /// agree — the two-writer design in the header comment rests on it — so there is
    /// a single implementation and both call it. It used to be two independent copies
    /// of the same expression, which is a correctness claim maintained by hand.
    ///
    /// Tolerant, because a value can arrive from `SettingsSyncManager`'s server blob
    /// rather than from `set(_:)`. Unparseable still means `.dark` (the shipped look),
    /// but it says so out loud rather than silently.
    static func parse(_ raw: String?) -> AppearanceMode {
        guard let raw, !raw.isEmpty else { return .dark }
        guard let mode = AppearanceMode(tolerantRawValue: raw) else {
            #if DEBUG
            print("⚠️ [Appearance] unrecognised appearance_mode \"\(raw)\" → defaulting to Dark")
            #endif
            return .dark
        }
        return mode
    }

    /// The persisted choice, defaulting to `.dark` (current app look).
    static var current: AppearanceMode {
        parse(UserDefaults.standard.string(forKey: storageKey))
    }

    /// Persist + apply a new choice.
    static func set(_ mode: AppearanceMode) {
        UserDefaults.standard.set(mode.rawValue, forKey: storageKey)
        apply(mode)
        #if DEBUG
        AppearanceProbe.dump("set→\(mode.rawValue)")
        #endif
    }

    /// Apply the stored choice. Three call sites, each covering a different gap:
    ///   1. launch (`iosApp.task`) — the initial application;
    ///   2. foreground (`iosApp` didBecomeActive) — windows created while we were
    ///      backgrounded (scene reconnect, a UIKit alert/overlay window) never got the
    ///      override, because `apply(_:)` only touches windows that exist at call time;
    ///   3. settings hydrate / sign-out (`SettingsSyncManager`) — the stored value
    ///      itself changed underneath us.
    static func applyStored() {
        apply(current)
    }

    /// Push the interface style onto every window of every connected scene — AND onto
    /// each window's view-controller chain.
    ///
    /// The controller half is load-bearing, not belt-and-braces. SwiftUI implements the
    /// root `.preferredColorScheme` by writing `overrideUserInterfaceStyle` on the root
    /// UIHostingController, NOT on the window, and **a view controller's own override
    /// beats the window's for its entire subtree**. So writing only the window here was
    /// silently masked: the window flipped instantly and nothing on screen changed,
    /// because the hosting controller still carried the previous style.
    ///
    /// That made this whole type inert and left the real switch to SwiftUI's next
    /// `@AppStorage`-driven body pass — which is what "the mode doesn't change until
    /// later" was. Measured directly: after `set(.light)` the window read `light` while
    /// the root controller (and the pixels) were still `dark`.
    ///
    /// Writing both means the change lands in the same runloop turn as the tap. SwiftUI
    /// still re-asserts its value on the following pass, but by then it is writing the
    /// value we already wrote, so the two agree instead of racing.
    ///
    /// The presented chain is walked because sheets and `fullScreenCover`s are separate
    /// presentations — Account is a `fullScreenCover` — and a presented controller that
    /// has its own override would otherwise keep the old theme while everything behind
    /// it changed.
    static func apply(_ mode: AppearanceMode) {
        let style = mode.interfaceStyle
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene else { continue }
            for window in windowScene.windows {
                window.overrideUserInterfaceStyle = style
                var controller = window.rootViewController
                while let current = controller {
                    current.overrideUserInterfaceStyle = style
                    controller = current.presentedViewController
                }
            }
        }
    }
}
