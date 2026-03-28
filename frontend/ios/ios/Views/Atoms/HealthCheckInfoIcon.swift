//
//  HealthCheckInfoIcon.swift
//  ios
//
//  Atom: Info icon button that displays Health Check explanation sheet
//

import SwiftUI

struct HealthCheckInfoIcon: View {
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
            Text("Health Check")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            HealthCheckInfoIcon(action: {})
        }
        .padding()
    }
}
