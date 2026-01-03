//
//  ChatTabView.swift
//  ios
//
//  Chat tab content view within the Learn/Wiser section
//

import SwiftUI

struct ChatTabView: View {
    @State private var inputText: String = ""
    @State private var suggestions: [SuggestionChip] = SuggestionChip.sampleData
    @State private var showingHistory: Bool = false
    @State private var isInConversation: Bool = false
    @State private var conversationMessages: [RichChatMessage] = []

    var onHistoryTap: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader(
                showingHistory: showingHistory,
                onHistoryTap: handleHistoryTap,
                onChevronTap: handleHistoryTap
            )

            if showingHistory {
                // History view
                ChatHistoryView(
                    onItemTap: handleHistoryItemTap,
                    onDismiss: { showingHistory = false }
                )
            } else if isInConversation {
                // Active conversation view
                ChatConversationView(messages: conversationMessages)
            } else {
                // Main chat entry view (empty state with suggestions)
                chatEntryView
            }
        }
    }

    // MARK: - Chat Entry View (Initial State)
    private var chatEntryView: some View {
        VStack(spacing: 0) {
            Spacer()

            // Suggestions section
            ChatSuggestionsSection(suggestions: suggestions) { chip in
                handleSuggestionTap(chip)
            }
            .padding(.bottom, AppSpacing.lg)

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
    private func handleHistoryTap() {
        withAnimation(.easeInOut(duration: 0.2)) {
            showingHistory.toggle()
        }
        onHistoryTap?()
    }

    private func handleHistoryItemTap(_ item: ChatHistoryItem) {
        // Load sample conversation for demo (in real app, load from backend)
        conversationMessages = RichChatMessage.sampleConversation

        withAnimation(.easeInOut(duration: 0.2)) {
            showingHistory = false
            isInConversation = true
        }
    }

    private func handleSuggestionTap(_ chip: SuggestionChip) {
        inputText = chip.text
        // Auto-send the suggestion
        handleSend()
    }

    private func handleAttachmentTap() {
        print("Attachment tapped")
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // Create user message
        let userMessage = RichChatMessage(
            role: .user,
            content: [.text(inputText)],
            timestamp: Date()
        )

        // Start conversation with user message
        conversationMessages = [userMessage]
        let query = inputText
        inputText = ""

        // Enter conversation mode
        withAnimation(.easeInOut(duration: 0.2)) {
            isInConversation = true
        }

        // Simulate AI response
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            let aiResponse = generateMockResponse(for: query)
            conversationMessages.append(aiResponse)
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

        if lowercased.contains("sentiment") || lowercased.contains("feeling") || lowercased.contains("tsla") || lowercased.contains("tesla") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Based on the latest market data and social sentiment analysis, here's what I found about Tesla (TSLA):"),
                    .sentimentAnalysis(SentimentAnalysis(
                        overallSentiment: .bullish,
                        percentage: 68,
                        bulletPoints: [
                            BulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                            BulletPoint(text: "Cybertruck production ramping up successfully", indicatorType: .success),
                            BulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning),
                            BulletPoint(text: "Analyst price targets range from $180-$350", indicatorType: .info)
                        ],
                        dataUpdatedText: "Data updated 5 minutes ago"
                    ))
                ],
                timestamp: Date()
            )
        } else if lowercased.contains("price") || lowercased.contains("performance") || lowercased.contains("stock") || lowercased.contains("aapl") || lowercased.contains("apple") {
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
                                description: "Traditional automakers and new EV startups intensifying competition globally",
                                impactLevel: .high
                            ),
                            RiskFactor(
                                iconName: "doc.text.fill",
                                iconColor: AppColors.neutral,
                                title: "Regulatory Changes",
                                description: "Potential changes in EV subsidies and environmental regulations",
                                impactLevel: .medium
                            ),
                            RiskFactor(
                                iconName: "shippingbox.fill",
                                iconColor: AppColors.neutral,
                                title: "Supply Chain Constraints",
                                description: "Battery materials and semiconductor availability concerns",
                                impactLevel: .medium
                            ),
                            RiskFactor(
                                iconName: "dollarsign.circle.fill",
                                iconColor: AppColors.primaryBlue,
                                title: "Valuation Concerns",
                                description: "High P/E ratio compared to traditional automakers",
                                impactLevel: .variable
                            )
                        ]
                    )),
                    .tip(TipData(
                        title: "RISK MITIGATION TIP",
                        content: "Consider diversifying your portfolio and maintaining a long-term investment horizon to weather short-term volatility."
                    ))
                ],
                timestamp: Date()
            )
        } else if lowercased.contains("crypto") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Here's what I found about the cryptocurrency market:"),
                    .sentimentAnalysis(SentimentAnalysis(
                        overallSentiment: .neutral,
                        percentage: 52,
                        bulletPoints: [
                            BulletPoint(text: "Bitcoin holding steady above key support levels", indicatorType: .success),
                            BulletPoint(text: "Regulatory uncertainty continues globally", indicatorType: .warning),
                            BulletPoint(text: "Institutional adoption increasing steadily", indicatorType: .info)
                        ],
                        dataUpdatedText: "Data updated 2 minutes ago"
                    ))
                ],
                timestamp: Date()
            )
        } else if lowercased.contains("tech") {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("Here's an overview of the tech sector:"),
                    .sentimentAnalysis(SentimentAnalysis(
                        overallSentiment: .bullish,
                        percentage: 72,
                        bulletPoints: [
                            BulletPoint(text: "AI and cloud computing driving strong growth", indicatorType: .success),
                            BulletPoint(text: "Major tech earnings exceeding expectations", indicatorType: .success),
                            BulletPoint(text: "Valuation concerns in some segments", indicatorType: .warning),
                            BulletPoint(text: "Interest rate environment remains key factor", indicatorType: .info)
                        ],
                        dataUpdatedText: "Data updated just now"
                    ))
                ],
                timestamp: Date()
            )
        } else {
            return RichChatMessage(
                role: .assistant,
                content: [
                    .text("I understand you're asking about \"\(query)\".\n\nBased on my analysis, here are some key points:\n\n• Current market conditions are being evaluated\n• Multiple factors are being considered\n• I recommend reviewing fundamental data\n\nWould you like me to provide more specific analysis on any particular aspect?")
                ],
                timestamp: Date()
            )
        }
    }
}

#Preview("Entry View") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatTabView()
    }
    .preferredColorScheme(.dark)
}

#Preview("History View") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatTabView()
    }
    .preferredColorScheme(.dark)
}
