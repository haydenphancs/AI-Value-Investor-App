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
    # Batch 2: add the read-back's "video_features" (a lesson: ["hook_card", "push_in"]; a template:
    # ["data_card", "push_in"] and the "data_card_slot" — the index into "cards" of the card the data card
    # replaces; none, no data card) and "end_card_cta" ("Search Caydex on the App Store" |
    # "caydexinvest.com") to preview a day the server froze them for — drawn exactly as the rendered
    # stage draws them.
    ./venv_marketing/bin/python -m marketing.preview path/to/script.json --logos-dir DIR --phonemes [--out DIR]
    # a Company Weekly TEMPLATE script (drop 2a). It is written by the WEB-side generator,
    # `./venv/bin/python scripts/marketing_news_preview.py --series <id> --fixture|--candidates`, whose
    # `--render` step runs exactly this command (`… --logos-dir <dir> --phonemes --out <dir>`).
    # Its logos are read from DIR (files named `<sha256[:32]>.<png|jpg>`, the bucket's content
    # address) instead of the bucket; a logo missing there draws its wordmark, as on a worker day.
    # --phonemes lists how Kokoro's G2P will speak every number, ticker and symbol (no voice model).
    # --out DIR writes there instead of marketing/out/<timestamp>.
    ./venv_marketing/bin/python -m marketing.preview path/to/script.json --art-file ART.jpg [--logos-dir DIR]
    # workstream C: draw a LOCAL JPEG as the run's per-post art (16:9, 1024-2752 px wide — the web's own
    # bounds, checked with the worker's `art.decode_problem`): on the `feature` picture's band and on the
    # opening card (one company), exactly as the rendered stage draws a verified read-back art. Spends
    # nothing and fetches nothing; report.json says where it was drawn and, when not, why. The web-side
    # generator makes such a file and passes it here itself: `scripts/marketing_news_preview.py --art --render`
    # (that flag SPENDS MONEY — the brief, the drawing and the picture check).

Needs ffmpeg with libass (Homebrew's and the image's both have it) and the Kokoro weights
(downloaded to HF_HOME on first use locally; baked into the Railway image). A push-in render that fails
here FAILS — the rendered stage would render once more without the push-in (render.py), but the preview
is how a host's ffmpeg is checked (`railway ssh` → this command on a push_in script) before the switch.

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
    # news-v23: the lesson picture's code-owned short footer (cards.LESSON_FOOTER — "Educational only" and "Caydex"
    # left the image), and the read-back's `look` a news-v23 web sends (the brand row's word and the kicker pill)
    "image_footer": "Not investment advice",
    "look": "news-v23",
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


def local_art(path: "Path | None"):
    """(cards.ArtImage, None) for a local JPEG passed with --art-file — checked as the worker checks a downloaded
    art (`art.entry_problem` on its size and bytes, `art.decode_problem` on its header and pixels) — or (None,
    problem) when it is not drawable art; (None, None) without a file. Never raised: the preview records it."""
    import hashlib
    import io

    from marketing import art as art_mod
    from marketing import cards

    if path is None:
        return None, None
    try:
        from PIL import Image

        data = Path(path).read_bytes()
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    except Exception as e:  # noqa: BLE001 — recorded in the report, never raised
        return None, f"{type(e).__name__}: {e}"
    sha = hashlib.sha256(data).hexdigest()
    problem = art_mod.entry_problem({"url": str(path), "sha256": sha, "bytes": len(data), "width": width,
                                     "height": height}) or art_mod.decode_problem(data, width=width, height=height)
    if problem is not None:
        return None, problem
    return cards.ArtImage(sha256=sha, path=str(Path(path).resolve()), width=width, height=height), None


def preview_template_image(script: dict, table: dict, out: Path, font: str, art=None):
    """The template post image's entry in report.json (`post_image.jpg`), never raised — like
    `preview_image`, for a template's closed `image_spec` drawn by `news_layouts`. `art` (workstream C, a
    cards.ArtImage) is offered only to a `feature` picture, as the rendered stage offers it; the entry says
    whether it was drawn (`art_drawn`) and, when not, why (`art_skipped`)."""
    from marketing import cards, news_layouts, render

    if not isinstance(script, dict) or script.get("image_spec") is None:
        return None
    try:
        spec = news_layouts.template_image_for_script(
            script, table, art=art if art is not None and render.picture_draws_art(script) else None)
        news_layouts.check_glyphs(spec, font)
        image = news_layouts.render_template_image(spec, font_path=font,
                                                   layout_engine=cards.resolve_layout_engine(None))
    except (ValueError, cards.MissingGlyphs, cards.CardOverflow, cards.ImageTooLarge, cards.CardAssetError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    (out / "post_image.jpg").write_bytes(image.data)
    return {"bytes": len(image.data), "quality": image.quality, "layout": spec.layout,
            "onscreen_text": news_layouts.drawn_strings(spec, image.layout),
            "logos": {k: ("logo" if not logo.wordmark else "wordmark") for k, logo in table.items()},
            "art_drawn": image.layout.art is not None, "art_skipped": image.art_skipped}


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
    ap.add_argument("--art-file", type=Path,
                    help="a local 16:9 JPEG drawn as the per-post art (a template script: the feature picture's "
                         "band and the opening card)")
    args = ap.parse_args(argv)
    script = json.loads(args.script.read_text(encoding="utf-8")) if args.script else DEMO
    template = is_template(script)
    lines = timings.narrated_lines(script)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or (_OUT / stamp)
    out.mkdir(parents=True, exist_ok=True)
    table = template_logos(script, args.logos_dir, out) if template else {}
    art, art_problem = local_art(args.art_file)
    art_report: dict = {}
    if art is not None and not template:
        art_problem, art = "a lesson draws no art (only a template script does) — ignored", None
    t0 = time.monotonic()
    narration = voice.synthesize_fitting(lines, voice=voice.DEFAULT_VOICE, speed=1.0, workdir=out,
                                         heartbeat=None, budget=voice.video_budget_seconds(),
                                         runner=voice.run_child)
    data, duration = voice.encode_m4a(narration.wav_path, out / "narration.m4a")
    words = voice.clamp_to_duration(narration.words, duration)
    (out / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    font = str(_FONTS / "Inter-Bold.ttf")
    engine = cards.resolve_layout_engine(None)
    # Batch 2: the script's frozen video features, read the worker's way (`cards.video_features`); the
    # feature cards (a lesson's hook card, a template's data card) are checked with this font and layout
    # engine, as the rendered stage checks them — one that cannot be drawn is left out (a WARNING).
    features = cards.video_features(script)
    specs = cards.cards_for_script(script, logos=table if template else None, font_path=font,
                                   layout_engine=engine,
                                   **({"art": art, "report": art_report} if art is not None else {}))
    push_in = cards.VIDEO_FEATURE_PUSH_IN in features
    missing = []
    try:
        cards.check_glyphs(specs, font, extra=[w["w"] for w in words])
    except cards.MissingGlyphs as e:
        missing = [str(e)]
    t_render = time.monotonic()
    # The opening follows the cards (`render.produce_video` derives hook_card from them: a template's
    # opening card, a lesson's hook card); push-in only when frozen — the rendered stage's own call.
    video, video_seconds, shown = render.produce_video(
        workdir=out, specs=specs, words=words, narration_seconds=duration, audio_file="narration.m4a",
        fonts_dir=str(_FONTS), logo_path=render.brand_logo_path(str(_FONTS)),
        threads=render.render_threads(), heartbeat=None, run_id="preview",
        max_seconds=render.max_video_seconds(), layout_engine=engine,
        **({"push_in": True} if push_in else {}),
    )
    (out / "preview.mp4").write_bytes(video)
    onscreen = []
    for i in shown:
        for text in cards.onscreen_strings(specs[i]):
            if text not in onscreen:
                onscreen.append(text)
    image_report = (preview_template_image(script, table, out, font, art=art) if template
                    else preview_image(script, out, font))
    art_entry = None
    if args.art_file is not None:
        art_entry = {"file": str(args.art_file), "problem": art_problem}
        if art is not None:
            art_entry.update({"sha256": art.sha256, "width": art.width, "height": art.height,
                              "cover": bool(specs) and specs[0].kind == "opening" and specs[0].art is not None,
                              "cover_skipped": art_report.get("opening_art_skipped"),
                              "picture": bool(image_report and image_report.get("art_drawn")),
                              "picture_skipped": (image_report or {}).get("art_skipped")})
    report = {
        "out": str(out), "lines": len(lines), "exact_lines": narration.exact_lines,
        "words": len(words), "narration_s": round(duration, 2), "video_s": round(video_seconds, 2),
        "speed": narration.speed, "m4a_bytes": len(data), "mp4_bytes": len(video),
        "cards_shown": shown, "card_kinds": [specs[i].kind for i in shown],
        "video_features": list(features), "push_in": push_in, "end_card_cta": specs[-1].end_cta,
        "data_card_slot": cards.applied_data_card_slot(specs),
        "onscreen_text": onscreen, "missing_glyphs": missing,
        "raqm": cards.raqm_available(), "render_wall_s": round(time.monotonic() - t_render, 1),
        "wall_s": round(time.monotonic() - t0, 1), "video": _ffprobe(out / "preview.mp4"),
        "post_image": image_report, "template": template, "art": art_entry,
        "phonemes": phoneme_report(lines) if args.phonemes else None,
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
