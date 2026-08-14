//
//  BookCoverImage.swift
//  ios
//
//  Atom: a book cover — the generated artwork when we have it, today's gradient when
//  we don't. The single place a cover is drawn, replacing four separate copies.
//
//  WHY AN ATOM WITH A STRING API, NOT A MOLECULE TAKING A BOOK:
//  it has to serve LibraryBook, EducationBook AND SearchBookItem. The moment it takes
//  `book: LibraryBook` it becomes a molecule *and* it can only serve one of the three.
//  `title` is the only field all three share, and it is already this app's cross-model
//  key (LearnView matches EducationBook to LibraryBook by title; BookmarkStore is
//  title-keyed). CompanyLogoView is the precedent: takes `ticker: String`, resolves its
//  own remote URL, lives in Atoms/.
//

import SwiftUI

struct BookCoverImage: View {
    /// Resolves the cover via `BookCoverArt.forTitle(_:)`. Also the accessibility label
    /// and the fallback's on-cover text.
    let title: String
    var width: CGFloat = 80
    var height: CGFloat = 110
    var cornerRadius: CGFloat = AppCornerRadius.medium
    /// BookDetailView's 4pt spine strip on the leading edge. Drawn over the artwork too —
    /// that strip is what makes a rectangle read as a book.
    var showsSpine: Bool = false
    /// Gradient used when the book has no cover entry. Defaults to the neutral card
    /// gradient; `LibraryBook` passes its own two hexes.
    var fallbackGradient: (start: String, end: String)? = nil

    private var art: BookCoverArt? { BookCoverArt.forTitle(title) }

    private var remoteURL: URL? {
        guard let art else { return nil }
        let s = art.url(forWidth: width)
        guard s.hasPrefix("http") else { return nil }
        return URL(string: s)
    }

    var body: some View {
        ZStack {
            if let remoteURL {
                AsyncImage(url: remoteURL) { phase in
                    if let image = phase.image {
                        image.resizable().aspectRatio(contentMode: .fill)
                    } else {
                        // Loading AND error both fall back — same shape as
                        // TrendingThemeTile. A cold, offline launch therefore shows
                        // exactly today's UI rather than an empty hole.
                        fallback
                    }
                }
            } else {
                fallback
            }

            if showsSpine {
                HStack(spacing: 0) {
                    Rectangle()
                        .fill(Color.black.opacity(0.15))
                        .frame(width: 4)
                    Spacer()
                }
            }
        }
        .frame(width: width, height: height)
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        // Light mode separates a cover from the white card by this edge; in dark
        // `cardEdge` is alpha 0 by design and the artwork's own darkness does the work.
        .overlay(
            RoundedRectangle(cornerRadius: cornerRadius)
                .strokeBorder(AppColors.cardEdge, lineWidth: 0.5)
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("\(title), book cover"))
        .accessibilityAddTraits(.isImage)
    }

    /// Byte-identical to what every card drew before this atom existed, so a total
    /// pipeline failure is a visual no-op rather than a regression.
    private var fallback: some View {
        ZStack {
            // Per-book cover gradient — content data, not a theme token, and the same
            // pair the cards drew before this atom. theme-lint exempts `coverGradient`
            // for exactly this reason; keep the name so the exemption stays honest.
            LinearGradient(
                colors: [Color(hex: coverGradient.start), Color(hex: coverGradient.end)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Text(title.uppercased())
                .font(AppTypography.captionTiny).fontWeight(.bold)
                .foregroundColor(AppColors.textOnAccent)
                .multilineTextAlignment(.center)
                .lineLimit(3)
                .minimumScaleFactor(0.6)   // at AX5 this used to truncate to an ellipsis
                .padding(.horizontal, AppSpacing.xs)
        }
    }

    private var coverGradient: (start: String, end: String) {
        fallbackGradient ?? (start: "3B3B5C", end: "1E1E2E")
    }
}

#Preview("States") {
    VStack(spacing: AppSpacing.lg) {
        HStack(spacing: AppSpacing.lg) {
            // has generated art
            BookCoverImage(title: "The Intelligent Investor")
            // no cover entry -> gradient + title, i.e. the pre-existing rendering
            BookCoverImage(title: "Not A Real Book",
                           fallbackGradient: (start: "7C3AED", end: "4C1D95"))
        }
        // detail hero, with the spine
        BookCoverImage(title: "Rich Dad Poor Dad",
                       width: 160, height: 220, showsSpine: true)
    }
    .padding()
    .background(AppColors.background)
}
