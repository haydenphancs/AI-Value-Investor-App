//
//  NewsCardView.swift
//  ios
//
//  Molecule: the ONE news card, shared by the ticker/crypto/ETF/index/commodity
//  detail News tabs (via TickerNewsCard) and the Updates Live News feed.
//
//  View-agnostic on purpose: it takes explicit fields, not a domain model, so
//  the two screens' different models (TickerNewsArticle, NewsArticle) both map
//  in without one depending on the other. `sentiment` is OPTIONAL — a nil hides
//  the badge, which is the Updates anti-fabrication rule (never show a `.neutral`
//  verdict no model produced). `timeAgo` is optional too: the Updates timeline
//  already shows the time on its rail, so it passes nil to avoid printing it
//  twice; the detail cards have no timeline, so they pass a value.
//

import SwiftUI

enum NewsCardStyle {
    /// Tap toggles inline AI bullets and shows the external-link/expand footer.
    /// Used by the detail News tabs.
    case expandable
    /// The whole card is one tap target → `onTap` (Updates navigates to the
    /// full News Detail screen). No inline expand, no footer.
    case tappable
}

struct NewsCardView: View {
    let headline: String
    let sourceName: String
    /// nil ⇒ no badge (the article has not been AI-enriched).
    var sentiment: NewsSentiment? = nil
    /// nil ⇒ hidden (caller already shows the time elsewhere, e.g. a timeline).
    var timeAgo: String? = nil
    var thumbnailName: String? = nil
    var imageURL: URL? = nil
    var relatedTickers: [String] = []
    var currentTicker: String? = nil
    var bullets: [String] = []
    var style: NewsCardStyle = .tappable
    /// True while an on-demand summary for THIS card is in flight (drives the
    /// inline spinner). Only meaningful on the Updates timeline, where tapping an
    /// un-enriched card summarises it in-app.
    var isSummarizing: Bool = false
    var onTap: (() -> Void)?
    var onExternalLinkTap: (() -> Void)?
    /// When set, tapping an un-enriched expandable card summarises it in-app
    /// (instead of `onTap`) — see the button action below.
    var onRequestSummary: (() -> Void)?
    var onTickerTap: ((String) -> Void)?

    @State private var isExpanded: Bool = false

    private var isExpandableStyle: Bool { style == .expandable }
    private var hasExpandableContent: Bool { !bullets.isEmpty }
    /// An un-enriched expandable card that can be summarised on tap.
    private var canSummarizeOnTap: Bool {
        isExpandableStyle && !hasExpandableContent && onRequestSummary != nil
    }

    /// Drop tokens that are not plausible tickers before rendering chips, and
    /// DE-DUPLICATE. Gemini occasionally emits a pseudo-ticker like "MARKET" (a
    /// chip that navigates nowhere) or repeats a symbol (`["AAPL","AAPL"]`) —
    /// and `TickerNewsRelatedTickers` keys its `ForEach` by the string itself, so
    /// a duplicate is a SwiftUI identity collision (dropped/glitched chip).
    private var displayTickers: [String] {
        var seen = Set<String>()
        var out: [String] = []
        for raw in relatedTickers {
            let t = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            guard (1...6).contains(t.count), t != "MARKET" else { continue }
            guard t.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" || $0 == "^" })
            else { continue }
            if seen.insert(t).inserted { out.append(t) }
        }
        return out
    }

    var body: some View {
        Button(action: {
            if isExpandableStyle && hasExpandableContent {
                withAnimation(.easeInOut(duration: 0.25)) { isExpanded.toggle() }
            } else if canSummarizeOnTap {
                // Un-enriched card: summarise it IN-APP rather than opening the
                // (often paywalled) publisher link. Reveal the area so the spinner
                // shows, then request the summary; bullets replace the spinner when
                // they arrive, and "Read full story" stays available either way.
                if !isExpanded {
                    withAnimation(.easeInOut(duration: 0.25)) { isExpanded = true }
                }
                onRequestSummary?()
            } else {
                onTap?()
            }
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                // Header: [sentiment] [time]  ·····  source
                HStack(spacing: AppSpacing.sm) {
                    if let sentiment {
                        NewsSentimentBadge(sentiment: sentiment)
                    }

                    if let timeAgo {
                        Text(timeAgo)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer(minLength: AppSpacing.sm)

                    Text(sourceName)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        // A long publisher name must truncate, not wrap the row
                        // taller than the thumbnail beside it.
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .layoutPriority(1)
                }

                // Content: headline + thumbnail
                HStack(alignment: .top, spacing: AppSpacing.xs) {
                    Text(headline)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(nil)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    Spacer(minLength: 0)

                    NewsThumbnail(
                        imageName: thumbnailName,
                        imageURL: imageURL,
                        width: 72,
                        height: 40
                    )
                }

                // Related tickers
                if !displayTickers.isEmpty {
                    TickerNewsRelatedTickers(
                        tickers: displayTickers,
                        currentTicker: currentTicker,
                        onTickerTap: onTickerTap
                    )
                }

                if isExpandableStyle {
                    // Inline AI summary on expand — bullets, or the on-demand
                    // summarise states (spinner while in flight; an honest fallback
                    // with a link if the summary came back empty).
                    if isExpanded && hasExpandableContent {
                        TickerNewsExpandedContent(bullets: bullets)
                            .padding(.top, AppSpacing.xs)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    } else if isExpanded && isSummarizing {
                        summarizingRow
                            .padding(.top, AppSpacing.xs)
                            .transition(.opacity)
                    } else if isExpanded && canSummarizeOnTap {
                        summaryUnavailableRow
                            .padding(.top, AppSpacing.xs)
                            .transition(.opacity)
                    }

                    // Footer: external link + expand toggle
                    TickerNewsCardFooter(
                        hasExpandableContent: hasExpandableContent,
                        isExpanded: isExpanded,
                        onExternalLinkTap: onExternalLinkTap,
                        onExpandToggle: {
                            withAnimation(.easeInOut(duration: 0.25)) { isExpanded.toggle() }
                        }
                    )
                }
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }

    /// Shown while an on-tap summary is being generated.
    private var summarizingRow: some View {
        HStack(spacing: AppSpacing.sm) {
            ProgressView()
                .controlSize(.small)
            Text("Summarizing…")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Shown when the on-tap summary came back empty (Gemini degraded). Keeps the
    /// reader's exit — the publisher link — reachable even though there are no
    /// bullets (the footer only shows the link once there is expandable content).
    private var summaryUnavailableRow: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Couldn't generate a summary right now.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
            if onExternalLinkTap != nil {
                Button(action: { onExternalLinkTap?() }) {
                    Text("Read the full story →")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            // Detail style: time shown, expandable.
            NewsCardView(
                headline: "Plug Power vs. FuelCell: Both Are Hot in 2026, but Only One Is Worth Buying Now",
                sourceName: "The Motley Fool",
                sentiment: .positive,
                timeAgo: "Yesterday",
                relatedTickers: ["PLUG", "FCEL", "MARKET"],
                currentTicker: "PLUG",
                bullets: ["Demand is improving.", "But dilution risk remains."],
                style: .expandable
            )

            // Updates style: NO time (timeline shows it), tappable, un-enriched
            // (no sentiment badge).
            NewsCardView(
                headline: "World Markets Watchlist: July 20, 2026",
                sourceName: "ETF Trends",
                sentiment: nil,
                timeAgo: nil,
                style: .tappable
            )
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
