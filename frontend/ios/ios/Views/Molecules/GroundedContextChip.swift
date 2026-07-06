//
//  GroundedContextChip.swift
//  ios
//
//  Molecule: the "Grounded on …" pill shown at the top of a contextual chat.
//  Tells the user what Cay AI is reading (the report / stock / article / ...),
//  driven by the session's context_type + reference_id.
//

import SwiftUI

struct GroundedContextChip: View {
    let contextType: ChatContextType
    /// A user-friendly reference (e.g. "AAPL"). nil hides the trailing detail.
    var referenceLabel: String? = nil

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: contextType.groundingIcon)
                .font(.system(size: 10, weight: .semibold))
            Text(labelText)
                .font(AppTypography.captionEmphasis)
                .lineLimit(1)
        }
        .foregroundColor(AppColors.primaryBlue)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs)
        .background(Capsule().fill(AppColors.primaryBlue.opacity(0.12)))
        .overlay(Capsule().stroke(AppColors.primaryBlue.opacity(0.30), lineWidth: 1))
    }

    private var labelText: String {
        let base = "Grounded on \(contextType.groundingLabel)"
        if let ref = referenceLabel, !ref.isEmpty {
            return "\(base) · \(ref)"
        }
        return base
    }
}

#Preview {
    VStack(spacing: 12) {
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL")
        GroundedContextChip(contextType: .stock, referenceLabel: "TSLA")
        GroundedContextChip(contextType: .moneyMovesArticle)
        GroundedContextChip(contextType: .book)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
