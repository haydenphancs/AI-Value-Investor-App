//
//  SignalOfConfidenceViewToggle.swift
//  ios
//
//  Atom: Toggle between Yield (%) and Capital ($) views for Signal of Confidence chart
//

import SwiftUI

struct SignalOfConfidenceViewToggle: View {
    @Binding var selectedView: SignalOfConfidenceViewType

    var body: some View {
        HStack(spacing: 0) {
            ForEach(SignalOfConfidenceViewType.allCases) { viewType in
                Button(action: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedView = viewType
                    }
                }) {
                    Text(viewType.rawValue)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(selectedView == viewType ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.xs + 2)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(selectedView == viewType ? AppColors.toggleSelectedBackground : Color.clear)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
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
