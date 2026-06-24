//
//  BookAudioContent.swift
//  ios
//
//  Per-book narration audio for the Book Library: ONE streamed .m4a per book (Supabase
//  'book-media' bucket) plus the start offset (seconds) of each core within that single file,
//  so the player can show a per-core timestamp and seek(to:) a core's start.
//
//  Generated from backend/data/book_audio/*.manifest.json by
//  backend/scripts/gen_book_audio_swift.py. Do not hand-edit — regenerate from the manifests.
//

import Foundation

struct BookAudioInfo {
    /// Public Supabase Storage URL of the single book narration file (streamed by AVPlayer).
    let audioUrl: String
    /// Real measured length of the whole book narration, in seconds.
    let totalSeconds: Int
    /// Core number -> start offset (seconds) within the single book audio file.
    let coreStartSeconds: [Int: Int]
}

extension BookAudioInfo {
    /// Keyed by LibraryBook.curriculumOrder. Only books with generated narration appear here;
    /// a missing order means "no narration yet" (the app shows no Listen audio for that book).
    static let byOrder: [Int: BookAudioInfo] = [
        1: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/1_rich-dad-poor-dad.m4a",
            totalSeconds: 1386,
            coreStartSeconds: [1: 0, 2: 193, 3: 395, 4: 575, 5: 775, 6: 985, 7: 1180]
        ),
        2: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/2_the-intelligent-investor.m4a",
            totalSeconds: 2246,
            coreStartSeconds: [1: 0, 2: 179, 3: 350, 4: 558, 5: 748, 6: 944, 7: 1114, 8: 1290, 9: 1486, 10: 1663, 11: 1858, 12: 2035]
        ),
        3: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/3_the-psychology-of-money.m4a",
            totalSeconds: 3040,
            coreStartSeconds: [1: 0, 2: 255, 3: 476, 4: 705, 5: 909, 6: 1252, 7: 1499, 8: 1730, 9: 2028, 10: 2235, 11: 2477, 12: 2748]
        ),
        4: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/4_one-up-on-wall-street.m4a",
            totalSeconds: 2361,
            coreStartSeconds: [1: 0, 2: 185, 3: 368, 4: 550, 5: 750, 6: 927, 7: 1107, 8: 1281, 9: 1535, 10: 1712, 11: 1896, 12: 2093]
        ),
        5: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/5_common-stocks-and-uncommon-profits.m4a",
            totalSeconds: 1141,
            coreStartSeconds: [1: 0, 2: 189, 3: 395, 4: 583, 5: 780, 6: 957]
        ),
        6: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/6_the-little-book-of-common-sense-investing.m4a",
            totalSeconds: 2867,
            coreStartSeconds: [1: 0, 2: 222, 3: 423, 4: 647, 5: 861, 6: 1046, 7: 1225, 8: 1480, 9: 1669, 10: 1893, 11: 2135, 12: 2337, 13: 2579]
        ),
        7: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/7_a-random-walk-down-wall-street.m4a",
            totalSeconds: 2081,
            coreStartSeconds: [1: 0, 2: 225, 3: 425, 4: 626, 5: 830, 6: 1020, 7: 1214, 8: 1424, 9: 1626, 10: 1846]
        ),
        8: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/8_the-essays-of-warren-buffett.m4a",
            totalSeconds: 1286,
            coreStartSeconds: [1: 0, 2: 180, 3: 423, 4: 669, 5: 860, 6: 1096]
        ),
        9: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/9_the-little-book-that-still-beats-the-market.m4a",
            totalSeconds: 1446,
            coreStartSeconds: [1: 0, 2: 237, 3: 495, 4: 876, 5: 1181]
        ),
        10: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/10_the-most-important-thing.m4a",
            totalSeconds: 3502,
            coreStartSeconds: [1: 0, 2: 231, 3: 473, 4: 690, 5: 906, 6: 1145, 7: 1389, 8: 1638, 9: 1904, 10: 2123, 11: 2443, 12: 2715, 13: 2941, 14: 3211]
        ),
    ]
}
