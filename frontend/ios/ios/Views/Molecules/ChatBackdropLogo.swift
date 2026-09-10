//
//  ChatBackdropLogo.swift
//  ios
//
//  Molecule: the Caydex mark, sunk into the background of an empty Ask Cay AI chat.
//
//  Replaces an earlier greeting block (avatar + "Ask Cay AI" + a subtitle + the date).
//  That said out loud what the screen already says — the title bar, the placeholder and
//  the chips all name the product — so it read as filler. A watermark does the same job
//  silently: it makes the empty state look deliberate rather than unfinished, and gets
//  out of the way the moment a conversation starts.
//
//  ── WHY THIS NEEDS ITS OWN ASSET ───────────────────────────────────────────────────
//  `CaydexLogo.png` CANNOT be used here. It is an OPAQUE 1024×1024 #171B26 plate with a
//  light glyph on it — alpha is 255 everywhere, verified at the pixel level (see
//  `CaydexLogoMark`, which exists solely to clip it into a badge). Drawn faded, it is a
//  faint dark SQUARE, not a faded mark, and in light mode it is a dark square on a
//  near-white page.
//
//  `CaydexGlyph` is that plate with the glyph keyed out into the alpha channel: white
//  pixels, alpha = how bright the source was, so the anti-aliased edges survive and the
//  plate is gone. It is a TEMPLATE image, which is what lets it take an adaptive tint
//  instead of shipping one variant per appearance.
//

import SwiftUI

struct ChatBackdropLogo: View {

    /// Width of the mark.
    var size: CGFloat = 80

    var body: some View {
        Image("CaydexGlyph")
            .resizable()
            .renderingMode(.template)
            .scaledToFit()
            .frame(width: size, height: size)
            // `textPrimary`, not a new token: it is already adaptive in the exact direction
            // this needs — near-white in dark, near-black in light — so the mark lifts a
            // little off a dark page and settles a little into a light one, which is the
            // whole effect. At 6% it carries no meaning and no contrast floor applies, so
            // it needs no `auditManifest` entry; it is decoration, not a graphic token.
            //
            // No blur: the low alpha alone is what sinks it into the page. A blur on top
            // of 6% opacity only smears an already-faint mark into a smudge.
            .foregroundStyle(AppColors.textPrimary.opacity(0.06))
            // Decorative only — the screen is already labelled, and VoiceOver reading
            // "Caydex" in the middle of an empty conversation is noise.
            .accessibilityHidden(true)
            .allowsHitTesting(false)
    }
}

#Preview("Dark") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        ChatBackdropLogo()
    }
    .environment(\.colorScheme, .dark)
}

#Preview("Light") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        ChatBackdropLogo()
    }
    .environment(\.colorScheme, .light)
}
