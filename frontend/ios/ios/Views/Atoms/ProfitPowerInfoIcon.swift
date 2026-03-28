//
//  ProfitPowerInfoIcon.swift
//  ios
//
//  Atom: Info icon button that displays profit power explanation sheet
//

import SwiftUI

struct ProfitPowerInfoIcon: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "info.circle")
                .font(AppTypography.iconSmall).fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.sm) {
            Text("Profit Power")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            ProfitPowerInfoIcon(action: {})
        }
        .padding()
    }
}
