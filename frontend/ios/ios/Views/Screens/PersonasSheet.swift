//
//  PersonasSheet.swift
//  ios
//
//  Sheet shown when the user taps "View All" on the persona selector.
//  Lists every available persona with full description; tapping one
//  selects it and dismisses the sheet.
//

import SwiftUI

struct PersonasSheet: View {
    let personas: [AnalysisPersona]
    @Binding var selectedPersona: AnalysisPersona
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.md) {
                        ForEach(personas) { persona in
                            personaRow(persona)
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)
                    .padding(.bottom, AppSpacing.xxxl)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Choose Persona")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Subviews

    private func personaRow(_ persona: AnalysisPersona) -> some View {
        let isSelected = persona == selectedPersona
        return Button(action: {
            selectedPersona = persona
            dismiss()
        }) {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                PersonaIcon(persona: persona, size: 56, isSelected: isSelected)

                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack {
                        Text(persona.name)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        Spacer()
                        if isSelected {
                            Image(systemName: "checkmark.circle.fill")
                                .font(AppTypography.iconSmall)
                                .foregroundColor(persona.accentColor)
                        }
                    }
                    Text(persona.tagline)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(persona.accentColor)
                    Text(persona.description)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(AppSpacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(
                                isSelected ? persona.accentColor : Color.clear,
                                lineWidth: 1.5
                            )
                    )
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    PersonasSheet(
        personas: AnalysisPersona.allCases,
        selectedPersona: .constant(.warrenBuffett)
    )
    .preferredColorScheme(.dark)
}
