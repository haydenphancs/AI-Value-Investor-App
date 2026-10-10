"""
Local preview of the whole media pipeline — voice, captions, cards and the finished MP4 (writes
nothing anywhere but `marketing/out/`, which is gitignored). It runs the SAME functions the worker's
stages run: the Kokoro synthesis child and the bit-exact AAC encode (`voice.py`), the timing table,
and `render.produce_video` (cards.py → PNGs, captions.py → captions.ass, video.py → one ffmpeg
call and its ffprobe gate), so what you watch is what the `rendered` stage would upload.

    cd backend
    ./venv_marketing/bin/python -m marketing.preview                   # built-in demo script
    ./venv_marketing/bin/python -m marketing.preview path/to/script.json
    # script.json = the worker's WorkerScript shape:
    #   {"hook": "...", "video_script": ["...", ...], "cards": [{"title": "...", "body": "..."}, ...],
    #    "disclaimer_card": "..."}
    ./venv_marketing/bin/python -m marketing.preview path/to/script.json --logos-dir DIR --phonemes [--out DIR]
    # a Company Weekly TEMPLATE script (drop 2a). It is written by the WEB-side generator,
    # `./venv/bin/python scripts/marketing_news_preview.py --series <id> --fixture|--candidates`, whose
    # `--render` step runs exactly this command (`… --logos-dir <dir> --phonemes --out <dir>`).
    # Its logos are read from DIR (files named `<sha256[:32]>.<png|jpg>`, the bucket's content
    # address) instead of the bucket; a logo missing there draws its wordmark, as on a worker day.
    # --phonemes lists how Kokoro's G2P will speak every number, ticker and symbol (no voice model).
    # --out DIR writes there instead of marketing/out/<timestamp>.

Needs ffmpeg with libass (Homebrew's and the image's both have it) and the Kokoro weights
(downloaded to HF_HOME on first use locally; baked into the Railway image).

Text layout: the Linux image's Pillow wheel lays text out with raqm; Homebrew's raqm is found only
when DYLD_FALLBACK_LIBRARY_PATH names /opt/homebrew/lib AT PROCESS START (dyld reads it once), so
on macOS the preview re-executes itself once with it set — otherwise its wraps would differ from
production's by ~5% of a line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"
_FONTS = Path(__file__).resolve().parent / "assets" / "fonts"
_HOMEBREW_LIB = "/opt/homebrew/lib"
_REEXEC_FLAG = "MARKETING_PREVIEW_REEXEC"
#: The bucket host a preview script's logo URLs carry (written by `scripts/marketing_news_preview.py
#: --series <id>`, which mirrors this constant); the preview reads `logos/<hex32>.<ext>` from --logos-dir
#: instead of fetching it.
PREVIEW_BUCKET_HOST = "preview.local"
#: A narrated token worth a pronunciation check: a digit, a currency/percent/ampersand sign, or 2+
#: capitals in a row (tickers, "13F", "CEO").
_SAY_CHECK_RE = re.compile(r"[0-9$%&]|[A-Z]{2,}")

DEMO = {
    "hook": "Meet your moody business partner.",
    "video_script": [
        "Every day, he offers a price for your share of the business.",
        "Some days he is gloomy, and some days he is giddy.",
        "His price follows his mood, not the business itself.",
        "You are never forced to accept his offer.",
        "The business keeps doing its work, whatever he says today.",
        "Knowing that is what keeps you calm when prices swing.",
    ],
    "cards": [
        {"title": "A partner with moods", "body": "Every day he names a price for your share."},
        {"title": "Mood, not value", "body": "His price follows his mood, not the business itself."},
        {"title": "Your choice", "body": "You never have to accept his offer on a gloomy day."},
    ],
    "carousel_slides": [],
    "disclaimer_card": ("Educational, impersonal information — not investment advice. Investing "
                        "involves risk. Script and narration generated with AI. Caydex · Preview"),
    "outlets": ["tiktok"],
    # Drop 1: the 4:5 post image (rendered to post_image.jpg when both keys are present).
    "image_post": {"title": "Mr. Market's mood is not the business",
                   "paragraphs": ["Every day, a moody partner offers a price for your share of the business.",
                                  "Some days he is gloomy, some days giddy. His price follows his mood.",
                                  "You never have to accept his offer. The business keeps doing its work."]},
    "image_footer": "Educational only · not investment advice · Written with AI assistance · Preview · Caydex",
}


def _ensure_raqm_env(argv: list) -> None:
    """On macOS, re-exec once with Homebrew's lib dir on the dyld fallback path (see the module
    docstring). Never loops: the flag marks the second process."""
    if sys.platform != "darwin" or os.environ.get(_REEXEC_FLAG) or not os.path.isdir(_HOMEBREW_LIB):
        return
    current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if _HOMEBREW_LIB in current.split(":"):
        return
    env = dict(os.environ, **{_REEXEC_FLAG: "1",
                               "DYLD_FALLBACK_LIBRARY_PATH": ":".join(p for p in (current, _HOMEBREW_LIB) if p)})
    os.execve(sys.executable, [sys.executable, "-m", "marketing.preview", *argv], env)


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration,size:stream=codec_name,width,height,pix_fmt,r_frame_rate,"
                          "avg_frame_rate,sample_rate,channels",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def preview_image(script: dict, out: Path, font: str):
    """The post image's entry in report.json; writes `post_image.jpg` into `out` when it renders.

    Rendered only when the script carries BOTH an `image_post` and a non-empty `image_footer` (the
    DEMO's shape, and a day the server froze an "image" format for). A real switches-off script
    carries the writer's `image_post` with `image_footer` None — no image that day — and gets a
    `skipped` note, never the ValueError `cards.image_for_script` raises for it. `render.image_needed`
    is not the gate: the DEMO has no `post_formats`, so it would never draw the DEMO's image.

    None when there is no `image_post`. Every render failure (a malformed `image_post`, a glyph Inter
    lacks, text that cannot fit, a JPEG over the byte cap, an unreadable font) is RECORDED as
    `{"error": ...}` and never raised: this runs after the narration and the video render, and the
    report must still be written."""
    from marketing import cards

    if not isinstance(script, dict) or script.get("image_post") is None:
        return None
    footer = script.get("image_footer")
    if not isinstance(footer, str) or not footer.strip():
        return {"skipped": "the script has an image_post but no image_footer (no image format that day)"}
    try:
        spec = cards.image_for_script(script)
        if spec is None:
            return None
        cards.check_image_glyphs(spec, font)
        image = cards.render_image(spec, font_path=font, layout_engine=cards.resolve_layout_engine(None))
    except (ValueError, cards.MissingGlyphs, cards.CardOverflow, cards.ImageTooLarge, cards.CardAssetError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    (out / "post_image.jpg").write_bytes(image.data)
    return {"bytes": len(image.data), "quality": image.quality, "step": image.layout.step,
            "onscreen_text": cards.image_onscreen_strings(spec)}


def is_template(script: dict) -> bool:
    """A Company Weekly template script (drop 2a): one card per narration line, opened by the
    company's opening card — what `render.stage_render` keys on (`video_layout`)."""
    from marketing import cards

    return isinstance(script, dict) and script.get("video_layout") == cards.VIDEO_LAYOUT_PER_LINE


def local_download(logos_dir: Path):
    """`render.download`'s signature over a local directory: the URL's last path segment
    (`<hex32>.<ext>`, the bucket's content address) is read from `logos_dir`. A missing file raises
    like a failed download — `logos.resolve_logos` then draws the wordmark, as on a worker day."""

    def download(url: str, *, max_bytes: int, timeout: float, read_timeout=None, max_seconds=None) -> bytes:
        name = str(url).rsplit("/", 1)[-1]
        if "/" in name or name.startswith("."):
            raise ValueError(f"not a logo object name: {name!r}")
        data = (Path(logos_dir) / name).read_bytes()
        if len(data) > max_bytes:
            raise ValueError(f"{name}: {len(data)} bytes > {max_bytes}")
        return data

    return download


def template_logos(script: dict, logos_dir: "Path | None", out: Path) -> dict:
    """{key: cards.LogoArt} for a template script — the worker's own resolve + table, the bucket
    replaced by `logos_dir` (None: every logo is a wordmark)."""
    from marketing import cards, logos

    resolved = logos.resolve_logos(script, bucket_origin=PREVIEW_BUCKET_HOST if logos_dir else None,
                                   download=local_download(logos_dir) if logos_dir else (lambda *a, **k: b""),
                                   dest_dir=out)
    return cards.logo_table(script, resolved)


def preview_template_image(script: dict, table: dict, out: Path, font: str):
    """The template post image's entry in report.json (`post_image.jpg`), never raised — like
    `preview_image`, for a template's closed `image_spec` drawn by `news_layouts`."""
    from marketing import cards, news_layouts

    if not isinstance(script, dict) or script.get("image_spec") is None:
        return None
    try:
        spec = news_layouts.template_image_for_script(script, table)
        news_layouts.check_glyphs(spec, font)
        image = news_layouts.render_template_image(spec, font_path=font,
                                                   layout_engine=cards.resolve_layout_engine(None))
    except (ValueError, cards.MissingGlyphs, cards.CardOverflow, cards.ImageTooLarge, cards.CardAssetError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    (out / "post_image.jpg").write_bytes(image.data)
    return {"bytes": len(image.data), "quality": image.quality, "layout": spec.layout,
            "onscreen_text": list(spec.strings),
            "logos": {k: ("logo" if not art.wordmark else "wordmark") for k, art in table.items()}}


def phoneme_report(lines: list) -> list:
    """[{"line": i, "text": token, "phonemes": ...}] for every narrated token worth a pronunciation
    check (`_SAY_CHECK_RE`), from Kokoro's own G2P with NO voice model loaded (`model=False`) — the
    same tokens the synthesis child speaks. A G2P failure is one {"error": ...} entry, never a raise."""
    try:
        from kokoro import KPipeline

        pipe = KPipeline(lang_code="a", model=False)
    except Exception as e:  # noqa: BLE001 — the check is advisory; the video is still rendered
        return [{"error": f"{type(e).__name__}: {e}"}]
    report = []
    for i, line in enumerate(lines):
        try:
            for result in pipe(line):
                for tok in (getattr(result, "tokens", None) or []):
                    text = str(getattr(tok, "text", "") or "")
                    if _SAY_CHECK_RE.search(text):
                        report.append({"line": i, "text": text, "phonemes": getattr(tok, "phonemes", None)})
        except Exception as e:  # noqa: BLE001
            report.append({"line": i, "error": f"{type(e).__name__}: {e}"})
    return report


def main(argv: list) -> int:
    _ensure_raqm_env(argv)
    from marketing import cards, render, timings, voice

    if not shutil.which("ffmpeg"):
        sys.stderr.write("ffmpeg is not on PATH\n")
        return 1
    ap = argparse.ArgumentParser(prog="marketing.preview")
    ap.add_argument("script", nargs="?", type=Path, help="a WorkerScript JSON file (default: the built-in demo)")
    ap.add_argument("--logos-dir", type=Path, help="a template script's logos: <sha256[:32]>.<png|jpg> files")
    ap.add_argument("--phonemes", action="store_true", help="report how Kokoro speaks numbers, tickers, symbols")
    ap.add_argument("--out", type=Path, help="output directory (default: marketing/out/<timestamp>)")
    args = ap.parse_args(argv)
    script = json.loads(args.script.read_text(encoding="utf-8")) if args.script else DEMO
    template = is_template(script)
    lines = timings.narrated_lines(script)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or (_OUT / stamp)
    out.mkdir(parents=True, exist_ok=True)
    table = template_logos(script, args.logos_dir, out) if template else {}
    t0 = time.monotonic()
    narration = voice.synthesize_fitting(lines, voice=voice.DEFAULT_VOICE, speed=1.0, workdir=out,
                                         heartbeat=None, budget=voice.video_budget_seconds(),
                                         runner=voice.run_child)
    data, duration = voice.encode_m4a(narration.wav_path, out / "narration.m4a")
    words = voice.clamp_to_duration(narration.words, duration)
    (out / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    specs = cards.cards_for_script(script, logos=table) if template else cards.cards_for_script(script)
    font = str(_FONTS / "Inter-Bold.ttf")
    missing = []
    try:
        cards.check_glyphs(specs, font, extra=[w["w"] for w in words])
    except cards.MissingGlyphs as e:
        missing = [str(e)]
    t_render = time.monotonic()
    video, video_seconds, shown = render.produce_video(
        workdir=out, specs=specs, words=words, narration_seconds=duration, audio_file="narration.m4a",
        fonts_dir=str(_FONTS), logo_path=render.brand_logo_path(str(_FONTS)),
        threads=render.render_threads(), heartbeat=None, run_id="preview",
        max_seconds=render.max_video_seconds(), layout_engine=cards.resolve_layout_engine(None),
        **({"hook_card": True} if template else {}),
    )
    (out / "preview.mp4").write_bytes(video)
    onscreen = []
    for i in shown:
        for text in cards.onscreen_strings(specs[i]):
            if text not in onscreen:
                onscreen.append(text)
    image_report = preview_template_image(script, table, out, font) if template else preview_image(script, out, font)
    report = {
        "out": str(out), "lines": len(lines), "exact_lines": narration.exact_lines,
        "words": len(words), "narration_s": round(duration, 2), "video_s": round(video_seconds, 2),
        "speed": narration.speed, "m4a_bytes": len(data), "mp4_bytes": len(video),
        "cards_shown": shown, "onscreen_text": onscreen, "missing_glyphs": missing,
        "raqm": cards.raqm_available(), "render_wall_s": round(time.monotonic() - t_render, 1),
        "wall_s": round(time.monotonic() - t0, 1), "video": _ffprobe(out / "preview.mp4"),
        "post_image": image_report, "template": template,
        "phonemes": phoneme_report(lines) if args.phonemes else None,
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
