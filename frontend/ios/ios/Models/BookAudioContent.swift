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
    ]
}
