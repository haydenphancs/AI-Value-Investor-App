//
//  ImageDownsampler.swift
//  ios
//
//  Decodes image data straight to a DISPLAY-sized bitmap with ImageIO, without ever holding
//  the full-resolution bitmap in memory.
//
//  A 1290×1080 JPEG decodes to ~5.6 MB whatever size it is drawn at; the same image
//  thumbnailed at 720 px is ~1.7 MB. Used by `DownsampledImageLoader`.
//
//  CoreGraphics + ImageIO only (no UIKit/SwiftUI import), so
//  `backend/tests/test_ios_themes_carousel_guards.py` can execute it with `xcrun swift -`.
//

import CoreGraphics
import Foundation
import ImageIO

nonisolated enum ImageDownsampler {
    /// A bitmap whose longer side is at most `maxPixelSize` (never upscaled), with the
    /// EXIF orientation applied. nil for empty, undecodable or non-image data, or a
    /// non-finite / sub-pixel size.
    static func downsample(_ data: Data, maxPixelSize: CGFloat) -> CGImage? {
        guard !data.isEmpty, maxPixelSize.isFinite, maxPixelSize >= 1 else { return nil }
        // Do not cache the full-size decode: the whole point is to never create it.
        let sourceOptions = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithData(data as CFData, sourceOptions),
              CGImageSourceGetCount(source) > 0 else { return nil }
        let options = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            // Decode NOW, on the caller's (background) thread, not lazily at first draw
            // on the main thread.
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: Int(maxPixelSize.rounded()),
        ] as CFDictionary
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options)
    }
}
