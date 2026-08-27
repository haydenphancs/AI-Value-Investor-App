//
//  NowPlayingBars.swift
//  ios
//
//  Atom: the four bouncing bars that mark audio as playing.
//
//  Lives here, not beside its old caller, because it is now rendered by `LargePlayButton`
//  (an atom) — a molecule/organism-level declaration would have an atom reaching upward.
//  It knows nothing about the app's domain, which is what makes it an atom rather than a
//  molecule: no episode, no article, no AudioManager.
//

import SwiftUI

/// The four bouncing bars that mark the article as playing.
///
/// Rendered by `LargePlayButton`, immediately right of the play/pause disc, so the indicator
/// and the "Now Playing" label it belongs to are one row. It used to be a SECOND row below the
/// button that repeated the label the button was already showing.
///
/// Filled with `textPrimary`, not `textOnAccent`: this sits on the page background now that
/// the headline moved off the artwork, and `textOnAccent` is white in both appearances —
/// invisible on the #F4F5F8 light page. That same trap had the label beside it, until the
/// bars moved in and the two inks were reconciled.
struct NowPlayingBars: View {
    @State private var isAnimating = false

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<4) { index in
                RoundedRectangle(cornerRadius: 1)
                    .fill(AppColors.textPrimary)
                    .frame(width: 3, height: isAnimating ? CGFloat.random(in: 8...16) : 4)
                    .animation(
                        .easeInOut(duration: 0.4)
                            .repeatForever()
                            .delay(Double(index) * 0.1),
                        value: isAnimating
                    )
            }
        }
        .frame(height: 16)
        .onAppear {
            isAnimating = true
        }
    }
}
