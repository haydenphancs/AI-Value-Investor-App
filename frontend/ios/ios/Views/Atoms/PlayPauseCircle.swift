//
//  PlayPauseCircle.swift
//  ios
//
//  Atom: the filled play/pause disc of the audio players.
//

import SwiftUI

/// The filled play/pause disc every audio player draws: the floating mini player, the square
/// beside the Ask Cay AI bar (`ChatBarAudioControl`) and the chat screen's top-bar pill
/// (`AudioStatusIsland`). One drawing, so the three can't drift into three different buttons.
///
/// It only draws — each caller owns its own tap, label and size.
struct PlayPauseCircle: View {
    let isPlaying: Bool
    var diameter: CGFloat = 44
    var glyphFont: Font = AppTypography.iconDefault

    var body: some View {
        ZStack {
            Circle()
                .fill(AppColors.primaryFill)
                .frame(width: diameter, height: diameter)

            Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                .font(glyphFont).fontWeight(.bold)
                .foregroundColor(AppColors.textOnAccent)
                // The play triangle's visual centre sits left of its frame's centre.
                .offset(x: isPlaying ? 0 : 1)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        PlayPauseCircle(isPlaying: true)
        PlayPauseCircle(isPlaying: false)
        PlayPauseCircle(isPlaying: true, diameter: 28, glyphFont: AppTypography.iconXS)
    }
    .padding()
    .background(AppColors.background)
}
