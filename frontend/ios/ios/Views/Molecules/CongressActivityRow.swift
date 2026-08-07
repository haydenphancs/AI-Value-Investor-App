//
//  CongressActivityRow.swift
//  ios
//
//  Molecule: Row displaying a single congressional trading activity
//  Shows name, role (Senator/Representative), date, trade value, owner, and price
//

import SwiftUI

struct CongressActivityRow: View {
    let activity: CongressActivity
    /// Nested inside RecentActivitiesSection's card at every production call site,
    /// so it must step the surface up or it is 1.00:1 against its parent in dark.
    var background: Color = AppColors.cardBackgroundNested
    var nameFont: Font = AppTypography.bodyEmphasis
    var valueFont: Font = AppTypography.bodyEmphasis

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Left side: Name, Role, Date
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(activity.name)
                    .font(nameFont)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(activity.role)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)

                Text(activity.formattedDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            // Right side: Change value, Owner tag, Price
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(activity.formattedRange)
                    .font(valueFont)
                    .foregroundColor(activity.changeColor)

                Text(activity.ownerLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(activity.ownerColor)

                if !activity.formattedPrice.isEmpty {
                    Text(activity.formattedPrice)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .padding(AppSpacing.md)
        // `.cardFill`, not a bare `.fill` — the fill alone fixes DARK (where
        // cardBackgroundNested #252B3B steps up from cardBackground #1E2330 at 1.11:1)
        // and does nothing at all in LIGHT, where both arms are #FFFFFF and the row was
        // 1.00:1 against its parent card with no hairline either. Light separates by
        // `cardEdge`, and only `.cardFill`/`.cardSurface`/`.cardBorder` draw it. The two
        // sibling row types (InsiderActivityRow, InstitutionalActivityRow) always did.
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(background)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.sm) {
            CongressActivityRow(
                activity: CongressActivity.sampleData[0]  // Pelosi - Purchase
            )

            CongressActivityRow(
                activity: CongressActivity.sampleData[1]  // Tuberville - Sale
            )

            CongressActivityRow(
                activity: CongressActivity.sampleData[2]  // Mullin - Purchase
            )
        }
        .padding()
    }
}
