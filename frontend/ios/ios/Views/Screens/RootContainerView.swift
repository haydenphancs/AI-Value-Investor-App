//
//  RootContainerView.swift
//  ios
//
//  Root container view with layered architecture for global audio player
//  Manages the tab navigation, mini player overlay, and full screen player
//

import SwiftUI

struct RootContainerView: View {
    @StateObject private var audioManager = AudioManager.shared

    var body: some View {
        ZStack {
            // Layer 1: Main Tab Navigation
            MainTabView()
                .environment(\.miniPlayerVisible, audioManager.hasActiveEpisode && !audioManager.isCompactMode)

            // Layer 2: Audio Player States
            // Note: isPlayerHiddenByScroll is only used within detail views (BookCoreDetailView),
            // not at the root level. The main screen always shows the player when active.
            if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer {
                if audioManager.isCompactMode {
                    // State B: Status Island (top, minimal pill near Dynamic Island)
                    VStack {
                        AudioStatusIsland()
                            .padding(.top, 8) // Below Dynamic Island
                        Spacer()
                    }
                    .transition(.asymmetric(
                        insertion: .move(edge: .top).combined(with: .opacity),
                        removal: .move(edge: .top).combined(with: .opacity)
                    ))
                } else {
                    // State A: Full Mini Player (bottom, floating above tab bar)
                    VStack {
                        Spacer()
                        GlobalMiniPlayer()
                            .padding(.bottom, 49) // Tab bar height
                    }
                    .transition(.asymmetric(
                        insertion: .move(edge: .bottom).combined(with: .opacity),
                        removal: .move(edge: .bottom).combined(with: .opacity)
                    ))
                }
            }

            // Layer 3: Full Screen Player (modal overlay)
            if audioManager.showFullScreenPlayer {
                FullScreenAudioPlayer()
                    .transition(.move(edge: .bottom))
                    .zIndex(100)
            }
        }
        .environmentObject(audioManager)
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.isCompactMode)
        .animation(.spring(response: 0.4, dampingFraction: 0.85), value: audioManager.showFullScreenPlayer)
        .preferredColorScheme(.dark)
    }
}

// MARK: - Mini Player Visibility Environment Key
struct MiniPlayerVisibleKey: EnvironmentKey {
    static let defaultValue: Bool = false
}

extension EnvironmentValues {
    var miniPlayerVisible: Bool {
        get { self[MiniPlayerVisibleKey.self] }
        set { self[MiniPlayerVisibleKey.self] = newValue }
    }
}

// MARK: - Mini Player Safe Area Modifier
/// Adds extra bottom padding to scrollable content when mini player is visible
struct MiniPlayerSafeAreaModifier: ViewModifier {
    @Environment(\.miniPlayerVisible) private var miniPlayerVisible

    // Mini player height (72) + padding (8) + extra spacing (8)
    private let miniPlayerHeight: CGFloat = 88

    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .bottom) {
                if miniPlayerVisible {
                    Color.clear
                        .frame(height: miniPlayerHeight)
                }
            }
    }
}

extension View {
    /// Adds extra bottom safe area when mini player is visible
    /// Apply this to ScrollViews and Lists to prevent content being hidden
    func miniPlayerSafeArea() -> some View {
        modifier(MiniPlayerSafeAreaModifier())
    }
}

// MARK: - Alternative: Content Inset Modifier
/// For ScrollViews that need contentInsets instead of safeAreaInset
struct MiniPlayerContentInsetModifier: ViewModifier {
    @Environment(\.miniPlayerVisible) private var miniPlayerVisible

    private let miniPlayerHeight: CGFloat = 88

    func body(content: Content) -> some View {
        content
            .padding(.bottom, miniPlayerVisible ? miniPlayerHeight : 0)
    }
}

extension View {
    /// Adds bottom padding when mini player is visible
    /// Use this for content that doesn't support safeAreaInset
    func miniPlayerContentInset() -> some View {
        modifier(MiniPlayerContentInsetModifier())
    }
}

// MARK: - Preview
#Preview {
    RootContainerView()
}
