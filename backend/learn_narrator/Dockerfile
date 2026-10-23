# learn_narrator/Dockerfile — the Learn NARRATOR worker (plan "Wiser weekly content engine" §1.8).
#
# A THIRD Railway service on this repo, Root Directory `/backend/learn_narrator` — the build context
# is this folder (Railway auto-detects THIS file; see railway.toml for why a config path is not used).
# It is neither the web app nor the marketing worker: chatterbox-tts pins numpy<2 and
# transformers==5.2.0, which the marketing worker's Kokoro stack cannot share. Nothing under
# learn_narrator/ imports app.*, and this image holds NO Supabase key — it reaches the database only
# through the token-gated internal API and uploads through signed URLs.
#
# Build it for linux/amd64 on an x86 host or in CI (RUNBOOK.md) — NEVER on the owner's 16 GB Mac.
# The smoke step renders a line with Chatterbox on the build machine's CPU.

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt constraints.txt learn_narrator/
# torch AND torchaudio from the CPU index FIRST (the 2.6.0+cpu wheels; the PyPI ones pull ~3 GB of
# CUDA), then everything else under the ONE resolved constraints file. Then: the resolver agrees
# with itself (pip check), no CUDA wheel crept in, and the torch build really is the CPU one.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 torchaudio==2.6.0 \
    && pip install --no-cache-dir -r learn_narrator/requirements.txt -c learn_narrator/constraints.txt \
    && pip check \
    && if pip list 2>/dev/null | grep -qiE '^(nvidia-|triton)'; then echo "CUDA wheels in the image:" >&2; pip list 2>/dev/null | grep -iE '^(nvidia-|triton)' >&2; exit 1; fi \
    && python -c "import torch, torchaudio; assert torch.version.cuda is None, torch.__version__; assert torch.__version__.startswith('2.6.0'), torch.__version__; assert torchaudio.__version__.startswith('2.6.0'), torchaudio.__version__; print('torch', torch.__version__, 'torchaudio', torchaudio.__version__)"

# Bake EVERY weight into the image: Chatterbox and Kokoro from Hugging Face (pinned commits, refs/main
# pointed at them) AND the torch.hub WAV2VEC2_ASR_BASE_960H checkpoint (MIT) — HF_HUB_OFFLINE does
# not cover torch.hub, so TORCH_HOME must be baked too. Copied before the rest of the package so a
# code change does not re-download ~3 GB.
ENV HF_HOME=/app/models/hf \
    TORCH_HOME=/app/models/torch \
    LEARN_NARRATOR_MODELS=/app/models
COPY __init__.py bake.py learn_narrator/
RUN python -m learn_narrator.bake

# Only the worker package — the build context IS the package, so the web app's source cannot be
# copied in even by mistake.
COPY . learn_narrator/

# Offline from here on: the baked weights are the ONLY weights. The children re-assert these
# switches and refuse torch.hub downloads; the preflight fails instead of downloading.
ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    NUMBA_CACHE_DIR=/tmp/learn_narrator_numba

# The build-time proof, with the NETWORK OFF: one Chatterbox line (prepared once, exaggeration
# passed), its AAC encode, a WAV2VEC2 forced alignment of it (torchaudio.functional.forced_align),
# and Kokoro with every baked voice — then the worker's own preflight. A weight the bake missed
# fails HERE, not on the first cron tick.
# ⚠️ Never put a network flag on RUN: Railway's Dockerfile validation rejects every RUN flag but a
# cache mount (the first deploy failed with "flag ... is not supported at Line 57", 2026-10-23). The
# smoke refuses the network IN-PROCESS instead (child._refuse_network), and the dead proxy below —
# on THIS command only, never an ENV line (the worker's httpx would follow it at run time) — fails
# any child process that tries to download (pip, curl).
RUN HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
    http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 \
    NO_PROXY= no_proxy= \
    python -m learn_narrator.child smoke /tmp/smoke \
    && python -m learn_narrator.main --preflight \
    && rm -rf /tmp/smoke

# Railway's cron runs this once per tick (every 20 min); the process MUST exit (Railway skips the
# next tick while one is alive). It claims at most one job.
CMD ["python", "-m", "learn_narrator.main"]
