//
//  CaydexLogoMark.swift
//  ios
//
//  Atom: the Caydex logo, rendered correctly in both appearances.
//
//  WHY THIS EXISTS
//  ---------------
//  `CaydexLogo.png` is an OPAQUE #171B26 tile with a light glyph — verified at
//  the pixel level: alpha is 255 across the whole 1024×1024, and the corners are
//  bit-identical to `AppColors.background`'s dark hex. It is an app-icon-style
//  plate, not a transparent glyph.
//
//  Drawn bare with `.scaledToFit()` on a light page it is therefore a hard-edged
//  dark-navy SQUARE floating on #F4F5F8 — which is what four screens did
//  (splash, app lock, sign-in, disclaimer). It reads as a rendering artifact.
//
//  The treatment that already worked was the header's: clip it to a rounded
//  rect so it reads as an intentional icon badge. That is correct in BOTH modes
//  and needs no second asset. This atom is that treatment, in one place, so the
//  five sites cannot drift — and so a future light-variant asset only has to be
//  wired up once.
//
//  Corner radius follows Apple's continuous-curvature icon ratio (≈22.37% of the
//  side) so the badge matches the app icon's silhouette at any size, rather than
//  a fixed radius that looks over-rounded at 160pt and under-rounded at 36pt.
//

import SwiftUI

struct CaydexLogoMark: View {
    let size: CGFloat

    /// Apple's superellipse icon ratio. 36pt → 8pt, matching the header's
    /// previous hand-tuned value, and it stays proportional as the mark scales.
    private var cornerRadius: CGFloat { size * 0.2237 }

    init(size: CGFloat) {
        self.size = size
    }

    var body: some View {
        Image("CaydexLogo")
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .accessibilityLabel("Caydex")
    }
}

#Preview("Light") {
    VStack(spacing: AppSpacing.xl) {
        ForEach([160.0, 96.0, 80.0, 56.0, 36.0], id: \.self) { s in
            CaydexLogoMark(size: s)
        }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
    .environment(\.colorScheme, .light)
}

#Preview("Dark") {
    VStack(spacing: AppSpacing.xl) {
        ForEach([160.0, 96.0, 80.0, 56.0, 36.0], id: \.self) { s in
            CaydexLogoMark(size: s)
        }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
    .environment(\.colorScheme, .dark)
}
