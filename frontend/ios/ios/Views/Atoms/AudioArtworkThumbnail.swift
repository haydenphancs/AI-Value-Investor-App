//
//  AudioArtworkThumbnail.swift
//  ios
//
//  Atom: Small artwork thumbnail for audio episodes
//  Displays gradient background with category icon
//

import SwiftUI

struct AudioArtworkThumbnail: View {
    let episode: AudioEpisode
    var size: CGFloat = 48
    var cornerRadius: CGFloat = AppCornerRadius.medium

    var body: some View {
        ZStack {
            // Gradient background
            LinearGradient(
                colors: episode.artworkColors,
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            // Category icon
            Image(systemName: episode.artworkIcon)
                .font(.system(size: size * 0.4, weight: .semibold))
                .foregroundColor(.white.opacity(0.9))
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
    }
}

// MARK: - Large Artwork (for Full Screen Player)
struct AudioArtworkLarge: View {
    let episode: AudioEpisode
    var size: CGFloat = 280
    @State private var isAnimating: Bool = false

    var body: some View {
        ZStack {
            // Outer glow effect
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            episode.artworkColors.first?.opacity(0.4) ?? .clear,
                            .clear
                        ],
                        center: .center,
                        startRadius: size * 0.4,
                        endRadius: size * 0.7
                    )
                )
                .frame(width: size * 1.4, height: size * 1.4)
                .scaleEffect(isAnimating ? 1.1 : 1.0)
                .animation(
                    .easeInOut(duration: 2.0).repeatForever(autoreverses: true),
                    value: isAnimating
                )

            // Main artwork
            ZStack {
                // Gradient background
                LinearGradient(
                    colors: episode.artworkColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )

                // Grainy texture
                Canvas { context, canvasSize in
                    for _ in 0..<Int(canvasSize.width * canvasSize.height / 100) {
                        let x = CGFloat.random(in: 0..<canvasSize.width)
                        let y = CGFloat.random(in: 0..<canvasSize.height)
                        let opacity = Double.random(in: 0.02...0.06)
                        context.fill(
                            Path(ellipseIn: CGRect(x: x, y: y, width: 1, height: 1)),
                            with: .color(.white.opacity(opacity))
                        )
                    }
                }

                // Icon
                Image(systemName: episode.artworkIcon)
                    .font(.system(size: size * 0.35, weight: .semibold))
                    .foregroundColor(.white.opacity(0.9))
            }
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
            .shadow(color: episode.artworkColors.first?.opacity(0.5) ?? .clear, radius: 30, y: 10)
        }
        .onAppear {
            isAnimating = true
        }
    }
}

// MARK: - Preview
#Preview {
    VStack(spacing: AppSpacing.xxl) {
        // Thumbnails
        HStack(spacing: AppSpacing.lg) {
            AudioArtworkThumbnail(episode: .sampleMoneyMoves)
            AudioArtworkThumbnail(episode: .sampleFTX)
            AudioArtworkThumbnail(episode: .sampleBook)
            AudioArtworkThumbnail(episode: .sampleDailyBrief)
        }

        // Large artwork
        AudioArtworkLarge(episode: .sampleMoneyMoves)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
