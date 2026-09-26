"""
Local preview of the Phase 3 voice + captions (writes nothing anywhere but `marketing/out/`,
which is gitignored). Uses the SAME functions the worker's `voiced` stage uses — the synthesis
child, the bit-exact AAC encode, the timing table — plus the caption builder Phase 4's render
will burn, and renders a solid-background 1080×1920 MP4 so a human can watch the sync.

    cd backend
    ./venv_marketing/bin/python -m marketing.preview                   # built-in demo script
    ./venv_marketing/bin/python -m marketing.preview path/to/script.json
    # script.json = the worker's WorkerScript shape: {"hook": "...", "video_script": ["...", ...]}

Needs ffmpeg with libass (Homebrew's and the image's both have it) and the Kokoro weights
(downloaded to HF_HOME on first use locally; baked into the Railway image).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from marketing import captions, timings, voice

_OUT = Path(__file__).resolve().parent / "out"
_FONTS = Path(__file__).resolve().parent / "assets" / "fonts"

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
}


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_name,width,height,sample_rate,channels",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main(argv: list) -> int:
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
    font = str(_FONTS / "Inter-Bold.ttf")
    missing = captions.missing_glyphs(font, [w["w"] for w in narration.words])
    ass = captions.build_ass(narration.words, captions.font_measurer(font))
    (out / "captions.ass").write_text(ass, encoding="utf-8")
    (out / "words.json").write_text(json.dumps(narration.words, indent=1), encoding="utf-8")
    video = out / "preview.mp4"
    # Solid page colour; `subtitles=` via a relative filename inside `out` avoids filter-path
    # escaping (a colon or quote in an absolute path breaks the filtergraph).
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x171B26:s=1080x1920:r=30:d={duration:.3f}",
           "-i", "narration.m4a",
           "-vf", f"ass=captions.ass:fontsdir={_FONTS}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-shortest", "-movflags", "+faststart", "-threads", "2", "preview.mp4"]
    subprocess.run(cmd, cwd=out, check=True)
    probe = _ffprobe(video)
    print(json.dumps({
        "out": str(out), "lines": len(lines), "exact_lines": narration.exact_lines,
        "words": len(narration.words), "narration_s": round(duration, 2), "speed": narration.speed,
        "m4a_bytes": len(data), "missing_glyphs": missing, "wall_s": round(time.monotonic() - t0, 1),
        "video": probe,
    }, indent=1))
    return 0 if shutil.which("ffmpeg") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
