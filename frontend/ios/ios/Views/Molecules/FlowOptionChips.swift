//
//  FlowOptionChips.swift
//  ios
//
//  A wrapping row of single-label selection chips.
//
//  Promoted out of `OnboardingView` (where it was `private`) when the Settings
//  preferences editor needed the same control. Copying it would have given the two
//  screens chips that drift apart in padding, fill or checkmark behaviour — and this is
//  the control a reader uses to answer the same questions in both places, so they must
//  look and behave identically.
//
//  A Molecule, not an Atom: it composes the `FlowLayout` atom and carries selection
//  behaviour. It knows nothing about the domain — it is generic over `Option` — so it is
//  reusable for any closed-vocabulary picker.
//

import SwiftUI

/// Label-only chips. A sibling of `OnboardingView`'s `FlowChips` rather than a generic
/// rewrite of it: that one renders a symbol AND a company name in one capsule, which is a
/// different chip, and merging the two behind a generic would leave a harder-to-read view
/// for no reuse. Both share the same `FlowLayout` atom and the same fill/ink pairing
/// (`primaryFill` + `textOnAccent` — never one without the other).
struct FlowOptionChips<Option: Hashable>: View {
    let options: [Option]
    let title: (Option) -> String
    let isSelected: (Option) -> Bool
    let onTap: (Option) -> Void

    var body: some View {
        FlowLayout(spacing: AppSpacing.sm) {
            ForEach(options, id: \.self) { option in
                let on = isSelected(option)
                Button { onTap(option) } label: {
                    HStack(spacing: 6) {
                        if on {
                            Image(systemName: "checkmark")
                                .font(AppTypography.iconXS)
                        }
                        Text(title(option))
                            .font(AppTypography.bodySmallEmphasis)
                    }
                    .foregroundColor(on ? AppColors.textOnAccent : AppColors.textPrimary)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        Capsule().fill(on ? AppColors.primaryFill : AppColors.cardBackgroundLight)
                    )
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

#Preview {
    FlowOptionChips(
        options: ["Value", "Growth", "Dividends", "Technology"],
        title: { $0 },
        isSelected: { $0 == "Growth" },
        onTap: { _ in }
    )
    .padding()
}
