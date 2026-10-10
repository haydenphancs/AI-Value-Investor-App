//
//  EarningsDataTypeToggle.swift
//  ios
//
//  Atom: Toggle button for switching between EPS and Revenue data types
//

import SwiftUI

struct EarningsDataTypeToggle: View {
    @Binding var selectedType: EarningsDataType

    var body: some View {
        HStack(spacing: 0) {
            ForEach(EarningsDataType.allCases, id: \.rawValue) { type in
                let isSelected = selectedType == type
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedType = type
                    }
                } label: {
                    // The short raw label fits the controls row; VoiceOver hears what the
                    // series really is ("Adjusted EPS", see EarningsDataType.seriesTitle).
                    Text(type.rawValue)
                        .accessibilityLabel(type.seriesTitle)
                        .font(AppTypography.labelSmallEmphasis)
                        .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        // Selection is an OUTLINE on the track, never a fill. The segment used
                        // to fill with primaryBlue, a TEXT token: the label on it measured 3.43
                        // light / 2.54 dark. Not toggleSelectedBackground either: against this
                        // cardBackgroundLight track it is 1.10 light / 1.37 dark, so the
                        // selection would all but vanish. The label stays on the track
                        // (textPrimary 15.53 / 14.12, textSecondary 6.62 / 7.37) and the stroke
                        // is primaryBlue at full opacity, 4.52 / 5.55 against it — the same
                        // "on" cue as EarningsPriceToggle beside it.
                        .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))
                        .overlay(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .strokeBorder(isSelected ? AppColors.primaryBlue : Color.clear, lineWidth: 1.5)
                        )
                }
                .buttonStyle(PlainButtonStyle())
                // The outline is the only visual cue besides the ink, so VoiceOver must hear it.
                .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
            }
        }
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsDataTypeToggle(selectedType: .constant(.eps))
    }
}

#Preview("Dark") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsDataTypeToggle(selectedType: .constant(.revenue))
    }
    .environment(\.colorScheme, .dark)
}
