//
//  LearnAudioCache.swift
//  ios
//
//  On-device store for Learn narration audio (Books, Money Moves, Journey).
//
//  ⚠️ WHY THIS EXISTS — it is an egress fix, not a latency one.
//
//  `AVPlayer` does not use `URLCache`. For a PROGRESSIVE `.m4a` (which all Learn narration is —
//  there is no HLS here, so `AVAssetDownloadTask` is not applicable either) AVFoundation streams
//  the asset and `AudioManager.teardownPlayer()` throws the bytes away. So every replay, every
//  backward scrub, every `resume()` after completion and every app relaunch re-pulled the WHOLE
//  file from Supabase Storage. `book-media` is 263 MB across ten files of 17-45 MB, and that one
//  mechanism produced 14.69 GB of CDN egress against a 5 GB allowance from SEVEN users.
//
//  ⚠️ KEYED ON THE STORAGE OBJECT PATH, NEVER ON THE URL. Narration lives in private buckets
//  (migration 128) and is reached through signed URLs whose `?token=` rotates roughly 4x/day —
//  6h server-side reuse (`learn_audio_urls.py`) against a 1h client refresh
//  (`BookAudioURLStore`). A URL-keyed cache would therefore miss on a byte-identical file four
//  times a day and cache nothing. The path is the stable identity; the query string is noise.
//  `JourneyContentStore.refreshedClipURL(matching:)` relies on the same invariant.
//
//  ⚠️ BUCKET ALLOWLIST IS A BOUNDARY, NOT HYGIENE. Only the three narration buckets are ever
//  mirrored to disk. Signed URLs for `research-pdfs` and `user-avatars` also flow through this
//  app; writing those to a shared on-disk store keyed by path would outlive the signature and
//  survive sign-out. Anything unrecognised returns nil and simply streams as before.
//

import Darwin
import Foundation

// Constants live at file scope, NOT as statics on the `@MainActor` class: they are read from
// `nonisolated` helpers and from URLSession's completion queue, and a stored static inside a
// global-actor-isolated type inherits that isolation.

/// The only buckets we mirror. See the boundary note above.
private let cacheableBuckets: Set<String> = [
    "book-media", "money-moves-media", "journey-media",
]

/// The whole narration library is ~361 MB, so this holds all of it and still leaves the Caches
/// directory bounded. The OS may evict the directory under disk pressure — that is correct for
/// a cache, and a miss just streams.
private let maxBytesOnDisk: Int64 = 400 * 1024 * 1024

/// Extended attribute holding the ETag the file was downloaded with. Stored ON the file so
/// eviction and purge stay a single `removeItem` — a sidecar would need parallel bookkeeping.
private let etagAttribute = "com.caydex.learnaudio.etag"

@MainActor
final class LearnAudioCache {
    static let shared = LearnAudioCache()

    private let directory: URL
    /// Object paths with a download in flight, so a burst of play/warm calls fetches once.
    private var inFlight: Set<String> = []
    /// Object paths already revalidated this session — one HEAD per asset per run, so scrubbing
    /// and replaying don't spam the origin.
    private var revalidated: Set<String> = []

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        directory = caches.appendingPathComponent("LearnAudio", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    // MARK: - Identity

    /// `…/storage/v1/object/sign/book-media/audio/x.m4a?token=…` → `book-media/audio/x.m4a`.
    ///
    /// Returns nil for anything that isn't a cacheable Storage object — a third-party URL, an
    /// already-local file, or a bucket outside the allowlist. Callers treat nil as "stream it".
    nonisolated static func objectPath(for remote: URL) -> String? {
        guard remote.isFileURL == false else { return nil }
        let parts = remote.path.split(separator: "/").map(String.init)
        // Layout: storage / v1 / object / {sign|public|authenticated} / <bucket> / <path…>
        guard let objectIdx = parts.firstIndex(of: "object") else { return nil }
        var bucketIdx = objectIdx + 1
        guard bucketIdx < parts.count else { return nil }
        if ["sign", "public", "authenticated"].contains(parts[bucketIdx]) { bucketIdx += 1 }
        // Need a bucket AND at least one path component under it.
        guard bucketIdx + 1 < parts.count else { return nil }
        guard cacheableBuckets.contains(parts[bucketIdx]) else { return nil }
        return parts[bucketIdx...].joined(separator: "/")
    }

    /// Flatten an object path into a single filename. The result contains no `/`, so no input
    /// can escape the cache directory.
    nonisolated private static func filename(for objectPath: String) -> String {
        objectPath.replacingOccurrences(of: "/", with: "__")
    }

    private func fileURL(for objectPath: String) -> URL {
        directory.appendingPathComponent(Self.filename(for: objectPath))
    }

    // MARK: - Read

    /// A complete local copy of `remote`, or nil. Synchronous on purpose: the player build path
    /// (`AudioManager.preparePlayer`) is synchronous, and this is one `stat`.
    ///
    /// Playing from the returned file costs zero network AND makes seeking free, which removes
    /// the other half of the waste — every scrub used to re-issue range requests.
    func cachedFile(for remote: URL) -> URL? {
        guard let key = Self.objectPath(for: remote) else { return nil }
        let file = fileURL(for: key)
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        // Touch for LRU. Best-effort: a failure here only makes eviction less accurate.
        try? FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: file.path)
        revalidateInBackground(remote: remote, key: key, file: file)
        return file
    }

    /// Check in the background that the mirror still matches the origin, and drop it if not.
    ///
    /// ⚠️ WHY THIS IS NOT OPTIONAL. Narration paths are STABLE across a re-record — the Learn
    /// content playbook explicitly supports deleting a stale `.m4a` and regenerating under the
    /// same slug. Read-along timings ride in the lesson/article JSONB and would update
    /// immediately, so a permanently-pinned mirror would leave new timings highlighting OLD
    /// audio: the exact drift the alignment pipeline exists to prevent, and invisible because
    /// the clip still plays.
    ///
    /// Deliberately AFTER handing back the file rather than before: playback stays instant and
    /// costs nothing, one play may be stale, and the asset self-heals for the next one. A HEAD
    /// is a few hundred bytes against the 17-45 MB this is saving.
    private func revalidateInBackground(remote: URL, key: String, file: URL) {
        guard !revalidated.contains(key) else { return }
        revalidated.insert(key)
        guard let known = Self.storedETag(on: file), !known.isEmpty else { return }

        var request = URLRequest(url: remote)
        request.httpMethod = "HEAD"
        let task = URLSession.shared.dataTask(with: request) { _, response, _ in
            // Only a definite, differing ETag invalidates. A network failure, an offline
            // device or a server that omits the header must leave the mirror alone —
            // discarding it on an inconclusive answer would re-download the whole file for
            // nothing, which is the problem this class exists to solve.
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let current = http.value(forHTTPHeaderField: "ETag"), !current.isEmpty,
                  current != known else { return }
            Task { @MainActor [weak self] in
                print("ℹ️ [LearnAudioCache] \(key) changed upstream — dropping the stale mirror")
                self?.invalidate(remote)
            }
        }
        task.resume()
    }

    // MARK: - Write

    /// Fetch `remote` once into the store, in the background. No-op when already cached, already
    /// in flight, or not a cacheable Storage object.
    ///
    /// Callers decide WHEN this is worth doing — see `AudioManager.warmCacheIfWorthwhile`. A
    /// speculative warm of a 45 MB book for someone who listened for four seconds would cost
    /// more than it saves.
    func warm(_ remote: URL) {
        guard let key = Self.objectPath(for: remote) else { return }
        guard !inFlight.contains(key) else { return }
        let file = fileURL(for: key)
        guard !FileManager.default.fileExists(atPath: file.path) else { return }
        inFlight.insert(key)

        let task = URLSession.shared.downloadTask(with: remote) { tmp, response, error in
            // MUST move the temp file here, synchronously — URLSession deletes it the moment
            // this handler returns, so deferring the move onto the main actor would lose it.
            let stored = Self.persist(tmp: tmp, response: response, error: error, to: file, key: key)
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.inFlight.remove(key)
                if stored { self.trimToBudget() }
            }
        }
        task.resume()
    }

    /// Move the downloaded temp file into place. Returns whether it landed.
    ///
    /// Runs off the main actor, on URLSession's completion queue.
    nonisolated private static func persist(
        tmp: URL?, response: URLResponse?, error: Error?, to file: URL, key: String
    ) -> Bool {
        if let error {
            // Non-fatal by design — playback already streamed fine. Logged so a persistently
            // failing warm is visible rather than silently costing full egress forever.
            print("⚠️ [LearnAudioCache] warm failed for \(key): \(type(of: error)): \(error.localizedDescription)")
            return false
        }
        guard let tmp else { return false }
        // Only a complete 200 is cacheable. A 206, a 4xx signature failure or an error page must
        // never be written — a truncated .m4a on disk would break playback on every later play,
        // which is far worse than the egress this saves.
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            print("⚠️ [LearnAudioCache] warm refused for \(key): HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1)")
            return false
        }
        let expected = http.expectedContentLength
        if expected > 0,
           let size = try? FileManager.default.attributesOfItem(atPath: tmp.path)[.size] as? Int64,
           size != expected {
            print("⚠️ [LearnAudioCache] warm refused for \(key): got \(size) bytes, expected \(expected)")
            return false
        }
        do {
            // Replace rather than fail if a concurrent warm already landed.
            if FileManager.default.fileExists(atPath: file.path) {
                try FileManager.default.removeItem(at: file)
            }
            try FileManager.default.moveItem(at: tmp, to: file)
            // Record what we stored, so a later re-record under the same path is detectable.
            if let etag = http.value(forHTTPHeaderField: "ETag") {
                storeETag(etag, on: file)
            }
            return true
        } catch {
            print("⚠️ [LearnAudioCache] warm could not store \(key): \(type(of: error)): \(error.localizedDescription)")
            return false
        }
    }

    /// Drop the mirror for `remote` after a playback failure.
    ///
    /// ⚠️ LOAD-BEARING. Both failure paths (`AudioManager.handlePlaybackFailure`,
    /// `AIVoiceManager`'s clip recovery) assume a failure means an expired signature and retry
    /// with a freshly-minted URL. That retry goes back through `cachedFile(for:)` — so without
    /// this, a corrupt or truncated local file would be handed straight back to the player on
    /// every attempt and that asset would be permanently unplayable, with re-signing unable to
    /// help. Invalidating first makes the retry a real retry. A no-op when nothing is cached,
    /// which is the common case (the asset was streaming and the signature really had expired).
    func invalidate(_ remote: URL) {
        guard let key = Self.objectPath(for: remote) else { return }
        // Let a re-downloaded mirror be revalidated again later in this same session.
        revalidated.remove(key)
        let file = fileURL(for: key)
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        do {
            try FileManager.default.removeItem(at: file)
            print("ℹ️ [LearnAudioCache] dropped mirror for \(key) after a playback failure")
        } catch {
            print("⚠️ [LearnAudioCache] could not drop \(key): \(type(of: error)): \(error.localizedDescription)")
        }
    }

    // MARK: - Eviction

    /// Evict least-recently-used files until the store is under budget.
    private func trimToBudget() {
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.fileSizeKey, .contentModificationDateKey]
        guard let entries = try? fm.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: keys, options: .skipsHiddenFiles
        ) else { return }

        var sized: [(url: URL, size: Int64, touched: Date)] = []
        var total: Int64 = 0
        for url in entries {
            guard let values = try? url.resourceValues(forKeys: Set(keys)) else { continue }
            let size = Int64(values.fileSize ?? 0)
            total += size
            sized.append((url, size, values.contentModificationDate ?? .distantPast))
        }
        guard total > maxBytesOnDisk else { return }

        // Oldest touch first.
        for entry in sized.sorted(by: { $0.touched < $1.touched }) {
            guard total > maxBytesOnDisk else { break }
            do {
                try fm.removeItem(at: entry.url)
                total -= entry.size
            } catch {
                print("⚠️ [LearnAudioCache] evict failed for \(entry.url.lastPathComponent): \(error.localizedDescription)")
            }
        }
    }

    /// Drop every cached clip. Used by Settings → Clear Cache.
    func purgeAll() {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil, options: .skipsHiddenFiles
        ) else { return }
        for url in entries {
            try? fm.removeItem(at: url)
        }
    }
}


// MARK: - ETag storage (extended attributes)

/// Attach `etag` to `file`. Best-effort: without it the file simply never revalidates, which
/// degrades to the pre-existing behaviour rather than to a wrong one.
fileprivate func storeETag(_ etag: String, on file: URL) {
    guard let data = etag.data(using: .utf8) else { return }
    _ = file.withUnsafeFileSystemRepresentation { path -> Int32 in
        guard let path else { return -1 }
        return data.withUnsafeBytes { buffer in
            setxattr(path, etagAttribute, buffer.baseAddress, data.count, 0, 0)
        }
    }
}

extension LearnAudioCache {
    nonisolated fileprivate static func storedETag(on file: URL) -> String? {
        file.withUnsafeFileSystemRepresentation { path -> String? in
            guard let path else { return nil }
            let length = getxattr(path, etagAttribute, nil, 0, 0, 0)
            guard length > 0 else { return nil }
            var data = Data(count: length)
            let read = data.withUnsafeMutableBytes { buffer in
                getxattr(path, etagAttribute, buffer.baseAddress, length, 0, 0)
            }
            guard read > 0 else { return nil }
            return String(data: data, encoding: .utf8)
        }
    }
}
