"""verify_learn_voice.py — does a narration clip carry the catalog's voice?

The catalog has ONE narrator: the Chatterbox clone of caydex_voice_achird_v2.wav, shared by every
Money Moves article and all 207 Investor Journey cards. Three clips once shipped in Gemini's
`Achird` instead, and nothing noticed for a week — it is not the kind of thing a schema test or a
build can see.

tests/test_money_moves_catalog_parity.py pins this too, but only for PUBLISHED clips: it divides by
the authored `audioDurationSeconds`, which does not exist until alignment has run. This is the
STAGING gate — it decodes the audio itself, so it works on a freshly rendered clip before anything
has been written back to the JSON, and before a bad render can be moved into the live directory.

    ./venv/bin/python scripts/verify_learn_voice.py data/money_moves_audio_clone/*.m4a
    ./venv/bin/python scripts/verify_learn_voice.py --baseline      # re-measure the reference band

Exit 0 = every clip is in-band. Exit 1 = at least one is not; the reason is named per clip.
Needs ffmpeg/ffprobe and numpy (a script, not a test — the suite stays hermetic).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
SR = 16000

# Measured 2026-08-22 over all 13 clone clips + a 362 s Investor Journey sample. The Gemini
# renders they exclude sat at 124-126 Hz / 170 WPM / 9.7k B/s, well outside every band.
F0_HZ = (130.0, 142.0)          # clone 132.2-137, Journey 133.3; Gemini 124-128. The floor sits
                                # BETWEEN the two clusters, not on one: at 128.0 it exactly equalled
                                # nvidias-ai-dominance and let a known-bad clip through this axis.
                                # Pitch is the weakest of the four anyway — the clone reference was
                                # cut from a Gemini Achird narration, so the voices are related.
                                # Encode and pace are what separate them cleanly.
WPM = (144.0, 166.0)            # TARGET_WPM 165 -> ~158 after block pauses; one clip re-speeded to 150
BYTES_PER_SEC = (11_500, 12_800)  # -b:a 96k; ffmpeg's default (Gemini path) lands ~9.7k
LUFS = (-17.5, -14.5)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(p: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(p)]).stdout.strip()
    return float(out) if out else 0.0


def median_f0(p: Path) -> float:
    """Median voiced pitch via frame autocorrelation. No model, no extra dependency — the point is
    to separate two narrators that differ by ~10 Hz, not to transcribe."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    W, H = int(0.040 * SR), int(0.020 * SR)
    lo, hi = int(SR / 300), int(SR / 70)          # 70-300 Hz search window
    out = []
    for i in range(0, max(0, len(x) - W), H):
        fr = x[i:i + W]
        if np.sqrt((fr ** 2).mean()) < 0.02:       # silence
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[W - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if not len(seg):
            continue
        k = int(np.argmax(seg)) + lo
        if ac[k] / ac[0] < 0.30:                   # unvoiced / weakly periodic
            continue
        out.append(SR / k)
    return float(np.median(out)) if out else float("nan")


def lufs(p: Path) -> float:
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
                "-filter:a", "loudnorm=print_format=json", "-f", "null", "-"]).stderr
    m = re.search(r'"input_i"\s*:\s*"?(-?[\d.]+)', err)
    return float(m.group(1)) if m else float("nan")


def _catalog() -> dict[str, dict]:
    j = BACKEND.parent / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
    if not j.exists():
        j = BACKEND / "data/money_moves.json"
    return {a["slug"]: a for a in json.loads(j.read_text(encoding="utf-8"))["articles"]}


def spoken_words(article: dict) -> int:
    """Mirrors generate_money_moves_audio.narration_blocks — title, subtitle, section titles and
    every spoken body block, markup stripped."""
    b = [article.get("title"), article.get("subtitle")]
    for s in article.get("sections", []):
        b.append(s.get("title"))
        for blk in s.get("content", []):
            k = blk.get("type")
            if k in ("paragraph", "subheading", "quote", "callout") and blk.get("text"):
                b.append(blk["text"])
            elif k == "bulletList":
                b += (blk.get("items") or [])
    return sum(len(re.sub(r"\*\*", "", x).split()) for x in b if x)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--baseline"]
    if "--baseline" in sys.argv:
        args = [str(p) for p in sorted((BACKEND / "data/money_moves_audio").glob("*.m4a"))]
    if not args:
        print(__doc__)
        return 2

    cat = _catalog()
    print(f"{'clip':<42}{'F0':>7}{'WPM':>7}{'B/s':>8}{'LUFS':>8}  verdict")
    bad = 0
    for a in args:
        p = Path(a)
        if not p.exists():
            print(f"{p.name:<42}{'':>30}  MISSING"); bad += 1; continue
        d = duration(p)
        if d <= 0:
            print(f"{p.name:<42}{'':>30}  UNREADABLE"); bad += 1; continue
        art = cat.get(p.stem)
        f0 = median_f0(p)
        w = (spoken_words(art) / d * 60) if art else float("nan")
        bps = p.stat().st_size / d
        lu = lufs(p)
        why = []
        if not F0_HZ[0] <= f0 <= F0_HZ[1]:
            why.append(f"pitch {f0:.1f}Hz outside {F0_HZ} — likely NOT the clone voice")
        if art and not WPM[0] <= w <= WPM[1]:
            why.append(f"pace {w:.1f} WPM outside {WPM}")
        if not BYTES_PER_SEC[0] <= bps <= BYTES_PER_SEC[1]:
            why.append(f"encode {bps:.0f} B/s outside {BYTES_PER_SEC} — wrong bitrate flag?")
        if not LUFS[0] <= lu <= LUFS[1]:
            why.append(f"loudness {lu:.2f} LUFS outside {LUFS}")
        if art is None:
            why.append("slug not in the catalog — filename typo?")
        ok = not why
        bad += 0 if ok else 1
        print(f"{p.stem:<42}{f0:>7.1f}{w:>7.1f}{bps:>8.0f}{lu:>8.2f}  {'OK' if ok else 'FAIL'}")
        for r in why:
            print(f"    - {r}")
    print(f"\n{len(args) - bad}/{len(args)} in-band")
    if bad:
        print("Re-render the failures with CLONE_MODE=block scripts/clone_learn_audio.py "
              "(ref caydex_voice_achird_v2.wav) — do NOT move them into the live dir.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
