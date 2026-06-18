"""
One-off: generate short Gemini TTS voice SAMPLES (one per candidate book voice) so we can pick a
per-book "author voice" for the Book Library. Each sample = a prebuilt voice + a persona style
prompt + a short line in that author's tone. Delivered at the voice's NATURAL pace (no tempo
normalization) so the character comes through; the real book pipeline normalizes pace later.

Output: backend/data/voice_samples/<NN>_<author>_<voice>.m4a  (skips existing)

Usage (from backend/):
    ./venv/bin/python scripts/gen_voice_samples.py
"""
import base64
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/voice_samples"
OUT.mkdir(parents=True, exist_ok=True)

KEY = os.environ.get("GEMINI_API_KEY")
_env = ROOT / ".env"
if not KEY and _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
assert KEY, "GEMINI_API_KEY not found in env or backend/.env"

MODEL = "gemini-2.5-flash-preview-tts"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
THROTTLE = float(os.environ.get("TTS_THROTTLE", "8"))

# (order, author, book, voice, style direction, sample line)
SAMPLES = [
    (1, "graham", "The Intelligent Investor", "Iapetus",
     "Read this as an erudite, classically-educated finance professor — articulate, precise, emotionally detached and measured, with dry wit and dignified, calm authority:",
     "The intelligent investor is a realist who sells to optimists and buys from pessimists. Mr. Market is there to serve you, not to instruct you."),
    (2, "buffett", "The Essays of Warren Buffett", "Zubenelgenubi",
     "Read this as a warm, wise, plain-spoken older man — patient, unhurried, with gentle good humor. Use a neutral American accent, not a strong regional or folksy twang:",
     "It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price. If you wouldn't own it for ten years, don't own it for ten minutes."),
    (9, "fisher", "Common Stocks and Uncommon Profits", "Schedar",
     "Read this as a meticulous, reserved, scholarly analyst — even, careful, methodical and precise, slightly formal:",
     "The wise investor buys a stock not because it is cheap, but because the business behind it can grow for many years to come."),
    (3, "lynch", "One Up On Wall Street", "Enceladus",
     "Read this as a seasoned, older stock-picker — sharp and engaging, in a mature, refined older man's voice. Not folksy, not regional:",
     "Behind every stock is a company. Go find out what it's doing! Often the best stock to buy is the one already sitting in your shopping cart."),
    (4, "housel", "The Psychology of Money", "Puck",
     "Read this as a calm, thoughtful modern essayist — reflective, intimate and understated, with gentle pacing:",
     "Doing well with money has little to do with how smart you are, and a lot to do with how you behave."),
    (5, "bogle", "The Little Book of Common Sense Investing", "Alnilam",
     "Read this as a principled elder statesman of investing — steady, firm, full of conviction, plain and direct:",
     "Don't look for the needle in the haystack. Just buy the haystack, and let the miracle of compounding do the rest."),
    (6, "malkiel", "A Random Walk Down Wall Street", "Orus",
     "Read this as a normal older man in his eighties — a witty professor emeritus, clear and plain-spoken, in a low, measured register:",
     "A blindfolded monkey throwing darts at the stock pages could pick a portfolio that does just as well as one chosen by the experts."),
    (7, "marks", "The Most Important Thing", "Sadaltager",
     "Read this as a seasoned, contemplative investor weighing each idea — calm gravitas, thoughtful and measured:",
     "Risk means more things can happen than will happen. The riskiest belief of all is that there is no risk."),
    (8, "greenblatt", "The Little Book that Still Beats the Market", "Achird",
     "Read this as a friendly, patient teacher explaining a clever idea simply to a curious beginner — warm and a touch playful:",
     "Here's the secret, and it isn't complicated: buy good companies at bargain prices. That's really the whole game."),
]


def synth(text: str, voice: str):
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
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
            wait = min(120, int(r.headers.get("retry-after", 0)) or 10 * (attempt + 1))
            print(f"    429 — waiting {wait}s (attempt {attempt+1}/8)")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(3)
            continue
        raise RuntimeError(f"TTS failed: HTTP {r.status_code}")
    raise RuntimeError("TTS failed after repeated 429s (daily quota cap?)")


def main():
    made = 0
    for order, author, book, voice, style, line in SAMPLES:
        m4a = OUT / f"{order}_{author}_{voice}.m4a"
        if m4a.exists():
            print(f"  {m4a.name} (exists, skip)")
            continue
        print(f"[{order}] {voice:12s} {book}")
        pcm, rate = synth(f"{style}\n\n{line}", voice)
        wav = OUT / f"{order}_{author}_{voice}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(pcm)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                        "-c:a", "aac", "-b:a", "96k", str(m4a)], check=True)
        wav.unlink(missing_ok=True)
        secs = len(pcm) / 2 / rate
        print(f"    -> {m4a.name} ({secs:.0f}s, {voice})")
        made += 1
        time.sleep(THROTTLE)
    print(f"\nDone. {made} new sample(s) -> {OUT}")


if __name__ == "__main__":
    main()
