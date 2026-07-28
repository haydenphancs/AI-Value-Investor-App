//
//  LegalDocumentView.swift
//  ios
//
//  Shared scaffold for long-form legal documents (Terms of Use, Privacy Policy).
//  Renders an intro + a list of heading/paragraph sections in the app's card style,
//  mirroring DisclaimersView. The same authored text is hosted as HTML on
//  caydexinvest.com (documents/legal/) to satisfy Apple's metadata-URL requirement;
//  these native screens satisfy the in-app "functional link" requirement.
//

import SwiftUI

struct LegalSection: Identifiable {
    let id = UUID()
    let heading: String
    let paragraphs: [String]
}

struct LegalDocumentView: View {
    let title: String
    let lastUpdated: String
    let intro: String
    let sections: [LegalSection]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Effective date + intro
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Last updated: \(lastUpdated)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text(intro)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.horizontal, AppSpacing.lg)

                    ForEach(Array(sections.enumerated()), id: \.element.id) { index, section in
                        legalCard(index: index + 1, section: section)
                    }

                    Spacer().frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.lg)
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }

    private func legalCard(index: Int, section: LegalSection) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("\(index). \(section.heading)")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(section.paragraphs, id: \.self) { paragraph in
                    Text(paragraph)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .padding(.horizontal, AppSpacing.lg)
    }
}
