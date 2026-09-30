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

Needs ffmpeg with libass (Homebrew's and the image's both have it) and the Kokoro weights
(downloaded to HF_HOME on first use locally; baked into the Railway image).

Text layout: the Linux image's Pillow wheel lays text out with raqm; Homebrew's raqm is found only
when DYLD_FALLBACK_LIBRARY_PATH names /opt/homebrew/lib AT PROCESS START (dyld reads it once), so
on macOS the preview re-executes itself once with it set — otherwise its wraps would differ from
production's by ~5% of a line.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"
_FONTS = Path(__file__).resolve().parent / "assets" / "fonts"
_HOMEBREW_LIB = "/opt/homebrew/lib"
_REEXEC_FLAG = "MARKETING_PREVIEW_REEXEC"

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


def main(argv: list) -> int:
    _ensure_raqm_env(argv)
    from marketing import cards, render, timings, voice

    if not shutil.which("ffmpeg"):
        sys.stderr.write("ffmpeg is not on PATH\n")
        return 1
    script = json.loads(Path(argv[0]).read_text(encoding="utf-8")) if argv else DEMO
    lines = timings.narrated_lines(script)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = _OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    narration = voice.synthesize_fitting(lines, voice=voice.DEFAULT_VOICE, speed=1.0, workdir=out,
                                         heartbeat=None, budget=voice.video_budget_seconds(),
                                         runner=voice.run_child)
    data, duration = voice.encode_m4a(narration.wav_path, out / "narration.m4a")
    words = voice.clamp_to_duration(narration.words, duration)
    (out / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    specs = cards.cards_for_script(script)
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
    )
    (out / "preview.mp4").write_bytes(video)
    onscreen = []
    for i in shown:
        for text in cards.onscreen_strings(specs[i]):
            if text not in onscreen:
                onscreen.append(text)
    report = {
        "out": str(out), "lines": len(lines), "exact_lines": narration.exact_lines,
        "words": len(words), "narration_s": round(duration, 2), "video_s": round(video_seconds, 2),
        "speed": narration.speed, "m4a_bytes": len(data), "mp4_bytes": len(video),
        "cards_shown": shown, "onscreen_text": onscreen, "missing_glyphs": missing,
        "raqm": cards.raqm_available(), "render_wall_s": round(time.monotonic() - t_render, 1),
        "wall_s": round(time.monotonic() - t0, 1), "video": _ffprobe(out / "preview.mp4"),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
