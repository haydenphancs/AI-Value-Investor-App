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

    // Fixed card dimensions for consistent sizing
    private let cardWidth: CGFloat = 100
    private let cardHeight: CGFloat = 120

    /// Two-line name for the card. Computed on the model so the "drop the article,
    /// last word on its own line" rule lives in one place.
    private var nameLines: (top: String, bottom: String) { persona.cardNameLines }

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

                // Name (split into two lines)
                VStack(spacing: 0) {
                    // Omitted for a one-word name, so the card doesn't reserve a line
                    // for an empty string.
                    if !nameLines.top.isEmpty {
                        Text(nameLines.top)
                            .font(AppTypography.labelSmall)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                    }

                    Text(nameLines.bottom)
                        .font(AppTypography.labelSmall)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }

                // Tagline - fixed height area
                Text(persona.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(persona.accentColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(height: 28) // Fixed height for tagline

                Spacer(minLength: 0)
            }
            .frame(width: cardWidth, height: cardHeight)
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
}
