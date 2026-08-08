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
    let spec: AppColors.TokenSpec

    var name: String { spec.name }
    var color: Color { spec.color }
    var role: AppColors.TokenRole { spec.role }
}

private struct SwatchRow: View {
    let swatch: Swatch
    let scheme: ColorScheme

    private var style: UIUserInterfaceStyle { scheme == .light ? .light : .dark }

    private func resolve(_ color: Color) -> UIColor {
        UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: style))
    }

    /// The WORST measurement across everything this token contractually has to clear,
    /// mirroring `ThemeContrastAudit.run()`.
    ///
    /// This used to be one number: the token against `cardBackground`, ignoring both
    /// `carriesOnAccentText` and the spec's own `surfaces` list. That produced six-plus
    /// PERMANENT false FAILs — every `*Fill` token is declared `.text` with `on: []`, so
    /// the preview measured e.g. `primaryFill` as ink on a card (~2.2:1 in dark) and
    /// painted it red, while the runtime audit passed it on the reverse check it actually
    /// has to satisfy. `textInverse` measured #FFFFFF on #FFFFFF = 1.00:1 in light for the
    /// same reason. A preview that is permanently part-red is a preview people learn to
    /// ignore, which is worse than not having one.
    private var measurement: (ratio: Double, floor: Double, against: String)? {
        if let ink = swatch.spec.carries {
            // A FILL: the check runs the other way round — is the ink it DECLARES legible
            // ON it? `gainFill`/`lossFill` declare `textOnFill` (near-black in dark);
            // the frozen fills declare `textOnAccent`.
            return (ThemeContrastAudit.ratio(resolve(ink.color), resolve(swatch.color)),
                    4.5, "\(ink.rawValue) on it")
        }
        let floor = ThemeContrastAudit.floor(for: swatch.role)
        guard floor > 0 else { return nil }
        var worst: (Double, String)?
        for key in swatch.spec.surfaces {
            guard let surface = AppColors.surfaceRegistry[key] else { continue }
            let r = ThemeContrastAudit.ratio(resolve(swatch.color), resolve(surface))
            if worst == nil || r < worst!.0 { worst = (r, key) }
        }
        guard let worst else { return nil }
        return (worst.0, floor, worst.1)
    }

    private var passes: Bool {
        guard let m = measurement else { return true }
        return m.ratio >= m.floor
    }

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

            if let m = measurement {
                VStack(alignment: .trailing, spacing: 0) {
                    Text(String(format: "%.2f", m.ratio))
                        .font(AppTypography.dataSmall)
                        .foregroundColor(passes ? AppColors.textMuted : AppColors.loss)
                    // Name the surface the number is measured against, so a failing row
                    // says WHICH pairing failed rather than leaving it to be guessed.
                    Text(m.against)
                        .font(AppTypography.captionTiny)
                        .foregroundColor(AppColors.textDisabled)
                        .lineLimit(1)
                }
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
        // Carry the whole spec, not three of its five fields — dropping
        // `carriesOnAccentText` and `surfaces` is what made the ratios wrong.
        AppColors.auditManifest.map { Swatch(spec: $0) }
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
