//
//  CaydexAIChatBar.swift
//  ios
//
//  Molecule: Shared bottom AI chat bar used across all detail screens.
//  Supports optional suggestion pills above the input field.
//

import SwiftUI

struct CaydexAIChatBar: View {
    @Binding var inputText: String
    var placeholder: String = "Ask Cay AI..."
    var suggestions: [String] = []
    /// When true the suggestion row drifts continuously (the global Ask Cay AI chat).
    /// Default false ⇒ the five detail bars keep a still row with no edit at their call
    /// sites, which is what the tester asked for: changing questions, no motion.
    ///
    /// Declared next to `suggestions` because Swift's memberwise init is positional:
    /// the call-site order has to mirror this one.
    var marquee: Bool = false
    var onSuggestionTap: ((String) -> Void)?
    var onSend: (() -> Void)?
    /// When true (e.g. AIChatScreen while the AI is replying), the send button is disabled so the
    /// user can't fire a second concurrent request. Default false ⇒ no change for other call sites.
    var isBusy: Bool = false
    /// The audio player's square to the left of the input (`ChatBarAudioControl`), shown while an
    /// episode is active. On for every screen with this bar; the AI chat screen alone turns it
    /// off, because its player sits in its top bar. Declared LAST: the memberwise init is
    /// positional, and every existing call site must keep compiling unchanged.
    var showsAudioControl: Bool = true

    /// The input card's height, measured, so the audio square matches it.
    @State private var inputHeight: CGFloat = 0

    private var canSend: Bool {
        !isBusy && !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Suggestion chips row (only shown when suggestions are provided).
            //
            // `MarqueeChipRow` rather than a ScrollView: it drifts when asked to, pauses on
            // touch so a moving chip is still tappable, and collapses to a still, pannable
            // row for Reduce Motion / VoiceOver / the detail bars — all through one
            // `isPaused` flag, with no second layout to keep in step. See that file's header.
            if !suggestions.isEmpty {
                MarqueeChipRow(
                    chips: suggestions,
                    drifts: marquee,
                    onTap: { onSuggestionTap?($0) }
                )
                .padding(.horizontal, AppSpacing.lg)
            }

            // Input row: the audio square (while an episode is active) to the LEFT of the input
            // card, as its own container. Spacing 0 — the square carries its own gap, so with no
            // episode the card is exactly as wide as it always was.
            HStack(spacing: 0) {
                if showsAudioControl {
                    ChatBarAudioControl(side: inputHeight)
                }

                // Input bar
                HStack(spacing: AppSpacing.md) {
                    // Sparkle icon
                    Image(systemName: AppSymbols.ai)
                        .font(AppTypography.iconMedium).fontWeight(.medium)
                        .foregroundColor(AppColors.primaryBlue)

                    // Text field
                    TextField(placeholder, text: $inputText)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                        // Tapping the bar just focuses the field (keyboard opens inline, the
                        // full chat cover stays closed) so the user can keep reading while typing.
                        // Hitting return fires the same send path as the button → opens the chat.
                        .submitLabel(.send)
                        .onSubmit {
                            if canSend {
                                onSend?()
                            }
                        }

                    // Send button
                    Button(action: {
                        if canSend {
                            onSend?()
                        }
                    }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(AppTypography.iconDisplay)
                            .foregroundColor(canSend ? AppColors.primaryBlue : AppColors.textMuted)
                    }
                    .buttonStyle(PlainButtonStyle())
                    .disabled(!canSend)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                .cardSurface(cornerRadius: AppCornerRadius.extraLarge, elevation: .raised)
                // The square's side follows the card's real height (it grows with text size).
                .onGeometryChange(for: CGFloat.self) { proxy in
                    proxy.size.height
                } action: { height in
                    inputHeight = height
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.md)
        .background(
            LinearGradient(
                colors: [
                    AppColors.background.opacity(0),
                    AppColors.background.opacity(0.7),
                    AppColors.background.opacity(0.95),
                    AppColors.background
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
    }
}

// MARK: - Suggestion Chip
struct CaydexAISuggestionChip: View {
    let text: String
    var onTap: (() -> Void)?

    /// What the pill DRAWS: 11pt caption + 8pt padding top and bottom.
    fileprivate static let visualHeight: CGFloat = 27
    /// What it can be HIT on. Apple's HIG minimum, not a tunable.
    fileprivate static let hitTargetHeight: CGFloat = 44

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(text)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                // Both REQUIRED by the marquee, not cosmetic: without them a long question
                // wraps, so the tile's width becomes a function of the width available to
                // it, and the loop unit never settles.
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .cardFill()
                )
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
                )
                // The pill stays its designed ~27pt — this is HIT AREA only, bringing the
                // target to Apple's 44pt minimum. Padding first, `contentShape` after:
                // `.hitSlop()` is a documented no-op on a Button label (its negative
                // padding hands the frame straight back), and a contentShape applied
                // BEFORE the padding would shrink back to the glyphs.
                .padding(.vertical, (Self.hitTargetHeight - Self.visualHeight) / 2)
                .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - Preview
#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()

                // With suggestions
                CaydexAIChatBar(
                    inputText: $text,
                    suggestions: ["What's the P/E ratio?", "Why does it move?", "What's the outlook?"]
                )

                // Without suggestions
                CaydexAIChatBar(
                    inputText: $text
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
