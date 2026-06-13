"""
Generate Achird narration clips (~170 WPM) for every Investor Journey lesson.

Reads the master content file (frontend journey_lessons.json), synthesizes one
.m4a per narrated card (title + content cards; completion is silent) via Gemini
TTS, normalizes the tempo to TARGET_WPM with ffmpeg, and writes them to
backend/data/journey_audio/<audioClip>.m4a — staged for upload to Supabase Storage
by seed_journey.py.

Usage:
    ./venv/bin/python scripts/generate_journey_audio.py            # all lessons
    ./venv/bin/python scripts/generate_journey_audio.py mr_market  # one lesson key prefix
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
JSON_PATH = REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
OUT = ROOT / "data/journey_audio"
OUT.mkdir(parents=True, exist_ok=True)

KEY = None
for line in (ROOT / ".env").read_text().splitlines():
    if line.startswith("GEMINI_API_KEY="):
        KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
assert KEY, "GEMINI_API_KEY not found in backend/.env"

MODEL = "gemini-2.5-flash-preview-tts"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
VOICE = "Achird"
TARGET_WPM = 170
STYLE = (
    "You are a patient, trusted financial mentor sitting across the table from a beginner. "
    "Speak in a warm, friendly, conversational tone, never robotic. Use gentle pauses at the "
    "periods so a new investor can absorb each idea. Read the following:\n\n"
)

only = sys.argv[1] if len(sys.argv) > 1 else None

# Deliberately slow and steady to stay well under the free-tier rate limits.
# Speed does not matter here; avoiding 429s does. Override with TTS_THROTTLE=<seconds>.
THROTTLE_SECONDS = float(os.environ.get("TTS_THROTTLE", "20"))
MAX_429_WAIT = 120       # cap a single Retry-After backoff


def strip_markup(s: str) -> str:
    return re.sub(r"\*\*", "", s)


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
            print(f"    429 rate-limited; waiting {wait}s (attempt {attempt + 1}/8)")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(3)
            continue
        # Other client error — raise WITHOUT the URL (which carries the API key)
        raise RuntimeError(f"TTS request failed: HTTP {r.status_code}")
    raise RuntimeError("TTS request failed after repeated 429s (likely a daily quota cap)")


def render(text: str, clip: str):
    m4a = OUT / f"{clip}.m4a"
    if m4a.exists():
        print(f"  {clip:30s} (exists, skip)")
        return False
    spoken = strip_markup(text)
    pcm, rate = synth_pcm(spoken)
    wav = OUT / f"{clip}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm)
    native_secs = len(pcm) / 2 / rate
    native_wpm = len(spoken.split()) / native_secs * 60
    atempo = max(0.5, min(2.0, TARGET_WPM / native_wpm))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-filter:a", f"atempo={atempo:.4f}", str(m4a)], check=True)
    wav.unlink(missing_ok=True)
    print(f"  {clip:30s} {native_secs / atempo:5.1f}s")
    return True


data = json.loads(JSON_PATH.read_text())
total = 0
for lesson in data["lessons"]:
    cards = lesson["cards"]
    title_clip = next((c.get("audioClip") for c in cards if c.get("audioClip")), "")
    if only and not title_clip.startswith(only):
        continue
    print(f"\n[{lesson['title']}]")
    for card in cards:
        clip = card.get("audioClip")
        if not clip:
            continue
        if render(card["text"], clip):
            total += 1
            time.sleep(THROTTLE_SECONDS)   # stay under the free-tier RPM limit
print(f"\nDone. {total} clips -> {OUT}")
