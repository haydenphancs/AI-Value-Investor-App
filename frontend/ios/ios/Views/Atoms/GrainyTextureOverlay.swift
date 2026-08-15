//
//  GrainyTextureOverlay.swift
//  ios
//
//  Atom: the procedural film grain that sits over a Money Moves gradient.
//
//  Moved here from MoneyMoveArticleHeroHeader when cover artwork landed: it is now used by
//  MoneyMoveCoverImage (Atoms) as well, and an atom must not reach up into an organism for
//  a dependency.
//
//  ⚠️ THE SPECK FIELD IS SEEDED. The original drew ~1,540 specks by calling `CGFloat.random`
//  and `Double.random` INSIDE the Canvas closure, which meant a different picture on every
//  evaluation — the grain visibly shimmered while scrolling, because a Canvas redraws
//  whenever its host's body does. It also cost one `context.fill` per speck.
//
//  Two changes, and they are coupled:
//   1. The field is generated ONCE from a fixed seed, in UNIT space, so it is identical on
//      every draw and independent of the frame it lands in (only the speck COUNT tracks area).
//   2. Specks are bucketed into four alpha bands and emitted as four compound paths, so a
//      draw is 4 fills instead of ~1,540. This matters because the See-All screen shows
//      ~11 gradient-fallback covers at once across its three carousels.
//
//  (1) is what makes `rendersAsynchronously` safe here: Apple's caveat about async Canvas
//  flickering applies to content that changes between draws, which is exactly what (1)
//  removed. Do not keep one without the other.
//

import SwiftUI

struct GrainyTextureOverlay: View {
    /// One speck per this many square points. 50 reproduces the original density exactly.
    var density: CGFloat = 50

    /// Ceiling on the field, so an unusually large frame cannot turn one draw into a stall.
    /// 2,400 covers a full-width 440x248 hero at the default density with room to spare.
    private static let maxSpecks = 2_400

    /// Midpoints of the original `0.02...0.08` range. Four bands is enough that the banding
    /// is invisible at 1px and 2-8% alpha, and it collapses ~1,540 fills into 4.
    private static let bucketAlpha: [Double] = [0.0275, 0.0425, 0.0575, 0.0725]

    /// The speck field in UNIT space (0..<1 on both axes), generated once from a fixed seed.
    private static let field: [(point: CGPoint, bucket: Int)] = {
        var rng = SplitMix64(seed: 0x9E37_79B9_7F4A_7C15)
        return (0..<maxSpecks).map { _ in
            (
                CGPoint(
                    x: CGFloat.random(in: 0..<1, using: &rng),
                    y: CGFloat.random(in: 0..<1, using: &rng)
                ),
                Int.random(in: 0..<bucketAlpha.count, using: &rng)
            )
        }
    }()

    var body: some View {
        // No `.drawingGroup()` here: Canvas already rasterises to a single layer, so it would
        // only add a redundant Metal offscreen pass — and anything inside a drawingGroup then
        // needs `.id(colorScheme)` to avoid keeping stale colours across an appearance flip.
        Canvas(opaque: false, rendersAsynchronously: true) { context, size in
            let wanted = min(Self.maxSpecks, Int(size.width * size.height / density))
            guard wanted > 0 else { return }

            var paths = [Path](repeating: Path(), count: Self.bucketAlpha.count)
            for speck in Self.field.prefix(wanted) {
                paths[speck.bucket].addEllipse(
                    in: CGRect(
                        x: speck.point.x * size.width,
                        y: speck.point.y * size.height,
                        width: 1,
                        height: 1
                    )
                )
            }
            for (index, path) in paths.enumerated() where !path.isEmpty {
                context.fill(path, with: .color(.white.opacity(Self.bucketAlpha[index])))
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Seeded PRNG, so the grain is the SAME picture on every evaluation.
///
/// ⚠️ If per-article grain variety is ever wanted, add an explicit `seed: UInt64` parameter and
/// derive it with a written-out hash (FNV-1a or similar) — NEVER from `String.hashValue`.
/// Swift seeds its string hasher randomly per process, so a slug-derived `hashValue` gives a
/// different field on every launch: the exact bug this file exists to fix, in a form the
/// "no unseeded .random" guard would not catch.
private struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

#Preview {
    ZStack {
        LinearGradient(colors: [AppColors.cardBackground, AppColors.background],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
        GrainyTextureOverlay()
    }
    .frame(height: 200)
    .padding()
}
