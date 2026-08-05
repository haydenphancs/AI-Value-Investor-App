//
//  PalettePreview.swift
//  ios
//
//  A visual swatch sheet for the whole palette, in BOTH appearances.
//
//  WHY THIS EXISTS
//  ---------------
//  The app has 533 `#Preview` blocks and, before this file, not one of them
//  exercised light vs dark — every canvas rendered in whatever scheme Xcode
//  happened to be in. A light-mode regression in any of the 520 previewed
//  components was therefore invisible at authoring time.
//
//  `ThemeContrastAudit` proves the numbers. This proves the *look*: the two
//  panes sit side by side, each swatch shows its measured ratio against the
//  surface it is on, and anything that fails is labelled rather than left for
//  someone to squint at. Open the canvas after touching AppTheme and the whole
//  palette is verifiable at a glance.
//

#if DEBUG

import SwiftUI

private struct Swatch: Identifiable {
    let id = UUID()
    let name: String
    let color: Color
    let role: AppColors.TokenRole
}

private struct SwatchRow: View {
    let swatch: Swatch
    let scheme: ColorScheme

    /// Measured against the card, which is the surface these sit on here.
    private var ratio: Double {
        let style: UIUserInterfaceStyle = scheme == .light ? .light : .dark
        let bg = UIColor(AppColors.cardBackground)
            .resolvedColor(with: UITraitCollection(userInterfaceStyle: style))
        let fg = UIColor(swatch.color)
            .resolvedColor(with: UITraitCollection(userInterfaceStyle: style))
        return ThemeContrastAudit.ratio(fg, bg)
    }

    private var floor: Double { ThemeContrastAudit.floor(for: swatch.role) }
    private var passes: Bool { floor == 0 || ratio >= floor }

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            RoundedRectangle(cornerRadius: 4)
                .fill(swatch.color)
                .frame(width: 26, height: 26)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(AppColors.border))

            Text(swatch.name)
                .font(AppTypography.captionSmall)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer(minLength: AppSpacing.xs)

            if floor > 0 {
                Text(String(format: "%.2f", ratio))
                    .font(AppTypography.dataSmall)
                    .foregroundColor(passes ? AppColors.textMuted : AppColors.loss)
            } else {
                Text("—")
                    .font(AppTypography.dataSmall)
                    .foregroundColor(AppColors.textDisabled)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct PalettePane: View {
    let scheme: ColorScheme

    private var swatches: [Swatch] {
        AppColors.auditManifest.map {
            Swatch(name: $0.name, color: $0.color, role: $0.role)
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 1) {
                Text(scheme == .light ? "LIGHT" : "DARK")
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.bottom, AppSpacing.sm)

                ForEach(swatches) { SwatchRow(swatch: $0, scheme: scheme) }

                Divider()
                    .overlay(AppColors.divider)
                    .padding(.vertical, AppSpacing.sm)

                // Surfaces + elevation, which a ratio number cannot convey —
                // this is the "does a card have a visible edge" check.
                Text("SURFACES")
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textSecondary)

                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    ForEach(["flat", "raised"], id: \.self) { name in
                        Text("card · \(name)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(AppSpacing.md)
                            .cardSurface(elevation: name == "flat" ? .flat : .raised)
                    }
                }
                .padding(.top, AppSpacing.xs)
            }
            .padding(AppSpacing.md)
        }
        .background(AppColors.background)
        .environment(\.colorScheme, scheme)
    }
}

#Preview("Palette · light + dark") {
    HStack(spacing: 0) {
        PalettePane(scheme: .light)
        PalettePane(scheme: .dark)
    }
}

#endif
