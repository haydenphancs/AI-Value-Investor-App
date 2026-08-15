//
//  PaywallHeaderView.swift
//  ios
//
//  Molecule: the top of the upgrade sheet, written by whatever opened it.
//
//  The screen used to open with one fixed line — "Unlock more research / More monthly
//  credits for AI reports and Cay AI chat, plus priority analysis." — regardless of
//  whether the user had just tapped a narration lock, a masked signal ticker, or the
//  Account upgrade card. (It also promised a priority tier that does not exist: report
//  scheduling is global and no code path reads `tier`.)
//

import SwiftUI

struct PaywallHeaderView: View {
    let context: PaywallContext

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: context.symbol)
                .font(.system(size: 32))
                .foregroundColor(context.accent)
                .accessibilityHidden(true)

            Text(context.headline)
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)

            Text(context.subheadline)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.xxl) {
            ForEach(PaywallContext.allCases, id: \.rawValue) { context in
                PaywallHeaderView(context: context)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
