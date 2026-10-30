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
                        // Direction of the SERIES when today's change is unknown:
                        // `asset.isPositive` is hard-false then, and the neutral mode
                        // strokes one tone by this flag, so the whole line went red
                        // under a "—" label. Same rule as MarketPulseCard.
                        isPositive: asset.changeKnown
                            ? asset.isPositive
                            : ((asset.sparklineData.last ?? 0) >= (asset.sparklineData.first ?? 0)),
                        // `showReference: false` is what removes the dashed baseline —
                        // `referencePrice: nil` alone means "anchor to the first point",
                        // which is exactly what a newly-starred ticker with no
                        // `market_close_snapshot` row got: a dashed line at data[0],
                        // red segments and a red end dot beneath it — a fabricated
                        // intraday decline beside a label that said "—" (F20-4 wired the
                        // flag into MarketPulseCard only; this was the second reader).
                        referencePrice: asset.changeKnown ? asset.previousClose : nil,
                        showReference: asset.changeKnown,
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

                    // The pre-market / after-hours chip sits LEFT of the day change, so the
                    // day change stays right-aligned under the price. Same line, so the
                    // card is no taller: the chip's 10pt text is shorter than the 13pt
                    // change beside it, and the height probe never has to know.
                    HStack(spacing: 0) {
                        extendedMoveChip
                        PriceChangeLabel(
                            changePercent: asset.changePercent,
                            changeAmount: asset.changeAmount,
                            mode: changeDisplayMode,
                            isKnown: asset.changeKnown
                        )
                    }
                }
                // `maxHeight: .infinity` + contentShape makes the tap area span the row's
                // full height WITHOUT changing the row's intrinsic height — which matters
                // because AssetsListSection sizes the whole List off a hidden AssetRow.
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Offered first, after the navigation side's minimum (80pt ticker column +
            // 80pt chart + gaps), so the chip's `ViewThatFits` measures the width really
            // left. At the default (equal) priority this column was offered half the row,
            // the chip always "fit", and on a 375pt phone the row overflowed its card.
            .layoutPriority(1)
            .accessibilityLabel("\(asset.formattedPrice), \(changeAccessibilityValue)\(extendedAccessibilityValue)")
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
        guard asset.changeKnown, asset.changePercent.isFinite else { return "change unavailable" }
        if changeDisplayMode == .amount {
            guard let formatted = asset.formattedChangeAmount else { return "change unavailable" }
            return formatted
        }
        return asset.formattedChange
    }

    /// The pre-market / after-hours move this row shows NOW, or nil.
    ///
    /// The backend sends a print only outside the regular session, but a saved snapshot can
    /// still paint this morning's pre-market print after the bell — so the bell decides
    /// here too, on New York's clock (owner rule 2026-10-29, "ET for markets"), never the
    /// phone's: `liveSessionStatus` is the same session rule as the backend's
    /// `session_phase`. The chip carries no clock time; one added later must be New
    /// York's, labelled " ET".
    private var extendedMove: (session: ExtendedSession, percent: Double)? {
        guard let session = asset.extendedSession,
              let percent = asset.extendedChangePercent, percent.isFinite else { return nil }
        if case .open? = MarketHoursUtil.liveSessionStatus() { return nil }
        return (session, percent)
    }

    /// `☾ +0.08%` (after hours) or `☀ -0.35%` (pre-market), in the row's % / $ mode.
    ///
    /// Shown WHOLE or not at all: when it does not fit beside the day change (a 375pt
    /// phone, or two double-digit moves on a 390-393pt one), `ViewThatFits` falls back to
    /// the zero-size view instead of squeezing a number into "…". `.layoutPriority(-1)`
    /// lets the day change take its full width first. VoiceOver reads the move either way.
    @ViewBuilder
    private var extendedMoveChip: some View {
        if let move = extendedMove {
            ViewThatFits(in: .horizontal) {
                PriceChangeLabel(
                    changePercent: move.percent,
                    changeAmount: asset.extendedChangeAmount,
                    mode: changeDisplayMode,
                    leadingSymbol: move.session.symbolName,
                    fontSize: 10
                )
                .padding(.trailing, AppSpacing.xs)
                Color.clear.frame(width: 0, height: 0)
            }
            .layoutPriority(-1)
        }
    }

    /// ", After hours $349.20, +0.08%" — appended to the price button's label, whether or
    /// not the chip found room on screen.
    private var extendedAccessibilityValue: String {
        guard let move = extendedMove, let price = asset.formattedExtendedPrice else { return "" }
        let change: String
        if changeDisplayMode == .amount, let amount = asset.extendedChangeAmount {
            change = TrackedAsset.formatSignedCurrency(amount)
        } else {
            let normalized = move.percent == 0 ? 0 : move.percent
            change = "\(normalized >= 0 ? "+" : "")\(String(format: "%.2f", normalized))%"
        }
        return ", \(move.session.spokenName) \(price), \(change)"
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

// The chip follows New York's clock: during the regular session (9:30-4:00 ET) this
// preview correctly shows no chip.
#Preview("Extended hours") {
    var afterHours = TrackedAsset.sampleData[0]
    afterHours.extendedPrice = 178.9
    afterHours.extendedChangePercent = 0.27
    afterHours.extendedSession = .afterHours
    var preMarket = TrackedAsset.sampleData[2]
    preMarket.extendedPrice = 377.5
    preMarket.extendedChangePercent = -12.37
    preMarket.extendedSession = .preMarket
    return VStack(spacing: AppSpacing.sm) {
        AssetRow(asset: afterHours)
        AssetRow(asset: preMarket)
        AssetRow(asset: afterHours, changeDisplayMode: .amount)
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
}
