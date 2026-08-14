//
//  LessonImageSlot.swift
//  ios
//
//  Atom: renders a lesson's hero illustration — a remote Supabase Storage URL, or a
//  bundled asset offline — on an always-white plate.
//
//  Why the plate. The artwork is flat vector drawn on a pure #FFFFFF background, so in
//  DARK mode the image is a white rectangle sitting on a #171B26 page. Painting the same
//  white behind the whole slot and clipping both to one rounded shape turns that from a
//  glaring slab into a deliberate card: the `.fit` letterbox gutters match the artwork's
//  own background instead of showing the dark page around it. One asset then serves both
//  appearances, which is why there is no light/dark variant of any of the 27 images.
//

import SwiftUI

struct LessonImageSlot: View {
    let imageName: String?
    var height: CGFloat = 200
    var horizontalPadding: CGFloat = AppSpacing.xxl

    /// Matches the placeholder's radius, so nothing shifts when art loads in.
    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: AppCornerRadius.large)
    }

    var body: some View {
        Group {
            if let name = imageName, name.hasPrefix("http"), let url = URL(string: name) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        plated { image.resizable().aspectRatio(contentMode: .fit) }
                    } else if phase.error != nil {
                        placeholder.frame(maxWidth: .infinity).frame(height: height)
                    } else {
                        // Loading is NOT a failure: showing "Image coming soon" here made
                        // every title card flash that text before its art arrived. A silent
                        // skeleton instead — and a neutral one, because a white flash in
                        // dark mode is worse than a grey one.
                        shape.cardFill().frame(maxWidth: .infinity).frame(height: height)
                    }
                }
            } else if let name = imageName, !name.isEmpty, UIImage(named: name) != nil {
                plated { Image(name).resizable().aspectRatio(contentMode: .fit) }
            } else {
                placeholder.frame(maxWidth: .infinity).frame(height: height)
            }
        }
        .padding(.horizontal, horizontalPadding)
    }

    /// Order matters: both frames must be applied BEFORE the background, so the plate
    /// fills the whole slot rect rather than only the fitted image rect.
    private func plated<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .frame(maxWidth: .infinity)
            .frame(height: height)
            .background(AppColors.mediaSurface)
            .clipShape(shape)
            // Required, not decoration: white-on-#F4F5F8 is 1.09:1, so in LIGHT mode the
            // border is the only thing separating the plate from the page. Its dark arm is
            // alpha 0 by design, so it costs nothing in dark. Same reasoning as
            // BookCoverImage, which floats artwork on the same token.
            .overlay(shape.strokeBorder(AppColors.cardEdge, lineWidth: 0.5))
            // Illustrative art with no alt text in the content JSON; without this VoiceOver
            // announces an unlabeled image between the headline and the body copy.
            .accessibilityHidden(true)
    }

    private var placeholder: some View {
        shape
            .cardFill()
            .overlay(
                VStack(spacing: AppSpacing.sm) {
                    Image(systemName: "photo")
                        .font(.system(size: 28, weight: .light))
                        .foregroundColor(AppColors.textMuted)
                    Text("Image coming soon")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                }
            )
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.xl) {
            LessonImageSlot(imageName: nil)                 // placeholder
            LessonImageSlot(imageName: "journey_missing")    // placeholder (asset absent)
        }
    }
}
