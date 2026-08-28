//
//  AssetRow.swift
//  ios
//
//  Molecule: Asset row displaying ticker, sparkline, price and change
//

import SwiftUI

struct AssetRow: View {
    let asset: TrackedAsset
    var onTap: (() -> Void)?
    /// How the change is rendered. Owned by `TrackingViewModel` so one tap switches every
    /// row at once, and persisted there so the choice survives a relaunch.
    var changeDisplayMode: ChangeDisplayMode = .percent
    /// Tapping the price block switches percent <-> dollars. Optional so the hidden height
    /// probe in `AssetsListSection` can still build a row with no callbacks.
    var onToggleChangeDisplay: (() -> Void)?

    var body: some View {
        // TWO sibling buttons, not one button wrapping everything and not a button NESTED in
        // another button's label. The row used to be a single Button around this whole HStack;
        // a nested Button inside a Button label is version-fragile in SwiftUI, and this row
        // also sits under `.swipeActions` + `.contextMenu` on a List that has already had a
        // gesture-conflict incident (see TrackingView's inner-List note). Two siblings keep
        // each tap unambiguous and leave the swipe recognizer alone.
        //
        // The card chrome moved OUT to this container so both buttons sit on one card.
        HStack(spacing: AppSpacing.lg) {
            Button {
                onTap?()
            } label: {
                HStack(spacing: AppSpacing.lg) {
                    // Ticker Info
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(asset.ticker)
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)

                        Text(asset.companyName)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                    }
                    .frame(width: 80, alignment: .leading)

                    // Sparkline Chart
                    SparklineView(
                        data: asset.sparklineData,
                        isPositive: asset.isPositive,
                        referencePrice: asset.previousClose,
                        // Only the traded part of the session, so a mid-morning row
                        // stops partway across instead of looking like a finished
                        // day. Matches the 1D chart this row opens into.
                        spanFrom: asset.sparkFrom,
                        spanTo: asset.sparkTo
                    )
                    .frame(height: 32)

                    Spacer(minLength: AppSpacing.sm)
                }
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(asset.ticker), \(asset.companyName)")
            .accessibilityHint("Opens details")

            // Price Info — its own tap target.
            Button {
                onToggleChangeDisplay?()
            } label: {
                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(asset.formattedPrice)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)

                    PriceChangeLabel(
                        changePercent: asset.changePercent,
                        changeAmount: asset.changeAmount,
                        mode: changeDisplayMode
                    )
                }
                // `maxHeight: .infinity` + contentShape makes the tap area span the row's
                // full height WITHOUT changing the row's intrinsic height — which matters
                // because AssetsListSection sizes the whole List off a hidden AssetRow.
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(asset.formattedPrice), \(changeAccessibilityValue)")
            .accessibilityHint("Switches between percent and dollar change")
        }
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.cardBackground)
        // Standalone rounded card — same radius as AlertCardView so the
        // holdings cards and the Alerts cards read as one family.
        .cornerRadius(AppCornerRadius.large)
        // Card edge: present in light, absent in dark (cardEdge).
        .cardBorder(cornerRadius: AppCornerRadius.large)
    }

    /// Spoken form of whichever value is on screen. VoiceOver used to get the row's children
    /// composed into one unlabelled Button; splitting the row makes two elements, so both
    /// need saying explicitly.
    private var changeAccessibilityValue: String {
        guard asset.changePercent.isFinite else { return "change unavailable" }
        if changeDisplayMode == .amount {
            guard let formatted = asset.formattedChangeAmount else { return "change unavailable" }
            return formatted
        }
        return asset.formattedChange
    }
}

#Preview {
    VStack(spacing: 0) {
        ForEach(TrackedAsset.sampleData) { asset in
            AssetRow(asset: asset)
            if asset.id != TrackedAsset.sampleData.last?.id {
                Divider()
                    .overlay(AppColors.cardBackgroundLight)
            }
        }
    }
    .background(AppColors.background)
}
