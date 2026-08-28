//
//  AlertDestinationRow.swift
//  ios
//
//  Molecule: one tappable "go here" row on an alert's detail screen.
//

import SwiftUI

/// A leading glyph, a label, and a chevron — in `primaryBlue`, so it reads as somewhere to go
/// rather than as another line of data.
///
/// Shaped after `AlertDetailView.leadWhaleRow`, which was the only navigable row on either detail
/// screen and proved the pattern. That row stays where it is: it carries a VALUE (the whale's name
/// and firm) on the right, so it is a data row that happens to navigate. This one carries no value —
/// the label *is* the destination — and so it belongs at the end of a card rather than among the
/// facts.
///
/// One component for both detail screens, deliberately. The digest cards and the notification rows
/// are the same feed to a user, and two hand-rolled versions of "open the ticker" would drift in
/// wording, colour and hit target the first time either screen was touched.
struct AlertDestinationRow: View {
    let destination: AlertDestination
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: destination.systemImage)
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.primaryBlue)
                    // Fixed width so the labels of stacked rows line up regardless of how wide
                    // each glyph draws.
                    .frame(width: 20)

                Text(destination.label)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: AppSpacing.sm)

                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.primaryBlue)
            }
            // The row is short; without this the tappable area is the text's own height and the
            // control is under the 44pt minimum.
            .frame(minHeight: HitSlop.minimumTarget)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(.isButton)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        AlertDestinationRow(
            destination: AlertDestination(
                label: "Open AAPL",
                systemImage: "chart.line.uptrend.xyaxis",
                target: .ticker(symbol: "AAPL", assetType: .stock, destination: .default)
            )
        ) {}
        AlertDestinationRow(
            destination: AlertDestination(
                label: "Institutional holders",
                systemImage: "building.columns.fill",
                target: .ticker(
                    symbol: "AAPL", assetType: .stock,
                    destination: TickerDestination(tab: .holders, section: .institutions)
                )
            )
        ) {}
        AlertDestinationRow(
            destination: AlertDestination(
                label: "View investor profile",
                systemImage: "person.crop.circle",
                target: .whale(id: "x")
            )
        ) {}
    }
    .padding(AppSpacing.lg)
    .cardSurface(cornerRadius: AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
}
