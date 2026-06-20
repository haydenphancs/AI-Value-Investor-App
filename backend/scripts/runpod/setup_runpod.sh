#!/usr/bin/env bash
# setup_runpod.sh — one-time-per-pod provisioner for the Chatterbox clone env.
#
# Run on the RunPod box AFTER the first sync_up.sh has placed the repo at
# $AI_INVESTOR_ROOT (default /workspace/AI-Value-Investor-App). Idempotent: safe to
# re-run. All heavy state (model weights, pip cache) lands on the NETWORK VOLUME so a
# fresh pod rebuilds in ~2-3 min instead of re-downloading multi-GB weights.
#
# Bootstrap (first SSH into a brand-new pod, before the first sync_up):
#     apt-get update && apt-get install -y rsync
# ...then run sync_up.sh from the Mac, then this script.
set -euo pipefail

VOL="${RUNPOD_VOLUME:-/workspace}"                              # network volume mount
export AI_INVESTOR_ROOT="${AI_INVESTOR_ROOT:-$VOL/AI-Value-Investor-App}"
export HF_HOME="$VOL/hf_cache"                                  # Chatterbox (HuggingFace) weights
export TORCH_HOME="$VOL/torch_cache"                            # torchaudio MMS_FA weights (if align runs here)
export PIP_CACHE_DIR="$VOL/pip_cache"                           # warm pip cache -> fast venv rebuilds
mkdir -p "$HF_HOME" "$TORCH_HOME" "$PIP_CACHE_DIR"

# Persist the env for every future shell on this pod (so generate/align pick up the volume caches).
if ! grep -q AI_INVESTOR_ROOT "$HOME/.bashrc" 2>/dev/null; then
  {
    echo "export AI_INVESTOR_ROOT=\"$AI_INVESTOR_ROOT\""
    echo "export HF_HOME=\"$HF_HOME\""
    echo "export TORCH_HOME=\"$TORCH_HOME\""
    echo "export PIP_CACHE_DIR=\"$PIP_CACHE_DIR\""
  } >> "$HOME/.bashrc"
fi

# System packages: ffmpeg (m4a encode), libsndfile1 (soundfile), rsync (sync).
apt-get update -y
apt-get install -y --no-install-recommends ffmpeg libsndfile1 rsync

# Isolated Python env (mirrors the Mac's backend/venv_clone).
cd "$AI_INVESTOR_ROOT/backend"
python -m venv venv_clone
# shellcheck disable=SC1091
. venv_clone/bin/activate
pip install --upgrade pip
# CUDA torch FIRST (cu124) so the rest of the resolve never pulls a CPU wheel.
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements_clone.txt

# Sanity gate: CUDA must be visible and ffmpeg must exist, or a full book wastes paid time.
python - <<'PY'
import torch
print("torch", torch.__version__, "| cuda_available", torch.cuda.is_available(), "| cuda", torch.version.cuda)
assert torch.cuda.is_available(), "CUDA NOT available — check the pod's GPU / driver before generating"
PY
command -v ffmpeg >/dev/null || { echo "ffmpeg missing after install"; exit 1; }

echo "setup_runpod.sh OK · HF_HOME=$HF_HOME · AI_INVESTOR_ROOT=$AI_INVESTOR_ROOT"
echo "Next: run a book through scripts/runpod/time_and_cost.py (see scripts/runpod/README.md)."
