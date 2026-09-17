# Marketing worker fonts

Vendored TTFs for the caption burn (libass `fontsdir`) and the Pillow card renderer.

Phase 4 adds the **Inter** family here (SIL Open Font License 1.1 — embedding in rendered
video is permitted; the OFL text must accompany the files). iOS uses SF Pro, which Apple's
licence does not allow to be embedded in rendered media, so Inter is the video face.

Until the files land, libass silently falls back to DejaVu (`fonts-dejavu-core` in the
image) — which is why `marketing/main.py`'s preflight manifest lists the fonts it
actually found: a missing font must be visible in `marketing_runs.metadata`, not discovered
in a published clip.
