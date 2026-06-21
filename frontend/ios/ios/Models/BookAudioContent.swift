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
            totalSeconds: 1054,
            coreStartSeconds: [1: 0, 2: 157, 3: 310, 4: 459, 5: 606, 6: 764, 7: 907]
        ),
        2: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/2_the-intelligent-investor.m4a",
            totalSeconds: 2246,
            coreStartSeconds: [1: 0, 2: 179, 3: 350, 4: 558, 5: 748, 6: 944, 7: 1114, 8: 1290, 9: 1486, 10: 1663, 11: 1858, 12: 2035]
        ),
        3: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/3_the-psychology-of-money.m4a",
            totalSeconds: 2878,
            coreStartSeconds: [1: 0, 2: 229, 3: 455, 4: 659, 5: 871, 6: 1156, 7: 1424, 8: 1658, 9: 1928, 10: 2135, 11: 2368, 12: 2583]
        ),
        4: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/4_one-up-on-wall-street.m4a",
            totalSeconds: 1987,
            coreStartSeconds: [1: 0, 2: 154, 3: 318, 4: 474, 5: 635, 6: 789, 7: 938, 8: 1081, 9: 1283, 10: 1430, 11: 1582, 12: 1738]
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
        8: BookAudioInfo(
            audioUrl: "https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/book-media/audio/8_the-essays-of-warren-buffett.m4a",
            totalSeconds: 1076,
            coreStartSeconds: [1: 0, 2: 155, 3: 360, 4: 554, 5: 731, 6: 906]
        ),
    ]
}
