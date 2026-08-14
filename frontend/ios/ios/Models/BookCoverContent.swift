//
//  BookCoverContent.swift
//  ios
//
//  Generated cover art for the Book Library — one composited JPEG per book per size,
//  served from the PUBLIC Supabase 'book-covers' bucket (migration 133).
//
//  Generated from backend/data/book_covers/*.manifest.json by
//  backend/scripts/gen_book_covers_swift.py. Do not hand-edit — regenerate.
//
//  ⚠️ THE URL IS BAKED IN ON PURPOSE — the opposite of BookAudioContent.swift, and NOT
//  a regression of that file's signed-URL fix. Narration is Pro/Max, so its URL must be
//  minted per request. A COVER IS FREE: a locked, signed-out user must see it. Covers
//  therefore live in their own PUBLIC bucket, deliberately excluded from migration 128's
//  flip of book-media / journey-media / money-moves-media to private. Do not move covers
//  into book-media and do not add book-covers to 128 — either one blanks every cover in
//  the app the day 128 is applied.
//
//  Keyed by NORMALIZED TITLE, not curriculumOrder: LibraryBook has curriculumOrder but
//  EducationBook and SearchBookItem do not. Title is the only field all three share, and
//  it is already this app's cross-model key (LearnView matches EducationBook to
//  LibraryBook by title; BookmarkStore is title-keyed).
//
//  TWO sizes, and they are not interchangeable. The type is re-set optically at each
//  size rather than scaled, because a 2:1 downscale of composited type aliases and
//  shimmers during scroll. Always resolve via `url(forWidth:)`.
//

import CoreGraphics
import Foundation

struct BookCoverArt {
    /// 240x330 — the 80x110pt cards (LibraryBookCard, EducationBookCard, SearchBookCard).
    let thumbURL: String
    /// 480x660 — BookDetailView's 160x220pt hero.
    let heroURL: String

    /// Picks the master whose type was set for this slot. Never scales one into the
    /// other's job. 120pt sits between the two call sizes (80 and 160).
    func url(forWidth width: CGFloat) -> String { width > 120 ? heroURL : thumbURL }
}

extension BookCoverArt {
    /// Title -> cover, keyed by `normalizedKey(_:)`. A missing key means "no cover yet"
    /// and callers fall back to the gradient. Never force-unwrap this.
    static let byTitle: [String: BookCoverArt] = [
        "richdadpoordad": BookCoverArt(   // 1. Rich Dad Poor Dad
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/1_rich-dad-poor-dad.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/1_rich-dad-poor-dad.hero.jpg"
        ),
        "theintelligentinvestor": BookCoverArt(   // 2. The Intelligent Investor
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/2_the-intelligent-investor.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/2_the-intelligent-investor.hero.jpg"
        ),
        "thepsychologyofmoney": BookCoverArt(   // 3. The Psychology of Money
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/3_the-psychology-of-money.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/3_the-psychology-of-money.hero.jpg"
        ),
        "oneuponwallstreet": BookCoverArt(   // 4. One Up On Wall Street
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/4_one-up-on-wall-street.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/4_one-up-on-wall-street.hero.jpg"
        ),
        "commonstocksanduncommonprofits": BookCoverArt(   // 5. Common Stocks and Uncommon Profits
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/5_common-stocks-and-uncommon-profits.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/5_common-stocks-and-uncommon-profits.hero.jpg"
        ),
        "thelittlebookofcommonsenseinvesting": BookCoverArt(   // 6. The Little Book of Common Sense Investing
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/6_the-little-book-of-common-sense-investing.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/6_the-little-book-of-common-sense-investing.hero.jpg"
        ),
        "arandomwalkdownwallstreet": BookCoverArt(   // 7. A Random Walk Down Wall Street
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/7_a-random-walk-down-wall-street.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/7_a-random-walk-down-wall-street.hero.jpg"
        ),
        "theessaysofwarrenbuffett": BookCoverArt(   // 8. The Essays of Warren Buffett
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/8_the-essays-of-warren-buffett.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/8_the-essays-of-warren-buffett.hero.jpg"
        ),
        "thelittlebookthatstillbeatsthemarket": BookCoverArt(   // 9. The Little Book that Still Beats the Market
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/9_the-little-book-that-still-beats-the-market.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/9_the-little-book-that-still-beats-the-market.hero.jpg"
        ),
        "themostimportantthing": BookCoverArt(   // 10. The Most Important Thing
            thumbURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/10_the-most-important-thing.thumb.jpg",
            heroURL: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-covers/covers/10_the-most-important-thing.hero.jpg"
        ),
    ]

    /// Convenience for `LibraryBook`, which does carry a curriculum order.
    static let byOrder: [Int: BookCoverArt] = [
        1: byTitle["richdadpoordad"]!,
        2: byTitle["theintelligentinvestor"]!,
        3: byTitle["thepsychologyofmoney"]!,
        4: byTitle["oneuponwallstreet"]!,
        5: byTitle["commonstocksanduncommonprofits"]!,
        6: byTitle["thelittlebookofcommonsenseinvesting"]!,
        7: byTitle["arandomwalkdownwallstreet"]!,
        8: byTitle["theessaysofwarrenbuffett"]!,
        9: byTitle["thelittlebookthatstillbeatsthemarket"]!,
        10: byTitle["themostimportantthing"]!,
    ]

    /// Lowercased, every non-alphanumeric dropped. A capitalisation or punctuation edit
    /// in LearnModels.swift therefore cannot silently blank a cover. The generator emits
    /// keys through this exact transform.
    static func normalizedKey(_ title: String) -> String {
        title.lowercased().unicodeScalars
            .filter { CharacterSet.alphanumerics.contains($0) }
            .reduce(into: "") { $0.unicodeScalars.append($1) }
    }

    static func forTitle(_ title: String) -> BookCoverArt? { byTitle[normalizedKey(title)] }
    static func forOrder(_ order: Int) -> BookCoverArt? { byOrder[order] }
}
