//
//  MailComposeView.swift
//  ios
//
//  SwiftUI bridge to `MFMailComposeViewController` — the system mail composer, presented
//  INSIDE the app.
//
//  WHY NOT `mailto:`
//  -----------------
//  `mailto:` hands the user to the Mail app and cannot carry a body reliably across every
//  mail client, cannot carry an attachment at all, and — worst — gives no way to know whether
//  the device can send mail until after the tap fails. `MFMailComposeViewController` answers
//  that up front via `canSendMail()`, so a screen can offer the copy-the-address path instead
//  of a Send button that cannot work. That is the whole reason this exists.
//
//  It carries attachments, so the screenshot the user picks on the feedback screen arrives
//  already attached rather than depending on them remembering to add it. Choosing a photo is
//  declared in the App Privacy filing — `PrivacyInfo.xcprivacy` and
//  `documents/legal/app-privacy-answers.md` both list Photos or Videos, and
//  `test_ios_feedback_flow.py` fails the build if the code and the filing ever disagree.
//

import SwiftUI
import MessageUI
import UIKit

/// Presents the system mail composer. Check `MailComposeView.canSendMail` before offering it —
/// presenting when that is false shows an unusable sheet.
struct MailComposeView: UIViewControllerRepresentable {

    /// Whether this device has a mail account configured.
    ///
    /// **False on the iOS Simulator, always** — it ships no Mail app. That is not an edge case
    /// to shrug at: it is the environment every simulator test runs in, so the degraded path is
    /// the one that gets exercised locally and it has to be good.
    static var canSendMail: Bool { MFMailComposeViewController.canSendMail() }

    /// One file to attach. `data` is the bytes as chosen; `mimeType` and `fileName` are what
    /// the recipient's client uses to render it, so getting the mime wrong shows a download
    /// stub instead of an inline image.
    struct Attachment {
        let data: Data
        let mimeType: String
        let fileName: String

        /// Build one from picked image bytes, sniffing the format rather than assuming.
        ///
        /// Screenshots are PNG; a photo from the library is usually HEIC or JPEG. Labelling a
        /// JPEG as `image/png` is the kind of thing that renders fine in Mail and as a broken
        /// attachment everywhere else, so the type is read from the magic bytes. HEIC is
        /// re-encoded to JPEG because plenty of desktop mail clients still cannot display it —
        /// and a bug report nobody can open is a bug report we did not receive.
        static func image(_ data: Data) -> Attachment {
            if data.starts(with: [0x89, 0x50, 0x4E, 0x47]) {
                return Attachment(data: data, mimeType: "image/png", fileName: "screenshot.png")
            }
            if data.starts(with: [0xFF, 0xD8, 0xFF]) {
                return Attachment(data: data, mimeType: "image/jpeg", fileName: "screenshot.jpg")
            }
            // HEIC or anything else: normalise through UIImage so the recipient can open it.
            if let jpeg = UIImage(data: data)?.jpegData(compressionQuality: 0.85) {
                return Attachment(data: jpeg, mimeType: "image/jpeg", fileName: "screenshot.jpg")
            }
            return Attachment(data: data, mimeType: "application/octet-stream",
                              fileName: "screenshot")
        }
    }

    let recipient: String
    let subject: String
    let body: String
    var attachments: [Attachment] = []

    /// Called with `true` when the user actually sent it, so the screen can thank them.
    /// Cancel, save-as-draft and failure all report `false` — we only celebrate a real send.
    var onFinish: (Bool) -> Void

    func makeUIViewController(context: Context) -> MFMailComposeViewController {
        let controller = MFMailComposeViewController()
        controller.mailComposeDelegate = context.coordinator
        controller.setToRecipients([recipient])
        controller.setSubject(subject)
        // Plain text, not HTML: the body is the user's own words plus a diagnostics block, and
        // it must stay editable and readable in whatever client they use.
        controller.setMessageBody(body, isHTML: false)
        for attachment in attachments {
            controller.addAttachmentData(
                attachment.data,
                mimeType: attachment.mimeType,
                fileName: attachment.fileName
            )
        }
        return controller
    }

    func updateUIViewController(_ controller: MFMailComposeViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onFinish: onFinish) }

    final class Coordinator: NSObject, MFMailComposeViewControllerDelegate {
        private let onFinish: (Bool) -> Void

        init(onFinish: @escaping (Bool) -> Void) {
            self.onFinish = onFinish
        }

        func mailComposeController(
            _ controller: MFMailComposeViewController,
            didFinishWith result: MFMailComposeResult,
            error: Error?
        ) {
            // Dismiss FIRST. The delegate is responsible for dismissing the composer — the
            // controller does not dismiss itself, and forgetting leaves the sheet stuck on
            // screen with no way out.
            controller.dismiss(animated: true) { [onFinish] in
                #if DEBUG
                if let error {
                    let ns = error as NSError
                    print("🔴 [Mail] compose failed: \(ns.domain) code=\(ns.code) — \(ns.localizedDescription)")
                }
                #endif
                onFinish(result == .sent)
            }
        }
    }
}
