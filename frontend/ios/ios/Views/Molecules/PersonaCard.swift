//
//  PersonaCard.swift
//  ios
//
//  Molecule: Persona selection card with icon, name, and tagline
//

import SwiftUI

struct PersonaCard: View {
    let persona: AnalysisPersona
    var isSelected: Bool = false
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.sm) {
                // Persona Icon
                PersonaIcon(
                    persona: persona,
                    size: 48,
                    isSelected: isSelected
                )

                // Name
                Text(persona.rawValue.components(separatedBy: " ").first ?? "")
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(persona.rawValue.components(separatedBy: " ").last ?? "")
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                // Tagline
                Text(persona.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(persona.accentColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                // Selection indicator
                if isSelected {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10))
                        Text("Selected")
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                } else {
                    Text("Tap to select")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .frame(width: 100)
            .padding(.vertical, AppSpacing.md)
            .padding(.horizontal, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(isSelected ? persona.accentColor.opacity(0.15) : AppColors.cardBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(
                                isSelected ? persona.accentColor : Color.clear,
                                lineWidth: 2
                            )
                    )
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            ForEach(AnalysisPersona.allCases) { persona in
                PersonaCard(
                    persona: persona,
                    isSelected: persona == .warrenBuffett
                )
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
