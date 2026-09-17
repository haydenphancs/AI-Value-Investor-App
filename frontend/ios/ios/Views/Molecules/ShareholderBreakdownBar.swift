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
    /// The institutional figure is unknown (0.0 placeholder). The bar then normalises
    /// by 100 instead of the segment sum — otherwise a lone 0.1% insider slice would
    /// stretch into a full orange bar — and fills the remainder with a muted tint.
    var institutionsUnknown: Bool = false

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

                // Unknown remainder: muted, so the bar reads "not measured" rather than
                // drawing the insider slice as if it were the whole float.
                if institutionsUnknown {
                    Rectangle()
                        .fill(HoldersColors.publicOther.opacity(0.35))
                        .frame(maxWidth: .infinity)
                }
            }
            .frame(height: barHeight)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        }
        .frame(height: barHeight)
    }

    private func segmentWidth(for percent: Double, totalWidth: CGFloat) -> CGFloat {
        let total = institutionsUnknown
            ? 100.0
            : insidersPercent + institutionsPercent + publicOtherPercent
        guard total > 0 else { return 0 }
        return (min(percent, total) / total) * totalWidth
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
