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
    /// When true, a blinking caret trails the content (tokens still streaming in).
    var isStreaming: Bool = false
    // Futuristic-chat extras (assistant messages only; defaults keep other callers/previews working).
    var thinking: ChatThinking? = nil
    var sources: [ChatSource]? = nil
    var suggestions: [String]? = nil
    /// Only the LATEST answer shows follow-up chips (so every historical answer doesn't).
    var showFollowUps: Bool = false
    var onFollowUpTap: ((String) -> Void)? = nil

    /// True while the thinking card is still "working" (reasoning/answer streaming, not yet done).
    private var thinkingActive: Bool {
        thinking?.isActive ?? false
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

            // Timestamp hidden while working (streaming OR thinking).
            if !isStreaming && !thinkingActive {
                MessageTimestamp(time: timestamp, alignment: .leading)
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
            ChatStockWidgetView(widget: widgetData)

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
    .preferredColorScheme(.dark)
}
