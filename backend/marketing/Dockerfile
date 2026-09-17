# marketing/Dockerfile — the marketing-engine MEDIA WORKER (design doc §12).
#
# A SECOND Railway service on this same repo (root directory /backend — the build context —
# config file /backend/marketing/railway.toml, cron schedule). It is NOT the web app and never
# runs it: the entrypoint is `python -m marketing.main`, and nothing under marketing/ imports
# app.* — so no
# lifespan loop starts twice and no Supabase key is needed in this container. It talks to the
# backend's token-gated internal API and PUTs media through signed upload URLs.
#
# Same Debian base + the same ffmpeg (bookworm's 5.1.9 is built with libass / libfreetype /
# libfontconfig / libfribidi / libx264 — everything the caption burn needs). Fonts: the OFL
# Inter family is vendored under marketing/assets/fonts (Phase 4 adds the files); fonts-dejavu-core is
# the fallback libass would silently use if a font were missing, which is why the preflight
# manifest lists what it actually found.
#
# Phase 3 adds Kokoro + torch-cpu here (`pip install --index-url https://download.pytorch.org/whl/cpu`)
# and BAKES the model weights into the image at build time: a Railway cron container is fresh
# on every tick, Hugging Face rate-limits anonymous downloads, and a 330 MB pull per run is the
# wrong side of both. Nothing in this image may ever hold SUPABASE_SERVICE_ROLE_KEY.

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        libfribidi0 \
        libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY marketing/requirements.txt marketing/requirements.txt
RUN pip install --no-cache-dir -r marketing/requirements.txt

# Only the worker package. The web app's source is deliberately NOT copied: the worker must
# stay importable without app.config (see marketing/__init__.py).
COPY marketing/ marketing/

ENV PYTHONUNBUFFERED=1 \
    MARKETING_FONTS_DIR=/app/marketing/assets/fonts

# Railway's cron runs this, once per schedule tick; the process MUST exit (Railway skips the
# next tick while the previous one is alive). The hour gate and the daily claim are inside.
CMD ["python", "-m", "marketing.main"]
