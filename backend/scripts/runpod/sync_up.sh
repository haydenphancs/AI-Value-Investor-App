#!/usr/bin/env bash
# sync_up.sh — Mac -> RunPod. Pushes ONLY what the clone job needs.
# Deliberately NO backend/.env, NO backend/app/ — zero secrets land on the pod.
#
# Requires (export before running, from the RunPod console pod details):
#     POD_HOST=<pod ip/host>   POD_PORT=<ssh port>   POD_USER=root (default)
# Prereq on a brand-new pod: `apt-get install -y rsync` (rsync must exist on both ends).
set -euo pipefail
: "${POD_HOST:?set POD_HOST (pod SSH host/ip from the RunPod console)}"
POD_PORT="${POD_PORT:-22}"
POD_USER="${POD_USER:-root}"
REPO="${AI_INVESTOR_ROOT:-/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App}"
DEST="/workspace/AI-Value-Investor-App"
SSH="ssh -p $POD_PORT"
REMOTE="$POD_USER@$POD_HOST"

ssh -p "$POD_PORT" "$REMOTE" \
  "mkdir -p $DEST/backend/scripts $DEST/documents $DEST/backend/data/voice_clone/refs"

# code + book source text + the gitignored reference clip(s). Explicit paths keep the
# transfer minimal and secret-free.
rsync -avz --no-o --no-g --partial -e "$SSH" "$REPO/backend/scripts/"               "$REMOTE:$DEST/backend/scripts/"
rsync -avz --no-o --no-g --partial -e "$SSH" "$REPO/backend/requirements_clone.txt" "$REMOTE:$DEST/backend/requirements_clone.txt"
rsync -avz --no-o --no-g --partial -e "$SSH" "$REPO/documents/books/"              "$REMOTE:$DEST/documents/books/"
rsync -avz --no-o --no-g --partial -e "$SSH" "$REPO/backend/data/voice_clone/refs/" "$REMOTE:$DEST/backend/data/voice_clone/refs/"

echo "sync_up complete -> $REMOTE:$DEST   (NO .env, NO app/ pushed)."
