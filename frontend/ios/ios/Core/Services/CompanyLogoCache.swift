//
//  CompanyLogoCache.swift
//  ios
//
//  A company's FMP CDN logo, decoded once per process and readable SYNCHRONOUSLY from a
//  view's body.
//
//  Why it exists: `CompanyLogoView` drew through `AsyncImage`, which keeps its phase in
//  per-view state — every NEW view starts at `.empty` and draws the initials tile, even with
//  the PNG in `URLCache`. SwiftUI makes a new view whenever it rebuilds one: a `LazyVStack`
//  row scrolled back in, the Research ⇄ Reports segment (an if/else that destroys the list),
//  re-entering a report. The CDN sends only a weak ETag with no Cache-Control, so `URLCache`
//  revalidates — on mobile data the initials showed for a visible beat (TestFlight 1.0 (9):
//  "the whole screen here is blink"). Here a logo already shown this session is drawn on the
//  rebuilt view's FIRST frame.
//
//  Not `DownsampledImageLoader`: that is an actor (no synchronous read), it cannot tell "this
//  symbol has no logo" from "offline", and its 48-entry cache would evict the Home heroes.
//
//  The store is a plain byte-capped dictionary, oldest-first eviction. Public CDN images, no
//  account data — so NOT reset in `discardDataForEndedSession()` (auth.md §7 is about account
//  data). Fetched through `URLSession.shared`, so `URLCache` still applies underneath. Never
//  touch `.shared` before `configureImageCache()` has run.
//

import CoreGraphics
import Foundation
import OSLog
import UIKit

/// One fetch's verdict.
nonisolated enum CompanyLogoFetchResult: Sendable {
    /// Decoded at display size.
    case image(UIImage)
    /// The CDN answered and has no usable logo (404/410, or a body that is not an image).
    /// Remembered for `CompanyLogoCache.noLogoTTL`, so a rebuild does not refetch it.
    case noLogo
    /// Offline, timed out, 429, 5xx. Never remembered — the next appearance retries.
    case failed

    var image: UIImage? {
        if case .image(let image) = self { return image }
        return nil
    }
}

@MainActor
final class CompanyLogoCache {
    static let shared = CompanyLogoCache()

    /// Longest side kept, in pixels: the largest tile (TrillionClubCard, 56 pt) at @3x, with
    /// headroom. The CDN serves 100 or 250 px PNGs; `ImageDownsampler` never upscales.
    nonisolated static let maxPixelSize: CGFloat = 192
    /// How long a symbol the CDN has no logo for is not asked for again.
    static let noLogoTTL: TimeInterval = 10 * 60
    /// Decoded-logo byte budget. A 192 px RGBA logo is ~147 KB, a 100 px one ~40 KB.
    static let maxBytes = 16 * 1024 * 1024

    private var images: [String: UIImage] = [:]
    /// Insertion order, oldest first: what `store` evicts.
    private var order: [String] = []
    private var bytes = 0
    private var noLogoUntil: [String: Date] = [:]
    private var inflight: [String: Task<CompanyLogoFetchResult, Never>] = [:]
    private let session: URLSession
    nonisolated private static let log = Logger(subsystem: "com.phan.caydex", category: "company-logo")

    init(session: URLSession = .shared) {
        self.session = session
    }

    /// The cache key and the CDN file name: trimmed, uppercased. nil for a blank ticker.
    nonisolated static func symbol(for ticker: String) -> String? {
        let symbol = ticker.uppercased().trimmingCharacters(in: .whitespacesAndNewlines)
        return symbol.isEmpty ? nil : symbol
    }

    nonisolated static func url(for symbol: String) -> URL? {
        URL(string: "https://images.financialmodelingprep.com/symbol/\(symbol).png")
    }

    /// SYNCHRONOUS on purpose — `CompanyLogoView.body` calls it, so a rebuilt view draws a
    /// logo already shown this session on its first frame instead of the initials.
    func image(for symbol: String) -> UIImage? { images[symbol] }

    /// The logo for `symbol`; nil when the CDN has none (remembered briefly) or the fetch
    /// failed (not remembered). Concurrent loads of one symbol share one request.
    func load(_ symbol: String) async -> UIImage? {
        if let hit = image(for: symbol) { return hit }
        if isKnownMissing(symbol) { return nil }
        if let running = inflight[symbol] { return await running.value.image }
        guard let url = Self.url(for: symbol) else {
            Self.log.error("no logo URL for \(symbol, privacy: .public)")
            return nil
        }

        let session = self.session
        // Detached: decode is CPU work and must not run on the main actor. Not cancelled when
        // the asking view goes away — the result still warms the cache.
        let task = Task.detached(priority: .userInitiated) {
            await CompanyLogoCache.fetch(url, session: session)
        }
        inflight[symbol] = task
        let result = await task.value
        inflight[symbol] = nil
        switch result {
        case .image(let image):
            store(image, for: symbol)
        case .noLogo:
            noLogoUntil[symbol] = Date().addingTimeInterval(Self.noLogoTTL)
        case .failed:
            break
        }
        return result.image
    }

    private func isKnownMissing(_ symbol: String) -> Bool {
        guard let until = noLogoUntil[symbol] else { return false }
        if until > Date() { return true }
        noLogoUntil[symbol] = nil
        return false
    }

    /// Keep `image`, then evict the oldest entries until the store is back under `maxBytes`.
    /// The newest entry always survives, so a live view never loses the logo it just loaded —
    /// a REPLACED symbol moves to the back of `order` too, or it could be evicted as "oldest".
    private func store(_ image: UIImage, for symbol: String) {
        if let old = images.updateValue(image, forKey: symbol) {
            bytes -= Self.cost(of: old)
            order.removeAll { $0 == symbol }
        }
        order.append(symbol)
        bytes += Self.cost(of: image)
        while bytes > Self.maxBytes, order.count > 1 {
            let oldest = order.removeFirst()
            if let evicted = images.removeValue(forKey: oldest) { bytes -= Self.cost(of: evicted) }
        }
    }

    private static func cost(of image: UIImage) -> Int {
        guard let bitmap = image.cgImage else { return 1 }
        return bitmap.bytesPerRow * bitmap.height
    }

    /// The CDN's own "no logo for this symbol". Any other non-2xx (429, 5xx, a proxy's 403)
    /// is transient and must not be remembered.
    nonisolated static func isNoLogo(status: Int) -> Bool {
        status == 404 || status == 410
    }

    /// Network + display-size decode. Only ever called from the detached task in `load`.
    nonisolated private static func fetch(_ url: URL, session: URLSession) async -> CompanyLogoFetchResult {
        let name = url.lastPathComponent
        do {
            let (data, response) = try await session.data(from: url)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                if Self.isNoLogo(status: http.statusCode) {
                    Self.log.info("no CDN logo for \(name, privacy: .public)")
                    return .noLogo
                }
                Self.log.error("logo fetch failed: HTTP \(http.statusCode, privacy: .public) for \(name, privacy: .public)")
                return .failed
            }
            // Over https a 2xx body that is not an image is the CDN's own answer (a captive
            // portal cannot answer for this host), so it is "no logo", not a retry.
            guard let bitmap = ImageDownsampler.downsample(data, maxPixelSize: Self.maxPixelSize) else {
                Self.log.error("logo undecodable (\(data.count, privacy: .public) bytes) for \(name, privacy: .public)")
                return .noLogo
            }
            return .image(UIImage(cgImage: bitmap))
        } catch {
            Self.log.warning("logo fetch failed (\(String(describing: type(of: error)), privacy: .public)): \(error.localizedDescription, privacy: .public) for \(name, privacy: .public)")
            return .failed
        }
    }
}
