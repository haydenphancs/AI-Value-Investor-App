//
//  ShareSheet.swift
//  ios
//
//  Atom: Share sheet component using UIActivityViewController
//  A UIKit bridge for presenting the native iOS share sheet
//

import SwiftUI

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    var excludedActivityTypes: [UIActivity.ActivityType]? = nil

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: items, applicationActivities: nil)
        controller.excludedActivityTypes = excludedActivityTypes
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - Preview
#Preview {
    Text("Share Sheet")
        .sheet(isPresented: .constant(true)) {
            ShareSheet(items: ["Sample content to share"])
        }
}
