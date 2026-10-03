//
//  ThinkingProcessCard.swift
//  ios
//
//  Molecule: the collapsible "thinking" card shown at the top of a Cay AI answer. While the
//  answer is generating it shows the live progress stage ("Reading AAPL's data") and auto-
//  expands to reveal the steps + sources; once done it collapses to a compact
//  "Done in Xs · N sources ▾" the user can re-expand. Modeled on SignalDisclosureRow's
//  header + rotating-chevron + move/opacity reveal pattern.
//
//  Report chat's live web search adds three things here: a "Searching the web…" header while
//  the search runs, a small "Web search" badge on the finished header, and web source pills
//  that open the article. The card never presents anything itself — a tap hands the URL to
//  `onOpenSource`, and the chat screen shows it in an in-app browser sheet.
//

import SwiftUI

struct ThinkingProcessCard: View {
    let thinking: ChatThinking
    var sources: [ChatSource] = []
    /// Opens a web source's article (the chat screen's in-app browser). nil → web pills render
    /// as plain labels, never as a button that does nothing.
    var onOpenSource: ((URL) -> Void)? = nil

    @State private var isExpanded = false
    /// Once the user taps the header we stop auto-collapsing so we don't fight them.
    @State private var didUserToggle = false

    /// The web search is running right now. Takes priority over "Thinking…" in the header.
    private var isSearchingWeb: Bool {
        thinking.isActive && thinking.webSearchState == .searching
    }

    /// The finished answer used web results → the header carries the "Web search" badge.
    private var showsWebSearchBadge: Bool {
        !thinking.isActive && thinking.webSearchState == .done
    }

    /// The pills the user can actually see. The server's stored `source_count` does not count
    /// the live web pills, so once pills are on the card their count wins.
    private var visibleSourceCount: Int {
        sources.isEmpty ? (thinking.sourceCount ?? 0) : sources.count
    }

    private var sourcesText: String? {
        let n = visibleSourceCount
        return n > 0 ? "\(n) source\(n == 1 ? "" : "s")" : nil
    }

    private var headerText: String {
        if thinking.isActive {
            if isSearchingWeb { return "Searching the web…" }
            return thinking.reasoningText != nil ? "Thinking…" : (thinking.stages.last ?? "Thinking…")
        }
        let done = "Done in \(thinking.elapsedSeconds)s"
        // With the badge, the source count is drawn after it instead (see `header`).
        if showsWebSearchBadge { return done }
        return sourcesText.map { "\(done) · \($0)" } ?? done
    }

    /// One spoken line for the header, so VoiceOver reads the badge as words rather than as
    /// "Done in 5s, dot, Web search, dot, 3 sources".
    private var headerAccessibilityLabel: String {
        guard showsWebSearchBadge else { return headerText }
        return ([headerText, "Web search"] + [sourcesText].compactMap { $0 }).joined(separator: ", ")
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
            .accessibilityLabel(headerAccessibilityLabel)

            if isExpanded {
                expandedBody
                    // ⚠️ `.opacity` ALONE — do not put `.move(edge: .top)` back.
                    // This content is revealed at the BOTTOM of a clipped container, so a top-edge move
                    // starts it offset UPWARD by its own height and slides it down THROUGH everything
                    // above it, translucent the whole way. On the Daily Scanners card that meant the
                    // leaderboard swept across the card's own header, hero and CTA, and was reported from
                    // TestFlight as "words coming from the background ... looks like a bug".
                    // A fade moves nothing and cannot overlap anything.
                    // (Genuinely top-anchored things — the audio status island, a banner pinned to the top
                    // of a screen — are the opposite case and keep their `.move(edge: .top)`.)
                    .transition(.opacity)
            }
        }
        .padding(10)
        .background(AppColors.textPrimary.opacity(0.035))
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
            if isSearchingWeb {
                Image(systemName: "globe")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(AppColors.primaryBlue)
            }
            Text(headerText)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(thinking.isActive ? AppColors.textSecondary : AppColors.textMuted)
                .lineLimit(1)
                .layoutPriority(1)
            if showsWebSearchBadge {
                webSearchBadge
            }
            Spacer(minLength: 6)
            Image(systemName: "chevron.down")
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(AppColors.textMuted)
                .rotationEffect(.degrees(isExpanded ? 180 : 0))
        }
        .contentShape(Rectangle())
    }

    /// "· [globe Web search] · 3 sources" after "Done in Xs". The badge is the answer's marker
    /// that third-party pages were read, so it never truncates; the source count may.
    private var webSearchBadge: some View {
        HStack(spacing: AppSpacing.xs) {
            Text("·")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)
            TintedTagBadge(
                text: "Web search",
                color: AppColors.primaryBlue,
                systemImage: "globe",
                font: AppTypography.captionSmallEmphasis
            )
            .fixedSize()
            if let sourcesText {
                Text("· \(sourcesText)")
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
            }
        }
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

            // Sources: grounding pills first, then web pills (the server's order).
            if !sources.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.xs) {
                        ForEach(sources) { source in
                            if source.isWeb {
                                webSourcePill(source)
                            } else {
                                sourcePill(source)
                            }
                        }
                    }
                }
            }
        }
        .padding(.top, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A grounding pill (what the answer read on this screen). Not a link.
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

    /// A web source: a link into the in-app browser when the pill carries a safe https address
    /// (`ChatSource.webURL`) AND the screen can open it; otherwise the same capsule as a plain
    /// label, so nothing that looks tappable is dead.
    @ViewBuilder
    private func webSourcePill(_ source: ChatSource) -> some View {
        if let url = source.webURL, let onOpenSource {
            Button {
                onOpenSource(url)
            } label: {
                webPillLabel(source, isLink: true)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(source.webAccessibilityLabel)
            .accessibilityHint("Opens the article in an in-app browser")
            .accessibilityAddTraits(.isLink)
        } else {
            webPillLabel(source, isLink: false)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(source.webAccessibilityLabel)
        }
    }

    /// Globe, "Publisher · Oct 1, 2026", and an outward arrow only on a link. Same text-role ink
    /// and capsule as the grounding pill; the globe and the arrow are what set it apart.
    private func webPillLabel(_ source: ChatSource, isLink: Bool) -> some View {
        let publisher = source.webPublisherName ?? "Web"
        let text = source.publishedDisplay.map { "\(publisher) · \($0)" } ?? publisher
        return HStack(spacing: 3) {
            Image(systemName: "globe")
                .font(.system(size: 9, weight: .semibold))
            Text(text)
                .font(AppTypography.captionSmall)
                .lineLimit(1)
            if isLink {
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 8, weight: .bold))
            }
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
            // Active, web search running: the header reads "Searching the web…".
            ThinkingProcessCard(
                thinking: ChatThinking(stages: ["Consulting the report"], sourceCount: 1,
                                       elapsedMs: nil, webSearchState: .searching),
                sources: [ChatSource(label: "Cay research report", detail: "AAPL")]
            )
            // Active with web pills (the done card collapses on appear, so they show here).
            // Sample sources only: reserved example.com hosts, no figures in any title.
            ThinkingProcessCard(
                thinking: ChatThinking(stages: ["Consulting the report", "Searching the web"],
                                       sourceCount: 4, elapsedMs: nil, webSearchState: .done),
                sources: [
                    ChatSource(label: "Cay research report", detail: "AAPL"),
                    // Tappable: publisher, date and an https link.
                    ChatSource(label: "Web", detail: "Example News", kind: "web",
                               title: "Company outlines its product roadmap",
                               url: "https://www.example.com/articles/roadmap",
                               publishedAt: "2026-10-01"),
                    // No publisher name and no date: falls back to the host.
                    ChatSource(label: "Web", detail: nil, kind: "web",
                               title: "Industry overview",
                               url: "https://news.example.org/industry-overview",
                               publishedAt: nil),
                    // A non-https address: a plain label, never a link.
                    ChatSource(label: "Web", detail: "Example Blog", kind: "web",
                               title: "Not a link",
                               url: "javascript:void(0)",
                               publishedAt: "2026-09-30")
                ],
                onOpenSource: { _ in }
            )
            // Done, web search used: "Done in 5s · Web search · 2 sources".
            ThinkingProcessCard(
                thinking: ChatThinking(stages: [], sourceCount: 1, elapsedMs: 5200,
                                       reasoning: "Compared the report with recent coverage.",
                                       webSearchState: .done),
                sources: [ChatSource(label: "Cay research report", detail: "AAPL"),
                          ChatSource(label: "Web", detail: "Example News", kind: "web",
                                     title: "Company outlines its product roadmap",
                                     url: "https://www.example.com/articles/roadmap",
                                     publishedAt: "2026-10-01")],
                onOpenSource: { _ in }
            )
            // Done, no web search (unchanged).
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
}
