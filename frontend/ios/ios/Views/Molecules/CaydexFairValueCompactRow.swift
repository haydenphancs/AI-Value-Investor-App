//
//  CaydexFairValueCompactRow.swift
//  ios
//
//  Molecule: the Caydex Fair Value Estimate in ONE compact block, for the Overview tab's
//  Price snapshot card (owner, 2026-10-23: "Overview tab row"). Before this the estimate
//  showed only on the Analysis tab, which is not the default, so most readers never saw it.
//  It sits under the card's header and stays visible while the card is collapsed.
//
//  Four states, the same as the Analysis tab's card:
//  • an estimate → the RANGE first (the owner's rule, 2026-09-26: "not only the exact
//    price"), then "Estimate $V · Price N% below/above the estimate", then the
//    "model estimate · not a price target" line;
//  • a refusal → "Not modelled" and the backend's reason;
//  • temporarily unavailable → `CaydexFairValueUnavailableRow` (not tappable);
//  • absent → nothing at all.
//  The estimate and the refusal open `CaydexFairValueSheet`, exactly as the full row does.
//
//  ⚠️ Wording is pinned by backend/tests/test_ios_fair_value.py and
//  test_ios_fair_value_placement.py: never Undervalued / Overvalued / Buy / Sell, and
//  never a value without its range. Text tokens only and no surface of its own: it lives
//  inside the Snapshots card.
//

import SwiftUI

struct CaydexFairValueCompactRow: View {
    let estimate: CaydexFairValue?
    /// The backend flagged the estimate as temporarily unavailable (no estimate sent).
    var isUnavailable: Bool = false
    /// The live header price the gap is measured against (nil → no gap line).
    let currentPrice: Double?

    @State private var showAssumptions = false

    var body: some View {
        if let estimate {
            Button {
                showAssumptions = true
            } label: {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.xs) {
                        ReportMetricRow(label: CaydexFairValue.title,
                                        value: headlineValue(estimate),
                                        valueColor: estimate.isEstimate ? AppColors.textPrimary
                                                                        : AppColors.textSecondary)
                        Image(systemName: "info.circle")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .accessibilityHidden(true)
                    }
                    switch estimate.state {
                    case .estimate:
                        if let line = estimateLine(estimate) {
                            Text(line)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        Text(CaydexFairValue.subtitle)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    case .refused(let reason):
                        Text(reason)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, AppSpacing.md)
                // LAST, after the padding: a Button is hit-tested on what its label draws,
                // so without it the gap between the lines is dead.
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText(estimate))
            .accessibilityHint("Shows the model's assumptions")
            .sheet(isPresented: $showAssumptions) {
                CaydexFairValueSheet(estimate: estimate, currentPrice: currentPrice)
            }
        } else if isUnavailable {
            CaydexFairValueUnavailableRow()
                .padding(.bottom, AppSpacing.md)
        }
    }

    /// The range for an estimate, "Not modelled" for a refusal.
    private func headlineValue(_ estimate: CaydexFairValue) -> String {
        switch estimate.state {
        case .estimate:
            return estimate.formattedRangeBounds ?? "—"
        case .refused:
            return CaydexFairValue.refusedHeadline
        }
    }

    /// "Estimate $229.53 · Price 18% below the estimate" (the gap only with a live price).
    private func estimateLine(_ estimate: CaydexFairValue) -> String? {
        guard let mid = estimate.formattedEstimate else { return nil }
        guard let gap = estimate.formattedGap(versus: currentPrice) else { return mid }
        return "\(mid) · \(gap)"
    }

    /// VoiceOver does not reliably read an en dash as "to", so the range is spelled out.
    private func accessibilityText(_ estimate: CaydexFairValue) -> String {
        switch estimate.state {
        case .estimate:
            let parts: [String?] = [CaydexFairValue.title, estimate.rangeAccessibilityLabel,
                         estimateLine(estimate), CaydexFairValue.subtitle]
            return parts.compactMap { $0 }.joined(separator: ". ")
        case .refused(let reason):
            return [CaydexFairValue.title, CaydexFairValue.refusedHeadline, reason].joined(separator: ". ")
        }
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            CaydexFairValueCompactRow(estimate: .sampleEstimate, currentPrice: 188.40)
            CaydexFairValueCompactRow(estimate: .sampleRefused, currentPrice: 300)
            CaydexFairValueCompactRow(estimate: nil, isUnavailable: true, currentPrice: 300)
            // Absent: draws nothing.
            CaydexFairValueCompactRow(estimate: nil, currentPrice: 300)
        }
        .padding()
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding()
    }
}
