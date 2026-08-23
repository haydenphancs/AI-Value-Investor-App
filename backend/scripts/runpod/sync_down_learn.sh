#!/usr/bin/env bash
# sync_down_learn.sh [moneymoves|journey] — RunPod -> Mac. Pull cloned LEARN narration clips.
#
# The book-only sync_down.sh does not cover these: clone_learn_audio.py writes to
# data/{money_moves,journey}_audio_clone/, which nothing else pulls.
#
# Lands them in the _clone/ staging dir on the Mac ON PURPOSE — never straight into the live
# audio dir. Re-voiced slugs must have their old clip backed up before being replaced, and
# align_*.py must run against the NEW local file, not the published one.
#
#     bash sync_down_learn.sh                          # both domains, every clip
#     bash sync_down_learn.sh moneymoves               # just Money Moves, every clip
#     bash sync_down_learn.sh moneymoves slug-a slug-b # ONLY these slugs  <- prefer this
#
# Name the slugs. A long-lived pod volume keeps every clip it has ever rendered — this one still
# held the 13 June renders — so an unfiltered pull drags already-published audio back into the
# staging dir and the local move step can no longer tell new work from old.
set -euo pipefail
: "${POD_HOST:?set POD_HOST}"
POD_PORT="${POD_PORT:-22}"
POD_USER="${POD_USER:-root}"
REPO="${AI_INVESTOR_ROOT:-/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App}"
REMOTE="$POD_USER@$POD_HOST"
DEST_ROOT="/workspace/AI-Value-Investor-App/backend/data"

case "${1:-both}" in
  moneymoves) DIRS=(money_moves_audio_clone); shift ;;
  journey)    DIRS=(journey_audio_clone); shift ;;
  both|"")    DIRS=(money_moves_audio_clone journey_audio_clone); shift || true ;;
  *) echo "usage: sync_down_learn.sh [moneymoves|journey] [slug ...]" >&2; exit 2 ;;
esac

SLUGS=("$@")
INC=(); if [ ${#SLUGS[@]} -gt 0 ]; then
  for s in "${SLUGS[@]}"; do INC+=(--include="$s.m4a"); done
  echo "pulling ONLY: ${SLUGS[*]}"
else
  INC=(--include="*.m4a")
  echo "pulling EVERY clip in the pod's staging dir (name slugs to narrow this)"
fi

for d in "${DIRS[@]}"; do
  if ! ssh -p "$POD_PORT" "$REMOTE" "[ -d $DEST_ROOT/$d ]"; then
    echo "  (pod has no $d — skipping)"; continue
  fi
  mkdir -p "$REPO/backend/data/$d"
  rsync -avz --no-o --no-g --partial -e "ssh -p $POD_PORT" \
    "${INC[@]}" --include="clone_run.log" --exclude="*" \
    "$REMOTE:$DEST_ROOT/$d/" "$REPO/backend/data/$d/"
done

echo
echo "sync_down_learn complete. VERIFY before you TERMINATE the pod:"
for d in "${DIRS[@]}"; do
  n=$(ls "$REPO/backend/data/$d"/*.m4a 2>/dev/null | wc -l | tr -d ' ')
  echo "  $d: $n clip(s)"
  ls -lh "$REPO/backend/data/$d"/*.m4a 2>/dev/null | awk '{print "    "$9"  "$5}'
done
