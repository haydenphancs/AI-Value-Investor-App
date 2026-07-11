//
//  ThinkingProcessCard.swift
//  ios
//
//  Molecule: the collapsible "thinking" card shown at the top of a Cay AI answer. While the
//  answer is generating it shows the live progress stage ("Reading AAPL's data") and auto-
//  expands to reveal the steps + grounded sources; once done it collapses to a compact
//  "Done in Xs · N sources ▾" the user can re-expand. Modeled on SignalDisclosureRow's
//  header + rotating-chevron + move/opacity reveal pattern.
//

import SwiftUI

struct ThinkingProcessCard: View {
    let thinking: ChatThinking
    var sources: [ChatSource] = []

    @State private var isExpanded = false
    /// Once the user taps the header we stop auto-collapsing so we don't fight them.
    @State private var didUserToggle = false

    private var headerText: String {
        if thinking.isActive {
            return thinking.reasoningText != nil ? "Thinking…" : (thinking.stages.last ?? "Thinking…")
        }
        let n = thinking.sourceCount ?? sources.count
        let src = n > 0 ? " · \(n) source\(n == 1 ? "" : "s")" : ""
        return "Done in \(thinking.elapsedSeconds)s\(src)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                didUserToggle = true
                withAnimation(.easeInOut(duration: 0.22)) { isExpanded.toggle() }
            } label: {
                header
            }
            .buttonStyle(.plain)

            if isExpanded {
                expandedBody
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(10)
        .background(Color.white.opacity(0.035))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(AppColors.primaryBlue.opacity(thinking.isActive ? 0.28 : 0.10), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        // Auto-expanded while thinking (so the user sees the steps), auto-collapsed when done.
        .onAppear { isExpanded = thinking.isActive }
        .onChange(of: thinking.elapsedMs) { _, elapsed in
            if elapsed != nil && !didUserToggle {
                withAnimation(.easeInOut(duration: 0.22)) { isExpanded = false }
            }
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.sm) {
            if thinking.isActive {
                ProgressView()
                    .controlSize(.small)
                    .tint(AppColors.primaryBlue)
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(AppColors.primaryBlue)
            }
            Text(headerText)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(thinking.isActive ? AppColors.textSecondary : AppColors.textMuted)
                .lineLimit(1)
            Spacer(minLength: 6)
            Image(systemName: "chevron.down")
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(AppColors.textMuted)
                .rotationEffect(.degrees(isExpanded ? 180 : 0))
        }
        .contentShape(Rectangle())
    }

    private var expandedBody: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            if let reasoning = thinking.reasoningText {
                // The model's streamed reasoning (grows sentence-by-sentence while active).
                Text(reasoning)
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                // Legacy discrete stages (older messages, or the non-reasoning fallback path).
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(Array(thinking.stages.enumerated()), id: \.offset) { idx, stage in
                        let isCurrent = thinking.isActive && idx == thinking.stages.count - 1
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: isCurrent ? "circle.dashed" : "checkmark")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundColor(isCurrent ? AppColors.primaryBlue : AppColors.textMuted)
                                .frame(width: 12)
                            Text(stage)
                                .font(AppTypography.captionSmall)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }
            }

            // Grounded sources
            if !sources.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.xs) {
                        ForEach(sources) { sourcePill($0) }
                    }
                }
            }
        }
        .padding(.top, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func sourcePill(_ source: ChatSource) -> some View {
        HStack(spacing: 3) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 9, weight: .semibold))
            Text(source.detail.map { "\(source.label) · \($0)" } ?? source.label)
                .font(AppTypography.captionSmall)
                .lineLimit(1)
        }
        .foregroundColor(AppColors.primaryBlue)
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, 3)
        .background(Capsule().fill(AppColors.primaryBlue.opacity(0.12)))
        .overlay(Capsule().stroke(AppColors.primaryBlue.opacity(0.28), lineWidth: 1))
    }
}

#Preview {
    ScrollView {
        VStack(alignment: .leading, spacing: 20) {
            // Active
            ThinkingProcessCard(
                thinking: ChatThinking(stages: ["Reading AAPL's data", "Reviewing the sources"],
                                       sourceCount: 2, elapsedMs: nil),
                sources: [ChatSource(label: "Company financials", detail: "AAPL"),
                          ChatSource(label: "SEC filing", detail: "Risk Factors")]
            )
            // Done
            ThinkingProcessCard(
                thinking: ChatThinking(stages: ["Reading AAPL's data", "Reviewing the sources",
                                                "Writing your answer"], sourceCount: 2, elapsedMs: 4200),
                sources: [ChatSource(label: "Cay research report", detail: "AAPL"),
                          ChatSource(label: "SEC filing", detail: "MD&A")]
            )
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
