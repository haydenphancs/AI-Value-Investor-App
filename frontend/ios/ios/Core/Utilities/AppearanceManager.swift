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

    /// The persisted choice, defaulting to `.dark` (current app look).
    static var current: AppearanceMode {
        let raw = UserDefaults.standard.string(forKey: storageKey)
        return AppearanceMode(rawValue: raw ?? "") ?? .dark
    }

    /// Persist + apply a new choice.
    static func set(_ mode: AppearanceMode) {
        UserDefaults.standard.set(mode.rawValue, forKey: storageKey)
        apply(mode)
    }

    /// Apply the stored choice (call on launch).
    static func applyStored() {
        apply(current)
    }

    /// Push the interface style onto every window of every connected scene.
    static func apply(_ mode: AppearanceMode) {
        let style = mode.interfaceStyle
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene else { continue }
            for window in windowScene.windows {
                window.overrideUserInterfaceStyle = style
            }
        }
    }
}
