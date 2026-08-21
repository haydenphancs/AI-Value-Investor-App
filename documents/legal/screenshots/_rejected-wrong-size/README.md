# Rejected — wrong dimensions

`add-credits-706x1024-WRONG-SIZE.png` is **706 × 1024**. App Store Connect requires the 6.9"
iPhone set to be exactly **1320 × 2868** and rejects anything else at upload.

Two separate problems produced it:

1. **It is a macOS window capture** (⇧⌘4 on the Simulator window), which grabs the scaled window,
   not the device framebuffer. Use **Simulator → File → Save Screen (⌘S)**, or
   `xcrun simctl io <udid> screenshot out.png` — both write true device resolution.
2. **It was taken on iPhone 17 Pro**, which is a 6.3" device at 1206 × 2622. The 6.9" size only
   comes from a Pro Max. Use **iPhone 17 Pro Max**, UDID `3C473C18-1FB5-417F-836B-3D0EFDFB7026`.

Re-shoot: in Xcode set the run destination to **iPhone 17 Pro Max**, then **⌘R** (not ⌘B, and not
`simctl launch` — only a scheme-driven run loads `Caydex.storekit`, and without it every pack
renders "Price unavailable"). Navigate to Add Credits, then ⌘S.
