//
//  GrainyTextureOverlay.swift
//  ios
//
//  Atom: the procedural film grain that sits over a Money Moves gradient.
//
//  Moved here from MoneyMoveArticleHeroHeader when cover artwork landed: it is now used by
//  MoneyMoveCoverImage (Atoms) as well, and an atom must not reach up into an organism for
//  a dependency. Behaviour is unchanged.
//

import SwiftUI

struct GrainyTextureOverlay: View {
    var body: some View {
        Canvas { context, size in
            for _ in 0..<Int(size.width * size.height / 50) {
                let x = CGFloat.random(in: 0..<size.width)
                let y = CGFloat.random(in: 0..<size.height)
                let opacity = Double.random(in: 0.02...0.08)

                context.fill(
                    Path(ellipseIn: CGRect(x: x, y: y, width: 1, height: 1)),
                    with: .color(.white.opacity(opacity))
                )
            }
        }
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
