//
//  AIMessageContent.swift
//  ios
//
//  Molecule: AI message content that renders different rich content types
//

import SwiftUI

struct AIMessageContent: View {
    let content: [RichContentType]
    let timestamp: String
    /// The message's real creation date, distinct from the formatted `timestamp` string.
    /// Rich cards that make a claim about NOW (the stock card's "Live" dot) need it to tell a
    /// freshly-generated payload from a replayed transcript row. nil keeps the previous behaviour.
    var messageDate: Date? = nil
    /// When true, a blinking caret trails the content (tokens still streaming in).
    var isStreaming: Bool = false
    // Futuristic-chat extras (assistant messages only; defaults keep other callers/previews working).
    var thinking: ChatThinking? = nil
    var sources: [ChatSource]? = nil
    var suggestions: [String]? = nil
    /// Only the LATEST answer shows follow-up chips (so every historical answer doesn't).
    var showFollowUps: Bool = false
    var onFollowUpTap: ((String) -> Void)? = nil
    /// What this turn cost — rendered ONLY when it cost less than usual (a free follow-up,
    /// or a refunded credit). nil on every normally-charged turn and every legacy message,
    /// which is the common case: stamping a price on every answer turns the chat into a
    /// meter, and the asking is the product.
    var credit: ChatTurnCostDTO? = nil

    /// True while the thinking card is still "working" (reasoning/answer streaming, not yet done).
    private var thinkingActive: Bool {
        thinking?.isActive ?? false
    }

    /// `gain` / `accentCyan` are TEXT-role tokens (4.5:1 in both appearances) — required,
    /// because `TintedTagBadge` draws the label IN this colour over a 12% tint of it. A
    /// `*Graphic` token here would be legible in dark and fail AA in light.
    private func creditTint(_ credit: ChatTurnCostDTO) -> Color {
        credit.outcome == "refunded" ? AppColors.gain : AppColors.accentCyan
    }

    private func creditIcon(_ credit: ChatTurnCostDTO) -> String {
        credit.outcome == "refunded" ? "arrow.uturn.backward.circle.fill" : "gift.fill"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Cay AI attribution
            CayAIMessageHeader()

            // Thinking card at the TOP of the answer (Copilot-style).
            if let thinking = thinking, thinking.shouldDisplay {
                ThinkingProcessCard(thinking: thinking, sources: sources ?? [])
            }

            // Answer body (skipped entirely during the pre-token thinking phase).
            if !content.isEmpty || isStreaming {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    ForEach(Array(content.enumerated()), id: \.offset) { _, item in
                        renderContent(item)
                    }
                    if isStreaming {
                        StreamingCaret()
                    }
                }
            }

            // Follow-up suggestion chips — only under the latest, finished answer.
            if showFollowUps, !isStreaming, !thinkingActive,
               let suggestions = suggestions, !suggestions.isEmpty {
                followUpChips(suggestions)
            }

            // Timestamp hidden while working (streaming OR thinking). The cost chip rides
            // the same metadata line — it is a footnote about the turn, not a second status
            // pill (mirrors the report card's "Refunded N credits" placement).
            if !isStreaming && !thinkingActive {
                HStack(spacing: AppSpacing.sm) {
                    MessageTimestamp(time: timestamp, alignment: .leading)
                    if let credit, credit.isWorthShowing, let label = credit.label {
                        TintedTagBadge(
                            text: label,
                            color: creditTint(credit),
                            systemImage: creditIcon(credit),
                            // A genuine short SENTENCE, not a word — 1 line would truncate
                            // "1 credit refunded — this answer…" to exactly the useless half.
                            textLineLimit: 2
                        )
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Full-width tappable follow-up questions (longer than the short empty-state chips, so they
    /// render as rows rather than reusing the horizontal SuggestionChipsRow).
    private func followUpChips(_ questions: [String]) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            ForEach(questions, id: \.self) { question in
                Button {
                    onFollowUpTap?(question)
                } label: {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "sparkles.2")
                            .font(.system(size: 11, weight: .semibold))
                        Text(question)
                            .font(AppTypography.bodySmall)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 0)
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .foregroundColor(AppColors.primaryBlue)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .fill(AppColors.primaryBlue.opacity(0.10))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(AppColors.primaryBlue.opacity(0.25), lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, AppSpacing.xs)
    }

    @ViewBuilder
    private func renderContent(_ content: RichContentType) -> some View {
        switch content {
        case .text(let text):
            MarkdownText(text: text)

        case .sentimentAnalysis(let analysis):
            SentimentAnalysisCard(analysis: analysis)

        case .stockPerformance(let performance):
            StockPerformanceCard(performance: performance)

        case .stockChart(let widgetData):
            ChatStockWidgetView(widget: widgetData, messageDate: messageDate)

        case .marketOverview(let widgetData):
            ChatMarketOverviewWidget(data: widgetData)

        case .riskFactors(let data):
            RiskFactorsCard(data: data)

        case .tip(let tipData):
            TipCard(tip: tipData)

        case .bulletPoints(let points):
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(points) { point in
                    BulletPointRow(bulletPoint: point)
                }
            }
        }
    }
}

#Preview {
    ScrollView {
        AIMessageContent(
            content: [
                .text("Based on the latest market data and social sentiment analysis, here's what I found about Tesla (TSLA):"),
                .sentimentAnalysis(SentimentAnalysis(
                    overallSentiment: .bullish,
                    percentage: 68,
                    bulletPoints: [
                        ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                        ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning)
                    ],
                    dataUpdatedText: "Data updated 5 minutes ago"
                ))
            ],
            timestamp: "2:37 PM"
        )
        .padding()
    }
    .background(AppColors.background)
}
