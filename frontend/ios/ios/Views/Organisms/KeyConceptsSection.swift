//
//  KeyConceptsSection.swift
//  ios
//
//  Organism: Horizontal scrolling section of key concepts
//

import SwiftUI

struct KeyConceptsSection: View {
    let concepts: [KeyConcept]
    var onSeeAll: (() -> Void)?
    var onConceptTap: ((KeyConcept) -> Void)?
    var onBookmark: ((KeyConcept) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Key Concepts")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Must Read")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onSeeAll?()
                }) {
                    Text("See All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of concept cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(concepts) { concept in
                        KeyConceptCard(
                            concept: concept,
                            onTap: { onConceptTap?(concept) },
                            onBookmark: { onBookmark?(concept) }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    VStack {
        KeyConceptsSection(concepts: KeyConcept.sampleData)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
