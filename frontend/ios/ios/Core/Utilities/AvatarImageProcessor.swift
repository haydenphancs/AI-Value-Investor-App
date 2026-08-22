//
//  AvatarImageProcessor.swift
//  ios
//
//  Turns whatever the photo library hands back into the one shape the avatar API accepts:
//  a square 512x512 JPEG.
//
//  WHY THE CLIENT DOES THIS, NOT THE SERVER
//  ----------------------------------------
//  `PhotosPickerItem.loadTransferable(type: Data.self)` returns the ORIGINAL file bytes — a
//  12-megapixel HEIC carrying GPS coordinates, capture time and device model in EXIF. Two
//  things follow, and neither is optional:
//
//   * Re-encoding through `UIImage` drops the SOURCE's metadata. That is what stops us
//     publishing the user's home coordinates inside their profile picture. There is no
//     "strip EXIF" call here because there does not need to be — the round trip loses it.
//
//     ⚠️ To be precise, because a hexdump will show the string "Exif" and look alarming:
//     the output is NOT metadata-free. `jpegData` writes its own minimal block — measured on
//     the real output: Orientation, XResolution, YResolution, ResolutionUnit, ColorSpace,
//     ExifImageWidth/Height. All encoder-generated, none derived from the source. What is
//     GONE is everything identifying: no GPS IFD at all, no Make, no Model, no DateTime.
//     Verified by running THIS file inside the simulator against a photo tagged with
//     Apple / iPhone 17 Pro / a timestamp, and reading the output back — all three absent.
//   * The server CANNOT be the primary defence: the pinned Pillow build has no HEIF decoder
//     and iPhones shoot HEIC by default, so a server-side re-encode would reject most real
//     photos. The backend validates (byte cap + JPEG magic) and NEVER decodes, precisely so an
//     attacker-supplied file cannot be turned into a decompression bomb on our machine.
//
//  Downscaling is also what keeps the upload inside the JSON body cap: base64 adds 33%, and a
//  full-size photo would blow the 1 MiB limit that guards every JSON route.
//

import UIKit

enum AvatarImageProcessor {

    /// Rendered edge, in pixels. The largest place an avatar is drawn is the ~80pt Account hero,
    /// so 512 covers a 3x screen with room to spare and still encodes to tens of KB.
    static let targetSide: CGFloat = 512

    /// JPEG quality. 0.8 is the knee of the curve for photographic content — visually
    /// indistinguishable from 1.0 at this size, roughly a third of the bytes.
    static let quality: CGFloat = 0.8

    /// Original photo bytes -> a square 512x512 JPEG, or nil when the data is not a decodable
    /// image (an iCloud photo that never downloaded, or a format this device cannot read).
    ///
    /// Returning nil rather than throwing keeps the call site honest: the caller must decide
    /// what to tell the user, and "nothing happened" is not one of the options.
    static func jpegForUpload(_ data: Data) -> Data? {
        guard let image = UIImage(data: data) else { return nil }
        let square = centreCropped(image)

        let format = UIGraphicsImageRendererFormat.default()
        // scale 1 because `targetSide` is already in PIXELS. Left at the device scale, a 3x
        // phone would render 1536x1536 and upload ~9x the bytes — silently, and only for users
        // on the newest hardware.
        format.scale = 1
        format.opaque = true   // no alpha in a JPEG anyway; opaque avoids a needless blend

        let renderer = UIGraphicsImageRenderer(
            size: CGSize(width: targetSide, height: targetSide), format: format
        )
        let resized = renderer.image { _ in
            square.draw(in: CGRect(x: 0, y: 0, width: targetSide, height: targetSide))
        }
        return resized.jpegData(compressionQuality: quality)
    }

    /// The largest centred square of the image, in its own oriented coordinate space.
    ///
    /// Uses the UIImage (not its cgImage) so `imageOrientation` is already applied — cropping
    /// the raw CGImage would take the wrong region from any photo shot in portrait, which is
    /// most of them.
    private static func centreCropped(_ image: UIImage) -> UIImage {
        let side = min(image.size.width, image.size.height)
        guard image.size.width != image.size.height else { return image }

        let origin = CGPoint(
            x: (image.size.width - side) / 2,
            y: (image.size.height - side) / 2
        )
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = image.scale
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(
            size: CGSize(width: side, height: side), format: format
        )
        return renderer.image { _ in
            image.draw(at: CGPoint(x: -origin.x, y: -origin.y))
        }
    }
}
