#!/bin/bash
# Orchestrates phase 2 of the 2016-01-01 ~ 2020-12-31 collection:
#   download batch -> commit -> push, looping until manifest_2016_2020.json is
#   exhausted.
#
# Layout: scripts + manifest + done log live in /home/user/data-scripts; the
# downloaded files go into the checkout of haechyaning-commits/data at
# /home/user/data. Only the output folder (자체감사파일3) is committed/pushed.
#
# Simple + robust model (no skip-worktree, no disk-freeing): all files stay
# materialized on disk, and each batch does a plain `git add 자체감사파일3`.
# Because no committed file is ever removed from the worktree, `git add` only
# ever stages ADDITIONS — never deletions — so batches accumulate correctly.
# (An earlier skip-worktree+rm approach broke under sparse-checkout by staging
# prior batches as deletions; total download is ~8GB and disk has room, so we
# just keep everything on disk.)
#
# Each `node` run is a fresh process, so it always reads the CURRENT $HTTPS_PROXY
# (the proxy port changes across container restarts). Push is chunked at ~400MB
# by the batch budget to stay under the request-size (413) cap.
set -u

DATA=/home/user/data
SCRIPTS=/home/user/data-scripts
BRANCH=main
OUT=자체감사파일3
BATCH_BYTES=${BATCH_BYTES:-350000000}   # ~350MB per batch (stays under push size cap)
REMAINING_FILE="$SCRIPTS/remaining_2016_2020.txt"

cd "$DATA" || { echo "cannot cd $DATA" >&2; exit 1; }
git config gc.auto 0 2>/dev/null || true

PREV_REMAINING=-1
STALL=0

while true; do
  node "$SCRIPTS/download_batch3.js" "$DATA/$OUT" "$BATCH_BYTES"
  rc=$?
  if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then
    echo "download_batch3.js exited rc=$rc; aborting" >&2
    exit $rc
  fi
  remaining=$(cat "$REMAINING_FILE" 2>/dev/null || echo -1)

  # Stage the whole output folder. Safe because we never delete worktree copies,
  # so only newly-downloaded files are staged (additions), never deletions.
  git add "$OUT" 2>/dev/null
  n_staged=$(git diff --cached --numstat | wc -l)
  n_del=$(git diff --cached --name-status | grep -c '^D' || true)
  if [ "$n_del" -ne 0 ]; then
    echo "SAFETY ABORT: $n_del deletions staged unexpectedly; not committing" >&2
    exit 5
  fi
  if [ "$n_staged" -gt 0 ]; then
    git commit -q -m "자체감사파일3: 2016~2020 자체감사결과 배치 추가 (${n_staged}개 파일, 남은 항목 ${remaining}건)

Co-Authored-By: Claude <noreply@anthropic.com>"

    pushed=0
    for delay in 0 2 4 8 16 30; do
      [ "$delay" -gt 0 ] && sleep "$delay"
      if git push -u origin "$BRANCH"; then pushed=1; break; fi
      echo "push failed; retrying in next backoff step" >&2
    done
    if [ "$pushed" -ne 1 ]; then
      echo "git push failed after retries; aborting (files remain committed locally)" >&2
      exit 3
    fi
  fi

  if [ "$remaining" = "0" ]; then
    echo "ALL DONE"
    break
  fi
  if [ "$remaining" = "$PREV_REMAINING" ]; then
    STALL=$((STALL + 1))
    if [ "$STALL" -ge 3 ]; then
      echo "No progress across 3 consecutive batches (remaining=$remaining); aborting" >&2
      exit 4
    fi
  else
    STALL=0
  fi
  PREV_REMAINING=$remaining
done
