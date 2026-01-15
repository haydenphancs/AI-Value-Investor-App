//
//  FinancialSectionHeader.swift
//  ios
//
//  Atom: Section header for financial sections with title, info button, and detail link
//

import SwiftUI

struct FinancialSectionHeader: View {
    let title: String
    var subtitle: String? = nil
    var infoTitle: String? = nil
    var infoDescription: String? = nil
    var showDetailLink: Bool = true
    var onDetailTap: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .center) {
            HStack(spacing: AppSpacing.sm) {
                Text(title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                if let infoTitle = infoTitle, let infoDescription = infoDescription {
                    FinancialInfoButton(
                        title: infoTitle,
                        description: infoDescription
                    )
                }

                if let subtitle = subtitle {
                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xs)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(AppColors.cardBackground)
                        )
                }
            }

            Spacer()

            if showDetailLink {
                Button {
                    onDetailTap?()
                } label: {
                    Text("Detail")
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 24) {
            FinancialSectionHeader(
                title: "Earnings",
                infoTitle: "Earnings",
                infoDescription: FinancialInfoContent.earnings
            )
            .padding(.horizontal)

            FinancialSectionHeader(
                title: "Health Check",
                subtitle: "[2/4] Mix",
                infoTitle: "Health Check",
                infoDescription: FinancialInfoContent.healthCheck
            )
            .padding(.horizontal)

            FinancialSectionHeader(
                title: "Growth",
                showDetailLink: true
            )
            .padding(.horizontal)
        }
    }
}
