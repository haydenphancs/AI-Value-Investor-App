//
//  NavBackButton.swift
//  ios
//
//  Atom: the back chevron, with a tap target you can actually hit.
//
//  WHY THIS EXISTS
//  ---------------
//  Reported from TestFlight: "Sometimes when I hit the back (<) button, it
//  doesn't work."
//
//  It was not intermittent — it was a dead zone, and whether a tap landed in it
//  was down to where in the glyph the thumb came down. Ten of the app's
//  seventeen back buttons were a bare `Image(systemName: "chevron.left")` inside
//  a `Button` with no frame, no padding and no content shape, so the touch
//  target was the GLYPH: about 10x18pt at `iconMedium`, against Apple's 44x44pt
//  minimum (HIG, Accessibility → Buttons and Controls).
//
//  On iOS 26 that is actively misleading in a toolbar, because the system draws
//  a 44pt "liquid glass" circle behind the item while the touch target still
//  follows the label. Measured on the Signal Ticker Detail screen (iPhone 17
//  Pro, iOS 26): the circle spans y 62–105pt, but a tap at (38, 65) — plainly
//  inside it — did nothing, while (38, 96) dismissed. The user aims at the
//  circle they can see and gets nothing perhaps a third of the time.
//
//  HOW TO USE IT
//  -------------
//  `alignment` controls where the glyph sits inside the 44pt box, so adopting
//  this never moves a tuned layout:
//    * `.center` (default) — toolbars, where the system centres the item anyway.
//    * `.leading` — inline header rows, where the chevron is aligned to the
//      screen's leading padding and must not shift right by ~17pt.
//

import SwiftUI

struct NavBackButton: View {
    /// Apple's minimum comfortable target (HIG). Not a design knob — shrinking
    /// this reintroduces the dead zone this atom exists to remove.
    static let hitTarget: CGFloat = 44

    var font: Font = AppTypography.iconMedium
    var weight: Font.Weight = .semibold
    var color: Color = AppColors.textPrimary
    var alignment: Alignment = .center
    /// Spoken by VoiceOver. "Back" is right for a pop; a screen presented as a
    /// cover should say what it closes.
    var accessibilityText: String = "Back"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(font)
                .fontWeight(weight)
                .foregroundColor(color)
                // Order is load-bearing: the frame first so the box is 44pt, THEN
                // the content shape so the whole box is hit-testable rather than
                // just the glyph's own coverage.
                .frame(
                    width: Self.hitTarget,
                    height: Self.hitTarget,
                    alignment: alignment
                )
                .contentShape(Rectangle())
                // …plus slop on top of the 44pt box. 44 is the minimum Apple will
                // call comfortable, and a back button lives in the top-left corner
                // — the hardest place on the screen to reach one-handed. The glyph
                // and the layout are untouched; only the touch area grows.
                .hitSlop()
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityText)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(alignment: .leading, spacing: AppSpacing.xl) {
            HStack {
                NavBackButton(alignment: .leading) {}
                Spacer()
            }
            .border(AppColors.primaryBlue.opacity(0.4))

            HStack {
                NavBackButton {}
                Spacer()
            }
            .border(AppColors.primaryBlue.opacity(0.4))

            Text("The bordered rows show the 44pt target the glyph sits in.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(AppSpacing.lg)
    }
}
