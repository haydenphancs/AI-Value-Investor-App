"""
Generate ONE Achird narration (~170 WPM) per BOOK for the Book Library.

Unlike Money Moves (one clip per article) and Journey (one clip per card), a book is a SINGLE
.m4a covering all of its cores in order, with:
  - a natural, VARIED connecting sentence (bridge) leading into each core — never a fixed
    template — that nods to the previous theme and names the next core,
  - a short SILENCE break between cores,
  - action plans EXCLUDED from the narration (per product decision),
and it emits a per-book MANIFEST recording where each core STARTS (seconds) in the final
audio, so the iOS core timeline can show a timestamp under each core number ("1" -> "0:00",
"2" -> "1:34", ...) and book-level playback can seek(to:) a core's start.

Core text is read from the SAME authored source the Swift content is generated from
(documents/Books/<book>/core N.txt) via gen_books_swift.parse_core, so narration and the
on-screen cores never drift. Action-plan steps live in parse_core's `action` (excluded here);
the only action artifact left in `sections` is the "The Action Plan" heading, which we drop.

Pipeline per core SEGMENT (bridge + body): chunk under the TTS input cap -> Gemini TTS PCM ->
concat -> ffmpeg atempo to TARGET_WPM. Segments are concatenated in the PCM domain with a
fixed silence between them, then encoded once to .m4a. Timestamps are measured from the
normalized PCM, so they are exact.

Output (from backend/):
  backend/data/book_audio/<order>_<slug>.m4a
  backend/data/book_audio/<order>_<slug>.manifest.json

Usage:
  ./venv/bin/python scripts/generate_book_audio.py 1        # curriculum order 1 (Rich Dad Poor Dad)
  ./venv/bin/python scripts/generate_book_audio.py 1 --force
"""
import base64
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

# parse_core / BOOKS / BD live in the Swift content generator. Importing it re-emits
# BooksContent.swift (idempotent, same bytes) and prints a summary — suppress that noise.
sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]                 # backend/
OUT = ROOT / "data/book_audio"
OUT.mkdir(parents=True, exist_ok=True)

# Prefer the process env (Railway injects GEMINI_API_KEY); fall back to backend/.env locally.
KEY = os.environ.get("GEMINI_API_KEY")
_env_file = ROOT / ".env"
if not KEY and _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
assert KEY, "GEMINI_API_KEY not found in env or backend/.env"

MODEL = "gemini-2.5-flash-preview-tts"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
VOICE = "Achird"
TARGET_WPM = 170
MAX_CHUNK_CHARS = 1800           # keep each TTS request comfortably under the model's input cap
BREAK_SECONDS = 2.5              # silence between cores ("a short break for each core")
STYLE = (
    "You are a warm, articulate audiobook narrator guiding a curious learner through the key "
    "ideas of a classic investing book. Speak in a clear, engaging, conversational tone, never "
    "robotic. Use natural pauses at the periods, and let the transitions between chapters "
    "breathe. Read the following:\n\n"
)

# Deliberately slow and steady to stay under the free-tier rate limits. Override with TTS_THROTTLE.
THROTTLE_SECONDS = float(os.environ.get("TTS_THROTTLE", "20"))
MAX_429_WAIT = 120

# ---------------------------------------------------------------------------------------------
# Bridges (connecting sentences). Hand-authored per book for quality — index 0 is the lead-in to
# core 1 (a short book intro), index i is the lead-in to core i+1. Each bridge NAMES its core, so
# the body narration omits the title (no double-read). Keep them VARIED — never one template.
# Books without an entry fall back to _generic_bridges() so the script still works.
# ---------------------------------------------------------------------------------------------
BRIDGES: dict[int, list[str]] = {
    1: [  # Rich Dad Poor Dad — Robert T. Kiyosaki (7 cores)
        "Welcome to Rich Dad Poor Dad, by Robert Kiyosaki. Over the next seven cores, we'll "
        "rewire how you think about money, work, and wealth. Let's begin with the foundation of "
        "it all: De-Programming the Employee Mindset.",

        "So that's the first job — breaking the employee programming that keeps most people "
        "stuck. Once your mindset shifts, you need a way to keep score that actually reflects "
        "wealth. That brings us to Core 2: Mastering the Financial Scorecard.",

        "Now that you can read the real scoreboard, a new question appears — how do you keep more "
        "of what you earn instead of handing it away? That's the heart of the next core: The "
        "Corporate Shield Strategy.",

        "With your earnings protected, it's time to go on offense. The wealthy don't wait for "
        "opportunity; they train themselves to see it everywhere. Step into Core 4: The "
        "Opportunity Hunter.",

        "Spotting an opportunity is one thing — being equipped to seize it is another. Which is "
        "why this next core flips the usual advice on its head: Trading Security for Skills.",

        "But here's the twist: knowledge alone has never made anyone rich. The real battle is the "
        "one inside your own head. So next, we face Core 6: Conquering the Inner Saboteur.",

        "You've done the hard mental work — the mindset, the money skills, the courage. All "
        "that's left now is to actually begin. Let's close the book with the First 3 Steps "
        "Launchpad.",
    ],
}

_GENERIC_TEMPLATES = [
    "Now, let's move on to {title}.",
    "That leads us naturally to the next core: {title}.",
    "With that in mind, we turn to {title}.",
    "Next up: {title}.",
    "Building on that, here's {title}.",
    "Which brings us to {title}.",
]


def _generic_bridges(book_title: str, author: str, titles: list[str]) -> list[str]:
    """Fallback varied bridges for a book without a hand-authored set."""
    out = [f"Welcome to {book_title}, by {author}. Let's begin with the first core: {titles[0]}."]
    for i, t in enumerate(titles[1:]):
        out.append(_GENERIC_TEMPLATES[i % len(_GENERIC_TEMPLATES)].format(title=t))
    return out


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def strip_markup(s: str) -> str:
    return re.sub(r"\*\*", "", s or "")


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def body_blocks(title: str, sections: list[tuple[str, str]]) -> list[str]:
    """Spoken blocks for a core: every section EXCEPT the action-plan heading. Title is omitted
    because the bridge already names the core."""
    blocks = []
    for kind, text in sections:
        if kind == "heading" and re.search(r"action\s+plan", text, re.I):
            continue
        blocks.append(strip_markup(text))
    return [b for b in blocks if b]


def chunk_blocks(blocks: list[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Group blocks into chunks under `limit` chars, never splitting a single block."""
    chunks, cur, cur_len = [], [], 0
    for b in blocks:
        if cur and cur_len + len(b) + 1 > limit:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += len(b) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def synth_pcm(text: str):
    body = {
        "contents": [{"parts": [{"text": STYLE + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}},
        },
    }
    for attempt in range(8):
        r = httpx.post(URL, json=body, timeout=180)
        if r.status_code == 200:
            part = r.json()["candidates"][0]["content"]["parts"][0]
            pcm = base64.b64decode(part["inlineData"]["data"])
            mime = part["inlineData"].get("mimeType", "")
            rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
            return pcm, rate
        if r.status_code == 429:
            wait = min(MAX_429_WAIT, int(r.headers.get("retry-after", 0)) or (10 * (attempt + 1)))
            print(f"      429 rate-limited; waiting {wait}s (attempt {attempt + 1}/8)")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(3)
            continue
        raise RuntimeError(f"TTS request failed: HTTP {r.status_code}")  # no URL (carries key)
    raise RuntimeError("TTS request failed after repeated 429s (likely a daily quota cap)")


def synth_segment(blocks: list[str], tmp: Path, name: str) -> tuple[bytes, int]:
    """Synthesize one core segment (bridge + body) to NORMALIZED (170 WPM) mono 16-bit PCM."""
    chunks = chunk_blocks(blocks)
    words = sum(len(b.split()) for b in blocks)
    pcm_all, rate = b"", 24000
    for i, chunk in enumerate(chunks):
        pcm, rate = synth_pcm(chunk)
        pcm_all += pcm
        if i < len(chunks) - 1:
            time.sleep(THROTTLE_SECONDS)

    native_secs = len(pcm_all) / 2 / rate
    native_wpm = (words / native_secs * 60) if native_secs else TARGET_WPM
    atempo = max(0.5, min(2.0, TARGET_WPM / native_wpm))

    raw = tmp / f"{name}.raw.wav"
    norm = tmp / f"{name}.norm.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm_all)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                    "-filter:a", f"atempo={atempo:.4f}", str(norm)], check=True)
    with wave.open(str(norm), "rb") as w:
        out_rate = w.getframerate()
        out_pcm = w.readframes(w.getnframes())
    raw.unlink(missing_ok=True); norm.unlink(missing_ok=True)
    return out_pcm, out_rate


def render_book(order: int, force: bool) -> None:
    entry = next((b for b in g.BOOKS if b[0] == order), None)
    if entry is None:
        raise SystemExit(f"No book with curriculum order {order} in gen_books_swift.BOOKS")
    _, dirname, btitle, bauthor = entry
    slug = slugify(btitle)
    m4a = OUT / f"{order}_{slug}.m4a"
    manifest_path = OUT / f"{order}_{slug}.manifest.json"
    if m4a.exists() and not force:
        raise SystemExit(f"{m4a.name} exists; pass --force to regenerate.")

    d = g.BD / dirname
    cores = sorted([p for p in d.glob("*.txt") if re.fullmatch(r"core\s+\d+", p.stem, re.I)],
                   key=g.core_num)
    parsed = [(g.core_num(p), *g.parse_core(p)) for p in cores]  # (num, title, sections, action, ...)

    titles = [title for _, title, *_ in parsed]
    bridges = BRIDGES.get(order) or _generic_bridges(btitle, bauthor, titles)
    if len(bridges) != len(parsed):
        raise SystemExit(f"bridge count {len(bridges)} != core count {len(parsed)} for order {order}")

    print(f"\n[{btitle}] {len(parsed)} cores -> {m4a.name}")
    tmp = OUT / f".tmp_{order}"
    tmp.mkdir(exist_ok=True)

    final_pcm, rate = b"", 24000
    silence = b""
    cores_manifest = []
    for idx, (num, title, sections, *_rest) in enumerate(parsed):
        blocks = [bridges[idx]] + body_blocks(title, sections)
        words = sum(len(b.split()) for b in blocks)
        print(f"  core {num} '{title}' (~{words} words incl. bridge)")
        seg_pcm, rate = synth_segment(blocks, tmp, f"core{num}")
        if not silence:
            silence = b"\x00\x00" * int(rate * BREAK_SECONDS)

        start_seconds = len(final_pcm) / 2 / rate
        cores_manifest.append({
            "number": num,
            "title": title,
            "start_seconds": round(start_seconds, 2),
            "start_label": fmt_ts(start_seconds),
        })
        final_pcm += seg_pcm
        if idx < len(parsed) - 1:
            final_pcm += silence            # break AFTER each core except the last
        time.sleep(THROTTLE_SECONDS)

    total_seconds = len(final_pcm) / 2 / rate
    final_wav = tmp / "book.wav"
    with wave.open(str(final_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(final_pcm)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(final_wav), str(m4a)], check=True)
    final_wav.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        tmp.rmdir()

    manifest = {
        "curriculum_order": order,
        "book_title": btitle,
        "author": bauthor,
        "slug": slug,
        "audio_file": m4a.name,
        "voice": VOICE,
        "target_wpm": TARGET_WPM,
        "break_seconds": BREAK_SECONDS,
        "total_seconds": round(total_seconds, 2),
        "total_label": fmt_ts(total_seconds),
        "cores": cores_manifest,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n  -> {m4a}  ({fmt_ts(total_seconds)})")
    print(f"  -> {manifest_path.name}")
    print("  core start times:")
    for c in cores_manifest:
        print(f"     {c['number']}: {c['start_label']}  {c['title']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    if not args:
        raise SystemExit("Usage: generate_book_audio.py <curriculum_order> [--force]")
    render_book(int(args[0]), force)
