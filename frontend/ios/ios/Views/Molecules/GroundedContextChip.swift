//
//  GroundedContextChip.swift
//  ios
//
//  Molecule: the "Grounded on …" pill shown at the top of a contextual chat.
//  Tells the user what Cay AI is reading (the report / stock / article / ...),
//  driven by the session's context_type + reference_id, and softened when the
//  server reports that grounding did not actually reach the turn.
//

import SwiftUI

struct GroundedContextChip: View {
    let contextType: ChatContextType
    /// A user-friendly reference (e.g. "AAPL"). nil hides the trailing detail.
    var referenceLabel: String? = nil
    /// The server's verdict for the latest turn (`ChatViewModel.contextGrounded`). nil = no
    /// verdict (no turn yet, an old server, or a context type the server does not vouch
    /// for) and renders the claim as before; `false` swaps it for the softened notice.
    var groundingArrived: Bool? = nil

    private var groundingUnavailable: Bool { groundingArrived == false }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: groundingUnavailable ? "info.circle" : contextType.groundingIcon)
                .font(.system(size: 10, weight: .semibold))
            Text(labelText)
                .font(AppTypography.captionEmphasis)
                .lineLimit(groundingUnavailable ? 2 : 1)
                .multilineTextAlignment(.center)
        }
        .foregroundColor(groundingUnavailable ? AppColors.textSecondary : AppColors.primaryBlue)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs)
        .background(
            Capsule().fill(groundingUnavailable ? Color.clear : AppColors.primaryBlue.opacity(0.12))
        )
        .overlay(
            Capsule().stroke(
                groundingUnavailable ? AppColors.border : AppColors.primaryBlue.opacity(0.30),
                lineWidth: 1
            )
        )
    }

    private var labelText: String {
        // Never "Grounded on …" once the server said the grounding did not arrive: the answer
        // underneath came from general knowledge, and the chip must not credit the report.
        if groundingUnavailable {
            return contextType.groundingUnavailableLabel
        }
        let ref = (referenceLabel ?? "").trimmingCharacters(in: .whitespaces)
        // A book's title IS the subject, so the category prefix only repeated it. The generic
        // label survives as the fallback for the (guarded, near-unreachable) case where the
        // reference cannot be resolved to a title — the entry points open an UNGROUNDED chat
        // rather than claim a book they could not identify.
        if contextType.groundingReferenceStandsAlone, !ref.isEmpty {
            return "Grounded on \(ref)"
        }
        let base = "Grounded on \(contextType.groundingLabel)"
        return ref.isEmpty ? base : "\(base) · \(ref)"
    }
}

#Preview {
    VStack(spacing: 12) {
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL")
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL", groundingArrived: true)
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL", groundingArrived: false)
        GroundedContextChip(contextType: .stock, referenceLabel: "TSLA")
        GroundedContextChip(contextType: .moneyMovesArticle)
        GroundedContextChip(contextType: .book, referenceLabel: "The Intelligent Investor")
        GroundedContextChip(contextType: .book)
    }
    .padding()
    .background(AppColors.background)
}
