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
    /// Report chats only: the analysis-style mode, e.g. "Growth Hunter Agent"
    /// (`ReportChatAgentMode.chatModeLabel`). Set, it turns the claim into
    /// "Cay AI · Growth Hunter Agent · AAPL report" — Cay AI stays the named speaker. Ignored on
    /// every other context type, and never shown over the softened notice.
    var agentModeLabel: String? = nil

    private var groundingUnavailable: Bool { groundingArrived == false }

    /// The mode label when it applies to THIS chip: a report chat, a non-blank label.
    private var activeModeLabel: String? {
        guard contextType == .tickerReport,
              let mode = agentModeLabel?.trimmingCharacters(in: .whitespaces),
              !mode.isEmpty
        else { return nil }
        return mode
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: groundingUnavailable ? "info.circle" : contextType.groundingIcon)
                .font(.system(size: 10, weight: .semibold))
            Text(labelText)
                .font(AppTypography.captionEmphasis)
                // The mode label runs ~240-275pt at the default size, so give it a second line
                // at large text sizes rather than truncate the speaker or the ticker away.
                .lineLimit(groundingUnavailable || activeModeLabel != nil ? 2 : 1)
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
        // One VoiceOver element with an explicit label: the glyph is decoration, and the
        // middle dots read as pauses rather than "dot".
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(labelText.replacingOccurrences(of: " · ", with: ", "))
    }

    private var labelText: String {
        // Never "Grounded on …" once the server said the grounding did not arrive: the answer
        // underneath came from general knowledge, and the chip must not credit the report.
        if groundingUnavailable {
            return contextType.groundingUnavailableLabel
        }
        let ref = (referenceLabel ?? "").trimmingCharacters(in: .whitespaces)
        // A report chat names its speaker and mode. The softened branch above still wins: a
        // report the answer never saw is never credited, whatever the mode.
        if contextType == .tickerReport, let mode = activeModeLabel {
            return ref.isEmpty ? "Cay AI · \(mode)" : "Cay AI · \(mode) · \(ref) report"
        }
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
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL", agentModeLabel: "Growth Hunter Agent")
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "GOOGL", groundingArrived: true, agentModeLabel: "Activist Concentrator Agent")
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "", agentModeLabel: "Deep Value Skeptic Agent")
        // The softened notice still wins over the mode.
        GroundedContextChip(contextType: .tickerReport, referenceLabel: "AAPL", groundingArrived: false, agentModeLabel: "Growth Hunter Agent")
        GroundedContextChip(contextType: .stock, referenceLabel: "TSLA")
        GroundedContextChip(contextType: .moneyMovesArticle)
        GroundedContextChip(contextType: .book, referenceLabel: "The Intelligent Investor")
        GroundedContextChip(contextType: .book)
    }
    .padding()
    .background(AppColors.background)
}
