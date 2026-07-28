//
//  AppearanceManager.swift
//  ios
//
//  Applies + persists the user's appearance choice (System / Dark / Light) by
//  setting `overrideUserInterfaceStyle` on every connected window scene — the
//  robust way to theme across fullScreenCovers/sheets (unlike `.preferredColorScheme`,
//  which only affects one view subtree).
//
//  NOTE: the app currently hard-codes `.preferredColorScheme(.dark)` in ~387 views,
//  which locally override this window setting. So today the effective look stays dark
//  regardless of the choice; the selection still persists + syncs. Removing those
//  forced-dark modifiers app-wide (so Light/System fully render) is a tracked
//  follow-up. Default is `.dark` to preserve the current look.
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
