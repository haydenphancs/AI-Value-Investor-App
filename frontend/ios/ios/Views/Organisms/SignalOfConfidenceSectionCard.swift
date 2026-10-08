//
//  SignalOfConfidenceSectionCard.swift
//  ios
//
//  Organism: Complete Signal of Confidence Section card for the Financial tab
//  Displays dividends, buybacks, and shares outstanding over time
//

import SwiftUI

struct SignalOfConfidenceSectionCard: View {
    // MARK: - Properties

    let signalData: SignalOfConfidenceSectionData
    let onDetailTapped: () -> Void

    // MARK: - State

    /// The Yield % / Capital $ choice, saved on this device so the card opens the way the
    /// user last left it. It was `@State`: `TickerDetailView`'s tab switch tears the
    /// Financials tab down, so every tab switch, ticker and relaunch reset it to Yield
    /// (TestFlight 1.0 (9): "set up once and permanently keep them"). `@AppStorage` keeps
    /// every live card in step, so a screen further down the stack follows the change.
    /// A stable token, never the toggle wording ("Yield (%)"); "" or an unknown token reads
    /// as Yield and is not written back. A display preference of this phone, not account
    /// data: deliberately NOT cleared by `AppState.discardDataForEndedSession()`.
    @AppStorage("caydex_capital_return_view") private var storedViewToken: String = ""
    @State private var showInfoSheet: Bool = false

    /// The saved view (or Yield). Assigned only from a toggle tap, which saves it.
    private var selectedView: SignalOfConfidenceViewType {
        get { SignalOfConfidenceViewType(preferenceToken: storedViewToken) ?? .yield }
        nonmutating set { storedViewToken = newValue.preferenceToken }
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // View toggle (Yield % / Capital $)
            SignalOfConfidenceViewToggle(selectedView: Binding(
                get: { selectedView },
                set: { selectedView = $0 }
            ))
                .padding(.leading, AppSpacing.xs)

            // Main chart
            SignalOfConfidenceChartView(
                dataPoints: signalData.dataPoints,
                viewType: selectedView
            )
            .padding(.top, AppSpacing.sm)

            // Say what a yield bar IS, truthfully for EVERY bar. A bar is a trailing twelve
            // months (it was the quarter x4, which turned payment timing into fake swings)
            // — except where four consecutive quarters of cash flow are not on file (a
            // recent listing's first bars, the three after a missing quarter), which the
            // server still builds as that quarter x4. The wire does not mark which bars
            // those are, so the caption names both bases rather than promising one.
            if selectedView == .yield && !signalData.dataPoints.isEmpty {
                Text("Each bar: trailing 12 months of dividends or buybacks ÷ market cap at that quarter's end (that quarter × 4 where four consecutive quarters aren't on file).")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The key for the chart's "—" dividend/buyback cells, in BOTH views (the label
            // rows print it in Yield and Capital alike) and only when some quarter has no
            // cash-flow figures — otherwise there is no dash to explain. It names its two
            // rows, because the shares row prints its own "—" for an unreported share
            // count, and it names no CAUSE: the flag is false both for a quarter missing
            // from the filing history and for a series whose cash-flow fetch failed, so
            // "no filing on record" would be a false claim about the company in the second.
            if signalData.hasUnreportedCashFlow {
                Text("— in Dividends/Buybacks = cash-flow figures unavailable for that quarter")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Legend
            SignalOfConfidenceLegendView()
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.sm)

            // Dividend Info — or, for a non-payer, the buyback half on its own.
            // `dividendInfo` is nil for every company that pays no dividend, so this
            // branch used to render nothing at all and the buyback verdict (which
            // never depended on dividends) was silently dropped.
            if let dividendInfo = signalData.dividendInfo {
                // `dividendYield` (dividend-only) sits beside the dividend-only average;
                // the total shareholder yield gets its own row. It used to pass
                // `totalYield` into the row above the average — apples to oranges.
                DividendInfoCard(
                    dividendInfo: dividendInfo,
                    dividendYield: signalData.summary.dividendYield,
                    totalYield: signalData.summary.totalYield
                )
                    .padding(.top, AppSpacing.md)
            } else {
                BuybackOnlyInfoCard(
                    buybackStatus: signalData.summary.buybackStatus,
                    buybackYield: signalData.summary.buybackYield,
                    shareCountChange: signalData.summary.shareCountChange,
                    shareCountChangeKnown: signalData.summary.shareCountChangeKnown,
                    // "+36.3% since Q4 '24" — the span the change is measured over.
                    shareCountWindowStart: signalData.shareCountWindowStart
                )
                    .padding(.top, AppSpacing.md)
            }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            SignalOfConfidenceInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Signal of Confidence")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                SignalOfConfidenceInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            // The "Details" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
            // Button(action: onDetailTapped) {
            // Text("Details")
            // .font(AppTypography.bodySmallEmphasis)
            // .foregroundColor(AppColors.primaryBlue)
            // }
            // .buttonStyle(.plain)
        }
    }
}

// MARK: - Saved-choice token

/// What the saved view is stored as: the case name, never `rawValue` — that is the toggle's
/// wording ("Yield (%)"). An unknown token decodes to nil, so the card shows Yield and leaves
/// the store alone.
private extension SignalOfConfidenceViewType {
    var preferenceToken: String {
        switch self {
        case .yield: return "yield"
        case .capital: return "capital"
        }
    }

    init?(preferenceToken: String) {
        guard let match = Self.allCases.first(where: { $0.preferenceToken == preferenceToken }) else { return nil }
        self = match
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                SignalOfConfidenceSectionCard(
                    signalData: SignalOfConfidenceSectionData.sampleData,
                    onDetailTapped: {}
                )

                // Interior cash-flow gap (Q4 '24): its cells read "—" and the card adds the
                // one-line key for that dash.
                SignalOfConfidenceSectionCard(
                    signalData: SignalOfConfidenceSectionData.sampleInteriorCashFlowGap,
                    onDetailTapped: {}
                )

                // Returns no capital and dilutes: an empty bar band with its note, no left
                // axis, "$0" cells, and "+37.5% since Q4 '24" in the buyback card.
                SignalOfConfidenceSectionCard(
                    signalData: SignalOfConfidenceSectionData.sampleNoCapitalReturnDiluting,
                    onDetailTapped: {}
                )
            }
            .padding()
        }
    }
}
