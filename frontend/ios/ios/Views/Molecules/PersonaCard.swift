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
    private let cardHeight: CGFloat = 170

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
                }

                // Tagline - fixed height area
                Text(persona.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(persona.accentColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(height: 28) // Fixed height for tagline

                Spacer(minLength: 0)

                // Selection indicator
                Group {
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
    .preferredColorScheme(.dark)
}
