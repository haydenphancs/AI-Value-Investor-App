"""
Generate Achird narration (~170 WPM) for each Money Moves article.

Reads the article catalog (frontend money_moves.json locally, or the vendored
backend/data/money_moves.json on Railway), builds one narration per article from its
sections, synthesizes it via Gemini TTS — CHUNKED to stay under the per-call input limit,
with the raw PCM concatenated — normalizes the tempo to TARGET_WPM with ffmpeg, and writes
one backend/data/money_moves_audio/<slug>.m4a per article (staged for upload by
seed_money_moves.py).

Usage (from backend/):
    ./venv/bin/python scripts/generate_money_moves_audio.py            # all articles
    ./venv/bin/python scripts/generate_money_moves_audio.py amazon     # slug substring filter
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]                 # backend/
REPO = ROOT.parent                                          # repo root
# Frontend tree is the source of truth locally; on Railway only backend/ is deployed,
# so fall back to the vendored copy at backend/data/money_moves.json.
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else ROOT / "data/money_moves.json"
OUT = ROOT / "data/money_moves_audio"
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
MAX_CHUNK_CHARS = 1800          # keep each TTS request comfortably under the model's input cap
STYLE = (
    "You are a sharp, engaging financial storyteller narrating an investing case study to a "
    "curious learner. Speak in a warm, clear, conversational tone, never robotic. Use natural "
    "pauses at the periods so the listener can follow the story. Read the following:\n\n"
)

only = sys.argv[1] if len(sys.argv) > 1 else None

# Deliberately slow and steady to stay under the free-tier rate limits. Override with TTS_THROTTLE.
THROTTLE_SECONDS = float(os.environ.get("TTS_THROTTLE", "20"))
MAX_429_WAIT = 120


def strip_markup(s: str) -> str:
    return re.sub(r"\*\*", "", s or "")


def narration_blocks(article: dict) -> list[str]:
    """Ordered readable text blocks for an article (title, subtitle, then each section)."""
    blocks: list[str] = []
    if article.get("title"):
        blocks.append(article["title"])
    if article.get("subtitle"):
        blocks.append(article["subtitle"])
    for section in article.get("sections", []):
        if section.get("title"):
            blocks.append(section["title"])
        for block in section.get("content", []):
            kind = block.get("type")
            if kind in ("paragraph", "subheading", "quote") and block.get("text"):
                blocks.append(block["text"])
            elif kind == "bulletList":
                blocks += (block.get("items") or [])
            elif kind == "callout" and block.get("text"):
                blocks.append(block["text"])
    return [strip_markup(b) for b in blocks if b]


def chunk_blocks(blocks: list[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Group blocks into chunks under `limit` chars, never splitting a single block."""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
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
        # Other client error — raise WITHOUT the URL (which carries the API key)
        raise RuntimeError(f"TTS request failed: HTTP {r.status_code}")
    raise RuntimeError("TTS request failed after repeated 429s (likely a daily quota cap)")


def render(article: dict) -> bool:
    slug = article["slug"]
    m4a = OUT / f"{slug}.m4a"
    if m4a.exists():
        print(f"  {slug:30s} (exists, skip)")
        return False

    blocks = narration_blocks(article)
    chunks = chunk_blocks(blocks)
    words = sum(len(b.split()) for b in blocks)
    print(f"  {slug:30s} {len(chunks)} chunk(s), ~{words} words")

    pcm_all = b""
    rate = 24000
    for i, chunk in enumerate(chunks):
        pcm, rate = synth_pcm(chunk)
        pcm_all += pcm
        if i < len(chunks) - 1:
            time.sleep(THROTTLE_SECONDS)   # throttle between chunk calls too

    wav = OUT / f"{slug}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm_all)
    native_secs = len(pcm_all) / 2 / rate
    native_wpm = (words / native_secs * 60) if native_secs else TARGET_WPM
    atempo = max(0.5, min(2.0, TARGET_WPM / native_wpm))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-filter:a", f"atempo={atempo:.4f}", str(m4a)], check=True)
    wav.unlink(missing_ok=True)
    print(f"    -> {slug}.m4a ({native_secs / atempo:.0f}s)")
    return True


data = json.loads(JSON_PATH.read_text())
total = 0
for article in data["articles"]:
    if only and only not in article["slug"]:
        continue
    if render(article):
        total += 1
        time.sleep(THROTTLE_SECONDS)
print(f"\nDone. {total} article narration(s) -> {OUT}")
