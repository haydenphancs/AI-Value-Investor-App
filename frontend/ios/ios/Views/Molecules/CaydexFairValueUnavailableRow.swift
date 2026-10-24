//
//  CaydexFairValueUnavailableRow.swift
//  ios
//
//  Molecule: the Caydex Fair Value Estimate's TEMPORARY gap — the backend could not compute
//  it right now (an upstream input was briefly down, `caydex_estimate_unavailable`). Before
//  this, that case drew no row at all, so a FRED outage looked like the estimate had never
//  existed. It is not a refusal, so it never says "Not modelled", and there is nothing to
//  open: no tap target.
//
//  Used by the Analysis tab's Valuation card and the Overview's compact row, so both say
//  the same thing.
//

import SwiftUI

struct CaydexFairValueUnavailableRow: View {
    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(CaydexFairValue.title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)
            Text(CaydexFairValue.temporarilyUnavailable)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        CaydexFairValueUnavailableRow()
            .padding()
            .cardSurface(cornerRadius: AppCornerRadius.large)
            .padding()
    }
}
