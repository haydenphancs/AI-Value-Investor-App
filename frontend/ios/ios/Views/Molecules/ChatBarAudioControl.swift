//
//  ChatBarAudioControl.swift
//  ios
//
//  Molecule: the audio player on a screen with an Ask Cay AI bar — a square to the left of the
//  bar, holding the mini player's play/pause disc.
//

import SwiftUI

/// The narration control beside an Ask Cay AI bar, shown while an episode is active.
///
/// WHY A SQUARE BESIDE THE BAR. The floating mini player cannot sit above the bar there: on the
/// asset screens the suggested-question chips already do, and the owner ruled the stack too messy.
/// Hiding the player instead (the old collapse to a "status island") assumed iOS shows Now Playing
/// in the Dynamic Island while the app is open — it does not. Apple's Live Activities guidance:
/// "Your Live Activity appears in the Dynamic Island while your app isn't in use." So the player
/// is this square, on every iPhone, with or without a Dynamic Island.
///
/// Tap = play/pause. Press and hold = the full-screen player (speed, skip, sleep timer, Read).
/// Deliberately not a `Button`: a Button fires on touch-up, so a hold would toggle playback AND
/// open the player.
struct ChatBarAudioControl: View {
    /// Observed directly rather than via `@EnvironmentObject`: the chat bar also renders in
    /// previews, and a missing environment object is a crash rather than a fallback.
    @ObservedObject private var audioManager = AudioManager.shared

    /// Side length — the chat bar passes its input card's measured height, so the two match.
    let side: CGFloat

    /// The square never goes below this, so the 44pt disc fits even when small text makes the
    /// chat bar shorter than the disc.
    static let minimumSide: CGFloat = 52

    var body: some View {
        // A container that persists, so the square's arrival and departure animate.
        ZStack {
            if audioManager.hasActiveEpisode {
                PlayPauseCircle(isPlaying: audioManager.isPlaying)
                    .frame(width: max(side, Self.minimumSide), height: max(side, Self.minimumSide))
                    // Its own container, separate from the chat bar's card — a square, so a
                    // tighter radius than the card's `extraLarge`.
                    .cardSurface(cornerRadius: AppCornerRadius.large, elevation: .raised)
                    .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                    .onTapGesture {
                        withAnimation(.spring(response: 0.2)) {
                            audioManager.togglePlayPause()
                        }
                    }
                    .onLongPressGesture(minimumDuration: 0.4) {
                        Haptics.impact(.light)
                        audioManager.expandPlayer()
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(audioManager.isPlaying ? "Pause narration" : "Play narration")
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction {
                        audioManager.togglePlayPause()
                    }
                    .accessibilityAction(named: "Open player") {
                        audioManager.expandPlayer()
                    }
                    // The gap to the chat bar lives HERE, not in the bar's HStack spacing, so a
                    // bar with no episode playing is exactly as wide as it always was.
                    .padding(.trailing, AppSpacing.sm)
                    .transition(.opacity.combined(with: .scale(scale: 0.9)))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
    }
}

#Preview {
    HStack(spacing: 0) {
        ChatBarAudioControl(side: 54)
        Text("Ask Cay AI...")
            .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
            .padding(.horizontal, AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.extraLarge, elevation: .raised)
    }
    .padding()
    .background(AppColors.background)
    .onAppear {
        AudioManager.shared.load(.sampleMoneyMoves)
        AudioManager.shared.resume()
    }
}
