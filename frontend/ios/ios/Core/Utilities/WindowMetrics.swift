//
//  WindowMetrics.swift
//  ios
//
//  The key window's safe-area insets, read from UIKit — independent of where a SwiftUI view
//  happens to sit in the layout.
//
//  WHY: `FullScreenAudioPlayer` has to span the whole window in every host (the root overlay
//  AND the `.overlay` inside a cover), so its `GeometryReader` carries `.ignoresSafeArea()`.
//  A GeometryReader that ignores the safe area reports ZERO `safeAreaInsets` — so the player's
//  `.padding(.top, geometry.safeAreaInsets.top)` was 0 and its header sat inside the Dynamic
//  Island band (TestFlight 1.0(8), measured to the point: capsule ≈14pt, header centre ≈55pt).
//  Keeping the full-window frame matters: its `.move(edge: .bottom)` transition then slides
//  it fully off-screen, and it ignores the keyboard. So the insets come from the window.
//
//  The app is portrait-only, so these are constant for a device once the window exists.
//

import UIKit

@MainActor
enum WindowMetrics {

    /// The key window (or the first window) across connected scenes, if one exists yet.
    static var keyWindow: UIWindow? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let windows = scenes.flatMap { $0.windows }
        return windows.first { $0.isKeyWindow } ?? windows.first
    }

    /// The key window's safe-area insets; `.zero` before any window exists.
    static var safeAreaInsets: UIEdgeInsets {
        keyWindow?.safeAreaInsets ?? .zero
    }
}
