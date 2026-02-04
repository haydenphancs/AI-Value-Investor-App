//
//  AudioStatusIsland.swift
//  ios
//
//  Minimal audio status indicator that hugs the Dynamic Island area
//  Shows when keyboard is active during chat - lightweight, status-only design
//

import SwiftUI

struct AudioStatusIsland: View {
    @EnvironmentObject private var audioManager: AudioManager

    // Layout constants
    private let pillHeight: CGFloat = 36
    private let pillCornerRadius: CGFloat = 18

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // Tiny animated waveform
            WaveformIndicator(isPlaying: audioManager.isPlaying)
                .frame(width: 20, height: 16)

            // Episode title (truncated)
            if let episode = audioManager.currentEpisode {
                Text(episode.title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 120)
            }

            // Play/Pause indicator (tiny)
            Button(action: {
                audioManager.togglePlayPause()
            }) {
                Image(systemName: audioManager.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 20, height: 20)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs)
        .frame(height: pillHeight)
        .background(
            Capsule()
                .fill(Color.black)
                .shadow(color: Color.black.opacity(0.3), radius: 8, y: 4)
        )
        .overlay(
            Capsule()
                .strokeBorder(Color.white.opacity(0.1), lineWidth: 0.5)
        )
        .onTapGesture {
            // Exit compact mode and show full player
            audioManager.exitCompactMode()
        }
    }
}

// MARK: - Tiny Waveform Indicator
private struct WaveformIndicator: View {
    let isPlaying: Bool

    @State private var animationPhase: CGFloat = 0

    private let barCount = 3
    private let barWidth: CGFloat = 2
    private let barSpacing: CGFloat = 2

    var body: some View {
        HStack(spacing: barSpacing) {
            ForEach(0..<barCount, id: \.self) { index in
                WaveformBar(
                    isPlaying: isPlaying,
                    delay: Double(index) * 0.15,
                    phase: animationPhase
                )
                .frame(width: barWidth)
            }
        }
        .onAppear {
            if isPlaying {
                startAnimation()
            }
        }
        .onChange(of: isPlaying) { oldValue, newValue in
            if newValue {
                startAnimation()
            }
        }
    }

    private func startAnimation() {
        withAnimation(.linear(duration: 0.8).repeatForever(autoreverses: false)) {
            animationPhase = 1
        }
    }
}

// MARK: - Individual Waveform Bar
private struct WaveformBar: View {
    let isPlaying: Bool
    let delay: Double
    let phase: CGFloat

    @State private var height: CGFloat = 4

    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(AppColors.primaryBlue)
            .frame(height: height)
            .onAppear {
                if isPlaying {
                    animateBar()
                }
            }
            .onChange(of: isPlaying) { oldValue, newValue in
                if newValue {
                    animateBar()
                } else {
                    withAnimation(.easeOut(duration: 0.2)) {
                        height = 4
                    }
                }
            }
    }

    private func animateBar() {
        let minHeight: CGFloat = 4
        let maxHeight: CGFloat = 14

        withAnimation(
            .easeInOut(duration: 0.4)
            .repeatForever(autoreverses: true)
            .delay(delay)
        ) {
            height = CGFloat.random(in: minHeight...maxHeight)
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            // Simulate Dynamic Island area
            AudioStatusIsland()
                .padding(.top, 60)

            Spacer()

            Text("Chat content area")
                .foregroundColor(AppColors.textSecondary)

            Spacer()
        }
    }
    .environmentObject(AudioManager.shared)
    .onAppear {
        AudioManager.shared.load(.sampleMoneyMoves)
        AudioManager.shared.resume()
    }
    .preferredColorScheme(.dark)
}

#Preview("Paused State") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            AudioStatusIsland()
                .padding(.top, 60)

            Spacer()
        }
    }
    .environmentObject(AudioManager.shared)
    .onAppear {
        AudioManager.shared.load(.sampleMoneyMoves)
    }
    .preferredColorScheme(.dark)
}
