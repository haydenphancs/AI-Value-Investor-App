//
//  AssetRow.swift
//  ios
//
//  Molecule: Asset row displaying ticker, sparkline, price and change
//

import SwiftUI

struct AssetRow: View {

    /// Shared with ``TrackedAssetsSkeleton`` so the placeholder cannot drift from the real
    /// row — it existed to mirror this geometry and already carried a literal duplicate of
    /// the old hardcoded 80.
    ///
    /// The sparkline is pinned rather than left flexible on purpose. `SparklineView` is one
    /// `GeometryReader` with no intrinsic width and a MINIMUM OF ZERO, so it absorbs every
    /// point of slack in the row (measured ~118-138pt on a 393pt device) and starves the
    /// name column — which is what a tester reported as "Oracle Corpo…". Now that the name
    /// column is flexible, an unpinned chart would flip the failure the other way and let a
    /// long name collapse the chart to nothing.
    ///
    /// 80pt floor rationale: Home's Market Pulse tiles already ship a ~68pt sparkline, and
    /// an early-session row draws only the traded FRACTION of this width (`spanFrom`/
    /// `spanTo`), so going much below 80 makes a 9:45am row read as a dot with a nub.
    static let sparklineWidth: CGFloat = 80
    /// Floor, not a fixed width — see the `minWidth` note at the call site.
    static let tickerColumnMinWidth: CGFloat = 80

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
                        // The symbol is NOT always 3-4 characters: this column carries
                        // FMP pair forms (BTCUSD, DOGEUSD) and index keys (^GSPC) as
                        // well as equities. headingSmall is reading-tier (1.4x ->
                        // 22.4pt), where a 6-character symbol measures wider than the
                        // ~80pt the column resolves to — and an unbreakable word either
                        // overflows the card or wraps to a second line, which silently
                        // changes the row HEIGHT that AssetsListSection hard-sizes the
                        // whole List from, with scrolling disabled.
                        Text(asset.ticker)
                            .font(AppTypography.headingSmall)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)

                        Text(asset.companyName)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)
                            .truncationMode(.tail)
                    }
                    // `minWidth`, not a hard `width: 80`: a hard width is min AND max, so the
                    // slack sitting in the sparkline could never reach the text no matter how
                    // much there was. Both fonts here are in the READING tier (1.4x), the
                    // most-growing one, which made a pinned box the worst possible pairing.
                    // Same fix, and same reasoning, as MarketPulseCard and ScannerLeaderboardRow.
                    .frame(minWidth: Self.tickerColumnMinWidth,
                           maxWidth: .infinity,
                           alignment: .leading)

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
                    .frame(width: Self.sparklineWidth, height: 32)

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
                    // Unguarded before: headingSmall is reading-tier (1.4x) and this Text
                    // is intrinsically sized, so a large price at a large content size
                    // grew straight into the chart's space.
                    Text(asset.formattedPrice)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)

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
