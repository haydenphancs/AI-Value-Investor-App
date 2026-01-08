//
//  CompanyProfileRow.swift
//  ios
//
//  Molecule: Row displaying company profile information
//

import SwiftUI

struct CompanyProfileRow: View {
    let label: String
    let value: String
    var isLink: Bool = false

    var body: some View {
        HStack {
            Text(label)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.footnoteBold)
                .foregroundColor(isLink ? AppColors.primaryBlue : AppColors.textPrimary)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        CompanyProfileRow(label: "CEO", value: "Tim Cook")
        CompanyProfileRow(label: "Founded", value: "April 1, 1976")
        CompanyProfileRow(label: "Employees", value: "161,000")
        CompanyProfileRow(label: "Headquarters", value: "Cupertino, CA")
        CompanyProfileRow(label: "Website", value: "www.apple.com", isLink: true)
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
