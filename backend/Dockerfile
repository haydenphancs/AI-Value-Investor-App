# Dockerfile — Caydex FastAPI backend.
#
# Why: WeasyPrint ("View Detailed Analysis" PDF) loads native libs at runtime via cffi —
# GLib/GObject, Pango, Cairo, gdk-pixbuf. The prior Nixpacks aptPkgs approach did NOT make
# them discoverable to the runtime Python (find_library failed -> OSError gobject-2.0-0).
# A standard Debian slim image installs them in paths WeasyPrint finds reliably.

FROM python:3.11-slim-bookworm

# libglib2.0-0 provides libgobject-2.0.so.0 (the missing lib). libpangocairo pulls pango+cairo;
# the rest cover image decoding, text shaping, bidi, mime detection, and a base font.
# ffmpeg is used by scripts/generate_journey_audio.py to normalize TTS narration tempo
# (run as a one-off via `railway ssh`); harmless to the web runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libharfbuzz0b \
        libfribidi0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (layer cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source (build context is backend/).
COPY . .

ENV PYTHONUNBUFFERED=1

# Railway injects $PORT; default 8000 for local docker run.
# --proxy-headers + --forwarded-allow-ips='*' makes uvicorn honor Railway's
# X-Forwarded-For so request/websocket .client.host is the REAL client IP (not the
# proxy's). Needed for accurate client-IP logging and for the per-IP auth rate
# limiters (`core/security.py`). Safe here: Railway's
# edge is the only ingress and always sets the header.
# --timeout-graceful-shutdown 30: on SIGTERM uvicorn waits for open connections to
# drain BEFORE it runs the lifespan shutdown, and with no bound that wait is infinite
# — one live SSE chat stream held the old instance until Railway SIGKILLed it, so the
# lifespan `finally` blocks that release the durable job claims (migration 147) never
# ran and a claim sat parked for its whole stale window (W2 B-1). 30 s is enough for a
# normal drain and well inside Railway's stop grace; the SSE reader reconciles on a
# drop (it never re-POSTs), so cutting a stream is safe.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 30"]
