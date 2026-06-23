//
//  GlobalAudioOverlay.swift
//  ios
//
//  Reusable overlay that keeps the global audio player visible on `.fullScreenCover` detail
//  screens. A full-screen cover draws ABOVE RootContainerView, hiding the root audio overlay — so
//  every covered screen renders the player itself via this modifier instead. It mirrors the three
//  presentations the root uses (top status island / bottom mini player / full-screen player) and
//  registers the screen as the overlay "host" so the root suppresses its own (no double render).
//
//  Apply ONE of these shapes per screen:
//   • Asset/stock screens     → `.globalAudioOverlay(token:, forceCompact: true)`
//       Immediately collapses to the top island on open (bottom stays clear for "Ask Cay AI").
//   • Other cover screens      → `.globalAudioOverlay(token:, showBottomMiniPlayer: true)`
//       (News / Trending / lists with no chat bar) — keeps the bottom mini player so audio persists.
//   • Wiser reading screens    → `.globalAudioOverlay(token:, onNavigateToCore:)`
//       The screen renders its own bottom mini player ABOVE the chat bar and drives compact mode
//       from chat-bar focus; this modifier just supplies the island + full-screen player + host.
//

import SwiftUI

struct GlobalAudioOverlay: ViewModifier {
    @StateObject private var audioManager = AudioManager.shared

    /// Whether this screen's bottom tab is the active one. For a screen pushed inside a tab's
    /// NavigationStack, tabs are opacity-mounted (no onDisappear on a tab switch), so forced-compact
    /// must follow this reactively or it leaks the island onto other tabs. fullScreenCover screens
    /// don't inherit it → default `true` (a presented cover is always foreground).
    @Environment(\.isActiveTab) private var isActiveTab

    /// Stable per-screen token (a `@State` UUID string on the host view). Idempotently keys the
    /// compact-mode reason for this screen.
    let token: String
    /// Asset screens set this — collapse to the top island for the screen's whole lifetime.
    var forceCompact: Bool = false
    /// Cover screens with no chat bar set this — render the bottom mini player when not compact.
    var showBottomMiniPlayer: Bool = false
    /// Books pass their core-jump closure so the full-screen player's "Read" button works.
    var onNavigateToCore: ((Int) -> Void)? = nil

    func body(content: Content) -> some View {
        content
            // Reserve bottom space so scroll content / fixed CTAs aren't hidden behind the floating
            // bottom mini player (mirrors RootContainerView's MiniPlayerSafeAreaModifier, 88pt).
            .safeAreaInset(edge: .bottom) {
                if showBottomMiniPlayer && audioManager.hasActiveEpisode
                    && !audioManager.isCompactMode && !audioManager.showFullScreenPlayer {
                    Color.clear.frame(height: 88)
                }
            }
            // Top status island — NON-DYNAMIC-ISLAND DEVICES ONLY. On a DI device the system Now
            // Playing is the top indicator; on non-DI devices there is none, so keep an in-app island.
            .overlay(alignment: .top) {
                if audioManager.hasActiveEpisode && audioManager.isCompactMode
                    && !audioManager.showFullScreenPlayer && !AudioManager.hasDynamicIsland {
                    AudioStatusIsland()
                        .padding(.top, 8)
                        .transition(.asymmetric(
                            insertion: .move(edge: .top).combined(with: .opacity),
                            removal: .move(edge: .top).combined(with: .opacity)
                        ))
                }
            }
            // Bottom mini player (only for screens without their own chat-bar-stacked player)
            .overlay(alignment: .bottom) {
                if showBottomMiniPlayer && audioManager.hasActiveEpisode && !audioManager.isCompactMode && !audioManager.showFullScreenPlayer {
                    GlobalMiniPlayer()
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            // Full-screen now-playing
            .overlay {
                if audioManager.showFullScreenPlayer {
                    FullScreenAudioPlayer(onNavigateToCore: onNavigateToCore)
                        .transition(.move(edge: .bottom))
                        .zIndex(100)
                }
            }
            // Re-inject so the hosted players resolve `@EnvironmentObject AudioManager` even though the
            // environment object does NOT cross the presenting `.fullScreenCover` boundary.
            .environmentObject(audioManager)
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.isCompactMode)
            .animation(.spring(response: 0.35, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
            .animation(.spring(response: 0.4, dampingFraction: 0.85), value: audioManager.showFullScreenPlayer)
            .onAppear {
                // Asset screens collapse to the island while their tab is foreground.
                if forceCompact { audioManager.setCompactMode(isActiveTab, reason: token) }
            }
            .onChange(of: isActiveTab) { _, active in
                // Reactively release/re-engage when the tab is backgrounded/foregrounded (a pushed
                // asset screen never gets onDisappear on a tab switch — tabs are opacity-mounted).
                if forceCompact { audioManager.setCompactMode(active, reason: token) }
            }
            .onDisappear {
                // Release both forced-compact and chat-focus reasons keyed by this token.
                audioManager.setCompactMode(false, reason: token)
            }
    }
}

// MARK: - Active-tab environment

/// Injected by ContentView onto each bottom tab (`selectedTab == X`) so deeply-pushed screens can tell
/// whether their tab is foreground — needed because tabs are opacity-mounted (no appear/disappear on a
/// tab switch). Defaults to `true`: views outside the tab tree (e.g. fullScreenCover content, which does
/// NOT inherit it) are treated as foreground when presented.
struct IsActiveTabKey: EnvironmentKey {
    static let defaultValue: Bool = true
}

extension EnvironmentValues {
    var isActiveTab: Bool {
        get { self[IsActiveTabKey.self] }
        set { self[IsActiveTabKey.self] = newValue }
    }
}

extension View {
    /// Keep the global audio player visible on a `.fullScreenCover` detail screen. See
    /// `GlobalAudioOverlay` for the three usage shapes.
    func globalAudioOverlay(
        token: String,
        forceCompact: Bool = false,
        showBottomMiniPlayer: Bool = false,
        onNavigateToCore: ((Int) -> Void)? = nil
    ) -> some View {
        modifier(GlobalAudioOverlay(
            token: token,
            forceCompact: forceCompact,
            showBottomMiniPlayer: showBottomMiniPlayer,
            onNavigateToCore: onNavigateToCore
        ))
    }

}
