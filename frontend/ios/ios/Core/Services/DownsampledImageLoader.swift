//
//  DownsampledImageLoader.swift
//  ios
//
//  Loads a remote image ONCE, decodes it at display size, and hands the same bitmap to every
//  view that shows it.
//
//  Why it exists: `AsyncImage` fetches and decodes a full-size bitmap per instance. The
//  Emerging Frontiers heroes are 1290×1080 JPEGs (~5.6 MB decoded each), and the endless
//  carousel draws every theme several times over — as AsyncImages that would be ~40 full-size
//  decodes (~220 MB) and ~5 identical requests per URL on a cold start. Here each hero is one
//  request and one ~1.7 MB bitmap, shared by every copy.
//
//  Fetched through `URLSession.shared`, so the app's `URLCache` still applies. A failure is
//  logged and NOT cached — the next load retries — and callers fall back to their placeholder.
//

import Foundation
import OSLog
import UIKit

actor DownsampledImageLoader {
    static let shared = DownsampledImageLoader()

    private struct Key: Hashable {
        let url: URL
        let maxPixelSize: Int
    }

    private let session: URLSession
    private var cache: [Key: UIImage] = [:]
    private var inflight: [Key: Task<UIImage?, Never>] = [:]
    /// Heroes are a handful of small bitmaps; the bound only guards against a caller that
    /// ever feeds this an unbounded stream of URLs.
    private static let maxCachedImages = 48
    private static let log = Logger(subsystem: "com.phan.caydex", category: "image-downsample")

    init(session: URLSession = .shared) {
        self.session = session
    }

    /// The image at `url` with its longer side ≤ `maxPixelSize` pixels, or nil when it could
    /// not be fetched or decoded. Concurrent calls for the same image share one request.
    func image(at url: URL, maxPixelSize: CGFloat) async -> UIImage? {
        // Same contract as `ImageDownsampler`, checked BEFORE the Int conversion below —
        // `Int(CGFloat.nan)` traps, so a computed size (0 or NaN before layout) would crash.
        guard maxPixelSize.isFinite, maxPixelSize >= 1 else {
            Self.log.error("image request with an unusable size \(maxPixelSize, privacy: .public)")
            return nil
        }
        let key = Key(url: url, maxPixelSize: Int(maxPixelSize.rounded()))
        if let hit = cache[key] { return hit }
        if let running = inflight[key] { return await running.value }

        let session = self.session
        // Detached: the decode is CPU work and must not serialise behind this actor.
        let task = Task.detached(priority: .utility) { () -> UIImage? in
            do {
                let (data, response) = try await session.data(from: url)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    Self.log.error("image fetch failed: HTTP \(http.statusCode, privacy: .public) for \(url.lastPathComponent, privacy: .public)")
                    return nil
                }
                guard let bitmap = ImageDownsampler.downsample(data, maxPixelSize: maxPixelSize) else {
                    Self.log.error("image undecodable (\(data.count, privacy: .public) bytes) for \(url.lastPathComponent, privacy: .public)")
                    return nil
                }
                return UIImage(cgImage: bitmap)
            } catch {
                Self.log.warning("image fetch failed (\(String(describing: type(of: error)), privacy: .public)): \(error.localizedDescription, privacy: .public) for \(url.lastPathComponent, privacy: .public)")
                return nil
            }
        }
        inflight[key] = task
        let result = await task.value
        inflight[key] = nil
        if let result {
            if cache.count >= Self.maxCachedImages { cache.removeAll(keepingCapacity: true) }
            cache[key] = result
        }
        return result
    }
}
