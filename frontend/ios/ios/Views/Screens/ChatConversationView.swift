//
//  ChatConversationView.swift
//  ios
//
//  Chat conversation view with messages and input
//

import SwiftUI

struct ChatConversationView: View {
    @State private var messages: [RichChatMessage]
    @State private var inputText: String = ""
    @State private var isLoading: Bool = false

    init(messages: [RichChatMessage] = RichChatMessage.sampleConversation) {
        _messages = State(initialValue: messages)
    }

    var body: some View {
        VStack(spacing: 0) {
            // Messages list
            ChatMessagesList(messages: messages)

            // Page indicator (for multi-page responses)
            PageIndicatorDots(currentPage: 0, totalPages: 3)
                .padding(.vertical, AppSpacing.sm)

            // Input section
            ChatInputSection(
                inputText: $inputText,
                onAttachmentTap: handleAttachmentTap,
                onSend: handleSend,
                onVoiceTap: handleVoiceTap,
                onImageTap: handleImageTap
            )
        }
    }

    // MARK: - Action Handlers
    private func handleAttachmentTap() {
        print("Attachment tapped")
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // Add user message
        let userMessage = RichChatMessage(
            role: .user,
            content: [.text(inputText)],
            timestamp: Date()
        )
        messages.append(userMessage)

        let query = inputText
        inputText = ""
        isLoading = true

        // Simulate AI response (in real app, this would call your backend)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            let aiResponse = generateMockResponse(for: query)
            messages.append(aiResponse)
            isLoading = false
        }
    }

    private func handleVoiceTap() {
        print("Voice input tapped")
    }

    private func handleImageTap() {
        print("Image input tapped")
    }

    // MARK: - Mock Response Generator
    private func generateMockResponse(for query: String) -> RichChatMessage {
        let lowercased = query.lowercased()

        if lowercased.contains("sentiment") || lowercased.contains("feeling") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Based on the latest market data and social sentiment analysis, here's what I found:"),
                    .sentimentAnalysis(SentimentAnalysis(
                        overallSentiment: .bullish,
                        percentage: 68,
                        bulletPoints: [
                            ChatBulletPoint(text: "Strong delivery numbers exceeded expectations", indicatorType: .success),
                            ChatBulletPoint(text: "Production ramping up successfully", indicatorType: .success),
                            ChatBulletPoint(text: "Competition intensifying in the market", indicatorType: .warning),
                            ChatBulletPoint(text: "Analyst price targets vary widely", indicatorType: .info)
                        ],
                        dataUpdatedText: "Data updated just now"
                    ))
                ],
                timestamp: Date()
            )
        } else if lowercased.contains("price") || lowercased.contains("performance") || lowercased.contains("stock") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Here's the stock performance over the past month:"),
                    .stockPerformance(StockPerformance(
                        currentPrice: 242.84,
                        changePercent: 8.7,
                        period: "1 Month",
                        dayHigh: 245.12,
                        dayLow: 238.45,
                        volume: "124.5M",
                        avgVolume: "98.2M",
                        chartData: [220, 225, 218, 230, 235, 228, 240, 238, 245, 242],
                        followUpQuestion: "Would you like me to analyze any specific timeframe or technical indicators?"
                    ))
                ],
                timestamp: Date()
            )
        } else if lowercased.contains("risk") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Here are the major risk factors to monitor:"),
                    .riskFactors(RiskFactorsData(
                        introText: "",
                        factors: [
                            RiskFactor(
                                iconName: "exclamationmark.triangle.fill",
                                iconColor: AppColors.bearish,
                                title: "Market Competition",
                                description: "Intensifying competition from established players and new entrants",
                                impactLevel: .high
                            ),
                            RiskFactor(
                                iconName: "doc.text.fill",
                                iconColor: AppColors.neutral,
                                title: "Regulatory Changes",
                                description: "Potential changes in subsidies and regulations",
                                impactLevel: .medium
                            )
                        ]
                    )),
                    .tip(TipData(
                        title: "RISK MITIGATION TIP",
                        content: "Consider diversifying your portfolio to manage risk exposure."
                    ))
                ],
                timestamp: Date()
            )
        } else {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("I understand you're asking about \"\(query)\". Let me help you with that.\n\nBased on my analysis, here are some key points to consider:\n\n• Market conditions remain favorable\n• Technical indicators suggest stability\n• Fundamental analysis shows strong fundamentals\n\nWould you like me to dive deeper into any specific aspect?")
                ],
                timestamp: Date()
            )
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatConversationView()
    }
    .preferredColorScheme(.dark)
}
