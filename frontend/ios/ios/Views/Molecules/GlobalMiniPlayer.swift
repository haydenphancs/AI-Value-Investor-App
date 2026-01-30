//
//  GlobalMiniPlayer.swift
//  ios
//
//  Molecule: Floating mini audio player with glassmorphism design
//  Appears above tab bar when audio is playing, expandable to full screen
//

import SwiftUI

struct GlobalMiniPlayer: View {
    @EnvironmentObject private var audioManager: AudioManager
    @State private var dragOffset: CGFloat = 0

    // Animation constants
    private let dismissThreshold: CGFloat = 100
    private let expandThreshold: CGFloat = -50

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                Spacer()

                // Mini player card
                miniPlayerCard
                    .offset(y: dragOffset)
                    .gesture(
                        DragGesture()
                            .onChanged { value in
                                // Allow drag up (negative) and down (positive)
                                dragOffset = value.translation.height
                            }
                            .onEnded { value in
                                handleDragEnd(value)
                            }
                    )
                    .animation(.spring(response: 0.3, dampingFraction: 0.8), value: dragOffset)

                // Spacer for tab bar (approximately 49pt + safe area)
                Color.clear
                    .frame(height: 0)
            }
            .ignoresSafeArea(.container, edges: .bottom)
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    // MARK: - Mini Player Card
    private var miniPlayerCard: some View {
        HStack(spacing: AppSpacing.md) {
            // Artwork thumbnail
            if let episode = audioManager.currentEpisode {
                AudioArtworkThumbnail(episode: episode, size: 48)
            }

            // Title and subtitle
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                if let episode = audioManager.currentEpisode {
                    // Marquee text for long titles
                    MarqueeText(
                        text: episode.title,
                        font: AppTypography.bodyBold,
                        color: AppColors.textPrimary
                    )
                    .frame(height: 18)

                    Text(episode.authorName)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // Play/Pause button
            Button(action: {
                withAnimation(.spring(response: 0.2)) {
                    audioManager.togglePlayPause()
                }
            }) {
                ZStack {
                    Circle()
                        .fill(AppColors.primaryBlue)
                        .frame(width: 36, height: 36)

                    Image(systemName: audioManager.isPlaying ? "pause.fill" : "play.fill")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                        .offset(x: audioManager.isPlaying ? 0 : 1) // Visual centering for play icon
                }
            }
            .buttonStyle(PlainButtonStyle())

            // Close button
            Button(action: {
                withAnimation(.spring(response: 0.3)) {
                    audioManager.stop()
                }
            }) {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .frame(height: 72)
        .background(
            glassmorphismBackground
        )
        .overlay(
            // Progress bar at top
            GeometryReader { proxy in
                Rectangle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: proxy.size.width * audioManager.progress, height: 2)
                    .animation(.linear(duration: 0.1), value: audioManager.progress)
            }
            .frame(height: 2)
            , alignment: .top
        )
        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
        .shadow(color: Color.black.opacity(0.3), radius: 20, y: 10)
        .padding(.horizontal, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
        .contentShape(Rectangle())
        .onTapGesture {
            audioManager.expandPlayer()
        }
    }

    // MARK: - Glassmorphism Background
    private var glassmorphismBackground: some View {
        ZStack {
            // Blurred background
            VisualEffectBlur(blurStyle: .systemThinMaterialDark)

            // Gradient overlay for depth
            LinearGradient(
                colors: [
                    Color.white.opacity(0.08),
                    Color.white.opacity(0.02)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            // Inner border glow
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .strokeBorder(
                    LinearGradient(
                        colors: [
                            Color.white.opacity(0.2),
                            Color.white.opacity(0.05)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 1
                )
        }
    }

    // MARK: - Drag Handling
    private func handleDragEnd(_ value: DragGesture.Value) {
        let velocity = value.predictedEndTranslation.height - value.translation.height

        // Swipe up to expand
        if value.translation.height < expandThreshold || velocity < -300 {
            audioManager.expandPlayer()
        }
        // Swipe down to dismiss (optional - could also just close)
        else if value.translation.height > dismissThreshold || velocity > 300 {
            audioManager.stop()
        }

        // Reset offset
        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
            dragOffset = 0
        }
    }
}

// MARK: - Visual Effect Blur (UIKit Bridge)
struct VisualEffectBlur: UIViewRepresentable {
    let blurStyle: UIBlurEffect.Style

    func makeUIView(context: Context) -> UIVisualEffectView {
        let view = UIVisualEffectView(effect: UIBlurEffect(style: blurStyle))
        return view
    }

    func updateUIView(_ uiView: UIVisualEffectView, context: Context) {
        uiView.effect = UIBlurEffect(style: blurStyle)
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            Text("Main Content")
                .foregroundColor(AppColors.textPrimary)
            Spacer()
        }

        GlobalMiniPlayer()
            .environmentObject(AudioManager.shared)
    }
    .onAppear {
        AudioManager.shared.play(.sampleMoneyMoves)
    }
    .preferredColorScheme(.dark)
}
