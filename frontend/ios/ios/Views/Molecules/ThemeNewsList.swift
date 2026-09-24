//
//  ThemeNewsList.swift
//  ios
//
//  Molecule: "Latest news" on the theme detail — recent headlines across the theme's
//  stocks (one multi-symbol news feed, cached server-side). A tap opens the article in the
//  in-app browser via the caller.
//

import SwiftUI

struct ThemeNewsList: View {
    let items: [ThemeNewsItem]
    var onOpen: ((URL) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { pair in
                row(pair.element)
                if pair.offset < items.count - 1 {
                    Rectangle()
                        .fill(AppColors.textPrimary.opacity(0.06))
                        .frame(height: 1)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    @ViewBuilder
    private func row(_ item: ThemeNewsItem) -> some View {
        let content = VStack(alignment: .leading, spacing: 4) {
            Text(item.title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            Text(meta(item))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
        }
        .padding(.vertical, AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())

        if let url = item.url {
            Button { onOpen?(url) } label: { content }
                .buttonStyle(.plain)
                .accessibilityHint("Opens the article")
        } else {
            content
        }
    }

    private func meta(_ item: ThemeNewsItem) -> String {
        var parts: [String] = []
        if !item.source.isEmpty { parts.append(item.source) }
        if let date = item.publishedAt {
            parts.append(Self.relative.localizedString(for: date, relativeTo: Date()))
        }
        return parts.joined(separator: " · ")
    }

    private static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f
    }()
}

#Preview {
    ThemeNewsList(items: [
        ThemeNewsItem(dto: ThemeNewsItemDTO(title: "Chipmakers extend gains as AI orders climb",
                                            source: "Reuters", url: "https://example.com/a",
                                            publishedAt: "2026-09-23T14:10:00Z", ticker: "NVDA"))!,
        ThemeNewsItem(dto: ThemeNewsItemDTO(title: "Memory prices soften in September",
                                            source: "Bloomberg", url: nil,
                                            publishedAt: "2026-09-22T09:00:00Z", ticker: "MU"))!,
    ])
    .padding()
    .background(AppColors.background)
}
