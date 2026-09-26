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
# Inter Bold face is vendored under marketing/assets/fonts; fonts-dejavu-core is
# the fallback libass would silently use if a font were missing, which is why the preflight
# manifest lists what it actually found.
#
# Phase 3 (2026-09-26) adds Kokoro + torch-cpu below and BAKES the model weights into the image
# at build time: a Railway cron container is fresh on every tick, Hugging Face rate-limits
# anonymous downloads, and a 330 MB pull per run is the wrong side of both. The voice stage needs
# ~2 GB of memory (1.7 GB peak measured locally): give the Railway service 4 GB. Nothing in this
# image may ever hold SUPABASE_SERVICE_ROLE_KEY.

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        libfribidi0 \
        libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY marketing/requirements.txt marketing/requirements.txt
# torch from the CPU index FIRST (see requirements.txt), then the rest; then prove no CUDA wheel
# crept in through a transitive dependency.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 \
    && pip install --no-cache-dir -r marketing/requirements.txt \
    && if pip list 2>/dev/null | grep -qi '^nvidia-'; then echo "CUDA wheels in the image" >&2; exit 1; fi

# Bake the Kokoro weights + the af_heart voice + spaCy's model into the image (a cron container
# is fresh every tick; Hugging Face rate-limits anonymous pulls). The warm-up synthesises one
# line, which downloads exactly what the voice stage will load; the resolved model revision is
# recorded next to the weights and reported by the preflight.
ENV HF_HOME=/app/models
RUN python -c "import json, pathlib; from kokoro import KPipeline; p = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M'); list(p('Warm up the voice.', voice='af_heart')); import spacy; spacy.load('en_core_web_sm'); from huggingface_hub import scan_cache_dir; revs = [{'repo': r.repo_id, 'revision': rev.commit_hash} for r in scan_cache_dir().repos for rev in r.revisions]; pathlib.Path('/app/models/revisions.json').write_text(json.dumps(revs))" \
    && cat /app/models/revisions.json

# Only the worker package. The web app's source is deliberately NOT copied: the worker must
# stay importable without app.config (see marketing/__init__.py).
COPY marketing/ marketing/

# HF_HUB_OFFLINE: the baked weights are the ONLY weights — a missing file fails loudly instead
# of silently pulling from the network on a cron tick.
ENV PYTHONUNBUFFERED=1 \
    MARKETING_FONTS_DIR=/app/marketing/assets/fonts \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Railway's cron runs this, once per schedule tick; the process MUST exit (Railway skips the
# next tick while the previous one is alive). The hour gate and the daily claim are inside.
CMD ["python", "-m", "marketing.main"]
