#!/usr/bin/env bash
# run_learn_clone.sh — POD-SIDE batch runner for Money Moves narration (Chatterbox clone).
#
# Why this exists: the clip list is long, each slug is a separate invocation, and SSH to a
# GPU-saturated pod drops (exit 255) often enough that a foreground loop loses the batch.
# Run it DETACHED and poll the log:
#
#     cd /workspace/AI-Value-Investor-App/backend
#     setsid nohup bash scripts/runpod/run_learn_clone.sh > /workspace/learn_clone.out 2>&1 &
#     tail -f /workspace/learn_clone.out          # safe to Ctrl-C / lose the connection
#
# Pass slugs to override the default list:
#     bash scripts/runpod/run_learn_clone.sh the-rise-of-lvmh metas-metaverse-pivot
#
# Output: data/money_moves_audio_clone/<slug>.m4a  (NOT the live dir — the Mac stages the move,
# because re-voiced slugs need their published clip backed up first).
set -uo pipefail            # deliberately NOT -e: one bad slug must not abandon the batch

cd "$(dirname "$0")/../.." || exit 1        # -> backend/
OUT="data/money_moves_audio_clone"
LOG="$OUT/clone_run.log"
export CLONE_MODE=block                     # defaults to 'sentence', which renders subtly wrong

# setup_runpod.sh exports HF_HOME by appending it to ~/.bashrc — and a detached
# `setsid nohup bash this-script` NEVER sources ~/.bashrc. Unset, the job ignores the multi-GB
# model cache already on the volume, tries to re-download it, and dies on a HuggingFace
# 429 Too Many Requests — one slug at a time, ~60s each, quietly burning GPU time that is billing.
# Derive it from the checkout instead, and only when the volume cache is really there, so this
# script still behaves on a Mac (where the cache lives in ~/.cache/huggingface).
VOL="$(cd "$(dirname "$0")/../../../.." 2>/dev/null && pwd || true)"
if [ -n "${VOL:-}" ] && [ -d "$VOL/hf_cache/hub" ]; then
  export HF_HOME="$VOL/hf_cache"
  [ -d "$VOL/torch_cache" ] && export TORCH_HOME="$VOL/torch_cache"
  echo "  HF_HOME=$HF_HOME (volume cache)"
fi

# The 9 clips the Mac is not doing. 6 authored-but-silent + 3 that shipped in the wrong
# (Gemini) voice and are being re-voiced. boeing-vs-airbus is deliberately absent: it was
# rendered on the Mac as the approval sample.
DEFAULT_SLUGS=(
  the-rise-of-lvmh
  microsofts-cloud-metamorphosis
  tsmc-the-foundry-that-runs-the-world
  the-home-depot-vs-lowes
  the-rise-of-tiktok-vs-instagram-reels
  metas-metaverse-pivot
  nvidias-ai-dominance
  the-fall-of-sears
  amd-vs-intel-the-cpu-wars
)
SLUGS=("$@"); [ ${#SLUGS[@]} -eq 0 ] && SLUGS=("${DEFAULT_SLUGS[@]}")

# ---- preflight: fail in seconds, not after the pod has billed an hour -------------------
fail() { echo "PREFLIGHT FAILED: $*" >&2; exit 1; }
[ -x ./venv_clone/bin/python ] || fail "no venv_clone — run scripts/runpod/setup_runpod.sh first"
command -v ffmpeg >/dev/null || fail "ffmpeg missing (the m4a encode happens AFTER generation)"
[ -f data/voice_clone/refs/caydex_voice_achird_v2.wav ] \
  || fail "reference clip missing — sync_up.sh pushes it; without it the voice cannot be cloned"
[ -f ../frontend/ios/ios/Resources/MoneyMoves/money_moves.json ] || [ -f data/money_moves.json ] \
  || fail "money_moves.json missing — sync_up.sh pushes it"
./venv_clone/bin/python - <<'PY' || fail "CUDA not available — this pod is no faster than the Mac"
import sys, torch
print(f"  cuda={torch.cuda.is_available()} device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'}")
sys.exit(0 if torch.cuda.is_available() else 1)
PY
# Prove every weight resolves from the LOCAL cache before generating. The model is fetched lazily
# on the first cache-miss block, so without this a bad HF_HOME surfaces minutes in, as a download
# failure, once per slug.
./venv_clone/bin/python - <<'HFCHK' || fail "model weights not in the local cache — check HF_HOME / re-run setup_runpod.sh"
import sys
from huggingface_hub import try_to_load_from_cache
files = ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt")
missing = [f for f in files if not isinstance(try_to_load_from_cache("ResembleAI/chatterbox", f), str)]
print("  model cache:", "complete" if not missing else f"MISSING {missing}")
sys.exit(1 if missing else 0)
HFCHK
# Cache proven complete, so forbid network resolution outright: a cached repo still HEAD-requests
# the Hub for the revision, and that HEAD is what returned 429 while the weights sat on disk.
export HF_HUB_OFFLINE=1

mkdir -p "$OUT"
echo "=== run_learn_clone: ${#SLUGS[@]} slug(s), mode=$CLONE_MODE, started $(date -u +%FT%TZ) ===" | tee -a "$LOG"
BATCH_START=$(date +%s); ok=0; failed=()

for i in "${!SLUGS[@]}"; do
  slug="${SLUGS[$i]}"
  echo "--- [$((i+1))/${#SLUGS[@]}] $slug ---" | tee -a "$LOG"
  t0=$(date +%s)
  ./venv_clone/bin/python scripts/clone_learn_audio.py moneymoves "$slug" 2>&1 \
    | grep -v "^Sampling:" | tee -a "$LOG"
  rc=${PIPESTATUS[0]}; t1=$(date +%s)
  if [ "$rc" -ne 0 ] || [ ! -f "$OUT/$slug.m4a" ]; then
    failed+=("$slug"); echo "  !! FAILED (rc=$rc) after $((t1-t0))s" | tee -a "$LOG"
  else
    ok=$((ok+1)); echo "  ok in $((t1-t0))s · batch elapsed $(( (t1-BATCH_START)/60 ))m" | tee -a "$LOG"
  fi
  # After the first slug the rate is known — check it before walking away.
  [ "$i" -eq 0 ] && echo "  >> first slug took $((t1-t0))s; extrapolate x${#SLUGS[@]} before leaving it" | tee -a "$LOG"
done

echo "=== DONE: $ok/${#SLUGS[@]} in $(( ($(date +%s)-BATCH_START)/60 ))m ===" | tee -a "$LOG"
[ ${#failed[@]} -gt 0 ] && { echo "FAILED: ${failed[*]} — re-run those slugs; finished ones are skipped" | tee -a "$LOG"; exit 1; }
echo "Now, from the Mac:  bash backend/scripts/runpod/sync_down_learn.sh moneymoves" | tee -a "$LOG"
