//
//  ShareholderBreakdownBar.swift
//  ios
//
//  Molecule: Horizontal stacked bar chart showing shareholder breakdown
//  Displays insiders, institutions, and public/other ownership percentages
//

import SwiftUI

struct ShareholderBreakdownBar: View {
    let insidersPercent: Double
    let institutionsPercent: Double
    let publicOtherPercent: Double

    // Configuration
    private let barHeight: CGFloat = 14
    private let cornerRadius: CGFloat = 7

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                // Insiders segment (Orange)
                if insidersPercent > 0 {
                    RoundedRectangle(cornerRadius: 0)
                        .fill(HoldersColors.insiders)
                        .frame(width: segmentWidth(for: insidersPercent, totalWidth: geometry.size.width))
                }

                // Institutions segment (Blue)
                if institutionsPercent > 0 {
                    Rectangle()
                        .fill(HoldersColors.institutions)
                        .frame(width: segmentWidth(for: institutionsPercent, totalWidth: geometry.size.width))
                }

                // Public/Other segment (Gray)
                if publicOtherPercent > 0 {
                    Rectangle()
                        .fill(HoldersColors.publicOther)
                        .frame(width: segmentWidth(for: publicOtherPercent, totalWidth: geometry.size.width))
                }
            }
            .frame(height: barHeight)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        }
        .frame(height: barHeight)
    }

    private func segmentWidth(for percent: Double, totalWidth: CGFloat) -> CGFloat {
        let total = insidersPercent + institutionsPercent + publicOtherPercent
        guard total > 0 else { return 0 }
        return (percent / total) * totalWidth
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            // Sample from design
            ShareholderBreakdownBar(
                insidersPercent: 12,
                institutionsPercent: 55,
                publicOtherPercent: 33
            )

            // More insider-heavy
            ShareholderBreakdownBar(
                insidersPercent: 45,
                institutionsPercent: 35,
                publicOtherPercent: 20
            )

            // Institution-dominated
            ShareholderBreakdownBar(
                insidersPercent: 5,
                institutionsPercent: 85,
                publicOtherPercent: 10
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}
