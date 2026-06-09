//
//  SignalOfConfidenceViewToggle.swift
//  ios
//
//  Atom: Toggle between Yield (%) and Capital ($) views for Signal of Confidence chart
//

import SwiftUI

struct SignalOfConfidenceViewToggle: View {
    @Binding var selectedView: SignalOfConfidenceViewType
    // Sizing knobs — defaults match the full Financials-tab chart; the compact
    // Capital Allocation mini-chart passes smaller values.
    var font: Font = AppTypography.bodySmallEmphasis
    var horizontalPadding: CGFloat = AppSpacing.md
    var verticalPadding: CGFloat = AppSpacing.xs + 2
    var innerPadding: CGFloat = AppSpacing.xs

    var body: some View {
        HStack(spacing: 0) {
            ForEach(SignalOfConfidenceViewType.allCases) { viewType in
                Button(action: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedView = viewType
                    }
                }) {
                    Text(viewType.rawValue)
                        .font(font)
                        .foregroundColor(selectedView == viewType ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, horizontalPadding)
                        .padding(.vertical, verticalPadding)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(selectedView == viewType ? AppColors.toggleSelectedBackground : Color.clear)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(innerPadding)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.toggleBackground)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            SignalOfConfidenceViewToggle(selectedView: .constant(.yield))
            SignalOfConfidenceViewToggle(selectedView: .constant(.capital))
        }
        .padding()
    }
}
