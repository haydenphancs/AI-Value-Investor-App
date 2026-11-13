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

    /// The price column's width while the market is OPEN, at the default text size (scaled
    /// with Dynamic Type by ``HoldingsPriceColumn``). The SAME on every card, never the card's
    /// own content width: when each card sized its column from its own numbers, the chart's x
    /// moved card to card — ETHUSD's sat ~40pt right of the stock rows after hours
    /// (2026-11-11). Every point the column does not take goes to the ticker/name column.
    ///
    /// 88: fits "$98,765.43" (~85pt at headingSmall) unshrunk and a BRK-A price inside the
    /// Text's 0.85 scale factor; the day change is at most ~80pt even in dollars
    /// ("+$1,234.56"). Keep it at or below 119 — what a 375pt phone can offer once the
    /// navigation side has its 176pt floor (80 + 16 + 80) — or that phone clamps it.
    static let priceColumnWidth: CGFloat = 88
    /// The price column's width outside the regular session when any card in the list can
    /// show a pre-market / after-hours chip: room for "☾ -12.37%  ▼ -10.25%" (~122-127pt).
    /// Not scaled: the chip and the day change use `Font.system(size:)`, which does not follow
    /// Dynamic Type. Binds on 393-402pt phones (offers 137 / 146); a 375pt phone offers only
    /// 119, so its widest chips drop whole.
    static let extendedPriceColumnWidth: CGFloat = 128

    /// True outside New York's regular session — the same rule as `extendedMove`, read once per
    /// list by `AssetsListSection` so every card reserves the same column, and by the card's
    /// own `afterHoursTail` gate.
    static var isOutsideRegularSession: Bool {
        if case .open? = MarketHoursUtil.liveSessionStatus() { return false }
        return true
    }

    let asset: TrackedAsset
    var onTap: (() -> Void)?
    /// How the change is rendered. Owned by `TrackingViewModel` so one tap switches every
    /// row at once, and persisted there so the choice survives a relaunch.
    var changeDisplayMode: ChangeDisplayMode = .percent
    /// Tapping the price block switches percent <-> dollars. Optional so the hidden height
    /// probe in `AssetsListSection` can still build a row with no callbacks.
    var onToggleChangeDisplay: (() -> Void)?
    /// Reserve the after-hours column (`extendedPriceColumnWidth`) instead of the regular one.
    /// ONE answer for the whole list, computed by `AssetsListSection` — never per card, or the
    /// cards with a chip and the cards without one get different columns again.
    var reservesExtendedMove: Bool = false

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
                        spanTo: asset.sparkTo,
                        // After the bell, the session's after-hours trading continues the
                        // line in gray (owner, 2026-11-11). Never for a coin: it has no
                        // extended session, so the backend sends no tail.
                        tail: afterHoursTail
                    )
                    .frame(width: Self.sparklineWidth, height: 32)
                    // No Spacer after the chart (owner, 2026-11-11: "i want the ticker and
                    // its name longer"): the ticker column's `maxWidth: .infinity` takes ALL
                    // the slack, and the chart sits one gap from the price column. A Spacer
                    // here held 8pt+ of empty space the names could not use.
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
                    // Tabular figures: every price carries exactly two decimals and sits
                    // flush right, so with equal-width digits the decimal points line up
                    // down the list (AppTypography's data tier does the same). The day
                    // change and the chip stay proportional — a tabular "1" is ~2.6pt
                    // wider at 13pt, and the chip has no room to spare on a 393pt phone.
                    Text(asset.formattedPrice)
                        .font(AppTypography.headingSmall.monospacedDigit())
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
                    // The line FILLS the column instead of hugging its content, so the chip
                    // is offered "column - day change" in both layout passes. Hugging, the
                    // column was re-offered exactly chip + change when it was placed, the
                    // chip's fit test landed a float step short on some cards, and the chip
                    // vanished from a box already sized for it (ORCL/ACHR/NVDA, 2026-11-11).
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
                // One column width for the whole list (see `HoldingsPriceColumn`), trailing,
                // so the chart's x and both right edges match on every card, chip or not.
                .holdingsPriceColumn(reservesExtendedMove: reservesExtendedMove)
                // `maxHeight: .infinity` + contentShape makes the tap area span the row's
                // full height WITHOUT changing the row's intrinsic height — which matters
                // because AssetsListSection sizes the whole List off a hidden AssetRow.
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Offered first: the column is given (row - the navigation side's 176pt minimum:
            // 80pt ticker column + 16 + 80pt chart) before that side is sized, and takes
            // min(that, its cap). At the default (equal) priority the stack sizes the column
            // first with HALF the row, it takes its whole cap (128 after hours), and on a
            // 375pt phone the navigation side is left 167 < 176 — the row overflows its card.
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
    ///
    /// The slot FILLS what the day change leaves (trailing, so the chip still sits against
    /// it): its width is then "column - day change" whatever the chip decided, so a stack
    /// that re-offers a child its own measured width when placing it hands back the same
    /// number — never a value recomputed by subtraction that can land a float step short.
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
            .frame(maxWidth: .infinity, alignment: .trailing)
            .layoutPriority(-1)
        }
    }

    /// The gray after-hours tail, outside New York's regular session only — the chip's own
    /// rule, so a saved snapshot painted after the bell never draws yesterday's after-hours
    /// over the new day's line. Before the open it is still the LAST session's after-hours
    /// (owner, 2026-11-11); today's pre-market moves only the ☀ chip.
    private var afterHoursTail: SparklineTail? {
        guard Self.isOutsideRegularSession else { return nil }
        return asset.afterHoursTail
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

/// The holdings price column: ONE width for every card in a list, never the card's own.
///
/// `minWidth: 0` together with `maxWidth` is the whole fix. With both bounds given, a frame
/// takes the width it is offered clamped to them, whatever its content — so every card,
/// offered the same (row - 176pt navigation floor) by `.layoutPriority(1)`, resolves to the
/// same min(offer, cap). The navigation side, and with it the chart's x, is then identical
/// card to card. With `maxWidth` alone the frame would hug a narrower child again.
///
/// The regular width scales with Dynamic Type on headingSmall's curve (`.headline`) — the only
/// text in the column that grows; the after-hours width does not (see the constants).
struct HoldingsPriceColumn: ViewModifier {
    let reservesExtendedMove: Bool
    @ScaledMetric(relativeTo: .headline) private var regularWidth: CGFloat = AssetRow.priceColumnWidth

    // Explicit: the private @ScaledMetric would make the memberwise init private, and the
    // `View` extension below must be able to call it.
    init(reservesExtendedMove: Bool) {
        self.reservesExtendedMove = reservesExtendedMove
    }

    func body(content: Content) -> some View {
        content.frame(
            minWidth: 0,
            maxWidth: max(regularWidth, reservesExtendedMove ? AssetRow.extendedPriceColumnWidth : 0),
            alignment: .trailing
        )
    }
}

extension View {
    /// Size a holdings price column (the real row's, or the skeleton's placeholder bars).
    func holdingsPriceColumn(reservesExtendedMove: Bool) -> some View {
        modifier(HoldingsPriceColumn(reservesExtendedMove: reservesExtendedMove))
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
        AssetRow(asset: afterHours, reservesExtendedMove: true)
        AssetRow(asset: preMarket, reservesExtendedMove: true)
        AssetRow(asset: afterHours, changeDisplayMode: .amount, reservesExtendedMove: true)
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
}

// One list, one price column: every chart starts at the same x and every price and change
// ends on the same edge — chip or no chip, % or $, a coin row with no extended print, a
// six-figure price. Widths are a 375 / 393 / 402pt phone minus the list's 16pt insets. The
// chips follow New York's clock, so during the regular session none is drawn and the column
// narrows to the regular width (the whole list together).
#Preview("Column alignment") {
    func row(_ ticker: String, _ name: String, _ price: Double, _ change: Double,
             extended: Double? = nil, type: String = "stock") -> TrackedAsset {
        TrackedAsset(
            ticker: ticker,
            companyName: name,
            price: price,
            changePercent: change,
            sparklineData: [10, 11, 10.5, 12, 11.8, 12.4],
            assetType: type,
            marketCap: nil,
            shares: nil,
            marketValue: nil,
            sector: nil,
            country: nil,
            backendPreviousClose: price / (1 + change / 100),
            extendedPrice: extended.map { price * (1 + $0 / 100) },
            extendedChangePercent: extended,
            extendedSession: extended == nil ? nil : .afterHours,
            extendedSparkline: extended.map { move in [12.4, 12.3, 12.4 * (1 + move / 100)] } ?? [],
            extendedRegularShare: extended == nil ? nil : 0.619,
            extendedSpanTo: extended == nil ? nil : 0.95
        )
    }
    let rows = [
        row("ORCL", "Oracle", 128.40, 8.83, extended: -2.10),
        row("ACHR", "Archer Aviation", 4.86, 3.18, extended: -0.34),
        row("CRWV", "CoreWeave", 60.77, 24.71, extended: -12.37),
        row("ETHUSD", "Ethereum", 2_054.87, 0.48, type: "crypto"),
        row("BRK-A", "Berkshire Hathaway", 712_345.67, -0.12, extended: 0.05),
    ]
    let reserves = AssetRow.isOutsideRegularSession
    return ScrollView {
        VStack(spacing: AppSpacing.xl) {
            ForEach([343, 361, 370] as [CGFloat], id: \.self) { width in
                VStack(spacing: AppSpacing.sm) {
                    ForEach(rows) { asset in
                        AssetRow(asset: asset, reservesExtendedMove: reserves)
                    }
                    AssetRow(asset: rows[0], changeDisplayMode: .amount, reservesExtendedMove: reserves)
                }
                .frame(width: width)
            }
        }
    }
    .background(AppColors.background)
}
