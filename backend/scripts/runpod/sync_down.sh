#!/usr/bin/env bash
# sync_down.sh [N] — RunPod -> Mac. Pull generated audio + manifests + cost reports.
# RUN THIS (and verify the files) BEFORE terminating the pod.
#
#     bash sync_down.sh        # pull every book's artifacts
#     bash sync_down.sh 2      # pull only book 2
set -euo pipefail
: "${POD_HOST:?set POD_HOST}"
POD_PORT="${POD_PORT:-22}"
POD_USER="${POD_USER:-root}"
REPO="${AI_INVESTOR_ROOT:-/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App}"
SRC="/workspace/AI-Value-Investor-App/backend/data/book_audio"
REMOTE="$POD_USER@$POD_HOST"
ORDER="${1:-}"
GLOB="${ORDER:+${ORDER}_*}"; GLOB="${GLOB:-*}"

mkdir -p "$REPO/backend/data/book_audio"
rsync -avz --no-o --no-g --partial -e "ssh -p $POD_PORT" \
  --include="${GLOB}.m4a" \
  --include="${GLOB}.manifest.json" \
  --include="${GLOB}.cost_report.json" \
  --include="${GLOB}.readalong.json" \
  --include="runpod_cost_log.jsonl" \
  --exclude="*" \
  "$REMOTE:$SRC/" "$REPO/backend/data/book_audio/"

echo
echo "sync_down complete. VERIFY these exist before you TERMINATE the pod:"
ls -lh "$REPO"/backend/data/book_audio/${ORDER:+${ORDER}_}* 2>/dev/null \
  || echo "  (no files matched — did the pod finish generating?)"
