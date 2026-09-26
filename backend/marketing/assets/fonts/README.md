# Marketing worker fonts

Vendored TTFs for the caption burn (libass `fontsdir`) and the Pillow measuring/card renderer.

- `Inter-Bold.ttf` — **Inter 4.1**, the STATIC Bold cut (`extras/ttf/Inter-Bold.ttf` of the
  official release `https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip`,
  archive sha256 `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`, file sha256
  `288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f`). Family name `Inter`,
  style `Bold`. A static cut on purpose: libass fakes bold on a variable font.
- `OFL.txt` — the SIL Open Font License 1.1 that must accompany the font (embedding in rendered
  video is permitted). iOS uses SF Pro, which Apple's licence does not allow to be embedded in
  rendered media, so Inter is the video face.

If a glyph is missing from the font, libass silently falls back to DejaVu (`fonts-dejavu-core`
in the image): `marketing/captions.py` checks glyph coverage before a caption file is written,
and the preflight manifest lists the fonts the image actually has.
