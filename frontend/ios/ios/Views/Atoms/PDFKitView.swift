//
//  PDFKitView.swift
//  ios
//
//  Atom: SwiftUI wrapper around PDFKit's PDFView for displaying a local PDF.
//  Generic — knows nothing about the app's domain.
//

import SwiftUI
import PDFKit

struct PDFKitView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        // The app's page colour, not Apple's `.systemGroupedBackground` — which is pure
        // #000000 in dark (harsher than the app's #171B26) and #F2F2F7 in light (not the
        // palette's #F4F5F8). This was the only `UIColor.system*` in the view layer, and
        // no guard can see it:
        // `test_ios_theme_parity.py::test_no_bare_swiftui_colours_as_ink_or_opaque_fill`
        // matches SwiftUI modifiers, not UIKit `UIColor.system*` property assignment.
        //
        // The PDF ITSELF stays light-on-white in both appearances, deliberately:
        // `backend/app/templates/pdf/report.html` has no `prefers-color-scheme`, because
        // the artifact is meant to be printed and shared. Stating that here so it stops
        // being re-filed as a dark-mode bug.
        view.backgroundColor = UIColor(AppColors.background)
        view.document = PDFDocument(url: url)
        return view
    }

    func updateUIView(_ uiView: PDFView, context: Context) {
        if uiView.document?.documentURL != url {
            uiView.document = PDFDocument(url: url)
        }
    }
}
