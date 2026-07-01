//
//  SignalHolderRow.swift
//  ios
//
//  Molecule: one holder row in the Signal Ticker Detail screen — a 13F fund
//  (whale) or congress member behind the ticker. Left = name / subtitle / date,
//  right = amount lines. Tappable (blue name + chevron) only when the holder is
//  in our registry (`isTappable`), opening their profile. Modeled on
//  CongressActivityRow / AlertDetailView.leadWhaleRow.
//

import SwiftUI

struct SignalHolderRow: View {
    let holder: SignalHolder
    var onTap: (() -> Void)? = nil

    var body: some View {
        if holder.isTappable {
            Button { onTap?() } label: { rowContent(tappable: true) }
                .buttonStyle(.plain)
        } else {
            rowContent(tappable: false)
        }
    }

    private func rowContent(tappable: Bool) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Left: name / subtitle (role or "13F fund") / date
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(holder.name)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(tappable ? AppColors.primaryBlue : AppColors.textPrimary)
                    .lineLimit(1)
                if !holder.subtitle.isEmpty {
                    Text(holder.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                }
                if !holder.dateText.isEmpty {
                    Text(holder.dateText)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: AppSpacing.sm)

            // Right: primary (allocation move / range, buy-green) + secondary ($ est / owner)
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                if !holder.primaryText.isEmpty {
                    Text(holder.primaryText)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.bullish)
                }
                if !holder.secondaryText.isEmpty {
                    Text(holder.secondaryText)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            if tappable {
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.primaryBlue)
                    .padding(.top, 2)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
        .contentShape(Rectangle())
    }
}
