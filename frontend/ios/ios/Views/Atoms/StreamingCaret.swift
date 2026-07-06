//
//  StreamingCaret.swift
//  ios
//
//  Atom: a blinking caret shown at the end of an assistant message while its
//  tokens are still streaming in.
//

import SwiftUI

struct StreamingCaret: View {
    @State private var dim = false

    var body: some View {
        RoundedRectangle(cornerRadius: 1.5)
            .fill(AppColors.primaryBlue)
            .frame(width: 8, height: 15)
            .opacity(dim ? 0.15 : 1)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                    dim = true
                }
            }
    }
}

#Preview {
    StreamingCaret()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
